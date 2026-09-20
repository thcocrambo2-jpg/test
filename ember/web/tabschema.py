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


# ═══════════════════════════════════════════════════ the seven tabs
# Order is the order they appear in the navigation, which is ui.TAB_ORDER's
# order with the two bespoke tabs (Gallery, Prompt Library) taken out —
# those have no form and so no schema.

SCHEMAS = (
    KREA2_SCHEMA,
    KREA2_V2_SCHEMA,
    KREA2_EDIT_SCHEMA,
    KREA2_V2_EDIT_SCHEMA,
    WAN_SCHEMA,
    MINIMAX_I2V_SCHEMA,
    MINIMAX_T2V_SCHEMA,
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
