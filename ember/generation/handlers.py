"""Every generator behind a Generate button, with no web layer in sight.

This is the half of the app that decides what gets rendered — it reads
control values, validates them, builds a workflow dict per job and drives
client.run. Every generate_* is a plain generator yielding plain values,
and nothing here knows what will display them.

That independence is what makes the module checkable: `scripts/golden.py`
imports it with no FastAPI, no browser and no GPU, and snapshots the
workflow dict each handler builds. These functions encode the VAE size
snapping, the model-swap VRAM release, the 2 MP reference cap and the Wan
frame-count arithmetic, and a snapshot is the only thing that notices when
one of them quietly changes.

zip_outputs() returns `(path_or_None, message)` rather than raising, so a
caller with nothing to zip has a sentence to show.
"""

import io
import random
import re
import time
import uuid
import zipfile
from pathlib import Path

from PIL import Image

from ember.licensing import catalog
from ember.generation import eta
from ember.web import gallery_index
from ember.generation import queue as jobqueue
from ember.licensing import presets
from ember.licensing import prompts
from ember.generation import recipes
from ember.comfy.client import ComfyUIError, client, model_signature, on_output, wan_client
from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.features import Key
from ember.logs import log
from ember.settings import (
    COMFY_LOG,
    COMFY_PORT,
    FREE_ON_SWAP,
    OUTPUT_DIR,
    WAN_COMFY_LOG,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
)
from ember.pipelines.krea2.constants import (
    DEFAULT_RESOLUTION,
    RESOLUTION_PRESETS,
)
from ember.pipelines.minimax.constants import MINIMAX_FPS
from ember.pipelines.wan.constants import (
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_FPS,
    WAN_MODE_DEFAULTS,
    WAN_RESOLUTIONS,
)
from ember.pipelines.common import (
    edit_lora_available,
    feature_lora,
    lora_file_available,
    model_defaults,
    model_file_available,
    resolve_model,
)
from ember.pipelines.krea2.workflow import (
    build_edit_workflow,
    build_workflow,
)
from ember.pipelines.krea2_v2.workflow import (
    build_v2_workflow,
    default_lora_slots as v2_default_lora_slots,
    model_defaults as v2_model_defaults,
    resolve_size as v2_resolve_size,
    status as v2_status,
    turbo_lora_available as v2_turbo_lora_available,
    turbo_lora_slot as v2_turbo_lora_slot,
)
from ember.pipelines.krea2_v2_edit.workflow import (
    build_v2_edit_workflow,
    fit_size as v2_edit_fit_size,
    status as v2_edit_status,
)
from ember.pipelines.minimax.workflow import (
    aspect_size as minimax_aspect_size,
    build_minimax_video_workflow,
    crop_to_canvas as minimax_crop_to_canvas,
    frames_for as minimax_frames,
    matches_image as minimax_matches_image,
    minimax_missing,
    minimax_models_available,
    resolve_size as minimax_resolve_size,
)
from ember.pipelines.wan.workflow import (
    build_wan_5b_workflow,
    build_wan_i2v_workflow,
    wan_5b_available,
    wan_lightning_available,
    wan_models_available,
)

# Number of LoRA slots the Krea2, Krea2 Edit and MiniMax tabs render, all
# blank. The UI rows, the handlers and the workflow chain are all driven
# from this, so changing it here is the whole change. (The V2 tabs have
# one row per LoRA in their feature's list instead — see
# ember.pipelines.krea2_v2.workflow.default_lora_slots.)
MAX_LORA_SLOTS = 8
# Slots past this stay in a collapsed accordion so a tall stack does not
# eat the whole column. Set it >= MAX_LORA_SLOTS to show every slot.
VISIBLE_LORA_SLOTS = 3

# The four tabs whose models and LoRAs come from the catalogue, by the
# feature key the catalogue files their lists under. Each handler below
# reads its own, so Krea2 and Krea2 Edit (and the two V2 tabs) can offer
# different lists without a second code path.
KREA_T2I = str(Key.KREA_T2I)
KREA_EDIT = str(Key.KREA_EDIT)
KREA_V2_T2I = str(Key.KREA_V2_T2I)
KREA_V2_EDIT = str(Key.KREA_V2_EDIT)
# The MiniMax tabs take LoRAs from the catalogue too, but no model: their
# weights are fixed in pipelines/minimax/constants.py, so only the LoRA
# list is read.
MINIMAX_I2V = str(Key.MINIMAX_I2V)
MINIMAX_T2V = str(Key.MINIMAX_T2V)

# Steps/CFG a Krea form starts on when its feature lists no model at all —
# an empty catalogue, which the tab reports and cannot run anyway. Only
# here so the form still has numbers to draw; a model record always wins.
_NO_MODEL_DEFAULTS = {"steps": 10, "cfg": 1.0}


# ── The catalogue, as the forms see it ───────────────────────────────────────
# Read late, never at import: the catalogue is loaded once by main.py after
# the licence check, and a list captured at import would be whatever an
# earlier import happened to see. Values are ids; labels are names.

def model_choices(feature) -> list:
    """The Model dropdown's values: the feature's model ids, in order."""
    return [model.id for model in catalog.feature_models(feature)]


def model_labels(feature) -> dict:
    """{model id: name} — what the dropdown shows for each value."""
    return {model.id: model.name for model in catalog.feature_models(feature)}


def default_model(feature) -> str:
    """The feature's first model id — its default — or "" when it has none."""
    choices = model_choices(feature)
    return choices[0] if choices else ""


