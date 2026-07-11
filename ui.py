"""Gradio UI.

Four tabs: single / simple-batch generation, inpainting (paint a mask
over an uploaded image), JSON batch jobs, and an output gallery. Written
for Gradio 6 (theme/css now belong to launch(), gr.File hands the handler
a plain file path).

Some hosts' networks break Gradio's *.gradio.live share tunnel (the link
504s even though the app is healthy), so after launching we probe the
share URL from inside the pod and, if it does not answer, start a
Cloudflare quick tunnel and print that URL instead. launch_ui() blocks
while the UI is live — press Ctrl-C to shut it down.
"""

import io
import json
import random
import re
import subprocess
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

import gradio as gr
import requests
from PIL import Image, ImageChops, ImageFilter

from client import ComfyUIError, client
from comfy import GPU_COUNT
from config import (
    DEFAULT_RESOLUTION,
    KREA2_VARIANT,
    OUTPUT_DIR,
    RESOLUTION_PRESETS,
    SAMPLERS,
    TEMP_DIR,
    VARIANT_DEFAULTS,
    log,
)
from workflow import (
    build_inpaint_workflow,
    build_workflow,
    list_lora_files,
    resolve_lora_name,
)

MAX_LORA_SLOTS = 3
DEFAULTS = VARIANT_DEFAULTS[KREA2_VARIANT]
LORA_CHOICES = ["None"] + list_lora_files()


def _snap(value, lo: int = 512, hi: int = 2048) -> int:
    """Clamp to [lo, hi] and round to a multiple of 16 (Krea 2 requirement)."""
    return max(lo, min(hi, int(round(int(value) / 16)) * 16))


