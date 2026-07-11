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
    "Variant: Krea 2 %s · models → %s · images → %s",
    KREA2_VARIANT, MODELS_DIR, OUTPUT_DIR,
)
