"""Krea 2 workflow builders (text-to-image, inpainting, instruction edit).

The t2i and inpaint workflows use only nodes that ship with current
ComfyUI: UNETLoader / CLIPLoader(type="krea2") / VAELoader,
LoraLoaderModelOnly, ConditioningZeroOut and KSampler — plus the native
multi-GPU placement nodes SelectCLIPDevice / SelectVAEDevice when a
second GPU is present (these pass through unchanged on machines where
gpu:1 does not exist, so the same workflow can never fail for lack of a
GPU). Inpainting adds LoadImage / LoadImageMask / VAEEncode /
SetLatentNoiseMask and a final ImageCompositeMasked — all core nodes as
well. The instruction-edit workflow additionally needs the
ComfyUI-Krea2Edit custom nodes and the Identity Edit LoRA (both fetched
during bootstrap/downloads).
"""

import difflib

from comfy import GPU_COUNT
from config import (
    ABLITERATED_ENCODER_FILE,
    EDIT_LORA_FILE,
    MODELS_DIR,
    TEXT_ENCODER_FILE,
    UNET_FILE,
    VAE_FILE,
    WAN_LIGHTNING_HIGH,
    WAN_LIGHTNING_LOW,
    log,
)

# LoRAs that live in the same folder but do not belong in the Krea style
# stack: the Identity Edit LoRA (added by build_edit_workflow itself) and
# the Wan 2.2 Lightning speed LoRAs (Wan-architecture, video tab only).
_NON_STYLE_LORAS = {EDIT_LORA_FILE, WAN_LIGHTNING_HIGH, WAN_LIGHTNING_LOW}


def list_lora_files() -> list[str]:
    """Style LoRA files currently available to ComfyUI."""
    return sorted(p.name for p in (MODELS_DIR / "loras").glob("*.safetensors")
                  if p.name not in _NON_STYLE_LORAS)


def edit_lora_available() -> bool:
    """True once the Identity Edit LoRA has been downloaded."""
    return (MODELS_DIR / "loras" / EDIT_LORA_FILE).exists()


def active_text_encoder() -> str:
    """Prefer the merged abliterated encoder, fall back to the standard one."""
    if (MODELS_DIR / "text_encoders" / ABLITERATED_ENCODER_FILE).exists():
        return ABLITERATED_ENCODER_FILE
    return TEXT_ENCODER_FILE


def resolve_lora_name(name) -> str | None:
    """Map a user-supplied LoRA name to an on-disk file (fuzzy match)."""
    if not name or str(name).strip().lower() in ("", "none"):
        return None
    name = str(name).strip()
    available = list_lora_files()
    if name in available:
        return name

    def norm(s: str) -> str:
        s = s.lower()
        for junk in (".safetensors", ".pt", "_", "-", " "):
            s = s.replace(junk, "")
        return s

    wanted = norm(name)
    for f in available:
        if wanted and wanted in norm(f):
            return f
    close = difflib.get_close_matches(
        wanted, [norm(f) for f in available], n=1, cutoff=0.5
    )
    if close:
        for f in available:
            if norm(f) == close[0]:
                return f
    log.warning("LoRA %r not found in %s — ignoring it", name, MODELS_DIR / "loras")
    return None


