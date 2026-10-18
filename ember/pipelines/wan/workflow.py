"""Wan 2.2 image-to-video workflow builder (ComfyUI API format).

Wan 2.2 I2V A14B is a two-expert MoE: a high-noise 14B model denoises the
early sampler steps and a low-noise 14B model finishes the late ones, so
the workflow chains two KSamplerAdvanced passes (the first returns its
leftover noise to the second). Only nodes that ship with current ComfyUI
are used: UNETLoader ×2, CLIPLoader(type="wan"), VAELoader,
LoraLoaderModelOnly, ModelSamplingSD3, WanImageToVideo, KSamplerAdvanced,
VAEDecode, CreateVideo and SaveVideo. The one custom node is RIFE VFI
(ComfyUI-Frame-Interpolation), and only when Smooth motion is ticked.

Turbo mode applies lightx2v's 4-step distillation LoRAs (one per expert,
strength 1.0) with CFG 1.0 — the same idea as the Krea 2 Turbo
checkpoint, delivered as LoRAs because the base weights are shared with
raw mode. Raw mode is the undistilled 20-step CFG 3.5 schedule.

Smooth motion puts RIFE between the decode and the video: the frames
Wan made, with RIFE's in between, saved at the frame rate times the
multiplier, so the clip keeps its length.

The alternative TI2V 5B model (build_wan_5b_workflow) is a single dense
model with its own Wan 2.2 VAE and a plain one-KSampler graph — lighter
in VRAM, 24 fps, no turbo/raw split.
"""

from ember.comfy.server import GPU_COUNT
from ember.settings import MODELS_DIR
from ember.pipelines.wan.constants import (
    WAN_5B_RIFE_MULTIPLIER,
    WAN_5B_UNET,
    WAN_5B_VAE,
    WAN_HIGH_UNET,
    WAN_LIGHTNING_HIGH,
    WAN_LIGHTNING_LOW,
    WAN_LOW_UNET,
    WAN_RIFE_FILE,
    WAN_RIFE_MODEL,
    WAN_RIFE_MULTIPLIER,
    WAN_RIFE_NODES,
    WAN_RIFE_SETTINGS,
    WAN_TEXT_ENCODER,
    WAN_VAE,
)

RIFE_NODE = WAN_RIFE_NODES[2]


def wan_models_available() -> bool:
    """True once every file the 14B I2V workflow needs has been downloaded."""
    required = [
        MODELS_DIR / "diffusion_models" / WAN_HIGH_UNET,
        MODELS_DIR / "diffusion_models" / WAN_LOW_UNET,
        MODELS_DIR / "text_encoders" / WAN_TEXT_ENCODER,
        MODELS_DIR / "vae" / WAN_VAE,
    ]
    return all(p.exists() for p in required)


def wan_5b_available() -> bool:
    """True once every file the 5B TI2V workflow needs has been downloaded."""
    required = [
        MODELS_DIR / "diffusion_models" / WAN_5B_UNET,
        MODELS_DIR / "text_encoders" / WAN_TEXT_ENCODER,
        MODELS_DIR / "vae" / WAN_5B_VAE,
    ]
    return all(p.exists() for p in required)


def wan_lightning_available() -> bool:
    """True once both Lightning speed LoRAs (turbo mode) are on disk."""
    return all((MODELS_DIR / "loras" / f).exists()
               for f in (WAN_LIGHTNING_HIGH, WAN_LIGHTNING_LOW))


def wan_rife_available() -> bool:
    """True once the RIFE checkpoint Smooth motion needs is on disk."""
    return (MODELS_DIR / WAN_RIFE_FILE).exists()


def _add_output(wf: dict, images_ref: list, fps: int, multiplier: int,
                filename_prefix: str) -> None:
    """The decoded frames to an mp4: CreateVideo + SaveVideo.

    With a multiplier above 1, RIFE VFI comes first and the video runs at
    fps times it, so the frames RIFE adds make it smoother, not longer.
    """
    if multiplier > 1:
        wf["interpolate"] = {
            "class_type": RIFE_NODE,
            "inputs": {"ckpt_name": WAN_RIFE_MODEL, "frames": images_ref,
                       "multiplier": int(multiplier), **WAN_RIFE_SETTINGS},
        }
        images_ref = ["interpolate", 0]
    wf["video"] = {
        "class_type": "CreateVideo",
        "inputs": {"images": images_ref, "fps": float(fps * multiplier)},
    }
    wf["save"] = {
        "class_type": "SaveVideo",
        "inputs": {"video": ["video", 0], "filename_prefix": filename_prefix,
                   "format": "mp4", "codec": "h264"},
    }


