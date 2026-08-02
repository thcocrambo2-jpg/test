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
#   ./build.sh                 -> dist/krea2app, then offer to publish it
#   ./build.sh -y              -> ... and publish without asking
#   ./build.sh --no-publish    -> build only
#   ./build.sh --upload-only   -> publish the dist/krea2app already there,
#                                 compiling nothing
#
set -euo pipefail

cd "$(dirname "$0")"

# Publishing asks by default. -y is for CI or a re-run you have already
# eyeballed; --no-publish is for a build you only want to test locally.
PUBLISH=ask
ASSUME_YES=0
BUILD=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)      ASSUME_YES=1 ;;
        --no-publish)  PUBLISH=no ;;
        --upload-only) BUILD=0 ;;
        -h|--help)
            # The header comment *is* the help, printed to wherever it now
            # ends — a line range here would silently truncate the usage
            # lines the next time something is added above them.
            awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next }
                 NR>1         { exit }' "$0"
            exit 0 ;;
        *)
            echo "unknown option: $1 (try --help)" >&2
            exit 2 ;;
    esac
    shift
done

# Needed by both paths below, so they are set before either runs.
PYTHON="${PYTHON:-python3}"
OUTPUT_DIR="dist"
OUTPUT_NAME="krea2app"

# The Publish section is a function so --upload-only can reach it without
# running the build. Its body is deliberately NOT indented: it is mostly
# heredocs and multi-line message strings whose content is positioned at
# column 0 on purpose, and indenting them would both break the heredoc
# terminators and put leading spaces into everything the operator reads.
publish() {
# ── Publish ───────────────────────────────────────────────────────────────────
#
# Push the binary to a PUBLIC Hugging Face repo, which is what the RunPod
# start script fetches. Public on purpose: that script sits in a template
# customers can read, so any credential it carried would not be one.
#
# What stops a stranger running this build is the seat check in
# licensing.py, not the obscurity of this URL — see the note at the top of
# that module about what the licence does and does not defend against.
#
# Three files go up in ONE commit:
#
#   krea2app      the binary
#   latest.json   its sha256 — the start script verifies against this, so
#                 splitting the two across commits would have a customer
#                 checking a new hash against old bytes and rejecting a
#                 build that is perfectly good
#   start.sh      scripts/runpod_start.sh, which is what pods actually run
#
# start.sh is published rather than pasted into the RunPod template
# because a template is cloned once and is then out of reach: a fix to a
# pasted script never reaches the customers who already have it. Their
# template holds a one-line bootstrapper that fetches this, so the start
# script is as updatable as the binary is. RunPod's start-command field
# also caps at 4000 characters, which the script has already outgrown.

ARTIFACT="$OUTPUT_DIR/$OUTPUT_NAME"
DIST_REPO="${KREA2_DIST_REPO:-krea2-dist}"
START_SCRIPT="scripts/runpod_start.sh"

publish_unavailable() {
    # The build itself succeeded, so this is only fatal when -y said to
    # publish. Without it the operator was going to be asked anyway, and
    # "answer no for them" is the same outcome.
    echo >&2
    echo "$1" >&2
    (( ASSUME_YES )) && exit 1
    exit 0
}

if [[ "$PUBLISH" == "no" ]]; then
    echo
    echo "Not publishing (--no-publish). Ship $ARTIFACT yourself, or re-run"
    echo "this script to push it."
    exit 0
fi

# Missing files are checked before the token, because they are the more
# fundamental problem and reporting them second is actively misleading:
# an --upload-only run against an empty dist/ would otherwise be told its
# token was the thing standing in the way.
#
# Checked at all rather than left to sha256sum, which reports a missing
# file as a read error in the middle of output that is otherwise about
# publishing. This is the normal way --upload-only goes wrong: run from
# the wrong directory, or against a dist/ that was never built.
[[ -f "$ARTIFACT" ]] || {
    echo >&2
    echo "ERROR: no binary at $ARTIFACT — there is nothing to publish." >&2
    echo "       Run ./build.sh without --upload-only to compile one." >&2
    exit 1
}

# Fatal rather than skipped: publishing a binary without the script that
# launches it leaves customer pods fetching a start.sh from the previous
# build, which is the one shape of mismatch nothing downstream can detect.
[[ -f "$START_SCRIPT" ]] || {
    echo >&2
    echo "ERROR: $START_SCRIPT is missing — it is published alongside the" >&2
    echo "       binary and pods fetch it on every start." >&2
    exit 1
}

if [[ -z "${HF_WRITE_TOKEN:-}" ]]; then
    publish_unavailable "\
Not publishing: HF_WRITE_TOKEN is unset.
Set it to a Hugging Face token with *write* scope and re-run to push
$ARTIFACT. Nothing is lost by publishing later — re-run with
--upload-only and it will not compile again.
(Same variable scripts/mirror_to_hf.py uses.)"
fi

echo
echo ">>> Preparing to publish ..."
sha="$(sha256sum "$ARTIFACT" | cut -d' ' -f1)"
size="$(stat -c %s "$ARTIFACT")"
commit="$(git rev-parse --short HEAD 2>/dev/null || echo '')"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
# Only meaningful once we know there is a checkout: outside one, git fails
# for a reason that has nothing to do with the tree being dirty, and
# reporting "uncommitted changes" then is a lie about the build's origin.
dirty=""
if [[ -n "$commit" ]]; then
    git diff --quiet HEAD 2>/dev/null || dirty=" (uncommitted changes)"
fi

# Resolve the account from the token rather than asking for it: a repo id
# typed by hand is a repo id that can be typed wrong, and the failure mode
# is a build published where nothing will look for it.
account="$("$PYTHON" - <<'PY' 2>/dev/null || true
import os
from huggingface_hub import HfApi
try:
    print(HfApi(token=os.environ["HF_WRITE_TOKEN"]).whoami()["name"])
except Exception:
    pass
PY
)"
[[ -n "$account" ]] || publish_unavailable "\
Not publishing: could not authenticate to Hugging Face with HF_WRITE_TOKEN.
Check the token is valid and has write scope."