def parse_resolution(value) -> tuple:
    """Accept a preset label or free-form 'WxH' / 'W×H' text."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return _snap(value[0]), _snap(value[1])
    text = str(value or "").strip()
    if text in RESOLUTION_PRESETS:
        return RESOLUTION_PRESETS[text]
    match = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)
    if match:
        return _snap(match.group(1)), _snap(match.group(2))
    compact = text.lower().replace(" ", "")
    for label, wh in RESOLUTION_PRESETS.items():
        if compact and compact.split("(")[0] in label.lower().replace(" ", ""):
            return wh
    return RESOLUTION_PRESETS[DEFAULT_RESOLUTION]


def _normalize_jobs(raw) -> list:
    """Normalize parsed JSON into the job dicts _run_jobs expects."""
    if isinstance(raw, dict) and "prompts" in raw:
        raw = raw["prompts"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(
            "JSON must be a job object, a list of jobs, or {'prompts': [...]}"
        )
    jobs = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"Every job must be a JSON object, got: {item!r}")
        loras = dict(item.get("loras", {}))
        for slot in range(1, MAX_LORA_SLOTS + 1):  # legacy flat keys lora1/lora1_w
            name = item.get(f"lora{slot}")
            if name and str(name).lower() != "none":
                loras[name] = item.get(f"lora{slot}_w", 0.8)
        resolved = []
        for name, weight in list(loras.items())[:MAX_LORA_SLOTS]:
            lora_file = resolve_lora_name(name)
            if lora_file:
                resolved.append((lora_file, float(weight)))
        width, height = parse_resolution(item.get("resolution"))
        sampler = item.get("sampler", SAMPLERS[0])
        jobs.append({
            "prompt": item.get("prompt", ""),
            "negative": item.get("negative", ""),
            "seed": int(item.get("seed", random.randint(0, 2**32 - 1))),
            "steps": int(item.get("steps", DEFAULTS["steps"])),
            "cfg": float(item.get("cfg", DEFAULTS["cfg"])),
            "width": width,
            "height": height,
            "sampler": sampler if sampler in SAMPLERS else SAMPLERS[0],
            "loras": resolved,
        })
    return jobs


def _run_jobs(jobs, builder=build_workflow, prefix="Krea2"):
    """Shared executor: yields (gallery_paths, status_text) as work progresses."""
    images = []
    total = len(jobs)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        job_prefix = prefix
        if job["loras"]:
            job_prefix += "_" + Path(job["loras"][0][0]).stem
        workflow = builder(filename_prefix=job_prefix, **job)
        size = f", {job['width']}×{job['height']}" if "width" in job else ""
        yield images, f"⏳ Job {label} — queued (seed {job['seed']}{size})"
        try:
            for event in client.run(workflow):
                if event["type"] == "progress" and event["total"]:
                    yield images, f"⏳ Job {label} — step {event['step']}/{event['total']}"
                elif event["type"] == "done":
                    images.extend(event["images"])
                    yield images, f"✅ Job {label} finished"
        except ComfyUIError as exc:
            yield images, f"❌ Job {label} failed: {exc}"
            return
    yield images, f"✅ All {total} job(s) done — images saved under {OUTPUT_DIR}"


def generate_single(prompt, negative, seed, randomize, steps, cfg, resolution,
                    sampler, lora1, w1, lora2, w2, lora3, w3, batch_count):
    """First tab: run batch_count jobs on sequential seeds."""
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    width, height = parse_resolution(resolution)
    loras = []
    for name, weight in ((lora1, w1), (lora2, w2), (lora3, w3)):
        lora_file = resolve_lora_name(name)
        if lora_file:
            loras.append((lora_file, float(weight)))
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "width": width, "height": height,
        "sampler": sampler, "loras": loras,
    } for i in range(int(batch_count))]
    for images, status in _run_jobs(jobs):
        yield images, status, base_seed


def _prepare_inpaint_inputs(editor_value, grow_px: int, blur_px: int):
    """ImageEditor value → (RGB image, L-mode mask or None), sized for the VAE.

    The mask is the union of the painted layers' alpha channels, optionally
    dilated (grow) and gaussian-blurred for a soft transition; None when
    nothing is painted (full-image img2img). Both images are downscaled so
    the long side is ≤ 2048 (never upscaled) and snapped to multiples of
    16, which Krea 2's VAE requires.
    """
    if not isinstance(editor_value, dict) or editor_value.get("background") is None:
        raise ValueError("Upload an image first.")
    background = editor_value["background"].convert("RGB")
    mask = None
    for layer in editor_value.get("layers") or []:
        if "A" not in layer.getbands():
            continue
        alpha = layer.getchannel("A")
        if alpha.size != background.size:
            alpha = alpha.resize(background.size)
        mask = alpha if mask is None else ImageChops.lighter(mask, alpha)
    if mask is not None and mask.getbbox() is None:
        mask = None
    if mask is not None:
        if grow_px:
            mask = mask.filter(ImageFilter.MaxFilter(grow_px * 2 + 1))
        if blur_px:
            mask = mask.filter(ImageFilter.GaussianBlur(blur_px))
    w, h = background.size
    scale = min(1.0, 2048 / max(w, h))
    w2 = max(64, int(w * scale) // 16 * 16)
    h2 = max(64, int(h * scale) // 16 * 16)
    if (w2, h2) != (w, h):
        background = background.resize((w2, h2), Image.LANCZOS)
        if mask is not None:
            mask = mask.resize((w2, h2), Image.LANCZOS)
    return background, mask


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def generate_inpaint(editor_value, prompt, negative, seed, randomize, steps,
                     cfg, denoise, sampler, grow, blur, batch_count):
    """Inpaint tab: repaint the painted region — or, with nothing painted,
    run the whole image through img2img at the chosen denoise."""
    try:
        image, mask = _prepare_inpaint_inputs(editor_value, int(grow), int(blur))
    except ValueError as exc:
        yield [], f"❌ {exc}", 0
        return
    if mask is None and float(denoise) >= 1.0:
        yield [], (
            "❌ Nothing is painted, so this would run as full-image img2img — "
            "but Denoise 1.0 would ignore the source image entirely. Lower "
            "Denoise (e.g. 0.5–0.8) or paint the region to replace."
        ), 0
        return
    base_seed = random.randint(0, 2**32 - 1) if randomize else int(seed)
    tag = uuid.uuid4().hex[:8]
    try:
        image_name = client.upload_image(_png_bytes(image), f"inpaint_{tag}.png")
        mask_name = (client.upload_image(_png_bytes(mask),
                                         f"inpaint_{tag}_mask.png")
                     if mask is not None else None)
    except Exception as exc:
        yield [], f"❌ Uploading the image to ComfyUI failed: {exc}", base_seed
        return
    jobs = [{
        "prompt": prompt, "negative": negative or "", "seed": base_seed + i,
        "steps": int(steps), "cfg": float(cfg), "denoise": float(denoise),
        "sampler": sampler, "image_name": image_name, "mask_name": mask_name,
        "loras": [],
    } for i in range(int(batch_count))]
    prefix = "Krea2Inpaint" if mask is not None else "Krea2Img2Img"
    for images, status in _run_jobs(jobs, builder=build_inpaint_workflow,
                                    prefix=prefix):
        yield images, status, base_seed


def generate_from_json(json_file, json_text):
    """JSON tab: file upload takes precedence over pasted text."""
    try:
        if json_file:
            raw = json.loads(Path(json_file).read_text())
        elif json_text and json_text.strip():
            raw = json.loads(json_text)
        else:
            yield [], "❌ Provide a JSON file or paste JSON text."
            return
        jobs = _normalize_jobs(raw)
    except (ValueError, OSError) as exc:
        yield [], f"❌ Invalid JSON: {exc}"
        return
    yield from _run_jobs(jobs)


def refresh_lora_choices():
    """Re-scan the LoRA folder (e.g. after dropping new files into it)."""
    choices = ["None"] + list_lora_files()
    return [gr.Dropdown(choices=choices) for _ in range(MAX_LORA_SLOTS)]


def list_output_images() -> list[str]:
    """Every generated image in OUTPUT_DIR, newest first."""
    return [str(p) for p in sorted(OUTPUT_DIR.rglob("*.png"),
                                   key=lambda p: p.stat().st_mtime, reverse=True)]


def refresh_gallery():
    """Re-scan OUTPUT_DIR for the Gallery tab."""
    images = list_output_images()
    return images, f"{len(images)} image(s) in `{OUTPUT_DIR}`"


def zip_outputs():
    """Bundle all generated images into one zip (the pod disk is ephemeral)."""
    images = list_output_images()
    if not images:
        return None, "No images to zip yet."
    # PNGs are already compressed — store instead of deflating (much faster).
    zip_path = OUTPUT_DIR / "all_outputs.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for img in images:
            zf.write(img, Path(img).relative_to(OUTPUT_DIR))
    size_mb = zip_path.stat().st_size / 1e6
    return str(zip_path), f"📦 Zipped {len(images)} image(s) ({size_mb:.0f} MB)"


with gr.Blocks(title="Krea 2 on RunPod") as ui:
    gr.Markdown(
        f"# ⚡ Krea 2 {KREA2_VARIANT.title()} — ComfyUI on RunPod\n"
        f"{GPU_COUNT} GPU(s) detected · native ComfyUI multi-GPU placement · "
        f"outputs saved to `{OUTPUT_DIR}`"
    )
    with gr.Tabs():
        with gr.Tab("Single / Simple Batch"):
            with gr.Row():
                with gr.Column(scale=2):
                    prompt_box = gr.Textbox(
                        label="Prompt", lines=5,
                        value="A photorealistic golden-hour portrait, natural "
                              "skin texture, shallow depth of field",
                    )
                    negative_box = gr.Textbox(
                        label="Negative prompt (only used when CFG > 1)", lines=2
                    )
                    with gr.Row():
                        steps_slider = gr.Slider(
                            1, 60, value=DEFAULTS["steps"], step=1, label="Steps"
                        )
                        cfg_slider = gr.Slider(
                            0.5, 8.0, value=DEFAULTS["cfg"], step=0.1, label="CFG"
                        )
                    with gr.Row():
                        resolution_dd = gr.Dropdown(
                            choices=list(RESOLUTION_PRESETS),
                            value=DEFAULT_RESOLUTION, label="Resolution",
                        )
                        sampler_dd = gr.Dropdown(
                            choices=SAMPLERS, value=SAMPLERS[0], label="Sampler"
                        )
                    with gr.Row():
                        seed_box = gr.Number(label="Seed", value=42, precision=0)
                        randomize_cb = gr.Checkbox(label="🎲 Random seed", value=True)
                        batch_slider = gr.Slider(
                            1, 20, value=1, step=1, label="Batch count"
                        )
                    gr.Markdown("### 🎭 LoRA stack")
                    lora_dds, lora_ws = [], []
                    for slot in range(1, MAX_LORA_SLOTS + 1):
                        with gr.Row():
                            lora_dds.append(gr.Dropdown(
                                choices=LORA_CHOICES, value="None",
                                label=f"LoRA {slot}", scale=3,
                            ))
                            lora_ws.append(gr.Slider(
                                0.0, 2.0, value=0.8, step=0.05,
                                label="Weight", scale=1,
                            ))
                    refresh_btn = gr.Button("🔄 Rescan LoRA folder", size="sm")
                    generate_btn = gr.Button(
                        "🚀 Generate", variant="primary", size="lg"
                    )
                with gr.Column(scale=3):
                    gallery = gr.Gallery(label="Output", columns=2, height=600)
                    status_box = gr.Textbox(label="Status", interactive=False)
                    seed_out = gr.Number(
                        label="Base seed used", interactive=False, precision=0
                    )
            generate_btn.click(
                fn=generate_single,
                inputs=[prompt_box, negative_box, seed_box, randomize_cb,
                        steps_slider, cfg_slider, resolution_dd, sampler_dd,
                        lora_dds[0], lora_ws[0], lora_dds[1], lora_ws[1],
                        lora_dds[2], lora_ws[2], batch_slider],
                outputs=[gallery, status_box, seed_out],
            )
            refresh_btn.click(fn=refresh_lora_choices, outputs=lora_dds)

        with gr.Tab("Inpaint / Img2Img"):
            gr.Markdown(
                "Upload an image, **paint over the region to replace**, and "
                "describe what should appear there — unpainted pixels are "
                "kept from the original. Paint **nothing** to re-imagine the "
                "whole image (img2img); in that mode lower **Denoise** "
                "(≈0.5–0.8) to control how much of the original survives."
            )
            with gr.Row():
                with gr.Column(scale=2):
                    inpaint_editor = gr.ImageEditor(
                        label="Image — paint the region to replace",
                        type="pil",
                        brush=gr.Brush(colors=["#FF3366"], color_mode="fixed"),
                    )
                    inpaint_prompt = gr.Textbox(
                        label="Prompt (describes the masked region)", lines=3
                    )
                    inpaint_negative = gr.Textbox(
                        label="Negative prompt (only used when CFG > 1)", lines=2
                    )
                    with gr.Row():
                        inpaint_steps = gr.Slider(
                            1, 60, value=DEFAULTS["steps"], step=1, label="Steps"
                        )
                        inpaint_cfg = gr.Slider(
                            0.5, 8.0, value=DEFAULTS["cfg"], step=0.1, label="CFG"
                        )
                    with gr.Row():
                        inpaint_denoise = gr.Slider(
                            0.1, 1.0, value=1.0, step=0.05,
                            label="Denoise (1 = replace fully)",
                        )
                        inpaint_sampler = gr.Dropdown(
                            choices=SAMPLERS, value=SAMPLERS[0], label="Sampler"
                        )
                    with gr.Row():
                        inpaint_grow = gr.Slider(
                            0, 32, value=8, step=1, label="Grow mask (px)"
                        )
                        inpaint_blur = gr.Slider(
                            0, 32, value=8, step=1, label="Blur mask edge (px)"
                        )
                    with gr.Row():
                        inpaint_seed = gr.Number(
                            label="Seed", value=42, precision=0
                        )
                        inpaint_random = gr.Checkbox(
                            label="🎲 Random seed", value=True
                        )
                        inpaint_batch = gr.Slider(
                            1, 20, value=1, step=1, label="Batch count"
                        )
                    inpaint_btn = gr.Button(
                        "🖌️ Inpaint", variant="primary", size="lg"
                    )
                with gr.Column(scale=3):
                    inpaint_gallery = gr.Gallery(
                        label="Output", columns=2, height=600
                    )
                    inpaint_status = gr.Textbox(
                        label="Status", interactive=False
                    )
                    inpaint_seed_out = gr.Number(
                        label="Base seed used", interactive=False, precision=0
                    )
            inpaint_btn.click(
                fn=generate_inpaint,
                inputs=[inpaint_editor, inpaint_prompt, inpaint_negative,
                        inpaint_seed, inpaint_random, inpaint_steps,
                        inpaint_cfg, inpaint_denoise, inpaint_sampler,
                        inpaint_grow, inpaint_blur, inpaint_batch],
                outputs=[inpaint_gallery, inpaint_status, inpaint_seed_out],
            )

        with gr.Tab("JSON Advanced Batch"):
            gr.Markdown(
                "Submit a list of jobs, e.g.\n"
                '```json\n'
                '[{"prompt": "a cat", "steps": 8, "resolution": "1216x832",\n'
                '  "loras": {"krea2_darkbrush": 1.0}},\n'
                ' {"prompt": "a dog", "seed": 7}]\n'
                '```'
            )
            with gr.Row():
                with gr.Column(scale=2):
                    json_file_in = gr.File(label="Upload JSON file", type="filepath")
                    json_text_in = gr.Textbox(
                        label="…or paste a JSON array here", lines=14
                    )
                    json_btn = gr.Button(
                        "🚀 Run JSON batch", variant="primary", size="lg"
                    )
                with gr.Column(scale=3):
                    json_gallery = gr.Gallery(
                        label="Batch output", columns=2, height=600
                    )
                    json_status = gr.Textbox(label="Status", interactive=False)
            json_btn.click(
                fn=generate_from_json,
                inputs=[json_file_in, json_text_in],
                outputs=[json_gallery, json_status],
            )

        with gr.Tab("Gallery"):
            with gr.Row():
                gallery_refresh_btn = gr.Button("🔄 Refresh", size="sm")
                gallery_zip_btn = gr.Button(
                    "📦 Zip all for download", size="sm"
                )
            gallery_info = gr.Markdown(
                f"{len(list_output_images())} image(s) in `{OUTPUT_DIR}`"
            )
            all_gallery = gr.Gallery(
                label="All generated images (newest first)",
                value=list_output_images(), columns=4, height=700,
            )
            gallery_zip_file = gr.File(
                label="Zip of all images", interactive=False
            )
            gallery_refresh_btn.click(
                fn=refresh_gallery, outputs=[all_gallery, gallery_info]
            )
            gallery_zip_btn.click(
                fn=zip_outputs, outputs=[gallery_zip_file, gallery_info]
            )


def _probe_url(url: str, deadline_s: int = 45) -> bool:
    """True if `url` serves the app (HTTP 2xx/3xx) within deadline_s."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=15)
            if resp.ok:
                return True
            log.warning("Share URL answered HTTP %d — retrying", resp.status_code)
        except Exception as exc:
            log.warning("Share URL not reachable yet (%s)", exc)
        time.sleep(5)
    return False


