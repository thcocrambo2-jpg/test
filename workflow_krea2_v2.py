"""Krea 2 V2 workflow builder.

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

import catalog
from comfy import GPU_COUNT
from config import (
    MODELS_DIR,
    V2_ASPECT_RATIOS,
    V2_FILMGRAIN_DEFAULTS,
    V2_NODE_REPOS,
    V2_SAMPLER_DEFAULTS,
    V2_SHARPEN_DEFAULTS,
    V2_VAE_FILE,
    V2_VARIANCE_DEFAULTS,
    log,
)
from workflow import (
    active_text_encoder,
    lora_file_available,
    model_file_available,
)

# Node classes this tab cannot run without. FilmGrain is deliberately not
# here: it only powers an optional toggle, so a missing post-processing
# pack must not disable the tab.
REQUIRED_NODES = ("ClownsharKSampler_Beta", "RBG_Smart_Seed_Variance")


# ── The catalogue, as the V2 tabs read it ────────────────────────────────────
# Both V2 tabs (krea_v2_t2i, krea_v2_edit) go through these with their own
# feature key, so each offers exactly its own feature's lists. The model
# lookup itself is workflow.resolve_model, shared with the Krea2 tabs.

def model_defaults(model) -> tuple[int, float, bool]:
    """(steps, cfg, turbo_lora) for a model record.

    `turbo_lora` is whether the record names a LoRA its recipe switches on
    — V2's raw, whose companion guide is "enable the Turbo LoRA at 0.6,
    raise steps to 20 and CFG to ~2.5". All three numbers are the
    record's, so the two variants differ by exactly what the record says.
    """
    return int(model.steps), float(model.cfg), model.turbo_lora is not None


def turbo_lora(model) -> "catalog.Lora | None":
    """The LoRA record a model's recipe switches on, or None."""
    if model is None or not model.turbo_lora:
        return None
    return catalog.lora(model.turbo_lora.get("lora"))


def turbo_lora_slot(feature, model) -> int | None:
    """Index of the model's turbo LoRA among the feature's rows, or None.

    The Model dropdown toggles this one row, so it is found by the id the
    model record names rather than by position or by file name — a list
    reordered in the DB, or two records sharing a file, stays safe.
    """
    wanted = (model.turbo_lora or {}).get("lora") if model else None
    if not wanted:
        return None
    for index, lora in enumerate(catalog.feature_loras(feature)):
        if lora.id == wanted:
            return index
    return None


def turbo_lora_available(model) -> bool:
    """True once the LoRA this model's recipe switches on has downloaded.

    True for a model that names none: nothing is missing.
    """
    if model is None or not model.turbo_lora:
        return True
    lora = turbo_lora(model)
    return lora is not None and lora_file_available(lora)


def vae_available() -> bool:
    """True once the Wan 2.1 VAE this pipeline uses has been downloaded."""
    return (MODELS_DIR / "vae" / V2_VAE_FILE).exists()


def default_lora_slots(feature) -> list:
    """The feature's LoRA rows as (enabled, lora id, strength).

    One row per LoRA in the feature's list, in list order, every one off
    and at its record's default strength. Which ones a fresh form turns on
    is the tab's Default preset's business, applied on load — not a fact
    this module restates. A row whose file has not downloaded is still a
    row: the LoRA is listed, and reported as missing (status) rather than
    silently absent from the stack.
    """
    return [(False, lora.id, float(lora.default_strength))
            for lora in catalog.feature_loras(feature)]


def missing_loras(feature, lora_ids=None) -> list:
    """Names of the feature's LoRAs that have not downloaded.

    `lora_ids` narrows it to those rows — the ones a run has switched on;
    None means every LoRA the feature lists.
    """
    wanted = None if lora_ids is None else set(lora_ids)
    return [lora.name for lora in catalog.feature_loras(feature)
            if (wanted is None or lora.id in wanted)
            and not lora_file_available(lora)]


def blocking_problems(feature) -> list:
    """What stops a V2 tab running at all — shared with the V2 Edit tab."""
    problems = []
    models = catalog.feature_models(feature)
    if not models:
        problems.append("the catalogue lists no model for this tab")
    elif not any(model_file_available(model) for model in models):
        problems.append("no V2 model has downloaded")
    if not vae_available():
        problems.append(f"the VAE `{V2_VAE_FILE}` has not downloaded")
    return problems


def status_notes(feature, enabled=None) -> list:
    """What a V2 tab can run without, but should say — shared with Edit.

    `enabled` is the LoRA ids a run has switched on. With it, the note
    names only those that are missing (they will be skipped); without it,
    every listed LoRA that has not downloaded.
    """
    notes = []
    absent = [m.name for m in catalog.feature_models(feature)
              if not model_file_available(m)]
    if absent:
        notes.append("these models have not downloaded and the dropdown "
                     "will refuse them: " + ", ".join(f"`{n}`" for n in absent))
    missing = missing_loras(feature, enabled)
    if missing:
        notes.append(("these LoRAs are switched on but have not downloaded, "
                      "and will be skipped: " if enabled is not None else
                      "these LoRAs are listed but have not downloaded: ")
                     + ", ".join(f"`{m}`" for m in missing))
    return notes


def status(feature, enabled=None) -> tuple[bool, str]:
    """(ready, message) for the tab — what is missing, in plain words.

    `enabled` is the LoRA ids the run being checked has switched on, so
    the warning names the ones that will be skipped; None reports every
    listed LoRA that has not downloaded.

    Node packs are not checked here: they register inside ComfyUI, which
    app.py verifies at startup (comfy.verify_custom_node). This covers the
    files, which is what a user can actually act on.
    """
    problems = blocking_problems(feature)
    if problems:
        return False, ("❌ Krea 2 V2 cannot run — " + " and ".join(problems)
                       + " yet. Restart the app so the download step can "
                         "fetch it.")
    notes = status_notes(feature, enabled)
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
    model,
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
    and strength_clip alike. `model` is the catalogue record the tab's
    Model dropdown named (catalog.Model): its file is the UNet and its
    steps/CFG are the sampler's starting point, which `sampler_settings`
    may then override key by key — as it does for
    V2_SAMPLER_DEFAULTS, and `variance_settings` for
    V2_VARIANCE_DEFAULTS. The Turbo LoRA raw mode wants is not added here:
    it is an ordinary slot in `loras`, so a caller that also ticks it by
    hand cannot end up applying it twice. `variance_seed` defaults to the
    image seed so a reproducible seed reproduces the whole graph, the
    variance node included.
    """
    steps, cfg, _turbo_lora = model_defaults(model)
    sampler = {**V2_SAMPLER_DEFAULTS, "steps": steps, "cfg": cfg,
               **(sampler_settings or {})}
    variance = {**V2_VARIANCE_DEFAULTS, **(variance_settings or {})}

    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": model.file, "weight_dtype": "default"},
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
