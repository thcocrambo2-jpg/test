#!/usr/bin/env bash
#
# Build the single-file artifact with Nuitka.
#
# RUN THIS ON THE POD, not on Windows. Nuitka emits native code for the OS it
# runs on (a Windows build would produce a .exe), and a standalone binary links
# against the build machine's glibc and will not start on an older one.
# Building where you deploy makes both problems disappear by construction.
#
# The build needs no GPU: Nuitka compiles source rather than executing it, so
# comfy.py's import-time GPU check never fires here.
#
# What ends up inside: this app plus everything it imports (gradio,
# huggingface_hub, requests, safetensors, websocket-client, Pillow).
# What does NOT: torch and ComfyUI — the app never imports them, it installs
# them at runtime and launches ComfyUI as a separate process. That is why
# bootstrap.runtime_python() exists.
#
#   ./build.sh            -> dist/krea2app
#
set -euo pipefail

cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "ERROR: build on Linux (the pod). Nuitka cannot cross-compile, so" >&2
    echo "       building here would produce a binary for $(uname -s)." >&2
    exit 1
fi

# Nuitka patches RPATHs into the bundled .so files and restores their modes
# with chmod. WSL's DrvFs mounts (/mnt/c) cannot store Unix modes unless the
# 'metadata' option is set, and chmod then fails with EPERM — minutes into the
# build, with a traceback that never mentions the mount.
#
# Probe the real capability rather than pattern-matching the path, so a
# /mnt/c mounted with metadata is allowed through and the pod is unaffected.
probe="$(mktemp -p . .buildprobe.XXXXXX)"
if ! chmod 0400 "$probe" 2>/dev/null || ! chmod 0600 "$probe" 2>/dev/null; then
    rm -f "$probe"
    here=$(basename "$PWD")
    cat >&2 <<EOF
ERROR: cannot change file permissions in $PWD

       Nuitka needs chmod to set RPATHs on the bundled libraries and would
       fail partway through the build with 'Operation not permitted'.
       This is a WSL Windows-drive mount without Unix metadata. Either:

       1) enable metadata and keep building here (still slower than native):

            printf '[automount]\\noptions = "metadata"\\n' | sudo tee /etc/wsl.conf
            # then, from PowerShell:   wsl --shutdown     and reopen WSL

       2) or build from the WSL filesystem, which is also much faster:

            cp -r "$PWD" ~/$here
            cd ~/$here && ./build.sh
EOF
    exit 1
fi
rm -f "$probe"

PYTHON="${PYTHON:-python3}"
OUTPUT_DIR="dist"
OUTPUT_NAME="krea2app"

# None of this ships in the RunPod PyTorch image, and pods are ephemeral, so a
# fresh pod needs all of it. Each tool is checked on its own: gcc being present
# does not mean patchelf is. Keep the build one command either way.
echo ">>> Ensuring build tooling ..."

# On Debian-family Python, Nuitka links a *static* libpython and needs the
# development headers to do it. The RunPod ML image ships them; a stock WSL
# Ubuntu does not, and the failure is an unhelpful
# "Automatic detection of static libpython failed".
# Probe INCLUDEPY (the base interpreter's include dir) rather than
# sysconfig.get_paths(), whose 'include' is empty inside a venv.
have_python_headers() {
    "$PYTHON" - <<'PY' >/dev/null 2>&1
import os, sys, sysconfig
sys.exit(0 if os.path.exists(
    os.path.join(sysconfig.get_config_var("INCLUDEPY"), "Python.h")) else 1)
PY
}

