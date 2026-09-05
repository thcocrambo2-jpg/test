"""Every generator behind a Generate button, with no Gradio in sight.

Lifted out of ui.py unchanged. This is the half of that file that decides
what gets rendered — it reads control values, validates them, builds a
workflow dict per job and drives client.run — and none of it ever knew it
was talking to Gradio: every generate_* is a plain generator yielding
plain values, and not one of them returns a gr.update().

That was true before this module existed; splitting it out only makes it
checkable. `import handlers` must not pull Gradio in, which is what lets
the FastAPI adapter (api.py) and the Gradio UI (ui.py) call exactly the
same code while one of the two is being replaced — and what stops the
replacement from quietly re-earning the bugs these functions encode: the
VAE size snapping, the model-swap VRAM release, the 2 MP reference cap,
the ReActor blank-frame detection, the Wan frame-count arithmetic.

The one function that changed shape in the move is zip_outputs(), which
used to return a `gr.update()` for the file component it fills. It now
returns `(path_or_None, message)` and ui.py wraps that back into an
update — the same two facts, minus the dependency.

ui.py keeps working by importing from here; there is no second copy of
anything.
"""

import io
import json
import random
import re
import time
import uuid
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

import gallery_index
import jobqueue
import presets
import prompts
import recipes
from client import ComfyUIError, client, model_signature, on_output, wan_client
from comfy import ensure_alive as comfy_ensure_alive
from config import (
    COMFY_LOG,
    COMFY_PORT,
    DEFAULT_RESOLUTION,
    FREE_ON_SWAP,
    KREA2_MODELS,
    OUTPUT_DIR,
    RESOLUTION_PRESETS,
    SAMPLERS,
    V2_TURBO_LORA_STRENGTH,
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_COMFY_LOG,
    WAN_COMFY_PORT,
    WAN_FPS,
    WAN_MODE_DEFAULTS,
    WAN_PARALLEL,
    WAN_RESOLUTIONS,
    log,
)
from workflow import (
    build_edit_workflow,
    build_inpaint_workflow,
    build_workflow,
    edit_lora_available,
    list_lora_files,
    list_model_names,
    model_defaults,
    model_file_available,
    resolve_lora_name,
    resolve_model_entry,
)
from workflow_flux import (
    build_flux_workflow,
    flux_model_available,
    flux_model_defaults,
    flux_model_names,
    flux_turbo_lora_available,
    list_flux_lora_files,
    resolve_flux_lora,
    resolve_flux_model,
)
from workflow_klein import (
    build_klein_edit_workflow,
    default_lora_slots as klein_default_lora_slots,
    list_lora_files as list_klein_lora_files,
    model_available as klein_model_available,
    model_defaults as klein_model_defaults,
    model_names as klein_model_names,
    resolve_lora as resolve_klein_lora,
    resolve_model as klein_resolve_model,
    resolve_output_size as klein_resolve_output_size,
    status as klein_status,
)
from workflow_krea2_v2 import (
    build_v2_workflow,
    default_lora_slots as v2_default_lora_slots,
    model_available as v2_model_available,
    model_defaults as v2_model_defaults,
    model_names as v2_model_names,
    resolve_model as v2_resolve_model,
    resolve_size as v2_resolve_size,
    status as v2_status,
    turbo_lora_available as v2_turbo_lora_available,
    turbo_lora_slot as v2_turbo_lora_slot,
)
from workflow_krea2_v2_edit import (
    build_v2_edit_workflow,
    fit_size as v2_edit_fit_size,
    status as v2_edit_status,
)
from workflow_reactor import (
    build_faceswap_workflow,
    default_swap_model,
    list_restore_models,
    list_swap_models,
    reactor_status,
)
from workflow_wan import (
    build_wan_5b_workflow,
    build_wan_i2v_workflow,
    wan_5b_available,
    wan_lightning_available,
    wan_models_available,
)

