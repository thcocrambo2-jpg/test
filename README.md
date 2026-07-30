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
| `KREA2_NODE_TAG`           | **Required.** Node tag issued alongside the license key         |
| `HF_TOKEN`                 | Hugging Face token — only needed for gated repos                |
| `CIVITAI_TOKEN`            | CivitAI API token — needed for most CivitAI LoRA downloads      |
| `KREA2_BASE_DIR`           | Base directory for everything (default `/workspace/krea2`)     |
| `KREA2_SKIP_LAUNCH`        | If set, run setup/downloads/server but skip launching the UI   |
| `KREA2_KEEP_MODELS_LOADED` | If set, don't unload models between swaps (see Model swapping)  |
| `KREA2_WAN_PARALLEL`       | If set, video jobs get their own ComfyUI instance (port 8189)   |
| `KREA2_MAIN_RESERVE_VRAM`  | Parallel mode: GB the Krea instance leaves free (default 26)   |
| `KREA2_WAN_RESERVE_VRAM`   | Parallel mode: GB the Wan instance leaves free (default 22)    |

## Features

Every tab is a feature that is switched on or off before the app downloads
anything. A feature that is off costs nothing: no tab, no custom nodes, no
weights on disk.

**Which tabs a pod gets is decided by its license key, and by nothing
else.** There is no environment variable for this — the license names a
**plan**, the license server resolves that to a feature list, the app
reads it from the acquire response, and a customer cannot switch a tab on
by editing their pod template. Set it when you issue the key:

```bash
npm run issue-key -- --name "Acme Corp" --plan pro --seats 2
```

| Key            | Tab                     | Extra download |
| -------------- | ----------------------- | -------------- |
| `krea_t2i`     | Single / Simple Batch   | ~26 GB (Krea 2 base, shared) |
| `krea_v2_t2i`  | 🔶 Krea 2 V2            | ~17 GB         |
| `gallery`      | Gallery                 | none           |
| `krea_edit`    | ✨ Edit (Instruction)   | ~1.9 GB + base |
| `krea_inpaint` | Inpaint / Img2Img       | base only      |
| `faceswap`     | 🎭 Face Swap (ReActor)  | ~1.8 GB        |
| `flux_t2i`     | 🌊 Flux 2               | ~57 GB         |
| `klein_i2i`    | 🧩 Klein Edit           | ~19 GB         |
| `wan_i2v`      | 🎬 Video (Wan 2.2)      | ~49 GB         |
| `json_batch`   | JSON Advanced Batch     | none           |

The four shipped plans stack: `starter` (19/mo) is the first three keys,
`creator` (39) adds the editing set, `pro` (59) adds Flux 2 and Klein, and
`studio` (89) adds video and JSON batch. `license-validator/README.md` has
the full table and how to change it.

Shared weights are handled for you — `krea_edit` and `krea_inpaint` both
run the Krea 2 base models, so granting either one fetches them, and
granting all three fetches them once.

Keys are permanent and names are not: a key is compiled into every shipped
binary, so renaming one drops that tab for anyone on an older build — the
client warns and ignores it. The seven model-bound keys were renamed to
`<model>_<task>` before any key was issued, which is the only window in
which that is free. There is no alias map for the old names.

A license with **no plan and no features** falls back to the built-in
defaults (`krea_t2i`, `krea_v2_t2i` and `gallery`) and logs a warning
saying so. That exists for keys issued before entitlements did; put
anything current on a plan, because "the build's defaults" is a moving
target across releases.

Changing what a key grants takes effect on the customer's **next start** —
whether you changed the license or the plan it sits on. A running instance
notices within a heartbeat and logs that a restart is needed, but does not
apply it: the tabs are built once at launch and the weights a newly
granted tab needs were never downloaded.

> **Upgrading from an earlier build:** `KREA2_FEATURES`,
> `KREA2_ENABLE_<KEY>` and `KREA2_DISABLE_<KEY>` are gone and are ignored
> if still set. Whatever those variables used to say belongs on the
> license instead:
> `npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update`

## Licensing

The app takes a **license seat** before it does anything else and will not
start without one. Set `KREA2_LICENSE_KEY` on the pod to the key you were
given, and `KREA2_NODE_TAG` to the node tag issued with it; one key allows
a fixed number of instances running at the same time. Both are required —
the tag names the deployment the key checks in against, and there is no
built-in default, so a pod missing either one stops at startup.

