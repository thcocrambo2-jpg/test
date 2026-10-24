"""The ⚡ Z-Image generator."""

import random
import uuid

from PIL import Image, ImageOps

from ember.comfy.client import client
from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.comfy.server import node_registered
from ember.generation.handlers import ZIMAGE_T2I, _check_model, _save_preset
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _png_bytes, _run_jobs
from ember.licensing import presets
from ember.pipelines.zimage.constants import (
    ZIMAGE_DEFAULT_RESOLUTION,
    ZIMAGE_RESOLUTIONS,
    ZIMAGE_UPSCALE_NODES,
)
from ember.pipelines.zimage.workflow import (
    UPSCALE_NODE,
    build_zimage_workflow,
    init_size,
    output_size,
    zimage_missing,
)


def _zimage_settings(seed, randomize, steps, cfg, resolution, multiplier,
                     sampler, model, upscale, batch_count, denoise,
                     lora_slots) -> dict:
    """The Z-Image tab's controls as a preset stores them.

    The UI values, ids and all — the shape of _krea_settings, plus the
    multiplier, the upscale tick and the start image's denoise. Never the
    start image itself: a preset is settings, and pictures stay with the
    form. Mirrors the generate_zimage signature; scripts/golden.py checks
    it against tabschema's settings().
    """
    return {
        "model": model,
        "steps": int(steps),
        "cfg": float(cfg),
        "resolution": resolution,
        "multiplier": float(multiplier),
        "sampler": sampler,
        "upscale": bool(upscale),
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "denoise": float(denoise),
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def prepare_init(image: Image.Image, width: int, height: int) -> Image.Image:
    """The start image as VAEEncode gets it: upright, RGB, at init_size.

    Turned upright from its EXIF orientation, transparency composited onto
    white, and resized with LANCZOS to its own shape at the form's area.
    """
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P"
                                        and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(white, rgba)
    image = image.convert("RGB")
    size = init_size(*image.size, width, height)
    if image.size != size:
        image = image.resize(size, Image.LANCZOS)
    return image


def generate_zimage(prompt, negative, seed, randomize, steps, cfg,
                    resolution, multiplier, sampler, model, upscale,
                    batch_count, save_preset, preset_name, image, denoise,
                    *lora_slots):
    """Z-Image tab: batch_count jobs on sequential seeds, optionally upscaled.

    With a start `image`, each job re-samples it at `denoise` instead of
    starting from noise — image to image, on the same model.
    """
    entry, error = _check_model(ZIMAGE_T2I, model)
    if error:
        yield [], error, 0
        return
    missing = zimage_missing(upscale)
    if missing:
        yield [], ("❌ The Ember Lite weights are not downloaded yet (missing: "
                   "%s) — restart the app so the download step can fetch "
                   "them." % ", ".join(missing)), 0
        return
    notice = ""
    if upscale or image is not None:
        # ComfyUI first: a server that is down or restarting answers "not
        # registered" for every node, which would blame the pack for it,
        # and refuses the start image's upload.
        alive, note = comfy_ensure_alive()
        if not alive:
            yield [], note, 0
            return
        if note:
            notice += note + "\n"
        if upscale and not node_registered(UPSCALE_NODE):
            yield [], (notice + "❌ Upscale 1.5x needs the %s node, which "
                       "ComfyUI has not loaded — restart the app so setup "
                       "can install %s, or untick Upscale."
                       % (UPSCALE_NODE, ZIMAGE_UPSCALE_NODES[0])), 0
            return
    notice += _save_preset(presets.TAB_ZIMAGE, save_preset, preset_name,
                           _zimage_settings(seed, randomize, steps, cfg,
                                            resolution, multiplier, sampler,
                                            model, upscale, batch_count,
                                            denoise, lora_slots))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = output_size(
        *ZIMAGE_RESOLUTIONS.get(resolution,
                                ZIMAGE_RESOLUTIONS[ZIMAGE_DEFAULT_RESOLUTION]),
        multiplier)
    init_name = ""
    if image is not None:
        prepared = prepare_init(image, width, height)
        width, height = prepared.size
        try:
            init_name = client.upload_image(
                _png_bytes(prepared),
                f"zimage_{uuid.uuid4().hex[:8]}_start.png")
        except Exception as exc:
            yield [], (notice + f"❌ Uploading the start image to ComfyUI "
                       f"failed: {exc}"), base_seed
            return
    loras, skipped = _resolve_lora_slots(ZIMAGE_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "loras": loras,
        "unet_file": entry.file, "upscale": bool(upscale),
        "init_image": init_name, "denoise": float(denoise),
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_zimage_workflow,
                                    prefix="EmberLite"):
        yield images, notice + status, base_seed