def _start_cloudflared(port: int):
    """Start a Cloudflare quick tunnel (no account needed); return (proc, url)."""
    binary = TEMP_DIR / "cloudflared"
    if not binary.exists():
        log.info("Downloading cloudflared ...")
        urllib.request.urlretrieve(
            "https://github.com/cloudflare/cloudflared/releases/latest/"
            "download/cloudflared-linux-amd64",
            binary,
        )
        binary.chmod(0o755)
    proc = subprocess.Popen(
        [str(binary), "tunnel", "--url", f"http://127.0.0.1:{port}",
         "--no-autoupdate"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + 90
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        if not line:
            break
        found = pattern.search(line)
        if found:
            return proc, found.group(0)
    proc.terminate()
    raise RuntimeError(
        "cloudflared did not produce a tunnel URL within 90s — "
        "restart the app to retry"
    )


def launch_ui() -> None:
    """Launch Gradio, verify the public link works, keep the app alive."""
    log.info("Launching Gradio ...")
    _app, _local_url, share_url = ui.launch(
        server_name="0.0.0.0", server_port=7860, share=True,
        show_error=True, ssr_mode=False, prevent_thread_lock=True,
        # OUTPUT_DIR is outside the cwd, so Gradio needs it whitelisted to
        # serve gallery images (on Kaggle the cwd contained the output dir).
        allowed_paths=[str(OUTPUT_DIR)],
    )
    tunnel_proc, public_url = None, share_url
    if share_url and _probe_url(share_url):
        log.info("gradio.live link verified")
    else:
        log.warning(
            "The gradio.live link is not answering (some hosts block or time "
            "out Gradio's share tunnel) — starting a Cloudflare tunnel instead."
        )
        tunnel_proc, public_url = _start_cloudflared(7860)
        if share_url:
            log.info("(the gradio.live link may still start working later: %s)",
                     share_url)
    print(f"\n{'=' * 60}\n>>> OPEN THE UI HERE: {public_url}\n{'=' * 60}\n",
          flush=True)
    try:
        while True:  # keep the app alive; press Ctrl-C to stop
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Stopping the UI")
        if tunnel_proc is not None:
            tunnel_proc.terminate()
        ui.close()
