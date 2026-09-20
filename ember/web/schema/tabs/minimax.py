"""The MiniMax H3 tabs: 🎥 MiniMax I2V and 🎞️ MiniMax T2V."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.pipelines.krea2.constants import SAMPLERS
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
from ember.pipelines.minimax.handler import (
    generate_minimax_t2v,
    generate_minimax_video,
)
from ember.web.schema.fields import (
    G_PROMPT,
    G_SEED,
    VIDEO_KEYS,
    _batch_field,
    _blank_lora_tail,
    _seed_fields,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


# The two MiniMax tabs. One model, one graph, one download; the core
# node takes an optional first frame, so the text tab is the image tab
# minus its upload — which is why the two field lists are the same
# list with `image` swapped for `aspect`. Both run on the main lane:
# see runner._run_wan_jobs on why they never ride the Wan instance.
# No negative prompt and no CFG, because the model is guidance-distilled
# like Flux; the prompt carries the sound as well as the motion, since
# every clip comes back with a soundtrack. The LoRA stack is each tab's
# own catalogue list, eight blank rows like Krea2's; there are no presets.
MINIMAX_I2V_SCHEMA = TabSchema(
    key=Key.MINIMAX_I2V, handler=generate_minimax_video,
    lane=runner.COMFY_LANE, prompt_field="prompt",
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
)

MINIMAX_T2V_SCHEMA = TabSchema(
    key=Key.MINIMAX_T2V, handler=generate_minimax_t2v,
    lane=runner.COMFY_LANE, prompt_field="prompt",
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
)
