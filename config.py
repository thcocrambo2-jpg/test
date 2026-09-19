"""Krea 2 on RunPod — configuration.

Everything user-tunable lives in this module: paths, model registries,
LoRA lists and (optional) access tokens. In section order:

    Build mode          FROZEN
    Disk layout         BASE_DIR and everything under it, ComfyUI's port
    Model selection     Krea 2 base models, encoder, LoRAs — the
                        Single / Edit / Inpaint tabs
    Krea 2 V2           the Krea2 advanced pipeline, self-contained
    Wan 2.2             image-to-video (+ the parallel-instance knobs)
    MiniMax H3          video with sound — image-to-video and text-to-video
    CivitAI LoRAs       shared LoRA lists, resolutions, samplers
    Licensing           seat check + access tokens

Which *tabs* exist is not decided here — that is features.py, and the
answer comes from the license key rather than from anything in this file
or the environment. This module only describes what each feature would
need if it were on, so it stays a plain data module that features.py can
import without a cycle.

Every environment variable this module reads, in one place:

    KREA2_BASE_DIR              where models, outputs and logs live
    KREA2_KEEP_MODELS_LOADED    do not unload between model swaps
    KREA2_SAGE_ATTENTION        0 runs ComfyUI without SageAttention
    KREA2_WAN_PARALLEL          second ComfyUI instance for video
    KREA2_MAIN_RESERVE_VRAM     GB left for Wan by the image instance
    KREA2_WAN_RESERVE_VRAM      GB left for images by the video instance
    KREA2_LICENSE_KEY           the customer key (required)
    KREA2_NODE_TAG              the deployment id the key checks in against
    KREA2_LICENSE_GRACE         seconds tolerated with no licence server
    KREA2_SHOWCASE_URL          public bucket holding the pricing page's images
    HF_TOKEN, CIVITAI_TOKEN     download credentials
"""

import logging
import os
import re
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("krea2")

# ── Build mode ────────────────────────────────────────────────────────────────
# True when running as a Nuitka-compiled binary (build.sh), False under a
# plain `python3 app.py`. Nuitka injects __compiled__ into every module it
# compiles, so this is the one authoritative check — keep it that way.
# Only two places may branch on it: bootstrap.runtime_python() and the
# app-requirements skip in bootstrap.install_comfyui(). Every extra branch
# is another way for the shipped binary to behave differently from a dev run.
FROZEN = "__compiled__" in globals()

# ── Disk layout ───────────────────────────────────────────────────────────────
# The pod filesystem is ephemeral: the ComfyUI install, model weights,
# generated images and logs all live under one base directory and are lost
# when the pod is destroyed. Override with the KREA2_BASE_DIR env var.
# Under --onefile PROJECT_DIR is Nuitka's temp extraction dir, which is
# correct: build.sh bundles requirements.txt alongside the code.
# BASE_DIR is unaffected — it is absolute, so models outlive the extraction.
PROJECT_DIR = Path(__file__).resolve().parent
# The pricing page's showcase copy — prose only, a few kilobytes, bundled
# because it is what decides whether the section renders at all. Under
# --onefile this resolves inside the extraction dir; build.sh bundles it
# with --include-data-files. A build that forgets it still starts, and the
# pricing page simply loses the section (see showcase.py).
#
# The *pictures* it names are not here. They live in the R2 bucket below,
# which is what keeps a page full of screenshots out of the binary.
ASSETS_DIR = PROJECT_DIR / "assets"
# .resolve() is load-bearing, not tidiness: a relative KREA2_BASE_DIR
# (KREA2_BASE_DIR=./tmp, natural on a dev box) would otherwise be resolved
# by each process against its own cwd. ComfyUI runs with cwd=COMFY_DIR
# (comfy.py) and is handed --output-directory as a string, so it would
# write to <cwd>/tmp/ComfyUI/tmp/output while this process — the mkdir
# below, the gallery scan, the "saved under ..." status line — all meant
# <cwd>/tmp/output. Images land somewhere real and the app cannot find
# them. Absolute here means every consumer reads the same path.
BASE_DIR = Path(
    os.environ.get("KREA2_BASE_DIR", "/workspace/krea2")
).expanduser().resolve()
TEMP_DIR = BASE_DIR     # ComfyUI install + model weights
WORKING_DIR = BASE_DIR  # generated images + logs
COMFY_DIR = TEMP_DIR / "ComfyUI"
MODELS_DIR = TEMP_DIR / "models"
OUTPUT_DIR = WORKING_DIR / "output"
COMFY_LOG = WORKING_DIR / "comfyui.log"

COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

# Unload the previous models when a job needs different base weights.
# ComfyUI keeps what it loaded until memory pressure evicts it, so without
# this a swap briefly holds two full model sets — the moment where the
# server gets OOM-killed once several multi-GB UNets are in rotation
# (Krea 2 V1 turbo/raw plus V2 turbo/raw, plus Wan). Freeing at
# the boundary caps the peak at one set and costs only the reload that a
# swap already pays for. Set KREA2_KEEP_MODELS_LOADED=1 to turn it off on
# a machine with room to spare, where keeping models warm is faster.
FREE_ON_SWAP = not os.environ.get("KREA2_KEEP_MODELS_LOADED")

# SageAttention: faster, slightly approximate attention kernels, used by
# every ComfyUI instance once bootstrap.install_sageattention has proved
# they run on this GPU (Linux, torch 2.8.0 or 2.11.0, an A100/A40/L40S/
# H100/RTX 50xx class card). On by default, as in the MiniMax template; set
# KREA2_SAGE_ATTENTION=0 to run with PyTorch attention instead — the thing
# to try first if a tab's output looks wrong on a card where it did not.
SAGE_ATTENTION = os.environ.get("KREA2_SAGE_ATTENTION", "1").strip().lower() \
    not in ("0", "false", "no", "off")