# Number of LoRA slots every stacking tab renders (Single, Edit, Inpaint,
# Flux). The UI rows, the handlers and the workflow chain are all driven
# from this, so changing it here is the whole change.
MAX_LORA_SLOTS = 8
# Slots past this stay in a collapsed accordion so a tall stack does not
# eat the whole column. Set it >= MAX_LORA_SLOTS to show every slot.
VISIBLE_LORA_SLOTS = 3
MODEL_CHOICES = list_model_names()
_d_steps, _d_cfg = model_defaults(resolve_model_entry(None))
DEFAULTS = {"steps": _d_steps, "cfg": _d_cfg}
LORA_CHOICES = ["None"] + list_lora_files()
FLUX_MODEL_CHOICES = flux_model_names()
FLUX_LORA_CHOICES = ["None"] + list_flux_lora_files()
_f_steps, _f_guidance, _ = flux_model_defaults(resolve_flux_model(None))
SWAP_MODEL_CHOICES = list_swap_models() or [default_swap_model()]
RESTORE_CHOICES = list_restore_models()

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


def _normalize_jobs(raw) -> list:
    """Normalize parsed JSON into the job dicts _run_jobs expects."""
    if isinstance(raw, dict) and "prompts" in raw:
        raw = raw["prompts"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(
            "JSON must be a job object, a list of jobs, or {'prompts': [...]}"
        )
    jobs = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"Every job must be a JSON object, got: {item!r}")
        loras = dict(item.get("loras", {}))
        for slot in range(1, MAX_LORA_SLOTS + 1):  # legacy flat keys lora1/lora1_w
            name = item.get(f"lora{slot}")
            if name and str(name).lower() != "none":
                loras[name] = item.get(f"lora{slot}_w", 0.8)
        resolved = []
        for name, weight in list(loras.items())[:MAX_LORA_SLOTS]:
            lora_file = resolve_lora_name(name)
            if lora_file:
                resolved.append((lora_file, float(weight)))
        width, height = parse_resolution(item.get("resolution"))
        sampler = item.get("sampler", SAMPLERS[0])
        # Optional "model" key (registry name, filename or a fragment of
        # either); absent/unknown falls back to the default model, and the
        # model's own step/CFG defaults apply unless the job sets them.
        entry = resolve_model_entry(item.get("model"))
        model_steps, model_cfg = model_defaults(entry)
        jobs.append({
            "prompt": item.get("prompt", ""),
            "negative": item.get("negative", ""),
            "seed": int(item.get("seed", random.randint(0, 2**32 - 1))),
            "steps": int(item.get("steps", model_steps)),
            "cfg": float(item.get("cfg", model_cfg)),
            "width": width,
            "height": height,
            "sampler": sampler if sampler in SAMPLERS else SAMPLERS[0],
            "loras": resolved,
            "unet_file": entry["file"],
        })
    return jobs


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
        yield images, note
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        job_prefix = prefix
        if job["loras"]:
            job_prefix += "_" + Path(job["loras"][0][0]).stem
        # Last, so the readable part of the name — the tab, and which LoRA
        # it ran with — still comes first in a directory listing.
        job_prefix += "_" + _run_tag()
        workflow = builder(filename_prefix=job_prefix, **job)
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
                if event["type"] == "progress" and event["total"]:
                    yield images, f"⏳ Job {label} — step {event['step']}/{event['total']}"
                elif event["type"] == "done":
                    images.extend(event["images"])
                    yield images, f"✅ Job {label} finished"
        except ComfyUIError as exc:
            yield images, f"❌ Job {label} failed: {exc}"
            return
    yield images, f"✅ All {total} job(s) done"


def _resolve_lora_slots(*slots) -> list:
    """Flat (name, weight, name, weight, ...) UI values → (file, strength).

    Variadic on purpose: the slot count then lives only in MAX_LORA_SLOTS,
    so adding a slot needs no change here or in any handler signature.
    Slots left at "None" resolve to nothing and drop out.
    """
    loras = []
    for name, weight in zip(slots[::2], slots[1::2]):
        lora_file = resolve_lora_name(name)
        if lora_file:
            loras.append((lora_file, float(weight)))
    return loras


def _resolve_flux_lora_slots(*slots) -> list:
    """_resolve_lora_slots for the Flux tab's separate LoRA folder."""
    loras = []
    for name, weight in zip(slots[::2], slots[1::2]):
        lora_file = resolve_flux_lora(name)
        if lora_file:
            loras.append((lora_file, float(weight)))
    return loras
def _check_model(model):
    """Resolve the dropdown value; return (entry, error_message_or_None)."""
    entry = resolve_model_entry(model)
    if not model_file_available(entry):
        return entry, (f"❌ Model “{entry['name']}” is not downloaded yet — "
                       "restart the app so the download step can fetch it.")
    return entry, None


