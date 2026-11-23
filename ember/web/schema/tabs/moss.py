"""The 🌿 MOSS tab."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.moss.constants import (
    MOSS_DEFAULT_SIZE,
    MOSS_DEFAULT_SPEED,
    MOSS_NORMAL,
    MOSS_SAMPLERS,
    MOSS_SCHEDULERS,
    MOSS_SIZES,
    MOSS_SPEEDS,
)
from ember.pipelines.moss.handler import generate_moss
from ember.web.schema.fields import (
    GEN_PRESET_NOTE,
    G_CORE,
    G_PROMPT,
    G_REFINE,
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
    _refine_fields,
    _seed_fields,
)
from ember.web.schema.model import Field, TabSchema

Key = features.Key


# Anima's form with Qwen Edit 2511's Speed select: Prompt, Output (size,
# Speed and model), a Sampler whose Steps and CFG show only for Normal —
# Fast runs the DMD LoRA's own 8 steps at CFG 1 — the ⚡ Ember Lite
# refine, Seed & batch, Presets under moss_t2i, and the plain model-only
# LoRA stack over the feature's own list. Normal's Steps and CFG come from
# the model record (50 and 3).
MOSS_SCHEMA = TabSchema(
    model_registry=handlers.MOSS_T2I,
    key=Key.MOSS_T2I, handler=generate_moss,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="moss",
    icon="🌿",
    blurb="Photos and illustrations that follow a long, detailed prompt.",
    category="generate", route="/generate/moss",
    submit_label="Generate", preset_tab=presets.TAB_MOSS,
    preset_note=GEN_PRESET_NOTE,
    groups=(G_PROMPT, G_CORE, G_SAMPLER, G_REFINE, G_SEED, G_SAVE_PRESET),
    fields=(
        Field("prompt", "Prompt", "textarea",
              "A candid photo of a woman reading on a sunlit balcony in "
              "Mumbai, potted plants and laundry lines behind her, soft "
              "afternoon light, shot on a phone, natural skin texture",
              lines=6, group="prompt",
              hint="MOSS reads long prompts well — describe the subject, "
                   "the place, the light and the camera."),
        Field("negative", "Negative prompt", "textarea",
              _model_negative(handlers.MOSS_T2I, ""), lines=3,
              group="prompt", collapsed=True, hint=_CFG_NOTE),
        Field("output_size", "Output size", "select", MOSS_DEFAULT_SIZE,
              choices=list(MOSS_SIZES), group="core", preset="output_size",
              wide=True),
        Field("speed", "Speed", "select", MOSS_DEFAULT_SPEED,
              choices=list(MOSS_SPEEDS), group="core", preset="speed",
              wide=True,
              hint="Normal is the model's own 50 steps at CFG 3. Fast runs "
                   "an 8-step distilled LoRA at CFG 1 — several times "
                   "quicker, with a little less detail."),
        _model_field(handlers.MOSS_T2I),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.MOSS_T2I, "steps"), lo=1, hi=80,
              step=1, group="sampler", preset="steps",
              show_if=("speed", MOSS_NORMAL)),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.MOSS_T2I, "cfg"),
              lo=1.0, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE, show_if=("speed", MOSS_NORMAL)),
        Field("sampler", "Sampler", "select", MOSS_SAMPLERS[0],
              choices=MOSS_SAMPLERS, group="sampler", preset="sampler"),
        Field("scheduler", "Scheduler", "select", MOSS_SCHEDULERS[0],
              choices=MOSS_SCHEDULERS, group="sampler",
              preset="scheduler"),
        *_seed_fields(),
        _batch_field(),
        *_preset_save_fields(),
        *_refine_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.MOSS_T2I)),
    ),
)
