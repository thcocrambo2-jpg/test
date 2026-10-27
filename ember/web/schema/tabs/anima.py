"""The 🌸 Anima tab."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.anima.constants import (
    ANIMA_DEFAULT_SIZE,
    ANIMA_LORA_SLOTS,
    ANIMA_NEGATIVE_HINT,
    ANIMA_SAMPLERS,
    ANIMA_SCHEDULERS,
    ANIMA_SIZES,
)
from ember.pipelines.anima.handler import generate_anima
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
    _blank_slots,
    _model_field,
    _model_negative,
    _model_setting,
    _preset_save_fields,
    _seed_fields,
    _triple_tail,
)
from ember.web.schema.model import Field, TabSchema

Key = features.Key


# Qwen 2.1's text-to-image form on Anima's sizes and samplers: Prompt,
# Output (size and model), Sampler with a scheduler, Seed & batch, Presets
# under anima_t2i, and a four-row model + CLIP LoRA stack over the
# feature's own list. Steps and CFG come from the model record (8 and 1
# for BAS:NSFW, a Turbo merge).
ANIMA_SCHEMA = TabSchema(
    model_registry=handlers.ANIMA_T2I,
    key=Key.ANIMA_T2I, handler=generate_anima,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="anima",
    icon="🌸",
    blurb="Anime illustrations from Danbooru-style tags or a sentence.",
    category="generate", route="/generate/anima",
    submit_label="Generate", preset_tab=presets.TAB_ANIMA,
    preset_note=GEN_PRESET_NOTE,
    groups=(G_PROMPT, G_CORE, G_SAMPLER, G_SEED, G_SAVE_PRESET),
    fields=(
        Field("prompt", "Prompt", "textarea",
              "masterpiece, best quality, 1girl, solo, silver hair, blue "
              "eyes, school uniform, cherry blossoms, petals in the wind, "
              "looking at viewer, smile",
              lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea",
              _model_negative(handlers.ANIMA_T2I, ""), lines=3,
              group="prompt", collapsed=True,
              hint=_CFG_NOTE + " The model card suggests: "
                   + ANIMA_NEGATIVE_HINT + "."),
        Field("output_size", "Output size", "select", ANIMA_DEFAULT_SIZE,
              choices=list(ANIMA_SIZES), group="core", preset="output_size",
              wide=True),
        _model_field(handlers.ANIMA_T2I),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.ANIMA_T2I, "steps"), lo=1, hi=60,
              step=1, group="sampler", preset="steps"),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.ANIMA_T2I, "cfg"),
              lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE),
        Field("sampler", "Sampler", "select", ANIMA_SAMPLERS[0],
              choices=ANIMA_SAMPLERS, group="sampler", preset="sampler"),
        Field("scheduler", "Scheduler", "select", ANIMA_SCHEDULERS[0],
              choices=ANIMA_SCHEDULERS, group="sampler",
              preset="scheduler"),
        *_seed_fields(),
        _batch_field(),
        *_preset_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_triple_tail(handlers.ANIMA_T2I,
                                  _blank_slots(ANIMA_LORA_SLOTS),
                                  "LoRA stack — model + CLIP")),
    ),
)