def _krea_settings(seed, randomize, steps, cfg, resolution, sampler, model,
                   batch_count, lora_slots) -> dict:
    """The Krea 2 tab's controls as the prompt library stores them.

    Deliberately the *UI* values, not the resolved job dict: what goes in
    here comes back out into these same controls on another pod, so the
    dropdown label is the useful thing to keep and the resolved filename
    is not. Mirrors the generate_single signature — when that gains a
    control, this is the other half of the change.
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
        "loras": [[name, float(weight)]
                  for name, weight in zip(lora_slots[::2], lora_slots[1::2])],
    }


def generate_single(prompt, negative, seed, randomize, steps, cfg, resolution,
                    sampler, model, batch_count, publish, publish_title,
                    save_preset, preset_name, *lora_slots):
    """First tab: run batch_count jobs on sequential seeds."""
    entry, error = _check_model(model)
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
    loras = _resolve_lora_slots(*lora_slots)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width, "height": height,
        "sampler": sampler, "loras": loras, "unet_file": entry["file"],
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


def _flux_model_info_text(entry) -> str:
    """One-line summary shown under the Flux Model dropdown."""
    steps, guidance, turbo = flux_model_defaults(entry)
    info = (f"**{entry.get('variant', 'raw').title()}** · "
            f"defaults: {steps} steps, guidance {guidance:g}"
            + (" · Turbo LoRA applied" if turbo else ""))
    if entry.get("trigger"):
        info += " · trigger words are inserted into the prompt (editable)"
    if not flux_model_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    return info

def generate_flux(prompt, seed, randomize, steps, guidance, resolution,
                  sampler, model, batch_count, *lora_slots):
    """Flux tab: text-to-image with Flux 2 (guidance-distilled, no negative)."""
    entry = resolve_flux_model(model)
    if not flux_model_available(entry):
        yield [], (f"❌ Model “{entry['name']}” is not downloaded yet — "
                   "restart the app so the download step can fetch it."), 0
        return
    _s, _g, turbo = flux_model_defaults(entry)
    if turbo and not flux_turbo_lora_available():
        yield [], ("❌ The Flux 2 Turbo LoRA is missing — restart the app to "
                   "download it, or pick a raw-variant model."), 0
        return
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = parse_resolution(resolution)
    loras = _resolve_flux_lora_slots(*lora_slots)
    jobs = [{
        "prompt": prompt, "seed": base_seed + i, "steps": int(steps),
        "guidance": float(guidance), "width": width, "height": height,
        "sampler": sampler, "loras": loras, "unet_file": entry["file"],
        "turbo_lora": turbo,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_flux_workflow,
                                    prefix="Flux2"):
        yield images, status, base_seed
# ── Klein Edit (Klein advanced FLUX.2 Klein 9B graph) ────────────────────────────
# Self-contained like the V2 tab: its own model, encoder, LoRA folder and
# defaults, all from the source workflow. Nothing here reads DEFAULTS,
# MODEL_CHOICES or the Flux constants, so tuning another tab never moves it.

KLEIN_LORA_SLOTS = klein_default_lora_slots()
KLEIN_LORA_CHOICES = ["None"] + list_klein_lora_files()
KLEIN_MODEL_CHOICES = klein_model_names()
_k_steps, _k_cfg, _k_guidance = klein_model_defaults(klein_resolve_model(None))


def _klein_model_info_text(entry) -> str:
    """One-line summary shown under the Klein Model dropdown."""
    steps, cfg, guidance = klein_model_defaults(entry)
    info = (f"defaults: {steps} steps, CFG {cfg:g}, guidance {guidance:g} · "
            f"`{entry['file']}`")
    if not klein_model_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    return info

def _resolve_klein_lora_slots(*slots) -> list:
    """Flat (enabled, name, weight) × N UI values → (file, strength) pairs.

    Mirrors the source workflow's Power Lora Loader, exactly as the V2 tab
    does: a row contributes only while its checkbox is on, and the order is
    preserved because LoRA application is not commutative.
    """
    loras = []
    for enabled, name, weight in zip(slots[::3], slots[1::3], slots[2::3]):
        if not enabled:
            continue
        lora_file = resolve_klein_lora(name)
        if lora_file:
            loras.append((lora_file, float(weight)))
    return loras

def generate_klein_edit(image, use_image2, image2, prompt, seed, randomize,
                        model, steps, cfg, guidance, sampler, scheduler,
                        reference_mp, output_mode, output_mp, custom_width,
                        custom_height, batch_count, *lora_slots):
    """Klein Edit tab: the Klein FLUX.2 Klein 9B editing graph.

    One or two source images are attached to the conditioning as reference
    latents, so the prompt describes the change rather than the whole
    picture — and with two images it should name them ("the person from
    image 1 wearing the hat from image 2"), which is how the source
    workflow's own note puts it.
    """
    if image is None:
        yield [], "❌ Upload image 1 first.", 0
        return
    ready, message = klein_status()
    if not ready:
        yield [], message, 0
        return
    entry = klein_resolve_model(model)
    if not klein_model_available(entry):
        yield [], (f"❌ Model “{entry['name']}” is not downloaded yet — "
                   "restart the app so the download step can fetch it."), 0
        return
    if not str(prompt or "").strip():
        yield [], "❌ Describe the edit you want.", 0
        return
    if use_image2 and image2 is None:
        yield [], ("❌ Input image 2 is enabled but empty — upload it, or "
                   "switch the toggle off."), 0
        return

    image = image.convert("RGB")
    width, height = klein_resolve_output_size(
        output_mode, image.size, output_mp, (custom_width, custom_height))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image), f"klein_{tag}.png")
        image2_name = None
        if use_image2:
            image2_name = client.upload_image(
                _png_bytes(image2.convert("RGB")), f"klein_{tag}_b.png")
    except Exception as exc:
        yield [], f"❌ Uploading the image to ComfyUI failed: {exc}", base_seed
        return

    jobs = [{
        "prompt": prompt, "image_name": image_name,
        "image2_name": image2_name, "seed": base_seed + i,
        "width": width, "height": height, "steps": int(steps),
        "cfg": float(cfg), "guidance": float(guidance), "sampler": sampler,
        "scheduler": scheduler, "reference_megapixels": float(reference_mp),
        "loras": _resolve_klein_lora_slots(*lora_slots),
        "unet_file": entry["file"],
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_klein_edit_workflow,
                                    prefix="KleinEdit"):
        yield images, status, base_seed

# ── Krea 2 V2 (Krea2 advanced graph) ─────────────────────────────────────────────
# This tab is deliberately self-contained: its own model, VAE, LoRA stack,
# sampler and defaults, all taken from the source workflow. Nothing here
# reads DEFAULTS, MODEL_CHOICES or LORA_CHOICES, so tuning the Single tab
# never moves it.

V2_LORA_SLOTS = v2_default_lora_slots()
V2_LORA_CHOICES = ["None"] + list_lora_files()
V2_MODEL_CHOICES = v2_model_names()
V2_TURBO_SLOT = v2_turbo_lora_slot()
_v2_steps, _v2_cfg, _ = v2_model_defaults(v2_resolve_model(None))


def _v2_model_info_text(entry) -> str:
    """One-line summary shown under the V2 Model dropdown."""
    steps, cfg, turbo_lora = v2_model_defaults(entry)
    info = (f"**{entry.get('variant', 'turbo').title()}** · "
            f"defaults: {steps} steps, CFG {cfg:g} · Turbo LoRA "
            + ("**on** at " f"{V2_TURBO_LORA_STRENGTH:g}" if turbo_lora
               else "off"))
    if not v2_model_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    if turbo_lora and not v2_turbo_lora_available():
        info += (" · ⚠️ **the Turbo LoRA this variant needs has not "
                 "downloaded** — raw output will be undistilled")
    return info

def _resolve_v2_lora_slots(*slots) -> list:
    """Flat (enabled, name, weight) × N UI values → (file, strength) pairs.

    Mirrors the source workflow's Power Lora Loader: a row contributes only
    while its checkbox is on, and order is preserved because LoRA
    application is not commutative.
    """
    loras = []
    for enabled, name, weight in zip(slots[::3], slots[1::3], slots[2::3]):
        if not enabled:
            continue
        lora_file = resolve_lora_name(name)
        if lora_file:
            loras.append((lora_file, float(weight)))
    return loras

def generate_v2(prompt, negative, seed, randomize, model, aspect, megapixels,
                multiple, eta, sampler_name, scheduler, steps, denoise, cfg,
                sampler_mode, bongmath, variance_preset, fine_tune_variance,
                variance_model_type, variance_schedule, cutoff_step,
                total_steps, cutoff_strength, shift_strength, sharpen,
                film_grain, batch_count, publish, publish_title,
                save_preset, preset_name, *lora_slots):
    """Krea 2 V2 tab: the Krea2 advanced turbo/raw text-to-image graph."""
    ready, message = v2_status()
    if not ready:
        yield [], message, 0
        return
    entry = v2_resolve_model(model)
    if not v2_model_available(entry):
        yield [], (f"❌ Model “{entry['name']}” is not downloaded yet — "
                   "restart the app so the download step can fetch it."), 0
        return
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = v2_resolve_size(aspect, megapixels, multiple)
    loras = _resolve_v2_lora_slots(*lora_slots)
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
        "loras": [[bool(on), name, float(weight)] for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }
    prompts.record(prompts.TAB_KREA2_V2, prompt, negative or "", settings,
                   publish=publish, title=publish_title)
    notice = _save_preset(presets.TAB_KREA2_V2, save_preset, preset_name,
                          settings)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "width": width, "height": height, "loras": loras,
        "unet_file": entry["file"],
        "sampler_settings": sampler_settings,
        "variance_settings": variance_settings,
        "sharpen": bool(sharpen), "film_grain": bool(film_grain),
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_v2_workflow,
                                    prefix="Krea2V2"):
        yield images, notice + status, base_seed

def _prepare_inpaint_inputs(editor_value, grow_px: int, blur_px: int):
    """ImageEditor value → (RGB image, L-mode mask or None), sized for the VAE.

    The mask is the union of the painted layers' alpha channels, optionally
    dilated (grow) and gaussian-blurred for a soft transition; None when
    nothing is painted (full-image img2img). Both images are downscaled so
    the long side is ≤ 2048 (never upscaled) and snapped to multiples of
    16, which Krea 2's VAE requires.
    """
    if not isinstance(editor_value, dict) or editor_value.get("background") is None:
        raise ValueError("Upload an image first.")
    background = editor_value["background"].convert("RGB")
    mask = None
    for layer in editor_value.get("layers") or []:
        if "A" not in layer.getbands():
            continue
        alpha = layer.getchannel("A")
        if alpha.size != background.size:
            alpha = alpha.resize(background.size)
        mask = alpha if mask is None else ImageChops.lighter(mask, alpha)
    if mask is not None and mask.getbbox() is None:
        mask = None
    if mask is not None:
        if grow_px:
            mask = mask.filter(ImageFilter.MaxFilter(grow_px * 2 + 1))
        if blur_px:
            mask = mask.filter(ImageFilter.GaussianBlur(blur_px))
    w, h = background.size
    scale = min(1.0, 2048 / max(w, h))
    w2 = max(64, int(w * scale) // 16 * 16)
    h2 = max(64, int(h * scale) // 16 * 16)
    if (w2, h2) != (w, h):
        background = background.resize((w2, h2), Image.LANCZOS)
        if mask is not None:
            mask = mask.resize((w2, h2), Image.LANCZOS)
    return background, mask


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def generate_inpaint(editor_value, prompt, negative, seed, randomize, steps,
                     cfg, denoise, sampler, grow, blur, model, batch_count,
                     *lora_slots):
    """Inpaint tab: repaint the painted region — or, with nothing painted,
    run the whole image through img2img at the chosen denoise."""
    entry, error = _check_model(model)
    if error:
        yield [], error, 0
        return
    try:
        image, mask = _prepare_inpaint_inputs(editor_value, int(grow), int(blur))
    except ValueError as exc:
        yield [], f"❌ {exc}", 0
        return
    if mask is None and float(denoise) >= 1.0:
        yield [], (
            "❌ Nothing is painted, so this would run as full-image img2img — "
            "but Denoise 1.0 would ignore the source image entirely. Lower "
            "Denoise (e.g. 0.5–0.8) or paint the region to replace."
        ), 0
        return
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image), f"inpaint_{tag}.png")
        mask_name = (client.upload_image(_png_bytes(mask),
                                         f"inpaint_{tag}_mask.png")
                     if mask is not None else None)
    except Exception as exc:
        yield [], f"❌ Uploading the image to ComfyUI failed: {exc}", base_seed
        return
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "denoise": float(denoise),
        "sampler": sampler, "image_name": image_name, "mask_name": mask_name,
        "loras": _resolve_lora_slots(*lora_slots),
        "unet_file": entry["file"],
    } for i in range(int(batch_count))]
    prefix = "Krea2Inpaint" if mask is not None else "Krea2Img2Img"
    for images, status in _run_jobs(jobs, builder=build_inpaint_workflow,
                                    prefix=prefix):
        yield images, status, base_seed


def _fit_edit_size(w: int, h: int, max_pixels: int = 2_000_000) -> tuple:
    """Edit-target size: keep aspect, cap at max_pixels, never upscale, /16.

    The Identity Edit LoRA bleeds/duplicates content above ~2 MP, so the
    cap is by area rather than the inpaint tab's 2048-px long side.

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
    entry, error = _check_model(model)
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
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "image_name": image_name,
        "image2_name": image2_name,
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "loras": _resolve_lora_slots(*lora_slots),
        "unet_file": entry["file"],
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_edit_workflow,
                                    prefix="Krea2Edit"):
        yield images, status, base_seed


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
    ready, message = v2_edit_status()
    if not ready:
        yield [], message, 0
        return
    entry = v2_resolve_model(model)
    if not v2_model_available(entry):
        yield [], (f"❌ Model “{entry['name']}” is not downloaded yet — "
                   "restart the app so the download step can fetch it."), 0
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
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "width": width, "height": height, "image_name": image_name,
        "image2_name": image2_name,
        "loras": _resolve_v2_lora_slots(*lora_slots),
        "unet_file": entry["file"],
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "fit_mode": fit_mode,
        "sampler_settings": sampler_settings,
        "variance_settings": variance_settings,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_v2_edit_workflow,
                                    prefix="Krea2V2Edit"):
        yield images, status, base_seed


