# Ember

Ember installs and drives [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
on a GPU machine and puts a React web UI in front of it. It ships as a
single binary: the front end is compiled into it as a Python module and
served from memory, and a licence server decides which tabs each key
opens. The tabs are 🎨 Krea2 and ✨ Krea2 Edit, 🔶 Krea2 V2 and 🔷 Krea2 V2
Edit (text-to-image and instruction editing on the Krea 2 model), 🎬 Wan
Video (Wan 2.2 image-to-video), and 🎥 MiniMax I2V and 🎞️ MiniMax T2V
(MiniMax H3 video with sound). Alongside them are a gallery, a job queue
with ETAs, recipes, a prompt library, settings presets and a pricing page.

## The four ways to run it

One app, one licence server, four ways of getting it onto a machine. They
differ in who compiles it and what the machine must already have. They do
**not** differ in what the app does, and all four take a seat through the
same licence check.

| | For | What runs | Where the app comes from |
| --- | --- | --- | --- |
| **1. Linux binary** | pod customers | `scripts/runpod_start.sh` → `dist/krea2app` | built by `./build.sh` on a pod, fetched from the licence server on every start |
| **2. Windows binary** | Windows customers | `scripts/windows_start.ps1` → `dist\krea2app.exe` | built by `.\build.ps1` on Windows, fetched the same way |
| **3. Docker image** | anyone with a GPU and Docker | `docker compose up` | the image carries the *environment*; the binary is still fetched inside it |
| **4. From source** | you, while developing | `python app.py` | your working tree |

A customer on a pod gets 1 — the proven path, and the one the pricing and
support flow assume. A customer on their own Windows PC with an NVIDIA
card gets 2; the `.exe` is not self-contained and still needs Python 3.12
and git. A customer who wants a reproducible environment gets 3, which
removes the first-boot ComfyUI install but not the model download. You,
changing code, use 4 — nothing else runs what you just edited, because
1, 2 and 3 all fetch the last *published* build.

**Which are proven.** 1 and 4 are what production runs on. 3 is written
but has never been built or run; treat it as unverified. 2 is newer, and
[Running on Windows](docs/running/windows.md) says what has been tested.

The two build scripts are a matched pair and neither can produce the
other's artifact — Nuitka compiles for the OS it runs on. They bundle the
same data files and packages, and `make check-args` fails if that stops
being true.

Full instructions: [Running on RunPod](docs/running/runpod.md),
[Running on Windows](docs/running/windows.md),
[Running in Docker](docs/running/docker.md).

## Quickstart, without a GPU

`scripts/dryrun.py` serves the whole app on <http://127.0.0.1:7860> with
no GPU, no ComfyUI and no weights. Generate is stubbed; everything else —
routing, the licence gate, the gallery, the queue, every tab's controls —
is the real thing.

PowerShell:

```powershell
python scripts\dryrun.py --features all
```

bash:

```bash
python3 scripts/dryrun.py --features all
```

`--features` fakes an entitlement, so no licence key is needed. Pass a
real `KREA2_LICENSE_KEY` and `KREA2_NODE_TAG` instead to exercise the
prompt library, presets and plans against the live server. See
[Running without a GPU](docs/running/dry-run.md).

## The repository

```
app.py                 3-line shim into ember.main.main(); the Nuitka target
ember/                 the app
  main.py              startup: licence → features → catalogue → setup → downloads → serve
  settings.py          every environment variable, and the paths derived from them
  logs.py              logging setup
  features.py          the feature keys, and what each one downloads
  licensing/           seats, the catalogue, plans, presets, prompts, and one HTTP client
  comfy/               installing ComfyUI, running it, and talking to it
  weights/             downloading models, and the Hugging Face mirror
  pipelines/           one package per model family: constants, workflow, handler
  generation/          the job queue, ETAs, recipes, the lanes and the LoRA slots
  web/                 FastAPI routes, tab schemas, the SPA and the compiled bundle
webui/                 the React source the bundle is built from
license-validator/     the licence server (Node, on Vercel)
scripts/               start scripts, the checks, and the operator tools
docker/                the bake stage that builds the image's environment
assets/                showcase and branding
docs/                  everything below
```

## Documentation

[docs/README.md](docs/README.md) is the index, grouped by reader. The
pages people reach for most:

- [Architecture overview](docs/architecture/overview.md) — the shape of the whole thing
- [Configuration](docs/configuration.md) — every environment variable
- [Troubleshooting](docs/troubleshooting.md) — what a failed start means
- [The checks](docs/development/checks.md) — what each one guards
- [Command reference](docs/reference/commands.md) — every command, by task
- [license-validator/README.md](license-validator/README.md) — the licence server
