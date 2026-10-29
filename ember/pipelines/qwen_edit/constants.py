"""Qwen-Image-Edit-2511 — the fixed half of the graph the 🪄 Qwen Edit 2511
tab builds.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py). The diffusion model is not here:
it is a catalogue record, listed for the qwen_edit_2511 feature, so the
Model dropdown works the way Qwen 2.1's does and a bf16 or int8 record can
be added later as a database edit. What stays here is what the graph is
wired around — the text encoder, the Lightning LoRAs, the core nodes and
the sampler settings. The VAE is the Qwen image VAE Krea already fetches
(pipelines/krea2/constants.py, the "krea2" group).

A data module on purpose: nothing is imported, so weights/downloads.py can
read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# Transcribed from ComfyUI's own template, image_qwen_image_edit_2511.json
# in Comfy-Org/workflow_templates. A prompt and 1 to 3 numbered images make
# one edited image: each picture is read by the Qwen2.5-VL text encoder and
# spliced into the sequence as a VAE latent, and image 1 is also the
# starting latent, so the result keeps its framing.
#
# Comfy-Org's repackaging keeps its files under split_files/, like Z-Image's,
# so downloads.py fetches them the same way.
QWEN_EDIT_HF_REPO = "Comfy-Org/Qwen-Image_ComfyUI"
QWEN_EDIT_TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"   # ~9.4 GB
QWEN_EDIT_HF_FILES = [
    f"text_encoders/{QWEN_EDIT_TEXT_ENCODER}",
]

# CLIPLoader's type, as the template has it.
QWEN_EDIT_CLIP_TYPE = "qwen_image"

# The core nodes the graph needs beyond the long-standing ones. The encoder
# has been in ComfyUI since the 2509 model; the reference method's
# index_timestep_zero option, which 2511 needs, first ships in v0.5.0.
QWEN_EDIT_ENCODE_NODE = "TextEncodeQwenImageEditPlus"
QWEN_EDIT_REF_METHOD_NODE = "FluxKontextMultiReferenceLatentMethod"
QWEN_EDIT_REF_METHOD = "index_timestep_zero"
QWEN_EDIT_COMFYUI_MIN = "v0.5.0"

# ── Images ────────────────────────────────────────────────────────────────────
# TextEncodeQwenImageEditPlus has three image inputs, image1 to image3, and
# the model card names 1 to 3 as the range it works best in. Image 1 is the
# one edited; 2 and 3 are references the prompt names by number — another
# person, an outfit, or a pose skeleton.
QWEN_EDIT_MAX_IMAGES = 3

# ── Lightning ─────────────────────────────────────────────────────────────────
# lightx2v's step-distilled LoRAs for 2511, model-only, at strength 1 after
# CFGNorm, as the template's "Enable 4steps LoRA?" switch wires them. With
# one on, the template's other two switches move steps to the LoRA's count
# and CFG to 1, so the form's Steps and CFG only apply to Normal.
QWEN_EDIT_LIGHTNING_REPO = "lightx2v/Qwen-Image-Edit-2511-Lightning"
QWEN_EDIT_LIGHTNING_4 = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"  # ~0.85 GB
QWEN_EDIT_LIGHTNING_8 = "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"  # ~0.85 GB
QWEN_EDIT_LIGHTNING_FILES = [QWEN_EDIT_LIGHTNING_4, QWEN_EDIT_LIGHTNING_8]
QWEN_EDIT_LIGHTNING_STRENGTH = 1.0
QWEN_EDIT_LIGHTNING_CFG = 1.0

# The Speed dropdown: a label, and the LoRA and step count it stands for —
# None for Normal, which runs the form's Steps and CFG with no LoRA.
QWEN_EDIT_NORMAL = "Normal (steps and CFG below)"
QWEN_EDIT_SPEEDS = {
    "⚡ Lightning · 4 steps": (QWEN_EDIT_LIGHTNING_4, 4),
    "⚡ Lightning · 8 steps": (QWEN_EDIT_LIGHTNING_8, 8),
    QWEN_EDIT_NORMAL: None,
}
QWEN_EDIT_DEFAULT_SPEED = "⚡ Lightning · 4 steps"

# ── The sampler ───────────────────────────────────────────────────────────────
# ModelSamplingAuraFlow at 3.1 and CFGNorm at 1, then euler on simple, as
# the template has them. Normal's 40 steps and CFG 4 are the model record's.
QWEN_EDIT_SHIFT = 3.1
QWEN_EDIT_CFG_NORM = 1.0
QWEN_EDIT_SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "res_multistep",
                      "er_sde"]
QWEN_EDIT_SCHEDULERS = ["simple", "beta", "normal"]
