# Data

Every collection this service stores, field by field, with the reasoning
behind the shapes that are easy to get wrong. For whoever reads or edits
the database directly.

## `licenses`

```js
{ key: "EMBER-XXXX-XXXX-XXXX", name: "Acme Corp", seats: 2,
  active: true, expires_at: ISODate | null,
  plan_id: "creator" | null,          // the normal route
  features: [...] | null,             // literal override; wins over plan_id
  features_extra: ["wan_i2v"] | null, // granted on top of the plan
  is_admin: false,                    // a role, not an entitlement
  created_at: ISODate }
```

`is_admin` grants **no tab and no capability** — what a licence can run is
`features` and nothing else, so marking one admin cannot change what it
generates. It decides exactly two things: an ordinary pod captures every
new prompt into the library silently while an admin pod captures nothing
and publishes only what its operator ticks, and only an admin licence may
save a preset through `POST /v1/presets`. Put it on the keys you generate
from yourself (`--admin`), or your own testing fills the review queue you
are the one working through.

Two optional fields pin builds: `build_sha` and `build_channel`, both
absent on an ordinary licence — see
[Build distribution](build-distribution.md).

## `plans` — `_id` is the plan key

```js
{ _id: "creator", name: "Creator", description: "...",
  price_monthly: 999, price_stars_monthly: 850, currency: "INR",
  discounts: { yearly: 25 },          // optional; overrides the cycle rate
  features: ["krea_t2i", ...], is_public: true, is_popular: true,
  sort_order: 20 }
```

