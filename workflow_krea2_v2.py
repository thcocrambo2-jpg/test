"""Krea 2 V2 workflow builder — the DesiMuseAI TURBO/RAW graph.

A node-for-node port of that workflow into ComfyUI API format. It is kept
apart from workflow.py because almost nothing about it is shared: its own
UNet quant (mxfp8), its own VAE (Wan 2.1), its own LoRA stack applied to
model *and* CLIP, RES4LYF's ClownsharKSampler_Beta instead of KSampler,
and an RBG Smart Seed Variance node between the positive prompt and the
sampler.

Three nodes in the source graph carry no execution semantics and are not
reproduced: rgthree's Fast Bypasser (a UI toggle whose output goes
nowhere), Label and MarkdownNote (annotations). Two more are translated
rather than copied, which changes no pixels:

  • Power Lora Loader (rgthree) → a LoraLoader chain. In "Single Strength"
    mode it applies one strength to the model and the CLIP, which is
    exactly what LoraLoader does; the on/off state per row becomes the
    slot's Enable checkbox. Note this is model+clip, unlike the
    LoraLoaderModelOnly chain workflow.py builds.
  • ResolutionSelector → PrimitiveInt → EmptyLatentImage is integer
    plumbing, so resolve_size() does the arithmetic here and the tab shows
    the result.

ImageSharpen and FilmGrain are bypassed (mode 4) in the source workflow.
They are built only when their toggle is on, so the defaults reproduce it
as shipped: VAEDecode straight to SaveImage.
"""

from comfy import GPU_COUNT
from config import (
    MODELS_DIR,
    V2_ASPECT_RATIOS,
    V2_FILMGRAIN_DEFAULTS,
    V2_LORA_STACK,
    V2_MODELS,
    V2_NODE_REPOS,
    V2_SAMPLER_DEFAULTS,
    V2_SHARPEN_DEFAULTS,
    V2_TURBO_LORA_FILE,
    V2_VAE_FILE,
    V2_VARIANCE_DEFAULTS,
    V2_VARIANT_DEFAULTS,
    log,
)
from workflow import active_text_encoder

# Node classes this tab cannot run without. FilmGrain is deliberately not
# here: it only powers an optional toggle, so a missing post-processing
# pack must not disable the tab.
REQUIRED_NODES = ("ClownsharKSampler_Beta", "RBG_Smart_Seed_Variance")


def model_names() -> list[str]:
    """Dropdown labels for every V2 model, in config order."""
    return [entry["name"] for entry in V2_MODELS]


def resolve_model(name) -> dict:
    """Map a UI model name to its V2_MODELS entry (default: first entry).

    Accepts the registry name, the filename, or a case-insensitive
    fragment of either — the same forgiving lookup resolve_model_entry
    does for the Single tab, kept separate because the registries are.
    """
    if not name or str(name).strip().lower() in ("", "none", "default"):
        return V2_MODELS[0]
    wanted = str(name).strip().lower()
    for entry in V2_MODELS:
        if wanted in (entry["name"].lower(), entry["file"].lower()):
            return entry
    for entry in V2_MODELS:
        if wanted in entry["name"].lower() or wanted in entry["file"].lower():
            return entry
    log.warning("Krea 2 V2 model %r not in V2_MODELS — using the default "
                "(%s)", name, V2_MODELS[0]["name"])
    return V2_MODELS[0]


def model_defaults(entry: dict) -> tuple[int, float, bool]:
    """(steps, cfg, turbo_lora) for an entry: overrides, else the variant."""
    variant = V2_VARIANT_DEFAULTS.get(entry.get("variant", "turbo"),
                                      V2_VARIANT_DEFAULTS["turbo"])
    return (int(entry.get("steps", variant["steps"])),
            float(entry.get("cfg", variant["cfg"])),
            bool(entry.get("turbo_lora", variant["turbo_lora"])))


