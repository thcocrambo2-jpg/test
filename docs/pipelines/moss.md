# MOSS

🌿 MOSS (`moss_t2i`, route `/generate/moss`) is a text-to-image tab for
OpenMOSS's **MOSS-Image**: a 5B single-stream DiT on a Qwen3.5-9B text
encoder and the FLUX.2 VAE. It reads long, detailed prompts well (the
model takes up to about 4K tokens) and does photographs and illustrations
in the same model. Weights, encoder and VAE are all Apache 2.0.

It is on no plan yet: the features row is `enabled: false`, and a key gets
the tab by being granted `moss_t2i` directly.

## The graph

Every node is core ComfyUI. MOSS-Image first ships in **ComfyUI v0.42.0**
(`MOSS_COMFYUI_MIN`), which is why the ComfyUI pin moved to v0.46.2 (see
[the mirror and the pins](../releasing/mirror-and-pins.md#the-comfyui-pin)).
The handler and `main.py` stand `MOSSImageLayoutPrompt` in for the
release, since the CLIPLoader type cannot be asked about directly.

| Node | Values |
| --- | --- |
| `UNETLoader` | the model record's file, weight dtype `default` |
| `CLIPLoader` | `qwen3.5_9b_int8_convrot.safetensors`, type `moss_image`, device `default` |
| `VAELoader` | `flux2-vae.safetensors` |
| `LoraLoaderModelOnly` | Fast only: `moss_image_sft_dmd_8step_lora.safetensors` at 1.0 |
| `LoraLoaderModelOnly` × n | the LoRA stack, model only |
| `CLIPTextEncode` | the prompt |
| `CLIPTextEncode` / `ConditioningZeroOut` | the negative — zeroed at CFG 1, encoded (even empty) otherwise |
| `EmptyFlux2LatentImage` | the output size, batch 1 |
| `KSampler` | steps, CFG, sampler, scheduler, denoise 1.0 |
| `VAEDecode` → `SaveImage` | prefix `MOSS` |

The model's shift of 5 is its own sampling setting in ComfyUI
(`supported_models.MOSSImage`), so no node sets it. The reference
pipeline's skip-layer guidance ("uncond skip" on layer 22) is left out:
ComfyUI's `SkipLayerGuidanceDiT` is a different technique, and
approximating it would change pictures without matching the original.

On two GPUs the encoder and VAE move to `gpu:1`, as on every other tab.

## Speed

| Speed | What runs |
| --- | --- |
| Normal (steps and CFG below) — default | the Steps and CFG sliders, 50 and 3 from the model record |
| ⚡ Fast · 8 steps | lodestones' 8-step DMD LoRA at strength 1, 8 steps, CFG 1 |

Fast is lodestones' data-space DMD distillation of MOSS-Image-SFT
(`lodestones/MOSS-Image-SFT-8-steps-dmd`, `lora.safetensors`, renamed into
`loras/`). Its model card says to load it at strength 1 in ComfyUI's LoRA
loader; with Fast picked the Steps and CFG sliders hide and are ignored.

Measured on an RTX 5050 laptop (8 GB), int8, 1024×1024, model load
included:

| torch | Normal (50 steps) | Fast (8 steps) |
| --- | --- | --- |
| 2.8.0 / cu128 (the Windows path) | 337 s (6.7 s/step) | 45 s |
| 2.14 / cu130 | 92 s (1.6 s/step) | 26 s |

The difference is comfy-kitchen: below CUDA 13 ComfyUI switches its CUDA
backend off, so int8 convrot weights run on eager kernels. The Docker
image's torch 2.11/cu130 keeps the fast path.

## The form

Prompt, and a collapsed negative (the model record's: `worst quality, bad
anatomy, watermark, jpeg artifact`, the user guide's line without its
`nsfw` tag). Output size, Speed and Model. Steps and CFG (Normal only),
sampler (`euler` first) and scheduler (`simple` first). The ⚡ Ember Lite
refine section, as on the other photo tabs. Seed, batch, presets, and the
plain eight-row model-only LoRA stack over the feature's own list (empty
for now).

Sizes are about 1 MP with each side a multiple of 16, as the user guide
asks — 1024×1024, 896×1152, 1152×896, 832×1248, 1248×832, 768×1360,
1360×768 — plus 1440×1440 at 2 MP.

## Weights

| File | From | Size |
| --- | --- | --- |
| `diffusion_models/moss_image_sft_int8_convrot.safetensors` | catalogue record `moss-image-sft-int8` (`Comfy-Org/MOSS-Image`) | 5.4 GB |
| `text_encoders/qwen3.5_9b_int8_convrot.safetensors` | `Comfy-Org/MOSS-Image`, the `moss` group | 9.7 GB |
| `vae/flux2-vae.safetensors` | `Comfy-Org/MOSS-Image`, the `moss` group | 0.3 GB |
| `loras/moss_image_sft_dmd_8step_lora.safetensors` | `lodestones/MOSS-Image-SFT-8-steps-dmd`, the `moss` group | 1.2 GB |

Comfy-Org's repack keeps its files at the repo root under ComfyUI's own
folder names, so each lands at the same relative path. Both repos are
pinned in `PINS.json` and listed under `pin_upstream`. Every item is
independent: a missing encoder or VAE costs the tab, a missing LoRA only
Fast, and the next start retries it.

The same repo has a bf16 model (10.2 GB), Base variants, a w4a8 encoder,
and edit and colour/depth control LoRAs. Another model is a catalogue
record added to `moss_t2i`'s list; nothing in the binary changes.

## Presets

🌿 MOSS keeps a list under `moss_t2i`, in Anima's shape plus `speed` and
the refine block. Saving needs `moss_t2i` in the licence server's
`PRESET_TABS`, so the server must be redeployed first. See
[presets](../features/presets.md).
