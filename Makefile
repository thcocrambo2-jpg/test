# Build, publish and roll back the Krea 2 app.
#
# Everything reads license-validator/.env, so there is one file holding
# credentials and no six-variable command line to retype after a reboot.
# That file is gitignored; nothing here writes to it.
#
#     make              what you can run
#     make check        verify the credentials without using them
#     make publish      upload dist/krea2app and point "stable" at it
#     make release      compile, then do the same
#
# Run it from WSL or a Linux pod. The recipes are bash — `source`, arrays
# and ${!indirect} are all used below — so this will not run under dash.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := help

ENV_FILE := license-validator/.env
ARTIFACT := dist/krea2app

# The pod's start script. Published by build.sh as part of a full
# release, but it is a plain file at a fixed key and a fix to it should
# not need a recompile of the binary it happens to launch. See start-sh.
POD_START := scripts/runpod_start.sh

# The Windows half of the pair. Nothing here builds it — Nuitka does not
# cross-compile, so `.\build.ps1` has to run on Windows — but the start
# script it publishes is a plain file, and pushing a fix to that should not
# require finding a Windows machine. See the start-ps1 target.
WIN_START := scripts/windows_start.ps1

# The Docker image (Dockerfile, docker-compose.yml). Unrelated to the
# artifact above and to $(ENV_FILE): it carries no credential and no
# licensed code, which is what makes it publishable. Running it is
# `docker compose up`, so there is no target for that here.
IMAGE    := krea2
TAG      := latest
REGISTRY :=

# What every recipe needs: the file, the deployment it talks to, and the
# token that opens its admin routes.
#
# Deliberately does NOT require the R2 write credentials. Listing builds
# and moving a channel are pure API calls, and the moment you most need
# them — something shipped broken and you are rolling it back — is exactly
# when you might be on a machine that has never built anything. Demanding
# an upload credential to perform a rollback would put the credential in
# the way of the emergency.
define LOAD_ENV
if [[ ! -f "$(ENV_FILE)" ]]; then
    echo "ERROR: $(ENV_FILE) not found." >&2
    echo "       Copy license-validator/.env.example and fill it in." >&2
    exit 1
fi
set -a
source "$(ENV_FILE)"
set +a

read_key="$${R2_ACCESS_KEY_ID:-}"
export KREA2_ADMIN_TOKEN="$${ADMIN_TOKEN:-}"

missing=()
for name in KREA2_NODE_TAG ADMIN_TOKEN; do
    [[ -n "$${!name:-}" ]] || missing+=("$$name")
