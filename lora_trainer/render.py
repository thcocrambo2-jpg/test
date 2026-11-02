"""Render with a trained face LoRA through the app's own ComfyUI.

The pod runs the compiled build, whose tabs offer only catalogue LoRAs,
so a LoRA trained a minute ago cannot be picked there. This goes around
the tab, not around the app: the settings are one of the 🔶 Krea2 V2
presets, read from the running app over its API (`/schema/.../apply`,
over the tab's own defaults), the graph is the app's own
`build_v2_workflow` from this checkout, and it is queued on the app's
ComfyUI — so a picture here is the picture the tab would make with the
same preset, plus the face LoRA on the end of the stack.

Model and LoRA ids resolve through the catalogue the app saved on its
last start (BASE_DIR/.catalog.json), which is what the running app
itself is using.
"""

import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ember import settings  # noqa: E402

if not settings.catalog_file():
    os.environ[settings.CATALOG_FILE_ENV] = str(settings.BASE_DIR / ".catalog.json")

TAB = "krea_v2_t2i"
APP_API = os.environ.get("LORA_TRAINER_APP", "http://127.0.0.1:7860/api/v1")
COMFY = "http://%s:%d" % (settings.COMFY_HOST, settings.COMFY_PORT)
SLOT_RE = re.compile(r"^(\w+)\.(\d+)\.(enabled|name|weight)$")


def _call(method: str, url: str, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def presets() -> dict:
    """The tab's preset names and its default, as the app offers them."""
    answer = _call("GET", "%s/presets/%s" % (APP_API, TAB))
    return {"names": [p["name"] for p in answer.get("presets") or []],
            "default": answer.get("default")}


def preset_values(preset: str) -> dict:
    """The tab's defaults with `preset` applied on top — the form, loaded."""
    schema = _call("GET", "%s/schema/%s" % (APP_API, TAB))
    values = {f["name"]: f.get("default") for f in schema["fields"]}
    stack = schema.get("lora") or {}
    for index, row in enumerate(stack.get("slots") or []):
        for part, value in row.items():
            values["%s.%d.%s" % (stack.get("key", "lora"), index, part)] = value
    applied = _call("POST", "%s/schema/%s/apply" % (APP_API, TAB),
                    {"preset": preset})["values"]
    if not applied:
        raise RuntimeError("Preset %r is not on offer." % preset)
    values.update(applied)
    return values


def preset_loras(values: dict) -> list:
    """The ticked rows of a loaded form, as (file, strength), in order."""
    from ember.licensing import catalog

    rows = {}
    for key, value in values.items():
        match = SLOT_RE.match(key)
        if match:
            rows.setdefault(int(match.group(2)), {})[match.group(3)] = value
    loras = []
    for index in sorted(rows):
        row = rows[index]
        record = catalog.lora(row.get("name"))
        if row.get("enabled") and record is not None:
            loras.append((record.file, float(row.get("weight") or 1.0)))
    return loras


def build(values: dict, prompt: str, negative: str, seed: int,
          face_lora: str, strength: float) -> dict:
    """generate_v2's arithmetic, then build_v2_workflow — one picture."""
    from ember.licensing import catalog
    from ember.pipelines.krea2_v2.workflow import (
        build_v2_workflow, resolve_size)
    from ember.pipelines.common import resolve_model

    model = resolve_model(TAB, values.get("model"))
    if model is None:
        raise RuntimeError("The catalogue (%s) lists no Krea2 V2 model."
                           % settings.catalog_file())
    width, height = resolve_size(values["aspect"], values["megapixels"],
                                 values["multiple"])
    v = values
    sampler = {"eta": float(v["eta"]), "sampler_name": v["sampler_name"],
               "scheduler": v["scheduler"], "steps": int(v["steps"]),
               "denoise": float(v["denoise"]), "cfg": float(v["cfg"]),
               "sampler_mode": v["sampler_mode"], "bongmath": bool(v["bongmath"])}
    variance = {"variance_preset": v["variance_preset"],
                "fine_tune_variance": int(v["fine_tune_variance"]),
                "model_type": v["variance_model_type"],
                "variance_schedule": v["variance_schedule"],
                "cutoff_step": int(v["cutoff_step"]),
                "total_steps": int(v["total_steps"]),
                "cutoff_strength": float(v["cutoff_strength"]),
                "shift_strength": int(v["shift_strength"])}
    loras = preset_loras(values) + [(face_lora, float(strength))]
    catalog.get()
    return build_v2_workflow(
        prompt=prompt, negative=negative, seed=seed, width=width,
        height=height, loras=loras, model=model, sampler_settings=sampler,
        variance_settings=variance, sharpen=bool(v.get("sharpen")),
        film_grain=bool(v.get("film_grain")),
        filename_prefix="Krea2V2_" + Path(face_lora).stem)


def queue(workflow: dict) -> str:
    answer = _call("POST", COMFY + "/prompt",
                   {"prompt": workflow, "client_id": "lora-trainer"})
    if answer.get("node_errors"):
        raise RuntimeError("ComfyUI refused the graph: %s"
                           % json.dumps(answer["node_errors"])[:500])
    return answer["prompt_id"]


def wait(prompt_id: str, timeout: float = 1800) -> list:
    """Block until ComfyUI has finished `prompt_id`; its output images."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = _call("GET", "%s/history/%s" % (COMFY, prompt_id))
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                messages = [m for m in status.get("messages") or []
                            if m and m[0] == "execution_error"]
                raise RuntimeError("ComfyUI failed: %s" % (
                    messages[-1][1].get("exception_message") if messages
                    else "unknown error"))
            if status.get("completed") or entry.get("outputs"):
                return [image for out in entry.get("outputs", {}).values()
                        for image in out.get("images") or []]
        time.sleep(1.5)
    raise RuntimeError("Timed out waiting for ComfyUI.")


def fetch(image: dict) -> bytes:
    query = urllib.parse.urlencode({
        "filename": image["filename"], "subfolder": image.get("subfolder", ""),
        "type": image.get("type", "output")})
    with urllib.request.urlopen("%s/view?%s" % (COMFY, query), timeout=60) as r:
        return r.read()


def with_trigger(prompt: str, trigger: str) -> str:
    """The prompt, with the trigger in front if it does not say it already."""
    if trigger and trigger.lower() not in prompt.lower():
        return "%s, %s" % (trigger, prompt)
    return prompt


def render(*, preset: str, prompt: str, negative: str | None, seed: int | None,
           count: int, face_lora: str, strength: float, trigger: str,
           on_progress=None) -> list:
    """`count` pictures (consecutive seeds); [(seed, png bytes), ...]."""
    values = preset_values(preset)
    if negative is None:
        negative = values.get("negative") or ""
    base = random.randint(0, 2**32 - 1) if seed is None else int(seed)
    prompt = with_trigger(prompt, trigger)
    made = []
    for i in range(count):
        if on_progress:
            on_progress(i, count)
        workflow = build(values, prompt, negative, base + i, face_lora,
                         strength)
        for image in wait(queue(workflow)):
            made.append((base + i, fetch(image)))
    if on_progress:
        on_progress(count, count)
    return made
