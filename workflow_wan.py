"""Wan 2.2 image-to-video workflow builder (ComfyUI API format).

Wan 2.2 I2V A14B is a two-expert MoE: a high-noise 14B model denoises the
early sampler steps and a low-noise 14B model finishes the late ones, so
the workflow chains two KSamplerAdvanced passes (the first returns its
leftover noise to the second). Only nodes that ship with current ComfyUI
are used: UNETLoader ×2, CLIPLoader(type="wan"), VAELoader,
LoraLoaderModelOnly, ModelSamplingSD3, WanImageToVideo, KSamplerAdvanced,
VAEDecode, CreateVideo and SaveVideo — no custom node packs.

Turbo mode applies the lightx2v "Lightning" 4-step distillation LoRAs
(one per expert, strength 1.0) with CFG 1.0 — the same idea as the Krea 2
Turbo checkpoint, delivered as LoRAs because the base weights are shared
with raw mode. Raw mode is the undistilled 20-step CFG 3.5 schedule.

The alternative TI2V 5B model (build_wan_5b_workflow) is a single dense
model with its own Wan 2.2 VAE and a plain one-KSampler graph — lighter
in VRAM, 24 fps, no turbo/raw split.

Both builders accept `segments`: with segments > 1 the sampler group is
repeated, each repeat starting from the previous segment's last decoded
frame (ImageFromBatch), so clips can run past the 5 s training length.
Every segment is saved as its own clip plus one stitched full-length
video — all with stock nodes, no app-side frame extraction or ffmpeg.
"""

from comfy import GPU_COUNT
from config import (
    MODELS_DIR,
    WAN_5B_UNET,
    WAN_5B_VAE,
    WAN_HIGH_UNET,
    WAN_LIGHTNING_HIGH,
    WAN_LIGHTNING_LOW,
    WAN_LOW_UNET,
    WAN_TEXT_ENCODER,
    WAN_VAE,
)


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


def _decoded_frames(length: int) -> int:
    """Frames the Wan VAE actually decodes: 4n+1 (81 stays 81, 80 → 77)."""
    return (int(length) - 1) // 4 * 4 + 1


