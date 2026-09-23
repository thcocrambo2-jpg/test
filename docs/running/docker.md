# Running in Docker

For anyone with an NVIDIA GPU and Docker who wants the environment fixed
rather than installed on first boot, and for a developer who wants to run
the working tree in exactly the environment a customer gets. This path is
written but has never been built or run in production — treat it as
unverified.

## What the image carries

Python 3.12 and the pinned torch stack on a slim `nvidia/cuda` base, the
ComfyUI checkout at its pinned SHA, the custom node packs, ComfyUI's Python
requirements, SageAttention — so a machine spends none of its first boot on
them.

It does **not** carry the app. `scripts/runpod_start.sh` is baked in
unmodified and still fetches the binary from the licence server, which
means publishing a build reaches these containers exactly as it reaches
every other pod, `make promote` can still roll them back, and the image
holds no secret and no licensed code. An app release needs no image
rebuild.

```
/entrypoint.sh  ->  /opt/ember/bin/start.sh  ->  the binary
 (symlinks the       (fetches + verifies       (the app finds
  baked ComfyUI)      the build)                everything present)
```

Models are never baked: ~90 GB, licence-gated, and they belong on the
volume.

## Running it on your own machine

PowerShell:

```powershell
copy .env.example .env      # then fill in the two values
docker compose up
```

bash:

```bash
cp .env.example .env
docker compose up
```

Nothing is set in the shell and no flags are typed — compose reads `.env`
from the project directory on its own, and `docker-compose.yml` carries the
GPU reservation, the shared-memory size, the port and the volume. The UI
link is printed in the output, and `http://localhost:7860` also works.

`docker compose down` stops the container and **keeps** the models, so the
next `up` finds them already there and downloads nothing.
**`docker compose down -v` deletes the volume** and the ~90 GB with it.

On Windows this needs Docker Desktop on the **WSL2 backend** plus a current
NVIDIA driver; no CUDA toolkit is installed on Windows. The Hyper-V backend
cannot pass a GPU through at all, and it shows up as
`torch.cuda.is_available()` being False in the log rather than as an error.

Storage is the named volume `krea2-data`, never a bind mount to a host
folder — the reasoning is in the comment in `docker-compose.yml`. It lives
in the WSL2 virtual disk on `C:`; Docker Desktop → Settings → Resources →
Advanced → *Disk image location* moves it to a drive with room for 90 GB.

Which variables belong in `.env` is in
[Configuration](../configuration.md).

## Reusing models you already downloaded

Every download guards on the file simply existing under
`settings.MODELS_DIR`, so weights copied in by hand count as downloaded.
Nothing on the host can write into a named volume directly, so the copy
runs in a throwaway container that can see both (PowerShell):

```powershell
docker run --rm `
  -v krea2-data:/workspace `
  -v C:\path\to\your\tmp\models:/seed:ro `
  alpine sh -c "mkdir -p /workspace/ember/models && cp -an /seed/. /workspace/ember/models/"
