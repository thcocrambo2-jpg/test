"""MOSS-Image — the fixed half of the graph the 🌿 MOSS tab builds.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py). The diffusion model is not here:
it is a catalogue record, listed for the moss_t2i feature, so the Model
dropdown works the way Anima's and Qwen 2.1's do and a bf16 or Base record
is a database edit. What stays here is what the graph is wired around —
the text encoder, the VAE, the 8-step LoRA, the Speed table and the sizes.

A data module on purpose: nothing is imported, so weights/downloads.py can
read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# MOSS-Image is OpenMOSS's 5B single-stream DiT on a Qwen3.5-9B text encoder
# and the FLUX.2 VAE. ComfyUI supports it natively from v0.42.0
# (comfy/ldm/moss_image, CLIPLoader type "moss_image"), so every node in the
# graph is core. Weights, encoder and VAE are Apache 2.0.
#
# Comfy-Org's repack keeps its files at the repo root under ComfyUI's own
# folder names (no split_files/), so they are fetched to the same relative
# path under MODELS_DIR.
MOSS_HF_REPO = "Comfy-Org/MOSS-Image"
MOSS_TEXT_ENCODER = "qwen3.5_9b_int8_convrot.safetensors"     # ~9.7 GB
MOSS_VAE = "flux2-vae.safetensors"                            # ~0.3 GB
MOSS_HF_FILES = [
    f"text_encoders/{MOSS_TEXT_ENCODER}",
    f"vae/{MOSS_VAE}",
]

MOSS_CLIP_TYPE = "moss_image"

# The release that first carries the model, and a node that arrived with it
# and stands for it: a checkout older than v0.42.0 has no MOSS support at
# all, and the CLIPLoader type cannot be asked about directly.
MOSS_MARKER_NODE = "MOSSImageLayoutPrompt"
MOSS_COMFYUI_MIN = "v0.42.0"

# ── Fast: the 8-step DMD LoRA ─────────────────────────────────────────────────
# lodestones' data-space DMD distillation of MOSS-Image-SFT (50 steps, CFG 3)
# to 8 evaluations with no CFG, shipped as a model-only LoRA that its model
# card says to load at strength 1 in ComfyUI's LoRA loader. The repo calls it
# lora.safetensors, so it is renamed on the way into loras/.
MOSS_DMD_REPO = "lodestones/MOSS-Image-SFT-8-steps-dmd"
MOSS_DMD_REPO_FILE = "lora.safetensors"                       # ~1.2 GB
MOSS_DMD_LORA = "moss_image_sft_dmd_8step_lora.safetensors"
MOSS_DMD_STRENGTH = 1.0
MOSS_DMD_STEPS = 8
MOSS_DMD_CFG = 1.0

# The Speed dropdown: a label, and the step count the DMD LoRA runs — None
# for Normal, which runs the form's Steps and CFG (the model record's 50 and
# 3) with no LoRA.
MOSS_NORMAL = "Normal (steps and CFG below)"
MOSS_FAST = "⚡ Fast · 8 steps"
MOSS_SPEEDS = {
    MOSS_NORMAL: None,
    MOSS_FAST: MOSS_DMD_STEPS,
}
MOSS_DEFAULT_SPEED = MOSS_NORMAL

# ── Output size ───────────────────────────────────────────────────────────────
# The user guide: 1M, 1.5M or 2M total pixels, each side a multiple of 16.
# About 1 MP in the usual buckets, square first as in the guide's default.
MOSS_SIZES = {
    "1:1 · 1024×1024": (1024, 1024),
    "4:5 · 896×1152": (896, 1152),
    "5:4 · 1152×896": (1152, 896),
    "2:3 · 832×1248": (832, 1248),
    "3:2 · 1248×832": (1248, 832),
    "9:16 · 768×1360": (768, 1360),
    "16:9 · 1360×768": (1360, 768),
    "1:1 · 1440×1440 (2 MP)": (1440, 1440),
}
MOSS_DEFAULT_SIZE = "1:1 · 1024×1024"

# ── The sampler ───────────────────────────────────────────────────────────────
# Flow-matching Euler on ComfyUI's simple schedule, the guide's default; the
# shift of 5 is the model's own sampling setting in ComfyUI, so no node sets
# it. Normal's 50 steps and CFG 3 are the model record's.
MOSS_SAMPLERS = ["euler", "euler_ancestral", "res_multistep", "er_sde"]
MOSS_SCHEDULERS = ["simple", "beta", "normal"]
