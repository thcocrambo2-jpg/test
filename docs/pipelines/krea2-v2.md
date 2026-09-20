# Krea 2 V2

The **🔶 Krea 2 V2** tab: the Krea2 advanced *KREA 2 TURBO/RAW* workflow
ported node-for-node into the app. For someone using the tab, and for
someone changing the graph.

It is a **second text-to-image pipeline**, not a variation of the first:
its own model and LoRA lists (the `krea_v2_t2i` feature in the
catalogue), its own VAE, its own LoRA rows and its own sampler. Nothing
it does moves the 🎨 Krea2 tab, and vice versa. A licence that does not
grant `krea_v2_t2i` skips the downloads and hides the tab.

Built by
[`ember/pipelines/krea2_v2/workflow.py`](../../ember/pipelines/krea2_v2/workflow.py),
with the fixed half in
[`ember/pipelines/krea2_v2/constants.py`](../../ember/pipelines/krea2_v2/constants.py).

The VAE is `wan21-vae.safetensors` from `wangkanai/wan21-vae`, which the
source workflow's guide recommends over the stock Qwen VAE.

## Turbo / Raw

The **Model** dropdown offers the feature's models from the catalogue,
and picking one resets the defaults its record carries. With the shipped
seed data:

| | Turbo (default) | Raw |
| --- | --- | --- |
| model | `krea2_turbo_mxfp8.safetensors` (~13.5 GB) | `krea2_raw_fp8_scaled.safetensors` (~13.1 GB) |
| steps | 10 | 20 |
| CFG | 1.0 | 2.5 |
| Turbo LoRA slot | off | **on** at 0.6 |
| sampler | `linear/euler` + `bong_tangent`, eta 0.5, bongmath on, standard | same |

That is precisely the raw recipe from the source workflow's companion
note, so the two variants differ by exactly the three things it lists.
The raw record says so itself: its `turbo_lora` is
`{lora: "krea2-turbo", strength: 0.6}`. The turbo model is the same
record the 🎨 Krea2 and ✨ Krea2 Edit tabs use, and every file downloads
once however many features list it.

The Turbo LoRA is toggled as **the row whose id the raw model's
`turbo_lora` names** in the visible LoRA stack — found by id, not by
position — rather than bolted on inside the workflow builder. That keeps
the row editable and, more importantly, makes it impossible to apply the
LoRA twice when a raw run also has that slot ticked by hand. It is the
same "never applied silently" rule the trigger words follow. Steps, CFG
and the slot all stay editable after the dropdown fires; whatever is on
screen is what gets submitted.

Because steps and CFG belong to the model, they are **not** in
`V2_SAMPLER_DEFAULTS`. That dict holds only the knobs both variants
share, so the two numbers have one source of truth: the model record in
the catalogue.

## Why it needs its own builder

Three things differ from the Krea2 tabs, and they are why this is a
separate builder rather than a flag on `build_workflow`:

- **`ClownsharKSampler_Beta`** (RES4LYF) replaces `KSampler`. `eta`,
  `bongmath` and the `bong_tangent` scheduler have no core equivalent.
  Steps and CFG come from the selected variant; everything else is
  shared.
- **`RBG_Smart_Seed_Variance`** sits between the positive prompt and the
  sampler, perturbing the conditioning per seed so a batch varies without
  drifting off-prompt. Its combo values carry emoji (`🌱 Subtle`,
  `📸 Krea2 (SingleStream)`) and must match the node's option lists
  character for character, or ComfyUI rejects the prompt.
- **LoRAs apply to the model *and* the CLIP.** The source uses rgthree's
  Power Lora Loader in "Single Strength" mode, so the chain here is
  `LoraLoader`, not the `LoraLoaderModelOnly` the other Krea tabs build.
  Each row keeps its own **On** checkbox, which is that node's per-row
  toggle.

## What was translated rather than copied

The app submits **API-format** graphs, so purely visual nodes have no
counterpart and are dropped: rgthree's *Fast Bypasser* (a UI toggle whose
output goes nowhere), *Label* and *MarkdownNote*. Two more are
translated, which changes no pixels:

| Source node | Here | Why |
| --- | --- | --- |
| Power Lora Loader (rgthree) | `LoraLoader` chain | identical math; on/off becomes the row's checkbox |
| ResolutionSelector → PrimitiveInt → EmptyLatentImage | `resolve_size()` in Python | integer plumbing; the tab shows the W×H it resolved to |
| WAS `Image Save` | `SaveImage` | the app finds outputs through ComfyUI's history, and WAS writes its own dated tree the Gallery would not see |

`ImageSharpen` and `FilmGrain` are **bypassed (`mode: 4`) in the source
workflow**, so both start off and the tab reproduces it as shipped —
`VAEDecode` straight to `SaveImage`. The Post-processing accordion turns
them on per job, in that order.

## Resolution

Resolution follows the source's aspect + megapixel scheme rather than a
preset list. Megapixels count as 1024² (ComfyUI's own convention, as in
`ImageScaleToTotalPixels`) and each side rounds to the nearest
`multiple`, so the workflow's 3:4 at 1.5 MP with multiple 8 resolves to
**1088×1448**. Those three are the tab's defaults (`V2_DEFAULT_ASPECT`,
`V2_DEFAULT_MEGAPIXELS`, `V2_DEFAULT_MULTIPLE`).

## Node packs

`ember.comfy.setup.install_v2_nodes` clones three packs for this tab, and
[`ember/main.py`](../../ember/main.py) verifies each class actually
registered after ComfyUI starts:

| Pack | Node | Needed for |
| --- | --- | --- |
| [RES4LYF](https://github.com/ClownsharkBatwing/RES4LYF) | `ClownsharKSampler_Beta` | the sampler — required |
| [ComfyUI-RBG-SmartSeedVariance](https://github.com/RamonGuthrie/ComfyUI-RBG-SmartSeedVariance) | `RBG_Smart_Seed_Variance` | conditioning variance — required |
| [ComfyUI-post-processing-nodes](https://github.com/EllangoK/ComfyUI-post-processing-nodes) | `FilmGrain` | the optional grain toggle only |

A failed clone disables this tab and nothing else, the same contract the
Wan installs follow.

## LoRA stack

One row per LoRA in the feature's catalogue list, in that order, each
**off** and at its record's `default_strength`. The tab's `Default`
preset — applied on load — is what switches the usual ones on (the source
workflow's seven). See [presets](../features/presets.md).

Files download into the shared `loras/` folder from the record's mirror,
the bundled mirror, or its CivitAI / Hugging Face source
(`CIVITAI_TOKEN` is needed for most CivitAI files).

A LoRA whose file has not downloaded stays in the list, labelled
"(not downloaded)"; a run that ticks it skips it, says so in the status
line and the log, and never submits an unresolvable `lora_name`.

Two filenames are worth knowing about. The companion guide links CivitAI
version `3109006` for the realism-engine family while the workflow names
the file `realism_engine_krea2_v3.1.safetensors`, and
`krea2_Enhancer.safetensors` is saved with the workflow's capitalisation
rather than the guide's. Both are the records' `file` in the database —
the graph only cares that the name on disk matches the record, so adjust
the record's source version if CivitAI serves a revision you did not
expect.

## Related

- [catalogue.md](catalogue.md) — the model and LoRA records, and the two
  shapes of LoRA stack.
- [krea2-v2-edit.md](krea2-v2-edit.md) — the edit tab that imports this
  pipeline wholesale.
- [krea2.md](krea2.md) — the first, independent Krea 2 pipeline.
