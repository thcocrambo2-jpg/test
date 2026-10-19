"""The 🎨 Anima generator."""

import random

from ember.generation.handlers import ANIMA_T2I, _check_model, _save_preset
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _run_jobs
from ember.licensing import presets
from ember.pipelines.anima.constants import ANIMA_DEFAULT_SIZE, ANIMA_SIZES
from ember.pipelines.anima.workflow import anima_missing, build_anima_workflow


def _anima_settings(output_size, model, steps, cfg, sampler, scheduler,
                    seed, randomize, batch_count, lora_slots) -> dict:
    """The Anima tab's controls as a preset stores them.

    The UI values, ids and all — Qwen 2.1's text-to-image shape.
    Mirrors the generate_anima signature; scripts/golden.py checks it
    against tabschema's settings().
    """
    return {
        "output_size": output_size,
        "model": model,
        "steps": int(steps),
        "cfg": float(cfg),
        "sampler": sampler,
        "scheduler": scheduler,
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def generate_anima(prompt, negative, output_size, model, steps, cfg,
                   sampler, scheduler, seed, randomize, batch_count,
                   save_preset, preset_name, *lora_slots):
    """Anima tab: batch_count text-to-image jobs on sequential seeds.

    Every node is core and long-standing, and the ComfyUI pin loads the
    model, so unlike Qwen 2.1 there is no node to ask for first.
    """
    if not str(prompt or "").strip():
        yield [], "❌ Write a prompt.", 0
        return
    entry, error = _check_model(ANIMA_T2I, model)
    if error:
        yield [], error, 0
        return
    missing = anima_missing()
    if missing:
        yield [], ("❌ The Anima weights are not downloaded yet (missing: "
                   "%s) — restart the app so the download step can fetch "
                   "them." % ", ".join(missing)), 0
        return

    notice = _save_preset(presets.TAB_ANIMA, save_preset, preset_name,
                          _anima_settings(output_size, model, steps, cfg,
                                          sampler, scheduler, seed,
                                          randomize, batch_count,
                                          lora_slots))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = ANIMA_SIZES.get(output_size,
                                    ANIMA_SIZES[ANIMA_DEFAULT_SIZE])
    loras, skipped = _resolve_lora_slots(ANIMA_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "scheduler": scheduler,
        "loras": loras, "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_anima_workflow,
                                    prefix="Anima"):
        yield images, notice + status, base_seed
