"""MiniMax H3 video workflow builder (ComfyUI API format) — with sound.

One builder for two tabs. MiniMax H3 is a packed audio+video DiT: a single
sampler pass produces the frames and the soundtrack together, and the core
`MiniMaxH3ImageToVideo` node takes an *optional* first frame — so
image-to-video and text-to-video are the same graph with `LoadImage`
present or absent. That is the whole difference between the two tabs.

Transcribed from the two "Custom Prompt" workflows in
hearmeman/comfyui-minimax-template:v8 (kept under tmp/minimax-reference/,
which is not shipped). Every node used here is core ComfyUI:
UNETLoader, CLIPLoader(type="minimax"), VAELoader ×2 (video + audio),
LoraLoaderModelOnly, MiniMaxH3ImageToVideo, BasicGuider, RandomNoise,
KSamplerSelect, BasicScheduler, SamplerCustomAdvanced, VAEDecode,
VAEDecodeAudio, CreateVideo and SaveVideo.

rgthree's Power Lora Loader, where the template puts a character LoRA,
became a chain of core LoraLoaderModelOnly nodes — the same translation
the V2 tab makes of it, so the pack is not installed. The chain sits
where the template has the loader, between the diffusion model and the
turbo LoRA. The template also routes CLIP through it, but a character
LoRA carries no text-encoder weights, so model-only is the same graph.
The tabs offer the LoRAs the catalogue lists for their feature
(catalog.py), none switched on by default.

Three things the template had were left out on purpose:

  * KJNodes' ModelPreviewOverrideKJ — a latent-preview override, cosmetic;
  * VHS_VideoCombine — CreateVideo takes an `audio` input, and it plus
    SaveVideo is what the Wan tab already writes MP4s with;
  * ExtendIntermediateSigmas — the template bypasses it (mode 4). It
    belonged to the old 4-step turbo LoRA and was switched off when the
    template moved to the 8-step one used here; running it would add two
    sampler steps the LoRA was not tuned for.

So no node pack is installed for this feature. What it needs instead is a
ComfyUI that has the MiniMax nodes at all: they ship in **v0.34.0**, which
is why scripts/PINS.json moved there. comfy.verify_core_node reports a
checkout that predates it at startup.

There is no negative prompt and no CFG: like Flux, the model is
guidance-distilled and runs through a BasicGuider. The 8-step turbo LoRA
is always on — the template titles it "Always On, Don't Touch" — and the
scheduler block below is that LoRA's recipe.
"""

import math

from ember.comfy.server import GPU_COUNT
from ember.settings import MODELS_DIR
from ember.pipelines.minimax.constants import (
    MINIMAX_ASPECT_RATIOS,
    MINIMAX_AUDIO_VAE,
    MINIMAX_CANVAS_MULTIPLE,
    MINIMAX_DEFAULT_RESOLUTION,
    MINIMAX_DEFAULTS,
    MINIMAX_FPS,
    MINIMAX_FRAME_OFFSET,
    MINIMAX_FRAME_STEP,
    MINIMAX_MAX_SIDE,
    MINIMAX_MIN_SIDE,
    MINIMAX_NATIVE_MAX_PIXELS,
    MINIMAX_NATIVE_SHORT_EDGE,
    MINIMAX_RESOLUTIONS,
    MINIMAX_STANDARD_MEGAPIXELS,
    MINIMAX_TEXT_ENCODER,
    MINIMAX_TURBO_LORA,
    MINIMAX_TURBO_LORA_STRENGTH,
    MINIMAX_UNET,
    MINIMAX_VIDEO_VAE,
)


def _required() -> dict:
    """Local path of every file both tabs load, by the name a message uses."""
    return {
        "diffusion model": MODELS_DIR / "diffusion_models" / MINIMAX_UNET,
        "text encoder": MODELS_DIR / "text_encoders" / MINIMAX_TEXT_ENCODER,
        "video VAE": MODELS_DIR / "vae" / MINIMAX_VIDEO_VAE,
        "audio VAE": MODELS_DIR / "vae" / MINIMAX_AUDIO_VAE,
        "turbo LoRA": MODELS_DIR / "loras" / MINIMAX_TURBO_LORA,
    }


