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

PYTHON="${PYTHON:-python3}"
OUTPUT_DIR="dist"
OUTPUT_NAME="krea2app"

# None of this ships in the RunPod PyTorch image, and pods are ephemeral, so a
# fresh pod needs all of it. Each tool is checked on its own: gcc being present
# does not mean patchelf is. Keep the build one command either way.
echo ">>> Ensuring build tooling ..."
missing=()
command -v gcc      >/dev/null 2>&1 || missing+=(build-essential)
command -v patchelf >/dev/null 2>&1 || missing+=(patchelf)  # Nuitka rewrites RPATHs
command -v ccache   >/dev/null 2>&1 || missing+=(ccache)    # what makes rebuilds fast
if (( ${#missing[@]} )); then
    echo "    apt-get install: ${missing[*]}"
    apt-get update -qq
    apt-get install -y -qq "${missing[@]}"
else
    echo "    system tooling already present"
fi

"$PYTHON" -c "import nuitka" 2>/dev/null || {
    echo "    pip install nuitka (not shipped in the RunPod image) ..."
    "$PYTHON" -m pip install -q nuitka
}

# The packages compiled into the binary must be importable here. A normal
# `python3 app.py` run on this pod has already installed them; this is only a
# safety net for a pod that has never run the app.
echo ">>> Checking the app's own dependencies are present ..."
"$PYTHON" - <<'PY' || "$PYTHON" -m pip install -q -r requirements.txt
import importlib
for m in ("gradio", "huggingface_hub", "requests", "safetensors",
          "websocket", "PIL"):
    importlib.import_module(m)
PY

echo ">>> Compiling (first build is slow — it compiles gradio's tree too) ..."
"$PYTHON" -m nuitka \
    --standalone \
    --onefile \
    --assume-yes-for-downloads \
    --jobs="$(nproc)" \
    --output-dir="$OUTPUT_DIR" \
    --output-filename="$OUTPUT_NAME" \
    --remove-output \
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
    `# Re-extracting ~150 MB on every start is wasteful; cache it per build.` \
    --onefile-tempdir-spec="{CACHE_DIR}/krea2app/{VERSION}" \
    \
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
