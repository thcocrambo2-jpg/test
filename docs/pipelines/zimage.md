# Ember Lite — text to image, with an optional upscale

The **⚡ Ember Lite** tab (`zimage_t2i`, at `/generate/ember-lite`): fast,
realistic text-to-image on Z-Image Turbo, with an **Upscale 1.5x** tick that adds a second, tiled
pass. For someone using the tab, and for someone changing the graph it
builds.

The graph is built by
[`ember/pipelines/zimage/workflow.py`](../../ember/pipelines/zimage/workflow.py)
and driven by
[`ember/pipelines/zimage/handler.py`](../../ember/pipelines/zimage/handler.py);
the fixed half — the text encoder, the VAE, the upscale pass, the
resolution and sampler lists — is in
[`ember/pipelines/zimage/constants.py`](../../ember/pipelines/zimage/constants.py).
The diffusion model is not: it is a catalogue record,
`z-image-turbo-bf16` (shown as "Ember Lite Turbo bf16"), so the Model dropdown works the way Krea's does. See
[catalogue.md](catalogue.md).

The feature is on no plan. A key gets it through `features_extra`; see
[Licensing and features](../architecture/licensing-and-features.md).

## Where the graph comes from

Two workflows from Hearmeman24's `comfyui-qwen-template`:
`Z_Image_Turbo.json` (text to image) and `Z_Image_Turbo_Upscale.json`
(the same graph plus an UltimateSDUpscale pass). The tab is both of them:
with the tick off it builds the first, with it on the second.

## The graph

Every node of the base graph is core ComfyUI:

| Node | Values |
| --- | --- |
| `UNETLoader` | the model record's file, weight dtype `default` |
| `CLIPLoader` | `qwen_3_4b.safetensors`, type `qwen_image`, device `default` |
| `VAELoader` | `ae.safetensors` |
| `LoraLoaderModelOnly` ×n | the tab's LoRA stack — see below |
| `CLIPTextEncode` ×2 | the prompt and the negative |
| `EmptyLatentImage` | the resolution times the multiplier, batch 1 |
| `KSampler` | Steps, CFG and Sampler from the form; scheduler `simple`, denoise 1 |
| `VAEDecode`, `SaveImage` | prefix `ZImage` |

The template's defaults are the tab's: **12 steps, CFG 1, `er_sde`**.
Steps and CFG come from the model record, as on the Krea tabs, and the
record says 12 and 1. At CFG 1 the sampler ignores the negative; the
template's negative text is still the default, so it is there once CFG
is raised.

**The text-encoder type.** `qwen_image` is what the template uses, and it
is a valid `CLIPLoader` type at the pinned ComfyUI. ComfyUI picks the
encoder class from the weights rather than from the type: every type but
`flux` and `flux2` loads a Qwen3-4B file as the Z-Image encoder, so this
and the `lumina2` of ComfyUI's own Z-Image template build the same model.

**Sizes.** The Resolution list is the template's resolution note, plus
the two 1080p sizes the upscale file adds:

| | |
| --- | --- |
| 1:1 | 1328×1328 |
| 16:9 / 9:16 | 1664×928 / 928×1664 |
| 4:3 / 3:4 | 1472×1140 / 1140×1472 |
| 3:2 / 2:3 | 1584×1056 / 1056×1584 |
| 1080p | 1920×1080 / **1080×1920** (the default) |

The **Resolution multiplier** (0.5–2.0, default 1) scales both sides, and
each is rounded with Python's `round()` — what the template's
`SimpleMath+` nodes do with `a*b`. The result goes to `EmptyLatentImage`
as it is. Its step of 8 is not enforced by ComfyUI's validator and the
node floors to the latent grid itself, so a 1140-wide size renders 1136
wide, exactly as in the template. The Krea tabs' `parse_resolution` is
deliberately not used: it would snap 1080 to 1088.

**Two GPUs.** As on every other tab, a second GPU gets the text encoder
and the VAE (`SelectCLIPDevice`, `SelectVAEDevice`), and the diffusion
model stays alone on `gpu:0`. On one GPU those nodes are not added.

### What was left out

- the two **`Text Prompt (JPS)`** nodes — text boxes feeding
  `CLIPTextEncode`;
- **`PrimitiveInt`**, **`Float`** and **`SimpleMath+`** (essentials) —
  width and height times the multiplier, which the handler computes;
- **`ModelPassThrough`** (KJNodes), the **`Reroute`s** and the Impact
  Pack's **`ToBasicPipe`** / **`FromBasicPipe_v2`** — wiring only;
- the four bypassed **`LoraLoaderModelOnly`** placeholders, which are the
  tab's LoRA stack instead;
- **`PreviewImage`** — the app saves with its own prefix, as every tab
  does.

So the only custom node pack the tab needs is ComfyUI_UltimateSDUpscale,
and only for the tick.

## The upscale pass

