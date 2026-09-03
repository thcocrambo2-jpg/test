"""Gradio UI.

Tabs: single / simple-batch generation, Krea 2 V2, the prompt library,
instruction-based editing (upload an image, describe the change) on the
Krea 2 and Krea 2 V2 pipelines, inpainting (paint a mask over an uploaded
image), ReActor face swap, Flux 2 generation, Flux 2 Klein 9B editing,
Wan 2.2 video, JSON batch jobs, and an output gallery.

Every one of them is a feature in features.py and is only built when that
feature is on — a tab that is off is never constructed, so its handlers
are never registered on Gradio's HTTP API either. Written for Gradio 6
(theme/css now belong to launch(), gr.File hands the handler a plain file
path).

Each tab's body is a `_tab_*` builder, and **TAB_ORDER is what decides
the order they appear in** — one tuple of (feature key, builder, tab id),
walked by the loop inside the Blocks. Moving an entry moves the tab and
nothing else has to change; a feature that is off is skipped, so the rest
close up with no gap. The order is fixed for the build, since the Blocks
tree is constructed once at import.

The prompt library is the one tab that reaches into another. Its cards
come from the licence server (prompts.py) and its Use buttons write a
whole recipe into the Krea 2 or Krea 2 V2 controls and switch to that
tab. Those controls belong to two other builders, so the Use wiring is
declared *after* the loop rather than inside the library's own body —
Gradio only needs a component to exist before the .click() naming it, not
before the tab it lives in, and keeping that one .click() out of the body
is exactly what leaves TAB_ORDER free to be reordered. The same handlers
also feed the library: generate_single and generate_v2 call
prompts.record() with what they were given. That call is silent by
contract — the customer is never told their prompts are saved, so it can
neither block generation nor surface a failure.

The two generation tabs also open with a **preset** dropdown: one tab's
settings, saved under a name on the licence server (presets.py) and
offered to every pod. Picking one writes every control below it and
leaves the prompt boxes alone, which is the whole difference between a
preset and a library card — same settings blob, same guarding, minus the
words. Only an admin licence can write one, from a tickbox next to the
publish one; everyone else reads. The preset each tab marks as its
default is applied on page load, so what a fresh session starts on is a
row in Atlas rather than something compiled into the binary.

Alongside the tabs there is one other view: the pricing panel, which is
the plan catalogue read from the licence server (plans.py) and rendered by
theme.pricing_html. "Plans & pricing" hides the tabs and shows it, Back
reverses that — one page, two views, rather than a Gradio route, which can
only be declared outside the `with gr.Blocks()` block and would mean a
second page carrying its own copy of this app's chrome. It is information
only — no entitlement and no purchase happens there — and it is always
built, whatever the licence grants.

The look of all of it lives in theme.py: the palette and Gradio theme
tokens, the CSS, the application header and the two bits of page JS. This
module only names things — `elem_classes="kx-..."` on a column, a status
box or a heading is a hook theme.CSS styles; none of them affect what a
control does, so a tab keeps working with the stylesheet stripped out.

Some hosts' networks break Gradio's *.gradio.live share tunnel (the link
504s even though the app is healthy), so after launching we probe the
share URL from inside the pod and, if it does not answer, start a
Cloudflare quick tunnel and print that URL instead. launch_ui() blocks
while the UI is live — press Ctrl-C to shut it down.
"""

import io
import json
import random
import re
import subprocess
import time
import urllib.request
import uuid
import zipfile
from functools import partial
from pathlib import Path

import gradio as gr
import requests
from PIL import Image, ImageChops, ImageFilter

import features
import gallery_index
import jobqueue
import licensing
import plans
import presets
import prompts
import recipes
import showcase
import theme
from client import ComfyUIError, client, model_signature, on_output, wan_client
from comfy import GPU_COUNT, ensure_alive as comfy_ensure_alive
from config import (
    COMFY_LOG,
    COMFY_PORT,
    DEFAULT_LORAS,
    DEFAULT_RESOLUTION,
    FREE_ON_SWAP,
    FLUX_MODELS,
    KLEIN_DEFAULT_CUSTOM_SIZE,
    KLEIN_DEFAULT_MEGAPIXELS,
    KLEIN_DEFAULTS,
    KLEIN_LORA_SUBDIR,
    KLEIN_OUTPUT_CUSTOM,
    KLEIN_OUTPUT_MODES,
    KLEIN_OUTPUT_SAME,
    KLEIN_REFERENCE_MEGAPIXELS,
    KLEIN_SCHEDULERS,
    KLEIN_WARN_PIXELS,
    KREA2_MODELS,
    OUTPUT_DIR,
    REACTOR_DEFAULT_DETECTOR,
    REACTOR_DETECTORS,
    RESOLUTION_PRESETS,
    SAMPLERS,
    TEMP_DIR,
    V2_ASPECT_RATIOS,
    V2_DEFAULT_ASPECT,
    V2_DEFAULT_MEGAPIXELS,
    V2_DEFAULT_MULTIPLE,
    V2_DEFAULT_NEGATIVE,
    V2_EDIT_DEFAULT_GROUNDING,
    V2_EDIT_DEFAULT_REF_BOOST,
    V2_EDIT_FIT_MODES,
    V2_SAMPLER_DEFAULTS,
    V2_SAMPLER_MODES,
    V2_SAMPLER_NAMES,
    V2_SCHEDULERS,
    V2_TURBO_LORA_STRENGTH,
    V2_VARIANCE_DEFAULTS,
    V2_VARIANCE_MODEL_TYPES,
    V2_VARIANCE_PRESETS,
    V2_VARIANCE_SCHEDULES,
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_DEFAULT_NEGATIVE,
    WAN_DEFAULT_RESOLUTION,
    WAN_FPS,
    WAN_MAX_SECONDS,
    WAN_MODE_DEFAULTS,
    WAN_COMFY_LOG,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
    WAN_RESOLUTIONS,
    WAN_VARIANT,
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
    default_restore_model,
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
    yield images, f"✅ All {total} job(s) done — images saved under {OUTPUT_DIR}"


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


def _lora_inputs(dropdowns, weights) -> list:
    """Interleave slot dropdowns/weights for a handler's *lora_slots tail.

    Kept last in every click() input list so the slot count can grow
    without disturbing the fixed leading arguments.
    """
    for dropdown, weight in zip(dropdowns, weights):
        _recipe_gate(dropdown, weight)
    return [component for pair in zip(dropdowns, weights) for component in pair]


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


def flux_model_changed(model_name, prompt_text):
    """Flux Model dropdown → its step/guidance defaults, info line, and
    trigger words swapped into the prompt box."""
    entry = resolve_flux_model(model_name)
    steps, guidance, _turbo = flux_model_defaults(entry)
    return (gr.Slider(value=steps), gr.Slider(value=guidance),
            gr.Markdown(value=_flux_model_info_text(entry)),
            gr.Textbox(value=_swap_trigger(prompt_text, entry, FLUX_MODELS)))


def refresh_flux_lora_choices():
    """Re-scan loras/flux2/ (e.g. after dropping new files into it)."""
    choices = ["None"] + list_flux_lora_files()
    return [gr.Dropdown(choices=choices) for _ in range(MAX_LORA_SLOTS)]


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


def klein_model_changed(model_name):
    """Klein Model dropdown → that model's steps / CFG / guidance defaults."""
    entry = klein_resolve_model(model_name)
    steps, cfg, guidance = klein_model_defaults(entry)
    return (gr.Slider(value=steps), gr.Slider(value=cfg),
            gr.Slider(value=guidance),
            gr.Markdown(value=_klein_model_info_text(entry)))


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


def _klein_size_note(width: int, height: int) -> str:
    """The '→ W×H' line, with a warning once the render gets expensive."""
    note = f"→ **{width} × {height}** ({width * height / 1e6:.2f} MP)"
    if width * height > KLEIN_WARN_PIXELS:
        note += ("  ⚠️ that is a large render — expect minutes per image and "
                 "a real chance of an out-of-memory kill. Switch the mode to "
                 "**scale** or **custom** to render smaller.")
    return note


def klein_size_preview(image, mode, megapixels, custom_width, custom_height):
    """Recompute that line whenever an input that feeds it changes."""
    if image is None and mode != KLEIN_OUTPUT_CUSTOM:
        return gr.Markdown(value="→ upload image 1 to see the output size.")
    source = image.size if image is not None else KLEIN_DEFAULT_CUSTOM_SIZE
    width, height = klein_resolve_output_size(
        mode, source, megapixels, (custom_width, custom_height))
    return gr.Markdown(value=_klein_size_note(width, height))


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


def refresh_klein_lora_choices():
    """Re-scan loras/klein/ for this tab's dropdowns."""
    choices = ["None"] + list_klein_lora_files()
    return [gr.Dropdown(choices=choices) for _ in range(len(KLEIN_LORA_SLOTS))]


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


def v2_model_changed(model_name):
    """V2 Model dropdown → its steps/CFG and the Turbo LoRA slot's state.

    The Turbo LoRA is toggled here rather than inside the workflow builder
    so it stays a visible, editable row: raw mode ticks slot
    V2_TURBO_SLOT on at its strength, turbo unticks it, and either way
    whatever is on screen is exactly what gets applied.
    """
    entry = v2_resolve_model(model_name)
    steps, cfg, turbo_lora = v2_model_defaults(entry)
    return (gr.Slider(value=steps), gr.Slider(value=cfg),
            gr.Markdown(value=_v2_model_info_text(entry)),
            gr.Checkbox(value=turbo_lora and v2_turbo_lora_available()),
            gr.Slider(value=V2_TURBO_LORA_STRENGTH))


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


def _v2_size_text(aspect, megapixels, multiple) -> str:
    """'this resolves to W×H' line under the resolution controls."""
    width, height = v2_resolve_size(aspect, megapixels, multiple)
    return f"→ **{width} × {height}** ({width * height / 1e6:.2f} MP actual)"


def v2_size_preview(aspect, megapixels, multiple):
    """Recompute that line when any of the three controls changes."""
    return gr.Markdown(value=_v2_size_text(aspect, megapixels, multiple))


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


def refresh_v2_lora_choices():
    """Re-scan the LoRA folder for this tab's dropdowns."""
    choices = ["None"] + list_lora_files()
    return [gr.Dropdown(choices=choices) for _ in range(len(V2_LORA_SLOTS))]


def _v2_lora_stack():
    """The workflow's LoRA rows, each with the on/off toggle it ships with.

    Unlike _lora_stack these rows are fixed to the source workflow's stack
    rather than config.DEFAULT_LORAS, and each carries an Enable checkbox
    because that is what rgthree's Power Lora Loader exposes. Returns
    (checkboxes, dropdowns, weights) in slot order.
    """
    gr.Markdown(
        "### 🎭 LoRA stack — model + CLIP\n"
        "The workflow's stack, in its original order, strengths and on/off "
        "states. Each strength applies to the model *and* the text encoder.",
        elem_classes="kx-section",
    )
    cbs, dds, ws = [], [], []
    for index, (on, name, strength) in enumerate(V2_LORA_SLOTS):
        with gr.Row():
            cbs.append(gr.Checkbox(
                value=on, label="On", scale=0, min_width=70,
                interactive=name in V2_LORA_CHOICES,
            ))
            dds.append(gr.Dropdown(
                choices=V2_LORA_CHOICES,
                value=name if name in V2_LORA_CHOICES else "None",
                label=f"LoRA {index + 1}", scale=3,
            ))
            ws.append(gr.Slider(0.0, 2.0, value=strength, step=0.01,
                                label="Strength", scale=1))
    gr.Button("🔄 Rescan LoRA folder", size="sm").click(
        fn=refresh_v2_lora_choices, outputs=dds)
    return cbs, dds, ws


def _lora_triples(cbs, dds, ws) -> list:
    """Interleave (enable, name, weight) slot triples for a *lora_slots tail.

    Used by both Power-Lora-Loader tabs (Krea 2 V2 and Klein Edit), whose
    rows carry a per-row on/off checkbox — the plain two-value _lora_inputs
    is for the tabs whose slots do not.
    """
    for checkbox, dropdown, weight in zip(cbs, dds, ws):
        _recipe_gate(dropdown, checkbox, weight)
    return [c for triple in zip(cbs, dds, ws) for c in triple]


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
            f"was still written to {OUTPUT_DIR}. Note the check also fails "
            "closed: if its detector model is missing, every swap comes back "
            "blank (see the ComfyUI log)."
        )
        return
    yield images, (f"✅ Face swapped at {width}×{height} — saved to "
                   f"{OUTPUT_DIR}")


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
    yield videos, latest, (
        f"✅ All {total} video(s) done — saved under {OUTPUT_DIR}"
    )


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
    else:
        fps, snap = WAN_FPS, 16
        shift = WAN_MODE_DEFAULTS["turbo" if turbo else "raw"]["shift"]
        builder = build_wan_i2v_workflow
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
        **({} if use_5b else {"lightning": turbo}),
    } for i in range(int(batch_count))]
    for videos, latest, status in _run_wan_jobs(jobs, builder=builder):
        yield videos, latest, status, base_seed


def wan_model_changed(model, mode):
    """Model radio → sliders get that model's defaults; the turbo/raw Mode
    radio only applies to 14B (the 5B has no Lightning distillation)."""
    if _is_wan_5b(model):
        return (gr.Radio(interactive=False),
                gr.Slider(value=WAN_5B_DEFAULTS["steps"]),
                gr.Slider(value=WAN_5B_DEFAULTS["cfg"]))
    d = WAN_MODE_DEFAULTS["turbo" if str(mode).lower().startswith("turbo")
                          else "raw"]
    return (gr.Radio(interactive=True),
            gr.Slider(value=d["steps"]), gr.Slider(value=d["cfg"]))


def wan_mode_changed(model, mode):
    """Mode radio → reset the steps/CFG sliders to that mode's defaults."""
    if _is_wan_5b(model):  # mode is disabled for 5B; keep sliders as-is
        return gr.Slider(), gr.Slider()
    d = WAN_MODE_DEFAULTS["turbo" if str(mode).lower().startswith("turbo")
                          else "raw"]
    return gr.Slider(value=d["steps"]), gr.Slider(value=d["cfg"])


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


def refresh_lora_choices():
    """Re-scan the LoRA folder (e.g. after dropping new files into it)."""
    choices = ["None"] + list_lora_files()
    return [gr.Dropdown(choices=choices) for _ in range(MAX_LORA_SLOTS)]


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


RECENT_IMAGES_LIMIT = 20


def list_recent_images(limit: int = RECENT_IMAGES_LIMIT) -> list[str]:
    """The newest generated still images (for the pick-from-existing pickers)."""
    return gallery_index.list_images(limit)


def _picked_recent(paths, evt: gr.SelectData):
    """Picker click → the ORIGINAL file behind the clicked tile.

    Resolved by *index* against the state that produced the grid, never by
    reading a path back out of the clicked tile. The grid holds 512px
    thumbnails, so trusting what was clicked would quietly feed a thumbnail
    into an edit/face-swap/video job — no error, just a ruined output.
    """
    idx = evt.index
    if not isinstance(idx, int) or not 0 <= idx < len(paths or []):
        return gr.Image()          # stale click, e.g. right after a refresh
    return paths[idx]


def _recent_page():
    """The picker's tiles, plus the originals they stand for."""
    paths = list_recent_images()
    return paths, gallery_index.thumbs_for(paths)


def _recent_picker(target_image):
    """A collapsed 'use a previous generation' gallery under an image input.

    Must be called inside a gr.Blocks context, after `target_image` exists.
    Clicking a thumbnail loads the full-resolution original into
    `target_image`; the refresh button re-scans OUTPUT_DIR.

    Filled when the accordion is opened, never at build time. This helper
    is instantiated once per image-input tab, so populating it eagerly cost
    a directory scan *and* a screenful of full-size PNGs per picker on every
    page load — for a panel most sessions never open.
    """
    with gr.Accordion(
        f"📂 Use a previous generation (last {RECENT_IMAGES_LIMIT} images)",
        open=False,
    ) as accordion:
        # The originals behind the tiles, in tile order — see _picked_recent.
        picker_paths = gr.State([])
        picker = gr.Gallery(
            value=None, label="Click an image to use it",
            columns=5, height=240, allow_preview=False,
            # No download button: it would hand over the thumbnail rather
            # than the image, with nothing to say so.
            buttons=[],
        )
        refresh_btn = gr.Button("🔄 Refresh", size="sm")
        accordion.expand(fn=_recent_page, outputs=[picker_paths, picker])
        refresh_btn.click(fn=_recent_page, outputs=[picker_paths, picker])
        picker.select(fn=_picked_recent, inputs=[picker_paths],
                      outputs=target_image)


# How many tiles the Gallery tab shows at a time. A gr.Gallery renders every
# item it is given at once, so this is what bounds the first paint on a pod
# with a few hundred generations behind it.
GALLERY_PAGE = 10


def _gallery_view(paths, shown):
    """The grid, the Load-more button and the count line, for a page depth."""
    shown = max(0, min(int(shown or 0), len(paths)))
    remaining = len(paths) - shown
    return (
        gr.Gallery(value=gallery_index.thumbs_for(paths[:shown])),
        gr.Button(value=f"⬇️ Load {min(GALLERY_PAGE, remaining)} more",
                  visible=remaining > 0),
        f"{len(paths)} file(s) in `{OUTPUT_DIR}` — showing {shown}",
    )


def refresh_gallery():
    """Tab opened, or Refresh clicked: re-scan and show the newest page.

    Deliberately back to page one. The list is newest-first, so anything
    generated since the last look is at the top — which is what someone
    pressing Refresh is looking for.
    """
    paths = list_output_images()
    return (paths, min(GALLERY_PAGE, len(paths)),
            *_gallery_view(paths, GALLERY_PAGE))


def _more_gallery(paths, shown):
    """Load-more: show one more page of what the last scan found.

    Pages out of the state rather than re-scanning, so paging back through
    a long history cannot renumber the tiles under a click.
    """
    shown = min(len(paths), int(shown or 0) + GALLERY_PAGE)
    return (shown, *_gallery_view(paths, shown))