# ── Model selection ───────────────────────────────────────────────────────────
# Variant-level defaults; a registry entry below can override them per-model.
# "turbo" = distilled models: few steps, CFG 1.0
# "raw"   = undistilled models: many steps, CFG 3.5-4.5 (much slower)
VARIANT_DEFAULTS = {
    "turbo": {"steps": 8, "cfg": 1.0},
    "raw": {"steps": 50, "cfg": 4.0},
}

HF_MODEL_REPO = "Comfy-Org/Krea-2"
TEXT_ENCODER_FILE = "qwen3vl_4b_fp8_scaled.safetensors"      # ~5.2 GB
VAE_FILE = "qwen_image_vae.safetensors"                      # ~0.25 GB

# Registry of selectable Krea 2 diffusion models (UNets) — same spirit as
# the LoRA lists further down: add an entry, restart (downloads are
# idempotent and a failed one never aborts setup), and it appears in the
# Model dropdown of the generate / edit / inpaint tabs and as "model" in
# JSON batch jobs. The FIRST entry is the default. Fields:
#   name            — unique label shown in the UI dropdown
#   file            — filename saved under models/diffusion_models/
#   variant         — "turbo" or "raw"; supplies the step/CFG defaults
#                     from VARIANT_DEFAULTS when the model is selected
#   steps, cfg      — optional per-model overrides of those defaults
#   hf_path         — repo path inside HF_MODEL_REPO to download, OR
#   civitai_version — CivitAI model *version* id: the number in
#                     civitai.com/api/download/models/<id>, which is also
#                     the part after the @ in an AIR urn like
#                     urn:air:krea2:unet:civitai:2762538@3118978
#                     (most downloads need CIVITAI_TOKEN)
#   trigger         — optional trigger words, auto-prepended to the prompt
#                     whenever this model is used (generate/inpaint/JSON;
#                     not in Edit instructions)
KREA2_MODELS = [
    {
        "name": "Krea 2 Turbo (official)",
        "file": "krea2_turbo_fp8_scaled.safetensors",   # ~13.1 GB
        "variant": "turbo",
        "hf_path": "diffusion_models/krea2_turbo_fp8_scaled.safetensors",
    },
    # Raw is deliberately not offered here — this tab is turbo-only by
    # plan design. Krea 2 Raw is still offered on the V2 tab (V2_MODELS
    # below), whose download_v2_models() fetches it independently of this
    # list, so commenting this out is a dropdown-only change: nothing
    # about what gets downloaded moves.
    # {
    #     "name": "Krea 2 Raw (official)",
    #     "file": "krea2_raw_fp8_scaled.safetensors",     # ~13.1 GB
    #     "variant": "raw",
    #     "hf_path": "diffusion_models/krea2_raw_fp8_scaled.safetensors",
    # },
    # {
    #     "name": "FinePn V2 (amateur phone photo)",
    #     "file": "Krea2_FinePornV2_FP8.safetensors",       # ~12.2 GB
    #     "variant": "turbo",
    #     "civitai_version": 3118978,
    #     "trigger": "this is an amateur photo taken from smartphone, "
    #                "casual photo",
    # },
]

# The default model's variant (first registry entry) — used for logging.
KREA2_VARIANT = KREA2_MODELS[0].get("variant", "turbo")

# Abliterated (uncensored) text encoder: its shards are downloaded from this
# repo and merged into a single ComfyUI-loadable file. When the merged file
# exists the workflow uses it; otherwise TEXT_ENCODER_FILE (downloaded as a
# fallback) is used instead.
ABLITERATED_ENCODER_REPO = "huihui-ai/Huihui-Qwen3-VL-4B-Instruct-abliterated"
ABLITERATED_ENCODER_FILE = "qwen3vl_4b_abliterated.safetensors"

# Instruction-based image editing ("upload a photo, describe the change"):
# the community Krea 2 Identity Edit LoRA plus the ComfyUI-Krea2Edit node
# pack feed the source image into the model itself — as in-context VAE
# latents and through the Qwen3-VL encoder — so edits preserve identity
# instead of repainting from scratch like plain img2img/inpaint.
# Off by default (feature key "krea_edit", ~1.9 GB for the LoRA); the node pack
# is only cloned when it is on, since nothing else uses those two nodes.
#
# Weights and nodes are one unit: v1.2 weights need the v1.2 nodes (they
# supply the FIT reference geometry and the ref_boost dial that
# build_edit_workflow wires), and the v1.2 nodes default fit_mode to "fit",
# which v1/v1.1 weights were not trained for. Bump both together or
# neither — scripts/PINS.json is what holds the node pack still.
KREA2EDIT_NODES_REPO = "https://github.com/lbouaraba/comfyui-krea2edit"
EDIT_LORA_REPO = "conradlocke/krea2-identity-edit"
EDIT_LORA_FILE = "krea2_identity_edit_v1_2.safetensors"  # ~1.83 GB

# Diffusion models come from the KREA2_MODELS registry above; only the
# shared VAE is a fixed download.
HF_MODEL_FILES = [
    f"vae/{VAE_FILE}",
]

# Official Krea 2 style LoRAs from the same HF repo (~0.5 GB each).
# Trim this list to save download time and disk space.
HF_LORA_FILES = [
    "loras/krea2_turbo_lora_rank_64_bf16.safetensors",
    # "loras/krea2_darkbrush.safetensors",
    # "loras/krea2_dotmatrix.safetensors",
    # "loras/krea2_kidsdrawing.safetensors",
    # "loras/krea2_neondrip.safetensors",
    # "loras/krea2_rainywindow.safetensors",
    # "loras/krea2_retroanime.safetensors",
    # "loras/krea2_softwatercolor.safetensors",
    # "loras/krea2_sunsetblur.safetensors",
    # "loras/krea2_vintagetarot.safetensors",
]

