"""Krea 2 V2 — the Krea2 advanced turbo/raw pipeline, as its own tab.

Facts about the model and its workflow, and nothing read from the
environment (that is ember/settings.py). Which models and LoRAs the tab
offers is its feature's list in the licence server's catalogue — see
licensing/catalog.py.

A data module on purpose: nothing is imported, so the Docker bake stage
and weights/downloads.py can read it without a GPU or a licence.
"""

# ── The pipeline ──────────────────────────────────────────────────────────────
# A second, self-contained text-to-image pipeline: the Krea2 advanced
# "KREA 2 TURBO/RAW" workflow, reproduced node-for-node in its own tab. It
# shares the text encoder with the Krea 2 tabs, and today the same catalogue
# model (the mxfp8 turbo UNet) — but its own VAE, its own sampler and its
# own LoRA rows, so tuning one never moves the other. Which models and
# LoRAs it offers is its feature's list in the catalogue (catalog.py).
#
# Three things make it different from the Single tab:
#   • RES4LYF's ClownsharKSampler_Beta replaces KSampler (eta/bongmath and
#     the bong_tangent scheduler have no core equivalent),
#   • an RBG Smart Seed Variance node sits between the positive prompt and
#     the sampler, perturbing the conditioning per seed,
#   • LoRAs apply to the model *and* the CLIP (the source workflow uses
#     rgthree's Power Lora Loader in "Single Strength" mode), unlike the
#     LoraLoaderModelOnly chain the other Krea tabs build.
# On by default (feature key "krea_v2_t2i", ~17 GB); a license that does not grant
# "krea_v2_t2i" skips the downloads, the three node packs and the tab.

# The workflow's companion guide recommends the Wan 2.1 VAE over the stock
# Qwen image VAE for this pipeline. Different repo, and it is stored under
# vae/wan/ upstream, so it is fetched explicitly rather than through the
# HF_MODEL_FILES list.
V2_VAE_FILE = "wan21-vae.safetensors"                            # ~254 MB
V2_VAE_HF_REPO = "wangkanai/wan21-vae"
V2_VAE_HF_PATH = f"vae/wan/{V2_VAE_FILE}"

# Custom node packs this tab needs. Cloned at bootstrap like Krea2Edit;
# each one is optional in the sense that a failed clone disables only this
# tab. RES4LYF and RBG change the image and have no core equivalent;
# post-processing supplies FilmGrain for the optional grain toggle.
V2_NODE_REPOS = [
    # (custom_nodes dir, git URL, a node class that proves it loaded)
    ("RES4LYF", "https://github.com/ClownsharkBatwing/RES4LYF",
     "ClownsharKSampler_Beta"),
    ("ComfyUI-RBG-SmartSeedVariance",
     "https://github.com/RamonGuthrie/ComfyUI-RBG-SmartSeedVariance",
     "RBG_Smart_Seed_Variance"),
    ("ComfyUI-post-processing-nodes",
     "https://github.com/EllangoK/ComfyUI-post-processing-nodes",
     "FilmGrain"),
]

# ClownsharKSampler_Beta settings, straight from the workflow. These are
# the knobs both variants share; steps and cfg are deliberately absent
# because they belong to the model record (catalog.Model.steps / .cfg)
# and would otherwise be a second source of truth for the same two
# numbers.
V2_SAMPLER_DEFAULTS = {
    "eta": 0.5,
    "sampler_name": "linear/euler",
    "scheduler": "bong_tangent",
    "steps_to_run": -1,
    "denoise": 1.0,
    "sampler_mode": "standard",
    "bongmath": True,
}
# RES4LYF builds these lists at import time from its own tables, so they
# cannot be enumerated here. The dropdowns accept free text — these are the
# values the workflow ships with plus the node's own defaults.
V2_SAMPLER_NAMES = ["linear/euler", "res_2m", "res_3m", "deis_2m", "euler"]
V2_SCHEDULERS = ["bong_tangent", "beta57", "normal", "karras", "simple"]
V2_SAMPLER_MODES = ["standard", "unsample", "resample"]

