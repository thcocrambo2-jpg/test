#!/usr/bin/env python3
"""Record what every tab's form looks like, so a rewrite can prove it kept it.

Operator tool, not part of the shipped app — the same posture as
scripts/dryrun.py, and it borrows that script's weightless boot so it can
run on a laptop with no GPU, no ComfyUI and no models.

    python scripts/parity.py --features all            # write the baseline
    python scripts/parity.py --features all --check    # diff against it

The problem this solves: there are no tests, and the thing being replaced
is a 5,389-line file whose forms are defined by where a `gr.Slider(...)`
happens to sit. "Did the rewrite lose a control on tab 7, or reword a
label, or change a default from 3.5 to 3.0" is not a question anybody can
answer by reading a diff that large. So take the answer from the running
Gradio app while it is still the truth, freeze it, and make the new build
reproduce it.

What it reads is ui._RECIPE_VIEWS: `{tab key: (tab id, [components])}`,
registered by _recipe_view at build time. That registry is the right
source precisely because of the guarantee in its own docstring — the list
is written once and used as the click's `inputs=`, so what it records
cannot drift from what the tab submits. Walking it gets the controls in
submission order, which is also the order the 28-positional handlers
expect and the order recipes are keyed by.

After the rewrite, tabschema.py emits this same document and `--check` is
the diff.
"""

import argparse
import json
import os
import sys
import types
from pathlib import Path

# One level up, and deliberately outside the package so Nuitka never sweeps
# it into the binary. Same reasoning as scripts/dryrun.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Before config is imported: it makes its directory tree at import time and
# the shipped default is the pod path /workspace/krea2. Share .dryrun with
# scripts/dryrun.py rather than making a second empty tree.
os.environ.setdefault("KREA2_BASE_DIR", str(ROOT / ".dryrun"))

from config import log  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "parity_baseline.json"

# Handlers whose signatures are part of the contract. These are the ones the
# adapter has to map JSON onto positionally, so a silent reorder here is the
# highest-risk failure in the whole rewrite (see also scripts/golden.py).
HANDLERS = (
    "generate_single", "generate_flux", "generate_klein_edit", "generate_v2",
    "generate_inpaint", "generate_edit", "generate_v2_edit",
    "generate_faceswap", "generate_wan_video", "generate_from_json",
    "_run_jobs", "_run_wan_jobs",
)


def stub_comfy() -> None:
    """Make `import comfy` work on a machine ComfyUI cannot run on.

    Lifted from scripts/dryrun.py, and it has to stay in step with it:
    comfy.py detects GPUs at import and raises when there are none, while
    ui.py binds `ensure_alive` at import time so a later patch is not seen.
    Nothing is generated here, but ui must still import.
    """
    def ensure_alive(*_args, **_kwargs):
        return False, "Parity dump - ComfyUI is not running."

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
        comfy.log_tail = lambda *a, **k: "<parity>"
        sys.modules["comfy"] = comfy
    comfy.ensure_alive = ensure_alive


def _plain(value, depth=0):
    """A JSON-safe, order-stable rendering of a component attribute.

    Defaults and choice lists are the point of this whole dump, so they
    have to survive a round-trip through JSON unchanged. Anything that is
    not data — a PIL image sitting in a `value=`, a callable default — is
    recorded as a type marker rather than dropped, so that "this used to
    be a callable and now it is None" still shows up as a difference.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth > 3:
        return "<%s>" % type(value).__name__
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, Path):
        return value.as_posix()
    return "<%s>" % type(value).__name__


def describe(component):
    """Everything about one control that a rewrite could silently change.

    Deliberately not `vars(component)`: Gradio hangs a great deal off a
    component that shifts between point releases, and a dump that changes
    when Gradio does would cry wolf. This is the closed set the React form
    has to reproduce — what it is, what it says, what it starts as, and
    what values it will accept.
    """
    record = {
        "type": type(component).__name__,
        "label": (getattr(component, "label", None) or "").strip() or None,
    }
    for attr in ("value", "minimum", "maximum", "step", "lines", "placeholder",
                 "interactive", "visible", "multiselect", "elem_id"):
        if hasattr(component, attr):
            record[attr] = _plain(getattr(component, attr))
    choices = getattr(component, "choices", None)
    if choices is not None:
        # Gradio 6 normalises choices to [(label, value)]. Keep both halves:
        # the label is what the user picks and the value is what the handler
        # receives, and a rewrite can get either one wrong on its own.
        record["choices"] = _plain(list(choices))
    return record


def snapshot():
    """The whole document: every tab's form, plus the handler signatures."""
    import inspect

    import ui

    tabs = {}
    for key, (tab_id, components) in ui._RECIPE_VIEWS.items():
        tabs[str(key)] = {
            "tab_id": tab_id,
            "controls": [describe(component) for component in components],
        }

    handlers = {}
    for name in HANDLERS:
        fn = getattr(ui, name, None)
        if fn is None:
            # Recorded as null rather than skipped: a handler that stops
            # existing is exactly the kind of change this is here to catch.
            handlers[name] = None
            continue
        sig = inspect.signature(fn)
        handlers[name] = {
            "positional": [p.name for p in sig.parameters.values()
                           if p.kind is p.POSITIONAL_OR_KEYWORD],
            "varargs": next((p.name for p in sig.parameters.values()
                             if p.kind is p.VAR_POSITIONAL), None),
            "keyword_only": [p.name for p in sig.parameters.values()
                             if p.kind is p.KEYWORD_ONLY],
        }

    return {"version": 1, "tabs": tabs, "handlers": handlers}


