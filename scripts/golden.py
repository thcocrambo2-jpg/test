#!/usr/bin/env python3
"""Freeze the workflow dict every generate_* handler builds, and diff it.

Operator tool, not part of the shipped app — the same posture as
scripts/parity.py and scripts/dryrun.py, and it borrows their weightless
boot so it runs on a laptop with no GPU, no ComfyUI and no models.

    python scripts/golden.py                  # write the snapshots
    python scripts/golden.py --check          # exit 1 on any difference

Why this exists
---------------
scripts/parity.py freezes the *forms*. This freezes what the handlers do
with them, and the two catch different things.

`generate_v2` takes **31 positional arguments**. The adapter that replaces
Gradio's `click(inputs=[...])` has to hand them over in exactly the right
order, and the failure mode of getting it wrong is not an exception: swap
`cutoff_step` and `total_steps` and you have swapped two ints, every
handler still runs, every picture still arrives, and they are quietly
worse. Nothing in the app notices, and no human reading a 5,000-line diff
notices either.

So take the answer from the code while it is still the truth. The seam is
`_run_jobs` (ui.py:356), which calls `builder(filename_prefix=..., **job)`
and then `client.run(workflow)` — patch `run`, keep the dict it was handed,
and that dict is the complete statement of what the arguments meant. A
swapped pair of ints moves a number inside it, and `--check` names the
JSON path it moved at.

What is stubbed, and why each one is necessary
----------------------------------------------
* `comfy_ensure_alive` -> `(True, "")`. scripts/dryrun.py's `stub_comfy()`
  makes it return `(False, note)`, which makes `_run_jobs` yield the note
  and return **before it builds anything at all**. Without this override
  every snapshot would be empty and the whole file would pass while
  proving nothing.
* the model / LoRA availability checks -> True. Every handler guards on
  "is this downloaded yet" and returns early when it is not; a laptop has
  none of the weights.
* `client.run` / `wan_client.run` -> capture and yield canned events.
* `client.upload_image` -> a fixed name. The real one POSTs to ComfyUI,
  and the name it returns lands *in* the workflow, so it has to be stable
  or every image handler's snapshot would differ on every run.
* `free_models` -> nothing. `_release_on_swap` calls it whenever two
  consecutive jobs need different weights, which is every handler after
  the first one here.
* `prompts.record` and `presets.save` -> nothing. Both POST to the licence
  server.

Snapshots live in scripts/golden/, one JSON per handler, and are
committed. Regenerate them deliberately — a diff here is either a bug you
have just caught or a change you meant to make, and it is worth being
sure which before overwriting it.
"""

import argparse
import io
import json
import os
import sys
import types
from pathlib import Path

# One level up, and deliberately outside the package so Nuitka never sweeps
# it into the binary. Same reasoning as scripts/parity.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Before config is imported: it makes its directory tree at import time and
# the shipped default is the pod path /workspace/krea2. Share .dryrun with
# the other scripts rather than making a third empty tree.
os.environ.setdefault("KREA2_BASE_DIR", str(ROOT / ".dryrun"))

from config import KLEIN_OUTPUT_CUSTOM, log  # noqa: E402

SNAPSHOTS = Path(__file__).resolve().parent / "golden"

# A source image whose size is deliberately awkward: 653x431 is neither a
# multiple of 16 nor of 32, and is under every "never upscale" cap. Every
# tab that fits, snaps or caps a size therefore has to actually do it, and
# the number it lands on is in the snapshot.
IMAGE_SIZE = (653, 431)

# What the patched upload returns. The real one is named with a uuid, so
# without a fixed answer here every image handler's workflow would differ
# from itself between two runs.
UPLOADED = "golden_upload.png"


