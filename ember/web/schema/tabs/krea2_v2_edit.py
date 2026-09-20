"""The Krea2 V2 Edit tab."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.krea2_v2.constants import V2_DEFAULT_NEGATIVE
from ember.pipelines.krea2_v2_edit.constants import V2_EDIT_FIT_MODES
from ember.pipelines.krea2_v2_edit.handler import generate_v2_edit
from ember.web.schema.fields import (
    EDIT_PRESET_NOTE,
    G_CORE,
    G_INPUTS,
    G_PROMPT,
    G_REFERENCE,
    G_SAMPLER,
    G_SEED,
    G_VARIANCE,
    IMAGE_KEYS,
    _CFG_NOTE,
    _batch_field,
    _model_field,
    _reference_fields,
    _seed_fields,
    _stack_slots,
    _triple_tail,
    _two_image_fields,
    _v2_sampler_fields,
    _v2_variance_fields,
)
from ember.web.schema.model import Field, TabSchema

Key = features.Key


KREA2_V2_EDIT_SCHEMA = TabSchema(
    model_registry=handlers.KREA_V2_EDIT,
    key=Key.KREA_V2_EDIT, handler=generate_v2_edit,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="v2edit",
    icon="🔷", blurb="Instruction editing on the V2 pipeline.",
    category="edit", route="/edit/krea2-v2-edit", submit_label="Edit",
    preset_tab=presets.TAB_KREA2_V2,
    preset_note=EDIT_PRESET_NOTE.format("🔶 Krea2 V2"),
    groups=(G_INPUTS, G_PROMPT, G_CORE, G_REFERENCE, G_SAMPLER,
            G_VARIANCE, G_SEED),
    fields=(
        *_two_image_fields("Source image (paste with Ctrl+V)",
                           "➕ Add a second reference (subject)",
                           "Second reference — subject"),
        Field("prompt", "Edit instruction", "textarea", "", lines=5,
              group="prompt"),
        Field("negative", "Negative prompt", "textarea",
              V2_DEFAULT_NEGATIVE, lines=3, group="prompt",
              collapsed=True, hint=_CFG_NOTE),
        *_seed_fields(),
        _model_field(handlers.KREA_V2_EDIT),
        *_reference_fields(),
        Field("fit_mode",
              "Reference geometry (fit = v1.2; the legacy crop is for "
              "older weights)",
              "select", V2_EDIT_FIT_MODES[0], choices=V2_EDIT_FIT_MODES,
              group="reference", wide=True),
        # No Denoise: the source reaches the model through conditioning
        # rather than the starting latent, so the builder pins it at 1.0.
        *_v2_sampler_fields(handlers.KREA_V2_EDIT, denoise=False),
        *_v2_variance_fields(),
        _batch_field(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_triple_tail(
                  handlers.KREA_V2_EDIT,
                  _stack_slots(handlers.KREA_V2_EDIT),
                  "LoRA stack — model + CLIP")),
    ),
)