def _is_reactor_reject(path) -> bool:
    """True for the blank frame ReActor returns when its SFW check fires.

    A flagged input is dropped from the image list, and ReActor's empty-list
    branch hands back a 512×512 solid black image rather than the original —
    so the swap "succeeds" and writes a black PNG. Detecting it here is the
    only way to tell the user what actually happened.
    """
    try:
        with Image.open(path) as img:
            return (img.size == (512, 512)
                    and img.convert("RGB").getbbox() is None)
    except Exception:
        return False


def generate_faceswap(base_image, face_image, swap_model, facedetection,
                      restore_model, restore_visibility, codeformer_weight,
                      input_index, source_index):
    """Face Swap tab: put a reference face onto a base image with ReActor.

    Deliberately resizes nothing: ReActor rewrites only the face region,
    so the saved PNG keeps the base image's exact resolution. One job, no
    seed and no batch — the swap is deterministic, so re-running the same
    two images would just rewrite the same result.
    """
    if base_image is None:
        yield [], ("❌ Choose a base image — pick one of your generations "
                   "below or upload it.")
        return
    if face_image is None:
        yield [], "❌ Upload a reference face image."
        return
    ready, message = reactor_status()
    if not ready:
        yield [], message
        return
    base_image = base_image.convert("RGB")
    face_image = face_image.convert("RGB")
    width, height = base_image.size
    tag = uuid.uuid4().hex[:8]
    try:
        base_name = client.upload_image(_png_bytes(base_image),
                                        f"swap_{tag}_base.png")
        face_name = client.upload_image(_png_bytes(face_image),
                                        f"swap_{tag}_face.png")
    except Exception as exc:
        yield [], f"❌ Uploading the images to ComfyUI failed: {exc}"
        return
    workflow = build_faceswap_workflow(
        # `tag` again rather than a second draw: the swap is one job, and
        # naming its output after the inputs it was built from is more
        # use than a fresh number. See _run_tag for why it is there.
        filename_prefix=f"Krea2FaceSwap_{tag}",
        base_image_name=base_name, face_image_name=face_name,
        swap_model=swap_model, facedetection=facedetection,
        face_restore_model=restore_model,
        face_restore_visibility=float(restore_visibility),
        codeformer_weight=float(codeformer_weight),
        input_faces_index=str(input_index or "0").strip() or "0",
        source_faces_index=str(source_index or "0").strip() or "0",
    )
    yield [], f"⏳ Swapping face — queued ({width}×{height})"
    images = []
    try:
        for event in client.run(workflow, timeout=600):
            if event["type"] == "progress" and event["total"]:
                yield images, (f"⏳ Swapping face — step "
                               f"{event['step']}/{event['total']}")
            elif event["type"] == "done":
                images = event["images"]
    except ComfyUIError as exc:
        yield images, f"❌ Face swap failed: {exc}"
        return
    if not images:
        yield [], ("❌ ReActor returned no image — usually no face was "
                   "detected in one of the two inputs. Check the ComfyUI "
                   "log, or try a clearer, more front-facing reference.")
        return
    if _is_reactor_reject(images[0]):
        yield images, (
            "⚠️ ReActor's SFW filter rejected an input, so no swap was "
            "performed — it returned a blank 512×512 frame instead, which "
            "was still saved. Note the check also fails closed: if its "
            "detector model is missing, every swap comes back blank "
            "(see the ComfyUI log)."
        )
        return
    yield images, f"✅ Face swapped at {width}×{height}"


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


