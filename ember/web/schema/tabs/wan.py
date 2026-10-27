"""The Wan 2.2 image-to-video tab."""

from dataclasses import replace

from ember import features
from ember.generation import runner
from ember.licensing import catalog
from ember.pipelines.krea2.constants import SAMPLERS
from ember.pipelines.wan.constants import (
    WAN_ARCH_5B,
    WAN_DEFAULT_NEGATIVE,
    WAN_DEFAULT_RESOLUTION,
    WAN_INTERPOLATE_DEFAULT,
    WAN_LEGACY_MODEL_VALUES,
    WAN_MAX_SECONDS,
    WAN_MODE_DEFAULTS,
    WAN_RESOLUTIONS,
    WAN_VARIANT,
)
from ember.pipelines.wan.handler import generate_wan_video
from ember.web.schema.fields import (
    G_PROMPT,
    G_SEED,
    VIDEO_KEYS,
    _batch_field,
    _model_field,
    _model_negative,
    _seed_fields,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


# The Model dropdown offers the feature's catalogue models — the built-in
# 14B and 5B when the licence server lists none — so another Wan
# checkpoint is a database record, not a build. Its values are model ids;
# they were these two labels before, and recipes and open forms still carry
# them, which `aliases` reads as the ids they became.
WAN_FEATURE = str(Key.WAN_I2V)


def _wan_14b_ids() -> list:
    """The models Mode applies to: every one that is not a 5B."""
    return [model.id for model in catalog.feature_models(WAN_FEATURE)
            if model.arch != WAN_ARCH_5B]


# The Mode radio's strings are what generate_wan_video branches on
# (`mode.startswith("turbo")`) — wire values, not labels, so they cannot be
# reworded freely.
WAN_MODE_CHOICES = ["Turbo (Lightning, 4 steps)", "Raw (20 steps)"]

WAN_SCHEMA = TabSchema(
    model_registry=WAN_FEATURE,
    key=Key.WAN_I2V, handler=generate_wan_video,
    lane=runner.WAN_LANE, prompt_field="prompt",
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
              "textarea", _model_negative(WAN_FEATURE, WAN_DEFAULT_NEGATIVE),
              lines=2, group="prompt", collapsed=True),
        replace(_model_field(WAN_FEATURE),
                aliases=WAN_LEGACY_MODEL_VALUES, preset=None),
        Field("mode", "Mode (14B only — the 5B has no Lightning)",
              "radio",
              WAN_MODE_CHOICES[0 if WAN_VARIANT == "turbo" else 1],
              choices=WAN_MODE_CHOICES, group="core", wide=True,
              show_if=("model", _wan_14b_ids)),
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
        Field("interpolate", "Smooth motion (RIFE frame interpolation)",
              "bool", WAN_INTERPOLATE_DEFAULT, group="core", wide=True,
              hint="Adds frames in between Wan's: 14B clips are saved at "
                   "64 fps, 5B at 48. Same length, a few seconds more "
                   "per video."),
    ),
)
