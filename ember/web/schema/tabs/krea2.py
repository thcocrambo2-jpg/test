"""The Krea 2 pipeline's two tabs: 🎨 Krea2 and ✨ Krea2 Edit."""


KREA2_SCHEMA = TabSchema(
    model_registry=handlers.KREA_T2I,
    key=Key.KREA_T2I, handler=handlers.generate_single,
    lane=handlers.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="krea2",
    icon="🎨", blurb="Type a sentence, get a photograph.",
    category="generate", route="/generate/krea2",
    submit_label="Generate", preset_tab=presets.TAB_KREA2,
    preset_note=GEN_PRESET_NOTE,
    groups=(G_PROMPT, G_CORE, G_SAMPLER, G_SEED, G_SAVE),
    fields=(
        Field("prompt", "Prompt", "textarea",
              "A photorealistic golden-hour portrait, natural skin "
              "texture, shallow depth of field", lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea", "", lines=3,
              group="prompt", collapsed=True, hint=_CFG_NOTE),
        *_seed_fields(),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.KREA_T2I, "steps"), lo=1, hi=60,
              step=1, group="sampler", preset="steps"),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.KREA_T2I, "cfg"),
              lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE),
        Field("resolution", "Resolution", "select", DEFAULT_RESOLUTION,
              choices=list(RESOLUTION_PRESETS), group="core",
              preset="resolution", wide=True),
        Field("sampler", "Sampler", "select", SAMPLERS[0],
              choices=SAMPLERS, group="sampler", preset="sampler",
              wide=True),
        _model_field(handlers.KREA_T2I),
        _batch_field(),
        *_save_fields(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.KREA_T2I)),
    ),
)

KREA2_EDIT_SCHEMA = TabSchema(
    model_registry=handlers.KREA_EDIT,
    key=Key.KREA_EDIT, handler=handlers.generate_edit,
    lane=handlers.COMFY_LANE, prompt_field="prompt",
    result_keys=IMAGE_KEYS, tab_id="edit",
    icon="✨", blurb="Change one thing about a picture without touching "
                    "the rest.",
    category="edit", route="/edit/krea2-edit", submit_label="Edit",
    preset_tab=presets.TAB_KREA2, preset_note=EDIT_PRESET_NOTE.format("🎨 Krea2"),
    groups=(G_INPUTS, G_PROMPT, G_CORE, G_REFERENCE, G_SAMPLER,
            G_SEED),
    fields=(
        *_two_image_fields("Source image (paste with Ctrl+V)",
                           "➕ Add a second reference (subject)",
                           "Second reference — subject"),
        Field("prompt", "Edit instruction", "textarea",
              "Remove all her clothes completely, make her fully nude. "
              "Keep the exact same face, facial features, expression, "
              "skin tone, hairstyle, body pose, hands position, and "
              "background. Do not change the face at all.          "
              "remove clothes exposing her naked average natural shaped "
              "tits. dont change her face",
              lines=5, group="prompt"),
        Field("negative", "Negative prompt", "textarea", "", lines=3,
              group="prompt", collapsed=True, hint=_CFG_NOTE),
        *_seed_fields(),
        Field("steps", "Steps", "slider",
              _model_setting(handlers.KREA_EDIT, "steps"), lo=1, hi=60,
              step=1, group="sampler", preset="steps"),
        Field("cfg", "CFG", "slider",
              _model_setting(handlers.KREA_EDIT, "cfg"),
              lo=0.5, hi=8.0, step=0.1, group="sampler", preset="cfg",
              hint=_CFG_NOTE),
        Field("sampler", "Sampler", "select", SAMPLERS[0],
              choices=SAMPLERS, group="sampler", preset="sampler",
              wide=True),
        *_reference_fields(),
        _model_field(handlers.KREA_EDIT),
        _batch_field(),
        Field("lora_slots", "LoRA stack", "repeat",
              repeat=_blank_lora_tail(handlers.KREA_EDIT)),
    ),
)
