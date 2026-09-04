"""One declarative schema per tab — what a form is, in one place.

The keystone of the rewrite. Gradio kept five different answers to "what
is on this tab" in five different shapes, and kept them consistent by
sheer proximity: the `gr.Slider(...)` that drew a control sat six lines
above the `click(inputs=[...])` that submitted it, so a human editing one
saw the other. Take the layout out of Python and that proximity is gone —
the React form is in another language, in another directory, built by
another toolchain.

So the five answers become one object, and everything else is derived:

  1. **The React form.** `to_json()` is what `/api/v1/schema/{tab}` serves.
  2. **API validation.** `coerce()` — the submit path, where an illegal
     value is a 422 rather than something the handler has to survive.
  3. **Recipe labels.** `recipe_fields()` writes the same
     `[[label, value], ...]` rows `ui._recipe_fields` wrote, in the same
     order, with the same labels — live pods have a `.recipes.jsonl`
     keyed on that text and a reworded label orphans history silently.
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
`inspect.signature(handler)` at import, the same way features.py:169-173
validates its own registry. **This is the most important defensive
measure in the whole rewrite.** What it replaces is the safety of seeing
`click(inputs=[...])` next to the components it names: without it, a
parameter renamed in handlers.py and not here is a silent argument shift,
and `generate_v2` takes 31 of them.

The two coercion paths Gradio conflated
---------------------------------------
Gradio had one notion of "put a value in a control", and it had to be
forgiving because the same code served a fresh click and a two-year-old
recipe. Split here, because the two want opposite things:

* `coerce()` is the **submit** path. The value came from a form this
  server just described. An out-of-range number or an unknown dropdown
  value is a bug or an attack, and it is a 422.
* `restore()` is the **recipe / preset apply** path. The value came from
  another pod, possibly from an older build. Out of range means *leave
  the control alone* (`ui._pick`, :2868) or *clamp it* (`ui._num`, :2880),
  and never an error — the rest of the recipe still loads.

`restore()` runs server-side rather than in the browser because
`choices()` for a LoRA dropdown is `available_lora_files()` — a listing of
**this pod's disk**. The browser cannot know it.

What is deliberately not here
-----------------------------
The ~150 lines of `*_changed` handlers in ui.py. `krea_model_changed`,
`v2_model_changed`, `wan_mode_changed` and friends are reactivity over
data that already sits in config.py — VARIANT_DEFAULTS, V2_VARIANT_
DEFAULTS, WAN_MODE_DEFAULTS, FLUX_VARIANT_DEFAULTS, KLEIN_DEFAULTS. The
API ships that data in `/api/v1/catalog` (see `catalog()`) and React
applies it, which is one round trip saved per keystroke and one fewer
copy of the same three numbers.
"""

import inspect
from dataclasses import dataclass, field as dc_field, replace
from typing import Any, Callable

