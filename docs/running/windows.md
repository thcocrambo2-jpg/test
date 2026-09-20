# Running on Windows

For someone changing the code on a Windows machine with an NVIDIA GPU:
`python app.py` against a checkout. The last section covers what a Windows
*customer* runs instead — the compiled `.exe`, which never sees a
repository. The requirements are the same either way, because the `.exe`
bundles the app and nothing else.

The app targets a pod, but it runs locally. Three things differ from a pod,
and the app handles all three:

- **No PyTorch base image.** On a pod torch arrives with
  `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. Locally nothing
  supplies it, so `setup.ensure_torch()` installs the same versions on the
  first run.
- **`EMBER_BASE_DIR` has no useful default.** It falls back to the pod path
  `/workspace/ember`, which on Windows silently resolves to
  `C:\workspace\ember`. Set it.
- **Symlinks need permission.** `setup.link_model_dirs()` symlinks
  ComfyUI's model folders. Turn on Developer Mode (Settings → System → For
  developers) or run from an elevated shell.

## 1. Environment

PowerShell:

```powershell
conda create -n krea2 python=3.12 -y
conda activate krea2
```

Python 3.12 because that is what Ubuntu 24.04 ships, so it is the version
the pod is tested on. If `conda activate` does nothing, run
`conda init powershell` once and reopen the terminal.

`git` must be on PATH — ComfyUI is cloned, not vendored (PowerShell):

```powershell
git --version                       # if this fails:
conda install -c conda-forge git -y
```

## 2. Configuration

Pick a directory with **45+ GB free**, outside the repo (`dist/` is the
Nuitka build output and is git-ignored, so a `git clean -xdf` would take
your weights with it). PowerShell:

```powershell
$env:EMBER_BASE_DIR    = "C:\ember"
$env:EMBER_LICENSE_KEY = "<your key>"
$env:EMBER_NODE_TAG    = "<your node tag>"
$env:HF_TOKEN          = "<hf read token>"   # optional, avoids rate limits
```

Those last as long as the terminal. To persist one (PowerShell):

```powershell
[Environment]::SetEnvironmentVariable("EMBER_BASE_DIR", "C:\ember", "User")
```

Never commit a file containing these — the licence key and node tag are
credentials. Every variable the app reads is in
[Configuration](../configuration.md).

## 3. Run

PowerShell:

```powershell
python app.py
```

Nothing else to install. The first run takes a while: it installs PyTorch
if absent, clones ComfyUI and its requirements, downloads whatever weights
the licence's features need, starts ComfyUI, then serves the UI. Every step
is idempotent, so an interrupted run resumes rather than restarting.

**The public URL on Windows comes from cloudflared**, exactly as it does on
a pod — one code path, both platforms. cloudflared is fetched once (~55 MB,
`cloudflared-windows-amd64.exe`) and cached under `EMBER_BASE_DIR` by
`scripts/windows_start.ps1`, from the pinned Hugging Face mirror, before
the app starts. So the usual case never touches github.com, and the app's
own download of it (`serve.cloudflared_binary()`) stays as the fallback.

If your DNS resolver is slow to pick up a freshly minted hostname — some
mobile hotspots cache the NXDOMAIN — the tunnel is up before the name
resolves, and the first load can fail for a minute. The local port works
throughout.

How much it downloads depends entirely on the licence — see
[Licensing and features](../architecture/licensing-and-features.md). A
`krea_t2i` + `gallery` licence pulls ~31 GB; adding `wan_i2v` pulls tens of
GB more.

For UI work you need none of this: `scripts/dryrun.py` stubs out ComfyUI
and needs no GPU, no licence and no weights. See [Dry run](dry-run.md).

## PyTorch

`setup.ensure_torch()` runs before anything else installs packages, because
ComfyUI's own `requirements.txt` lists `torch` unpinned — on a machine
without one, that pip pass would take whatever PyPI defaults to.

**Whatever is already installed wins.** The check is capability-based, not
version-matching: torch imports, CUDA is available, this card's `sm_XX` is
in the build's arch list, and a real matmul runs on the GPU. A pod on a
different torch than the pins passes untouched. `setup.TORCH_STACK` and
`setup.TORCH_INDEX` are used only when torch is missing entirely.

That last check matters on Blackwell cards (RTX 50xx, `sm_120`), which have
no kernels before CUDA 12.8. A default PyPI wheel installs cleanly, imports
cleanly, reports `cuda.is_available()` as `True`, and then fails at the
first generation — so version strings are not trusted and a kernel is
actually launched.

If torch is present but unusable, the app **raises and names the fix**
rather than replacing it: on a pod that torch is the tested base image.
Only `torchvision`/`torchaudio` are auto-repaired, with `--no-deps` so
torch cannot be moved.

To use a different CUDA line, install it yourself before the first run and
`ensure_torch()` will accept it (PowerShell):

```powershell
pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

