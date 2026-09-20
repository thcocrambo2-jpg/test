"""The Wan 2.2 image-to-video tab."""

from ember import features
from ember.generation import runner
from ember.pipelines.krea2.constants import SAMPLERS
from ember.pipelines.wan.constants import (
    WAN_DEFAULT_NEGATIVE,
    WAN_DEFAULT_RESOLUTION,
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
    _seed_fields,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


# The Wan tab's two radio lists. They name model families rather than
# files, and the strings are what generate_wan_video branches on (`_is_wan_5b`, `mode.startswith("turbo")`) — so they
# are wire values, not labels, and cannot be reworded freely.
WAN_MODEL_CHOICES = ["14B two-expert (best quality, 16 fps)",
                     "5B TI2V (lighter, 24 fps)"]
WAN_MODE_CHOICES = ["Turbo (Lightning, 4 steps)", "Raw (20 steps)"]

WAN_SCHEMA = TabSchema(
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
)