def _append_video_outputs(wf: dict, decode_refs: list, *, fps: int,
                          frames: int, filename_prefix: str) -> None:
    """Attach CreateVideo/SaveVideo nodes for the decoded segment(s).

    One segment keeps the original graph shape (a single "video" + "save"
    pair). With several, each segment is also saved on its own (prefix
    _seg1, _seg2, ... — if a later segment drifts, the earlier clips
    survive) and the stitched full video is saved under the plain prefix.
    Segment k > 0 starts with a re-encode of segment k-1's last frame, so
    that duplicate first frame is dropped before stitching.
    """
    def save_video(key: str, images_ref: list, prefix: str) -> None:
        wf[f"video{key}"] = {
            "class_type": "CreateVideo",
            "inputs": {"images": images_ref, "fps": float(fps)},
        }
        wf[f"save{key}"] = {
            "class_type": "SaveVideo",
            "inputs": {"video": [f"video{key}", 0],
                       "filename_prefix": prefix,
                       "format": "mp4", "codec": "h264"},
        }

    if len(decode_refs) == 1:
        save_video("", decode_refs[0], filename_prefix)
        return
    parts = [decode_refs[0]]
    for k, decode_ref in enumerate(decode_refs, start=1):
        save_video(f"_seg{k}", decode_ref, f"{filename_prefix}_seg{k}")
        if k > 1:
            wf[f"trim_seg{k}"] = {
                "class_type": "ImageFromBatch",
                "inputs": {"image": decode_ref, "batch_index": 1,
                           "length": frames - 1},
            }
            parts.append([f"trim_seg{k}", 0])
    joined = parts[0]
    for k, part in enumerate(parts[1:], start=2):
        wf[f"join_seg{k}"] = {
            "class_type": "ImageBatch",
            "inputs": {"image1": joined, "image2": part},
        }
        joined = [f"join_seg{k}", 0]
    save_video("", joined, filename_prefix)


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
    segments: int = 1,
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
    schedules. `segments` > 1 repeats the whole sampler group that many
    times, feeding each repeat the previous segment's last frame as its
    start image (same prompt, seed+k) — the frame-chaining way to get
    clips longer than the 5 s training length.
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

    boundary = max(1, int(steps) // 2)
    frames = _decoded_frames(length)
    segments = max(1, int(segments))
    start_ref = ["source", 0]
    decode_refs = []
    for k in range(segments):
        sfx = "" if k == 0 else f"_{k + 1}"
        wf[f"i2v{sfx}"] = {
            "class_type": "WanImageToVideo",
            "inputs": {
                "positive": ["positive", 0], "negative": ["negative", 0],
                "vae": vae_ref, "width": int(width), "height": int(height),
                "length": int(length), "batch_size": 1,
                "start_image": start_ref,
            },
        }
        wf[f"sampler_high{sfx}"] = {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                "add_noise": "enable", "noise_seed": int(seed) + k,
                "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": "simple",
                "start_at_step": 0, "end_at_step": boundary,
                "return_with_leftover_noise": "enable",
                "model": high_ref, "positive": [f"i2v{sfx}", 0],
                "negative": [f"i2v{sfx}", 1], "latent_image": [f"i2v{sfx}", 2],
            },
        }
        wf[f"sampler_low{sfx}"] = {
            "class_type": "KSamplerAdvanced",
            "inputs": {
                "add_noise": "disable", "noise_seed": 0,
                "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": "simple",
                "start_at_step": boundary, "end_at_step": 10000,
                "return_with_leftover_noise": "disable",
                "model": low_ref, "positive": [f"i2v{sfx}", 0],
                "negative": [f"i2v{sfx}", 1],
                "latent_image": [f"sampler_high{sfx}", 0],
            },
        }
        wf[f"decode{sfx}"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [f"sampler_low{sfx}", 0], "vae": vae_ref},
        }
        decode_refs.append([f"decode{sfx}", 0])
        if k + 1 < segments:
            wf[f"last_frame{sfx}"] = {
                "class_type": "ImageFromBatch",
                "inputs": {"image": [f"decode{sfx}", 0],
                           "batch_index": frames - 1, "length": 1},
            }
            start_ref = [f"last_frame{sfx}", 0]

    _append_video_outputs(wf, decode_refs, fps=fps, frames=frames,
                          filename_prefix=filename_prefix)
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
    segments: int = 1,
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
    there is no turbo/raw split. `segments` chains sampler groups off the
    previous segment's last frame, exactly as in the 14B builder.
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

    frames = _decoded_frames(length)
    segments = max(1, int(segments))
    start_ref = ["source", 0]
    decode_refs = []
    for k in range(segments):
        sfx = "" if k == 0 else f"_{k + 1}"
        wf[f"latent{sfx}"] = {
            "class_type": "Wan22ImageToVideoLatent",
            "inputs": {"vae": vae_ref, "width": int(width),
                       "height": int(height), "length": int(length),
                       "batch_size": 1, "start_image": start_ref},
        }
        wf[f"sampler{sfx}"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(seed) + k, "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": "simple", "denoise": 1.0,
                "model": model_ref, "positive": ["positive", 0],
                "negative": ["negative", 0],
                "latent_image": [f"latent{sfx}", 0],
            },
        }
        wf[f"decode{sfx}"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [f"sampler{sfx}", 0], "vae": vae_ref},
        }
        decode_refs.append([f"decode{sfx}", 0])
        if k + 1 < segments:
            wf[f"last_frame{sfx}"] = {
                "class_type": "ImageFromBatch",
                "inputs": {"image": [f"decode{sfx}", 0],
                           "batch_index": frames - 1, "length": 1},
            }
            start_ref = [f"last_frame{sfx}", 0]

    _append_video_outputs(wf, decode_refs, fps=fps, frames=frames,
                          filename_prefix=filename_prefix)
    return wf