# ── Krea 2 V2 (Krea2 advanced turbo/raw text-to-image) ────────────────────────────
# A second, self-contained text-to-image pipeline: the Krea2 advanced
# "KREA 2 TURBO/RAW" workflow, reproduced node-for-node in its own tab. It
# deliberately shares nothing with the tabs above except the text encoder —
# its own UNet quant, its own VAE, its own LoRA stack and its own defaults,
# so tuning one never moves the other.
#
# Three things make it different from the Single tab:
#   • RES4LYF's ClownsharKSampler_Beta replaces KSampler (eta/bongmath and
#     the bong_tangent scheduler have no core equivalent),
#   • an RBG Smart Seed Variance node sits between the positive prompt and
#     the sampler, perturbing the conditioning per seed,
#   • LoRAs apply to the model *and* the CLIP (the source workflow uses
#     rgthree's Power Lora Loader in "Single Strength" mode), unlike the
#     LoraLoaderModelOnly chain the other Krea tabs build.
# On by default (feature key "krea_v2_t2i", ~17 GB); a license that does not grant
# "krea_v2_t2i" skips the downloads, the three node packs and the tab.

# Variant-level defaults, same scheme as VARIANT_DEFAULTS:
# a registry entry picks one with its "variant" field and may
# override any value. `turbo_lora` is the on/off state the Krea 2 Turbo
# LoRA slot takes when the variant is selected — that LoRA *is* the raw
# recipe from the source workflow's companion guide (enable it at 0.6,
# raise steps to 20 and CFG to ~2.5), so the two variants differ by
# exactly the three things that guide lists.
V2_VARIANT_DEFAULTS = {
    "turbo": {"steps": 10, "cfg": 1.0, "turbo_lora": False},
    "raw": {"steps": 20, "cfg": 2.5, "turbo_lora": True},
}

# The Krea 2 Turbo LoRA is slot 1 of V2_LORA_STACK below rather than
# something the builder bolts on. Keeping it a normal, visible slot is what
# stops it being applied twice when a raw run also has it ticked by hand —
# the same "never applied silently" rule the trigger words follow.
V2_TURBO_LORA_FILE = "krea2_turbo_lora_rank_64_bf16.safetensors"
V2_TURBO_LORA_STRENGTH = 0.6

# Selectable models for the V2 tab. Same fields as KREA2_MODELS
# (name / file / variant / optional steps, cfg, turbo_lora overrides /
# hf_path within HF_MODEL_REPO / optional trigger); the first entry is the
# default and is the model the source workflow ships with.
#
# Raw's file (krea2_raw_fp8_scaled) was previously also offered on the
# Krea 2 Turbo tab and shared between the two loops; that tab is
# turbo-only now (plan design), so download_v2_models() is what fetches
# this file — see its docstring for why it does so independently rather
# than assuming another group already did.
V2_MODELS = [
    {
        "name": "Krea 2 Turbo mxfp8 (workflow default)",
        "file": "krea2_turbo_mxfp8.safetensors",        # ~13.5 GB
        "variant": "turbo",
        "hf_path": "diffusion_models/krea2_turbo_mxfp8.safetensors",
    },
    {
        "name": "Krea 2 Raw fp8",
        "file": "krea2_raw_fp8_scaled.safetensors",     # ~13.1 GB, shared
        "variant": "raw",
        "hf_path": "diffusion_models/krea2_raw_fp8_scaled.safetensors",
    },
]

# The workflow's companion guide recommends the Wan 2.1 VAE over the stock
# Qwen image VAE for this pipeline. Different repo, and it is stored under
# vae/wan/ upstream, so it is fetched explicitly rather than through the
# HF_MODEL_FILES list.
V2_VAE_FILE = "wan21-vae.safetensors"                            # ~254 MB
V2_VAE_HF_REPO = "wangkanai/wan21-vae"
V2_VAE_HF_PATH = f"vae/wan/{V2_VAE_FILE}"

# Custom node packs this tab needs. Cloned at bootstrap like Krea2Edit;
# each one is optional in the sense that a failed clone disables only this
# tab. RES4LYF and RBG change the image and have no core equivalent;
# post-processing supplies FilmGrain for the optional grain toggle.
V2_NODE_REPOS = [
    # (custom_nodes dir, git URL, a node class that proves it loaded)
    ("RES4LYF", "https://github.com/ClownsharkBatwing/RES4LYF",
     "ClownsharKSampler_Beta"),
    ("ComfyUI-RBG-SmartSeedVariance",
     "https://github.com/RamonGuthrie/ComfyUI-RBG-SmartSeedVariance",
     "RBG_Smart_Seed_Variance"),
    ("ComfyUI-post-processing-nodes",
     "https://github.com/EllangoK/ComfyUI-post-processing-nodes",
     "FilmGrain"),
]

