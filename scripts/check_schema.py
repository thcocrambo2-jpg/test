#!/usr/bin/env python3
"""Diff tabschema.py against the parity baseline, control by control.

    python scripts/check_schema.py                  # structure only
    python scripts/check_schema.py --choices        # choices too

What this is for
----------------
scripts/parity.py froze what the *Gradio* forms contain. tabschema.py is
the hand-written claim that the React forms contain the same thing. This
is the diff between the two, and it is the only thing standing between a
careful transcription and a plausible one.

The labels are the part that matters most, and not for the reason it
looks. A label is not only what a control says: `.recipes.jsonl` on every
live pod stores `[[label, value], ...]` positionally and reads it back by
matching the label against the control at that index (ui._use_recipe). A
label reworded by one character does not fail — that one control silently
keeps whatever it had while the rest of the recipe loads. So the check has
to be character-for-character, including the emoji.

Defaults matter for a quieter reason: a default is what an untouched form
submits, so a default that drifts changes every picture made by someone
who did not touch that control.

`--choices` is off by default for the same reason
`parity.py --structure-only` exists: the LoRA dropdowns are
`available_lora_files()`, a listing of the disk this happens to run on, so
a laptop and a pod disagree for reasons that are not regressions.
Structure does not vary — slot counts come from the static V2_LORA_STACK /
KLEIN_LORA_STACK, and only the enabled flag and the choice list read disk.

Not a Gradio importer: it reads the committed baseline JSON and
tabschema.py, so it runs with no GPU, no ComfyUI and (unlike parity.py)
without constructing a Blocks tree.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KREA2_BASE_DIR", str(ROOT / ".dryrun"))

BASELINE = Path(__file__).resolve().parent / "parity_baseline.json"

# Schema kind -> the Gradio class the baseline recorded. Two kinds map to
# Textbox because Gradio had one component for a line and a paragraph and
# this app does not; the `lines` comparison keeps them apart anyway.
KIND_TO_GRADIO = {
    "text": "Textbox",
    "textarea": "Textbox",
    "number": "Number",
    "slider": "Slider",
    "select": "Dropdown",
    "radio": "Radio",
    "bool": "Checkbox",
    "image": "Image",
    "mask": "ImageEditor",
    "file": "File",
}

# Attributes the baseline records that a schema field can be compared on.
# `interactive`, `visible`, `elem_id` and `multiselect` are deliberately
# absent: they are Gradio's rendering flags, and the React form expresses
# the same intent with `show_if` and CSS rather than with a component
# property. `placeholder` is absent because Gradio's `info=` and its
# `placeholder=` both landed in the same field and the baseline kept only
# one of them.
COMPARED = ("label", "value", "minimum", "maximum", "step", "lines")

# Where the schema is deliberately *stricter* than Gradio was, by control
# label and attribute. Listed rather than tolerated silently: each of
# these is a decision somebody made, and the next person to see it in a
# diff deserves the reason rather than a rule that swallows a whole class
# of difference.
#
# All three are gr.Number, which in Gradio carries no bounds at all — so
# a negative seed or a zero-width output was a form the UI would happily
# submit and ComfyUI would reject a minute later, on a worker thread,
# into a status box. Floors are the same answer given earlier.
TIGHTENED = {
    ("Seed", "minimum"): (None, 0,
                          "a negative seed is not a seed; there was no "
                          "floor because gr.Number has none"),
    ("Width (custom mode)", "minimum"): (None, 64,
                                         "below one VAE tile the graph "
                                         "cannot build"),
    ("Height (custom mode)", "minimum"): (None, 64,
                                          "below one VAE tile the graph "
                                          "cannot build"),
}


def _baseline_value(control, kind):
    """The baseline's `value`, normalised the way a schema default is.

    Gradio serialises an untouched Textbox as null and hands the handler
    an empty string; the schema stores the empty string. Same fact, two
    spellings, and comparing them raw would report ten differences that
    are not differences.
    """
    value = control.get("value")
    if kind in ("text", "textarea"):
        return value if isinstance(value, str) else ""
    if kind == "bool":
        return value is True
    return value


def _schema_controls(schema):
    """Every control the schema declares, in submission order.

    The LoRA tail is expanded here rather than left as one field, because
    that is how Gradio recorded it: eight dropdowns and eight sliders, not
    one stack. Comparing the expanded form is what proves the pair/triple
    shape is right, which is the thing that shifts every argument after it
    when it is wrong.
    """
    rows = []
    for f in schema.named():
        rows.append({
            "kind": f.kind, "label": f.label, "value": f.initial(),
            "minimum": f.lo, "maximum": f.hi, "step": f.step,
            "lines": f.lines, "choices": list(f.options()) or None,
        })
    tail = schema.tail()
    if tail is not None:
        for index, slot in enumerate(tail.rows()):
            for part in tail.parts:
                rows.append({
                    "kind": part.kind,
                    "label": part.label.format(n=index + 1),
                    "value": slot[part.name],
                    "minimum": part.lo, "maximum": part.hi,
                    "step": part.step, "lines": part.lines,
                    "choices": list(part.options()) or None,
                })
    return rows


def compare(baseline, schema, compare_choices, notes):
    """Differences for one tab, as lines anybody can act on.

    Deliberate tightenings land in `notes` rather than in the return
    value: they are reported on every run, and they are not failures.
    """
    theirs = baseline["controls"]
    ours = _schema_controls(schema)
    lines = []
    if len(theirs) != len(ours):
        lines.append("  control count: baseline %d, schema %d"
                     % (len(theirs), len(ours)))
    for index in range(max(len(theirs), len(ours))):
        them = theirs[index] if index < len(theirs) else None
        us = ours[index] if index < len(ours) else None
        if them is None:
            lines.append("  + [%d] %r   (schema has one more)"
                         % (index, us["label"]))
            continue
        if us is None:
            lines.append("  - [%d] %r   (the schema dropped it)"
                         % (index, them["label"]))
            continue
        want_type = KIND_TO_GRADIO.get(us["kind"])
        if them["type"] != want_type:
            lines.append("  ~ [%d] %r type: %s -> %s (kind %r)"
                         % (index, them["label"], them["type"], want_type,
                            us["kind"]))
        for attr in COMPARED:
            mine = us[attr] if attr != "value" else us["value"]
            theirs_value = (_baseline_value(them, us["kind"])
                            if attr == "value" else them.get(attr))
            # A number that is 8 in one place and 8.0 in the other is the
            # same number; Gradio and a Python literal disagree on which.
            if isinstance(mine, (int, float)) and \
                    isinstance(theirs_value, (int, float)) and \
                    not isinstance(mine, bool) and \
                    not isinstance(theirs_value, bool):
                if float(mine) == float(theirs_value):
                    continue
            if mine == theirs_value:
                continue
            allowed = TIGHTENED.get((them["label"], attr))
            if allowed is not None and (theirs_value, mine) == allowed[:2]:
                notes.add("%s %s: %r -> %r  (%s)"
                          % (them["label"], attr, theirs_value, mine,
                             allowed[2]))
                continue
            lines.append("  ~ [%d] %r %s: %r -> %r"
                         % (index, them["label"], attr, theirs_value, mine))
        if compare_choices:
            if (them.get("choices") is None) != (us["choices"] is None):
                lines.append("  ~ [%d] %r choices present: %s -> %s"
                             % (index, them["label"],
                                them.get("choices") is not None,
                                us["choices"] is not None))
            elif them.get("choices") is not None:
                # Gradio 6 normalises choices to [label, value] pairs; the
                # schema keeps one string because label and value have
                # never differed in this app.
                mine = us["choices"]
                theirs_value = [c[1] if isinstance(c, list) else c
                                for c in them["choices"]]
                if mine != theirs_value:
                    lines.append("  ~ [%d] %r choices: %r -> %r"
                                 % (index, them["label"], theirs_value, mine))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diff tabschema.py against scripts/parity_baseline.json.",
    )
    parser.add_argument(
        "--choices", action="store_true",
        help="compare the choice lists too. Off by default: the LoRA "
             "dropdowns are read off this machine's disk, so a laptop and "
             "a pod differ for reasons that are not regressions.",
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    args = parser.parse_args()

    import features
    features.resolve([f.key for f in features.FEATURES])

    import tabschema

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    failures, checked = 0, 0
    notes = set()

    for key, tab in baseline["tabs"].items():
        schema = tabschema.BY_KEY.get(key)
        if schema is None:
            print("%s: the baseline has this tab and tabschema.py does not"
                  % key)
            failures += 1
            continue
        checked += 1
        if schema.tab_id != tab["tab_id"]:
            print("%s: tab_id %r -> %r  (recipes are keyed on it)"
                  % (key, tab["tab_id"], schema.tab_id))
            failures += 1
        lines = compare(tab, schema, args.choices, notes)
        if lines:
            failures += 1
            print("%s: %d difference(s)" % (key, len(lines)))
            print("\n".join(lines))

    extra = set(tabschema.BY_KEY) - set(baseline["tabs"])
    for key in sorted(extra):
        print("%s: tabschema.py has this tab and the baseline does not" % key)
        failures += 1

    # The other half of the contract: the handler signatures. tabschema
    # asserts these at import, so reaching this line means they held —
    # saying so is what makes a green run mean something.
    for schema in tabschema.SCHEMAS:
        handler = baseline["handlers"].get(schema.handler.__name__)
        if handler is None:
            continue
        declared = [f.name for f in schema.named()]
        if declared != handler["positional"]:
            print("%s: field names do not match the baseline signature\n"
                  "  baseline: %s\n  schema:   %s"
                  % (schema.key, handler["positional"], declared))
            failures += 1

    if notes:
        print("deliberately stricter than Gradio was:")
        for line in sorted(notes):
            print("  * %s" % line)

    if failures:
        sys.exit(1)
    note = "" if args.choices else " (structure only)"
    print("schema OK - %d tab(s) match %s%s"
          % (checked, args.baseline.name, note))


if __name__ == "__main__":
    main()
