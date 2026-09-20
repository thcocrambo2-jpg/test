"""One declarative schema per tab — what a form is, in one place.

The keystone of the web layer. "What is on this tab" has five separate
consumers, and the React form that draws it is in another language, in
another directory, built by another toolchain — so nothing keeps those
five in step by proximity. They have to be one object, with everything
else derived from it:

  1. **The React form.** `to_json()` is what `/api/v1/schema/{tab}` serves.
  2. **API validation.** `coerce()` — the submit path, where an illegal
     value is a 422 rather than something the handler has to survive.
  3. **Recipe labels.** `recipe_fields()` writes `[[label, value], ...]`
     rows — live pods have a `.recipes.jsonl` keyed on that text, so a
     reworded label orphans history silently.
  4. **The preset settings dict.** `settings()` rebuilds the blob
     `_krea_settings` / `generate_v2` store on the licence server, and
     `preset_values()` reads one back into the controls.
  5. **The positional call.** `call_args()` — and that is the whole
     positional adapter, because of the invariant below.

The invariant
-------------
**`Field.name` is the handler's parameter name.** Not "maps to", not
"looks like" — is. That is free, because the signatures already read
`prompt, negative, seed, randomize, steps, cfg, ...`, and it is what makes
`call_args` four lines instead of a lookup table nobody can audit.

`_assert_signatures()` at the bottom of this module enforces it against
`inspect.signature(handler)` at import, the same way `features.FEATURES`
validates its own registry. **This is the most important defensive
measure in the module.** Without it, a parameter renamed in
`ember.generation.handlers` and not here is a silent argument shift, and
`generate_v2` takes 31 of them.

The two coercion paths
----------------------
"Put a value in a control" is two different jobs, and they want opposite
things, so they are two functions:

* `coerce()` is the **submit** path. The value came from a form this
  server just described. An out-of-range number or an unknown dropdown
  value is a bug or an attack, and it is a 422.
* `restore()` is the **recipe / preset apply** path. The value came from
  another pod, possibly from an older build. An unknown choice leaves the
  control alone and a number is clamped, never an error — the rest of the
  recipe still loads.

`restore()` runs server-side rather than in the browser because the
choices for a Model or LoRA dropdown are **this pod's catalogue** — the
feature's lists as the licence server answered them at startup
(ember.licensing.catalog). The browser only ever sees them through this
module.

Ids and labels
--------------
The Krea tabs' Model and LoRA dropdowns, and the MiniMax tabs' LoRA
dropdowns, carry catalogue *ids* as their values — what presets, prompts
and the handlers all speak — and the record's name as what the user
reads. A Field whose values are ids has `labels`, and `to_json()` ships
them as `choiceLabels` next to `choices`; every other Field's value is its
own label, as it always was.

What is deliberately not here
-----------------------------
Per-control reactivity. Resetting Steps and CFG when the model changes,
or swapping a Wan mode's defaults in, is a lookup in data the pod already
holds — each catalogue model record's steps, CFG and turbo LoRA, and the
Wan pipeline's WAN_MODE_DEFAULTS. The API ships that data in
`/api/v1/catalog` (see `catalog()`) and React applies it, which is one
round trip saved per keystroke and one fewer copy of the same three
numbers.
"""

import inspect
from dataclasses import dataclass, field as dc_field, replace
from typing import Any, Callable

# As `assets`: this module has a catalog() of its own — the /catalog answer.
from ember.licensing import catalog as assets
from ember import features
from ember.licensing import presets
from ember.generation import handlers
from ember.logs import log
from ember.pipelines.krea2.constants import (
    DEFAULT_RESOLUTION,
    RESOLUTION_PRESETS,
    SAMPLERS,
)
from ember.pipelines.krea2_v2.constants import (
    V2_ASPECT_RATIOS,
    V2_DEFAULT_ASPECT,
    V2_DEFAULT_MEGAPIXELS,
    V2_DEFAULT_MULTIPLE,
    V2_DEFAULT_NEGATIVE,
    V2_SAMPLER_DEFAULTS,
    V2_SAMPLER_MODES,
    V2_SAMPLER_NAMES,
    V2_SCHEDULERS,
    V2_VARIANCE_DEFAULTS,
    V2_VARIANCE_MODEL_TYPES,
    V2_VARIANCE_PRESETS,
    V2_VARIANCE_SCHEDULES,
)
from ember.pipelines.krea2_v2_edit.constants import (
    V2_EDIT_DEFAULT_GROUNDING,
    V2_EDIT_DEFAULT_REF_BOOST,
    V2_EDIT_FIT_MODES,
)
from ember.pipelines.minimax.constants import (
    MINIMAX_ASPECT_RATIOS,
    MINIMAX_DEFAULT_ASPECT,
    MINIMAX_DEFAULT_RESOLUTION,
    MINIMAX_DEFAULT_SECONDS,
    MINIMAX_DEFAULTS,
    MINIMAX_MAX_SECONDS,
    MINIMAX_MIN_SECONDS,
    MINIMAX_RESOLUTIONS,
    MINIMAX_T2V_RESOLUTIONS,
)
from ember.pipelines.wan.constants import (
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_DEFAULT_NEGATIVE,
    WAN_DEFAULT_RESOLUTION,
    WAN_FPS,
    WAN_MAX_SECONDS,
    WAN_MODE_DEFAULTS,
    WAN_RESOLUTIONS,
    WAN_VARIANT,
)

Key = features.Key


class Invalid(ValueError):
    """A submitted value the schema will not accept. api.py answers 422.

    Carries the field name so the message can say which control, which is
    the difference between a form the customer can fix and one that just
    says no.
    """

    def __init__(self, field: str, message: str):
        super().__init__("%s: %s" % (field, message))
        self.field = field
        self.message = message


def _resolve(value):
    """A default or a choice list, which may be a callable.

    Callables are how the catalogue-derived lists stay honest. The
    catalogue is loaded by app.py after the licence check, which is after
    this module could have captured anything at import; and a label that
    says whether a LoRA has downloaded is a fact about this disk *now*.
    Resolving late gives the API both without a restart.
    """
    return value() if callable(value) else value


# ─────────────────────────────────────────────────────────── the pieces

@dataclass(frozen=True)
class Repeat:
    """The handler's `*varargs` tail, as a repeating row of sub-fields.

    Every tab submits **triples** `(enabled, lora id, weight)`, and the
    order shifts every argument after it — see
    docs/architecture/web-ui.md, "Submission order is load-bearing". The
    row carries a per-row on/off checkbox, so switching one off keeps its
    LoRA instead of resetting the dropdown to "None".

    `parts` is the submission order *within* one slot, so `call_args`
    flattens `slots x parts` and that is the tail. `slots()` returns one
    dict of per-slot default overrides each, because V2 has one row per
    LoRA in its feature's list (off, at the LoRA's default strength)
    rather than a blank row repeated N times.

    `key` is the prefix the browser sends values under (`lora.0.weight`).
    It is not the varargs parameter name, which is `lora_slots` on every
    tab and would make the wire format read oddly.
    """

    parts: tuple
    slots: Callable[[], tuple]
    key: str = "lora"
    title: str = "LoRA stack"

    def rows(self) -> tuple:
        """Per-slot default dicts, part name -> value, guarded for choices."""
        out = []
        for row in self.slots():
            values = {}
            for part in self.parts:
                if part.name in row:
                    value = row[part.name]
                    if part.choices is not None:
                        options = part.options()
                        if value not in options:
                            value = part.default
                    values[part.name] = value
                else:
                    values[part.name] = _resolve(part.default)
            out.append(values)
        return tuple(out)

    def count(self) -> int:
        return len(self.slots())

    def value_key(self, index: int, part: str) -> str:
        return "%s.%d.%s" % (self.key, index, part)


