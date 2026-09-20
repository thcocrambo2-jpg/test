# Krea 2 V2 Edit

The **🔷 Krea2 V2 Edit** tab: the instruction-edit recipe grafted onto the
Krea 2 V2 pipeline. It stands to the ✨ Edit tab exactly as 🔶 Krea 2 V2
stands to 🎨 Krea2. For someone using the tab, and for someone changing
the graph.

Feature key `krea_v2_edit`. Built by
[`ember/pipelines/krea2_v2_edit/workflow.py`](../../ember/pipelines/krea2_v2_edit/workflow.py),
with its own
[`constants.py`](../../ember/pipelines/krea2_v2_edit/constants.py).

## Everything V2 about it is imported, not restated

The builder imports from
[`ember/pipelines/krea2_v2/workflow.py`](../../ember/pipelines/krea2_v2/workflow.py)
rather than repeating it, so the two tabs cannot drift. It reuses:

- the same catalogue helpers, read with **its own** feature key
  `krea_v2_edit`, so its model and LoRA lists are its own;
- the same model-record defaults (steps, CFG, the Turbo LoRA row);
- the same Wan 2.1 VAE;
- the same model + CLIP LoRA rows, with the Turbo LoRA row still toggled
  by the Model dropdown;
- the same `ClownsharKSampler_Beta` settings;
- the same `RBG_Smart_Seed_Variance` node.

The edit half — `Krea2EditModelPatch`, `Krea2EditGroundedEncode`, the
Identity Edit LoRA, **Grounding** and **Reference fidelity** — behaves
exactly as described in [krea2.md](krea2.md#-edit-instruction--nano-banana-style-editing).

## Why swapping the VAE is safe here

Swapping the VAE is safe *for this tab specifically* because the two are
the same family: Qwen-Image's VAE is a Wan 2.1 derivative over the same
16-channel latent space, so the source latents `Krea2EditModelPatch`
prepends as in-context tokens still mean what the Identity Edit LoRA was
trained to read. A VAE from any other family would not be substitutable
this way.

## Three things deliberately not carried over

| | 🔶 Krea 2 V2 | 🔷 Krea2 V2 Edit |
| --- | --- | --- |
| resolution | aspect + megapixels (`resolve_size`) | from the source image, aspect kept, capped at 2 MP (`fit_size`) |
| denoise | a slider (default 1.0) | pinned at 1.0, not exposed — the source arrives through conditioning, not through the starting latent |
| sharpen / film grain | toggles, off, reproducing the bypassed source nodes | absent; there is no source graph to reproduce |

## The mixed LoRA chain

The chain mixes two node types on purpose:

1. The Identity Edit LoRA goes on first as `LoraLoaderModelOnly` at
   strength 1.0 **as trained**. The grounded encoder reads the image
   through the CLIP, so patching the CLIP with an edit LoRA is not part
   of that recipe.
2. The V2 stack then applies over it as `LoraLoader` (model + CLIP),
   which is what Power Lora Loader's "Single Strength" mode does.

Each node passes through whatever input it does not touch, so the mixed
chain is well-formed. As on the ✨ Edit tab, the edit LoRA is added **by
the builder** and is not one of the visible slots, which is what makes
applying it twice impossible.

## Negatives

An empty **Negatives** box becomes `ConditioningZeroOut`, saving an
encoder pass; the 🔶 Krea 2 V2 tab always encodes its negative because
its source graph does. The box is prefilled with `V2_DEFAULT_NEGATIVE`
either way, and CFG 1.0 — the turbo default — ignores it regardless.

## Node packs

This tab needs **both** sets: the three V2 packs and `comfyui-krea2edit`.
`ember.comfy.setup.install_v2_nodes` and
`ember.comfy.setup.install_custom_nodes` each run when *either* of the
features that wants them is granted, and
[`ember/main.py`](../../ember/main.py) verifies all four classes
registered once ComfyUI is up. Neither install is fatal: a failed clone
leaves this tab reporting the missing node and every other tab untouched.

## Presets

This tab reads 🔶 Krea 2 V2's presets — the same dropdown, filled from
the generation tab it shares a pipeline with, and it never writes one.
Which fields transfer and which do not is in
[presets](../features/presets.md).

## Related

- [krea2-v2.md](krea2-v2.md) — the pipeline this imports.
- [krea2.md](krea2.md) — the original edit recipe, in full.
- [catalogue.md](catalogue.md) — the model and LoRA records.
