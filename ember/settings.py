"""Ember — everything read from the environment, and what follows from it.

This is the only module in the package that touches `os.environ`
(scripts/check_imports.py enforces it). Anything a pod's environment
panel can change is decided here, once, and validated where it is read,
so that no other module has to guess what an unset or malformed value
means. Facts about a model or its graph are not environment and are not
here: each pipeline keeps its own in `ember/pipelines/<name>/constants.py`.

Nothing runs when this module is imported: no logging is configured, no
directory is made, nothing is logged. An entry point calls `logs.setup()`,
`ensure_dirs()` and `log_startup()`, in that order, before it does
anything else.

Every environment variable the app reads, in one place:

    EMBER_BASE_DIR              where models, outputs and logs live
    EMBER_KEEP_MODELS_LOADED    do not unload between model swaps
    EMBER_SAGE_ATTENTION        0 runs ComfyUI without SageAttention
    EMBER_WAN_PARALLEL          second ComfyUI instance for video
    EMBER_MAIN_RESERVE_VRAM     GB left for Wan by the image instance
    EMBER_WAN_RESERVE_VRAM      GB left for images by the video instance
    EMBER_LICENSE_KEY           the customer key (required)
    EMBER_NODE_TAG              the deployment id the key checks in against
    EMBER_LICENSE_GRACE         seconds tolerated with no licence server
    EMBER_SHOWCASE_URL          public bucket holding the pricing page's images
    EMBER_UI_REQUIRE_TOKEN      1 demands the UI token in the URL fragment
    EMBER_SKIP_LAUNCH           start everything, then stop before serving
    EMBER_MIRROR_USER           the Hugging Face account the mirrors live under
    EMBER_NO_MIRROR             1 downloads from upstream, never the mirror
    EMBER_CATALOG_FILE          read the catalogue from this file, not the server
    RUNPOD_POD_ID               a stable instance id for the licence seat
    RUNPOD_POD_HOSTNAME         the fallback when the pod id is not set
    HF_TOKEN, CIVITAI_TOKEN     download credentials
    OPENROUTER_API_KEY          the key MiniMax's Auto prompt writes with

Each EMBER_ variable above also answers to its KREA2_ spelling, and
EMBER_ wins when both are set. Pods and RunPod templates in the field
carry the KREA2_ names in their environment panels, and a rename that
needed every one of them edited by hand would take a working pod down;
reading both means nobody has to touch anything to keep running.
`main()` names the legacy variables a run is relying on, once, so an
operator knows what to change when they are ready. The two RunPod ids
the two download credentials and the OpenRouter key are somebody else's
names and have one spelling each.
"""

import os
import re
from pathlib import Path

from ember.logs import log

# ── Build mode ────────────────────────────────────────────────────────────────
# True when running as a Nuitka-compiled binary (build.sh), False under a
# plain `python3 app.py`. Nuitka injects __compiled__ into every module it
# compiles, so this is the one authoritative check — keep it that way.
# Only two places may branch on it: bootstrap.runtime_python() and the
# app-requirements skip in bootstrap.install_comfyui(). Every extra branch
# is another way for the shipped binary to behave differently from a dev run.
FROZEN = "__compiled__" in globals()

# ── Both spellings ────────────────────────────────────────────────────────────
# The variables that have an EMBER_ name and a KREA2_ name. _env() reads
# only these and legacy_names() scans only these, so the list an operator
# is told to rename cannot drift from the list the app actually reads.
DUAL_NAMES = (
    "BASE_DIR",
    "CATALOG_FILE",
    "KEEP_MODELS_LOADED",
    "LICENSE_GRACE",
    "LICENSE_KEY",
    "MAIN_RESERVE_VRAM",
    "MIRROR_USER",
    "NODE_TAG",
    "NO_MIRROR",
    "SAGE_ATTENTION",
    "SHOWCASE_URL",
    "SKIP_LAUNCH",
    "UI_REQUIRE_TOKEN",
    "WAN_PARALLEL",
    "WAN_RESERVE_VRAM",
)


def _env(name: str, default: str | None = None) -> str | None:
    """EMBER_<name>, or KREA2_<name>, or `default`.

    EMBER_ first, so an operator who has set the new name gets it even on
    a pod whose template still carries the old one — which is how a
    machine is moved across: add the new variable, confirm, remove the
    old one.

    Empty is a value, not an absence: EMBER_X="" shadows KREA2_X, the way
    `os.environ.get` has always treated it, so clearing a variable in the
    panel means the same thing under either name.
    """
    if name not in DUAL_NAMES:
        raise KeyError("%s is not one of the dual-spelled variables" % name)
    value = os.environ.get("EMBER_" + name)
    if value is None:
        value = os.environ.get("KREA2_" + name)
    return default if value is None else value


def legacy_names() -> list[str]:
    """The KREA2_ variables this environment is being read through.

    Scanned rather than accumulated as values are read, because two of
    the fifteen are read at call time — a catalogue override is resolved
    long after main() has said its piece — and a line that named only the
    variables read so far would name a different set depending on when it
    ran.
    """
    return sorted("KREA2_" + name for name in DUAL_NAMES
                  if "EMBER_" + name not in os.environ
                  and "KREA2_" + name in os.environ)


# ── Disk layout ───────────────────────────────────────────────────────────────
# The pod filesystem is ephemeral: the ComfyUI install, model weights,
# generated images and logs all live under one base directory and are lost
# when the pod is destroyed. Override with the EMBER_BASE_DIR env var.
# Under --onefile PROJECT_DIR is Nuitka's temp extraction dir, which is
# correct: build.sh bundles requirements.txt alongside the code.
# BASE_DIR is unaffected — it is absolute, so models outlive the extraction.
PROJECT_DIR = Path(__file__).resolve().parents[1]
# The pricing page's showcase copy — prose only, a few kilobytes, bundled
# because it is what decides whether the section renders at all. Under
# --onefile this resolves inside the extraction dir; build.sh bundles it
# with --include-data-files. A build that forgets it still starts, and the
# pricing page simply loses the section (see showcase.py).
#
# The *pictures* it names are not here. They live in the R2 bucket below,
# which is what keeps a page full of screenshots out of the binary.
ASSETS_DIR = PROJECT_DIR / "assets"

# The volume a pod mounts, and the two directories this app has been
# given on it. Named here rather than written into the function below, so
# that the choice can be exercised against a temp tree.
WORKSPACE_DIR = Path("/workspace")


def _default_base_dir(workspace: Path = WORKSPACE_DIR) -> Path:
    """Where everything lives when no variable says.

    /workspace/ember, except on a volume that already holds
    /workspace/krea2 and no /workspace/ember — that is about 90 GB of
    weights a customer has already paid to download, and the pod that
    mounts the volume next has to find them where they are. This reads
    the disk and nothing else: no directory is made, nothing is moved,
    nothing is copied, nothing is linked. The only way to have both is to
    have deliberately made the new one.
    """
    current = workspace / "ember"
    existing = workspace / "krea2"
    if existing.is_dir() and not current.exists():
        return existing
    return current


# .resolve() is load-bearing, not tidiness: a relative EMBER_BASE_DIR
# (EMBER_BASE_DIR=./tmp, natural on a dev box) would otherwise be resolved
# by each process against its own cwd. ComfyUI runs with cwd=COMFY_DIR
# (ember/comfy/server.py) and is handed --output-directory as a string, so
# it would write to <cwd>/tmp/ComfyUI/tmp/output while this process —
# ensure_dirs() below, the gallery scan, the "saved under ..." status line
# — all meant <cwd>/tmp/output. Images land somewhere real and the app
# cannot find them. Absolute here means every consumer reads the same path.
#
# What an unset variable means is decided by what is already on the disk;
# _default_base_dir() above is the other half of this line.
_BASE_DIR = _env("BASE_DIR")
BASE_DIR = Path(
    _default_base_dir() if _BASE_DIR is None else _BASE_DIR
).expanduser().resolve()
TEMP_DIR = BASE_DIR     # ComfyUI install + model weights
WORKING_DIR = BASE_DIR  # generated images + logs
COMFY_DIR = TEMP_DIR / "ComfyUI"
MODELS_DIR = TEMP_DIR / "models"
OUTPUT_DIR = WORKING_DIR / "output"
COMFY_LOG = WORKING_DIR / "comfyui.log"

COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

# Unload the previous models when a job needs different base weights.
# ComfyUI keeps what it loaded until memory pressure evicts it, so without
# this a swap briefly holds two full model sets — the moment where the
# server gets OOM-killed once several multi-GB UNets are in rotation
# (Krea 2 V1 turbo/raw plus V2 turbo/raw, plus Wan). Freeing at
# the boundary caps the peak at one set and costs only the reload that a
# swap already pays for. Set EMBER_KEEP_MODELS_LOADED=1 to turn it off on
# a machine with room to spare, where keeping models warm is faster.
FREE_ON_SWAP = not _env("KEEP_MODELS_LOADED")

# SageAttention: faster, slightly approximate attention kernels, used by
# every ComfyUI instance once bootstrap.install_sageattention has proved
# they run on this GPU (Linux, torch 2.8.0 or 2.11.0, an A100/A40/L40S/
# H100/RTX 50xx class card). On by default, as in the MiniMax template; set
# EMBER_SAGE_ATTENTION=0 to run with PyTorch attention instead — the thing
# to try first if a tab's output looks wrong on a card where it did not.
SAGE_ATTENTION = _env("SAGE_ATTENTION", "1").strip().lower() \
    not in ("0", "false", "no", "off")

# ── The Wan video instance ────────────────────────────────────────────────────
# With EMBER_WAN_PARALLEL=1 a second ComfyUI instance serves the Video tab
# on its own port, so a quick Krea image never queues behind a long video
# render. Each instance is told to leave VRAM for the other via
# --reserve-vram; on a 48 GB A40 the defaults give Krea ~22 GB and Wan
# ~26 GB. Without the flag (default) both tabs share one ComfyUI queue —
# zero OOM risk, but jobs run strictly one after another. Only has any
# effect when the "wan_i2v" feature is on: app.py checks both before paying
# for a second instance.
WAN_PARALLEL = bool(_env("WAN_PARALLEL"))
WAN_COMFY_PORT = 8189
WAN_COMFY_LOG = WORKING_DIR / "comfyui_wan.log"
KREA_RESERVE_VRAM_GB = float(_env("MAIN_RESERVE_VRAM", "26"))
WAN_RESERVE_VRAM_GB = float(_env("WAN_RESERVE_VRAM", "22"))

# ── Licensing ─────────────────────────────────────────────────────────────────
# One customer key allows a fixed number of concurrent running instances.
# The key is per-customer and set on the pod like the tokens below.
#
# The endpoint is assembled here from a bare deployment id rather than read
# as a whole URL, for two reasons. An endpoint that can be repointed is a
# licence check that can be answered by any server the customer chooses;
# accepting only the id means the host can never be anything other than a
# vercel.app subdomain. And the variable is named for what it looks like on
# a pod — a node tag, sitting among RunPod's own — rather than for what it
# does, so the licensing path is not the first thing read in the env panel.
#
# There is deliberately no fallback. The id is not compiled into the binary,
# so a pod that does not carry it cannot check out a seat at all.
_NODE_TAG = (_env("NODE_TAG") or "").strip().lower()
# One DNS label, nothing more. A dot, a slash, a colon or a port is how a
# tag would smuggle in a different host, so reject the value outright rather
# than strip the offending characters and use whatever is left.
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{6,61}[a-z0-9]", _NODE_TAG):
    _NODE_TAG = ""
# Empty when the tag is missing or malformed. licensing.acquire_or_exit()
# turns that into the stop message; nothing else may call the API without
# going through it.
LICENSE_API_URL = f"https://{_NODE_TAG}.vercel.app" if _NODE_TAG else ""
LICENSE_KEY = _env("LICENSE_KEY") or None
# How long the app keeps running when the license server is unreachable.
# Long enough that an outage does not kill a video render mid-way, short
# enough that a pod cut off from the server does not run indefinitely.
LICENSE_GRACE_SECONDS = float(_env("LICENSE_GRACE", "1800"))

# Optional. The mirror repos (see mirror.py) are public, so a customer pod
# pulls every mirrored weight anonymously; this is only needed for gated
# upstream repos. Keep it *unset* rather than wrong — a stale token turns
# an anonymous download into a 401, which reads as "the mirror is missing
# files" when the real problem is the credential.
HF_TOKEN = os.environ.get("HF_TOKEN") or None
# Only needed when a download falls through to CivitAI — i.e. when the
# mirror could not serve it. A healthy mirrored pod never uses this.
CIVITAI_TOKEN = os.environ.get("CIVITAI_TOKEN") or None
# The key 🎥 MiniMax I2V's and 🎞️ MiniMax T2V's Auto prompt writes with.
# OpenRouter's own name for it, which is what the ComfyUI workflows the
# feature came from read, so a pod already set up for those needs nothing
# new. Optional: the tab takes a pasted key when this is unset.
OPENROUTER_API_KEY = (os.environ.get("OPENROUTER_API_KEY") or "").strip() or None

# ── Showcase images ───────────────────────────────────────────────────────────
# Where the pricing page's screenshots are served from — a public Cloudflare
# R2 bucket, holding the same folder layout showcase.json names:
#
#     https://pub-<hash>.r2.dev/krea_edit/compare-a/before.webp
#
# Note the host is the subdomain Cloudflare assigns when public access is
# switched on, not one named after the bucket — enabling it is what mints
# the value, so there is nothing to guess at from the bucket name. A custom
# domain works the same way and is the better choice in production, since
# r2.dev is rate limited and Cloudflare does not intend it for live traffic.
#
# They are fetched by the customer's browser, not by this app, so nothing
# here downloads them and no credential is involved: the bucket has to be
# public (an r2.dev subdomain or a custom domain). Plain <img> loads need no
# CORS header either — only fetch() would.
#
# The alternative was compiling them into the binary, which put every
# screenshot into a onefile build that is re-extracted on every launch. This
# way a new picture is an upload, not a release.
#
# Empty is a supported state, not a broken one: the section still renders,
# in full, with every picture as a placeholder tile naming the file it wants
# (see showcase.py). That is also what a dev checkout gets for free.
SHOWCASE_BASE_URL = (_env("SHOWCASE_URL") or "").strip()
# https only, and no query or fragment: the value is pasted into an env
# panel rather than reviewed in a diff, and it ends up as the prefix of
# every image src on the page. Anything else is dropped with a warning
# rather than used — the page then reads exactly as it does with no bucket
# configured at all.
#
# The complaint is held rather than logged, because nothing here runs at
# import time any more and no entry point has configured logging yet when
# it does. log_startup() is what says it out loud.
SHOWCASE_URL_WARNING = ""
if SHOWCASE_BASE_URL and not SHOWCASE_BASE_URL.lower().startswith("https://"):
    SHOWCASE_URL_WARNING = (
        "EMBER_SHOWCASE_URL is not an https:// URL — ignoring it. "
        "The pricing showcase will render with placeholder tiles."
    )
    SHOWCASE_BASE_URL = ""
SHOWCASE_BASE_URL = SHOWCASE_BASE_URL.split("?")[0].split("#")[0].rstrip("/")

# ── The rest of the environment ───────────────────────────────────────────────
# Values other modules used to read for themselves. They are here for the
# same reason as everything above: one module decides what an unset or
# malformed variable means, and check_config.py can then record every one
# of them.

# The UI's auth gate. The token is in the URL fragment rather than demanded
# up front, so a link that lost its fragment still opens; set
# EMBER_UI_REQUIRE_TOKEN=1 to put the gate back. See web/api.py.
UI_REQUIRE_TOKEN = bool(_env("UI_REQUIRE_TOKEN"))

# Do everything a normal start does — licence, catalogue, bootstrap,
# downloads, ComfyUI — and then stop instead of serving. What a pod that is
# only meant to warm its volume runs.
SKIP_LAUNCH = bool(_env("SKIP_LAUNCH"))

# The Hugging Face account the weight mirrors live under, and the escape
# hatch that ignores them. EMBER_NO_MIRROR=1 goes straight to upstream —
# for debugging a suspected bad mirror without editing anything. See
# weights/mirror.py.
MIRROR_USER = _env("MIRROR_USER", "thcocrambo2")
MIRROR_ENABLED = not _env("NO_MIRROR")

# Where the model and LoRA catalogue is read from when it is not fetched
# from the licence server: a JSON file in the same shape, which is how a
# dry run and every check work offline.
CATALOG_FILE_ENV = "EMBER_CATALOG_FILE"

# The order a pod's own identity is looked for in, for the licence seat.
# scripts/runpod_start.sh mirrors it, so the two must not drift.
INSTANCE_ID_VARS = ("RUNPOD_POD_ID", "RUNPOD_POD_HOSTNAME")


def catalog_file() -> str | None:
    """The catalogue file override, read at call time.

    Not a module-level constant, unlike everything above: scripts/
    check_schema.py and scripts/dryrun.py set the variable after this
    module has been imported, and a value frozen at import would ignore
    them. See licensing/catalog.load().
    """
    return _env("CATALOG_FILE")


def instance_id_candidates() -> list[str]:
    """The pod ids the environment offers, best first, read at call time.

    Empty when the app is not running on a pod; licensing/seat.py then
    falls back to the hostname and finally to a random id.
    """
    return [value.strip() for value in
            (os.environ.get(var) for var in INSTANCE_ID_VARS) if value]


def pod_id() -> str:
    """RunPod's own id for this pod, or "" off a pod.

    Reported to the licence server alongside the seat, which is why it is
    the raw variable rather than the resolved instance id.
    """
    return os.environ.get("RUNPOD_POD_ID", "")


def ensure_dirs() -> None:
    """Make the tree the app writes into. Idempotent.

    Called by every entry point rather than run when this module is
    imported, so that reading a constant never creates a directory — which
    would otherwise put a /workspace/ember tree on any machine that
    imports the configuration.
    """
    for directory in (TEMP_DIR, MODELS_DIR, OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def log_startup() -> None:
    """The lines the configuration module used to print as it was imported.

    Which features are on is logged by main.py once features.py has
    resolved them — this module deliberately does not know, so that
    importing it from features.py stays acyclic. The models and LoRAs
    themselves are logged by catalog.load(), which runs after the licence
    check — this module cannot know them either.
    """
    if SHOWCASE_URL_WARNING:
        log.warning("%s", SHOWCASE_URL_WARNING)
    log.info("Weights → %s · images → %s", MODELS_DIR, OUTPUT_DIR)
