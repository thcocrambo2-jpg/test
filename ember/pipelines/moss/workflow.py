"""MOSS-Image text-to-image workflow builder (ComfyUI API format).

Every node is core ComfyUI: UNETLoader on the catalogue model, CLIPLoader
(type moss_image) on the Qwen3.5-9B encoder, VAELoader on the FLUX.2 VAE,
an optional LoraLoaderModelOnly for the 8-step DMD LoRA, the tab's
model-only LoRA chain, CLIPTextEncode for the prompt and the negative,
EmptyFlux2LatentImage (MOSS decodes with the FLUX.2 VAE, so it takes that
latent), KSampler, VAEDecode and SaveImage. The model's shift of 5 is its
own sampling setting in ComfyUI, so no node sets it.

What differs from the official pipeline, on purpose:

  * no skip-layer guidance. The reference pipeline's "uncond skip" on
    layer 22 is not what ComfyUI's SkipLayerGuidanceDiT does, so it is
    left out rather than approximated;
  * at CFG 1 (Fast, or Normal with CFG set to 1) the negative is the
    positive's ConditioningZeroOut: it would do nothing, and encoding it
    is a second pass through a 9B encoder. Otherwise it is encoded, even
    when empty, as the reference pipeline does.

Which diffusion model and which LoRAs a graph loads is not decided here:
the builder takes file names, and pipelines/common.py turns the tab's
catalogue ids into those names.
"""

from ember.comfy.server import GPU_COUNT
from ember.pipelines.moss.constants import (
    MOSS_CLIP_TYPE,
    MOSS_DMD_LORA,
    MOSS_DMD_STRENGTH,
    MOSS_HF_FILES,
    MOSS_TEXT_ENCODER,
    MOSS_VAE,
)
from ember.settings import MODELS_DIR


def moss_missing(fast: bool = False) -> list[str]:
    """The fixed pipeline files not on disk yet, as paths under MODELS_DIR.

    The text encoder and the VAE, and the DMD LoRA when `fast` asks for
    it. The diffusion model is the catalogue's, checked by _check_model.
    """
    wanted = [*MOSS_HF_FILES]
    if fast:
        wanted.append(f"loras/{MOSS_DMD_LORA}")
    return [relpath for relpath in wanted
            if not (MODELS_DIR / relpath).exists()]


def build_moss_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 50,
    cfg: float = 3.0,
    width: int = 1024,
    height: int = 1024,
    sampler: str = "euler",
    scheduler: str = "simple",
    fast: bool = False,
    loras=(),
    unet_file: str,
    filename_prefix: str = "MOSS",
) -> dict:
    """Build a MOSS-Image text-to-image workflow in ComfyUI API format.

    `fast` puts the DMD LoRA first in the model chain; the caller passes its
    8 steps and CFG 1. `loras` is a sequence of (filename, strength) pairs,
    model-only.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": MOSS_TEXT_ENCODER,
                       "type": MOSS_CLIP_TYPE, "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": MOSS_VAE},
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

    if fast:
        wf["dmd_lora"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": MOSS_DMD_LORA,
                       "strength_model": MOSS_DMD_STRENGTH,
                       "model": model_ref},
        }
        model_ref = ["dmd_lora", 0]

    for i, (lora_file, strength) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": lora_file,
                       "strength_model": float(strength),
                       "model": model_ref},
        }
        model_ref = [node, 0]

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    if float(cfg) == 1.0:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        }
    else:
        wf["negative"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative or "", "clip": clip_ref},
        }
    wf["latent"] = {
        "class_type": "EmptyFlux2LatentImage",
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
