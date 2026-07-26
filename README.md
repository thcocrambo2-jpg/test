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
| `KREA2_LICENSE_KEY`        | **Required.** Customer license key (see Licensing below)        |
| `HF_TOKEN`                 | Hugging Face token — only needed for gated repos                |
| `CIVITAI_TOKEN`            | CivitAI API token — needed for most CivitAI LoRA downloads      |
| `KREA2_BASE_DIR`           | Base directory for everything (default `/workspace/krea2`)     |
| `KREA2_SKIP_LAUNCH`        | If set, run setup/downloads/server but skip launching the UI   |
| `KREA2_DISABLE_WAN`        | If set, skip the ~49 GB Wan 2.2 downloads and hide the Video tab |
| `KREA2_DISABLE_FLUX`       | If set, skip the ~57 GB Flux 2 downloads and hide the Flux tab  |
| `KREA2_DISABLE_REACTOR`    | If set, skip the ~1.8 GB ReActor downloads and hide the Face Swap tab |
| `KREA2_WAN_PARALLEL`       | If set, video jobs get their own ComfyUI instance (port 8189)   |
| `KREA2_MAIN_RESERVE_VRAM`  | Parallel mode: GB the Krea instance leaves free (default 26)   |
| `KREA2_WAN_RESERVE_VRAM`   | Parallel mode: GB the Wan instance leaves free (default 22)    |

## Licensing

The app takes a **license seat** before it does anything else and will not
start without one. Set `KREA2_LICENSE_KEY` on the pod to the key you were
given; one key allows a fixed number of instances running at the same time.

Seats are leases rather than a counter, so an instance that dies without
releasing — `SIGKILL`, an OOM kill, a hard pod terminate — frees its own
seat within a few minutes with nothing to clean up. Stopping the app
cleanly returns it immediately. Restarting on the same pod reclaims the
same seat rather than spending a second one, because `RUNPOD_POD_ID` is
used as the instance identity when it is present.

The check runs first in `main()`, ahead of the ComfyUI clone and the
downloads, so a seat problem surfaces in seconds instead of after ~90 GB.
If the license server becomes unreachable *while* the app is running it
keeps going for `KREA2_LICENSE_GRACE` seconds (default 1800) so an outage
does not kill a long video render.

Exit codes: `2` no key set, `3` key rejected (invalid, revoked, expired,
or all seats in use), `4` license server unreachable at startup, `5` the
license stopped being valid mid-run.

The server lives in `license-validator/` — see its README for issuing keys
and deploying.

## Layout

- `deps/ComfyUI-ReActor` — vendored ReActor node pack, copied into `custom_nodes` at bootstrap
- `app.py` — entry point; orchestrates the startup flow
- `licensing.py` — license seat acquire / heartbeat / release (stdlib only)
- `license-validator/` — the Node/Express + MongoDB license server
- `config.py` — paths, Krea 2 model registry, LoRA lists, Wan 2.2 settings, tokens, presets
- `bootstrap.py` — clone ComfyUI + install requirements
- `downloads.py` — HF / CivitAI model + LoRA downloads (resume + retries)
- `comfy.py` — GPU detection + ComfyUI server start/wait (1–2 instances)
- `workflow.py` — Krea 2 workflow builders, text-to-image + inpainting + instruction edit (ComfyUI API format)
- `workflow_wan.py` — Wan 2.2 image-to-video workflow builder (two-expert A14B)
- `workflow_reactor.py` — ReActor face-swap workflow builder + availability checks
- `client.py` — ComfyUI HTTP/websocket client (queue, progress, image upload)
- `ui.py` — Gradio UI (single/batch, edit, inpaint, face swap, flux, video, JSON batch, gallery tabs) and launch logic
- `build.sh` — compiles the app into a single distributable binary (see below)

## Shipping a binary (Nuitka)

`./build.sh` compiles the app into one self-contained executable
(`dist/krea2app`) so it can be handed to someone without shipping the source.
Nuitka translates Python to C and compiles it to **native machine code** —
unlike PyInstaller, which ships `.pyc` bytecode that decompiles back to
near-original source.