def stub_comfy() -> None:
    """Make `import comfy` work on a machine ComfyUI cannot run on.

    Lifted from scripts/dryrun.py and has to stay in step with it: comfy.py
    detects GPUs at import and raises when there are none, while ui.py
    binds `ensure_alive` at import time so a later patch is not seen.

    Note the difference from the other two scripts: theirs returns False,
    so nothing is ever built. This one returns **True** — see the module
    docstring.
    """
    def ensure_alive(*_args, **_kwargs):
        return True, ""

    try:
        import comfy
    except RuntimeError as exc:            # no NVIDIA GPU visible
        log.warning("No GPU detected (%s) - faking the comfy module", exc)
        comfy = types.ModuleType("comfy")
        comfy.GPUS, comfy.GPU_COUNT = [], 1
        comfy.start_comfyui = lambda *a, **k: None
        comfy.wait_for_comfyui = lambda *a, **k: None
        comfy.verify_custom_node = lambda *a, **k: True
        comfy.node_registered = lambda *a, **k: True
        comfy.log_tail = lambda *a, **k: "<golden>"
        sys.modules["comfy"] = comfy
    comfy.ensure_alive = ensure_alive


def handler_module():
    """Where the generate_* functions live.

    `handlers` after Section 2 step 1 lifts them out of `ui`, and `ui`
    before it. Asking for the former first is what lets the same snapshots
    verify the move: the point of a pure move is that this file does not
    have to change, and that the JSON does not either.
    """
    try:
        import handlers
    except ImportError:
        import ui as handlers            # pre-split layout
    return handlers


class Capture:
    """The patched `client.run`: keep the workflow, yield plausible events.

    The events are the shape client.py:270 documents — a couple of
    progress ticks and a done — so the handler's own generator loop runs to
    the end and a batch of two really does build two workflows.
    """

    def __init__(self):
        self.workflows = []
        self.settings = None      # the blob prompts.record was handed

    def run(self, workflow, timeout=None):
        self.workflows.append(workflow)
        yield {"type": "progress", "step": 1, "total": 8}
        yield {"type": "progress", "step": 8, "total": 8}
        yield {"type": "done", "images": [str(Path("golden") / "out.png")]}


def _image():
    """One synthesised source image, for the tabs that take an upload."""
    from PIL import Image
    return Image.new("RGB", IMAGE_SIZE, (90, 120, 200))


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def patch(module, capture) -> None:
    """Stub everything that would touch a GPU, a disk or the network.

    Patched on the *handler module's* namespace rather than on the module
    that defines each name, because that is where the handlers resolve
    them: `from workflow import model_file_available` binds a local name,
    and patching `workflow.model_file_available` afterwards would not be
    seen. The same reason scripts/dryrun.py patches `comfy.ensure_alive`
    before ui is imported.
    """
    import client
    import presets
    import prompts

    for name in (
        "model_file_available", "edit_lora_available",
        "v2_model_available", "v2_turbo_lora_available",
        "klein_model_available",
        "flux_model_available", "flux_turbo_lora_available",
        "wan_models_available", "wan_5b_available", "wan_lightning_available",
    ):
        if hasattr(module, name):
            setattr(module, name, lambda *a, **k: True)

    for name in ("v2_status", "v2_edit_status", "klein_status",
                 "reactor_status"):
        if hasattr(module, name):
            setattr(module, name, lambda *a, **k: (True, ""))

    module.comfy_ensure_alive = lambda *a, **k: (True, "")

    # The per-job filename token. Random by design — that is the whole
    # point of it — and it lands *in* the workflow as filename_prefix, so
    # without this every snapshot would differ on every run. Same reason
    # upload_image is pinned to UPLOADED below.
    module._run_tag = lambda: "golden00"

    # Silent by contract in the app; a POST to the licence server here.
    # Kept rather than discarded: the fourth positional is the settings
    # blob the licence server stores, and it is the thing tabschema has to
    # reproduce byte for byte (see check_settings).
    def record(tab, prompt, negative, settings, **_kwargs):
        capture.settings = settings

    prompts.record = record
    presets.save = lambda *a, **k: (True, "stubbed")

    for comfy_client in (client.client, client.wan_client):
        comfy_client.run = capture.run
        comfy_client.upload_image = lambda data, name: UPLOADED
        comfy_client.free_models = lambda *a, **k: True
        comfy_client.interrupt = lambda *a, **k: None


