"""Gradio UI.

Two tabs: single / simple-batch generation and JSON batch jobs. Written
for Gradio 6 (theme/css now belong to launch(), gr.File hands the handler
a plain file path).

Some hosts' networks break Gradio's *.gradio.live share tunnel (the link
504s even though the app is healthy), so after launching we probe the
share URL from inside the pod and, if it does not answer, start a
Cloudflare quick tunnel and print that URL instead. launch_ui() blocks
while the UI is live — press Ctrl-C to shut it down.
"""

import json
import random
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import gradio as gr
import requests

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
from workflow import build_workflow, list_lora_files, resolve_lora_name

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


def _run_jobs(jobs):
    """Shared executor: yields (gallery_paths, status_text) as work progresses."""
    images = []
    total = len(jobs)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        prefix = "Krea2"
        if job["loras"]:
            prefix += "_" + Path(job["loras"][0][0]).stem
        workflow = build_workflow(filename_prefix=prefix, **job)
        yield images, (
            f"⏳ Job {label} — queued "
            f"(seed {job['seed']}, {job['width']}×{job['height']})"
        )
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
