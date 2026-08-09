#!/usr/bin/env python3
"""Launch the Gradio UI locally, with no GPU, no ComfyUI and no weights.

Operator tool, not part of the shipped app. The point is to look at the
UI — which tabs a license actually grants, how they lay out, what the
controls do — without a pod, without ~90 GB of downloads and without a
card that can hold a 35 GB UNet.

    $env:KREA2_LICENSE_KEY="your_license_key";  $env:KREA2_NODE_TAG="your_node_tag"; .\.venv\Scripts\python.exe scripts\dryrun.py

    python scripts/dryrun.py                        # tabs from your license
    python scripts/dryrun.py --features "single,klein"   # offline, no server
    python scripts/dryrun.py --features all

The first form is the one worth using: it calls the real license server
with KREA2_LICENSE_KEY, so it verifies the whole entitlement path —
key → features array → which tabs get built — against the record you
actually edited. It takes a seat for as long as it runs, and gives it
back on Ctrl-C. --features skips the server entirely and is for working
on the UI itself, offline or on a key you would rather not spend a seat
on.

What is deliberately NOT run: bootstrap (the ComfyUI clone and the pip
install), downloads.download_everything(), and the ComfyUI server. So
every tab builds and every control works, but pressing Generate reports
that ComfyUI is not running — the model dropdowns are read from the
registry in config.py, not from disk, which is what makes a weightless
run possible at all.
"""

import argparse
import os
import sys
import types
from pathlib import Path

# The app's modules live one level up; this script is deliberately outside
# the package so build.sh / Nuitka never sweep it into the shipped binary.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Somewhere local to put the (empty) models/output/temp tree, before
# config is imported — it creates those directories at import time and the
# shipped default is a pod path, /workspace/krea2.
os.environ.setdefault(
    "KREA2_BASE_DIR", str(Path(__file__).resolve().parent.parent / ".dryrun"),
)

from config import OUTPUT_DIR, log  # noqa: E402


def stub_comfy() -> None:
    """Make `import comfy` work on a machine ComfyUI cannot run on.

    comfy.py detects GPUs at import and raises when there are none, and
    ui.py/workflow.py both import it for GPU_COUNT. A machine with a small
    card imports it for real and only needs ensure_alive replaced; a
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
        import comfy
    except RuntimeError as exc:            # no NVIDIA GPU visible
        log.warning("No GPU detected (%s) — faking the comfy module", exc)
        comfy = types.ModuleType("comfy")
        comfy.GPUS, comfy.GPU_COUNT = [], 1
        comfy.start_comfyui = lambda *a, **k: None
        comfy.wait_for_comfyui = lambda *a, **k: None
        comfy.verify_custom_node = lambda *a, **k: True
        comfy.node_registered = lambda *a, **k: True
        comfy.log_tail = lambda *a, **k: "<dry run>"
        sys.modules["comfy"] = comfy
    else:
        log.info("GPU detected — using the real comfy module (server not started)")

    # Patched before ui is imported: ui.py binds `from comfy import
    # ensure_alive as comfy_ensure_alive` at import time, so a later patch
    # would not be seen. Without this, every Generate click tries to start
    # a ComfyUI that is not installed.
    comfy.ensure_alive = ensure_alive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Gradio UI locally with no GPU, ComfyUI or weights.",
    )
    parser.add_argument(
        "--features", metavar="LIST",
        help='skip the license server and force this set, e.g. "single,klein" '
             'or "all". Without it, the tabs come from your license key.',
    )
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument(
        "--share", action="store_true",
        help="expose a public gradio.live link (default: localhost only)",
    )
    args = parser.parse_args()

    import features

    if args.features:
        keys = [f.key for f in features.FEATURES] if args.features == "all" else [
            token.strip() for token in args.features.replace(";", ",").split(",")
            if token.strip()
        ]
        log.warning("Skipping the license check — forcing features: %s",
                    ", ".join(keys))
        features.resolve(keys)
    else:
        import licensing
        licensing.acquire_or_exit()
        features.resolve(licensing.entitlements())

    log.info("Features — %s", features.summary())

    stub_comfy()

    # Imported last, and only now: ui.py builds its gr.Blocks at import
    # time, so this line is where the tabs are decided.
    import theme
    import ui

    log.info("Starting Gradio on http://127.0.0.1:%d ...", args.port)
    # theme.launch_kwargs() is what the real launch_ui() passes too, so what
    # you see here is what a customer sees — the point of the script.
    ui.ui.launch(
        server_name="127.0.0.1", server_port=args.port, share=args.share,
        show_error=True, ssr_mode=False, allowed_paths=[str(OUTPUT_DIR)],
        **theme.launch_kwargs(),
    )


if __name__ == "__main__":
    main()