# The workflow's LoRA stack, in its original order and with its original
# strengths and on/off states. Entries are
# (filename, strength, enabled_by_default, civitai_version_id) — the
# version id is None for LoRAs already fetched from Hugging Face.
# Every strength applies to the model and the CLIP alike (Single Strength).
V2_LORA_STACK = [
    # Slot 1 — toggled on/off by the Model dropdown (see V2_VARIANT_DEFAULTS).
    (V2_TURBO_LORA_FILE, V2_TURBO_LORA_STRENGTH, False, None),
    ("krea2filterbypass3.safetensors", 0.93, True, 3067151),
    ("krea2_Enhancer.safetensors", 0.4, True, 3065628),
    ("Krea2-realism-V2.safetensors", 0.3, True, 3090634),
    # The companion guide links version 3109006 for this LoRA family; the
    # workflow names the file v3.1. If CivitAI serves a different revision
    # the graph still runs — only the filename on disk has to match.
    ("realism_engine_krea2_v3.1.safetensors", 0.6, True, 3109006),
    ("RealisticSnapshotKrea2.safetensors", 0.8, True, 3084537),
    ("purelens_krea2.safetensors", 0.6, True, 3114242),
    ("lenovo_krea2.safetensors", 0.5, True, 3075606),
    ("MysticXXX_KREA2_v3.safetensors", 1.0, False, 3116175),
    ("KNPV4.1_pre.safetensors", 1.0, False, 3085473),
    ("snofs_krea_v1.safetensors", 1.0, False, 3104629),
    ("Halide-v1.safetensors", 1.0, False, 3265522),
    ("Krea2FilterBypass_3vector.safetensors", 1.0, False, 3067151),
    ("Realism_Engine_Krea2_v2.0.safetensors", 1.0, False, 3070702),
    ("SNOFS_Krea2_v1.0.safetensors", 1.0, False, 3072664),
    ("Krea2_AIO_NSFW_v1.0.safetensors", 1.0, False, 3071904),
    ("Realistic_Snapshot_Krea2_v0.5.safetensors", 1.0, False, 3084537),
    ("galaxyace_krea2.safetensors", 1.0, False, 3069544),
    ("HMBody_D_e10.safetensors", 1.0, False, 3160327),
    ("elusarca-photo.safetensors", 1.0, False, 3151907),
    ("Krea2_NSFW_plus.safetensors", 1.0, False, 3084588),
    ("nicegirls_krea2.safetensors", 1.0, False, 3075498),
    ("Krea2-realism-V1.safetensors", 1.0, False, 3066973),
    ("snofs_krea_v1_1.safetensors", 1.0, False, 3104629),
    ("desi-realism-v1.safetensors", 1.0, False, 3194454),
    ("pawg.safetensors", 1.0, False, 3173942),
]

# ClownsharKSampler_Beta settings, straight from the workflow. These are
# the knobs both variants share; steps and cfg are deliberately absent
# because they belong to the model (V2_VARIANT_DEFAULTS) and would
# otherwise be a second source of truth for the same two numbers.
V2_SAMPLER_DEFAULTS = {
    "eta": 0.5,
    "sampler_name": "linear/euler",
    "scheduler": "bong_tangent",
    "steps_to_run": -1,
    "denoise": 1.0,
    "sampler_mode": "standard",
    "bongmath": True,
}
# RES4LYF builds these lists at import time from its own tables, so they
# cannot be enumerated here. The dropdowns accept free text — these are the
# values the workflow ships with plus the node's own defaults.
V2_SAMPLER_NAMES = ["linear/euler", "res_2m", "res_3m", "deis_2m", "euler"]
V2_SCHEDULERS = ["bong_tangent", "beta57", "normal", "karras", "simple"]
V2_SAMPLER_MODES = ["standard", "unsample", "resample"]

# RBG Smart Seed Variance settings, straight from the workflow. The combo
# strings carry emoji and must match the node's option lists character for
# character, or ComfyUI rejects the prompt.
V2_VARIANCE_DEFAULTS = {
    "variance_preset": "🌱 Subtle",
    "fine_tune_variance": 50,
    "model_type": "📸 Krea2 (SingleStream)",
    "fade_curve": "Instant",
    "noise_injection": "Beginning Steps",
    "protect_mode": "🚫 None",
    "protect_regions": "",
    "direction_shift": "🚫 None",
    "shift_strength": 100,
    "variance_schedule": "constant",
    "cutoff_step": 8,
    "total_steps": 20,
    "cutoff_strength": 0.0,
    "vibe_blend": 0.5,
}
V2_VARIANCE_PRESETS = ["❌ Disabled", "🌱 Subtle", "🌿 Balanced", "🪴 Creative",
                       "🌳 Bold", "🌴 Wild", "⚙️ Custom"]
V2_VARIANCE_MODEL_TYPES = [
    "⚡ Z-Image Turbo", "📸 Krea2 (SingleStream)", "🖼️ Qwen-Image",
    "🔮 Flux (Dev/Schnell)", "🎨 Chroma HD", "🧧 ERNIE-Image",
    "🔮 Ideogram 4.0", "🖌️ SDXL", "🎬 Wan2.2", "⚙️ Other",
]
V2_VARIANCE_SCHEDULES = ["constant", "decreasing", "step_cutoff",
                         "tiered_release", "hard_lock"]

# Post-processing. Both nodes are bypassed (mode 4) in the source workflow,
# so both toggles start off and the tab reproduces it exactly as shipped.
V2_SHARPEN_DEFAULTS = {"sharpen_radius": 1, "sigma": 0.35, "alpha": 1.0}
V2_FILMGRAIN_DEFAULTS = {"intensity": 0.05, "scale": 1.0, "temperature": 0.0,
                         "vignette": 0.0}

# The workflow sizes its latent with a ResolutionSelector feeding
# EmptyLatentImage. That is pure integer plumbing, so the same arithmetic
# runs in Python here (megapixels × 1024², the core ComfyUI convention) and
# the tab shows the width/height it resolved to. 3:4 at 1.5 MP snapped to
# /8 gives the workflow's 1088×1448.
V2_ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "3:4 (Portrait Standard)": (3, 4),
    "2:3 (Portrait)": (2, 3),
    "9:16 (Portrait Tall)": (9, 16),
    "4:3 (Landscape Standard)": (4, 3),
    "3:2 (Landscape)": (3, 2),
    "16:9 (Landscape Wide)": (16, 9),
}
V2_DEFAULT_ASPECT = "3:4 (Portrait Standard)"
V2_DEFAULT_MEGAPIXELS = 1.5
V2_DEFAULT_MULTIPLE = 8