def model_settings(feature) -> dict:
    """{steps, cfg} of the feature's default model, for the form defaults."""
    models = catalog.feature_models(feature)
    if not models:
        return dict(_NO_MODEL_DEFAULTS)
    steps, cfg = model_defaults(models[0])
    return {"steps": steps, "cfg": cfg}


def lora_choices(feature) -> list:
    """A LoRA dropdown's values: "None" and the feature's LoRA ids.

    Exactly the feature's list. Files on this disk that the catalogue does
    not list are not offered, and a listed LoRA whose file has not
    downloaded is — it is labelled as missing (lora_labels) and skipped,
    with a warning, if a run switches it on.
    """
    return [catalog.NONE] + [lora.id for lora in catalog.feature_loras(feature)]


def lora_labels(feature) -> dict:
    """{lora id: name} for the LoRA dropdowns, "None" included."""
    labels = {catalog.NONE: catalog.NONE}
    for lora in catalog.feature_loras(feature):
        labels[lora.id] = (lora.name if lora_file_available(lora)
                           else f"{lora.name} (not downloaded)")
    return labels


def stored_lora(value):
    """A LoRA slot's form value as the settings blob stores it.

    The form spells an empty slot "None" (a select needs a string); the
    blob stores null, so a preset never carries a magic string the licence
    server would have to know. tabschema.settings() calls this too, which
    is what keeps the two blobs byte-identical.
    """
    return None if value in (None, catalog.NONE) else value

# The queue's lanes, and the ComfyUI instance each one is stopped through.
# One worker per lane, which is the same "one at a time" rule the
# concurrency_id on every click used to state — and the video lane is
# separate for the same reason it had its own concurrency_id: when
# KREA2_WAN_PARALLEL gives it a ComfyUI of its own, a five-minute render
# must not sit in front of a picture. Registered here, at import, because
# the workers only park on a condition variable until something is
# submitted; nothing touches ComfyUI before the first job runs.
COMFY_LANE = "comfy"
WAN_LANE = "wan" if WAN_PARALLEL else COMFY_LANE
jobqueue.register_lane(COMFY_LANE, client.interrupt)
if WAN_LANE != COMFY_LANE:
    jobqueue.register_lane(WAN_LANE, wan_client.interrupt)
def _snap(value, lo: int = 512, hi: int = 2048) -> int:
    """Clamp to [lo, hi] and round to a multiple of 16 (Krea 2 requirement)."""
    return max(lo, min(hi, int(round(int(value) / 16)) * 16))


