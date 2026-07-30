"""Krea 2 on RunPod — configuration.

Everything user-tunable lives in this module: paths, model registries,
LoRA lists and (optional) access tokens. In section order:

    Build mode          FROZEN
    Disk layout         BASE_DIR and everything under it, ComfyUI's port
    Model selection     Krea 2 base models, encoder, LoRAs — the
                        Single / Edit / Inpaint tabs
    Krea 2 V2           the Krea2 advanced pipeline, self-contained
    Wan 2.2             image-to-video (+ the parallel-instance knobs)
    Flux 2              text-to-image
    Klein Edit          Flux 2 Klein 9B image editing, self-contained
    ReActor             face swap
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
    KREA2_WAN_PARALLEL          second ComfyUI instance for video
    KREA2_MAIN_RESERVE_VRAM     GB left for Wan by the image instance
    KREA2_WAN_RESERVE_VRAM      GB left for images by the video instance
    KREA2_LICENSE_KEY           the customer key (required)
    KREA2_NODE_TAG              the deployment id the key checks in against
    KREA2_LICENSE_GRACE         seconds tolerated with no licence server
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
# correct: build.sh bundles deps/ and requirements.txt alongside the code.
# BASE_DIR is unaffected — it is absolute, so models outlive the extraction.
PROJECT_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("KREA2_BASE_DIR", "/workspace/krea2"))
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
# (Krea 2 V1 turbo/raw plus V2 turbo/raw, plus Flux and Wan). Freeing at
# the boundary caps the peak at one set and costs only the reload that a
# swap already pays for. Set KREA2_KEEP_MODELS_LOADED=1 to turn it off on
# a machine with room to spare, where keeping models warm is faster.
FREE_ON_SWAP = not os.environ.get("KREA2_KEEP_MODELS_LOADED")

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
    {
        "name": "Krea 2 Raw (official)",
        "file": "krea2_raw_fp8_scaled.safetensors",     # ~13.1 GB
        "variant": "raw",
        "hf_path": "diffusion_models/krea2_raw_fp8_scaled.safetensors",
    }
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
KREA2EDIT_NODES_REPO = "https://github.com/lbouaraba/comfyui-krea2edit"
EDIT_LORA_REPO = "conradlocke/krea2-identity-edit"
EDIT_LORA_FILE = "krea2_identity_edit_v1_1.safetensors"  # ~1.83 GB

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

# Variant-level defaults, same scheme as VARIANT_DEFAULTS / FLUX_VARIANT_
# DEFAULTS: a registry entry picks one with its "variant" field and may
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
# Raw costs no extra disk: krea2_raw_fp8_scaled is already in KREA2_MODELS,
# and the downloads are keyed on the destination path, so whichever tab
# asks for it first fetches it and the other finds it cached.
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

# ── Flux 2 ────────────────────────────────────────────────────────────────────
# The Flux tab generates images with Flux 2 Dev (32B, guidance-distilled:
# no CFG/negative prompt — a FluxGuidance value steers it instead). The
# fp8 model is ~35.5 GB and the Mistral text encoder ~18 GB, so on a 48 GB
# A40 run Flux WITHOUT KREA2_WAN_PARALLEL (it needs nearly the whole GPU)
# and expect a slow model swap when switching between Flux and Krea jobs.
# Off by default (feature key "flux_t2i"); a license granting "flux_t2i" fetches
# its ~57 GB and shows the tab.
FLUX_HF_REPO = "Comfy-Org/flux2-dev"
FLUX_TEXT_ENCODER = "mistral_3_small_flux2_fp8.safetensors"  # ~18.0 GB
FLUX_VAE = "flux2-vae.safetensors"                           # ~0.34 GB
FLUX_TURBO_LORA = "Flux2TurboComfyv2.safetensors"            # ~2.8 GB

# Shared Flux files (all under split_files/ in the repo, flattened locally).
FLUX_HF_FILES = [
    f"text_encoders/{FLUX_TEXT_ENCODER}",
    f"vae/{FLUX_VAE}",
    f"loras/{FLUX_TURBO_LORA}",
]

# Variant-level defaults for Flux models. "turbo" applies the official
# Turbo distillation LoRA (8 steps); "raw" is the undistilled 20-step
# schedule (~2.5× slower). Add new keys ("hyper", ...) freely — a model
# entry picks one via its "variant" field and can override any value.
FLUX_VARIANT_DEFAULTS = {
    "turbo": {"steps": 8, "guidance": 4.0, "turbo_lora": True},
    "raw": {"steps": 20, "guidance": 4.0, "turbo_lora": False},
}

# Registry of selectable Flux 2 models — same scheme as KREA2_MODELS
# (name / file / variant / optional steps, guidance, turbo_lora overrides /
# hf_path within FLUX_HF_REPO or civitai_version / optional trigger).
# The first entry is the default. Both official entries share one file:
# turbo is the same weights plus the Turbo LoRA at generation time.
FLUX_MODELS = [
    {
        "name": "Flux 2 Dev Turbo (official)",
        "file": "flux2_dev_fp8mixed.safetensors",   # ~35.5 GB
        "variant": "turbo",
        "hf_path": "diffusion_models/flux2_dev_fp8mixed.safetensors",
    },
    {
        "name": "Flux 2 Dev Raw (official)",
        "file": "flux2_dev_fp8mixed.safetensors",   # same file, no Turbo LoRA
        "variant": "raw",
        "hf_path": "diffusion_models/flux2_dev_fp8mixed.safetensors",
    },
]

# Flux LoRAs live in their own subfolder (loras/flux2/) so they never mix
# with the Krea 2 LoRA dropdowns — the architectures are incompatible.
# Entries are (civitai_version_id, filename_to_save_as), exactly like
# CIVITAI_LORAS below; files dropped into loras/flux2/ by hand also appear
# after a rescan.
FLUX_LORA_SUBDIR = "flux2"
FLUX_CIVITAI_LORAS = [
    # (1234567, "some_flux2_lora.safetensors"),
]

# ── Flux 2 Klein 9B Edit ──────────────────────────────────────────────────────
# The Klein Edit tab is the Klein i2i "FLUX.2 KLEIN 9B EDIT v1.3" workflow
# ported node-for-node (workflow_klein.py). It edits images rather than
# generating them: one or two sources are VAE-encoded and attached to the
# conditioning as ReferenceLatents, so the model works from what it is shown
# and the prompt describes the change ("the person from image 1 wearing the
# hat from image 2").
#
# It shares nothing with the Flux 2 tab but the VAE file. Klein 9B is a much
# smaller model — 9.4 GB against Flux 2 Dev's 35.5 GB — with its own Qwen3-8B
# text encoder, so the two never appear in each other's dropdowns and running
# this tab does not require the ~57 GB the Flux tab needs. Off by default
# (feature key "klein_i2i"); a license granting "klein_i2i" fetches its ~19 GB and
# shows the tab.
KLEIN_TEXT_ENCODER = "qwen_3_8b_fp8mixed_abliterated.safetensors"   # ~9.2 GB
KLEIN_TEXT_ENCODER_REPO = "edicamargo/qwen_3_8b_fp8mixed_abliterated"
# The same file the Flux 2 tab uses, from the same repo. Aliased rather than
# copied so there is one source of truth for the name, and fetched by this
# group as well because "klein_i2i" can be the only feature that is on —
# downloads key on the destination path, so whichever asks first fetches it
# and the other logs a cache hit.
KLEIN_VAE = FLUX_VAE
KLEIN_VAE_HF_REPO = FLUX_HF_REPO

# Selectable Klein models. Same scheme as the other registries, with one
# difference: hf_repo is per-entry and hf_path is a plain repo path (Black
# Forest Labs does not use Comfy-Org's split_files/ layout), so add an entry,
# restart, and it appears in the tab's Model dropdown.
#
# The workflow's companion note also lists GGUF quants for smaller GPUs.
# Those are NOT a registry entry away: they load through UnetLoaderGGUF from
# a custom node pack rather than UNETLoader, so they would need a builder
# branch and a node-pack install — deliberately out of scope here.
KLEIN_MODELS = [
    {
        "name": "Flux 2 Klein 9B fp8 (workflow default)",
        "file": "flux-2-klein-9b-fp8.safetensors",       # ~9.4 GB
        "hf_repo": "black-forest-labs/FLUX.2-klein-9b-fp8",
        "hf_path": "flux-2-klein-9b-fp8.safetensors",
    },
]

# KSamplerAdvanced settings, straight from the workflow. steps, cfg and
# guidance are deliberately absent: they belong to the model (KLEIN_DEFAULTS
# below, overridable per registry entry) and would otherwise be a second
# source of truth for the same three numbers. What is left spells "run the
# whole schedule in one pass", which is what makes KSamplerAdvanced behave
# like the plain KSampler the other tabs build.
KLEIN_SAMPLER_DEFAULTS = {
    "add_noise": "enable",
    "start_at_step": 0,
    "end_at_step": 10000,
    "return_with_leftover_noise": "disable",
}

# The knobs the tab exposes, at the workflow's values. cfg 1.0 with a
# ConditioningZeroOut negative is the distilled-Flux idiom: FluxGuidance
# steers prompt adherence instead, which is why both numbers are here.
KLEIN_DEFAULTS = {
    "steps": 8,
    "cfg": 1.0,
    "guidance": 4.0,
    "sampler_name": "euler",
    "scheduler": "normal",
}
# Sampler names come from the shared SAMPLERS list below; schedulers are
# core ComfyUI's, listed here rather than shared because the V2 tab's are
# RES4LYF's and the two sets have nothing to do with each other.
KLEIN_SCHEDULERS = ["normal", "simple", "karras", "beta", "sgm_uniform",
                    "ddim_uniform"]

# ImageScaleToTotalPixels on each source image before it is encoded as a
# reference latent. This is the workflow's own value and is independent of
# the *output* size — the reference is what the model looks at, the output
# latent is what it paints into.
KLEIN_REFERENCE_MEGAPIXELS = 1.0
KLEIN_SCALE_METHOD = "lanczos"
KLEIN_RESOLUTION_STEPS = 1

# The source workflow's three output-resolution groups, of which only the
# first is live as shipped (the other two are bypassed). "Same as image 1"
# means exactly that — a 12 MP phone photo asks for a 12 MP render — so the
# tab warns above KLEIN_WARN_PIXELS and clamps each side at KLEIN_MAX_SIDE
# rather than letting a paste turn into an OOM kill.
KLEIN_OUTPUT_SAME = "Same as image 1 (workflow default)"
KLEIN_OUTPUT_SCALED = "Scale image 1 to megapixels"
KLEIN_OUTPUT_CUSTOM = "Custom width × height"
KLEIN_OUTPUT_MODES = [KLEIN_OUTPUT_SAME, KLEIN_OUTPUT_SCALED,
                      KLEIN_OUTPUT_CUSTOM]
KLEIN_DEFAULT_MEGAPIXELS = 1.0
KLEIN_DEFAULT_CUSTOM_SIZE = (1024, 1024)
KLEIN_MAX_SIDE = 4096
KLEIN_WARN_PIXELS = 4_000_000

# Klein LoRAs live in their own subfolder (loras/klein/) for the same reason
# the Flux ones do: the architectures are incompatible, so a Klein LoRA in
# the Krea 2 dropdown is a job that cannot run. list_lora_files() globs
# loras/ without recursing, so nothing here leaks into the other tabs.
#
# The stack is the workflow's, in its order, at its strengths and on/off
# states — entries are (filename, strength, enabled_by_default,
# civitai_version_id), the same shape as V2_LORA_STACK. Each strength
# applies to the model and the CLIP alike (rgthree "Single Strength").
KLEIN_LORA_SUBDIR = "klein"
KLEIN_LORA_STACK = [
    ("klein_snofs_v1_4.safetensors", 1.0, True, 2960556),
    ("ultra_real_v4.safetensors", 1.0, True, 2846977),
    ("realistic_klein_v3.safetensors", 1.0, True, 2876634),
]

# ── ReActor face swap ─────────────────────────────────────────────────────────
# The Face Swap tab runs ComfyUI-ReActor: an ONNX face-swap pipeline
# (inswapper_128) that is completely independent of the diffusion models.
# It takes a finished image plus a reference face and replaces the face in
# place — no UNet, no text encoder, no VAE — so it never touches the Krea 2
# stack and costs nothing in VRAM while a generation is running.
#
# Everything it needs is downloaded up front by downloads.py, *including*
# the three files ReActor would otherwise fetch during the first swap (the
# RetinaFace detector, the face-parsing net and the NSFW classifier), so a
# swap never reaches out to the network. ~1.8 GB in total. Off by default
# (feature key "faceswap"); a license granting "faceswap" installs the
# node pack, fetches the models and shows the tab.
REACTOR_NODES_DIR = "ComfyUI-ReActor"
# The node pack is vendored in deps/, so bootstrap installs it by copying
# rather than cloning — nothing is fetched from GitHub. The repo URL stays
# as the fallback for a checkout that does not carry deps/.
REACTOR_LOCAL_NODES = PROJECT_DIR / "deps" / REACTOR_NODES_DIR
REACTOR_NODES_REPO = "https://github.com/Gourieff/ComfyUI-ReActor"

# Swap + restore models live in a HF *dataset* repo, so these downloads
# need repo_type="dataset". Entries are (path in the repo, path under
# MODELS_DIR) — the repo nests everything under models/, which is not the
# layout ComfyUI wants, so each file is placed explicitly.
REACTOR_HF_REPO = "Gourieff/ReActor"
REACTOR_SWAP_MODEL = "inswapper_128.onnx"           # ~554 MB
REACTOR_RESTORE_MODEL = "codeformer-v0.1.0.pth"     # ~377 MB
REACTOR_HF_FILES = [
    (f"models/{REACTOR_SWAP_MODEL}", f"insightface/{REACTOR_SWAP_MODEL}"),
    (f"models/facerestore_models/{REACTOR_RESTORE_MODEL}",
     f"facerestore_models/{REACTOR_RESTORE_MODEL}"),
    # Add any other restorer from the same repo and it appears in the tab's
    # "Face restoration" dropdown after a restart:
    # ("models/facerestore_models/GFPGANv1.4.pth",
    #  "facerestore_models/GFPGANv1.4.pth"),
]

# The models originate from insightface, but the package is not involved:
# ReActor's own reactor_core/analyzer.py drives them through onnxruntime.
# ReActorFaceAnalysis(name="buffalo_l", root=models/insightface) expects
# the five ONNX files unpacked flat in models/insightface/models/buffalo_l/
# — otherwise it downloads and unzips the archive itself on the first swap.
REACTOR_INSIGHTFACE_PACK = "buffalo_l"
REACTOR_INSIGHTFACE_ZIP = "models/buffalo_l.zip"    # ~289 MB

# r_facelib resolves its '../../models/facedetection' against the node
# pack's own directory, i.e. ComfyUI/models/facedetection — which
# link_model_dirs symlinks at MODELS_DIR/facedetection. Pre-fetching these
# two is what lets the *first* swap run without a network connection.
REACTOR_FACEDETECTION_FILES = [
    "https://github.com/xinntao/facexlib/releases/download/v0.1.0/"
    "detection_Resnet50_Final.pth",                                  # ~110 MB
    "https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/"
    "parsing_parsenet.pth",                                          # ~85 MB
]

# ComfyUI-ReActor is the SFW edition: it classifies every input image with
# this ViT before swapping and returns flagged images unswapped. The node
# looks for it under models/nsfw_detector/vit-base-nsfw-detector, and the
# check runs whether or not the model is present — so it is downloaded
# here rather than left to fetch itself mid-swap.
REACTOR_NSFW_REPO = "AdamCodd/vit-base-nsfw-detector"
REACTOR_NSFW_DIR = "nsfw_detector/vit-base-nsfw-detector"

# Detector back-ends the ReActorFaceSwap node accepts, in its own order.
REACTOR_DETECTORS = ["retinaface_resnet50", "retinaface_mobile0.25",
                     "YOLOv5l", "YOLOv5n"]
REACTOR_DEFAULT_DETECTOR = "retinaface_resnet50"

# ── CivitAI LoRAs ─────────────────────────────────────────────────────────────
# Entries are (model_version_id, filename_to_save_as). The version id is the
# number in the CivitAI download URL: civitai.com/api/download/models/<id>
# Most CivitAI downloads require an API token (set the CIVITAI_TOKEN env
# var). Add or remove entries freely — a failed LoRA download is logged
# and skipped, it never aborts the setup.
CIVITAI_LORAS = [
    # (3067151, "Krea2FilterBypass_3vector.safetensors"),
    (3070702, "Realism_Engine_Krea2_v2.0.safetensors"),
    (3072664, "SNOFS_Krea2_v1.0.safetensors"),
    (3090634, "Krea2-realism-V2.safetensors"),
    (3071904, "Krea2_AIO_NSFW_v1.0.safetensors"),
    # (3084537, "Realistic_Snapshot_Krea2_v0.5.safetensors"),
    (3069544, "galaxyace_krea2.safetensors"),
    (3160327, "HMBody_D_e10.safetensors"),
    (3151907, "elusarca-photo.safetensors"),
    # (3084588, "Krea2_NSFW_plus.safetensors"),
    # (3075498, "nicegirls_krea2.safetensors"),
    # (3066973, "Krea2-realism-V1.safetensors"),
    # (3075606, "lenovo_krea2.safetensors"),
    # (3114242, "purelens_krea2.safetensors"),
    # (3104629, "snofs_krea_v1_1.safetensors"),
    # (3085473, "KNPV4.1_pre.safetensors"),
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

for _dir in (TEMP_DIR, MODELS_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# Which features are on is logged by app.py once features.py has resolved
# them — this module deliberately does not know, so that importing config
# from features.py stays acyclic.
log.info(
    "Variant: Krea 2 %s · models → %s · images → %s",
    KREA2_VARIANT, MODELS_DIR, OUTPUT_DIR,
)