# The workflow's Negatives node, verbatim.
V2_DEFAULT_NEGATIVE = (
    "This low quality greyscale unfinished sketch is inaccurate and flawed. "
    "The image is very blurred and lacks detail with excessive chromatic "
    "aberrations and artifacts. The image is overly saturated with excessive "
    "bloom. It has a toony aesthetic with bold outlines and flat colors.\n\n"
    "Fake, unreal, wrong anatomy, big eyes, bad anatomy, extra limbs, "
    "missing fingers, fused fingers, poorly drawn hands, poorly drawn face, "
    "mutated hands, long neck, extra arms, extra legs, extra fingers, "
    "disfigured, malformed limbs, missing limbs, skinny, fat, jpeg artifacts, "
    "watermark, signature, text, logo, exaggerated features, unnatural skin "
    "tone, bokeh, deformed, lowres, out of frame, aliasing, blurry "
    "background, doll-like skin, plastic texture, uncanny valley, incorrect "
    "proportions, duplicate body parts, unnatural poses, poor anatomical "
    "proportions, pubic hair, digital art, drawing, cartoon, large breasts, "
    "huge breasts, faded colors, pastel tones, beigesthetic, flat lighting, "
    "overexposed whites, AI glow, plastic skin, skincare-ad look, studio "
    "lighting, cartoon or 3D style, text watermark or logos, cleft chin, "
    "indented chin, smooth groin, Barbie-doll anatomy, blurred genitalia, "
    "aged, wrinkles, massive breasts, 28G, airbrushed, plastic skin, clothes, "
    "bra, underwear, iPhone, smartphone, wide angle distortion, tattoos, "
    "piercings, excessive cum."
)

# ── Krea 2 V2 Edit (instruction editing on the V2 spine) ──────────────────────
# The ✨ Krea2 Edit tab's recipe — the Identity Edit LoRA plus the
# ComfyUI-Krea2Edit nodes — rebuilt on the V2 pipeline instead of the
# Krea 2 v1 one: V2_MODELS, the Wan 2.1 VAE, the 11-slot model+CLIP LoRA
# stack, ClownsharKSampler_Beta and Smart Seed Variance. Everything above
# is reused verbatim, so tuning the V2 tab tunes this one too and there is
# no second copy of those numbers to drift.
#
# The VAE swap is safe here specifically because the two are the same
# family: Qwen-Image's VAE is a Wan 2.1 derivative with the same 16-channel
# latent space, so the source latents Krea2EditModelPatch prepends as
# in-context tokens still mean what the Identity Edit LoRA was trained to
# read. A VAE from any other family would not be substitutable this way.
#
# Off by default (feature key "krea_v2_edit"); it needs the V2 downloads
# (~17 GB), the Identity Edit LoRA (~1.9 GB) and both sets of node packs.

# The Identity Edit LoRA bleeds and duplicates content above ~2 MP, and the
# cap applies to the source as much as the output — this tab derives one
# from the other, and VAE-encoding a 12 MP phone photo as a reference is a
# needless 12 MP of VRAM.
V2_EDIT_MAX_PIXELS = 2_000_000

# 384-768 is the LoRA's trained grounding range; above it the model starts
# emitting duplicated "double picture" compositions. ref_boost is how hard
# the edit holds the reference: 1.0 neutral, ~4 strong likeness, past ~10
# removals stop working. Same numbers the v1 Edit tab ships.
V2_EDIT_DEFAULT_GROUNDING = 768
V2_EDIT_DEFAULT_REF_BOOST = 4.0

# Krea2EditModelPatch's reference geometry. "fit" is the v1.2 behaviour
# (resample the reference in pixel space, so a source whose aspect differs
# from the output is fitted rather than stretched); the legacy value is
# kept selectable for anyone running older weights.
V2_EDIT_FIT_MODES = ["fit", "crop (legacy)"]

# ── Wan 2.2 image-to-video ────────────────────────────────────────────────────
# Two model families, switchable per-job in the Video tab:
#   • I2V A14B — two 14B "experts" (a high-noise model for the early
#     denoising steps, a low-noise one for the late steps; ~14.3 GB each,
#     fp8) at 16 fps. Best quality; turbo mode uses the lightx2v
#     "Lightning" 4-step LoRAs (~1.2 GB each).
#   • TI2V 5B — a single dense 5B model (~10 GB fp16) with its own
#     higher-compression Wan 2.2 VAE (~1.4 GB) at 24 fps. Lower quality
#     than 14B but much lighter in VRAM (no expert swap mid-run), so it
#     suits the parallel mode well.
# Both share the UMT5-XXL text encoder (~6.7 GB). Everything comes from
# the same Comfy-Org repackaged repo — ~49 GB in total on top of the Krea
# downloads. Off by default (feature key "wan_i2v"); a license granting "wan_i2v"
# fetches all of it and shows the Video tab, and nothing else changes.
WAN_HF_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
WAN_HIGH_UNET = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
WAN_LOW_UNET = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
WAN_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
WAN_LIGHTNING_HIGH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
WAN_LIGHTNING_LOW = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
WAN_5B_UNET = "wan2.2_ti2v_5B_fp16.safetensors"
WAN_5B_VAE = "wan2.2_vae.safetensors"

# Repo-relative → local (all under split_files/ upstream, flattened locally).
WAN_HF_FILES = [
    f"diffusion_models/{WAN_HIGH_UNET}",
    f"diffusion_models/{WAN_LOW_UNET}",
    f"diffusion_models/{WAN_5B_UNET}",
    f"text_encoders/{WAN_TEXT_ENCODER}",
    f"vae/{WAN_VAE}",
    f"vae/{WAN_5B_VAE}",
    f"loras/{WAN_LIGHTNING_HIGH}",
    f"loras/{WAN_LIGHTNING_LOW}",
]

