# Command reference

Every command this project is driven by, the shell each one runs in, and
the environment variables each one needs. Most sections are a one-line
pointer at the page that explains the thing; what stays here in full is
what has no better home: the shell conventions, first-time setup, git,
and where the real credentials live.

> **No secrets in this file.** Every credential below is written as a
> `<placeholder>`. The real values live in three gitignored files:
> `.env`, `license-validator/.env`, and `commands.txt`. Keep it that way —
> this file is not gitignored.

---

# Quick reference

The four commands reached for most often. Each is repeated in full, with
its caveats, on the page named beside it.

## Launch

### Linux — a GPU pod ([running on RunPod](../running/runpod.md))

```bash
# what a customer runs: fetch the published build and start it
EMBER_LICENSE_KEY=<key> EMBER_NODE_TAG=<tag> \
  bash -c 'curl -fsSL https://<tag>.vercel.app/v1/start.sh -o /tmp/ember-start.sh && exec bash /tmp/ember-start.sh'

# from source, on a pod that already has the repo
EMBER_LICENSE_KEY=<key> EMBER_NODE_TAG=<tag> python3 app.py

# no GPU, no ComfyUI, no weights — just the UI
EMBER_LICENSE_KEY=<key> EMBER_NODE_TAG=<tag> python3 scripts/dryrun.py
```

### Windows — desktop ([running on Windows](../running/windows.md))

```powershell
# what a customer runs
$env:EMBER_LICENSE_KEY="<key>"
$env:EMBER_NODE_TAG="<tag>"
powershell -ExecutionPolicy Bypass -File ember-start.ps1

# no GPU, no ComfyUI, no weights — just the UI
$env:EMBER_LICENSE_KEY="<key>"; $env:EMBER_NODE_TAG="<tag>"; .\.venv\Scripts\python.exe scripts\dryrun.py
```

Needs Python 3.12, git and an NVIDIA GPU on the machine — the `.exe` is
not self-contained. Docker instead:
[running under Docker](../running/docker.md).

## Build

Nuitka does not cross-compile: each platform builds its own artifact.

### Linux — `dist/ember`, on a **POD** or **WSL** ([build-linux](../releasing/build-linux.md))

```bash
./build.sh --no-publish    # build only, needs no credentials
./build.sh                 # build, then offer to publish
./build.sh -y              # build and publish without asking
./build.sh --upload-only   # publish the existing artifact, compile nothing

make compile               # check-args, then ./build.sh --no-publish
make release               # check-args, compile, publish
```

### Windows — `dist\ember.exe`, on **PS** ([build-windows](../releasing/build-windows.md))

```powershell
.\build.ps1                    # compile only — publishing is OFF by default here
.\build.ps1 -Publish           # ... then ask before publishing
.\build.ps1 -Publish -Yes      # ... and publish without asking
.\build.ps1 -UploadOnly        # publish the existing .exe, compile nothing
```

Publishing (either platform) needs `R2_ACCOUNT_ID`, the **write** R2
token, `R2_BUILDS_BUCKET`, `EMBER_NODE_TAG` and `EMBER_ADMIN_TOKEN` —
`make` loads all of them from `license-validator/.env`. A plain build
needs none.

---

## 0. Conventions

Three shells appear in this project. They are not interchangeable, and
the env-var syntax differs in each.

| Tag | What it is | Where you run it |
| --- | --- | --- |
| **PS** | Windows PowerShell 5.1 | Windows, repo root |
| **WSL** | Ubuntu 24.04 under WSL2 (bash) | Windows, via `wsl` |
| **POD** | RunPod / Linux shell (bash) | the GPU pod |

Setting an env var for one command:

```powershell
# PS
$env:EMBER_LICENSE_KEY="<key>"; python scripts\dryrun.py
```

```bash
# WSL / POD
EMBER_LICENSE_KEY=<key> python3 scripts/dryrun.py
# or for a whole session:
export EMBER_LICENSE_KEY=<key>
```

**`make` is WSL/POD only.** The Makefile declares `SHELL := /bin/bash`
and uses `source`, arrays and `${!indirect}` — it will not run under
PowerShell or dash. The Windows build has no make target; it is
`.\build.ps1`.

Repo root on this machine: `C:\Adarsh\Personal\Learn\DSA\cp\test\test`
(as `/mnt/c/Adarsh/Personal/Learn/DSA/cp/test/test` from WSL).

