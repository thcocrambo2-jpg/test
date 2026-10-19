"""Anima — the fixed half of the graph the 🎨 Anima tab builds.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py). The diffusion model is not here:
it is a catalogue record, listed for the anima_t2i feature, so the Model
dropdown works the way Z-Image's and Qwen 2.1's do and another Anima build
(Turbo, Aesthetic) is a database edit. What stays here is what the graph
is wired around — the text encoder, the VAE and the size table.

A data module on purpose: nothing is imported, so weights/downloads.py can
read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# Anima is CircleStone Labs' and Comfy Org's 2B anime model, a Cosmos-
# Predict2 DiT with an LLM adapter, on a Qwen3 0.6B text encoder and the
# Qwen-Image VAE. ComfyUI supports it natively (comfy/ldm/anima), so the
# graph is the model card's example workflow and every node is core.
#
# Licence: the CircleStone Labs Non-Commercial License. The feature is on
# no plan and its weights are never mirrored (the mirror repos are public)
# — see docs/pipelines/anima.md before granting it to anyone.
#
# The model card's repo keeps its files under split_files/, like the Wan
# and Z-Image repacks.
ANIMA_HF_REPO = "circlestone-labs/Anima"
ANIMA_TEXT_ENCODER = "qwen_3_06b_base.safetensors"           # ~1.2 GB
ANIMA_HF_FILES = [
    f"text_encoders/{ANIMA_TEXT_ENCODER}",
]
# The VAE is the Qwen-Image VAE, byte for byte the file the Krea tabs
# already decode with (krea2.constants.VAE_FILE, same SHA-256), so it is
# fetched from Comfy-Org/Krea-2 the same way and a key with both tabs
# downloads it once.

# CLIPLoader's type, as the example workflow has it. ComfyUI tells the
# encoder apart by its weights (TEModel.QWEN3_06B), not by this.
ANIMA_CLIP_TYPE = "stable_diffusion"

# ── Output size ───────────────────────────────────────────────────────────────
# The model card: "Works at resolutions between 512^2 and 1536^2 pixels".
# About 1 MP in the usual buckets, each side a multiple of 64, square
# first as in the example workflow (1024×1024).
ANIMA_SIZES = {
    "1:1 · 1024×1024": (1024, 1024),
    "4:5 · 896×1152": (896, 1152),
    "5:4 · 1152×896": (1152, 896),
    "2:3 · 832×1216": (832, 1216),
    "3:2 · 1216×832": (1216, 832),
    "9:16 · 768×1344": (768, 1344),
    "16:9 · 1344×768": (1344, 768),
}
ANIMA_DEFAULT_SIZE = "1:1 · 1024×1024"

# ── The sampler ───────────────────────────────────────────────────────────────
# euler first: what BAS:NSFW, a Turbo merge, is tuned for (8 steps, CFG 1),
# and the model card's pick for the Turbo and Aesthetic builds. Then the
# card's other favourites. Steps and CFG are the model record's.
ANIMA_SAMPLERS = ["euler", "er_sde", "euler_ancestral", "dpmpp_2m_sde_gpu"]
ANIMA_SCHEDULERS = ["simple", "beta"]

# The model card's recommended negative, offered as a hint rather than a
# default: at CFG 1 a negative does nothing.
ANIMA_NEGATIVE_HINT = ("worst quality, low quality, score_1, score_2, "
                       "score_3, artist name, blurry, jpeg artifacts")

# The LoRA stack's rows, over the feature's catalogue list (empty for now).
ANIMA_LORA_SLOTS = 4
