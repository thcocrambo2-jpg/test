"""The schema types: what a control is, and what a tab is made of.

`Field`, `Group`, `Repeat` and `TabSchema` are the shapes; `Invalid` is
what the submit path raises. The five consumers these serve, and the
invariant that `Field.name` *is* the handler's parameter name, are
described in `ember.web.tabschema`, which assembles the tabs out of them.
"""

from dataclasses import dataclass, field as dc_field, replace
from typing import Any, Callable

from ember.licensing import catalog as assets
from ember import features
from ember.generation import loras
from ember.generation import sources

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

    The first ten attributes are the contract ember.web.tabschema names.
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
            # The name its source was kept under, if it was and still is
            # (sources.py). A recipe from before that holds None, and a
            # source swept since is gone; both leave the drop zone alone.
            if sources.path(value) is None:
                return False, None
            return True, value
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
    handler: Callable               # the pipeline's generate_* generator
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
    # The MiniMax tabs' Auto prompt panel, which writes the Prompt box from
    # a short idea (ember/pipelines/minimax/autoprompt.py). A flag and not
    # a Field because it is not a handler argument: what it produces is the
    # prompt, which already is one.
    autoprompt: bool = False

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
            "promptField": self.prompt_field,
            "resultKeys": list(self.result_keys),
            "presetTab": self.preset_tab,
            "presetNote": self.preset_note,
            "modelRegistry": self.model_registry,
            "autoprompt": self.autoprompt,
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

    def recipe_fields(self, values: dict, kept: dict | None = None) -> list:
        """[[label, value], ...] in submission order — ui._recipe_fields.

        Byte-compatible with what is already on pods' disks, which is the
        whole point: `.recipes.jsonl` is keyed positionally and read back
        by label, so a reworded label or a moved control orphans the
        history rather than failing loudly. The two rules it reproduces:

          * a control that is not a *setting* — the publish and preset
            tickboxes, `ui._RECIPE_SKIP` — is stored as None, so loading a
            recipe cannot silently re-arm a publish;
          * an uploaded file is stored as the name `kept` gives it — the
            copy sources.py keeps so a recipe can hand the picture back —
            or as None when there is no copy, as it always used to be.
        """
        kept = kept or {}
        rows = []
        for f in self.named():
            value = values.get(f.name)
            if f.kind == "image":
                value = kept.get(f.name)
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
        (loras.stored_lora — the same call the handlers make).

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
                rows.append([loras.stored_lora(v) if part.name == "name"
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
