"""Qwen Image 2.1 workflow builders: reference (a prompt and 1–10 images)
and text to image.

The reference graph is transcribed from Hearmeman24's comfyui-qwen-template
workflow qwen_image_2.1_reference_workflow.json. Every node is core ComfyUI:
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

The text-to-image graph is the same template's qwen_image_2.1_workflow.json,
the ComfyUI form of the text-to-image half of Qwen's Qwen-Image-2.1 workflow
Space: the same loaders and LoRA chain, CLIPTextEncode (which this encoder
wraps in Qwen 2.1's own text-to-image template), ConditioningZeroOut for the
negative, EmptyLatentImage, KSampler, VAEDecode and SaveImage. It has no
QwenImage21Cache — the template has none, and the cache is for the
references, which this graph does not have. The Space's optional prompt
rewriter (Qwen-Image-2.1-PE-T2I, a 9B model of its own) is left out, as are
the template's Power Lora Loader and resolution note, for the same reasons
as above. A negative prompt, when there is one, is encoded instead of
zeroed, so the CFG slider has something to push against.

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


def _loaders(wf: dict, unet_file: str, loras) -> tuple:
    """Add the loaders and the LoRA chain both graphs open with to `wf`.

    UNETLoader, CLIPLoader and VAELoader, the encoder and VAE moved to
    gpu:1 when there are two GPUs, then one LoraLoader per (filename,
    strength) pair, each strength driving the model and the CLIP alike, as
    the template's loader did. Returns the model, CLIP and VAE refs at the
    end of all that.
    """
    wf["unet"] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
    }
    wf["clip"] = {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": QWEN21_TEXT_ENCODER,
                   "type": QWEN21_CLIP_TYPE, "device": "default"},
    }
    wf["vae"] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": QWEN21_VAE},
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
    return model_ref, clip_ref, vae_ref


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
    wf = {}
    model_ref, clip_ref, vae_ref = _loaders(wf, unet_file, loras)

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


def build_qwen21_t2i_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 25,
    cfg: float = 1.0,
    width: int = 2048,
    height: int = 2048,
    sampler: str = "euler",
    scheduler: str = "simple",
    loras=(),
    unet_file: str,
    filename_prefix: str = "Qwen21",
) -> dict:
    """Build a Qwen Image 2.1 text-to-image workflow in ComfyUI API format.

    The template's graph: the prompt through CLIPTextEncode, the negative
    its ConditioningZeroOut — unless `negative` has text, which is encoded
    the same way instead. `loras` is a sequence of (filename, strength)
    pairs, as for the reference graph.
    """
    wf = {}
    model_ref, clip_ref, vae_ref = _loaders(wf, unet_file, loras)

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    if str(negative or "").strip():
        wf["negative"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": clip_ref},
        }
    else:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
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
            "model": model_ref, "positive": ["positive", 0],
            "negative": ["negative", 0], "latent_image": ["latent", 0],
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