def _run_wan_jobs(jobs, builder=build_wan_i2v_workflow):
    """Video executor: yields (all_videos, latest_video, status_text)."""
    videos = []
    total = len(jobs)
    latest = None
    # Video jobs go to their own instance under KREA2_WAN_PARALLEL, so the
    # port that has to be alive is the one wan_client talks to.
    alive, note = comfy_ensure_alive(
        port=WAN_COMFY_PORT if WAN_PARALLEL else COMFY_PORT,
        log_path=WAN_COMFY_LOG if WAN_PARALLEL else COMFY_LOG,
    )
    if not alive:
        yield videos, latest, note
        return
    if note:
        yield videos, latest, note
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        workflow = builder(**job)
        swap_note = _release_on_swap(wan_client, workflow)
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
            for event in wan_client.run(workflow, timeout=7200):
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

def generate_from_json(json_file, json_text):
    """JSON tab: file upload takes precedence over pasted text."""
    try:
        if json_file:
            raw = json.loads(Path(json_file).read_text())
        elif json_text and json_text.strip():
            raw = json.loads(json_text)
        else:
            yield [], "❌ Provide a JSON file or paste JSON text."
            return
        jobs = _normalize_jobs(raw)
    except (ValueError, OSError) as exc:
        yield [], f"❌ Invalid JSON: {exc}"
        return
    yield from _run_jobs(jobs)

