"""Krea 2 Edit V2 — the 🔷 Krea2 V2 Edit tab on the V2 edit LoRAs.

Only what this tab does differently. The graph is the krea2_v2_edit
builder's, and so is everything it imports from the V2 tab — the Wan 2.1
VAE, the sampler and variance defaults, the LoRA rows.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py).
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# conradlocke's Krea 2 Identity Edit V2 ships as two LoRAs, from the same
# repo as v1.2: Identity Edit V2 restages the person in one photo (a new
# pose, scene, outfit or light) and General Edit V2 makes plain-language
# edits (remove, recolour, relight, swap the background). Both need
# comfyui-krea2edit 2.0.0 or later, which is what scripts/PINS.json holds.
#
# Licence: unlike v1.2, which is under the Krea 2 Community License, the V2
# LoRAs are free for non-commercial use only (LICENSE_V2.md in the repo).
# So the feature is on no plan, and the files are never mirrored, because
# the mirror repos are public. They download from the upstream repo at the
# revision PINS.json records.
#
# Off by default (feature key "krea_edit_v2"); it needs the V2 downloads
# (~17 GB), the two edit LoRAs (~3.6 GB) and both sets of node packs.
EDIT_V2_LORA_REPO = "conradlocke/krea2-identity-edit"
EDIT_V2_IDENTITY_FILE = "krea2_identity_edit_v2.safetensors"  # ~1.79 GB
EDIT_V2_GENERAL_FILE = "krea2_general_edit_v2.safetensors"    # ~1.79 GB
EDIT_V2_FILES = (EDIT_V2_IDENTITY_FILE, EDIT_V2_GENERAL_FILE)

# The Edit LoRA dropdown: label -> the model-only (file, strength) chain.
# One LoRA per task, at 1.0 as trained. Both at 1.0 breaks the image; the
# author's number for combining them is about 0.6 each.
EDIT_V2_MODES = {
    "🧑 Identity — restage the person (pose, scene, outfit, light)":
        ((EDIT_V2_IDENTITY_FILE, 1.0),),
    "✂️ General — remove, recolour, relight, swap the background":
        ((EDIT_V2_GENERAL_FILE, 1.0),),
    "🧑✂️ Both at 0.6":
        ((EDIT_V2_IDENTITY_FILE, 0.6), (EDIT_V2_GENERAL_FILE, 0.6)),
}
EDIT_V2_DEFAULT_MODE = next(iter(EDIT_V2_MODES))

# Krea2EditModelPatch's ref_position / ref_position_a options, verbatim
# from comfyui-krea2edit 2.0.0. "aligned" pins the reference onto the
# output layout, as the LoRAs were trained; "beside" puts it next to the
# canvas, so the identity still flows but the person can be restaged
# freely, which is the one for a pose change. "bag of features" is the
# author's experimental mode.
EDIT_V2_REF_POSITIONS = ["aligned (trained)", "beside (right of target)",
                         "bag of features"]
# The author's single-reference restage runs "beside" or "aligned"; beside
# is the default because a new pose is what this tab is for. A second
# reference is the scene, which stays aligned (scene aligned + person
# beside is the author's recipe for compositing).
EDIT_V2_DEFAULT_REF_POSITION = EDIT_V2_REF_POSITIONS[1]
EDIT_V2_DEFAULT_REF_POSITION_A = EDIT_V2_REF_POSITIONS[0]

# The author's V2 settings: ref_boost 2 for Identity (General's is 4) and
# grounding 768. fit_mode stays at the builder's "fit"; the legacy crop
# was for v1/v1.1 weights.
EDIT_V2_DEFAULT_REF_BOOST = 2.0
