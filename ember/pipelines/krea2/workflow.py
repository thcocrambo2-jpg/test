"""Krea 2 workflow builders (text-to-image, instruction edit).

The t2i workflow uses only nodes that ship with current
ComfyUI: UNETLoader / CLIPLoader(type="krea2") / VAELoader,
LoraLoaderModelOnly, ConditioningZeroOut and KSampler — plus the native
multi-GPU placement nodes SelectCLIPDevice / SelectVAEDevice when a
second GPU is present (these pass through unchanged on machines where
gpu:1 does not exist, so the same workflow can never fail for lack of a
GPU). The instruction-edit workflow additionally needs the
ComfyUI-Krea2Edit custom nodes and the Identity Edit LoRA (both fetched
during bootstrap/downloads).

Which diffusion model and which style LoRAs a graph loads is not decided
here: the builders take file names, and the helpers below turn a tab's
catalogue ids (catalog.py) into those names. The catalogue is per
feature, so every helper that reads it takes the feature key — the Krea2
and Krea2 Edit tabs each read their own lists, and so do the two V2 tabs
(workflow_krea2_v2.py).
"""

from ember.licensing import catalog
from ember.comfy.server import GPU_COUNT
from ember.config import (
    ABLITERATED_ENCODER_FILE,
    EDIT_LORA_FILE,
    MODELS_DIR,
    TEXT_ENCODER_FILE,
    VAE_FILE,
    log,
)


# ── The catalogue, per feature ───────────────────────────────────────────────
# Ids are what forms, presets and prompts carry; a file name appears only
# once a graph is being built. These are shared by all four Krea tabs, so
# they live here rather than in either family's builder module.

def resolve_model(feature, model_id) -> "catalog.Model | None":
    """The feature's model record for a form value (an id).

    Exact ids only: the value came from a dropdown this pod described, or
    from a preset the licence server checked against the same ids. An id
    the feature does not list — a model switched off since the preset was
    saved — falls back to the feature's first model, loudly, rather than
    refusing the run. None only when the feature lists no model at all.
    """
    models = catalog.feature_models(feature)
    if not models:
        return None
    for model in models:
        if model.id == model_id:
            return model
    if model_id not in (None, "", catalog.NONE):
        log.warning("Model %r is not offered on %s — using the default (%s)",
                    model_id, feature, models[0].id)
    return models[0]


def model_defaults(model) -> tuple[int, float]:
    """(steps, cfg) for a model record — the record carries them now."""
    return int(model.steps), float(model.cfg)


def model_file_available(model) -> bool:
    """True once the model record's UNet file has been downloaded."""
    return (MODELS_DIR / "diffusion_models" / model.file).exists()


def lora_file_available(lora) -> bool:
    """True once the LoRA record's file has been downloaded."""
    return (MODELS_DIR / "loras" / lora.file).exists()


def feature_lora(feature, lora_id) -> "catalog.Lora | None":
    """The LoRA record for a form value, if the feature offers that id.

    A tab's stack offers exactly its feature's list, so an id outside it —
    one from a preset saved on another tab, or a LoRA switched off since —
    is not this tab's to load, even when the catalogue knows it.
    """
    if lora_id in (None, "", catalog.NONE):
        return None
    return next((lora for lora in catalog.feature_loras(feature)
                 if lora.id == lora_id), None)


def edit_lora_available() -> bool:
    """True once the Identity Edit LoRA has been downloaded."""
    return (MODELS_DIR / "loras" / EDIT_LORA_FILE).exists()


def active_text_encoder() -> str:
    """Prefer the merged abliterated encoder, fall back to the standard one."""
    if (MODELS_DIR / "text_encoders" / ABLITERATED_ENCODER_FILE).exists():
        return ABLITERATED_ENCODER_FILE
    return TEXT_ENCODER_FILE


