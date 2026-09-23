"""The Auto prompt routes on the two MiniMax tabs.

Five routes per tab that sets `autoprompt`, mounted by routes.tabs from
inside its per-tab loop and behind the same gate as the tab's others, so
a licence without the tab gets a 403 here too. What they drive is
ember.pipelines.minimax.autoprompt; this module only turns requests into
its calls and its Refused into a 422.
"""

from fastapi import APIRouter, Body, HTTPException

from ember.pipelines.minimax import autoprompt
from ember.web import tabschema
from ember.web.routes.common import _resolve_uploads

# The controls the prompt is written for: the start image for its
# content and shape, and the three that decide the clip's duration and
# canvas. Everything else on the tab has no bearing on the words.
_READS = ("image", "aspect", "resolution", "seconds")


def mount(api: APIRouter, schema, gate: list) -> None:
    """One tab's Auto prompt routes. Called only from tabs._mount_tab."""
    key = str(schema.key)
    base = "/tabs/%s/autoprompt" % key

    @api.get(base, dependencies=gate, name="autoprompt_config_" + key)
    def config():
        return autoprompt.config()

    def read(body: dict) -> dict:
        """The controls in _READS from a submitted bag, checked and typed."""
        raw = body.get("values")
        if not isinstance(raw, dict):
            raise HTTPException(400, "Send a `values` object.")
        resolved = _resolve_uploads(schema, raw)
        values = {}
        for name in _READS:
            field = schema.field(name)
            if field is not None:
                values[name] = field.coerce(resolved.get(name, field.initial()))
        return values

    @api.post(base, dependencies=gate, name="autoprompt_start_" + key)
    def start(body: dict = Body(default={})):
        """Begin writing a prompt. Answers at once with the task to poll.

        `values` is the tab's form bag with the image as an upload id,
        exactly what Generate sends; only _READS are looked at.
        """
        try:
            values = read(body)
            return autoprompt.start(
                key, values, idea=str(body.get("idea") or ""),
                model=str(body.get("model") or autoprompt.DEFAULT_MODEL),
                api_key=str(body.get("key") or ""),
                image=values.get("image"))
        except tabschema.Invalid as exc:
            raise HTTPException(422, str(exc))
        except autoprompt.Refused as exc:
            raise HTTPException(422, str(exc))

    @api.post(base + "/text", dependencies=gate,
              name="autoprompt_text_" + key)
    def text(body: dict = Body(default={})):
        """What the model would be sent, as one text to copy. No call made."""
        try:
            values = read(body)
            return {"text": autoprompt.copy_text(
                key, values, idea=str(body.get("idea") or ""),
                image=values.get("image"))}
        except tabschema.Invalid as exc:
            raise HTTPException(422, str(exc))
        except autoprompt.Refused as exc:
            raise HTTPException(422, str(exc))

    @api.get(base + "/{task_id}", dependencies=gate,
             name="autoprompt_status_" + key)
    def status(task_id: str):
        found = autoprompt.status(key, task_id)
        if found is None:
            raise HTTPException(404, "That prompt is no longer being "
                                     "written here.")
        return found

    @api.post(base + "/{task_id}/cancel", dependencies=gate,
              name="autoprompt_cancel_" + key)
    def cancel(task_id: str):
        found = autoprompt.cancel(key, task_id)
        if found is None:
            raise HTTPException(404, "That prompt is no longer being "
                                     "written here.")
        return found