def minimax_missing() -> list[str]:
    """Which of the five files are not on disk yet, by human name."""
    return [name for name, path in _required().items() if not path.exists()]


def minimax_models_available() -> bool:
    """True once every file the graph loads has been downloaded."""
    return not minimax_missing()


def frames_for(seconds: float) -> int:
    """Seconds -> a frame count on the model's 17k+5 grid, at 24 fps.

    The node snaps whatever it is handed the same way (align_frame_count in
    comfy_extras/nodes_minimax_h3.py), so passing the snapped number
    changes nothing in the graph — it is done here so the status line and
    the recipe say the frame count that actually runs. 5 s is 124 frames,
    the node's default; 15 s is 362, the top of the trained range.
    """
    frames = max(MINIMAX_FRAME_OFFSET,
                 int(round(float(seconds) * MINIMAX_FPS)))
    remainder = frames % MINIMAX_FRAME_STEP
    return frames + (MINIMAX_FRAME_OFFSET - remainder) % MINIMAX_FRAME_STEP


def _snap(value: float) -> int:
    """One side: rounded to the canvas multiple, kept within [256, 1536]."""
    multiple = MINIMAX_CANVAS_MULTIPLE
    return max(MINIMAX_MIN_SIDE,
               min(MINIMAX_MAX_SIDE, int(round(value / multiple)) * multiple))


def _fit_to_image(nom_w: float, nom_h: float) -> tuple:
    """Match image's canvas: the nominal size in 32s, keeping its shape.

    Standard and native round each side to the nearest 32 and clamp each
    side on its own (_snap) — that is their template's and node's
    arithmetic, and it is left alone. Both bend the shape: rounding by up
    to 2-3% (720 × 1280 -> 704 × 1280), and the clamp by a third for a
    panorama (3000 × 600 -> 1536 × 448). This rule exists to keep the
    picture's shape, so it does neither. The long edge is brought under
    1536 and the short edge over 256 by scaling the pair together; then
    each side is tried rounded down and up, and the pair nearest the
    nominal shape wins (720 × 1280 -> 736 × 1312, 0.3% off), the area
    nearest nominal breaking a tie. Past about 6:1 the two limits cannot
    both hold and the shape gives way; crop_to_canvas trims the source to
    match, so even then nothing stretches.
    """
    long_edge, short_edge = max(nom_w, nom_h), min(nom_w, nom_h)
    if long_edge > MINIMAX_MAX_SIDE:
        shrink = MINIMAX_MAX_SIDE / long_edge
        nom_w, nom_h = nom_w * shrink, nom_h * shrink
        short_edge *= shrink
    if short_edge < MINIMAX_MIN_SIDE:
        grow = MINIMAX_MIN_SIDE / short_edge
        nom_w, nom_h = nom_w * grow, nom_h * grow

    multiple = MINIMAX_CANVAS_MULTIPLE

    def sides(value):
        down = math.floor(value / multiple) * multiple
        return {max(MINIMAX_MIN_SIDE, min(MINIMAX_MAX_SIDE, side))
                for side in (down, down + multiple)}

    ratio, area = nom_w / nom_h, nom_w * nom_h
    return min(((w, h) for w in sides(nom_w) for h in sides(nom_h)),
               key=lambda wh: (abs(math.log(wh[0] / wh[1] / ratio)),
                               abs(math.log(wh[0] * wh[1] / area))))


def matches_image(resolution: str) -> bool:
    """True for the Match image rule — the one that crops its source."""
    return MINIMAX_RESOLUTIONS.get(resolution) == "source"


