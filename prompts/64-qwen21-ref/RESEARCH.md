# Qwen Image 2.1 reference editing: research notes

Research done 2026-09-27 on branch `63-zimage` (tip `11739bb`). This file is
input for the implementation prompt that follows the UI mockup. It is not a
prompt itself.

Source: `Hearmeman24/comfyui-qwen-template` at `858888f`,
`workflows/Qwen Image 2.1/qwen_image_2.1_reference_workflow.json`. A copy is
committed next to this file:
**`prompts/64-qwen21-ref/reference/qwen_image_2.1_reference_workflow.json`**.

## What the workflow does

"Keep your AI character consistent: add up to 10 reference images and a
prompt." A text prompt plus 1–10 reference images produces one new image.
The references are seen twice: by the Qwen3-VL text encoder, and spliced
into the sequence as VAE latents.

## The graph (node id: what it is)

| Id | Node | Values / wiring |
| --- | --- | --- |
| 1 | `UNETLoader` | `qwen_image_2.1_int8_convrot.safetensors`, `default` |
| 13 | `CLIPLoader` | `qwen3vl_8b_int8_convrot.safetensors`, type `qwen_image`, `default` |
| 4 | `VAELoader` | `qwen_image_2.1_vae_bf16.safetensors` |
| 34 | `Power Lora Loader (rgthree)` | empty. It maps onto the app's LoRA stack. It patches **model and clip** (V2-style), not model-only |
| 48 | `QwenImage21Cache` (core) | `device=auto`, `dtype=default`, on the model after the LoRAs |
| 37 | `QwenImageReferencePack` (custom) | the upload widget; outputs `image_1`…`image_10`, `max_reference_edge 2048` |
| 47 | `PrimitiveStringMultiline` | the prompt |
| 39 | `TextEncodeQwenImage21` (core) | `prompt`, `negative_prompt ""`, `resolution 1024`, `vae`, `images.image_1..10`; outputs positive, negative, **latent** |
| 10 | `EmptyLatentImage` | **2048 × 2048**. The KSampler uses this, not node 39's latent output |
| 2 | `KSampler` | **25 steps, CFG 1, `euler`, `simple`, denoise 1** |
| 3 → 15 | `VAEDecode` → `SaveImage` | |
| 40/41/43/46 | `SetNode`/`GetNode` (KJNodes) | wiring only; leave them out |

The resolution note in the workflow gives 1:1 2048×2048, 4:3 2400×1792,
3:4 1792×2400, 3:2 2528×1696, 2:3 1696×2528, 16:9 2752×1536 and
9:16 1536×2752.

The template's catalogue settings suggest: GPU RTX 5090 / PRO 6000; sampler
`euler`; scheduler `beta` or `simple`; steps 25–40; CFG 1.

## The two core nodes (ComfyUI master, `comfy_extras/nodes_qwen.py`)

**`TextEncodeQwenImage21`** takes:

- inputs `clip`, `prompt`, `negative_prompt`, optional `vae`;
- `resolution` (int, default 1024, 0–4096, step 32);
- an **Autogrow** `images` input: `image_1` … `image_16`, minimum 0.

How it works:

- Each reference is resized to about `resolution²` pixels, keeping its
  aspect ratio, in multiples of 32. `0` keeps each reference's native size.
- The text encoder sees RGB with alpha composited over white; the VAE
  encodes it, and the result is attached as `reference_latents`.
- The `latent` output is an empty latent at **reference 1's resized size**.
  Its tooltip says sampling at any other size "shifts the edit". The
  template ignores this and samples at 2048².
- With zero references it is plain text-to-image.

**`QwenImage21Cache`** takes `model`, `device` (auto/gpu/cpu/off) and
`dtype` (default/int8/int4). It sets the KV-cache options and is marked
experimental.

Order matters: images are sorted by number, and reference 1 decides the
latent size of node 39's output.

## ⚠️ Blocker: the ComfyUI version

- The app pins **ComfyUI v0.34.0** (`scripts/PINS.json`, `12d5279`).
- `TextEncodeQwenImage21` and `QwenImage21Cache` first appear in
  **v0.37.0**. They are not in v0.36.0, even though the workflow JSON says
  `ver 0.36.0`. The latest tag is v0.37.4.
