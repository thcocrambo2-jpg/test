"""The 🪄 Qwen Edit 2511 tab."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.qwen_edit.constants import (
    QWEN_EDIT_DEFAULT_SPEED,
    QWEN_EDIT_MAX_IMAGES,
    QWEN_EDIT_NORMAL,
    QWEN_EDIT_SAMPLERS,
    QWEN_EDIT_SCHEDULERS,
    QWEN_EDIT_SPEEDS,
)
from ember.pipelines.qwen_edit.handler import generate_qwen_edit
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


# 🧩 Qwen 2.1 Reference's form, for an edit model: the numbered images in
# the right column above the result (stack-first), then Prompt, Output with
# Speed and the model, a Sampler whose Steps and CFG show only for Normal —
# Lightning runs its own — presets with no prompt library, and the plain
# model-only LoRA stack over the feature's own list. No output size: the result takes
# image 1's shape, as the template does.
QWEN_EDIT_SCHEMA = TabSchema(
    model_registry=handlers.QWEN_EDIT_2511,
    key=Key.QWEN_EDIT_2511, handler=generate_qwen_edit,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="qwenedit",
    icon="🪄",
    blurb="Edit a photo by instruction — a new pose, angle, outfit or "
          "scene — with up to 3 images.",
    category="edit", route="/edit/qwen-edit-2511",
    submit_label="Edit", preset_tab=presets.TAB_QWEN_EDIT,
    preset_note=GEN_PRESET_NOTE,
    groups=(Group("references", "Images", column="right",
                  stack_first=True),
            G_PROMPT, G_CORE, G_SAMPLER, G_SEED, G_SAVE_PRESET),
    fields=(
        Field("images", "Images (paste with Ctrl+V)",
              "images", (), lo=1, hi=QWEN_EDIT_MAX_IMAGES,
              group="references", column="right",
              hint="Image 1 is the one edited. Name the others by number: "
                   "“put the woman in image 1 in the pose from image 2”.",
              empty_note="Add the image to edit.", library="poses"),
        Field("prompt", "Edit instruction", "textarea",
              "Turn the woman in image 1 around so her back is to the "
              "camera, looking back at the camera over her right shoulder. "
              "Keep her face, hair, outfit and the background the same.",
              lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea",
              _model_negative(handlers.QWEN_EDIT_2511, ""), lines=3,
              group="prompt", collapsed=True, hint=_CFG_NOTE),
        Field("speed", "Speed", "select", QWEN_EDIT_DEFAULT_SPEED,
              choices=list(QWEN_EDIT_SPEEDS), group="core", preset="speed",
              wide=True,
              hint="Lightning runs 4 or 8 steps at CFG 1. Normal is slower "
                   "and follows the prompt more closely."),
        _model_field(handlers.QWEN_EDIT_2511),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.QWEN_EDIT_2511, "steps"), lo=1, hi=80,
              step=1, group="sampler", preset="steps",
              show_if=("speed", QWEN_EDIT_NORMAL)),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.QWEN_EDIT_2511, "cfg"),
              lo=1.0, hi=10.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE, show_if=("speed", QWEN_EDIT_NORMAL)),
        Field("sampler", "Sampler", "select", QWEN_EDIT_SAMPLERS[0],
              choices=QWEN_EDIT_SAMPLERS, group="sampler", preset="sampler"),
        Field("scheduler", "Scheduler", "select", QWEN_EDIT_SCHEDULERS[0],
              choices=QWEN_EDIT_SCHEDULERS, group="sampler",
              preset="scheduler"),
        *_seed_fields(),
        _batch_field(),
        *_preset_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.QWEN_EDIT_2511)),
    ),
)
