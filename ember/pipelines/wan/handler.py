"""The Video tab's generator: Wan 2.2 image-to-video."""

import random
import uuid

from ember.comfy.client import wan_client
from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.comfy.server import node_registered
from ember import features
from ember.generation.runner import _png_bytes, _run_tag, _run_wan_jobs
from ember.pipelines.common import resolve_model
from ember.pipelines.wan.constants import (
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_ARCH_5B,
    WAN_LEGACY_MODEL_VALUES,
    WAN_FPS,
    WAN_MODE_DEFAULTS,
    WAN_RESOLUTIONS,
    WAN_RIFE_NODES,
)
from ember.pipelines.wan.workflow import (
    RIFE_NODE,
    build_wan_5b_workflow,
    build_wan_i2v_workflow,
    wan_5b_available,
    wan_lightning_available,
    wan_models_available,
    wan_rife_available,
)
from ember.settings import (
    COMFY_LOG,
    COMFY_PORT,
    WAN_COMFY_LOG,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
)


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


def _wan_model(model_id):
    """The catalogue model for the Model value — the built-in pair when the
    licence server lists none (catalog.py). A label from before the value
    was an id is read as the id it became; an id the feature does not list
    falls back to its first model (resolve_model)."""
    model_id = WAN_LEGACY_MODEL_VALUES.get(model_id, model_id)
    return resolve_model(str(features.Key.WAN_I2V), model_id)


def _interpolation_problem() -> str:
    """Why Smooth motion cannot run right now, or "" when it can.

    Asked of the instance the clips go to — the second one when
    EMBER_WAN_PARALLEL is on — and only after that instance answers, so a
    server that is down or restarting is not blamed on the pack.
    """
    if not wan_rife_available():
        return ("the RIFE checkpoint is not downloaded yet — restart the "
                "app so the download step can fetch it")
    port, log_path = ((WAN_COMFY_PORT, WAN_COMFY_LOG) if WAN_PARALLEL
                      else (COMFY_PORT, COMFY_LOG))
    alive, _note = comfy_ensure_alive(port=port, log_path=log_path)
    if alive and not node_registered(RIFE_NODE, port=port):
        return ("ComfyUI has not loaded the %s node — restart the app so "
                "setup can install %s" % (RIFE_NODE, WAN_RIFE_NODES[0]))
    return ""


def generate_wan_video(image, prompt, negative, model, mode, seed, randomize,
                       steps, cfg, resolution, seconds, sampler, batch_count,
                       interpolate=False):
    """Video tab: animate an uploaded image with Wan 2.2 (14B I2V or 5B TI2V).

    Smooth motion is an improvement rather than the job, so when it cannot
    run the clip is made without it and the status says why — a missing
    pack never costs the tab.
    """
    if image is None:
        yield [], None, "❌ Upload an image first.", 0
        return
    entry = _wan_model(model)
    if entry is None:                       # cannot happen: there is a built-in
        yield [], None, ("❌ No Wan model is configured — restart the app "
                         "once the licence server can be reached."), 0
        return
    use_5b = entry.arch == WAN_ARCH_5B
    low = entry.part("low")
    if use_5b and not wan_5b_available(entry.file):
        yield [], None, (f"❌ Wan model “{entry.name}” is not downloaded yet "
                         "— restart the app so the download step can fetch "
                         "it, or pick another Model."), 0
        return
    if not use_5b and low is None:
        yield [], None, (f"❌ Wan model “{entry.name}” is a two-expert 14B "
                         "model with no low-noise expert listed — its "
                         "catalogue record needs parts.low."), 0
        return
    if not use_5b and not wan_models_available(entry.file, low.file):
        yield [], None, (f"❌ Wan model “{entry.name}” is not downloaded yet "
                         "— restart the app so the download step can fetch "
                         "it, or pick another Model."), 0
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
    notice = ""
    if interpolate:
        problem = _interpolation_problem()
        if problem:
            notice = ("⚠️ Smooth motion skipped: %s. The video is at %d "
                      "fps.\n" % (problem, fps))
            interpolate = False
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
        "interpolate": bool(interpolate),
        # Spelled out here rather than left to the builder's default so
        # the tag can go on it — see _run_tag. Videos land in wan/, and
        # that subfolder has its own counter to be knocked backwards.
        "filename_prefix": f"{wan_prefix}_{_run_tag()}",
        **({"unet": entry.file} if use_5b else
           {"lightning": turbo, "unet_high": entry.file,
            "unet_low": low.file}),
    } for i in range(int(batch_count))]
    for videos, latest, status in _run_wan_jobs(jobs, builder=builder):
        yield videos, latest, notice + status, base_seed