The same check returns the key's **entitlements** — the tabs it grants
(see Features above). They are applied immediately after the seat is
taken, before the ComfyUI clone and the downloads, which is what makes a
feature that is off cost nothing rather than being hidden after the fact.

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

Exit codes: `2` no key or no node tag set, `3` key rejected (invalid,
revoked, expired, or all seats in use), `4` license server unreachable at
startup, `5` the license stopped being valid mid-run.

The server lives in `license-validator/` — see its README for issuing keys
and deploying.

## Model swapping and crash recovery

With Krea 2 V1 (turbo/raw), V2 (turbo mxfp8/raw), Flux and Wan all
selectable, several multi-gigabyte UNets are in rotation. ComfyUI keeps
what it has loaded until memory pressure evicts it, so a swap has a window
where **two full model sets are resident** — and that window is where the
server gets OOM-killed. When it dies mid-job the websocket drops
(`Connection to remote host was lost`) and every later job fails with
`Connection refused`, whichever tab it came from.

Two mechanisms handle this:

- **Unload on swap.** Before submitting, the runner compares the graph's
  heavy weights (`unet_name` and `clip_name`, derived from the workflow
  itself in `client.model_signature`) against what that ComfyUI instance
  last loaded. If they differ it calls ComfyUI's `POST /free` and **waits
  for free VRAM to stop rising** before queueing. The waiting matters:
  `/free` only sets a flag the prompt worker consumes between jobs, so
  submitting immediately can win the race and execute with the old models
  still loaded — exactly the peak this avoids.

  Two things are deliberately excluded from the signature, because `/free`
  is all-or-nothing and anything included can cost a full UNet reload.
  **LoRAs**, since they are patches on top of the base weights — changing
  prompt, seed, steps or LoRA slots costs nothing. And **VAEs**, at
  0.25–1.4 GB: V1 and V2 use different ones (`qwen_image` vs `wan21`), so
  counting them would dump a 13 GB UNet the two tabs otherwise share just
  to swap 254 MB. **Point V1 and V2 at the same UNet and switching between
  the tabs needs no reload at all.**

  Set `KREA2_KEEP_MODELS_LOADED=1` to disable on a machine with room to
  spare, where keeping models warm is faster.
- **One job at a time.** Every generation event shares the
  `concurrency_id="comfy"` group, so a second tab's Generate queues rather
  than running alongside. They all feed one single-threaded ComfyUI prompt
  worker anyway, so nothing real is lost — but without it a second handler
  runs far enough to call `/free` while the first job still holds the
  models, stalling on the VRAM-settle wait and corrupting the
  what-is-loaded bookkeeping. Video keeps its own group when
  `KREA2_WAN_PARALLEL` gives it a separate ComfyUI instance.