def model_available(entry: dict | None = None) -> bool:
    """True once that entry's UNet has been downloaded (default: the first)."""
    entry = entry or V2_MODELS[0]
    return (MODELS_DIR / "diffusion_models" / entry["file"]).exists()


def turbo_lora_slot() -> int | None:
    """Index of the Krea 2 Turbo LoRA in the stack, or None if absent.

    The Model dropdown toggles this one slot, so the index is looked up
    rather than assumed — reordering V2_LORA_STACK stays safe.
    """
    for index, (name, _s, _e, _v) in enumerate(V2_LORA_STACK):
        if name == V2_TURBO_LORA_FILE:
            return index
    return None


def turbo_lora_available() -> bool:
    """True once the Krea 2 Turbo LoRA raw mode switches on has downloaded."""
    return (MODELS_DIR / "loras" / V2_TURBO_LORA_FILE).exists()


def vae_available() -> bool:
    """True once the Wan 2.1 VAE this pipeline uses has been downloaded."""
    return (MODELS_DIR / "vae" / V2_VAE_FILE).exists()


def available_lora_files() -> set:
    """Names from the V2 stack that are actually on disk."""
    return {name for name, _s, _e, _v in V2_LORA_STACK
            if (MODELS_DIR / "loras" / name).exists()}


def default_lora_slots() -> list:
    """The workflow's LoRA rows as (enabled, filename, strength).

    Order, strengths and on/off states come straight from the source
    graph. A row whose file did not download starts disabled so the tab
    never submits a lora_name ComfyUI cannot resolve.
    """
    on_disk = available_lora_files()
    return [(enabled and name in on_disk, name, strength)
            for name, strength, enabled, _version in V2_LORA_STACK]


def missing_enabled_loras() -> list:
    """Files the workflow enables by default that have not downloaded."""
    on_disk = available_lora_files()
    return [name for name, _s, enabled, _v in V2_LORA_STACK
            if enabled and name not in on_disk]


def status() -> tuple[bool, str]:
    """(ready, message) for the tab — what is missing, in plain words.

    Node packs are not checked here: they register inside ComfyUI, which
    app.py verifies at startup (comfy.verify_custom_node). This covers the
    files, which is what a user can actually act on.
    """
    problems = []
    if not any(model_available(entry) for entry in V2_MODELS):
        problems.append("no V2 model has downloaded")
    if not vae_available():
        problems.append(f"the VAE `{V2_VAE_FILE}` has not downloaded")
    if problems:
        return False, ("❌ Krea 2 V2 cannot run — " + " and ".join(problems)
                       + " yet. Restart the app so the download step can "
                         "fetch it.")
    notes = []
    absent = [e["name"] for e in V2_MODELS if not model_available(e)]
    if absent:
        notes.append("these models have not downloaded and the dropdown "
                     "will refuse them: " + ", ".join(f"`{n}`" for n in absent))
    missing = missing_enabled_loras()
    if missing:
        notes.append("these LoRAs from the workflow's stack did not download "
                     "and their slots start off: "
                     + ", ".join(f"`{m}`" for m in missing))
    if notes:
        return True, "⚠️ Ready, but " + "; ".join(notes)
    packs = ", ".join(f"`{d}`" for d, _r, _c in V2_NODE_REPOS)
    return True, f"✅ Ready. Node packs required: {packs}"


def resolve_size(aspect: str, megapixels: float, multiple: int) -> tuple:
    """ResolutionSelector's arithmetic: aspect + megapixels → (width, height).

    Megapixels are counted as 1024² (ComfyUI's own convention, as in
    ImageScaleToTotalPixels), and each side is rounded to the nearest
    `multiple`. The workflow's 3:4 at 1.5 MP with multiple 8 resolves to
    1088×1448.
    """
    ratio_w, ratio_h = V2_ASPECT_RATIOS.get(
        aspect, V2_ASPECT_RATIOS["3:4 (Portrait Standard)"]
    )
    total = max(0.1, float(megapixels)) * 1024 * 1024
    height = (total / (ratio_w / ratio_h)) ** 0.5
    width = height * ratio_w / ratio_h
    step = max(1, int(multiple))

    def snap(value: float) -> int:
        return max(step, int(round(value / step)) * step)

    return snap(width), snap(height)