def _walk(node, path=()):
    """Flatten a nested document to {dotted path: leaf}.

    A structural diff of two deep dicts is unreadable; a diff of two flat
    key-to-value maps names the exact control and the exact attribute that
    moved, which is the only form of this report anybody can act on.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (str(index),))
    else:
        yield ".".join(path), node


def diff(baseline, current, ignore=()):
    """Human-readable differences, missing/added/changed, in that order.

    `ignore` names path segments to drop before comparing, so that
    --structure-only can take the disk-derived choice lists out of the
    picture without needing a second kind of baseline file.
    """
    def keep(key):
        return not any(("." + segment + ".") in ("." + key + ".")
                       for segment in ignore)

    old = {k: v for k, v in _walk(baseline) if keep(k)}
    new = {k: v for k, v in _walk(current) if keep(k)}
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
        description="Freeze or verify every tab's form against a baseline.",
    )
    parser.add_argument(
        "--features", metavar="LIST", default="all",
        help='force this feature set, e.g. "single,klein" or "all" (default). '
             "Without a full set the dump is partial and must not be written "
             "over a baseline taken with all of them.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="compare against the baseline and exit 1 on any difference",
    )
    parser.add_argument(
        "--structure-only", action="store_true",
        help="ignore `choices` when comparing. What a dropdown offers is "
             "read off the disk (the LoRA lists come from available_lora_"
             "files()), so a baseline taken on a laptop and a --check run "
             "on a pod differ for reasons that are not regressions. The "
             "control structure itself does not vary: slot counts come "
             "from the static V2_LORA_STACK / KLEIN_LORA_STACK, and only "
             "the enabled flag reads the disk.",
    )
    parser.add_argument("--out", type=Path, default=BASELINE)
    args = parser.parse_args()

    import features

    keys = ([f.key for f in features.FEATURES] if args.features == "all" else
            [t.strip() for t in args.features.replace(";", ",").split(",")
             if t.strip()])
    features.resolve(keys)
    log.info("Features - %s", features.summary())

    stub_comfy()

    # Imported last, and only now: ui.py builds its gr.Blocks at import
    # time, so this line is where the tabs are decided.
    import ui  # noqa: F401

    current = snapshot()
    log.info("Captured %d tabs, %d controls, %d handlers",
             len(current["tabs"]),
             sum(len(t["controls"]) for t in current["tabs"].values()),
             len(current["handlers"]))

    if not args.check:
        args.out.write_text(
            json.dumps(current, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("wrote %s" % args.out)
        return

    if not args.out.exists():
        sys.exit("no baseline at %s - run without --check first" % args.out)

    baseline = json.loads(args.out.read_text(encoding="utf-8"))
    ignore = ("choices",) if args.structure_only else ()
    lines = diff(baseline, current, ignore)
    if not lines:
        note = " (structure only)" if args.structure_only else ""
        print("parity OK - %d tabs match %s%s"
              % (len(current["tabs"]), args.out.name, note))
        return
    print("%d difference(s) from %s:" % (len(lines), args.out.name))
    print("\n".join(lines))
    sys.exit(1)


if __name__ == "__main__":
    main()
