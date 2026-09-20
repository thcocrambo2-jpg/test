# Ember documentation

Ember is a FastAPI + React web app that installs and drives ComfyUI on a
GPU machine. This index is grouped by what you are trying to do. Start
with [Architecture overview](architecture/overview.md) if you want the
shape of the whole thing in one page.

## Running it

For someone who wants the app working on a machine.

| Page | What it covers |
| --- | --- |
| [Running on RunPod](running/runpod.md) | The four ways to run it, and the proven one: a GPU pod fetching the published Linux binary |
| [Running on Windows](running/windows.md) | The Windows binary, what the machine needs, PyTorch and SageAttention |
| [Running in Docker](running/docker.md) | The image that bakes the environment, Compose, and reusing models you already have |
| [Running without a GPU](running/dry-run.md) | `scripts/dryrun.py`, and the mocked models under `tmp2/` |
| [Configuration](configuration.md) | Every environment variable: default, effect, and who sets it |
| [Troubleshooting](troubleshooting.md) | What a healthy start logs, and what each failed one means |

## Using it

For someone with the app in front of them.

| Page | What it covers |
| --- | --- |
| [Prompt library](features/prompt-library.md) | Shared prompts, silent capture, and approval |
| [Settings presets](features/presets.md) | Saved settings, who may write one, and what they cost at startup |
| [The pricing page](features/pricing-page.md) | Plans, billing cycles and the feature showcase |
| [Prompt undo / redo](features/undo-redo.md) | The four cases, and why the buttons exist |
| [Image input shortcuts](features/image-shortcuts.md) | Click, drop, paste, and the recent-generations strip |

## The models

One page per tab family, plus the catalogue that decides which models
and LoRAs a licence can see.

| Page | What it covers |
| --- | --- |
| [Models and LoRAs](pipelines/catalogue.md) | The catalogue: where it comes from, and the id contract |
| [Krea 2](pipelines/krea2.md) | 🎨 Krea2 text-to-image and ✨ Krea2 Edit |
| [Krea 2 V2](pipelines/krea2-v2.md) | 🔶 The V2 graph, turbo and raw |
| [Krea 2 V2 Edit](pipelines/krea2-v2-edit.md) | The V2 graph with editing |
| [Video](pipelines/wan.md) | Wan 2.2 image-to-video, and the parallel instance |
| [Video with sound](pipelines/minimax.md) | MiniMax H3, both tabs, and the LoRA stack |

## Changing the code

For someone editing this repository.

| Page | What it covers |
| --- | --- |
| [Architecture overview](architecture/overview.md) | The startup order and why it is that order; the processes; a map of `ember/` |
| [Licensing and features](architecture/licensing-and-features.md) | Seats as leases, feature keys, and the four-layer licence gate |
| [The job queue](architecture/job-queue.md) | Lanes, record-then-run, SSE, and the ETA |
| [The Gallery and recipes](architecture/gallery-and-recipes.md) | Browsing output, and "how was this made?" |
| [Model swapping](architecture/model-swapping.md) | Signatures, freeing VRAM, and crash recovery |
| [The web UI](architecture/web-ui.md) | Routing, the theme, and why the React bundle is committed |
| [The checks](development/checks.md) | One section per check: what it guards and the silent failure it prevents |
| [Adding a pipeline](development/adding-a-pipeline.md) | Every place a new model family touches, with a worked example |
| [Conventions](development/conventions.md) | Config tiers, empty `__init__`s, commits, and the names that can never change |

## Shipping a build

For someone publishing a release.

| Page | What it covers |
| --- | --- |
| [Building the Linux binary](releasing/build-linux.md) | Nuitka on a pod or in WSL2 |
| [Building the Windows binary](releasing/build-windows.md) | `build.ps1`, and what each machine needs |
| [Publishing, channels and rollback](releasing/publishing.md) | The `make` targets, credentials, and rolling forward |
| [The mirror and the pins](releasing/mirror-and-pins.md) | Where weights come from, and how they stay fixed |

## Reference

- [Command reference](reference/commands.md) — every command, by task, with the shell named.

## The licence server

`license-validator/` is a separate Node app on Vercel. It decides which
tabs a key gets and serves the catalogue.

- [license-validator/README.md](../license-validator/README.md) — design, setup and deploying
- [Plans and entitlements](../license-validator/docs/plans-and-entitlements.md)
- [Endpoints](../license-validator/docs/endpoints.md)
- [Catalogue data](../license-validator/docs/catalogue-data.md)
- [Build distribution](../license-validator/docs/build-distribution.md)
- [Telegram bot](../license-validator/docs/telegram-bot.md)
- [Data](../license-validator/docs/data.md)
