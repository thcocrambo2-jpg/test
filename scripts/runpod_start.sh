#!/usr/bin/env bash
#
# RunPod start script — paste this into the template's container start
# command. It fetches the published build, checks it, and runs it.
#
# The customer sets two environment variables on the pod and nothing else:
#
#     KREA2_LICENSE_KEY   the key they were issued
#     KREA2_NODE_TAG      the deployment id issued with it
#
# Leave both EMPTY in the template. A template is public and every field in
# it is readable by whoever clones it, so a key typed in here is a key
# given to everyone. That is also why the build is fetched from a public
# repo with no credential: there is nowhere in this file a secret could
# safely live. What actually limits who can run the app is the seat check
# the binary makes on startup, not where it was downloaded from.
#
# Optional, for when something is wrong:
#
#     KREA2_BUILD_REV     a Hugging Face revision to pin to (default main)
#     KREA2_BASE_DIR      where models and outputs live (default below)
#     CIVITAI_TOKEN       only if a CivitAI download starts refusing anonymous
#
# Note this REPLACES the image's own /start.sh, so SSH and JupyterLab do
# not come up. That is deliberate for a customer pod — the app's output
# goes to the container log, which is where they read the UI link from.

set -uo pipefail

# Where the build lives. Set at publish time by build.sh; override only to
# point a pod at a different account.
REPO="${KREA2_DIST_REPO:-thcocrambo2/krea2-dist}"
REV="${KREA2_BUILD_REV:-main}"
NAME="krea2app"

# Under /workspace on purpose: that is the RunPod volume, so the ~90 GB of
# weights downloaded on the first run survive a Stop and the second start
# takes minutes instead of an hour. Terminate destroys it and pays for the
# whole download again.
BASE="${KREA2_BASE_DIR:-/workspace/krea2}"
BIN_DIR="$BASE/bin"
BIN="$BIN_DIR/$NAME"
URL="https://huggingface.co/$REPO/resolve/$REV"

say()  { printf '[krea2] %s\n' "$*"; }
die()  { printf '\n[krea2] ERROR: %s\n\n' "$*" >&2; exit 1; }

echo
say "starting — build from $REPO@$REV"

# ── What the customer has to have set ────────────────────────────────────────
# Checked here rather than left to the binary so a missing variable costs a
# few seconds instead of a 300 MB download. The wording matches what
# licensing.py says for the same two problems, so a customer who hits one
# of them later reads the same sentence twice rather than two different
# ones about the same mistake.
[[ -n "${KREA2_LICENSE_KEY:-}" ]] || die "\
No license key found.
       Set KREA2_LICENSE_KEY in this pod's environment variables to the
       key you were given, then start the pod again."

[[ -n "${KREA2_NODE_TAG:-}" ]] || die "\
This pod is missing its node tag.
       Set KREA2_NODE_TAG in this pod's environment variables to the value
       issued with your license key, then start the pod again."

say "license key ${KREA2_LICENSE_KEY:0:10}… · node tag $KREA2_NODE_TAG"

