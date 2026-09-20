# Krea 2 — text-to-image and instruction editing

The two original Krea 2 tabs: **🎨 Krea2** (`krea_t2i`) and
**✨ Edit (Instruction)** (`krea_edit`). For someone using them, and for
someone changing the graph they build.

Both graphs are built by
[`ember/pipelines/krea2/workflow.py`](../../ember/pipelines/krea2/workflow.py);
the fixed half of each — the VAE, the text encoders, the resolution and
sampler lists — is in
[`ember/pipelines/krea2/constants.py`](../../ember/pipelines/krea2/constants.py).
The models and style LoRAs are not: those come from the catalogue, per
feature, by id. See [catalogue.md](catalogue.md).

## 🎨 Krea2 — text to image

Type a sentence, get a photograph. `build_workflow` uses only nodes that
ship with current ComfyUI: `UNETLoader`, `CLIPLoader(type="krea2")`,
`VAELoader`, a `LoraLoaderModelOnly` chain, `CLIPTextEncode`,
`EmptyLatentImage`, `KSampler`, `VAEDecode` and `SaveImage`. No node pack
is installed for this tab.

It mirrors the official Krea 2 template. No `ModelSampling` node is
needed — the 1.15 sigma shift is built into ComfyUI's Krea2 model class —
and LoRAs apply to the **diffusion model only**, which is why the chain is
`LoraLoaderModelOnly` rather than `LoraLoader`.

The sampler runs at scheduler `simple` and denoise 1.0. The tab's sampler
list is `SAMPLERS` (`er_sde` first, then `euler`, `euler_ancestral`,
`dpmpp_2m`, `res_multistep`) — native samplers that work well with Krea 2
on that scheduler. Resolution is a fixed list, `RESOLUTION_PRESETS`, from
1024×1024 up to 1536×1024, defaulting to `1024×1536 (Portrait XL)`.
Steps and CFG default to the selected model record's values, not to
anything in the code.

An empty **Negative prompt** becomes `ConditioningZeroOut` instead of a
second `CLIPTextEncode`, which saves an encoder pass. At CFG 1.0 — the
turbo default — the negative is ignored by the sampler either way.

Outputs are written with the filename prefix `Krea2`.

### The text encoder

Two encoders are possible and `active_text_encoder()` in
[`ember/pipelines/common.py`](../../ember/pipelines/common.py) picks
between them at build time. The abliterated (uncensored) Qwen3-VL encoder
is downloaded as shards from `ABLITERATED_ENCODER_REPO` and merged into a
single ComfyUI-loadable `ABLITERATED_ENCODER_FILE`; when that merged file
is on disk the graph names it, and otherwise it falls back to
`TEXT_ENCODER_FILE` (~5.2 GB), which is downloaded either way. The VAE,
`qwen_image_vae.safetensors`, is a fixed download from
`Comfy-Org/Krea-2`.

### Two GPUs

When a second GPU is present, `_model_nodes` inserts the native
`SelectCLIPDevice` and `SelectVAEDevice` nodes to keep the ~13 GB
diffusion model alone on `gpu:0` and park the text encoder and VAE on
`gpu:1`, so nothing swaps mid-run. On a single-GPU machine those nodes are
simply not added, so the same builder can never produce a graph that
fails for lack of a GPU.

## ✨ Edit (Instruction) — nano-banana-style editing

Upload an image and describe the change: "make the jacket red", "this
person walking a dog on a beach". `build_edit_workflow` is the same
skeleton with the edit half grafted on.

Unlike img2img, the source image is fed to the **model itself** rather
than used as a starting latent:

- `Krea2EditModelPatch` prepends the source's VAE latents as clean
  in-context tokens;
- `Krea2EditGroundedEncode` lets the Qwen3-VL encoder read the image
  alongside the instruction.

So the model actually sees the picture it is editing, and identity and
unchanged regions survive instead of being repainted from scratch.
Denoise stays at 1.0 — the source arrives through conditioning, not
through the latent.

Both nodes come from the community
[ComfyUI-Krea2Edit](https://github.com/lbouaraba/comfyui-krea2edit) pack,
cloned into `custom_nodes` by `ember.comfy.setup.install_custom_nodes`
only when this feature is granted, since nothing else uses those two
nodes. The weights are the community
[Krea 2 Identity Edit LoRA](https://huggingface.co/conradlocke/krea2-identity-edit)
(~1.83 GB, auto-downloaded).

The Identity Edit LoRA is applied **by the builder**, first, at strength
1.0 as trained, and is not one of the visible LoRA slots — which is what
makes applying it twice impossible. Style LoRAs from the tab's catalogue
list stack after it, exactly as on the text-to-image tab.

Outputs are capped at about **2 MP**; above that the LoRA duplicates
content. The filename prefix is `Krea2Edit`.

### The two sliders

**Grounding** (`grounding_px`) trades edit strength against likeness
fidelity: lower changes more, higher preserves more. Its range is the
LoRA's trained **384–768**, and running above that is what makes the
model emit duplicated "double picture" compositions.

**Reference fidelity** (`ref_boost`) is how hard the edit holds the
source. 1.0 is neutral, about 4 gives strong face and body likeness, and
past about 10 removals stop working — so a removal wants a lower value,
along with the roughly 20 steps / CFG ≈ 3 recipe.

### Why the versions are one unit

`EDIT_LORA_FILE` is the v1.2 weights, and they need the v1.2 nodes: those
are what supply the FIT reference geometry (a source whose aspect ratio
differs from the output is fitted, not stretched) and the `ref_boost`
input. Conversely the v1.2 nodes default `fit_mode` to `fit`, which the
v1 and v1.1 weights were not trained for. The node pack is held still by
`scripts/PINS.json` — **bump both together or neither.**

Three of the patch node's inputs are what make this the v1.2 recipe, and
all three are *optional* inputs whose absence degrades silently rather
than raising:

- **`vae` + `source_image`** — without **both**, `fit_mode` does nothing.
  They are what lets the node resample the reference in pixel space.
  (`fit_mode="crop (legacy)"` is the v1/v1.1 geometry, kept as an argument
  for running older weights.)
- **`target_latent`** — the same latent `KSampler` starts from. Passing it
  makes the node pre-encode at execution time instead of during the first
  sampling step, so the diffusion model is not evicted mid-run on a GPU
  that is already sharing VRAM with a second ComfyUI instance (see
  `KREA2_MAIN_RESERVE_VRAM` in `docs/configuration.md`).

### Controls that belong to editing alone

Grounding, reference fidelity, the second-reference switch and the fit
mode are not carried by any preset, so a preset applied on this tab
leaves them exactly where you set them. The Edit tab reads 🎨 Krea2's
presets — see [presets](../features/presets.md).

## Related

- [catalogue.md](catalogue.md) — where the models and LoRAs come from, and
  how the eight blank slots work.
- [krea2-v2.md](krea2-v2.md) — the second, independent text-to-image
  pipeline.
- [krea2-v2-edit.md](krea2-v2-edit.md) — this edit recipe grafted onto
  that pipeline.