done
if (( $${#missing[@]} )); then
    echo "ERROR: unset in $(ENV_FILE): $${missing[*]}" >&2
    exit 1
fi

API="https://$$KREA2_NODE_TAG.vercel.app"
endef

# The extra half that only uploading needs.
#
# build.sh wants R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY; .env holds two
# pairs under different names, because the service that reads that file
# must only ever get the read-only one. Resolving the names here means the
# write token is used exactly where it is needed and nowhere else, and
# neither side has to know the other's naming.
define LOAD_WRITE
export R2_ACCESS_KEY_ID="$${R2_WRITE_ACCESS_KEY_ID:-}"
export R2_SECRET_ACCESS_KEY="$${R2_WRITE_SECRET_ACCESS_KEY:-}"

missing=()
for name in R2_ACCOUNT_ID R2_WRITE_ACCESS_KEY_ID R2_WRITE_SECRET_ACCESS_KEY \
            R2_BUILDS_BUCKET; do
    [[ -n "$${!name:-}" ]] || missing+=("$$name")
done
if (( $${#missing[@]} )); then
    echo "ERROR: unset in $(ENV_FILE): $${missing[*]}" >&2
    echo "       These are only needed to upload. 'make builds', 'make" >&2
    echo "       promote' and 'make health' work without them." >&2
    exit 1
fi

# The read token is what the deployment holds, so finding it here means
# the write fields were filled in from the wrong screen. Caught now
# because R2 does not refuse the upload until the bytes have been sent —
# a few hundred megabytes to arrive at a 403.
if [[ -n "$$read_key" && "$$read_key" == "$$R2_ACCESS_KEY_ID" ]]; then
    echo "ERROR: R2_WRITE_ACCESS_KEY_ID is the same as R2_ACCESS_KEY_ID." >&2
    echo "       The write fields need the Object Read & Write token," >&2
    echo "       not the read-only one the API uses." >&2
    exit 1
fi
endef

.PHONY: help check check-args compile publish release health builds promote \
        start-ps1 start-sh image image-dev image-push webui webui-dev

help:
	@echo
	echo "  make check      verify credentials and reach the API"
	echo "  make health     what the deployment reports"
	echo "  make builds     every build published, newest first"
	echo
	echo "  make compile    build $(ARTIFACT) for LINUX, publish nothing"
	echo "  make publish    upload $(ARTIFACT) and point \"stable\" at it"
	echo "  make release    compile, then publish"
	echo "  make check-args everything a build must agree about, checked"
	echo "  make start-sh   push a $(POD_START) fix without a full publish"
	echo
	echo "  make webui      rebuild the React bundle - THE ONLY TARGET NEEDING NODE"
	echo "  make webui-dev  Vite's dev server, proxying the API to :7860"
	echo
	echo "  make promote SHA=<sha256>    roll a channel back to a build"
	echo
	echo "  Windows builds are made on Windows - Nuitka cannot cross-compile:"
	echo "      .\\\\build.ps1               compile dist/krea2app.exe"
	echo "      .\\\\build.ps1 -Publish      ... and publish it"
	echo "  make start-ps1  push a $(WIN_START) fix without a Windows box"
	echo
	echo "  make image      build $(IMAGE):$(TAG), the environment image"
	echo "  make image-dev  ... plus app deps, to run a working tree in it"
	echo "  make image-push push it to REGISTRY=<host/owner>"
	echo
	echo "  credentials     $(ENV_FILE)"
	echo

# Reports rather than enforces. A diagnostic that stops at the first
# missing value tells you one thing per run, which is the opposite of what
# you want when you are trying to find out what is wrong.
check:
	@$(LOAD_ENV)
	echo
	echo "  env file    $(ENV_FILE)"
	echo "  account     $${R2_ACCOUNT_ID:-(unset)}"
	echo "  bucket      $${R2_BUILDS_BUCKET:-(unset)}"
	echo "  api         $$API"
	echo "  admin token $${KREA2_ADMIN_TOKEN:0:6}..."
	if [[ -z "$${R2_WRITE_ACCESS_KEY_ID:-}" ]]; then
	    echo "  write key   NOT SET - 'make publish' will refuse; everything else works"
	elif [[ "$$read_key" == "$${R2_WRITE_ACCESS_KEY_ID}" ]]; then
	    echo "  write key   WRONG - same as the read-only key"
	else
	    echo "  write key   $${R2_WRITE_ACCESS_KEY_ID:0:6}...  (differs from read key: ok)"
	fi
	echo
	if [[ -f "$(ARTIFACT)" ]]; then
	    echo "  artifact    $(ARTIFACT)  ($$(stat -c %s "$(ARTIFACT)") bytes)"
	else
	    echo "  artifact    none yet - run 'make compile'"
	fi
	if [[ -f "$(ARTIFACT).exe" ]]; then
	    echo "  windows     $(ARTIFACT).exe  ($$(stat -c %s "$(ARTIFACT).exe") bytes)"
	else
	    echo "  windows     none here - built on Windows with .\\\\build.ps1"
	fi
	echo
	# Proves the admin token actually opens the deployment, which is the
	# one credential a successful upload still cannot tell you about.
	code=$$(curl -s -o /dev/null -w '%{http_code}' \
	        -H "Authorization: Bearer $$KREA2_ADMIN_TOKEN" \
	        "$$API/v1/admin/builds" || echo 000)
	case "$$code" in
	    200) echo "  admin api   ok" ;;
	    401) echo "  admin api   REJECTED - ADMIN_TOKEN does not match the deployment" ;;
	    404) echo "  admin api   404 - ADMIN_TOKEN is unset on the deployment" ;;
	    000) echo "  admin api   unreachable - check KREA2_NODE_TAG" ;;
	    *)   echo "  admin api   HTTP $$code" ;;
	esac
	# What each platform's customers would be handed right now. Two builds
	# hold "stable" once Windows is published - one per platform - so a
	# single answer here would be arbitrary, and "is the Windows build live
	# yet" is the question this target exists to answer.
	#
	# Written without an indented block on purpose. Make strips the leading
	# whitespace from every line of a .ONESHELL recipe, not just the tab, so
	# an indented `for` body arrives at Python with no indentation at all and
	# dies with an IndentationError - which 2>/dev/null then hides, leaving a
	# check that silently reports nothing.
	curl -s "$$API/health" | python3 -c '
	import json, sys
	stable = json.load(sys.stdin).get("stable_builds") or {}
	for name in sorted(stable): print("  stable %-7s %s" % (name, stable[name][:12] if stable[name] else "NOT PUBLISHED"))
	if not stable: print("  stable      (this deployment predates per-platform builds)")
	' 2>/dev/null || true
	echo

health:
	@$(LOAD_ENV)
	curl -s "$$API/health" | python3 -m json.tool 2>/dev/null || \
	    echo "could not reach $$API/health"

builds:
	@$(LOAD_ENV)
	curl -s -H "Authorization: Bearer $$KREA2_ADMIN_TOKEN" \
	     "$$API/v1/admin/builds" | python3 -m json.tool 2>/dev/null || \
	    echo "could not list builds - try 'make check'"

# The React front end. The ONLY target that needs Node — run it on a
# machine with Node >= 20 (the same one that runs license-validator/), then
# COMMIT webui_bundle.py.
#
# Committing a generated file is the whole answer to constraint 5 in
# context.md: neither build host has Node, and neither ever will. The
# alternative is a toolchain on a RunPod pod and on a Windows box, kept in
# step with this one, to turn TSX into JavaScript that is identical either
# way. It also means a fresh clone runs scripts/dryrun.py immediately,
# without npm.
#
# `npm ci`, not `npm install`: it installs exactly package-lock.json, which
# is one of the files SOURCE_HASH covers. `npm install` may resolve a newer
# transitive dependency, rewrite the lock file, and move the hash — turning
# "rebuild the front end" into a spurious stale-bundle failure on the next
# build.
webui:
	@cd webui && npm ci && npm run build
	python3 scripts/gen_webui_bundle.py

# Vite's own server, with hot reload. It proxies /api, /media and /thumbs
# to :7860, so run the app as well:
#     python scripts/dryrun.py --features all
webui-dev:
	@cd webui && npm run dev

# Cheap, credential-free, and a dependency of both build targets. Three
# checks, one gate — widened rather than given its own target so that
# `compile` and `release` keep their single prerequisite:
#
#   check_build_args  the two scripts each hold their own copy of the
#                     Nuitka --include-* list, and a flag added to one and
#                     not the other produces a build that compiles, runs,
#                     and is quietly missing something.
#   check_webui       webui_bundle.py is committed, so an edit to
#                     webui/src that nobody rebuilt would ship the
#                     previous front end without a word.
#   check_routes      the API's routes against what the front end calls.
#
# Every one of them catches a defect that a successful compile hides,
# which is why they cost a fraction of a second here rather than an
# afternoon in a customer's log.
check-args:
	@python3 scripts/check_build_args.py
	python3 scripts/check_webui.py
	python3 scripts/check_routes.py

compile: check-args
	@./build.sh --no-publish

publish:
	@$(LOAD_ENV)
	$(LOAD_WRITE)
	./build.sh --upload-only

release: check-args
	@$(LOAD_ENV)
	$(LOAD_WRITE)
	./build.sh -y

# Publish the pod start script on its own.
#
# The mirror image of start-ps1, and it exists for the same reason: this is
# one object at a fixed key, `./build.sh` normally uploads it alongside the
# binary, and a fix to the script should not cost a Nuitka compile and a new
# build document. The binary is untouched - this writes start.sh and nothing
# else, which is the object /v1/start.sh redirects to.
start-sh:
	@$(LOAD_ENV)
	$(LOAD_WRITE)
	if [[ ! -f "$(POD_START)" ]]; then
	    echo "ERROR: $(POD_START) not found." >&2
	    exit 1
	fi
	# The inverse of the BOM check start-ps1 does, and needed for the same
	# reason: the two scripts are read by tools with opposite tastes. bash
	# reads a BOM as the first three bytes of the shebang line, and a CR at
	# the end of it as part of the interpreter's name - so either one fails on
	# the pod with "bad interpreter", a message that names neither the cause
	# nor, usually, the right file. .gitattributes pins *.sh to LF; this is
	# what catches a file that got past it.
	if [[ "$$(head -c 3 "$(POD_START)" | xxd -p)" == "efbbbf" ]]; then
	    echo "ERROR: $(POD_START) starts with a UTF-8 BOM." >&2
	    echo "       bash reads it as part of the shebang and the pod fails" >&2
	    echo "       with 'bad interpreter'. Re-save it as UTF-8, no BOM." >&2
	    exit 1
	fi
	# -U, not a bare grep: a grep built for Windows (Git Bash, MSYS)
	# opens files in text mode and strips the CR before the pattern ever
	# sees it, so the check silently passes on exactly the machine most
	# likely to have introduced the CR. On Linux -U is documented as
	# having no effect, so it costs nothing to always pass it.
	if grep -qU $$'\r' "$(POD_START)"; then
	    echo "ERROR: $(POD_START) has CRLF line endings." >&2
	    echo "       The pod would fail with: bad interpreter: /usr/bin/env bash^M" >&2
	    echo "       Convert it to LF before publishing." >&2
	    exit 1
	fi
	url=$$(python3 scripts/r2_presign.py --key start.sh --method PUT --expires 900)
	code=$$(curl -sS -o /dev/null -w '%{http_code}' -T "$(POD_START)" "$$url")
	if [[ "$$code" == "200" ]]; then
	    echo "uploaded $(POD_START) -> r2://$$R2_BUILDS_BUCKET/start.sh"
	    echo "Pods get it on their next start."
	else
	    echo "ERROR: upload failed (HTTP $$code)" >&2
	    exit 1
	fi

# Publish the Windows start script on its own.
#
# It is normally uploaded by `.\build.ps1 -Publish`, alongside the .exe —
# but the two are independent objects, and a fix to the start script should
# not need a Windows machine, a recompile, or a new build document. This is
# the same PUT that script does, from wherever you already have the write
# credentials.
#
# The binary is untouched: this writes one object, at the fixed key
# /v1/start.ps1 redirects to.
start-ps1:
	@$(LOAD_ENV)
	$(LOAD_WRITE)
	if [[ ! -f "$(WIN_START)" ]]; then
	    echo "ERROR: $(WIN_START) not found." >&2
	    exit 1
	fi
	# Rejected rather than uploaded: powershell.exe decodes a .ps1 with
	# no BOM as Windows-1252, so a file that lost its BOM in an editor
	# fails to PARSE on the customer's machine — before any of its own
	# error handling can say anything. Cheaper to catch here than to
	# publish and find out from a customer.
	if [[ "$$(head -c 3 "$(WIN_START)" | xxd -p)" != "efbbbf" ]]; then
	    echo "ERROR: $(WIN_START) has no UTF-8 BOM." >&2
	    echo "       PowerShell 5.1 would read its comments as Windows-1252" >&2
	    echo "       and fail to parse the file. Re-save it as UTF-8 with BOM." >&2
	    exit 1
	fi
	url=$$(python3 scripts/r2_presign.py --key start.ps1 --method PUT --expires 900)
	code=$$(curl -sS -o /dev/null -w '%{http_code}' -T "$(WIN_START)" "$$url")
	if [[ "$$code" == "200" ]]; then
	    echo "uploaded $(WIN_START) -> r2://$$R2_BUILDS_BUCKET/start.ps1"
	    echo "Windows customers get it on their next start."
	else
	    echo "ERROR: upload failed (HTTP $$code)" >&2
	    exit 1
	fi

# Rolling back and rolling forward are the same call: the channel is just
# a name on whichever build document holds it.
promote:
	@$(LOAD_ENV)
	if [[ -z "$${SHA:-}" ]]; then
	    echo "usage: make promote SHA=<sha256>   (see 'make builds')" >&2
	    exit 2
	fi
	curl -s -X POST \
	     -H "Authorization: Bearer $$KREA2_ADMIN_TOKEN" \
	     -H 'Content-Type: application/json' \
	     -d "{\"sha256\":\"$$SHA\",\"channel\":\"$${CHANNEL:-stable}\"}" \
	     "$$API/v1/admin/builds/promote" | python3 -m json.tool

# ── The Docker image ──────────────────────────────────────────────────────
# Needs no credentials at all, which is the point: nothing in it is secret,
# so anyone with the repo can reproduce it. Bumping a pin in
# scripts/PINS.json is the only thing that changes what comes out.
image:
	@docker build -t "$(IMAGE):$(TAG)" .
	echo
	echo "built $(IMAGE):$(TAG)"
	echo "run it with: docker compose up   (after copying .env.example to .env)"
	echo

# The same image plus the app's Python dependencies, so a mounted working
# tree can run in it. Layered on $(IMAGE):$(TAG), so build that first.
image-dev: image
	@docker build -t "$(IMAGE):dev" \
	    --build-arg "BASE_IMAGE=$(IMAGE):$(TAG)" \
	    -f docker/Dockerfile.dev .
	echo
	echo "built $(IMAGE):dev"
	echo "run your working tree with:"
	echo "    docker compose -f docker-compose.dev.yml up"
	echo

# Tagged at push time rather than at build time, so the same local image
# can go to more than one registry without rebuilding.
image-push:
	@if [[ -z "$(REGISTRY)" ]]; then
	    echo "usage: make image-push REGISTRY=ghcr.io/<owner>" >&2
	    echo "       (or REGISTRY=docker.io/<user>)" >&2
	    exit 2
	fi
	docker tag "$(IMAGE):$(TAG)" "$(REGISTRY)/$(IMAGE):$(TAG)"
	docker push "$(REGISTRY)/$(IMAGE):$(TAG)"
	echo
	echo "pushed $(REGISTRY)/$(IMAGE):$(TAG)"
	echo "customers set KREA2_IMAGE=$(REGISTRY)/$(IMAGE):$(TAG) in .env"
	echo

