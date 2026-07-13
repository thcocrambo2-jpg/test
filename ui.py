"""Gradio UI.

Five tabs: single / simple-batch generation, instruction-based editing
(upload an image, describe the change), inpainting (paint a mask over an
uploaded image), JSON batch jobs, and an output gallery. Written
for Gradio 6 (theme/css now belong to launch(), gr.File hands the handler
a plain file path).

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
from pathlib import Path

import gradio as gr
import requests
from PIL import Image, ImageChops, ImageFilter

from client import ComfyUIError, client, wan_client
from comfy import GPU_COUNT
from config import (
    DEFAULT_LORAS,
    DEFAULT_RESOLUTION,
    FLUX_ENABLED,
    FLUX_MODELS,
    KREA2_MODELS,
    OUTPUT_DIR,
    RESOLUTION_PRESETS,
    SAMPLERS,
    TEMP_DIR,
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_DEFAULT_NEGATIVE,
    WAN_DEFAULT_RESOLUTION,
    WAN_ENABLED,
    WAN_FPS,
    WAN_MAX_SECONDS,
    WAN_MODE_DEFAULTS,
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
from workflow_wan import (
    build_wan_5b_workflow,
    build_wan_i2v_workflow,
    wan_5b_available,
    wan_lightning_available,
    wan_models_available,
)

MAX_LORA_SLOTS = 3
MODEL_CHOICES = list_model_names()
_d_steps, _d_cfg = model_defaults(resolve_model_entry(None))
DEFAULTS = {"steps": _d_steps, "cfg": _d_cfg}
LORA_CHOICES = ["None"] + list_lora_files()
FLUX_MODEL_CHOICES = flux_model_names()
FLUX_LORA_CHOICES = ["None"] + list_flux_lora_files()
_f_steps, _f_guidance, _ = flux_model_defaults(resolve_flux_model(None))


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


def _run_jobs(jobs, builder=build_workflow, prefix="Krea2"):
    """Shared executor: yields (gallery_paths, status_text) as work progresses."""
    images = []
    total = len(jobs)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        job_prefix = prefix
        if job["loras"]:
            job_prefix += "_" + Path(job["loras"][0][0]).stem
        workflow = builder(filename_prefix=job_prefix, **job)
        size = f", {job['width']}×{job['height']}" if "width" in job else ""
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


def _resolve_lora_slots(lora1, w1, lora2, w2, lora3, w3) -> list:
    """UI LoRA dropdown/weight pairs → resolved (filename, strength) list."""
    loras = []
    for name, weight in ((lora1, w1), (lora2, w2), (lora3, w3)):
        lora_file = resolve_lora_name(name)
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


def generate_single(prompt, negative, seed, randomize, steps, cfg, resolution,
                    sampler, model, lora1, w1, lora2, w2, lora3, w3,
                    batch_count):
    """First tab: run batch_count jobs on sequential seeds."""
    entry, error = _check_model(model)
    if error:
        yield [], error, 0
        return
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = parse_resolution(resolution)
    loras = _resolve_lora_slots(lora1, w1, lora2, w2, lora3, w3)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width, "height": height,
        "sampler": sampler, "loras": loras, "unet_file": entry["file"],
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs):
        yield images, status, base_seed


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
                  sampler, model, lora1, w1, lora2, w2, lora3, w3,
                  batch_count):
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
    loras = []
    for name, weight in ((lora1, w1), (lora2, w2), (lora3, w3)):
        lora_file = resolve_flux_lora(name)
        if lora_file:
            loras.append((lora_file, float(weight)))
    jobs = [{
        "prompt": prompt, "seed": base_seed + i, "steps": int(steps),
        "guidance": float(guidance), "width": width, "height": height,
        "sampler": sampler, "loras": loras, "unet_file": entry["file"],
        "turbo_lora": turbo,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_flux_workflow,
                                    prefix="Flux2"):
        yield images, status, base_seed


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
                     cfg, denoise, sampler, grow, blur, model,
                     lora1, w1, lora2, w2, lora3, w3, batch_count):
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
        "loras": _resolve_lora_slots(lora1, w1, lora2, w2, lora3, w3),
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
    """
    scale = min(1.0, (max_pixels / (w * h)) ** 0.5)
    return (max(64, int(w * scale) // 16 * 16),
            max(64, int(h * scale) // 16 * 16))


def generate_edit(image, prompt, negative, seed, randomize, steps, cfg,
                  sampler, grounding, model, lora1, w1, lora2, w2, lora3, w3,
                  batch_count):
    """Edit tab: instruction-based editing. The model sees the source image
    (Identity Edit LoRA dual conditioning), so the prompt describes the
    change to make — no mask, no denoise tuning."""
    if image is None:
        yield [], "❌ Upload an image first.", 0
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
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image), f"edit_{tag}.png")
    except Exception as exc:
        yield [], f"❌ Uploading the image to ComfyUI failed: {exc}", base_seed
        return
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "image_name": image_name,
        "grounding_px": int(grounding),
        "loras": _resolve_lora_slots(lora1, w1, lora2, w2, lora3, w3),
        "unet_file": entry["file"],
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_edit_workflow,
                                    prefix="Krea2Edit"):
        yield images, status, base_seed


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
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        workflow = builder(**job)
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


