"""The 🧩 Qwen 2.1 Reference and 🌄 Qwen 2.1 generators."""

import random
import uuid

from PIL import Image, ImageOps

from ember.comfy.client import client
from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.comfy.server import node_registered
from ember.generation.handlers import (
    QWEN21_EDIT,
    QWEN21_T2I,
    _check_model,
    _save_preset,
)
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _png_bytes, _run_jobs
from ember.licensing import presets
from ember.pipelines.qwen21.constants import (
    QWEN21_CACHE_NODE,
    QWEN21_COMFYUI_MIN,
    QWEN21_DEFAULT_OUTPUT_SIZE,
    QWEN21_ENCODE_NODE,
    QWEN21_MAX_REFERENCE_EDGE,
    QWEN21_MAX_REFERENCES,
    QWEN21_OUTPUT_SIZES,
    QWEN21_T2I_DEFAULT_SIZE,
    QWEN21_T2I_SIZES,
)
from ember.pipelines.qwen21.workflow import (
    build_qwen21_ref_workflow,
    build_qwen21_t2i_workflow,
    qwen21_missing,
    reference_size,
)


def _qwen21_settings(output_size, model, steps, cfg, sampler, scheduler,
                     reference_detail, seed, randomize, batch_count,
                     lora_slots) -> dict:
    """The Qwen 2.1 Reference tab's controls as a preset stores them.

    The UI values, ids and all, in the flat shape of _zimage_settings.
    Never the reference images: a preset is settings, and pictures stay
    with the form, as on every other tab. scripts/golden.py checks this
    against tabschema's settings().
    """
    return {
        "output_size": output_size,
        "model": model,
        "steps": int(steps),
        "cfg": float(cfg),
        "sampler": sampler,
        "scheduler": scheduler,
        "reference_detail": int(reference_detail),
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    }


def prepare_reference(image: Image.Image) -> Image.Image:
    """One reference, the way the template's upload node hands it over.

    Turned upright from its EXIF orientation, transparency composited onto
    white (which is what the encoder's vision tower sees anyway), and the
    longest edge capped at QWEN21_MAX_REFERENCE_EDGE with LANCZOS — the
    node's `max_reference_edge`. Never enlarged.
    """
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P"
                                        and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(white, rgba)
    image = image.convert("RGB")
    longest = max(image.size)
    if longest > QWEN21_MAX_REFERENCE_EDGE:
        scale = QWEN21_MAX_REFERENCE_EDGE / longest
        image = image.resize((max(1, round(image.width * scale)),
                              max(1, round(image.height * scale))),
                             Image.LANCZOS)
    return image


