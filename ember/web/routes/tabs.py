"""The four routes every generation tab has, and the licence gate on them.

A tab's schema, applying a preset or a recipe to it, the latest yield of
one of its runs, and queueing a click. All four are registered by the loop
below and nowhere else, which is what makes `require_feature` unskippable.
"""

from fastapi import APIRouter, Body, Depends, HTTPException

from ember.web import gallery_index
from ember.generation import queue as jobqueue
from ember.licensing import presets
from ember.generation import recipes
from ember.web import tabschema
from ember.web.routes.common import (
    require_auth, require_feature, _display_json, _queue_json,
    _resolve_uploads,
)


def _mount_tabs(api: APIRouter) -> None:
    """Register the per-tab routes — the only place they are created.

    Layer one of the licence gate. Every route below is attached inside
    this loop with `Depends(require_feature(schema.key))`, so there is no
    way to add a tab route that is not gated; adding one by hand outside
    this function is the mistake scripts/check_routes.py exists to catch.

    Note the loop walks `tabschema.SCHEMAS` and not `entitled()`: the
    routes are registered for every tab and each one *refuses*, rather
    than not existing. That is deliberate — a licence upgrade takes effect
    on the next `features.resolve()` without rebuilding the app, and a
    403 with a sentence is a better answer than a 404 to a customer who
    has just paid for the tab.
    """
    for schema in tabschema.SCHEMAS:
        _mount_tab(api, schema)


def _mount_tab(api: APIRouter, schema) -> None:
    """One tab's three routes, closed over its schema."""
    gate = [Depends(require_auth), Depends(require_feature(schema.key))]
    key = str(schema.key)

    @api.get("/schema/" + key, dependencies=gate, name="schema_" + key)
    def get_schema():
        return schema.to_json()

    @api.post("/schema/%s/apply" % key, dependencies=gate,
              name="apply_" + key)
    def apply(body: dict = Body(default={})):
        """A preset name or a stored recipe -> values for the form.

        Server-side, and it has to be: guarding a value means knowing
        whether this pod has that LoRA file and what this build's slider
        range is, and the browser knows neither. Out of range is not an
        error here — an unknown choice leaves the control alone and a
        number is clamped, which is ui._pick and ui._num exactly.

        Answers `{"values": {...}}` holding only the controls it could
        set. Everything absent keeps whatever the customer had, which is
        the difference between "load what I can" and "reset the form".
        """
        if "preset" in body:
            name = str(body.get("preset") or "")
            settings = _preset_settings(schema.preset_tab, name)
            if settings is None:
                # A preset disabled or renamed while the page sat open.
                # Changing nothing is the honest answer.
                return {"values": {}, "applied": None}
            return {"values": schema.preset_values(settings),
                    "applied": name}
        if "recipe" in body:
            stored = body.get("recipe")
            if isinstance(stored, str):
                try:
                    target = gallery_index.safe_path(stored)
                except (ValueError, OSError):
                    raise HTTPException(404, "No such file.")
                stored = recipes.for_path(target)
            if not isinstance(stored, dict):
                return {"values": {}, "applied": None}
            if str(stored.get("tab")) != key:
                raise HTTPException(
                    400, "That recipe belongs to the %s tab."
                         % (stored.get("tab_label") or stored.get("tab")))
            # Every control as recorded, with one substitution: Seed is
            # the seed that picture actually ran on rather than whatever
            # number was sitting in the box, ignored, while it ran.
            #
            # Random seed is *not* touched, and the pair is the whole
            # design. This used to switch it off as well, on the reading
            # that somebody loading a recipe wants that picture back. They
            # do not -- they have that picture. They want its settings and
            # then another one like it, and with the seed pinned the next
            # Generate could only reproduce the file byte for byte: same
            # seed, same graph, same picture. ComfyUI answered that out of
            # the cache in about a second, having sampled nothing, so the
            # run looked like it had not happened at all.
            #
            # Left as recorded, Random seed comes back the way nearly every
            # generation is made -- on -- and Generate produces a new
            # picture from those settings. The exact frame stays one click
            # away, because the number to do it with is now in the box:
            # untick Random seed and run. Restoring the value costs nothing
            # while the tick is on, since the control ignores it.
            values = schema.restore_recipe(stored.get("fields") or [])
            if stored.get("seed") is not None and schema.field("seed"):
                values["seed"] = stored["seed"]
            return {"values": values, "applied": stored.get("tab_label")}
        if "settings" in body:
            return {"values": schema.preset_values(body.get("settings") or {}),
                    "applied": None}
        raise HTTPException(400, "Send a preset name, a recipe or settings.")

    @api.get("/tabs/%s/display" % key, dependencies=gate,
             name="display_" + key)
    def display(job: str | None = None):
        """The latest yield of one of this tab's runs.

        Without `job`, the run this tab should be showing —
        jobqueue.display_for's reading: the newest one that has actually
        produced something.

        With `job`, that run in particular. This is the half the polling
        fallback needs and did not have: it can only ask one question per
        tab per round, and while a second run is starting the answer to
        the unqualified question is already about the second run. Naming
        the job is how the first one's last picture gets collected.

        `job` always comes back, so the caller attaches output to the run
        that made it instead of guessing from the tab — null when there is
        no such run here, or it has yielded nothing yet.
        """
        if job is None:
            job_id, revision, result = jobqueue.display_for(key)
        else:
            job_id, revision, result = jobqueue.display_of(job, tab=key)
        return {"job": job_id, "revision": revision,
                "result": _display_json(schema, result)}

    @api.post("/tabs/%s/generate" % key, dependencies=gate,
              name="generate_" + key)
    def generate(body: dict = Body(default={})):
        """Queue one click. Returns in microseconds.

        The click does not generate — it writes the click down and hands
        it to the queue, which runs one job at a time per lane on a worker
        thread.
        """
        raw = body.get("values")
        if not isinstance(raw, dict):
            raise HTTPException(400, "Send a `values` object.")
        try:
            resolved = _resolve_uploads(schema, raw)
            args, values = schema.submit(resolved)
        except tabschema.Invalid as exc:
            # str(exc) is "steps: must be at most 60" — the field name is
            # half the message, and a form that cannot say which control
            # it is complaining about is a form nobody can fix.
            raise HTTPException(422, str(exc))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))

        view = jobqueue.submit(
            lane=schema.lane, tab=key, tab_label=schema.title(),
            title=_job_title(schema, values),
            fn=_recording(schema, values), args=args,
            result_keys=schema.result_keys,
        )
        ahead = max(0, view.place - 1)
        return {
            "jobId": view.id,
            "place": view.place,
            "message": ("🕑 Queued — it starts as soon as the GPU is free."
                        if ahead == 0 else
                        "🕑 Queued — %d job(s) ahead of it." % ahead),
            "queue": _queue_json(),
        }


