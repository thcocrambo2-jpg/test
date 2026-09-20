"""The Krea2 V2 Edit generator."""

import random
import uuid

from PIL import Image

from ember.comfy.client import client
from ember.generation.handlers import KREA_V2_EDIT, _check_model
from ember.generation.loras import (
    _enabled_lora_ids,
    _resolve_lora_slots,
    _skipped_note,
)
from ember.generation.runner import _png_bytes, _run_jobs
from ember.pipelines.krea2_v2_edit.workflow import (
    build_v2_edit_workflow,
    fit_size as v2_edit_fit_size,
    status as v2_edit_status,
)


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
    ready, message = v2_edit_status(KREA_V2_EDIT,
                                    _enabled_lora_ids(lora_slots))
    if not ready:
        yield [], message, 0
        return
    entry, error = _check_model(KREA_V2_EDIT, model)
    if error:
        yield [], error, 0
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
    loras, skipped = _resolve_lora_slots(KREA_V2_EDIT, lora_slots)
    note = _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "width": width, "height": height, "image_name": image_name,
        "image2_name": image2_name,
        "loras": loras,
        "model": entry,
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "fit_mode": fit_mode,
        "sampler_settings": sampler_settings,
        "variance_settings": variance_settings,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_v2_edit_workflow,
                                    prefix="Krea2V2Edit"):
        yield images, note + status, base_seed
