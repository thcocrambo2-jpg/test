# Krea 2 on RunPod — ComfyUI + Gradio

A standalone Python app (converted from the Kaggle notebook) that bootstraps
ComfyUI, downloads the Krea 2 Turbo models + LoRAs plus the Wan 2.2
image-to-video models, and serves a Gradio UI.

## The four ways to run it

One app, one licence server, four ways of getting the app onto a machine.
They differ in who compiles it and what the machine has to have already;
they do **not** differ in what the app does, and all four take a seat
through the same licence check.

| | For | What runs | Where the app comes from |
| --- | --- | --- | --- |
| **1. Linux binary** | RunPod customers | `scripts/runpod_start.sh` → `dist/krea2app` | built by `./build.sh` on a pod, fetched from the licence server on every start |
| **2. Windows binary** | Windows customers | `scripts/windows_start.ps1` → `dist\krea2app.exe` | built by `.\build.ps1` on Windows, fetched the same way |
| **3. Docker image** | anyone with a GPU and Docker | `docker compose up` | the image carries the *environment*; the binary is still fetched by `runpod_start.sh` inside it |
| **4. From source** | you, while developing | `python app.py` | your working tree |

**Which one to use.**

- **A customer on RunPod** gets 1. It is the proven path and the one the
  pricing and support flow assume.
