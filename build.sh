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
# Publishing uploads to a private Cloudflare R2 bucket and then registers
# the build with the licence API, which is what points a channel at it.
# Both halves need environment variables, and --no-publish needs none of
# them:
#
#   R2_ACCOUNT_ID          Cloudflare account id
#   R2_ACCESS_KEY_ID       R2 API token, Object Read & Write on the bucket
#   R2_SECRET_ACCESS_KEY   ... its secret
#   R2_BUILDS_BUCKET       krea2-builds
#   KREA2_NODE_TAG         the deployment id — the same tag pods carry.
#                          The API is https://<tag>.vercel.app, assembled
#                          here exactly as config.py assembles it there
#   KREA2_ADMIN_TOKEN      the ADMIN_TOKEN set on that deployment
#
#   KREA2_BUILD_CHANNEL    which channel to point at this build
#                          (default "stable"; set it to something else to
#                          upload without customers getting it)
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
# Two objects go to one PRIVATE Cloudflare R2 bucket:
#
#   builds/<sha256>/krea2app   the binary, addressed by its own hash
#   start.sh                   scripts/runpod_start.sh, at a fixed key
#
# Neither is reachable without going through the licence API. A pod asks
# /v1/build with its key and gets a short-lived signed URL back, so a
# lapsed or revoked key cannot pull a new build at all; /v1/start.sh
# redirects to a signed URL for the script, which is what the RunPod
# template's one-line bootstrapper fetches.
#
# start.sh is published rather than pasted into the template because a
# template is cloned once and is then out of reach — a fix to a pasted
# script never reaches anyone who already has one. The template holds only
# the bootstrapper, so the start script stays as updatable as the binary.
# RunPod's start-command field also caps at 4000 characters, which the
# script has long outgrown.
#
# What stops a stranger *running* this build is still the seat check in
# licensing.py, not where the bytes are kept — see the note at the top of
# that module. Gating the download stops a lapsed key getting a new build
# and shows which machines pull on which key. It does not stop a binary
# someone already has from being copied.
#
# The binary is content-addressed, so publishing never overwrites: this
# adds a build and then moves a channel pointer to it. Rolling back is
# moving that pointer again, with nothing re-uploaded. start.sh is the one
# thing written in place, because the redirect has to find it at a key
# that does not change.
#
# Uploading and registering are separate steps on purpose. Bytes in the
# bucket that no channel points at are harmless, so an upload that
# succeeds and a register that fails leaves nothing broken and the retry
# re-transfers nothing.

ARTIFACT="$OUTPUT_DIR/$OUTPUT_NAME"
START_SCRIPT="scripts/runpod_start.sh"

# Where the freshly uploaded build gets registered, assembled from the same
# node tag the app and the start script use rather than from a URL of its
# own. There is only ever one deployment to talk to, so a second variable
# naming it would be a second thing to keep in step — and the one that is
# wrong is always the one you forget you set.
#
# Normalised and validated exactly as config.py and runpod_start.sh do it,
# so a tag that works on a pod works here.
NODE_TAG="${KREA2_NODE_TAG:-}"
NODE_TAG="${NODE_TAG//[[:space:]]/}"
NODE_TAG="${NODE_TAG,,}"
API_URL=""
[[ "$NODE_TAG" =~ ^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$ ]] \
    && API_URL="https://$NODE_TAG.vercel.app"

CHANNEL="${KREA2_BUILD_CHANNEL:-stable}"

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

# The R2 credential here must be an Object Read & Write token. It is the
# only place one exists — the licence API holds a read-only token and can
# therefore never overwrite a published build, which is the whole point of
# keeping the two apart.
r2_missing=()
for var in R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY \
           R2_BUILDS_BUCKET; do
    [[ -n "${!var:-}" ]] || r2_missing+=("$var")
