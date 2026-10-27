"""The MiniMax H3 tabs: 🎥 MiniMax I2V and 🎞️ MiniMax T2V."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import catalog
from ember.licensing import presets
from ember.pipelines.krea2.constants import SAMPLERS
from ember.pipelines.minimax.constants import (
    MINIMAX_ASPECT_RATIOS,
    MINIMAX_DEFAULT_ASPECT,
    MINIMAX_DEFAULT_RESOLUTION,
    MINIMAX_DEFAULT_SECONDS,
    MINIMAX_MAX_SECONDS,
    MINIMAX_MIN_SECONDS,
    MINIMAX_RESOLUTIONS,
    MINIMAX_SAMPLING_DEFAULTS,
    MINIMAX_SCHEDULERS,
    MINIMAX_T2V_RESOLUTIONS,
)
from ember.pipelines.minimax.handler import (
    generate_minimax_t2v,
    generate_minimax_video,
)
from ember.pipelines.minimax.workflow import model_defaults
from ember.web.schema.fields import (
    G_PROMPT,
    G_SAVE_PRESET,
    G_SEED,
    MINIMAX_PRESET_NOTE,
    VIDEO_KEYS,
    _batch_field,
    _blank_lora_tail,
    _model_field,
    _preset_save_fields,
    _seed_fields,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


# The two MiniMax tabs. One model, one graph, one download; the core
# node takes an optional first frame, so the text tab is the image tab
# minus its upload — which is why the two field lists are the same
# list with `image` swapped for `aspect`. Both run on the main lane:
# see runner._run_wan_jobs on why they never ride the Wan instance.
# The Model dropdown offers the feature's catalogue models — the built-in
# H3 int8 when the licence server lists none — so another H3 checkpoint is
# a database record, not a build. It comes after the prompt and, like
# every new control, does not shift what older recipes restore: those are
# matched by label in order (TabSchema.restore_recipe).
# No negative prompt and no CFG, because the model is guidance-distilled
# like Flux; the prompt carries the sound as well as the motion, since
# every clip comes back with a soundtrack. The LoRA stack is each tab's
# own catalogue list, eight blank rows like Krea2's. The two tabs share one
# preset list (presets.TAB_MINIMAX), read and saved from either — the text
# tab's presets carry an aspect ratio the image tab has no control for,
# which the image tab simply skips.
# Both carry the Auto prompt panel, which writes the Prompt box from a
# short idea before the click — see ember/pipelines/minimax/autoprompt.py.
# The Sampling controls start on the default model's recipe and follow the
# Model dropdown (model_defaults, sent in the /catalog rows): Eros Max's
# turbo-hybrid record turns the Turbo LoRA to 0 and asks for LCM, the
# built-in one names nothing and gets the template's. A preset, recipe or
# token that names a model but none of these takes that model's
# (TabSchema.model_follows), so one saved before the controls existed
# still runs its model the way it was saved.
MINIMAX_SAMPLERS = SAMPLERS + ["uni_pc", "lcm"]


def _minimax_default(feature, key):
    """The feature's default model's value for one Sampling control."""
    def default():
        models = catalog.feature_models(feature)
        return (model_defaults(models[0])[key] if models
                else MINIMAX_SAMPLING_DEFAULTS[key])
    return default


def _minimax_follows(feature):
    """model id -> the Sampling values picking that model sets, or {}."""
    def follows(model_id):
        model = next((m for m in catalog.feature_models(feature)
                      if m.id == model_id), None)
        return model_defaults(model) if model is not None else {}
    return follows


def _sampling_fields(feature):
    """Steps through Turbo LoRA, the two tabs' shared Sampling group."""
    return (
        Field("steps", "Steps", "slider", _minimax_default(feature, "steps"),
              lo=1, hi=40, step=1, group="sampling", preset="steps",
              hint="Picking a model sets these to its recipe."),
    )


def _recipe_fields(feature):
    """Sampler through Turbo LoRA, after Duration in submission order."""
    return (
        Field("sampler", "Sampler", "select",
              _minimax_default(feature, "sampler"),
              choices=MINIMAX_SAMPLERS, group="sampling",
              preset="sampler", wide=True),
        Field("scheduler", "Scheduler", "select",
              _minimax_default(feature, "scheduler"),
              choices=MINIMAX_SCHEDULERS, group="sampling",
              preset="scheduler"),
        Field("shift_video", "Shift (video)", "slider",
              _minimax_default(feature, "shift_video"),
              lo=1, hi=20, step=0.5, group="sampling", preset="shift_video",
              hint="12 is the model's own."),
        Field("shift_audio", "Shift (audio)", "slider",
              _minimax_default(feature, "shift_audio"),
              lo=1, hi=20, step=0.5, group="sampling", preset="shift_audio",
              hint="3 is the model's own."),
        Field("turbo_strength", "Turbo LoRA", "slider",
              _minimax_default(feature, "turbo_strength"),
              lo=0, hi=1.5, step=0.05, group="sampling",
              preset="turbo_strength",
              hint="0 for a checkpoint with turbo merged in."),
    )


