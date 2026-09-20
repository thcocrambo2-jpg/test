# Catalogue data

The models and LoRAs the app's tabs offer, how they are stored and
validated here, and how a pod reads them. For whoever curates the
catalogue or changes the rules around it.

Which models and LoRAs the tabs offer lives here, not in the app.
Three collections, all keyed by a readable string id:

- `loras` — one record per LoRA file: its name, `file`, where it downloads
  from (`source`), our own copy (`mirror`), its `default_strength`, and
  `enabled` / `sort_order`.
- `models` — one record per model *setting*: `file`, `source`, `mirror`,
  `variant`, `steps`, `cfg` and the `turbo_lora` its recipe switches on.
  Two records may share a file.
- `feature_assets` — per feature key, the ordered model ids and LoRA ids that
  tab's dropdowns hold. The first model is the tab's default. An id may sit in
  any number of features.

**Ids are permanent.** Presets and prompts store them, so renaming one
orphans every preset that names it. Fix a label with `name`, never with
the id. To retire a record, switch it off.

## LoRA-only features

Not every tab has a Model dropdown. `LORA_ONLY_FEATURES` in
[`../src/assets.js`](../src/assets.js) is the set of feature keys that
offer LoRAs and no models:

```js
export const LORA_ONLY_FEATURES = new Set(["minimax_i2v", "minimax_t2v"]);
```

The MiniMax tabs run one fixed graph over one fixed set of weights, named
in [`../../ember/pipelines/minimax/constants.py`](../../ember/pipelines/minimax/constants.py),
and stack LoRAs on top of it. An empty `models` list is what those tabs
*are*, not a tab with nothing to run.

`emptyModelsProblem(feature, list)`, in the same module, is the rule that
encodes it: an empty `models` array is an error for any other feature and
fine for these two. A missing or malformed list is not its business —
`idListProblems()` reports that. Every path that can set a feature's model
list goes through it: `POST /v1/admin/feature-assets`, `npm run assets`
and `npm run seed-assets`.

## What a pod reads

`POST /v1/catalog` is what a pod reads, once, at startup — after its seat is
taken and before it downloads anything. It answers in exactly the shape of
[`../data/assets.json`](../data/assets.json):

```js
{ ok: true,
  loras:  [{ id, name, file, source, mirror, default_strength, trigger }],
  models: [{ id, name, file, source, mirror, variant, steps, cfg, turbo_lora, trigger }],
  features: { krea_t2i: { models: ["krea2-turbo-mxfp8"], loras: ["krea2-turbo", ...] }, ... } }
```

Enabled records only, LoRAs in `sort_order`, and each feature's lists cut
down to the ids in the same answer, order kept. A stored record that fails
validation (only a hand edit in Atlas can make one) is skipped and logged,
never sent — `GET /v1/admin/assets` shows it with its `problems`. It needs a
valid licence, checked exactly like `/v1/acquire` (`403` on a bad key,
`503` on a database failure — the pod then falls back to the copy it saved
last time). Nothing reaches a running pod: an edit is picked up on its next
start.

## Validation rules

Every write goes through [`../src/assets.js`](../src/assets.js); the pod
applies the same rules again on read, in
[`../../ember/licensing/catalog.py`](../../ember/licensing/catalog.py).

| Field | Rule |
| --- | --- |
| id | `^[a-z0-9][a-z0-9-]{0,62}$` |
| `file` | `^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$` — a bare file name, no directories |
| `source` | `{kind: "civitai", version: <positive int>}` or `{kind: "hf", repo: "owner/name", path}` |
| `mirror` | `{repo: "owner/name", path}` or `null` |
| paths | non-empty, no leading `/`, no `..` segment |

A field outside a record's known set is refused rather than stored, so a
typo cannot become a setting that silently does nothing. `loras.file` is
unique; `models.file` deliberately is not, because two model records
sharing a file is the normal way to offer the same weights at two settings.
A feature's list refuses duplicates rather than collapsing them: a list is
a dropdown in order, and an id twice in it is a mistake about that order.

## Editing the catalogue

```bash
npm run seed-assets                          # data/assets.json → the three collections
npm run seed-assets -- --dry-run
npm run assets                               # list everything (--loras | --models | --features)
npm run assets -- --disable-lora realism-v2  # --enable-lora, --enable-model, --disable-model
npm run assets -- --feature krea_t2i --add-lora pawg --at 3
npm run assets -- --feature krea_t2i --remove-lora pawg
npm run assets -- --feature krea_v2_t2i --add-model krea2-raw-fp8 --at 1   # position 1 = default
npm run assets -- --mirror realism-v2 --repo owner/name --path loras/x.safetensors
```

`seed-assets` validates the whole file before writing anything, and is
idempotent. What a record *is* is `$set`, so editing it in the file and
re-running updates it; `enabled` and `sort_order` are `$setOnInsert`, so a
record you switched off stays off. A `mirror: null` in the file leaves a
stored mirror alone. Feature lists are replaced whole from the file. It
never deletes.

The mirror script records where it uploaded a LoRA with
`POST /v1/admin/loras` `{id, mirror: {repo, path}}` — a partial update that
changes nothing else about the record. Put the same value in
`data/assets.json`, or the next `seed-assets` run will overwrite it with the
file's.

## Settings blobs

Presets and prompts store a tab's settings, and every model and LoRA in
them is **an id**, never a file name or a label:

```js
// krea_t2i — eight positional slots
{ model: "krea2-turbo-mxfp8", steps: 10, cfg: 1.0, resolution: "...", sampler: "er_sde",
  seed: 42, randomize: true, batch_count: 1,
  loras: [[true, "hmbody-d-e10", 0.8], [false, null, 0.8], ...] }

// krea_v2_t2i — positional too: row i fills the tab's slot i
{ model: "krea2-turbo-mxfp8", aspect: "...", megapixels: 1.5, multiple: 8, ...,
  sampler: { ... }, variance: { ... }, sharpen: false, film_grain: false,
  loras: [[false, "krea2-turbo", 0.6], [true, "filter-bypass-3", 0.93], ...] }
```

Every LoRA row is a triple `[on, lora id | null, weight]`. An empty slot is
`null`; the form calls it "None", but that word never reaches storage. The
server refuses (`400`) a blob whose `model` is not an existing model id or
whose `loras` holds anything else. Disabled records still count as
existing — a preset naming one is not wrong, the tab just leaves that slot
empty while it is off. The rest of the blob belongs to the tabs and is only
bounded in size.

`seed-presets` and `seed-prompts` apply the same check, so **run
`seed-assets` first** — against an empty catalogue they refuse every row.
