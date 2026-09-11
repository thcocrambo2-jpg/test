# Command reference

Every command this project is driven by, the shell each one runs in, and the
environment variables each one needs.

> **No secrets in this file.** Every credential below is written as a
> `<placeholder>`. The real values live in three gitignored files:
> `.env`, `license-validator/.env`, and `commands.txt`. Keep it that way —
> this file is not gitignored.

---

# Quick reference

The four commands reached for most often. Each is repeated in full, with its
caveats, in the section named beside it.

## Launch

### Linux — a GPU pod (§9.1, §3)

```bash
# what a customer runs: fetch the published build and start it
KREA2_LICENSE_KEY=<key> KREA2_NODE_TAG=<tag> \
  bash -c 'curl -fsSL https://<tag>.vercel.app/v1/start.sh -o /tmp/krea2-start.sh && exec bash /tmp/krea2-start.sh'

# from source, on a pod that already has the repo
KREA2_LICENSE_KEY=<key> KREA2_NODE_TAG=<tag> python3 app.py

# no GPU, no ComfyUI, no weights — just the UI
KREA2_LICENSE_KEY=<key> KREA2_NODE_TAG=<tag> python3 scripts/dryrun.py
```

### Windows — desktop (§9.2, §3)

```powershell
# what a customer runs
$env:KREA2_LICENSE_KEY="<key>"
$env:KREA2_NODE_TAG="<tag>"
powershell -ExecutionPolicy Bypass -File krea2-start.ps1

# no GPU, no ComfyUI, no weights — just the UI
$env:KREA2_LICENSE_KEY="<key>"; $env:KREA2_NODE_TAG="<tag>"; .\.venv\Scripts\python.exe scripts\dryrun.py
```

Needs Python 3.12, git and an NVIDIA GPU on the machine — the `.exe` is not
self-contained. Docker instead: §9.3.

## Build

Nuitka does not cross-compile: each platform builds its own artifact.

### Linux — `dist/krea2app`, on a **POD** or **WSL** (§6.1)

```bash
./build.sh --no-publish    # build only, needs no credentials
./build.sh                 # build, then offer to publish
./build.sh -y              # build and publish without asking
./build.sh --upload-only   # publish the existing artifact, compile nothing

make compile               # check-args, then ./build.sh --no-publish
make release               # check-args, compile, publish
```

### Windows — `dist\krea2app.exe`, on **PS** (§6.2)

```powershell
.\build.ps1                    # compile only — publishing is OFF by default here
.\build.ps1 -Publish           # ... then ask before publishing
.\build.ps1 -Publish -Yes      # ... and publish without asking
.\build.ps1 -UploadOnly        # publish the existing .exe, compile nothing
```

Publishing (either platform) needs `R2_ACCOUNT_ID`, the **write** R2 token,
`R2_BUILDS_BUCKET`, `KREA2_NODE_TAG` and `KREA2_ADMIN_TOKEN` — `make` loads
all of them from `license-validator/.env`. A plain build needs none.

---

## 0. Conventions

Three shells appear in this project. They are not interchangeable, and the
env-var syntax differs in each.

| Tag | What it is | Where you run it |
| --- | --- | --- |
| **PS** | Windows PowerShell 5.1 | Windows, repo root |
| **WSL** | Ubuntu 24.04 under WSL2 (bash) | Windows, via `wsl` |
| **POD** | RunPod / Linux shell (bash) | the GPU pod |

Setting an env var for one command:

```powershell
# PS
$env:KREA2_LICENSE_KEY="<key>"; python scripts\dryrun.py
```

```bash
# WSL / POD
KREA2_LICENSE_KEY=<key> python3 scripts/dryrun.py
# or for a whole session:
export KREA2_LICENSE_KEY=<key>
```

**`make` is WSL/POD only.** The Makefile declares `SHELL := /bin/bash` and
uses `source`, arrays and `${!indirect}` — it will not run under PowerShell
or dash. The Windows build has no make target; it is `.\build.ps1`.

