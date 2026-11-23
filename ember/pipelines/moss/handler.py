"""The 🌿 MOSS generator."""

import random

from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.comfy.server import node_registered
from ember.generation.handlers import MOSS_T2I, _check_model, _save_preset
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _run_jobs
from ember.licensing import presets
from ember.pipelines.moss.constants import (
    MOSS_COMFYUI_MIN,
    MOSS_DEFAULT_SIZE,
    MOSS_DEFAULT_SPEED,
    MOSS_DMD_CFG,
    MOSS_MARKER_NODE,
    MOSS_SIZES,
    MOSS_SPEEDS,
)
from ember.pipelines.moss.workflow import build_moss_workflow, moss_missing
from ember.pipelines.zimage.refine import refine_spec


def _moss_settings(output_size, speed, model, steps, cfg, sampler,
                   scheduler, seed, randomize, batch_count, lora_slots,
                   refine=False, refine_denoise=0.25) -> dict:
    """The MOSS tab's controls as a preset stores them.

    Anima's shape plus `speed` — the UI values, ids and all. Mirrors the
    generate_moss signature; scripts/golden.py checks it against
    tabschema's settings().
    """
    return {
        "output_size": output_size,
        "speed": speed,
        "model": model,
        "steps": int(steps),
        "cfg": float(cfg),
        "sampler": sampler,
        "scheduler": scheduler,
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "refine": {"on": bool(refine), "denoise": float(refine_denoise)},
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def generate_moss(prompt, negative, output_size, speed, model, steps, cfg,
                  sampler, scheduler, seed, randomize, batch_count,
                  save_preset, preset_name, refine, refine_denoise,
                  refine_prompt, *lora_slots):
    """MOSS tab: batch_count text-to-image jobs on sequential seeds.

    Normal runs the form's Steps and CFG with no LoRA; Fast runs the DMD
    LoRA at its own 8 steps and CFG 1, whatever the sliders say.
    """
    if not str(prompt or "").strip():
        yield [], "❌ Write a prompt.", 0
        return
    entry, error = _check_model(MOSS_T2I, model)
    if error:
        yield [], error, 0
        return
    refiner, error = refine_spec(refine, refine_denoise, refine_prompt,
                                 prompt)
    if error:
        yield [], error, 0
        return
    fast_steps = MOSS_SPEEDS.get(speed, MOSS_SPEEDS[MOSS_DEFAULT_SPEED])
    fast = fast_steps is not None
    steps_run, cfg_run = ((fast_steps, MOSS_DMD_CFG) if fast
                          else (int(steps), float(cfg)))
    missing = moss_missing(fast)
    if missing:
        yield [], ("❌ The MOSS weights are not downloaded yet (missing: %s) "
                   "— restart the app so the download step can fetch them."
                   % ", ".join(missing)), 0
        return

    # ComfyUI first: a server that is down or restarting answers "not
    # registered" for every node, which would blame the version for it.
    alive, notice = comfy_ensure_alive()
    if not alive:
        yield [], notice, 0
        return
    notice = notice + "\n" if notice else ""
    if not node_registered(MOSS_MARKER_NODE):
        yield [], (notice + "❌ This ComfyUI has no MOSS-Image support — the "
                   "MOSS tab needs ComfyUI %s or later. Restart the app so "
                   "setup can move ComfyUI to the pinned release."
                   % MOSS_COMFYUI_MIN), 0
        return

    notice += _save_preset(presets.TAB_MOSS, save_preset, preset_name,
                           _moss_settings(output_size, speed, model, steps,
                                          cfg, sampler, scheduler, seed,
                                          randomize, batch_count,
                                          lora_slots, refine,
                                          refine_denoise))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = MOSS_SIZES.get(output_size, MOSS_SIZES[MOSS_DEFAULT_SIZE])
    loras, skipped = _resolve_lora_slots(MOSS_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": steps_run, "cfg": cfg_run, "width": width, "height": height,
        "sampler": sampler, "scheduler": scheduler, "fast": fast,
        "loras": loras, "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_moss_workflow,
                                    prefix="MOSS", refine=refiner):
        yield images, notice + status, base_seed