# Every finished prompt tells the index what it wrote, which both keeps the
# listing correct without a rescan and is what queues the new files'
# thumbnails. Registered against the client rather than the three separate
# places that consume its "done" event (_run_jobs, generate_faceswap,
# _run_wan_jobs), so a fourth executor gets this for free.
on_output(gallery_index.note_new)
# And the same for the recipe behind them, for the same reason: one
# producer of the "done" event, so a fourth executor gets this free too.
on_output(recipes.note_output)


def list_output_images() -> list[str]:
    """Every generated image and video in OUTPUT_DIR, newest first."""
    return gallery_index.list_media()
def zip_outputs():
    """Bundle all generated media into one zip (the pod disk is ephemeral).

    Returns (path_or_None, message). The path is None when there is
    nothing to zip, which is what the caller renders as "hide the
    download panel" — in Gradio that panel is a
    full-width drop zone with nothing in it until this runs, and on a
    phone it was a screenful of nothing between the button and the
    picture — on every visit, for the sake of the one that asks for a zip.
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

def _swap_trigger(text, entry, registry=None) -> str:
    """Put the selected model's trigger words into the prompt text.

    Any other registered model's trigger (within the same registry) is
    removed first, so switching models swaps triggers instead of stacking
    them. The text stays fully editable — whatever ends up in the box is
    used verbatim (nothing is added silently at generation time).
    """
    text = text or ""
    for other in (registry if registry is not None else KREA2_MODELS):
        trig = (other.get("trigger") or "").strip()
        if not trig:
            continue
        idx = text.lower().find(trig.lower())
        if idx >= 0:
            text = text[:idx] + text[idx + len(trig):]
    text = text.strip().strip(",").strip()
    trigger = (entry.get("trigger") or "").strip()
    if trigger:
        return f"{trigger}, {text}" if text else trigger
    return text


def _model_info_text(entry) -> str:
    """One-line summary shown under the Model dropdown."""
    steps, cfg = model_defaults(entry)
    info = (f"**{entry.get('variant', 'turbo').title()}** · "
            f"defaults: {steps} steps, CFG {cfg:g}")
    if entry.get("trigger"):
        info += " · trigger words are inserted into the prompt (editable)"
    if not model_file_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    return info
