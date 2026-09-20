"""Krea 2 V2 Edit — instruction editing on the V2 spine.

Only what this tab does differently. Everything else it uses — the Wan 2.1
VAE, the sampler and variance defaults, the LoRA rows — is the V2 tab's,
imported from pipelines/krea2_v2/constants.py by the builder, so tuning
the V2 tab tunes this one too and there is no second copy to drift.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py).
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# The ✨ Krea2 Edit tab's recipe — the Identity Edit LoRA plus the
# ComfyUI-Krea2Edit nodes — rebuilt on the V2 pipeline instead of the
# Krea 2 v1 one: its feature's catalogue models, the Wan 2.1 VAE, the
# model+CLIP LoRA rows, ClownsharKSampler_Beta and Smart Seed Variance.
# Everything the V2 tab defines is reused verbatim, so tuning the V2 tab
# tunes this one too and there is no second copy of those numbers to drift.
#
# The VAE swap is safe here specifically because the two are the same
# family: Qwen-Image's VAE is a Wan 2.1 derivative with the same 16-channel
# latent space, so the source latents Krea2EditModelPatch prepends as
# in-context tokens still mean what the Identity Edit LoRA was trained to
# read. A VAE from any other family would not be substitutable this way.
#
# Off by default (feature key "krea_v2_edit"); it needs the V2 downloads
# (~17 GB), the Identity Edit LoRA (~1.9 GB) and both sets of node packs.

# The Identity Edit LoRA bleeds and duplicates content above ~2 MP, and the
# cap applies to the source as much as the output — this tab derives one
# from the other, and VAE-encoding a 12 MP phone photo as a reference is a
# needless 12 MP of VRAM.
V2_EDIT_MAX_PIXELS = 2_000_000

# 384-768 is the LoRA's trained grounding range; above it the model starts
# emitting duplicated "double picture" compositions. ref_boost is how hard
# the edit holds the reference: 1.0 neutral, ~4 strong likeness, past ~10
# removals stop working. Same numbers the v1 Edit tab ships.
V2_EDIT_DEFAULT_GROUNDING = 768
V2_EDIT_DEFAULT_REF_BOOST = 4.0

# Krea2EditModelPatch's reference geometry. "fit" is the v1.2 behaviour
# (resample the reference in pixel space, so a source whose aspect differs
# from the output is fitted rather than stretched); the legacy value is
# kept selectable for anyone running older weights.
V2_EDIT_FIT_MODES = ["fit", "crop (legacy)"]

