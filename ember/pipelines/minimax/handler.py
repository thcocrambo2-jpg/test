"""The 🎥 MiniMax I2V and 🎞️ MiniMax T2V generators."""

import random
import uuid

from ember.comfy.client import client
from ember.generation.handlers import MINIMAX_I2V, MINIMAX_T2V
from ember.generation.loras import _resolve_lora_slots, _skipped_note
from ember.generation.runner import _png_bytes, _run_tag, _run_wan_jobs
from ember.pipelines.minimax.constants import MINIMAX_FPS
from ember.pipelines.minimax.workflow import (
    aspect_size as minimax_aspect_size,
    build_minimax_video_workflow,
    crop_to_canvas as minimax_crop_to_canvas,
    frames_for as minimax_frames,
    matches_image as minimax_matches_image,
    minimax_missing,
    minimax_models_available,
    resolve_size as minimax_resolve_size,
)


def _minimax_note() -> str | None:
    """Why MiniMax cannot run yet, or None when every weight is on disk."""
    if minimax_models_available():
        return None
    return ("❌ The MiniMax H3 weights are not downloaded yet (missing: %s) — "
            "restart the app so the download step can fetch them."
            % ", ".join(minimax_missing()))


def _minimax_jobs(*, prompt, base_seed, steps, width, height, seconds,
                  sampler, batch_count, prefix, loras, image_name=None) -> list:
    """The per-clip job dicts both MiniMax tabs hand to _run_wan_jobs.

    One function because the two tabs differ in exactly one key —
    `image_name`, which the text tab leaves None so the builder leaves
    LoadImage out — and everything else about a clip is decided the same
    way on both.
    """
    return [{
        "prompt": prompt or "", "seed": base_seed + i, "steps": int(steps),
        "width": width, "height": height,
        "length": minimax_frames(seconds), "fps": MINIMAX_FPS,
        "sampler": sampler, "image_name": image_name, "loras": loras,
        # Clips land in minimax/, a subfolder with a counter of its own to
        # be knocked backwards — see _run_tag.
        "filename_prefix": f"minimax/{prefix}_{_run_tag()}",
    } for i in range(int(batch_count))]


def generate_minimax_video(image, prompt, seed, randomize, steps, resolution,
                           seconds, sampler, batch_count, *lora_slots):
    """MiniMax I2V tab: animate an uploaded image into a clip with sound."""
    if image is None:
        yield [], None, "❌ Upload an image first.", 0
        return
    note = _minimax_note()
    if note:
        yield [], None, note, 0
        return
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
        # The main instance, whatever KREA2_WAN_PARALLEL says — see
        # _run_wan_jobs on why MiniMax never rides the Wan lane.
        image_name = client.upload_image(_png_bytes(image),
                                         f"minimax_{tag}.png")
    except Exception as exc:
        yield [], None, f"❌ Uploading the image to ComfyUI failed: {exc}", 0
        return
    loras, skipped = _resolve_lora_slots(MINIMAX_I2V, lora_slots)
    notice = _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, batch_count=batch_count,
                         prefix="MiniMaxI2V", loras=loras,
                         image_name=image_name)
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed


def generate_minimax_t2v(prompt, aspect, seed, randomize, steps, resolution,
                         seconds, sampler, batch_count, *lora_slots):
    """MiniMax T2V tab: a clip with sound from the prompt alone."""
    if not (prompt or "").strip():
        yield [], None, ("❌ Write a prompt first — there is no image for "
                         "this tab to go on."), 0
        return
    note = _minimax_note()
    if note:
        yield [], None, note, 0
        return
    width, height = minimax_aspect_size(aspect, resolution)
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    loras, skipped = _resolve_lora_slots(MINIMAX_T2V, lora_slots)
    notice = _skipped_note(skipped)
    jobs = _minimax_jobs(prompt=prompt, base_seed=base_seed, steps=steps,
                         width=width, height=height, seconds=seconds,
                         sampler=sampler, batch_count=batch_count,
                         prefix="MiniMaxT2V", loras=loras)
    for videos, latest, status in _run_wan_jobs(
            jobs, builder=build_minimax_video_workflow, comfy_client=client):
        yield videos, latest, notice + status, base_seed