# Same idea as the Krea turbo/raw variants, but both live behind one Mode
# radio in the Video tab because they share the same base models:
#   turbo = Lightning distillation LoRAs, 4 steps (2 high + 2 low), CFG 1.0
#   raw   = no LoRA, 20 steps (10 + 10), CFG 3.5 — ~5× slower, a bit sharper
# motion. `shift` is the ModelSamplingSD3 sigma shift each mode was tuned for.
WAN_MODE_DEFAULTS = {
    "turbo": {"steps": 4, "cfg": 1.0, "shift": 5.0, "lightning": True},
    "raw": {"steps": 20, "cfg": 3.5, "shift": 8.0, "lightning": False},
}
WAN_VARIANT = "turbo"

# The 5B model has no Lightning distillation — one "standard" schedule
# (the official template: 20 steps, CFG 5, shift 8, 24 fps).
WAN_5B_DEFAULTS = {"steps": 20, "cfg": 5.0, "shift": 8.0}

WAN_FPS = 16                  # the A14B models are trained at 16 fps
WAN_5B_FPS = 24               # TI2V 5B is trained at 24 fps
WAN_MAX_SECONDS = 5.0         # 81 frames (14B) / 121 frames (5B)
# Target pixel areas; the actual size keeps the source image's aspect ratio.
WAN_RESOLUTIONS = {
    "480p (faster)": 832 * 480,
    "720p (sharper, ~3× slower)": 1280 * 720,
}
WAN_DEFAULT_RESOLUTION = "480p (faster)"

# Standard Wan negative prompt (from the official templates). Only used
# when CFG > 1, i.e. in raw mode.
WAN_DEFAULT_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，"
    "手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)

# With KREA2_WAN_PARALLEL=1 a second ComfyUI instance serves the Video tab
# on its own port, so a quick Krea image never queues behind a long video
# render. Each instance is told to leave VRAM for the other via
# --reserve-vram; on a 48 GB A40 the defaults give Krea ~22 GB and Wan
# ~26 GB. Without the flag (default) both tabs share one ComfyUI queue —
# zero OOM risk, but jobs run strictly one after another. Only has any
# effect when the "wan_i2v" feature is on: app.py checks both before paying
# for a second instance.
WAN_PARALLEL = bool(os.environ.get("KREA2_WAN_PARALLEL"))
WAN_COMFY_PORT = 8189
WAN_COMFY_LOG = WORKING_DIR / "comfyui_wan.log"
KREA_RESERVE_VRAM_GB = float(os.environ.get("KREA2_MAIN_RESERVE_VRAM", 26))
WAN_RESERVE_VRAM_GB = float(os.environ.get("KREA2_WAN_RESERVE_VRAM", 22))

# ── MiniMax H3 — video with sound (image-to-video and text-to-video) ─────────
# MiniMax H3 is a packed audio+video DiT: one sampler pass produces the
# frames and the soundtrack together, so every clip comes back with audio,
# which Wan never did. Two tabs share one graph — the core
# MiniMaxH3ImageToVideo node takes an *optional* first frame, so text-to-
# video is the same workflow with LoadImage left out. Two feature keys
# ("minimax_i2v", "minimax_t2v"), one asset group ("minimax") between them.
#
# Transcribed from the two Custom Prompt workflows in
# hearmeman/comfyui-minimax-template:v8 (kept under tmp/minimax-reference/,
# not shipped). Everything that graph does is core ComfyUI — the rgthree
# Power Lora Loader it carried was empty, its KJNodes preview override is
# cosmetic, and CreateVideo + SaveVideo already write Wan's MP4s — so no
# node pack is installed for this. What it does need is **ComfyUI v0.34.0
# or later**, where these nodes first ship: scripts/PINS.json moved from
# v0.29.0 to v0.34.0 for exactly this reason, bootstrap.repin_checkout moves
# an existing checkout there, and comfy.verify_core_node says so at startup
# when one still predates it.
#
# The quant is the template's default: the int8 "convrot" diffusion model
# and text encoder. It is the largest download in the app (~56 GB), and 27
# GB of that is the 32B Qwen3-VL text encoder. The fp8 and bf16 builds in
# the same repo are the same graph under another filename and are not
# offered — each would be another 21-66 GB on the volume.
MINIMAX_HF_REPO = "Comfy-Org/MiniMax-H3"
MINIMAX_UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"        # ~21.0 GB
MINIMAX_TEXT_ENCODER = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"  # ~27.1 GB
MINIMAX_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"               # ~5.2 GB
MINIMAX_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"               # ~0.6 GB
# The repo's layout is ComfyUI's, so these download straight into place.
MINIMAX_HF_FILES = [
    f"diffusion_models/{MINIMAX_UNET}",
    f"text_encoders/{MINIMAX_TEXT_ENCODER}",
    f"vae/{MINIMAX_VIDEO_VAE}",
    f"vae/{MINIMAX_AUDIO_VAE}",
]
# The 8-step turbo LoRA, always on — the template titles it "Always On,
# Don't Touch" and the sampler settings below are its recipe. It lives in
# lightx2v's repo rather than Comfy-Org's: the 768p 8-step build is only
# published there.
MINIMAX_TURBO_LORA_REPO = "lightx2v/Minimax-h3-Turbo"
MINIMAX_TURBO_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"  # ~2.0 GB
MINIMAX_TURBO_LORA_STRENGTH = 0.8

