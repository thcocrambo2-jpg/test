"""The Krea2 Edit V2 generator."""

import random
import uuid

from PIL import Image

from ember.comfy.client import client
from ember.generation.handlers import KREA_EDIT_V2, _check_model
from ember.generation.loras import (
    _enabled_lora_ids,
    _resolve_lora_slots,
    _skipped_note,
)
from ember.generation.runner import _png_bytes, _run_jobs
from ember.pipelines.zimage.refine import refine_spec
from ember.pipelines.krea2_edit_v2.constants import (
    EDIT_V2_MODES,
    EDIT_V2_REF_POSITIONS,
)
from ember.pipelines.krea2_edit_v2.workflow import (
    build_v2_edit_workflow,
    fit_size as v2_edit_fit_size,
    status as edit_v2_status,
)


def generate_edit_v2(image, use_image2, image2, prompt, negative, seed,
                     randomize, model, edit_mode, ref_position,
                     ref_position_a,
                     grounding, ref_boost, ref_boost_a, eta,
                     sampler_name,
                     scheduler, steps, cfg, sampler_mode, bongmath,
                     variance_preset, fine_tune_variance,
                     variance_model_type, variance_schedule, cutoff_step,
                     total_steps, cutoff_strength, shift_strength,
                     batch_count, refine, refine_denoise, refine_prompt,
                     *lora_slots):
    """Krea 2 Edit V2 tab: the Krea2 V2 Edit tab on the V2 edit LoRAs.

    Same contract as generate_v2_edit, plus the Edit LoRA choice and where
    each reference sits. There is no geometry control: V2 runs the "fit"
    geometry, and the legacy crop was only ever for v1/v1.1 weights.
    """
    if image is None:
        yield [], "❌ Upload an image first.", 0
        return
    if use_image2 and image2 is None:
        yield [], ("❌ Second reference is enabled but empty — upload it, "
                   "or switch the toggle off."), 0
        return
    if edit_mode not in EDIT_V2_MODES:
        yield [], f"❌ Unknown edit LoRA choice “{edit_mode}”.", 0
        return
    for position in (ref_position, ref_position_a):
        if position not in EDIT_V2_REF_POSITIONS:
            yield [], f"❌ Unknown reference placement “{position}”.", 0
            return
    ready, message = edit_v2_status(KREA_EDIT_V2,
                                    _enabled_lora_ids(lora_slots),
                                    mode=edit_mode)
    if not ready:
        yield [], message, 0
        return
    entry, error = _check_model(KREA_EDIT_V2, model)
    if error:
        yield [], error, 0
        return
    if not str(prompt or "").strip():
        yield [], "❌ Describe the change (e.g. “she sits cross-legged on "\
                  "the bed”).", 0
        return
    refiner, error = refine_spec(refine, refine_denoise, refine_prompt,
                                 prompt, edit=True)
    if error:
        yield [], error, 0
        return
    image = image.convert("RGB")
    width, height = v2_edit_fit_size(*image.size)
    if (width, height) != image.size:
        image = image.resize((width, height), Image.LANCZOS)
    if use_image2:
        image2 = image2.convert("RGB")
        b_size = v2_edit_fit_size(*image2.size)
        if b_size != image2.size:
            image2 = image2.resize(b_size, Image.LANCZOS)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image),
                                         f"editv2_{tag}.png")
        image2_name = None
        if use_image2:
            image2_name = client.upload_image(_png_bytes(image2),
                                              f"editv2_{tag}_b.png")
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
    loras, skipped = _resolve_lora_slots(KREA_EDIT_V2, lora_slots)
    note = _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "width": width, "height": height, "image_name": image_name,
        "image2_name": image2_name,
        "loras": loras,
        "model": entry,
        "edit_loras": EDIT_V2_MODES[edit_mode],
        "ref_position": ref_position,
        "ref_position_a": ref_position_a,
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "sampler_settings": sampler_settings,
        "variance_settings": variance_settings,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_v2_edit_workflow,
                                    prefix="Krea2EditV2", refine=refiner):
        yield images, note + status, base_seed
