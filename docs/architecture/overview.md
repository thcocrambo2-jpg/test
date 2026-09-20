# Architecture overview

How Ember is put together: what runs, in what order it starts, and where
each piece of the code lives. For someone changing the code. If you only
want to run the app, start from `docs/running/`.

Ember is a FastAPI application that installs and drives ComfyUI on a GPU
machine, and serves a React front end over one public URL. The Python
entry point is `app.py`, a three-line shim over
[`ember.main.main()`](../../ember/main.py).

## Startup, in order

`main()` is a single straight-line function, and the order of its steps is
the design. Each one sits where it does because of what must already be
true, or must *not* yet have happened, when it runs.

| # | Step | Code |
| --- | --- | --- |
| 1 | Configure logging, make the output tree, log where things live | `logs.setup()`, `settings.ensure_dirs()`, `settings.log_startup()` |
| 2 | Take a licence seat, or stop | `seat.acquire_or_exit()` |
| 3 | Resolve which tabs the key grants | `features.resolve()` |
| 4 | Load the model and LoRA catalogue | `catalog.load()` |
| 5 | Clone ComfyUI, install requirements and custom nodes | `ember.comfy.setup` |
| 6 | Download the weights the granted features need | `downloads.download_everything()` |
| 7 | Start one or two ComfyUI servers and wait for their APIs | `ember.comfy.server` |
| 8 | Serve the app and open the tunnel | `serve.serve()` |

**Configuration first, and nothing on import.**
[`ember/settings.py`](../../ember/settings.py) runs nothing when it is
imported: no logger, no `mkdir`, no log line. So the three calls at the
top of `main()` are the first statements in the process, before anything
else can log or write. Every other entry point — the scripts under
`scripts/` — makes the same three calls for the same reason.

**The licence before anything expensive.** A customer who cannot take a
seat finds out in seconds rather than after ~90 GB of downloads. That is
only possible because [`ember/licensing/seat.py`](../../ember/licensing/seat.py)
is stdlib-only: it runs ahead of the pip pass that everything below waits
for. See [licensing-and-features.md](licensing-and-features.md).

**Features after the licence, and never earlier.** Which tabs a pod gets
comes from the licence and from nothing else, so the answer cannot exist
before step 2 returns. That is why `features.resolve()` is a call rather
than something computed at import, and why every reader asks
`features.enabled()` at call time — an answer that only exists now still
reaches modules that were imported long before it.

**The catalogue before the downloads.** The catalogue is the download list
as much as it is the dropdown list, so it has to be known before step 6.
It is frozen from then on, which is what stops the files fetched, the
choices offered and the Krea 2 V2 slot count from ever disagreeing. It is
stdlib-only too, so it also runs ahead of pip. A server that does not
answer degrades to the last saved copy in `BASE_DIR/.catalog.json`, then
to an empty catalogue that only the Krea tabs notice. See
[Catalogue](../pipelines/catalogue.md).

**Setup before downloads, and PyTorch before ComfyUI.**
`setup.ensure_torch()` runs before `setup.install_comfyui()` because
ComfyUI's own `requirements.txt` lists `torch` unpinned: on a machine
without one, that pip pass would pull whatever PyPI defaults to, which on
a Blackwell card is a torch with no kernels for it.
`setup.install_sageattention()` is last of the installs, so nothing after
it can move torch out from under the build it picked.

**Downloads before ComfyUI starts,** because a feature that is off costs
no disk and no download time — a promise that is only real if the download
list is the resolved feature list rather than the full one.

**GPU detection happens when `ember.comfy.server` is imported,** which is
why that import sits inside `main()` rather than at the top of the module.
The same is true of `ember.weights.downloads` and `ember.web.serve`:
anything needing a third-party package is imported only after step 5 has
installed it, so the app can bootstrap itself on a bare pod.

**Custom nodes are verified before the app serves.** Node packs register
at ComfyUI startup and a failed import is only reported in `comfyui.log`,
so `main()` checks the Krea 2 V2 packs, the MiniMax core node and the
Krea 2 Edit pack itself rather than letting the first generation fail with
a bare "node not found".

`KREA2_SKIP_LAUNCH=1` stops after step 7: everything is installed and
running, nothing is served.

## The processes

On a running pod:

| Process | Port | What it is |
| --- | --- | --- |
| The app | 7860 | uvicorn — the API, the React bundle, the SSE stream |
| ComfyUI | 8188 | the main instance, driven over HTTP and websocket |
| ComfyUI (video) | 8189 | a second instance, only with `KREA2_WAN_PARALLEL=1` *and* the Video tab granted |
| `cloudflared` | — | a quick tunnel giving the app its public `*.trycloudflare.com` URL |

