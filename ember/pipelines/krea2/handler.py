"""The 🎨 Krea2 and ✨ Krea2 Edit generators."""

import random
import uuid

from PIL import Image

from ember.comfy.client import client
from ember.generation.handlers import (
    KREA_EDIT,
    KREA_T2I,
    _check_model,
    _save_preset,
    parse_resolution,
)
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _png_bytes, _run_jobs
from ember.licensing import presets
from ember.licensing import prompts
from ember.pipelines.common import edit_lora_available
from ember.pipelines.krea2.workflow import build_edit_workflow


def _krea_settings(seed, randomize, steps, cfg, resolution, sampler, model,
                   batch_count, lora_slots) -> dict:
    """The Krea 2 tab's controls as the prompt library stores them.

    Deliberately the *UI* values, not the resolved job dict: what goes in
    here comes back out into these same controls on another pod, so the
    model and LoRA *ids* are the useful thing to keep and the file names
    they resolve to are not. Mirrors the generate_single signature — when
    that gains a control, this is the other half of the change.

    LoRA rows are [enabled, lora id or None, weight] — the shape every
    Krea tab stores, and the one the licence server checks a preset
    against. An empty slot is None here and "None" in the form; see
    stored_lora.
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
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def generate_single(prompt, negative, seed, randomize, steps, cfg, resolution,
                    sampler, model, batch_count, publish, publish_title,
                    save_preset, preset_name, *lora_slots):
    """First tab: run batch_count jobs on sequential seeds."""
    entry, error = _check_model(KREA_T2I, model)
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
    loras, skipped = _resolve_lora_slots(KREA_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width, "height": height,
        "sampler": sampler, "loras": loras, "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs):
        yield images, notice + status, base_seed


def _fit_edit_size(w: int, h: int, max_pixels: int = 2_000_000) -> tuple:
    """Edit-target size: keep aspect, cap at max_pixels, never upscale, /16.

    The Identity Edit LoRA bleeds/duplicates content above ~2 MP, so the
    cap is by area rather than by the long side.

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
    entry, error = _check_model(KREA_EDIT, model)
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
    loras, skipped = _resolve_lora_slots(KREA_EDIT, lora_slots)
    note = _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "image_name": image_name,
        "image2_name": image2_name,
        "grounding_px": int(grounding), "ref_boost": float(ref_boost),
        "ref_boost_a": float(ref_boost_a),
        "loras": loras,
        "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_edit_workflow,
                                    prefix="Krea2Edit"):
        yield images, note + status, base_seed
