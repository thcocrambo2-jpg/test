"""MiniMax H3 — video with sound (image-to-video and text-to-video).

Facts about the model and the one graph both tabs build, and nothing read
from the environment (that is ember/settings.py).

A data module on purpose: nothing is imported, so the Docker bake stage
and weights/downloads.py can read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
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
# the minimax pipeline's resolve_size. "Standard" is the template's
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

