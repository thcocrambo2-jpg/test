"""Flux 2 Klein 9B Edit workflow builder — the DesiMuseAI v1.3 graph.

A node-for-node port of that workflow into ComfyUI API format. Every node
it executes is stock ComfyUI, so unlike the Krea 2 V2 tab this one installs
no custom node packs: UNETLoader, CLIPLoader(type="flux2"), VAELoader,
LoraLoader, CLIPTextEncode, LoadImage, ImageScaleToTotalPixels, VAEEncode,
ReferenceLatent, FluxGuidance, ConditioningZeroOut, EmptyFlux2LatentImage,
KSamplerAdvanced, VAEDecode, SaveImage.

What the graph does that the other tabs do not: the source image is scaled
to 1 MP, VAE-encoded and attached to the *conditioning* through
ReferenceLatent. The model therefore sees the image it is editing, and the
sampler still starts from empty noise at the output resolution — which is
why the reference size and the output size are two separate settings, and
why "same as image 1" is about the output, not the reference.

Three of the source workflow's nodes carry no execution semantics and are
not reproduced: rgthree's Fast Groups Bypasser (a UI toggle whose output
goes nowhere), Label and MarkdownNote. Four more are translated rather
than copied, which changes no pixels:

  • Power Lora Loader (rgthree) → a LoraLoader chain. In "Single Strength"
    mode it applies one strength to the model and the CLIP, which is what
    LoraLoader does; each row's on/off state becomes its Enable checkbox.
  • GetImageSize → EmptyFlux2LatentImage is integer plumbing, so
    resolve_output_size() does the arithmetic here and the tab shows the
    W×H it resolved to.
  • Any Switch (rgthree) picks whichever of the three resolution groups is
    not bypassed — a Python branch on the output-mode dropdown.
  • WAS "Image Save" → SaveImage. The app finds outputs through ComfyUI's
    history, and WAS writes its own dated tree the Gallery tab would not
    see. PreviewImage and Image Comparer (rgthree) are display-only.

The second input image and the two alternative resolution groups are
bypassed (mode 4) in the source workflow, so they start off here and the
defaults reproduce it as shipped.
"""

import difflib

from comfy import GPU_COUNT
from config import (
    KLEIN_DEFAULTS,
    KLEIN_LORA_STACK,
    KLEIN_LORA_SUBDIR,
    KLEIN_MAX_SIDE,
    KLEIN_MODELS,
    KLEIN_OUTPUT_CUSTOM,
    KLEIN_OUTPUT_SAME,
    KLEIN_OUTPUT_SCALED,
    KLEIN_REFERENCE_MEGAPIXELS,
    KLEIN_RESOLUTION_STEPS,
    KLEIN_SAMPLER_DEFAULTS,
    KLEIN_SCALE_METHOD,
    KLEIN_TEXT_ENCODER,
    KLEIN_VAE,
    MODELS_DIR,
    log,
)


# ── LoRAs ─────────────────────────────────────────────────────────────────────

def lora_dir():
    """The folder Klein LoRAs live in (loras/klein/)."""
    return MODELS_DIR / "loras" / KLEIN_LORA_SUBDIR


def list_lora_files() -> list[str]:
    """Klein LoRA files (bare names) available under loras/klein/."""
    return sorted(p.name for p in lora_dir().glob("*.safetensors"))


def resolve_lora(name) -> str | None:
    """Map a user-supplied Klein LoRA name to an on-disk file (fuzzy match).

    Same forgiving lookup as the Flux tab's, kept separate because the
    folders are: a name that matches nothing here must not silently fall
    through to a Krea 2 or Flux LoRA of a different architecture.
    """
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
    log.warning("Klein LoRA %r not found in %s — ignoring it", name, lora_dir())
    return None


def available_lora_files() -> set:
    """Names from the Klein stack that are actually on disk."""
    return {name for name, _s, _e, _v in KLEIN_LORA_STACK
            if (lora_dir() / name).exists()}


def default_lora_slots() -> list:
    """The workflow's LoRA rows as (enabled, filename, strength).

    Order, strengths and on/off states come straight from the source graph.
    A row whose file did not download starts disabled so the tab never
    submits a lora_name ComfyUI cannot resolve.
    """
    on_disk = available_lora_files()
    return [(enabled and name in on_disk, name, strength)
            for name, strength, enabled, _version in KLEIN_LORA_STACK]


def missing_enabled_loras() -> list:
    """Files the workflow enables by default that have not downloaded."""
    on_disk = available_lora_files()
    return [name for name, _s, enabled, _v in KLEIN_LORA_STACK
            if enabled and name not in on_disk]


# ── Model registry ────────────────────────────────────────────────────────────