The second ComfyUI instance is only worth its VRAM reservation when there
is a Video tab to serve, so `KREA2_WAN_PARALLEL` on its own does not buy
one. When it exists, each instance reserves VRAM for the other
(`--reserve-vram`) so the two can coexist on one GPU; the defaults are
tuned for a 48 GB A40. The queue then gets a second lane — see
[job-queue.md](job-queue.md).

Two services live off the machine:

- **The licence server** (`license-validator/`, Node on Vercel). It takes
  and renews the seat, resolves the plan into a feature list, and serves
  the model and LoRA catalogue, the plans behind the pricing page, the
  presets and the prompt library. Every one of those degrades to a cached
  or empty answer rather than failing the app.
- **R2**, which holds the published binaries. The app never talks to it;
  the start scripts fetch the build through the licence API before the app
  exists. See [Publishing](../releasing/publishing.md).

Weights come from Hugging Face and CivitAI, through a mirror when one is
reachable. [`ember/weights/mirror.py`](../../ember/weights/mirror.py)
resolves every asset mirror-first and falls through to upstream *silently*
— worth knowing when a download is slower than expected, because nothing
looks wrong until the day an upstream file has actually vanished.

## The packages

```
app.py                three-line shim -> ember.main.main()
ember/
  main.py             the startup flow above, and nothing else
  settings.py         the only module that reads os.environ, and what follows from it
  logs.py             the application logger and the one call that configures it
  features.py         which tabs the licence grants: the Key enum and the FEATURES registry
  licensing/          the licence server, as five read-only clients
    seat.py           acquire / heartbeat / release; stdlib only
    catalog.py        the model and LoRA catalogue, by feature key
    plans.py          the public plan catalogue behind the pricing page
    presets.py        each tab's named starting points
    prompts.py        the prompt library
  comfy/
    setup.py          clone ComfyUI, install requirements and custom node packs
    server.py         GPU detection and ComfyUI launch (one or two instances)
    client.py         the ComfyUI HTTP + websocket client: queue, progress, upload, interrupt
  weights/
    downloads.py      Hugging Face and CivitAI fetches, resumable and retried
    mirror.py         mirror-first, pinned asset resolution
  pipelines/
    common.py         what every pipeline reads out of the catalogue and off the disk
    <family>/constants.py   facts about that model and its graph
    <family>/workflow.py    the ComfyUI API-format graph builders
    <family>/handler.py     that family's generate_* generators
  generation/
    runner.py         the lanes, and the loop each job runs through
    loras.py          the LoRA slots Krea and MiniMax share
    handlers.py       the feature keys, the preset save, and the output listing
    queue.py          the visible job queue: one worker thread per lane
    eta.py            how long the running job has left
    recipes.py        what each generated file was made with
  web/
    api.py            builds the app and calls each route module in turn
    routes/           one module per area: uploads, media, queue, tabs, licence, events
    tabschema.py      assembles the tabs, in navigation order
    schema/           the schema types, the shared field builders, one module per tab
    serve.py          uvicorn plus the Cloudflare quick tunnel
    spa.py            serves the compiled React bundle out of process memory
    webui_bundle.py   generated and committed; every built asset as a gzip bytes literal
    gallery_index.py  lists OUTPUT_DIR and makes the thumbnails
    showcase.py       the copy and pictures on the pricing page
webui/                the React app (Vite + TypeScript)
license-validator/    the Node/Express + MongoDB licence server
```

Five pipeline families live under `ember/pipelines/`: `krea2`,
`krea2_v2`, `krea2_v2_edit`, `wan` and `minimax`. Each is a
`constants.py` and a `workflow.py`, and nothing else in the tree needs to
know how many there are — see `docs/pipelines/`.

**Every `__init__.py` under `ember/` is empty, deliberately.**
`ember/comfy/server.py` detects GPUs when it is imported and raises
without one; a package `__init__` that imported its siblings would drag
that into the Docker bake stage and into every laptop script.
`scripts/check_imports.py` enforces it, along with the rule that
`settings.py` is the only module reading the environment. See
[Conventions](../development/conventions.md).

## Where to read next

- [licensing-and-features.md](licensing-and-features.md) — seats,
  entitlements, and what a feature key costs
- [job-queue.md](job-queue.md) — lanes, cancellation, progress and the ETA
- [gallery-and-recipes.md](gallery-and-recipes.md) — the output browser
  and how a picture gets made again
- [model-swapping.md](model-swapping.md) — VRAM, `/free`, and crash
  recovery
- [web-ui.md](web-ui.md) — the React app, routing, and why the bundle is
  committed