---

## 1. Environment variables

→ [configuration](../configuration.md), which lists every variable the
app reads, plus the operator and build-time ones.

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
system Python fails with `externally-managed-environment`. The RunPod
image disables this, which is why the pod does not need one.

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
npm run seed-assets      # models, LoRAs, and what each Krea tab offers
npm run seed-presets     # starting presets (needs seed-assets first)
npm run seed-prompts     # starter prompts (needs seed-assets first)
```

---

## 3. Run the app locally (no GPU, no ComfyUI, no weights)

→ [running a dry run](../running/dry-run.md).

---

## 4. Front end (React)

→ [the web UI](../architecture/web-ui.md), and
[publishing](../releasing/publishing.md#the-react-bundle) for why the
bundle is committed.

---

## 5. Pre-flight checks

→ [the checks](../development/checks.md).

---

## 6. Building

→ [build-linux](../releasing/build-linux.md) and
[build-windows](../releasing/build-windows.md).

---

## 7. Publishing and rollback

→ [publishing](../releasing/publishing.md).

---

## 8. The licence server

→ [`license-validator/README.md`](../../license-validator/README.md) for
running it locally, deploying it and the CORS rules, and the pages
beside it for the rest:

- [Plans and entitlements](../../license-validator/docs/plans-and-entitlements.md) — plans, resolution order, feature keys
- [Endpoints](../../license-validator/docs/endpoints.md) — every route and the status-code contract
- [Catalogue data](../../license-validator/docs/catalogue-data.md) — models, LoRAs and settings blobs
- [Build distribution](../../license-validator/docs/build-distribution.md) — R2, channels and the download cap
- [Telegram bot](../../license-validator/docs/telegram-bot.md) — setup, commands and the sweep
- [Data](../../license-validator/docs/data.md) — every collection, field by field

---

## 9. How a customer launches

→ [running on RunPod](../running/runpod.md),
[on Windows](../running/windows.md),
[under Docker](../running/docker.md).

---

## 10. Docker

→ [running under Docker](../running/docker.md).

---

## 11. Assets and mirroring

### 11.1 Mirror models to Hugging Face

→ [the mirror and the pins](../releasing/mirror-and-pins.md).

### 11.2 Showcase images for the pricing page

What the showcase is and how the page decides what to render is in
[Pricing page](../features/pricing-page.md); these are the commands.

**PS**, needs `EMBER_BASE_DIR`. The upload also needs `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_SHOWCASE_BUCKET` (the
**public** bucket — `--list`, `--scaffold` and `--dry-run` need none of
them):

```powershell
$env:EMBER_BASE_DIR = "C:\Adarsh\Personal\Learn\DSA\cp\test\test\tmp"
python scripts\r2_upload_showcase.py --scaffold      # tree + dummy images
python scripts\r2_upload_showcase.py --list          # every path the page requests
python scripts\r2_upload_showcase.py --dry-run
python scripts\r2_upload_showcase.py                 # upload everything present
python scripts\r2_upload_showcase.py --only krea_t2i
python scripts\r2_upload_showcase.py --force --quality 90
```

Keep the placeholder's **name**, ignore its extension — the stem is what
gets matched, and the file is converted to the catalogue's format on the
way up. A pass-through upload keeps EXIF, so a source photograph can
carry a camera serial and a GPS fix onto a public bucket; the script
warns when that happens.

---

## 12. Utilities and troubleshooting

→ [troubleshooting](../troubleshooting.md).

---

## 13. Git

Per the working agreement on this repo: **a new branch per task, and
never push to the remote** unless explicitly asked.

```bash
git status
git switch -c <branch>
git add -A && git commit
git log --oneline -10
git diff
```

See [conventions](../development/conventions.md) for what goes in a
commit message and why moves and edits are separate commits.

---

## 14. Where the real values live

| File | Holds | Tracked? |
| --- | --- | --- |
| `.env` | The two customer values for Docker Compose | gitignored |
| `.env.example` | Template for the above | committed |
| `license-validator/.env` | Mongo URI, admin token, both R2 token pairs, node tag | gitignored |
| `license-validator/.env.example` | Template for the above | committed |
| `commands.txt` | Scratch notes holding live keys and tokens | gitignored |

If a credential from any of these is ever pasted into a tracked file,
rotate it rather than deleting the line — the value is in the history
from that commit onward.
