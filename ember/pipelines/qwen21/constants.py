"""Qwen Image 2.1 — the fixed half of the graphs the 🧩 Qwen 2.1 Reference
and 🌄 Qwen 2.1 tabs build.

Facts about the model and its workflows, and nothing read from the
environment (that is ember/settings.py). The diffusion model is not here:
it is a catalogue record, listed for the qwen21_edit and qwen21_t2i
features, so the Model dropdown works the way Krea's and Z-Image's do and
a bf16 record can be added later as a database edit. What stays here is
what the graphs are wired around — the text encoder, the VAE, the two core
nodes and the size rules.

A data module on purpose: nothing is imported, so weights/downloads.py can
read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# Transcribed from qwen_image_2.1_reference_workflow.json in Hearmeman24's
# comfyui-qwen-template. A prompt and 1 to 10 numbered reference images
# make one new image that keeps them consistent: the references are read by
# the Qwen3-VL text encoder and spliced into the sequence as VAE latents.
#
# Comfy-Org's repo keeps its files in ComfyUI's own layout (text_encoders/,
# vae/, diffusion_models/), like the MiniMax repo, so each lands in place.
QWEN21_HF_REPO = "Comfy-Org/Qwen-Image-2.1"
QWEN21_TEXT_ENCODER = "qwen3vl_8b_int8_convrot.safetensors"   # ~9.4 GB
QWEN21_VAE = "qwen_image_2.1_vae_bf16.safetensors"            # ~0.7 GB
QWEN21_HF_FILES = [
    f"text_encoders/{QWEN21_TEXT_ENCODER}",
    f"vae/{QWEN21_VAE}",
]

# CLIPLoader's type, as the template has it.
QWEN21_CLIP_TYPE = "qwen_image"

# The two core nodes the graph is built on, and the ComfyUI release they
# first ship in. Neither is in v0.36.0, whatever the template's JSON says.
QWEN21_ENCODE_NODE = "TextEncodeQwenImage21"
QWEN21_CACHE_NODE = "QwenImage21Cache"
QWEN21_COMFYUI_MIN = "v0.37.0"

# QwenImage21Cache's options, fixed at the template's values: the KV cache
# on spare VRAM then RAM, stored losslessly.
QWEN21_CACHE_SETTINGS = {"device": "auto", "dtype": "default"}

# ── References ────────────────────────────────────────────────────────────────
# The template's upload node (QwenImageReferencePack) takes up to 10 and
# caps each one's longest edge at 2048 before the encoder sees it. The app
# does both itself, in the handler, rather than installing the pack.
QWEN21_MAX_REFERENCES = 10
QWEN21_MAX_REFERENCE_EDGE = 2048

# TextEncodeQwenImage21's `resolution`: each reference is resized to about
# this squared, in multiples of 32, keeping its shape. 0 keeps each one's
# own size.
QWEN21_DEFAULT_DETAIL = 1024
QWEN21_DETAIL_MAX = 4096
QWEN21_DETAIL_STEP = 32

# ── Output size ───────────────────────────────────────────────────────────────
# The template's resolution note, as (width, height), plus the choice the
# node itself recommends: the shape of reference 1. The KSampler samples an
# EmptyLatentImage of this size, as the template does — not the encoder's
# own `latent` output, which is sized to `resolution` (1024²).
QWEN21_SAME_AS_REFERENCE = "Same as reference 1"
QWEN21_OUTPUT_SIZES = {
    QWEN21_SAME_AS_REFERENCE: None,
    "1:1 · 2048×2048": (2048, 2048),
    "4:3 · 2400×1792": (2400, 1792),
    "3:4 · 1792×2400": (1792, 2400),
    "3:2 · 2528×1696": (2528, 1696),
    "2:3 · 1696×2528": (1696, 2528),
    "16:9 · 2752×1536": (2752, 1536),
    "9:16 · 1536×2752": (1536, 2752),
}
QWEN21_DEFAULT_OUTPUT_SIZE = QWEN21_SAME_AS_REFERENCE
# "Same as reference 1": reference 1's shape at about 2048² pixels, each
# side a multiple of 32 — the grid the encoder sizes references on, and
# every preset above sits on it too. workflow.reference_size() is the
# rule; the browser gets these two numbers to show the same answer.
QWEN21_TARGET_PIXELS = 2048 * 2048
QWEN21_SIZE_MULTIPLE = 32

# ── The sampler ───────────────────────────────────────────────────────────────
# The template runs euler on simple, and its notes offer beta too.
QWEN21_SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "res_multistep",
                   "er_sde"]
QWEN21_SCHEDULERS = ["simple", "beta"]

# The LoRA stack's rows. The template's Power Lora Loader was empty; the
# form offers four, over the feature's catalogue list.
QWEN21_LORA_SLOTS = 4

# ── Text to image ─────────────────────────────────────────────────────────────
# The 🌄 Qwen 2.1 tab, transcribed from qwen_image_2.1_workflow.json in the
# same template: the text-to-image half of Qwen's own Qwen-Image-2.1
# workflow Space, on the same encoder, VAE and diffusion model. Its sizes
# are the template's note, which is the model card's aspect-ratio table —
# the reference tab's list without "Same as reference 1".
QWEN21_T2I_SIZES = {label: size for label, size in QWEN21_OUTPUT_SIZES.items()
                    if size is not None}
QWEN21_T2I_DEFAULT_SIZE = "1:1 · 2048×2048"