# ── Tooling ──────────────────────────────────────────────────────────────────
# The RunPod PyTorch images ship all of these; a leaner base image might
# not. Checked together because git and python3 are not for this script at
# all — the binary shells out to them to clone ComfyUI and install torch.
# Finding out here costs a second; finding out later costs the 300 MB
# download first and reports it as a failure deep inside the app.
declare -A NEEDS=(
    [curl]=curl
    [sha256sum]=coreutils
    [python3]=python3
    [git]=git
)
missing=()
for tool in "${!NEEDS[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("${NEEDS[$tool]}")
done
if (( ${#missing[@]} )); then
    say "installing: ${missing[*]}"
    apt-get update -qq && apt-get install -y -qq "${missing[@]}" \
        || die "\
could not install ${missing[*]}, which this image is missing and the
       app needs. Try a RunPod PyTorch template — they ship all of it."
fi

mkdir -p "$BIN_DIR" || die "could not create $BIN_DIR"

# ── Which build to run ───────────────────────────────────────────────────────
# latest.json carries the sha256 of the binary sitting next to it, written
# in the same commit. It answers two questions at once: what to verify a
# fresh download against, and whether the copy already on the volume is
# current — which is what makes a restart skip the download entirely and a
# newly published build roll out on the next start with nothing to do here.
say "checking for the current build ..."
meta="$(curl -fsSL --retry 5 --retry-delay 3 --retry-connrefused \
        "$URL/latest.json")"
rc=$?

# curl's 22 means it reached the host and was refused — nothing about this
# pod's network is wrong, the build simply is not where we looked. Worth
# separating: telling a customer to check their internet when the real fix
# is at the supplier's end sends them somewhere they cannot fix anything.
# (Hugging Face answers 401, not 404, for a repo that does not exist, so a
# wrong repo name and an unpublished build land here together.)
if (( rc == 22 )); then
    die "\
no app build was found at $REPO (revision $REV).
       Nothing is wrong with this pod. Contact your supplier — either no
       build has been published yet, or KREA2_BUILD_REV is set to a
       revision that does not exist. Unset it to take the current build."
elif (( rc != 0 )); then
    die "\
could not reach Hugging Face to check for the app build (curl $rc).
       This pod needs outbound internet access. If it has some, the
       download host may be blocked — check with your supplier."
fi

want="$(printf '%s' "$meta" | python3 -c \
    'import json,sys; print(json.load(sys.stdin).get("sha256") or "")' \
    2>/dev/null)"
[[ -n "$want" ]] || die "\
the published build has no checksum, so it cannot be verified.
       This is a problem at the supplier's end, not on this pod."

have=""
[[ -f "$BIN" ]] && have="$(sha256sum "$BIN" | cut -d' ' -f1)"

if [[ "$have" == "$want" ]]; then
    say "already have this build — skipping the download"
else
    [[ -n "$have" ]] && say "a newer build is available — updating"
    say "downloading the app (this takes a moment) ..."
    tmp="$BIN.part"
    # Straight to .part and renamed only once the hash matches: a download
    # cut off by a dropped pod network would otherwise leave a truncated
    # binary that looks installed and fails to start on every future boot.
    curl -fL -sS --retry 5 --retry-delay 3 --retry-connrefused \
        -o "$tmp" "$URL/$NAME"
    rc=$?
    if (( rc != 0 )); then
        rm -f "$tmp"
        # latest.json was readable a moment ago, so a refusal here is not
        # the repo being missing — it is that commit carrying a checksum
        # but no binary beside it.
        (( rc == 22 )) && die "\
the app build at $REPO is incomplete — it publishes a checksum but no
       binary. Nothing is wrong with this pod; contact your supplier."
        die "\
the app download failed (curl $rc) — check this pod's internet access
       and start it again."
    fi

    got="$(sha256sum "$tmp" | cut -d' ' -f1)"
    if [[ "$got" != "$want" ]]; then
        rm -f "$tmp"
        die "\
the downloaded app is damaged (checksum does not match).
       Start the pod again — this is almost always an interrupted
       download and a second attempt fixes it."
    fi
    chmod +x "$tmp" && mv -f "$tmp" "$BIN" \
        || die "could not install the app to $BIN"
    say "downloaded and verified"
fi

# ── Run ──────────────────────────────────────────────────────────────────────
export KREA2_BASE_DIR="$BASE"

cat <<EOF

  Models and outputs live in $BASE — on the pod volume, so they survive
  Stop. Terminating the pod deletes them and the next start downloads
  everything again.

  The first start installs ComfyUI and downloads the models your license
  covers, which takes a while. Watch this log: the app prints the link to
  open the UI when it is ready, between two lines of '='.

EOF

# exec, not a plain call: the binary becomes PID 1, so RunPod's stop sends
# SIGTERM straight to it and licensing.py's handler gives the seat back
# instead of the pod dying with it still held.
exec "$BIN"