Repo root on this machine: `C:\Adarsh\Personal\Learn\DSA\cp\test\test`
(as `/mnt/c/Adarsh/Personal/Learn/DSA/cp/test/test` from WSL).

---

## 1. Environment variables

### 1.1 The app at runtime (`config.py`)

Read by `app.py`, `scripts/dryrun.py`, and the shipped binary.

| Variable | Required | Purpose |
| --- | --- | --- |
| `KREA2_LICENSE_KEY` | **yes** | The key. Takes a seat; decides which tabs exist. |
| `KREA2_NODE_TAG` | **yes** | Deployment id. The API is `https://<tag>.vercel.app`, assembled client-side. Bare label — no scheme, no `.vercel.app`. |
| `KREA2_BASE_DIR` | no | Where models/outputs/temp live. Default `/workspace/krea2` on a pod; `.dryrun/` under `scripts/dryrun.py`. |
| `KREA2_SHOWCASE_URL` | no | Public R2 base URL for pricing-page images. |
| `KREA2_LICENSE_GRACE` | no | Grace window when the licence server is unreachable. |
| `KREA2_KEEP_MODELS_LOADED` | no | Keep weights resident between runs. |
| `KREA2_MAIN_RESERVE_VRAM` | no | VRAM held back on the main ComfyUI. |
| `KREA2_WAN_RESERVE_VRAM` | no | Same, for the Wan video instance. |
| `KREA2_WAN_PARALLEL` | no | Run the Wan instance alongside the main one. |
| `KREA2_SKIP_LAUNCH` | no | Bootstrap without starting ComfyUI. |
| `KREA2_UI_REQUIRE_TOKEN` | no | Demand the access key from the URL fragment. The UI is open to anyone with the link without it. |
| `KREA2_MODELS` / `KREA2_VARIANT` | no | Model registry overrides. |
| `KREA2_NO_MIRROR` | no | Never pull from the HF mirror. |
| `KREA2_MIRROR_USER` | no | Which HF account the mirror lives under. |
| `HF_TOKEN` | no | Hugging Face **read** token, for gated repos. |
| `CIVITAI_TOKEN` | no | Only if CivitAI starts refusing anonymous downloads. |
| `RUNPOD_POD_ID` | auto | Set by RunPod; used for the instance id. |
| `COMFYUI_SERVER` | no | Point at an already-running ComfyUI. |

### 1.2 `.env` — Docker Compose (the customer's file)

Template: `.env.example`. Compose refuses to start without `.env`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `KREA2_LICENSE_KEY` | **yes** | As above. |
| `KREA2_NODE_TAG` | **yes** | As above. |
| `KREA2_IMAGE` | no | Run a published tag, e.g. `ghcr.io/<owner>/krea2:latest`. |
| `KREA2_DEV_IMAGE` | no | Dev compose only; default `krea2:dev`. |
| `KREA2_DEV_SOURCE` | no | Set to `/src` by `docker-compose.dev.yml`; flips the entrypoint to run mounted source. |
| `KREA2_USE_BAKED_COMFY` | no | `0` = ignore the baked ComfyUI, install a fresh one. |
| `KREA2_START_SOURCE` | no | `api` = fetch `start.sh` from the licence server, not the baked copy. |
| `CIVITAI_TOKEN` | no | As above. |

### 1.3 `license-validator/.env` — the licence server and publishing

Template: `license-validator/.env.example`. Read by the Express app **and**
by every `make` target (they `source` this one file).

