# Krea 2 Edit V2

The **💠 Krea2 Edit V2** tab: the 🔷 Krea2 V2 Edit tab with conradlocke's
Identity Edit V2 and General Edit V2 LoRAs in place of Identity Edit
v1.2, plus comfyui-krea2edit 2.0.0's reference placement. For someone
using the tab, and for someone changing it.

Feature key `krea_edit_v2`, route `/edit/krea2-edit-v2`. Its constants are
in
[`ember/pipelines/krea2_edit_v2/constants.py`](../../ember/pipelines/krea2_edit_v2/constants.py),
and the graph is built by `build_v2_edit_workflow` in
[`ember/pipelines/krea2_v2_edit/workflow.py`](../../ember/pipelines/krea2_v2_edit/workflow.py).

## Licence

The V2 LoRAs are **free for non-commercial use only**. Commercial use,
which includes offering a service built on them, needs a paid licence
from the author, agreed case by case. The terms are in `LICENSE_V2.md` in
[`conradlocke/krea2-identity-edit`](https://huggingface.co/conradlocke/krea2-identity-edit).
v1.2, which 🔷 Krea2 V2 Edit runs, stays under the Krea 2 Community
License, which allows commercial use.

So:

- the features row is `enabled: false` and the tab is on no plan, not even
  `admin`. A key gets it through `features_extra`, for evaluation;
- the two files download from the upstream repo at the revision
  [`scripts/PINS.json`](../../scripts/PINS.json) records, and are never
  mirrored, because the mirror repos are public.

Putting it on sale after a commercial licence is in place is a DB-only
edit: add `krea_edit_v2` to a plan and flip its row to `enabled: true`. A
showcase block is optional and needs a build.

## What is different from 🔷 Krea2 V2 Edit

Everything not in this table is the 🔷 tab's: the V2 model dropdown, the
Wan 2.1 VAE, the model + CLIP LoRA rows, `ClownsharKSampler_Beta`, Smart
Seed Variance, the 2 MP cap and the pinned denoise. This tab has model and
LoRA lists of its own (`feature_assets/krea_edit_v2`) and reads 🔶 Krea2
V2's presets, as the 🔷 tab does.

| | 🔷 Krea2 V2 Edit | 💠 Krea2 Edit V2 |
| --- | --- | --- |
| edit LoRA | `krea2_identity_edit_v1_2` at 1.0 | **Edit LoRA** dropdown: Identity V2 at 1.0, General V2 at 1.0, or both at 0.6 |
| reference placement | not sent (the node's default, aligned) | **Person placement** and **Scene placement** dropdowns |
| reference fidelity default | 4 | 2, the author's V2 Identity value |
| reference geometry | a dropdown (fit / crop (legacy)) | always `fit`; the legacy crop was for v1/v1.1 weights |

Each edit LoRA goes on model-only, in front of the V2 stack, as on the 🔷
tab. "Both at 0.6" chains two `LoraLoaderModelOnly` nodes, because the
author's note is that both at 1.0 breaks the image.

## Reference placement

`ref_position` places the **last** reference: the only image, or the
subject when there are two. `ref_position_a` places the first, the scene,
and is sent only when a second reference is on.

| Option | What it does |
| --- | --- |
| `aligned (trained)` | The reference is pinned onto the output layout, as the LoRAs were trained. Keeps the photo's composition. |
| `beside (right of target)` | The reference sits next to the canvas. Identity still flows, but the layout pin is gone, so the person can be restaged freely. The default here, because a new pose is what the tab is for. |
| `bag of features` | Experimental: every token of a reference shares one position. Content without layout. |

The author's recipe for putting a person into a scene is scene `aligned`,
person `beside`, which is what the two defaults give.

## Settings that matter

From the author's model card:

- Turbo, 8 steps, CFG 1. Raw at 20 steps and CFG 3 for removals with
  General Edit.
- Reference fidelity 2 for Identity, 4 for General. Grounding 768.
- Keep the output at or under 2 MP, which the tab enforces.
- Say how much of the person is visible ("full body", "sitting
  cross-legged on a log"). "standing" alone tends to copy the photo's crop.
- Name the new outfit concretely, or the reference's clothing carries
  over.

## Node pack

comfyui-krea2edit is pinned at **2.0.0** (`13ef60f2`). 2.0.0 only adds
the two placement inputs, and their defaults reproduce 1.2.x, so ✨ Krea2
Edit and 🔷 Krea2 V2 Edit run on it unchanged.

ComfyUI ignores inputs a node does not declare, so on an older checkout
this tab would run and silently keep every person aligned. Two things
guard against that:

- `install_custom_nodes` moves an existing git checkout to the pin with
  `repin_checkout`, as it does for ComfyUI itself. A tree unpacked from
  the mirror tarball has no `.git` and is left alone.
- With this feature on, startup asks ComfyUI whether
  `Krea2EditModelPatch` declares `ref_position`, and logs an error when it
  does not.

## Downloads

The `edit_lora_v2` asset group fetches `krea2_identity_edit_v2.safetensors`
and `krea2_general_edit_v2.safetensors` (~1.8 GB each) into `loras/`.
Each is tried on its own, and the tab refuses only the Edit LoRA choice
whose file is missing. The V2 weights and node packs come from the same
groups as 🔷 Krea2 V2 Edit's.

## Related

- [krea2-v2-edit.md](krea2-v2-edit.md) — the tab this one is built from.
- [krea2.md](krea2.md) — the original edit recipe, in full.
- [mirror-and-pins.md](../releasing/mirror-and-pins.md) — why these files
  stay out of the mirror.
