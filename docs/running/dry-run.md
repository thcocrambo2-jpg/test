# Running without a GPU

For someone changing the code or the front end: `scripts/dryrun.py` serves
the whole interface with no GPU, no ComfyUI, no weights and — if you want —
no licence. It is an operator tool, not part of the shipped app.

The point is to look at the UI: which tabs a licence actually grants, how
they lay out, what the controls do, without a pod, without ~90 GB of
downloads, and without a card that can hold a 35 GB UNet.

## Running it

Offline, forcing a set of tabs — PowerShell:

```powershell
python scripts\dryrun.py --features all
python scripts\dryrun.py --features "krea_t2i,wan_i2v"
```

bash (WSL or a pod):

```bash
python3 scripts/dryrun.py --features all
```

Against the real licence server, which verifies the whole entitlement
path — key → features array → which tabs get built — and reads the model
and LoRA catalogue from the same server, so the dropdowns show what the
database holds. It takes a seat for as long as it runs and gives it back
on Ctrl-C. PowerShell:

```powershell
$env:KREA2_LICENSE_KEY="<key>"; $env:KREA2_NODE_TAG="<tag>"; python scripts\dryrun.py
```

bash:

```bash
KREA2_LICENSE_KEY=<key> KREA2_NODE_TAG=<tag> python3 scripts/dryrun.py
```

| Flag | Effect |
| --- | --- |
| `--features LIST\|all` | skip the licence server and force this set of feature keys |
| `--port N` | default 7860 |
| `--tunnel` | open a Cloudflare quick tunnel for a public URL |
| `--api-only` | accepted and ignored; there is one app and this script has always served it |

Without `--features` the tabs come from your licence key. With it, nothing
is asked of the server at all and no seat is spent.

## What it serves, and from where

It binds `http://127.0.0.1:7860` and serves the React front end out of the
committed `ember/web/webui_bundle.py` — the same source the shipped binary
uses, so a fresh clone with no `webui/node_modules` runs this immediately.
`webui/dist` is the fallback when that module is absent.

**The bundle wins over `webui/dist`,** so an edit to `webui/src` does *not*
reach the dry run until `make webui` regenerates the module. That is the one
asymmetry worth remembering, and the startup line says which source was
used.

While changing the front end itself, run Vite alongside and use Vite's port
instead — you get hot reload, and it proxies `/api`, `/media` and `/thumbs`
back to the Python process. PowerShell, two terminals:

```powershell
python scripts\dryrun.py --features all      # one terminal
cd webui; npm run dev                        # another
```

Authentication is off, which is exactly why the script refuses to bind
anything but loopback: an app that asks for no token should not be
reachable from the next desk. Set `KREA2_UI_REQUIRE_TOKEN=1` to put the
gate back — see [Configuration](../configuration.md).

## Where the catalogue comes from

The model and LoRA dropdowns are read from the catalogue, never from disk,
which is what makes a weightless run possible at all. Every model and LoRA
is simply reported as not downloaded.

- With a licence key: `POST /v1/catalog` on the licence server.
- With `--features`: `license-validator/data/assets.json`, the seed
  document, unless `KREA2_CATALOG_FILE` already names another file. Point
  that at a saved server answer to reproduce exactly what one licence sees.

`KREA2_BASE_DIR` defaults to `.dryrun/` under the repo, so the empty
models/output tree lands somewhere local rather than at the pod path.

## What it deliberately does not do

The ComfyUI clone and pip install, `downloads.download_everything()` and
the ComfyUI server itself are all skipped, and `ember.comfy.server` is
replaced with a stub. So every tab renders and every control works, but
pressing Generate reports that ComfyUI is not running. That is the point.

## Mocked weights in `tmp2/`

To exercise the download path and the "already downloaded" state without
fetching anything, `scripts/mock_models.py` writes a zero-byte file at
exactly the path the real code checks. bash:

```bash
KREA2_BASE_DIR=tmp2 python3 scripts/mock_models.py          # create the placeholders
KREA2_BASE_DIR=tmp2 python3 scripts/mock_models.py --check  # what would a real run still fetch?
```

PowerShell:

```powershell
$env:KREA2_BASE_DIR="tmp2"; python scripts\mock_models.py
```

It runs every asset group in `downloads.ASSET_GROUPS` with the network
stubbed out, so the next real run logs `✓ (cached)` for all of it. Existing
files are never touched — a real weight stays a real weight, and a re-run
only adds what a configuration change introduced. What it created is
appended to `<KREA2_BASE_DIR>/mock-manifest.txt`, so the placeholders can be
told apart later. `--check` installs stubs that fail instead and exits 1 if
anything would still go to the network, which doubles as "is this base
directory complete?".

Two things to know before running it:

- **Always pass `KREA2_BASE_DIR` explicitly.** It otherwise defaults to
  `.dryrun`, and mock weights there change which text encoder
  `pipelines.common.active_text_encoder()` picks, which fails
  `scripts/golden.py --check`.
- **The only argument it understands is `--check`.** Anything else — a
  `--help`, a typo — is treated as "go" and creates the placeholders.

A generation fails on an empty file, of course: this is for exercising the
app, not the models. `tmp2/` is also where a local ComfyUI checkout with
mocked weights lives, for validating the golden workflows — see
[Checks](../development/checks.md).

On a Windows console, set `PYTHONIOENCODING=utf-8` before running either
script, or the ✓ in their output raises.

## Next

- [Configuration](../configuration.md) — every environment variable
- [Troubleshooting](../troubleshooting.md)
- [Windows](windows.md) — the same checkout, with a real GPU
- [Checks](../development/checks.md) — what each `scripts/check_*.py` guards