def _pick_gallery(paths, evt: gr.SelectData):
    """Grid click → the full-resolution original behind the clicked tile.

    This is the whole point of the tab: tiles are 512px WebP, and the
    several-MB original is fetched only for the one image someone asked to
    see. Resolved by index against the state that built the grid — see
    _picked_recent for why the clicked tile's own path is not trustworthy.
    """
    idx = evt.index
    if not isinstance(idx, int) or not 0 <= idx < len(paths or []):
        # Stale click, e.g. a tile clicked after a refresh renumbered the
        # grid. The viewers keep what they have; the recipe panel is put
        # away, because the one thing worse than no recipe is a recipe
        # sitting under a heading naming a different picture. Cleared with
        # a real None rather than a gr.update() — this output is a
        # gr.State, whose value is whatever it is handed, update object
        # and all.
        return (gr.Image(), gr.Video(), gr.update(visible=False),
                gr.update(), None, gr.update(interactive=False))
    path = paths[idx]
    is_video = path.lower().endswith(gallery_index.VIDEO_EXT)
    # Read on the click rather than kept per tile: a gallery page is ten
    # files and the store is a dict, so this is a lookup, and doing it here
    # means a recipe written since the page was drawn is still found.
    recipe = recipes.for_path(path)
    usable = bool(recipe) and str(recipe.get("tab")) in _RECIPE_VIEWS
    return (gr.Image(value=None if is_video else path, visible=not is_video),
            gr.Video(value=path if is_video else None, visible=is_video),
            gr.update(visible=True,
                      label=f"🧾 How this was made — {Path(path).name}"),
            _recipe_body(recipe, path),
            recipe,
            gr.update(interactive=usable))


def zip_outputs():
    """Bundle all generated media into one zip (the pod disk is ephemeral)."""
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
    return str(zip_path), f"📦 Zipped {len(images)} file(s) ({size_mb:.0f} MB)"


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


def krea_model_changed(model_name, prompt_text):
    """Model dropdown → that model's step/CFG defaults, info line, and
    trigger words swapped into the prompt box."""
    entry = resolve_model_entry(model_name)
    steps, cfg = model_defaults(entry)
    return (gr.Slider(value=steps), gr.Slider(value=cfg),
            gr.Markdown(value=_model_info_text(entry)),
            gr.Textbox(value=_swap_trigger(prompt_text, entry)))


def _model_selector():
    """Model dropdown + info line (must be inside a gr.Blocks context).

    The caller wires .change() once its tab's steps/CFG sliders and
    prompt box exist.
    """
    dropdown = gr.Dropdown(choices=MODEL_CHOICES, value=MODEL_CHOICES[0],
                           label="Model")
    info = gr.Markdown(_model_info_text(resolve_model_entry(None)),
                       elem_classes="kx-meta")
    return dropdown, info


def _default_lora_slots() -> list:
    """Per-slot (name, weight) defaults from config.DEFAULT_LORAS.

    Only LoRAs that actually downloaded are pre-selected; missing files
    leave their slot at "None" so the UI never references a bad choice.
    """
    slots = [(name, weight) for name, weight in DEFAULT_LORAS
             if name in LORA_CHOICES][:MAX_LORA_SLOTS]
    slots += [("None", 0.8)] * (MAX_LORA_SLOTS - len(slots))
    return slots


def _lora_rows(slots, choices):
    """Render (dropdown, weight) rows for `slots`, collapsing the overflow.

    Slots past VISIBLE_LORA_SLOTS go inside a closed accordion so raising
    MAX_LORA_SLOTS does not push the Generate button off screen. The
    nesting is purely visual: the returned lists stay flat and in slot
    order, which is what _lora_inputs and the handlers' *lora_slots tail
    rely on. With MAX_LORA_SLOTS <= VISIBLE_LORA_SLOTS no accordion is
    created at all, so the layout is unchanged.
    """
    dds, ws = [], []

    def row(slot, name, weight):
        with gr.Row():
            dds.append(gr.Dropdown(choices=choices, value=name,
                                   label=f"LoRA {slot}", scale=3))
            ws.append(gr.Slider(0.0, 2.0, value=weight, step=0.05,
                                label="Weight", scale=1))

    for slot, (name, weight) in enumerate(slots[:VISIBLE_LORA_SLOTS], start=1):
        row(slot, name, weight)
    extra = slots[VISIBLE_LORA_SLOTS:]
    if extra:
        # Start open when a hidden slot is already in use, so an active
        # LoRA is never invisible behind a collapsed header.
        with gr.Accordion(
            f"➕ {len(extra)} more LoRA slot{'s' if len(extra) > 1 else ''}",
            open=any(name != "None" for name, _ in extra),
        ):
            for offset, (name, weight) in enumerate(extra):
                row(VISIBLE_LORA_SLOTS + offset + 1, name, weight)
    return dds, ws


def _lora_stack():
    """Krea 2 LoRA rows + rescan button (Single, Edit and Inpaint tabs).

    Must be called inside a gr.Blocks context. Returns (dropdowns, weights)
    in slot order; the rescan button refreshes its own tab's dropdowns.
    """
    gr.Markdown("### 🎭 LoRA stack", elem_classes="kx-section")
    dds, ws = _lora_rows(_default_lora_slots(), LORA_CHOICES)
    gr.Button("🔄 Rescan LoRA folder", size="sm").click(
        fn=refresh_lora_choices, outputs=dds)
    return dds, ws


def _flux_lora_stack():
    """The same stack for Flux, which has its own folder and choices."""
    gr.Markdown("### 🎭 Flux LoRA stack (`loras/flux2/`)",
                elem_classes="kx-section")
    dds, ws = _lora_rows([("None", 0.8)] * MAX_LORA_SLOTS, FLUX_LORA_CHOICES)
    gr.Button("🔄 Rescan Flux LoRA folder", size="sm").click(
        fn=refresh_flux_lora_choices, outputs=dds)
    return dds, ws


def _klein_lora_stack():
    """The Klein workflow's three LoRA rows, with the toggles it ships with.

    Fixed to the source workflow's stack rather than config.DEFAULT_LORAS,
    and each row carries an Enable checkbox because that is what rgthree's
    Power Lora Loader exposes — the same shape as _v2_lora_stack, over a
    different folder. Returns (checkboxes, dropdowns, weights) in slot order.
    """
    gr.Markdown(
        f"### 🎭 LoRA stack — model + CLIP (`loras/{KLEIN_LORA_SUBDIR}/`)\n"
        "The workflow's stack, in its original order, strengths and on/off "
        "states. Each strength applies to the model *and* the text encoder.",
        elem_classes="kx-section",
    )
    cbs, dds, ws = [], [], []
    for index, (on, name, strength) in enumerate(KLEIN_LORA_SLOTS):
        with gr.Row():
            cbs.append(gr.Checkbox(
                value=on, label="On", scale=0, min_width=70,
                interactive=name in KLEIN_LORA_CHOICES,
            ))
            dds.append(gr.Dropdown(
                choices=KLEIN_LORA_CHOICES,
                value=name if name in KLEIN_LORA_CHOICES else "None",
                label=f"LoRA {index + 1}", scale=3,
            ))
            ws.append(gr.Slider(0.0, 2.0, value=strength, step=0.01,
                                label="Strength", scale=1))
    gr.Button("🔄 Rescan Klein LoRA folder", size="sm").click(
        fn=refresh_klein_lora_choices, outputs=dds)
    return cbs, dds, ws


def _tab_intro(text: str):
    """A tab's opening paragraph, as a callout rather than loose body copy.

    Purely presentational: same markdown, rendered inside a bordered note
    so it reads as guidance and not as a control. The status lines the tabs
    append to their intro already speak in symbols — ❌ for "this tab cannot
    run", ⚠️ for "it can, but something is missing" — so the note takes its
    colour from whichever is present instead of leaving both to read as
    ordinary body copy.
    """
    classes = ["kx-note"]
    if "❌" in text:
        classes.append("kx-note-error")
    elif "⚠️" in text:
        classes.append("kx-note-warn")
    return gr.Markdown(text, elem_classes=classes)


def _publish_row():
    """The admin-only "publish this to the library" controls.

    Returns (checkbox, title) for a generate handler's `publish` and
    `publish_title` arguments. Both are **built either way and hidden for
    a customer**, never omitted: a tab's click() input list is fixed at
    build time, and one that changed shape with the licence would need
    two versions of every handler signature. Hidden components still send
    their value, so the handler reads False and "" and captures the way it
    always did.

    Hidden and not merely disabled, and that is the whole point of it: a
    customer is never told their prompts are saved, and a greyed-out
    "publish to the library" box tells them.
    """
    admin = licensing.is_admin()
    with gr.Row(visible=admin):
        checkbox = gr.Checkbox(
            label="⭐ Publish this prompt to the library", value=False,
            scale=0, min_width=280,
        )
        title = gr.Textbox(
            label="Card title (optional)", scale=1,
            placeholder="Golden hour portrait",
        )
    _recipe_skip(checkbox, title)
    return checkbox, title


def _preset_save_row():
    """The admin-only "save these settings as a preset" controls.

    The settings-side twin of _publish_row, built the same way and for the
    same reasons — both either way and hidden for a customer, because a
    tab's click() input list is fixed at build time and a hidden checkbox
    still sends False.

    Hiding rather than disabling is a weaker requirement here than it is
    for publishing (there is no secret to keep — customers can see the
    presets and are told where they come from), but a control nobody can
    use is still clutter, and the two rows sit together.
    """
    admin = licensing.is_admin()
    with gr.Row(visible=admin):
        checkbox = gr.Checkbox(
            label="💾 Save these settings as a preset", value=False,
            scale=0, min_width=280,
        )
        name = gr.Textbox(
            label="Preset name", scale=1,
            placeholder="Portrait · soft light",
            info="Always saved as a new preset — a name already in use "
                 "gets a “(2)”. Blank is stamped with the time.",
        )
    _recipe_skip(checkbox, name)
    return checkbox, name


def _reset_after_generate(event, publish, publish_title,
                          save_preset, preset_name):
    """Disarm the publish and preset boxes once the click has been taken.

    Both are per-run decisions, not modes. Left ticked they are silent —
    nothing on the next run says "this one is going to the library too" —
    so they disarm themselves instead of relying on the admin to
    remember. The two text boxes go with them, otherwise the next publish
    quietly inherits the last card's name.

    Chained off the generate click rather than registered as a second
    click handler, so it cannot land between the click and the payload the
    generate handler reads its `publish` value from. That ordering became
    *more* important once the click only queued the work: the values are
    copied into the job at submit time, so clearing the controls
    immediately afterwards is both safe and exactly right — the boxes are
    disarmed before the next click rather than at the end of a render
    that may be minutes away.

    Refreshing the preset dropdown is no longer part of this. The save now
    happens wherever the job runs, which is after this returns, so the
    poll that carries a job's images back into its tab refills the
    dropdown too — see _queue_tick.
    """
    return event.then(
        fn=_after_generate,
        outputs=[publish, publish_title, save_preset, preset_name],
    )


def _after_generate():
    """The reset itself — see _reset_after_generate."""
    return False, "", False, ""


def _preset_dropdown_update(tab):
    """Refill a tab's preset dropdown after a background job saved one.

    force=True: presets.save already dropped the cache, so this is the
    read that fills it again with the preset that was just written.

    Choices only, deliberately: naming a value here would move a
    selection the admin did not touch. A save never overwrites — a name
    already in use is stored as "… (2)" — so guessing which choice the new
    preset became would be wrong as often as right, and the dropdown
    naming the preset that is actually *loaded* stays true either way.
    """
    choices, _default = _preset_choices(tab, force=True)
    return gr.update(choices=choices)


# ----------------------------------------------------------------- recipes
# Every generation records what it was made with, and the Gallery tab reads
# it back — see recipes.py for the store. This half is the wiring: which
# controls a tab's recipe is made of, how a stored one is rendered, and how
# it goes back into the controls it came from.
#
# The trick that makes this one implementation rather than ten is that a
# tab's recipe *is* its click's `inputs` list. Recording it is zipping that
# list against the values Gradio just handed over, and restoring it is
# writing them back into the very same components — so a tab that grows a
# control gets it in its recipes with no change here.

# key → (tab id for switching to it, [that tab's input components]), in
# build order. A tab this licence does not grant never registers, so its
# recipes simply cannot be loaded — which is the correct answer, and the
# same one the prompt library gives for a card it cannot use.
_RECIPE_VIEWS = {}

# The two controls a recipe deliberately overrides on the way back in, by
# label. Every seeded tab labels them identically — see _use_recipe.
SEED_LABEL = "Seed"
RANDOM_SEED_LABEL = "🎲 Random seed"

# Controls whose value is a file the customer supplied rather than a
# setting: an uploaded image, a mask, a JSON file. They are recorded as
# None and skipped on the way back, and the panel says so rather than
# quietly restoring nine tenths of a recipe.
_UNRECORDED = (gr.Image, gr.ImageEditor, gr.File, gr.Gallery, gr.Video)

# Controls that sit in a click's inputs but are not settings: the publish
# and save-preset boxes. They are per-run decisions — see
# _reset_after_generate, which disarms them after every click — so putting
# them in a recipe would mean loading one silently re-arms a publish, which
# is the one thing that must never happen by accident.
_RECIPE_SKIP = set()


def _recipe_skip(*components):
    """Keep these controls out of every recipe, wherever they are used."""
    _RECIPE_SKIP.update(id(component) for component in components)


# member control → the control that decides whether it means anything. An
# empty LoRA slot is a dropdown reading "None" and a weight of 0.8, and
# printing eight rows of "Weight 0.8" under every recipe is eight rows of
# noise. Recorded either way, because restoring a slot has to restore all
# of it — this only decides what the panel is worth showing.
_RECIPE_GATES = {}


def _recipe_gate(gate, *members):
    """Show `members` in a recipe only when `gate` has a value."""
    _RECIPE_GATES.update({id(member): id(gate) for member in members})


def _recipe_view(key, tab_id, inputs):
    """Register a tab's controls as its recipe, and hand the list back.

    Returns `inputs` so the caller can use it directly as the click's
    `inputs=`, which is the point: the list is written once, and the thing
    that is recorded cannot drift from the thing that is submitted.
    """
    _RECIPE_VIEWS[str(key)] = (tab_id, list(inputs))
    return inputs


def _recipe_components():
    """Every registered control, flattened in the registry's order.

    The one definition of that order — _use_recipe returns its updates in
    it, and the button's outputs list is built from it.
    """
    return [component for _tab_id, components in _RECIPE_VIEWS.values()
            for component in components]


def _recordable(value):
    """Is this control value worth storing?

    An allowlist, not a json.dumps attempt. Every control that holds a
    *setting* answers with a string, a number or a bool; everything else
    is an uploaded file, and storing those would turn a few hundred bytes
    a picture into a second copy of the input.
    """
    return value is None or isinstance(value, (str, int, float, bool))


def _recipe_fields(components, values):
    """[[label, value], ...] for one click, in the tab's own control order.

    Positional, and it has to be: a LoRA stack is eight controls all
    labelled "Weight", so the order is the only thing that says which slot
    each one belongs to. The label rides along anyway, because it is what
    the panel shows and what _use_recipe checks before writing a value
    back into a build that may have moved on since.
    """
    fields = []
    for component, value in zip(components, values):
        label = (getattr(component, "label", None) or "").strip()
        keep = _recordable(value) and id(component) not in _RECIPE_SKIP
        fields.append([label, value if keep else None])
    return fields


def _recipe_pair(entry) -> bool:
    """Is this a usable [label, value] out of a stored recipe?

    The store is a file on a pod's disk that outlives the build that wrote
    it, so every reader treats a row as data of unknown shape rather than
    as something it can unpack.
    """
    return isinstance(entry, (list, tuple)) and len(entry) >= 2


def _recipe_at(fields, index):
    """The stored value at `index`, or None — see _recipe_pair."""
    entry = fields[index] if index < len(fields) else None
    return entry[1] if _recipe_pair(entry) else None


