"""The ⚡ Ember Lite tab — Z-Image Turbo underneath."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.zimage.constants import (
    ZIMAGE_DEFAULT_DENOISE,
    ZIMAGE_DEFAULT_MULTIPLIER,
    ZIMAGE_DEFAULT_NEGATIVE,
    ZIMAGE_DEFAULT_RESOLUTION,
    ZIMAGE_RESOLUTIONS,
    ZIMAGE_SAMPLERS,
)
from ember.pipelines.zimage.handler import generate_zimage
from ember.web.schema.fields import (
    GEN_PRESET_NOTE,
    G_CORE,
    G_PROMPT,
    G_SAMPLER,
    G_SAVE_PRESET,
    G_SEED,
    IMAGE_KEYS,
    _CFG_NOTE,
    _batch_field,
    _blank_lora_tail,
    _model_field,
    _model_negative,
    _model_setting,
    _preset_save_fields,
    _seed_fields,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


# Krea2's form, on a plain KSampler that maps one to one onto its
# controls. What Krea2 has no control for sits where it belongs: the
# resolution multiplier with the resolution in Output, and the upscale
# tick in a section of its own, because it switches a whole second pass
# on. Presets like the MiniMax tabs — its own list, and no prompt library
# to publish to. The LoRA stack is Z-Image's own catalogue list, since
# Krea LoRAs do not load on this model. The optional start image sits
# in a closed section in the right column, above the result, like the
# Video tab's start frame, so the tab reads as text to image until it is
# opened.
ZIMAGE_SCHEMA = TabSchema(
    model_registry=handlers.ZIMAGE_T2I,
    key=Key.ZIMAGE_T2I, handler=generate_zimage,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="zimage",
    icon="⚡", blurb="Fast, realistic text-to-image.",
    category="generate", route="/generate/ember-lite",
    submit_label="Generate", preset_tab=presets.TAB_ZIMAGE,
    preset_note=GEN_PRESET_NOTE,
    groups=(Group("start", "Start image", column="right", collapsible=True,
                  default_open=False),
            G_PROMPT, G_CORE, G_SAMPLER,
            Group("upscale", "Upscale", dense=True, collapsible=True),
            G_SEED, G_SAVE_PRESET),
    fields=(
        Field("prompt", "Prompt", "textarea",
              "A candid smartphone photo of a woman reading in a sunlit "
              "café, natural skin texture, soft window light",
              lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea",
              _model_negative(handlers.ZIMAGE_T2I, ZIMAGE_DEFAULT_NEGATIVE),
              lines=3, group="prompt",
              collapsed=True, hint=_CFG_NOTE),
        *_seed_fields(),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.ZIMAGE_T2I, "steps"), lo=1, hi=60,
              step=1, group="sampler", preset="steps"),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.ZIMAGE_T2I, "cfg"),
              lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE),
        Field("resolution", "Resolution", "select", ZIMAGE_DEFAULT_RESOLUTION,
              choices=list(ZIMAGE_RESOLUTIONS), group="core",
              preset="resolution", wide=True),
        Field("multiplier", "Resolution multiplier", "slider",
              ZIMAGE_DEFAULT_MULTIPLIER, lo=0.5, hi=2.0, step=0.05,
              group="core", preset="multiplier",
              hint="Scales both sides of the resolution."),
        Field("sampler", "Sampler", "select", ZIMAGE_SAMPLERS[0],
              choices=ZIMAGE_SAMPLERS, group="sampler", preset="sampler",
              wide=True),
        _model_field(handlers.ZIMAGE_T2I),
        Field("upscale", "Upscale 1.5x (UltimateSDUpscale + 4xLSDIR)",
              "bool", False, group="upscale", preset="upscale", wide=True,
              hint="A second, tiled pass at 1.5x the size. Only the "
                   "upscaled image is saved."),
        _batch_field(),
        *_preset_save_fields(),
        # After the preset boxes rather than at the top of the list, so
        # every control already in this tab's recipes keeps its position.
        Field("image", "Start image (optional, paste with Ctrl+V)", "image",
              None, group="start", column="right",
              hint="Image to image: re-renders this picture with the "
                   "prompt instead of starting from noise. It keeps its "
                   "own shape at the Resolution's size."),
        Field("denoise", "Denoise", "slider", ZIMAGE_DEFAULT_DENOISE,
              lo=0.05, hi=1.0, step=0.01, group="start", column="right",
              preset="denoise",
              hint="Only with a start image. 0.3–0.4 keeps its content "
                   "and repaints skin and light; 1 ignores it."),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.ZIMAGE_T2I)),
    ),
)
