"""The 🧩 Qwen 2.1 Reference and 🌄 Qwen 2.1 tabs."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.qwen21.constants import (
    QWEN21_DEFAULT_DETAIL,
    QWEN21_DEFAULT_OUTPUT_SIZE,
    QWEN21_DETAIL_MAX,
    QWEN21_DETAIL_STEP,
    QWEN21_LORA_SLOTS,
    QWEN21_MAX_REFERENCES,
    QWEN21_OUTPUT_SIZES,
    QWEN21_SAMPLERS,
    QWEN21_SCHEDULERS,
    QWEN21_T2I_DEFAULT_SIZE,
    QWEN21_T2I_SIZES,
)
from ember.pipelines.qwen21.handler import (
    generate_qwen21_ref,
    generate_qwen21_t2i,
)
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
    _model_setting,
    _preset_save_fields,
    _seed_fields,
    _triple_tail,
)
from ember.web.schema.model import Field, Group, TabSchema

Key = features.Key


# The references sit in the right column above the result, like Krea2
# Edit's Images, so the prompt and the numbered tiles are side by side on
# a wide screen. When the page stacks they move to the top instead
# (`stack_first`), above the prompt that names them. Everything else is
# Z-Image's form: Output with the size and the model, a Sampler with a
# scheduler, the encoder's `resolution` in a closed Advanced section,
# presets with no prompt library, and a model + CLIP LoRA stack over the
# feature's own list, since the template's loader patched both.
QWEN21_REF_SCHEMA = TabSchema(
    model_registry=handlers.QWEN21_EDIT,
    key=Key.QWEN21_EDIT, handler=generate_qwen21_ref,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="qwen21ref",
    icon="🧩",
    blurb="Keep a character, an outfit or a product consistent: up to 10 "
          "reference images and a prompt.",
    category="edit", route="/edit/qwen21-reference",
    submit_label="Generate", preset_tab=presets.TAB_QWEN21,
    preset_note=GEN_PRESET_NOTE,
    groups=(Group("references", "References", column="right",
                  stack_first=True),
            G_PROMPT, G_CORE, G_SAMPLER,
            Group("advanced", "Advanced", collapsible=True,
                  default_open=False),
            G_SEED, G_SAVE_PRESET),
    fields=(
        Field("references", "Reference images (paste with Ctrl+V)",
              "images", (), lo=1, hi=QWEN21_MAX_REFERENCES,
              group="references", column="right",
              hint="Refer to images by number in your prompt: “the woman "
                   "in image 1 wearing the jacket from image 2”.",
              empty_note="Add at least one reference image to generate."),
        Field("prompt", "Prompt", "textarea",
              "The woman from image 1 wearing the leather jacket from "
              "image 2, sitting at a table outside the café in image 3. "
              "Late afternoon sun, candid 35 mm photo, shallow depth of "
              "field.",
              lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea", "", lines=3,
              group="prompt", collapsed=True, hint=_CFG_NOTE),
        Field("output_size", "Output size", "select",
              QWEN21_DEFAULT_OUTPUT_SIZE, choices=list(QWEN21_OUTPUT_SIZES),
              group="core", preset="output_size", wide=True),
        _model_field(handlers.QWEN21_EDIT),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.QWEN21_EDIT, "steps"), lo=1, hi=60,
              step=1, group="sampler", preset="steps"),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.QWEN21_EDIT, "cfg"),
              lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE),
        Field("sampler", "Sampler", "select", QWEN21_SAMPLERS[0],
              choices=QWEN21_SAMPLERS, group="sampler", preset="sampler"),
        Field("scheduler", "Scheduler", "select", QWEN21_SCHEDULERS[0],
              choices=QWEN21_SCHEDULERS, group="sampler",
              preset="scheduler"),
        Field("reference_detail", "Reference detail", "slider",
              QWEN21_DEFAULT_DETAIL, lo=0, hi=QWEN21_DETAIL_MAX,
              step=QWEN21_DETAIL_STEP, group="advanced",
              preset="reference_detail", wide=True,
              hint="How finely each reference is read. 0 keeps each "
                   "image's own size."),
        *_seed_fields(),
        _batch_field(),
        *_preset_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_triple_tail(handlers.QWEN21_EDIT,
                                  _blank_slots(QWEN21_LORA_SLOTS),
                                  "LoRA stack — model + CLIP")),
    ),
)


# The Reference tab's form with the references and Advanced taken out:
# Prompt, Output with the size and the model, a Sampler with a scheduler,
# presets with no prompt library, and the same model + CLIP LoRA stack over
# this feature's own list. Sizes are the template's note, 1:1 first, as the
# model card leads with it.
QWEN21_T2I_SCHEMA = TabSchema(
    model_registry=handlers.QWEN21_T2I,
    key=Key.QWEN21_T2I, handler=generate_qwen21_t2i,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="qwen21t2i",
    icon="🌄",
    blurb="Detailed 2K images from a prompt, with legible text in them.",
    category="generate", route="/generate/qwen21",
    submit_label="Generate", preset_tab=presets.TAB_QWEN21_T2I,
    preset_note=GEN_PRESET_NOTE,
    groups=(G_PROMPT, G_CORE, G_SAMPLER, G_SEED, G_SAVE_PRESET),
    fields=(
        Field("prompt", "Prompt", "textarea",
              "A neon shop sign that reads \"OPEN ALL NIGHT\" above a "
              "noodle bar, rainy night, reflections on wet pavement, "
              "35 mm photo",
              lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea", "", lines=3,
              group="prompt", collapsed=True, hint=_CFG_NOTE),
        Field("output_size", "Output size", "select",
              QWEN21_T2I_DEFAULT_SIZE, choices=list(QWEN21_T2I_SIZES),
              group="core", preset="output_size", wide=True),
        _model_field(handlers.QWEN21_T2I),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.QWEN21_T2I, "steps"), lo=1, hi=60,
              step=1, group="sampler", preset="steps"),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.QWEN21_T2I, "cfg"),
              lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE),
        Field("sampler", "Sampler", "select", QWEN21_SAMPLERS[0],
              choices=QWEN21_SAMPLERS, group="sampler", preset="sampler"),
        Field("scheduler", "Scheduler", "select", QWEN21_SCHEDULERS[0],
              choices=QWEN21_SCHEDULERS, group="sampler",
              preset="scheduler"),
        *_seed_fields(),
        _batch_field(),
        *_preset_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_triple_tail(handlers.QWEN21_T2I,
                                  _blank_slots(QWEN21_LORA_SLOTS),
                                  "LoRA stack — model + CLIP")),
    ),
)
