"""Z-Image Turbo workflow builder (text to image, optional 1.5x upscale).

Transcribed from Hearmeman24's comfyui-qwen-template workflows
Z_Image_Turbo.json and Z_Image_Turbo_Upscale.json. The base graph is core
ComfyUI throughout: UNETLoader, CLIPLoader(type="qwen_image"), VAELoader,
LoraLoaderModelOnly, CLIPTextEncode ×2, EmptyLatentImage, KSampler,
VAEDecode and SaveImage. The upscale pass adds UpscaleModelLoader and
UltimateSDUpscale (the ComfyUI_UltimateSDUpscale pack), and only when the
tab's tick asks for it — off, the graph has no trace of it.

What the template had and this leaves out, on purpose:

  * the two `Text Prompt (JPS)` nodes — text boxes feeding CLIPTextEncode;
  * `PrimitiveInt`, `Float` and `SimpleMath+` (essentials) — width and
    height times the resolution multiplier, computed by the handler;
  * `ModelPassThrough` (KJNodes), the `Reroute`s and the Impact Pack's
    `ToBasicPipe` / `FromBasicPipe_v2` — wiring only;
  * the four bypassed `LoraLoaderModelOnly` placeholders, which are the
    tab's LoRA stack instead;
  * `PreviewImage` — the app saves with its own prefix, as every tab does.

Which diffusion model and which LoRAs a graph loads is not decided here:
the builder takes file names, and pipelines/common.py turns the tab's
catalogue ids into those names.
"""

from ember.comfy.server import GPU_COUNT
from ember.pipelines.common import chain_loras
from ember.pipelines.zimage.constants import (
    ZIMAGE_CLIP_TYPE,
    ZIMAGE_HF_FILES,
    ZIMAGE_SCHEDULER,
    ZIMAGE_TEXT_ENCODER,
    ZIMAGE_UPSCALE_HF_FILE,
    ZIMAGE_UPSCALE_MODEL,
    ZIMAGE_UPSCALE_NODES,
    ZIMAGE_UPSCALE_SETTINGS,
    ZIMAGE_VAE,
)
from ember.settings import MODELS_DIR

# The class the upscale pass needs registered in ComfyUI.
UPSCALE_NODE = ZIMAGE_UPSCALE_NODES[2]


def zimage_missing(upscale: bool = False) -> list[str]:
    """The fixed pipeline files not on disk yet, as paths under MODELS_DIR.

    The text encoder and the VAE always; the upscale model only when the
    run is going to use it, since the graph without the tick never loads it.
    The diffusion model is the catalogue's, checked by _check_model.
    """
    wanted = list(ZIMAGE_HF_FILES)
    if upscale:
        wanted.append(ZIMAGE_UPSCALE_HF_FILE)
    return [relpath for relpath in wanted
            if not (MODELS_DIR / relpath).exists()]


def output_size(width: int, height: int, multiplier: float) -> tuple:
    """(width, height) times the multiplier, rounded as SimpleMath+ rounds.

    The template feeds `a*b` through SimpleMath+, whose INT output is
    Python's round() of the product — so this is the same number, and 1080
    at 1.0 stays 1080.
    """
    scale = float(multiplier)
    return round(width * scale), round(height * scale)


def build_zimage_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 12,
    cfg: float = 1.0,
    width: int,
    height: int,
    sampler: str = "er_sde",
    loras=(),
    unet_file: str,
    upscale: bool = False,
    filename_prefix: str = "ZImage",
) -> dict:
    """Build a Z-Image Turbo workflow in ComfyUI API format.

    `width` / `height` are the size already multiplied out; `loras` is a
    sequence of (filename, strength) pairs, already resolved; `unet_file`
    is the diffusion model's file, resolved from the tab's catalogue model
    id. Both prompts are always encoded, as in the template: at CFG 1 the
    sampler ignores the negative, and above it the text is what counts.

    With `upscale`, the decoded image goes through 4xLSDIR and one
    UltimateSDUpscale pass at 1.5x, re-sampled by the same LoRA-patched
    model on the same conditioning, with tiles the size of the base image
    and the job's seed. Only the upscaled image is saved.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": ZIMAGE_TEXT_ENCODER,
                       "type": ZIMAGE_CLIP_TYPE, "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": ZIMAGE_VAE},
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

    model_ref = chain_loras(wf, model_ref, loras)

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    wf["negative"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": negative, "clip": clip_ref},
    }
    wf["latent"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
    }
    wf["sampler"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": ZIMAGE_SCHEDULER,
            "denoise": 1.0,
            "model": model_ref, "positive": ["positive", 0],
            "negative": ["negative", 0], "latent_image": ["latent", 0],
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    image_ref = ["decode", 0]

    if upscale:
        wf["upscale_model"] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": ZIMAGE_UPSCALE_MODEL},
        }
        wf["upscale"] = {
            "class_type": UPSCALE_NODE,
            "inputs": {
                "image": image_ref, "model": model_ref,
                "positive": ["positive", 0], "negative": ["negative", 0],
                "vae": vae_ref, "upscale_model": ["upscale_model", 0],
                "seed": int(seed),
                "tile_width": int(width), "tile_height": int(height),
                **ZIMAGE_UPSCALE_SETTINGS,
            },
        }
        image_ref = ["upscale", 0]

    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": image_ref},
    }
    return wf