With **Upscale 1.5x** ticked, the decoded image goes through two more
nodes, and only the upscaled image is saved:

- `UpscaleModelLoader` — `4xLSDIR.pth`;
- `UltimateSDUpscale` — the decoded image, the **same LoRA-patched model**
  and the same two conditionings as the base pass, the VAE, and the
  upscale model. Its tiles are the base image's width and height, as the
  template links them, and its seed is the job's.

Everything else on it is fixed, from the template, and named by the
node's inputs at the pinned commit:

| Input | Value | Input | Value |
| --- | --- | --- | --- |
| `upscale_by` | 1.5 | `tile_padding` | 32 |
| `steps` | 8 | `seam_fix_mode` | `None` |
| `cfg` | 1 | `seam_fix_denoise` | 1 |
| `sampler_name` | `er_sde` | `seam_fix_width` | 64 |
| `scheduler` | `simple` | `seam_fix_mask_blur` | 8 |
| `denoise` | 0.18 | `seam_fix_padding` | 16 |
| `mode_type` | `Chess` | `force_uniform_tiles` | true |
| `mask_blur` | 8 | `tiled_decode` | false |
| | | `batch_size` | 1 |

`batch_size` is not in the template: the pack gained it after the
workflow was saved, and it is required at the pin. 1 is its default and
what the older pack did.

With the tick on, ComfyUI's progress bar shows three passes per image:
the render, then 4xLSDIR counting one step per 512-px tile (12 at
1080×1920), then one 8-step pass per tile of the 1.5× image (four at
that size).

The tick is off by default. Off, the graph has no trace of the pass — it
is the Turbo workflow and nothing else. On, the tab first checks that
ComfyUI is answering and that the `UltimateSDUpscale` node is registered,
and says so plainly when it is not, rather than letting ComfyUI reject
the graph.

## The weights

| File | Folder | From | Size |
| --- | --- | --- | --- |
| `z_image_turbo_bf16.safetensors` | `diffusion_models` | `Comfy-Org/z_image_turbo`, the catalogue record | 12.3 GB |
| `qwen_3_4b.safetensors` | `text_encoders` | `Comfy-Org/z_image_turbo` | 8.0 GB |
| `ae.safetensors` | `vae` | `Comfy-Org/z_image_turbo` | 0.34 GB |
| `4xLSDIR.pth` | `upscale_models` | `Hearmeman/comfyui-template-assets` | 0.07 GB |
| **total** | | | **~20.8 GB** |

The diffusion model is fetched by the `catalog` asset group like every
catalogue model; the other three by the `zimage` group
(`download_zimage_models`). The upscale model is fetched whether or not
the tick is ever used. Both repos are pinned in `scripts/PINS.json`. The
Comfy-Org repo is org-backed and upstream-only, like Krea's and Wan's;
the asset repo is a community one, so `4xLSDIR.pth` also has a
`mirror_manifest.json` entry — see
[the mirror and the pins](../releasing/mirror-and-pins.md).

`upscale_models` is the fifth folder `link_model_dirs()` points ComfyUI
at, and only this tab reads it.

## The node pack

[ComfyUI_UltimateSDUpscale](https://github.com/ssitu/ComfyUI_UltimateSDUpscale)
(ssitu), pinned in `scripts/PINS.json`. `install_zimage_nodes()` clones it
only when `zimage_t2i` is granted, and `main.py` logs whether
`UltimateSDUpscale` registered. A failure is logged, never fatal: the
tick then refuses with a message, and the rest of the tab and every
other tab work.

The pack wraps the A1111 Ultimate SD Upscale script as a **git
submodule**, and fills an empty one at import by downloading that
script's master branch, unpinned. So `clone_pinned()` and
`repin_checkout()` check out submodules at the commits the pin records
(`init_submodules`, a no-op for every checkout without a `.gitmodules`),
and a clone that fails part way is removed rather than left for the pack
to fill. It has no mirror tarball yet, so the pinned clone is the path a
pod takes.

## The LoRA stack

Eight blank rows, all off, like 🎨 Krea2's, chained as
`LoraLoaderModelOnly` nodes between the diffusion model and the sampler —
where the template's four bypassed placeholders sit. The upscale pass
samples with the same patched model. Each row offers the catalogue's
list for `zimage_t2i`, which holds Z-Image LoRAs only: Krea LoRAs do not
load on Z-Image, so none are shared. The tab does not insert a LoRA's
trigger words, so type them into the prompt.

## Presets

The tab has its own preset list, filed under `zimage_t2i`, with the save
tickbox and no prompt-library publish — the Prompt Library only knows the
Krea tabs. A preset carries the model, steps, CFG, resolution,
multiplier, sampler, the Upscale tick, seed, randomize, batch count and
the LoRA stack. See [presets](../features/presets.md).

## Related

- [catalogue.md](catalogue.md) — the model record and the LoRA list.
- [krea2.md](krea2.md) — the tab whose form this one copies.
