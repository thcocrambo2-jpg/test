# Configuration

Every environment variable the app reads, what it does, and who is expected
to set it. For someone running the app; the second half is for whoever
builds and publishes it.

The app reads the environment in exactly one module,
[`ember/settings.py`](../ember/settings.py) — that is enforced by
`scripts/check_imports.py`. Anything a pod's environment panel can change
is decided there, once, and validated where it is read, so no other module
has to guess what an unset or malformed value means. Facts about a model or
its graph are not environment and are not there: each pipeline keeps its own
in `ember/pipelines/<name>/constants.py`.

Setting a variable for one command — PowerShell:

```powershell
$env:EMBER_LICENSE_KEY="<key>"; python app.py
```

bash (WSL or a pod):

```bash
EMBER_LICENSE_KEY=<key> python3 app.py
# or for a whole session:
export EMBER_LICENSE_KEY=<key>
```

To persist one on Windows — PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("EMBER_BASE_DIR", "C:\ember", "User")
```

Never commit a file containing the licence key or the node tag. They are
credentials.

**The `KREA2_*` spelling of every variable on this page still works.** Each
name is read as `EMBER_*` first and falls back to `KREA2_*`, and a start
that used any fallback says so in one line of the log. The fallback is a
transition, not a second supported name: it will be removed, so rename the
variables on your pod when convenient.

## The app at runtime

Read by `app.py`, `scripts/dryrun.py` and the shipped binary. "Who" is who
normally sets it: the **customer** on their pod or PC, the **operator**
running the deployment, or a **developer** working on a checkout.

| Variable | Default | Effect | Who |
| --- | --- | --- | --- |
| `EMBER_LICENSE_KEY` | none — **required** | The customer key. Takes a seat and decides which tabs exist. Unset, the app stops before any expensive work. | customer |
| `EMBER_NODE_TAG` | none — **required** | The deployment id the key checks in against. The licence API is `https://<tag>.vercel.app`, assembled from this. | customer |
| `EMBER_BASE_DIR` | `/workspace/ember`, or `/workspace/krea2` when that exists and `/workspace/ember` does not (`.dryrun/` under `scripts/dryrun.py`) | Where the ComfyUI install, model weights, generated images and logs live. | customer, developer |
| `EMBER_LICENSE_GRACE` | `1800` (seconds) | How long the app keeps running when the licence server is unreachable. Long enough not to kill a video render mid-way, short enough that a cut-off pod does not run indefinitely. | operator |
| `EMBER_KEEP_MODELS_LOADED` | unset | Set to anything and models are *not* unloaded between swaps. Off by default; see [Model swapping](architecture/model-swapping.md). | developer |
| `EMBER_SAGE_ATTENTION` | `1` | `0`, `false`, `no` or `off` runs ComfyUI with PyTorch attention instead of SageAttention. The first thing to try if a tab's output looks wrong on a card where it did not. | customer |
| `EMBER_WAN_PARALLEL` | unset | Set to anything and video jobs get their own ComfyUI instance on port 8189, so a quick image never queues behind a long render. Only has an effect when the `wan_i2v` feature is on. | operator |
| `EMBER_MAIN_RESERVE_VRAM` | `26` (GB) | Parallel mode only: VRAM the image instance leaves free for the video one. | operator |
| `EMBER_WAN_RESERVE_VRAM` | `22` (GB) | Parallel mode only: VRAM the video instance leaves free for the image one. | operator |
| `EMBER_SHOWCASE_URL` | empty | Public bucket serving the pricing page's screenshots. Empty is a supported state: the section still renders, with a placeholder tile per missing picture. | operator |
| `EMBER_UI_REQUIRE_TOKEN` | unset | Set to anything and the UI demands the access token from the URL fragment. Without it the UI is open to anyone with the link. | operator |
| `EMBER_SKIP_LAUNCH` | unset | Do everything a normal start does — licence, catalogue, setup, downloads, ComfyUI — then stop instead of serving. What a pod that only warms its volume runs. | operator |
| `EMBER_MIRROR_USER` | `thcocrambo2` | The Hugging Face account the weight mirrors live under. | developer |
| `EMBER_NO_MIRROR` | unset | Set to anything and every download goes straight upstream, never to the mirror. For debugging a suspected bad mirror without editing anything. | developer |
| `EMBER_CATALOG_FILE` | unset | Read the model and LoRA catalogue from this JSON file instead of the licence server. What makes a dry run and every check work offline. | developer |
| `RUNPOD_POD_ID` | set by RunPod | A stable instance id for the licence seat, and reported alongside it. | automatic |
| `RUNPOD_POD_HOSTNAME` | set by RunPod | The fallback when the pod id is not set. Off a pod, the seat falls back to the hostname and finally to a random id. | automatic |
| `HF_TOKEN` | unset | Hugging Face **read** token. Only needed for gated upstream repos — the mirrors are public, so a healthy pod pulls anonymously. | customer |
| `CIVITAI_TOKEN` | unset | CivitAI API token. Only used when a download falls through to CivitAI, i.e. when the mirror could not serve it. | customer |

### The validation worth knowing about

- **`EMBER_NODE_TAG` must be one DNS label** — 8 to 63 characters, lower
  case letters, digits and hyphens, starting and ending with an
  alphanumeric. A dot, a slash, a colon or a port is how a tag would
  smuggle in a different host, so a value carrying one is rejected outright
  rather than stripped and partly used. A bare id rather than a whole URL
  means the licence check can never be answered by a server the customer
  chose. There is deliberately **no fallback**: the id is not compiled into
  the binary, so a pod that does not carry it cannot take a seat at all.
