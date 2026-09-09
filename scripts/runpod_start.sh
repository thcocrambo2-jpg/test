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
# given to everyone. That is also why there is no credential anywhere in
# this file: the build now comes from a private bucket, but the thing that
# opens it is the customer's own licence key, which is already on the pod.
# Nothing here has to be a secret.
#
# What actually limits who can *run* the app is still the seat check the
# binary makes on startup, not where it was downloaded from. Gating the
# download stops a lapsed key fetching a new build and shows which
# machines pull on which key; it does not stop a binary someone already
# has from being copied, and nothing here pretends otherwise.
#
# Optional, for when something is wrong:
#
#     KREA2_BASE_DIR      where models and outputs live (default below)
#     CIVITAI_TOKEN       only if a CivitAI download starts refusing anonymous
#
# There is no longer a KREA2_BUILD_REV. Which build a licence gets is
# decided by the server, so pinning one customer to an older build or
# rolling everybody back is done there — see /v1/admin/builds/promote —
# rather than by talking a customer through editing their pod.
#
# Note this REPLACES the image's own /start.sh, so SSH and JupyterLab do
# not come up. That is deliberate for a customer pod — the app's output
# goes to the container log, which is where they read the UI link from.

set -uo pipefail

NAME="krea2app"

# Under /workspace on purpose: that is the RunPod volume, so the ~90 GB of
# weights downloaded on the first run survive a Stop and the second start
# takes minutes instead of an hour. Terminate destroys it and pays for the
# whole download again.
BASE="${KREA2_BASE_DIR:-/workspace/krea2}"
BIN_DIR="$BASE/bin"
BIN="$BIN_DIR/$NAME"

say()  { printf '[krea2] %s\n' "$*"; }
die()  { printf '\n[krea2] ERROR: %s\n\n' "$*" >&2; exit 1; }

echo
say "starting"

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

# Normalised and then checked exactly as config.py does it, so a tag the
# binary would accept is never rejected here — the two must agree, or a pod
# fails at this step with a message about its tag and then works fine the
# moment someone lowercases it by hand. config.py strips and lowercases;
# whitespace anywhere fails the pattern either way, so removing all of it
# reaches the same verdict more legibly.
NODE_TAG="${KREA2_NODE_TAG//[[:space:]]/}"
NODE_TAG="${NODE_TAG,,}"

# One DNS label and nothing else. The tag is what the endpoint is assembled
# from, so a value carrying a dot, a slash, a colon or a port is a value
# pointing this script at a host of someone else's choosing — which at the
# download step below means running a binary from wherever they nominated.
# Rejected outright rather than sanitised: whatever is left after removing
# the offending characters is not a tag anybody issued.
if [[ ! "$NODE_TAG" =~ ^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$ ]]; then
    die "\
this pod's node tag is not valid.
       Set KREA2_NODE_TAG to the value issued with your license key —
       it is a single name, with no dots, slashes or colons in it."
fi

API="https://$NODE_TAG.vercel.app"

# The pod id, so the server can tell one machine's downloads from another's
# on the same key. Same preference order as licensing.py's instance id, and
# for the same reason: a stable value means a pod that restarts looks like
# itself rather than like a new machine every time.
INSTANCE="${RUNPOD_POD_ID:-${RUNPOD_POD_HOSTNAME:-$(hostname 2>/dev/null || echo unknown)}}"

say "license key ${KREA2_LICENSE_KEY:0:10}… · node tag $NODE_TAG"

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
# The build lives in a private bucket, so this asks the licence server for
# it: send the key and the sha256 of whatever is already on the volume, get
# back either "that is the current one" or a short-lived download URL for
# the one this licence should have.
#
# Sending the hash we already have is what makes a restart free. The server
# mints no signature and writes no download record for a pod that is merely
# rebooting, which keeps the rate limit measuring the thing it is meant to
# measure: distinct machines pulling the binary.
have=""
[[ -f "$BIN" ]] && have="$(sha256sum "$BIN" | cut -d' ' -f1)"

say "checking for the current build ..."

# --fail is deliberately NOT used: a refusal here carries a message written
# for the customer ("this license expired on ..."), and -f would throw the
# body away and leave nothing to print but a number.
payload="$(KEY="$KREA2_LICENSE_KEY" INST="$INSTANCE" HAVE="$have" python3 -c '
import json, os
print(json.dumps({
    "license_key": os.environ["KEY"],
    "instance_id": os.environ["INST"],
    "current_sha": os.environ["HAVE"] or None,
}))')"

resp="$(curl -sS --retry 5 --retry-delay 3 --retry-connrefused \
        -X POST -H "Content-Type: application/json" \
        --data "$payload" -w '\n%{http_code}' "$API/v1/build" 2>/dev/null)"
rc=$?
code="${resp##*$'\n'}"
body="${resp%$'\n'*}"

# One parse, five lines out, fixed order — so a message containing spaces
# or quotes cannot turn into extra shell words.
parsed="$(printf '%s' "$body" | python3 -c '
import json, sys
# Pinned rather than left to the platform: on Windows print() emits CRLF,
# and read -r keeps the CR, so every field would carry an invisible
# trailing character. The pod is Linux and would never hit it — this is so
# the block can be exercised on a development machine and behave there
# exactly as it does here.
sys.stdout.reconfigure(newline="\n")
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
b = d.get("build") or {}
for value in (
    "1" if d.get("up_to_date") else "",
    b.get("sha256") or "",
    b.get("url") or "",
    " ".join(str(d.get("message") or "").split()),
):
    print(value)
