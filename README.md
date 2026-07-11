# Krea 2 on RunPod — ComfyUI + Gradio

A standalone Python app (converted from the Kaggle notebook) that bootstraps
ComfyUI, downloads the Krea 2 Turbo models + LoRAs, and serves a Gradio UI.

## Run

On a RunPod GPU pod (PyTorch base image, `git` available):

```bash
python app.py
```

That single command clones ComfyUI if missing, installs requirements,
downloads any missing models (~20 GB on first run), starts the ComfyUI
server, waits for it, and launches the Gradio UI. A public URL is printed
when the UI is up (`>>> OPEN THE UI HERE: ...`). Press Ctrl-C to stop.

The pod filesystem is treated as ephemeral — models, outputs and logs all
live under the base directory and are lost when the pod is destroyed.

## Environment variables (all optional)

| Variable            | Purpose                                                        |
| ------------------- | -------------------------------------------------------------- |
| `HF_TOKEN`          | Hugging Face token — only needed for gated repos               |
| `CIVITAI_TOKEN`     | CivitAI API token — needed for most CivitAI LoRA downloads     |
| `KREA2_BASE_DIR`    | Base directory for everything (default `/workspace/krea2`)     |
| `KREA2_SKIP_LAUNCH` | If set, run setup/downloads/server but skip launching the UI   |

## Layout

- `app.py` — entry point; orchestrates the startup flow
- `config.py` — paths, model variant (Turbo), LoRA lists, tokens, presets
- `bootstrap.py` — clone ComfyUI + install requirements
- `downloads.py` — HF / CivitAI model + LoRA downloads (resume + retries)
- `comfy.py` — GPU detection + ComfyUI server start/wait
- `workflow.py` — Krea 2 workflow builders, text-to-image + inpainting (ComfyUI API format)
- `client.py` — ComfyUI HTTP/websocket client (queue, progress, image upload)
- `ui.py` — Gradio UI (single/batch, inpaint, JSON batch, gallery tabs) and launch logic

## Inpainting

The **Inpaint** tab accepts an uploaded image; paint a mask over the region
to replace and describe the replacement in the prompt. Only stock ComfyUI
nodes are used (`SetLatentNoiseMask` + `ImageCompositeMasked`), so it works
with the same Turbo model — no extra downloads. The denoise slider controls
how much of the original survives in the masked region (1.0 = full
replacement); grow/blur expand and soften the mask edge for seamless blends.
Images are downscaled to a 2048 px long side and snapped to multiples of 16
before encoding.
