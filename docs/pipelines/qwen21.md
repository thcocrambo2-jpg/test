# Qwen Image 2.1 — a prompt and up to 10 reference images, or a prompt alone

The **🧩 Qwen 2.1 Reference** tab (`qwen21_edit`): the user adds 1 to 10
numbered reference images and writes a prompt that names them by number
("the woman from image 1 wearing the jacket from image 2"), and Qwen
Image 2.1 makes one new image that keeps them consistent — a character,
an outfit, a product. For someone using the tab, and for someone changing
the graph it builds.

The graph is built by
[`ember/pipelines/qwen21/workflow.py`](../../ember/pipelines/qwen21/workflow.py)
and driven by
[`ember/pipelines/qwen21/handler.py`](../../ember/pipelines/qwen21/handler.py);
the fixed half — the text encoder, the VAE, the two core nodes, the size
and sampler lists, the reference limits — is in
[`ember/pipelines/qwen21/constants.py`](../../ember/pipelines/qwen21/constants.py).
The diffusion model is not: it is a catalogue record,
`qwen-image-2-1-int8`, so the Model dropdown works the way Krea's and
Z-Image's do, and a bf16 record can be added later as a database edit.
See [catalogue.md](catalogue.md).

A second tab, **🌄 Qwen 2.1** (`qwen21_t2i`), is plain text-to-image on
the same model, encoder and VAE; see [Text to image](#text-to-image) at
the end. Everything above that section is about the Reference tab.

Both features are on no plan. A key gets one through `features_extra`;
see [Licensing and features](../architecture/licensing-and-features.md).

## Where the graph comes from

`qwen_image_2.1_reference_workflow.json` from Hearmeman24's
`comfyui-qwen-template`. The references are seen twice: by the Qwen3-VL
text encoder, and spliced into the sequence as VAE latents.

## The graph

Every node is core ComfyUI, and the two Qwen nodes first ship in
**ComfyUI v0.37.0** — the reason the ComfyUI pin is at v0.37.4 (see
[the mirror and the pins](../releasing/mirror-and-pins.md#the-comfyui-pin)).

| Node | Values |
| --- | --- |
| `UNETLoader` | the model record's file, weight dtype `default` |
| `CLIPLoader` | `qwen3vl_8b_int8_convrot.safetensors`, type `qwen_image`, device `default` |
| `VAELoader` | `qwen_image_2.1_vae_bf16.safetensors` |
| `LoraLoader` ×n | the tab's LoRA stack, on the model and the CLIP — see below |
| `QwenImage21Cache` | on the LoRA-patched model; device `auto`, dtype `default` |
| `LoadImage` ×n | one per reference, in the user's order |
| `TextEncodeQwenImage21` | the prompt, the negative, the VAE, `resolution` (Reference detail), `images.image_1` … `images.image_N` |
| `EmptyLatentImage` | the output size, batch 1 |
| `KSampler` | Steps, CFG, Sampler and Scheduler from the form; denoise 1 |
| `VAEDecode`, `SaveImage` | prefix `Qwen21Ref` |

The template's defaults are the tab's: **25 steps, CFG 1, `euler`,
`simple`**. Steps and CFG come from the model record, as on the Krea
tabs. The scheduler offers `simple` and `beta`, the two the template's
notes suggest. At CFG 1 the sampler ignores the negative prompt.

**The references are numbered, and the number is the slot.** Reference
*N* is wired to `images.image_N`, and only as many inputs as there are
references are wired. The node sorts its inputs by that number, so
reordering the tiles on the form is renumbering them for the model. The
names are ComfyUI's Autogrow names — the input `images`, a dot, then
`image_N` — which `/object_info` lists under the node's template. The
prompt validator ignores an input name it does not know, so a wrong name
would pass validation and quietly send no picture; linking
`images.image_1` to the wrong type is refused, which is how the names
were checked.

**Reference detail** is the encoder's `resolution` (0–4096, step 32,
default 1024): each reference is resized to about that squared, in
multiples of 32, keeping its shape. `0` keeps each reference's own size.
It sits in a closed Advanced section, because 1024 is right for nearly
everyone.

**Two GPUs.** As on every other tab, a second GPU gets the text encoder
and the VAE (`SelectCLIPDevice`, `SelectVAEDevice`), and the diffusion
model stays alone on `gpu:0`. On one GPU those nodes are not added.

### The output size

A select, defaulting to **Same as reference 1**, then the template's
resolution note:

| | |
| --- | --- |
| 1:1 | 2048×2048 |
| 4:3 / 3:4 | 2400×1792 / 1792×2400 |
| 3:2 / 2:3 | 2528×1696 / 1696×2528 |
| 16:9 / 9:16 | 2752×1536 / 1536×2752 |

A preset is used exactly. **Same as reference 1** is reference 1's shape
at about 2048² pixels, each side a multiple of 32 — the encoder's own
resize rule with 2048 in place of its `resolution`:

    width  = max(32, floor(sqrt(2048² × w/h) / 32 + 0.5) × 32)
    height = max(32, floor(sqrt(2048² × h/w) / 32 + 0.5) × 32)

A 1536×2048 photo comes to **1760×2368**; a square one to 2048×2048.
`reference_size()` in `workflow.py` is the one formula. `/catalog` sends
the browser its two numbers under `sizeFromImage`, so the line under the
select ("Output will be 1760 × 2368, the shape of reference 1") names the
size the pod will render, and changes when another image is dragged to
the front. Both sides round a half up, so the two agree on every size.

The KSampler samples that `EmptyLatentImage`, as the template does. The
encoder's own `latent` output is not used: it is sized to reference 1 at
`resolution` (1024²), not at 2048².

### What the handler does to each reference

Before uploading it to ComfyUI, in the order given:

- turns it upright from its EXIF orientation (`ImageOps.exif_transpose`);
- composites transparency onto white, which is what the encoder's vision
  tower sees anyway;
- caps the longest edge at **2048** with LANCZOS — the template's
  `max_reference_edge`. Never enlarges;
- uploads it under a name of its own, as the edit tabs do.

The tab refuses, with a message, no references, more than 10, an empty
prompt, a model, text encoder or VAE that is not downloaded, and a
ComfyUI too old to have the two Qwen nodes — after checking ComfyUI is
answering, so a restarting server is not taken for an old one. `main.py`
also logs that last case at startup (`verify_core_node`).

### What was left out

- **`QwenImageReferencePack`** (Hearmeman24/ComfyUI-QwenImageRefPack) —
  the template's upload canvas, with per-image crop, rotate and mirror and
  the edge cap. The tab is the upload UI and the handler caps the edge, so
  no node pack is installed, pinned or mirrored for this tab. Rotate,
  mirror and crop are not offered;
- **`PrimitiveStringMultiline`** — the prompt box;
- the KJNodes **`SetNode` / `GetNode`** pairs — wiring only;
- **`MarkdownNote`** — the resolution note, which is the Output size
  list;
- a **Mentions** line that reads the image numbers out of the prompt and
  flags any not added, and rewriting those numbers when the tiles are
  reordered. The prompt's numbers are the user's to keep in step.

## The reference field

The References section holds the app's one `images` field: a numbered
grid of up to 10 tiles, image 1 marked **Main**. Pictures come in by
click (several at once), drop (several files), paste, or the Recent
generations strip, where a click adds the picture as the next number.
Tiles reorder by their drag handle or with **Alt+←** / **Alt+→**, and a
click opens one full size in the gallery Lightbox. A file that is not a
picture, or one past the tenth, is named inside the field and the rest
still goes in. Generate stays disabled, with the reason under it, until
there is at least one reference.

On a wide screen the section sits in the right column above the result,
beside the prompt that names its numbers. When the page stacks it moves
to the top, under the preset bar. See [the web UI](../architecture/web-ui.md)
for the field type and the stack-first flag.

## The weights

| File | Folder | From | Size |
| --- | --- | --- | --- |
| `qwen_image_2.1_int8_convrot.safetensors` | `diffusion_models` | `Comfy-Org/Qwen-Image-2.1`, the catalogue record | 7.3 GB |
| `qwen3vl_8b_int8_convrot.safetensors` | `text_encoders` | `Comfy-Org/Qwen-Image-2.1` | 9.4 GB |
| `qwen_image_2.1_vae_bf16.safetensors` | `vae` | `Comfy-Org/Qwen-Image-2.1` | 0.7 GB |
| **total** | | | **~17.3 GB** |

The diffusion model is fetched by the `catalog` asset group like every
catalogue model; the other two by the `qwen21` group
(`download_qwen21_models`). The repo keeps ComfyUI's folder layout, so
each file lands in place. It is org-backed, so it is pinned in
`scripts/PINS.json` and fetched upstream with no mirror.

The template recommends an RTX 5090 or a PRO 6000. Plan on a card with
at least 24 GB.

## The LoRA stack

Four blank rows, all off, chained as `LoraLoader` nodes between the
diffusion model and the cache node — where the template's empty Power
Lora Loader sits. That loader patched the model **and** the CLIP, so each
row does both at one strength, like the Krea2 V2 stack, and the encoder
reads the patched CLIP. The rows offer the catalogue's list for
`qwen21_edit`, which is empty until Qwen 2.1 LoRAs are added to it; Krea
and Z-Image LoRAs do not load on this model.

## Presets

The tab has its own preset list, filed under `qwen21_edit`, with the save
tickbox and no prompt-library publish. A preset carries the output size,
model, steps, CFG, sampler, scheduler, reference detail, seed, randomize,
batch count and the LoRA stack — never the reference images. See
[presets](../features/presets.md).

## Text to image

**🌄 Qwen 2.1** (`qwen21_t2i`, route `/generate/qwen21`) writes one image
from a prompt. Its graph is `qwen_image_2.1_workflow.json` from the same
template, which is the ComfyUI form of the text-to-image half of Qwen's own
[Qwen-Image-2.1 workflow Space](https://huggingface.co/spaces/Qwen/Qwen-Image-2.1-workflow):
the same model, a prompt in and an image out, at the model card's
aspect-ratio table.

```
UNETLoader ─ LoraLoader × n ─────────────────────────── KSampler ─ VAEDecode ─ SaveImage
CLIPLoader(qwen_image) ─┘  CLIPTextEncode(prompt) ──┬── positive      │          (prefix "Qwen21")
                                                    └ ConditioningZeroOut ─ negative
VAELoader ──────────────────────────── EmptyLatentImage(output size) ─┘
```

`build_qwen21_t2i_workflow` shares its loaders and LoRA chain with the
reference graph (`_loaders`). The differences from it:

- **`CLIPTextEncode`, not `TextEncodeQwenImage21`.** With this encoder
  and `type="qwen_image"`, ComfyUI wraps the prompt in Qwen 2.1's own
  text-to-image template, so the core node is the right one. There is no
  `QwenImage21Cache`: the template has none, and it caches the references
  this graph does not have.
- **The negative is zeroed**, as in the template — unless the Negative
  prompt box has text, which is then encoded the same way, so that a CFG
  above 1 has something to push against.
- **Output size** is the template's note without "Same as reference 1",
  1:1 · 2048×2048 by default.

The form is the Reference tab's without the references and Advanced:
Prompt, Output (size and model), Sampler (steps, CFG, sampler,
scheduler), Seed & batch, Presets, and the same four-row model + CLIP
LoRA stack over `qwen21_t2i`'s own catalogue list, which is empty for
now. Steps and CFG come from the model record: 25 and 1, the template's.
Qwen's Space runs 28 steps and the model card 40, through diffusers'
scheduler; raise Steps to try those.

The Space's optional prompt rewriter (`Qwen-Image-2.1-PE-T2I`, a 9B model
of its own) is left out, and so is its image-edit half: editing with
references is the other tab.

It needs no download of its own: the `qwen21` group and the catalogue
model are the Reference tab's, so a key with both tabs fetches them once.
The handler checks the same things the Reference tab's does, apart from
the references: a prompt, the model, the two fixed files, and a ComfyUI
new enough for Qwen Image 2.1 — the graph uses no Qwen node, but the
model loads only on the release that added them, so
`TextEncodeQwenImage21` stands for the version.

Presets are a list of their own, under `qwen21_t2i`: the Reference tab's
blob without `reference_detail`.

## Related

- [catalogue.md](catalogue.md) — the model record and the LoRA list.
- [zimage.md](zimage.md) — the tab whose form and presets this one copies.
- [the Gallery and recipes](../architecture/gallery-and-recipes.md) — how
  a finished image hands its references back.
