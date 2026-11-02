"""The 🎥 MiniMax I2V, 🎞️ MiniMax T2V and 🖼️ MiniMax R2V generators."""

import random
import uuid

from PIL import Image, ImageOps

from ember.comfy.client import client
from ember.generation.handlers import (
    MINIMAX_I2V,
    MINIMAX_R2V,
    MINIMAX_T2V,
    _save_preset,
)
from ember.generation.loras import (
    _resolve_lora_slots,
    _skipped_note,
    stored_lora,
)
from ember.generation.runner import _png_bytes, _run_tag, _run_wan_jobs
from ember.licensing import presets
from ember.pipelines.common import model_file_available, resolve_model
from ember.pipelines.minimax.constants import (
    MINIMAX_FPS,
    MINIMAX_R2V_DEFAULT_REF_SIZE,
    MINIMAX_R2V_MAX_REFERENCES,
    MINIMAX_R2V_REF_SHORT_EDGE,
    MINIMAX_R2V_REF_SIZES,
    MINIMAX_SAMPLING_DEFAULTS,
)
from ember.pipelines.minimax.workflow import (
    aspect_size as minimax_aspect_size,
    build_minimax_video_workflow,
    crop_to_canvas as minimax_crop_to_canvas,
    frames_for as minimax_frames,
    matches_image as minimax_matches_image,
    minimax_missing,
    minimax_models_available,
    model_defaults as minimax_model_defaults,
    resolve_size as minimax_resolve_size,
)


def _minimax_model_info_text(entry) -> str:
    """The line under a MiniMax tab's Model dropdown: its recipe."""
    d = minimax_model_defaults(entry)
    info = f"defaults: {d['steps']} steps, {d['sampler']} / {d['scheduler']}"
    if d["turbo_strength"] > 0:
        info += f" · turbo LoRA {d['turbo_strength']:g}"
    elif entry.variant == "base":
        info += " · no turbo LoRA"
    else:
        info += " · turbo merged in, so the turbo LoRA is off"
    if (d["shift_video"], d["shift_audio"]) != (
            MINIMAX_SAMPLING_DEFAULTS["shift_video"],
            MINIMAX_SAMPLING_DEFAULTS["shift_audio"]):
        info += f" · shift {d['shift_video']:g} / {d['shift_audio']:g}"
    if not model_file_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    return info


def _minimax_model(feature, model_id, turbo=True):
    """(model record, why it cannot run or None) for the Model value.

    resolve_model falls back to the feature's first model for an id it
    does not list, so there is always a record here: the catalogue's, or
    the built-in one when the catalogue lists none (catalog.py). `turbo`
    False skips the turbo LoRA, which the reference tab never loads.
    """
    model = resolve_model(feature, model_id)
    if model is None:                       # cannot happen: there is a built-in
        return None, ("❌ No MiniMax model is configured — restart the app "
                      "once the licence server can be reached.")
    if minimax_models_available(model.file, turbo):
        return model, None
    return model, ("❌ The MiniMax H3 weights for “%s” are not downloaded yet "
                   "(missing: %s) — restart the app so the download step can "
                   "fetch them."
                   % (model.name,
                      ", ".join(minimax_missing(model.file, turbo))))


def _minimax_settings(model, seed, randomize, steps, resolution, seconds,
                      sampler, scheduler, shift_video, shift_audio,
                      turbo_strength, batch_count, lora_slots,
                      aspect=None, ref_size=None) -> dict:
    """The MiniMax tabs' controls as a preset stores them.

    The UI values, model and LoRA ids and all — the same shape as
    _krea_settings, plus the clip length. `aspect` only on the text and
    reference tabs; the image tab skips it when it applies a preset. The
    reference tab, which keeps a list of its own, has no Turbo LoRA
    control, so it passes None for `turbo_strength` and the key is left
    out, and it adds `ref_size`. Never the pictures. scripts/golden.py
    checks this against tabschema's settings().
    """
    settings = {
        "model": model,
        "steps": int(steps),
        "resolution": resolution,
        "seconds": int(round(float(seconds))),
        "sampler": sampler,
        "scheduler": scheduler,
        "shift_video": float(shift_video),
        "shift_audio": float(shift_audio),
    }
    if turbo_strength is not None:
        settings["turbo_strength"] = float(turbo_strength)
    settings.update({
        "seed": int(seed or 0),
        "randomize": bool(randomize),
        "batch_count": int(batch_count),
        "loras": [[bool(on), stored_lora(name), float(weight)]
                  for on, name, weight
                  in zip(lora_slots[::3], lora_slots[1::3], lora_slots[2::3])],
    })
    if aspect is not None:
        settings["aspect"] = aspect
    if ref_size is not None:
        settings["ref_size"] = ref_size
    return settings