# Accept either a bare name or a full owner/name in KREA2_DIST_REPO, so
# pushing to an organisation needs no extra flag.
[[ "$DIST_REPO" == */* ]] || DIST_REPO="$account/$DIST_REPO"

cat <<EOF

    repo      https://huggingface.co/$DIST_REPO   (PUBLIC)
    file      $OUTPUT_NAME  ($(numfmt --to=iec --suffix=B "$size" 2>/dev/null || echo "$size bytes"))
    sha256    $sha
    plus      start.sh  (from $START_SCRIPT)
    source    ${commit:-unknown}${branch:+ on $branch}$dirty
    account   $account

  This overwrites the current build at that path. Customer pods pick it up
  on their next start — anything already running is unaffected.

EOF

if (( ASSUME_YES )); then
    echo ">>> Publishing (-y) ..."
elif [[ ! -t 0 ]]; then
    publish_unavailable "\
Not publishing: no terminal to confirm on. Re-run with -y to publish
non-interactively."
else
    read -r -p "  Publish to $DIST_REPO? [y/N] " answer
    case "$answer" in
        [yY]|[yY][eE][sS]) ;;
        *) echo; echo "Not published. $ARTIFACT is built and waiting."; exit 0 ;;
    esac
fi

KREA2_PUB_FILE="$ARTIFACT" \
KREA2_PUB_NAME="$OUTPUT_NAME" \
KREA2_PUB_REPO="$DIST_REPO" \
KREA2_PUB_SHA="$sha" \
KREA2_PUB_COMMIT="$commit" \
KREA2_PUB_BRANCH="$branch" \
KREA2_PUB_START="$START_SCRIPT" \
"$PYTHON" - <<'PY'
import io, json, os, platform, time

from huggingface_hub import CommitOperationAdd, HfApi

path = os.environ["KREA2_PUB_FILE"]
name = os.environ["KREA2_PUB_NAME"]
repo = os.environ["KREA2_PUB_REPO"]

meta = {
    "file": name,
    "sha256": os.environ["KREA2_PUB_SHA"],
    "size": os.path.getsize(path),
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "git_commit": os.environ.get("KREA2_PUB_COMMIT") or None,
    "git_branch": os.environ.get("KREA2_PUB_BRANCH") or None,
    "arch": f"{platform.system().lower()}-{platform.machine()}",
    "_note": ("Published by build.sh. The start script reads sha256 from "
              "here to verify its download and to tell an up-to-date pod "
              "it can skip one."),
}

api = HfApi(token=os.environ["HF_WRITE_TOKEN"])
api.create_repo(repo, repo_type="model", private=False, exist_ok=True)
api.create_commit(
    repo_id=repo,
    repo_type="model",
    commit_message=f"build {meta['git_commit'] or meta['built_at']}",
    operations=[
        CommitOperationAdd(path_in_repo=name, path_or_fileobj=path),
        CommitOperationAdd(
            path_in_repo="start.sh",
            path_or_fileobj=os.environ["KREA2_PUB_START"],
        ),
        CommitOperationAdd(
            path_in_repo="latest.json",
            path_or_fileobj=io.BytesIO(
                json.dumps(meta, indent=2, sort_keys=True).encode() + b"\n"),
        ),
    ],
)
PY

cat <<EOF

>>> Published: https://huggingface.co/$DIST_REPO

    The RunPod template's container start command — this never changes,
    so a template already cloned by a customer picks up every future
    build and every fix to the start script on its next start:

      bash -c 'curl -fsSL https://huggingface.co/$DIST_REPO/resolve/main/start.sh -o /tmp/krea2-start.sh && exec bash /tmp/krea2-start.sh'

    To roll a customer back, every build stays in the repo's history — set
    KREA2_BUILD_REV to that commit's revision on their pod and the start
    script fetches it instead of main.

    The binary needs python3, git, an NVIDIA driver and disk on the target
    pod — it installs ComfyUI and downloads models on first run, exactly
    like 'python3 app.py' does.
EOF

}

if (( ! BUILD )); then
    # Asking for both is a contradiction rather than a no-op worth guessing
    # at — it would compile nothing and publish nothing, and say so only in
    # passing.
    [[ "$PUBLISH" == "no" ]] && {
        echo "--upload-only and --no-publish together would do nothing." >&2
        exit 2
    }
    echo ">>> --upload-only: not compiling, publishing what is in $OUTPUT_DIR/"
    publish
    exit 0
fi

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
    "$PYTHON" -m pip install -q "nuitka[onefile]"
}

# zstandard is what compresses the onefile payload. Plain `pip install
# nuitka` does not pull it, and its absence is only a warning — so the
# build succeeds and quietly produces a much larger binary. That size is
# paid on every cold pod start, by every customer, forever.
"$PYTHON" -c "import zstandard" 2>/dev/null || {
    echo "    pip install zstandard (onefile compression) ..."
    "$PYTHON" -m pip install -q zstandard
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
APP_IMPORTS="import gradio, huggingface_hub, requests, safetensors, websocket, PIL, hf_xet"
if "$PYTHON" -c "$APP_IMPORTS" >/dev/null 2>&1; then
    echo "    all present"
else
    echo "    missing — installing from requirements.txt ..."
    "$PYTHON" -m pip install -q -r requirements.txt
    "$PYTHON" -c "$APP_IMPORTS" || {
        echo "ERROR: dependencies still not importable after install." >&2
        exit 1
    }
    echo "    installed"
fi

# Checked rather than left to Nuitka, and fatal rather than a warning. A
# binary built without these still runs — it just quietly stops using the
# mirror and the pins, which is the one failure here that looks like
# success. It surfaces much later as a customer being asked for a CivitAI
# token, or as weights that changed under a release nobody rebuilt.
echo ">>> Checking the mirror data files are present ..."
for datafile in scripts/mirror_manifest.json scripts/PINS.json; do
    [[ -f "$datafile" ]] || {
        echo "ERROR: $datafile is missing." >&2
        echo "       It is compiled into the binary; without it the app runs" >&2
        echo "       un-mirrored and un-pinned, downloading every weight from" >&2
        echo "       its original upstream. Restore it from git, or run" >&2
        echo "       scripts/mirror_to_hf.py --pins-only to regenerate PINS." >&2
        exit 1
    }
done
echo "    mirror_manifest.json + PINS.json will be bundled"

# hf_xet's metadata, located here because the path carries its version.
#
# Why this is not just --include-distribution-metadata: Nuitka's own
# metadata finder matches distribution names EXACTLY, while
# importlib.metadata treats hf_xet and hf-xet as the same distribution.
# huggingface_hub asks for the underscore spelling (is_package_available
# -> importlib.metadata.version("hf_xet")) and Nuitka can only register
# the hyphen one, so neither spelling of that flag switches Xet on.
#
# Shipping the real .dist-info as a data directory puts it on sys.path,
# where Python's ordinary path-based finder — which does normalise —
# resolves it. Verified with both spellings answering 1.5.2 inside a
# compiled binary.
#
# Fatal, not skipped: without it every Xet-backed download silently drops
# to single-stream HTTP, turning a model fetch of seconds into minutes.
echo ">>> Locating hf_xet's distribution metadata ..."
xet_metadata=()
xet_distinfo="$("$PYTHON" - <<'PY' 2>/dev/null || true
import glob, pathlib
import hf_xet
site = pathlib.Path(hf_xet.__file__).resolve().parent.parent
found = sorted(glob.glob(str(site / "hf_xet-*.dist-info")))
print(found[0] if found else "")
PY
)"
if [[ -n "$xet_distinfo" && -d "$xet_distinfo" ]]; then
    xet_metadata=(--include-data-dir="$xet_distinfo=$(basename "$xet_distinfo")")
    echo "    $(basename "$xet_distinfo") will be bundled"
else
    echo "ERROR: could not find hf_xet's .dist-info next to the installed" >&2
    echo "       package. Without it the binary compiles fine and then runs" >&2
    echo "       every model download over plain HTTP instead of Xet." >&2
    echo "       Try: $PYTHON -m pip install --force-reinstall hf_xet" >&2
    exit 1
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
    `# mirror.py looks for these at PROJECT_DIR first, then PROJECT_DIR/scripts` \
    `# — they live under scripts/ in a checkout so Nuitka never sweeps the` \
    `# operator tooling in, and are flattened to the root here. Without them` \
    `# the binary runs un-mirrored and un-pinned: mirror.location() answers` \
    `# None for everything, so every weight goes to its original upstream and` \
    `# the CivitAI LoRAs start needing a customer CIVITAI_TOKEN that the` \
    `# mirror exists precisely to make unnecessary. It degrades silently —` \
    `# two warnings at startup and then apparently normal behaviour.` \
    --include-data-files=scripts/mirror_manifest.json=mirror_manifest.json \
    --include-data-files=scripts/PINS.json=PINS.json \
    \
    `# huggingface_hub imports hf_xet inside a try/except and only when a` \
    `# repo is Xet-backed, so the import graph does not reach it and the` \
    `# binary silently ships without it. The cost is not subtle: every` \
    `# Xet-backed download drops to single-stream HTTP, which is the whole` \
    `# difference between a model fetch taking seconds and taking minutes.` \
    `# It is a compiled extension, so it must be bundled — a pip install on` \
    `# the pod is invisible to the huggingface_hub inside this bundle.` \
    `#` \
    `# The metadata flag is the one that actually switches Xet on, for the` \
    `# same reason it is needed for gradio below: huggingface_hub does not` \
    `# probe with an import, it calls importlib.metadata.version("hf_xet")` \
    `# in utils/_runtime.py. Without the .dist-info that raises` \
    `# PackageNotFoundError, so the package is compiled in and reported` \
    `# missing — the exact warning the pod logs on every download.` \
    `#` \
    `# Note the two spellings: the importable module is hf_xet, the` \
    `# distribution on PyPI is hf-xet. Nuitka wants each flag given the` \
    `# name that belongs to it and warns rather than fails on the wrong` \
    `# one, so getting this backwards produces a working build that is` \
    `# still missing the metadata Xet detection depends on.` \
    --include-package=hf_xet \
    --include-distribution-metadata=hf-xet \
    ${xet_metadata[@]+"${xet_metadata[@]}"} \
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
    `# Two of gradio's own dependencies read their version from a data file` \
    `# rather than from metadata — safehttpx/__init__.py opens` \
    `# version.txt next to itself at import time, and groovy does the same.` \
    `# Nuitka compiles both packages (the import graph reaches them through` \
    `# gradio.processing_utils) but ships no package data unless told, so` \
    `# the binary starts, downloads every model, and only then dies with` \
    `# FileNotFoundError: .../safehttpx/version.txt — an import-time crash,` \
    `# so no tab of the app ever renders. --include-package-data is enough;` \
    `# neither package looks itself up through importlib.metadata.` \
    --include-package-data=safehttpx \
    --include-package-data=groovy \
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

publish