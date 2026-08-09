"""Krea 2 V2 Edit workflow builder — instruction editing on the V2 spine.

The ✨ Krea2 Edit tab is `build_edit_workflow` in workflow.py: the Krea 2
v1 stack (fp8 UNet, Qwen image VAE, model-only LoRA chain, plain
`KSampler`) with the Identity Edit LoRA and the two ComfyUI-Krea2Edit
nodes bolted on. This module is that same edit recipe grafted onto the
Krea 2 V2 pipeline instead — exactly the relationship the 🔶 Krea2 V2 tab
has to the 🎨 Krea2 one.

Everything V2 about it is imported from workflow_krea2_v2 rather than
restated, so the two tabs cannot drift: the same `V2_MODELS` registry and
variant defaults, the same Wan 2.1 VAE, the same 11-slot model+CLIP LoRA
stack, the same `ClownsharKSampler_Beta` settings and the same
`RBG_Smart_Seed_Variance` node. What is new here is only the edit half.

Three things are deliberately *not* carried over from build_v2_workflow:

  • **Resolution.** V2 sizes its latent from an aspect ratio and a
    megapixel budget; an edit's output size comes from the source image
    (see `fit_size`), because that is the one thing the user did not have
    to choose.
  • **Denoise.** Pinned at 1.0 and not exposed. The source enters through
    conditioning and the patched model, not through the starting latent,
    so a lower denoise would only leave the empty latent half-noised.
  • **ImageSharpen / FilmGrain.** The V2 tab carries them because its
    source graph does (bypassed); there is no such graph here, so
    `VAEDecode` goes straight to `SaveImage`.

The Identity Edit LoRA stays **model-only at 1.0**, as trained, while the
V2 stack above it applies to model *and* CLIP. That mismatch is
intentional: the V2 strengths come from a Power Lora Loader in "Single
Strength" mode, and the edit LoRA's from its own training recipe. Mixing
`LoraLoaderModelOnly` and `LoraLoader` in one chain is fine — each node
passes through whichever input it does not touch.
"""

from comfy import GPU_COUNT
from config import (
    EDIT_LORA_FILE,
    V2_EDIT_MAX_PIXELS,
    V2_MODELS,
    V2_NODE_REPOS,
    V2_SAMPLER_DEFAULTS,
    V2_VAE_FILE,
    V2_VARIANCE_DEFAULTS,
    log,
)
from workflow import active_text_encoder, edit_lora_available
from workflow_krea2_v2 import (
    REQUIRED_NODES as V2_REQUIRED_NODES,
    default_lora_slots,
    missing_enabled_loras,
    model_available,
    model_defaults,
    model_names,
    resolve_model,
    turbo_lora_available,
    turbo_lora_slot,
    vae_available,
)

# Node classes this tab cannot run without: the V2 sampler and variance
# nodes plus the two Krea2Edit ones. FilmGrain is absent for the same
# reason as in workflow_krea2_v2 — and here the tab does not build it at
# all.
REQUIRED_NODES = (*V2_REQUIRED_NODES,
                  "Krea2EditModelPatch", "Krea2EditGroundedEncode")

# Re-exported so ui.py can import this tab's whole surface from one
# module. They are the V2 registry helpers unchanged — the two tabs share
# a model dropdown, a LoRA stack and a Turbo LoRA slot by design.
__all__ = [
    "REQUIRED_NODES", "build_v2_edit_workflow", "default_lora_slots",
    "fit_size", "model_available", "model_defaults", "model_names",
    "resolve_model", "status", "turbo_lora_available", "turbo_lora_slot",
]