Pin all three. They ship compiled extensions linked against each other's
ABI, and `torchaudio`'s version tracks torch's exactly.

## SageAttention

After the installs and before ComfyUI starts,
`setup.install_sageattention()` puts in the SageAttention build the
installed torch can load, runs a real attention kernel on the GPU, and only
if that works starts every ComfyUI instance with `--use-sage-attention`. It
never changes torch, and a failure anywhere costs speed, not the app.

| Installed torch | SageAttention | Source |
| --- | --- | --- |
| 2.8.0 (`runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`) | 1.0.6 | PyPI — Triton kernels, no compiled extension |
| 2.11.0, CUDA 13.0 (the Docker image) | 2.2.0 | the wheel MiniMax template v8 bakes (a comfyui-runtime release asset) |
| anything else, or Windows | none | — |

Every download is sha256-pinned. It needs a card whose compute capability
is in `setup.SAGE_ARCHES` — A100, A40/A6000/3090, L40S/4090, H100/H200,
RTX 50xx/PRO 6000 — and skips T4, V100 and B200. Measured on an RTX 5050 at
16k–64k tokens against PyTorch attention: 1.8–1.9× with 1.0.6 on torch 2.8,
2.5–2.9× with 2.2 on torch 2.11. A healthy start logs:

```
SageAttention 1.0.6 OK on sm_120 — ComfyUI starts with --use-sage-attention
```

It is approximate attention and it applies to every tab. If a tab's output
looks wrong on a card where it did not before, set `EMBER_SAGE_ATTENTION=0`
first.

## Hardware

The models are sized for a pod, so a consumer card is the constraint:

| | Needed |
| --- | --- |
| VRAM | The Krea 2 UNet alone is ~13 GB. Below that, ComfyUI's single-GPU path offloads the remainder to system RAM — it works, but expect minutes per image rather than seconds. |
| System RAM | Whatever does not fit in VRAM streams from RAM every step. With 8 GB of VRAM, budget ~15 GB free; less means swapping. |
| Disk | ~31 GB for `krea_t2i`, plus the ComfyUI checkout. |

Close memory-hungry applications before generating. The download phase does
not care, so the practical order is: start the download, free RAM before
the first generation.

## What a Windows customer runs

A customer never has a checkout. `scripts/windows_start.ps1` is published
as `ember-start.ps1`; it fetches the current build, verifies it, and runs
it. After setting the two variables (PowerShell):

```powershell
$env:EMBER_LICENSE_KEY="<key>"
$env:EMBER_NODE_TAG="<tag>"
powershell -ExecutionPolicy Bypass -File ember-start.ps1
```

The `.exe` is **not** self-contained, by design: the app never imports
torch or ComfyUI, it pip-installs them and runs ComfyUI as a separate
process. So the machine still needs Python 3.12, git, and an NVIDIA GPU
with a current driver. The script refuses to go on without `curl.exe`,
`python` (with a working `pip`) and `git`, and probes whether it can
create a symlink before anything downloads. The GPU itself is checked
later, by `setup.ensure_torch()`, which launches a real kernel on it.
There is no credential in the script — the same reasoning as on
[RunPod](runpod.md#how-a-customer-launches).

`EMBER_BASE_DIR` and `CIVITAI_TOKEN` are the only other variables worth
setting on a customer machine.

## Next

- [Configuration](../configuration.md)
- [Troubleshooting](../troubleshooting.md)
- [Building the Windows binary](../releasing/build-windows.md)