def resolve_size(width: int, height: int, resolution: str) -> tuple:
    """The canvas for a source (or aspect) of width × height.

    Three rules, one per entry of MINIMAX_RESOLUTIONS:

      standard  ResolutionSelector's arithmetic at 0.7 MP — the template's
                setting: scale so the area is 0.7 × 1024², round each side
                to 32.
      native    adapt_canvas from the node file: a 768 short edge, the area
                capped at 768 × 1344, each side rounded to 32. It is what
                the model was trained on, and 40-50% more pixels.
      source    Match image, opt-in: the picture's own size, in 32s that
                keep its shape (_fit_to_image) — scaled down, aspect kept,
                only when it is bigger than native's 768 × 1344 area,
                because past that the model is out of its trained range and
                the clip gets much slower. A small picture stays small: it
                never upscales, beyond the 256 floor. The handler crops the
                source to this canvas (crop_to_canvas) so the node's
                stretch has nothing left to bend.

    Only the ratio of width to height matters to standard and native, so
    the text-to-video tab passes the two small integers of an aspect ratio
    and the image tab passes the picture's pixel size. Sides are kept
    within [256, 1536] so a panorama cannot ask for a 3000-pixel canvas.
    Source reads the size itself, so it is image-only; aspect_size falls
    back to standard.
    """
    rule = MINIMAX_RESOLUTIONS[resolution]
    if rule == "source":
        nom_w, nom_h = float(width), float(height)
        if nom_w * nom_h > MINIMAX_NATIVE_MAX_PIXELS:
            shrink = math.sqrt(MINIMAX_NATIVE_MAX_PIXELS / (nom_w * nom_h))
            nom_w, nom_h = nom_w * shrink, nom_h * shrink
        return _fit_to_image(nom_w, nom_h)
    if rule == "native":
        ratio = width / height
        if ratio >= 1.0:
            nom_w = MINIMAX_NATIVE_SHORT_EDGE * ratio
            nom_h = MINIMAX_NATIVE_SHORT_EDGE
        else:
            nom_w = MINIMAX_NATIVE_SHORT_EDGE
            nom_h = MINIMAX_NATIVE_SHORT_EDGE / ratio
        if nom_w * nom_h > MINIMAX_NATIVE_MAX_PIXELS:
            shrink = math.sqrt(MINIMAX_NATIVE_MAX_PIXELS / (nom_w * nom_h))
            nom_w, nom_h = nom_w * shrink, nom_h * shrink
        return _snap(nom_w), _snap(nom_h)
    total = MINIMAX_STANDARD_MEGAPIXELS * 1024 * 1024
    scale = math.sqrt(total / (width * height))
    return _snap(width * scale), _snap(height * scale)


def aspect_size(aspect: str, resolution: str) -> tuple:
    """resolve_size for one of MINIMAX_ASPECT_RATIOS' labels.

    The text tab does not offer Match image, but a stale preset could
    still carry it; with no picture to take a size from, it means standard.
    """
    w_ratio, h_ratio = MINIMAX_ASPECT_RATIOS[aspect]
    if matches_image(resolution):
        resolution = MINIMAX_DEFAULT_RESOLUTION
    return resolve_size(w_ratio, h_ratio, resolution)


def crop_to_canvas(image, width: int, height: int):
    """The source, centre-cropped to the canvas's shape, so nothing stretches.

    MiniMaxH3ImageToVideo resizes the first frame to the canvas with crop
    "disabled" — a plain stretch (its own comment: "geometry anchor: plain
    stretch to canvas"). A canvas in 32s is almost never exactly the
    picture's shape, so cropping first turns that stretch into a uniform
    scale. Match image only; standard and native hand the node the whole
    picture as before. The trim is a thin strip off two opposite edges —
    under 1% for ordinary shapes, more only past the ~6:1 _fit_to_image
    can honour. `image` is a PIL image; the same object comes back when it
    already fits.
    """
    src_w, src_h = image.size
    target = width / height
    if src_w / src_h > target:
        new_w = max(1, round(src_h * target))
        left = (src_w - new_w) // 2
        box = (left, 0, left + new_w, src_h)
    else:
        new_h = max(1, round(src_w / target))
        top = (src_h - new_h) // 2
        box = (0, top, src_w, top + new_h)
    if box == (0, 0, src_w, src_h):
        return image
    return image.crop(box)