def list_output_images() -> list[str]:
    """Every generated image and video in OUTPUT_DIR, newest first."""
    media = [p for pattern in ("*.png", "*.mp4", "*.webm")
             for p in OUTPUT_DIR.rglob(pattern)]
    return [str(p) for p in sorted(media, key=lambda p: p.stat().st_mtime,
                                   reverse=True)]


RECENT_IMAGES_LIMIT = 20


def list_recent_images(limit: int = RECENT_IMAGES_LIMIT) -> list[str]:
    """The newest generated still images (for the pick-from-existing pickers)."""
    pngs = sorted(OUTPUT_DIR.rglob("*.png"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in pngs[:limit]]


def _picked_recent(evt: gr.SelectData):
    """Gallery click → file path for a gr.Image input (no-op if unreadable)."""
    value = evt.value
    path = None
    if isinstance(value, dict):
        media = value.get("image") or {}
        path = media.get("path") or media.get("url")
    elif isinstance(value, str):
        path = value
    return path if path else gr.Image()


def _recent_picker(target_image):
    """A collapsed 'use a previous generation' gallery under an image input.

    Must be called inside a gr.Blocks context, after `target_image` exists.
    Clicking a thumbnail loads that file into `target_image`; the refresh
    button re-scans OUTPUT_DIR for the newest images.
    """
    with gr.Accordion(
        f"📂 Use a previous generation (last {RECENT_IMAGES_LIMIT} images)",
        open=False,
    ):
        picker = gr.Gallery(
            value=list_recent_images(), label="Click an image to use it",
            columns=5, height=240, allow_preview=False,
        )
        refresh_btn = gr.Button("🔄 Refresh", size="sm")
        refresh_btn.click(fn=list_recent_images, outputs=picker)
        picker.select(fn=_picked_recent, outputs=target_image)


def refresh_gallery():
    """Re-scan OUTPUT_DIR for the Gallery tab."""
    images = list_output_images()
    return images, f"{len(images)} file(s) in `{OUTPUT_DIR}`"


def zip_outputs():
    """Bundle all generated media into one zip (the pod disk is ephemeral)."""
    images = list_output_images()
    if not images:
        return None, "No images to zip yet."
    # PNGs/MP4s are already compressed — store instead of deflating (faster).
    zip_path = OUTPUT_DIR / "all_outputs.zip"
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
    info = gr.Markdown(_model_info_text(resolve_model_entry(None)))
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


def _lora_stack():
    """LoRA slot dropdown/weight rows + rescan button (used by two tabs).

    Must be called inside a gr.Blocks context. Returns (dropdowns, weights);
    the rescan button is wired to refresh its own tab's dropdowns.
    """
    gr.Markdown("### 🎭 LoRA stack")
    dds, ws = [], []
    for slot, (default_name, default_weight) in enumerate(
            _default_lora_slots(), start=1):
        with gr.Row():
            dds.append(gr.Dropdown(
                choices=LORA_CHOICES, value=default_name,
                label=f"LoRA {slot}", scale=3,
            ))
            ws.append(gr.Slider(
                0.0, 2.0, value=default_weight, step=0.05,
                label="Weight", scale=1,
            ))
    refresh_btn = gr.Button("🔄 Rescan LoRA folder", size="sm")
    refresh_btn.click(fn=refresh_lora_choices, outputs=dds)
    return dds, ws


with gr.Blocks(title="Krea 2 on RunPod") as ui:
    gr.Markdown(
        "# ⚡ Krea 2"
        + (" + Flux 2" if FLUX_ENABLED else "")
        + (" + Wan 2.2 Video" if WAN_ENABLED else "")
        + " — ComfyUI on RunPod\n"
        f"{len(MODEL_CHOICES)} Krea model(s) · {GPU_COUNT} GPU(s) detected · "
        f"native ComfyUI multi-GPU placement · "
        f"outputs saved to `{OUTPUT_DIR}`"
    )
    with gr.Tabs():
        with gr.Tab("Single / Simple Batch"):
            with gr.Row():
                with gr.Column(scale=2):
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
                    generate_btn = gr.Button(
                        "🚀 Generate", variant="primary", size="lg"
                    )
                with gr.Column(scale=3):
                    gallery = gr.Gallery(label="Output", columns=2, height=600)
                    status_box = gr.Textbox(label="Status", interactive=False)
                    seed_out = gr.Number(
                        label="Base seed used", interactive=False, precision=0
                    )
            generate_btn.click(
                fn=generate_single,
                inputs=[prompt_box, negative_box, seed_box, randomize_cb,
                        steps_slider, cfg_slider, resolution_dd, sampler_dd,
                        model_dd,
                        lora_dds[0], lora_ws[0], lora_dds[1], lora_ws[1],
                        lora_dds[2], lora_ws[2], batch_slider],
                outputs=[gallery, status_box, seed_out],
            )

        with gr.Tab("✨ Edit (Instruction)"):
            gr.Markdown(
                "Upload an image and **describe the change** — no painting "
                "needed. The Identity Edit LoRA lets the model see the "
                "source image, so it can recolor, add or replace objects, "
                "restyle, or re-stage a person in a new scene while keeping "
                "their identity. Defaults (8–12 steps, CFG 1.0) suit most "
                "edits; removals work better with ~20 steps and CFG ≈ 3."
                + ("" if edit_lora_available() else
                   "\n\n⚠️ **The Identity Edit LoRA is not downloaded yet** "
                   "(~1.9 GB) — restart the app to fetch it; this tab will "
                   "refuse to run until then.")
            )
            with gr.Row():
                with gr.Column(scale=2):
                    edit_image = gr.Image(
                        label="Source image (paste with Ctrl+V)", type="pil",
                        sources=["upload", "clipboard"],
                    )
                    _recent_picker(edit_image)
                    edit_prompt = gr.Textbox(
                        label="Edit instruction",
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
                        edit_grounding = gr.Slider(
                            512, 1536, value=768, step=64,
                            label="Grounding (low = stronger edit, "
                                  "high = keep likeness)",
                        )
                        edit_sampler = gr.Dropdown(
                            choices=SAMPLERS, value=SAMPLERS[0], label="Sampler"
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
                    edit_btn = gr.Button("✨ Edit", variant="primary", size="lg")
                with gr.Column(scale=3):
                    edit_gallery = gr.Gallery(label="Output", columns=2, height=600)
                    edit_status = gr.Textbox(label="Status", interactive=False)
                    edit_seed_out = gr.Number(
                        label="Base seed used", interactive=False, precision=0
                    )
            edit_btn.click(
                fn=generate_edit,
                inputs=[edit_image, edit_prompt, edit_negative, edit_seed,
                        edit_random, edit_steps, edit_cfg, edit_sampler,
                        edit_grounding, edit_model_dd,
                        edit_lora_dds[0], edit_lora_ws[0],
                        edit_lora_dds[1], edit_lora_ws[1],
                        edit_lora_dds[2], edit_lora_ws[2], edit_batch],
                outputs=[edit_gallery, edit_status, edit_seed_out],
            )

        with gr.Tab("Inpaint / Img2Img"):
            gr.Markdown(
                "Upload an image, **paint over the region to replace**, and "
                "describe what should appear there — unpainted pixels are "
                "kept from the original. Paint **nothing** to re-imagine the "
                "whole image (img2img); in that mode lower **Denoise** "
                "(≈0.5–0.8) to control how much of the original survives."
            )
            with gr.Row():
                with gr.Column(scale=2):
                    inpaint_editor = gr.ImageEditor(
                        label="Image — paint the region to replace "
                              "(paste with Ctrl+V)",
                        type="pil",
                        sources=["upload", "clipboard"],
                        brush=gr.Brush(colors=["#FF3366"], color_mode="fixed"),
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
                    inpaint_btn = gr.Button(
                        "🖌️ Inpaint", variant="primary", size="lg"
                    )
                with gr.Column(scale=3):
                    inpaint_gallery = gr.Gallery(
                        label="Output", columns=2, height=600
                    )
                    inpaint_status = gr.Textbox(
                        label="Status", interactive=False
                    )
                    inpaint_seed_out = gr.Number(
                        label="Base seed used", interactive=False, precision=0
                    )
            inpaint_btn.click(
                fn=generate_inpaint,
                inputs=[inpaint_editor, inpaint_prompt, inpaint_negative,
                        inpaint_seed, inpaint_random, inpaint_steps,
                        inpaint_cfg, inpaint_denoise, inpaint_sampler,
                        inpaint_grow, inpaint_blur, inpaint_model_dd,
                        inpaint_lora_dds[0], inpaint_lora_ws[0],
                        inpaint_lora_dds[1], inpaint_lora_ws[1],
                        inpaint_lora_dds[2], inpaint_lora_ws[2],
                        inpaint_batch],
                outputs=[inpaint_gallery, inpaint_status, inpaint_seed_out],
            )

        if FLUX_ENABLED:
            with gr.Tab("🌊 Flux 2"):
                gr.Markdown(
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
                    with gr.Column(scale=2):
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
                            _flux_model_info_text(resolve_flux_model(None))
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
                        gr.Markdown("### 🎭 Flux LoRA stack (`loras/flux2/`)")
                        flux_lora_dds, flux_lora_ws = [], []
                        for _slot in range(1, MAX_LORA_SLOTS + 1):
                            with gr.Row():
                                flux_lora_dds.append(gr.Dropdown(
                                    choices=FLUX_LORA_CHOICES, value="None",
                                    label=f"LoRA {_slot}", scale=3,
                                ))
                                flux_lora_ws.append(gr.Slider(
                                    0.0, 2.0, value=0.8, step=0.05,
                                    label="Weight", scale=1,
                                ))
                        flux_lora_refresh = gr.Button(
                            "🔄 Rescan Flux LoRA folder", size="sm"
                        )
                        flux_lora_refresh.click(
                            fn=refresh_flux_lora_choices,
                            outputs=flux_lora_dds,
                        )
                        flux_btn = gr.Button(
                            "🌊 Generate", variant="primary", size="lg"
                        )
                    with gr.Column(scale=3):
                        flux_gallery = gr.Gallery(
                            label="Output", columns=2, height=600
                        )
                        flux_status = gr.Textbox(
                            label="Status", interactive=False
                        )
                        flux_seed_out = gr.Number(
                            label="Base seed used", interactive=False,
                            precision=0,
                        )
                flux_btn.click(
                    fn=generate_flux,
                    inputs=[flux_prompt, flux_seed, flux_random, flux_steps,
                            flux_guidance, flux_resolution, flux_sampler,
                            flux_model_dd,
                            flux_lora_dds[0], flux_lora_ws[0],
                            flux_lora_dds[1], flux_lora_ws[1],
                            flux_lora_dds[2], flux_lora_ws[2], flux_batch],
                    outputs=[flux_gallery, flux_status, flux_seed_out],
                )

        if WAN_ENABLED:
            with gr.Tab("🎬 Video (Wan 2.2)"):
                _wan_defaults = WAN_MODE_DEFAULTS[WAN_VARIANT]
                _wan_mode_choices = ["Turbo (Lightning, 4 steps)",
                                     "Raw (20 steps)"]
                _wan_model_choices = ["14B two-expert (best quality, 16 fps)",
                                      "5B TI2V (lighter, 24 fps)"]
                gr.Markdown(
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
                    with gr.Column(scale=2):
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
                        wan_btn = gr.Button(
                            "🎬 Generate video", variant="primary", size="lg"
                        )
                    with gr.Column(scale=3):
                        wan_video_out = gr.Video(
                            label="Latest video", autoplay=True
                        )
                        wan_files_out = gr.Files(
                            label="All videos from this run", interactive=False
                        )
                        wan_status = gr.Textbox(
                            label="Status", interactive=False
                        )
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
                wan_btn.click(
                    fn=generate_wan_video,
                    inputs=[wan_image, wan_prompt, wan_negative, wan_model,
                            wan_mode, wan_seed, wan_random, wan_steps,
                            wan_cfg, wan_resolution, wan_seconds, wan_sampler,
                            wan_batch],
                    outputs=[wan_files_out, wan_video_out, wan_status,
                             wan_seed_out],
                )

        with gr.Tab("JSON Advanced Batch"):
            gr.Markdown(
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
                with gr.Column(scale=2):
                    json_file_in = gr.File(label="Upload JSON file", type="filepath")
                    json_text_in = gr.Textbox(
                        label="…or paste a JSON array here", lines=14
                    )
                    json_btn = gr.Button(
                        "🚀 Run JSON batch", variant="primary", size="lg"
                    )
                with gr.Column(scale=3):
                    json_gallery = gr.Gallery(
                        label="Batch output", columns=2, height=600
                    )
                    json_status = gr.Textbox(label="Status", interactive=False)
            json_btn.click(
                fn=generate_from_json,
                inputs=[json_file_in, json_text_in],
                outputs=[json_gallery, json_status],
            )

        with gr.Tab("Gallery"):
            with gr.Row():
                gallery_refresh_btn = gr.Button("🔄 Refresh", size="sm")
                gallery_zip_btn = gr.Button(
                    "📦 Zip all for download", size="sm"
                )
            gallery_info = gr.Markdown(
                f"{len(list_output_images())} file(s) in `{OUTPUT_DIR}`"
            )
            all_gallery = gr.Gallery(
                label="All generated images & videos (newest first)",
                value=list_output_images(), columns=4, height=700,
            )
            gallery_zip_file = gr.File(
                label="Zip of all images", interactive=False
            )
            gallery_refresh_btn.click(
                fn=refresh_gallery, outputs=[all_gallery, gallery_info]
            )
            gallery_zip_btn.click(
                fn=zip_outputs, outputs=[gallery_zip_file, gallery_info]
            )


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
        allowed_paths=[str(OUTPUT_DIR)],
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
