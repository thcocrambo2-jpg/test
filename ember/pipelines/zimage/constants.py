"""Z-Image Turbo — the fixed half of the graph the ⚡ Z-Image tab builds.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py). The diffusion model is not here:
it is a catalogue record, listed for the zimage_t2i feature, so the Model
dropdown works the way Krea's does. What stays here is what the graph is
wired around — the text encoder, the VAE and the upscale pass.

A data module on purpose: nothing is imported, so weights/downloads.py and
ember/comfy/setup.py (and with it the Docker bake stage) can read it
without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# Transcribed from two workflows in Hearmeman24's comfyui-qwen-template,
# Z_Image_Turbo.json and Z_Image_Turbo_Upscale.json. The second is the
# first plus one UltimateSDUpscale pass, and the tab's Upscale tick is the
# choice between them. Every node of the base graph is core ComfyUI.
#
# Comfy-Org's repackaging keeps its files under split_files/, like the Wan
# repo, so downloads.py fetches them the same way. The template links its
# VAE from a community copy (modelzpalace/ae.safetensors); this is the one
# in the org repo.
ZIMAGE_HF_REPO = "Comfy-Org/z_image_turbo"
ZIMAGE_TEXT_ENCODER = "qwen_3_4b.safetensors"      # ~8.0 GB, Qwen3-4B
ZIMAGE_VAE = "ae.safetensors"                       # ~0.34 GB
ZIMAGE_HF_FILES = [
    f"text_encoders/{ZIMAGE_TEXT_ENCODER}",
    f"vae/{ZIMAGE_VAE}",
]

# CLIPLoader's type, as the template has it. ComfyUI picks the encoder
# class from the weights, not from this: every type but flux/flux2 loads a
# Qwen3-4B state dict as the Z-Image encoder (comfy/sd.py), so "qwen_image"
# and the "lumina2" of ComfyUI's own Z-Image template build the same thing.
ZIMAGE_CLIP_TYPE = "qwen_image"

# ── The upscale pass ──────────────────────────────────────────────────────────
# 4xLSDIR upscales the decoded image, then UltimateSDUpscale re-samples it
# in tiles with the same model, LoRAs and prompts. The model comes from the
# template's own asset repo, a community repo, so it has a mirror entry
# (scripts/mirror_manifest.json) as well as a pin.
ZIMAGE_UPSCALE_REPO = "Hearmeman/comfyui-template-assets"
ZIMAGE_UPSCALE_MODEL = "4xLSDIR.pth"                # ~0.07 GB
ZIMAGE_UPSCALE_HF_FILE = f"upscale_models/{ZIMAGE_UPSCALE_MODEL}"

# (custom_nodes dir, git URL, a node class that proves it loaded) — the
# shape of V2_NODE_REPOS. The one node pack this tab needs, and only for
# the upscale pass. It carries a git submodule (the A1111 script it wraps),
# which setup.py initialises at the pinned commit.
ZIMAGE_UPSCALE_NODES = (
    "ComfyUI_UltimateSDUpscale",
    "https://github.com/ssitu/ComfyUI_UltimateSDUpscale",
    "UltimateSDUpscale",
)

# UltimateSDUpscale's inputs, fixed, straight from the template — named by
# the node's INPUT_TYPES at the pinned commit rather than by widget
# position. Two are not here. `tile_width` / `tile_height` are linked to
# the base width and height in the template, and the builder does the same.
# `seed` is the job's own. `batch_size` is not in the template at all: the
# pack gained it after the workflow was saved, and 1 is both its default
# and what the older pack did.
ZIMAGE_UPSCALE_SETTINGS = {
    "upscale_by": 1.5,
    "steps": 8,
    "cfg": 1.0,
    "sampler_name": "er_sde",
    "scheduler": "simple",
    "denoise": 0.18,
    "mode_type": "Chess",
    "mask_blur": 8,
    "tile_padding": 32,
    "seam_fix_mode": "None",
    "seam_fix_denoise": 1.0,
    "seam_fix_width": 64,
    "seam_fix_mask_blur": 8,
    "seam_fix_padding": 16,
    "force_uniform_tiles": True,
    "tiled_decode": False,
    "batch_size": 1,
}

# ── The form ──────────────────────────────────────────────────────────────────
# The template's resolution note, plus the two 1080p sizes the upscale file
# adds, as (width, height). They reach EmptyLatentImage as they are, times
# the multiplier: its step of 8 is not enforced by ComfyUI's validator,
# and the node floors to the latent grid itself, so 1140 renders 1136 wide
# here exactly as it does in the template. Krea's parse_resolution would
# snap 1080 to 1088 instead.
ZIMAGE_RESOLUTIONS = {
    "1328×1328 (1:1)": (1328, 1328),
    "1664×928 (16:9)": (1664, 928),
    "928×1664 (9:16)": (928, 1664),
    "1472×1140 (4:3)": (1472, 1140),
    "1140×1472 (3:4)": (1140, 1472),
    "1584×1056 (3:2)": (1584, 1056),
    "1056×1584 (2:3)": (1056, 1584),
    "1920×1080 (1080p)": (1920, 1080),
    "1080×1920 (1080p portrait)": (1080, 1920),
}
ZIMAGE_DEFAULT_RESOLUTION = "1080×1920 (1080p portrait)"
# The template's "Resolution Multiplier", applied to both sides and rounded
# with Python's round(), which is what its SimpleMath+ nodes do.
ZIMAGE_DEFAULT_MULTIPLIER = 1.0

# The KSampler's sampler list. The template runs er_sde on the "simple"
# scheduler, which stays fixed.
ZIMAGE_SAMPLERS = ["er_sde", "euler", "euler_ancestral", "dpmpp_2m",
                   "res_multistep"]
ZIMAGE_SCHEDULER = "simple"

# The template's negative, verbatim. At its CFG of 1 the sampler ignores
# the negative, so it only takes effect once CFG is raised.
ZIMAGE_DEFAULT_NEGATIVE = (
    "cinematic, glossy finish, shallow depth of field, cinematic bokeh, "
    "uncanny anatomy, frame-perfect symmetry, blurred background, fat, low "
    "resolution, big ears, vertical lines, lines glitch"
)