def fit_size(width: int, height: int,
             max_pixels: int = V2_EDIT_MAX_PIXELS) -> tuple[int, int]:
    """Edit-target size: keep aspect, cap at max_pixels, never upscale, /16.

    The same arithmetic the v1 Edit tab uses (ui._fit_edit_size), kept
    here so this module owns its own geometry: the cap is by *area*
    rather than a long-side limit because what the Identity Edit LoRA
    degrades on is total pixels, and it applies to the source as much as
    to the output.
    """
    scale = min(1.0, (max_pixels / max(1, width * height)) ** 0.5)
    return (max(64, int(width * scale) // 16 * 16),
            max(64, int(height * scale) // 16 * 16))


def status() -> tuple[bool, str]:
    """(ready, message) for the tab — what is missing, in plain words.

    The V2 half of this is workflow_krea2_v2.status(); the edit LoRA is
    the extra requirement, and it is fatal rather than a warning because
    without it the graph is just an expensive img2img that ignores the
    instruction. Node packs are not checked here — they register inside
    ComfyUI, which app.py verifies at startup.
    """
    problems = []
    if not any(model_available(entry) for entry in V2_MODELS):
        problems.append("no V2 model has downloaded")
    if not vae_available():
        problems.append(f"the VAE `{V2_VAE_FILE}` has not downloaded")
    if not edit_lora_available():
        problems.append(f"the Identity Edit LoRA `{EDIT_LORA_FILE}` has not "
                        "downloaded")
    if problems:
        # Comma-and rather than the plain " and ".join workflow_krea2_v2
        # uses: this tab has three things to be missing, and "A and B and
        # C" reads as a run-on where two items did not.
        if len(problems) > 2:
            listed = ", ".join(problems[:-1]) + " and " + problems[-1]
        else:
            listed = " and ".join(problems)
        return False, ("❌ Krea 2 V2 Edit cannot run — " + listed
                       + " yet. Restart the app so the download step can "
                         "fetch it.")
    notes = []
    absent = [e["name"] for e in V2_MODELS if not model_available(e)]
    if absent:
        notes.append("these models have not downloaded and the dropdown "
                     "will refuse them: " + ", ".join(f"`{n}`" for n in absent))
    missing = missing_enabled_loras()
    if missing:
        notes.append("these LoRAs from the workflow's stack did not download "
                     "and their slots start off: "
                     + ", ".join(f"`{m}`" for m in missing))
    if notes:
        return True, "⚠️ Ready, but " + "; ".join(notes)
    packs = ", ".join(f"`{d}`" for d, _r, _c in V2_NODE_REPOS)
    return True, (f"✅ Ready. Node packs required: {packs}, "
                  "`comfyui-krea2edit`")


def build_v2_edit_workflow(
    *,
    prompt: str,
    negative: str = "",
    seed: int = 0,
    width: int,
    height: int,
    image_name: str,
    loras=(),
    unet_file: str | None = None,
    grounding_px: int = 768,
    ref_boost: float = 4.0,
    fit_mode: str = "fit",
    sampler_settings=None,
    variance_settings=None,
    variance_seed: int | None = None,
    filename_prefix: str = "Krea2V2Edit",
) -> dict:
    """Build the Krea 2 V2 instruction-edit workflow in ComfyUI API format.

    `image_name` references a file already uploaded to ComfyUI's input
    folder (client.upload_image); `width`/`height` are the target size the
    caller derived from it with `fit_size`. `loras` is a sequence of
    (filename, strength) pairs from the V2 stack, already resolved and
    filtered to the enabled rows — the Identity Edit LoRA is *not* among
    them, because this builder prepends it itself, which is what makes it
    impossible to apply twice.

    `unet_file` selects the diffusion model (V2_MODELS registry) and
    supplies the steps/CFG its variant defines, which `sampler_settings`
    may then override key by key — as it does for V2_SAMPLER_DEFAULTS, and
    `variance_settings` for V2_VARIANCE_DEFAULTS. `variance_seed` defaults
    to the image seed so a reproducible seed reproduces the whole graph.

    Denoise is forced to 1.0 whatever `sampler_settings` says: the source
    image reaches the model through Krea2EditModelPatch and the grounded
    encoder, never through the starting latent, so the sampler must denoise
    the empty latent completely.

    The three patch-node inputs that make this the v1.2 recipe rather than
    v1.1 are all wired, and all three are optional inputs whose absence
    degrades silently rather than erroring:

      • `vae` + `source_image` — without *both*, fit_mode does nothing.
        They are what lets the node resample the reference in pixel space.
      • `target_latent` — the same latent the sampler starts from, so the
        node pre-encodes at execution time instead of during the first
        sampling step and the diffusion model is not evicted mid-run.
    """
    entry = resolve_model(unet_file)
    steps, cfg, _turbo_lora = model_defaults(entry)
    sampler = {**V2_SAMPLER_DEFAULTS, "steps": steps, "cfg": cfg,
               **(sampler_settings or {}), "denoise": 1.0}
    variance = {**V2_VARIANCE_DEFAULTS, **(variance_settings or {})}

    wf = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": entry["file"], "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": active_text_encoder(), "type": "krea2",
                       "device": "default"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": V2_VAE_FILE},
        },
    }
    model_ref, clip_ref, vae_ref = ["unet", 0], ["clip", 0], ["vae", 0]

    if GPU_COUNT >= 2:
        # Same placement every other tab uses. Pure device assignment — it
        # cannot change the image, and on the single-GPU pods this app
        # targets these nodes are never built at all.
        wf["clip_gpu1"] = {
            "class_type": "SelectCLIPDevice",
            "inputs": {"clip": clip_ref, "device": "gpu:1"},
        }
        wf["vae_gpu1"] = {
            "class_type": "SelectVAEDevice",
            "inputs": {"vae": vae_ref, "device": "gpu:1"},
        }
        clip_ref, vae_ref = ["clip_gpu1", 0], ["vae_gpu1", 0]

    # The Identity Edit LoRA first, at the strength it was trained at, and
    # model-only — the grounded encoder reads the image through the CLIP,
    # so patching the CLIP with an edit LoRA is not part of the recipe.
    wf["edit_lora"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"lora_name": EDIT_LORA_FILE, "strength_model": 1.0,
                   "model": model_ref},
    }
    model_ref = ["edit_lora", 0]

    # Then the V2 stack, model + CLIP, exactly as the V2 tab applies it.
    # Both refs advance together because the source node feeds its CLIP
    # output onward to the encoders.
    for i, (lora_file, strength) in enumerate(loras):
        node = f"lora{i}"
        wf[node] = {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": lora_file,
                       "strength_model": float(strength),
                       "strength_clip": float(strength),
                       "model": model_ref, "clip": clip_ref},
        }
        model_ref, clip_ref = [node, 0], [node, 1]

    wf["source"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    wf["encode"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["source", 0], "vae": vae_ref},
    }
    wf["latent"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": int(width), "height": int(height),
                   "batch_size": 1},
    }
    wf["edit_model"] = {
        "class_type": "Krea2EditModelPatch",
        "inputs": {
            "model": model_ref,
            "source_latent": ["encode", 0],
            "source_image": ["source", 0],
            "vae": vae_ref,
            "target_latent": ["latent", 0],
            "fit_mode": fit_mode,
            "ref_boost": float(ref_boost),
        },
    }
    wf["positive"] = {
        "class_type": "Krea2EditGroundedEncode",
        "inputs": {"clip": clip_ref, "prompt": prompt,
                   "image": ["source", 0], "grounding_px": int(grounding_px)},
    }
    if negative.strip():
        wf["negative"] = {
            "class_type": "Krea2EditGroundedEncode",
            "inputs": {"clip": clip_ref, "prompt": negative,
                       "image": ["source", 0],
                       "grounding_px": int(grounding_px)},
        }
    else:
        # One fewer encoder pass, and the same thing an empty negative
        # meant in build_edit_workflow. Note the V2 tab always encodes its
        # negative because its source graph does; here an empty box is a
        # real possibility, since the instruction carries the intent.
        wf["negative"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        }
    # The variance node sits on the positive conditioning only, as it does
    # in the V2 graph — the difference is that what it perturbs here is the
    # grounded encoding, which carries the source image as well as the
    # instruction. Set the preset to "❌ Disabled" for a batch that varies
    # by sampling noise alone.
    wf["variance"] = {
        "class_type": "RBG_Smart_Seed_Variance",
        "inputs": {
            "conditioning": ["positive", 0],
            "seed": int(seed if variance_seed is None else variance_seed),
            **variance,
        },
    }
    wf["sampler"] = {
        "class_type": "ClownsharKSampler_Beta",
        "inputs": {
            "model": ["edit_model", 0],
            "positive": ["variance", 0],
            "negative": ["negative", 0],
            "latent_image": ["latent", 0],
            "seed": int(seed),
            **sampler,
        },
    }
    wf["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", 0], "vae": vae_ref},
    }
    wf["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": ["decode", 0]},
    }
    log.debug("Krea 2 V2 Edit graph: %d nodes, %d style LoRA(s) over the "
              "Identity Edit LoRA", len(wf), len(loras))
    return wf