def build_wan_i2v_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 4,
    cfg: float = 1.0,
    width: int,
    height: int,
    length: int = 81,
    fps: int = 16,
    sampler: str = "euler",
    shift: float = 5.0,
    lightning: bool = True,
    interpolate: bool = False,
    image_name: str,
    filename_prefix: str = "wan/Wan22I2V",
) -> dict:
    """Build a Wan 2.2 I2V A14B workflow in ComfyUI API format.

    `image_name` references a file already uploaded to ComfyUI's input
    folder (client.upload_image); WanImageToVideo VAE-encodes it as the
    clean first frame (no CLIP-vision model needed in 2.2). `length` is
    the frame count and must be a multiple of 4 plus 1 (81 = 5 s at 16
    fps, the training length). The two experts swap at steps // 2, the
    split the official template uses for both the 4- and 20-step
    schedules. `interpolate` runs RIFE at WAN_RIFE_MULTIPLIER, so the clip
    is saved at 64 fps.
    """
    wf = {
        "unet_high": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": WAN_HIGH_UNET, "weight_dtype": "default"},
        },
        "unet_low": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": WAN_LOW_UNET, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": WAN_TEXT_ENCODER, "type": "wan",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": WAN_VAE},
        },
    }
    high_ref, low_ref = ["unet_high", 0], ["unet_low", 0]
    clip_ref, vae_ref = ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # Same placement plan as the Krea workflows: keep the 14 GB experts
        # alone on gpu:0, park the text encoder + VAE on gpu:1.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    if lightning:
        wf["lightning_high"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": WAN_LIGHTNING_HIGH, "strength_model": 1.0,
                       "model": high_ref},
        }
        wf["lightning_low"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": WAN_LIGHTNING_LOW, "strength_model": 1.0,
                       "model": low_ref},
        }
        high_ref, low_ref = ["lightning_high", 0], ["lightning_low", 0]

    wf["shift_high"] = {
        "class_type": "ModelSamplingSD3",
        "inputs": {"model": high_ref, "shift": float(shift)},
    }
    wf["shift_low"] = {
        "class_type": "ModelSamplingSD3",
        "inputs": {"model": low_ref, "shift": float(shift)},
    }
    high_ref, low_ref = ["shift_high", 0], ["shift_low", 0]

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    if negative.strip():
        wf["negative"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": clip_ref},
        }
    else:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        }

    wf["source"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    wf["i2v"] = {
        "class_type": "WanImageToVideo",
        "inputs": {
            "positive": ["positive", 0], "negative": ["negative", 0],
            "vae": vae_ref, "width": int(width), "height": int(height),
            "length": int(length), "batch_size": 1,
            "start_image": ["source", 0],
        },
    }

    boundary = max(1, int(steps) // 2)
    wf["sampler_high"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "add_noise": "enable", "noise_seed": int(seed),
            "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": "simple",
            "start_at_step": 0, "end_at_step": boundary,
            "return_with_leftover_noise": "enable",
            "model": high_ref, "positive": ["i2v", 0],
            "negative": ["i2v", 1], "latent_image": ["i2v", 2],
        },
    }
    wf["sampler_low"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "add_noise": "disable", "noise_seed": 0,
            "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": "simple",
            "start_at_step": boundary, "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
            "model": low_ref, "positive": ["i2v", 0],
            "negative": ["i2v", 1], "latent_image": ["sampler_high", 0],
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler_low", 0], "vae": vae_ref},
    }
    _add_output(wf, ["decode", 0], fps,
                WAN_RIFE_MULTIPLIER if interpolate else 1, filename_prefix)
    return wf


def build_wan_5b_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    steps: int = 20,
    cfg: float = 5.0,
    width: int,
    height: int,
    length: int = 121,
    fps: int = 24,
    sampler: str = "euler",
    shift: float = 8.0,
    interpolate: bool = False,
    image_name: str,
    filename_prefix: str = "wan/Wan22TI2V5B",
) -> dict:
    """Build a Wan 2.2 TI2V 5B image-to-video workflow (ComfyUI API format).

    Much simpler than the 14B graph: one dense model, one KSampler. The
    start image enters through Wan22ImageToVideoLatent, which VAE-encodes
    it into the first latent frame and hands the sampler a noise mask so
    that frame is kept. Uses the Wan **2.2** VAE (16× spatial compression
    — width/height must be multiples of 32) and runs at 24 fps; 121
    frames ≈ 5 s. There is no Lightning distillation for this model, so
    there is no turbo/raw split. `interpolate` runs RIFE at
    WAN_5B_RIFE_MULTIPLIER, so the clip is saved at 48 fps.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": WAN_5B_UNET, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": WAN_TEXT_ENCODER, "type": "wan",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": WAN_5B_VAE},
        },
    }
    model_ref = ["unet", 0]
    clip_ref, vae_ref = ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    wf["shift"] = {
        "class_type": "ModelSamplingSD3",
        "inputs": {"model": model_ref, "shift": float(shift)},
    }
    model_ref = ["shift", 0]

    wf["positive"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": prompt, "clip": clip_ref},
    }
    if negative.strip():
        wf["negative"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": clip_ref},
        }
    else:
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        }

    wf["source"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    wf["latent"] = {
        "class_type": "Wan22ImageToVideoLatent",
        "inputs": {"vae": vae_ref, "width": int(width), "height": int(height),
                   "length": int(length), "batch_size": 1,
                   "start_image": ["source", 0]},
    }
    wf["sampler"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": sampler, "scheduler": "simple", "denoise": 1.0,
            "model": model_ref, "positive": ["positive", 0],
            "negative": ["negative", 0], "latent_image": ["latent", 0],
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    _add_output(wf, ["decode", 0], fps,
                WAN_5B_RIFE_MULTIPLIER if interpolate else 1, filename_prefix)
    return wf
