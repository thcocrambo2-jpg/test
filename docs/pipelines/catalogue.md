# Models and LoRAs (the catalogue)

Which diffusion models and style LoRAs each tab offers. For someone
running the app who wants to add or remove one, and for someone changing
the code who needs to know where those lists come from.

The Krea diffusion models and the style LoRAs are **not compiled into the
binary**. They live in the licence-server database, and the pod reads them
at startup. The database side — the collections, the admin CLI, the
validation rules — is documented in
[Catalogue data](../../license-validator/docs/catalogue-data.md); this page is the pod's half.

## The three collections

- **`models`** — one record per selectable model, keyed by a readable id
  (`krea2-turbo-mxfp8`): its name, file, download source (a CivitAI
  version or a Hugging Face repo path), optional mirror, `variant`,
  `steps`, `cfg`, an optional `turbo_lora` (the LoRA id and strength its
  recipe switches on) and optional `trigger` words.
- **`loras`** — one record per LoRA (`realism-engine-v3-1`): name, file,
  source, mirror, `default_strength`, `trigger` and a `sort_order`.
- **`feature_assets`** — per feature, the ordered model ids and LoRA ids
  that tab offers. The first model is the tab's default. An id may appear
  in any number of features.

Six features have a `feature_assets` entry: `krea_t2i`, `krea_edit`,
`krea_v2_t2i`, `krea_v2_edit`, `minimax_i2v` and `minimax_t2v`.

## Tabs with LoRAs and no models

The two MiniMax tabs list LoRAs and leave `models` empty. Their weights
are fixed — the diffusion model, the text encoder, the two VAEs and the
turbo LoRA are named in
[`ember/pipelines/minimax/constants.py`](../../ember/pipelines/minimax/constants.py),
not chosen from a dropdown — so there is nothing for a Model dropdown to
offer and the tab renders without one.

The licence server would otherwise refuse an empty `models` list, because
for every other tab an empty list means a tab that cannot run anything.
`LORA_ONLY_FEATURES` in the server's `src/assets.js` holds
`minimax_i2v` and `minimax_t2v`, and `emptyModelsProblem` skips its rule
for exactly those two. Only an *empty array* is waived: a missing or
malformed list is still reported, on every feature.

See [minimax.md](minimax.md) for what those LoRAs do in the graph.

## How the pod reads it

[`ember/licensing/catalog.py`](../../ember/licensing/catalog.py) fetches
all three collections in one `POST /v1/catalog`, once, right after the
licence check and before any download. The answer is then frozen for the
life of the process: downloads, dropdowns and the V2 tabs' slot count are
all derived from it, and a list that changed under a running pod would
put those out of step with each other.

Sources, in order:

| Source | When |
| --- | --- |
| `KREA2_CATALOG_FILE=<json>` | a file in the same shape, instead of asking the server — what the dry run and the checks use |
| `POST /v1/catalog` | the live answer, saved to `BASE_DIR/.catalog.json` |
| `.catalog.json` | the last live answer, when the server does not reply |

With none of them the catalogue is empty: the Krea tabs report that they
have no models, the MiniMax tabs offer no LoRAs but still run, and the
tabs that do not read it are unaffected.

`license-validator/data/assets.json` is the seed document and also the
shape of the catalogue answer, which is why it doubles as the offline
catalogue for `scripts/dryrun.py` and `scripts/golden.py`.

The module is stdlib-only on purpose: it runs before the pip install that
the heavier modules wait for.

## The rules that follow from this

- **Adding or removing a model or LoRA is a database edit and a pod
  restart.** No rebuild, no new binary.
- **Ids are the contract.** Presets, prompts and every stored form value
  carry the id, never the file name. The only place an id becomes a file
  name is when a download or a ComfyUI graph needs the name on disk
  (`Lora.file`, `Model.file`). Never rename or reuse an id.
- **A tab offers exactly its feature's list.** A file sitting in
  `models/loras/` that the catalogue does not list is never offered. A
  listed model or LoRA whose file has not downloaded stays in the list
  and is reported as not downloaded; a run that picks it refuses (model)
  or skips it with a warning (LoRA), and never submits an unresolvable
  name to ComfyUI.
- Every file downloads **once**, however many features list it.

## Picking a model

Choosing a model resets the Steps and CFG sliders to the defaults on its
record, and, if the record carries trigger words, inserts them into the
prompt box — visible and editable, never appended silently, so delete
them if you do not want them. A model whose download failed shows a
warning under the dropdown and refuses to run, without affecting the
others.

Because steps and CFG belong to the model record, they are deliberately
not duplicated in any pipeline's `constants.py`: the record is the one
source of truth for them.

## The two shapes of LoRA stack

| | Krea2, Krea2 Edit, MiniMax I2V, MiniMax T2V | Krea2 V2, Krea2 V2 Edit |
| --- | --- | --- |
| rows | `MAX_LORA_SLOTS` blank slots (currently 8), all empty | one row per LoRA in the feature's list, in that order |
| starting state | every slot `None` and off | every row off, at its record's `default_strength` |
| what a row offers | the feature's whole LoRA list | that row's own LoRA |
| chain | `LoraLoaderModelOnly` (diffusion model only) | `LoraLoader` (model **and** CLIP) |

`MAX_LORA_SLOTS` in
[`ember/generation/handlers.py`](../../ember/generation/handlers.py)
drives the rows, the handlers and the workflow chain together, so
changing that one constant is the whole change: the handlers take their
slots as a variadic tail and the builders already loop over the resolved
list. Slots left at `None` drop out of the graph.

To keep a tall stack from eating the column, only the first
`VISIBLE_LORA_SLOTS` (3) rows are shown; the rest sit in a collapsed
**"➕ N more LoRA slots"** accordion, which opens on load if any hidden
slot is already in use, so an active LoRA is never invisible. The nesting
is purely visual — the slot list stays flat and ordered. Set
`VISIBLE_LORA_SLOTS >= MAX_LORA_SLOTS` to show every slot and skip the
accordion.

The V2 tabs' rows are described under
[krea2-v2.md](krea2-v2.md#lora-stack); the MiniMax chain under
[minimax.md](minimax.md#the-lora-stack).

## Where the files come from

A record's `source` is a CivitAI version or a Hugging Face repo path, and
its optional `mirror` is a Hugging Face repo of your own. The mirror is
tried first, at the pinned revision, and the upstream source is the
fallback at the *same* revision —
[`ember/weights/mirror.py`](../../ember/weights/mirror.py) is the policy
and `ember/weights/downloads.py` the transport. Most CivitAI files need
`CIVITAI_TOKEN`.

## Related

- [presets](../features/presets.md) and
  [prompt library](../features/prompt-library.md) — both store catalogue
  ids, and both check them against what this build offers before applying
  anything.
- [Licensing and features](../architecture/licensing-and-features.md) — which features a licence
  grants in the first place.