| Variable | Required for | Purpose |
| --- | --- | --- |
| `MONGODB_URI` | server, DB scripts | Atlas connection string. Never leaves the server. |
| `MONGODB_DB` | server | Default `krea2_license`. |
| `ADMIN_TOKEN` | admin routes, publish | Guards `/v1/admin/*`. Unset → those routes 404. `make` exports it as `KREA2_ADMIN_TOKEN`. |
| `KREA2_NODE_TAG` | every `make` target | Which deployment to talk to. |
| `STALE_SECONDS` | server | Seat freed this long after the last heartbeat. Default 180. |
| `HEARTBEAT_SECONDS` | server | Cadence handed to clients. Default 60. |
| `SESSION_TTL_SECONDS` | server | Session row retention. Default 900. |
| `R2_ACCOUNT_ID` | server, publish | Cloudflare account id. |
| `R2_ACCESS_KEY_ID` | server | R2 token, **Object Read only**. |
| `R2_SECRET_ACCESS_KEY` | server | Its secret. |
| `R2_BUILDS_BUCKET` | server, publish | Default `krea2-builds`. Private bucket. |
| `R2_WRITE_ACCESS_KEY_ID` | publish only | **Object Read & Write** token. `make` maps it onto `R2_ACCESS_KEY_ID` for `build.sh`. |
| `R2_WRITE_SECRET_ACCESS_KEY` | publish only | Its secret. |
| `BUILD_URL_TTL_SECONDS` | server | Presigned URL lifetime. Default 1800. |
| `BUILD_DOWNLOADS_PER_HOUR` | server | Cap per licence. `0` disables. Default 20. |
| `DOWNLOAD_TTL_SECONDS` | server | Download-log retention. Default 2592000. |
| `CONTACT_URL` | server | Telegram link on the pricing page. Must be `https:`. |
| `PORT` | local server | Default 3000. |

**The two R2 tokens are deliberately different.** The read-only pair is what
the public service holds; the write pair exists only where builds are made.
`make` refuses to publish if it finds them equal — R2 does not 403 until the
whole artifact has already been uploaded.

### 1.4 Build-time only

| Variable | Used by | Purpose |
| --- | --- | --- |
| `PYTHON` | `build.sh`, `build.ps1` | Which interpreter to compile in. |
| `KREA2_ADMIN_TOKEN` | `build.sh`, `build.ps1` | The deployment's `ADMIN_TOKEN`. Exported by `make`. |
| `KREA2_BUILD_CHANNEL` | `build.sh`, `build.ps1` | Channel to point at this build. Default `stable`; set something else to upload without shipping it. |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUILDS_BUCKET` | publish half | The **write** token here, unlike in 1.3. |
| `HF_WRITE_TOKEN` | `scripts/mirror_to_hf.py` | HF write token for the mirror repo. |
| `R2_SHOWCASE_BUCKET` | `scripts/r2_upload_showcase.py` | The **public** bucket serving `KREA2_SHOWCASE_URL`. |

`--no-publish` builds need none of the credentials.

---

## 2. First-time setup

### 2.1 Windows — a Python 3.12 environment

**PS**, with conda:

```powershell
conda create -n krea2 python=3.12 -y
conda activate krea2          # if nothing happens: conda init powershell
pip install -r requirements.txt
```

Or without conda:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
```

### 2.2 WSL — for building and publishing

**PS**, once:

```powershell
wsl --list --verbose          # * marks the default
wsl --set-default Ubuntu-24.04
```

**WSL**:

```bash
sudo apt update && sudo apt install -y python3-venv git
python3 -m venv ~/build-venv && source ~/build-venv/bin/activate
pip install -r requirements.txt
```

A venv is required: Ubuntu 24.04 enforces PEP 668 and installing into the
system Python fails with `externally-managed-environment`. The RunPod image
disables this, which is why the pod does not need one.

### 2.3 Node — only for the front end and the licence server

**PS or WSL**, Node >= 20:

```bash
cd webui && npm ci
cd license-validator && npm install
```

### 2.4 The licence database — once per cluster

**PS or WSL**, from `license-validator/`, needs `MONGODB_URI`:

```bash
npm run init-db          # create indexes — the serverless path never does
npm run seed-catalog     # plans + features
npm run seed-presets     # starting presets
npm run seed-prompts     # starter prompts
```

---

## 3. Run the app locally (no GPU, no ComfyUI, no weights)