def _model_nodes(loras, unet_file: str) -> tuple[dict, list, list, list]:
    """Loader, GPU-placement and LoRA nodes shared by every workflow.

    `unet_file` is the diffusion model's file name, already resolved from
    the tab's model id. Returns (wf, model_ref, clip_ref, vae_ref); the
    refs point at the end of each chain so callers can keep wiring nodes
    onto them.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_file, "weight_dtype": "default"},
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
    unet_file: str,
    filename_prefix: str = "Krea2",
) -> dict:
    """Build a Krea 2 text-to-image workflow in ComfyUI API format.

    Mirrors the official Krea 2 template: no ModelSampling node is needed
    (shift 1.15 is built into ComfyUI's Krea2 model class), LoRAs apply to
    the diffusion model only.
    `loras` is a sequence of (filename, strength) pairs, already resolved;
    `unet_file` is the diffusion model's file, resolved from the tab's
    catalogue model id (resolve_model).
    """
    wf, model_ref, clip_ref, vae_ref = _model_nodes(loras, unet_file)
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
    image2_name: str | None = None,
    grounding_px: int = 768,
    ref_boost: float = 4.0,
    ref_boost_a: float = 1.0,
    fit_mode: str = "fit",
    loras=(),
    unet_file: str,
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
    trained); style `loras` stack after it. Denoise stays 1.0: the source
    enters through conditioning, not the starting latent.

    Three of the patch node's inputs are what make this the v1.2 recipe
    rather than the v1.1 one, and all three are optional inputs whose
    absence degrades silently rather than erroring:

      • `vae` + `source_image` — without *both*, fit_mode does nothing.
        They are what lets the node resample the reference in pixel space,
        so a source whose aspect ratio differs from width/height is fitted
        instead of stretched. (`fit_mode="crop (legacy)"` is the v1/v1.1
        geometry, kept as an argument for running older weights.)
      • `target_latent` — the same latent KSampler starts from. The node
        pre-encodes at execution time instead of during the first sampling
        step, so the diffusion model is not evicted mid-run on a GPU that
        is already sharing VRAM (see KREA2_MAIN_RESERVE_VRAM).

    `grounding_px` trades edit adherence (lower) against identity fidelity
    (higher); 384-768 is the trained range and above it the model starts
    emitting duplicated/split compositions. `ref_boost` is how hard the
    edit holds the reference: 1.0 is neutral, ~4 gives strong likeness,
    and past ~10 removals stop working. Generate at ≤ 2 MP either way —
    the source bleeds into the output above that.

    `image2_name` is the optional **second reference**, and passing it is
    what turns this into a two-input edit ("put this person into this
    scene", "these two people in one shot"). The order is the one training
    used and is not interchangeable: the scene — the image whose
    composition the output keeps — goes to `image_name`, the subject to
    `image2_name`. Both references reach the model twice, as latents on
    the patch node (`source_latent_b`) and as vision tokens on *both*
    grounded encoders (`image_b`), which is how the shipped workflow wires
    its second group.

    Two references also split the fidelity dial in two. `ref_boost` always
    applies to the *last* reference — so it means "the source" in a
    one-image edit and "the subject" in a two-image one — and `ref_boost_a`
    is the same dial for the scene. `ref_boost_a` is only emitted when
    there is a second reference, since it does nothing without one.

    With no `image2_name` this builds exactly the graph it always did: the
    b-inputs are left unwired rather than passed as None, which is what
    upstream means by "leave the b-inputs unconnected for single-image
    use".
    """
    wf, model_ref, clip_ref, vae_ref = _model_nodes(
        [(EDIT_LORA_FILE, 1.0), *loras], unet_file
    )

    wf["source"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    wf["encode"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["source", 0], "vae": vae_ref},
    }
    wf["latent"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
    }
    patch_inputs = {
        "model": model_ref,
        "source_latent": ["encode", 0],
        "source_image": ["source", 0],
        "vae": vae_ref,
        "target_latent": ["latent", 0],
        "fit_mode": fit_mode,
        "ref_boost": float(ref_boost),
    }
    # Shared by both grounded encoders, so the negative sees exactly the
    # references the positive does — the trained unconditional.
    grounding = {"grounding_px": int(grounding_px)}
    if image2_name:
        wf["source_b"] = {
            "class_type": "LoadImage",
            "inputs": {"image": image2_name},
        }
        wf["encode_b"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["source_b", 0], "vae": vae_ref},
        }
        patch_inputs["source_latent_b"] = ["encode_b", 0]
        patch_inputs["source_image_b"] = ["source_b", 0]
        patch_inputs["ref_boost_a"] = float(ref_boost_a)
        grounding["image_b"] = ["source_b", 0]
    wf["edit_model"] = {
        "class_type": "Krea2EditModelPatch",
        "inputs": patch_inputs,
    }
    wf["positive"] = {
        "class_type": "Krea2EditGroundedEncode",
        "inputs": {"clip": clip_ref, "prompt": prompt,
                   "image": ["source", 0], **grounding},
    }
    if negative.strip():
        wf["negative"] = {
            "class_type": "Krea2EditGroundedEncode",
            "inputs": {"clip": clip_ref, "prompt": negative,
                       "image": ["source", 0], **grounding},
        }
    else:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
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