# The sampler block, verbatim from the template: BasicScheduler "simple" at
# 8 steps, euler, no CFG — a BasicGuider, because like Flux the model is
# guidance-distilled and has no negative prompt. The template also carries
# an ExtendIntermediateSigmas node, but bypassed (mode 4): it was the recipe
# for the old 4-step v0.1 turbo LoRA, and the template switched it off in
# the same commit that moved to this 8-step v1.0 LoRA. So the sigmas go to
# the sampler as the scheduler makes them.
MINIMAX_DEFAULTS = {"steps": 8, "sampler": "euler", "scheduler": "simple"}

# Geometry. 24 fps, and the frame count has to sit on the model's "17k + 5"
# grid: 124 frames is 5 s and the node's default, 362 is 15 s and the top
# of the trained range. Sides are multiples of 32.
MINIMAX_FPS = 24
MINIMAX_FRAME_STEP = 17
MINIMAX_FRAME_OFFSET = 5
MINIMAX_MIN_SECONDS = 5.0
MINIMAX_MAX_SECONDS = 15.0
MINIMAX_DEFAULT_SECONDS = 5.0
MINIMAX_CANVAS_MULTIPLE = 32
MINIMAX_MIN_SIDE = 256
MINIMAX_MAX_SIDE = 1536
# Three canvas rules, chosen per job with the Resolution radio — see
# workflow_minimax.resolve_size. "Standard" is the template's
# ResolutionSelector at 0.7 MP, and the default. "Native" is the model's
# own canvas, which the node file calls adapt_canvas: a 768 short edge with
# the area capped at 768 × 1344 — 40-50% more pixels, slower, and what the
# 768p turbo LoRA was trained against. Both take the source (or chosen)
# aspect, but round and clamp each side on its own, which bends it
# slightly (a panorama badly), and the node stretches the picture to
# match. "Match image" is opt-in and image-only: the upload's own size in
# 32s that keep its shape, scaled down only past native's area cap, with
# the upload centre-cropped to that shape so nothing is stretched. The
# text tab has no picture, so it offers the first two only.
MINIMAX_STANDARD_MEGAPIXELS = 0.7
MINIMAX_NATIVE_SHORT_EDGE = 768
MINIMAX_NATIVE_MAX_PIXELS = 768 * 1344
MINIMAX_MATCH_IMAGE = "Match image (its own size, capped near 1 MP)"
MINIMAX_RESOLUTIONS = {
    "Standard (0.7 MP — the template default)": "standard",
    "Native 768p (short edge 768, slower)": "native",
    MINIMAX_MATCH_IMAGE: "source",
}
MINIMAX_T2V_RESOLUTIONS = [label for label, rule in MINIMAX_RESOLUTIONS.items()
                           if rule != "source"]
MINIMAX_DEFAULT_RESOLUTION = "Standard (0.7 MP — the template default)"
# The text-to-video tab has no picture to take an aspect from, so it
# offers ResolutionSelector's own list, labels verbatim. The template
# shipped with 9:16 selected.
MINIMAX_ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}
MINIMAX_DEFAULT_ASPECT = "9:16 (Portrait Widescreen)"
# The core node both tabs are built on, and the ComfyUI release it arrived
# in. app.py asks the running server for it whenever either tab is granted.
MINIMAX_NODE = "MiniMaxH3ImageToVideo"
MINIMAX_COMFYUI_MIN = "v0.34.0"

FLUX_HF_REPO = "Comfy-Org/flux2-dev"
FLUX_VAE = "flux2-vae.safetensors"                           # ~0.34 GB

# ── CivitAI LoRAs ─────────────────────────────────────────────────────────────
# Entries are (model_version_id, filename_to_save_as). The version id is the
# number in the CivitAI download URL: civitai.com/api/download/models/<id>
# Most CivitAI downloads require an API token (set the CIVITAI_TOKEN env
# var). Add or remove entries freely — a failed LoRA download is logged
# and skipped, it never aborts the setup.
CIVITAI_LORAS = [
    (3067151, "Krea2FilterBypass_3vector.safetensors"),
    (3070702, "Realism_Engine_Krea2_v2.0.safetensors"),
    (3072664, "SNOFS_Krea2_v1.0.safetensors"),
    (3090634, "Krea2-realism-V2.safetensors"),
    (3071904, "Krea2_AIO_NSFW_v1.0.safetensors"),
    (3084537, "Realistic_Snapshot_Krea2_v0.5.safetensors"),
    (3069544, "galaxyace_krea2.safetensors"),
    (3160327, "HMBody_D_e10.safetensors"),
    (3151907, "elusarca-photo.safetensors"),
    (3084588, "Krea2_NSFW_plus.safetensors"),
    (3075498, "nicegirls_krea2.safetensors"),
    (3066973, "Krea2-realism-V1.safetensors"),
    (3075606, "lenovo_krea2.safetensors"),
    (3114242, "purelens_krea2.safetensors"),
    (3104629, "snofs_krea_v1_1.safetensors"),
    (3085473, "KNPV4.1_pre.safetensors"),
    (3194454, "desi-realism-v1.safetensors"),
    (3173942, "pawg.safetensors"),
    (3067151, "krea2filterbypass3.safetensors"),
    (3065628, "krea2_Enhancer.safetensors"),
    # The companion guide links version 3109006 for this LoRA family; the
    # workflow names the file v3.1. If CivitAI serves a different revision
    # the graph still runs — only the filename on disk has to match.
    (3109006, "realism_engine_krea2_v3.1.safetensors"),
    (3084537, "RealisticSnapshotKrea2.safetensors"),
    (3116175, "MysticXXX_KREA2_v3.safetensors"),
    (3104629, "snofs_krea_v1.safetensors"),
    (3265522, "Halide-v1.safetensors"),
]

