"""Wan 2.2 — image-to-video.

Facts about the two model families and the graph the Video tab builds,
and nothing read from the environment. The parallel-instance knobs
(EMBER_WAN_PARALLEL and the VRAM reserves) are environment, so they live
in ember/settings.py.

A data module on purpose: nothing is imported, so the Docker bake stage
and weights/downloads.py can read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# Two model families, switchable per-job in the Video tab:
#   • I2V A14B — two 14B "experts" (a high-noise model for the early
#     denoising steps, a low-noise one for the late steps; ~14.3 GB each,
#     fp8) at 16 fps. Best quality; turbo mode uses lightx2v's rank-64
#     4-step distillation LoRAs (~0.7 GB each).
#   • TI2V 5B — a single dense 5B model (~10 GB fp16) with its own
#     higher-compression Wan 2.2 VAE (~1.4 GB) at 24 fps. Lower quality
#     than 14B but much lighter in VRAM (no expert swap mid-run), so it
#     suits the parallel mode well.
# Both share the UMT5-XXL text encoder (~6.7 GB). The models come from the
# Comfy-Org repackaged repo and the turbo LoRAs from lightx2v's — ~48 GB
# in total on top of the Krea downloads. Off by default (feature key
# "wan_i2v"); a license granting "wan_i2v" fetches all of it and shows the
# Video tab, and nothing else changes.
WAN_HF_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
WAN_HIGH_UNET = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
WAN_LOW_UNET = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
WAN_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
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
]

# The turbo LoRAs: lightx2v's 1022 release of the A14B I2V distillation,
# rank 64, which moves more and follows the prompt better than the v1
# Lightning pair Comfy-Org repackaged. They sit at the root of lightx2v's
# repo, so they are placed under loras/ by hand, like MiniMax's turbo LoRA.
WAN_LIGHTNING_REPO = "lightx2v/Wan2.2-Distill-Loras"
WAN_LIGHTNING_HIGH = "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"  # ~0.63 GB
WAN_LIGHTNING_LOW = "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"    # ~0.74 GB

# ── Frame interpolation ──────────────────────────────────────────────────────
# The Smooth motion tick runs RIFE 4.9 over the decoded frames, as
# HearmemanAI's "Wan 2.2 I2V — 60 FPS" workflow does. Wan renders at its
# trained frame rate; RIFE adds frames in between and the clip is saved at
# that rate times the multiplier, so it is smoother and exactly as long.
# (The workflow saves 16 x 4 at 60 fps, which slows every clip by about 7%.)
#
# (custom_nodes dir, git URL, a node class that proves it loaded) — the
# shape of V2_NODE_REPOS. The pack's own requirements are not installed:
# RIFE needs only torch, einops, numpy and pyyaml, which ComfyUI already
# has, and the rest (opencv-contrib, kornia, cupy) serve other models.
WAN_RIFE_NODES = (
    "ComfyUI-Frame-Interpolation",
    "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation",
    "RIFE VFI",
)
# The pack looks for checkpoints under its own ckpts/<model>/ and downloads
# a missing one from unpinned URLs (the first of which is gone). Its ckpts/
# is linked to this folder under MODELS_DIR instead, and the checkpoint is
# fetched there, from a pinned HF copy of the same file. A community repo,
# so it has a mirror entry as well as a pin.
WAN_RIFE_DIR = "frame_interpolation"
WAN_RIFE_REPO = "Isi99999/Frame_Interpolation_Models"
WAN_RIFE_MODEL = "rife49.pth"                           # ~0.02 GB
WAN_RIFE_FILE = f"{WAN_RIFE_DIR}/rife/{WAN_RIFE_MODEL}"

# RIFE VFI's inputs, from the workflow, keyed by the node's INPUT_TYPES at
# the pin. bfloat16 halves the memory 321 frames at 720p take.
WAN_RIFE_SETTINGS = {
    "clear_cache_after_n_frames": 5,
    "fast_mode": False,
    "ensemble": False,
    "scale_factor": 1.0,
    "dtype": "bfloat16",
    "torch_compile": False,
    "batch_size": 1,
}
# Frames out per frame in: 14B's 16 fps becomes 64, the 5B's 24 becomes 48.
WAN_RIFE_MULTIPLIER = 4
WAN_5B_RIFE_MULTIPLIER = 2
WAN_INTERPOLATE_DEFAULT = True

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
