#!/usr/bin/env bash
#
# Container entrypoint. Deliberately thin: it does the two things that are
# true only inside the image, then hands over to the same start script a
# RunPod template downloads from /v1/start.sh. Everything about licences,
# builds and checksums lives there and is not repeated here.
#
#     1. say what this image is made of
#     2. point $KREA2_BASE_DIR/ComfyUI at the baked checkout
#     3. exec /opt/krea2/bin/start.sh
#
# Two escape hatches, both for the case where the baked tree turns out to
# be wrong:
#
#     KREA2_USE_BAKED_COMFY=0   ignore the baked ComfyUI entirely. The app
#                               then clones and pip-installs on its own,
#                               exactly as a non-Docker pod does today.
#     KREA2_START_SOURCE=api    fetch the current start.sh from the licence
#                               server instead of using the baked copy, so
#                               a start-script fix ships without a new
#                               image. Needs KREA2_NODE_TAG, same as ever.

set -uo pipefail

BAKED="/opt/krea2"
BASE="${KREA2_BASE_DIR:-/workspace/krea2}"

say() { printf '[krea2-image] %s\n' "$*"; }

echo
# What is actually in here, from the manifest bake_nodes.py wrote — so a
# container's contents are answerable from its own log rather than from
# remembering which tag was deployed.
if [[ -f "$BAKED/baked.json" ]]; then
    python3 - "$BAKED/baked.json" <<'PY' || true
import json, sys
d = json.load(open(sys.argv[1]))
print("[krea2-image] built %s" % d.get("built_at"))
print("[krea2-image] ComfyUI %s" % (d.get("comfyui_sha") or "?")[:12])
for name, sha in sorted((d.get("custom_nodes") or {}).items()):
    # ASCII on purpose: this runs under whatever locale the base image
    # happens to set, and a UnicodeEncodeError here would swallow the
    # whole manifest over a dash.
    print("[krea2-image]   %-32s %s"
          % (name, (sha[:12] if sha else "NOT BAKED - installed at boot")))
PY
else
    say "no baked.json — this does not look like a Krea 2 image"
fi

mkdir -p "$BASE" || {
    printf '\n[krea2-image] ERROR: could not create %s\n' "$BASE" >&2
    printf '       Mount a volume there, or set KREA2_BASE_DIR.\n\n' >&2
    exit 1
}

# ── The baked ComfyUI ────────────────────────────────────────────────────
# A symlink rather than a copy: ComfyUI's source is a couple of gigabytes
# of small files and $BASE is usually network storage, where copying it is
# minutes on every fresh volume. Nothing that must survive a restart lives
# in there — comfy.py passes --output-directory and --temp-directory, and
# link_model_dirs() symlinks the model folders back out to $BASE/models.
if [[ "${KREA2_USE_BAKED_COMFY:-1}" == "0" ]]; then
    say "KREA2_USE_BAKED_COMFY=0 — ignoring the baked ComfyUI; the app will"
    say "install its own, which is the non-Docker behaviour."
elif [[ -e "$BASE/ComfyUI" && ! -L "$BASE/ComfyUI" ]]; then
    # A volume carrying an install from the current non-Docker flow. Left
    # exactly as it is: bootstrap.install_comfyui() will find main.py and
    # skip, which is what that pod already does today. Replacing someone's
    # working checkout because they switched images would be a change
    # nobody asked for, and their custom nodes are in there.
    say "$BASE/ComfyUI already exists — using it, not the baked copy."
elif ln -sfn "$BAKED/ComfyUI" "$BASE/ComfyUI" 2>/dev/null; then
    say "ComfyUI → $BAKED/ComfyUI (baked into this image)"
else
    # Docker Desktop's file sharing does not reliably support creating
    # symlinks on a bind-mounted Windows folder. Copying is slow but it
    # works, and a boot that takes longer beats a boot that fails with a
    # message about symlinks.
    say "could not symlink $BASE/ComfyUI — copying instead. This is slow;"
    say "a named volume (docker volume create) avoids it."
    cp -a "$BAKED/ComfyUI" "$BASE/ComfyUI" || {
        printf '\n[krea2-image] ERROR: could not install ComfyUI to %s\n\n' \
            "$BASE/ComfyUI" >&2
        exit 1
    }
fi

# ── Dev mode ─────────────────────────────────────────────────────────────
# Run a mounted source tree instead of the published binary. The point is
# the loop it replaces: `docker compose up` runs whatever `make release`
# last shipped, so it cannot test the code you are editing.
#
# The licence check is NOT bypassed — app.py still takes a seat, because
# licensing is the app's behaviour and a dev mode that skipped it would be
# testing something customers never run. Only the *download* is skipped.
if [[ -n "${KREA2_DEV_SOURCE:-}" ]]; then
    if [[ ! -f "$KREA2_DEV_SOURCE/app.py" ]]; then
        printf '\n[krea2-image] ERROR: no app.py in %s\n' "$KREA2_DEV_SOURCE" >&2
        printf '       KREA2_DEV_SOURCE must point at the repo, mounted\n' >&2
        printf '       into the container. See docker-compose.dev.yml.\n\n' >&2
        exit 1
    fi
    say "dev mode — running $KREA2_DEV_SOURCE/app.py"
    say "the licence server is not asked for a build; your code is the build."
    cd "$KREA2_DEV_SOURCE" || exit 1
    # Uncompiled, config.FROZEN is False, so install_comfyui() also installs
    # requirements.txt — the app repairs a dev image that is missing its own
    # dependencies. docker/Dockerfile.dev pre-installs them so that pass is
    # a no-op rather than a minute of pip on every run.
    exec python3 app.py
fi

# ── Which start script ───────────────────────────────────────────────────
START="$BAKED/bin/start.sh"
if [[ "${KREA2_START_SOURCE:-baked}" == "api" ]]; then
    if [[ -z "${KREA2_NODE_TAG:-}" ]]; then
        say "KREA2_START_SOURCE=api needs KREA2_NODE_TAG — using the baked"
        say "start script instead."
    elif curl -fsSL "https://${KREA2_NODE_TAG}.vercel.app/v1/start.sh" \
            -o /tmp/krea2-start.sh; then
        say "using the start script from the licence server"
        START=/tmp/krea2-start.sh
    else
        say "could not fetch the start script — using the baked copy."
    fi
fi

# exec, not a call: start.sh execs the binary in turn, so the app ends up
# as PID 1 and a `docker stop` SIGTERM reaches licensing.py's handler,
# which gives the seat back. A wrapper process in between would swallow it.
exec bash "$START"