@dataclass(frozen=True)
class Field:
    """One control, in submission order.

    The first ten attributes are the contract the module docstring names.
    The rest are presentation — where the control sits and when it is
    shown — which lives here rather than in the React layout so that
    moving a control is a one-word edit in one language.
    """

    # ── the contract ────────────────────────────────────────────────
    name: str                       # == the handler's parameter name
    label: str                      # recipe-keyed text — see the docstring
    kind: str                       # text|textarea|number|slider|select|...
    default: Any = None             # value, or a callable read late
    choices: Any = None             # sequence, or a callable read late
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    group: str | None = None
    column: str = "left"
    record: bool = True             # goes into the recipe
    preset: str | None = None       # dotted path in the settings blob
    repeat: Repeat | None = None    # marks the *varargs tail

    # ── presentation ────────────────────────────────────────────────
    lines: int | None = None
    placeholder: str | None = None
    hint: str | None = None
    show_if: tuple | None = None     # (other field name, value it must hold)
    wide: bool = False
    # Folds away entirely rather than showing its first few rows.
    #
    # For the negative prompt, which is the only thing wearing it. Two of
    # the tabs default it to ~1,300 characters of comma-separated
    # boilerplate nobody reads twice, and a textarea showing two rows of
    # that is two rows of noise above the control anybody actually came
    # for.
    #
    # Distinct from the length-triggered Expand on every long textarea:
    # that one keeps the box and grows it, this one removes the box.
    collapsed: bool = False
    # RES4LYF builds its sampler and scheduler lists at load time, so a
    # name this build does not list is still a name the node may accept.
    # So the two V2 dropdowns accept a value that is not in their own
    # list; without this flag a preset from a pod with a newer RES4LYF
    # would 422 on submit.
    allow_custom: bool = False
    # {value: label}, or a callable returning one, for a choice field
    # whose values are catalogue ids: the Model dropdowns and the LoRA
    # stacks. Shipped as `choiceLabels`; None means every value is its own
    # label, which is every other field.
    labels: Any = None

    def options(self) -> tuple:
        """This field's choices, resolved. Empty for a non-choice field."""
        return tuple(_resolve(self.choices) or ())

    def initial(self):
        """The value a fresh form starts on."""
        return _resolve(self.default)

    def choice_labels(self) -> dict | None:
        """{value: label} for an id-valued choice field, else None."""
        if self.labels is None:
            return None
        return dict(_resolve(self.labels) or {})

    # ── the two coercion paths ──────────────────────────────────────

    def coerce(self, value):
        """Submit path: return the value the handler should be called with.

        Raises Invalid rather than repairing. The value came from a form
        this server described seconds ago, so anything outside it is a bug
        or an attack and neither is improved by guessing.
        """
        kind = self.kind
        if kind == "image":
            return value                     # resolved by api.py's uploads
        if kind == "bool":
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "on")
            return bool(value)
        if kind in ("text", "textarea"):
            return "" if value is None else str(value)
        if kind in ("number", "slider"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise Invalid(self.name, "expected a number, got %r" % (value,))
            if self.lo is not None and number < float(self.lo):
                raise Invalid(self.name, "must be at least %g" % self.lo)
            if self.hi is not None and number > float(self.hi):
                raise Invalid(self.name, "must be at most %g" % self.hi)
            # Whole numbers stay whole. A seed handed to the handler as
            # 42.0 becomes "42.0" in a filename prefix and a float in the
            # recipe, and the difference is visible on disk.
            if self.step is not None and float(self.step).is_integer():
                return int(round(number))
            return number
        if kind in ("select", "radio"):
            text = "" if value is None else str(value)
            options = self.options()
            if options and text not in options and not self.allow_custom:
                raise Invalid(self.name, "%r is not one of the choices" % text)
            return text
        raise Invalid(self.name, "unknown field kind %r" % kind)

    def restore(self, value, current=_resolve):
        """Recipe / preset path: (ok, value). `ok` False means leave it alone.

        The whole cross-pod safety story, in two rules:

          * a choice this pod does not offer leaves the control where it
            was — a select handed a value outside its options is a
            *broken* control rather than a wrong one;
          * a number outside this build's range is clamped into it rather
            than dropped, because the range is a property of this build
            and not of the recipe. The recipe stays as close as this UI
            can express it.
        """
        kind = self.kind
        if kind == "image":
            return False, None               # never stored — see _UNRECORDED
        if kind == "bool":
            return True, bool(value)
        if kind in ("text", "textarea"):
            return True, "" if value is None else str(value)
        if kind in ("number", "slider"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return False, None
            if self.lo is not None:
                number = max(float(self.lo), number)
            if self.hi is not None:
                number = min(float(self.hi), number)
            if self.step is not None and float(self.step).is_integer():
                number = int(round(number))
            return True, number
        if kind in ("select", "radio"):
            options = self.options()
            if value in options or (self.allow_custom and value):
                return True, value
            return False, None
        return False, None

    def to_json(self) -> dict:
        """The wire shape `webui/src/api/types.ts:Field` describes."""
        row = {
            "name": self.name,
            "type": self.kind,
            "label": self.label,
            "default": self.initial(),
            "column": self.column,
        }
        if self.group:
            row["group"] = self.group
        if self.lo is not None:
            row["min"] = self.lo
        if self.hi is not None:
            row["max"] = self.hi
        if self.step is not None:
            row["step"] = self.step
        if self.choices is not None:
            row["choices"] = list(self.options())
        labels = self.choice_labels()
        if labels is not None:
            row["choiceLabels"] = labels
        if self.allow_custom:
            row["allowCustom"] = True
        if self.lines is not None:
            row["lines"] = self.lines
        if self.placeholder:
            row["placeholder"] = self.placeholder
        if self.hint:
            row["hint"] = self.hint
        if self.show_if:
            row["showIf"] = {"field": self.show_if[0], "equals": self.show_if[1]}
        if self.wide:
            row["wide"] = True
        if self.collapsed:
            row["collapsed"] = True
        return row


@dataclass(frozen=True)
class Group:
    """How a tab's fields are gathered for the eye. Layout, not contract."""

    id: str
    title: str
    renderer: str = "default"        # default|seed|sampler|variance
    collapsible: bool = False
    default_open: bool = True
    dense: bool = False
    column: str | None = None

    def to_json(self) -> dict:
        row = {"id": self.id, "renderer": self.renderer,
               "title": self.title}
        if self.collapsible:
            row["collapsible"] = True
            row["defaultOpen"] = self.default_open
        if self.dense:
            row["dense"] = True
        if self.column:
            row["column"] = self.column
        return row


@dataclass(frozen=True)
class TabSchema:
    """One tab: everything about it that is not a pixel."""

    # ── the contract ────────────────────────────────────────────────
    key: Key                        # features.Key — what the licence gates
    handler: Callable               # the generator in handlers.py
    lane: str                       # jobqueue lane; video gets its own
    prompt_field: str               # names the queue row
    result_keys: tuple              # what the handler's yield tuple means
    fields: tuple                   # SUBMISSION ORDER. Never reorder.

    # ── presentation ────────────────────────────────────────────────
    tab_id: str = ""                # the tab id recipes are keyed on
    label: str = ""                 # falls back to features.label_for()
    icon: str = ""
    blurb: str = ""
    category: str = "generate"      # generate|edit|video|library
    route: str = ""
    output: str = "image"           # image|video
    submit_label: str = "Generate"
    groups: tuple = ()
    preset_tab: str | None = None   # presets.TAB_* this tab's dropdown reads
    preset_note: str = ""
    # Which list in catalog()["models"] this tab's Model dropdown is
    # naming — the tab's feature key, since each Krea feature has its own
    # list in the catalogue. It is what lets the browser do what
    # krea_model_changed and its three siblings did: pick a model, get its
    # step and CFG defaults and its trigger words, with no round trip.
    model_registry: str | None = None

    # ── derived ─────────────────────────────────────────────────────

    def named(self) -> tuple:
        """The plain fields — everything but the repeating tail."""
        return tuple(f for f in self.fields if f.repeat is None)

    def tail(self) -> Repeat | None:
        """The repeating tail, or None for a tab without one."""
        return next((f.repeat for f in self.fields if f.repeat), None)

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    def title(self) -> str:
        """What this tab is called. The licence server wins — see
        features.label_for, which is why this is not a constant."""
        return features.label_for(self.key)

    # ── 1. the React form ───────────────────────────────────────────

    def to_json(self) -> dict:
        tail = self.tail()
        return {
            "key": str(self.key),
            "tabId": self.tab_id,
            "handler": self.handler.__name__,
            "label": self.title(),
            "icon": self.icon,
            "blurb": self.blurb,
            "category": self.category,
            "route": self.route,
            "output": self.output,
            "submitLabel": self.submit_label,
            "ready": True,
            "promptField": self.prompt_field,
            "resultKeys": list(self.result_keys),
            "presetTab": self.preset_tab,
            "presetNote": self.preset_note,
            "modelRegistry": self.model_registry,
            "fields": [f.to_json() for f in self.named()],
            "groups": [g.to_json() for g in self.groups],
            "lora": _tail_json(tail),
        }

    # ── 2. API validation ───────────────────────────────────────────

    def coerce(self, raw: dict) -> dict:
        """A submitted value bag -> the same bag, checked and typed.

        Missing keys fall back to the field's own default rather than
        failing: a browser that has not been reloaded since a control was
        added should submit the other twenty-nine and get the new one at
        its default.
        """
        values = {}
        for f in self.named():
            values[f.name] = f.coerce(raw.get(f.name, f.initial()))
        tail = self.tail()
        if tail is not None:
            for index, row in enumerate(tail.rows()):
                for part in tail.parts:
                    key = tail.value_key(index, part.name)
                    values[key] = part.coerce(raw.get(key, row[part.name]))
        return values

    def defaults(self) -> dict:
        """The value bag a fresh form starts on."""
        values = {f.name: f.initial() for f in self.named()}
        tail = self.tail()
        if tail is not None:
            for index, row in enumerate(tail.rows()):
                for part in tail.parts:
                    values[tail.value_key(index, part.name)] = row[part.name]
        return values

    # ── 3. recipe labels ────────────────────────────────────────────

    def recipe_fields(self, values: dict) -> list:
        """[[label, value], ...] in submission order — ui._recipe_fields.

        Byte-compatible with what is already on pods' disks, which is the
        whole point: `.recipes.jsonl` is keyed positionally and read back
        by label, so a reworded label or a moved control orphans the
        history rather than failing loudly. The two rules it reproduces:

          * a control that is not a *setting* — the publish and preset
            tickboxes, `ui._RECIPE_SKIP` — is stored as None, so loading a
            recipe cannot silently re-arm a publish;
          * an uploaded file is stored as None too (`ui._UNRECORDED`), and
            the panel says so rather than restoring nine tenths of a
            recipe in silence.
        """
        rows = []
        for f in self.named():
            value = values.get(f.name)
            keep = f.record and isinstance(value, (str, int, float, bool))
            rows.append([f.label, value if keep else None])
        tail = self.tail()
        if tail is not None:
            for index in range(tail.count()):
                for part in tail.parts:
                    value = values.get(tail.value_key(index, part.name))
                    keep = isinstance(value, (str, int, float, bool))
                    rows.append([part.label.format(n=index + 1),
                                 value if keep else None])
        return rows

    def restore_recipe(self, rows) -> dict:
        """A stored recipe's fields -> the values it is safe to write back.

        Guarded per ui._recipe_update: an unknown choice leaves the
        control alone and an out-of-range number is clamped. A row whose
        label no longer matches the control at that position is skipped
        outright — the recipe was written by a build whose controls sat in
        a different order, and guessing is worse than leaving one control
        where it is.
        """
        values = {}
        controls = list(self._controls())
        for index, (label, field_key, control) in enumerate(controls):
            row = rows[index] if index < len(rows) else None
            if not (isinstance(row, (list, tuple)) and len(row) >= 2):
                continue
            if row[0] != label:
                continue
            ok, value = control.restore(row[1])
            if ok:
                values[field_key] = value
        return values

    def _controls(self):
        """(label, value key, Field) for every control, in submission order."""
        for f in self.named():
            yield f.label, f.name, f
        tail = self.tail()
        if tail is not None:
            for index in range(tail.count()):
                for part in tail.parts:
                    yield (part.label.format(n=index + 1),
                           tail.value_key(index, part.name), part)

    # ── 4. the preset settings dict ─────────────────────────────────

    def settings(self, values: dict) -> dict:
        """The blob the prompt library and the preset store keep.

        Built from `Field.preset`, which is a dotted path — "model",
        "sampler.eta", "variance.cutoff_step" — so the nesting the V2 tab
        stores falls out of the field list rather than out of a second
        hand-written function. The LoRA rows are appended as
        [enabled, lora id, weight], with the form's "None" stored as null
        (handlers.stored_lora — the same call the handlers make).

        `test_settings_match_ui()` asserts this equals `_krea_settings`
        for the Krea2 tab. That local check is the whole proof, because
        the licence server stores the blob opaquely — presetWire() in
        license-validator/src/app.js returns `settings: row.settings || {}`
        and never inspects it.
        """
        blob = {}
        for f in self.named():
            if not f.preset:
                continue
            value = values.get(f.name)
            head, _, rest = f.preset.partition(".")
            if rest:
                blob.setdefault(head, {})[rest] = _cast(f, value)
            else:
                blob[head] = _cast(f, value)
        tail = self.tail()
        if tail is not None:
            rows = []
            for index in range(tail.count()):
                row = [values.get(tail.value_key(index, part.name))
                       for part in tail.parts]
                rows.append([handlers.stored_lora(v) if part.name == "name"
                             else _cast(part, v)
                             for part, v in zip(tail.parts, row)])
            blob["loras"] = rows
        return blob

    def preset_values(self, settings: dict) -> dict:
        """A stored settings blob -> the values it is safe to write back.

        The `restore` half of the same map. Everything ui._sub, ui._rows,
        ui._pick, ui._num and ui._lora_updates did, minus the branching:
        the blob is read as whatever Mongo happened to hold (a V2 row with
        a *string* where `sampler` should be a dict is a shape that has to
        survive), so every lookup goes through a type check first.

        One difference from the guarded scalars, and it is deliberate,
        lifted from ui._lora_updates: a LoRA id this tab does not offer
        becomes "None" **explicitly**, and its row off, rather than being
        left alone. The slots are being reset to a whole other recipe, and
        a leftover LoRA from whatever was loaded before would silently join
        it. A stored null is an empty slot and comes back as "None".
        """
        if not isinstance(settings, dict):
            return {}
        values = {}
        for f in self.named():
            if not f.preset:
                continue
            head, _, rest = f.preset.partition(".")
            source = settings
            if rest:
                nested = settings.get(head)
                if not isinstance(nested, dict):
                    continue
                source, head = nested, rest
            if head not in source:
                continue
            ok, value = f.restore(source[head])
            if ok:
                values[f.name] = value

        tail = self.tail()
        if tail is not None:
            rows = settings.get("loras")
            rows = rows if isinstance(rows, list) else []
            defaults = tail.rows()
            for index in range(tail.count()):
                row = rows[index] if index < len(rows) else None
                row = list(row) if isinstance(row, (list, tuple)) else []
                known = None
                for offset, part in enumerate(tail.parts):
                    key = tail.value_key(index, part.name)
                    stored = row[offset] if offset < len(row) else None
                    if part.name == "name":
                        if stored is None:            # an empty slot
                            known, values[key] = True, assets.NONE
                            continue
                        known = stored in part.options()
                        values[key] = stored if known else assets.NONE
                    elif part.name == "enabled":
                        values[key] = bool(stored) if known is not False else False
                    else:
                        ok, value = part.restore(stored)
                        values[key] = value if ok else defaults[index][part.name]
                # `enabled` is submitted before `name`, so the "this tab
                # does not offer that id" answer is only known after the
                # loop. Re-applied here rather than by reordering the
                # parts, which are submission order and cannot move.
                if known is False and "enabled" in [p.name for p in tail.parts]:
                    values[tail.value_key(index, "enabled")] = False
        return values

    # ── 5. the positional call ──────────────────────────────────────

    def call_args(self, values: dict) -> tuple:
        """The whole positional adapter.

        Four lines because of the invariant: `Field.name` *is* the
        parameter name, and `fields` *is* submission order. Everything
        that could go wrong here has been moved into
        `_assert_signatures()`, which runs at import.
        """
        args = [values[f.name] for f in self.named()]
        tail = self.tail()
        if tail is not None:
            for index in range(tail.count()):
                args.extend(values[tail.value_key(index, part.name)]
                            for part in tail.parts)
        return tuple(args)

    def submit(self, raw: dict) -> tuple:
        """coerce + gate + call_args — the funnel every generation passes.

        The licence check is here as well as on the route because the
        route-level one is a *registration* decision and this is a *call*
        decision, and they fail differently: a route that is accidentally
        registered unconditionally still cannot get past this line. See
        api.py's four layers, and
        docs/architecture/licensing-and-features.md, "The licence gate on
        the API", for why one layer is not enough — `community_prompts`
        has `needs=()`, so the weights backstop that covers every other
        tab does not cover it.
        """
        if not features.enabled(self.key):
            raise PermissionError(
                "%s is not part of this licence." % self.title()
            )
        values = self.coerce(raw)
        return self.call_args(values), values


def _cast(f: Field, value):
    """One value, in the type the settings blob has always stored it as.

    `int(steps)`, `float(cfg)`, `bool(randomize)` — the casts
    `_krea_settings` and `generate_v2` write by hand. Reproduced from the
    field's own kind so the blob is byte-identical to what pods already
    have on the licence server.
    """
    if f.kind == "bool":
        return bool(value)
    if f.kind in ("number", "slider"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        if f.step is not None and float(f.step).is_integer():
            return int(round(number))
        return number
    return value


def _tail_json(tail: Repeat | None):
    """The repeating tail as `webui/src/api/types.ts:LoraSpec`."""
    if tail is None:
        return None
    names = [p.name for p in tail.parts]
    slot = next(p for p in tail.parts if p.name == "name")
    weight = next(p for p in tail.parts if p.name == "weight")
    enabled = next(p for p in tail.parts if p.name == "enabled")
    spec = {
        "shape": "triple",
        "count": tail.count(),
        "title": tail.title,
        "key": tail.key,
        "choices": list(slot.options()),
        # "LoRA {n}" -> "LoRA"; the slot number is the renderer's business.
        "slotLabel": slot.label.replace(" {n}", ""),
        "weightLabel": weight.label,
        "weightMin": weight.lo,
        "weightMax": weight.hi,
        "weightStep": weight.step,
        "weightDefault": _resolve(weight.default),
        "enabledLabel": enabled.label,
        "enabledDefault": bool(_resolve(enabled.default)),
        "parts": names,
        # Per-slot defaults. V2 has one row per LoRA in its feature's
        # list, off, at the LoRA's default strength; Krea2 repeats a blank
        # row.
        "slots": [dict(row) for row in tail.rows()],
    }
    # The LoRA dropdown's values are ids; these are what it shows.
    labels = slot.choice_labels()
    if labels is not None:
        spec["choiceLabels"] = labels
    return spec


# ══════════════════════════════════════════════════════ shared pieces
# Written once and referenced from every tab that has them. The seven
# forms are the same handful of blocks in a different order, which is
# a fact seven hand-written layouts could state only by repeating it.

def _lora_choices(feature):
    """The feature's LoRA ids (plus "None"), read late — see `_resolve`.

    Exactly the catalogue's list for this feature: a file on this disk the
    catalogue does not name is not offered, and a listed LoRA that has not
    downloaded still is (its label says so; the handler skips it).
    """
    return lambda: handlers.lora_choices(feature)


def _lora_labels(feature):
    """{lora id: name} for the same dropdown, "None" included."""
    return lambda: handlers.lora_labels(feature)


def _model_field(feature):
    """The Model dropdown of a Krea tab: its feature's model ids, named.

    The first model in the feature's list is the default, as the
    catalogue defines it.
    """
    return Field("model", "Model", "select",
                 lambda: handlers.default_model(feature),
                 choices=lambda: handlers.model_choices(feature),
                 labels=lambda: handlers.model_labels(feature),
                 group="core", preset="model", wide=True)


def _model_setting(feature, key):
    """The feature's default model's `steps` or `cfg`, read late."""
    return lambda: handlers.model_settings(feature)[key]


def _blank_slots(count):
    """`count` rows at the part defaults — the plain eight-slot stacks."""
    return lambda: tuple({} for _ in range(count))


def _stack_slots(feature):
    """(enabled, lora id, strength) rows — one per LoRA the feature lists.

    V2 does not repeat a blank row: it has a row for every LoRA in its
    feature's list, in that order, all off and each at its record's
    default strength (handlers.v2_lora_slots). The tab's Default preset,
    applied on load, is what switches the usual ones on.
    """
    return lambda: tuple({"enabled": on, "name": name, "weight": strength}
                         for on, name, strength
                         in handlers.v2_lora_slots(feature))


def _triple_tail(feature, slots, title):
    """`(enabled, lora id, weight)` slots — every Krea tab.

    The order inside the row is the submission order of the handler's
    varargs tail, so `enabled` really does come first. Getting it wrong
    shifts every argument after it.

    `slots` is the rows callable rather than a loader, because the two
    families fill their stack from different places: V2 has a row per
    LoRA in its feature's list (`_stack_slots`), the Krea2 family repeats
    a blank row (`_blank_slots`). The dropdown offers the feature's LoRA
    ids either way. Everything else about the row is the same, which is
    the point.
    """
    return Repeat(
        parts=(
            Field("enabled", "On", "bool", False),
            Field("name", "LoRA {n}", "select", assets.NONE,
                  choices=_lora_choices(feature),
                  labels=_lora_labels(feature)),
            Field("weight", "Strength", "slider", 1.0, lo=0.0, hi=2.0,
                  step=0.01),
        ),
        slots=slots,
        title=title,
    )


def _blank_lora_tail(feature):
    """The Krea2 / Krea2 Edit / MiniMax stack: eight blank rows, all off,
    over the feature's list.

    Model-only (`LoraLoaderModelOnly`, in the krea2 and minimax pipelines),
    where the V2 stack is model *and* CLIP, so the title stays plain rather
    than borrowing V2's.
    """
    return _triple_tail(feature,
                        _blank_slots(handlers.MAX_LORA_SLOTS),
                        "LoRA stack")


def _seed_fields(default=42):
    """Seed and the random tick — byte-identical on seven tabs.

    Batch count is deliberately *not* here even though it renders in the
    same row (`group="seed"`). It sits at a different place in every
    signature — last, after the model — and `fields` is submission order,
    so grouping it with its neighbours on screen would have shifted two
    arguments on seven tabs. Which is exactly what _assert_signatures()
    caught the first time this file was written, and the reason that
    check is worth its weight.

    `hi` on the seed is deliberately open. ui._num clamped a restored seed
    to 2**32-1, but the V2 tab's own default is 370102505887178, so that
    clamp would have mangled the shipped value on the way back in. Left
    unbounded above, floored at zero.
    """
    return (
        Field("seed", "Seed", "number", default, lo=0, step=1,
              group="seed", preset="seed"),
        Field("randomize", "🎲 Random seed", "bool", True,
              group="seed", preset="randomize"),
    )


def _batch_field(hi=20):
    """Batch count — renders with the seed row, submits at the end."""
    return Field("batch_count", "Batch count", "slider", 1, lo=1, hi=hi,
                 step=1, group="seed", preset="batch_count")


def _save_fields():
    """Publish and save-preset — per-run decisions, never in a recipe.

    `record=False` is ui._RECIPE_SKIP: these are disarmed after every
    click (ui._reset_after_generate), so putting them in a recipe would
    mean loading one silently re-arms a publish. That is the one thing
    that must never happen by accident.
    """
    return (
        Field("publish", "⭐ Publish this prompt to the library", "bool",
              False, group="save", record=False),
        Field("publish_title", "Card title (optional)", "text", "", lines=1,
              group="save", record=False, show_if=("publish", True)),
        Field("save_preset", "💾 Save these settings as a preset", "bool",
              False, group="save", record=False),
        Field("preset_name", "Preset name", "text", "", lines=1,
              group="save", record=False, show_if=("save_preset", True)),
    )


def _v2_sampler_fields(feature, denoise=True):
    """The ClownsharKSampler block, shared by the V2 and V2 Edit tabs.

    V2 Edit has no Denoise: the source image reaches the model through
    conditioning rather than through the starting latent, so it is pinned
    at 1.0 in the builder. That single difference is the `denoise` flag
    rather than a second copy of the block. Steps and CFG start on the
    feature's default model record's numbers.
    """
    rows = [
        Field("eta", "Eta", "slider", V2_SAMPLER_DEFAULTS["eta"], lo=0.0,
              hi=2.0, step=0.01, group="sampler", preset="sampler.eta"),
        Field("sampler_name", "Sampler", "select", V2_SAMPLER_NAMES[0],
              choices=V2_SAMPLER_NAMES, group="sampler",
              preset="sampler.sampler_name", allow_custom=True),
        Field("scheduler", "Scheduler", "select", V2_SCHEDULERS[0],
              choices=V2_SCHEDULERS, group="sampler",
              preset="sampler.scheduler", allow_custom=True),
        Field("steps", "Steps", "slider",
              _model_setting(feature, "steps"), lo=1, hi=100, step=1,
              group="sampler", preset="sampler.steps"),
    ]
    if denoise:
        rows.append(
            Field("denoise", "Denoise", "slider",
                  V2_SAMPLER_DEFAULTS["denoise"], lo=0.0, hi=1.0, step=0.01,
                  group="sampler", preset="sampler.denoise"))
    rows += [
        Field("cfg", "CFG", "slider", _model_setting(feature, "cfg"),
              lo=0.0, hi=20.0, step=0.1, group="sampler",
              preset="sampler.cfg", hint=_CFG_NOTE),
        Field("sampler_mode", "Sampler mode", "select", V2_SAMPLER_MODES[0],
              choices=V2_SAMPLER_MODES, group="sampler",
              preset="sampler.sampler_mode"),
        Field("bongmath", "bongmath", "bool",
              V2_SAMPLER_DEFAULTS["bongmath"], group="sampler",
              preset="sampler.bongmath"),
    ]
    return tuple(rows)


def _v2_variance_fields():
    """RBG Smart Seed Variance, shared by the V2 and V2 Edit tabs.

    `cutoff_step` (8) and `total_steps` (20) sit next to each other and
    are both small ints. Swapping them raises nothing and quietly makes
    worse pictures — see scripts/golden.py, which exists for this pair.
    """
    d = V2_VARIANCE_DEFAULTS
    return (
        Field("variance_preset", "Preset", "select", d["variance_preset"],
              choices=V2_VARIANCE_PRESETS, group="variance",
              preset="variance.variance_preset"),
        Field("fine_tune_variance", "Fine tune", "slider",
              d["fine_tune_variance"], lo=0, hi=100, step=1,
              group="variance", preset="variance.fine_tune_variance"),
        Field("variance_model_type", "Model type", "select", d["model_type"],
              choices=V2_VARIANCE_MODEL_TYPES, group="variance",
              preset="variance.model_type"),
        Field("variance_schedule", "Schedule", "select",
              d["variance_schedule"], choices=V2_VARIANCE_SCHEDULES,
              group="variance", preset="variance.variance_schedule"),
        Field("cutoff_step", "Cutoff step", "slider", d["cutoff_step"],
              lo=0, hi=100, step=1, group="variance",
              preset="variance.cutoff_step"),
        Field("total_steps", "Total steps", "slider", d["total_steps"],
              lo=1, hi=100, step=1, group="variance",
              preset="variance.total_steps"),
        Field("cutoff_strength", "Cutoff strength", "slider",
              d["cutoff_strength"], lo=0.0, hi=1.0, step=0.1,
              group="variance", preset="variance.cutoff_strength"),
        Field("shift_strength", "Shift strength", "slider",
              d["shift_strength"], lo=0, hi=200, step=1, group="variance",
              preset="variance.shift_strength"),
    )


def _reference_fields(group="reference"):
    """Grounding / reference fidelity / scene fidelity — both Edit tabs."""
    return (
        Field("grounding",
              "Grounding (low = stronger edit, high = keep likeness)",
              "slider", V2_EDIT_DEFAULT_GROUNDING, lo=384, hi=768, step=64,
              group=group, wide=True),
        Field("ref_boost",
              "Reference fidelity (1 = neutral, ~4 = strong likeness, "
              ">10 breaks removals)",
              "slider", V2_EDIT_DEFAULT_REF_BOOST, lo=0.0, hi=10.0, step=0.5,
              group=group, wide=True),
        Field("ref_boost_a", "Scene fidelity (1 = neutral)", "slider", 1.0,
              lo=0.0, hi=10.0, step=0.5, group=group, wide=True),
    )


def _two_image_fields(label1, toggle, label2):
    """Source image, the second-reference toggle and the second image.

    The two Edit tabs share it. The labels are arguments, transcribed
    verbatim; see the module docstring on why that matters.
    """
    return (
        Field("image", label1, "image", None, group="inputs", column="right"),
        Field("use_image2", toggle, "bool", False, group="inputs",
              column="right"),
        Field("image2", label2, "image", None, group="inputs",
              column="right", show_if=("use_image2", True)),
    )


# The groups every tab draws from. `renderer` names the React component
# that lays the body out — SeedRow, SamplerPanel, VariancePanel — which is
# how three tabs share one control cluster without three copies of it.
G_PROMPT = Group("prompt", "Prompt")
G_SEED = Group("seed", "Seed & batch", renderer="seed")
G_SAVE = Group("save", "Publish & presets", collapsible=True,
               default_open=False)
G_SAMPLER = Group("sampler", "Sampler", renderer="sampler", collapsible=True)
G_VARIANCE = Group("variance", "Variance", renderer="variance",
                   collapsible=True, default_open=False)
G_INPUTS = Group("inputs", "Images", column="right")
# Shared rather than written out per tab, so two tabs cannot end up
# titling the same kind of thing differently.
G_CORE = Group("core", "Output", dense=True)
G_REFERENCE = Group("reference", "Reference", dense=True)

# The Wan tab's two radio lists. They name model families rather than
# files, and the strings are what generate_wan_video branches on (`_is_wan_5b`, `mode.startswith("turbo")`) — so they
# are wire values, not labels, and cannot be reworded freely.
WAN_MODEL_CHOICES = ["14B two-expert (best quality, 16 fps)",
                     "5B TI2V (lighter, 24 fps)"]
WAN_MODE_CHOICES = ["Turbo (Lightning, 4 steps)", "Raw (20 steps)"]

# The two prompt boxes every generation tab opens with. The negative's
# label differs per tab (it names the CFG condition, and the V2 tabs call
# it "Negatives"), so only the positive is shared.
_CFG_NOTE = "Above 1 turns the negative prompt on."

# ui._GEN_INFO / ui._EDIT_INFO, verbatim: what a tab's preset dropdown
# says under itself. The Edit tabs offer their generation tab's presets.
GEN_PRESET_NOTE = "Loads every setting below. Your prompt is left alone."
EDIT_PRESET_NOTE = ("The {} tab's presets, minus the dials an edit does not "
                    "have. Your image and instruction are left alone.")

# What every image tab's handler yields, in order. `_freeze` zips these
# onto the tuple, so jobqueue stops carrying a status_index int and the
# panel reads result["status"] instead of result[1]. See jobqueue._freeze.
IMAGE_KEYS = ("images", "status", "seed")
VIDEO_KEYS = ("videos", "latest", "status", "seed")


# ═══════════════════════════════════════════════════ the seven tabs
# Order is the order they appear in the navigation, which is ui.TAB_ORDER's
# order with the two bespoke tabs (Gallery, Prompt Library) taken out —
# those have no form and so no schema.

SCHEMAS = (

    TabSchema(
        model_registry=handlers.KREA_T2I,
        key=Key.KREA_T2I, handler=handlers.generate_single,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="krea2",
        icon="🎨", blurb="Type a sentence, get a photograph.",
        category="generate", route="/generate/krea2",
        submit_label="Generate", preset_tab=presets.TAB_KREA2,
        preset_note=GEN_PRESET_NOTE,
        groups=(G_PROMPT, G_CORE, G_SAMPLER, G_SEED, G_SAVE),
        fields=(
            Field("prompt", "Prompt", "textarea",
                  "A photorealistic golden-hour portrait, natural skin "
                  "texture, shallow depth of field", lines=5, group="prompt"),
            Field("negative", "Negative prompt", "textarea", "", lines=3,
                  group="prompt", collapsed=True, hint=_CFG_NOTE),
            *_seed_fields(),
            Field("steps", "Steps", "slider",
                  _model_setting(handlers.KREA_T2I, "steps"), lo=1, hi=60,
                  step=1, group="sampler", preset="steps"),
            Field("cfg", "CFG", "slider",
                  _model_setting(handlers.KREA_T2I, "cfg"),
                  lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
                  hint=_CFG_NOTE),
            Field("resolution", "Resolution", "select", DEFAULT_RESOLUTION,
                  choices=list(RESOLUTION_PRESETS), group="core",
                  preset="resolution", wide=True),
            Field("sampler", "Sampler", "select", SAMPLERS[0],
                  choices=SAMPLERS, group="sampler", preset="sampler",
                  wide=True),
            _model_field(handlers.KREA_T2I),
            _batch_field(),
            *_save_fields(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_blank_lora_tail(handlers.KREA_T2I)),
        ),
    ),

    TabSchema(
        model_registry=handlers.KREA_V2_T2I,
        key=Key.KREA_V2_T2I, handler=handlers.generate_v2,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="krea2v2",
        icon="🔶", blurb="The V2 pipeline, with the full ClownsharKSampler "
                        "stack.",
        category="generate", route="/generate/krea2-v2",
        submit_label="Generate", preset_tab=presets.TAB_KREA2_V2,
        preset_note=GEN_PRESET_NOTE,
        groups=(G_PROMPT, G_CORE,
                # Controls, so they go with the controls.
                G_SAMPLER, G_VARIANCE,
                Group("post", "Post-processing", dense=True, collapsible=True,
                      default_open=False),
                G_SEED, G_SAVE),
        fields=(
            Field("prompt", "Prompt", "textarea", "", lines=5,
                  group="prompt"),
            Field("negative", "Negative prompt", "textarea",
                  V2_DEFAULT_NEGATIVE, lines=3, group="prompt",
                  collapsed=True, hint=_CFG_NOTE),
            # The seed the source workflow shipped with, kept as-is.
            *_seed_fields(default=370102505887178),
            _model_field(handlers.KREA_V2_T2I),
            Field("aspect", "Resolution", "select", V2_DEFAULT_ASPECT,
                  choices=list(V2_ASPECT_RATIOS), group="core",
                  preset="aspect", wide=True),
            Field("megapixels", "Megapixels", "slider",
                  V2_DEFAULT_MEGAPIXELS, lo=0.5, hi=4.0, step=0.1,
                  group="core", preset="megapixels"),
            Field("multiple", "Multiple of", "slider", V2_DEFAULT_MULTIPLE,
                  lo=8, hi=64, step=8, group="core", preset="multiple"),
            *_v2_sampler_fields(handlers.KREA_V2_T2I),
            *_v2_variance_fields(),
            Field("sharpen", "Sharpen (radius 1, sigma 0.35, alpha 1)",
                  "bool", False, group="post", preset="sharpen", wide=True),
            Field("film_grain", "Film grain (intensity 0.05, scale 1)",
                  "bool", False, group="post", preset="film_grain",
                  wide=True),
            _batch_field(),
            *_save_fields(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_triple_tail(
                      handlers.KREA_V2_T2I,
                      _stack_slots(handlers.KREA_V2_T2I),
                      "LoRA stack — model + CLIP")),
        ),
    ),

    TabSchema(
        model_registry=handlers.KREA_EDIT,
        key=Key.KREA_EDIT, handler=handlers.generate_edit,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="edit",
        icon="✨", blurb="Change one thing about a picture without touching "
                        "the rest.",
        category="edit", route="/edit/krea2-edit", submit_label="Edit",
        preset_tab=presets.TAB_KREA2, preset_note=EDIT_PRESET_NOTE.format("🎨 Krea2"),
        groups=(G_INPUTS, G_PROMPT, G_CORE, G_REFERENCE, G_SAMPLER,
                G_SEED),
        fields=(
            *_two_image_fields("Source image (paste with Ctrl+V)",
                               "➕ Add a second reference (subject)",
                               "Second reference — subject"),
            Field("prompt", "Edit instruction", "textarea",
                  "Remove all her clothes completely, make her fully nude. "
                  "Keep the exact same face, facial features, expression, "
                  "skin tone, hairstyle, body pose, hands position, and "
                  "background. Do not change the face at all.          "
                  "remove clothes exposing her naked average natural shaped "
                  "tits. dont change her face",
                  lines=5, group="prompt"),
            Field("negative", "Negative prompt", "textarea", "", lines=3,
                  group="prompt", collapsed=True, hint=_CFG_NOTE),
            *_seed_fields(),
            Field("steps", "Steps", "slider",
                  _model_setting(handlers.KREA_EDIT, "steps"), lo=1, hi=60,
                  step=1, group="sampler", preset="steps"),
            Field("cfg", "CFG", "slider",
                  _model_setting(handlers.KREA_EDIT, "cfg"),
                  lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
                  hint=_CFG_NOTE),
            Field("sampler", "Sampler", "select", SAMPLERS[0],
                  choices=SAMPLERS, group="sampler", preset="sampler",
                  wide=True),
            *_reference_fields(),
            _model_field(handlers.KREA_EDIT),
            _batch_field(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_blank_lora_tail(handlers.KREA_EDIT)),
        ),
    ),

    TabSchema(
        model_registry=handlers.KREA_V2_EDIT,
        key=Key.KREA_V2_EDIT, handler=handlers.generate_v2_edit,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="v2edit",
        icon="🔷", blurb="Instruction editing on the V2 pipeline.",
        category="edit", route="/edit/krea2-v2-edit", submit_label="Edit",
        preset_tab=presets.TAB_KREA2_V2,
        preset_note=EDIT_PRESET_NOTE.format("🔶 Krea2 V2"),
        groups=(G_INPUTS, G_PROMPT, G_CORE, G_REFERENCE, G_SAMPLER,
                G_VARIANCE, G_SEED),
        fields=(
            *_two_image_fields("Source image (paste with Ctrl+V)",
                               "➕ Add a second reference (subject)",
                               "Second reference — subject"),
            Field("prompt", "Edit instruction", "textarea", "", lines=5,
                  group="prompt"),
            Field("negative", "Negative prompt", "textarea",
                  V2_DEFAULT_NEGATIVE, lines=3, group="prompt",
                  collapsed=True, hint=_CFG_NOTE),
            *_seed_fields(),
            _model_field(handlers.KREA_V2_EDIT),
            *_reference_fields(),
            Field("fit_mode",
                  "Reference geometry (fit = v1.2; the legacy crop is for "
                  "older weights)",
                  "select", V2_EDIT_FIT_MODES[0], choices=V2_EDIT_FIT_MODES,
                  group="reference", wide=True),
            # No Denoise: the source reaches the model through conditioning
            # rather than the starting latent, so the builder pins it at 1.0.
            *_v2_sampler_fields(handlers.KREA_V2_EDIT, denoise=False),
            *_v2_variance_fields(),
            _batch_field(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_triple_tail(
                      handlers.KREA_V2_EDIT,
                      _stack_slots(handlers.KREA_V2_EDIT),
                      "LoRA stack — model + CLIP")),
        ),
    ),

    TabSchema(
        key=Key.WAN_I2V, handler=handlers.generate_wan_video,
        lane=handlers.WAN_LANE, prompt_field="prompt",
        result_keys=VIDEO_KEYS, tab_id="video", output="video",
        icon="🎬", blurb="Turn a still into a few seconds of video.",
        category="video", route="/video/wan", submit_label="Animate",
        groups=(Group("inputs", "Start frame", column="right"), G_PROMPT,
                Group("core", "Model", dense=True),
                Group("sampling", "Sampling", dense=True), G_SEED),
        fields=(
            Field("image", "Start image (paste with Ctrl+V)", "image", None,
                  group="inputs", column="right"),
            Field("prompt", "Motion prompt", "textarea", "", lines=3,
                  group="prompt",
                  placeholder="she turns her head and smiles, gentle camera "
                              "push-in, wind in the hair"),
            Field("negative",
                  "Negative prompt (only used when CFG > 1, i.e. Raw mode)",
                  "textarea", WAN_DEFAULT_NEGATIVE, lines=2, group="prompt", collapsed=True),
            Field("model", "Model", "radio", WAN_MODEL_CHOICES[0],
                  choices=WAN_MODEL_CHOICES, group="core", wide=True),
            Field("mode", "Mode (14B only — the 5B has no Lightning)",
                  "radio",
                  WAN_MODE_CHOICES[0 if WAN_VARIANT == "turbo" else 1],
                  choices=WAN_MODE_CHOICES, group="core", wide=True,
                  show_if=("model", WAN_MODEL_CHOICES[0])),
            *_seed_fields(),
            Field("steps", "Steps", "slider",
                  WAN_MODE_DEFAULTS[WAN_VARIANT]["steps"], lo=1, hi=40,
                  step=1, group="sampling"),
            Field("cfg", "CFG", "slider",
                  WAN_MODE_DEFAULTS[WAN_VARIANT]["cfg"], lo=0.5, hi=8.0,
                  step=0.1, group="sampling"),
            Field("resolution", "Resolution (keeps the source aspect)",
                  "radio", WAN_DEFAULT_RESOLUTION,
                  choices=list(WAN_RESOLUTIONS), group="core", wide=True),
            Field("seconds", "Duration (seconds)", "slider", WAN_MAX_SECONDS,
                  lo=1.0, hi=WAN_MAX_SECONDS, step=0.25, group="sampling",
                  wide=True),
            Field("sampler", "Sampler", "select", "euler",
                  choices=SAMPLERS + ["uni_pc"], group="sampling", wide=True),
            _batch_field(hi=10),
        ),
    ),

    # The two MiniMax tabs. One model, one graph, one download; the core
    # node takes an optional first frame, so the text tab is the image tab
    # minus its upload — which is why the two field lists are the same
    # list with `image` swapped for `aspect`. Both run on the main lane:
    # see handlers._run_wan_jobs on why they never ride the Wan instance.
    # No negative prompt and no CFG, because the model is guidance-distilled
    # like Flux; the prompt carries the sound as well as the motion, since
    # every clip comes back with a soundtrack. The LoRA stack is each tab's
    # own catalogue list, eight blank rows like Krea2's; there are no presets.
    TabSchema(
        key=Key.MINIMAX_I2V, handler=handlers.generate_minimax_video,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=VIDEO_KEYS, tab_id="minimax_i2v", output="video",
        icon="🎥", blurb="Turn a still into a clip that comes with its own "
                        "sound.",
        category="video", route="/video/minimax", submit_label="Animate",
        groups=(Group("inputs", "Start frame", column="right"), G_PROMPT,
                Group("core", "Output", dense=True),
                Group("sampling", "Sampling", dense=True), G_SEED),
        fields=(
            Field("image", "Start image (paste with Ctrl+V)", "image", None,
                  group="inputs", column="right"),
            Field("prompt", "Prompt (the motion, and the sound)", "textarea",
                  "", lines=4, group="prompt",
                  placeholder="she looks up and laughs, rain against the "
                              "window, a kettle starting to whistle"),
            *_seed_fields(),
            Field("steps", "Steps", "slider", MINIMAX_DEFAULTS["steps"],
                  lo=1, hi=40, step=1, group="sampling",
                  hint="The turbo LoRA is tuned for 8."),
            Field("resolution", "Resolution (keeps the source aspect)",
                  "radio", MINIMAX_DEFAULT_RESOLUTION,
                  choices=list(MINIMAX_RESOLUTIONS), group="core", wide=True),
            Field("seconds", "Duration (seconds)", "slider",
                  MINIMAX_DEFAULT_SECONDS, lo=MINIMAX_MIN_SECONDS,
                  hi=MINIMAX_MAX_SECONDS, step=1.0, group="sampling",
                  wide=True,
                  hint="Snapped to the model's frame grid at 24 fps."),
            Field("sampler", "Sampler", "select", MINIMAX_DEFAULTS["sampler"],
                  choices=SAMPLERS + ["uni_pc"], group="sampling", wide=True),
            _batch_field(hi=10),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_blank_lora_tail(handlers.MINIMAX_I2V)),
        ),
    ),

    TabSchema(
        key=Key.MINIMAX_T2V, handler=handlers.generate_minimax_t2v,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=VIDEO_KEYS, tab_id="minimax_t2v", output="video",
        icon="🎞️", blurb="A clip with sound, from words alone.",
        category="video", route="/video/minimax-t2v",
        submit_label="Generate",
        groups=(G_PROMPT, Group("core", "Output", dense=True),
                Group("sampling", "Sampling", dense=True), G_SEED),
        fields=(
            Field("prompt", "Prompt (the scene, the motion, and the sound)",
                  "textarea", "", lines=5, group="prompt",
                  placeholder="a kettle on a gas hob comes to the boil and "
                              "whistles, steam catching the window light, "
                              "slow push-in"),
            Field("aspect", "Aspect ratio", "select", MINIMAX_DEFAULT_ASPECT,
                  choices=list(MINIMAX_ASPECT_RATIOS), group="core",
                  wide=True),
            *_seed_fields(),
            Field("steps", "Steps", "slider", MINIMAX_DEFAULTS["steps"],
                  lo=1, hi=40, step=1, group="sampling",
                  hint="The turbo LoRA is tuned for 8."),
            Field("resolution", "Resolution", "radio",
                  MINIMAX_DEFAULT_RESOLUTION,
                  choices=MINIMAX_T2V_RESOLUTIONS, group="core", wide=True),
            Field("seconds", "Duration (seconds)", "slider",
                  MINIMAX_DEFAULT_SECONDS, lo=MINIMAX_MIN_SECONDS,
                  hi=MINIMAX_MAX_SECONDS, step=1.0, group="sampling",
                  wide=True,
                  hint="Snapped to the model's frame grid at 24 fps."),
            Field("sampler", "Sampler", "select", MINIMAX_DEFAULTS["sampler"],
                  choices=SAMPLERS + ["uni_pc"], group="sampling", wide=True),
            _batch_field(hi=10),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_blank_lora_tail(handlers.MINIMAX_T2V)),
        ),
    ),
)

BY_KEY = {str(schema.key): schema for schema in SCHEMAS}


def get(key) -> TabSchema:
    """One tab's schema. KeyError for a tab that has no form."""
    return BY_KEY[str(key)]


def entitled() -> tuple:
    """The schemas this licence grants, in navigation order.

    One of the four layers guarding the licence gate — see
    docs/architecture/licensing-and-features.md, "The licence gate on the
    API".
    `/catalog` and `/schema/{tab}` both filter through this, so a tab the
    licence does not grant is not merely unreachable — it is not described
    either, and the React navigation never learns it exists.
    """
    return tuple(s for s in SCHEMAS if features.enabled(s.key))


# Tabs with no form at all. Listed here rather than in the React app so
# that one list drives the whole navigation, and so the licence filter
# above covers them too.
BESPOKE = (
    {"key": str(Key.GALLERY), "label": "Gallery", "icon": "🖼️",
     "category": "library", "route": "/library/gallery"},
    {"key": str(Key.COMMUNITY_PROMPTS), "label": "Prompt Library",
     "icon": "🌟", "category": "library", "route": "/library/prompts"},
)


# ══════════════════════════════════════════════ the reactivity catalogue
# Per-control reactivity, as data. Every rule here is a lookup in what
# the pod already holds — a model dropdown that resets Steps and CFG from
# the model record, a Wan mode radio that does the same, a model that
# swaps its trigger words into the prompt. Shipped so React applies them
# locally, with no round trip and no second copy of the same three
# numbers.

# The V2 family's rows carry one more default than the Krea2 family's:
# whether the model's recipe switches its turbo LoRA on.
_V2_FEATURES = (handlers.KREA_V2_T2I, handlers.KREA_V2_EDIT)


def _model_rows(feature):
    """One feature's model list, flattened for the browser.

    `defaults` is (steps, cfg) for Krea2 / Krea2 Edit and (steps, cfg,
    turbo_lora) for the V2 tabs, because the two families genuinely
    differ and pretending otherwise would mean the browser guessing which
    of the two a row means. `id` is the dropdown's value; `name` its label.
    """
    v2 = feature in _V2_FEATURES
    rows = []
    for model in assets.feature_models(feature):
        if v2:
            steps, cfg, turbo = handlers.v2_model_defaults(model)
            defaults = {"steps": steps, "cfg": cfg, "turbo_lora": turbo}
            info = handlers._v2_model_info_text(model)
        else:
            steps, cfg = handlers.model_defaults(model)
            defaults = {"steps": steps, "cfg": cfg}
            info = handlers._model_info_text(model)
        rows.append({
            "id": model.id,
            "name": model.name,
            "file": model.file,
            "variant": model.variant,
            "trigger": model.trigger or "",
            "defaults": defaults,
            # The model info line under every Model dropdown, which the
            # React app has no other way to render: whether the weights
            # are on this pod is a fact about this pod's disk.
            "available": bool(handlers.model_file_available(model)),
            "info": info,
        })
    return rows


def catalog() -> dict:
    """Everything the forms need that is not a field. Served at /catalog.

    Filtered to the entitled tabs, because this is one of the four layers
    the licence gate is built from: a tab this licence does not grant is
    not described here at all, so the navigation never learns it exists.
    """
    return {
        "tabs": [s.to_json() for s in entitled()],
        "bespoke": [row for row in BESPOKE
                    if features.enabled(row["key"])],
        # Keyed by feature key — each entitled Krea tab's own list, which
        # is what its schema's `modelRegistry` names.
        "models": {
            s.model_registry: _model_rows(s.model_registry)
            for s in entitled() if s.model_registry
        },
        # The Wan tab's model radio disables the mode radio for the 5B —
        # it has no Lightning distillation — and both radios reset Steps
        # and CFG. `is5b` is the same substring test _is_wan_5b makes, so
        # the browser and the handler agree on which model is which.
        "wan": {
            "modes": WAN_MODE_DEFAULTS,
            "fiveB": dict(WAN_5B_DEFAULTS, fps=WAN_5B_FPS),
            "fps": WAN_FPS,
            "maxSeconds": WAN_MAX_SECONDS,
            "modeless": WAN_MODEL_CHOICES[1],
            "resolutions": {k: v for k, v in WAN_RESOLUTIONS.items()},
        },
        "resolutions": {k: list(v) for k, v in RESOLUTION_PRESETS.items()},
        "aspects": {k: list(v) if isinstance(v, (list, tuple)) else v
                    for k, v in V2_ASPECT_RATIOS.items()},
        "samplers": list(SAMPLERS),
    }


# ═════════════════════════════════════════════════════ the assertions
# Run at import, the same way features.py:169-173 validates its own
# registry, and for the same reason: the only thing that can break these
# is editing this file, so the cost is one check per process and the
# alternative is finding out from a customer's pictures.


def _assert_signatures() -> None:
    """Every schema against inspect.signature(handler).

    **The most important defensive measure in this module.** The form
    that submits these arguments is in another language, in another
    directory, so nothing across that boundary enforces the invariant,
    and the failure it prevents is silent: rename a parameter in
    `ember.generation.handlers`, forget it here, and `call_args` shifts
    every argument after it. generate_v2 has 31.
    """
    for schema in SCHEMAS:
        sig = inspect.signature(schema.handler)
        positional = [p.name for p in sig.parameters.values()
                      if p.kind is p.POSITIONAL_OR_KEYWORD]
        varargs = next((p.name for p in sig.parameters.values()
                        if p.kind is p.VAR_POSITIONAL), None)
        declared = [f.name for f in schema.named()]
        where = "%s -> %s" % (schema.key, schema.handler.__name__)

        if declared != positional:
            extra = set(declared) - set(positional)
            missing = set(positional) - set(declared)
            raise RuntimeError(
                "tabschema %s: the fields do not match the signature.\n"
                "  schema:    %s\n  handler:   %s\n"
                "  not in the handler: %s\n  not in the schema:  %s"
                % (where, declared, positional,
                   sorted(extra) or "-", sorted(missing) or "-")
            )

        tails = [f for f in schema.fields if f.repeat is not None]
        if len(tails) > 1:
            raise RuntimeError("tabschema %s: more than one repeating tail"
                               % where)
        tail_name = tails[0].name if tails else None
        if tail_name != varargs:
            raise RuntimeError(
                "tabschema %s: repeating tail is %r but the handler's "
                "varargs is %r" % (where, tail_name, varargs)
            )
        if tails and tails[0] is not schema.fields[-1]:
            raise RuntimeError(
                "tabschema %s: the repeating tail must be the last field — "
                "it is the *varargs, and everything after it would be "
                "swallowed by it." % where
            )

        names = [f.name for f in schema.named()]
        if len(set(names)) != len(names):
            raise RuntimeError("tabschema %s: duplicate field names" % where)

        if schema.field(schema.prompt_field) is None:
            raise RuntimeError("tabschema %s: prompt_field %r is not a field"
                               % (where, schema.prompt_field))

        group_ids = {g.id for g in schema.groups}
        for f in schema.named():
            if f.group and f.group not in group_ids:
                raise RuntimeError("tabschema %s: field %r names group %r, "
                                   "which the tab does not declare"
                                   % (where, f.name, f.group))
            if f.show_if and schema.field(f.show_if[0]) is None:
                raise RuntimeError("tabschema %s: field %r is shown by %r, "
                                   "which is not a field"
                                   % (where, f.name, f.show_if[0]))

        if not schema.result_keys:
            raise RuntimeError("tabschema %s: no result_keys" % where)
        if "status" not in schema.result_keys:
            raise RuntimeError(
                "tabschema %s: result_keys has no 'status' — jobqueue reads "
                "the progress line out of it by that name" % where
            )


def _assert_settings() -> None:
    """schema.settings() == handlers._krea_settings(), for the same values.

    This is the entire proof that presets keep working, and it really is
    enough on its own. `presetWire()` in license-validator/src/app.js
    returns `settings: row.settings || {}` and never looks inside, so the
    blob is opaque server-side — nothing about it is validated, migrated
    or indexed anywhere but here. If this local comparison holds, a preset
    written by any build loads into any other and back again unchanged.

    Checked on the Krea 2 tab because _krea_settings is a plain function
    with no availability guards, so it can be called at import on a
    machine with no weights. The V2 blob is checked the same way in
    scripts/golden.py, where the stubs to get past v2_status() already
    exist.
    """
    schema = get(Key.KREA_T2I)
    values = schema.defaults()
    args = schema.call_args(values)
    named = len(schema.named())
    expected = handlers._krea_settings(
        seed=args[2], randomize=args[3], steps=args[4], cfg=args[5],
        resolution=args[6], sampler=args[7], model=args[8],
        batch_count=args[9], lora_slots=args[named:],
    )
    actual = schema.settings(values)
    if actual != expected:
        keys = set(actual) | set(expected)
        rows = ["  %s: %r != %r" % (k, actual.get(k), expected.get(k))
                for k in sorted(keys) if actual.get(k) != expected.get(k)]
        raise RuntimeError(
            "tabschema: the Krea 2 settings blob no longer matches "
            "handlers._krea_settings — every preset on the licence server "
            "is stored in this shape.\n" + "\n".join(rows)
        )


_assert_signatures()
_assert_settings()
log.debug("tabschema: %d tab(s) validated against their handlers",
          len(SCHEMAS))