def parse_resolution(value) -> tuple:
    """Accept a preset label or free-form 'WxH' / 'W×H' text."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return _snap(value[0]), _snap(value[1])
    text = str(value or "").strip()
    if text in RESOLUTION_PRESETS:
        return RESOLUTION_PRESETS[text]
    match = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)
    if match:
        return _snap(match.group(1)), _snap(match.group(2))
    compact = text.lower().replace(" ", "")
    for label, wh in RESOLUTION_PRESETS.items():
        if compact and compact.split("(")[0] in label.lower().replace(" ", ""):
            return wh
    return RESOLUTION_PRESETS[DEFAULT_RESOLUTION]


# Base weights each ComfyUI instance currently has loaded, keyed by its
# base URL. Only ever read and written from _release_on_swap below.
_LAST_MODEL_SIG = {}


def _release_on_swap(comfy_client, workflow) -> str:
    """Unload the previous models when this graph needs different ones.

    Without this, a swap has a window where both model sets are resident —
    ComfyUI holds the old ones until memory pressure evicts them — and on
    a pod with three Krea UNets in rotation that window is where the
    process gets OOM-killed. Freeing at the boundary makes the peak one
    model set instead of two.

    Costs nothing on a repeat job (same signature, no call) and costs only
    the reload on a genuine swap, which was going to happen regardless.
    Returns a status note, or "" when nothing was done.
    """
    if not FREE_ON_SWAP:
        return ""
    signature = model_signature(workflow)
    previous = _LAST_MODEL_SIG.get(comfy_client.base)
    # Record first: a failed free must not make the next job think the old
    # models are still the loaded ones.
    _LAST_MODEL_SIG[comfy_client.base] = signature
    if previous is None or previous == signature:
        return ""
    log.info("Model swap detected — unloading the previous models first")
    comfy_client.free_models()
    return "♻️ Different models than the last job — unloading the old ones"


def _run_tag() -> str:
    """A short random token, so no two jobs can ever share a filename.

    ComfyUI does not remember how many files it has written. It derives
    the next counter by listing the output folder and taking the highest
    one it finds, plus one (`folder_paths.get_save_image_path`). Delete
    the three newest pictures and that maximum drops by three, so the
    next three generations are written under the *exact* names of the
    files that were just deleted.

    Nothing downstream survives that. `/media/Krea2_00042_.png` is the
    same URL for the old file and the new one, and api._send serves it
    with `max-age=86400`, so a browser that saw the deleted picture keeps
    showing it in place of the new one for a day. The recipe is filed
    under the same relative path too, so the caption ends up describing a
    picture that is not the one on screen.

    A token in the prefix takes the counter out of the argument: every
    job gets a namespace nothing has ever written to, the counter starts
    at 1 inside it, and the name is unique whatever has been deleted.
    Eight hex characters is 4 billion, drawn per job rather than per
    process, which for a folder of a few thousand is not worth a
    collision check.

    A function, and module-level, so scripts/golden.py can stub it to a
    fixed value the way it already stubs client.upload_image — for
    exactly this reason: it lands *in* the workflow, and a snapshot that
    changed on every run would freeze nothing.
    """
    return uuid.uuid4().hex[:8]


def _run_jobs(jobs, builder=build_workflow, prefix="Krea2"):
    """Shared executor: yields (gallery_paths, status_text) as work progresses."""
    images = []
    total = len(jobs)
    alive, note = comfy_ensure_alive()
    if not alive:
        yield images, note
        return
    if note:
        eta.forget(client.base)            # restarted: nothing is loaded
        yield images, note
    timer = eta.tracker(getattr(builder, "__name__", prefix), total,
                        client.base)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        job_prefix = prefix
        if job["loras"]:
            job_prefix += "_" + Path(job["loras"][0][0]).stem
        # Last, so the readable part of the name — the tab, and which LoRA
        # it ran with — still comes first in a directory listing.
        job_prefix += "_" + _run_tag()
        workflow = builder(filename_prefix=job_prefix, **job)
        # Started before the swap, so the unload it may wait on counts as
        # part of this picture's time — it is, from where the customer sits.
        signature = model_signature(workflow)
        timer.start(workflow, signature, eta.is_cold(client.base, signature))
        swap_note = _release_on_swap(client, workflow)
        if swap_note:
            yield images, f"{swap_note} — job {label} will be slower"
        size = f", {job['width']}×{job['height']}" if "width" in job else ""
        # The seed this particular picture runs on, which is the one thing
        # the controls cannot be read back for: a batch walks consecutive
        # seeds, and with "🎲 Random seed" ticked none of them is the
        # number in the box. Stamped per job, so a batch of four files
        # four recipes that differ in exactly the field that matters.
        recipes.stamp(seed=job["seed"])
        yield images, f"⏳ Job {label} — queued (seed {job['seed']}{size})"
        try:
            for event in client.run(workflow):
                timer.event(event)
                if event["type"] == "progress" and event["total"]:
                    yield images, f"⏳ Job {label} — step {event['step']}/{event['total']}"
                elif event["type"] == "done":
                    images.extend(event["images"])
                    yield images, f"✅ Job {label} finished"
        except ComfyUIError as exc:
            yield images, f"❌ Job {label} failed: {exc}"
            return
    yield images, f"✅ All {total} job(s) done"


def _resolve_lora_slots(feature, slots) -> tuple[list, list]:
    """Flat (enabled, lora id, weight) × N UI values → (file, strength) pairs.

    Every Krea tab's stack, the V2 ones included: a row contributes only
    while its checkbox is on, and order is preserved because LoRA
    application is not commutative. Switching a row off keeps its id in
    the dropdown instead of throwing it away, which is the whole reason
    the column exists.

    Values are exact ids from the feature's list, and this is the one
    place an id becomes a file name. A ticked row whose id the feature
    does not offer, or whose file has not downloaded, is skipped rather
    than failing the run — logged, and returned as the second value so the
    handler can say so in its status line. Slots left at "None" drop out
    even when ticked.

    `slots` is the handler's whole varargs tail, so the slot count lives
    only in the schema and adding a row needs no change here.
    """
    loras, skipped = [], []
    for enabled, lora_id, weight in zip(slots[::3], slots[1::3], slots[2::3]):
        if not enabled or lora_id in (None, "", catalog.NONE):
            continue
        lora = feature_lora(feature, lora_id)
        if lora is None:
            log.warning("LoRA %r is not offered on %s — skipping it",
                        lora_id, feature)
            skipped.append(f"`{lora_id}` (not offered on this tab)")
            continue
        if not lora_file_available(lora):
            log.warning("LoRA %s (%s) has not downloaded — skipping it",
                        lora.id, lora.file)
            skipped.append(f"`{lora.name}` (not downloaded)")
            continue
        loras.append((lora.file, float(weight)))
    return loras, skipped


def _skipped_note(skipped) -> str:
    """A status-line prefix naming the LoRAs a run had to leave out."""
    if not skipped:
        return ""
    return "⚠️ Skipped LoRA: " + ", ".join(skipped) + "\n"


def _enabled_lora_ids(slots) -> list:
    """The ids of the rows a run switched on — for the V2 status notes."""
    return [lora_id for enabled, lora_id in zip(slots[::3], slots[1::3])
            if enabled and lora_id not in (None, "", catalog.NONE)]


def _check_model(feature, model_id):
    """Resolve the dropdown value; return (model, error_message_or_None)."""
    model = resolve_model(feature, model_id)
    if model is None:
        return None, ("❌ The model catalogue lists no model for this tab — "
                      "restart the app once the licence server can be "
                      "reached.")
    if not model_file_available(model):
        return model, (f"❌ Model “{model.name}” is not downloaded yet — "
                       "restart the app so the download step can fetch it.")
    return model, None


def _krea_settings(seed, randomize, steps, cfg, resolution, sampler, model,
                   batch_count, lora_slots) -> dict:
    """The Krea 2 tab's controls as the prompt library stores them.

    Deliberately the *UI* values, not the resolved job dict: what goes in
    here comes back out into these same controls on another pod, so the
    model and LoRA *ids* are the useful thing to keep and the file names
    they resolve to are not. Mirrors the generate_single signature — when
    that gains a control, this is the other half of the change.

    LoRA rows are [enabled, lora id or None, weight] — the shape every
    Krea tab stores, and the one the licence server checks a preset
    against. An empty slot is None here and "None" in the form; see
    stored_lora.
    """
    return {
        "model": model,
        "steps": int(steps),
        "cfg": float(cfg),
        "resolution": resolution,
        "sampler": sampler,
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def generate_single(prompt, negative, seed, randomize, steps, cfg, resolution,
                    sampler, model, batch_count, publish, publish_title,
                    save_preset, preset_name, *lora_slots):
    """First tab: run batch_count jobs on sequential seeds."""
    entry, error = _check_model(KREA_T2I, model)
    if error:
        yield [], error, 0
        return
    settings = _krea_settings(seed, randomize, steps, cfg, resolution,
                              sampler, model, batch_count, lora_slots)
    # After the guard, before the work: a click that could never run does
    # not belong in the library, but one that fails half way through a
    # batch still had a recipe worth keeping. Returns instantly and
    # cannot raise, and decides for itself whether this pod captures
    # automatically or only on `publish` — see prompts.record.
    prompts.record(
        prompts.TAB_KREA2, prompt, negative or "", settings,
        publish=publish, title=publish_title,
    )
    notice = _save_preset(presets.TAB_KREA2, save_preset, preset_name, settings)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = parse_resolution(resolution)
    loras, skipped = _resolve_lora_slots(KREA_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width, "height": height,
        "sampler": sampler, "loras": loras, "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs):
        yield images, notice + status, base_seed


def _save_preset(tab, save, name, settings) -> str:
    """Save these settings as a preset if asked to; return a status prefix.

    The counterpart of `publish` for the settings half: one tickbox, read
    on the click that generates, admin-only and enforced on the server
    against the licence document.

    Every tick is a **new** preset. Loading one, adjusting it and saving is
    the ordinary gesture, and it must not destroy the preset it started
    from — so the server steps a repeated name to "… (2)" rather than
    overwriting, and an empty name is stamped with the time rather than
    refused. See presets.save; both decisions are why the message below
    names the preset that was actually written.

    It reports, where publishing deliberately does not. Publishing is
    invisible by design — the customer is never told prompts are saved, so
    nothing about it may appear in the UI — but a preset is a thing an
    admin is *waiting on*, and "did it save?" is a fair question. The
    answer rides on the status box as a first line, so it costs no
    component and is gone on the next run.

    Never raises and never blocks the generation: presets.save returns
    (ok, message) for everything that can go wrong, including a licence
    the server refuses.

    It also tells the job queue, because this runs on a worker thread now
    rather than on the click that asked for it — so the dropdown listing
    the presets has no other way to learn that what it is showing just
    went stale. See jobqueue.note_preset_saved and _queue_tick.
    """
    if not save:
        return ""
    ok, message = presets.save(tab, name, settings)
    (log.info if ok else log.warning)("Preset: %s", message)
    if ok:
        jobqueue.note_preset_saved(tab)
    return ("✅ " if ok else "⚠️ ") + message + "\n"


# ── Krea 2 V2 (Krea2 advanced graph) ─────────────────────────────────────────────
# This tab is deliberately self-contained: its own VAE, LoRA rows, sampler
# and defaults. Its models and LoRAs are its own feature's lists in the
# catalogue (krea_v2_t2i, and krea_v2_edit for the Edit tab), so tuning
# the Krea2 tab's lists never moves it.

def v2_lora_slots(feature) -> list:
    """The V2 rows for one of the two V2 features: (enabled, id, strength)."""
    return v2_default_lora_slots(feature)


def v2_turbo_slot(feature, model_id=None):
    """Index of the row a model's recipe switches on, for that feature.

    `model_id` defaults to the feature's raw model — the first one whose
    record names a turbo LoRA — which is the only kind that has one.
    """
    models = catalog.feature_models(feature)
    model = (resolve_model(feature, model_id) if model_id else
             next((m for m in models if m.turbo_lora), None))
    return v2_turbo_lora_slot(feature, model)


def _v2_model_info_text(entry) -> str:
    """One-line summary shown under the V2 Model dropdown.

    The strength quoted is the model record's own turbo_lora strength —
    what its recipe switches the row on at — not the LoRA's default.
    """
    steps, cfg, turbo_lora = v2_model_defaults(entry)
    info = (f"**{entry.variant.title()}** · "
            f"defaults: {steps} steps, CFG {cfg:g} · Turbo LoRA "
            + ("**on** at " f"{float(entry.turbo_lora['strength']):g}"
               if turbo_lora else "off"))
    if entry.trigger:
        info += " · trigger words are inserted into the prompt (editable)"
    if not model_file_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    if turbo_lora and not v2_turbo_lora_available(entry):
        info += (" · ⚠️ **the Turbo LoRA this variant needs has not "
                 "downloaded** — raw output will be undistilled")
    return info

def generate_v2(prompt, negative, seed, randomize, model, aspect, megapixels,
                multiple, eta, sampler_name, scheduler, steps, denoise, cfg,
                sampler_mode, bongmath, variance_preset, fine_tune_variance,
                variance_model_type, variance_schedule, cutoff_step,
                total_steps, cutoff_strength, shift_strength, sharpen,
                film_grain, batch_count, publish, publish_title,
                save_preset, preset_name, *lora_slots):
    """Krea 2 V2 tab: the Krea2 advanced turbo/raw text-to-image graph."""
    ready, message = v2_status(KREA_V2_T2I, _enabled_lora_ids(lora_slots))
    if not ready:
        yield [], message, 0
        return
    entry, error = _check_model(KREA_V2_T2I, model)
    if error:
        yield [], error, 0
        return
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = v2_resolve_size(aspect, megapixels, multiple)
    loras, skipped = _resolve_lora_slots(KREA_V2_T2I, lora_slots)
    sampler_settings = {
        "eta": float(eta), "sampler_name": sampler_name,
        "scheduler": scheduler, "steps": int(steps),
        "denoise": float(denoise), "cfg": float(cfg),
        "sampler_mode": sampler_mode, "bongmath": bool(bongmath),
    }
    variance_settings = {
        "variance_preset": variance_preset,
        "fine_tune_variance": int(fine_tune_variance),
        "model_type": variance_model_type,
        "variance_schedule": variance_schedule,
        "cutoff_step": int(cutoff_step), "total_steps": int(total_steps),
        "cutoff_strength": float(cutoff_strength),
        "shift_strength": int(shift_strength),
    }
    # Both dicts above are already exactly the shape the library wants, so
    # the settings blob reuses them rather than rebuilding them — the size
    # is stored as the aspect/megapixels/multiple the controls hold, not
    # the width/height they resolve to, so a loaded prompt puts the three
    # sliders back where they were. See _krea_settings.
    #
    # One blob, two readers: the prompt library stores it alongside the
    # prompt text, a preset stores it *instead* of the prompt text. Keeping
    # them the same shape is what lets one set of "load this into the
    # controls" code serve both.
    settings = {
        "model": model,
        "aspect": aspect,
        "megapixels": float(megapixels),
        "multiple": int(multiple),
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "sampler": sampler_settings,
        "variance": variance_settings,
        "sharpen": bool(sharpen),
        "film_grain": bool(film_grain),
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }
    prompts.record(prompts.TAB_KREA2_V2, prompt, negative or "", settings,
                   publish=publish, title=publish_title)
    notice = _save_preset(presets.TAB_KREA2_V2, save_preset, preset_name,
                          settings)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "width": width, "height": height, "loras": loras,
        "model": entry,
        "sampler_settings": sampler_settings,
        "variance_settings": variance_settings,
        "sharpen": bool(sharpen), "film_grain": bool(film_grain),
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_v2_workflow,
                                    prefix="Krea2V2"):
        yield images, notice + status, base_seed

def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _fit_edit_size(w: int, h: int, max_pixels: int = 2_000_000) -> tuple:
    """Edit-target size: keep aspect, cap at max_pixels, never upscale, /16.

    The Identity Edit LoRA bleeds/duplicates content above ~2 MP, so the
    cap is by area rather than by the long side.

    The v1.2 nodes' FIT geometry means the *aspect ratio* no longer has to
    match — a 3:2 source rendered at 1:1 is fitted rather than stretched.
    The area cap is what is left, and it applies to the source as much as
    to the output: this tab derives one from the other, and a 12 MP phone
    photo VAE-encoded as a reference is a needless 12 MP of VRAM.
    """
    scale = min(1.0, (max_pixels / (w * h)) ** 0.5)
    return (max(64, int(w * scale) // 16 * 16),
            max(64, int(h * scale) // 16 * 16))


def generate_edit(image, use_image2, image2, prompt, negative, seed,
                  randomize, steps, cfg, sampler, grounding, ref_boost,
                  ref_boost_a, model, batch_count, *lora_slots):
    """Edit tab: instruction-based editing. The model sees the source image
    (Identity Edit LoRA dual conditioning), so the prompt describes the
    change to make — no mask, no denoise tuning.

    With the second reference enabled this becomes a two-input edit: the
    first image is the scene (it sets the output size), the second is the
    subject to place into it. Off, it behaves exactly as it always has.
    """
    if image is None:
        yield [], "❌ Upload an image first.", 0
        return
    if use_image2 and image2 is None:
        yield [], ("❌ Second reference is enabled but empty — upload it, "
                   "or switch the toggle off."), 0
        return
    entry, error = _check_model(KREA_EDIT, model)
    if error:
        yield [], error, 0
        return
    if not edit_lora_available():
        yield [], ("❌ The Identity Edit LoRA is not downloaded yet — "
                   "restart the app so the download step can fetch it."), 0
        return
    if not str(prompt or "").strip():
        yield [], "❌ Describe the change (e.g. “make the jacket red”).", 0
        return
    image = image.convert("RGB")
    width, height = _fit_edit_size(*image.size)
    if (width, height) != image.size:
        image = image.resize((width, height), Image.LANCZOS)
    # The subject keeps its OWN aspect ratio — the patch node resamples
    # every reference onto the target grid in pixel space, so it does not
    # have to match the scene. It is still capped to the same 2 MP budget,
    # because nothing else stops a 24 MP phone photo from being handed
    # whole to the VAEEncode behind source_latent_b.
    if use_image2:
        image2 = image2.convert("RGB")
        b_size = _fit_edit_size(*image2.size)
        if b_size != image2.size:
            image2 = image2.resize(b_size, Image.LANCZOS)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image), f"edit_{tag}.png")
        image2_name = None
        if use_image2:
            image2_name = client.upload_image(_png_bytes(image2),
                                              f"edit_{tag}_b.png")
    except Exception as exc:
        yield [], f"❌ Uploading the image to ComfyUI failed: {exc}", base_seed
        return
    loras, skipped = _resolve_lora_slots(KREA_EDIT, lora_slots)
    note = _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "image_name": image_name,
        "image2_name": image2_name,
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "loras": loras,
        "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_edit_workflow,
                                    prefix="Krea2Edit"):
        yield images, note + status, base_seed


def generate_v2_edit(image, use_image2, image2, prompt, negative, seed,
                     randomize, model,
                     grounding, ref_boost, ref_boost_a, fit_mode, eta,
                     sampler_name,
                     scheduler, steps, cfg, sampler_mode, bongmath,
                     variance_preset, fine_tune_variance,
                     variance_model_type, variance_schedule, cutoff_step,
                     total_steps, cutoff_strength, shift_strength,
                     batch_count, *lora_slots):
    """Krea 2 V2 Edit tab: instruction editing on the V2 pipeline.

    Same contract as generate_edit — upload an image, describe the change,
    the size comes from the source — over the V2 model, VAE, LoRA stack,
    ClownsharKSampler and Smart Seed Variance. There is no Denoise control
    because the source reaches the model through conditioning rather than
    the starting latent, so it is pinned at 1.0 in the builder.
    """
    if image is None:
        yield [], "❌ Upload an image first.", 0
        return
    if use_image2 and image2 is None:
        yield [], ("❌ Second reference is enabled but empty — upload it, "
                   "or switch the toggle off."), 0
        return
    ready, message = v2_edit_status(KREA_V2_EDIT,
                                    _enabled_lora_ids(lora_slots))
    if not ready:
        yield [], message, 0
        return
    entry, error = _check_model(KREA_V2_EDIT, model)
    if error:
        yield [], error, 0
        return
    if not str(prompt or "").strip():
        yield [], "❌ Describe the change (e.g. “make the jacket red”).", 0
        return
    image = image.convert("RGB")
    width, height = v2_edit_fit_size(*image.size)
    if (width, height) != image.size:
        image = image.resize((width, height), Image.LANCZOS)
    # Same rule as the v1 Edit tab: the subject keeps its own aspect ratio
    # (the patch node refits it in pixel space) but not its full size.
    if use_image2:
        image2 = image2.convert("RGB")
        b_size = v2_edit_fit_size(*image2.size)
        if b_size != image2.size:
            image2 = image2.resize(b_size, Image.LANCZOS)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image),
                                         f"v2edit_{tag}.png")
        image2_name = None
        if use_image2:
            image2_name = client.upload_image(_png_bytes(image2),
                                              f"v2edit_{tag}_b.png")
    except Exception as exc:
        yield [], f"❌ Uploading the image to ComfyUI failed: {exc}", base_seed
        return
    sampler_settings = {
        "eta": float(eta), "sampler_name": sampler_name,
        "scheduler": scheduler, "steps": int(steps), "cfg": float(cfg),
        "sampler_mode": sampler_mode, "bongmath": bool(bongmath),
    }
    variance_settings = {
        "variance_preset": variance_preset,
        "fine_tune_variance": int(fine_tune_variance),
        "model_type": variance_model_type,
        "variance_schedule": variance_schedule,
        "cutoff_step": int(cutoff_step), "total_steps": int(total_steps),
        "cutoff_strength": float(cutoff_strength),
        "shift_strength": int(shift_strength),
    }
    loras, skipped = _resolve_lora_slots(KREA_V2_EDIT, lora_slots)
    note = _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "width": width, "height": height, "image_name": image_name,
        "image2_name": image2_name,
        "loras": loras,
        "model": entry,
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "fit_mode": fit_mode,
        "sampler_settings": sampler_settings,
        "variance_settings": variance_settings,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_v2_edit_workflow,
                                    prefix="Krea2V2Edit"):
        yield images, note + status, base_seed


def _fit_video_size(w: int, h: int, target_area: int, snap: int = 16) -> tuple:
    """Video size: keep the source aspect ratio at roughly target_area px.

    Sides are snapped to multiples of `snap` (16 for the 14B models, 32
    for the 5B model's higher-compression VAE). Upscaling small sources
    is allowed — Wan renders at the target size regardless — and each
    side is clamped to [256, 1536].
    """
    scale = (target_area / (w * h)) ** 0.5
    return (max(256, min(1536, int(round(w * scale / snap)) * snap)),
            max(256, min(1536, int(round(h * scale / snap)) * snap)))


def _seconds_to_frames(seconds: float, fps: int) -> int:
    """Wan frame counts must be a multiple of 4 plus 1 (e.g. 81 = 5 s at
    16 fps for the 14B models, 121 = 5 s at 24 fps for the 5B)."""
    return max(17, int(round(float(seconds) * fps / 4)) * 4 + 1)


def _run_wan_jobs(jobs, builder=build_wan_i2v_workflow, comfy_client=None):
    """Video executor: yields (all_videos, latest_video, status_text).

    `comfy_client` is the instance the clips run on — wan_client unless a
    caller says otherwise. The MiniMax tabs say otherwise: they run on the
    main instance whatever KREA2_WAN_PARALLEL says, because their int8
    model plus 32B text encoder is ~48 GB of weights and does not fit
    beside a second instance holding VRAM back for Wan. The port and log
    that have to be alive follow the client, since ensure_alive must ask
    the instance the job is actually going to.
    """
    videos = []
    total = len(jobs)
    latest = None
    comfy_client = comfy_client or wan_client
    # Only Wan's own client points at the second instance, and only when
    # that instance exists — without the flag wan_client *is* client.
    on_wan_instance = comfy_client is wan_client and WAN_PARALLEL
    alive, note = comfy_ensure_alive(
        port=WAN_COMFY_PORT if on_wan_instance else COMFY_PORT,
        log_path=WAN_COMFY_LOG if on_wan_instance else COMFY_LOG,
    )
    if not alive:
        yield videos, latest, note
        return
    if note:
        eta.forget(comfy_client.base)      # restarted: nothing is loaded
        yield videos, latest, note
    timer = eta.tracker(getattr(builder, "__name__", "video"), total,
                        comfy_client.base)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        workflow = builder(**job)
        signature = model_signature(workflow)          # see _run_jobs
        timer.start(workflow, signature,
                    eta.is_cold(comfy_client.base, signature))
        swap_note = _release_on_swap(comfy_client, workflow)
        if swap_note:
            yield videos, latest, f"{swap_note} — video {label} will be slower"
        recipes.stamp(seed=job["seed"])       # see _run_jobs
        yield videos, latest, (
            f"⏳ Video {label} — queued (seed {job['seed']}, "
            f"{job['width']}×{job['height']}, {job['length']} frames)"
        )
        try:
            # Raw 720p renders can take the better part of an hour on an
            # A40, so the video timeout is far above the image one.
            for event in comfy_client.run(workflow, timeout=7200):
                timer.event(event)
                if event["type"] == "progress" and event["total"]:
                    yield videos, latest, (
                        f"⏳ Video {label} — step "
                        f"{event['step']}/{event['total']}"
                    )
                elif event["type"] == "done":
                    videos.extend(event["images"])
                    latest = videos[-1] if videos else None
                    yield videos, latest, f"✅ Video {label} finished"
        except ComfyUIError as exc:
            yield videos, latest, f"❌ Video {label} failed: {exc}"
            return
    yield videos, latest, f"✅ All {total} video(s) done"


def _is_wan_5b(model) -> bool:
    return "5b" in str(model).lower()


def generate_wan_video(image, prompt, negative, model, mode, seed, randomize,
                       steps, cfg, resolution, seconds, sampler, batch_count):
    """Video tab: animate an uploaded image with Wan 2.2 (14B I2V or 5B TI2V)."""
    if image is None:
        yield [], None, "❌ Upload an image first.", 0
        return
    use_5b = _is_wan_5b(model)
    if use_5b and not wan_5b_available():
        yield [], None, ("❌ The Wan 2.2 5B model is not downloaded yet — "
                         "restart the app so the download step can fetch "
                         "it, or switch Model to 14B."), 0
        return
    if not use_5b and not wan_models_available():
        yield [], None, ("❌ The Wan 2.2 14B models are not downloaded yet — "
                         "restart the app so the download step can fetch "
                         "them, or switch Model to 5B."), 0
        return
    turbo = not use_5b and str(mode).lower().startswith("turbo")
    if turbo and not wan_lightning_available():
        yield [], None, ("❌ The Lightning speed LoRAs are missing — switch "
                         "Mode to Raw, or restart the app to download "
                         "them."), 0
        return
    if use_5b:
        fps, snap = WAN_5B_FPS, 32
        shift = WAN_5B_DEFAULTS["shift"]
        builder = build_wan_5b_workflow
        wan_prefix = "wan/Wan22TI2V5B"
    else:
        fps, snap = WAN_FPS, 16
        shift = WAN_MODE_DEFAULTS["turbo" if turbo else "raw"]["shift"]
        builder = build_wan_i2v_workflow
        wan_prefix = "wan/Wan22I2V"
    image = image.convert("RGB")
    width, height = _fit_video_size(*image.size, WAN_RESOLUTIONS[resolution],
                                    snap=snap)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = wan_client.upload_image(_png_bytes(image),
                                             f"wan_{tag}.png")
    except Exception as exc:
        yield [], None, f"❌ Uploading the image to ComfyUI failed: {exc}", 0
        return
    jobs = [{
        "prompt": prompt or "", "negative": negative or "",
        "seed": base_seed + i, "steps": int(steps), "cfg": float(cfg),
        "width": width, "height": height,
        "length": _seconds_to_frames(seconds, fps), "fps": fps,
        "sampler": sampler, "shift": shift, "image_name": image_name,
        # Spelled out here rather than left to the builder's default so
        # the tag can go on it — see _run_tag. Videos land in wan/, and
        # that subfolder has its own counter to be knocked backwards.
        "filename_prefix": f"{wan_prefix}_{_run_tag()}",
        **({} if use_5b else {"lightning": turbo}),
    } for i in range(int(batch_count))]
    for videos, latest, status in _run_wan_jobs(jobs, builder=builder):
        yield videos, latest, status, base_seed

def _minimax_note() -> str | None:
    """Why MiniMax cannot run yet, or None when every weight is on disk."""
    if minimax_models_available():
        return None
    return ("❌ The MiniMax H3 weights are not downloaded yet (missing: %s) — "
            "restart the app so the download step can fetch them."
            % ", ".join(minimax_missing()))


def _minimax_jobs(*, prompt, base_seed, steps, width, height, seconds,
                  sampler, batch_count, prefix, loras, image_name=None) -> list:
    """The per-clip job dicts both MiniMax tabs hand to _run_wan_jobs.

    One function because the two tabs differ in exactly one key —
    `image_name`, which the text tab leaves None so the builder leaves
    LoadImage out — and everything else about a clip is decided the same
    way on both.
    """
    return [{
        "prompt": prompt or "", "seed": base_seed + i, "steps": int(steps),
        "width": width, "height": height,
        "length": minimax_frames(seconds), "fps": MINIMAX_FPS,
        "sampler": sampler, "image_name": image_name, "loras": loras,
        # Clips land in minimax/, a subfolder with a counter of its own to
        # be knocked backwards — see _run_tag.
        "filename_prefix": f"minimax/{prefix}_{_run_tag()}",
    } for i in range(int(batch_count))]


def generate_minimax_video(image, prompt, seed, randomize, steps, resolution,
                           seconds, sampler, batch_count, *lora_slots):
    """MiniMax I2V tab: animate an uploaded image into a clip with sound."""
    if image is None:
        yield [], None, "❌ Upload an image first.", 0
        return
    note = _minimax_note()
    if note:
        yield [], None, note, 0
        return
    image = image.convert("RGB")
    width, height = minimax_resolve_size(*image.size, resolution)
    if minimax_matches_image(resolution):
        # Match image trims to the canvas's shape first: the node stretches
        # the first frame to fit, and a canvas in 32s is rarely the
        # picture's exact shape. Standard and native send it whole.
        image = minimax_crop_to_canvas(image, width, height)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        # The main instance, whatever KREA2_WAN_PARALLEL says — see
        # _run_wan_jobs on why MiniMax never rides the Wan lane.
        image_name = client.upload_image(_png_bytes(image),
                                         f"minimax_{tag}.png")
    except Exception as exc:
        yield [], None, f"❌ Uploading the image to ComfyUI failed: {exc}", 0
        return
    loras, skipped = _resolve_lora_slots(MINIMAX_I2V, lora_slots)
    notice = _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, batch_count=batch_count,
                         prefix="MiniMaxI2V", loras=loras,
                         image_name=image_name)
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed


def generate_minimax_t2v(prompt, aspect, seed, randomize, steps, resolution,
                         seconds, sampler, batch_count, *lora_slots):
    """MiniMax T2V tab: a clip with sound from the prompt alone."""
    if not (prompt or "").strip():
        yield [], None, ("❌ Write a prompt first — there is no image for "
                         "this tab to go on."), 0
        return
    note = _minimax_note()
    if note:
        yield [], None, note, 0
        return
    width, height = minimax_aspect_size(aspect, resolution)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    loras, skipped = _resolve_lora_slots(MINIMAX_T2V, lora_slots)
    notice = _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, batch_count=batch_count,
                         prefix="MiniMaxT2V", loras=loras)
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed


# Every finished prompt tells the index what it wrote, which both keeps the
# listing correct without a rescan and is what queues the new files'
# thumbnails. Registered against the client rather than the two separate
# places that consume its "done" event (_run_jobs, _run_wan_jobs), so a
# third executor gets this for free.
on_output(gallery_index.note_new)
# And the same for the recipe behind them, for the same reason: one
# producer of the "done" event, so a third executor gets this free too.
on_output(recipes.note_output)


def list_output_images() -> list[str]:
    """Every generated image and video in OUTPUT_DIR, newest first."""
    return gallery_index.list_media()
def zip_outputs():
    """Bundle all generated media into one zip (the pod disk is ephemeral).

    Returns (path_or_None, message). The path is None when there is
    nothing to zip, which is what the caller renders as "hide the
    download panel" rather than showing an empty drop zone on every visit
    for the sake of the one that asks for a zip.
    """
    images = list_output_images()
    if not images:
        return None, "No images to zip yet."
    # A fresh name per zip, and the previous one deleted. OUTPUT_DIR is
    # served static (see the set_static_paths call further down), which
    # assumes a path's contents never change — reusing "all_outputs.zip"
    # would let a browser hand the user the *previous* zip from its cache.
    # The bare "all_outputs.zip" in the glob is the pre-timestamp name, so a
    # pod that has been running since before this change loses its copy too
    # rather than leaving a stale several-GB file on an ephemeral disk.
    for stale in OUTPUT_DIR.glob("all_outputs*.zip"):
        stale.unlink(missing_ok=True)
    # PNGs/MP4s are already compressed — store instead of deflating (faster).
    zip_path = OUTPUT_DIR / f"all_outputs_{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for img in images:
            zf.write(img, Path(img).relative_to(OUTPUT_DIR))
    size_mb = zip_path.stat().st_size / 1e6
    return (str(zip_path),
            f"📦 Zipped {len(images)} file(s) ({size_mb:.0f} MB)")

def _swap_trigger(text, entry, feature) -> str:
    """Put the selected model's trigger words into the prompt text.

    Any other model's trigger in the same feature's list is removed first,
    so switching models swaps triggers instead of stacking them. The text
    stays fully editable — whatever ends up in the box is used verbatim
    (nothing is added silently at generation time).
    """
    text = text or ""
    for other in catalog.feature_models(feature):
        trig = (other.trigger or "").strip()
        if not trig:
            continue
        idx = text.lower().find(trig.lower())
        if idx >= 0:
            text = text[:idx] + text[idx + len(trig):]
    text = text.strip().strip(",").strip()
    trigger = (entry.trigger or "").strip()
    if trigger:
        return f"{trigger}, {text}" if text else trigger
    return text


def _model_info_text(entry) -> str:
    """One-line summary shown under the Model dropdown."""
    steps, cfg = model_defaults(entry)
    info = (f"**{entry.variant.title()}** · "
            f"defaults: {steps} steps, CFG {cfg:g}")
    if entry.trigger:
        info += " · trigger words are inserted into the prompt (editable)"
    if not model_file_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    return info
