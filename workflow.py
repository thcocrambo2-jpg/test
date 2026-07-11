"""Krea 2 workflow builder.

The workflow uses only nodes that ship with current ComfyUI:
UNETLoader / CLIPLoader(type="krea2") / VAELoader, LoraLoaderModelOnly,
ConditioningZeroOut and KSampler — plus the native multi-GPU placement
nodes SelectCLIPDevice / SelectVAEDevice when a second GPU is present
(these pass through unchanged on machines where gpu:1 does not exist,
so the same workflow can never fail for lack of a GPU).
"""

import difflib

from comfy import GPU_COUNT
from config import (
    ABLITERATED_ENCODER_FILE,
    MODELS_DIR,
    TEXT_ENCODER_FILE,
    UNET_FILE,
    VAE_FILE,
    log,
)


def list_lora_files() -> list[str]:
    """LoRA files currently available to ComfyUI."""
    return sorted(p.name for p in (MODELS_DIR / "loras").glob("*.safetensors"))


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
    """Build a Krea 2 workflow in ComfyUI API format.

    Mirrors the official Krea 2 template: no ModelSampling node is needed
    (shift 1.15 is built into ComfyUI's Krea2 model class), LoRAs apply to
    the diffusion model only, and an empty negative prompt becomes
    ConditioningZeroOut, which also skips one text-encoder pass.
    `loras` is a sequence of (filename, strength) pairs, already resolved.
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