`scripts/dryrun.py` renders every entitled tab without downloading ~90 GB.
Generate reports that ComfyUI is not running; everything else works.

**PS**, against the real licence server (verifies the whole entitlement path
and takes a seat until Ctrl-C):

```powershell
$env:KREA2_LICENSE_KEY="<key>"; $env:KREA2_NODE_TAG="<tag>"; .\.venv\Scripts\python.exe scripts\dryrun.py
```

**PS**, offline — no server, no seat:

```powershell
python scripts\dryrun.py --features all
python scripts\dryrun.py --features "single,klein"
```

**WSL/POD** equivalents:

```bash
KREA2_LICENSE_KEY=<key> KREA2_NODE_TAG=<tag> python3 scripts/dryrun.py
python3 scripts/dryrun.py --features all
```

Flags: `--features LIST|all`, `--port N` (default 7860), `--tunnel`
(Cloudflare quick tunnel for a public URL), `--api-only` (accepted, ignored).

Serves `http://127.0.0.1:7860` from the committed `webui_bundle.py`. The
bundle wins over `webui/dist`, so an edit to `webui/src` does **not** appear
until `make webui` regenerates it — the startup line says which source it used.

### The real app (a GPU pod)

**POD**, needs `KREA2_LICENSE_KEY` and `KREA2_NODE_TAG`:

```bash
python3 app.py
```

---

## 4. Front end (React)

**WSL** — the only target that needs Node. Rebuild, then **commit
`webui_bundle.py`**; neither build host has Node.

```bash
make webui        # npm ci && npm run build && scripts/gen_webui_bundle.py
```

`npm ci`, not `npm install`: the lock file is covered by `SOURCE_HASH`, and
`npm install` may move it and turn this into a spurious stale-bundle failure.

Vite dev server with hot reload — it proxies `/api`, `/media` and `/thumbs`
to :7860, so run the app as well:

```bash
make webui-dev                             # WSL
cd webui && npm run dev                    # PS or WSL, same thing
python scripts/dryrun.py --features all    # in another shell
```

Other **PS or WSL** targets, from `webui/`:

```bash
npm run build       # tsc --noEmit && vite build
npm run typecheck   # tsc --noEmit
npm run preview     # serve the built dist
```

---

## 5. Pre-flight checks

**WSL**, credential-free and fast. `make compile` and `make release` run
these automatically.

```bash
make check-args     # all three of the first block below
```

Individually, **PS or WSL**:

```bash
python scripts/check_build_args.py   # build.sh vs build.ps1 Nuitka flag lists
python scripts/check_webui.py        # webui_bundle.py stale vs webui/src
python scripts/check_webui.py --quiet
python scripts/check_routes.py       # API routes vs what the front end calls

python scripts/check_schema.py       # tab schema structure
python scripts/check_schema.py --choices
python scripts/golden.py             # freeze handler workflow snapshots
python scripts/golden.py --check     # exit 1 on any difference
```

Verify credentials without using them — **WSL**, needs `license-validator/.env`:

```bash
make check          # env file, R2 keys, admin token, artifact, stable builds
make health         # what the deployment reports
make builds         # every build published, newest first
```

---

## 6. Building

Nuitka does not cross-compile. Linux and Windows artifacts are built on their
own platforms; neither script can produce the other's output.

### 6.1 Linux — `dist/krea2app`

**POD** (preferred — a standalone binary links against the build machine's
glibc, so building where you deploy avoids one that will not start):

```bash
./build.sh                 # build, then offer to publish
./build.sh -y              # build and publish without asking
./build.sh --no-publish    # build only — needs no credentials
./build.sh --upload-only   # publish the existing dist/krea2app, compile nothing
./build.sh --help
```

**WSL** works too, matching the pod by version rather than by construction.
Point at a specific interpreter with `PYTHON=`:

```bash
PYTHON=~/build-venv/bin/python3 ./build.sh --no-publish
```

Via make — **WSL**:

```bash
make compile        # check-args, then ./build.sh --no-publish
```

**Building under `/mnt/c/…` needs one extra setting.** DrvFs cannot store Unix
file modes, and Nuitka's `chmod` dies minutes into the compile. Either:

```bash
# 1) allow Unix modes on Windows drives, and keep building where you are
printf '[automount]\noptions = "metadata"\n' | sudo tee /etc/wsl.conf
#    then from PS:  wsl --shutdown   (wait ~8s, reopen)
mount | grep ' /mnt/c '                # verify
git config core.fileMode false         # if it produces spurious mode diffs
```

```bash
# 2) or build from the WSL filesystem — also much faster (ext4 vs 9p)
cp -r /mnt/c/Adarsh/Personal/Learn/DSA/cp/test/test ~/test && cd ~/test && ./build.sh
```

### 6.2 Windows — `dist\krea2app.exe`

**PS**. Publishing is **off by default** here, unlike `build.sh` — this runs
on desktops with uncommitted experiments in the tree.

```powershell
.\build.ps1                    # compile only
.\build.ps1 -Publish           # ... then ask before publishing
.\build.ps1 -Publish -Yes      # ... and publish without asking
.\build.ps1 -UploadOnly        # publish the existing .exe, compile nothing
```

Point it at a build interpreter:

```powershell
$env:PYTHON = "$env:USERPROFILE\miniconda3\envs\krea2\python.exe"
.\build.ps1

# or
$env:PYTHON = "$PWD\.venv312\Scripts\python.exe"
.\build.ps1
```

A Windows build registers with `platform="windows"`, so publishing to `stable`
from here is safe for Linux pods — `/v1/build` resolves by (channel, platform)
and defaults to linux for any client that does not say.

---

## 7. Publishing and rollback

**WSL or POD**. All of these read `license-validator/.env`.

```bash
make publish        # upload dist/krea2app and point "stable" at it
make release        # check-args, compile, then publish
```

Publishing needs the write pair (`R2_WRITE_*`) plus `R2_ACCOUNT_ID`,
`R2_BUILDS_BUCKET`, `KREA2_NODE_TAG` and `ADMIN_TOKEN`.

Roll back or forward — the same operation, since a channel is just a name on
one build document:

```bash
make builds                                  # find the sha
make promote SHA=<sha256>
make promote SHA=<sha256> CHANNEL=beta
```

Publish the Windows start script alone, without a Windows machine:

```bash
make start-ps1      # PUTs scripts/windows_start.ps1 to r2://<bucket>/start.ps1
```

It refuses a file with no UTF-8 BOM — `powershell.exe` would decode it as
Windows-1252 and fail to parse on the customer's machine, before any of its
own error handling could say anything.

Raw equivalents, **any shell with curl**, needs `KREA2_ADMIN_TOKEN`:

```bash
curl -s -H "Authorization: Bearer $KREA2_ADMIN_TOKEN" \
     https://<tag>.vercel.app/v1/admin/builds

curl -s -X POST -H "Authorization: Bearer $KREA2_ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"sha256":"<older sha>","channel":"stable"}' \
     https://<tag>.vercel.app/v1/admin/builds/promote

curl -s https://<tag>.vercel.app/health
```

Presign an R2 URL by hand — **any shell**, needs the four `R2_*`:

```bash
python scripts/r2_presign.py --key builds/<sha256>/krea2app
python scripts/r2_presign.py --key start.ps1 --method PUT --expires 900
```

---

## 8. The licence server

### 8.1 Run it locally

**PS or WSL**, from `license-validator/`, needs `MONGODB_URI`:

```bash
npm start        # node server.js
npm run dev      # node --watch server.js
```

### 8.2 Deploy to Vercel

**PS or WSL**, from `license-validator/`:

```bash
npm i -g vercel
vercel           # first deploy, links the project
vercel --prod
```

Set `MONGODB_URI`, `MONGODB_DB`, `ADMIN_TOKEN` and the four `R2_*` variables
in Project Settings → Environment Variables, then redeploy. `vercel.json`
rewrites every path to `api/index.js`.

