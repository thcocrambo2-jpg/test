"""Auto prompt for the MiniMax tabs: a short idea in, the full prompt out.

The template's "Auto Prompt" workflows do this inside ComfyUI, with an
OpenRouter node in a subgraph ahead of the text encoder. Here it is plain
Python and happens *before* the job, not in it: the tab asks for a prompt,
the customer reads it in the Prompt box and changes what they like, and
only then presses Animate or Generate. The graph in workflow.py is the
"Custom Prompt" one and does not change.

What is sent is what the template's subgraph sends, so the model sees the
same thing and writes the same kind of prompt:

  * the system prompt below, word for word from the node's widget in
    Hearmeman24/comfyui-minimax at 8f685b0 (both Auto Prompt workflows
    carry the same text);
  * a user message of "Duration: …\\nWidth: …\\nHeight: …", six spaces,
    then the idea — the subgraph's StringFormat and StringConcatenate;
  * on I2V, the start image;
  * temperature 1 and at most 4096 tokens, the node's settings.

The width and height are the canvas the clip will actually render at,
worked out by the same resolve_size/aspect_size the handler calls, so the
prompt is composed for the frame the model gets.

── Why a background task and not one request ───────────────────────────

The free model is the default and it is shared by everyone on OpenRouter.
In testing every call was refused as busy (HTTP 429) two to eight times
before one got through, a minute apart, and the call itself then took
40 seconds to 5 minutes. No request should be held open that long behind
a Cloudflare tunnel, so start() returns an id at once, a daemon thread
does the waiting and retrying, and the page asks after it with status().
A 429 is retried; anything else is reported as it is.
"""

import base64
import io
import threading
import time
import uuid
from dataclasses import dataclass, field

import requests