missing=()
command -v gcc      >/dev/null 2>&1 || missing+=(build-essential)
command -v patchelf >/dev/null 2>&1 || missing+=(patchelf)  # Nuitka rewrites RPATHs
command -v ccache   >/dev/null 2>&1 || missing+=(ccache)    # what makes rebuilds fast
have_python_headers                 || missing+=(python3-dev)
if (( ${#missing[@]} )); then
    # Root in a RunPod container, but a normal user under WSL — so only
    # reach for sudo when we actually need it.
    SUDO=""
    if [[ $EUID -ne 0 ]]; then
        command -v sudo >/dev/null 2>&1 || {
            echo "ERROR: need root (or sudo) to install: ${missing[*]}" >&2
            exit 1
        }
        SUDO="sudo"
    fi
    echo "    apt-get install: ${missing[*]}"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq "${missing[@]}"
else
    echo "    system tooling already present"
fi

"$PYTHON" -c "import nuitka" 2>/dev/null || {
    echo "    pip install nuitka (not shipped in the RunPod image) ..."
    "$PYTHON" -m pip install -q nuitka
}

# If the headers are still missing (no sudo, or the distro names the package
# differently), fall back to a shared libpython instead of failing the build:
# --standalone bundles libpython either way, so this only changes how it links.
libpython_flag=()
if have_python_headers; then
    echo "    Python headers present — linking static libpython"
else
    echo "    no Python headers — linking shared libpython instead"
    libpython_flag=(--static-libpython=no)
fi

# Nuitka can only compile in what it can import, so these must be present in
# the build interpreter. A pod that has already run `python3 app.py` has them;
# a fresh one does not.
echo ">>> Checking the app's own dependencies are present ..."
if "$PYTHON" -c "import gradio, huggingface_hub, requests, safetensors, websocket, PIL" \
        >/dev/null 2>&1; then
    echo "    all present"
else
    echo "    missing — installing from requirements.txt ..."
    "$PYTHON" -m pip install -q -r requirements.txt
    "$PYTHON" -c "import gradio, huggingface_hub, requests, safetensors, websocket, PIL" || {
        echo "ERROR: dependencies still not importable after install." >&2
        exit 1
    }
    echo "    installed"
fi

echo ">>> Compiling (first build is slow — it compiles gradio's tree too) ..."
"$PYTHON" -m nuitka \
    --standalone \
    --onefile \
    --assume-yes-for-downloads \
    --jobs="$(nproc)" \
    --output-dir="$OUTPUT_DIR" \
    --output-filename="$OUTPUT_NAME" \
    --remove-output \
    ${libpython_flag[@]+"${libpython_flag[@]}"} \
    \
    `# Bundled next to __file__, which is where config.PROJECT_DIR points.` \
    `# deps/ carries the vendored ReActor pack that install_reactor() copies` \
    `# into custom_nodes instead of cloning from GitHub.` \
    --include-data-dir=deps=deps \
    --include-data-files=requirements.txt=requirements.txt \
    \
    `# gradio is pulled in whole rather than by import graph: it loads` \
    `# components dynamically, and its frontend ships as package data.` \
    `# The metadata flags matter because gradio looks up its own version at` \
    `# runtime, which fails in a frozen app without them.` \
    --include-package=gradio \
    --include-package-data=gradio \
    --include-distribution-metadata=gradio \
    --include-package=gradio_client \
    --include-package-data=gradio_client \
    --include-distribution-metadata=gradio_client \
    \
    `# No --onefile-tempdir-spec on purpose: a fixed cache dir lets a rebuilt` \
    `# binary silently reuse the previous extraction. Re-extracting on each` \
    `# launch costs seconds, against an app that then loads 13 GB of models.` \
    app.py

echo
echo ">>> Built: $OUTPUT_DIR/$OUTPUT_NAME"
echo ">>> Sanity check — no source should ship:"
if strings "$OUTPUT_DIR/$OUTPUT_NAME" | grep -q "def generate_single"; then
    echo "    WARNING: found app source text in the binary" >&2
else
    echo "    OK: no app source found"
fi
echo
echo "Ship $OUTPUT_DIR/$OUTPUT_NAME. It needs python3, git, an NVIDIA driver"
echo "and disk on the target pod — it installs ComfyUI and downloads models"
echo "on first run, exactly like 'python3 app.py' does."
