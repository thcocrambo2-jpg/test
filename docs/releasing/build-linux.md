# Building the Linux binary

For whoever ships a build. `build.sh` compiles the app into one
self-contained executable, `dist/ember`, so it can be handed to
someone without shipping the source. Uploading that artifact is a
separate step — see [publishing](publishing.md).

Nuitka translates Python to C and compiles it to **native machine code**,
unlike PyInstaller, which ships `.pyc` bytecode that decompiles back to
near-original source.

## Where to build

**Build on the pod, not on Windows.** Nuitka cannot cross-compile, and a
standalone binary links against the build machine's glibc and will not
start on an older one; building where you deploy sidesteps both. The
build needs no GPU — Nuitka compiles source rather than running it, so
the import-time GPU check in `ember/comfy/server.py` never fires — and
`build.sh` installs `nuitka` and a compiler if the pod lacks them.

This page is about the **Linux** artifact. The Windows `.exe` is the same
idea run on the other side of the same wall: `.\build.ps1` on a Windows
machine, because the inability to cross-compile cuts both ways. See
[Building the Windows binary](build-windows.md).

## What the binary contains

| Inside the binary | Installed on first run |
| --- | --- |
| this app's code (compiled) | ComfyUI (`git clone`) |
| fastapi, uvicorn, huggingface_hub, requests, safetensors, websocket-client, Pillow | torch + ComfyUI's requirements |
| the compiled React bundle (`ember/web/webui_bundle.py`) | ~90 GB of models |

ComfyUI's dependencies *cannot* be compiled in: the app never imports
them, and ComfyUI runs as a **separate process with its own
interpreter**, so it needs them in the pod's system Python. That is also
what keeps the artifact around 100–200 MB instead of multi-gigabyte. The
target pod needs `python3`, `git`, an NVIDIA driver and disk — not the
CUDA toolkit, since torch's wheels ship their own CUDA libraries.

If the binary comes out unexpectedly large, the first thing to check is
the build interpreter's environment: Nuitka walks whatever is installed
next to it, so an env carrying torch or matplotlib produces a much bigger
graph than a venv holding only `requirements.txt`.

## What keeps compiled and uncompiled identical

- **`settings.FROZEN`** — the single authoritative "am I compiled?" check
  (Nuitka injects `__compiled__` into every module). Exactly **two**
  places may branch on it, both in `ember/comfy/setup.py`:
  `runtime_python()` and the app-requirements skip in `install_comfyui()`.
  Any third branch is a way for the shipped binary to diverge from what
  you test.
- **`ember.comfy.setup.runtime_python()`** — compiled, `sys.executable` is
  *the binary*, so the sites that run `pip` and launch ComfyUI would
  otherwise pass nonsense arguments to themselves. It resolves the system
  `python3` when frozen and returns `sys.executable` otherwise, so
  **`python3 app.py` keeps working exactly as before**. That is the
  development path and must stay intact.

Day to day nothing changes: keep running `python3 app.py`. Build only
when you want to hand over an artifact. Nuitka caches the C compilation,
so the first build is the slow one. If you add a dependency that works
under `python3 app.py` but fails in the binary, the usual cause is
package *data* files: add `--include-package-data=<pkg>` in `build.sh`.

## Building on the pod

**POD** (bash):

```bash
cd /test
git pull
bash build.sh          # or chmod +x build.sh && ./build.sh
```

`bash build.sh` avoids needing the executable bit, which git does not
carry when the file is committed from Windows. The script installs
whatever the pod lacks — `build-essential`, `patchelf`, `ccache`,
`nuitka`, `zstandard` — none of which ship in the RunPod image, and all
of which are gone again on a fresh pod. Output is `dist/ember`.

The flags:

```bash
./build.sh --no-publish    # build only — needs no credentials
./build.sh                 # build, then offer to publish
./build.sh -y              # build and publish without asking
./build.sh --upload-only   # publish the existing dist/ember, compile nothing
./build.sh --help
```

Point it at a specific interpreter with `PYTHON=`:

```bash
PYTHON=~/build-venv/bin/python3 ./build.sh --no-publish
```

Via make — **WSL or POD**, and it runs the pre-flight checks first:

```bash
make compile        # check-args, then ./build.sh --no-publish
```

### Smoke-testing it

Smoke-test on the same pod, which is the fastest check available: ComfyUI
and the models are already on disk, so the setup step skips everything
and goes straight to serving.

```bash
./dist/ember
```

Expect `ComfyUI already present … — skipping clone`, `ComfyUI API on port
8188 is ready` and, on a licence granting a Krea 2 V2 tab, `Custom node
ClownsharKSampler_Beta is registered`. A **fresh** pod is the real
end-to-end test — only that exercises the clone and download paths.

