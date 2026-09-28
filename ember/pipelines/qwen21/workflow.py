"""Qwen Image 2.1 reference workflow builder (a prompt and 1–10 images).

Transcribed from Hearmeman24's comfyui-qwen-template workflow
qwen_image_2.1_reference_workflow.json. Every node is core ComfyUI:
UNETLoader, CLIPLoader(type="qwen_image"), VAELoader, a LoraLoader chain,
QwenImage21Cache, one LoadImage per reference, TextEncodeQwenImage21,
EmptyLatentImage, KSampler, VAEDecode and SaveImage. The two Qwen nodes
first ship in ComfyUI v0.37.0.

What the template had and this leaves out, on purpose:

  * `QwenImageReferencePack` (Hearmeman24/ComfyUI-QwenImageRefPack) — an
    upload canvas with per-image crop, rotate and mirror, plus a cap on the
    longest edge. The tab is the upload UI, and the handler caps the edge
    with PIL, so each reference reaches the graph through a core LoadImage;
  * `PrimitiveStringMultiline` — the prompt box;
  * the KJNodes `SetNode` / `GetNode` pairs — wiring only;
  * `Power Lora Loader (rgthree)`, empty in the template, which is the
    tab's LoRA stack instead. It patched model *and* CLIP, so the stack
    here is a LoraLoader chain like Krea2 V2's, and the encoder reads the
    patched CLIP;
  * `MarkdownNote` — the resolution note, which is the tab's Output size
    list.

Which diffusion model and which LoRAs a graph loads is not decided here:
the builder takes file names, and pipelines/common.py turns the tab's
catalogue ids into those names.
"""

import math

from ember.comfy.server import GPU_COUNT
from ember.pipelines.qwen21.constants import (
    QWEN21_CACHE_NODE,
    QWEN21_CACHE_SETTINGS,
    QWEN21_CLIP_TYPE,
    QWEN21_ENCODE_NODE,
    QWEN21_HF_FILES,
    QWEN21_SIZE_MULTIPLE,
    QWEN21_TARGET_PIXELS,
    QWEN21_TEXT_ENCODER,
    QWEN21_VAE,
)
from ember.settings import MODELS_DIR


def qwen21_missing() -> list[str]:
    """The fixed pipeline files not on disk yet, as paths under MODELS_DIR.

    The text encoder and the VAE. The diffusion model is the catalogue's,
    checked by _check_model.
    """
    return [relpath for relpath in QWEN21_HF_FILES
            if not (MODELS_DIR / relpath).exists()]


def reference_size(width: int, height: int) -> tuple:
    """The output size "Same as reference 1" means, for a reference this big.

    Its shape at about QWEN21_TARGET_PIXELS, each side rounded to a
    multiple of QWEN21_SIZE_MULTIPLE — TextEncodeQwenImage21's own resize
    rule, with 2048 where the node has its `resolution`. Half-up rounding
    rather than Python's round(), because the browser computes the same
    number for the hint under the select (tabschema.catalog() sends it
    these two constants) and JavaScript rounds a half up.
    """
    ratio = width / height
    step = QWEN21_SIZE_MULTIPLE

    def side(length: float) -> int:
        return max(step, math.floor(length / step + 0.5) * step)

    return (side(math.sqrt(QWEN21_TARGET_PIXELS * ratio)),
            side(math.sqrt(QWEN21_TARGET_PIXELS / ratio)))


def build_qwen21_ref_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 25,
    cfg: float = 1.0,
    width: int,
    height: int,
    sampler: str = "euler",
    scheduler: str = "simple",
    reference_detail: int = 1024,
    reference_names=(),
    loras=(),
    unet_file: str,
    filename_prefix: str = "Qwen21Ref",
) -> dict:
    """Build a Qwen Image 2.1 reference workflow in ComfyUI API format.

    `reference_names` are the uploaded references, in the user's order: the
    first is wired to `images.image_1`, and so on, so "image 2" in the
    prompt is the second one. Only as many inputs as there are references
    are wired. `width` / `height` are the output size, already resolved;
    `reference_detail` is the encoder's `resolution`. `loras` is a sequence
    of (filename, strength) pairs, each strength driving the model and the
    CLIP alike, as the template's loader did.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": QWEN21_TEXT_ENCODER,
                       "type": QWEN21_CLIP_TYPE, "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": QWEN21_VAE},
        },
    }
    model_ref, clip_ref, vae_ref = ["unet", 0], ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # The same placement every other pipeline makes: the diffusion
        # model alone on gpu:0, the text encoder and VAE on gpu:1.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    for i, (lora_file, strength) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": lora_file,
                       "strength_model": float(strength),
                       "strength_clip": float(strength),
                       "model": model_ref, "clip": clip_ref},
        }
        model_ref, clip_ref = [node, 0], [node, 1]

    wf["cache"] = {
        "class_type": QWEN21_CACHE_NODE,
        "inputs": {"model": model_ref, **QWEN21_CACHE_SETTINGS},
    }

    images = {}
    for number, name in enumerate(reference_names, start=1):
        node = f"ref{number}"
        wf[node] = {"class_type": "LoadImage", "inputs": {"image": name}}
        images[f"images.image_{number}"] = [node, 0]

    wf["encode"] = {
        "class_type": QWEN21_ENCODE_NODE,
        "inputs": {
            "clip": clip_ref, "prompt": prompt,
            "negative_prompt": negative, "vae": vae_ref,
            "resolution": int(reference_detail),
            **images,
        },
    }
    wf["latent"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
    }
    wf["sampler"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": scheduler,
            "denoise": 1.0,
            "model": ["cache", 0], "positive": ["encode", 0],
            "negative": ["encode", 1], "latent_image": ["latent", 0],
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix,
                   "images": ["decode", 0]},
    }
    return wf
