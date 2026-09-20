# Number of LoRA slots the Krea2, Krea2 Edit and MiniMax tabs render, all
# blank. The UI rows, the handlers and the workflow chain are all driven
# from this, so changing it here is the whole change. (The V2 tabs have
# one row per LoRA in their feature's list instead — see
# ember.pipelines.krea2_v2.workflow.default_lora_slots.)
MAX_LORA_SLOTS = 8
# Slots past this stay in a collapsed accordion so a tall stack does not
# eat the whole column. Set it >= MAX_LORA_SLOTS to show every slot.
VISIBLE_LORA_SLOTS = 3


def lora_choices(feature) -> list:
    """A LoRA dropdown's values: "None" and the feature's LoRA ids.

    Exactly the feature's list. Files on this disk that the catalogue does
    not list are not offered, and a listed LoRA whose file has not
    downloaded is — it is labelled as missing (lora_labels) and skipped,
    with a warning, if a run switches it on.
    """
    return [catalog.NONE] + [lora.id for lora in catalog.feature_loras(feature)]


def lora_labels(feature) -> dict:
    """{lora id: name} for the LoRA dropdowns, "None" included."""
    labels = {catalog.NONE: catalog.NONE}
    for lora in catalog.feature_loras(feature):
        labels[lora.id] = (lora.name if lora_file_available(lora)
                           else f"{lora.name} (not downloaded)")
    return labels


def stored_lora(value):
    """A LoRA slot's form value as the settings blob stores it.

    The form spells an empty slot "None" (a select needs a string); the
    blob stores null, so a preset never carries a magic string the licence
    server would have to know. tabschema.settings() calls this too, which
    is what keeps the two blobs byte-identical.
    """
    return None if value in (None, catalog.NONE) else value


def _resolve_lora_slots(feature, slots) -> tuple[list, list]:
    """Flat (enabled, lora id, weight) × N UI values → (file, strength) pairs.

    Every Krea tab's stack, the V2 ones included: a row contributes only
    while its checkbox is on, and order is preserved because LoRA
    application is not commutative. Switching a row off keeps its id in
    the dropdown instead of throwing it away, which is the whole reason
    the column exists.

    Values are exact ids from the feature's list, and this is the one
    place an id becomes a file name. A ticked row whose id the feature
    does not offer, or whose file has not downloaded, is skipped rather
    than failing the run — logged, and returned as the second value so the
    handler can say so in its status line. Slots left at "None" drop out
    even when ticked.

    `slots` is the handler's whole varargs tail, so the slot count lives
    only in the schema and adding a row needs no change here.
    """
    loras, skipped = [], []
    for enabled, lora_id, weight in zip(slots[::3], slots[1::3], slots[2::3]):
        if not enabled or lora_id in (None, "", catalog.NONE):
            continue
        lora = feature_lora(feature, lora_id)
        if lora is None:
            log.warning("LoRA %r is not offered on %s — skipping it",
                        lora_id, feature)
            skipped.append(f"`{lora_id}` (not offered on this tab)")
            continue
        if not lora_file_available(lora):
            log.warning("LoRA %s (%s) has not downloaded — skipping it",
                        lora.id, lora.file)
            skipped.append(f"`{lora.name}` (not downloaded)")
            continue
        loras.append((lora.file, float(weight)))
    return loras, skipped


def _skipped_note(skipped) -> str:
    """A status-line prefix naming the LoRAs a run had to leave out."""
    if not skipped:
        return ""
    return "⚠️ Skipped LoRA: " + ", ".join(skipped) + "\n"


def _enabled_lora_ids(slots) -> list:
    """The ids of the rows a run switched on — for the V2 status notes."""
    return [lora_id for enabled, lora_id in zip(slots[::3], slots[1::3])
            if enabled and lora_id not in (None, "", catalog.NONE)]