def _preset_settings(tab, name):
    """One preset's settings blob by name, or None if it is not on offer."""
    if not tab or not name:
        return None
    for row in presets.catalogue().for_tab(tab):
        if row.name == name:
            return row.settings
    return None


def _job_title(schema, values) -> str:
    """A one-line name for a queued job — its prompt.

    Truncated hard: the queue is a list to scan, not a place to read a
    prompt back. An empty prompt gets a dash, which is honest — what
    identifies that job is its tab and its place in the line.
    """
    text = " ".join(str(values.get(schema.prompt_field) or "").split())
    if not text:
        return "—"
    return text[:59] + "…" if len(text) > 60 else text


def _recording(schema, values):
    """The handler, wrapped so whatever it writes is filed under a recipe.

    The wrapper exists to move one line onto the *worker* thread. The
    values were read on the request, but the recipe has to be announced
    from the thread that will do the generating — recipes keys it by
    thread, so the output hook deep inside client.run can find it without
    every executor having to pass it down (see recipes.py).

    A generator, because the handlers are: `yield from` keeps the tab's
    own progress reporting exactly as it was, and the `finally` runs on a
    cancelled job too, since closing a generator raises GeneratorExit at
    its current yield.
    """
    fields = schema.recipe_fields(values)
    key = str(schema.key)
    tab_id = schema.tab_id
    label = schema.title()
    handler = schema.handler

    def run(*args):
        recipes.begin(key, label, tab_id, fields)
        try:
            yield from handler(*args)
        finally:
            recipes.end()

    return run