def _recipe_escape(text) -> str:
    """A value, safe to drop in a markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _recipe_body(recipe, path) -> str:
    """A stored recipe, as something readable under the selected file.

    Prompts are pulled out above the table as quotes and everything else
    goes in it. Which is which comes from the *live* control at that
    position rather than from anything stored: a text box is a text box,
    and a prompt reflowed into a table cell — where a newline cannot go —
    is exactly the thing someone opened this panel to read. The length
    test is the fallback for a recipe whose tab this licence does not
    grant, where there is no control left to ask.

    The seed rides in the heading and is deliberately kept out of the
    table. What the box held is not what the picture ran on — a batch
    walks consecutive seeds, and a random tick ignores the box entirely —
    so listing both would put two different seeds on one panel.
    """
    if not recipe:
        return (f"No recipe on file for **{Path(path).name}**.\n\n"
                "Only generations made since this pod started keeping them "
                "have one — anything older, or copied into the output "
                "folder by hand, cannot be traced back.")

    when = time.strftime("%d %b %Y, %H:%M",
                         time.localtime(recipe.get("at") or 0))
    head = f"**{recipe.get('tab_label') or 'Unknown tab'}** · {when}"
    seed = recipe.get("seed")
    if seed is not None:
        head += f" · seed `{seed}`"
    lines = [head]

    _tab_id, components = _RECIPE_VIEWS.get(str(recipe.get("tab")), (None, []))
    fields = recipe.get("fields") or []
    # id(control) → what it is set to, so a gated control can be looked up
    # by the member it gates. Only meaningful for a recipe from this build,
    # which is the only case where components is non-empty.
    live = {id(component): _recipe_at(fields, index)
            for index, component in enumerate(components)}
    quotes, table = [], []
    for index, entry in enumerate(fields):
        if not _recipe_pair(entry):
            continue
        label, value = entry[0], entry[1]
        if value is None or value == "" or value == "None" or not label:
            continue
        if label in (SEED_LABEL, RANDOM_SEED_LABEL):
            continue
        component = components[index] if index < len(components) else None
        gate = _RECIPE_GATES.get(id(component))
        if gate is not None and live.get(gate) in (None, "", "None"):
            continue                  # an empty slot's settings say nothing
        text = str(value)
        is_text = (isinstance(component, gr.Textbox) if component is not None
                   else len(text) > 60 or "\n" in text)
        (quotes if is_text else table).append((label, text))

    for label, text in quotes:
        body = "\n> ".join(text.strip().splitlines())
        lines.append(f"\n**{label}**\n\n> {body}")
    if table:
        lines.append("\n| Setting | Value |\n| --- | --- |")
        lines += [f"| {_recipe_escape(label)} | `{_recipe_escape(text)}` |"
                  for label, text in table]

    if str(recipe.get("tab")) not in _RECIPE_VIEWS:
        lines.append(f"\n⚠️ The **{recipe.get('tab_label')}** tab is not part "
                     "of this licence, so these settings can be read but not "
                     "loaded.")
    return "\n".join(lines)


def _recipe_update(component, value):
    """One stored value → an update for the control it belongs to.

    Guarded exactly the way the prompt library's `_pick` and `_num` are,
    and for the same reason: a recipe can outlive the build that wrote it.
    A dropdown handed a value outside its choices is a *broken* component
    rather than a wrong one, and a slider's range is a property of this
    build rather than of the recipe — so an unknown choice leaves the
    control alone and an out-of-range number is clamped into it.
    """
    if isinstance(component, (gr.Dropdown, gr.Radio)):
        choices = [choice[1] if isinstance(choice, (list, tuple)) else choice
                   for choice in (component.choices or [])]
        return gr.update(value=value) if value in choices else gr.update()
    if isinstance(component, gr.Checkbox):
        return gr.update(value=bool(value))
    if isinstance(component, (gr.Slider, gr.Number)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return gr.update()
        low, high = component.minimum, component.maximum
        if low is not None:
            number = max(float(low), number)
        if high is not None:
            number = min(float(high), number)
        return gr.update(value=number)
    if isinstance(component, gr.Textbox):
        return gr.update(value="" if value is None else str(value))
    return gr.update()


def _use_recipe(recipe):
    """▶️ Load these settings — put a stored recipe back into its tab.

    Writes updates for *every* registered tab's controls, because the
    outputs list is fixed at build time; the tabs a recipe is not for get
    bare gr.update()s, which change nothing. Same shape as _use_prompt,
    and for the same reason.

    Two values are deliberately not restored as recorded. **Seed** becomes
    the seed the picture actually ran on rather than whatever was in the
    box, and **🎲 Random seed** is switched off — together they are the
    difference between "the same settings" and "the same image", which is
    what someone clicking this is asking for.
    """
    recipe = recipe or {}
    tab = str(recipe.get("tab") or "")
    fields = recipe.get("fields") or []
    seed = recipe.get("seed")

    updates, selected, missing = [], gr.update(), 0
    for key, (tab_id, components) in _RECIPE_VIEWS.items():
        if key != tab:
            updates.extend([gr.update()] * len(components))
            continue
        if tab_id:
            selected = gr.update(selected=tab_id)
        for index, component in enumerate(components):
            label = (getattr(component, "label", None) or "").strip()
            stored = fields[index] if index < len(fields) else None
            if not _recipe_pair(stored) or stored[0] != label:
                # The recipe was written by a build whose controls sat in a
                # different order. Rather than guess, that one control keeps
                # what it has — the rest of the recipe still loads.
                updates.append(gr.update())
                continue
            value = stored[1]
            if label == SEED_LABEL and seed is not None:
                value = seed
            elif label == RANDOM_SEED_LABEL:
                value = False
            if value is None and isinstance(component, _UNRECORDED):
                missing += 1
            updates.append(_recipe_update(component, value))

    if not tab:
        message = "⚠️ Nothing to load — pick an image first."
    elif tab not in _RECIPE_VIEWS:
        message = (f"⚠️ **{recipe.get('tab_label')}** is not part of this "
                   "licence, so its settings cannot be loaded.")
    else:
        message = f"✅ Loaded into **{recipe.get('tab_label')}**."
        if seed is not None:
            message = (f"✅ Loaded into **{recipe.get('tab_label')}** on seed "
                       f"`{seed}`, with random seed switched off — so it "
                       "renders the same image.")
        if missing:
            message += (f" The {missing} uploaded file(s) it used are not "
                        "stored — pick those again before generating.")
    return (*updates, selected, message)


# ------------------------------------------------------------------- queue
# Clicking Generate does not generate. It writes the click down — the
# handler, the values every control held, the tab it came from — and hands
# it to jobqueue, which runs one job at a time per lane on a worker
# thread. Three things follow from that, and all three are the point:
#
#   * the button comes back immediately, so a second idea can be queued
#     while the first renders instead of waiting for a human to be sitting
#     there when it finishes
#   * the queue is a list of real objects, so it can be shown
#   * and a thing that can be shown can have a ✕ next to it
#
# What a click cannot do any more is push its own results, because it is
# over long before there are any. So the panel below carries a gr.Timer,
# and one poll a second redraws the queue *and* writes each job's latest
# output back into the tab that submitted it (_queue_tick). Every tab
# registers its output components here on the way past.

# The row pool. Fixed, because a Blocks tree is built once at import and
# cannot grow a component later — the same constraint, and the same
# answer, as the prompt library's LIBRARY_CARDS.
QUEUE_ROWS = 12
# How often the browser asks what happened. Fast enough that a status line
# reads as live, and the timer stops itself the moment nothing is running
# (see _queue_tick), so an idle pod is not polled at all.
QUEUE_POLL_SECONDS = 1.0

# (feature key, [components the tab's handler yields into]), filled as the
# tabs are built and read once, after the loop, to lay out _queue_tick's
# outputs. A tab this licence does not grant never registers, so it costs
# nothing here either.
_QUEUE_VIEWS = []
# (presets tab id, its dropdown) for the two tabs that can save one.
_QUEUE_PRESET_VIEWS = []


def _queue_view(key, outputs, preset_tab=None, preset_dd=None):
    """Register a tab's outputs as where its jobs' results are written."""
    _QUEUE_VIEWS.append((str(key), list(outputs)))
    if preset_dd is not None:
        _QUEUE_PRESET_VIEWS.append((preset_tab, preset_dd))


def _job_title(args, index):
    """A one-line name for a queued job — its prompt, where it has one.

    Truncated hard: the queue is a list to scan, not a place to read a
    prompt back. Tabs whose work has no prompt at all (Face Swap, the JSON
    batch) pass index=None and get a dash, which is honest — what
    identifies those jobs is their tab and their place in the line.
    """
    if index is None or index >= len(args):
        return "—"
    text = " ".join(str(args[index] or "").split())
    if not text:
        return "—"
    return text[:59] + "…" if len(text) > 60 else text


def _enqueue(key, fn, *, prompt_arg=None, status_index=1, lane=None):
    """Turn a tab's handler into the click that only queues it.

    `fn` is untouched — the same generator the click used to consume — so
    what a tab does once its turn comes is exactly what it always did. All
    this adds is the recording, and the recording is a copy: the values
    are read out of the controls now, so changing them afterwards cannot
    reach a job that is already in the line.

    Returns two values because every tab has a status box to say where the
    work landed, and because the timer has to be switched on — a poll that
    is off would leave the queue looking empty until something else woke
    it.
    """
    lane = lane or COMFY_LANE

    def submit(*args):
        view = jobqueue.submit(
            lane=lane, tab=str(key), tab_label=features.label_for(key),
            title=_job_title(args, prompt_arg),
            fn=_recording(key, fn, args), args=args,
            status_index=status_index,
        )
        ahead = max(0, view.place - 1)
        line = ("🕑 Queued — it starts as soon as the GPU is free."
                if ahead == 0 else
                f"🕑 Queued — {ahead} job(s) ahead of it.")
        return gr.Timer(QUEUE_POLL_SECONDS, active=True), line

    return submit


def _recording(key, fn, args):
    """`fn`, wrapped so whatever it writes is filed under a recipe.

    The wrapper exists to move one line onto the *worker* thread. The
    values were read from the controls back on the click, but the recipe
    has to be announced from the thread that will do the generating —
    recipes keys it by thread, so that the output hook deep inside
    client.run can find it without every executor having to pass it down
    (see recipes.py).

    A generator, because the handlers are: `yield from` keeps the tab's
    own progress reporting exactly as it was, and the `finally` runs on a
    cancelled job too, since closing a generator raises GeneratorExit at
    its current yield.
    """
    tab_id, components = _RECIPE_VIEWS.get(str(key), (None, []))
    fields = _recipe_fields(components, args)

    def run(*call_args):
        recipes.begin(str(key), features.label_for(key), tab_id, fields)
        try:
            yield from fn(*call_args)
        finally:
            recipes.end()

    return run


_QUEUE_ICONS = {
    jobqueue.QUEUED: "🕑",
    jobqueue.RUNNING: "⚡",
    jobqueue.DONE: "✅",
    jobqueue.FAILED: "❌",
    jobqueue.CANCELLED: "🚫",
}


def _queue_row_body(view) -> str:
    """One row's markup: what it is, where it came from, how it is going."""
    where = (f"#{view.place} in line" if view.status == jobqueue.QUEUED
             else view.status.capitalize())
    detail = " ".join((view.progress or "").split())
    if len(detail) > 110:
        detail = detail[:109].rstrip() + "…"
    return (f"{_QUEUE_ICONS.get(view.status, '•')} **{view.tab_label}** · "
            f"{where}\n\n{view.title}\n\n<small>{detail}</small>")


def _queue_row_button(view):
    """The ✕. What it means depends entirely on what the job is doing.

    A waiting job is simply forgotten. A running one has a prompt on the
    ComfyUI server that has to be interrupted, which is slower and less
    certain — so it says Stop rather than pretending otherwise. A finished
    job's button only tidies the list.
    """
    if view.status == jobqueue.QUEUED:
        return gr.update(value="✕ Remove", variant="secondary")
    if view.status == jobqueue.RUNNING:
        return gr.update(value="🛑 Stop", variant="stop")
    return gr.update(value="✕ Clear", variant="secondary")


def _queue_panel(rows, waiting, running):
    """The whole panel for one snapshot, in _queue_tick's output order."""
    # Newest end of the list: the pool is smaller than jobqueue.HISTORY, and
    # what has just been queued matters more than what finished an hour ago.
    shown = rows[-QUEUE_ROWS:]
    if running and waiting:
        label = f"🗂️ Queue — {running} running · {waiting} waiting"
    elif running:
        label = f"🗂️ Queue — {running} running"
    elif waiting:
        label = f"🗂️ Queue — {waiting} waiting"
    else:
        label = "🗂️ Queue — nothing running"

    if not rows:
        info = ("Nothing queued. Every **Generate** click is added here, so "
                "several can be lined up at once — and any of them taken "
                "back out again.")
    elif len(rows) > QUEUE_ROWS:
        info = f"Showing the most recent **{QUEUE_ROWS}** of {len(rows)}."
    else:
        info = f"**{len(rows)}** job(s)."

    groups, bodies, buttons = [], [], []
    for slot in range(QUEUE_ROWS):
        view = shown[slot] if slot < len(shown) else None
        groups.append(gr.update(visible=view is not None))
        bodies.append(gr.update(value=_queue_row_body(view)) if view
                      else gr.update())
        buttons.append(_queue_row_button(view) if view else gr.update())

    return (gr.update(label=label), [view.id for view in shown],
            gr.update(value=info), *groups, *bodies, *buttons)


def _changed(previous, current) -> bool:
    """Is this output value different from the one already on screen?

    Guards the gallery specifically. A running job stamps a new revision
    on every sampler step, so without this each poll would hand the
    gallery the same list of paths again and it would re-render a second
    later — a visible flicker under a job that has produced no new image
    in a minute. Anything that cannot be compared counts as changed, which
    costs at worst one wasted update.
    """
    try:
        return previous != current
    except Exception:      # noqa: BLE001 - an odd value is not worth raising over
        return True


def _queue_tick(seen):
    """One poll: redraw the queue, and carry results back into their tabs.

    `seen` is per-browser-session state — the revisions this session has
    already drawn, and the values it last pushed to each tab. The queue
    itself is process-wide, because there is one GPU behind it; what a
    given page has rendered is not, so every open page keeps its own.

    Returns gr.update() for everything that has not moved, which is nearly
    everything on nearly every poll: a tab whose job finished ten minutes
    ago must not have its gallery rewritten once a second.
    """
    seen = dict(seen or {})
    revision, rows, waiting, running = jobqueue.snapshot()

    if seen.get("queue") == revision:
        panel = (gr.update(), [view.id for view in rows[-QUEUE_ROWS:]],
                 gr.update(), *([gr.update()] * (3 * QUEUE_ROWS)))
    else:
        seen["queue"] = revision
        panel = _queue_panel(rows, waiting, running)

    tabs = []
    for tab, components in _QUEUE_VIEWS:
        stamp, result = jobqueue.display_for(tab)
        if result is None or seen.get(("tab", tab)) == stamp:
            tabs.extend([gr.update()] * len(components))
            continue
        seen[("tab", tab)] = stamp
        previous = seen.get(("value", tab)) or []
        # A handler may yield fewer values than the tab has outputs — an
        # early "❌ that model is not downloaded" bails before it knows a
        # seed — and those trailing components keep what they are showing.
        values, updates = [], []
        for index in range(len(components)):
            if index >= len(result):
                values.append(previous[index] if index < len(previous)
                              else None)
                updates.append(gr.update())
                continue
            value = result[index]
            values.append(value)
            updates.append(
                value if index >= len(previous)
                or _changed(previous[index], value) else gr.update()
            )
        seen[("value", tab)] = values
        tabs.extend(updates)

    presets_out = []
    for preset_tab, _dropdown in _QUEUE_PRESET_VIEWS:
        stamp = jobqueue.preset_revision(preset_tab)
        if not stamp or seen.get(("preset", preset_tab)) == stamp:
            presets_out.append(gr.update())
            continue
        seen[("preset", preset_tab)] = stamp
        presets_out.append(_preset_dropdown_update(preset_tab))

    # Idle means idle: the poll that observed the last job finish also
    # pushed its results, so there is nothing left to ask about. A Generate
    # click switches the timer back on, and so does opening the page.
    ticking = gr.Timer(QUEUE_POLL_SECONDS,
                       active=bool(waiting or running))
    return (seen, ticking, *panel, *tabs, *presets_out)


def _queue_cancel(slot, ids, seen):
    """A row's ✕ — see jobqueue.cancel, then redraw from the new truth."""
    if ids and slot < len(ids):
        log.info("Queue: %s", jobqueue.cancel(ids[slot]))
    return _queue_tick(seen)


def _queue_clear(seen):
    """🧹 Clear finished — drops the finished rows, touches nothing live."""
    message = jobqueue.clear_finished()
    if message:
        log.info("Queue: %s", message)
    return _queue_tick(seen)


def _queue_wake():
    """Page load: start polling, in case work is already running.

    The queue outlives any one page — it is the pod's, not the browser's —
    so a page opened while a job is half done has to find that out, and a
    timer that starts off would never ask.
    """
    return gr.Timer(QUEUE_POLL_SECONDS, active=True)


def _cta(label: str):
    """A tab's primary action button — Generate, Edit, Swap face, ...

    One per tab, styled as the one thing on the screen worth clicking.
    kx-cta also keeps it pinned to the bottom of the viewport while its
    control column is on screen, which matters because several of those
    columns are two screens tall with the LoRA stack open.
    """
    return gr.Button(label, variant="primary", size="lg",
                     elem_classes="kx-cta")


def _status_box():
    """The read-only status line every generation tab reports through."""
    return gr.Textbox(label="Status", interactive=False,
                      elem_classes="kx-status")


def _pricing_body(force: bool = False) -> str:
    """The pricing panel's markup, over a catalogue fresh enough to show.

    Called when the panel is opened, never at import time, and both halves
    of that matter. ui.py builds its Blocks while the pod is still
    starting, so a licence server that is slow or unreachable must not be
    able to hold startup up; and a customer who never opens the panel
    never causes the request at all.

    plans.catalogue() reports failure rather than raising, so the unhappy
    paths — no node tag, server down, empty catalogue — all arrive here as
    a Catalogue carrying `error` and render as a panel saying so.
    """
    plan_id, plan_name = licensing.plan()
    return theme.pricing_html(
        plans.catalogue(force=force),
        current_plan_id=plan_id, current_plan_name=plan_name,
    )


def _open_pricing():
    """Swap the tabs for the pricing panel, filling it on the way in.

    Fetching here rather than at page load is what keeps the licence
    server off the startup path — see _pricing_body.
    """
    return (gr.update(visible=False),      # the header's "Plans & pricing"
            gr.update(visible=False),      #   button, and the tabs
            gr.update(visible=False),      # the queue panel, which is about
            gr.update(visible=False),      #   running tabs, and the footer,
            gr.update(visible=True),       #   whose hint is too
            _pricing_body())


def _close_pricing():
    """Put the tabs back. The panel keeps its markup for the next open."""
    return (gr.update(visible=True), gr.update(visible=True),
            gr.update(visible=True), gr.update(visible=True),
            gr.update(visible=False))


# ------------------------------------------------------------ prompt library
# Cards are built from real components rather than one gr.HTML block — the
# opposite call to the pricing panel, and for the one reason that matters:
# every card carries a button that has to write values into another tab's
# controls, and markup cannot do that.
#
# The pool is fixed at LIBRARY_CARDS and shown or hidden per page, because
# a Blocks tree is built once at import and cannot grow a component later.
LIBRARY_CARDS = 12

# Filter labels → what prompts.library() wants. The dicts are the single
# source for both, so a radio and its query cannot drift apart.
_LIB_TABS = {
    "Everything": None,
    "🎨 Krea2": prompts.TAB_KREA2,
    "🔶 Krea2 V2": prompts.TAB_KREA2_V2,
}
_LIB_SOURCES = {
    "All prompts": None,
    "⭐ Official": "admin",
    "👥 Community": "community",
}
_LIB_TAB_NAMES = {prompts.TAB_KREA2: "Krea2", prompts.TAB_KREA2_V2: "Krea2 V2"}


def _sub(settings, key):
    """A nested settings dict, or an empty one for anything else.

    Not the same check as `or {}`: these blobs are stored verbatim on the
    licence server and come back as whatever is in Mongo, so a V2 row
    holding a *string* where `sampler` should be a dict is a shape this
    has to survive rather than a shape it can assume away. Every reader
    below then gets .get() on a dict and the card still renders.
    """
    value = settings.get(key)
    return value if isinstance(value, dict) else {}


def _rows(settings):
    """The `loras` list, or an empty one — same reasoning as _sub."""
    value = settings.get("loras")
    return value if isinstance(value, list) else []


