"""What every pipeline reads out of the catalogue, and off the disk.

Ids are what forms, presets and prompts carry; a file name appears only
once a graph is being built. These turn one into the other, and they are
here rather than in a family's builder because the Krea and MiniMax
catalogue paths both go through them — all four Krea tabs and the two
MiniMax tabs.

The catalogue is per feature, so every helper that reads it takes the
feature key: each tab offers exactly its own feature's lists.
"""

from ember.licensing import catalog
from ember.logs import log
from ember.pipelines.krea2.constants import (
    ABLITERATED_ENCODER_FILE,
    EDIT_LORA_FILE,
    TEXT_ENCODER_FILE,
)
from ember.settings import MODELS_DIR


def resolve_model(feature, model_id) -> "catalog.Model | None":
    """The feature's model record for a form value (an id).

    Exact ids only: the value came from a dropdown this pod described, or
    from a preset the licence server checked against the same ids. An id
    the feature does not list — a model switched off since the preset was
    saved — falls back to the feature's first model, loudly, rather than
    refusing the run. None only when the feature lists no model at all.
    """
    models = catalog.feature_models(feature)
    if not models:
        return None
    for model in models:
        if model.id == model_id:
            return model
    if model_id not in (None, "", catalog.NONE):
        log.warning("Model %r is not offered on %s — using the default (%s)",
                    model_id, feature, models[0].id)
    return models[0]


def model_defaults(model) -> tuple[int, float]:
    """(steps, cfg) for a model record — the record carries them now."""
    return int(model.steps), float(model.cfg)


def model_file_available(model) -> bool:
    """True once the model record's UNet file has been downloaded."""
    return (MODELS_DIR / "diffusion_models" / model.file).exists()


def lora_file_available(lora) -> bool:
    """True once the LoRA record's file has been downloaded."""
    return (MODELS_DIR / "loras" / lora.file).exists()


def feature_lora(feature, lora_id) -> "catalog.Lora | None":
    """The LoRA record for a form value, if the feature offers that id.

    A tab's stack offers exactly its feature's list, so an id outside it —
    one from a preset saved on another tab, or a LoRA switched off since —
    is not this tab's to load, even when the catalogue knows it.
    """
    if lora_id in (None, "", catalog.NONE):
        return None
    return next((lora for lora in catalog.feature_loras(feature)
                 if lora.id == lora_id), None)


def edit_lora_available() -> bool:
    """True once the Identity Edit LoRA has been downloaded."""
    return (MODELS_DIR / "loras" / EDIT_LORA_FILE).exists()


def active_text_encoder() -> str:
    """Prefer the merged abliterated encoder, fall back to the standard one."""
    if (MODELS_DIR / "text_encoders" / ABLITERATED_ENCODER_FILE).exists():
        return ABLITERATED_ENCODER_FILE
    return TEXT_ENCODER_FILE