def build_minimax_video_workflow(
    *,
    prompt: str,
    seed: int = 0,
    steps: int = 8,
    width: int,
    height: int,
    length: int = 124,
    fps: int = 24,
    sampler: str = "euler",
    image_name: str | None = None,
    loras=(),
    filename_prefix: str = "minimax/MiniMaxH3",
) -> dict:
    """Build the MiniMax H3 graph in ComfyUI API format.

    `image_name` is a file already uploaded to ComfyUI's input folder
    (client.upload_image); it becomes the first frame, resized to the
    canvas by the node, which is why the caller derives width and height
    from the picture and crops it to that shape first (crop_to_canvas). None is text-to-video: the same graph
    with no LoadImage, and the node builds the clip from the prompt alone.

    `length` is the frame count on the 17k+5 grid (frames_for) and `fps`
    is what CreateVideo stamps on the MP4 — 24, the rate the model runs
    at. The soundtrack comes out of the same latent through the audio VAE
    and rides along on CreateVideo's `audio` input.

    `loras` is a sequence of (filename, strength) pairs, already resolved
    (loras._resolve_lora_slots) — applied in order, ahead of the turbo
    LoRA, as the template chains them.
    """
    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": MINIMAX_UNET, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": MINIMAX_TEXT_ENCODER, "type": "minimax",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": MINIMAX_VIDEO_VAE},
        },
        "audio_vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": MINIMAX_AUDIO_VAE},
        },
    }
    model_ref = ["unet", 0]
    clip_ref, vae_ref = ["clip", 0], ["vae", 0]
    audio_vae_ref = ["audio_vae", 0]

    if GPU_COUNT >= 2:
        # Same placement plan as the Krea and Wan graphs: the 21 GB
        # diffusion model alone on gpu:0, the 27 GB text encoder and the
        # video VAE on gpu:1. The audio VAE is 0.6 GB and stays put.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    for i, (lora_file, weight) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": lora_file, "strength_model": float(weight),
                       "model": model_ref},
        }
        model_ref = [node, 0]

    # Always on. The scheduler settings below are this LoRA's recipe, so
    # there is no raw mode to switch it off for — see the module docstring.
    wf["turbo"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"lora_name": MINIMAX_TURBO_LORA,
                   "strength_model": float(MINIMAX_TURBO_LORA_STRENGTH),
                   "model": model_ref},
    }
    model_ref = ["turbo", 0]

    conditioning = {
        "clip": clip_ref, "vae": vae_ref, "prompt": prompt,
        "width": int(width), "height": int(height), "length": int(length),
    }
    if image_name is not None:
        wf["source"] = {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        }
        conditioning["first_frame"] = ["source", 0]
    # Outputs: 0 = positive conditioning, 1 = the empty audio+video latent.
    wf["i2v"] = {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": conditioning,
    }

    wf["guider"] = {
        "class_type": "BasicGuider",
        "inputs": {"model": model_ref, "conditioning": ["i2v", 0]},
    }
    wf["noise"] = {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": int(seed)},
    }
    wf["sampler"] = {
        "class_type": "KSamplerSelect",
        "inputs": {"sampler_name": sampler},
    }
    wf["sigmas"] = {
        "class_type": "BasicScheduler",
        "inputs": {"model": model_ref,
                   "scheduler": MINIMAX_DEFAULTS["scheduler"],
                   "steps": int(steps), "denoise": 1.0},
    }
    wf["sample"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {"noise": ["noise", 0], "guider": ["guider", 0],
                   "sampler": ["sampler", 0],
                   "sigmas": ["sigmas", 0],
                   "latent_image": ["i2v", 1]},
    }
    # The one latent carries both streams; each VAE decodes its half.
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sample", 0], "vae": vae_ref},
    }
    wf["decode_audio"] = {
        "class_type": "VAEDecodeAudio",
        "inputs": {"samples": ["sample", 0], "vae": audio_vae_ref},
    }
    wf["video"] = {
        "class_type": "CreateVideo",
        "inputs": {"images": ["decode", 0], "fps": float(fps),
                   "audio": ["decode_audio", 0]},
    }
    wf["save"] = {
        "class_type": "SaveVideo",
        "inputs": {"video": ["video", 0], "filename_prefix": filename_prefix,
                   "format": "mp4", "codec": "h264"},
    }
    return wf
