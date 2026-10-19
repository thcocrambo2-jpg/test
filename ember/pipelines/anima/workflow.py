"""Anima text-to-image workflow builder (ComfyUI API format).

The model card's example workflow (the graph embedded in example.png on
circlestone-labs/Anima). Every node is core ComfyUI: UNETLoader on the
catalogue model, CLIPLoader on qwen_3_06b_base, VAELoader on the Qwen-Image
VAE, a LoraLoader chain, CLIPTextEncode for the prompt, ConditioningZeroOut
for the negative, EmptyLatentImage, KSampler, VAEDecode and SaveImage.

What differs from the example, on purpose:

  * the example encodes its negative; here an empty negative is the
    positive's ConditioningZeroOut, as on Qwen 2.1 and Z-Image, and one
    with text is encoded. BAS:NSFW runs at CFG 1, where a negative does
    nothing either way;
  * PreviewImage is SaveImage, so the picture reaches the gallery;
  * the LoRA chain, which the example does not have: LoraLoader, model and
    CLIP alike, because Anima LoRAs trained with kohya carry text-encoder
    weights too.

Which diffusion model and which LoRAs a graph loads is not decided here:
the builder takes file names, and pipelines/common.py turns the tab's
catalogue ids into those names.
"""

from ember.comfy.server import GPU_COUNT
from ember.pipelines.anima.constants import (
    ANIMA_CLIP_TYPE,
    ANIMA_HF_FILES,
    ANIMA_TEXT_ENCODER,
)
from ember.pipelines.krea2.constants import VAE_FILE
from ember.settings import MODELS_DIR


def anima_missing() -> list[str]:
    """The fixed pipeline files not on disk yet, as paths under MODELS_DIR.

    The text encoder and the VAE it shares with the Krea tabs. The
    diffusion model is the catalogue's, checked by _check_model.
    """
    return [relpath for relpath in (*ANIMA_HF_FILES, f"vae/{VAE_FILE}")
            if not (MODELS_DIR / relpath).exists()]


def build_anima_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 8,
    cfg: float = 1.0,
    width: int = 1024,
    height: int = 1024,
    sampler: str = "euler",
    scheduler: str = "simple",
    loras=(),
    unet_file: str,
    filename_prefix: str = "Anima",
) -> dict:
    """Build an Anima text-to-image workflow in ComfyUI API format.

    `loras` is a sequence of (filename, strength) pairs, each strength
    driving the model and the CLIP alike.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": ANIMA_TEXT_ENCODER,
                       "type": ANIMA_CLIP_TYPE, "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VAE_FILE},
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