def model_names() -> list[str]:
    """Dropdown labels for every registered Klein model, in config order."""
    return [entry["name"] for entry in KLEIN_MODELS]


def resolve_model(name) -> dict:
    """Map a UI model name to its KLEIN_MODELS entry (default: first entry)."""
    if not name or str(name).strip().lower() in ("", "none", "default"):
        return KLEIN_MODELS[0]
    wanted = str(name).strip().lower()
    for entry in KLEIN_MODELS:
        if wanted in (entry["name"].lower(), entry["file"].lower()):
            return entry
    for entry in KLEIN_MODELS:
        if wanted in entry["name"].lower() or wanted in entry["file"].lower():
            return entry
    log.warning("Klein model %r not in KLEIN_MODELS — using the default (%s)",
                name, KLEIN_MODELS[0]["name"])
    return KLEIN_MODELS[0]


def model_defaults(entry: dict) -> tuple[int, float, float]:
    """(steps, cfg, guidance) for an entry: overrides, else KLEIN_DEFAULTS."""
    return (int(entry.get("steps", KLEIN_DEFAULTS["steps"])),
            float(entry.get("cfg", KLEIN_DEFAULTS["cfg"])),
            float(entry.get("guidance", KLEIN_DEFAULTS["guidance"])))


def model_available(entry: dict | None = None) -> bool:
    """True once that entry's UNet has been downloaded (default: the first)."""
    entry = entry or KLEIN_MODELS[0]
    return (MODELS_DIR / "diffusion_models" / entry["file"]).exists()


def encoder_available() -> bool:
    """True once the Qwen3-8B text encoder this pipeline needs is on disk."""
    return (MODELS_DIR / "text_encoders" / KLEIN_TEXT_ENCODER).exists()


def vae_available() -> bool:
    """True once the Flux 2 VAE is on disk (shared with the Flux 2 tab)."""
    return (MODELS_DIR / "vae" / KLEIN_VAE).exists()


def status() -> tuple[bool, str]:
    """(ready, message) for the tab — what is missing, in plain words."""
    problems = []
    if not any(model_available(entry) for entry in KLEIN_MODELS):
        problems.append("no Klein model has downloaded")
    if not encoder_available():
        problems.append(f"the text encoder `{KLEIN_TEXT_ENCODER}` has not "
                        "downloaded")
    if not vae_available():
        problems.append(f"the VAE `{KLEIN_VAE}` has not downloaded")
    if problems:
        return False, ("❌ Klein Edit cannot run — " + " and ".join(problems)
                       + " yet. Restart the app so the download step can "
                         "fetch it.")
    notes = []
    absent = [e["name"] for e in KLEIN_MODELS if not model_available(e)]
    if absent:
        notes.append("these models have not downloaded and the dropdown will "
                     "refuse them: " + ", ".join(f"`{n}`" for n in absent))
    missing = missing_enabled_loras()
    if missing:
        notes.append("these LoRAs from the workflow's stack did not download "
                     "and their slots start off: "
                     + ", ".join(f"`{m}`" for m in missing))
    if notes:
        return True, "⚠️ Ready, but " + "; ".join(notes)
    return True, "✅ Ready. No custom node packs needed — every node is stock."


# ── Output size ───────────────────────────────────────────────────────────────

def _snap(value: float) -> int:
    """Round to a multiple of 16 and clamp to a sane range.

    Flux 2 latents are 16× compressed, so an odd size would be rounded by
    ComfyUI anyway; doing it here is what lets the tab show the number it
    is actually going to render. The upper clamp is a guardrail rather than
    a translation of the workflow: "same as image 1" faithfully means a
    12 MP phone photo asks for a 12 MP render, and without a ceiling one
    paste can OOM-kill the server for every other tab too.
    """
    return max(256, min(KLEIN_MAX_SIDE, int(round(value / 16)) * 16))


def resolve_output_size(mode: str, source: tuple, megapixels: float,
                        custom: tuple) -> tuple:
    """The Any Switch, in Python: which resolution group feeds the sampler.

    `source` is image 1's (width, height). The workflow ships with the two
    alternative groups bypassed, so KLEIN_OUTPUT_SAME is the default and
    reproduces it exactly.
    """
    width, height = source
    if mode == KLEIN_OUTPUT_CUSTOM:
        width, height = custom
    elif mode == KLEIN_OUTPUT_SCALED:
        # ImageScaleToTotalPixels' own arithmetic: megapixels are counted
        # as 1024² and the aspect ratio of the source is preserved.
        scale = (max(0.1, float(megapixels)) * 1024 * 1024
                 / max(1, width * height)) ** 0.5
        width, height = width * scale, height * scale
    return _snap(width), _snap(height)


