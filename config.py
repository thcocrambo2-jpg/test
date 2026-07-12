"""Krea 2 on RunPod — configuration.

Everything user-tunable lives in this module: paths, model variant,
LoRA lists and (optional) access tokens.
"""

import logging
import os
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("krea2")

# ── Disk layout ───────────────────────────────────────────────────────────────
# The pod filesystem is ephemeral: the ComfyUI install, model weights,
# generated images and logs all live under one base directory and are lost
# when the pod is destroyed. Override with the KREA2_BASE_DIR env var.
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

# ── Model selection ───────────────────────────────────────────────────────────
# "turbo" = 8-step distilled model, CFG 1.0 (default)
# "raw"   = full model, ~50 steps, CFG 3.5-4.5 (much slower)
KREA2_VARIANT = "turbo"

VARIANT_DEFAULTS = {
    "turbo": {"steps": 8, "cfg": 1.0},
    "raw": {"steps": 50, "cfg": 4.0},
}

HF_MODEL_REPO = "Comfy-Org/Krea-2"
UNET_FILE = f"krea2_{KREA2_VARIANT}_fp8_scaled.safetensors"  # ~13.1 GB
TEXT_ENCODER_FILE = "qwen3vl_4b_fp8_scaled.safetensors"      # ~5.2 GB
VAE_FILE = "qwen_image_vae.safetensors"                      # ~0.25 GB

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
KREA2EDIT_NODES_REPO = "https://github.com/lbouaraba/comfyui-krea2edit"
EDIT_LORA_REPO = "conradlocke/krea2-identity-edit"
EDIT_LORA_FILE = "krea2_identity_edit_v1_1.safetensors"  # ~1.83 GB

HF_MODEL_FILES = [
    f"diffusion_models/{UNET_FILE}",
    f"vae/{VAE_FILE}",
]

# Official Krea 2 style LoRAs from the same HF repo (~0.5 GB each).
# Trim this list to save download time and disk space.
HF_LORA_FILES = [
    "loras/krea2_darkbrush.safetensors",
    "loras/krea2_dotmatrix.safetensors",
    "loras/krea2_kidsdrawing.safetensors",
    "loras/krea2_neondrip.safetensors",
    "loras/krea2_rainywindow.safetensors",
    "loras/krea2_retroanime.safetensors",
    "loras/krea2_softwatercolor.safetensors",
    "loras/krea2_sunsetblur.safetensors",
    "loras/krea2_vintagetarot.safetensors",
]

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
# downloads. Set KREA2_DISABLE_WAN=1 to skip all of it (the Video tab
# disappears and nothing else changes).
WAN_ENABLED = not os.environ.get("KREA2_DISABLE_WAN")
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
# zero OOM risk, but jobs run strictly one after another.
WAN_PARALLEL = bool(os.environ.get("KREA2_WAN_PARALLEL"))
WAN_COMFY_PORT = 8189
WAN_COMFY_LOG = WORKING_DIR / "comfyui_wan.log"
KREA_RESERVE_VRAM_GB = float(os.environ.get("KREA2_MAIN_RESERVE_VRAM", 26))
WAN_RESERVE_VRAM_GB = float(os.environ.get("KREA2_WAN_RESERVE_VRAM", 22))

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
    (3084588, "Krea2_NSFW_plus.safetensors"),
    (3075498, "nicegirls_krea2.safetensors"),
    (3066973, "Krea2-realism-V1.safetensors"),
    (3075606, "lenovo_krea2.safetensors"),
    (3114242, "purelens_krea2.safetensors"),
    (3104629, "snofs_krea_v1_1.safetensors"),
]

# LoRAs pre-selected in the UI's three slots (generate / edit / inpaint tabs).
# Entries are (filename, default weight); a file that failed to download is
# silently skipped and the slot falls back to "None".
DEFAULT_LORAS = [
    ("Krea2-realism-V2.safetensors", 0.8),
    ("Realism_Engine_Krea2_v2.0.safetensors", 0.8),
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
DEFAULT_RESOLUTION = "1024×1024 (Square)"

# Valid native ComfyUI samplers that work well with Krea 2 ("simple" scheduler).
SAMPLERS = ["er_sde", "euler", "euler_ancestral", "dpmpp_2m", "res_multistep"]

# Optional — Comfy-Org/Krea-2 is a public repo; only needed for gated repos.
HF_TOKEN = os.environ.get("HF_TOKEN") or None
# Needed for most CivitAI downloads (create one at civitai.com → account settings).
CIVITAI_TOKEN = os.environ.get("CIVITAI_TOKEN") or None

for _dir in (TEMP_DIR, MODELS_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

log.info(
    "Variant: Krea 2 %s · Wan 2.2 I2V %s · models → %s · images → %s",
    KREA2_VARIANT,
    ("parallel instance" if WAN_PARALLEL else "shared queue")
    if WAN_ENABLED else "disabled",
    MODELS_DIR, OUTPUT_DIR,
)