def generate_qwen21_ref(references, prompt, negative, output_size, model,
                        steps, cfg, sampler, scheduler, reference_detail,
                        seed, randomize, batch_count, save_preset,
                        preset_name, *lora_slots):
    """Qwen 2.1 Reference tab: batch_count jobs on sequential seeds.

    `references` is the list of pictures in the order the form numbers
    them, so "image 1" in the prompt is references[0].
    """
    references = [image for image in (references or []) if image is not None]
    if not references:
        yield [], "❌ Add at least one reference image.", 0
        return
    if len(references) > QWEN21_MAX_REFERENCES:
        yield [], ("❌ Up to %d reference images — remove %d and try again."
                   % (QWEN21_MAX_REFERENCES,
                      len(references) - QWEN21_MAX_REFERENCES)), 0
        return
    if not str(prompt or "").strip():
        yield [], ("❌ Write a prompt, naming the references by number "
                   "(e.g. “the woman from image 1 in the jacket from "
                   "image 2”)."), 0
        return
    entry, error = _check_model(QWEN21_EDIT, model)
    if error:
        yield [], error, 0
        return
    missing = qwen21_missing()
    if missing:
        yield [], ("❌ The Qwen 2.1 weights are not downloaded yet (missing: "
                   "%s) — restart the app so the download step can fetch "
                   "them." % ", ".join(missing)), 0
        return

    # ComfyUI first: a server that is down or restarting answers "not
    # registered" for every node, which would blame the version for it.
    alive, notice = comfy_ensure_alive()
    if not alive:
        yield [], notice, 0
        return
    notice = notice + "\n" if notice else ""
    absent = [node for node in (QWEN21_ENCODE_NODE, QWEN21_CACHE_NODE)
              if not node_registered(node)]
    if absent:
        yield [], (notice + "❌ This ComfyUI has no %s node — Qwen Image 2.1 "
                   "needs ComfyUI %s or later. Restart the app so setup can "
                   "move ComfyUI to the pinned release."
                   % (" or ".join(absent), QWEN21_COMFYUI_MIN)), 0
        return

    notice += _save_preset(presets.TAB_QWEN21, save_preset, preset_name,
                           _qwen21_settings(output_size, model, steps, cfg,
                                            sampler, scheduler,
                                            reference_detail, seed,
                                            randomize, batch_count,
                                            lora_slots))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)

    prepared = [prepare_reference(image) for image in references]
    preset = QWEN21_OUTPUT_SIZES.get(
        output_size, QWEN21_OUTPUT_SIZES[QWEN21_DEFAULT_OUTPUT_SIZE])
    width, height = preset or reference_size(*prepared[0].size)

    tag = uuid.uuid4().hex[:8]
    try:
        names = [client.upload_image(_png_bytes(image),
                                     f"qwen21_{tag}_{number}.png")
                 for number, image in enumerate(prepared, start=1)]
    except Exception as exc:
        yield [], (notice + f"❌ Uploading the references to ComfyUI failed: "
                   f"{exc}"), base_seed
        return

    loras, skipped = _resolve_lora_slots(QWEN21_EDIT, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "scheduler": scheduler,
        "reference_detail": int(reference_detail),
        "reference_names": names, "loras": loras,
        "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_qwen21_ref_workflow,
                                    prefix="Qwen21Ref"):
        yield images, notice + status, base_seed


def _qwen21_t2i_settings(output_size, model, steps, cfg, sampler, scheduler,
                         seed, randomize, batch_count, lora_slots) -> dict:
    """The Qwen 2.1 tab's controls as a preset stores them.

    The Reference tab's shape without `reference_detail`. scripts/golden.py
    checks this against tabschema's settings().
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


def generate_qwen21_t2i(prompt, negative, output_size, model, steps, cfg,
                        sampler, scheduler, seed, randomize, batch_count,
                        save_preset, preset_name, *lora_slots):
    """Qwen 2.1 tab: batch_count text-to-image jobs on sequential seeds."""
    if not str(prompt or "").strip():
        yield [], "❌ Write a prompt.", 0
        return
    entry, error = _check_model(QWEN21_T2I, model)
    if error:
        yield [], error, 0
        return
    missing = qwen21_missing()
    if missing:
        yield [], ("❌ The Qwen 2.1 weights are not downloaded yet (missing: "
                   "%s) — restart the app so the download step can fetch "
                   "them." % ", ".join(missing)), 0
        return

    # The graph is all long-standing nodes, but the model and its encoder
    # load only on the ComfyUI release that added the Qwen 2.1 nodes, so
    # their encode node stands for the version. ComfyUI first, as above.
    alive, notice = comfy_ensure_alive()
    if not alive:
        yield [], notice, 0
        return
    notice = notice + "\n" if notice else ""
    if not node_registered(QWEN21_ENCODE_NODE):
        yield [], (notice + "❌ This ComfyUI cannot load Qwen Image 2.1 — it "
                   "needs ComfyUI %s or later. Restart the app so setup can "
                   "move ComfyUI to the pinned release."
                   % QWEN21_COMFYUI_MIN), 0
        return

    notice += _save_preset(presets.TAB_QWEN21_T2I, save_preset, preset_name,
                           _qwen21_t2i_settings(output_size, model, steps,
                                                cfg, sampler, scheduler,
                                                seed, randomize, batch_count,
                                                lora_slots))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = QWEN21_T2I_SIZES.get(
        output_size, QWEN21_T2I_SIZES[QWEN21_T2I_DEFAULT_SIZE])
    loras, skipped = _resolve_lora_slots(QWEN21_T2I, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width,
        "height": height, "sampler": sampler, "scheduler": scheduler,
        "loras": loras, "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs, builder=build_qwen21_t2i_workflow,
                                    prefix="Qwen21"):
        yield images, notice + status, base_seed