# ── Workflow ──────────────────────────────────────────────────────────────────

def build_klein_edit_workflow(
    *,
    prompt: str,
    image_name: str,
    image2_name: str | None = None,
    seed: int = 0,
    width: int = 1024,
    height: int = 1024,
    steps: int | None = None,
    cfg: float | None = None,
    guidance: float | None = None,
    sampler: str | None = None,
    scheduler: str | None = None,
    reference_megapixels: float = KLEIN_REFERENCE_MEGAPIXELS,
    loras=(),
    unet_file: str | None = None,
    filename_prefix: str = "KleinEdit",
) -> dict:
    """Build the Klein 9B edit workflow in ComfyUI API format.

    `image_name` (and the optional `image2_name`) are names already
    uploaded to ComfyUI's input folder, as LoadImage expects. `loras` is a
    sequence of (bare_filename, strength) pairs from loras/klein/ — the
    subfolder prefix is added here — and each strength drives
    strength_model and strength_clip alike, as the source's Power Lora
    Loader does in Single Strength mode.

    Anything left at None takes the selected model's default, so a caller
    that only knows the prompt still gets the workflow's own recipe.
    """
    entry = resolve_model(unet_file)
    default_steps, default_cfg, default_guidance = model_defaults(entry)
    steps = default_steps if steps is None else int(steps)
    cfg = default_cfg if cfg is None else float(cfg)
    guidance = default_guidance if guidance is None else float(guidance)
    sampler = sampler or KLEIN_DEFAULTS["sampler_name"]
    scheduler = scheduler or KLEIN_DEFAULTS["scheduler"]

    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": entry["file"], "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": KLEIN_TEXT_ENCODER, "type": "flux2",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": KLEIN_VAE},
        },
    }
    model_ref, clip_ref, vae_ref = ["unet", 0], ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # Same placement plan as the other tabs. Pure device assignment —
        # it cannot change the image, and on the single-GPU pods this app
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
    # source node feeds its CLIP output to the text encoder.
    for i, (lora_file, strength) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": f"{KLEIN_LORA_SUBDIR}/{lora_file}",
                       "strength_model": float(strength),
                       "strength_clip": float(strength),
                       "model": model_ref, "clip": clip_ref},
        }
        model_ref, clip_ref = [node, 0], [node, 1]

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    cond_ref = ["positive", 0]

    # Each source image: LoadImage → ImageScaleToTotalPixels → VAEEncode →
    # ReferenceLatent, chained onto the conditioning in order. Image 2's
    # group is bypassed in the source workflow, so it is built only when
    # the tab's "Enable input image 2" toggle asked for it.
    for index, name in enumerate((image_name, image2_name), start=1):
        if not name:
            continue
        load, scale = f"load{index}", f"scale{index}"
        encode, reference = f"encode{index}", f"reference{index}"
        wf[load] = {
            "class_type": "LoadImage",
            "inputs": {"image": name},
        }
        wf[scale] = {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {"image": [load, 0],
                       "upscale_method": KLEIN_SCALE_METHOD,
                       "megapixels": float(reference_megapixels),
                       "resolution_steps": KLEIN_RESOLUTION_STEPS},
        }
        wf[encode] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": [scale, 0], "vae": vae_ref},
        }
        wf[reference] = {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": cond_ref, "latent": [encode, 0]},
        }
        cond_ref = [reference, 0]

    wf["guidance"] = {
        "class_type": "FluxGuidance",
        "inputs": {"conditioning": cond_ref, "guidance": guidance},
    }
    # Klein is guidance-distilled and runs at CFG 1.0, where the negative
    # branch is not evaluated at all — the source workflow still wires one,
    # zeroed out, because KSamplerAdvanced requires it.
    wf["negative"] = {
        "class_type": "ConditioningZeroOut",
        "inputs": {"conditioning": ["guidance", 0]},
    }
    wf["latent"] = {
        "class_type": "EmptyFlux2LatentImage",
        "inputs": {"width": int(width), "height": int(height),
                   "batch_size": 1},
    }
    wf["sampler"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": model_ref,
            "positive": ["guidance", 0],
            "negative": ["negative", 0],
            "latent_image": ["latent", 0],
            "noise_seed": int(seed),
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler,
            "scheduler": scheduler,
            **KLEIN_SAMPLER_DEFAULTS,
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    # SaveImage rather than WAS "Image Save": the app finds outputs through
    # ComfyUI's history (client.output_images), and SaveImage is what puts
    # them there in the layout the Gallery tab and the zip download expect.
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": ["decode", 0]},
    }
    log.debug("Klein edit graph: %d nodes, %d LoRA(s), %d source image(s)",
              len(wf), len(loras), 2 if image2_name else 1)
    return wf
