# Video with sound (MiniMax H3)

Two tabs on one model. **🎥 MiniMax I2V** animates an uploaded image and
**🎞️ MiniMax T2V** builds the clip from the prompt alone, and both come
back **with a soundtrack**. For someone using the tabs, and for someone
changing the graph.

MiniMax H3 is a packed audio+video DiT: one sampler pass produces the
frames and the audio together. That is the difference from Wan, and it is
why the prompt on these tabs describes what things *sound* like as well
as how they move.

Feature keys `minimax_i2v` and `minimax_t2v`. Built by
[`ember/pipelines/minimax/workflow.py`](../../ember/pipelines/minimax/workflow.py),
with the model facts in
[`ember/pipelines/minimax/constants.py`](../../ember/pipelines/minimax/constants.py).

## One builder, two tabs

The core `MiniMaxH3ImageToVideo` node takes an **optional** first frame.
That is the whole reason one builder serves both tabs: text-to-video is
the same graph with `LoadImage` left out. The two field lists are the
same list with `image` swapped for `aspect`.

## The download

Both features share one asset group, so granting both downloads the
weights once — and it is the largest download in the app:

| File | Size |
| --- | --- |
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` — the diffusion model, int8 | ~21 GB |
| `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` — the 32B Qwen3-VL text encoder, int8 | ~27 GB |
| video VAE (fp16) and audio VAE (fp32) | ~5.8 GB |
| `minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` — the turbo LoRA | ~2 GB |
| **total** | **~56 GB** |

## The graph

Transcribed from the two "Custom Prompt" workflows in
`hearmeman/comfyui-minimax-template:v8`, the way the V2 graph was. Every
node used is core ComfyUI — `UNETLoader`, `CLIPLoader(type="minimax")`,
`VAELoader` ×2 (video and audio), `LoraLoaderModelOnly`,
`MiniMaxH3ImageToVideo`, `BasicGuider`, `RandomNoise`, `KSamplerSelect`,
`BasicScheduler`, `SamplerCustomAdvanced`, `VAEDecode`, `VAEDecodeAudio`,
`CreateVideo` and `SaveVideo` — so **no node pack is installed** for this
feature.

Three of the template's nodes were left out on purpose:

- **KJNodes' `ModelPreviewOverrideKJ`** — a latent-preview override,
  purely cosmetic.
- **`VHS_VideoCombine`** — `CreateVideo` takes an `audio` input, and it
  plus `SaveVideo` is what the Wan tab already writes MP4s with.
- **`ExtendIntermediateSigmas`** — the template bypasses it (`mode: 4`).
  It belonged to the old 4-step turbo LoRA and was switched off when the
  template moved to the 8-step one used here; running it would add two
  sampler steps the LoRA was not tuned for.

There is **no negative prompt and no CFG**: like Flux, the model is
guidance-distilled and runs through a `BasicGuider`. The 8-step turbo
LoRA is always on at 0.8 — the template titles it "Always On, Don't
Touch" — and the sampler block is that LoRA's recipe: `BasicScheduler`
`simple`, euler, no guidance.

## The LoRA stack

The template puts a character LoRA in rgthree's Power Lora Loader. Here
that became a chain of core `LoraLoaderModelOnly` nodes — the same
translation the V2 tab makes of it, which is why the pack is not
installed. The chain sits exactly where the template has the loader:
**between the diffusion model and the turbo LoRA**, so the turbo LoRA is
always last.

The template also routes CLIP through its loader, but a character LoRA
carries no text-encoder weights, so a model-only chain is the same graph.

On screen the stack is **eight blank rows**, like 🎨 Krea2's — not one
row per LoRA the way the V2 tabs do it. Each row's dropdown offers the
LoRAs the catalogue lists for that tab's feature. The rows are built
off; the tabs' `Default` preset, applied on load, is what switches the
usual ones on. Slots left at `None` drop out of the chain.

These are the only tabs whose catalogue entry lists **LoRAs and no
models**: their weights are fixed in `constants.py`, so there is no Model
dropdown. The licence server permits the empty `models` list through
`LORA_ONLY_FEATURES` — see [catalogue.md](catalogue.md#tabs-with-loras-and-no-models).

The shipped LoRA records name a Hugging Face `mirror` alongside their
CivitAI `source`, so the mirror is tried first at the pinned revision and
CivitAI is the fallback at the same revision. Most CivitAI downloads need
`CIVITAI_TOKEN`.

## Presets

Both tabs share **one preset list**, filed on the licence server under
`minimax_i2v` (`presets.TAB_MINIMAX`). The two tabs run one model over one
LoRA list, so a stack that works on one works on the other — the way
✨ Krea2 Edit reads 🎨 Krea2's presets. Unlike the Edit tabs, both are
generation tabs, so both read the list *and* can save to it (admin only).

A preset carries steps, resolution, duration, sampler, seed, batch count
and the LoRA stack — no `model`, since these tabs have none. One saved
from 🎞️ MiniMax T2V also carries its aspect ratio, which 🎥 MiniMax I2V
skips (the image sets its shape). Its Match image resolution is likewise
left alone on T2V, which does not offer it.

The `Default` preset switches on `hmnsfw-aio-v2-5`, `vgna`, `hmbrst`,
`hmpenis-v2-0` and `humanmotion-v1-0` at 0.5. See
[presets](../features/presets.md).

## What the tabs expose

Steps (default 8), Duration 5–15 s, Sampler, Batch count, and a
Resolution radio.

- ***Standard*** (the default) is the template's 0.7 MP
  `ResolutionSelector`.
- ***Native 768p*** is the model's own canvas: a 768 short edge capped at
  768×1344 — what the node file calls `adapt_canvas`.

I2V takes the aspect from the uploaded image; T2V offers
`ResolutionSelector`'s eight aspect ratios. Standard and Native round and
clamp each side to a multiple of 32 on its own, so the canvas is usually
1–2% off the picture's shape (a panorama far more), and the node
stretches the picture to fit.

I2V also offers an opt-in third rule, ***Match image***: the upload's own
size in 32s, chosen to keep its shape, scaled down only when it is bigger
than 768×1344's area — never up, and the rounding can land a few percent
over, about 1.1 MP at most. The upload is centre-cropped to that exact
shape first, typically by under 1%, so nothing is stretched. 1080×1920
renders at 768×1376, and 720×1280 at 736×1312.

Duration snaps to the model's **17k+5** frame grid at 24 fps: 5 s is 124
frames and 15 s is 362, which is the trained range.

Clips are saved as MP4 under `output/minimax/` and appear in the Gallery
like Wan's.

## Auto prompt

Both tabs carry an **Auto prompt** panel above the Prompt group: tick it,
type a short idea, press **Write prompt**, and an LLM on OpenRouter writes
the full prompt into the Prompt box — numbered shots with timestamps,
camera and lens, expressions, the soundscape and the music. It only
writes; the clip is still made by the tab's own button, after the prompt
has been read and edited. Tick **Generate when ready** and the prompt is
queued the moment it arrives instead.

**Copy LLM prompt** puts the exact text Write prompt would send — the
system prompt, then the size block and the idea — on the clipboard
without calling anything, for somebody who would rather ask their own
chat LLM and paste the answer back. On I2V they attach the start image
themselves; the text cannot carry it.

It is the template's "Auto Prompt" workflows, taken out of ComfyUI. The
template runs an OpenRouter node in a subgraph ahead of the text encoder;
here the app makes the same call itself, in
[`ember/pipelines/minimax/autoprompt.py`](../../ember/pipelines/minimax/autoprompt.py),
so the graph is still the "Custom Prompt" one and nothing about a render
changes. The request is the node's: its system prompt word for word,
`Duration / Width / Height` then the idea, the start image on I2V,
temperature 1, 4096 tokens. Width and height are the canvas the clip will
really render at, from the same `resolve_size`/`aspect_size` the handler
calls.

Two models, chosen per tab:

| Model | | |
| --- | --- | --- |
| Qwen 3.8 27B (`qwen/qwen3.8-27b:free`) | **free**, the default | Shared by every free user on OpenRouter, so it refuses as busy (HTTP 429) for a while before it answers. |
| Gemini 3 Flash (`google/gemini-3-flash-preview`) | paid, about $0.002 a prompt | The template's model. Answers in seconds. |

Both need an OpenRouter key; a free model just does not charge it. The
key box arrives filled in with `OPENROUTER_API_KEY` when the pod has it
set (see [configuration](../configuration.md)), and a pasted key wins
over it for as long as the page is open. Nothing is kept in the browser.

**Why it is a background task.** In testing, every free call was refused
two to eight times, a minute apart, before one got through, and the call
itself then took 40 seconds to 5 minutes. A request held open that long
does not survive a Cloudflare tunnel, so `POST
/api/v1/tabs/<tab>/autoprompt` answers at once with a task id, a thread
does the calling and retrying — up to 10 calls, 60 s apart, 360 s each —
and the page polls `GET …/autoprompt/<id>` once a second. The panel shows
the countdown to the next try. The polling lives in a store outside the
panel (`webui/src/store/autoprompt.ts`), so looking at another tab while
it waits loses nothing: the prompt still lands in the right tab, and
Generate when ready still queues it.

The routes are mounted from `routes/tabs.py`'s per-tab loop, behind the
same gate as the tab's others, so a licence without the tab gets a 403.

## This feature moved the ComfyUI pin

The MiniMax nodes ship in ComfyUI **v0.34.0**, so `scripts/PINS.json`
pins `12d52794`, the v0.34.0 tag. Three things follow:

- `ember.comfy.setup.install_comfyui()` moves an *existing* checkout to
  the pin when its HEAD differs (`repin_checkout`), so a pod volume or a
  Windows install that already holds ComfyUI picks the new revision up on
  its next start instead of keeping an old one for ever. It fetches
  exactly the pinned commit and never fails startup: if the fetch cannot
  happen the app runs on what it has, and
  `ember.comfy.server.verify_core_node` logs which tab that costs.
  `MINIMAX_COMFYUI_MIN` is the version it reports against.
- The move was checked before it was made. Under v0.34.0 the four pinned
  node packs (RES4LYF, RBG Smart Seed Variance, post-processing,
  Krea2Edit) all import without error, and every existing tab's golden
  workflow passes its prompt validator unchanged. `scripts/golden.py
  --check` is byte-identical before and after.
- What was **not** checked is a GPU render on v0.34.0: no machine here
  holds these models. The first pod start after this lands is the test of
  that.

## Why they never ride the parallel instance

Both tabs run on the **main** ComfyUI instance whatever
`EMBER_WAN_PARALLEL` says. The int8 model plus the 32B text encoder is
about 48 GB of weights, which does not fit beside a second instance
holding VRAM back for Wan on a 48 GB card.

The template runs ComfyUI with `--disable-dynamic-vram`. This app does
not change its ComfyUI arguments for one tab, so if a MiniMax render
fails to allocate on a card that runs the template fine, that flag is the
first thing to try.

## Related

- [catalogue.md](catalogue.md) — the LoRA records and the LoRA-only rule.
- [wan.md](wan.md) — the other video pipeline.