def cases(module) -> dict:
    """handler name -> the exact argument tuple it is called with.

    Every value is deliberately *different* from its neighbours and from
    the control's default. Two adjacent ints that are both 8 would hide
    the swap this file exists to catch, so `cutoff_step` is 7 and
    `total_steps` is 23 rather than both being what the slider ships with.

    The LoRA tails are the two shapes from context.md 4.3: triples
    `(enabled, name, weight)` everywhere the row carries an on/off column,
    which is now every tab but Flux — Flux keeps the bare `(name, weight)`
    pair, a separate folder and a separate pipeline.
    """
    model = module.MODEL_CHOICES[0]
    v2_model = module.V2_MODEL_CHOICES[0]
    klein_model = module.KLEIN_MODEL_CHOICES[0]
    flux_model = module.FLUX_MODEL_CHOICES[-1]      # the raw variant
    image = _image()

    pairs = tuple(v for i in range(module.MAX_LORA_SLOTS)
                  for v in ("None", round(0.35 + i / 100, 2)))
    krea_triples = tuple(v for i in range(module.MAX_LORA_SLOTS)
                         for v in (False, "None", round(0.35 + i / 100, 2)))
    triples = tuple(v for i in range(len(module.KLEIN_LORA_SLOTS))
                    for v in (False, "None", round(0.4 + i / 100, 2)))
    v2_triples = tuple(v for i in range(len(module.V2_LORA_SLOTS))
                       for v in (False, "None", round(0.45 + i / 100, 2)))

    return {
        "generate_single": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            1234567, False, 9, 1.7, "1216×832 (Landscape)", "euler",
            model, 2, False, "", False, "",
        ) + krea_triples,

        "generate_flux": (
            "a lighthouse in a storm, 35mm",
            2345678, False, 11, 3.3, "1024×1024 (Square)", "dpmpp_2m",
            flux_model, 2,
        ) + pairs,

        "generate_klein_edit": (
            image, True, image, "put a red hat on the person",
            3456789, False, klein_model, 6, 1.3, 2.7, "euler", "karras",
            1.25, KLEIN_OUTPUT_CUSTOM, 1.75, 912, 688, 2,
        ) + triples,

        "generate_v2": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            4567890, False, v2_model, "16:9 (Landscape Wide)", 2.25, 32,
            0.35, "res_2m", "beta57", 13, 0.85, 2.4, "unsample", False,
            "🌿 Balanced", 37, "🖼️ Qwen-Image", "decreasing",
            7, 23, 0.65, 140, True, True, 2, False, "", False, "",
        ) + v2_triples,

        "generate_v2_edit": (
            image, True, image, "put a red hat on the person",
            "blurry, watermark", 5678901, False, v2_model,
            640, 3.5, 2.5, "crop (legacy)", 0.35, "res_3m", "normal",
            13, 2.4, "resample", False,
            "🪴 Creative", 61, "🔮 Flux (Dev/Schnell)", "step_cutoff",
            7, 23, 0.65, 140, 2,
        ) + v2_triples,

        "generate_wan_video": (
            image, "the waves roll in", "static, blurry",
            "14B two-expert (best quality, 16 fps)", "Raw (20 steps)",
            6789012, False, 17, 3.1, "720p (sharper, ~3× slower)", 3.25,
            "dpmpp_2m", 2,
        ),
    }


