# Krea 2 on RunPod — ComfyUI + Gradio

A standalone Python app (converted from the Kaggle notebook) that bootstraps
ComfyUI, downloads the Krea 2 Turbo models + LoRAs plus the Wan 2.2
image-to-video models, and serves a Gradio UI.

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

| Variable                   | Purpose                                                         |
| -------------------------- | --------------------------------------------------------------- |
| `HF_TOKEN`                 | Hugging Face token — only needed for gated repos                |
| `CIVITAI_TOKEN`            | CivitAI API token — needed for most CivitAI LoRA downloads      |
| `KREA2_BASE_DIR`           | Base directory for everything (default `/workspace/krea2`)     |
| `KREA2_SKIP_LAUNCH`        | If set, run setup/downloads/server but skip launching the UI   |
| `KREA2_DISABLE_WAN`        | If set, skip the ~49 GB Wan 2.2 downloads and hide the Video tab |
| `KREA2_WAN_PARALLEL`       | If set, video jobs get their own ComfyUI instance (port 8189)   |
| `KREA2_MAIN_RESERVE_VRAM`  | Parallel mode: GB the Krea instance leaves free (default 26)   |
| `KREA2_WAN_RESERVE_VRAM`   | Parallel mode: GB the Wan instance leaves free (default 22)    |

## Layout

- `app.py` — entry point; orchestrates the startup flow
- `config.py` — paths, Krea 2 model registry, LoRA lists, Wan 2.2 settings, tokens, presets
- `bootstrap.py` — clone ComfyUI + install requirements
- `downloads.py` — HF / CivitAI model + LoRA downloads (resume + retries)
- `comfy.py` — GPU detection + ComfyUI server start/wait (1–2 instances)
- `workflow.py` — Krea 2 workflow builders, text-to-image + inpainting + instruction edit (ComfyUI API format)
- `workflow_wan.py` — Wan 2.2 image-to-video workflow builder (two-expert A14B)
- `client.py` — ComfyUI HTTP/websocket client (queue, progress, image upload)
- `ui.py` — Gradio UI (single/batch, edit, inpaint, video, JSON batch, gallery tabs) and launch logic

## Instruction editing (Edit tab)

The **✨ Edit (Instruction)** tab does nano-banana-style editing: upload an
image and describe the change ("make the jacket red", "this person walking
a dog on a beach") — no mask painting. It uses the community
[Krea 2 Identity Edit LoRA](https://huggingface.co/conradlocke/krea2-identity-edit)
(~1.9 GB, auto-downloaded) together with the
[ComfyUI-Krea2Edit](https://github.com/lbouaraba/comfyui-krea2edit) node
pack (auto-cloned into `custom_nodes` at bootstrap). The source image is
injected both as in-context VAE latents and through the Qwen3-VL text
encoder, so the model actually sees the image it is editing and preserves
identity/unchanged regions. The **Grounding** slider trades edit strength
(lower) against likeness fidelity (higher); style LoRAs can be stacked on
top just like in the other tabs. Outputs are capped at ~2 MP (the LoRA
duplicates content above that).

## Inpainting

The **Inpaint** tab accepts an uploaded image; paint a mask over the region
to replace and describe the replacement in the prompt. Only stock ComfyUI
nodes are used (`SetLatentNoiseMask` + `ImageCompositeMasked`), so it works
with the same Turbo model — no extra downloads. The denoise slider controls
how much of the original survives in the masked region (1.0 = full
replacement); grow/blur expand and soften the mask edge for seamless blends.
Images are downscaled to a 2048 px long side and snapped to multiples of 16
before encoding.

## Krea 2 model switching

The generate / edit / inpaint tabs each have a **Model** dropdown fed by
the `KREA2_MODELS` registry in `config.py` — the same add-an-entry-and-
restart workflow as the LoRA lists. Each entry names its file, a
`variant` flag (`turbo` or `raw`) that supplies the step/CFG defaults
(overridable per model), a download source (`hf_path` in the official
repo **or** `civitai_version` — the number after the `@` in a CivitAI AIR
urn), and optional `trigger` words. Picking a model resets the Steps/CFG
sliders to its defaults and, if it has trigger words, inserts them into
the prompt box — visible and editable, never appended silently; delete
them if you don't want them. JSON batch jobs select a model with an
optional `"model"` key. A model whose download failed shows a warning
under the dropdown and refuses to run, without affecting the others.

## Image input shortcuts

All image inputs (Edit, Inpaint, Video) accept **clipboard paste** — press
Ctrl+V with the component focused or use its paste source button. The Edit
and Video tabs additionally have a collapsed **"Use a previous generation"**
picker showing the last 20 generated images; clicking a thumbnail loads it
as the source directly, no download/re-upload round-trip.

## Video (Wan 2.2 image-to-video)

The **🎬 Video (Wan 2.2)** tab animates an uploaded image into a clip of
up to 5 s. Two models are switchable per-job in the UI; all files
(~49 GB total) auto-download from `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`
and only stock ComfyUI nodes are used.

- **14B two-expert (I2V A14B)** — two 14 B fp8 "expert" models
  (high-noise for the early sampler steps, low-noise for the late ones,
  ~14.3 GB each) at 16 fps. Best quality. Two modes mirror the Krea
  turbo/raw split:
  - **Turbo** — the lightx2v *Lightning* distillation LoRAs on both
    experts, 4 steps, CFG 1.0. Roughly 5× faster; the default.
  - **Raw** — the undistilled 20-step, CFG 3.5 schedule. Slightly better
    motion/detail, but expect 15–45+ minutes per clip on an A40.
- **5B (TI2V 5B)** — a single dense ~10 GB fp16 model with its own
  Wan 2.2 VAE, 24 fps, 20 steps / CFG 5 (no Lightning, so no turbo/raw
  choice). Lower quality than 14B, but it fits in ~18 GB of VRAM with no
  expert swap mid-run — the best choice when running the parallel video
  instance — and model switches to/from Krea are much faster.

Resolution is chosen as a 480p or 720p *area* while keeping the source
image's aspect ratio (sides snapped to /16, or /32 for 5B). Videos are
saved as MP4 under `output/wan/` and show up in the Gallery tab and the
zip download.

By default video jobs share the image tabs' ComfyUI queue (safe, serial).
Start with `KREA2_WAN_PARALLEL=1` to give video its own ComfyUI instance on
port 8189 so quick image jobs don't wait behind a long render — both
instances then split the GPU via `--reserve-vram` (defaults tuned for a
48 GB A40; note the two workloads also share compute, so each runs slower
while overlapping). Set `KREA2_DISABLE_WAN=1` to skip the downloads and
hide the tab entirely. Make sure the pod volume has room: Krea (~32 GB) +
Wan (~49 GB) + ComfyUI needs a ≥ 100 GB disk (a 120 GB volume fits with
~35 GB left for outputs).