**Run it on the pod, not on Windows.** Nuitka cannot cross-compile, and a
standalone binary links against the build machine's glibc and will not start on
an older one; building where you deploy sidesteps both. The build needs no GPU
(Nuitka compiles source rather than running it, so `comfy.py`'s import-time GPU
check never fires) and `build.sh` installs `nuitka` and a compiler if the pod
lacks them.

What the binary contains vs. what it still installs at runtime:

| Inside the binary | Installed on first run |
| --- | --- |
| this app's code (compiled) | ComfyUI (`git clone`) |
| gradio, huggingface_hub, requests, safetensors, websocket-client, Pillow | torch + ComfyUI's requirements |
| `deps/ComfyUI-ReActor` (bundled data) | ReActor's requirements + onnxruntime |
| | ~90 GB of models |

ComfyUI's and ReActor's dependencies *cannot* be compiled in: the app never
imports them, and ComfyUI runs as a **separate process with its own
interpreter**, so it needs them in the pod's system Python. That is also what
keeps the artifact around 100–200 MB instead of multi-gigabyte. The target pod
needs `python3`, `git`, an NVIDIA driver and disk — not the CUDA toolkit, since
torch's wheels ship their own CUDA libraries.

Two things make the compiled and uncompiled paths behave identically:

- **`config.FROZEN`** — the single authoritative "am I compiled?" check
  (Nuitka injects `__compiled__` into every module). Exactly **two** places may
  branch on it: `bootstrap.runtime_python()` and the app-requirements skip in
  `install_comfyui()`. Any third branch is a way for the shipped binary to
  diverge from what you test.
- **`bootstrap.runtime_python()`** — compiled, `sys.executable` is *the binary*,
  so the 7 sites that run `pip` and launch ComfyUI would otherwise pass
  nonsense arguments to themselves. It resolves the system `python3` when
  frozen and returns `sys.executable` otherwise, so **`python3 app.py` keeps
  working exactly as before** — that is the development path and must stay
  intact.