def check_settings(name, args, settings) -> None:
    """tabschema.settings() == what the handler stored. Raises on drift.

    The other half of the preset contract, and the half that cannot be
    checked at import: the two tabs that write a settings blob are the two
    whose handlers guard on `v2_status()` and on the weights being
    downloaded, so reaching the `prompts.record` call at all needs the
    stubs this file already installs.

    tabschema asserts the *Krea 2* blob against handlers._krea_settings at
    import, because that one is a plain function. The V2 blob is built
    inline inside generate_v2, so the only way to see it is to run the
    handler — which is what happens here.

    The inversion in the middle is worth reading twice. `Field.name` is
    the handler parameter name and `fields` is submission order, so
    zipping the two reconstructs exactly the value bag the API would have
    produced from a form. If that invariant ever stops holding, this
    reconstruction is wrong and the comparison fails, which is the
    behaviour you want from a check whose whole premise is the invariant.
    """
    import tabschema

    schema = next((s for s in tabschema.SCHEMAS
                   if s.handler.__name__ == name), None)
    if schema is None or settings is None:
        return
    values = {f.name: value for f, value in zip(schema.named(), args)}
    tail = schema.tail()
    if tail is not None:
        rest = args[len(schema.named()):]
        for index in range(tail.count()):
            for offset, part in enumerate(tail.parts):
                key = tail.value_key(index, part.name)
                values[key] = rest[index * len(tail.parts) + offset]
    ours = schema.settings(values)
    if ours != settings:
        keys = set(ours) | set(settings)
        rows = ["  %s: %r != %r" % (k, ours.get(k), settings.get(k))
                for k in sorted(keys) if ours.get(k) != settings.get(k)]
        raise SystemExit(
            "%s: tabschema.settings() does not match the blob the handler "
            "stores. Every preset on the licence server is in that shape."
            % name + "\n" + "\n".join(rows)
        )


def snapshot() -> dict:
    """Run every case and collect the workflows each one built."""
    module = handler_module()
    result = {}
    for name, args in cases(module).items():
        capture = Capture()
        patch(module, capture)
        handler = getattr(module, name)
        # Drained, not just started: a generator that is never iterated
        # builds nothing, and the batch counts above are 2 precisely so
        # that stopping early would show up as a missing workflow.
        statuses = []
        for yielded in handler(*args):
            row = yielded if isinstance(yielded, tuple) else (yielded,)
            statuses.append(next((v for v in row if isinstance(v, str)), ""))
        if not capture.workflows:
            raise SystemExit(
                "%s built no workflow — it bailed out before the seam. "
                "Last status: %s"
                % (name, statuses[-1] if statuses else "(nothing)")
            )
        check_settings(name, args, capture.settings)
        result[name] = capture.workflows
    return result


def _walk(node, path=()):
    """Flatten a nested document to {dotted path: leaf}. See parity.py."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (str(index),))
    else:
        yield ".".join(path), node


def diff(baseline, current) -> list:
    """Human-readable differences, missing/added/changed, in that order."""
    old = dict(_walk(baseline))
    new = dict(_walk(current))
    lines = []
    for key in sorted(set(old) - set(new)):
        lines.append("  - %s = %r   (gone)" % (key, old[key]))
    for key in sorted(set(new) - set(old)):
        lines.append("  + %s = %r   (new)" % (key, new[key]))
    for key in sorted(set(old) & set(new)):
        if old[key] != new[key]:
            lines.append("  ~ %s: %r -> %r" % (key, old[key], new[key]))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze or verify the workflow dict every handler builds.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="compare against the committed snapshots and exit 1 on any "
             "difference",
    )
    parser.add_argument("--out", type=Path, default=SNAPSHOTS)
    args = parser.parse_args()

    import features
    features.resolve([f.key for f in features.FEATURES])

    stub_comfy()
    current = snapshot()
    log.info("Captured %d handlers, %d workflows",
             len(current), sum(len(v) for v in current.values()))

    if not args.check:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, workflows in current.items():
            (args.out / ("%s.json" % name)).write_text(
                json.dumps(workflows, indent=2, ensure_ascii=False,
                           sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print("wrote %d snapshot(s) to %s" % (len(current), args.out))
        return

    failures = 0
    for name, workflows in current.items():
        path = args.out / ("%s.json" % name)
        if not path.exists():
            print("no snapshot at %s - run without --check first" % path)
            failures += 1
            continue
        lines = diff(json.loads(path.read_text(encoding="utf-8")), workflows)
        if lines:
            failures += 1
            print("%s: %d difference(s)" % (path.name, len(lines)))
            print("\n".join(lines))
    if failures:
        sys.exit(1)
    print("golden OK - %d handler(s) build the same workflows as %s"
          % (len(current), args.out.name))


if __name__ == "__main__":
    main()
