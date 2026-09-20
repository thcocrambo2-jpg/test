"""Krea 2 — the fixed half of the graph the Krea2 and Krea2 Edit tabs build.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py). The diffusion models and the
style LoRAs are not here either: they come from the licence server's
catalogue, per feature, by id — see licensing/catalog.py. What stays here
is what a tab's graph is wired around rather than what a user picks.

A data module on purpose: nothing is imported, so the Docker bake stage
and weights/downloads.py can read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# HF_MODEL_REPO is kept because the shared VAE below is fetched from it.
HF_MODEL_REPO = "Comfy-Org/Krea-2"
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

# Diffusion models come from the catalogue (catalog.py); only the shared
# VAE is a fixed download.
HF_MODEL_FILES = [
    f"vae/{VAE_FILE}",
]

# ── Krea 2 forms ──────────────────────────────────────────────────────────────
# The Krea2 / Krea2 Edit tabs' resolution and sampler lists.
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