' 2>/dev/null)"
{ read -r up_to_date; read -r want; read -r url; IFS= read -r message; } \
    <<<"$parsed"

if (( rc != 0 )) || [[ "$code" != "200" ]]; then
    # Anything that is not a clean 200 — unreachable, expired, revoked,
    # rate limited, nothing published — takes the same branch, because the
    # right thing to do about all of them is identical: if there is a
    # verified build already on this volume, run it and let the seat check
    # in the binary deliver the real verdict. That check is the enforcement
    # anyway, its message is the one worth reading, and it means a licence
    # server having a bad afternoon does not brick a pod that has
    # everything it needs to run.
    if [[ -n "$have" ]]; then
        say "could not check for a newer build${message:+ — $message}"
        say "running the build already on this pod"
    elif [[ -n "$message" ]]; then
        die "$message"
    elif (( rc != 0 )); then
        die "\
could not reach the license server to fetch the app (curl $rc).
       This pod needs outbound internet access. If it has some, the
       server may be blocked — check with your supplier."
    else
        die "\
the license server refused to hand out the app (HTTP $code).
       Nothing is wrong with this pod; contact your supplier."
    fi
elif [[ -n "$up_to_date" ]]; then
    say "already have this build — skipping the download"
else
    [[ -n "$want" && -n "$url" ]] || die "\
the license server did not say which build to run.
       This is a problem at the supplier's end, not on this pod."

    [[ -n "$have" ]] && say "a newer build is available — updating"
    say "downloading the app (this takes a moment) ..."
    tmp="$BIN.part"
    # Removed first: a .part left by a pod killed mid-download belongs to
    # whatever build was current then, and resuming onto it would append
    # new bytes to old ones. The checksum below would catch it, but only
    # after another full download, and on every boot from then on.
    rm -f "$tmp"
    # Straight to .part and renamed only once the hash matches: a download
    # cut off by a dropped pod network would otherwise leave a truncated
    # binary that looks installed and fails to start on every future boot.
    curl -fL -sS --retry 5 --retry-delay 3 --retry-connrefused \
        -o "$tmp" "$url"
    rc=$?
    if (( rc != 0 )); then
        rm -f "$tmp"
        # 22 is the signed URL being refused. The realistic cause is a pod
        # slow enough that it expired mid-attempt, and starting again gets
        # a fresh one — so say that rather than sending them to support.
        (( rc == 22 )) && die "\
the app download link was refused, which usually means it expired
       before the download finished. Start the pod again to get a new one."
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

# ── The tunnel helper ────────────────────────────────────────────────────────
# serve.py needs cloudflared to hand the customer a public URL, and since
# the Gradio UI was removed it is the ONLY thing that produces one. Left to
# itself the app fetches it from the GitHub "latest" release on first
# launch, which makes one github.com endpoint a hard dependency of every
# first start - and the failure mode is a customer with no link at all.
#
# So it is fetched here instead, from the same public Hugging Face mirror
# the weights come from, pinned to a release and checked against a known
# sha256. serve.cloudflared_binary() returns early when the file is already
# at KREA2_BASE_DIR, so putting it there is the entire change: no recompile,
# no new build published, just this script.
#
# EVERY failure below is a note rather than a hard stop. If the mirror is
# unreachable, or the bytes are wrong, the app still starts and still falls
# back to the GitHub release exactly as it does today - so this can only
# ever add a source, never take one away.
#
# Refreshing to a newer cloudflared is scripts/mirror_cloudflared.py, then
# the three constants below, then `make start-ps1` / `make start-sh`.
CF_BIN="$BASE/cloudflared"
CF_URL="https://huggingface.co/thcocrambo2/krea2-tools/resolve/ba22c6ba0afc0c9f9b644c133310903831bbc17b/cloudflared/2026.8.3/cloudflared-linux-amd64"
CF_SHA="f29324fe934d1e100617484c78deef803c4dc2cd351d645bbde42e96b4fccc5e"

# Not re-verified when it is already there: serve.py checks only that the
# file exists, and hashing 40 MB on every boot to reach the same verdict
# would be the most expensive line in this script.
if [[ ! -f "$CF_BIN" ]]; then
    say "fetching the tunnel helper (once) ..."
    cf_tmp="$CF_BIN.part"
    rm -f "$cf_tmp"
    curl -fL -sS --retry 3 --retry-delay 2 --retry-connrefused \
        -o "$cf_tmp" "$CF_URL"
    cf_rc=$?
    if (( cf_rc != 0 )); then
        rm -f "$cf_tmp"
        say "note: could not fetch the tunnel helper (curl $cf_rc) — the app"
        say "      will download it from its original source."
    else
        cf_got="$(sha256sum "$cf_tmp" | cut -d' ' -f1)"
        if [[ "$cf_got" != "$CF_SHA" ]]; then
            rm -f "$cf_tmp"
            say "note: the tunnel helper arrived damaged — the app will"
            say "      download it from its original source."
        # chmod before the rename, not after: serve.py chmods only the copy
        # it downloads itself, so a file arriving here non-executable would
        # stay that way. Renamed only once it is both verified and runnable,
        # because serve.py trusts its mere existence.
        elif chmod +x "$cf_tmp" && mv -f "$cf_tmp" "$CF_BIN"; then
            say "tunnel helper ready"
        else
            rm -f "$cf_tmp"
            say "note: could not install the tunnel helper to $CF_BIN — the"
            say "      app will download it from its original source."
        fi
    fi
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
