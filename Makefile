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
        start-ps1 image image-dev image-push

help:
	@echo
	echo "  make check      verify credentials and reach the API"
	echo "  make health     what the deployment reports"
	echo "  make builds     every build published, newest first"
	echo
	echo "  make compile    build $(ARTIFACT) for LINUX, publish nothing"
	echo "  make publish    upload $(ARTIFACT) and point \"stable\" at it"
	echo "  make release    compile, then publish"
	echo "  make check-args build.sh and build.ps1 must bundle the same things"
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

# Cheap, credential-free, and a dependency of both build targets: the two
# scripts each hold their own copy of the Nuitka --include-* list, and a
# flag added to one and not the other produces a build that compiles, runs,
# and is quietly missing something. Catching that costs a fraction of a
# second here against noticing it in a customer's log.
check-args:
	@python3 scripts/check_build_args.py

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