## Building locally in WSL2 (optional)

The pod image is `runpod/pytorch:…-ubuntu2404` → glibc 2.39, Python 3.12,
so **WSL2 with Ubuntu 24.04 matches it exactly** and its output runs on
the pod. Ubuntu 22.04 (glibc 2.35) also works and is safer, since older
glibc runs on newer hosts but not the reverse. The build needs no GPU, no
CUDA and no torch.

**PS** (PowerShell):

```powershell
wsl --install -d Ubuntu-24.04     # once
wsl -d Ubuntu-24.04               # every time, to open a shell in it
```

**Always pass `-d Ubuntu-24.04`.** Bare `wsl` opens whatever distro is
*default*, which on a machine with Docker Desktop or Rancher Desktop is
their bundled one — recognisable by a `#` root prompt, no `sudo`, and
Windows drives at `/mnt/host/c/…` instead of `/mnt/c/…`. Each distro has
its own filesystem, so the venv and `/etc/wsl.conf` below exist only
inside Ubuntu-24.04. Check with `wsl --list --verbose` (`*` marks the
default) or make it the default once:

```powershell
wsl --set-default Ubuntu-24.04
```

**WSL** (bash):

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone <this repo> && cd test
python3 -m venv ~/build-venv && source ~/build-venv/bin/activate
pip install -r requirements.txt
./build.sh
```

Use a venv: Ubuntu 24.04 enforces PEP 668, so installing into the system
Python fails with `externally-managed-environment`. The RunPod image
disables this, which is why the pod does not need one. `build.sh` uses
whichever `python3` is active, so an activated venv is picked up
automatically, and it prefixes `sudo` when not running as root.

### Building under `/mnt/c/…` needs one extra setting

WSL mounts Windows drives with DrvFs, which cannot store Unix file modes
by default, so `chmod` fails with `EPERM`. Nuitka patches RPATHs into the
bundled `.so` files and restores their modes afterwards, and dies there —
minutes into the compile, with a `PermissionError` traceback that never
mentions the mount. `build.sh` probes for this up front (it tries a real
`chmod`, rather than guessing from the path) and stops in a second with
both fixes printed. Either:

**WSL**:

```bash
# 1) allow Unix modes on Windows drives, and keep building where you are
printf '[automount]\noptions = "metadata"\n' | sudo tee /etc/wsl.conf
#    then, from PowerShell:  wsl --shutdown    (wait ~8s, then reopen)
mount | grep ' /mnt/c '                # verify
git config core.fileMode false         # if it produces spurious mode diffs
```

```bash
# 2) or build from the WSL filesystem, which is also much faster
cp -r /mnt/c/…/test ~/test && cd ~/test && ./build.sh
```

Option 2 is the better default: WSL2 reaches `/mnt/c` over 9p, and this
build touches on the order of a thousand C files. Enabling `metadata`
also makes git notice file-mode changes it previously ignored, which is
what the `core.fileMode` line above is for.

**The trade-off:** the pod keeps the build environment identical *by
construction*, while WSL matches it *by version*. If RunPod bumps its base
image past Ubuntu 24.04, a WSL-built binary may stop starting, and the
symptom — a glibc error at exec — is obscure.

## Getting the binary off the pod

`scp` over RunPod's SSH proxy often fails (`ssh.runpod.io` is a terminal
proxy, not a full SSH server), so try it first and fall back.

Locally (bash):

```bash
# works only if the proxy supports SCP
scp -i ~/.ssh/id_ed25519 <user>@ssh.runpod.io:/test/dist/ember .
```

**POD** then locally (bash):

```bash
runpodctl send /test/dist/ember      # on the pod — prints a one-time code
runpodctl receive <code>                # locally
```

`runpodctl` ships on pods and is peer-to-peer, so it ignores the SSH
proxy's limitations. Failing both, serve it over the already-exposed app
port while the app is stopped — **POD**:

```bash
cd /test/dist && python3 -m http.server 7860
# then download https://<POD_ID>-7860.proxy.runpod.net/ember
```

The artifact is a **Linux** binary — it will not run on Windows;
downloading is only for redistribution. Whoever receives it needs
`chmod +x ember` first, since the executable bit does not survive most
transfers. For a Windows customer you do not transcode this file, you
build [the other one](build-windows.md).

## Before you build

Everything a build must agree about is checked by `make check-args`, and
both `make compile` and `make release` depend on it — so the Linux build
refuses to run while any of it is out of step. See
[the checks](../development/checks.md) for what each one guards.
