"""The 🔶 Krea2 V2 generator, and the V2 model and LoRA row helpers."""

import random

from ember.licensing import catalog
from ember.licensing import presets
from ember.licensing import prompts
from ember.generation.handlers import (
    KREA_V2_T2I,
    _check_model,
    _save_preset,
)
from ember.generation.loras import (
    _enabled_lora_ids,
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _run_jobs
from ember.pipelines.common import (
    model_file_available,
    resolve_model,
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
