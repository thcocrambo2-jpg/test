"""The 🪄 Qwen Edit 2511 generator."""

import random
import uuid

from ember.comfy.client import client
from ember.comfy.server import ensure_alive as comfy_ensure_alive
from ember.comfy.server import node_registered
from ember.generation.handlers import (
    QWEN_EDIT_2511,
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
from ember.pipelines.qwen21.handler import prepare_reference
from ember.pipelines.qwen_edit.constants import (
    QWEN_EDIT_COMFYUI_MIN,
    QWEN_EDIT_DEFAULT_SPEED,
    QWEN_EDIT_ENCODE_NODE,
    QWEN_EDIT_LIGHTNING_CFG,
    QWEN_EDIT_MAX_IMAGES,
    QWEN_EDIT_REF_METHOD_NODE,
    QWEN_EDIT_SPEEDS,
)
from ember.pipelines.qwen_edit.workflow import (
    build_qwen_edit_workflow,
    qwen_edit_missing,
)


def _qwen_edit_settings(speed, model, steps, cfg, sampler, scheduler, seed,
                        randomize, batch_count, lora_slots) -> dict:
    """The Qwen Edit 2511 tab's controls as a preset stores them.

    The UI values, ids and all, in the flat shape of _qwen21_settings.
    Never the images: a preset is settings, and pictures stay with the
    form, as on every other tab. scripts/golden.py checks this against
    tabschema's settings().
    """
    return {
        "speed": speed,
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


def generate_qwen_edit(images, prompt, negative, speed, model, steps, cfg,
                       sampler, scheduler, seed, randomize, batch_count,
                       save_preset, preset_name, *lora_slots):
    """Qwen Edit 2511 tab: batch_count jobs on sequential seeds.

    `images` is the list of pictures in the order the form numbers them:
    images[0] is the one edited, and "image 2" in the prompt is images[1].
    """
    images = [image for image in (images or []) if image is not None]
    if not images:
        yield [], "❌ Add the image to edit.", 0
        return
    if len(images) > QWEN_EDIT_MAX_IMAGES:
        yield [], ("❌ Up to %d images — remove %d and try again."
                   % (QWEN_EDIT_MAX_IMAGES,
                      len(images) - QWEN_EDIT_MAX_IMAGES)), 0
        return
    if not str(prompt or "").strip():
        yield [], ("❌ Write an edit instruction (e.g. “turn her around so "
                   "her back is to the camera, looking over her shoulder”)."
                   ), 0
        return
    entry, error = _check_model(QWEN_EDIT_2511, model)
    if error:
        yield [], error, 0
        return
    recipe = QWEN_EDIT_SPEEDS.get(speed,
                                  QWEN_EDIT_SPEEDS[QWEN_EDIT_DEFAULT_SPEED])
    lightning, steps_run, cfg_run = None, int(steps), float(cfg)
    if recipe:
        lightning, steps_run = recipe
        cfg_run = QWEN_EDIT_LIGHTNING_CFG
    missing = qwen_edit_missing(lightning)
    if missing:
        yield [], ("❌ The Qwen Edit 2511 weights are not downloaded yet "
                   "(missing: %s) — restart the app so the download step can "
                   "fetch them." % ", ".join(missing)), 0
        return

    # ComfyUI first: a server that is down or restarting answers "not
    # registered" for every node, which would blame the version for it.
    alive, notice = comfy_ensure_alive()
    if not alive:
        yield [], notice, 0
        return
    notice = notice + "\n" if notice else ""
    absent = [node for node in (QWEN_EDIT_ENCODE_NODE,
                                QWEN_EDIT_REF_METHOD_NODE)
              if not node_registered(node)]
    if absent:
        yield [], (notice + "❌ This ComfyUI has no %s node — Qwen Edit 2511 "
                   "needs ComfyUI %s or later. Restart the app so setup can "
                   "move ComfyUI to the pinned release."
                   % (" or ".join(absent), QWEN_EDIT_COMFYUI_MIN)), 0
        return

    notice += _save_preset(presets.TAB_QWEN_EDIT, save_preset, preset_name,
                           _qwen_edit_settings(speed, model, steps, cfg,
                                               sampler, scheduler, seed,
                                               randomize, batch_count,
                                               lora_slots))
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)

    tag = uuid.uuid4().hex[:8]
    try:
        names = [client.upload_image(_png_bytes(prepare_reference(image)),
                                     f"qwenedit_{tag}_{number}.png")
                 for number, image in enumerate(images, start=1)]
    except Exception as exc:
        yield [], (notice + f"❌ Uploading the images to ComfyUI failed: "
                   f"{exc}"), base_seed
        return

    loras, skipped = _resolve_lora_slots(QWEN_EDIT_2511, lora_slots)
    notice += _skipped_note(skipped)
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": steps_run, "cfg": cfg_run, "sampler": sampler,
        "scheduler": scheduler, "image_names": names,
        "lightning": lightning, "loras": loras, "unet_file": entry.file,
    } for i in range(int(batch_count))]
    for result, status in _run_jobs(jobs, builder=build_qwen_edit_workflow,
                                    prefix="QwenEdit2511"):
        yield result, notice + status, base_seed