Two things the platform will not do for you: run `npm run init-db` yourself
(the serverless path deliberately never creates indexes), and allow Atlas
network access from `0.0.0.0/0` (Vercel's function IPs are not fixed, so an
IP allowlist fails intermittently in a way that looks like a bug in the
service).

### 8.3 Licence keys

**PS or WSL**, from `license-validator/`, needs `MONGODB_URI`:

```bash
npm run issue-key -- --name "Acme Corp" --plan pro --seats 2
npm run issue-key -- --name "Trial" --plan creator --seats 1 --days 30
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --admin --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --no-admin --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --revoke
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --enable
npm run issue-key                    # no args: prints the known plans and features
```

`--plan` and `--features` together are refused as ambiguous. Prefer `--plan`
with `--features-extra` — a literal `features` array is frozen at issue time
and follows no later pricing change.

`--admin` is a **role, not an entitlement**. It grants no tab. It decides
whether the pod captures prompts automatically (ordinary pods do, silently;
admin pods do not) and whether the key may save presets at all.

### 8.4 Catalogue

```bash
npm run seed-catalog
npm run seed-catalog -- --dry-run
```

Idempotent upsert, never deletes. Re-run after editing `FEATURES` in
`src/features.js` or `DEFAULT_PLANS` in `src/plans.js`. Note the direction:
once seeded, the *collection* is what the API reads, so a price or feature
list can be changed in Atlas without a redeploy — and a hand edit there is
reverted by the next seed run unless the code is updated to match.

### 8.5 Prompt library moderation

```bash
npm run prompts                                  # the review queue
npm run prompts -- --list --all
npm run prompts -- --show 65f1...
npm run prompts -- --approve 65f1...
npm run prompts -- --reject  65f1...
npm run prompts -- --add --tab krea_v2_t2i --title "Golden hour portrait" \
                   --prompt "..." --settings ./settings.json
npm run seed-prompts
npm run seed-prompts -- --dry-run
```

### 8.6 Settings presets

```bash
npm run presets                                  # everything
npm run presets -- --tab krea_v2_t2i
npm run presets -- --show 65f1...
npm run presets -- --enable  65f1...
npm run presets -- --disable 65f1...
npm run presets -- --default 65f1...             # what a session opens on
npm run presets -- --order 65f1... --to 10
npm run presets -- --rename 65f1... --to "Portrait, soft"
npm run presets -- --delete  65f1...
npm run presets -- --add --tab krea_t2i --name "Sharp landscape" \
                   --settings ./settings.json
npm run seed-presets
npm run seed-presets -- --dry-run
```

### 8.7 One-off queries against Atlas

For anything the scripts above do not cover. **PS or WSL**, from
`license-validator/` — `src/config.js` loads `.env` itself, so no env setup is
needed. Write a `.mjs` file and run it:

```javascript
// inspect.mjs
import { collections } from "./src/db.js";
import { resolveEntitlement, invalidatePlans } from "./src/plans.js";

const { licenses, plans } = await collections();
const lic = await licenses.findOne({ key: "KREA2-..." });
console.log(lic);
console.log(await resolveEntitlement(lic));   // what /v1/acquire would answer
process.exit(0);
```

```bash
node inspect.mjs
```

`resolveEntitlement` is the same function `/v1/acquire` runs, so it is the
only honest way to confirm what a key actually grants. Call `invalidatePlans()`
after writing a plan, or a warm module cache answers with the old one for up
to 60 seconds.

---

## 9. How a customer launches

### 9.1 RunPod

`scripts/runpod_start.sh` goes in the template's container start command.
Leave `KREA2_LICENSE_KEY` and `KREA2_NODE_TAG` **empty in the template** — a
template is public and every field in it is readable by whoever clones it. The
customer sets them on the pod.

**POD**:

