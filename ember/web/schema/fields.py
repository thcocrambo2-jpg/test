"""The blocks the tab definitions are built from.

Seven forms, the same handful of control clusters in a different order.
Each builder returns Fields (or a `Repeat` tail) ready to drop into a
`TabSchema.fields` tuple, in submission order.
"""

from ember.licensing import catalog as assets
from ember.generation import handlers
from ember.pipelines.krea2_v2.constants import (
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
)
from ember.web.schema.model import Field, Group, Repeat


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
    verbatim; see ember.web.tabschema on why that matters.
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