def _model_nodes(loras) -> tuple[dict, list, list, list]:
    """Loader, GPU-placement and LoRA nodes shared by every workflow.

    Returns (wf, model_ref, clip_ref, vae_ref); the refs point at the end
    of each chain so callers can keep wiring nodes onto them.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET_FILE, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": active_text_encoder(), "type": "krea2",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VAE_FILE},
        },
    }
    model_ref, clip_ref, vae_ref = ["unet", 0], ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # Native core nodes: keep the 13 GB diffusion model alone on gpu:0,
        # park the text encoder + VAE on gpu:1 so nothing swaps mid-run.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    for i, (lora_file, weight) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": lora_file, "strength_model": float(weight),
                       "model": model_ref},
        }
        model_ref = [node, 0]
    return wf, model_ref, clip_ref, vae_ref


def _conditioning_nodes(wf: dict, prompt: str, negative: str, clip_ref) -> None:
    """Add the 'positive'/'negative' conditioning nodes to wf.

    An empty negative prompt becomes ConditioningZeroOut, which also skips
    one text-encoder pass.
    """
    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    if negative.strip():
        wf["negative"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": clip_ref},
        }
    else:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        }


def build_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 8,
    cfg: float = 1.0,
    width: int = 1024,
    height: int = 1024,
    sampler: str = "er_sde",
    loras=(),
    filename_prefix: str = "Krea2",
) -> dict:
    """Build a Krea 2 text-to-image workflow in ComfyUI API format.

    Mirrors the official Krea 2 template: no ModelSampling node is needed
    (shift 1.15 is built into ComfyUI's Krea2 model class), LoRAs apply to
    the diffusion model only.
    `loras` is a sequence of (filename, strength) pairs, already resolved.
    """
    wf, model_ref, clip_ref, vae_ref = _model_nodes(loras)
    _conditioning_nodes(wf, prompt, negative, clip_ref)

    wf["latent"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
    }
    wf["sampler"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": "simple", "denoise": 1.0,
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
        "inputs": {"filename_prefix": filename_prefix, "images": ["decode", 0]},
    }
    return wf


def build_inpaint_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 8,
    cfg: float = 1.0,
    sampler: str = "er_sde",
    denoise: float = 1.0,
    image_name: str,
    mask_name: str | None = None,
    loras=(),
    filename_prefix: str = "Krea2Inpaint",
) -> dict:
    """Build a Krea 2 inpainting / img2img workflow in ComfyUI API format.

    `image_name` and `mask_name` reference files already uploaded to
    ComfyUI's input folder (client.upload_image). With a mask,
    SetLatentNoiseMask re-noises only the masked latent region, so the
    sampler sees the whole image as context but only repaints the mask;
    the decoded result is then composited back over the source so unmasked
    pixels also survive the VAE round-trip untouched. denoise 1.0 replaces
    the masked region completely; lower values keep more of its structure.

    With mask_name=None the whole image is re-noised (classic img2img) —
    denoise then controls how far the result may drift from the source,
    and no compositing happens. Output size is the source image size
    (the UI snaps it to /16).
    """
    wf, model_ref, clip_ref, vae_ref = _model_nodes(loras)
    _conditioning_nodes(wf, prompt, negative, clip_ref)

    wf["source"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    wf["encode"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["source", 0], "vae": vae_ref},
    }
    latent_ref = ["encode", 0]
    if mask_name:
        wf["mask"] = {
            "class_type": "LoadImageMask",
            "inputs": {"image": mask_name, "channel": "red"},
        }
        wf["masked_latent"] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": ["encode", 0], "mask": ["mask", 0]},
        }
        latent_ref = ["masked_latent", 0]
    wf["sampler"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": "simple",
            "denoise": float(denoise),
            "model": model_ref, "positive": ["positive", 0],
            "negative": ["negative", 0], "latent_image": latent_ref,
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    save_ref = ["decode", 0]
    if mask_name:
        wf["composite"] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": ["source", 0], "source": ["decode", 0],
                       "x": 0, "y": 0, "resize_source": False,
                       "mask": ["mask", 0]},
        }
        save_ref = ["composite", 0]
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": save_ref},
    }
    return wf


def build_edit_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 8,
    cfg: float = 1.0,
    width: int,
    height: int,
    sampler: str = "er_sde",
    image_name: str,
    grounding_px: int = 768,
    loras=(),
    filename_prefix: str = "Krea2Edit",
) -> dict:
    """Build an instruction-edit workflow (Krea 2 Identity Edit LoRA).

    Unlike img2img, the source image is fed to the model itself:
    Krea2EditModelPatch prepends its VAE latents as clean in-context
    tokens and Krea2EditGroundedEncode lets the Qwen3-VL encoder read the
    image alongside the instruction — so "make the jacket red" edits the
    photo instead of repainting it from scratch. Both nodes come from the
    ComfyUI-Krea2Edit pack (installed by bootstrap.install_custom_nodes).

    The Identity Edit LoRA is always applied first at strength 1.0 (as
    trained); style `loras` stack after it. `grounding_px` trades edit
    adherence (lower) against identity fidelity (higher). Denoise stays
    1.0: the source enters through conditioning, not the starting latent,
    and `width`/`height` should match the source aspect ratio (≤ 2 MP).
    """
    wf, model_ref, clip_ref, vae_ref = _model_nodes(
        [(EDIT_LORA_FILE, 1.0), *loras]
    )

    wf["source"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    wf["encode"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["source", 0], "vae": vae_ref},
    }
    wf["edit_model"] = {
        "class_type": "Krea2EditModelPatch",
        "inputs": {"model": model_ref, "source_latent": ["encode", 0]},
    }
    wf["positive"] = {
        "class_type": "Krea2EditGroundedEncode",
        "inputs": {"clip": clip_ref, "prompt": prompt,
                   "image": ["source", 0], "grounding_px": int(grounding_px)},
    }
    if negative.strip():
        wf["negative"] = {
            "class_type": "Krea2EditGroundedEncode",
            "inputs": {"clip": clip_ref, "prompt": negative,
                       "image": ["source", 0],
                       "grounding_px": int(grounding_px)},
        }
    else:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        }
    wf["latent"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
    }
    wf["sampler"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": "simple", "denoise": 1.0,
            "model": ["edit_model", 0], "positive": ["positive", 0],
            "negative": ["negative", 0], "latent_image": ["latent", 0],
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": ["decode", 0]},
    }
    return wf