- **An unset `EMBER_BASE_DIR` picks a directory that already holds
  weights.** A volume carrying `/workspace/krea2` and no `/workspace/ember`
  is used where it is: nothing is moved or copied, so a 90 GB volume never
  re-downloads. A volume with `/workspace/ember`, or with neither, gets
  `/workspace/ember`.
- **`EMBER_BASE_DIR` is expanded and resolved to an absolute path.** A
  relative value like `./tmp` is natural on a dev box, and would otherwise
  be resolved by each process against its own working directory — ComfyUI
  runs with its own, so images would land somewhere the app never looks.
- **`EMBER_SHOWCASE_URL` must be `https://`,** and any query or fragment is
  dropped. Anything else is ignored with a warning at startup rather than
  used, and the page then reads exactly as it does with no bucket at all.
- **Keep `HF_TOKEN` unset rather than wrong.** A stale token turns an
  anonymous download into a 401, which reads as "the mirror is missing
  files" when the real problem is the credential.

## Docker Compose — the customer's `.env`

Template: `.env.example`. Compose refuses to start without `.env`, and
reads it from the project directory on its own — nothing is set in the
shell. See [Docker](running/docker.md).

| Variable | Default | Effect |
| --- | --- | --- |
| `EMBER_LICENSE_KEY` | none — **required** | As above. |
| `EMBER_NODE_TAG` | none — **required** | As above. |
| `EMBER_IMAGE` | `ember:latest` | A published tag to run instead of a locally built image. |
| `EMBER_USE_BAKED_COMFY` | `1` | `0` ignores the ComfyUI baked into the image; the app clones and pip-installs its own. |
| `EMBER_START_SOURCE` | `baked` | `api` fetches the start script from the licence server, so a fix there applies without a new image. Needs `EMBER_NODE_TAG`. |
| `EMBER_DEV_SOURCE` | unset | Set to `/src` by `docker-compose.dev.yml`; flips the entrypoint to run mounted source instead of the published binary. |
| `EMBER_DEV_IMAGE` | `ember:dev` | Dev compose only. |
| `CIVITAI_TOKEN` | unset | As above. |

This file is **not** `license-validator/.env`, which holds publishing
credentials. Different file, different purpose, and neither goes into the
image.

## Operator and build-time variables

None of these are read by the running app. They belong to whoever compiles
and publishes a build, and `--no-publish` builds need none of the
credentials. See [Publishing](releasing/publishing.md).

| Variable | Used by | Effect |
| --- | --- | --- |
| `PYTHON` | `build.sh`, `build.ps1` | Which interpreter to compile in. |
| `EMBER_ADMIN_TOKEN` | `build.sh`, `build.ps1` | The deployment's `ADMIN_TOKEN`, which guards `/v1/admin/*`. Exported by `make` from `license-validator/.env`. |
| `EMBER_BUILD_CHANNEL` | `build.sh`, `build.ps1` | Which channel to point this build at. Default `stable`; set something else to upload a build without shipping it to customers. |
| `R2_ACCOUNT_ID` | build, publish | Cloudflare account id. |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | build, publish | The R2 token the upload uses. `make` maps the **write** pair onto these names. |
| `R2_BUILDS_BUCKET` | build, publish | Default `krea2-builds`. Private bucket. |
| `R2_SHOWCASE_BUCKET` | `scripts/r2_upload_showcase.py` | The **public** bucket that serves `EMBER_SHOWCASE_URL`. |
| `HF_WRITE_TOKEN` | `scripts/mirror_to_hf.py` | Hugging Face write token for the mirror repos. See [Mirror and pins](releasing/mirror-and-pins.md). |

## The licence server's own `.env`

Template: `license-validator/.env.example`. Read by the Express app **and**
by every `make` target, which `source` the same file. Full detail is in
[the licence server's docs](../license-validator/README.md).

| Variable | Required for | Effect |
| --- | --- | --- |
| `MONGODB_URI` | server, DB scripts | Atlas connection string. Never leaves the server. |
| `MONGODB_DB` | server | Default `krea2_license`. |
| `ADMIN_TOKEN` | admin routes, publishing | Guards `/v1/admin/*`. Unset, those routes 404. `make` exports it as `EMBER_ADMIN_TOKEN`. |
| `EMBER_NODE_TAG` | every `make` target | Which deployment to talk to. |
| `STALE_SECONDS` | server | A seat is freed this long after its last heartbeat. Default 180. |
| `HEARTBEAT_SECONDS` | server | Cadence handed to clients. Default 60. |
| `SESSION_TTL_SECONDS` | server | Session row retention. Default 900. |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | server | R2 token, **Object Read only**. |
| `R2_WRITE_ACCESS_KEY_ID`, `R2_WRITE_SECRET_ACCESS_KEY` | publishing only | **Object Read & Write** token. |
| `BUILD_URL_TTL_SECONDS` | server | Presigned build URL lifetime. Default 1800. |
| `BUILD_DOWNLOADS_PER_HOUR` | server | Cap per licence. `0` disables. Default 20. |
| `DOWNLOAD_TTL_SECONDS` | server | Download-log retention. Default 2592000. |
| `CONTACT_URL` | server | The Telegram link on the pricing page. Must be `https:`. |
| `PORT` | local server | Default 3000. |

**The two R2 token pairs are deliberately different.** The read-only pair is
what the public service holds; the write pair exists only where builds are
made. `make` refuses to publish if it finds them equal — R2 does not 403
until the whole artifact has already been uploaded.

## Next

- [Troubleshooting](troubleshooting.md) — what a missing or malformed value
  looks like at startup
- [RunPod](running/runpod.md), [Windows](running/windows.md),
  [Docker](running/docker.md), [Dry run](running/dry-run.md)