MINIMAX_I2V_SCHEMA = TabSchema(
    model_registry=handlers.MINIMAX_I2V,
    key=Key.MINIMAX_I2V, handler=generate_minimax_video,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=VIDEO_KEYS, tab_id="minimax_i2v", output="video",
    icon="🎥", blurb="Turn a still into a clip that comes with its own "
                    "sound.",
    category="video", route="/video/minimax", submit_label="Animate",
    autoprompt=True, preset_tab=presets.TAB_MINIMAX,
    preset_note=MINIMAX_PRESET_NOTE,
    model_follows=_minimax_follows(handlers.MINIMAX_I2V),
    groups=(Group("inputs", "Start frame", column="right"), G_PROMPT,
            Group("core", "Output", dense=True),
            Group("sampling", "Sampling", dense=True), G_SEED,
            G_SAVE_PRESET),
    fields=(
        Field("image", "Start image (paste with Ctrl+V)", "image", None,
              group="inputs", column="right"),
        Field("prompt", "Prompt (the motion, and the sound)", "textarea",
              "", lines=4, group="prompt",
              placeholder="she looks up and laughs, rain against the "
                          "window, a kettle starting to whistle"),
        _model_field(handlers.MINIMAX_I2V),
        *_seed_fields(),
        *_sampling_fields(handlers.MINIMAX_I2V),
        Field("resolution", "Resolution (keeps the source aspect)",
              "radio", MINIMAX_DEFAULT_RESOLUTION,
              choices=list(MINIMAX_RESOLUTIONS), group="core",
              preset="resolution", wide=True),
        Field("seconds", "Duration (seconds)", "slider",
              MINIMAX_DEFAULT_SECONDS, lo=MINIMAX_MIN_SECONDS,
              hi=MINIMAX_MAX_SECONDS, step=1.0, group="sampling",
              preset="seconds", wide=True,
              hint="Snapped to the model's frame grid at 24 fps."),
        *_recipe_fields(handlers.MINIMAX_I2V),
        _batch_field(hi=10),
        *_preset_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.MINIMAX_I2V)),
    ),
)

MINIMAX_T2V_SCHEMA = TabSchema(
    model_registry=handlers.MINIMAX_T2V,
    key=Key.MINIMAX_T2V, handler=generate_minimax_t2v,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=VIDEO_KEYS, tab_id="minimax_t2v", output="video",
    icon="🎞️", blurb="A clip with sound, from words alone.",
    category="video", route="/video/minimax-t2v",
    submit_label="Generate",
    autoprompt=True, preset_tab=presets.TAB_MINIMAX,
    preset_note=MINIMAX_PRESET_NOTE,
    model_follows=_minimax_follows(handlers.MINIMAX_T2V),
    groups=(G_PROMPT, Group("core", "Output", dense=True),
            Group("sampling", "Sampling", dense=True), G_SEED,
            G_SAVE_PRESET),
    fields=(
        Field("prompt", "Prompt (the scene, the motion, and the sound)",
              "textarea", "", lines=5, group="prompt",
              placeholder="a kettle on a gas hob comes to the boil and "
                          "whistles, steam catching the window light, "
                          "slow push-in"),
        _model_field(handlers.MINIMAX_T2V),
        Field("aspect", "Aspect ratio", "select", MINIMAX_DEFAULT_ASPECT,
              choices=list(MINIMAX_ASPECT_RATIOS), group="core",
              preset="aspect", wide=True),
        *_seed_fields(),
        *_sampling_fields(handlers.MINIMAX_T2V),
        Field("resolution", "Resolution", "radio",
              MINIMAX_DEFAULT_RESOLUTION,
              choices=MINIMAX_T2V_RESOLUTIONS, group="core",
              preset="resolution", wide=True),
        Field("seconds", "Duration (seconds)", "slider",
              MINIMAX_DEFAULT_SECONDS, lo=MINIMAX_MIN_SECONDS,
              hi=MINIMAX_MAX_SECONDS, step=1.0, group="sampling",
              preset="seconds", wide=True,
              hint="Snapped to the model's frame grid at 24 fps."),
        *_recipe_fields(handlers.MINIMAX_T2V),
        _batch_field(hi=10),
        *_preset_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.MINIMAX_T2V)),
    ),
)