# LoRAs pre-selected in the UI's three slots (generate / edit / inpaint tabs).
# Entries are (filename, default weight); a file that failed to download is
# silently skipped and the slot falls back to "None".
DEFAULT_LORAS = [
    ("HMBody_D_e10.safetensors", 0.8),
    ("Realism_Engine_Krea2_v2.0.safetensors", 0.4),
    ("galaxyace_krea2.safetensors", 0.8),
]

RESOLUTION_PRESETS = {
    "1024×1024 (Square)": (1024, 1024),
    "1216×832 (Landscape)": (1216, 832),
    "832×1216 (Portrait)": (832, 1216),
    "1344×768 (Wide)": (1344, 768),
    "768×1344 (Tall)": (768, 1344),
    "1536×1024 (Landscape XL)": (1536, 1024),
    "1024×1536 (Portrait XL)": (1024, 1536),
}
DEFAULT_RESOLUTION = "1024×1536 (Portrait XL)"

# Valid native ComfyUI samplers that work well with Krea 2 ("simple" scheduler).
SAMPLERS = ["er_sde", "euler", "euler_ancestral", "dpmpp_2m", "res_multistep"]

# ── Licensing ─────────────────────────────────────────────────────────────────
# One customer key allows a fixed number of concurrent running instances.
# The key is per-customer and set on the pod like the tokens below.
#
# The endpoint is assembled here from a bare deployment id rather than read
# as a whole URL, for two reasons. An endpoint that can be repointed is a
# licence check that can be answered by any server the customer chooses;
# accepting only the id means the host can never be anything other than a
# vercel.app subdomain. And the variable is named for what it looks like on
# a pod — a node tag, sitting among RunPod's own — rather than for what it
# does, so the licensing path is not the first thing read in the env panel.
#
# There is deliberately no fallback. The id is not compiled into the binary,
# so a pod that does not carry it cannot check out a seat at all.
_NODE_TAG = (os.environ.get("KREA2_NODE_TAG") or "").strip().lower()
# One DNS label, nothing more. A dot, a slash, a colon or a port is how a
# tag would smuggle in a different host, so reject the value outright rather
# than strip the offending characters and use whatever is left.
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{6,61}[a-z0-9]", _NODE_TAG):
    _NODE_TAG = ""
# Empty when the tag is missing or malformed. licensing.acquire_or_exit()
# turns that into the stop message; nothing else may call the API without
# going through it.
LICENSE_API_URL = f"https://{_NODE_TAG}.vercel.app" if _NODE_TAG else ""
LICENSE_KEY = os.environ.get("KREA2_LICENSE_KEY") or None
# How long the app keeps running when the license server is unreachable.
# Long enough that an outage does not kill a video render mid-way, short
# enough that a pod cut off from the server does not run indefinitely.
LICENSE_GRACE_SECONDS = float(os.environ.get("KREA2_LICENSE_GRACE", 1800))

# Optional. The mirror repos (see mirror.py) are public, so a customer pod
# pulls every mirrored weight anonymously; this is only needed for gated
# upstream repos. Keep it *unset* rather than wrong — a stale token turns
# an anonymous download into a 401, which reads as "the mirror is missing
# files" when the real problem is the credential.
HF_TOKEN = os.environ.get("HF_TOKEN") or None
# Only needed when a download falls through to CivitAI — i.e. when the
# mirror could not serve it. A healthy mirrored pod never uses this.
CIVITAI_TOKEN = os.environ.get("CIVITAI_TOKEN") or None

# ── Showcase images ───────────────────────────────────────────────────────────
# Where the pricing page's screenshots are served from — a public Cloudflare
# R2 bucket, holding the same folder layout showcase.json names:
#
#     https://pub-<hash>.r2.dev/krea_edit/compare-a/before.webp
#
# Note the host is the subdomain Cloudflare assigns when public access is
# switched on, not one named after the bucket — enabling it is what mints
# the value, so there is nothing to guess at from the bucket name. A custom
# domain works the same way and is the better choice in production, since
# r2.dev is rate limited and Cloudflare does not intend it for live traffic.
#
# They are fetched by the customer's browser, not by this app, so nothing
# here downloads them and no credential is involved: the bucket has to be
# public (an r2.dev subdomain or a custom domain). Plain <img> loads need no
# CORS header either — only fetch() would.
#
# The alternative was compiling them into the binary, which put every
# screenshot into a onefile build that is re-extracted on every launch. This
# way a new picture is an upload, not a release.
#
# Empty is a supported state, not a broken one: the section still renders,
# in full, with every picture as a placeholder tile naming the file it wants
# (see showcase.py). That is also what a dev checkout gets for free.
SHOWCASE_BASE_URL = (os.environ.get("KREA2_SHOWCASE_URL") or "").strip()
# https only, and no query or fragment: the value is pasted into an env
# panel rather than reviewed in a diff, and it ends up as the prefix of
# every image src on the page. Anything else is dropped with a warning
# rather than used — the page then reads exactly as it does with no bucket
# configured at all.
if SHOWCASE_BASE_URL and not SHOWCASE_BASE_URL.lower().startswith("https://"):
    log.warning("KREA2_SHOWCASE_URL is not an https:// URL — ignoring it. "
                "The pricing showcase will render with placeholder tiles.")
    SHOWCASE_BASE_URL = ""
SHOWCASE_BASE_URL = SHOWCASE_BASE_URL.split("?")[0].split("#")[0].rstrip("/")

for _dir in (TEMP_DIR, MODELS_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# Which features are on is logged by app.py once features.py has resolved
# them — this module deliberately does not know, so that importing config
# from features.py stays acyclic.
log.info(
    "Variant: Krea 2 %s · models → %s · images → %s",
    KREA2_VARIANT, MODELS_DIR, OUTPUT_DIR,
)