- **A customer on their own Windows PC with an NVIDIA card** gets 2. They
  need Python 3.12 and git installed — the `.exe` is not self-contained
  (see [Windows binary](#windows-binary-buildps1)).
- **A customer who wants a reproducible environment**, or who is on Linux
  but not RunPod, gets 3. It removes the first-boot ComfyUI install, not
  the model download.
- **You, changing code**, use 4. Nothing else lets you test what you just
  edited: 1, 2 and 3 all run the last *published* build.

**Which are proven.** 1 and 4 are what production runs on. 3 is written
but has never been built or run — treat it as unverified. 2 is new; what
has and has not been tested is spelled out in its section below.

The two build scripts are a matched pair and neither can produce the
other's artifact — Nuitka compiles for the OS it runs on and cannot
cross-compile. They bundle the same set of data files and packages, and
`make check-args` fails the build if that ever stops being true.

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

## Running locally on Windows

This is the **source** path on Windows — `python app.py` against a checkout,
which is what you want while changing code. A Windows *customer* runs the
compiled `.exe` instead and never sees a repository; that is
[Windows binary](#windows-binary-buildps1). The requirements below are the
same either way, because the `.exe` bundles the app and nothing else.

The app targets a pod, but it runs on a local Windows machine with an NVIDIA
GPU. Three things differ from a pod, and the app handles all three:

- **No PyTorch base image.** On a pod torch arrives with
  `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. Locally nothing supplies
  it, so `bootstrap.ensure_torch()` installs the same versions on first run.
- **`KREA2_BASE_DIR` has no useful default.** It falls back to the pod path
  `/workspace/krea2`, which on Windows silently resolves to
  `C:\workspace\krea2`. Set it.
- **Symlinks need permission.** `link_model_dirs()` symlinks ComfyUI's model
  folders. Turn on Developer Mode (Settings → System → For developers) or run
  from an elevated shell.

### 1. Environment

```powershell
conda create -n krea2 python=3.12 -y
conda activate krea2
```

Python 3.12 because that is what Ubuntu 24.04 ships, so it is the version the
pod is tested on. If `conda activate` does nothing, run `conda init powershell`
once and reopen the terminal.

`git` must be on PATH — ComfyUI is cloned, not vendored:

```powershell
git --version                       # if this fails:
conda install -c conda-forge git -y
```

### 2. Configuration

Pick a directory with **45+ GB free**, outside the repo (`dist/` is the Nuitka
build output and is git-ignored, so a `git clean -xdf` would take your weights
with it):

```powershell
$env:KREA2_BASE_DIR    = "C:\krea2"
$env:KREA2_LICENSE_KEY = "<your key>"
$env:KREA2_NODE_TAG    = "<your node tag>"
$env:HF_TOKEN          = "<hf read token>"   # optional, avoids rate limits
```

Those last as long as the terminal. To persist one:

```powershell
[Environment]::SetEnvironmentVariable("KREA2_BASE_DIR", "C:\krea2", "User")
```

Never commit a file containing these — the license key and node tag are
credentials.

### 3. Run

```powershell
python app.py
```

Nothing else to install. The first run takes a while: it installs PyTorch if
absent, clones ComfyUI and its requirements, downloads whatever weights the
license's features need, starts ComfyUI, then serves the UI. Every step is
idempotent, so an interrupted run resumes rather than restarting.

How much it downloads depends entirely on the license — see Features. A
`krea_t2i` + `gallery` licence pulls ~31 GB; adding `flux_t2i` or `wan_i2v`
pulls tens of GB more.

### PyTorch

`ensure_torch()` runs before anything else installs packages, because
ComfyUI's own `requirements.txt` lists `torch` unpinned — on a machine without
one, that pip pass would take whatever PyPI defaults to.

**Whatever is already installed wins.** The check is capability-based, not
version-matching: torch imports, CUDA is available, this card's `sm_XX` is in
the build's arch list, and a real matmul runs on the GPU. A pod on a different
torch than the pins passes untouched. The pins are used only when torch is
missing entirely.

That last check matters on Blackwell cards (RTX 50xx, `sm_120`), which have no
kernels before CUDA 12.8. A default PyPI wheel installs cleanly, imports
cleanly, reports `cuda.is_available()` as `True`, and then fails at the first
generation — so version strings are not trusted and a kernel is actually
launched.

If torch is present but unusable, the app **raises and names the fix** rather
than replacing it: on a pod that torch is the tested base image. Only
`torchvision`/`torchaudio` are auto-repaired, with `--no-deps` so torch cannot
be moved.

To use a different CUDA line, install it yourself before the first run and
`ensure_torch()` will accept it:

```powershell
pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

Pin all three. They ship compiled extensions linked against each other's ABI,
and `torchaudio`'s version tracks torch's exactly.

### Checking it worked

A healthy startup logs this line before ComfyUI starts:

```
PyTorch OK — torch 2.8.0+cu128 (CUDA 12.8) on NVIDIA GeForce RTX 5050 Laptop GPU [sm_120]
```

To check the GPU stack on its own:

```powershell
python -c "import torch, torchvision, torchaudio; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability())"
nvidia-smi
```

For UI work you do not need any of this. `scripts/dryrun.py` stubs out ComfyUI
and needs no GPU, no license and no weights:

```powershell
python scripts/dryrun.py --features all
```

It cannot generate — it is for the interface only.

### Hardware

The models are sized for a pod, so a consumer card is the constraint:

| | Needed |
| --- | --- |
| VRAM | The Krea 2 UNet alone is ~13 GB. Below that, ComfyUI's single-GPU path offloads the remainder to system RAM — it works, but expect minutes per image rather than seconds. |
| System RAM | Whatever does not fit in VRAM streams from RAM every step. With 8 GB of VRAM, budget ~15 GB free; less means swapping. |
| Disk | ~31 GB for `krea_t2i`, plus the ComfyUI checkout. |

Close memory-hungry applications before generating. The download phase does
not care, so the practical order is: start the download, free RAM before the
first generation.

### Troubleshooting

| Symptom | Cause |
| --- | --- |
| `OSError: [WinError 127] The specified procedure could not be found` | `torchvision`/`torchaudio` compiled against a different torch. `ensure_torch()` repairs this automatically; it only surfaces if something installed a mismatch afterwards. |
| `no kernel image is available for execution on the device` | torch has no kernels for this GPU. The startup check catches it and prints the install command. |
| `IndexError: list index out of range` in `resolve_model` | A model registry in `config.py` was emptied. Trimming one to a single entry is fine; emptying it is not — several are indexed at `[0]` during import. |
| `WinError 193` from `cloudflared` | The gradio.live tunnel did not answer and the fallback downloads a Linux binary. Everything already downloaded is cached, so a re-run skips straight past it. |
| Exits within seconds, no downloads | `KREA2_LICENSE_KEY` / `KREA2_NODE_TAG` unset — the license seat is taken before any expensive work. |
| Weights land somewhere unexpected | `KREA2_BASE_DIR` unset, so the pod default resolved to `C:\workspace\krea2`. |

## Docker image

An image that carries the environment — the ComfyUI checkout at its pinned
SHA, the custom node packs, ComfyUI's Python requirements, ReActor's
dependency set, a working onnxruntime — so a machine spends none of its
first boot on them.

It does **not** carry the app. `scripts/runpod_start.sh` is baked in
unmodified and still fetches the binary from the licence server, which
means publishing a build reaches these containers exactly as it reaches
every other pod, `make promote` can still roll them back, and the image
holds no secret and no licensed code. An app release needs no image
rebuild.

```
/entrypoint.sh  ->  /opt/krea2/bin/start.sh  ->  the binary
 (symlinks the       (fetches + verifies       (bootstrap finds
  baked ComfyUI)      the build)                everything present)
```

Models are never baked: ~90 GB, licence-gated, and they belong on the
volume.

### Running it on your own machine

```
copy .env.example .env      # then fill in the two values
docker compose up
```

Nothing is set in the shell and no flags are typed — compose reads `.env`
from the project directory on its own, and `docker-compose.yml` carries
the GPU reservation, the shared-memory size, the port and the volume. The
UI link is printed in the output, and `http://localhost:7860` also works.

On Windows this needs Docker Desktop on the **WSL2 backend** plus a current
NVIDIA driver; no CUDA toolkit is installed on Windows. The Hyper-V backend
cannot pass a GPU through at all, and it shows up as
`torch.cuda.is_available()` being False in the log rather than as an error.

Storage is the named volume `krea2-data`, never a bind mount to a host
folder — see the comment in `docker-compose.yml` for why. It lives in the
WSL2 virtual disk on `C:`; Docker Desktop → Settings → Resources →
Advanced → *Disk image location* moves it to a drive with room for 90 GB.

`docker compose down` keeps the models, so the next `up` finds them already
there and downloads nothing — the same thing `KREA2_BASE_DIR=./tmp` does
for a local `python app.py`. **`docker compose down -v` deletes the
volume** and the weights with it.

### Reusing models you already downloaded

Every download guards on the file simply existing under `MODELS_DIR`
(`downloads.py`), so weights copied in by hand count as downloaded. Nothing
on the host can write into a named volume directly, so the copy runs in a
throwaway container that can see both:

```powershell
docker run --rm `
  -v krea2-data:/workspace `
  -v C:\path\to\your\tmp\models:/seed:ro `
  alpine sh -c "mkdir -p /workspace/krea2/models && cp -an /seed/. /workspace/krea2/models/"
```

Run it before the first `docker compose up`, or the app will already be
downloading the same files. The source is mounted read-only, so this cannot
touch the originals.

`cp -an` is no-clobber, which makes the command re-runnable: after
downloading more models locally, run it again and only the new files copy.
The one thing it will not do is replace a file that is already in the
volume — to refresh a corrupt one, delete it there first.

Seeding is optional for anything in `config.py`'s registries; the app
downloads what is missing on its own, and copying only saves the
bandwidth. It is the *only* route for files you added by hand, such as a
LoRA that no registry lists.

Copy `models/` only. The image has its own ComfyUI at the pinned SHA, and
the entrypoint leaves a real `$KREA2_BASE_DIR/ComfyUI` directory alone —
seeding one there would shadow the baked copy for no benefit.

### On RunPod

Use the image as the template's container image and leave the start command
empty. Expose HTTP port `7860`, mount the network volume at `/workspace`,
and leave `KREA2_LICENSE_KEY` and `KREA2_NODE_TAG` **empty** in the
template — a template is public, so a key typed into one is a key given to
everyone (the reasoning is in `scripts/runpod_start.sh`'s header). The
customer fills them in on their own pod.

Pods on the existing template are unaffected and keep fetching
`start.sh` from `/v1/start.sh` as before.

### Running your working tree in the image

`docker compose up` runs the binary the licence server hands out — whatever
`make release` last shipped, not the code in this directory. To run the
working tree instead, in the same CUDA/ComfyUI environment a customer gets:

```
make image                                            # krea2:latest
docker compose -f docker-compose.dev.yml up --build    # krea2:dev, then run
```

Edit, Ctrl-C, `up` again. The repo is bind-mounted at `/src` and
`KREA2_DEV_SOURCE=/src` is what makes the entrypoint skip the download and
`exec python3 app.py`.

It shares the `krea2-data` volume with the production service, so a dev run
does not download another 90 GB. If you have not run the production service
yet, `docker volume create krea2-data` first — the dev file declares the
volume `external` so it cannot silently create a second, empty one.

Two things are deliberately *not* different in dev mode. The licence seat is
still taken, because that is the app's behaviour and a dev mode that skipped
it would be testing something no customer runs. And the environment is the
production image, so a dependency that is missing there is missing here.

`docker/Dockerfile.dev` only adds `requirements.txt` — gradio and the rest,
which the production image has no use for because `build.sh` compiles them
into the binary. It is an optimisation rather than a requirement: run from
source, `config.FROZEN` is False, so `bootstrap.install_comfyui()` installs
them itself. Without the dev image that pip pass repeats on every run,
because the container it writes into is deleted on `down`.

Do not run both compose files at once — they would contend for port 7860
and the GPU.

### Building and publishing

```
make image                                # -> krea2:latest
make image-push REGISTRY=ghcr.io/<owner>  # -> ghcr.io/<owner>/krea2:latest
```

Neither needs credentials from `license-validator/.env`; nothing in the
image is secret. `.dockerignore` is an allowlist rather than a list of
exclusions, so a file that is not named in it cannot reach the build
context at all — that is what keeps `license-validator/.env`, the root
`.env` and `dist/krea2app` out of a published image.

What goes into the image comes from `scripts/PINS.json`: `docker/bake_nodes.py`
runs `bootstrap.py`'s own clone and mirror-tarball helpers at build time, so
bumping a pin is the only edit needed to move the image. Every container
prints what it was built from on boot.

### Escape hatches

| Variable | Effect |
| --- | --- |
| `KREA2_USE_BAKED_COMFY=0` | Ignore the baked ComfyUI; the app clones and pip-installs its own, which is the non-Docker behaviour. For a baked tree that turns out to be wrong. |
| `KREA2_START_SOURCE=api` | Fetch the start script from the licence server instead of using the baked copy, so a fix there applies without a new image. |
| `KREA2_IMAGE` | A published tag to run instead of a locally built one. |

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
| `krea_v2_edit` | 🔷 Krea 2 V2 Edit       | ~1.9 GB + V2   |
| `krea_inpaint` | Inpaint / Img2Img       | base only      |
| `faceswap`     | 🎭 Face Swap (ReActor)  | ~1.8 GB        |
| `flux_t2i`     | 🌊 Flux 2               | ~57 GB         |
| `klein_i2i`    | 🧩 Klein Edit           | ~19 GB         |
| `wan_i2v`      | 🎬 Video (Wan 2.2)      | ~49 GB         |
| `json_batch`   | JSON Advanced Batch     | none           |
| `community_prompts` | 🌟 Prompt Library  | none           |

The four shipped plans stack: `starter` (19/mo) is the first three keys,
`creator` (39) adds the editing set and the prompt library, `pro` (59)
adds Flux 2 and Klein, and `studio` (89) adds video and JSON batch.
`license-validator/README.md` has the full table and how to change it.

Shared weights are handled for you — `krea_edit` and `krea_inpaint` both
run the Krea 2 base models, so granting either one fetches them, and
granting all three fetches them once. `krea_v2_edit` is the same trick one
level over: it shares the Identity Edit LoRA with `krea_edit` and the ~17 GB
of weights with `krea_v2_t2i`, so its own cost is only whichever of those
two is not already granted.

`krea_v2_edit` ships on the internal `admin` plan only. Put it on
`creator`/`pro`/`studio` in `license-validator/src/plans.js` (next to
`krea_edit`) when it should be something a customer can buy.

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

### The pricing page

`/pricing` is a page of its own next to the tabs: every public plan, what
it costs, and the tabs it includes, with the customer's own tier marked.
It is reachable from the **Plans & pricing** button in the app bar, next to
the plan and expiry that bar already shows.

The data is the license server's own catalogue — `plans.py` reads
`GET /v1/plans` at page load and caches it for five minutes, so a price or
a feature list edited in Atlas shows up without redeploying the app or
restarting the pod.

Prices are quoted per billing cycle. The server sends the cycles it is
offering and what each one costs, and the page renders a tab per cycle —
Monthly, Quarterly, Yearly — with the saving badged on the tab and on the
card. Cycles that are switched off are not sent and get no tab, and with
only one cycle live there is no tab bar at all. Launching quarterly or
yearly pricing is therefore a flag on the license server: no rebuild here,
and no new binary for the pods. **Reading it changes nothing.** Which tabs a pod
builds still comes only from the flat `features` array in the acquire
response, so a catalogue that is stale, empty or unreachable costs the
page its cards and nothing else — it renders a panel saying so, with a
Refresh button, and every tab keeps working.

The tier marked "Your plan" comes from `plan_id` on the acquire response.
A license that lists its features directly instead of naming a plan has
none, which is normal — the page then marks nothing and says so, and the
app bar calls it a "Custom licence".

The bar's expiry pill comes from `expires_at` on the same response
(`licensing.expires_at()`), and it is **display only** — the server refuses
an expired key at acquire and stops answering its heartbeat, so nothing in
the app reads that date to decide anything, and a pod with a wrong clock
cannot lock a customer out of a valid key. A key with no end date, and a
license server too old to send the field, both render no pill at all.

## Prompt library (`community_prompts`)

The 🌟 Prompt Library tab is a catalogue of working recipes for the Krea 2
and Krea 2 V2 tabs. Every card carries a prompt and the whole settings
blob behind it; **Use** writes all of it into that tab's controls and
switches to it. Two kinds of card:

- **⭐ Official** — written by you, with `npm run prompts -- --add`. Public
  the moment they exist.
- **👥 Community** — captured from customers' own generations. **Private
  on arrival**, and invisible until approved.

### Capture is silent, and deduplicated

`generate_single` and `generate_v2` call `prompts.record()` with what they
were given, right after their validation guards and before any work. That
call cannot slow generation down and cannot fail it: it fingerprints the
recipe, drops it on a bounded queue, and one daemon thread does the HTTP.
Every error on that path dies in a `debug` log — **nothing about this is
ever shown to the customer.**

### Your own pods do not capture (`is_admin`)

A license marked `is_admin` behaves differently, and only here — it is a
role, not an entitlement, so it grants no tab and changes nothing about
what a pod can generate:

```bash
npm run issue-key -- --name "Internal" --plan admin --seats 3 --admin
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --admin --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --no-admin --update
```

On an admin pod **nothing is captured automatically.** Instead the Krea2
and Krea2 V2 tabs grow a `⭐ Publish this prompt to the library` checkbox
above Generate, off by default, with an optional card title next to it.
Tick it, press Generate, and that prompt goes live as an **⭐ official**
card immediately — no review, because you are the reviewer. Untick it and
nothing is stored at all. The box unticks itself once the run finishes,
clearing the title with it: publishing is a per-image decision, so it
cannot stay armed into the next generation by accident.

That split exists so your own testing does not fill the review queue you
are the one working through, and so an official prompt is something chosen
rather than something collected.

Two details worth knowing:

- The checkbox is **hidden**, not disabled, for customers. A greyed-out
  "publish to the library" box would tell them their prompts are being
  saved, which is the one thing this feature must not do. The components
  are still built (a tab's input list is fixed at build time) and a hidden
  checkbox sends `False`, so a customer pod captures exactly as before.
- Publishing **bypasses the deduplication**. It is one deliberate tick of a
  checkbox, not a recipe the app happened to notice, so publishing the same
  prompt twice really does create two cards.

The server enforces both halves against the license document rather than
trusting the pod: `publish: true` from a license without `is_admin` is not
an error, it is just ignored and stored as an ordinary community
submission.

The fingerprint is a sha256 over the prompt, the negative and every
setting **except the seed, the 🎲 toggle and the batch count**. That
exclusion is the point: with a random seed every click would otherwise
look like a new prompt, and twenty re-rolls of one idea would be twenty
requests and twenty rows. Those three values are still *stored*, so a
loaded prompt arrives with the seed it was made with — they just do not
decide whether two recipes are the same one. The pod remembers the last
500 fingerprints; the unique index on the server collapses whatever gets
past that (a restart, a second pod on one key, two customers who typed the
same thing).

### Approval

Nothing a customer submits is public until you say so:

```bash
cd license-validator
npm run prompts                      # the review queue
npm run prompts -- --show <id>       # prompt, settings, which licence
npm run prompts -- --approve <id>
npm run prompts -- --reject  <id>    # decided, and never queued again
```

`--reject` is not a delete. It sets `reviewed_at` and leaves `is_public`
false, so the row leaves the queue for good — and because the fingerprint
stays unique, the same recipe submitted again tomorrow collapses onto the
row you already ruled on instead of coming back as new work.

One licence may have at most 200 un-reviewed prompts waiting. Past that
its writes are dropped and answered `200` — a customer who was never told
their prompts are saved cannot be told they have been throttled.

### Cross-pod safety

A prompt written on one pod is loaded on another that may have different
models and LoRA files. Every value is checked against what *this* build
offers before it is applied: an unknown model, resolution or sampler
leaves its control alone, a missing LoRA file resets that slot to `None`,
and numbers are clamped into their slider's range. A card always loads —
worst case it loads slightly less of itself.

A card for a tab this licence does not grant still renders, with its
button reading `🔒 Needs Krea2 V2` instead of being hidden. What the tab
you have not bought can do is exactly the thing worth seeing.

## Settings presets

The Krea2 and Krea2 V2 tabs open with an **⚙️ Preset** dropdown. Picking one
writes every control below it — model, steps, CFG, resolution, sampler,
seed, batch count, the whole LoRA stack — and **leaves the prompt boxes
alone.** That is the entire difference between a preset and a library card:
same settings blob, same guarding, minus the words.

Presets live on the licence server, so changing one changes it for every
customer without shipping a binary.

### The Edit tabs read the same list

**✨ Krea2 Edit** shows 🎨 Krea2's presets, and **✨ Krea2 V2 Edit** shows
🔶 Krea2 V2's — the same dropdown, at the top of the same column, filled
from the generation tab it shares a pipeline with. A recipe is a recipe
whether the pixels come from noise or from an uploaded image, and dialling
one back in by hand to edit with it was the whole friction.

Nothing is saved twice. A preset still belongs to the tab it was saved
from — `presets.TABS` is still those two, one row in one collection — and
an Edit tab simply reads it and writes the part it has controls for:

| Not applied on ✨ Krea2 Edit | Not applied on ✨ Krea2 V2 Edit |
| --- | --- |
| `resolution` — the source image sets the output size | `aspect`, `megapixels`, `multiple` — likewise |
| | `denoise` — the source reaches the model through conditioning, not the starting latent |
| | Sharpen and film grain — generation-tab controls |

Everything else transfers as stored: model, steps, CFG, sampler, seed,
randomize, batch count and the LoRA stack on Krea2 Edit; the whole
ClownsharKSampler and Smart Seed Variance blocks and the eleven-row stack
on V2 Edit. The controls that belong to editing alone — grounding,
reference fidelity, the second-reference switch, the fit mode — are left
exactly where you set them, because no preset carries them.

The `is_default` preset is applied to the Edit tabs on page load too, so a
pod whose Krea2 default says 12 steps does not open its Edit tab at 8. The
🔄 button and the save-from-the-queue refill work there as they do on the
generation tabs.

Saving is unchanged: the `💾 Save these settings as a preset` tickbox is a
generation-tab control, so an Edit tab reads presets and never writes one.

### Only an admin can write one

Same gesture as publishing a prompt, and the same rule behind it. An admin
pod grows a `💾 Save these settings as a preset` checkbox next to the
publish one, with an optional name box beside it. Tick it, press Generate:
whatever the controls hold at that moment is saved and appears in the
dropdown for every pod on its next start or 🔄. The box unticks itself when
the run finishes, exactly like publishing.

Unlike publishing, this one **reports back** — a first line on the status
box saying `✅ Preset "Portrait · soft light" saved.` or why it did not.
Prompt capture is silent because the customer was never told it happens; a
preset is a box you deliberately ticked and are waiting on.

The customer's half of the tab is read-only: the checkbox is hidden (their
input list is fixed at build time, and a hidden checkbox sends `False`), and
`POST /v1/presets` refuses `403 forbidden` for any licence the document does
not mark `is_admin`. The server checks the record, never the flag on the
request.

### Every tick is a new preset

Load `Default`, change six things, tick, Generate — you get a **new** preset
and `Default` is untouched. That is the ordinary gesture, so saving never
overwrites:

- **A name already in use** becomes `Portrait (2)`, then `(3)`. The status
  line names whichever it got (`✅ Preset "Portrait (2)" saved. (that name
  was taken)`), because it is not always the name you typed. Twenty of one
  name is the cap, and past it the save is refused rather than looping.
- **A blank name** is stamped with the time — `Preset 2026-08-09 15:19` —
  rather than refused. A ticked box that silently saved nothing is the worse
  outcome; rename it afterwards with
  `npm run presets -- --rename <id> --to "…"`.

The dropdown selection is left where it is unless the name you typed is
exactly what got stored, so it never claims to be showing a preset that is
not the one just written.

Editing a preset **in place** is deliberately the admin tools' job, where
naming an existing preset is the whole point of the call:
`npm run presets -- --add --tab … --name "Portrait" --settings ./x.json`
overwrites `Portrait`'s settings and leaves its flags alone.

The `(tab, name)` unique index is what makes all of this safe: it is the
only race-free answer to "is this name free", so two admins saving the same
name in the same second get two presets rather than one landing on the
other's row.

### The enable flag, and the default

```bash
cd license-validator
npm run seed-presets                          # the shipped defaults, as presets
npm run presets                                # list everything, on and off
npm run presets -- --show <id>                 # one preset's whole settings blob
npm run presets -- --disable <id>              # out of every dropdown, kept in the db
npm run presets -- --enable  <id>
npm run presets -- --default <id>              # what a fresh session opens on
npm run presets -- --order <id> --to 10        # where it sits in the dropdown
npm run presets -- --rename <id> --to "Portrait, soft"
npm run presets -- --delete <id>
```

Two flags decide what customers see:

- **`enabled`** — `GET /v1/presets` returns nothing else. `--disable` is the
  tool for a preset that turns out to be wrong: it leaves the dropdowns
  immediately and the row stays put, so `--enable` brings it back with
  nothing retyped. `--delete` is for the preset that should never have
  existed.
- **`is_default`** — at most one per tab, and it is the one a **fresh page
  load applies** — to that tab and to its Edit tab. Setting it on one clears
  it on every other preset for that tab, so there is only ever one answer to
  "what does this tab open on".

`npm run seed-presets` writes the values compiled into `config.py` as a
preset called `Default` on each tab and marks it `is_default`. Nothing about
what anyone gets changes on the day you run it — it makes what everyone
already gets nameable, and therefore editable from Atlas without a rebuild.
Those compiled values stay the floor: they are what the controls are built
with, and what a pod that cannot reach the server keeps.

Re-running the seed updates the settings and **never touches the two
flags** — a preset you switched off stays off — the same `$set`/
`$setOnInsert` split `seed-prompts` makes.

### What a preset costs at startup

The list is fetched once while `ui.py` builds its Blocks (one request, all
tabs), which is after the seat check, so the licence server is already on
that path. A server that is slow or down costs the dropdown and nothing
else — every control keeps its compiled default and the tab generates
normally. Failures are cached for 30s so a bad minute does not put a
timeout on every page load.

### Cross-pod safety

Identical to the prompt library's, and the same code: an unknown model,
resolution or sampler leaves its control alone, a missing LoRA file resets
that slot to `None`, numbers are clamped into their slider's range, and a
preset name that has since been disabled or renamed applies nothing rather
than blanking the tab.

One wrinkle worth knowing, shared with the library's **Use** button:
applying a preset that names a *different* model fires the Model dropdown's
own change handler, so Steps and CFG land on that model's variant defaults
rather than the preset's. Everything else applies as stored, and a preset
for the model already selected — the ordinary case, and the only one when a
registry holds a single model — is unaffected.

## The job queue

Clicking **Generate** does not generate. It writes the click down — the
handler, the values every control held at that moment, the tab it came
from — hands it to `jobqueue.py` and returns in a few milliseconds. A
worker thread per lane then runs the recorded jobs one at a time, in the
order they arrived.

That indirection buys three things:

- **The button comes straight back.** Queue a second idea while the first
  is still rendering. Previously the whole render happened inside the
  Gradio event the click fired, and Gradio's default `trigger_mode="once"`
  left the button dead until it finished — so the pod idled between jobs
  whenever nobody was sitting there to click again.
- **The queue is visible.** Gradio's own queue serialised the work
  perfectly well, but nothing could see into it. The panel under the tabs
  lists every job, live, with its tab, its prompt and its progress.
- **And it can be edited.** Each row carries a button, and what it does
  depends on the job: **✕ Remove** forgets one that is still waiting,
  **🛑 Stop** interrupts one that is already running, **✕ Clear** tidies a
  finished one off the list. **🧹 Clear finished** does the last of those
  in bulk.

The panel sits under the tabs rather than inside any of them, because the
queue belongs to the pod and not to a tab: a Krea job and a video are
waiting on the same GPU. Its label carries the counts (`🗂️ Queue — 1
running · 3 waiting`), so a collapsed panel still says how deep the queue
is.

### Lanes

A lane is one worker, which makes it exactly the "only one of these at a
time" rule that `concurrency_id` used to state:

| Lane | Jobs | ComfyUI instance |
| --- | --- | --- |
| `comfy` | every image tab, and video when it shares an instance | the main one |
| `wan` | video, only when `KREA2_WAN_PARALLEL=1` | the second one |

A running job is stopped through `ComfyClient.interrupt()` — ComfyUI's
`POST /interrupt` — on its lane's instance. Stopping is asynchronous by
nature: the interrupt aborts the prompt ComfyUI is executing *now*, and
the worker sees the cancel flag at the job's next yield and stops feeding
it the rest of its batch. So a batch of eight can finish the picture it is
on before the queue lets go of it, which is why the row says *stopping*
rather than *stopped* until it is.

### How results get back to a tab

The click is long over by the time there are any, so it cannot push them.
Instead the panel carries a `gr.Timer`; one poll a second redraws the
queue and writes each job's latest yield into the components that
submitted it (`_queue_tick` in `ui.py`). Every generation tab registers
those components on the way past with `_queue_view`, so a licence granting
three tabs polls three tabs.

Two details keep that cheap. The poll returns `gr.update()` — a no-op —
for everything that has not moved since the browser last drew it, so a tab
whose job finished ten minutes ago is not rewritten once a second. And the
timer **switches itself off** when nothing is queued or running: an idle
pod is not polled at all. A Generate click switches it back on, and so
does opening the page, which is what lets a browser opened mid-job find
the job already running.

A tab shows the newest job *that has begun* — so it switches to a new run
when that run starts, not when it was queued, and goes on showing the last
finished run while three more wait behind it.

### What a queued job is frozen against

The arguments are read out of the controls at click time and copied into
the job, so moving a slider afterwards cannot reach work already in the
line. That extends to the two per-run tickboxes: **publish** and **save as
preset** are read on the click, then disarmed immediately, because they
are per-run decisions rather than modes.

The preset save itself now happens wherever the job runs, which is after
the click that asked for it has returned. So `presets.save` tells the
queue (`jobqueue.note_preset_saved`), and the same poll that carries the
images back refills the preset dropdown — every dropdown showing that
tab's list, which since the Edit tabs joined in is two of them.

## Recipes — "how was this made?"

An image in the Gallery tab used to be a dead end. It was the one that
worked, and everything behind it — the prompt, the model, the LoRA stack,
and above all the **seed** that a ticked *🎲 Random seed* threw away — had
left the UI the moment the next click overwrote the controls.

So every finished prompt now writes a **recipe** beside its output. Click
a tile in the Gallery and a *🧾 How this was made* panel opens under it,
showing the prompt as a quote and the settings as a table, with the seed
the picture actually ran on in the heading. **▶️ Load these settings**
puts the whole lot back into the tab it came from and switches to that
tab.

Two values are deliberately not restored as recorded: **Seed** becomes the
seed that particular picture ran on rather than whatever was in the box,
and **🎲 Random seed** is switched off. Together they are the difference
between "the same settings" and "the same image", which is what someone
clicking that button is asking for.

### What is in one

A recipe is the *UI's* values, not the resolved ComfyUI graph — the same
call `prompts.py` and `presets.py` make. The dropdown label is what goes
back into a dropdown; the filename it resolved to on this pod is not, and
would be wrong on the next one.

A tab's recipe **is its Generate click's `inputs` list** — that is the
trick that makes this one implementation rather than ten. Recording it is
zipping that list against the values Gradio just handed over; restoring it
is writing them back into the very same components. A tab that grows a
control gets it in its recipes with no change anywhere:

```python
    generate_btn.click(
        fn=_enqueue(features.Key.KREA_T2I, generate_single, prompt_arg=0),
        inputs=_recipe_view(features.Key.KREA_T2I, "krea2", [
            prompt_box, negative_box, seed_box, randomize_cb, ...]),
        outputs=[_queue_timer, status_box],
    )
```

`_recipe_view` registers the list and hands it straight back, so what is
recorded cannot drift from what is submitted.

Three things are deliberately left out:

- **Uploaded files** — a source image, an inpaint mask, a JSON batch file.
  Storing those would turn a few hundred bytes a picture into a second
  copy of the input; the panel says how many a recipe needed so you know
  to pick them again.
- **The publish and save-preset boxes**, via `_recipe_skip`. They are
  per-run decisions rather than settings (see `_reset_after_generate`), so
  putting them in a recipe would mean loading one silently re-arms a
  publish.
- **Empty LoRA slots**, from the *panel* only. They are recorded — a slot
  has to restore whole — but a dropdown reading "None" beside a weight of
  0.8 is a row of noise, so `_recipe_gate` pairs each weight with the
  dropdown that decides whether it means anything.

### Where it is kept

One append-only JSONL file next to the images: `<output>/.recipes.jsonl`.
No database, no licence server, nothing to configure — and it survives a
restart because it is a file.

- **Append-only**, because the alternative is rewriting the whole map
  after every picture. Later lines win, so an update is just another line,
  and the file is compacted at most once per process (when it holds twice
  as many lines as live recipes).
- **Beside the images**, so a recipe lives and dies on the same disk as
  the file it describes. The leading dot means `gallery_index` already
  skips it, which keeps it out of the gallery grid and out of the Zip
  button's archive for free.
- **Keyed by the path relative to the output dir**, so moving the tree, or
  mounting it somewhere else on the next pod, does not orphan everything
  in it.
- **Bounded** at `MAX_RECIPES` (5000), oldest dropped on compaction.

### How the seed is captured

`_run_jobs` and `_run_wan_jobs` call `recipes.stamp(seed=...)` before each
prompt, because the executor is the only place the real seed is known — a
batch of four walks four consecutive seeds, and a random tick ignores the
box entirely. Since `client.on_output` fires once per ComfyUI prompt, a
batch of four writes four recipes differing in exactly the field that
matters.

The recipe itself is announced from the *worker* thread (`_recording` in
`ui.py` wraps the tab's handler), and `recipes.py` keys it by thread. That
is what lets the output hook deep inside `client.run` find it without
every executor having to pass it down.

### Reading one back safely

A recipe is a file on a pod's disk that outlives the build that wrote it,
so `_use_recipe` treats every stored row as data of unknown shape —
exactly the guarding the prompt library's `_pick` and `_num` already do:

- a value is only written when the **label still matches** the control at
  that position, so a build that reordered its controls loses that one
  field rather than scrambling the tab
- a dropdown handed a choice this pod does not have is **left alone**, not
  broken
- a number outside this build's slider range is **clamped into it**
- a recipe for a tab this licence does not grant can be **read but not
  loaded**, and the panel says so

### Deleting a file

The viewer under the grid carries a **🗑️ Delete**, which removes the
selected file for good — the pod's disk is ephemeral and there is no trash
to fish anything back out of, so it is two clicks: 🗑️ arms it, and a
confirm button in a row that only exists while it is armed does it.
Selecting a different picture disarms, because arming is a property of the
selection rather than of the tab.

Three things go, in that order:

1. **The file**, via `gallery_index.delete()`. It will only touch a path
   that resolves to a media file *inside* the output dir — the same two
   rules the index uses to decide what it lists. A gallery click resolves
   against a `gr.State`, i.e. against client-supplied data, so a stale or
   forged path must not be able to reach `.recipes.jsonl`, the zip, or
   anything outside the tree at all.
2. **Its thumbnail**, best-effort. Nothing lists `.thumbs`, so a leftover
   is invisible rather than wrong, and it is not worth failing a delete
   that has already happened and cannot be undone.
3. **Its recipe**, via `recipes.forget()`, which compacts the store on the
   spot. The JSONL is append-only and later lines win, so rewriting the
   file without the key is the only way to make it stop existing —
   otherwise it would come back on the next restart describing a picture
   that is gone. A rewrite is O(everything), which is affordable for a
   deliberate one-at-a-time gesture behind a confirm step.

The grid is then rebuilt from the state list minus that one path rather
than by re-scanning, for the same reason **Load more** pages out of state:
a rescan jumps back to page one, and someone who has paged four screens
down to tidy up would lose their place on every delete. A file that was
already gone from disk counts as a success — it is not there, which is
what was asked for. A path the index refuses, or one the OS will not let
go of, leaves the list alone and says so.

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
- **One job at a time.** Every generation runs on a *lane* in
  `jobqueue.py`, and a lane is one worker thread — so a second tab's
  Generate queues rather than running alongside. They all feed one
  single-threaded ComfyUI prompt worker anyway, so nothing real is lost —
  but without it a second handler runs far enough to call `/free` while
  the first job still holds the models, stalling on the VRAM-settle wait
  and corrupting the what-is-loaded bookkeeping. Video keeps its own lane
  when `KREA2_WAN_PARALLEL` gives it a separate ComfyUI instance. (This
  used to be Gradio's own `concurrency_id="comfy"` group, which enforced
  the same rule invisibly — see **The job queue** below for why it moved.)
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
- `plans.py` — the public plan catalogue behind the `/pricing` page, read from the license server (stdlib only, read-only)
- `license-validator/` — the Node/Express + MongoDB license server
- `config.py` — paths, Krea 2 model registry, LoRA lists, Wan 2.2 settings, tokens, presets
- `bootstrap.py` — clone ComfyUI + install requirements
- `downloads.py` — HF / CivitAI model + LoRA downloads (resume + retries)
- `comfy.py` — GPU detection + ComfyUI server start/wait (1–2 instances)
- `workflow.py` — Krea 2 workflow builders, text-to-image + inpainting + instruction edit (ComfyUI API format)
- `workflow_krea2_v2.py` — Krea 2 V2 builder (the Krea2 advanced turbo/raw graph)
- `workflow_krea2_v2_edit.py` — Krea 2 V2 Edit builder (that graph's instruction-edit variant)
- `workflow_klein.py` — Flux 2 Klein 9B edit builder (the Klein advanced Klein Edit graph)
- `workflow_wan.py` — Wan 2.2 image-to-video workflow builder (two-expert A14B)
- `workflow_reactor.py` — ReActor face-swap workflow builder + availability checks
- `client.py` — ComfyUI HTTP/websocket client (queue, progress, image upload, interrupt)
- `jobqueue.py` — the visible job queue behind every Generate button (worker per lane, cancel, history)
- `recipes.py` — what each generated file was made with, so the Gallery can load it back
- `ui.py` — Gradio UI (single/batch, edit, V2 edit, inpaint, face swap, flux, klein edit, video, JSON batch, gallery tabs), the `/pricing` page, and launch logic

### Tab order

Each tab's body is a `_tab_*` builder in `ui.py`, and **`TAB_ORDER` is the one
thing that decides the order they appear in** — a tuple of
`(feature key, builder, tab id)` that a loop inside the `gr.Blocks` walks:

```python
TAB_ORDER = (
    (features.Key.KREA_T2I,          _tab_krea_t2i,          "krea2"),
    (features.Key.KREA_V2_T2I,       _tab_krea_v2_t2i,       "krea2v2"),
    (features.Key.COMMUNITY_PROMPTS, _tab_community_prompts, "prompts"),
    ...
)
```

Move an entry and the tab moves; nothing else changes. A feature that is off is
skipped entirely, so the remaining tabs close up with no gap. The order is fixed
for the build — the Blocks tree is constructed once at import, so it cannot vary
per licence.

`tab_id` is only needed by a tab something else selects programmatically. Gradio
otherwise numbers tabs by construction order, which shifts with the licence, so
the Prompt Library's "switch to that tab" would land on whichever tab happened to
be third for that customer.

Note the ordering of `sort_order` in the licence server's `features` collection
is a *different* thing: it orders the pricing page and the `features` array in an
acquire response, and never reaches the tab strip. Likewise the `FEATURES` tuple
in `features.py`, which drives `summary()`, `assets()` and `enabled_keys()` only.

One rule for the builders: cross-tab event wiring belongs *after* the loop, not
inside a body. The Prompt Library's Use buttons write into the Krea 2 and Krea 2
V2 controls, which belong to two other builders — Gradio only needs a component
to exist before the `.click()` naming it, not before the tab it lives in, so
lifting that one wiring block out is what keeps `TAB_ORDER` freely reorderable.
Builders return whatever the rest of the page needs from them (the two generation
tabs return their control lists, the library returns its state and buttons);
everything else returns `None`.
- `theme.py` — the UI's look: Gradio theme tokens, CSS, application header, pricing markup, page JS (see below)
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
| Sticky application header | brand, the plan this license is on, when it expires (amber inside the last week), and the **Plans & pricing** button. Nothing else: the engine chips, counts and output path that used to be here were all true and none of them was read |
| Footer | the `Ctrl`+`Enter` hint, model/GPU counts, and the output path (click it to copy) |
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

This whole section is about the **Linux** artifact. The Windows `.exe` is the
same idea run on the other side of the same wall — `.\build.ps1` on a Windows
machine, because that inability to cross-compile cuts both ways. See
[Windows binary](#windows-binary-buildps1).

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

### Publishing (`make`)

`build.sh` uploads the binary to a **private** Cloudflare R2 bucket and then
registers it with the licence API, which is what points a channel at it. The
`Makefile` at the repo root wraps both halves and reads every credential from
`license-validator/.env`, so there is one file to fill in rather than six
variables to retype after every reboot.

| Command | Runs | Needs |
| --- | --- | --- |
| `make` | lists these | — |
| `make compile` | `build.sh --no-publish` — compiles `dist/krea2app`, uploads nothing | nothing |
| `make publish` | `build.sh --upload-only` — uploads the binary already in `dist/` and points `stable` at it | write token + admin |
| `make release` | `build.sh -y` — compile **and** publish in one step | write token + admin |
| `make check` | credentials, artifact, and whether the admin token actually opens the deployment | admin |
| `make health` | the deployment's `/health` — `db`, `r2`, `stable_build` | admin |
| `make builds` | every build ever published, newest first | admin |
| `make promote SHA=<sha256>` | point a channel at a build | admin |

Two variables tune a publish:

```bash
KREA2_BUILD_CHANNEL=beta make publish    # upload without customers getting it
make promote SHA=<older sha> CHANNEL=beta
```

**Rollback and roll-forward are the same call.** Builds are content-addressed
at `builds/<sha256>/krea2app`, so publishing never overwrites and every build
stays in the bucket. A channel is just a name sitting on one build document —
`make builds` lists them, `make promote` moves the name. Nothing is
re-uploaded and pods take it on their next start. To hold a single customer
on a specific build, set `build_sha` on their licence document instead; it
wins over the channel.

**Credentials.** `license-validator/.env` holds two R2 tokens under different
names, and the split is load-bearing: the licence service is public-facing and
gets **Object Read only** (`R2_ACCESS_KEY_ID`), while publishing gets **Object
Read & Write** (`R2_WRITE_ACCESS_KEY_ID`). A leak of the deployed credential
therefore cannot replace the binary customers download. `make check` refuses
to publish if the two are identical, because R2 does not reject a bad-signature
PUT until the bytes have arrived — a few hundred megabytes to reach a 403.

`make check` is the cheap pre-flight; run it before spending an upload:

```
  account     8487b896…
  bucket      krea2-builds
  api         https://<node-tag>.vercel.app
  write key   038e9e…  (differs from read key: ok)
  artifact    dist/krea2app  (100600024 bytes)
  admin api   ok
```

`admin api 404` means `ADMIN_TOKEN` is unset on the deployment *or* the
deployment predates these routes — the two return an identical body, and both
are fixed by redeploying with the environment variables set. `/health` gaining
`"r2":"configured"` is the confirmation the new code landed. See
[`license-validator/README.md`](license-validator/README.md) for the service
side: how `/v1/build` gates a download on the licence, and why that stops a
lapsed key fetching a *new* build without pretending to stop a binary someone
already has from being copied.

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
since the executable bit does not survive most transfers. For a Windows
customer you do not transcode this file, you build the other one — see below.

## Windows binary (`build.ps1`)

The same app compiled to `dist\krea2app.exe`, so a customer with an NVIDIA
card and no cloud account runs one script and gets the RunPod experience on
their own machine. Everything about licences, seats, channels and rollback
is unchanged: it is the same licence server, the same private bucket, the
same `stable` channel.

**It has to be built on Windows.** Nuitka emits native code for the OS it
runs on, so `build.sh` and `build.ps1` are two scripts producing two
artifacts, and there is no cross-compiling either way.

```powershell
.\build.ps1                  # compile only. Publishes nothing.
.\build.ps1 -Publish         # ... then ask before publishing
.\build.ps1 -Publish -Yes    # ... publish without asking
.\build.ps1 -UploadOnly      # publish what is already in dist\
```

Publishing is **off by default**, unlike `build.sh`, where the default is
to ask. This one runs on a desktop rather than on a pod that exists only to
build, so the common case is "does it still compile" on a tree with
uncommitted work in it, and that must not end at a prompt whose yes reaches
customers.

It needs the same credentials `build.sh` does (`R2_*`, `KREA2_NODE_TAG`,
`KREA2_ADMIN_TOKEN`) and reads them from the environment. On Windows there
is no `make`, so set them in the shell:

```powershell
$env:R2_ACCOUNT_ID = "..."; $env:R2_ACCESS_KEY_ID = "..."   # etc.
```

### What the build machine needs

`build.ps1` installs `nuitka`, `zstandard` and the app's own requirements
itself. What it cannot install is a C compiler, and the rule there is not
the one you would guess:

| Build interpreter | Compiler |
| --- | --- |
| **Python 3.12 or older** | MSVC if present, otherwise Nuitka downloads MinGW64 — nothing to install |
| **Python 3.13 or newer** | **MSVC build tools required.** Nuitka refuses MinGW64 above 3.12 |

So on a machine with 3.13+ and no Visual Studio there is no build, and
Nuitka's own message for it (`FATAL: Error, cannot use '--mingw64' on
Python version 3.13 or higher`) arrives *after* the pip installs and the
dependency checks have all passed. `build.ps1` therefore checks the pair up
front and refuses immediately, naming both ways out.

**Prefer a Python 3.12 build environment.** It needs no Visual Studio, and
3.12 is what the pod builds on and what the app is tested against — so the
two artifacts differ in as few ways as possible.

The `krea2` conda environment from
[Running locally on Windows](#running-locally-on-windows) is already that:
Python 3.12 with the app's dependencies installed, which is exactly what
Nuitka needs to compile them in. Point the build at it:

```powershell
$env:PYTHON = "$env:USERPROFILE\miniconda3\envs\krea2\python.exe"
.\build.ps1
```

Or from scratch, without conda:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHON = "$PWD\.venv312\Scripts\python.exe"
.\build.ps1
```

`$env:PYTHON` is how you point the script at an interpreter other than
whatever `python` resolves to, exactly as `PYTHON=` does for `build.sh`.
Note that it is the **build** interpreter only — it decides what gets
compiled in, and has nothing to do with the Python 3.12 the customer's
machine needs for ComfyUI.

`build.ps1` installs `nuitka` and `zstandard` into whichever environment
you point it at, the same way `build.sh` does on a pod.

### What the machine running it still needs

The `.exe` is **not** self-contained, and that is the same design as on
Linux rather than a Windows shortcoming: the app never imports torch or
ComfyUI, it installs them and runs ComfyUI as a **separate process**. So
the target machine needs

- **Python 3.12** on `PATH` — from python.org, with "Add python.exe to
  PATH" ticked. It is what ComfyUI runs on. The Microsoft Store build
  causes enough path trouble to be worth avoiding.
- **git** — ComfyUI is cloned, not vendored.
- **an NVIDIA GPU** with a current driver. `ensure_torch()` installs the
  cu128 wheels, which is what Blackwell cards (RTX 50xx, `sm_120`) need.
- **Developer Mode**, or an elevated shell. `link_model_dirs()` symlinks
  ComfyUI's model folders; without the privilege ComfyUI silently sees no
  models and every generation fails validation with a message that never
  mentions symlinks.
- **~45 GB free**, more with Flux or Wan.

`scripts/windows_start.ps1` checks all of these and says which is missing
before downloading anything.

**SmartScreen and antivirus will complain.** The binary is unsigned, so the
first run gets "Windows protected your PC" → *More info* → *Run anyway*.
Code signing is not attempted here — it needs a certificate and is a
separate decision. Defender also rescans the onefile extraction on every
launch, so excluding the base directory is worth suggesting to anyone who
finds startup slow.

### What a customer runs

The exact counterpart of the RunPod template's container start command —

```bash
bash -c 'curl -fsSL https://$KREA2_NODE_TAG.vercel.app/v1/start.sh -o /tmp/krea2-start.sh && exec bash /tmp/krea2-start.sh'
```

— fetch the current start script, then run it. Windows has no template
field to paste it into, so it goes in a file the customer keeps. Save this
as **`krea2.cmd`** on their desktop; double-clicking it starts the app:

```bat
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Join-Path $env:TEMP 'krea2-start.ps1'; $u = 'https://' + $env:KREA2_NODE_TAG + '.vercel.app/v1/start.ps1'; curl.exe -fsSL $u -o $s; if ($LASTEXITCODE -ne 0) { Write-Host 'Could not fetch the start script. Check KREA2_NODE_TAG and your internet connection.'; exit 1 }; & $s; exit $LASTEXITCODE"
```

Or, from a PowerShell window they already have open:

```powershell
$s = "$env:TEMP\krea2-start.ps1"
curl.exe -fsSL https://$env:KREA2_NODE_TAG.vercel.app/v1/start.ps1 -o $s
powershell -ExecutionPolicy Bypass -File $s
```

Both need `KREA2_LICENSE_KEY` and `KREA2_NODE_TAG` set as **user**
environment variables (not just for the session), which is the equivalent
of filling them into a pod's environment panel:

```powershell
[Environment]::SetEnvironmentVariable("KREA2_LICENSE_KEY", "<key>", "User")
[Environment]::SetEnvironmentVariable("KREA2_NODE_TAG", "<tag>", "User")
```

Three details that differ from the bash one-liner, all forced by Windows:

- **`-ExecutionPolicy Bypass`** replaces nothing in the bash version — it
  is simply required, because the default policy refuses to run a
  downloaded `.ps1` at all.
- **`exit $LASTEXITCODE`** replaces `exec`. There is no exec, so the script
  runs as a child and its exit code has to be passed back by hand;
  `-Command` otherwise returns its own status and the app's is lost.
  (The `-File` form above does this on its own.)
- **`curl.exe`, not `curl`** — in PowerShell, bare `curl` is an alias for
  `Invoke-WebRequest`, which takes none of these flags.

A copy of a start script a customer holds is a copy no fix ever reaches,
which is why the file above holds only the bootstrapper. Everything that
might need changing lives in `start.ps1`, on the server.

The script mirrors `runpod_start.sh` step for step — same variable checks,
same node-tag validation, sends the sha256 of the binary it already has so
a restart downloads nothing, falls back to the on-disk build when the
server is unreachable, verifies the download before running it. Two things
differ, both because Windows differs:

- Models default to **`C:\krea2`**, not `/workspace/krea2`. The pod default
  resolves to `C:\workspace\krea2` on Windows, which is a real path and the
  wrong one. Override with `KREA2_BASE_DIR`.
- There is no `exec`, so the app runs as a child process. Ctrl-C reaches it
  and releases the seat cleanly; closing the window does not, and that seat
  is freed by the server's stale-lease sweep a few minutes later.

### How a Windows build stays away from Linux pods

This is the part worth understanding before publishing one, because the
failure mode is silent on the publishing side and total on the receiving
side: a Linux pod handed a `.exe` downloads it, matches the checksum, and
dies with `Exec format error`.

A build document carries a **`platform`**, and `/v1/build` resolves a build
by *(channel, platform)* rather than by channel alone:

```
POST /v1/build {license_key, instance_id, current_sha}              -> linux
POST /v1/build {license_key, instance_id, current_sha, platform}    -> as asked
```

**A client that sends no `platform` gets Linux.** That is not a default
chosen for tidiness — it is the compatibility guarantee. Every pod running
today sends exactly the first body, and `scripts/runpod_start.sh` is
unchanged, so they all keep resolving the build they already had.

Three things had to change together, and all three are in
`license-validator/`:

1. `/v1/build` filters by platform, on **both** the channel lookup and the
   `build_sha` pin. A pin names one artifact and an artifact is for one OS,
   so a customer pinned to a Linux sha and running Windows gets a clean
   "no build" rather than the wrong one.
2. `promote()` scopes its `$pull` by platform. Without that, promoting a
   Windows build to `stable` would take `stable` off the **Linux** build
   and every Linux pod would get `no_build` on its next start — an outage
   caused at publish time, before any pod asked for anything.
3. `buildKey()` takes the filename from the build document, so a Windows
   artifact is stored at `builds/<sha>/krea2app.exe`. Content addressing
   already keeps the two apart; this is so a bucket listing is readable.

Builds published before any of this exists have no `platform` field and are
treated as Linux, so nothing needed migrating.

**Deploy order matters.** The licence server change must be live *before*
the first Windows build is published and before the start script reaches a
customer. An old server ignores the `platform` field it does not know about
and answers with the Linux build; `windows_start.ps1` refuses to run
anything that is not marked `windows` — including a response with no
platform at all — so the failure is a clear message rather than a mystery,
but it is still a failure.

### Keeping the two build scripts in step

`build.ps1` carries its own copy of the long `--include-*` argument list.
That duplication is deliberate — `build.sh` produces what every customer
runs today and was left byte-for-byte alone — and the price of it is drift:

```bash
make check-args          # or: python scripts/check_build_args.py
```

`make compile` and `make release` both depend on it, so the Linux build
refuses to run while the two disagree. It is worth having because every
flag in that list is one whose absence produces a binary that **compiles
and runs** and is then quietly wrong — no Xet acceleration, un-pinned
weights, a missing `version.txt` that only crashes after the models have
downloaded.

Flags that genuinely belong to one platform (`--jobs`, `--mingw64`,
`--static-libpython`, the output filename) are listed in `PLATFORM_SPECIFIC`
in that script, with the reason.

### Publishing a start-script fix without a Windows machine

`start.ps1` and the `.exe` are two independent objects in the bucket. A fix
to the start script is one upload:

```bash
make start-ps1
```

It refuses to publish a `.ps1` that has lost its UTF-8 BOM, because
`powershell.exe` decodes a BOM-less script as Windows-1252 and a single
em-dash in a comment then ends a string early — the file fails to *parse*,
before any of its own error handling can say why.

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
identity/unchanged regions. Style LoRAs can be stacked on top just like in
the other tabs. Outputs are capped at ~2 MP (the LoRA duplicates content
above that).

Two sliders steer it. **Grounding** trades edit strength (lower) against
likeness fidelity (higher); its range is the LoRA's trained 384–768, and
running above that is what makes the model emit duplicated "double
picture" compositions. **Reference fidelity** (`ref_boost`) is how hard the
edit holds the source: 1.0 is neutral, ~4 gives strong face/body likeness,
and past ~10 removals stop working — so removals want a lower value along
with the ~20 steps / CFG ≈ 3 recipe.

The LoRA version and the node-pack version are one unit. `EDIT_LORA_FILE`
in `config.py` is v1.2, which needs the v1.2 nodes for the FIT reference
geometry (a source whose aspect ratio differs from the output is fitted,
not stretched) and for `ref_boost`; conversely the v1.2 nodes default
`fit_mode` to `fit`, which v1/v1.1 weights were not trained for. The node
pack is held still by `scripts/PINS.json` — bump both together or neither.

## Krea 2 V2 Edit

The **🔷 Krea2 V2 Edit** tab is that same instruction-edit recipe grafted onto
the Krea 2 V2 pipeline, and it stands to the Edit tab exactly as Krea 2 V2
stands to Single. Feature key `krea_v2_edit`; builder
`workflow_krea2_v2_edit.py`.

Everything V2 about it is **imported from `workflow_krea2_v2.py` rather than
restated**, so the two tabs cannot drift: the same `V2_MODELS` registry and
turbo/raw defaults, the same Wan 2.1 VAE, the same 11-slot model+CLIP LoRA
stack (with the Turbo LoRA still on slot 1, still toggled by the Model
dropdown), the same `ClownsharKSampler_Beta` settings and the same
`RBG_Smart_Seed_Variance` node. The edit half — `Krea2EditModelPatch`,
`Krea2EditGroundedEncode`, the Identity Edit LoRA, **Grounding** and
**Reference fidelity** — behaves as described for the Edit tab above.

Swapping the VAE is safe *here specifically* because the two are the same
family: Qwen-Image's VAE is a Wan 2.1 derivative over the same 16-channel
latent space, so the source latents `Krea2EditModelPatch` prepends as
in-context tokens still mean what the Identity Edit LoRA was trained to read.
A VAE from any other family would not be substitutable this way.

Three things are deliberately **not** carried over from the V2 tab:

| | V2 | V2 Edit |
| --- | --- | --- |
| resolution | aspect + megapixels (`resolve_size`) | from the source image, aspect kept, capped at 2 MP (`fit_size`) |
| denoise | a slider (default 1.0) | pinned at 1.0, not exposed — the source arrives through conditioning, not the starting latent |
| sharpen / film grain | toggles, off, reproducing the bypassed source nodes | absent; there is no source graph to reproduce |

The LoRA chain mixes two node types on purpose. The Identity Edit LoRA goes on
first as `LoraLoaderModelOnly` at strength 1.0 **as trained** — the grounded
encoder reads the image through the CLIP, so patching the CLIP with an edit
LoRA is not part of that recipe — and the V2 stack then applies over it as
`LoraLoader` (model + CLIP), which is what Power Lora Loader's "Single
Strength" mode does. Each node passes through whatever input it does not
touch, so the mixed chain is well-formed. As in the Edit tab, the edit LoRA is
added **by the builder** and is not one of the visible slots, which is what
makes applying it twice impossible.

An empty **Negatives** box becomes `ConditioningZeroOut`, saving an encoder
pass; the V2 tab always encodes its negative because its source graph does.
The box is prefilled with `V2_DEFAULT_NEGATIVE` either way, and CFG 1.0 —
the turbo default — ignores it regardless.

This tab needs **both** sets of node packs: the three V2 ones above plus
`comfyui-krea2edit`. `bootstrap.install_custom_nodes` and
`bootstrap.install_v2_nodes` each run when *either* of the features that
wants them is on, and `app.py` verifies all four classes registered.

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

## Prompt undo / redo

Every prompt box carries a pair of small **↶ / ↷** buttons under it, plus
Ctrl+Z and Ctrl+Y (or Ctrl+Shift+Z) while the box has focus.

They exist for phones. A desktop browser gives a textarea its own undo
stack and Ctrl+Z reaches it; a phone keyboard has no Ctrl, and no mobile
browser exposes undo for a text field any other way — so a prompt trimmed
on a phone was simply not recoverable.

The history lives in the page JS (`theme.JS`) rather than leaning on the
browser's, for three reasons:

- it is the only way a *button* can drive it
- it survives a value written by Gradio, so loading a **preset**, a
  **recipe** or a **library card** over a prompt is undoable — the native
  stack knows nothing about those, because assigning `.value` from script
  never enters it
- the keys are routed through the same stack, so the buttons and Ctrl+Z
  cannot drift apart the way two separate histories would

A burst of typing is one entry, the way an editor does it: a pause of
450 ms, or finishing a word, closes the current entry and opens the next
— stepping back one character at a time would be worse than no undo at
all. Up to 100 entries are kept per box, and each box has its own history.

Attached to every editable `<textarea>` on the page, which is exactly the
prompt and negative-prompt boxes plus the JSON batch box; status lines are
`interactive=False`, so they render disabled and are skipped. New boxes are
picked up by a `MutationObserver` (debounced to one scan a frame, because
the queue's poll touches the DOM every second), so a tab built later or the
pricing panel swapping sections in and out costs nothing.

Two details that are easy to get wrong and are load-bearing here:

- stepping the history assigns `textarea.value` and then **dispatches an
  `input` event**. Gradio's binding listens for that, and a scripted
  assignment fires nothing on its own — without it the box would show the
  old text while Generate still sent the new one
- the buttons call `preventDefault()` on `pointerdown`, so pressing one
  does not take focus. On a phone that is what keeps the keyboard open and
  the caret in place between taps

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