```bash
bash -c 'curl -fsSL https://<tag>.vercel.app/v1/start.sh -o /tmp/krea2-start.sh && exec bash /tmp/krea2-start.sh'
```

Optional on the pod: `KREA2_BASE_DIR` (default `/workspace/krea2` — under the
RunPod volume, so ~90 GB of weights survive a Stop; Terminate destroys it),
and `CIVITAI_TOKEN`.

### 9.2 Windows desktop

**PS**, after setting the two variables:

```powershell
$env:KREA2_LICENSE_KEY="<key>"
$env:KREA2_NODE_TAG="<tag>"
powershell -ExecutionPolicy Bypass -File krea2-start.ps1
```

The `.exe` is **not** self-contained, by design — the app pip-installs torch
and clones ComfyUI at runtime. The machine still needs Python 3.12, git, and
an NVIDIA GPU with a current driver. The script checks for all three.

### 9.3 Docker

**PS**:

```powershell
copy .env.example .env
notepad .env

# or in one line
Set-Content .env "KREA2_LICENSE_KEY=<key>`nKREA2_NODE_TAG=<tag>"

docker compose up --build      # first time: builds the image, then runs
docker compose up              # every time after
docker compose down            # stop. KEEPS the models.
docker compose down -v         # stop and DELETE the ~90 GB volume
```

---

## 10. Docker

### 10.1 First-time checks

Docker Desktop must be running on the **WSL2 backend** with an NVIDIA driver
on the host. The Hyper-V backend cannot pass the GPU through.

```powershell
docker version
```

### 10.2 Seed the volume from models already on disk

Optional, and only **before** the first `up`. Copies ~26 GB so the first boot
downloads nothing. Source is mounted read-only; `cp -an` is no-clobber, so
re-running after downloading more models only copies what is new.

**PS**:

```powershell
docker run --rm `
  -v krea2-data:/workspace `
  -v C:\Adarsh\Personal\Learn\DSA\cp\test\test\tmp\models:/seed:ro `
  alpine sh -c "mkdir -p /workspace/krea2/models && cp -an /seed/. /workspace/krea2/models/"

# check it landed
docker run --rm -v krea2-data:/workspace alpine sh -c "du -sh /workspace/krea2/models; ls /workspace/krea2/models"
```

### 10.3 Run your working tree instead of the published binary

`krea2:dev` is `krea2:latest` plus `requirements.txt`. The repo is bind-mounted
at `/src`, and `KREA2_DEV_SOURCE=/src` makes the entrypoint run `python3 app.py`.
A licence seat is still taken — a dev mode that skipped it would be testing
something nobody runs.

```powershell
docker build -t krea2:latest .
docker compose -f docker-compose.dev.yml up --build

# after the first time
docker compose -f docker-compose.dev.yml up
docker compose -f docker-compose.dev.yml down

docker volume create krea2-data      # if the production service has never run
```

**Do not run `docker-compose.yml` and `docker-compose.dev.yml` at the same
time** — they fight over port 7860 and the GPU.

### 10.4 Build and publish the image

Needs no credentials at all, which is the point: nothing in it is secret, so
anyone with the repo can reproduce it.

**WSL**:

```bash
make image                          # krea2:latest
make image-dev                      # krea2:dev (builds krea2:latest first)
make image-push REGISTRY=ghcr.io/<owner>
```

**PS**, the same without make:

```powershell
docker build -t krea2:latest .
docker build -t krea2:dev --build-arg BASE_IMAGE=krea2:latest -f docker/Dockerfile.dev .
docker tag krea2:latest ghcr.io/<owner>/krea2:latest
docker push ghcr.io/<owner>/krea2:latest
```

The customer then adds one line to their `.env`:

```
KREA2_IMAGE=ghcr.io/<owner>/krea2:latest
```

### 10.5 Poking at it

