"""The ⚡ Z-Image generator."""

import random

from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.comfy.server import node_registered
from ember.generation.handlers import ZIMAGE_T2I, _check_model, _save_preset
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _run_jobs
from ember.licensing import presets
from ember.pipelines.zimage.constants import (
    ZIMAGE_DEFAULT_RESOLUTION,
    ZIMAGE_RESOLUTIONS,
    ZIMAGE_UPSCALE_NODES,
)
from ember.pipelines.zimage.workflow import (
    UPSCALE_NODE,
    build_zimage_workflow,
    output_size,
    zimage_missing,
)


def _zimage_settings(seed, randomize, steps, cfg, resolution, multiplier,
                     sampler, model, upscale, batch_count, lora_slots) -> dict:
    """The Z-Image tab's controls as a preset stores them.

    The UI values, ids and all — the shape of _krea_settings, plus the
    multiplier and the upscale tick. Mirrors the generate_zimage signature;
    scripts/golden.py checks it against tabschema's settings().
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
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def generate_zimage(prompt, negative, seed, randomize, steps, cfg,
                    resolution, multiplier, sampler, model, upscale,
                    batch_count, save_preset, preset_name, *lora_slots):
    """Z-Image tab: batch_count jobs on sequential seeds, optionally upscaled."""
    entry, error = _check_model(ZIMAGE_T2I, model)
    if error:
        yield [], error, 0
        return
    missing = zimage_missing(upscale)
    if missing:
        yield [], ("❌ The Z-Image weights are not downloaded yet (missing: "
                   "%s) — restart the app so the download step can fetch "
                   "them." % ", ".join(missing)), 0
        return
    notice = ""
    if upscale:
        # ComfyUI first: a server that is down or restarting answers "not
        # registered" for every node, which would blame the pack for it.
        alive, note = comfy_ensure_alive()
        if not alive:
            yield [], note, 0
            return
        if note:
            notice += note + "\n"
        if not node_registered(UPSCALE_NODE):
            yield [], (notice + "❌ Upscale 1.5x needs the %s node, which "
                       "ComfyUI has not loaded — restart the app so setup "
                       "can install %s, or untick Upscale."
                       % (UPSCALE_NODE, ZIMAGE_UPSCALE_NODES[0])), 0
            return
    notice += _save_preset(presets.TAB_ZIMAGE, save_preset, preset_name,
                           _zimage_settings(seed, randomize, steps, cfg,
                                            resolution, multiplier, sampler,
                                            model, upscale, batch_count,
                                            lora_slots))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = output_size(
        *ZIMAGE_RESOLUTIONS.get(resolution,
                                ZIMAGE_RESOLUTIONS[ZIMAGE_DEFAULT_RESOLUTION]),
        multiplier)
    loras, skipped = _resolve_lora_slots(ZIMAGE_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "loras": loras,
        "unet_file": entry.file, "upscale": bool(upscale),
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_zimage_workflow,
                                    prefix="ZImage"):
        yield images, notice + status, base_seed
