"""The 🔶 Krea2 V2 tab."""

from ember import features
from ember.generation import handlers
from ember.licensing import presets
from ember.pipelines.krea2_v2.constants import (
    V2_ASPECT_RATIOS,
    V2_DEFAULT_ASPECT,
    V2_DEFAULT_MEGAPIXELS,
    V2_DEFAULT_MULTIPLE,
    V2_DEFAULT_NEGATIVE,
)
from ember.web.schema.fields import (
    GEN_PRESET_NOTE,
    G_CORE,
    G_PROMPT,
    G_SAMPLER,
    G_SAVE,
    G_SEED,
    G_VARIANCE,
    IMAGE_KEYS,
    _CFG_NOTE,
    _batch_field,
    _model_field,
    _save_fields,
    _seed_fields,
    _stack_slots,
    _triple_tail,
    _v2_sampler_fields,
    _v2_variance_fields,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


KREA2_V2_SCHEMA = TabSchema(
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
)