```powershell
docker compose logs -f
docker compose exec krea2 bash                       # shell in the running container
docker run --rm -it --entrypoint bash krea2:latest   # shell in a fresh one

# what the image was built from
docker run --rm --entrypoint cat krea2:latest /opt/krea2/baked.json

# torch / CUDA the image actually has
docker run --rm --entrypoint python3 krea2:latest -c "import torch; print(torch.__version__, torch.version.cuda)"

# confirm nothing secret got baked in
docker run --rm --entrypoint bash krea2:latest -lc "ls -a /opt/krea2; ls / | grep -i license || echo 'no license-validator: good'"

docker build --no-cache -t krea2:latest .            # ignore the layer cache
docker volume ls
docker volume inspect krea2-data
```

---

## 11. Assets and mirroring

### 11.1 Mirror models to Hugging Face

**POD**, needs `HF_WRITE_TOKEN` and an operator key with every feature granted.
Idempotent and resumable: the mirror repo, not the local disk, decides the work.

```bash
# 1. boot a pod with everything on and let it finish downloading (~200 GB)
KREA2_LICENSE_KEY=<operator key> python3 app.py

# 2. capture pod state
python3 scripts/mirror_to_hf.py --pins-only

# 3. read the checklist
python3 scripts/mirror_to_hf.py --dry-run

# 4. top up and upload (~18 GB — only what is at risk of disappearing)
python3 scripts/mirror_to_hf.py
```

Flags: `--dry-run`, `--audit`, `--pins-only`, `--public`, `--skip-downloads`,
`--include-disabled`, `--only KEY` (repeatable), `--staging PATH`.

What gets mirrored is decided by `scripts/mirror_manifest.json`, never by the
script. Adding a LoRA means editing that JSON.

### 11.2 Showcase images for the pricing page

**PS**, needs `KREA2_BASE_DIR`. The upload also needs `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_SHOWCASE_BUCKET` (the
**public** bucket — `--list`, `--scaffold` and `--dry-run` need none of them):

```powershell
$env:KREA2_BASE_DIR = "C:\Adarsh\Personal\Learn\DSA\cp\test\test\tmp"
python scripts\r2_upload_showcase.py --scaffold      # tree + dummy images
python scripts\r2_upload_showcase.py --list          # every path the page requests
python scripts\r2_upload_showcase.py --dry-run
python scripts\r2_upload_showcase.py                 # upload everything present
python scripts\r2_upload_showcase.py --only krea_t2i
python scripts\r2_upload_showcase.py --force --quality 90
```

Keep the placeholder's **name**, ignore its extension — the stem is what gets
matched, and the file is converted to the catalogue's format on the way up.
A pass-through upload keeps EXIF, so a source photograph can carry a camera
serial and a GPS fix onto a public bucket; the script warns when that happens.

---

## 12. Utilities and troubleshooting

Inspect what is embedded in generated images — **PS or WSL**:

```bash
python3 scripts/inspect_image_metadata.py                  # ComfyUI's input dir
python3 scripts/inspect_image_metadata.py path/to/dir
python3 scripts/inspect_image_metadata.py a.png b.jpg
python3 scripts/inspect_image_metadata.py --full           # no truncation
python3 scripts/inspect_image_metadata.py --json           # machine-readable
```

Free port 7860 when something is still holding it — **PS**:

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 7860).OwningProcess -Force
```

---

## 13. Git

Per the working agreement on this repo: **a new branch per task, and never
push to the remote** unless explicitly asked.

```bash
git status
git switch -c <branch>
git add -A && git commit
git log --oneline -10
git diff
```

---

## 14. Where the real values live

| File | Holds | Tracked? |
| --- | --- | --- |
| `.env` | The two customer values for Docker Compose | gitignored |
| `.env.example` | Template for the above | committed |
| `license-validator/.env` | Mongo URI, admin token, both R2 token pairs, node tag | gitignored |
| `license-validator/.env.example` | Template for the above | committed |
| `commands.txt` | Scratch notes holding live keys and tokens | gitignored |

If a credential from any of these is ever pasted into a tracked file, rotate
it rather than deleting the line — the value is in the history from that
commit onward.