- The Qwen 2.1 model (`comfy/ldm/qwen_image21/`) and the int8 "convrot"
  weight format are new too.

So this feature **needs a ComfyUI pin bump to ≥ v0.37.0**, which affects
every tab. The implementation has to:

- re-validate every golden against the new ComfyUI;
- check that MiniMax (`MINIMAX_COMFYUI_MIN v0.34.0`), Wan, Krea2, V2 and
  Z-Image still pass `/prompt` validation;
- check the torch/CUDA requirements of the new release against the Docker
  image;
- rely on `repin_checkout()` to move existing volumes.

A small real render on the local RTX 5050 with the Krea weights in `tmp/`
is a cheap sanity check. Treat the bump as its own commit, verified before
the feature lands.

## The custom node pack: skip it

`Hearmeman24/ComfyUI-QwenImageRefPack` ("Qwen Image References Manager")
is a canvas UI inside ComfyUI:

- up to 10 uploads;
- crop, rotate and horizontal mirror per image;
- a 5×2 grid in socket order;
- `max_reference_edge` (2048) downscaling of the longest edge;
- outputs `image_1..10`, with `None` for empty slots.

Almost all of that is UI, and the app has its own UI. The pod can do the
rest itself, just as `generate_edit` already resizes with PIL:

- apply rotate and mirror (and crop, if it's added) with PIL;
- cap the longest edge at 2048;
- upload each file with `client.upload_image`;
- wire one core `LoadImage` per reference into
  `TextEncodeQwenImage21.images.image_N`.

That means **no node pack, no pin and no mirror tarball.** The graph is
all core except `LoadImage`, which is also core.

## Weights (≈ 17.4 GB)

| File | Dir | Source | Size |
| --- | --- | --- | --- |
| `qwen_image_2.1_int8_convrot.safetensors` | diffusion_models | HF `Comfy-Org/Qwen-Image-2.1`, `diffusion_models/…` | 7.3 GB |
| `qwen3vl_8b_int8_convrot.safetensors` | text_encoders | HF `Comfy-Org/Qwen-Image-2.1`, `text_encoders/…` | 9.4 GB |
| `qwen_image_2.1_vae_bf16.safetensors` | vae | HF `Comfy-Org/Qwen-Image-2.1`, `vae/…` | 0.7 GB |

All three are org-backed, so they need a pin but no mirror. The diffusion
model can be a catalogue model record, as with Krea and Z-Image, so a bf16
variant could be added later as a DB edit.

## How it would fit the app

- **Feature key**: `qwen21_edit` (following `krea_edit`). Tab `🧩 Qwen 2.1
  Reference`, `category="edit"`, route `/edit/qwen21-reference`. The key is
  permanent once it ships, so confirm it with the user.
- **The new UI piece**: a **multi-image reference field**. Today's schema
  has only a single-image field (`type: 'image'`, rendered by
  `ImageDropField` in `webui/src/components/fields/index.tsx`, which
  handles click, drop, paste, Remove/Replace and the recent-outputs strip).
  The Krea2 Edit tab fakes two images with `_two_image_fields`. Ten
  separate fields would be unusable, so this needs a new field type (for
  example `images`, max 10). It has to cover the form, submit
  (`webui/src/api/http.ts` multipart), the schema model
  (`ember/web/schema/model.py`), tabschema's signature assertion (a list
  argument, not ten positional ones), presets/recipes, and the gallery's
  "reuse settings", which re-fetches image fields
  (`Lightbox.tsx` ~l.530).
- **Controls, from the workflow**:
  - prompt; negative (hidden behind the CFG > 1 hint, as elsewhere);
  - references (1–10);
  - output size: the workflow's aspect presets (default 2048×2048), plus a
    "same as reference 1" option, which is what the node itself
    recommends;
  - reference detail: `resolution`, default 1024, advanced;
  - steps 25; CFG 1; sampler euler; scheduler simple/beta;
  - seed and batch; model dropdown; LoRA stack (model + CLIP, like V2);
    presets.
  - `QwenImage21Cache` stays fixed at auto/default.
- **Per-reference edits** that the RefPack offers (rotate, mirror, crop)
  are optional. The mockup shows them so the user can decide.