Day to day nothing changes: keep running `python3 app.py`. Build only when you
want to hand over an artifact. Nuitka caches the C compilation, so the first
build (which compiles gradio's whole tree) is the slow one. If you add a
dependency that works under `python3 app.py` but fails in the binary, the usual
cause is package *data* files: add `--include-package-data=<pkg>` in
`build.sh`.

### Building on the pod

```bash
cd /test
git pull
bash build.sh          # or chmod +x build.sh && ./build.sh
```

`bash build.sh` avoids needing the executable bit, which git does not carry
when the file is committed from Windows. The script installs whatever the pod
lacks — `build-essential`, `patchelf`, `ccache`, `nuitka` — none of which ship
in the RunPod image, and all of which are gone again on a fresh pod. Output is
`dist/krea2app`.

Smoke-test it on the same pod, which is the fastest check available: ComfyUI
and the models are already on disk, so the bootstrap skips everything and goes
straight to serving.

```bash
./dist/krea2app
```

Expect `ComfyUI already present … — skipping clone`,
`ReActor nodes already present …`, `ComfyUI API on port 8188 is ready` and
`Custom node ReActorFaceSwap is registered`. A **fresh** pod is the real
end-to-end test — only that exercises the clone and download paths.

### Building locally in WSL2 (optional)

The pod image is `runpod/pytorch:…-ubuntu2404` → glibc 2.39, Python 3.12, so
**WSL2 with Ubuntu 24.04 matches it exactly** and its output runs on the pod.
Ubuntu 22.04 (glibc 2.35) also works and is safer, since older glibc runs on
newer hosts but not the reverse. The build needs no GPU, no CUDA and no torch.

```powershell
wsl --install -d Ubuntu-24.04     # once
wsl -d Ubuntu-24.04               # every time, to open a shell in it
```

**Always pass `-d Ubuntu-24.04`.** Bare `wsl` opens whatever distro is
*default*, which on a machine with Docker Desktop or Rancher Desktop is their
bundled one — recognisable by a `#` root prompt, no `sudo`, and Windows drives
at `/mnt/host/c/…` instead of `/mnt/c/…`. Each distro has its own filesystem, so
the venv and `/etc/wsl.conf` below exist only inside Ubuntu-24.04. Check with
`wsl --list --verbose` (`*` marks the default) or make it the default once:

```powershell
wsl --set-default Ubuntu-24.04
```

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone <this repo> && cd test
python3 -m venv ~/build-venv && source ~/build-venv/bin/activate
pip install -r requirements.txt
./build.sh
```

Use a venv: Ubuntu 24.04 enforces PEP 668, so installing into the system
Python fails with `externally-managed-environment` (the RunPod image disables
this, which is why the pod does not need it). `build.sh` uses whichever
`python3` is active, so an activated venv is picked up automatically, and it
prefixes `sudo` when not running as root.

**Building under `/mnt/c/…` needs one extra setting.** WSL mounts Windows
drives with DrvFs, which cannot store Unix file modes by default, so `chmod`
fails with `EPERM`. Nuitka patches RPATHs into the bundled `.so` files and
restores their modes afterwards, and dies there — minutes into the compile,
with a `PermissionError` traceback that never mentions the mount. `build.sh`
probes for this up front (it tries a real `chmod`, rather than guessing from
the path) and stops in a second with both fixes printed. Either:

```bash
# 1) allow Unix modes on Windows drives, and keep building where you are
printf '[automount]\noptions = "metadata"\n' | sudo tee /etc/wsl.conf
#    then, from PowerShell:  wsl --shutdown    (wait ~8s, then reopen)
#    verify with:            mount | grep ' /mnt/c '
```

```bash
# 2) or build from the WSL filesystem, which is also much faster
cp -r /mnt/c/…/test ~/test && cd ~/test && ./build.sh
```

Option 2 is the better default: WSL2 reaches `/mnt/c` over 9p, and this build
touches ~1745 C files plus all of gradio's tree. Note that enabling `metadata`
also makes git notice file-mode changes it previously ignored; if that produces
spurious `old mode / new mode` diffs, set `git config core.fileMode false`.

Trade-off: the pod keeps the build environment identical *by construction*,
while WSL matches it *by version*. If RunPod bumps its base image past Ubuntu
24.04, a WSL-built binary may stop starting, and the symptom — a glibc error at
exec — is obscure.

### Getting the binary off the pod

`scp` over RunPod's SSH proxy often fails (`ssh.runpod.io` is a terminal proxy,
not a full SSH server), so try it first and fall back:

```bash
# locally — works only if the proxy supports SCP
scp -i ~/.ssh/id_ed25519 <user>@ssh.runpod.io:/test/dist/krea2app .
```

```bash
# on the pod — prints a one-time code
runpodctl send /test/dist/krea2app
# locally
runpodctl receive <code>
```

`runpodctl` ships on pods and is peer-to-peer, so it ignores the SSH proxy's
limitations. Failing both, serve it over the already-exposed Gradio port while
the app is stopped:

```bash
cd /test/dist && python3 -m http.server 7860
# then download https://<POD_ID>-7860.proxy.runpod.net/krea2app
```

The artifact is a **Linux** binary — it will not run on Windows; downloading is
only for redistribution. Whoever receives it needs `chmod +x krea2app` first,
since the executable bit does not survive most transfers.

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
Images are snapped to multiples of 16 before encoding.

The editor runs with `fixed_canvas=True` and a 1536 px canvas. That is not
cosmetic: with Gradio's default `fixed_canvas=False` the canvas grows to the
uploaded image's dimensions, so a 12 MP phone photo allocates a 4032×3024
RGBA canvas *plus* a paint layer in the browser, the tab runs out of memory
and **the page reloads on upload** ([gradio#8556](https://github.com/gradio-app/gradio/issues/8556)).
Pinning the canvas makes Gradio rescale uploads to fit it instead, so large
photos work — at the cost of small images being scaled up to the canvas.
`format="png"` overrides Gradio's lossy webp default, since unmasked pixels
are composited back from that image.

## Face swap (ReActor)

The **🎭 Face Swap (ReActor)** tab replaces the face in one image with the
face from a reference photo, using
[ComfyUI-ReActor](https://github.com/Gourieff/ComfyUI-ReActor). The node
pack is **vendored in `deps/ComfyUI-ReActor`** and bootstrap installs it by
copying that folder into `custom_nodes` — nothing is fetched from GitHub.
(It is copied rather than symlinked so the `../../models/...` paths inside
`r_facelib` resolve against the ComfyUI install instead of this project
directory.) A checkout without `deps/` falls back to cloning the repo, and
an existing `custom_nodes/ComfyUI-ReActor` is always left alone. Pick the
base image from the collapsed
**"Use a previous generation"** picker — the same last-20 gallery the Edit
and Video tabs use — or upload/paste one, then upload the reference face.

This is not a diffusion pass. No UNet, text encoder or VAE is loaded: the
graph is `LoadImage ×2 → ReActorFaceSwap → SaveImage`, ReActor detects the
face, takes the reference identity embedding and rewrites only the face
region with an ONNX model. So it runs in seconds, costs almost no VRAM,
leaves the Krea 2 pipeline completely untouched, and the output keeps the
base image's **exact resolution** — nothing on either side resizes (the
one exception is a rejected input, see the SFW note below). The
`SaveImage` node reads output 0 (`SWAPPED_IMAGE`), so exactly one file is
written and it is the finished swap; it lands in `OUTPUT_DIR` with the
usual ComfyUI PNG metadata and shows up in the Gallery tab and the zip
download like every other output.

Defaults match the ReActor recommendations: `inswapper_128`,
`retinaface_resnet50`, CodeFormer restoration (visibility 1.0, weight 0.5)
and face index 0 on both sides. Restoration is optional — set **Face
restoration** to `none` to skip it. Both index boxes accept `0`, `0,1` or
`0-2` to pick among several detected faces, counted left to right.

Everything is downloaded up front by `downloads.py` (~1.8 GB) so a swap
makes **no network calls at generation time**:

| File | Destination |
| --- | --- |
| `inswapper_128.onnx` (~554 MB) | `models/insightface/` |
| `buffalo_l` pack (~289 MB, unzipped) | `models/insightface/models/buffalo_l/` |
| `codeformer-v0.1.0.pth` (~377 MB) | `models/facerestore_models/` |
| `detection_Resnet50_Final.pth` (~110 MB) | `models/facedetection/` |
| `parsing_parsenet.pth` (~85 MB) | `models/facedetection/` |
| `AdamCodd/vit-base-nsfw-detector` (~350 MB) | `models/nsfw_detector/vit-base-nsfw-detector/` |

The last three are the ones ReActor would otherwise fetch lazily during
the first swap (via `r_facelib` and its SFW check), which is why they are
pre-fetched here rather than left to download themselves. Add another
restorer from the same repo to `REACTOR_HF_FILES` in `config.py` and it
appears in the dropdown after a restart.

Two things worth knowing:

- **No C++ toolchain is needed.** Despite ReActor's reputation, this
  version does *not* use the `insightface` package: it vendors its own face
  analysis in `reactor_core/` (`ReActorFaceAnalysis`, `SCRFD`,
  `ArcFaceONNX`, ...) and runs the `buffalo_l` ONNX files through
  `onnxruntime` directly — `import insightface` appears nowhere in the pack
  and it is absent from its `requirements.txt`. Everything bootstrap
  installs (`onnxruntime-gpu`, `onnx`, `opencv-python`, `albumentations`,
  `segment_anything`, `ultralytics`) is a wheel, so no `cmake`, no
  compiler. The `models/insightface/` folder keeps that name only because
  it is where ReActor looks for the swap model and the pack.

- **onnxruntime is matched to the pod's CUDA.** PyPI's current
  `onnxruntime-gpu` wheel links CUDA 13, so on a CUDA 12 pod it installs
  cleanly and then dies at import with `libcudart.so.13: cannot open shared
  object file` — which surfaces only as ComfyUI skipping the node pack and
  the first swap failing with "node not found". `install_onnxruntime()`
  therefore picks a build from `torch.version.cuda` (CUDA 12 → pinned
  `onnxruntime-gpu==1.22.0` plus Microsoft's CUDA 12 feed), **verifies it
  by actually importing it**, and removes and retries on failure, ending at
  the CPU build — inswapper_128 is small, so a CPU swap still takes
  seconds. After ComfyUI starts, `comfy.verify_custom_node()` confirms
  `ReActorFaceSwap` registered and prints the traceback from `comfyui.log`
  if it did not.
- **This is the SFW edition of ReActor.** It classifies every input image
  before swapping (`scripts/reactor_sfw.py`, flagging `nsfw` above score
  0.979). A flagged image is *dropped*, and ReActor's empty-list branch
  returns a **512×512 solid black frame** rather than the original — so the
  job "succeeds" and writes a black PNG. The tab detects that frame and
  says so in the status box instead of reporting success, but it is the one
  case where the output does not keep the base resolution. Given the NSFW
  LoRAs in `CIVITAI_LORAS`, expect it to trigger on some inputs.

  The check also **fails closed**: `nsfw_image()` returns `True` for
  everything when its model cannot be loaded, so a missing detector makes
  *every* swap come back blank. That is why the ~350 MB
  `vit-base-nsfw-detector` is in the pre-download list above and not
  treated as optional — `ensure_nsfw_model` looks for exactly `config.json`,
  `model.safetensors` and `preprocessor_config.json` in that directory.

Set `KREA2_DISABLE_REACTOR=1` to skip the downloads and hide the tab.

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

The generate / edit / inpaint / Flux tabs each stack **`MAX_LORA_SLOTS`
LoRA slots** (`ui.py`, currently 8). That one constant drives the UI rows,
the handlers and the `LoraLoaderModelOnly` chain, so changing it is the
whole change — the handlers take their slots as a variadic tail and the
workflow builder already loops over the resolved list. Slots left at
"None" drop out, and slots beyond `DEFAULT_LORAS` simply start empty.

To keep a tall stack from eating the column, only the first
`VISIBLE_LORA_SLOTS` (3) are shown; the rest sit in a collapsed
**"➕ N more LoRA slots"** accordion, which opens on load if any hidden
slot is already in use so an active LoRA is never invisible. The nesting
is purely visual — the slot lists stay flat and ordered. Set
`VISIBLE_LORA_SLOTS >= MAX_LORA_SLOTS` to show every slot and skip the
accordion entirely.

## Flux 2

The **🌊 Flux 2** tab does text-to-image with Flux 2 Dev (32B,
`flux2_dev_fp8mixed`, ~35.5 GB) plus the Mistral-Small text encoder
(~18 GB) and Flux 2 VAE — ~57 GB of downloads from `Comfy-Org/flux2-dev`;
set `KREA2_DISABLE_FLUX=1` to skip all of it. Flux 2 is
guidance-distilled, so there is no CFG/negative prompt — a **Guidance**
value (~4) steers it, and sampling uses the official template's
custom-sampler graph (`Flux2Scheduler` + `BasicGuider` +
`SamplerCustomAdvanced`), all stock ComfyUI nodes.

Models come from the `FLUX_MODELS` registry in `config.py` (same scheme
as `KREA2_MODELS`: `variant` → defaults from `FLUX_VARIANT_DEFAULTS`,
per-model overrides, `hf_path`/`civitai_version` sources, optional
`trigger`). The two stock entries share one weights file: **Turbo**
applies the official Flux 2 Turbo LoRA (8 steps, default) and **Raw**
runs undistilled (20 steps). Flux LoRAs are listed in
`FLUX_CIVITAI_LORAS` and live in `loras/flux2/`, so they never mix with
the Krea 2 LoRA dropdowns (the architectures are incompatible).

VRAM note: at ~35 GB the fp8 model wants nearly the whole A40 — run Flux
**without** `KREA2_WAN_PARALLEL`, and expect a 1–3 min model swap when
alternating Flux and Krea jobs (the two model sets cannot stay resident
together).

## Image input shortcuts

All image inputs (Edit, Inpaint, Face Swap, Video) accept **clipboard
paste** — press Ctrl+V with the component focused or use its paste source
button. The Edit, Face Swap and Video tabs additionally have a collapsed
**"Use a previous generation"** picker showing the last 20 generated
images; clicking a thumbnail loads it as the source directly, no
download/re-upload round-trip.

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