- **Restart if it died anyway.** `comfy.ensure_alive()` runs before every
  batch: if the API does not answer it restarts ComfyUI (preserving the
  instance's `--reserve-vram` flags) and reports **the last 20 lines of
  `comfyui.log`** in the tab's status box. Transport failures are also
  converted to `ComfyUIError` in `client.py`, so a dead server reads as a
  status message rather than a Gradio traceback, and the app no longer
  needs a manual restart to recover.

If a crash persists, `comfyui.log` names the cause: `Killed process` in
`dmesg -T` means the OOM killer, while a `Segmentation fault` or CUDA
error in the log points at a custom node instead.

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
- `workflow_krea2_v2.py` — Krea 2 V2 builder (the Krea2 advanced turbo/raw graph)
- `workflow_klein.py` — Flux 2 Klein 9B edit builder (the Klein advanced Klein Edit graph)
- `workflow_wan.py` — Wan 2.2 image-to-video workflow builder (two-expert A14B)
- `workflow_reactor.py` — ReActor face-swap workflow builder + availability checks
- `client.py` — ComfyUI HTTP/websocket client (queue, progress, image upload)
- `ui.py` — Gradio UI (single/batch, edit, inpaint, face swap, flux, klein edit, video, JSON batch, gallery tabs) and launch logic
- `theme.py` — the UI's look: Gradio theme tokens, CSS, application header, page JS (see below)
- `build.sh` — compiles the app into a single distributable binary (see below)

### UI theme

`theme.py` owns everything visual and `ui.py` owns behaviour, so the two can
be worked on independently. `theme.launch_kwargs()` returns the
`theme`/`css`/`head`/`js` arguments for `launch()` — Gradio 6 takes them
there rather than on `gr.Blocks` — and both callers use it, so
`scripts/dryrun.py` shows exactly what a customer sees.

Colours are set as Gradio theme tokens, each with its `*_dark` counterpart,
and the CSS only refers to them through `var(--...)`. That means light and
dark come from one palette and cannot drift apart. `ui.py` contributes
nothing but `elem_classes="kx-…"` hooks (`kx-panel`, `kx-note`, `kx-cta`,
`kx-section`, `kx-meta`, `kx-status`, `kx-fine`) — no control's behaviour
depends on the stylesheet, and the app still works with it stripped out.

Beyond the palette the UI adds:

| | |
|---|---|
| Sticky application header | brand, the engines this license granted, model/GPU counts, and the output path (click it to copy) |
| Segmented tab bar | Gradio's own overflow menu still handles tabs that do not fit |
| One control card per tab | Gradio's per-field frames are flattened, so a 30-control tab reads as one form instead of thirty boxes |
| Sticky primary action | Generate / Edit / Swap stays reachable in columns that run past two screens |
| Tab intro callouts | `_tab_intro()` tints the note amber on a ⚠️ (something is missing) and red on a ❌ (the tab cannot run) |
| `Ctrl`/`Cmd` + `Enter` | runs the tab you are looking at; the footer says so |

Two Gradio internals are worth knowing about if a future Gradio changes the
look: the tab bar is styled through `.tab-container > button`, and Gradio
sets `overflow: hidden` on `.gradio-container`, which the CSS relaxes to
`clip` because otherwise nothing can be `position: sticky`. Both are in the
`GRADIO INTERNALS` section at the bottom of `theme.py`.

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

## Krea 2 V2 (Krea2 advanced graph)

The **🔶 Krea 2 V2** tab is the Krea2 advanced *KREA 2 TURBO/RAW* workflow ported
node-for-node into this app. It is a second text-to-image pipeline rather than
a variation of the first: its own model registry, its own VAE
(`wan21-vae.safetensors` from `wangkanai/wan21-vae`, which that workflow's
guide recommends over the stock Qwen VAE), its own 11-slot LoRA stack and its
own defaults. Nothing it does moves the Single tab, and vice versa. Feature
key `v2` — a license that does not grant it skips the ~17 GB of downloads
and hides the tab.

### Turbo / Raw

`V2_MODELS` works exactly like `KREA2_MODELS` and `FLUX_MODELS` — a **Model**
dropdown, and picking one resets that variant's defaults:

| | Turbo (default) | Raw |
| --- | --- | --- |
| model | `krea2_turbo_mxfp8.safetensors` (~13.5 GB) | `krea2_raw_fp8_scaled.safetensors` (~13.1 GB) |
| steps | 10 | 20 |
| CFG | 1.0 | 2.5 |
| Turbo LoRA slot | off | **on** at 0.6 |
| sampler | `linear/euler` + `bong_tangent`, eta 0.5, bongmath on, standard | same |

That is precisely the raw recipe from the source workflow's companion note, so
the two variants differ by exactly the three things it lists. **Raw costs no
extra disk** — `krea2_raw_fp8_scaled` is already in `KREA2_MODELS`, and
downloads are keyed on the destination path, so whichever registry asks for it
first fetches it and the other logs a cache hit.

The Turbo LoRA is toggled as **slot 1 of the visible LoRA stack**, not bolted
on inside the workflow builder. That is deliberate: it keeps the row editable
and, more importantly, makes it impossible to apply the LoRA twice when a raw
run also has that slot ticked by hand — the same "never applied silently" rule
the trigger words follow. Steps, CFG and the slot all stay editable after the
dropdown fires; whatever is on screen is what gets submitted.

Because steps and CFG belong to the model, they are **not** in
`V2_SAMPLER_DEFAULTS` — that dict holds only the knobs both variants share, so
the two numbers have one source of truth (`V2_VARIANT_DEFAULTS`).

Three things differ from the tabs above, and they are why this needs its own
builder (`workflow_krea2_v2.py`) rather than a flag on `build_workflow`:

- **`ClownsharKSampler_Beta`** (RES4LYF) replaces `KSampler`. `eta`,
  `bongmath` and the `bong_tangent` scheduler have no core equivalent. Steps
  and CFG come from the selected variant (see the table above); everything
  else is shared.
- **`RBG_Smart_Seed_Variance`** sits between the positive prompt and the
  sampler, perturbing the conditioning per seed so a batch varies without
  drifting off-prompt. Its combo values carry emoji (`🌱 Subtle`,
  `📸 Krea2 (SingleStream)`) and must match the node's option lists character
  for character or ComfyUI rejects the prompt.
- **LoRAs apply to the model *and* the CLIP.** The source uses rgthree's
  Power Lora Loader in "Single Strength" mode, so the chain here is
  `LoraLoader`, not the `LoraLoaderModelOnly` the other Krea tabs build.
  Each row keeps its own **On** checkbox, which is that node's per-row toggle.

### What was translated rather than copied

The app submits **API-format** graphs, so purely visual nodes have no
counterpart and are dropped: rgthree's *Fast Bypasser* (a UI toggle whose
output goes nowhere), *Label* and *MarkdownNote*. Two more are translated,
which changes no pixels:

| Source node | Here | Why |
| --- | --- | --- |
| Power Lora Loader (rgthree) | `LoraLoader` chain | identical math; on/off becomes the row's checkbox |
| ResolutionSelector → PrimitiveInt → EmptyLatentImage | `resolve_size()` in Python | integer plumbing; the tab shows the W×H it resolved to |
| WAS `Image Save` | `SaveImage` | the app finds outputs through ComfyUI's history, and WAS writes its own dated tree the Gallery tab would not see |

`ImageSharpen` and `FilmGrain` are **bypassed (`mode: 4`) in the source
workflow**, so both start off and the tab reproduces it as shipped —
`VAEDecode` straight to `SaveImage`. The Post-processing accordion turns them
on per job, in that order.

Resolution follows the source's aspect + megapixel scheme rather than a preset
list. Megapixels count as 1024² (ComfyUI's own convention, as in
`ImageScaleToTotalPixels`) and each side rounds to the nearest `multiple`, so
the workflow's 3:4 at 1.5 MP with multiple 8 resolves to **1088×1448**.

### Node packs

Bootstrap clones three packs for this tab, and `app.py` verifies each class
actually registered after ComfyUI starts:

| Pack | Node | Needed for |
| --- | --- | --- |
| [RES4LYF](https://github.com/ClownsharkBatwing/RES4LYF) | `ClownsharKSampler_Beta` | the sampler — required |
| [ComfyUI-RBG-SmartSeedVariance](https://github.com/RamonGuthrie/ComfyUI-RBG-SmartSeedVariance) | `RBG_Smart_Seed_Variance` | conditioning variance — required |
| [ComfyUI-post-processing-nodes](https://github.com/EllangoK/ComfyUI-post-processing-nodes) | `FilmGrain` | the optional grain toggle only |

A failed clone disables this tab and nothing else, the same contract the Wan,
Flux and ReActor installs follow.

### LoRA stack

All eleven rows from the source workflow are present in its order, with its
strengths and its on/off states — seven on by default. They download from
CivitAI (`CIVITAI_TOKEN` needed for most) into the shared `loras/` folder.
A row whose file did not download starts disabled and is named in the status
line under the tab header, so a missing LoRA never submits an unresolvable
`lora_name`.

Two filenames are worth knowing about: the companion guide links CivitAI
version `3109006` for the realism-engine family while the workflow names the
file `realism_engine_krea2_v3.1.safetensors`, and `krea2_Enhancer.safetensors`
is saved with the workflow's capitalisation rather than the guide's. Both are
in `V2_LORA_STACK` in `config.py` — the graph only cares that the name on disk
matches the name in the slot, so adjust the version id there if CivitAI serves
a revision you did not expect.

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

Feature key `faceswap` — a license granting it installs the node pack,
fetches the ~1.8 GB of models and shows the tab.

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
(~18 GB) and Flux 2 VAE — ~57 GB of downloads from `Comfy-Org/flux2-dev`,
fetched only for a license granting feature key `flux_t2i`. Flux 2 is
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

## Klein Edit (Klein advanced FLUX.2 Klein 9B graph)

The **🧩 Klein Edit** tab is the Klein *FLUX.2 KLEIN 9B EDIT v1.3*
workflow ported node-for-node into this app. It **edits** images rather
than generating them: upload a picture, describe the change, and the
source is scaled to 1 MP, VAE-encoded and attached to the conditioning as
a `ReferenceLatent`, so the model actually sees what it is editing. Every
node it runs is stock ComfyUI, so unlike the Krea 2 V2 tab it installs no
custom node packs. Feature key `klein` — a license granting it fetches its
~19 GB and shows the tab.

| File | Source | Size |
| --- | --- | --- |
| `flux-2-klein-9b-fp8.safetensors` | `black-forest-labs/FLUX.2-klein-9b-fp8` | ~9.4 GB |
| `qwen_3_8b_fp8mixed_abliterated.safetensors` | `edicamargo/qwen_3_8b_fp8mixed_abliterated` | ~9.2 GB |
| `flux2-vae.safetensors` | `Comfy-Org/flux2-dev` | ~0.34 GB |
| 3 LoRAs → `loras/klein/` | CivitAI (`CIVITAI_TOKEN` needed) | ~0.5 GB |

Nothing is shared with the Flux 2 tab except that VAE file, and downloads
are keyed on the destination path — so enabling both fetches it once, and
enabling only `klein` still fetches it. At 9.4 GB Klein is a quarter the
size of Flux 2 Dev, which is what makes swapping to and from the Krea
tabs cheap rather than a 1–3 minute stall.

**Two input images.** The source workflow's second image group is
bypassed as shipped, so the tab starts with one. Tick **Enable input
image 2** to combine two sources, and name them in the prompt the way its
own note tells you to — *“the person from image 1 is wearing the hat from
image 2”*. Both images go through the same 1 MP scale + encode chain and
their reference latents stack on the conditioning in order.

**Reference size and output size are different settings.** The reference
is what the model looks at (the workflow's 1 MP); the output is the empty
latent it paints into. The workflow's three resolution groups are
reproduced as a **Mode** dropdown, with only the first live as shipped:

| Mode | What it does |
| --- | --- |
| Same as image 1 (default) | renders at the source image's own resolution |
| Scale image 1 to megapixels | keeps the aspect ratio at a chosen MP |
| Custom width × height | exactly what you type |

"Same as image 1" means what it says: a 12 MP phone photo asks for a
12 MP render. Each side is snapped to /16 and clamped at 4096 px, and the
tab warns under the controls once the resolved size passes ~4 MP —
otherwise one paste can OOM-kill the ComfyUI server that every other tab
shares. Switch to scale or custom mode to render smaller.

**Sampling** is the workflow's `KSamplerAdvanced` at 8 steps, CFG 1.0,
`euler`/`normal`, with `FluxGuidance` 4.0 and a `ConditioningZeroOut`
negative — Klein is guidance-distilled, so at CFG 1.0 the negative branch
is never evaluated and guidance is what steers prompt adherence. Steps,
CFG, guidance, sampler and scheduler are all editable; the rest
(`add_noise`, `start_at_step`/`end_at_step`, leftover noise) live in
`KLEIN_SAMPLER_DEFAULTS` and spell "run the whole schedule in one pass".

**LoRAs.** All three rows from the source workflow are present in its
order, at strength 1.0, on by default, applied to the model *and* the
CLIP (rgthree Power Lora Loader in Single Strength mode → a `LoraLoader`
chain). They live in `loras/klein/` so they never mix with the Krea 2 or
Flux dropdowns — Klein 9B is a third incompatible architecture. A row
whose file did not download starts disabled and is named in the status
line under the tab header.

What was translated rather than copied, all of it pixel-neutral:

| Source node | Here | Why |
| --- | --- | --- |
| Power Lora Loader (rgthree) | `LoraLoader` chain | identical math; on/off becomes the row's checkbox |
| `GetImageSize` → `EmptyFlux2LatentImage` | `resolve_output_size()` in Python | integer plumbing; the tab shows the W×H it resolved to |
| Any Switch (rgthree) | the Mode dropdown | it selects whichever resolution group is not bypassed |
| WAS `Image Save` | `SaveImage` | the app finds outputs through ComfyUI's history, and WAS writes its own dated tree the Gallery tab would not see |

Fast Groups Bypasser, Label, MarkdownNote, `PreviewImage` and Image
Comparer are display-only and have no API-format counterpart, so they are
dropped. The companion note's GGUF quants are **not** a registry entry
away: they load through `UnetLoaderGGUF` from a custom node pack rather
than `UNETLoader`, which would need a builder branch and a node install.

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
while overlapping). The Video tab needs feature key `wan_i2v` on the license —
that is what fetches the models and shows it, and `KREA2_WAN_PARALLEL`
only buys a second ComfyUI instance when it is granted.
Make sure the pod volume has room: Krea (~32 GB) +
Wan (~49 GB) + ComfyUI needs a ≥ 100 GB disk (a 120 GB volume fits with
~35 GB left for outputs).
