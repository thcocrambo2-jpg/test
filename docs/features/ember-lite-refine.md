# ⚡ Ember Lite refine

A realism pass over every picture an image tab makes: each result is
re-rendered once by Z-Image Turbo (Ember Lite's model) from its own
pixels, so the content stays and the skin, light and grain are repainted.
For someone using the app, and for someone changing the code.

## Where it is

A closed **⚡ Ember Lite refine** section, just above Seed & batch, on
eight tabs:

| Generate | Edit |
| --- | --- |
| 🎨 Krea2, 🔶 Krea2 V2, 🌄 Qwen 2.1 | ✨ Krea2 Edit, 🔷 Krea2 V2 Edit, 💠 Krea2 Edit V2, 🧩 Qwen 2.1 Reference, 🪄 Qwen Edit 2511 |

Not on ⚡ Ember Lite itself, which already has a start image, and not on
🌸 Anima, whose drawings a photographic pass would undo.

It needs the `zimage_refine` feature, which is not the Ember Lite tab's:
a key can have either without the other. Without it the section is not on
the form at all. See [Licensing](#licensing).

## The controls

| Control | Default | What it does |
| --- | --- | --- |
| Refine with ⚡ Ember Lite | off | Refines every result of the click |
| Refine denoise | 0.25 | How much of each picture is re-sampled. 0.25 keeps the picture and repaints its surface; above about 0.5 Ember Lite starts redrawing it |
| Refine prompt (optional) | empty | What the refine is prompted with; see below |

The denoise and the prompt show once the tick is on. The tick and the
denoise are preset settings, stored as `refine: {on, denoise}` in the
blob; the prompt, like the main prompt, is not.

### The refine prompt

When the box is empty:

- **Generate tabs** refine with their own prompt. It describes the
  picture, and it is what the img2prompt sweep refined with.
- **Edit tabs** refine with a built-in realism prompt,
  `REFINE_EDIT_PROMPT` in
  [`ember/pipelines/zimage/constants.py`](../../ember/pipelines/zimage/constants.py).
  Their prompt is an instruction — "make the jacket red" — which would
  push the change a second time, or mean nothing ("remove the man behind
  her"). The built-in prompt describes only how a real photograph looks,
  never what is in it: at 0.25 the content comes from the pixels.

Anything typed in the box wins on every tab.

## What is saved

**Both pictures, each its own Gallery entry.** The refined file is named
after its original plus `_refined` (`Krea2V2_<tag>_00001_.png` and
`Krea2V2_<tag>_refined_00001_.png`), so the two sit side by side, and the
result panel lists each refined picture right after its original.

Each has its own recipe, and loading either from the Gallery makes that
picture again:

- **The original** keeps its own tab's recipe, with the tick **off**.
- **The refined one** is filed as the **⚡ Ember Lite** run it amounts
  to: the original as the Start image (kept in the source store, like any
  upload), the refine's prompt, denoise and seed, 12 steps at CFG 1 on
  `er_sde`, no LoRAs, Upscale off, plus `refined_from`, the original's
  path. So **Load these settings** on a refined picture opens Ember Lite
  ready to refine the same original again — change the denoise or the
  prompt and press Generate. The Gallery offers that button only for a
  tab the licence has, so without the Ember Lite tab a refined picture
  has none; the refine itself needs only `zimage_refine`.

### Refining the same picture again

Open the refined picture in the Gallery, **Load these settings**, and
Ember Lite comes up on the original with what the refine used. Only the
refine runs — the original is not rendered again — and every try is a
new Ember Lite picture of its own. The graph that reload builds is the
refine's exactly, which is why the refine is sized the way the next
section says.

If a refine fails, the click stops there with a ⚠️ status; every picture
already made is kept.

## How it runs

[`ember/pipelines/zimage/refine.py`](../../ember/pipelines/zimage/refine.py)
turns the three controls into a `Refine` (or refuses the click up front if
the model or Z-Image's files are not downloaded), and the handler passes
it to `_run_jobs` in
[`ember/generation/runner.py`](../../ember/generation/runner.py).

`_run_jobs` runs the batch exactly as it does without the tick, then
`_refine_pass` takes every picture in order: it opens it, uploads it as
the start image, and runs `build_zimage_workflow` — the same image to
image the Ember Lite tab runs — with the img2prompt sweep's settings.

**Size.** As the Ember Lite tab sizes a start image, so its recipe can
say it: Resolution `1328×1328 (1:1)` times the multiplier that gives the
original's pixel count, snapped to the slider's 0.05 steps
(`refine_multiplier`), and the original's own shape at that area on the
grid of 16. A 2048×1152 Krea2 V2 picture refines at multiplier 1.15,
2032×1152 — within a few percent of its own size, never cropped or
stretched.

| | |
| --- | --- |
| steps / CFG / sampler | 12 / 1 / `er_sde`, scheduler `simple` |
| seed | the original picture's |
| LoRAs | none |
| upscale | off |
| model | the first in the `zimage_refine` feature's catalogue list |

**One model swap per click.** The refines run after the whole batch, so
the first one swaps to Z-Image (`_release_on_swap` frees the batch's
model) and the rest reuse it; the next click swaps back. The two models
are never in VRAM together. Each refine is about three sampled steps,
so the swap costs more than the refine; `EMBER_KEEP_MODELS_LOADED=1`
skips the unload on a GPU big enough to hold both.

## Licensing

`zimage_refine` is an **add-on** (`features.ADDONS`): a feature with no
tab of its own, which switches fields on other tabs on through
`Field.feature`. A field with a feature this licence lacks stays a
handler argument, so no signature depends on the licence, but
`TabSchema.to_json` leaves it out of the form, `coerce` holds it at its
default whatever is submitted, and a settings token does not set it.
`scripts/check_routes.py` exempts add-ons from "every key owns a tab",
and fails one that no field names.

The feature needs the `zimage` asset group (the text encoder, VAE and
upscale model) and `catalog`: its model is its own `feature_assets`
entry, `zimage_refine`, listing `z-image-turbo-bf16`, so a key with the
refine and not the tab still downloads it. It is on no plan, like
`zimage_t2i`; a key gets it through `features_extra`.

## Related

- [Ember Lite](../pipelines/zimage.md#the-start-image) — the start image
  this reuses.
- [Model swapping](../architecture/model-swapping.md) — why the refines
  wait for the batch.
- [Gallery and recipes](../architecture/gallery-and-recipes.md).