def _minimax_jobs(*, prompt, base_seed, steps, width, height, seconds,
                  sampler, scheduler, shift_video, shift_audio,
                  turbo_strength, batch_count, prefix, loras, unet,
                  image_name=None, reference_names=None,
                  ref_image_size="match") -> list:
    """The per-clip job dicts the MiniMax tabs hand to _run_wan_jobs.

    One function because the tabs differ only in what conditions the clip
    — `image_name`, which the text tab leaves None so the builder leaves
    LoadImage out, or the reference tab's `reference_names` and
    `ref_image_size` — and everything else about a clip is decided the
    same way on all three.
    """
    references = ({"reference_names": reference_names,
                   "ref_image_size": ref_image_size}
                  if reference_names is not None else {})
    return [{
        "prompt": prompt or "", "seed": base_seed + i, "steps": int(steps),
        "width": width, "height": height,
        "length": minimax_frames(seconds), "fps": MINIMAX_FPS,
        "sampler": sampler, "scheduler": scheduler,
        "shift_video": float(shift_video), "shift_audio": float(shift_audio),
        "turbo_strength": float(turbo_strength),
        "image_name": image_name, "loras": loras,
        "unet": unet,
        # Clips land in minimax/, a subfolder with a counter of its own to
        # be knocked backwards — see _run_tag.
        "filename_prefix": f"minimax/{prefix}_{_run_tag()}",
        **references,
    } for i in range(int(batch_count))]


def generate_minimax_video(image, prompt, model, seed, randomize, steps,
                           resolution, seconds, sampler, scheduler,
                           shift_video, shift_audio, turbo_strength,
                           batch_count, save_preset, preset_name,
                           *lora_slots):
    """MiniMax I2V tab: animate an uploaded image into a clip with sound."""
    if image is None:
        yield [], None, "❌ Upload an image first.", 0
        return
    entry, note = _minimax_model(MINIMAX_I2V, model)
    if note:
        yield [], None, note, 0
        return
    notice = _save_preset(presets.TAB_MINIMAX, save_preset, preset_name,
                          _minimax_settings(entry.id, seed, randomize, steps,
                                            resolution, seconds, sampler,
                                            scheduler, shift_video,
                                            shift_audio, turbo_strength,
                                            batch_count, lora_slots))
    image = image.convert("RGB")
    width, height = minimax_resolve_size(*image.size, resolution)
    if minimax_matches_image(resolution):
        # Match image trims to the canvas's shape first: the node stretches
        # the first frame to fit, and a canvas in 32s is rarely the
        # picture's exact shape. Standard and native send it whole.
        image = minimax_crop_to_canvas(image, width, height)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        # The main instance, whatever EMBER_WAN_PARALLEL says — see
        # _run_wan_jobs on why MiniMax never rides the Wan lane.
        image_name = client.upload_image(_png_bytes(image),
                                         f"minimax_{tag}.png")
    except Exception as exc:
        yield [], None, (notice + "❌ Uploading the image to ComfyUI "
                         f"failed: {exc}"), 0
        return
    loras, skipped = _resolve_lora_slots(MINIMAX_I2V, lora_slots)
    notice += _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, scheduler=scheduler,
                         shift_video=shift_video, shift_audio=shift_audio,
                         turbo_strength=turbo_strength,
                         batch_count=batch_count,
                         prefix="MiniMaxI2V", loras=loras, unet=entry.file,
                         image_name=image_name)
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed


def generate_minimax_t2v(prompt, model, aspect, seed, randomize, steps,
                         resolution, seconds, sampler, scheduler,
                         shift_video, shift_audio, turbo_strength,
                         batch_count, save_preset, preset_name,
                         *lora_slots):
    """MiniMax T2V tab: a clip with sound from the prompt alone."""
    if not (prompt or "").strip():
        yield [], None, ("❌ Write a prompt first — there is no image for "
                         "this tab to go on."), 0
        return
    entry, note = _minimax_model(MINIMAX_T2V, model)
    if note:
        yield [], None, note, 0
        return
    notice = _save_preset(presets.TAB_MINIMAX, save_preset, preset_name,
                          _minimax_settings(entry.id, seed, randomize, steps,
                                            resolution, seconds, sampler,
                                            scheduler, shift_video,
                                            shift_audio, turbo_strength,
                                            batch_count, lora_slots,
                                            aspect=aspect))
    width, height = minimax_aspect_size(aspect, resolution)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    loras, skipped = _resolve_lora_slots(MINIMAX_T2V, lora_slots)
    notice += _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, scheduler=scheduler,
                         shift_video=shift_video, shift_audio=shift_audio,
                         turbo_strength=turbo_strength,
                         batch_count=batch_count,
                         prefix="MiniMaxT2V", loras=loras, unet=entry.file)
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed


def prepare_reference(image: Image.Image) -> Image.Image:
    """One reference picture, ready to upload.

    Turned upright from its EXIF orientation, transparency composited onto
    white, and the short edge cut to MINIMAX_R2V_REF_SHORT_EDGE when it is
    longer — the node shrinks it that far itself on "max" and further on
    "match", and never enlarges, so this only saves the upload. The shape
    is kept: the node encodes each reference at its own aspect, not the
    clip's.
    """
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P"
                                        and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(white, rgba)
    image = image.convert("RGB")
    short = min(image.size)
    if short > MINIMAX_R2V_REF_SHORT_EDGE:
        scale = MINIMAX_R2V_REF_SHORT_EDGE / short
        image = image.resize((max(1, round(image.width * scale)),
                              max(1, round(image.height * scale))),
                             Image.LANCZOS)
    return image


def generate_minimax_r2v(references, prompt, model, aspect, ref_size, seed,
                         randomize, steps, resolution, seconds, sampler,
                         scheduler, shift_video, shift_audio, batch_count,
                         save_preset, preset_name, *lora_slots):
    """MiniMax R2V tab: a clip with sound built from reference pictures.

    `references` is the list in the order the form numbers them, so
    <Picture 1> in the prompt is references[0]. The clip has no first
    frame: its shape is the Aspect ratio's, as on the text tab.
    """
    references = [image for image in (references or []) if image is not None]
    if not references:
        yield [], None, "❌ Add at least one reference picture.", 0
        return
    if len(references) > MINIMAX_R2V_MAX_REFERENCES:
        yield [], None, ("❌ Up to %d reference pictures — remove %d and try "
                         "again." % (MINIMAX_R2V_MAX_REFERENCES,
                                     len(references)
                                     - MINIMAX_R2V_MAX_REFERENCES)), 0
        return
    if not (prompt or "").strip():
        yield [], None, ("❌ Write a prompt, naming the pictures as "
                         "<Picture 1>, <Picture 2>… — say what to keep "
                         "from each."), 0
        return
    entry, note = _minimax_model(MINIMAX_R2V, model, turbo=False)
    if note:
        yield [], None, note, 0
        return
    notice = _save_preset(presets.TAB_MINIMAX_R2V, save_preset, preset_name,
                          _minimax_settings(entry.id, seed, randomize, steps,
                                            resolution, seconds, sampler,
                                            scheduler, shift_video,
                                            shift_audio, None, batch_count,
                                            lora_slots, aspect=aspect,
                                            ref_size=ref_size))
    width, height = minimax_aspect_size(aspect, resolution)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        # The main instance, like the other two MiniMax tabs.
        names = [client.upload_image(_png_bytes(prepare_reference(image)),
                                     f"minimax_{tag}_{number}.png")
                 for number, image in enumerate(references, start=1)]
    except Exception as exc:
        yield [], None, (notice + "❌ Uploading the references to ComfyUI "
                         f"failed: {exc}"), 0
        return
    loras, skipped = _resolve_lora_slots(MINIMAX_R2V, lora_slots)
    notice += _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, scheduler=scheduler,
                         shift_video=shift_video, shift_audio=shift_audio,
                         turbo_strength=0.0, batch_count=batch_count,
                         prefix="MiniMaxR2V", loras=loras, unet=entry.file,
                         reference_names=names,
                         ref_image_size=MINIMAX_R2V_REF_SIZES.get(
                             ref_size,
                             MINIMAX_R2V_REF_SIZES[
                                 MINIMAX_R2V_DEFAULT_REF_SIZE]))
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed
