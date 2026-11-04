#!/usr/bin/env python3
"""Launch the app locally, with no GPU, no ComfyUI and no weights.

Operator tool, not part of the shipped app. The point is to look at the
UI — which tabs a license actually grants, how they lay out, what the
controls do — without a pod, without ~90 GB of downloads and without a
card that can hold a 35 GB UNet.

    $env:EMBER_LICENSE_KEY="your_license_key";  $env:EMBER_NODE_TAG="your_node_tag"; .\.venv\Scripts\python.exe scripts\dryrun.py

    python scripts/dryrun.py                        # tabs from your license
    python scripts/dryrun.py --features "krea_t2i,wan_i2v"   # offline, no server
    python scripts/dryrun.py --features all

Serves the React front end at http://127.0.0.1:7860 the same way the
shipped binary does — out of the committed `webui_bundle.py`, so a fresh
clone with no `webui/node_modules` runs this immediately. `webui/dist` is
the fallback when that module is absent. To work on the front end itself,
run `npm run dev` in `webui/` as well and use Vite's port — it proxies
/api, /media and /thumbs back here.

Note which one is being served: the bundle wins, so an edit to `webui/src`
does NOT show up here until `make webui` regenerates it. The startup line
says which source it used.

The first form is the one worth using: it calls the real license server
with EMBER_LICENSE_KEY, so it verifies the whole entitlement path —
key → features array → which tabs get built — against the record you
actually edited, and it reads the model and LoRA catalogue from the same
server (POST /v1/catalog), so the Krea dropdowns show what the DB holds.
It takes a seat for as long as it runs, and gives it back on Ctrl-C.
--features skips the server entirely and is for working on the UI itself,
offline or on a key you would rather not spend a seat on; the catalogue
then comes from license-validator/data/assets.json (the seed document)
unless EMBER_CATALOG_FILE names another file.

What is deliberately NOT run: bootstrap (the ComfyUI clone and the pip
install), downloads.download_everything(), and the ComfyUI server. So
every tab renders and every control works, but pressing Generate reports
that ComfyUI is not running — the model and LoRA dropdowns are read from
the catalogue (catalog.py), not from disk, which is what makes a
weightless run possible at all. Every model and LoRA is simply reported
as not downloaded.
"""

import argparse
import os
import sys
import types
from pathlib import Path

# The app's modules live one level up; this script is deliberately outside
# the package so build.sh / Nuitka never sweep it into the shipped binary.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Somewhere local to put the (empty) models/output/temp tree that
# ensure_dirs() below makes — the shipped default is a pod path,
# /workspace/ember.
os.environ.setdefault("EMBER_BASE_DIR", str(ROOT / ".dryrun"))

# The seed catalogue, for the offline form (--features). See main().
SEED_CATALOG = ROOT / "license-validator" / "data" / "assets.json"

from ember import logs, settings  # noqa: E402
from ember.logs import log  # noqa: E402

# Importing a module configures nothing and creates nothing any more, so
# this entry point does it: the logger, the tree under EMBER_BASE_DIR,
# and the line saying where weights and images go.
logs.setup()
settings.ensure_dirs()
settings.log_startup()


def stub_comfy() -> None:
    """Make `import comfy` work on a machine ComfyUI cannot run on.

    ember.comfy.server detects GPUs at import and raises when there are
    none, and ember.generation.runner and the pipeline workflow modules
    both import it for GPU_COUNT. A machine with a
    small card imports it for real and only needs ensure_alive replaced; a
    machine with no card at all needs the module faked before anything
    imports it. Either way the app's own code is untouched.
    """
    def ensure_alive(*_args, **_kwargs):
        return False, (
            "🧪 Dry run — ComfyUI is not running, so nothing can be "
            "generated. The UI itself is live: tabs, controls and "
            "validation all work."
        )

    try:
        from ember.comfy import server as comfy
    except RuntimeError as exc:            # no NVIDIA GPU visible
        log.warning("No GPU detected (%s) — faking the comfy module", exc)
        comfy = types.ModuleType("ember.comfy.server")
        comfy.GPUS, comfy.GPU_COUNT = [], 1
        comfy.start_comfyui = lambda *a, **k: None
        comfy.wait_for_comfyui = lambda *a, **k: None
        comfy.verify_custom_node = lambda *a, **k: True
        comfy.node_registered = lambda *a, **k: True
        comfy.log_tail = lambda *a, **k: "<dry run>"
        sys.modules["ember.comfy.server"] = comfy
        # A from-import of the parent package copies the binding at import
        # time, so the stub has to be visible as that attribute too.
        import ember.comfy
        ember.comfy.server = comfy
    else:
        log.info("GPU detected — using the real comfy module (server not started)")

    # Patched before ember.generation.runner is imported: runner.py binds
    # `from comfy import ensure_alive as comfy_ensure_alive` at import time,
    # so a later patch would not be seen. Without this, every Generate click tries to
    # start a ComfyUI that is not installed.
    comfy.ensure_alive = ensure_alive


def stub_library(folder: str) -> None:
    """Serve a folder of picture + token pairs as the Prompt Library.

    The pod reads the library from the licence server, which a dry run
    usually cannot reach with a build that knows the newest routes. This
    stands in for all three reads — a page, one token, a random pick — from
    pairs like Gallery's zip download writes (`x.png` beside `x.txt`), so
    the tab, its shuffle and Random generate can be tried locally. Thumbs
    go to the browser as data URLs, made on first view. No categories or
    tags: those live only in the real index.
    """
    import base64
    import hashlib
    import io
    import random as stdrandom

    from PIL import Image

    from ember.licensing import prompts, tokens

    entries = []
    for picture in sorted(Path(folder).iterdir()):
        if picture.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        text = picture.with_suffix(".txt")
        if not text.is_file():
            continue
        try:
            clean, data = tokens.read_token(text.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        entry_id = hashlib.sha256(clean.encode()).hexdigest()[:12]
        entries.append({"id": entry_id, "tab": data["tab"], "token": clean,
                        "path": picture})
    log.warning("Prompt Library stubbed from %s: %d entries", folder,
                len(entries))
    thumbs: dict[str, tuple[str, int, int]] = {}

    def thumb(entry):
        if entry["id"] not in thumbs:
            with Image.open(entry["path"]) as image:
                image = image.convert("RGB")
                image.thumbnail((480, 480))
                buffer = io.BytesIO()
                image.save(buffer, "WEBP", quality=80)
                url = ("data:image/webp;base64,"
                       + base64.b64encode(buffer.getvalue()).decode())
                thumbs[entry["id"]] = (url, image.width, image.height)
        return thumbs[entry["id"]]

    def library(tab=None, category=None, tags=(), skip=0, limit=24,
                force=False, shuffle=None):
        rows = [e for e in entries if not tab or e["tab"] == tab]
        if category or tags:
            rows = []
        if shuffle is not None:
            rows.sort(key=lambda e: hashlib.sha256(
                f"{shuffle}:{e['id']}".encode()).hexdigest())
        page = []
        for entry in rows[skip:skip + limit]:
            url, width, height = thumb(entry)
            page.append(prompts.Prompt(
                id=entry["id"], tab=entry["tab"], title=None, category=None,
                tags=(), width=width, height=height, thumb=url, full=url,
                usable=True))
        counts = {}
        for entry in entries:
            counts[entry["tab"]] = counts.get(entry["tab"], 0) + 1
        return prompts.Library(prompts=tuple(page), total=len(rows),
                               skip=skip, limit=limit, tabs=counts)

    def token(entry_id):
        found = next((e for e in entries if e["id"] == entry_id), None)
        return (found["token"], None) if found else (None, "No such card.")

    def pick_random(tab, count, category=None, tags=()):
        rows = [] if category or tags else [e for e in entries
                                             if e["tab"] == tab]
        chosen = stdrandom.sample(rows, min(count, len(rows)))
        return ([prompts.Picked(e["id"], None, e["token"]) for e in chosen],
                len(rows), None)

    prompts.library = library
    prompts.token = token
    prompts.pick_random = pick_random


def hold_jobs(seconds: float) -> None:
    """Keep every job running for `seconds` before its handler starts.

    A dry run's jobs fail in milliseconds (no weights, no ComfyUI), so the
    queue is never more than one deep and its ordering — Run next,
    Generate next, drag to reorder — cannot be tried by hand. Wrapped at
    jobqueue.submit, which the routes call through the module, so every
    tab is held the same way whatever its handler checks first. The wait
    counts down in the job's status line and Cancel ends it.
    """
    import time
    from ember.generation import queue as jobqueue

    submit = jobqueue.submit

    def held_submit(*, fn, **kwargs):
        keys = tuple(kwargs.get("result_keys", ("images", "status")))

        def line(text):
            # A yield is positional against the tab's result keys.
            return tuple(text if key == "status" else
                         [] if key in ("images", "videos") else None
                         for key in keys)

        def held(*args):
            job = jobqueue._CURRENT.job
            end = time.time() + seconds
            while time.time() < end:
                left = int(end - time.time()) + 1
                yield line(f"🧪 Dry run — holding, {left}s left")
                if job is not None and job._stop.is_set():
                    return
                time.sleep(1)
            outcome = fn(*args)
            if hasattr(outcome, "__next__"):
                yield from outcome
            else:
                yield outcome
        return submit(fn=held, **kwargs)

    jobqueue.submit = held_submit
    log.warning("Dry run: every job is held for %gs before it runs", seconds)


def mark_auto_trigger(ids: str) -> None:
    """Set `auto_trigger` on these LoRAs in the loaded catalogue.

    What flipping the field in Atlas does, for this run only. A LoRA with
    no trigger is named but changes nothing, as on a real pod.
    """
    import dataclasses
    from ember.licensing import catalog

    loras = catalog.get().loras
    for lora_id in (part.strip() for part in ids.split(",")):
        lora = loras.get(lora_id)
        if lora is None:
            log.warning("--auto-trigger: no LoRA %r in the catalogue", lora_id)
            continue
        loras[lora_id] = dataclasses.replace(lora, auto_trigger=True)
        log.info("--auto-trigger: %s (trigger %r)", lora_id, lora.trigger)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the app locally with no GPU, ComfyUI or weights.",
    )
    parser.add_argument(
        "--features", metavar="LIST",
        help='skip the license server and force this set, e.g. "krea_t2i,wan_i2v" '
             'or "all". Without it, the tabs come from your license key.',
    )
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument(
        "--hold", type=float, default=0.0, metavar="SECONDS",
        help="keep each job running this long before it fails, so later "
             "clicks wait in the queue (default: fail at once).",
    )
    parser.add_argument(
        "--library", metavar="DIR",
        help="serve this folder of picture + .txt token pairs as the "
             "Prompt Library, instead of asking the licence server.",
    )
    parser.add_argument(
        "--auto-trigger", metavar="IDS",
        help='mark these LoRA ids auto_trigger, e.g. "hm1girl-v1-0,'
             'bloomgirls-v1-0", so the prompt and the stack keep each '
             'other in step without editing the DB.',
    )
    parser.add_argument(
        "--tunnel", action="store_true",
        help="open a Cloudflare quick tunnel for a public URL (default: "
             "localhost only).",
    )
    parser.add_argument(
        "--api-only", action="store_true",
        help="accepted and ignored. There is only one app now, and this "
             "script has always served it; the flag stays so the command "
             "lines in older notes keep working.",
    )
    args = parser.parse_args()

    from ember.licensing import catalog
    from ember import features

    if args.features:
        keys = [f.key for f in features.FEATURES] if args.features == "all" else [
            token.strip() for token in args.features.replace(";", ",").split(",")
            if token.strip()
        ]
        log.warning("Skipping the license check — forcing features: %s",
                    ", ".join(keys))
        features.resolve(keys)
        # No server to ask, so the seed document stands in for the DB —
        # unless a EMBER_CATALOG_FILE already says which file to read.
        os.environ.setdefault(catalog.FILE_ENV, str(SEED_CATALOG))
        catalog.load()
    else:
        from ember.licensing import seat as licensing
        licensing.acquire_or_exit()
        features.resolve(licensing.entitlements())
        # The same order app.py keeps: after the licence check (the
        # catalogue request is licence-checked too), before anything
        # builds a form out of it.
        catalog.load(licensing.instance_id())

    log.info("Features — %s", features.summary())
    if args.auto_trigger:
        mark_auto_trigger(args.auto_trigger)

    stub_comfy()
    if args.hold > 0:
        hold_jobs(args.hold)
    if args.library:
        stub_library(args.library)

    # Nothing to set: the auth gate is open unless EMBER_UI_REQUIRE_TOKEN
    # says otherwise. It is still why this script refuses to bind anything
    # but loopback — an app that asks for no token should not be reachable
    # from the next desk.
    from ember.web import serve

    # webui.mount() logs which source it picked — the committed bundle or
    # webui/dist — on the next line, so this one does not guess.
    log.info("Starting on http://127.0.0.1:%d. For front-end work run "
             "`npm run dev` in webui/ as well and use Vite's port, which "
             "proxies /api back here.", args.port)
    serve.serve(port=args.port, host="127.0.0.1", tunnel=args.tunnel)


if __name__ == "__main__":
    main()