def _pick(value, choices):
    """Set a dropdown, or leave it alone if this pod has no such choice.

    The whole cross-pod safety story in one function. A prompt is written
    on someone else's pod, which may have models, LoRA files or a build
    this one does not — and a Gradio dropdown handed a value outside its
    `choices` is a broken component, not a wrong one. Leaving the control
    where it was is always safe and always renders.
    """
    return gr.update(value=value) if value in choices else gr.update()


def _num(value, lo, hi):
    """Set a slider/number, clamped into range; no-op for a non-number.

    Ranges are a property of this build, not of the prompt, so a value
    from a version whose slider went further is clamped rather than
    dropped — the recipe stays as close as this UI can express it.
    """
    try:
        return gr.update(value=max(lo, min(hi, float(value))))
    except (TypeError, ValueError):
        return gr.update()


def _lora_updates(rows, count, choices, default_weight=0.8):
    """(names, weights) updates for a plain LoRA stack, padded to `count`.

    A file this pod does not have becomes "None" *explicitly* rather than
    being left alone: the slots are being reset to a whole other recipe,
    and a leftover LoRA from whatever was loaded before would silently
    join it.
    """
    names, weights = [], []
    for index in range(count):
        row = rows[index] if index < len(rows) else None
        name = row[0] if row else None
        names.append(gr.update(value=name if name in choices else "None"))
        weight = row[1] if row and len(row) > 1 else default_weight
        weights.append(_num(weight, 0.0, 2.0))
    return names, weights


def _krea_updates(entry):
    """A library prompt → updates for every Krea 2 control, in target order."""
    return [
        gr.update(value=entry.prompt),
        gr.update(value=entry.negative or ""),
        *_krea_setting_updates(entry.settings or {}),
    ]


def _krea_setting_updates(settings):
    """The same, minus the two prompt boxes — the settings alone.

    Split out because a **preset** is exactly this and nothing else: the
    dials without the words. Both readers hand over the same blob (see
    generate_single), so the guarding — unknown model left alone, missing
    LoRA file blanked, numbers clamped — is written once and covers a
    preset written on this pod and a prompt written on someone else's
    alike.
    """
    names, weights = _lora_updates(_rows(settings), MAX_LORA_SLOTS,
                                   LORA_CHOICES)
    return [
        _pick(settings.get("model"), MODEL_CHOICES),
        _num(settings.get("steps"), 1, 60),
        _num(settings.get("cfg"), 0.5, 8.0),
        _pick(settings.get("resolution"), list(RESOLUTION_PRESETS)),
        _pick(settings.get("sampler"), SAMPLERS),
        _num(settings.get("seed"), 0, 2**32 - 1),
        gr.update(value=bool(settings.get("randomize", True))),
        _num(settings.get("batch_count", 1), 1, 20),
        *names, *weights,
    ]


def _v2_updates(entry):
    """A library prompt → updates for every Krea 2 V2 control, in order."""
    return [
        gr.update(value=entry.prompt),
        gr.update(value=entry.negative or ""),
        *_v2_setting_updates(entry.settings or {}),
    ]


def _v2_setting_updates(settings):
    """The settings half of the above — what a preset carries."""
    sampler = _sub(settings, "sampler")
    variance = _sub(settings, "variance")

    # V2's rows carry an on/off checkbox, so they are triples rather than
    # the pairs _lora_updates handles. A row naming a file this pod does
    # not have is switched off as well as blanked — leaving it ticked
    # would apply "None" at a strength, which reads as a stack that did
    # not load.
    rows = _rows(settings)
    enables, names, weights = [], [], []
    for index in range(len(V2_LORA_SLOTS)):
        row = rows[index] if index < len(rows) else None
        name = row[1] if row and len(row) > 1 else None
        known = name in V2_LORA_CHOICES
        enables.append(gr.update(value=bool(row[0]) if row and known else False))
        names.append(gr.update(value=name if known else "None"))
        weights.append(_num(row[2] if row and len(row) > 2 else 1.0, 0.0, 2.0))

    return [
        _pick(settings.get("model"), V2_MODEL_CHOICES),
        _pick(settings.get("aspect"), list(V2_ASPECT_RATIOS)),
        _num(settings.get("megapixels"), 0.5, 4.0),
        _num(settings.get("multiple"), 8, 64),
        _num(settings.get("seed"), 0, 2**32 - 1),
        gr.update(value=bool(settings.get("randomize", True))),
        _num(settings.get("batch_count", 1), 1, 20),
        _num(sampler.get("eta"), 0.0, 2.0),
        # Both of these are allow_custom_value dropdowns — RES4LYF builds
        # its lists at load time, so a name this build does not list is
        # still a name the node may well accept.
        gr.update(value=sampler["sampler_name"]) if sampler.get("sampler_name")
        else gr.update(),
        gr.update(value=sampler["scheduler"]) if sampler.get("scheduler")
        else gr.update(),
        _num(sampler.get("steps"), 1, 100),
        _num(sampler.get("denoise"), 0.0, 1.0),
        _num(sampler.get("cfg"), 0.0, 20.0),
        _pick(sampler.get("sampler_mode"), V2_SAMPLER_MODES),
        gr.update(value=bool(sampler.get("bongmath", True))),
        _pick(variance.get("variance_preset"), V2_VARIANCE_PRESETS),
        _num(variance.get("fine_tune_variance"), 0, 100),
        _pick(variance.get("model_type"), V2_VARIANCE_MODEL_TYPES),
        _pick(variance.get("variance_schedule"), V2_VARIANCE_SCHEDULES),
        _num(variance.get("cutoff_step"), 0, 100),
        _num(variance.get("total_steps"), 1, 100),
        _num(variance.get("cutoff_strength"), 0.0, 1.0),
        _num(variance.get("shift_strength"), 0, 200),
        gr.update(value=bool(settings.get("sharpen", False))),
        gr.update(value=bool(settings.get("film_grain", False))),
        *enables, *names, *weights,
    ]


# ---------------------------------------------------------- settings presets
# The dropdown at the top of the two generation tabs. A preset is one of
# those tabs' control panels saved under a name on the licence server
# (presets.py), so this section is the prompt library's machinery pointed at
# a different source: the same _pick/_num/_lora_updates guarding, the same
# "apply a settings blob to a list of components" shape, minus the two
# prompt boxes.
#
# Which builder belongs to which tab. Read at apply time rather than
# branched on with an if, so adding a third tab is an entry here plus its
# targets — the same trick TAB_ORDER plays.
_PRESET_UPDATES = {
    presets.TAB_KREA2: _krea_setting_updates,
    presets.TAB_KREA2_V2: _v2_setting_updates,
}

# How many of a tab's targets are prompt boxes. Both lists start with the
# positive prompt and the negative, and everything after them is settings —
# which is exactly the difference between what the prompt library writes
# into a tab and what a preset does.
PROMPT_BOXES = 2


def _preset_choices(tab, force=False):
    """(names, the default's name or None) for one tab's dropdown.

    Only presets the server marks `enabled` ever get here, and the default
    is only the one explicitly flagged: with nothing flagged the dropdown
    opens blank and the controls keep the values config.py compiled into
    them, which is the right answer for a deployment that has not seeded
    any presets.

    A failed read is an empty list and nothing else — no message, because
    a preset list that cannot be reached says nothing about whether this
    pod can generate, and the tab is perfectly usable at its built-in
    defaults. presets.catalogue has already logged the reason once.
    """
    rows = presets.catalogue(force=force).for_tab(tab)
    default = next((row.name for row in rows if row.is_default), None)
    return [row.name for row in rows], default


def _preset_settings(tab, name):
    """One preset's settings blob by name, or None if it is not on offer."""
    for row in presets.catalogue().for_tab(tab):
        if row.name == name:
            return row.settings
    return None


def _apply_preset(tab, count, name):
    """A preset name → updates for that tab's settings controls.

    `count` is how many controls the caller wired as outputs, which is what
    the "nothing to apply" answer has to be as long as. A name that is not
    on offer — a preset disabled or renamed while this page sat open — is
    exactly that case: changing nothing is the honest response, and the
    controls keep whatever the customer had set.

    One wrinkle, shared with the prompt library's Use button. Applying a
    preset that names a *different* model fires the Model dropdown's own
    change handler, which resets Steps and CFG to that model's variant
    defaults — so those two land on the model's values rather than the
    preset's. Everything else applies as stored. A preset for the model
    already selected (the ordinary case, and the only one when a registry
    holds a single model) is unaffected, because a value that does not
    change fires nothing.
    """
    settings = _preset_settings(tab, name) if name else None
    if not isinstance(settings, dict):
        return [gr.update()] * count
    return _PRESET_UPDATES[tab](settings)


def _load_default_presets():
    """Page load → each tab's default preset applied to its controls.

    Both tabs every time, in the order the outputs list is built after the
    TAB_ORDER loop; a tab this licence does not grant contributes nothing
    to that list and nothing here. With no default flagged, or with the
    server unreachable, every update is a no-op and the controls keep the
    values config.py built them with.

    This is what makes "the defaults" editable from Atlas: seed the shipped
    values as a preset, mark it default, and a fresh session opens on
    whatever that row says instead of on what the binary was compiled with.
    """
    updates = []
    for tab, targets in ((presets.TAB_KREA2, _krea_preset_targets),
                         (presets.TAB_KREA2_V2, _v2_preset_targets)):
        if not targets:
            continue
        _choices, default = _preset_choices(tab)
        updates += _apply_preset(tab, len(targets), default)
    return updates


def _refresh_presets(tab, current):
    """🔄 next to the dropdown — refetch the list, keep the selection.

    force=True skips presets.TTL_SECONDS, which is the whole point of the
    button: it exists for the minute after a preset is saved or switched
    off somewhere else. The current selection survives if it is still on
    offer and is cleared if it is not, rather than being left naming a
    preset this dropdown no longer has.
    """
    choices, _default = _preset_choices(tab, force=True)
    return gr.update(choices=choices,
                     value=current if current in choices else None)


def _preset_row(tab):
    """The preset dropdown and its refresh button, for one tab.

    Built at the top of the control column because it is the control that
    moves every other one. Filled at build time rather than lazily: the
    dropdown has to have its choices before the page renders, and the pod
    has already talked to the licence server to take its seat by the time
    ui.py is imported. A server that is unreachable costs the list and
    nothing else — see _preset_choices.
    """
    choices, default = _preset_choices(tab)
    with gr.Row():
        dropdown = gr.Dropdown(
            choices=choices, value=default, label="⚙️ Preset", scale=1,
            info="Loads every setting below. Your prompt is left alone.",
        )
        refresh = gr.Button("🔄", scale=0, min_width=60)
    return dropdown, refresh


def _card_chips(entry) -> str:
    """The one-line settings summary under a card's prompt text."""
    settings = entry.settings or {}
    chips = [_LIB_TAB_NAMES.get(entry.tab, entry.tab)]
    if settings.get("model"):
        chips.append(str(settings["model"]))
    if entry.tab == prompts.TAB_KREA2:
        chips += [f"{settings.get('steps', '?')} steps",
                  f"CFG {settings.get('cfg', '?')}"]
        if settings.get("resolution"):
            chips.append(str(settings["resolution"]))
        live = [row for row in _rows(settings)
                if row and row[0] not in (None, "None")]
    else:
        sampler = _sub(settings, "sampler")
        chips += [f"{sampler.get('steps', '?')} steps",
                  f"CFG {sampler.get('cfg', '?')}"]
        if settings.get("aspect"):
            # Just the ratio — the labels read "3:4 (Portrait Standard)".
            chips.append(str(settings["aspect"]).split(" ")[0])
        live = [row for row in _rows(settings)
                if row and len(row) > 2 and row[0]]
    if live:
        chips.append(f"{len(live)} LoRA" + ("s" if len(live) > 1 else ""))
    return " · ".join(f"`{chip}`" for chip in chips)


def _card_body(entry) -> str:
    """One card's markdown: what it is, what it says, what it is set to."""
    heading = (f"⭐ **{entry.title}**" if entry.title
               else "⭐ **Official prompt**" if entry.is_official
               else "👥 **Community prompt**")
    text = entry.prompt.strip().replace("\n", " ")
    if len(text) > 260:
        text = text[:259].rstrip() + "…"
    return f"{heading}\n\n> {text}\n\n{_card_chips(entry)}"


def _card_button(entry):
    """The Use button for one card — or the reason it cannot be used.

    A card for a tab this licence does not grant still renders, and says
    plainly why the button is dead. Hiding it would be worse: what the
    tab you have not bought can do is exactly the thing worth seeing, and
    it is the same argument the pricing panel makes.
    """
    targets = _krea_targets if entry.tab == prompts.TAB_KREA2 else _v2_targets
    name = _LIB_TAB_NAMES.get(entry.tab, entry.tab)
    if targets is None:
        return gr.update(value=f"🔒 Needs {name}", interactive=False)
    return gr.update(value=f"▶️ Use in {name}", interactive=True)


def _library_render(tab_label, source_label, search, page, force=False):
    """One page of the library → every card, the pager and the state.

    Returns a flat tuple in the order the outputs list is built below:
    rows state, page state, the info line, prev/next, then the card
    groups, bodies and buttons.
    """
    page = max(0, int(page or 0))
    result = prompts.library(
        tab=_LIB_TABS.get(tab_label),
        source=_LIB_SOURCES.get(source_label),
        search=(search or "").strip(),
        skip=page * LIBRARY_CARDS, limit=LIBRARY_CARDS, force=force,
    )
    # A filter can shrink the library under a page number that was fine a
    # moment ago. Landing on an empty page reads as "no prompts", which is
    # wrong and looks broken — go back to the first one instead.
    if not result.prompts and result.error is None and page > 0:
        page = 0
        result = prompts.library(
            tab=_LIB_TABS.get(tab_label),
            source=_LIB_SOURCES.get(source_label),
            search=(search or "").strip(),
            skip=0, limit=LIBRARY_CARDS, force=force,
        )

    rows = list(result.prompts)[:LIBRARY_CARDS]
    first = page * LIBRARY_CARDS

    if result.error:
        # Warn, not error: the library being unreadable says nothing about
        # whether this pod can generate, which it plainly can.
        info = f"⚠️ {result.error}"
    elif not rows:
        info = ("No prompts here yet. Try **Everything** in both filters, or "
                "clear the search box.")
    else:
        info = (f"Showing **{first + 1}–{first + len(rows)}** of "
                f"**{result.total}**")

    groups, bodies, buttons = [], [], []
    for slot in range(LIBRARY_CARDS):
        entry = rows[slot] if slot < len(rows) else None
        groups.append(gr.update(visible=entry is not None))
        bodies.append(gr.update(value=_card_body(entry)) if entry
                      else gr.update())
        buttons.append(_card_button(entry) if entry else gr.update())

    return (
        rows, page, info,
        gr.update(interactive=page > 0),
        gr.update(interactive=first + len(rows) < result.total),
        *groups, *bodies, *buttons,
    )


def _library_first(tab_label, source_label, search):
    """A filter or search changed — always back to page one."""
    return _library_render(tab_label, source_label, search, 0)


def _library_refresh(tab_label, source_label, search, page):
    """🔄 Refresh — skip the TTL so a just-approved prompt shows up."""
    return _library_render(tab_label, source_label, search, page, force=True)


def _library_prev(tab_label, source_label, search, page):
    return _library_render(tab_label, source_label, search, int(page or 0) - 1)


def _library_next(tab_label, source_label, search, page):
    return _library_render(tab_label, source_label, search, int(page or 0) + 1)


def _use_prompt(slot, rows):
    """Load card `slot` into its tab, and switch to it.

    Returns updates for *both* generation tabs' controls every time — the
    outputs list is fixed at build time, so the tab that is not being
    loaded gets a bare gr.update(), which changes nothing. A card whose
    tab is not licensed cannot reach here (its button is dead), but it is
    guarded anyway: `rows` is client state, and this is what happens if
    it arrives stale.
    """
    krea = [gr.update()] * len(_krea_targets or [])
    v2 = [gr.update()] * len(_v2_targets or [])
    selected = gr.update()

    entry = rows[slot] if rows and slot < len(rows) else None
    if entry is not None:
        if entry.tab == prompts.TAB_KREA2 and _krea_targets:
            krea, selected = _krea_updates(entry), gr.update(selected="krea2")
        elif entry.tab == prompts.TAB_KREA2_V2 and _v2_targets:
            v2, selected = _v2_updates(entry), gr.update(selected="krea2v2")

    return (*krea, *v2, selected)



# ── Tab builders ─────────────────────────────────────────────────────────────
#
# One function per tab, each building its own body into whichever
# gr.Tab context the TAB_ORDER loop below has opened. `tab` is that
# context object, needed only by the tabs that wire an event on it.
#
# A builder returns whatever the rest of the page needs from it — the
# two generation tabs return the control lists the Prompt Library writes
# into, the library returns the state and buttons that wiring attaches
# to, and everything else returns None because nothing outside it cares.


