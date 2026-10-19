# Anima (anime text-to-image)

The **🌸 Anima** tab writes anime illustrations from Danbooru-style tags,
a sentence, or both. For someone using the tab, and for whoever decides
who gets it.

Feature key `anima_t2i`, route `/generate/anima`. Built by
[`ember/pipelines/anima/workflow.py`](../../ember/pipelines/anima/workflow.py),
with the fixed files in
[`ember/pipelines/anima/constants.py`](../../ember/pipelines/anima/constants.py).

## Licence

Anima is CircleStone Labs' and Comfy Org's model, under the **CircleStone
Labs Non-Commercial License v1.2**, and every derivative inherits it —
BAS:NSFW, the catalogue's model, included. In short:

- Personal, non-commercial use is allowed: private entertainment, hobby
  projects, so long as no payment arises from using the model.
- Use "for revenue-generating activity" or "in direct interactions with …
  third-party end users" is not, and the right to sell derivatives
  "extends solely to the model weights, and not to any larger product".
  Selling Ember access to the tab needs a commercial licence from
  CircleStone.
- Images it makes may be used for any purpose, commercial included —
  except what the licence forbids outright, which includes child sexual
  abuse material.

So, until there is a commercial licence:

- `anima_t2i` is on **no plan**, `admin` included, and its `features` row
  is `enabled: false`, so no pricing page or plan card mentions it.
- It is granted by hand to one key, `EMBER-EDIT-ONLY`, for personal use.
- Its weights are **never mirrored**. The mirror repos are public, and a
  copy there would be distribution. The text encoder has a pin and no
  manifest item; the model record's `mirror` is null, and
  `mirror_to_hf.py` never uploads catalogue models.

Once there is a commercial licence, putting it on sale is a database
edit: add `anima_t2i` to a plan's `features`, set its `features` row to
`enabled: true`, and add a showcase block. No app build and no server
deploy beyond the one that ships this tab.

## The model

One catalogue record, `bas-nsfw-anima-v1-0`: **BAS:NSFW base-v1.0**
(CivitAI version 3043144), a merge of Anima Base v1.0, the Anima Turbo
LoRA and about 25 style and NSFW LoRAs. It is the diffusion model alone,
4.2 GB in bf16. The Turbo merge sets its recipe: **8 steps, CFG 1,
euler**, and the record's trigger words — `anime, anime coloring, anime
screenshot` — go into the prompt when it is picked. Downloading it needs
`CIVITAI_TOKEN`, like any CivitAI file now.

The official builds on `circlestone-labs/Anima` — Turbo v1.1 (CFG 1,
8–12 steps) and Aesthetic v1.1 (30–50 steps, CFG 4–5) — are the same
architecture and can join the list as further model records.

The other two files are fixed:

- `text_encoders/qwen_3_06b_base.safetensors` (1.2 GB) from
  `circlestone-labs/Anima`, pinned, fetched by the `anima` asset group;
- `vae/qwen_image_vae.safetensors` (254 MB), the Qwen-Image VAE — byte
  for byte the Krea tabs' (same SHA-256), so the feature names the
  `krea2` group too and a key with both tabs fetches it once.

Anima is a 2B Cosmos-Predict2 DiT with an LLM adapter. ComfyUI supports it
natively, the pinned v0.37.4 included, so there is no node pack and no
pin bump. It fits easily on an 8 GB card.

## The graph

The model card's example workflow, all core nodes: UNETLoader,
CLIPLoader (`stable_diffusion`, as the example has it — ComfyUI tells the
encoder apart by its weights), VAELoader, a LoraLoader chain on the model
and the CLIP, CLIPTextEncode, EmptyLatentImage, KSampler, VAEDecode and
SaveImage. An empty negative is the positive's ConditioningZeroOut; one
with text is encoded, so CFG above 1 has something to push against.

## The form

Prompt, Negative (collapsed, with the model card's recommended negative
in its hint), Output size, Model, Steps, CFG, Sampler, Scheduler, Seed &
batch, Presets, and a four-row LoRA stack over `anima_t2i`'s own list,
which is empty for now.

Sizes are about 1 MP in the usual buckets, square first: 1024×1024,
896×1152, 1152×896, 832×1216, 1216×832, 768×1344 and 1344×768. The
model card says it works from 512² to 1536².

Samplers are euler first (BAS's), then the model card's favourites:
er_sde, euler_ancestral and dpmpp_2m_sde_gpu. Schedulers are simple and
beta.

## Prompting

From the model card: lowercase tags with spaces rather than underscores
(score tags keep theirs), tag order quality → count → character → series
→ artist → general, artists prefixed with `@`, and weights higher than
SDXL's — `(chibi:2)`. Quality tags are `masterpiece, best quality, …`
and `score_9` … `score_1`; time tags are `year 2025` or `newest`, `mid`,
`old`; safety tags are `safe, sensitive, nsfw, explicit`.

## Presets

A list of its own, under `anima_t2i`, in 🌄 Qwen 2.1's shape. Saving
needs `anima_t2i` in the licence server's `PRESET_TABS`, so the server
has to be redeployed once with this change. See
[Settings presets](../features/presets.md).

## Related

- [qwen21.md](qwen21.md) — the tab this one's form is modelled on.
- [Catalogue data](../../license-validator/docs/catalogue-data.md#animas-list)
  — the model record.
