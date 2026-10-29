"""Qwen-Image-Edit-2511 workflow builder: a prompt and 1–3 images.

Transcribed from ComfyUI's own template, image_qwen_image_edit_2511.json in
Comfy-Org/workflow_templates. Every node is core ComfyUI: UNETLoader,
CLIPLoader(type="qwen_image"), VAELoader, ModelSamplingAuraFlow, CFGNorm,
an optional Lightning LoraLoaderModelOnly, the tab's LoRA chain, one
LoadImage per picture, FluxKontextImageScale on image 1, two
TextEncodeQwenImageEditPlus (the prompt and the negative, both reading
every picture), a FluxKontextMultiReferenceLatentMethod on each, VAEEncode
of the scaled image 1 as the starting latent, KSampler, VAEDecode and
SaveImage.

What the template had and this leaves out, on purpose:

  * the subgraph wrapper — wiring only;
  * the three ComfySwitchNode / Primitive pairs behind "Enable 4steps
    LoRA?" — the switch picks between two models, two step counts and two
    CFGs, so the builder makes that choice itself and emits only the
    branch it took;
  * `MarkdownNote` — the template's notes.

Which diffusion model and which LoRAs a graph loads is not decided here:
the builder takes file names, and pipelines/common.py turns the tab's
catalogue ids into those names.
"""

from ember.comfy.server import GPU_COUNT
from ember.pipelines.krea2.constants import VAE_FILE
from ember.pipelines.qwen_edit.constants import (
    QWEN_EDIT_CFG_NORM,
    QWEN_EDIT_CLIP_TYPE,
    QWEN_EDIT_ENCODE_NODE,
    QWEN_EDIT_HF_FILES,
    QWEN_EDIT_LIGHTNING_FILES,
    QWEN_EDIT_LIGHTNING_STRENGTH,
    QWEN_EDIT_REF_METHOD,
    QWEN_EDIT_REF_METHOD_NODE,
    QWEN_EDIT_SHIFT,
    QWEN_EDIT_TEXT_ENCODER,
)
from ember.settings import MODELS_DIR


def qwen_edit_missing(lightning: str | None = None) -> list[str]:
    """The fixed pipeline files a run needs that are not on disk yet.

    The text encoder, the shared VAE, and the Lightning LoRA `lightning`
    names, if any — so a missing 8-step file refuses only the 8-step
    choice. Paths under MODELS_DIR. The diffusion model is the
    catalogue's, checked by _check_model.
    """
    wanted = [*QWEN_EDIT_HF_FILES, f"vae/{VAE_FILE}"]
    if lightning:
        wanted.append(f"loras/{lightning}")
    return [relpath for relpath in wanted
            if not (MODELS_DIR / relpath).exists()]


def build_qwen_edit_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 40,
    cfg: float = 4.0,
    sampler: str = "euler",
    scheduler: str = "simple",
    image_names=(),
    lightning: str | None = None,
    loras=(),
    unet_file: str,
    filename_prefix: str = "QwenEdit2511",
) -> dict:
    """Build a Qwen-Image-Edit-2511 workflow in ComfyUI API format.

    `image_names` are the uploaded pictures, in the user's order: the first
    is wired to `image1` and is the one edited, and so on, so "image 2" in
    the prompt is the second one. `lightning` is a Lightning LoRA file or
    None; with one, `steps` and `cfg` are expected to be its own already.
    `loras` is a sequence of (filename, strength) pairs, model-only, like
    the Lightning LoRA.
    """
    if lightning is not None and lightning not in QWEN_EDIT_LIGHTNING_FILES:
        raise ValueError(f"not a Lightning LoRA: {lightning}")
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": QWEN_EDIT_TEXT_ENCODER,
                       "type": QWEN_EDIT_CLIP_TYPE, "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VAE_FILE},
        },
    }
    clip_ref, vae_ref = ["clip", 0], ["vae", 0]
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

    wf["shift"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": ["unet", 0], "shift": QWEN_EDIT_SHIFT},
    }
    wf["cfg_norm"] = {
        "class_type": "CFGNorm",
        "inputs": {"model": ["shift", 0], "strength": QWEN_EDIT_CFG_NORM},
    }
    model_ref = ["cfg_norm", 0]
    if lightning:
        wf["lightning"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": lightning,
                       "strength_model": QWEN_EDIT_LIGHTNING_STRENGTH,
                       "model": model_ref},
        }
        model_ref = ["lightning", 0]
    for i, (lora_file, strength) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": lora_file,
                       "strength_model": float(strength),
                       "model": model_ref},
        }
        model_ref = [node, 0]

    images = {}
    for number, name in enumerate(image_names, start=1):
        node = f"image{number}"
        wf[node] = {"class_type": "LoadImage", "inputs": {"image": name}}
        images[f"image{number}"] = [node, 0]
    # Image 1 goes in at the template's size, the nearest of Kontext's
    # ~1 MP buckets: both encoders read it from here, and so does the
    # starting latent, which is what sets the output size.
    wf["scale"] = {
        "class_type": "FluxKontextImageScale",
        "inputs": {"image": images["image1"]},
    }
    images["image1"] = ["scale", 0]

    for node, text in (("positive", prompt), ("negative", negative or "")):
        wf[f"encode_{node}"] = {
            "class_type": QWEN_EDIT_ENCODE_NODE,
            "inputs": {"clip": clip_ref, "prompt": text, "vae": vae_ref,
                       **images},
        }
        wf[node] = {
            "class_type": QWEN_EDIT_REF_METHOD_NODE,
            "inputs": {"conditioning": [f"encode_{node}", 0],
                       "reference_latents_method": QWEN_EDIT_REF_METHOD},
        }
    wf["latent"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["scale", 0], "vae": vae_ref},
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