def _tab_krea_t2i(tab):
    """The KREA_T2I tab body."""
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            preset_dd, preset_refresh = _preset_row(presets.TAB_KREA2)
            prompt_box = gr.Textbox(
                label="Prompt", lines=5,
                value="A photorealistic golden-hour portrait, natural "
                      "skin texture, shallow depth of field",
            )
            negative_box = gr.Textbox(
                label="Negative prompt (only used when CFG > 1)", lines=2
            )
            model_dd, model_info = _model_selector()
            with gr.Row():
                steps_slider = gr.Slider(
                    1, 60, value=DEFAULTS["steps"], step=1, label="Steps"
                )
                cfg_slider = gr.Slider(
                    0.5, 8.0, value=DEFAULTS["cfg"], step=0.1, label="CFG"
                )
            model_dd.change(
                fn=krea_model_changed,
                inputs=[model_dd, prompt_box],
                outputs=[steps_slider, cfg_slider, model_info,
                         prompt_box],
            )
            with gr.Row():
                resolution_dd = gr.Dropdown(
                    choices=list(RESOLUTION_PRESETS),
                    value=DEFAULT_RESOLUTION, label="Resolution",
                )
                sampler_dd = gr.Dropdown(
                    choices=SAMPLERS, value=SAMPLERS[0], label="Sampler"
                )
            with gr.Row():
                seed_box = gr.Number(label="Seed", value=42, precision=0)
                randomize_cb = gr.Checkbox(label="🎲 Random seed", value=True)
                batch_slider = gr.Slider(
                    1, 20, value=1, step=1, label="Batch count"
                )
            lora_dds, lora_ws = _lora_stack()
            krea_publish, krea_publish_title = _publish_row()
            krea_save_preset, krea_preset_name = _preset_save_row()
            generate_btn = _cta("🚀 Generate")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            gallery = gr.Gallery(label="Output", columns=2, height=600)
            status_box = _status_box()
            seed_out = gr.Number(
                label="Base seed used", interactive=False, precision=0
            )
    _queue_view(features.Key.KREA_T2I, [gallery, status_box, seed_out],
                preset_tab=presets.TAB_KREA2, preset_dd=preset_dd)
    _krea_run = generate_btn.click(
        fn=_enqueue(features.Key.KREA_T2I, generate_single, prompt_arg=0),
        inputs=_recipe_view(features.Key.KREA_T2I, "krea2", [
            prompt_box, negative_box, seed_box, randomize_cb,
            steps_slider, cfg_slider, resolution_dd, sampler_dd,
            model_dd, batch_slider, krea_publish, krea_publish_title,
            krea_save_preset, krea_preset_name,
            *_lora_inputs(lora_dds, lora_ws)]),
        outputs=[_queue_timer, status_box],
    )
    _reset_after_generate(_krea_run, krea_publish, krea_publish_title,
                          krea_save_preset, krea_preset_name)
    # Same order as _krea_settings writes them, so loading a
    # prompt is a zip rather than a lookup.
    _krea_targets = [prompt_box, negative_box, model_dd,
                     steps_slider, cfg_slider, resolution_dd,
                     sampler_dd, seed_box, randomize_cb,
                     batch_slider, *lora_dds, *lora_ws]
    # A preset is the same list without the two prompt boxes — see
    # _krea_setting_updates. Wired here rather than after the TAB_ORDER
    # loop because, unlike the library's Use buttons, both ends of this
    # live in this tab.
    _preset_targets = _krea_targets[PROMPT_BOXES:]
    preset_dd.change(
        fn=partial(_apply_preset, presets.TAB_KREA2, len(_preset_targets)),
        inputs=preset_dd, outputs=_preset_targets,
    )
    preset_refresh.click(
        fn=partial(_refresh_presets, presets.TAB_KREA2),
        inputs=preset_dd, outputs=preset_dd,
    )

    return _krea_targets