done
if (( ${#r2_missing[@]} )); then
    publish_unavailable "\
Not publishing: ${r2_missing[*]} unset.
The binary goes to a private R2 bucket. Create an R2 API token with
Object Read & Write on that bucket and export:

    R2_ACCOUNT_ID          your Cloudflare account id
    R2_ACCESS_KEY_ID       from the R2 API token
    R2_SECRET_ACCESS_KEY   from the R2 API token
    R2_BUILDS_BUCKET       krea2-builds"
fi

# Exported rather than assumed: the check above sees a plain shell
# variable, but r2_presign.py runs as a child process and only ever sees
# the environment. Setting one without exporting it would pass the check
# and then fail inside the signer, complaining the value is unset.
export R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUILDS_BUCKET

# Separated from the token check below, because "unset" and "set to
# something that is not a tag" send you to different places and collapsing
# them would have you checking a value that is right there and correct.
if [[ -z "$API_URL" ]]; then
    if [[ -z "${KREA2_NODE_TAG:-}" ]]; then
        publish_unavailable "\
Not publishing: KREA2_NODE_TAG is unset.
It names the deployment to register this build with — the same tag the
pods carry. Export it and re-run with --upload-only:

    KREA2_NODE_TAG     the deployment id (a single name, no dots or
                       slashes, as it appears in <name>.vercel.app)"
    fi
    publish_unavailable "\
Not publishing: KREA2_NODE_TAG is not a valid deployment id.
It has to be one name as it appears in <name>.vercel.app — no dots, no
slashes, no https:// prefix. Got: $KREA2_NODE_TAG"
fi

if [[ -z "${KREA2_ADMIN_TOKEN:-}" ]]; then
    publish_unavailable "\
Not publishing: KREA2_ADMIN_TOKEN is unset.
Uploading the binary without registering it would put bytes in the bucket
that nothing points at — pods would keep running the previous build and
nothing would say why. Export it and re-run with --upload-only:

    KREA2_ADMIN_TOKEN  the ADMIN_TOKEN set on $API_URL"
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

cat <<EOF

    bucket    r2://$R2_BUILDS_BUCKET   (PRIVATE)
    binary    builds/$sha/$OUTPUT_NAME
              $(numfmt --to=iec --suffix=B "$size" 2>/dev/null || echo "$size bytes")
    start.sh  start.sh  (from $START_SCRIPT, overwritten in place)
    register  $API_URL  ->  channel "$CHANNEL"
    source    ${commit:-unknown}${branch:+ on $branch}$dirty

  The binary is addressed by its own hash, so this adds a build rather
  than replacing one. What changes is where "$CHANNEL" points. Pods pick
  that up on their next start — anything already running is unaffected,
  and every previous build stays in the bucket to roll back to.

EOF

if (( ASSUME_YES )); then
    echo ">>> Publishing (-y) ..."
elif [[ ! -t 0 ]]; then
    publish_unavailable "\
Not publishing: no terminal to confirm on. Re-run with -y to publish
non-interactively."
else
    read -r -p "  Publish to r2://$R2_BUILDS_BUCKET? [y/N] " answer
    case "$answer" in
        [yY]|[yY][eE][sS]) ;;
        *) echo; echo "Not published. $ARTIFACT is built and waiting."; exit 0 ;;
    esac
fi

# Upload with a presigned PUT rather than an SDK. The signing is ~80 lines
# of stdlib in scripts/r2_presign.py, against pulling boto3 or the aws CLI
# onto every machine that builds. One PUT is enough: R2 takes a single
# object up to 5 GB and this is a few hundred megabytes.
upload() {
    local file="$1" key="$2" url stats size speed seconds
    url="$("$PYTHON" scripts/r2_presign.py --key "$key" --method PUT \
           --expires 3600)" || return 1

    # --progress-bar draws to stderr while the transfer runs; -w prints the
    # totals to stdout when it finishes. Both, because the bar is the thing
    # that reassures you during a slow upload and it is also the thing that
    # renders as nothing on a fast one — leaving you unable to tell a
    # hundred megabytes that flew by from a no-op. The totals always print.
    stats="$(curl -fSL --progress-bar --retry 3 --retry-delay 5 \
             -w '%{size_upload} %{speed_upload} %{time_total}' \
             -T "$file" "$url")" || return 1

    read -r size speed seconds <<<"$stats"
    # numfmt wants integers and curl reports bytes-per-second as a float.
    printf '    sent %s in %ss (%s/s)\n' \
        "$(numfmt --to=iec --suffix=B "$size" 2>/dev/null || echo "$size bytes")" \
        "$seconds" \
        "$(numfmt --to=iec --suffix=B "${speed%.*}" 2>/dev/null || echo "$speed")"

    # A PUT that uploaded nothing is not a success, whatever the status
    # code said. Worth its own check: an empty or unreadable artifact would
    # otherwise publish as a perfectly valid zero-byte build, and the first
    # thing to notice would be a customer's pod failing to execute it.
    if [[ "$size" == "0" ]]; then
        echo "    ERROR: nothing was uploaded — $file is empty or unreadable" >&2
        return 1
    fi
}

echo ">>> Uploading $OUTPUT_NAME ..."
upload "$ARTIFACT" "builds/$sha/$OUTPUT_NAME" || {
    echo >&2
    echo "ERROR: uploading the binary to R2 failed." >&2
    echo "       Check R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY are an" >&2
    echo "       Object Read & Write token for $R2_BUILDS_BUCKET." >&2
    exit 1
}

echo ">>> Uploading start.sh ..."
upload "$START_SCRIPT" "start.sh" || {
    echo >&2
    echo "ERROR: uploading start.sh to R2 failed. The binary is already up" >&2
    echo "       at builds/$sha/$OUTPUT_NAME — re-run with --upload-only," >&2
    echo "       which re-transfers it but breaks nothing." >&2
    exit 1
}

# Registering is what actually publishes: until this runs, the bytes are in
# the bucket and no channel points at them, so pods carry on running the
# previous build.
echo ">>> Registering the build ..."
KREA2_REG_SHA="$sha" \
KREA2_REG_SIZE="$size" \
KREA2_REG_COMMIT="$commit" \
KREA2_REG_BRANCH="$branch" \
KREA2_REG_CHANNEL="$CHANNEL" \
KREA2_REG_URL="$API_URL" \
KREA2_ADMIN_TOKEN="$KREA2_ADMIN_TOKEN" \
"$PYTHON" - <<'PY' || { echo >&2 "ERROR: registering the build failed."; exit 1; }
import json, os, platform, sys, time
import urllib.error, urllib.request

body = json.dumps({
    "sha256": os.environ["KREA2_REG_SHA"],
    "size": int(os.environ["KREA2_REG_SIZE"]),
    "git_commit": os.environ.get("KREA2_REG_COMMIT") or None,
    "git_branch": os.environ.get("KREA2_REG_BRANCH") or None,
    "arch": f"{platform.system().lower()}-{platform.machine()}",
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "promote": os.environ["KREA2_REG_CHANNEL"],
}).encode()

request = urllib.request.Request(
    f"{os.environ['KREA2_REG_URL'].rstrip('/')}/v1/admin/builds",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ['KREA2_ADMIN_TOKEN']}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        json.load(response)
except urllib.error.HTTPError as err:
    detail = (err.read() or b"").decode(errors="replace")[:400]
    # 401 here is the single most likely way this step fails, and it is
    # worth naming: the bytes are already uploaded, so the fix is one
    # variable and a --upload-only re-run, not a rebuild.
    print(f"  HTTP {err.code} from the API: {detail}", file=sys.stderr)
    if err.code == 401:
        print("  KREA2_ADMIN_TOKEN does not match the ADMIN_TOKEN set on "
              "that deployment.", file=sys.stderr)
    raise SystemExit(1)
except Exception as err:
    print(f"  could not reach {os.environ['KREA2_REG_URL']}: {err}",
          file=sys.stderr)
    raise SystemExit(1)
PY

cat <<EOF

>>> Published: builds/$sha/$OUTPUT_NAME  ->  channel "$CHANNEL"

    The RunPod template's container start command. It never changes, so a
    template picks up every future build and every fix to the start script
    on its next start — \$KREA2_NODE_TAG is expanded on the pod, from the
    same variable the licence check already needs:

      bash -c 'curl -fsSL https://\$KREA2_NODE_TAG.vercel.app/v1/start.sh -o /tmp/krea2-start.sh && exec bash /tmp/krea2-start.sh'

    To roll back, every build stays in the bucket and in the builds
    collection. List them and move the channel — no re-upload, and pods
    take it on their next start:

      curl -s -H "Authorization: Bearer \$KREA2_ADMIN_TOKEN" \\
           $API_URL/v1/admin/builds
      curl -s -X POST -H "Authorization: Bearer \$KREA2_ADMIN_TOKEN" \\
           -H 'Content-Type: application/json' \\
           -d '{"sha256":"<older sha>","channel":"$CHANNEL"}' \\
           $API_URL/v1/admin/builds/promote

    To hold one customer on a specific build, set build_sha on their
    licence document instead — it wins over the channel.

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
    `# The pricing page's feature showcase — the copy only. A few KB of` \
    `# JSON, read at runtime from config.ASSETS_DIR (= PROJECT_DIR/assets,` \
    `# i.e. inside the extraction dir). Leaving it out is not fatal: the page` \
    `# falls back to the plan cards alone and logs why.` \
    `#` \
    `# The screenshots it names are deliberately NOT bundled. They are served` \
    `# from the public R2 bucket in KREA2_SHOWCASE_URL and fetched by the` \
    `# customer's browser, which keeps a page of forty pictures out of a` \
    `# onefile binary that is re-extracted on every launch — and means a new` \
    `# screenshot is an upload rather than a release.` \
    --include-data-files=assets/showcase/showcase.json=assets/showcase/showcase.json \
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