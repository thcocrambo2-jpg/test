"""The Krea2 Edit V2 tab."""

from ember import features
from ember.generation import handlers
from ember.generation import runner
from ember.licensing import presets
from ember.pipelines.krea2_edit_v2.constants import (
    EDIT_V2_DEFAULT_MODE,
    EDIT_V2_DEFAULT_REF_BOOST,
    EDIT_V2_DEFAULT_REF_POSITION,
    EDIT_V2_DEFAULT_REF_POSITION_A,
    EDIT_V2_MODES,
    EDIT_V2_REF_POSITIONS,
)
from ember.pipelines.krea2_edit_v2.handler import generate_edit_v2
from ember.pipelines.krea2_v2.constants import V2_DEFAULT_NEGATIVE
from ember.web.schema.fields import (
    EDIT_PRESET_NOTE,
    G_CORE,
    G_INPUTS,
    G_PROMPT,
    G_REFERENCE,
    G_REFINE,
    G_SAMPLER,
    G_SEED,
    G_VARIANCE,
    IMAGE_KEYS,
    _CFG_NOTE,
    _batch_field,
    _model_field,
    _model_negative,
    _reference_fields,
    _refine_fields,
    _seed_fields,
    _stack_slots,
    _triple_tail,
    _two_image_fields,
    _v2_sampler_fields,
    _v2_variance_fields,
)
from ember.web.schema.model import Field, TabSchema

Key = features.Key


# The 🔷 Krea2 V2 Edit form with the Edit LoRA choice and reference
# placement in place of the geometry dropdown, over this feature's own
# model and LoRA lists. Presets are the 🔶 Krea2 V2 tab's, as the 🔷 tab's
# are.
KREA2_EDIT_V2_SCHEMA = TabSchema(
    model_registry=handlers.KREA_EDIT_V2,
    key=Key.KREA_EDIT_V2, handler=generate_edit_v2,
    lane=runner.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="editv2",
    icon="💠", blurb="Restage a person, or edit by instruction, with the "
                     "Edit V2 LoRAs.",
    category="edit", route="/edit/krea2-edit-v2", submit_label="Edit",
    preset_tab=presets.TAB_KREA2_V2,
    preset_note=EDIT_PRESET_NOTE.format("🔶 Krea2 V2"),
    groups=(G_INPUTS, G_PROMPT, G_CORE, G_REFERENCE, G_SAMPLER,
            G_VARIANCE, G_REFINE, G_SEED),
    fields=(
        *_two_image_fields("Source image (paste with Ctrl+V)",
                           "➕ Add a second reference (subject)",
                           "Second reference — subject"),
        Field("prompt", "Edit instruction", "textarea", "", lines=5,
              group="prompt"),
        Field("negative", "Negative prompt", "textarea",
              _model_negative(handlers.KREA_EDIT_V2, V2_DEFAULT_NEGATIVE),
              lines=3, group="prompt",
              collapsed=True, hint=_CFG_NOTE),
        *_seed_fields(),
        _model_field(handlers.KREA_EDIT_V2),
        Field("edit_mode", "Edit LoRA", "select", EDIT_V2_DEFAULT_MODE,
              choices=list(EDIT_V2_MODES), group="core", wide=True),
        Field("ref_position",
              "Person placement (beside = free to restage, aligned = keep "
              "the photo's layout)",
              "select", EDIT_V2_DEFAULT_REF_POSITION,
              choices=EDIT_V2_REF_POSITIONS, group="reference", wide=True),
        Field("ref_position_a", "Scene placement (second reference only)",
              "select", EDIT_V2_DEFAULT_REF_POSITION_A,
              choices=EDIT_V2_REF_POSITIONS, group="reference", wide=True,
              show_if=("use_image2", True)),
        *_reference_fields(ref_boost=EDIT_V2_DEFAULT_REF_BOOST),
        # No Denoise: the source reaches the model through conditioning
        # rather than the starting latent, so the builder pins it at 1.0.
        *_v2_sampler_fields(handlers.KREA_EDIT_V2, denoise=False),
        *_v2_variance_fields(),
        _batch_field(),
        *_refine_fields(edit=True),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_triple_tail(
                  handlers.KREA_EDIT_V2,
                  _stack_slots(handlers.KREA_EDIT_V2),
                  "LoRA stack — model + CLIP")),
    ),
)
