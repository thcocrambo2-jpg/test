"""Flux 2 text-to-image workflow builder (ComfyUI API format).

Flux 2 Dev is guidance-distilled: there is no CFG and no negative prompt.
Instead a FluxGuidance value is baked into the conditioning and sampling
runs through the custom-sampler stack — RandomNoise + BasicGuider +
SamplerCustomAdvanced with Flux2Scheduler sigmas — exactly mirroring the
official ComfyUI template. Only stock nodes are used: UNETLoader,
CLIPLoader(type="flux2"), VAELoader, LoraLoaderModelOnly, CLIPTextEncode,
FluxGuidance, EmptyFlux2LatentImage, Flux2Scheduler, KSamplerSelect,
RandomNoise, BasicGuider, SamplerCustomAdvanced, VAEDecode, SaveImage.

"Turbo" is the official Flux 2 Turbo LoRA (8 steps) on top of the same
Dev weights; "raw" is the undistilled 20-step schedule — the same
LoRA-based turbo scheme as Wan. Flux style LoRAs live in their own
loras/flux2/ subfolder so they never leak into the Krea 2 dropdowns.
"""

import difflib

from comfy import GPU_COUNT
from config import (
    FLUX_LORA_SUBDIR,
    FLUX_MODELS,
    FLUX_TEXT_ENCODER,
    FLUX_TURBO_LORA,
    FLUX_VAE,
    FLUX_VARIANT_DEFAULTS,
    MODELS_DIR,
    log,
)


# ── LoRAs ─────────────────────────────────────────────────────────────────────

def list_flux_lora_files() -> list[str]:
    """Flux LoRA files (bare names) available under loras/flux2/."""
    folder = MODELS_DIR / "loras" / FLUX_LORA_SUBDIR
    return sorted(p.name for p in folder.glob("*.safetensors"))


def resolve_flux_lora(name) -> str | None:
    """Map a user-supplied Flux LoRA name to an on-disk file (fuzzy match)."""
    if not name or str(name).strip().lower() in ("", "none"):
        return None
    name = str(name).strip()
    available = list_flux_lora_files()
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
    log.warning("Flux LoRA %r not found in %s — ignoring it",
                name, MODELS_DIR / "loras" / FLUX_LORA_SUBDIR)
    return None


# ── Model registry ────────────────────────────────────────────────────────────

def flux_model_names() -> list[str]:
    """Dropdown labels for every registered Flux model, in config order."""
    return [entry["name"] for entry in FLUX_MODELS]


def resolve_flux_model(name) -> dict:
    """Map a UI model name to its FLUX_MODELS entry (default: first entry)."""
    if not name or str(name).strip().lower() in ("", "none", "default"):
        return FLUX_MODELS[0]
    wanted = str(name).strip().lower()
    for entry in FLUX_MODELS:
        if wanted in (entry["name"].lower(), entry["file"].lower()):
            return entry
    for entry in FLUX_MODELS:
        if wanted in entry["name"].lower() or wanted in entry["file"].lower():
            return entry
    log.warning("Flux model %r not in FLUX_MODELS — using the default (%s)",
                name, FLUX_MODELS[0]["name"])
    return FLUX_MODELS[0]


def flux_model_defaults(entry: dict) -> tuple[int, float, bool]:
    """(steps, guidance, turbo_lora) for an entry: overrides, else variant."""
    variant = FLUX_VARIANT_DEFAULTS.get(entry.get("variant", "raw"),
                                        FLUX_VARIANT_DEFAULTS["raw"])
    return (int(entry.get("steps", variant["steps"])),
            float(entry.get("guidance", variant["guidance"])),
            bool(entry.get("turbo_lora", variant["turbo_lora"])))


def flux_model_available(entry: dict) -> bool:
    """True once the entry's UNet plus the shared encoder/VAE are on disk."""
    required = [
        MODELS_DIR / "diffusion_models" / entry["file"],
        MODELS_DIR / "text_encoders" / FLUX_TEXT_ENCODER,
        MODELS_DIR / "vae" / FLUX_VAE,
    ]
    return all(p.exists() for p in required)


def flux_turbo_lora_available() -> bool:
    return (MODELS_DIR / "loras" / FLUX_TURBO_LORA).exists()


# ── Workflow ──────────────────────────────────────────────────────────────────

def build_flux_workflow(
    *,
    prompt: str,
    seed: int = 0,
    steps: int = 8,
    guidance: float = 4.0,
    width: int = 1024,
    height: int = 1024,
    sampler: str = "euler",
    loras=(),
    unet_file: str | None = None,
    turbo_lora: bool = True,
    filename_prefix: str = "Flux2",
) -> dict:
    """Build a Flux 2 text-to-image workflow in ComfyUI API format.

    `loras` is a sequence of (bare_filename, strength) pairs from
    loras/flux2/ — the subfolder prefix is added here. When `turbo_lora`
    is set, the official Turbo LoRA is applied first at strength 1.0
    (that is what makes the 8-step "turbo" schedule work); style LoRAs
    stack after it.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file or FLUX_MODELS[0]["file"],
                       "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": FLUX_TEXT_ENCODER, "type": "flux2",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": FLUX_VAE},
        },
    }
    model_ref = ["unet", 0]
    clip_ref, vae_ref = ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # Same placement plan as the Krea/Wan workflows.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    if turbo_lora:
        wf["turbo"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": FLUX_TURBO_LORA, "strength_model": 1.0,
                       "model": model_ref},
        }
        model_ref = ["turbo", 0]

    for i, (lora_file, weight) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": f"{FLUX_LORA_SUBDIR}/{lora_file}",
                       "strength_model": float(weight), "model": model_ref},
        }
        model_ref = [node, 0]

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    wf["guidance"] = {
        "class_type": "FluxGuidance",
        "inputs": {"conditioning": ["positive", 0],
                   "guidance": float(guidance)},
    }
    wf["latent"] = {
        "class_type": "EmptyFlux2LatentImage",
        "inputs": {"width": int(width), "height": int(height),
                   "batch_size": 1},
    }
    wf["noise"] = {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": int(seed)},
    }
    wf["sampler_sel"] = {
        "class_type": "KSamplerSelect",
        "inputs": {"sampler_name": sampler},
    }
    wf["sigmas"] = {
        "class_type": "Flux2Scheduler",
        "inputs": {"steps": int(steps), "width": int(width),
                   "height": int(height)},
    }
    wf["guider"] = {
        "class_type": "BasicGuider",
        "inputs": {"model": model_ref, "conditioning": ["guidance", 0]},
    }
    wf["sample"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {"noise": ["noise", 0], "guider": ["guider", 0],
                   "sampler": ["sampler_sel", 0], "sigmas": ["sigmas", 0],
                   "latent_image": ["latent", 0]},
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sample", 0], "vae": vae_ref},
    }
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix,
                   "images": ["decode", 0]},
    }
    return wf