import features
import presets
import handlers
from config import (
    FLUX_MODELS,
    FLUX_VARIANT_DEFAULTS,
    KLEIN_DEFAULT_CUSTOM_SIZE,
    KLEIN_DEFAULT_MEGAPIXELS,
    KLEIN_DEFAULTS,
    KLEIN_MODELS,
    KLEIN_OUTPUT_CUSTOM,
    KLEIN_OUTPUT_MODES,
    KLEIN_OUTPUT_SCALED,
    KLEIN_REFERENCE_MEGAPIXELS,
    KLEIN_SAMPLER_DEFAULTS,
    KLEIN_SCHEDULERS,
    KREA2_MODELS,
    REACTOR_DEFAULT_DETECTOR,
    REACTOR_DETECTORS,
    RESOLUTION_PRESETS,
    DEFAULT_RESOLUTION,
    SAMPLERS,
    V2_ASPECT_RATIOS,
    V2_DEFAULT_ASPECT,
    V2_DEFAULT_MEGAPIXELS,
    V2_DEFAULT_MULTIPLE,
    V2_DEFAULT_NEGATIVE,
    V2_EDIT_DEFAULT_GROUNDING,
    V2_EDIT_DEFAULT_REF_BOOST,
    V2_EDIT_FIT_MODES,
    V2_MODELS,
    V2_SAMPLER_DEFAULTS,
    V2_SAMPLER_MODES,
    V2_SAMPLER_NAMES,
    V2_SCHEDULERS,
    V2_VARIANCE_DEFAULTS,
    V2_VARIANCE_MODEL_TYPES,
    V2_VARIANCE_PRESETS,
    V2_VARIANCE_SCHEDULES,
    V2_VARIANT_DEFAULTS,
    VARIANT_DEFAULTS,
    WAN_5B_DEFAULTS,
    WAN_5B_FPS,
    WAN_DEFAULT_NEGATIVE,
    WAN_DEFAULT_RESOLUTION,
    WAN_FPS,
    WAN_MAX_SECONDS,
    WAN_MODE_DEFAULTS,
    WAN_RESOLUTIONS,
    WAN_VARIANT,
    log,
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

    Callables are how the disk-derived lists stay honest. `LORA_CHOICES`
    is `available_lora_files()` read at import; a pod that downloads a
    LoRA while the app runs has a longer list, and the Rescan button in
    the Gradio UI existed exactly to pick that up. Resolving late gives
    the API the same behaviour without a button.
    """
    return value() if callable(value) else value


# ─────────────────────────────────────────────────────────── the pieces

@dataclass(frozen=True)
class Repeat:
    """The handler's `*varargs` tail, as a repeating row of sub-fields.

    Two shapes exist in this app and the difference shifts every argument
    after it (context.md 4.3):

      * **pairs** `(name, weight)` — Krea2, Flux, Edit, Inpaint. Eight
        slots, from MAX_LORA_SLOTS.
      * **triples** `(enabled, name, weight)` — the two Power-Lora-Loader
        tabs, V2 and Klein, whose rows carry a per-row on/off checkbox.

    `parts` is the submission order *within* one slot, so `call_args`
    flattens `slots x parts` and that is the tail. `slots()` returns one
    dict of per-slot default overrides each, because V2 and Klein take
    their rows from the source workflow's own stack rather than from a
    blank row repeated N times — and a row whose file did not download
    comes back off and blank, which is a fact about this pod's disk.

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
    moving a control is a one-word edit in one language (Section 1's
    decision 5, and the fix for V2's Steps/CFG/Sampler having sat in the
    *output* column while the other eight tabs put them with the controls).
    """

    # ── the contract ────────────────────────────────────────────────
    name: str                       # == the handler's parameter name
    label: str                      # verbatim from ui.py — see the docstring
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
    accept: str | None = None
    # RES4LYF builds its sampler and scheduler lists at load time, so a
    # name this build does not list is still a name the node may accept.
    # The two V2 dropdowns are gr.Dropdown(allow_custom_value=True); this
    # is that flag, and without it a preset from a pod with a newer
    # RES4LYF would 422 on submit.
    allow_custom: bool = False

    def options(self) -> tuple:
        """This field's choices, resolved. Empty for a non-choice field."""
        return tuple(_resolve(self.choices) or ())

    def initial(self):
        """The value a fresh form starts on."""
        return _resolve(self.default)

    # ── the two coercion paths ──────────────────────────────────────

    def coerce(self, value):
        """Submit path: return the value the handler should be called with.

        Raises Invalid rather than repairing. The value came from a form
        this server described seconds ago, so anything outside it is a bug
        or an attack and neither is improved by guessing.
        """
        kind = self.kind
        if kind in ("image", "mask", "file"):
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

        The whole cross-pod safety story, and it is deliberately the same
        two rules ui._pick and ui._num state:

          * a choice this pod does not offer leaves the control where it
            was — a Gradio dropdown handed a value outside its `choices`
            is a *broken* component rather than a wrong one, and the React
            select has the same problem;
          * a number outside this build's range is clamped into it rather
            than dropped, because the range is a property of this build
            and not of the recipe. The recipe stays as close as this UI
            can express it.
        """
        kind = self.kind
        if kind in ("image", "mask", "file"):
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
        if self.accept:
            row["accept"] = self.accept
        return row


@dataclass(frozen=True)
class Group:
    """How a tab's fields are gathered for the eye. Layout, not contract."""

    id: str
    title: str | None = None
    renderer: str = "default"        # default|seed|sampler|variance
    collapsible: bool = False
    default_open: bool = True
    dense: bool = False
    column: str | None = None

    def to_json(self) -> dict:
        row = {"id": self.id, "renderer": self.renderer}
        if self.title:
            row["title"] = self.title
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
    prompt_field: str | None        # names the queue row; None = "—"
    result_keys: tuple              # what the handler's yield tuple means
    fields: tuple                   # SUBMISSION ORDER. Never reorder.

    # ── presentation ────────────────────────────────────────────────
    tab_id: str = ""                # the Gradio tab id, kept for recipes
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
    # Which registry in catalog()["models"] this tab's Model dropdown is
    # naming. It is what lets the browser do what krea_model_changed and
    # its three siblings did: pick a model, get that variant's step and
    # CFG defaults and its trigger words, with no round trip.
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
        its default, which is what Gradio did too.
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
        hand-written function. The LoRA rows are appended in the shape
        that tab's own reader expects: pairs as [name, weight], triples as
        [enabled, name, weight].

        `test_settings_match_ui()` asserts this equals `_krea_settings`
        for the Krea2 tab. That local check is the whole proof, because
        the licence server stores the blob opaquely — presetWire() in
        license-validator/src/app.js returns `settings: row.settings || {}`
        and never inspects it (context.md 4.10).
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
                rows.append([_cast(part, v) for part, v in zip(tail.parts, row)])
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
        lifted from ui._lora_updates: a LoRA file this pod does not have
        becomes "None" **explicitly** rather than being left alone. The
        slots are being reset to a whole other recipe, and a leftover LoRA
        from whatever was loaded before would silently join it.
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
                        known = stored in part.options()
                        values[key] = stored if known else "None"
                    elif part.name == "enabled":
                        values[key] = bool(stored) if known is not False else False
                    else:
                        ok, value = part.restore(stored)
                        values[key] = value if ok else defaults[index][part.name]
                # `enabled` is submitted before `name`, so the "this pod
                # does not have the file" answer is only known after the
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
        api.py's four layers, and context.md 4.5 for why one layer is not
        enough — `json_batch` and `community_prompts` have `needs=()`, so
        the downloads.py backstop that covers every other tab does not
        cover them.
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
    enabled = next((p for p in tail.parts if p.name == "enabled"), None)
    return {
        "shape": "triple" if enabled is not None else "pair",
        "count": tail.count(),
        "title": tail.title,
        "key": tail.key,
        "choices": list(slot.options()),
        # "LoRA {n}" -> "LoRA"; the slot number is the renderer's business.
        "slotLabel": slot.label.replace(" {n}", ""),
        # "Weight" on the pair tabs, "Strength" on the triple ones. Kept
        # apart because they are different words in the app people use.
        "weightLabel": weight.label,
        "weightMin": weight.lo,
        "weightMax": weight.hi,
        "weightStep": weight.step,
        "weightDefault": _resolve(weight.default),
        "enabledLabel": enabled.label if enabled else None,
        "enabledDefault": bool(_resolve(enabled.default)) if enabled else False,
        "parts": names,
        # Per-slot defaults. V2 and Klein take their rows from the source
        # workflow's stack rather than from a blank row repeated N times,
        # and a row whose file did not download comes back off and blank.
        "slots": [dict(row) for row in tail.rows()],
    }


# ══════════════════════════════════════════════════════ shared pieces
# Written once and referenced from every tab that has them. Nine of the
# ten forms are the same handful of blocks in a different order, which is
# a fact twelve hand-written layouts could state only by repeating it.

def _lora_choices():
    """The Krea 2 LoRA folder, read now rather than at import.

    The Gradio UI had a "🔄 Rescan LoRA folder" button for exactly this:
    the lists were captured at import and a file dropped in afterwards was
    invisible until a restart. Reading late makes the button unnecessary.
    """
    return ["None"] + handlers.list_lora_files()


def _flux_lora_choices():
    return ["None"] + handlers.list_flux_lora_files()


def _klein_lora_choices():
    return ["None"] + handlers.list_klein_lora_files()


def _blank_slots(count):
    """`count` rows at the part defaults — the plain eight-slot stacks."""
    return lambda: tuple({} for _ in range(count))


def _stack_slots(loader):
    """(enabled, name, strength) rows from a source workflow's own stack.

    V2 and Klein do not repeat a blank row: their rows, order, strengths
    and on/off states come from the graph they were transcribed from, and
    `default_lora_slots()` has already switched off any row whose file did
    not download.
    """
    return lambda: tuple({"enabled": on, "name": name, "weight": strength}
                         for on, name, strength in loader())


def _pair_tail(choices, title):
    """`(name, weight)` slots — Krea2, Flux, Edit, Inpaint."""
    return Repeat(
        parts=(
            Field("name", "LoRA {n}", "select", "None", choices=choices),
            Field("weight", "Weight", "slider", 0.8, lo=0.0, hi=2.0,
                  step=0.05),
        ),
        slots=_blank_slots(handlers.MAX_LORA_SLOTS),
        title=title,
    )


def _triple_tail(choices, loader, title):
    """`(enabled, name, weight)` slots — the Power-Lora-Loader tabs.

    The order inside the row is the submission order of the handler's
    varargs tail, so `enabled` really does come first. Getting it wrong
    shifts every argument after it.
    """
    return Repeat(
        parts=(
            Field("enabled", "On", "bool", False),
            Field("name", "LoRA {n}", "select", "None", choices=choices),
            Field("weight", "Strength", "slider", 1.0, lo=0.0, hi=2.0,
                  step=0.01),
        ),
        slots=_stack_slots(loader),
        title=title,
    )


def _krea_lora_tail():
    return _pair_tail(_lora_choices, "🎭 LoRA stack")


def _seed_fields(default=42):
    """Seed and the random tick — byte-identical on eight tabs.

    Batch count is deliberately *not* here even though it renders in the
    same row (`group="seed"`). It sits at a different place in every
    signature — last, after the model — and `fields` is submission order,
    so grouping it with its neighbours on screen would have shifted two
    arguments on eight tabs. Which is exactly what _assert_signatures()
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


def _v2_sampler_fields(denoise=True):
    """The ClownsharKSampler block, shared by the V2 and V2 Edit tabs.

    V2 Edit has no Denoise: the source image reaches the model through
    conditioning rather than through the starting latent, so it is pinned
    at 1.0 in the builder. That single difference is the `denoise` flag
    rather than a second copy of the block.
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
              V2_VARIANT_DEFAULTS["turbo"]["steps"], lo=1, hi=100, step=1,
              group="sampler", preset="sampler.steps"),
    ]
    if denoise:
        rows.append(
            Field("denoise", "Denoise", "slider",
                  V2_SAMPLER_DEFAULTS["denoise"], lo=0.0, hi=1.0, step=0.01,
                  group="sampler", preset="sampler.denoise"))
    rows += [
        Field("cfg", "CFG", "slider", V2_VARIANT_DEFAULTS["turbo"]["cfg"],
              lo=0.0, hi=20.0, step=0.1, group="sampler",
              preset="sampler.cfg"),
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


def _reference_fields(group="core"):
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

    Three tabs share it and they word it differently — Klein says "Input
    image 1" where the two Edit tabs say "Source image" — so the labels
    are arguments. They are transcribed verbatim; see the module docstring
    on why that matters.
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

# The Wan tab's two radio lists, verbatim from ui.py:3549-3552. They name
# model families rather than files, and the strings are what generate_wan_
# video branches on (`_is_wan_5b`, `mode.startswith("turbo")`) — so they
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
PLAIN_KEYS = ("images", "status")
VIDEO_KEYS = ("videos", "latest", "status", "seed")


# ══════════════════════════════════════════════════════ the ten tabs
# Order is the order they appear in the navigation, which is ui.TAB_ORDER's
# order with the two bespoke tabs (Gallery, Prompt Library) taken out —
# those have no form and so no schema.

SCHEMAS = (

    TabSchema(
        model_registry='krea2',
        key=Key.KREA_T2I, handler=handlers.generate_single,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="krea2",
        icon="🎨", blurb="Type a sentence, get a photograph.",
        category="generate", route="/generate/krea2",
        submit_label="Generate", preset_tab=presets.TAB_KREA2,
        preset_note=GEN_PRESET_NOTE,
        groups=(G_PROMPT, Group("core", "Output", dense=True), G_SEED, G_SAVE),
        fields=(
            Field("prompt", "Prompt", "textarea",
                  "A photorealistic golden-hour portrait, natural skin "
                  "texture, shallow depth of field", lines=5, group="prompt"),
            Field("negative", "Negative prompt (only used when CFG > 1)",
                  "textarea", "", lines=2, group="prompt"),
            *_seed_fields(),
            Field("steps", "Steps", "slider",
                  lambda: handlers.DEFAULTS["steps"], lo=1, hi=60, step=1,
                  group="core", preset="steps"),
            Field("cfg", "CFG", "slider", lambda: handlers.DEFAULTS["cfg"],
                  lo=0.5, hi=8.0, step=0.1, group="core", preset="cfg",
                  hint=_CFG_NOTE),
            Field("resolution", "Resolution", "select", DEFAULT_RESOLUTION,
                  choices=list(RESOLUTION_PRESETS), group="core",
                  preset="resolution", wide=True),
            Field("sampler", "Sampler", "select", SAMPLERS[0],
                  choices=SAMPLERS, group="core", preset="sampler", wide=True),
            Field("model", "Model", "select",
                  lambda: handlers.MODEL_CHOICES[0],
                  choices=lambda: handlers.MODEL_CHOICES, group="core",
                  preset="model", wide=True),
            _batch_field(),
            *_save_fields(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_krea_lora_tail()),
        ),
    ),

    TabSchema(
        model_registry='v2',
        key=Key.KREA_V2_T2I, handler=handlers.generate_v2,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="krea2v2",
        icon="🔶", blurb="The V2 pipeline, with the full ClownsharKSampler "
                        "stack.",
        category="generate", route="/generate/krea2-v2",
        submit_label="Generate", preset_tab=presets.TAB_KREA2_V2,
        preset_note=GEN_PRESET_NOTE,
        groups=(G_PROMPT, Group("core", "Output", dense=True),
                # In ui.py these sat in the *output* column, alone among the
                # ten tabs. They are controls, so they go with the controls.
                G_SAMPLER, G_VARIANCE,
                Group("post", "Post-processing", dense=True, collapsible=True,
                      default_open=False),
                G_SEED, G_SAVE),
        fields=(
            Field("prompt", "Positive Prompt", "textarea", "", lines=6,
                  group="prompt"),
            Field("negative", "Negatives", "textarea", V2_DEFAULT_NEGATIVE,
                  lines=6, group="prompt"),
            # The seed the source workflow shipped with, kept as-is.
            *_seed_fields(default=370102505887178),
            Field("model", "Model", "select",
                  lambda: handlers.V2_MODEL_CHOICES[0],
                  choices=lambda: handlers.V2_MODEL_CHOICES, group="core",
                  preset="model", wide=True),
            Field("aspect", "Aspect ratio", "select", V2_DEFAULT_ASPECT,
                  choices=list(V2_ASPECT_RATIOS), group="core",
                  preset="aspect", wide=True),
            Field("megapixels", "Megapixels", "slider",
                  V2_DEFAULT_MEGAPIXELS, lo=0.5, hi=4.0, step=0.1,
                  group="core", preset="megapixels"),
            Field("multiple", "Multiple of", "slider", V2_DEFAULT_MULTIPLE,
                  lo=8, hi=64, step=8, group="core", preset="multiple"),
            *_v2_sampler_fields(),
            *_v2_variance_fields(),
            Field("sharpen", "Sharpen (radius 1, sigma 0.35, alpha 1)",
                  "bool", False, group="post", preset="sharpen", wide=True),
            Field("film_grain", "Film grain (intensity 0.05, scale 1)",
                  "bool", False, group="post", preset="film_grain",
                  wide=True),
            _batch_field(),
            *_save_fields(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_triple_tail(_lora_choices,
                                      handlers.v2_default_lora_slots,
                                      "🎭 LoRA stack — model + CLIP")),
        ),
    ),

    TabSchema(
        model_registry='krea2',
        key=Key.KREA_INPAINT, handler=handlers.generate_inpaint,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="inpaint",
        icon="🖌️", blurb="Paint over what should change. Leave the rest "
                        "alone.",
        category="edit", route="/edit/inpaint", submit_label="Inpaint",
        groups=(Group("canvas", column="right"), G_PROMPT,
                Group("core", "Sampling", dense=True),
                Group("mask", "Mask shaping", dense=True), G_SEED),
        fields=(
            # The canvas is the work surface, not a sidebar control, so it
            # takes the wide column and the results stack under it. Being
            # able to say that in the schema rather than in the layout code
            # is what `column` is for: Gradio put a 440x280 editor in the
            # left sidebar while the right half of the screen sat empty.
            Field("editor_value",
                  "Image — paint the region to replace (paste with Ctrl+V)",
                  "mask", None, group="canvas", column="right"),
            Field("prompt", "Prompt (describes the masked region)",
                  "textarea", "", lines=3, group="prompt"),
            Field("negative", "Negative prompt (only used when CFG > 1)",
                  "textarea", "", lines=2, group="prompt"),
            *_seed_fields(),
            Field("steps", "Steps", "slider",
                  lambda: handlers.DEFAULTS["steps"], lo=1, hi=60, step=1,
                  group="core"),
            Field("cfg", "CFG", "slider", lambda: handlers.DEFAULTS["cfg"],
                  lo=0.5, hi=8.0, step=0.1, group="core", hint=_CFG_NOTE),
            Field("denoise", "Denoise (1 = replace fully)", "slider", 1.0,
                  lo=0.1, hi=1.0, step=0.05, group="core", wide=True,
                  hint="With nothing painted this runs as whole-image "
                       "img2img, where 1.0 ignores the source entirely — "
                       "0.5–0.8 is the useful range."),
            Field("sampler", "Sampler", "select", SAMPLERS[0],
                  choices=SAMPLERS, group="core", wide=True),
            Field("grow", "Grow mask (px)", "slider", 8, lo=0, hi=32, step=1,
                  group="mask",
                  hint="Dilates the painted region before blurring."),
            Field("blur", "Blur mask edge (px)", "slider", 8, lo=0, hi=32,
                  step=1, group="mask",
                  hint="Softens the edge so the seam disappears."),
            Field("model", "Model", "select",
                  lambda: handlers.MODEL_CHOICES[0],
                  choices=lambda: handlers.MODEL_CHOICES, group="core",
                  wide=True),
            _batch_field(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_krea_lora_tail()),
        ),
    ),

    TabSchema(
        model_registry='krea2',
        key=Key.KREA_EDIT, handler=handlers.generate_edit,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="edit",
        icon="✨", blurb="Change one thing about a picture without touching "
                        "the rest.",
        category="edit", route="/edit/krea2-edit", submit_label="Edit",
        preset_tab=presets.TAB_KREA2, preset_note=EDIT_PRESET_NOTE.format("🎨 Krea2"),
        groups=(G_INPUTS, G_PROMPT, Group("core", "Sampling", dense=True),
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
                  lines=3, group="prompt"),
            Field("negative", "Negative prompt (only used when CFG > 1)",
                  "textarea", "", lines=2, group="prompt"),
            *_seed_fields(),
            Field("steps", "Steps", "slider",
                  lambda: handlers.DEFAULTS["steps"], lo=1, hi=60, step=1,
                  group="core", preset="steps"),
            Field("cfg", "CFG", "slider", lambda: handlers.DEFAULTS["cfg"],
                  lo=0.5, hi=8.0, step=0.1, group="core", preset="cfg"),
            Field("sampler", "Sampler", "select", SAMPLERS[0],
                  choices=SAMPLERS, group="core", preset="sampler",
                  wide=True),
            *_reference_fields(),
            Field("model", "Model", "select",
                  lambda: handlers.MODEL_CHOICES[0],
                  choices=lambda: handlers.MODEL_CHOICES, group="core",
                  preset="model", wide=True),
            _batch_field(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_krea_lora_tail()),
        ),
    ),

    TabSchema(
        model_registry='v2',
        key=Key.KREA_V2_EDIT, handler=handlers.generate_v2_edit,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="v2edit",
        icon="🔷", blurb="Instruction editing on the V2 pipeline.",
        category="edit", route="/edit/krea2-v2-edit", submit_label="Edit",
        preset_tab=presets.TAB_KREA2_V2,
        preset_note=EDIT_PRESET_NOTE.format("🔶 Krea2 V2"),
        groups=(G_INPUTS, G_PROMPT, Group("core", "Output", dense=True),
                G_SAMPLER, G_VARIANCE, G_SEED),
        fields=(
            *_two_image_fields("Source image (paste with Ctrl+V)",
                               "➕ Add a second reference (subject)",
                               "Second reference — subject"),
            Field("prompt", "Edit instruction", "textarea", "", lines=3,
                  group="prompt"),
            Field("negative", "Negatives (only used when CFG > 1)",
                  "textarea", V2_DEFAULT_NEGATIVE, lines=4, group="prompt"),
            *_seed_fields(),
            Field("model", "Model", "select",
                  lambda: handlers.V2_MODEL_CHOICES[0],
                  choices=lambda: handlers.V2_MODEL_CHOICES, group="core",
                  preset="model", wide=True),
            *_reference_fields(),
            Field("fit_mode",
                  "Reference geometry (fit = v1.2; the legacy crop is for "
                  "older weights)",
                  "select", V2_EDIT_FIT_MODES[0], choices=V2_EDIT_FIT_MODES,
                  group="core", wide=True),
            # No Denoise: the source reaches the model through conditioning
            # rather than the starting latent, so the builder pins it at 1.0.
            *_v2_sampler_fields(denoise=False),
            *_v2_variance_fields(),
            _batch_field(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_triple_tail(_lora_choices,
                                      handlers.v2_default_lora_slots,
                                      "🎭 LoRA stack — model + CLIP")),
        ),
    ),

    TabSchema(
        model_registry='flux',
        key=Key.FLUX_T2I, handler=handlers.generate_flux,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="flux",
        icon="🌊", blurb="The Flux pipeline, guidance-driven.",
        category="generate", route="/generate/flux", submit_label="Generate",
        groups=(G_PROMPT, Group("core", "Output", dense=True), G_SEED),
        fields=(
            Field("prompt", "Prompt", "textarea",
                  "A photorealistic golden-hour portrait, natural skin "
                  "texture, shallow depth of field", lines=5, group="prompt"),
            *_seed_fields(),
            Field("steps", "Steps", "slider", lambda: handlers._f_steps,
                  lo=1, hi=50, step=1, group="core"),
            Field("guidance", "Guidance", "slider",
                  lambda: handlers._f_guidance, lo=0.0, hi=10.0, step=0.1,
                  group="core"),
            Field("resolution", "Resolution", "select", DEFAULT_RESOLUTION,
                  choices=list(RESOLUTION_PRESETS), group="core", wide=True),
            Field("sampler", "Sampler", "select", "euler", choices=SAMPLERS,
                  group="core", wide=True),
            Field("model", "Model", "select",
                  lambda: handlers.FLUX_MODEL_CHOICES[0],
                  choices=lambda: handlers.FLUX_MODEL_CHOICES, group="core",
                  wide=True),
            _batch_field(),
            Field("lora_slots", "Flux LoRA stack", "repeat",
                  repeat=_pair_tail(_flux_lora_choices,
                                    "🎭 Flux LoRA stack (`loras/flux2/`)")),
        ),
    ),

    TabSchema(
        model_registry='klein',
        key=Key.KLEIN_I2I, handler=handlers.generate_klein_edit,
        lane=handlers.COMFY_LANE, prompt_field="prompt",
        result_keys=IMAGE_KEYS, tab_id="klein",
        icon="🧩", blurb="Flux 2 Klein, with up to two reference images.",
        category="edit", route="/edit/klein", submit_label="Edit",
        groups=(G_INPUTS, G_PROMPT, Group("core", "Sampling", dense=True),
                Group("size", "Output size", dense=True), G_SEED),
        fields=(
            *_two_image_fields("Input image 1 (paste with Ctrl+V)",
                               "➕ Enable input image 2", "Input image 2"),
            Field("prompt", "Edit prompt", "textarea", "", lines=4,
                  group="prompt"),
            *_seed_fields(),
            Field("model", "Model", "select",
                  lambda: handlers.KLEIN_MODEL_CHOICES[0],
                  choices=lambda: handlers.KLEIN_MODEL_CHOICES, group="core",
                  wide=True),
            Field("steps", "Steps", "slider", lambda: handlers._k_steps,
                  lo=1, hi=50, step=1, group="core"),
            Field("cfg", "CFG", "slider", lambda: handlers._k_cfg, lo=0.5,
                  hi=8.0, step=0.1, group="core"),
            Field("guidance", "Guidance", "slider",
                  lambda: handlers._k_guidance, lo=0.0, hi=10.0, step=0.1,
                  group="core"),
            Field("sampler", "Sampler", "select",
                  KLEIN_DEFAULTS["sampler_name"], choices=SAMPLERS,
                  group="core"),
            Field("scheduler", "Scheduler", "select",
                  KLEIN_DEFAULTS["scheduler"], choices=KLEIN_SCHEDULERS,
                  group="core"),
            Field("reference_mp",
                  "Reference size (MP) — what the model looks at", "slider",
                  KLEIN_REFERENCE_MEGAPIXELS, lo=0.25, hi=4.0, step=0.05,
                  group="size", wide=True),
            Field("output_mode", "Mode", "select", KLEIN_OUTPUT_MODES[0],
                  choices=KLEIN_OUTPUT_MODES, group="size", wide=True),
            Field("output_mp", "Megapixels (scale mode)", "slider",
                  KLEIN_DEFAULT_MEGAPIXELS, lo=0.25, hi=4.0, step=0.05,
                  group="size", wide=True,
                  show_if=("output_mode", KLEIN_OUTPUT_SCALED)),
            Field("custom_width", "Width (custom mode)", "number",
                  KLEIN_DEFAULT_CUSTOM_SIZE[0], lo=64, step=1, group="size",
                  show_if=("output_mode", KLEIN_OUTPUT_CUSTOM)),
            Field("custom_height", "Height (custom mode)", "number",
                  KLEIN_DEFAULT_CUSTOM_SIZE[1], lo=64, step=1, group="size",
                  show_if=("output_mode", KLEIN_OUTPUT_CUSTOM)),
            _batch_field(),
            Field("lora_slots", "LoRA stack", "repeat",
                  repeat=_triple_tail(
                      _klein_lora_choices, handlers.klein_default_lora_slots,
                      "🎭 LoRA stack — model + CLIP (`loras/klein/`)")),
        ),
    ),

    TabSchema(
        key=Key.FACESWAP, handler=handlers.generate_faceswap,
        lane=handlers.COMFY_LANE, prompt_field=None,
        result_keys=PLAIN_KEYS, tab_id="faceswap",
        icon="🎭", blurb="Put one face into another photograph.",
        category="edit", route="/edit/faceswap", submit_label="Swap",
        groups=(G_INPUTS, Group("core", "Detection", dense=True),
                Group("restore", "Restoration", dense=True)),
        fields=(
            Field("base_image",
                  "Base image — the face here gets replaced "
                  "(paste with Ctrl+V)",
                  "image", None, group="inputs", column="right"),
            Field("face_image",
                  "Reference face — the face to put in (paste with Ctrl+V)",
                  "image", None, group="inputs", column="right"),
            Field("swap_model", "Swap model", "select",
                  lambda: handlers.default_swap_model(),
                  choices=lambda: handlers.SWAP_MODEL_CHOICES, group="core",
                  wide=True),
            Field("facedetection", "Face detector", "select",
                  REACTOR_DEFAULT_DETECTOR, choices=REACTOR_DETECTORS,
                  group="core", wide=True),
            Field("restore_model", "Face restoration (optional)", "select",
                  lambda: handlers.RESTORE_CHOICES[0],
                  choices=lambda: handlers.RESTORE_CHOICES, group="restore",
                  wide=True),
            Field("restore_visibility", "Restoration visibility", "slider",
                  1.0, lo=0.1, hi=1.0, step=0.05, group="restore", wide=True),
            Field("codeformer_weight",
                  "CodeFormer weight (0 = stronger cleanup, 1 = stay closer "
                  "to the swap)",
                  "slider", 0.5, lo=0.0, hi=1.0, step=0.05, group="restore",
                  wide=True),
            Field("input_index", "Face index in base image", "text", "0",
                  lines=1, group="core",
                  hint="Left to right. Also accepts 0,1 or 0-2"),
            Field("source_index", "Face index in reference", "text", "0",
                  lines=1, group="core",
                  hint="Left to right. Also accepts 0,1 or 0-2"),
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
                  "textarea", WAN_DEFAULT_NEGATIVE, lines=2, group="prompt"),
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

    TabSchema(
        key=Key.JSON_BATCH, handler=handlers.generate_from_json,
        lane=handlers.COMFY_LANE, prompt_field=None,
        result_keys=PLAIN_KEYS, tab_id="json",
        icon="📦", blurb="Run a JSON array of jobs straight through the "
                        "graph.",
        category="library", route="/library/batch",
        submit_label="Run batch",
        groups=(Group("core", "Batch source"),),
        fields=(
            Field("json_file", "Upload JSON file", "file", None,
                  group="core", wide=True, accept="application/json,.json"),
            Field("json_text", "…or paste a JSON array here", "textarea", "",
                  lines=14, group="core", wide=True),
        ),
    ),
)

BY_KEY = {str(schema.key): schema for schema in SCHEMAS}


def get(key) -> TabSchema:
    """One tab's schema. KeyError for a tab that has no form."""
    return BY_KEY[str(key)]


def entitled() -> tuple:
    """The schemas this licence grants, in navigation order.

    One of the four layers guarding the licence gate (context.md 4.5).
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
# What the ~150 lines of *_changed handlers in ui.py were made of. Every
# one of them is a lookup in a dict config.py already holds — a model
# dropdown that resets Steps and CFG, a Wan mode radio that does the same,
# a Flux model that swaps its trigger words into the prompt. Shipped as
# data so React applies them locally, with no round trip and no second
# copy of the same three numbers.


def _model_rows(registry, variant_defaults, keys, available, info):
    """One model registry, flattened for the browser.

    `keys` names the per-variant numbers this family has — (steps, cfg)
    for Krea 2, (steps, guidance, turbo_lora) for Flux — because the
    registries genuinely differ and pretending otherwise would mean the
    browser guessing which of the two a name means.
    """
    rows = []
    for entry in registry:
        variant = entry.get("variant", "turbo")
        defaults = dict(variant_defaults.get(variant, {}))
        for key in keys:
            if key in entry:
                defaults[key] = entry[key]
        rows.append({
            "name": entry["name"],
            "file": entry.get("file"),
            "variant": variant,
            "trigger": entry.get("trigger") or "",
            "defaults": {key: defaults.get(key) for key in keys},
            # The model info line under every Model dropdown, which the
            # React app has no other way to render: whether the weights
            # are on this pod is a fact about this pod's disk.
            "available": bool(available(entry)),
            "info": info(entry),
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
        "models": {
            "krea2": _model_rows(
                KREA2_MODELS, VARIANT_DEFAULTS, ("steps", "cfg"),
                lambda e: handlers.model_file_available(e),
                handlers._model_info_text),
            "v2": _model_rows(
                V2_MODELS, V2_VARIANT_DEFAULTS,
                ("steps", "cfg", "turbo_lora"),
                lambda e: handlers.v2_model_available(e),
                handlers._v2_model_info_text),
            "flux": _model_rows(
                FLUX_MODELS, FLUX_VARIANT_DEFAULTS,
                ("steps", "guidance", "turbo_lora"),
                lambda e: handlers.flux_model_available(e),
                handlers._flux_model_info_text),
            "klein": _model_rows(
                KLEIN_MODELS, {}, ("steps", "cfg", "guidance"),
                lambda e: handlers.klein_model_available(e),
                handlers._klein_model_info_text),
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
        "klein": {
            "outputModes": KLEIN_OUTPUT_MODES,
            "sampler": KLEIN_SAMPLER_DEFAULTS,
            "defaults": KLEIN_DEFAULTS,
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

    **The most important defensive measure in the rewrite.** What it
    replaces is the safety Gradio gave for free — `click(inputs=[...])`
    written six lines under the components it names, so a human editing
    one saw the other. Nothing enforces that across a language boundary,
    and the failure it prevents is silent: rename a parameter in
    handlers.py, forget it here, and `call_args` shifts every argument
    after it. generate_v2 has 31.
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

        if schema.prompt_field and schema.field(schema.prompt_field) is None:
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
    """schema.settings() == ui's _krea_settings(), for the same values.

    This is the entire proof that presets keep working, and it really is
    enough on its own. `presetWire()` in license-validator/src/app.js
    returns `settings: row.settings || {}` and never looks inside, so the
    blob is opaque server-side — nothing about it is validated, migrated
    or indexed anywhere but here (context.md 4.10). If this local
    comparison holds, a preset written by the Gradio build loads into the
    React build and back again unchanged.

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