`price_monthly` is the **only** INR price stored. What a quarter or a year
costs is derived from it and the discount on the billing document below, so
there is no second figure to forget to update — see `cyclePrice()` in
[`../src/plans.js`](../src/plans.js). `price_stars_monthly` is the
[Telegram bot's](telegram-bot.md) price and is never served on `/v1/plans`.

## `plans/__billing`

The billing cycles, as one lookup document in the same collection.
`__`-prefixed ids are filtered out of `allPlans()`, so it is never
mistaken for a tier by `/v1/plans` or by a licence's `plan_id`.

```js
{ _id: "__billing", kind: "billing",
  cycles: [
    { id: "monthly",   label: "Monthly",   months: 1,  enabled: true,  discount_percent: 0 },
    { id: "quarterly", label: "Quarterly", months: 3,  enabled: false, discount_percent: 10 },
    { id: "yearly",    label: "Yearly",    months: 12, enabled: false, discount_percent: 20 },
  ] }
```

`enabled` is the launch switch: `/v1/plans` sends only the cycles that are
on, and the pricing page renders a tab per cycle it is sent — so switching
quarterly or yearly on is **one boolean in Atlas**, live within a minute,
with no redeploy and no new app build. With everything but monthly off
there is no tab bar at all. Monthly is forced on however the document is
edited; a catalogue with no base cycle has no price to show for anything,
and `price_monthly` *is* the list price, so the base cycle is never
discounted.

Like the plans, it is upserted from the code on every seed run, so a toggle
flipped in Atlas is reverted by the next one unless `DEFAULT_BILLING` is
updated to match. Nothing bills anyone from this document — what a customer
pays is arranged by hand or by the bot, and how long their key lasts is
`expires_at` on the licence — so it is display config, and gets no more
protection than a price does.

## `features` — `_id` is the feature key

Seeded from the code registry for reading alongside the plans in Atlas;
[`../src/features.js`](../src/features.js) stays the source of truth and is
what `/v1/plans` serves.

```js
{ _id: "wan_i2v", name: "Wan Video",
  description: "...", category: "video", sort_order: 90,
  enabled: true }
```

`enabled: false` withdraws a feature **from the catalogue only**: it is
dropped from `/v1/plans` and from every plan's feature list on it, so a tab
that is built but not launched stops being something the pricing page
promises. It never filters `/v1/acquire` — entitlements are what a customer
already paid for, and a flag about what a page advertises must not take a
working tab away from a running pod. Withdrawing a feature from the people
who have it means editing the plans that grant it. An absent field means
enabled.

There is deliberately **no `features_cache` on the licence.** Denormalising
the resolved list would turn one plan edit into a fan-out write across
every licence on that tier, and a half-failed fan-out leaves licences
silently disagreeing with the plan they claim — the exact failure the
indirection exists to remove. There are a handful of plans, so the whole
collection is cached in module scope for 60 s instead: a cold start pays
one small query, warm invocations pay nothing.

## `sessions`

```js
{ license_key: "EMBER-...", instance_id: "<RUNPOD_POD_ID or uuid>",
  last_seen: ISODate, created_at: ISODate,
  meta: { ip, pod_id, hostname, version, gpu, seen_at } }
```

`created_at` on a session row is never overwritten, so the gap between it
and `last_seen` is how long that instance has been up.

## `prompts` — the prompt library

```js
{ fingerprint: "<64-char sha256>",   // unique; the deduplication key
  tab: "krea_t2i" | "krea_v2_t2i",
  source: "community" | "admin",
  is_public: false,                  // approval flips this; the read filters on it
  reviewed_at: null,                 // null = still in the queue
  title: null,                       // admin prompts only
  prompt: "...", negative: "...",
  settings: { ... },                 // the whole replay blob; models and LoRAs by id
  license_key: "EMBER-...",          // never projected to any client
  seen_count: 3,                     // how many pods sent this same recipe
  created_at: ISODate, updated_at: ISODate }
```

`fingerprint` is computed by the pod over the prompt and every setting
*except* the seed, the randomize toggle and the batch count. It is the one
client-chosen unique key in this database, which is why the write validates
it is really 64 hex characters: anything else would be a row that can never
be deduplicated against.

`settings` is interpreted here in exactly one respect: its `model` and its
`loras` rows are catalogue ids, and a write whose ids name nothing is
refused — see [Settings blobs](catalogue-data.md#settings-blobs). The rest
of the shape belongs to the app's tabs and changes with them, so validating
it would couple this service to a UI it should know nothing about. It is
bounded (8 KB) rather than checked, and the client guards every value
against what its own build offers before applying any of it.

## `presets` — named settings blobs

The same blob shape as a prompt, under a name, per tab.

```js
{ _id: ObjectId, tab: "krea_t2i", name: "Portrait",
  settings: { ... },                 // validated exactly like a prompt's
  enabled: true,                     // off = still stored, no longer offered
  is_default: false,                 // at most one per tab
  sort_order: 0,
  created_by: "EMBER-...",           // the licence that saved it
  created_at: ISODate, updated_at: ISODate }
```

`(tab, name)` is unique. A pod saving a preset whose name is taken gets
`"Portrait (2)"` and is told which name it got; naming an existing preset
is the admin route's job, where that is the whole point of the call.
Setting `is_default` clears it from every other preset on the same tab in
the same operation.

## `loras` — `_id` is the LoRA id

```js
{ _id: "realism-engine-v3-1", name: "Realism Engine v3.1",
  file: "realism_engine_krea2_v3.1.safetensors",       // unique
  source: { kind: "civitai", version: 3109006 },        // or { kind: "hf", repo, path }
  mirror: { repo: "owner/name", path: "loras/..." } | null,
  default_strength: 0.6, trigger: "",
  enabled: true, sort_order: 50,
  created_at: ISODate, updated_at: ISODate }
```

## `models` — `_id` is the model id

```js
{ _id: "krea2-raw-fp8", name: "Krea 2 Raw fp8",
  file: "krea2_raw_fp8_scaled.safetensors",             // NOT unique
  source: { kind: "hf", repo: "Comfy-Org/Krea-2", path: "diffusion_models/..." },
  mirror: null, variant: "turbo" | "raw", steps: 20, cfg: 2.5,
  turbo_lora: { lora: "krea2-turbo", strength: 0.6 } | null,
  trigger: "", enabled: true,
  created_at: ISODate, updated_at: ISODate }
```

## `feature_assets` — `_id` is the feature key

```js
{ _id: "krea_v2_t2i",
  models: ["krea2-turbo-mxfp8", "krea2-raw-fp8"],       // first = the tab's default
  loras: ["krea2-turbo", "filter-bypass-3", ...],       // dropdown order
  created_at: ISODate, updated_at: ISODate }
```

An empty `models` list is legal only for a LoRA-only feature — see
[Catalogue data](catalogue-data.md). `npm run remove-feature` deletes a
feature's `feature_assets` document along with its catalogue row; the
`loras` and `models` records are shared and stay.

## `builds` — `_id` is the artifact's sha256

```js
{ _id: "<sha256>", size: 412398112, version: "<git commit>",
  git_commit: "...", git_branch: "...", arch: "x86_64",
  platform: "linux" | "windows",
  filename: "ember",                 // part of the signed object key
  channels: ["stable"],              // $setOnInsert — promote edits it
  built_at: ISODate, published_at: ISODate }
```

Registering a build is an upsert on the sha, so republishing the same
artifact is idempotent; `channels` and `published_at` are `$setOnInsert`
so a re-register never silently unpromotes anything. `downloads` logs one
row per presigned URL, keyed on the licence, and expires itself.

`filename` is stored per build rather than assumed, which is what makes
promoting an older build to `stable` work: the start script downloads
under whatever name that build recorded, not under the name the current
build would have.

## `orders`

One document per purchase attempt, and the only reason a payment webhook
can be received twice without selling anything twice.

```js
{ _id: "b3f1c8e2-...",              // uuid, and IS the invoice payload
  telegram_user_id: 987654321, telegram_chat_id: 987654321,
  intent: "new" | "renew" | "upgrade",
  plan_id: "creator", cycle: "monthly", months: 1,
  amount_stars: 850,                // what was quoted, at invoice time
  paid_amount: 850,                 // what Telegram says was charged
  currency: "XTR", seats: 1,
  status: "CREATED" | "INVOICED" | "PAID" | "PROVISIONED" |
          "DELIVERED" | "FAILED_PROVISION" | "EXPIRED_UNPAID",
  telegram_payment_charge_id: "...",// absent until paid — sparse index
  license_key: "EMBER-...",         // absent until provisioned
  provision_attempts: 0, last_error: null,
  created_at, updated_at, invoiced_at, paid_at, provisioned_at,
  delivered_at }
```

`amount_stars` and `paid_amount` are stored separately and compared before
provisioning — a mismatch is `FAILED_PROVISION` with no licence written.
Every status change is one conditional update naming the status it must come
from, so a replayed webhook matches nothing.

## `telegram_users`

Who has talked to the bot. `_id` is the Telegram user id itself, so every
write is a plain upsert. Deliberately thin; nothing on the licensing path
reads it.

```js
{ _id: 987654321, username: "acme_ops", first_name: "Ravi",
  language_code: "en", is_blocked: false,
  first_seen_at, last_seen_at }
```

`licenses` gains one optional field, `telegram_user_id`, on keys sold through
the bot. It is a label for `/mykeys` and renewal, never an input: nothing in
`licenseProblem()`, `resolveEntitlement()` or `/v1/build` branches on it, and
`seatPayload()` never sends it. A CLI-issued key has none and is exactly as
valid.

## Indexes

`npm run init-db` creates them all; `ensureIndexes()` in
[`../src/db.js`](../src/db.js) is the list.

- `licenses`: unique `key`; `plan_id`; sparse `telegram_user_id`.
- `sessions`: unique `(license_key, instance_id)`; `(license_key,
  last_seen)`; TTL on `last_seen`.
- `prompts`: unique `fingerprint`; `(is_public, tab, created_at)`;
  `(reviewed_at, created_at)`; `(license_key, reviewed_at)`.
- `presets`: unique `(tab, name)`; `(enabled, tab, sort_order, name)`.
- `builds`: `(channels, platform)` — the channel lookup is now a pair, so
  the channel alone no longer identifies one document — and
  `published_at`. Nothing depends on either existing; the collection holds
  one row per build ever published.
- `downloads`: `(license_key, created_at)` for the hourly cap, and a TTL on
  `created_at`. Housekeeping only — the cap filters on `created_at` in the
  query.
- `orders`: **unique sparse** `telegram_payment_charge_id` (two orders
  cannot record one charge — and sparse is not optional, or every unpaid
  order would index as `null` and collide), `(telegram_user_id,
  created_at)`, `(status, updated_at)` for the sweep, and a sparse
  `license_key`.
- `loras`: **unique** `file`, and `(enabled, sort_order)`.

`plans`, `features`, `telegram_users`, `models` and `feature_assets` are
keyed by their `_id` and need nothing beyond it — `models.file` is
deliberately not unique.

The seat TTL is housekeeping, not correctness: Mongo's TTL monitor runs
roughly once a minute, so acquire filters on `last_seen` in the query.
