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

.PHONY: help check compile publish release health builds promote

help:
	@echo
	echo "  make check      verify credentials and reach the API"
	echo "  make health     what the deployment reports"
	echo "  make builds     every build published, newest first"
	echo
	echo "  make compile    build $(ARTIFACT), publish nothing"
	echo "  make publish    upload $(ARTIFACT) and point \"stable\" at it"
	echo "  make release    compile, then publish"
	echo
	echo "  make promote SHA=<sha256>    roll a channel back to a build"
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

compile:
	@./build.sh --no-publish

publish:
	@$(LOAD_ENV)
	$(LOAD_WRITE)
	./build.sh --upload-only

release:
	@$(LOAD_ENV)
	$(LOAD_WRITE)
	./build.sh -y

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