# RBG Smart Seed Variance settings, straight from the workflow. The combo
# strings carry emoji and must match the node's option lists character for
# character, or ComfyUI rejects the prompt.
V2_VARIANCE_DEFAULTS = {
    "variance_preset": "🌱 Subtle",
    "fine_tune_variance": 50,
    "model_type": "📸 Krea2 (SingleStream)",
    "fade_curve": "Instant",
    "noise_injection": "Beginning Steps",
    "protect_mode": "🚫 None",
    "protect_regions": "",
    "direction_shift": "🚫 None",
    "shift_strength": 100,
    "variance_schedule": "constant",
    "cutoff_step": 8,
    "total_steps": 20,
    "cutoff_strength": 0.0,
    "vibe_blend": 0.5,
}
V2_VARIANCE_PRESETS = ["❌ Disabled", "🌱 Subtle", "🌿 Balanced", "🪴 Creative",
                       "🌳 Bold", "🌴 Wild", "⚙️ Custom"]
V2_VARIANCE_MODEL_TYPES = [
    "⚡ Z-Image Turbo", "📸 Krea2 (SingleStream)", "🖼️ Qwen-Image",
    "🔮 Flux (Dev/Schnell)", "🎨 Chroma HD", "🧧 ERNIE-Image",
    "🔮 Ideogram 4.0", "🖌️ SDXL", "🎬 Wan2.2", "⚙️ Other",
]
V2_VARIANCE_SCHEDULES = ["constant", "decreasing", "step_cutoff",
                         "tiered_release", "hard_lock"]

# Post-processing. Both nodes are bypassed (mode 4) in the source workflow,
# so both toggles start off and the tab reproduces it exactly as shipped.
V2_SHARPEN_DEFAULTS = {"sharpen_radius": 1, "sigma": 0.35, "alpha": 1.0}
V2_FILMGRAIN_DEFAULTS = {"intensity": 0.05, "scale": 1.0, "temperature": 0.0,
                         "vignette": 0.0}

# The workflow sizes its latent with a ResolutionSelector feeding
# EmptyLatentImage. That is pure integer plumbing, so the same arithmetic
# runs in Python here (megapixels × 1024², the core ComfyUI convention) and
# the tab shows the width/height it resolved to. 3:4 at 1.5 MP snapped to
# /8 gives the workflow's 1088×1448.
V2_ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "3:4 (Portrait Standard)": (3, 4),
    "2:3 (Portrait)": (2, 3),
    "9:16 (Portrait Tall)": (9, 16),
    "4:3 (Landscape Standard)": (4, 3),
    "3:2 (Landscape)": (3, 2),
    "16:9 (Landscape Wide)": (16, 9),
}
V2_DEFAULT_ASPECT = "3:4 (Portrait Standard)"
V2_DEFAULT_MEGAPIXELS = 1.5
V2_DEFAULT_MULTIPLE = 8

# The workflow's Negatives node, verbatim.
V2_DEFAULT_NEGATIVE = (
    "This low quality greyscale unfinished sketch is inaccurate and flawed. "
    "The image is very blurred and lacks detail with excessive chromatic "
    "aberrations and artifacts. The image is overly saturated with excessive "
    "bloom. It has a toony aesthetic with bold outlines and flat colors.\n\n"
    "Fake, unreal, wrong anatomy, big eyes, bad anatomy, extra limbs, "
    "missing fingers, fused fingers, poorly drawn hands, poorly drawn face, "
    "mutated hands, long neck, extra arms, extra legs, extra fingers, "
    "disfigured, malformed limbs, missing limbs, skinny, fat, jpeg artifacts, "
    "watermark, signature, text, logo, exaggerated features, unnatural skin "
    "tone, bokeh, deformed, lowres, out of frame, aliasing, blurry "
    "background, doll-like skin, plastic texture, uncanny valley, incorrect "
    "proportions, duplicate body parts, unnatural poses, poor anatomical "
    "proportions, pubic hair, digital art, drawing, cartoon, large breasts, "
    "huge breasts, faded colors, pastel tones, beigesthetic, flat lighting, "
    "overexposed whites, AI glow, plastic skin, skincare-ad look, studio "
    "lighting, cartoon or 3D style, text watermark or logos, cleft chin, "
    "indented chin, smooth groin, Barbie-doll anatomy, blurred genitalia, "
    "aged, wrinkles, massive breasts, 28G, airbrushed, plastic skin, clothes, "
    "bra, underwear, iPhone, smartphone, wide angle distortion, tattoos, "
    "piercings, excessive cum."
)

