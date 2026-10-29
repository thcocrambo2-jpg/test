# Qwen Edit 2511 — instruction editing with up to 3 images

The **🪄 Qwen Edit 2511** tab (`qwen_edit_2511`): the user adds 1 to 3
numbered images and writes an edit instruction, and Qwen-Image-Edit-2511
changes image 1 — a new pose, camera angle, outfit or scene — using the
others as references the prompt names by number ("put the woman in image
1 in the pose from image 2"). For someone using the tab, and for someone
changing the graph it builds.

The graph is built by
[`ember/pipelines/qwen_edit/workflow.py`](../../ember/pipelines/qwen_edit/workflow.py)
and driven by
[`ember/pipelines/qwen_edit/handler.py`](../../ember/pipelines/qwen_edit/handler.py);
the fixed half — the text encoder, the Lightning LoRAs, the core nodes,
the Speed choices and the sampler lists — is in
[`ember/pipelines/qwen_edit/constants.py`](../../ember/pipelines/qwen_edit/constants.py).
The diffusion model is a catalogue record,
`qwen-image-edit-2511-fp8mixed`, so a bf16 or int8 record can be added
later as a database edit. See [catalogue.md](catalogue.md).

How it differs from 🧩 Qwen 2.1 Reference: that tab makes a *new* image
from an empty latent, guided by its references. This one starts from
image 1 — it is the starting latent as well as a reference — so the
result keeps that photo's framing, and only what the instruction asks
for changes.

Everything Qwen ships for 2511, and the Lightning and Multiple Angles
LoRAs, is Apache 2.0. The feature is on no plan only because it is new;
a key gets it through `features_extra`. See
[Licensing and features](../architecture/licensing-and-features.md).

## Where the graph comes from

ComfyUI's own template, `image_qwen_image_edit_2511.json` in
`Comfy-Org/workflow_templates`. Each picture is read twice: by the
Qwen2.5-VL text encoder, and spliced into the sequence as a VAE latent.

## The graph

Every node is core ComfyUI. The reference method's `index_timestep_zero`
option, which 2511 needs, first ships in **ComfyUI v0.5.0**; the pin is
well past that (see
[the mirror and the pins](../releasing/mirror-and-pins.md#the-comfyui-pin)).

| Node | Values |
| --- | --- |
| `UNETLoader` | the model record's file, weight dtype `default` |
| `CLIPLoader` | `qwen_2.5_vl_7b_fp8_scaled.safetensors`, type `qwen_image`, device `default` |
| `VAELoader` | `qwen_image_vae.safetensors` — Krea's VAE, the same bytes |
| `ModelSamplingAuraFlow` | shift 3.1 |
| `CFGNorm` | strength 1 |
| `LoraLoaderModelOnly` | the Lightning LoRA at 1.0 — only with a Lightning speed |
| `LoraLoaderModelOnly` ×n | the tab's LoRA stack, after Lightning |
| `LoadImage` ×n | one per image, in the user's order |
| `FluxKontextImageScale` | image 1, to the nearest of Kontext's ~1 MP sizes |
| `TextEncodeQwenImageEditPlus` ×2 | the prompt and the negative, each with the VAE and `image1` (scaled) … `image3` |
| `FluxKontextMultiReferenceLatentMethod` ×2 | `index_timestep_zero` on each |
| `VAEEncode` | the scaled image 1 — the starting latent, which sets the output size |
| `KSampler` | Steps, CFG, Sampler and Scheduler; denoise 1 |
| `VAEDecode`, `SaveImage` | prefix `QwenEdit2511` |

**The images are numbered, and the number is the slot.** Image *N* is
wired to `imageN` on both encoders, and only as many as there are. The
node has three inputs, which is the tab's limit, and the model card names
1 to 3 as the range it works best in. The handler turns each picture
upright, puts transparency on white and caps the longest edge at 2048,
as 🧩 Qwen 2.1 Reference does (`prepare_reference`).

**No output size.** The result takes image 1's shape, at about one
megapixel, as the template does.

**Two GPUs.** As on every other tab, a second GPU gets the text encoder
and the VAE (`SelectCLIPDevice`, `SelectVAEDevice`).

### What was left out

The template wraps the graph in a subgraph and switches Lightning with
three `ComfySwitchNode`s — one for the model, one for steps, one for CFG —
off a single "Enable 4steps LoRA?" boolean. The builder makes that choice
itself and emits only the branch it took.

## Speed: Lightning or Normal

| Speed | LoRA | Steps | CFG |
| --- | --- | --- | --- |
| ⚡ Lightning · 4 steps (default) | `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16` | 4 | 1 |
| ⚡ Lightning · 8 steps | `Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16` | 8 | 1 |
| Normal | none | the Steps slider (record default 40) | the CFG slider (record default 4) |

The Steps and CFG sliders show only for Normal. With Lightning they keep
their values but are not used, as the template's switches ignore them.
At CFG 1 the sampler ignores the negative prompt, so it only matters on
Normal. The box starts on the model record's `negative` — skeleton lines,
bad anatomy and a different person — which suits the pose-skeleton use;
change it on the record, not in code.

## Prompting a pose change

Describe the change and what must stay: "Turn the woman in image 1 around
so her back is to the camera, looking back at the camera over her right
shoulder. Keep her face, hair, outfit and the background the same." For a
pose the words do not pin down, add a pose skeleton (an OpenPose render)
as image 2 and say "in the pose from image 2".

For a camera move rather than a body pose, tick the **Multiple Angles**
LoRA in the stack and start the prompt with its trigger: `<sks> back view
eye-level shot medium shot`. Its prompt format is `<sks> [azimuth]
[elevation] [distance]`; see the model card at
`fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA`.

## The weights

| Group | File | Size |
| --- | --- | --- |
| catalogue | `qwen_image_edit_2511_fp8mixed.safetensors` (diffusion model) | ~20.5 GB |
| `qwen_edit` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` (text encoder) | ~9.4 GB |
| `qwen_edit` | the two Lightning LoRAs | ~0.85 GB each |
| `krea2` | `qwen_image_vae.safetensors` | ~0.25 GB |
| catalogue | `qwen-image-edit-2511-multiple-angles-lora.safetensors` | ~0.3 GB |

The encoder comes from `Comfy-Org/Qwen-Image_ComfyUI`'s `split_files/`,
the Lightning LoRAs from the root of
`lightx2v/Qwen-Image-Edit-2511-Lightning`, placed in `loras/`. Each file
is tried on its own: a missing encoder costs the tab, a missing Lightning
LoRA only its Speed choice, and the next start retries it. All four
upstream repos are pinned in `scripts/PINS.json`.

## The LoRA stack

Eight blank slots over the feature's own list, model-only
(`LoraLoaderModelOnly`), like the Krea2 and Ember Lite stacks, because
the Lightning LoRA and the 2511 LoRAs on CivitAI patch the model alone.
The chain sits after Lightning.

## Presets

The tab has its own preset list, filed under `qwen_edit_2511`, with the
save tickbox and no prompt-library publish. A preset carries Speed, model,
steps, CFG, sampler, scheduler, seed, randomize, batch count and the LoRA
stack — never the images. See [presets](../features/presets.md).

## Related

- [Qwen Image 2.1](qwen21.md) — 🧩 Qwen 2.1 Reference, which makes a new
  image from up to 10 references
- [Krea 2 Edit V2](krea2-edit-v2.md) — the other pose-and-scene edit tab
- [The catalogue](catalogue.md)