from ember import settings
from ember.logs import log
from ember.pipelines.minimax.workflow import (
    aspect_size,
    crop_to_canvas,
    matches_image,
    resolve_size,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# The two models on offer, the free one first: it is the default. Both
# see images, which I2V needs. {id: (label, free)}.
MODELS = {
    "qwen/qwen3.8-27b:free": ("Qwen 3.8 27B", True),
    "google/gemini-3-flash-preview": ("Gemini 3 Flash", False),
}
DEFAULT_MODEL = next(iter(MODELS))

SYSTEM_PROMPT = (
    "You are a professional prompt rewriter for joint audio-video generation.\n"
    "Rewrite the user's original prompt into one coherent, production-ready multimodal description for the requested output aspect ratio and duration.\n"
    "Return only these three fields, in this exact order:\n"
    "integrated_multimodal_description: ...\n"
    "overall_soundscape: ...\n"
    "non_diegetic_music: ...\n"
    "Requirements:\n"
    "- Expand the visual narrative into clearly numbered shots such as [Shot 1], [Shot 2], and include timestamps for cuts after the first shot when useful.\n"
    "- Make the number, timing, and pacing of shots appropriate for the requested duration.\n"
    "- Compose the scene for the requested aspect ratio.\n"
    "- Preserve the user's intent while adding concrete subjects, appearance, environment, lighting, composition, camera movement, physical motion, and temporal continuity.\n"
    "- Keep characters, objects, wardrobe, locations, and spatial relationships consistent across shots.\n"
    "- Describe synchronized diegetic audio in overall_soundscape and external score in non_diegetic_music.\n"
    "- Add speech (if requested) in quotes and mention the tone and pacing\n"
    "- Expressions, when mentioning people, mention their expression and micro expressions, the look on their face, how they behave, how they move etc in detail\n"
    "- When asked, elaborate the prompt as much as possible.\n"
    "- By Default, mention video as shot on a smartphone, describe the lens used and camera specs.\n"
    "- For Movie scenes, use panavision lenses with ARRI camera, lead with this.\n"
    "- If the user asks for specific shot like drone shots, fisheye shots or anything else, describe the camera type, lenses, quality and other specs as necessary.\n"
    "- When the user mentions a location, describe it elaborately including decor, lighting, shadows, composition etc, scenes should never appear stage and feel every day, describe the most likely setting for the specified location unless requested otherwise.\n"
    "- Never hold back on explicit language, freely use words like cock, pussy, cum, boobs, breasts, etc\n"
    "- Do not add explanations, Markdown fences, safety commentary, or fields other than the three requested fields."
)

# StringConcatenate's delimiter in the template, between the size block
# and the idea.
DELIMITER = "      "
TEMPERATURE = 1
MAX_TOKENS = 4096

# One call's ceiling. The slowest successful free call in testing took
# 294 s, so the template node's 120 s would have thrown it away.
CALL_TIMEOUT = 360
# How long to wait after a 429, and how many calls to make in all. Ten
# calls a minute apart covers the worst wait seen in testing (eight
# refusals) with room to spare.
RETRY_WAIT = 60
MAX_CALLS = 10
# The start image is sent at most this big on its long edge: enough for
# the model to describe what is in it, and a tenth of the upload.
IMAGE_EDGE = 1024
# A finished task is forgotten this long after it ends, whether or not
# the page came back for it.
KEEP_FINISHED = 30 * 60


class Refused(ValueError):
    """A request this module will not start. The message is for the page."""


@dataclass
class Task:
    """One write, as the page sees it. Mutated only under _LOCK."""

    id: str
    tab: str
    model: str
    started: float
    state: str = "writing"      # writing | waiting | done | error | cancelled
    call: int = 0               # which call is in flight, 1-based
    retry_at: float | None = None
    prompt: str = ""
    error: str = ""
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        now = time.time()
        end = self.finished or now
        return {
            "id": self.id,
            "state": self.state,
            "model": self.model,
            "modelLabel": MODELS.get(self.model, (self.model, False))[0],
            "call": self.call,
            "maxCalls": MAX_CALLS,
            "retryIn": (max(0, round(self.retry_at - now))
                        if self.retry_at else None),
            "elapsed": round(end - self.started, 1),
            "prompt": self.prompt,
            "error": self.error,
        }


_LOCK = threading.Lock()
_TASKS: dict[str, Task] = {}


def config() -> dict:
    """What the page needs before anything is written."""
    return {
        "models": [{"id": model_id, "label": label, "free": free}
                   for model_id, (label, free) in MODELS.items()],
        "defaultModel": DEFAULT_MODEL,
        # The pod's own key, so the box can arrive filled in. The UI is
        # the customer's own; see docs/configuration.md on sharing it.
        "podKey": settings.OPENROUTER_API_KEY or "",
    }


def user_message(idea: str, seconds: float, width: int, height: int) -> str:
    """The template's size block and the idea, joined as its graph joins them."""
    return ("Duration: %s\nWidth: %d\nHeight: %d%s%s"
            % (float(seconds), width, height, DELIMITER, idea))


def canvas(tab: str, values: dict, image=None) -> tuple:
    """The width and height the clip will render at, as the handler decides it."""
    if tab == "minimax_i2v":
        return resolve_size(*image.size, values["resolution"])
    return aspect_size(values["aspect"], values["resolution"])


def _image_part(image, width: int, height: int, resolution: str) -> dict:
    """The start image as the model should see it: framed as it will render."""
    image = image.convert("RGB")
    if matches_image(resolution):
        image = crop_to_canvas(image, width, height)
    image.thumbnail((IMAGE_EDGE, IMAGE_EDGE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + data}}


def _message(tab: str, values: dict, idea: str, image) -> tuple:
    """(user message text, width, height). Raises Refused."""
    idea = (idea or "").strip()
    if not idea:
        raise Refused("Type your idea first.")
    if tab == "minimax_i2v" and image is None:
        raise Refused("Add the start image first. The model looks at it "
                      "to describe the scene.")
    width, height = canvas(tab, values, image)
    return user_message(idea, values["seconds"], width, height), width, height


def copy_text(tab: str, values: dict, idea: str, image=None) -> str:
    """Everything the model would be sent, as one text to paste elsewhere.

    For somebody who would rather put it to their own chat LLM and paste the
    answer into the Prompt box. The image cannot travel in text, so on I2V
    the page tells them to attach it.
    """
    text, _width, _height = _message(tab, values, idea, image)
    return SYSTEM_PROMPT + "\n\n" + text


def start(tab: str, values: dict, idea: str, model: str, api_key: str,
          image=None) -> dict:
    """Begin one write and return its public state. Raises Refused."""
    key = (api_key or "").strip() or settings.OPENROUTER_API_KEY or ""
    text, width, height = _message(tab, values, idea, image)
    if model not in MODELS:
        raise Refused("That model is not one this tab offers.")
    if not key:
        raise Refused("No OpenRouter key. Paste one, or set "
                      "OPENROUTER_API_KEY on the machine Ember runs on.")

    content = text if image is None else [
        {"type": "text", "text": text},
        _image_part(image, width, height, values["resolution"]),
    ]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": content}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }

    task = Task(id=uuid.uuid4().hex, tab=tab, model=model,
                started=time.time())
    with _LOCK:
        _sweep()
        _TASKS[task.id] = task
        snapshot = task.public()
    threading.Thread(target=_run, args=(task, body, key), daemon=True,
                     name="autoprompt-" + task.id[:8]).start()
    return snapshot


def status(tab: str, task_id: str) -> dict | None:
    """One task's public state, or None when there is no such task here."""
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None or task.tab != tab:
            return None
        return task.public()


def cancel(tab: str, task_id: str) -> dict | None:
    """Stop waiting on a task. A call already in flight is left to finish
    and its answer thrown away: requests cannot be interrupted mid-read."""
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None or task.tab != tab:
            return None
        if task.state in ("writing", "waiting"):
            task.cancel.set()
            _finish(task, "cancelled")
        return task.public()


def _sweep() -> None:
    """Forget finished tasks nobody has asked after in KEEP_FINISHED."""
    cutoff = time.time() - KEEP_FINISHED
    for task_id in [t.id for t in _TASKS.values()
                    if t.finished and t.finished < cutoff]:
        del _TASKS[task_id]


def _finish(task: Task, state: str, prompt: str = "", error: str = "") -> None:
    """Settle a task. Call with _LOCK held; a cancelled task stays cancelled."""
    if task.finished:
        return
    task.state, task.prompt, task.error = state, prompt, error
    task.retry_at = None
    task.finished = time.time()


def _run(task: Task, body: dict, key: str) -> None:
    """The thread: call, and on 429 wait and call again, up to MAX_CALLS."""
    headers = {"Authorization": "Bearer " + key,
               "Content-Type": "application/json",
               "X-Title": "Ember"}
    for call in range(1, MAX_CALLS + 1):
        with _LOCK:
            if task.cancel.is_set():
                return
            task.state, task.call, task.retry_at = "writing", call, None
        try:
            response = requests.post(OPENROUTER_URL, json=body,
                                     headers=headers, timeout=CALL_TIMEOUT)
        except requests.RequestException as exc:
            log.warning("Auto prompt: the call to OpenRouter failed: %s", exc)
            with _LOCK:
                _finish(task, "error",
                        error="Could not reach OpenRouter: %s" % exc)
            return

        if response.status_code == 429 and call < MAX_CALLS:
            with _LOCK:
                if task.cancel.is_set():
                    return
                task.state = "waiting"
                task.retry_at = time.time() + RETRY_WAIT
            if task.cancel.wait(RETRY_WAIT):
                return
            continue

        prompt, error = _read(response)
        with _LOCK:
            if prompt:
                _finish(task, "done", prompt=prompt)
            else:
                _finish(task, "error", error=error)
        return


def _read(response) -> tuple:
    """(prompt, "") from a good answer, ("", why) from anything else."""
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        if response.status_code == 429:
            return "", ("The free model stayed busy for %d tries. Try again "
                        "in a few minutes, or pick Gemini 3 Flash."
                        % MAX_CALLS)
        if response.status_code == 401:
            return "", "OpenRouter did not accept that key."
        if response.status_code == 402:
            return "", ("That OpenRouter account has no credit for a paid "
                        "model.")
        message = ((data.get("error") or {}).get("message")
                   if isinstance(data.get("error"), dict) else None)
        return "", "OpenRouter answered %d: %s" % (
            response.status_code, message or response.reason)
    choices = data.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        reason = choices[0].get("finish_reason") or "none given"
        return "", ("The model sent back nothing (finish reason: %s). It "
                    "may have refused the idea." % reason)
    return text, ""