def build_v2_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    width: int,
    height: int,
    loras=(),
    unet_file: str | None = None,
    sampler_settings=None,
    variance_settings=None,
    variance_seed: int | None = None,
    sharpen: bool = False,
    film_grain: bool = False,
    filename_prefix: str = "Krea2V2",
) -> dict:
    """Build the Krea 2 V2 workflow in ComfyUI API format.

    `loras` is a sequence of (filename, strength) pairs, already resolved
    and filtered to the enabled rows; each strength drives strength_model
    and strength_clip alike. `unet_file` selects the diffusion model
    (V2_MODELS registry) and supplies the steps/CFG its variant defines,
    which `sampler_settings` may then override key by key — as it does for
    V2_SAMPLER_DEFAULTS, and `variance_settings` for
    V2_VARIANCE_DEFAULTS. The Turbo LoRA raw mode wants is not added here:
    it is an ordinary slot in `loras`, so a caller that also ticks it by
    hand cannot end up applying it twice. `variance_seed` defaults to the
    image seed so a reproducible seed reproduces the whole graph, the
    variance node included.
    """
    entry = resolve_model(unet_file)
    steps, cfg, _turbo_lora = model_defaults(entry)
    sampler = {**V2_SAMPLER_DEFAULTS, "steps": steps, "cfg": cfg,
               **(sampler_settings or {})}
    variance = {**V2_VARIANCE_DEFAULTS, **(variance_settings or {})}

    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": entry["file"], "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": active_text_encoder(), "type": "krea2",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": V2_VAE_FILE},
        },
    }
    model_ref, clip_ref, vae_ref = ["unet", 0], ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # Same placement the other tabs use. Pure device assignment — it
        # cannot change the image, and on the single-GPU pods this app
        # targets these nodes are never built at all.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    # Power Lora Loader, expanded. Both refs advance together because the
    # source node feeds its CLIP output to the two text encoders.
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
    wf["negative"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": negative, "clip": clip_ref},
    }
    # The variance node sits on the positive conditioning only, exactly as
    # wired in the source graph (link 162 → 163).
    wf["variance"] = {
        "class_type": "RBG_Smart_Seed_Variance",
        "inputs": {
            "conditioning": ["positive", 0],
            "seed": int(seed if variance_seed is None else variance_seed),
            **variance,
        },
    }
    wf["latent"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": int(width), "height": int(height),
                   "batch_size": 1},
    }
    wf["sampler"] = {
        "class_type": "ClownsharKSampler_Beta",
        "inputs": {
            "model": model_ref,
            "positive": ["variance", 0],
            "negative": ["negative", 0],
            "latent_image": ["latent", 0],
            "seed": int(seed),
            **sampler,
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }

    image_ref = ["decode", 0]
    if sharpen:
        wf["sharpen"] = {
            "class_type": "ImageSharpen",
            "inputs": {"image": image_ref, **V2_SHARPEN_DEFAULTS},
        }
        image_ref = ["sharpen", 0]
    if film_grain:
        wf["grain"] = {
            "class_type": "FilmGrain",
            "inputs": {"image": image_ref, **V2_FILMGRAIN_DEFAULTS},
        }
        image_ref = ["grain", 0]

    # SaveImage rather than WAS "Image Save": the app finds outputs through
    # ComfyUI's history (client.output_images), and SaveImage is what puts
    # them there in the layout the Gallery tab and the zip download expect.
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": image_ref},
    }
    log.debug("Krea 2 V2 graph: %d nodes, %d LoRA(s)", len(wf), len(loras))
    return wf