```

Run it before the first `docker compose up`, or the app will already be
downloading the same files. The source is mounted read-only, so this cannot
touch the originals.

`cp -an` is no-clobber, which makes the command re-runnable: after
downloading more models locally, run it again and only the new files copy.
The one thing it will not do is replace a file that is already in the
volume — to refresh a corrupt one, delete it there first.

Seeding is optional for anything the catalogue lists (see
[The catalogue](../pipelines/catalogue.md)); the app downloads what is
missing on its own, and copying only saves the bandwidth. A file the
catalogue does not list is never offered in a dropdown, so copying one in
by hand does nothing on its own — add a record for it to the database.

Copy `models/` only. The image has its own ComfyUI at the pinned SHA, and
the entrypoint leaves a real `$EMBER_BASE_DIR/ComfyUI` directory alone —
seeding one there would shadow the baked copy for no benefit.

## On RunPod

Use the image as the template's container image and leave the start command
empty. Expose HTTP port `7860`, mount the network volume at `/workspace`,
and leave `EMBER_LICENSE_KEY` and `EMBER_NODE_TAG` **empty** in the
template — a template is public, so a key typed into one is a key given to
everyone (see [RunPod](runpod.md#how-a-customer-launches)). The customer
fills them in on their own pod.

Pods on the existing template are unaffected and keep fetching `start.sh`
from `/v1/start.sh` as before.

To get the image onto RunPod, push it to a registry RunPod can pull from,
use that tag as the container image, and set the template's **CUDA version
filter to 13.0** (bash):

```bash
make image
make image-push REGISTRY=docker.io/<user>
```

`make` is WSL/POD only — the Makefile declares `SHELL := /bin/bash`.

## Torch and CUDA

The image runs exactly what MiniMax template v8
(`hearmeman/comfyui-minimax-template:v8`) runs: **torch 2.11.0, torchvision
0.26.0 and torchaudio 2.11.0 on CUDA 13.0, with SageAttention 2.2.0.** One
configuration, not a menu. `docker/bake_torch.py` holds those pins, takes
the SageAttention from `setup.sage_requirement()` so the image and the app
agree, and records both in `baked.json`. The build installs that torch over
the `nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04` base — chosen over
`runpod/pytorch`, which carries a torch and a CUDA toolchain this image
would only replace, the difference between a ~40 GB image and this one —
asserts the result, and installs the hash-pinned SageAttention wheel. Every
boot logs it:

```
[ember-image] torch 2.11.0+cu130 (CUDA 13.0) - SageAttention 2.2.0
```

CUDA 13 needs an **R580 or newer driver** on the host, which is why the
RunPod template has to filter on CUDA 13.0, as template v8's does.

The plain RunPod template (`runpod/pytorch:1.0.2-cu1281-torch280` plus the
start command) does not use this image and is unchanged: nothing at boot
changes torch — `setup.ensure_torch()` keeps whatever working torch it
finds — so that flow keeps torch 2.8.0 and gets SageAttention 1.0.6. See
[SageAttention](windows.md#sageattention).

## Running your working tree in the image

`docker compose up` runs the binary the licence server hands out — whatever
`make release` last shipped, not the code in this directory. To run the
working tree instead, in the same CUDA/ComfyUI environment a customer gets
(bash):

```bash
make image                                             # ember:latest
docker compose -f docker-compose.dev.yml up --build    # ember:dev, then run
```

Edit, Ctrl-C, `up` again. The repo is bind-mounted at `/src` and
`EMBER_DEV_SOURCE=/src` is what makes the entrypoint skip the download and
`exec python3 app.py`.

It shares the `krea2-data` volume with the production service, so a dev run
does not download another 90 GB. If you have not run the production service
yet, `docker volume create krea2-data` first — the dev file declares the
volume `external` so it cannot silently create a second, empty one.

Two things are deliberately *not* different in dev mode. The licence seat is
still taken, because that is the app's behaviour and a dev mode that skipped
it would be testing something no customer runs. And the environment is the
production image, so a dependency that is missing there is missing here.

`docker/Dockerfile.dev` only adds `requirements.txt` — fastapi and the
rest, which the production image has no use for because `build.sh` compiles
them into the binary. It is an optimisation rather than a requirement: run
from source, `settings.FROZEN` is False, so `setup.install_comfyui()`
installs them itself. Without the dev image that pip pass repeats on every
run, because the container it writes into is deleted on `down`.

Do not run both compose files at once — they would contend for port 7860
and the GPU.

## Building and publishing the image

bash:

```bash
make image                                # -> ember:latest
make image-push REGISTRY=ghcr.io/<owner>  # -> ghcr.io/<owner>/ember:latest
```

Neither needs credentials from `license-validator/.env`; nothing in the
image is secret. `.dockerignore` is an allowlist rather than a list of
exclusions, so a file that is not named in it cannot reach the build
context at all — that is what keeps `license-validator/.env`, the root
`.env` and `dist/ember` out of a published image.

The bake stage is separate from the published image for the same reason. It
copies individual named files — the thirteen `ember/` modules that
`docker/bake_nodes.py` and `docker/bake_torch.py` actually import, plus
`scripts/PINS.json` and `scripts/mirror_manifest.json` — never a directory,
and only `/opt/ember` is copied out of it. Adding an import to a bake
script means adding its module to both the `COPY` lines and the
`.dockerignore` allowlist, or the build fails loudly.

What goes into the image comes from `scripts/PINS.json`:
`docker/bake_nodes.py` runs `ember/comfy/setup.py`'s own clone and
mirror-tarball helpers at build time, so bumping a pin is the only edit
needed to move the image. Every container prints what it was built from on
boot. See [Mirror and pins](../releasing/mirror-and-pins.md).

A rebuild only rebuilds, and a push only uploads, what actually changed.
Torch and SageAttention (~5 GB, most of it torch's `nvidia-*` CUDA wheels)
are installed before the ComfyUI tree is copied in, so only a change to
`docker/bake_torch.py`'s pins touches them; a pin bump rebuilds the
ComfyUI and node-pack layers above them (~2 GB); an app change that only
touches the bake stage's `ember/` files reruns the bakes, gets
byte-identical output and leaves every layer cached. That last case
depends on the bakes writing nothing that varies between runs — which is
why `built_at` in `baked.json` is stamped by the final layer rather than
by `bake_nodes.py`.

## Escape hatches

| Variable | Effect |
| --- | --- |
| `EMBER_USE_BAKED_COMFY=0` | Ignore the baked ComfyUI; the app clones and pip-installs its own, which is the non-Docker behaviour. For a baked tree that turns out to be wrong. |
| `EMBER_START_SOURCE=api` | Fetch the start script from the licence server instead of using the baked copy, so a fix there applies without a new image. |
| `EMBER_IMAGE` | A published tag to run instead of a locally built one. |
| `EMBER_DEV_SOURCE` | Set to `/src` by `docker-compose.dev.yml`; flips the entrypoint to run mounted source. |
| `EMBER_DEV_IMAGE` | Dev compose only; defaults to `ember:dev`. |

## Next

- [Configuration](../configuration.md)
- [Troubleshooting](../troubleshooting.md)
- [Dry run](dry-run.md) — the UI with no GPU and no Docker at all