def _tab_krea_v2_t2i(tab):
    """The KREA_V2_T2I tab body."""
    _v2_message = v2_status()[1]
    _tab_intro(
        "The **KREA 2 TURBO/RAW** graph, reproduced "
        "as-is: the mxfp8 or raw Krea 2 model with the Wan 2.1 "
        "VAE, an 11-LoRA model+CLIP stack, RES4LYF's "
        "**ClownsharKSampler** (`linear/euler` + `bong_tangent`, "
        "eta 0.5, bongmath on) and **RBG Smart Seed Variance** on "
        "the positive prompt. Picking a model resets Steps/CFG "
        "and the Turbo LoRA slot to that variant's defaults — "
        "all of it still editable. This tab shares nothing with "
        "the Single tab.\n\n"
        f"{_v2_message}"
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            v2_preset_dd, v2_preset_refresh = _preset_row(presets.TAB_KREA2_V2)
            v2_prompt = gr.Textbox(
                label="Positive Prompt", lines=6,
                placeholder="The source workflow ships this box "
                            "empty — describe your image here.",
            )
            v2_negative = gr.Textbox(
                label="Negatives", lines=6,
                value=V2_DEFAULT_NEGATIVE,
            )
            v2_model_dd = gr.Dropdown(
                choices=V2_MODEL_CHOICES,
                value=V2_MODEL_CHOICES[0], label="Model",
            )
            v2_model_info = gr.Markdown(
                _v2_model_info_text(v2_resolve_model(None)),
                elem_classes="kx-meta",
            )
            gr.Markdown("### 📐 Resolution",
                        elem_classes="kx-section")
            with gr.Row():
                v2_aspect = gr.Dropdown(
                    choices=list(V2_ASPECT_RATIOS),
                    value=V2_DEFAULT_ASPECT, label="Aspect ratio",
                )
                v2_megapixels = gr.Slider(
                    0.5, 4.0, value=V2_DEFAULT_MEGAPIXELS,
                    step=0.1, label="Megapixels",
                )
                v2_multiple = gr.Slider(
                    8, 64, value=V2_DEFAULT_MULTIPLE, step=8,
                    label="Multiple of",
                )
            v2_size_info = gr.Markdown(
                _v2_size_text(
                    V2_DEFAULT_ASPECT, V2_DEFAULT_MEGAPIXELS,
                    V2_DEFAULT_MULTIPLE,
                ),
                elem_classes="kx-meta",
            )
            for _control in (v2_aspect, v2_megapixels, v2_multiple):
                _control.change(
                    fn=v2_size_preview,
                    inputs=[v2_aspect, v2_megapixels, v2_multiple],
                    outputs=v2_size_info,
                )
            with gr.Row():
                v2_seed = gr.Number(label="Seed", value=370102505887178,
                                    precision=0)
                v2_randomize = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                v2_batch = gr.Slider(1, 20, value=1, step=1,
                                     label="Batch count")
            v2_cbs, v2_dds, v2_ws = _v2_lora_stack()
            v2_publish, v2_publish_title = _publish_row()
            v2_save_preset, v2_preset_name = _preset_save_row()
            v2_generate_btn = _cta("🚀 Generate")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            v2_gallery = gr.Gallery(label="Output", columns=2,
                                    height=600)
            v2_status_box = _status_box()
            v2_seed_out = gr.Number(label="Base seed used",
                                    interactive=False, precision=0)
            with gr.Accordion("⚙️ ClownsharKSampler", open=True):
                with gr.Row():
                    v2_steps = gr.Slider(
                        1, 100, value=_v2_steps, step=1,
                        label="Steps",
                    )
                    v2_cfg = gr.Slider(
                        0.0, 20.0, value=_v2_cfg, step=0.1,
                        label="CFG",
                    )
                with gr.Row():
                    v2_sampler_name = gr.Dropdown(
                        choices=V2_SAMPLER_NAMES,
                        value=V2_SAMPLER_DEFAULTS["sampler_name"],
                        label="Sampler", allow_custom_value=True,
                    )
                    v2_scheduler = gr.Dropdown(
                        choices=V2_SCHEDULERS,
                        value=V2_SAMPLER_DEFAULTS["scheduler"],
                        label="Scheduler", allow_custom_value=True,
                    )
                gr.Markdown(
                    "RES4LYF builds its sampler/scheduler lists at "
                    "load time, so both accept free text — the "
                    "listed values are the workflow's plus the "
                    "node's own defaults.",
                    elem_classes="kx-fine",
                )
                with gr.Row():
                    v2_eta = gr.Slider(
                        0.0, 2.0, value=V2_SAMPLER_DEFAULTS["eta"],
                        step=0.01, label="Eta",
                    )
                    v2_denoise = gr.Slider(
                        0.0, 1.0,
                        value=V2_SAMPLER_DEFAULTS["denoise"],
                        step=0.01, label="Denoise",
                    )
                with gr.Row():
                    v2_sampler_mode = gr.Dropdown(
                        choices=V2_SAMPLER_MODES,
                        value=V2_SAMPLER_DEFAULTS["sampler_mode"],
                        label="Sampler mode",
                    )
                    v2_bongmath = gr.Checkbox(
                        label="bongmath",
                        value=V2_SAMPLER_DEFAULTS["bongmath"],
                    )
            with gr.Accordion("🌱 Smart Seed Variance", open=False):
                gr.Markdown(
                    "Perturbs the positive conditioning per seed, "
                    "so a batch varies without drifting off-prompt.",
                    elem_classes="kx-fine",
                )
                with gr.Row():
                    v2_variance_preset = gr.Dropdown(
                        choices=V2_VARIANCE_PRESETS,
                        value=V2_VARIANCE_DEFAULTS["variance_preset"],
                        label="Preset",
                    )
                    v2_fine_tune = gr.Slider(
                        0, 100,
                        value=V2_VARIANCE_DEFAULTS["fine_tune_variance"],
                        step=1, label="Fine tune",
                    )
                v2_variance_model = gr.Dropdown(
                    choices=V2_VARIANCE_MODEL_TYPES,
                    value=V2_VARIANCE_DEFAULTS["model_type"],
                    label="Model type",
                )
                with gr.Row():
                    v2_variance_schedule = gr.Dropdown(
                        choices=V2_VARIANCE_SCHEDULES,
                        value=V2_VARIANCE_DEFAULTS["variance_schedule"],
                        label="Schedule",
                    )
                    v2_shift_strength = gr.Slider(
                        0, 200,
                        value=V2_VARIANCE_DEFAULTS["shift_strength"],
                        step=1, label="Shift strength",
                    )
                with gr.Row():
                    v2_cutoff_step = gr.Slider(
                        0, 100,
                        value=V2_VARIANCE_DEFAULTS["cutoff_step"],
                        step=1, label="Cutoff step",
                    )
                    v2_total_steps = gr.Slider(
                        1, 100,
                        value=V2_VARIANCE_DEFAULTS["total_steps"],
                        step=1, label="Total steps",
                    )
                    v2_cutoff_strength = gr.Slider(
                        0.0, 1.0,
                        value=V2_VARIANCE_DEFAULTS["cutoff_strength"],
                        step=0.1, label="Cutoff strength",
                    )
            with gr.Accordion("🎞️ Post-processing", open=False):
                gr.Markdown(
                    "Both are **bypassed in the source workflow**, "
                    "so both start off and the tab reproduces it "
                    "exactly as shipped. Sharpen runs first, then "
                    "grain.",
                    elem_classes="kx-fine",
                )
                v2_sharpen = gr.Checkbox(
                    label="Sharpen (radius 1, sigma 0.35, alpha 1)",
                    value=False,
                )
                v2_grain = gr.Checkbox(
                    label="Film grain (intensity 0.05, scale 1)",
                    value=False,
                )
    # Wired here, not at creation: the Model dropdown lives in
    # the left column while the sliders it drives are in the
    # right one, so every component has to exist first.
    if V2_TURBO_SLOT is not None:
        v2_model_dd.change(
            fn=v2_model_changed, inputs=v2_model_dd,
            outputs=[v2_steps, v2_cfg, v2_model_info,
                     v2_cbs[V2_TURBO_SLOT], v2_ws[V2_TURBO_SLOT]],
        )
    _queue_view(features.Key.KREA_V2_T2I,
                [v2_gallery, v2_status_box, v2_seed_out],
                preset_tab=presets.TAB_KREA2_V2, preset_dd=v2_preset_dd)
    _v2_run = v2_generate_btn.click(
        fn=_enqueue(features.Key.KREA_V2_T2I, generate_v2, prompt_arg=0),
        inputs=_recipe_view(features.Key.KREA_V2_T2I, "krea2v2", [
            v2_prompt, v2_negative, v2_seed, v2_randomize, v2_model_dd,
            v2_aspect, v2_megapixels, v2_multiple,
            v2_eta, v2_sampler_name, v2_scheduler, v2_steps,
            v2_denoise, v2_cfg, v2_sampler_mode, v2_bongmath,
            v2_variance_preset, v2_fine_tune,
            v2_variance_model, v2_variance_schedule,
            v2_cutoff_step, v2_total_steps,
            v2_cutoff_strength, v2_shift_strength,
            v2_sharpen, v2_grain, v2_batch, v2_publish, v2_publish_title,
            v2_save_preset, v2_preset_name,
            *_lora_triples(v2_cbs, v2_dds, v2_ws)]),
        outputs=[_queue_timer, v2_status_box],
    )
    _reset_after_generate(_v2_run, v2_publish, v2_publish_title,
                          v2_save_preset, v2_preset_name)
    _v2_targets = [
        v2_prompt, v2_negative, v2_model_dd,
        v2_aspect, v2_megapixels, v2_multiple,
        v2_seed, v2_randomize, v2_batch,
        v2_eta, v2_sampler_name, v2_scheduler, v2_steps,
        v2_denoise, v2_cfg, v2_sampler_mode, v2_bongmath,
        v2_variance_preset, v2_fine_tune, v2_variance_model,
        v2_variance_schedule, v2_cutoff_step, v2_total_steps,
        v2_cutoff_strength, v2_shift_strength,
        v2_sharpen, v2_grain,
        *v2_cbs, *v2_dds, *v2_ws,
    ]
    # Same split as the Krea 2 tab: everything past the two prompt boxes
    # is what a preset carries.
    _preset_targets = _v2_targets[PROMPT_BOXES:]
    v2_preset_dd.change(
        fn=partial(_apply_preset, presets.TAB_KREA2_V2, len(_preset_targets)),
        inputs=v2_preset_dd, outputs=_preset_targets,
    )
    v2_preset_refresh.click(
        fn=partial(_refresh_presets, presets.TAB_KREA2_V2),
        inputs=v2_preset_dd, outputs=v2_preset_dd,
    )

    return _v2_targets


def _tab_community_prompts(tab):
    """The COMMUNITY_PROMPTS tab body."""
    library_tab = tab

    _tab_intro(
        "Ready-made prompts for **Krea2** and **Krea2 V2** — "
        "⭐ ones we put together, and 👥 ones the community is "
        "using. **Use** loads the prompt *and* every setting "
        "behind it into that tab, so you can run it as it is or "
        "treat it as a starting point.\n\n"
        "Anything a prompt asks for that this pod does not have "
        "— a model, a LoRA file — is left as it was rather than "
        "applied, so a card always loads."
    )
    # kx-lib-filters: these are visible cards rather than the
    # invisible blocks of a control column, so they need the
    # vertical padding the .form rule strips — see theme.py.
    with gr.Row(elem_classes="kx-lib-filters"):
        lib_tab_filter = gr.Radio(
            choices=list(_LIB_TABS), value="Everything",
            label="Tab",
        )
        lib_source_filter = gr.Radio(
            choices=list(_LIB_SOURCES), value="All prompts",
            label="Source",
        )
    with gr.Row(elem_classes="kx-lib-filters"):
        lib_search = gr.Textbox(
            label="Search", scale=4, submit_btn=True,
            placeholder="portrait, cinematic, anime … "
                        "(press Enter)",
        )
        lib_refresh = gr.Button("🔄 Refresh", size="sm", scale=0)
    lib_info = gr.Markdown(
        "🔄 Refresh to load the prompt library.",
        elem_classes="kx-meta",
    )

    # The page's rows, and which page it is. State rather than
    # a recomputed fetch, so clicking Use costs nothing and
    # cannot show a card different from the one clicked.
    lib_rows = gr.State([])
    lib_page = gr.State(0)

    # A fixed pool, shown and hidden per page: a Blocks tree is
    # built once at import and cannot grow a component later,
    # so "a card per result" is not on the table.
    lib_cards, lib_bodies, lib_buttons = [], [], []
    for _start in range(0, LIBRARY_CARDS, 3):
        with gr.Row():
            for _slot in range(_start, _start + 3):
                with gr.Column(visible=False, min_width=260,
                               elem_classes="kx-prompt-card"
                               ) as _card:
                    lib_bodies.append(gr.Markdown())
                    lib_buttons.append(
                        gr.Button("▶️ Use", size="sm")
                    )
                lib_cards.append(_card)

    with gr.Row(elem_classes="kx-navrow"):
        lib_prev = gr.Button("← Previous", size="sm",
                             interactive=False)
        lib_next = gr.Button("Next →", size="sm",
                             interactive=False)

    _lib_filters = [lib_tab_filter, lib_source_filter, lib_search]
    _lib_outputs = [lib_rows, lib_page, lib_info, lib_prev,
                    lib_next, *lib_cards, *lib_bodies,
                    *lib_buttons]

    # Fetched when the tab is opened, never at build time —
    # the same rule the pricing panel follows, so a licence
    # server that is slow cannot hold up pod startup. The 300s
    # cache in prompts.py absorbs re-opening it.
    library_tab.select(fn=_library_first, inputs=_lib_filters,
                       outputs=_lib_outputs)
    for _control in (lib_tab_filter, lib_source_filter):
        _control.change(fn=_library_first, inputs=_lib_filters,
                        outputs=_lib_outputs)
    lib_search.submit(fn=_library_first, inputs=_lib_filters,
                      outputs=_lib_outputs)
    for _button, _handler in ((lib_refresh, _library_refresh),
                              (lib_prev, _library_prev),
                              (lib_next, _library_next)):
        _button.click(fn=_handler,
                      inputs=[*_lib_filters, lib_page],
                      outputs=_lib_outputs)

    return lib_rows, lib_buttons


def _tab_krea_edit(tab):
    """The KREA_EDIT tab body."""
    _tab_intro(
        "Upload an image and **describe the change** — no painting "
        "needed. The Identity Edit LoRA lets the model see the "
        "source image, so it can recolor, add or replace objects, "
        "restyle, or re-stage a person in a new scene while keeping "
        "their identity. Defaults (8–12 steps, CFG 1.0) suit most "
        "edits; removals work better with ~20 steps, CFG ≈ 3 and a "
        "lower reference fidelity. Fewer steps favour composition, "
        "more favour face detail."
        + ("" if edit_lora_available() else
           "\n\n⚠️ **The Identity Edit LoRA is not downloaded yet** "
           "(~1.9 GB) — restart the app to fetch it; this tab will "
           "refuse to run until then.")
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            edit_image = gr.Image(
                label="Source image (paste with Ctrl+V)", type="pil",
                sources=["upload", "clipboard"],
            )
            _recent_picker(edit_image)
            edit_use_image2 = gr.Checkbox(
                label="➕ Add a second reference (subject)", value=False,
                info="Two-input edit: image 1 is the scene and sets the "
                     "output size, image 2 is the subject to place into "
                     "it. Placing both in one pass is more reliable than "
                     "two edits in a row.",
            )
            edit_image2 = gr.Image(
                label="Second reference — subject", type="pil",
                visible=False, sources=["upload", "clipboard"],
            )
            edit_prompt = gr.Textbox(
                label="Edit instruction",
                value="Remove all her clothes completely, make her fully nude. Keep the exact same face, facial features, expression, skin tone, hairstyle, body pose, hands position, and background. Do not change the face at all.          remove clothes exposing her naked average natural shaped tits. dont change her face",
                placeholder="make the jacket red · this person "
                            "walking a dog on a beach at sunset",
                lines=3,
            )
            edit_negative = gr.Textbox(
                label="Negative prompt (only used when CFG > 1)", lines=2
            )
            edit_model_dd, edit_model_info = _model_selector()
            with gr.Row():
                edit_steps = gr.Slider(
                    1, 60, value=DEFAULTS["steps"], step=1, label="Steps"
                )
                edit_cfg = gr.Slider(
                    0.5, 8.0, value=DEFAULTS["cfg"], step=0.1, label="CFG"
                )
            edit_model_dd.change(
                fn=krea_model_changed,
                inputs=[edit_model_dd, edit_prompt],
                outputs=[edit_steps, edit_cfg, edit_model_info,
                         edit_prompt],
            )
            with gr.Row():
                # 384-768 is the LoRA's trained grounding range.
                # The old slider went to 1536 (v1's range) and
                # defaulted to 1152 — far above what v1.1/v1.2
                # ever saw, which is the documented cause of
                # duplicated "double picture" outputs.
                edit_grounding = gr.Slider(
                    384, 768, value=768, step=64,
                    label="Grounding (low = stronger edit, "
                          "high = keep likeness)",
                )
                edit_ref_boost = gr.Slider(
                    0.0, 10.0, value=4.0, step=0.5,
                    label="Reference fidelity (1 = neutral, "
                          "~4 = strong likeness, >10 breaks "
                          "removals)",
                    info="Applies to the last reference — the subject "
                         "when a second one is on.",
                )
            edit_ref_boost_a = gr.Slider(
                0.0, 10.0, value=1.0, step=0.5, visible=False,
                label="Scene fidelity (1 = neutral)",
                info="The same dial for the first reference. Only has an "
                     "effect with a second reference on.",
            )
            edit_use_image2.change(
                fn=lambda on: (gr.Image(visible=bool(on)),
                               gr.Slider(visible=bool(on))),
                inputs=edit_use_image2,
                outputs=[edit_image2, edit_ref_boost_a],
            )
            with gr.Row():
                edit_sampler = gr.Dropdown(
                    choices=SAMPLERS, value=SAMPLERS[0], label="Sampler",
                    info="Outpainting? Switch to euler. SDE samplers "
                         "inject noise that disrupts the reference-copy "
                         "channel and ruins outpaint coherence.",
                )
            with gr.Row():
                edit_seed = gr.Number(label="Seed", value=42, precision=0)
                edit_random = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                edit_batch = gr.Slider(
                    1, 20, value=1, step=1, label="Batch count"
                )
            edit_lora_dds, edit_lora_ws = _lora_stack()
            edit_btn = _cta("✨ Edit")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            edit_gallery = gr.Gallery(label="Output", columns=2, height=600)
            edit_status = _status_box()
            edit_seed_out = gr.Number(
                label="Base seed used", interactive=False, precision=0
            )
    _queue_view(features.Key.KREA_EDIT,
                [edit_gallery, edit_status, edit_seed_out])
    edit_btn.click(
        fn=_enqueue(features.Key.KREA_EDIT, generate_edit, prompt_arg=3),
        inputs=_recipe_view(features.Key.KREA_EDIT, "edit", [
            edit_image, edit_use_image2, edit_image2,
            edit_prompt, edit_negative, edit_seed,
            edit_random, edit_steps, edit_cfg, edit_sampler,
            edit_grounding, edit_ref_boost, edit_ref_boost_a,
            edit_model_dd, edit_batch,
            *_lora_inputs(edit_lora_dds, edit_lora_ws)]),
        outputs=[_queue_timer, edit_status],
    )


def _tab_krea_v2_edit(tab):
    """The KREA_V2_EDIT tab body."""
    _tab_intro(
        "The **✨ Krea2 Edit** recipe on the **🔶 Krea2 V2** "
        "pipeline: upload an image and describe the change, but "
        "over the V2 model and Wan 2.1 VAE, the 11-LoRA "
        "model+CLIP stack, RES4LYF's **ClownsharKSampler** and "
        "**RBG Smart Seed Variance** — every default taken from "
        "the V2 tab. The Identity Edit LoRA is applied first at "
        "1.0 as trained, and the stack below sits on top of it.\n\n"
        "Output size comes from your image (aspect kept, capped "
        "at 2 MP), so there is no resolution control; there is no "
        "Denoise either, because the source reaches the model "
        "through conditioning rather than the starting latent.\n\n"
        f"{v2_edit_status()[1]}"
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            v2e_image = gr.Image(
                label="Source image (paste with Ctrl+V)",
                type="pil", sources=["upload", "clipboard"],
            )
            _recent_picker(v2e_image)
            v2e_use_image2 = gr.Checkbox(
                label="➕ Add a second reference (subject)", value=False,
                info="Two-input edit: image 1 is the scene and sets the "
                     "output size, image 2 is the subject to place into "
                     "it. Placing both in one pass is more reliable than "
                     "two edits in a row.",
            )
            v2e_image2 = gr.Image(
                label="Second reference — subject", type="pil",
                visible=False, sources=["upload", "clipboard"],
            )
            v2e_prompt = gr.Textbox(
                label="Edit instruction", lines=3,
                placeholder="make the jacket red · this person "
                            "walking a dog on a beach at sunset",
            )
            v2e_negative = gr.Textbox(
                label="Negatives (only used when CFG > 1)",
                lines=4, value=V2_DEFAULT_NEGATIVE,
            )
            v2e_model_dd = gr.Dropdown(
                choices=V2_MODEL_CHOICES,
                value=V2_MODEL_CHOICES[0], label="Model",
            )
            v2e_model_info = gr.Markdown(
                _v2_model_info_text(v2_resolve_model(None)),
                elem_classes="kx-meta",
            )
            with gr.Row():
                v2e_grounding = gr.Slider(
                    384, 768, value=V2_EDIT_DEFAULT_GROUNDING,
                    step=64,
                    label="Grounding (low = stronger edit, "
                          "high = keep likeness)",
                )
                v2e_ref_boost = gr.Slider(
                    0.0, 10.0, value=V2_EDIT_DEFAULT_REF_BOOST,
                    step=0.5,
                    label="Reference fidelity (1 = neutral, "
                          "~4 = strong likeness, >10 breaks "
                          "removals)",
                    info="Applies to the last reference — the subject "
                         "when a second one is on.",
                )
            v2e_ref_boost_a = gr.Slider(
                0.0, 10.0, value=1.0, step=0.5, visible=False,
                label="Scene fidelity (1 = neutral)",
                info="The same dial for the first reference. Only has an "
                     "effect with a second reference on.",
            )
            v2e_use_image2.change(
                fn=lambda on: (gr.Image(visible=bool(on)),
                               gr.Slider(visible=bool(on))),
                inputs=v2e_use_image2,
                outputs=[v2e_image2, v2e_ref_boost_a],
            )
            v2e_fit_mode = gr.Dropdown(
                choices=V2_EDIT_FIT_MODES,
                value=V2_EDIT_FIT_MODES[0],
                label="Reference geometry (fit = v1.2; the legacy "
                      "crop is for older weights)",
            )
            with gr.Row():
                v2e_seed = gr.Number(label="Seed", value=42,
                                     precision=0)
                v2e_randomize = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                v2e_batch = gr.Slider(1, 20, value=1, step=1,
                                      label="Batch count")
            v2e_cbs, v2e_dds, v2e_ws = _v2_lora_stack()
            v2e_btn = _cta("✨ Edit")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            v2e_gallery = gr.Gallery(label="Output", columns=2,
                                     height=600)
            v2e_status_box = _status_box()
            v2e_seed_out = gr.Number(label="Base seed used",
                                     interactive=False,
                                     precision=0)
            with gr.Accordion("⚙️ ClownsharKSampler", open=True):
                with gr.Row():
                    v2e_steps = gr.Slider(
                        1, 100, value=_v2_steps, step=1,
                        label="Steps",
                    )
                    v2e_cfg = gr.Slider(
                        0.0, 20.0, value=_v2_cfg, step=0.1,
                        label="CFG",
                    )
                gr.Markdown(
                    "The V2 defaults. For **removals**, the Edit "
                    "LoRA prefers more steps (~20) and CFG ≈ 3 "
                    "with a lower reference fidelity; fewer steps "
                    "favour composition, more favour face detail.",
                    elem_classes="kx-fine",
                )
                with gr.Row():
                    v2e_sampler_name = gr.Dropdown(
                        choices=V2_SAMPLER_NAMES,
                        value=V2_SAMPLER_DEFAULTS["sampler_name"],
                        label="Sampler", allow_custom_value=True,
                    )
                    v2e_scheduler = gr.Dropdown(
                        choices=V2_SCHEDULERS,
                        value=V2_SAMPLER_DEFAULTS["scheduler"],
                        label="Scheduler", allow_custom_value=True,
                    )
                with gr.Row():
                    v2e_eta = gr.Slider(
                        0.0, 2.0, value=V2_SAMPLER_DEFAULTS["eta"],
                        step=0.01, label="Eta",
                    )
                    v2e_sampler_mode = gr.Dropdown(
                        choices=V2_SAMPLER_MODES,
                        value=V2_SAMPLER_DEFAULTS["sampler_mode"],
                        label="Sampler mode",
                    )
                v2e_bongmath = gr.Checkbox(
                    label="bongmath",
                    value=V2_SAMPLER_DEFAULTS["bongmath"],
                )
            with gr.Accordion("🌱 Smart Seed Variance", open=False):
                gr.Markdown(
                    "Perturbs the grounded conditioning per seed, "
                    "so a batch of edits varies without drifting "
                    "off-instruction. Set the preset to "
                    "**❌ Disabled** to vary by sampling noise "
                    "alone.",
                    elem_classes="kx-fine",
                )
                with gr.Row():
                    v2e_variance_preset = gr.Dropdown(
                        choices=V2_VARIANCE_PRESETS,
                        value=V2_VARIANCE_DEFAULTS["variance_preset"],
                        label="Preset",
                    )
                    v2e_fine_tune = gr.Slider(
                        0, 100,
                        value=V2_VARIANCE_DEFAULTS["fine_tune_variance"],
                        step=1, label="Fine tune",
                    )
                v2e_variance_model = gr.Dropdown(
                    choices=V2_VARIANCE_MODEL_TYPES,
                    value=V2_VARIANCE_DEFAULTS["model_type"],
                    label="Model type",
                )
                with gr.Row():
                    v2e_variance_schedule = gr.Dropdown(
                        choices=V2_VARIANCE_SCHEDULES,
                        value=V2_VARIANCE_DEFAULTS["variance_schedule"],
                        label="Schedule",
                    )
                    v2e_shift_strength = gr.Slider(
                        0, 200,
                        value=V2_VARIANCE_DEFAULTS["shift_strength"],
                        step=1, label="Shift strength",
                    )
                with gr.Row():
                    v2e_cutoff_step = gr.Slider(
                        0, 100,
                        value=V2_VARIANCE_DEFAULTS["cutoff_step"],
                        step=1, label="Cutoff step",
                    )
                    v2e_total_steps = gr.Slider(
                        1, 100,
                        value=V2_VARIANCE_DEFAULTS["total_steps"],
                        step=1, label="Total steps",
                    )
                    v2e_cutoff_strength = gr.Slider(
                        0.0, 1.0,
                        value=V2_VARIANCE_DEFAULTS["cutoff_strength"],
                        step=0.1, label="Cutoff strength",
                    )
    # Wired here for the same reason as the V2 tab: the Model
    # dropdown is in the left column and the sliders it drives
    # are in the right one, so both have to exist first.
    if V2_TURBO_SLOT is not None:
        v2e_model_dd.change(
            fn=v2_model_changed, inputs=v2e_model_dd,
            outputs=[v2e_steps, v2e_cfg, v2e_model_info,
                     v2e_cbs[V2_TURBO_SLOT],
                     v2e_ws[V2_TURBO_SLOT]],
        )
    _queue_view(features.Key.KREA_V2_EDIT,
                [v2e_gallery, v2e_status_box, v2e_seed_out])
    v2e_btn.click(
        fn=_enqueue(features.Key.KREA_V2_EDIT, generate_v2_edit,
                    prompt_arg=3),
        inputs=_recipe_view(features.Key.KREA_V2_EDIT, "v2edit", [
            v2e_image, v2e_use_image2, v2e_image2,
            v2e_prompt, v2e_negative, v2e_seed,
            v2e_randomize, v2e_model_dd, v2e_grounding,
            v2e_ref_boost, v2e_ref_boost_a, v2e_fit_mode, v2e_eta,
            v2e_sampler_name, v2e_scheduler, v2e_steps,
            v2e_cfg, v2e_sampler_mode, v2e_bongmath,
            v2e_variance_preset, v2e_fine_tune,
            v2e_variance_model, v2e_variance_schedule,
            v2e_cutoff_step, v2e_total_steps,
            v2e_cutoff_strength, v2e_shift_strength, v2e_batch,
            *_lora_triples(v2e_cbs, v2e_dds, v2e_ws)]),
        outputs=[_queue_timer, v2e_status_box],
    )


def _tab_krea_inpaint(tab):
    """The KREA_INPAINT tab body."""
    _tab_intro(
        "Upload an image, **paint over the region to replace**, and "
        "describe what should appear there — unpainted pixels are "
        "kept from the original. Paint **nothing** to re-imagine the "
        "whole image (img2img); in that mode lower **Denoise** "
        "(≈0.5–0.8) to control how much of the original survives."
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            inpaint_editor = gr.ImageEditor(
                label="Image — paint the region to replace "
                      "(paste with Ctrl+V)",
                type="pil",
                sources=["upload", "clipboard"],
                brush=gr.Brush(colors=["#FF3366"], color_mode="fixed"),
                # fixed_canvas defaults to False, which sizes the
                # canvas to the uploaded image: a 12 MP phone photo
                # then allocates a 4032×3024 RGBA canvas *plus* a
                # paint layer, the browser tab runs out of memory and
                # the page reloads (gradio#8556). Pinning the canvas
                # makes Gradio rescale the upload to fit it instead.
                # 1536 is a deliberate cap: _prepare_inpaint_inputs
                # would downscale to 2048 anyway, and Krea 2 inpaints
                # comfortably at this size.
                canvas_size=(1536, 1536),
                fixed_canvas=True,
                # Default is lossy webp. Unmasked pixels are composited
                # back from this image, so keep it lossless.
                format="png",
            )
            inpaint_prompt = gr.Textbox(
                label="Prompt (describes the masked region)", lines=3
            )
            inpaint_negative = gr.Textbox(
                label="Negative prompt (only used when CFG > 1)", lines=2
            )
            inpaint_model_dd, inpaint_model_info = _model_selector()
            with gr.Row():
                inpaint_steps = gr.Slider(
                    1, 60, value=DEFAULTS["steps"], step=1, label="Steps"
                )
                inpaint_cfg = gr.Slider(
                    0.5, 8.0, value=DEFAULTS["cfg"], step=0.1, label="CFG"
                )
            inpaint_model_dd.change(
                fn=krea_model_changed,
                inputs=[inpaint_model_dd, inpaint_prompt],
                outputs=[inpaint_steps, inpaint_cfg,
                         inpaint_model_info, inpaint_prompt],
            )
            with gr.Row():
                inpaint_denoise = gr.Slider(
                    0.1, 1.0, value=1.0, step=0.05,
                    label="Denoise (1 = replace fully)",
                )
                inpaint_sampler = gr.Dropdown(
                    choices=SAMPLERS, value=SAMPLERS[0], label="Sampler"
                )
            with gr.Row():
                inpaint_grow = gr.Slider(
                    0, 32, value=8, step=1, label="Grow mask (px)"
                )
                inpaint_blur = gr.Slider(
                    0, 32, value=8, step=1, label="Blur mask edge (px)"
                )
            with gr.Row():
                inpaint_seed = gr.Number(
                    label="Seed", value=42, precision=0
                )
                inpaint_random = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                inpaint_batch = gr.Slider(
                    1, 20, value=1, step=1, label="Batch count"
                )
            inpaint_lora_dds, inpaint_lora_ws = _lora_stack()
            inpaint_btn = _cta("🖌️ Inpaint")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            inpaint_gallery = gr.Gallery(
                label="Output", columns=2, height=600
            )
            inpaint_status = _status_box()
            inpaint_seed_out = gr.Number(
                label="Base seed used", interactive=False, precision=0
            )
    _queue_view(features.Key.KREA_INPAINT,
                [inpaint_gallery, inpaint_status, inpaint_seed_out])
    inpaint_btn.click(
        fn=_enqueue(features.Key.KREA_INPAINT, generate_inpaint,
                    prompt_arg=1),
        inputs=_recipe_view(features.Key.KREA_INPAINT, "inpaint", [
            inpaint_editor, inpaint_prompt, inpaint_negative,
            inpaint_seed, inpaint_random, inpaint_steps,
            inpaint_cfg, inpaint_denoise, inpaint_sampler,
            inpaint_grow, inpaint_blur, inpaint_model_dd, inpaint_batch,
            *_lora_inputs(inpaint_lora_dds, inpaint_lora_ws)]),
        outputs=[_queue_timer, inpaint_status],
    )


def _tab_faceswap(tab):
    """The FACESWAP tab body."""
    _swap_ready, _swap_problem = reactor_status()
    _tab_intro(
        "Take one of your generated images, upload a **reference "
        "face**, and ReActor replaces the face in place. This is "
        "not a diffusion pass: no Krea 2 model is loaded, the "
        "swap runs on ONNX in seconds, and the output keeps the "
        "base image's **exact resolution** — only the face "
        "region is rewritten. Only the finished swap is saved.\n\n"
        "Every model is fetched at startup, so a swap makes no "
        "network calls. Note that this is the **SFW edition** of "
        "ReActor: it classifies every input image first, and an "
        "image that trips the filter is dropped — you get a "
        "blank 512×512 frame instead of a swap, which the status "
        "box calls out. The check fails *closed*, so if its "
        "detector model ever goes missing every swap comes back "
        "blank."
        + ("" if _swap_ready else f"\n\n⚠️ {_swap_problem[2:]}")
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            swap_base = gr.Image(
                label="Base image — the face here gets replaced "
                      "(paste with Ctrl+V)",
                type="pil", sources=["upload", "clipboard"],
            )
            _recent_picker(swap_base)
            swap_face = gr.Image(
                label="Reference face — the face to put in "
                      "(paste with Ctrl+V)",
                type="pil", sources=["upload", "clipboard"],
            )
            with gr.Row():
                swap_model_dd = gr.Dropdown(
                    choices=SWAP_MODEL_CHOICES,
                    value=default_swap_model(),
                    label="Swap model",
                )
                swap_detector_dd = gr.Dropdown(
                    choices=REACTOR_DETECTORS,
                    value=REACTOR_DEFAULT_DETECTOR,
                    label="Face detector",
                )
            with gr.Row():
                swap_restore_dd = gr.Dropdown(
                    choices=RESTORE_CHOICES,
                    value=default_restore_model(),
                    label="Face restoration (optional)",
                )
                swap_visibility = gr.Slider(
                    0.1, 1.0, value=1.0, step=0.05,
                    label="Restoration visibility",
                )
            swap_codeformer_w = gr.Slider(
                0.0, 1.0, value=0.5, step=0.05,
                label="CodeFormer weight (0 = stronger cleanup, "
                      "1 = stay closer to the swap)",
            )
            with gr.Row():
                swap_input_idx = gr.Textbox(
                    value="0", label="Face index in base image",
                    info="Left to right. Also accepts 0,1 or 0-2",
                )
                swap_source_idx = gr.Textbox(
                    value="0", label="Face index in reference",
                    info="Left to right. Also accepts 0,1 or 0-2",
                )
            swap_btn = _cta("🎭 Swap face")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            swap_gallery = gr.Gallery(
                label="Swapped output", columns=1, height=600
            )
            swap_status = _status_box()
    _queue_view(features.Key.FACESWAP, [swap_gallery, swap_status])
    swap_btn.click(
        fn=_enqueue(features.Key.FACESWAP, generate_faceswap),
        inputs=_recipe_view(features.Key.FACESWAP, "faceswap", [
            swap_base, swap_face, swap_model_dd,
            swap_detector_dd, swap_restore_dd, swap_visibility,
            swap_codeformer_w, swap_input_idx, swap_source_idx]),
        outputs=[_queue_timer, swap_status],
    )


def _tab_flux_t2i(tab):
    """The FLUX_T2I tab body."""
    _tab_intro(
        "Text-to-image with **Flux 2 Dev** (32B). The model is "
        "guidance-distilled: there is no CFG or negative prompt "
        "— **Guidance** steers prompt adherence instead "
        "(~4 is the sweet spot). **Turbo** (default) applies the "
        "official Turbo LoRA at 8 steps; the Raw entry runs the "
        "undistilled 20-step schedule. ⚠️ At ~35 GB this model "
        "wants nearly the whole A40: don't combine it with "
        "KREA2_WAN_PARALLEL, and expect a slow first job / model "
        "swap when switching between Flux and Krea."
        + ("" if flux_model_available(resolve_flux_model(None))
           else "\n\n⚠️ **The Flux 2 models are not downloaded "
                "yet** (~57 GB) — restart the app to fetch them; "
                "this tab will refuse to run until then.")
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            flux_prompt = gr.Textbox(
                label="Prompt", lines=5,
                value="A photorealistic golden-hour portrait, "
                      "natural skin texture, shallow depth of "
                      "field",
            )
            flux_model_dd = gr.Dropdown(
                choices=FLUX_MODEL_CHOICES,
                value=FLUX_MODEL_CHOICES[0], label="Model",
            )
            flux_model_info = gr.Markdown(
                _flux_model_info_text(resolve_flux_model(None)),
                elem_classes="kx-meta",
            )
            with gr.Row():
                flux_steps = gr.Slider(
                    1, 50, value=_f_steps, step=1, label="Steps"
                )
                flux_guidance = gr.Slider(
                    0.0, 10.0, value=_f_guidance, step=0.1,
                    label="Guidance",
                )
            flux_model_dd.change(
                fn=flux_model_changed,
                inputs=[flux_model_dd, flux_prompt],
                outputs=[flux_steps, flux_guidance,
                         flux_model_info, flux_prompt],
            )
            with gr.Row():
                flux_resolution = gr.Dropdown(
                    choices=list(RESOLUTION_PRESETS),
                    value=DEFAULT_RESOLUTION, label="Resolution",
                )
                flux_sampler = gr.Dropdown(
                    choices=SAMPLERS, value="euler",
                    label="Sampler",
                )
            with gr.Row():
                flux_seed = gr.Number(
                    label="Seed", value=42, precision=0
                )
                flux_random = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                flux_batch = gr.Slider(
                    1, 20, value=1, step=1, label="Batch count"
                )
            flux_lora_dds, flux_lora_ws = _flux_lora_stack()
            flux_btn = _cta("🌊 Generate")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            flux_gallery = gr.Gallery(
                label="Output", columns=2, height=600
            )
            flux_status = _status_box()
            flux_seed_out = gr.Number(
                label="Base seed used", interactive=False,
                precision=0,
            )
    _queue_view(features.Key.FLUX_T2I,
                [flux_gallery, flux_status, flux_seed_out])
    flux_btn.click(
        fn=_enqueue(features.Key.FLUX_T2I, generate_flux, prompt_arg=0),
        inputs=_recipe_view(features.Key.FLUX_T2I, "flux", [
            flux_prompt, flux_seed, flux_random, flux_steps,
            flux_guidance, flux_resolution, flux_sampler,
            flux_model_dd, flux_batch,
            *_lora_inputs(flux_lora_dds, flux_lora_ws)]),
        outputs=[_queue_timer, flux_status],
    )


def _tab_klein_i2i(tab):
    """The KLEIN_I2I tab body."""
    _tab_intro(
        "The **FLUX.2 Klein 9B Edit** graph, "
        "reproduced as-is. Upload an image and describe the "
        "change — the source is scaled to "
        f"{KLEIN_REFERENCE_MEGAPIXELS:g} MP, encoded and attached "
        "to the conditioning as a **reference latent**, so the "
        "model edits what it is shown. Enable **input image 2** "
        "to combine two sources; when you do, say which is which "
        "in the prompt (*“the person from image 1 wearing the hat "
        "from image 2”*). Klein 9B is ~9.4 GB, so it loads and "
        "swaps far faster than Flux 2 Dev.\n\n"
        f"{klein_status()[1]}"
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            klein_image = gr.Image(
                label="Input image 1 (paste with Ctrl+V)",
                type="pil", sources=["upload", "clipboard"],
            )
            _recent_picker(klein_image)
            klein_use_image2 = gr.Checkbox(
                label="➕ Enable input image 2", value=False,
                info="Bypassed in the source workflow, so it "
                     "starts off here too.",
            )
            klein_image2 = gr.Image(
                label="Input image 2", type="pil", visible=False,
                sources=["upload", "clipboard"],
            )
            klein_use_image2.change(
                fn=lambda on: gr.Image(visible=bool(on)),
                inputs=klein_use_image2, outputs=klein_image2,
            )
            klein_prompt = gr.Textbox(
                label="Edit prompt", lines=4,
                placeholder="The source workflow ships this box "
                            "empty — describe your edit here.",
            )
            klein_model_dd = gr.Dropdown(
                choices=KLEIN_MODEL_CHOICES,
                value=KLEIN_MODEL_CHOICES[0], label="Model",
            )
            klein_model_info = gr.Markdown(
                _klein_model_info_text(klein_resolve_model(None)),
                elem_classes="kx-meta",
            )
            with gr.Row():
                klein_steps = gr.Slider(
                    1, 50, value=_k_steps, step=1, label="Steps"
                )
                klein_cfg = gr.Slider(
                    0.5, 8.0, value=_k_cfg, step=0.1, label="CFG"
                )
                klein_guidance = gr.Slider(
                    0.0, 10.0, value=_k_guidance, step=0.1,
                    label="Guidance",
                )
            klein_model_dd.change(
                fn=klein_model_changed, inputs=klein_model_dd,
                outputs=[klein_steps, klein_cfg, klein_guidance,
                         klein_model_info],
            )
            with gr.Row():
                klein_sampler = gr.Dropdown(
                    choices=SAMPLERS,
                    value=KLEIN_DEFAULTS["sampler_name"],
                    label="Sampler",
                )
                klein_scheduler = gr.Dropdown(
                    choices=KLEIN_SCHEDULERS,
                    value=KLEIN_DEFAULTS["scheduler"],
                    label="Scheduler",
                )
            klein_reference_mp = gr.Slider(
                0.25, 4.0, value=KLEIN_REFERENCE_MEGAPIXELS,
                step=0.05,
                label="Reference size (MP) — what the model looks at",
            )
            gr.Markdown("#### 🖼️ Output resolution",
                        elem_classes="kx-section")
            klein_output_mode = gr.Dropdown(
                choices=KLEIN_OUTPUT_MODES, value=KLEIN_OUTPUT_SAME,
                label="Mode",
            )
            with gr.Row():
                klein_output_mp = gr.Slider(
                    0.25, 4.0, value=KLEIN_DEFAULT_MEGAPIXELS,
                    step=0.05, label="Megapixels (scale mode)",
                )
                klein_custom_w = gr.Number(
                    label="Width (custom mode)",
                    value=KLEIN_DEFAULT_CUSTOM_SIZE[0],
                    precision=0,
                )
                klein_custom_h = gr.Number(
                    label="Height (custom mode)",
                    value=KLEIN_DEFAULT_CUSTOM_SIZE[1],
                    precision=0,
                )
            klein_size_out = gr.Markdown(
                "→ upload image 1 to see the output size.",
                elem_classes="kx-meta",
            )
            _klein_size_inputs = [klein_image, klein_output_mode,
                                  klein_output_mp, klein_custom_w,
                                  klein_custom_h]
            for _component in _klein_size_inputs:
                _component.change(
                    fn=klein_size_preview,
                    inputs=_klein_size_inputs,
                    outputs=klein_size_out,
                )
            with gr.Row():
                klein_seed = gr.Number(
                    label="Seed", value=42, precision=0
                )
                klein_random = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                klein_batch = gr.Slider(
                    1, 20, value=1, step=1, label="Batch count"
                )
            klein_cbs, klein_dds, klein_ws = _klein_lora_stack()
            klein_btn = _cta("🧩 Edit")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            klein_gallery = gr.Gallery(
                label="Output", columns=2, height=600
            )
            klein_status_box = _status_box()
            klein_seed_out = gr.Number(
                label="Base seed used", interactive=False,
                precision=0,
            )
    _queue_view(features.Key.KLEIN_I2I,
                [klein_gallery, klein_status_box, klein_seed_out])
    klein_btn.click(
        fn=_enqueue(features.Key.KLEIN_I2I, generate_klein_edit,
                    prompt_arg=3),
        inputs=_recipe_view(features.Key.KLEIN_I2I, "klein", [
            klein_image, klein_use_image2, klein_image2,
            klein_prompt, klein_seed, klein_random,
            klein_model_dd, klein_steps, klein_cfg,
            klein_guidance, klein_sampler, klein_scheduler,
            klein_reference_mp, klein_output_mode,
            klein_output_mp, klein_custom_w, klein_custom_h, klein_batch,
            *_lora_triples(klein_cbs, klein_dds, klein_ws)]),
        outputs=[_queue_timer, klein_status_box],
    )


def _tab_wan_i2v(tab):
    """The WAN_I2V tab body."""
    _wan_defaults = WAN_MODE_DEFAULTS[WAN_VARIANT]
    _wan_mode_choices = ["Turbo (Lightning, 4 steps)",
                         "Raw (20 steps)"]
    _wan_model_choices = ["14B two-expert (best quality, 16 fps)",
                          "5B TI2V (lighter, 24 fps)"]
    _tab_intro(
        "Upload an image and **describe the motion** — Wan 2.2 "
        "animates it into a clip of up to 5 s. The **14B** "
        "two-expert model gives the best quality at 16 fps: "
        "**Turbo** uses the Lightning distillation LoRAs "
        "(4 steps, CFG 1, ~5× faster); **Raw** is the "
        "undistilled 20-step schedule — slightly better motion "
        "and detail, but expect 15–45+ min per clip on an A40. "
        "The **5B** model is a single lighter model at 24 fps — "
        "lower quality than 14B, but far less VRAM (best choice "
        "in parallel mode) and no turbo/raw split. "
        + ("Videos run on their own ComfyUI instance, so the "
           "image tabs stay responsive while a clip renders."
           if WAN_PARALLEL else
           "Videos share the image tabs' ComfyUI queue: a "
           "running video delays queued image jobs (start with "
           "KREA2_WAN_PARALLEL=1 for a separate video instance).")
        + ("" if wan_models_available() or wan_5b_available() else
           "\n\n⚠️ **The Wan 2.2 models are not downloaded yet** "
           "(~49 GB) — restart the app to fetch them; this tab "
           "will refuse to run until then.")
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            wan_image = gr.Image(
                label="Start image (paste with Ctrl+V)",
                type="pil", sources=["upload", "clipboard"],
            )
            _recent_picker(wan_image)
            wan_prompt = gr.Textbox(
                label="Motion prompt",
                placeholder="she turns her head and smiles, "
                            "gentle camera push-in, wind in "
                            "the hair",
                lines=3,
            )
            wan_negative = gr.Textbox(
                label="Negative prompt (only used when CFG > 1, "
                      "i.e. Raw mode)",
                value=WAN_DEFAULT_NEGATIVE, lines=2,
            )
            wan_model = gr.Radio(
                choices=_wan_model_choices,
                value=_wan_model_choices[0],
                label="Model",
            )
            wan_mode = gr.Radio(
                choices=_wan_mode_choices,
                value=_wan_mode_choices[0 if WAN_VARIANT == "turbo"
                                        else 1],
                label="Mode (14B only — the 5B has no Lightning)",
            )
            with gr.Row():
                wan_steps = gr.Slider(
                    1, 40, value=_wan_defaults["steps"], step=1,
                    label="Steps",
                )
                wan_cfg = gr.Slider(
                    0.5, 8.0, value=_wan_defaults["cfg"], step=0.1,
                    label="CFG",
                )
            with gr.Row():
                wan_resolution = gr.Radio(
                    choices=list(WAN_RESOLUTIONS),
                    value=WAN_DEFAULT_RESOLUTION,
                    label="Resolution (keeps the source aspect)",
                )
            with gr.Row():
                wan_seconds = gr.Slider(
                    1.0, WAN_MAX_SECONDS, value=WAN_MAX_SECONDS,
                    step=0.25, label="Duration (seconds)",
                )
                wan_sampler = gr.Dropdown(
                    choices=SAMPLERS + ["uni_pc"], value="euler",
                    label="Sampler",
                )
            with gr.Row():
                wan_seed = gr.Number(
                    label="Seed", value=42, precision=0
                )
                wan_random = gr.Checkbox(
                    label="🎲 Random seed", value=True
                )
                wan_batch = gr.Slider(
                    1, 10, value=1, step=1, label="Batch count"
                )
            wan_btn = _cta("🎬 Generate video")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            wan_video_out = gr.Video(
                label="Latest video", autoplay=True
            )
            wan_files_out = gr.Files(
                label="All videos from this run", interactive=False
            )
            wan_status = _status_box()
            wan_seed_out = gr.Number(
                label="Base seed used", interactive=False,
                precision=0,
            )
    wan_model.change(
        fn=wan_model_changed, inputs=[wan_model, wan_mode],
        outputs=[wan_mode, wan_steps, wan_cfg],
    )
    wan_mode.change(
        fn=wan_mode_changed, inputs=[wan_model, wan_mode],
        outputs=[wan_steps, wan_cfg],
    )
    _queue_view(features.Key.WAN_I2V,
                [wan_files_out, wan_video_out, wan_status, wan_seed_out])
    wan_btn.click(
        # status_index=2: this tab yields two output components before its
        # status text, where every other tab yields one.
        fn=_enqueue(features.Key.WAN_I2V, generate_wan_video, prompt_arg=1,
                    status_index=2, lane=WAN_LANE),
        inputs=_recipe_view(features.Key.WAN_I2V, "video", [
            wan_image, wan_prompt, wan_negative, wan_model,
            wan_mode, wan_seed, wan_random, wan_steps,
            wan_cfg, wan_resolution, wan_seconds, wan_sampler, wan_batch]),
        outputs=[_queue_timer, wan_status],
    )


def _tab_json_batch(tab):
    """The JSON_BATCH tab body."""
    _tab_intro(
        "Submit a list of jobs, e.g.\n"
        '```json\n'
        '[{"prompt": "a cat", "steps": 8, "resolution": "1216x832",\n'
        '  "loras": {"krea2_darkbrush": 1.0}},\n'
        ' {"prompt": "a dog", "seed": 7, "model": "FinePn"}]\n'
        '```\n'
        'The optional `"model"` picks a registered Krea 2 model by '
        'name, filename or fragment (steps/CFG default to that '
        "model's settings; trigger words are **not** auto-added — "
        "write the full prompt you want)."
    )
    with gr.Row():
        with gr.Column(scale=2, elem_classes="kx-panel"):
            json_file_in = gr.File(label="Upload JSON file", type="filepath")
            json_text_in = gr.Textbox(
                label="…or paste a JSON array here", lines=14
            )
            json_btn = _cta("🚀 Run JSON batch")
        with gr.Column(scale=3, elem_classes="kx-panel-out"):
            json_gallery = gr.Gallery(
                label="Batch output", columns=2, height=600
            )
            json_status = _status_box()
    _queue_view(features.Key.JSON_BATCH, [json_gallery, json_status])
    json_btn.click(
        fn=_enqueue(features.Key.JSON_BATCH, generate_from_json),
        inputs=_recipe_view(features.Key.JSON_BATCH, "json", [
            json_file_in, json_text_in]),
        outputs=[_queue_timer, json_status],
    )


def _tab_gallery(tab):
    """The GALLERY tab body."""
    with gr.Row():
        gallery_refresh_btn = gr.Button("🔄 Refresh", size="sm")
        gallery_zip_btn = gr.Button(
            "📦 Zip all for download", size="sm"
        )
    gallery_info = gr.Markdown(
        "🔄 Refresh to load the gallery.",
        elem_classes="kx-meta",
    )
    # The originals, newest first, and how many of them the grid is
    # currently showing. State rather than a recomputed scan, so a click
    # cannot resolve against a different list than the one it was made on.
    gallery_paths = gr.State([])
    gallery_shown = gr.State(0)
    all_gallery = gr.Gallery(
        label="All generated images & videos (newest first)",
        # Filled when the tab is opened, never at build time: this used to
        # scan OUTPUT_DIR twice while the Blocks was still being built, and
        # then serve every full-size PNG in it to anyone who loaded the page.
        value=None, columns=5, height=520,
        # Clicking opens the original in the viewer below rather than
        # Gradio's lightbox, which would only enlarge the thumbnail.
        allow_preview=False,
        object_fit="cover",
        # Only fullscreen. The download buttons would hand over the 512px
        # thumbnail — silently, with nothing to say it is not the image.
        # Downloads come from the viewer below and the Zip button.
        buttons=["fullscreen"],
    )
    gallery_more_btn = gr.Button(
        f"⬇️ Load {GALLERY_PAGE} more", size="sm", visible=False
    )
    # One of these at a time, chosen by what was clicked — a video cannot
    # be shown in a gr.Image, and the Gallery tab is the only place a Wan
    # render can be watched again once its own tab has moved on.
    gallery_image = gr.Image(
        label="Selected image", visible=False, interactive=False,
        buttons=["download", "fullscreen"],
    )
    gallery_video = gr.Video(
        label="Selected video", visible=False, interactive=False,
    )
    # What the selected file was made with, and the way to make it again.
    # Hidden until something is picked, because an empty panel saying
    # "nothing selected" is a row of chrome that is wrong most of the time.
    with gr.Accordion("🧾 How this was made", open=True,
                      visible=False, elem_classes="kx-recipe") as recipe_panel:
        recipe_body = gr.Markdown(elem_classes="kx-meta")
        # The whole recipe, so the button resolves against the thing that
        # was drawn rather than re-reading a store that may have moved on.
        recipe_state = gr.State(None)
        recipe_btn = gr.Button(
            "▶️ Load these settings", variant="primary", size="sm",
            interactive=False,
        )
        # Filled by _use_recipe, which is wired after the tabs — its
        # outputs reach into every generation tab, and the last of those
        # is built after this one.
        recipe_status = gr.Markdown(elem_classes="kx-meta")
    gallery_zip_file = gr.File(
        label="Zip of all images", interactive=False
    )
    open_outputs = [gallery_paths, gallery_shown, all_gallery,
                    gallery_more_btn, gallery_info]
    tab.select(fn=refresh_gallery, outputs=open_outputs)
    gallery_refresh_btn.click(fn=refresh_gallery, outputs=open_outputs)
    gallery_more_btn.click(
        fn=_more_gallery, inputs=[gallery_paths, gallery_shown],
        outputs=[gallery_shown, all_gallery, gallery_more_btn, gallery_info],
    )
    all_gallery.select(
        fn=_pick_gallery, inputs=[gallery_paths],
        outputs=[gallery_image, gallery_video,
                 recipe_panel, recipe_body, recipe_state, recipe_btn],
    )
    gallery_zip_btn.click(
        fn=zip_outputs, outputs=[gallery_zip_file, gallery_info]
    )
    # The button is wired after the tabs, for the same reason the prompt
    # library's Use buttons are: it writes into every generation tab, and
    # this tab is not the last one built.
    return recipe_btn, recipe_state, recipe_status


# The tab strip, left to right. This tuple is the only thing that decides
# the order tabs appear in — moving an entry moves the tab, and nothing
# else has to change. A feature that is off is skipped, so the rest close
# up with no gap.
#
# `tab_id` is an explicit Gradio id, needed only by a tab something else
# selects programmatically: without one Gradio numbers tabs by
# construction order, which shifts with the licence, so the Prompt
# Library's "switch to that tab" would land on whatever happened to be
# third that day.
TAB_ORDER = (
    # Every generation tab carries an explicit id, and the ids here are the
    # ones _recipe_view registers: the Gallery's "Load these settings"
    # switches to whichever tab a picture came from, and that is any of
    # them. (The Prompt Library reaches only the first two, which is why
    # those two ids predate the rest and are not the feature keys.)
    (features.Key.KREA_T2I, _tab_krea_t2i, 'krea2'),
    (features.Key.KREA_V2_T2I, _tab_krea_v2_t2i, 'krea2v2'),
    # Its Use buttons reach into the two generation tabs, but that is
    # wired after the loop, so it may sit anywhere in this list.
    (features.Key.KREA_INPAINT, _tab_krea_inpaint, 'inpaint'),
    (features.Key.FACESWAP, _tab_faceswap, 'faceswap'),
    (features.Key.KREA_EDIT, _tab_krea_edit, 'edit'),
    (features.Key.KREA_V2_EDIT, _tab_krea_v2_edit, 'v2edit'),
    (features.Key.FLUX_T2I, _tab_flux_t2i, 'flux'),
    (features.Key.KLEIN_I2I, _tab_klein_i2i, 'klein'),
    (features.Key.WAN_I2V, _tab_wan_i2v, 'video'),
    (features.Key.COMMUNITY_PROMPTS, _tab_community_prompts, 'prompts'),
    (features.Key.GALLERY, _tab_gallery, None),
    (features.Key.JSON_BATCH, _tab_json_batch, 'json'),
)


# Serve generated media straight from OUTPUT_DIR instead of copying it into
# Gradio's cache. Without this, every filepath handed to a gr.Gallery is
# sha256'd and copy2'd (processing_utils.move_files_to_cache) — so one gallery
# refresh re-reads the whole output tree and doubles its footprint on a disk
# that is already ephemeral. Marking the dir static short-circuits both.
#
# "Static" means "assumed not to change at a given path". Every file ComfyUI
# writes has a fresh _00001_ counter, so that holds — the one exception is
# zip_outputs(), which is why the zip it writes carries a timestamp.
#
# This has to run before any component with a filepath value is built, i.e.
# before the Blocks below. It is process-wide, so scripts/dryrun.py inherits
# it by importing this module. It grants no access allowed_paths (see
# launch_ui) did not already grant.
gr.set_static_paths([str(OUTPUT_DIR)])

with gr.Blocks(title="Ember") as ui:
    # The application bar: brand and licence on the left, the way into the
    # pricing panel on the right. A Row rather than one gr.HTML because that
    # way in has to be a real Gradio button — the panel is this same page
    # with the tabs hidden, so there is no URL for a link to point at (see
    # the block after the footer) — and it belongs next to the licence it is
    # about rather than in a strip of its own below.
    #
    # licensing answers both of these before ui is imported (app.py takes
    # the seat first), and answers None for both on a dry run with
    # --features, which the header renders as a licence naming no plan.
    with gr.Row(elem_id="kx-header"):
        gr.HTML(
            theme.header_html(
                plan_name=licensing.plan()[1],     # (id, name) — name only
                expires_at=licensing.expires_at(),
            ),
            elem_classes="kx-headline", container=False, padding=False,
        )
        pricing_open_btn = gr.Button("💳 Plans & pricing", size="sm",
                                     elem_classes="kx-navbtn", scale=0)

    # The queue's clock, and the per-page record of what it has already
    # drawn. Both are declared *before* the tabs because every Generate
    # button lists the timer in its outputs to switch it on, and a click
    # can only name a component that already exists. Its .tick() is
    # registered after the panel below, once every output it writes to is
    # known — Gradio is happy to have the event added later, only not the
    # component.
    _queue_timer = gr.Timer(QUEUE_POLL_SECONDS, active=False)
    _queue_seen = gr.State({})

    with gr.Tabs() as main_tabs:
        # Build order is TAB_ORDER's order. Each builder's return value is
        # kept so the cross-tab wiring below can find it.
        _exports = {}
        for _key, _build, _tab_id in TAB_ORDER:
            if not features.enabled(_key):
                continue
            with gr.Tab(features.label_for(_key), id=_tab_id) as _tab:
                _exports[_key] = _build(_tab)

    # Read at request time by _card_button and _use_prompt, so they must be
    # module state rather than the builders' locals. Still None when the tab
    # is not licensed, which is what the library reads to grey a card out.
    _krea_targets = _exports.get(features.Key.KREA_T2I)
    _v2_targets = _exports.get(features.Key.KREA_V2_T2I)

    # Declared after the loop, and that is what lets TAB_ORDER be reordered
    # freely: the Use buttons write into the two generation tabs' controls,
    # so wiring them inside the library's own body would force it to be
    # built last. Gradio only requires a component to exist before the
    # .click() naming it, not before the tab it lives in.
    if features.Key.COMMUNITY_PROMPTS in _exports:
        _lib_rows, _lib_buttons = _exports[features.Key.COMMUNITY_PROMPTS]
        # Every Use button writes to both tabs' controls, because the
        # outputs list is fixed at build time; _use_prompt no-ops the half
        # it is not loading. An unlicensed tab contributes nothing, and its
        # cards' buttons are dead.
        _use_outputs = [*(_krea_targets or []), *(_v2_targets or []),
                        main_tabs]
        for _slot, _button in enumerate(_lib_buttons):
            _button.click(fn=partial(_use_prompt, _slot),
                          inputs=_lib_rows, outputs=_use_outputs)

    # The Gallery's "Load these settings". Wired here rather than in the
    # tab for the same reason as the library's Use buttons — it writes into
    # every generation tab's controls, and the JSON tab is built after the
    # Gallery — and reading _recipe_components() only now is what lets a
    # licence decide which of those tabs exist to be written to.
    if features.Key.GALLERY in _exports:
        _recipe_btn, _recipe_state, _recipe_status = _exports[
            features.Key.GALLERY]
        _recipe_btn.click(
            fn=_use_recipe, inputs=_recipe_state,
            outputs=[*_recipe_components(), main_tabs, _recipe_status],
        )

    # The settings half of the same idea: each generation tab's dropdown is
    # built already showing the preset the server marks as that tab's
    # default, so the values behind it have to be applied for the two to
    # agree. Done on page load rather than at build time because the
    # components carry config.py's values as their `value=` — that is the
    # floor a pod with no server keeps, and this writes over it when there
    # is one.
    _krea_preset_targets = (_krea_targets or [])[PROMPT_BOXES:]
    _v2_preset_targets = (_v2_targets or [])[PROMPT_BOXES:]
    if _krea_preset_targets or _v2_preset_targets:
        ui.load(fn=_load_default_presets,
                outputs=[*_krea_preset_targets, *_v2_preset_targets])

    # Outside the Tabs: one line under every tab, carrying the Ctrl+Enter
    # hint (theme.JS binds it) — a shortcut nobody would find otherwise —
    # and the model/GPU counts and output path, which used to sit in the
    # header until it was cut back to the licence.
    footer = gr.HTML(
        theme.footer_html(model_count=len(MODEL_CHOICES), gpu_count=GPU_COUNT,
                          output_dir=OUTPUT_DIR),
        elem_id="kx-footer", container=False, padding=False,
    )

    # ------------------------------------------------------------- queue
    # Under the tabs rather than inside any one of them, because the queue
    # is the pod's and not a tab's: a Krea job and a video are waiting on
    # the same GPU, and seeing that from whichever tab you are on is most
    # of the value.
    #
    # Open by default. A queue whose whole purpose is to be looked at
    # should not have to be found first, and it is one line tall when
    # empty. Closing it is not fought over either — the counts live in the
    # label, so a collapsed panel still says how deep the queue is.
    with gr.Accordion("🗂️ Queue — nothing running", open=True,
                      elem_classes="kx-queue") as queue_panel:
        with gr.Row(elem_classes="kx-navrow"):
            queue_clear_btn = gr.Button("🧹 Clear finished", size="sm")
        queue_info = gr.Markdown(
            "Nothing queued. Every **Generate** click is added here, so "
            "several can be lined up at once — and any of them taken back "
            "out again.",
            elem_classes="kx-meta",
        )
        # The ids currently on screen, in row order. Client state, like the
        # library's rows: the ✕ in slot 3 has to resolve to a job, and the
        # slot number alone stops meaning anything the moment the list
        # under it moves.
        queue_ids = gr.State([])
        queue_groups, queue_bodies, queue_buttons = [], [], []
        for _slot in range(QUEUE_ROWS):
            with gr.Row(visible=False,
                        elem_classes="kx-queue-row") as _queue_row:
                queue_bodies.append(
                    gr.Markdown(elem_classes="kx-queue-body")
                )
                queue_buttons.append(
                    gr.Button("✕ Remove", size="sm", scale=0, min_width=110)
                )
            queue_groups.append(_queue_row)

    # Everything a poll writes, in the order _queue_tick returns it. Built
    # here, after the tabs, because the per-tab half of that list is
    # whatever those tabs registered on their way past — a licence that
    # grants three tabs polls three tabs.
    _queue_outputs = [
        _queue_seen, _queue_timer,
        queue_panel, queue_ids, queue_info,
        *queue_groups, *queue_bodies, *queue_buttons,
        *[component for _key, components in _QUEUE_VIEWS
          for component in components],
        *[dropdown for _tab, dropdown in _QUEUE_PRESET_VIEWS],
    ]
    _queue_timer.tick(fn=_queue_tick, inputs=_queue_seen,
                      outputs=_queue_outputs, show_progress="hidden")
    queue_clear_btn.click(fn=_queue_clear, inputs=_queue_seen,
                          outputs=_queue_outputs, show_progress="hidden")
    for _slot, _button in enumerate(queue_buttons):
        _button.click(fn=partial(_queue_cancel, _slot),
                      inputs=[queue_ids, _queue_seen],
                      outputs=_queue_outputs, show_progress="hidden")
    # A page opened while the pod is mid-job has to find that out, and a
    # timer that starts off would never ask.
    ui.load(fn=_queue_wake, outputs=_queue_timer)

    # ----------------------------------------------------------- pricing panel
    # The plan catalogue, as a view of this same page: opening it hides the
    # tabs and the footer, and the Back button puts them back. Not a tenth
    # tab, because it belongs to no generation flow and reads the same for
    # every licence; and not a gr.Blocks route, because Gradio only allows
    # those outside the Blocks context, which would mean a second page with
    # its own header and its own copy of this app's chrome.
    #
    # Deliberately not behind a features.enabled() check: what a tier costs
    # is not something a tier can be sold the right to read, and a customer
    # deciding whether to upgrade is exactly the one whose licence does not
    # grant the thing they are reading about.
    with gr.Column(visible=False) as pricing_view:
        with gr.Row(elem_classes="kx-navrow"):
            pricing_back_btn = gr.Button("← Back to the app", size="sm")
            pricing_refresh = gr.Button("🔄 Refresh plans", size="sm")
        # Empty until opened — _open_pricing fills it, so nothing here
        # touches the licence server while the pod is still booting.
        pricing_body = gr.HTML(container=False, padding=False)
        # Under the plan cards: what each granted tab actually does, in
        # prose and screenshots. Filled at build time rather than on open,
        # and deliberately a component of its own:
        #
        #   * it reads nothing but bundled files, so there is no reason to
        #     wait for the panel to be opened — and no reason for Refresh,
        #     which refetches the price list, to rebuild it
        #   * the whole panel starts hidden and every picture in here is
        #     lazy, so sitting in the DOM from page load costs no requests
        gr.HTML(theme.showcase_html(showcase.showcase()),
                container=False, padding=False)

    _pricing_views = [pricing_open_btn, main_tabs, queue_panel, footer,
                      pricing_view]
    pricing_open_btn.click(fn=_open_pricing,
                           outputs=[*_pricing_views, pricing_body])
    pricing_back_btn.click(fn=_close_pricing, outputs=_pricing_views)
    # force=True skips plans.TTL_SECONDS — the button exists for the minute
    # after a price is edited on the server.
    pricing_refresh.click(fn=lambda: _pricing_body(force=True),
                          outputs=pricing_body)


def _probe_url(url: str, deadline_s: int = 45) -> bool:
    """True if `url` serves the app (HTTP 2xx/3xx) within deadline_s."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=15)
            if resp.ok:
                return True
            log.warning("Share URL answered HTTP %d — retrying", resp.status_code)
        except Exception as exc:
            log.warning("Share URL not reachable yet (%s)", exc)
        time.sleep(5)
    return False


def _start_cloudflared(port: int):
    """Start a Cloudflare quick tunnel (no account needed); return (proc, url)."""
    binary = TEMP_DIR / "cloudflared"
    if not binary.exists():
        log.info("Downloading cloudflared ...")
        urllib.request.urlretrieve(
            "https://github.com/cloudflare/cloudflared/releases/latest/"
            "download/cloudflared-linux-amd64",
            binary,
        )
        binary.chmod(0o755)
    proc = subprocess.Popen(
        [str(binary), "tunnel", "--url", f"http://127.0.0.1:{port}",
         "--no-autoupdate"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + 90
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        if not line:
            break
        found = pattern.search(line)
        if found:
            return proc, found.group(0)
    proc.terminate()
    raise RuntimeError(
        "cloudflared did not produce a tunnel URL within 90s — "
        "restart the app to retry"
    )


def launch_ui() -> None:
    """Launch Gradio, verify the public link works, keep the app alive."""
    log.info("Launching Gradio ...")
    _app, _local_url, share_url = ui.launch(
        server_name="0.0.0.0", server_port=7860, share=True,
        show_error=True, ssr_mode=False, prevent_thread_lock=True,
        # OUTPUT_DIR is outside the cwd, so Gradio needs it whitelisted to
        # serve gallery images (on Kaggle the cwd contained the output dir).
        # Nothing else needs whitelisting: the pricing page's showcase
        # images are loaded by the browser straight from the R2 bucket, so
        # they never pass through this app at all.
        allowed_paths=[str(OUTPUT_DIR)],
        **theme.launch_kwargs(),
    )
    tunnel_proc, public_url = None, share_url
    if share_url and _probe_url(share_url):
        log.info("gradio.live link verified")
    else:
        log.warning(
            "The gradio.live link is not answering (some hosts block or time "
            "out Gradio's share tunnel) — starting a Cloudflare tunnel instead."
        )
        tunnel_proc, public_url = _start_cloudflared(7860)
        if share_url:
            log.info("(the gradio.live link may still start working later: %s)",
                     share_url)
    print(f"\n{'=' * 60}\n>>> OPEN THE UI HERE: {public_url}\n{'=' * 60}\n",
          flush=True)
    try:
        while True:  # keep the app alive; press Ctrl-C to stop
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Stopping the UI")
        if tunnel_proc is not None:
            tunnel_proc.terminate()
        ui.close()
