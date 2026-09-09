# license-validator

Seat-limited license server for the Krea 2 app. One customer gets one key;
the key allows N concurrent running instances **and decides which tabs
they get**. It exists so the Atlas connection string stays on a server
instead of inside a binary handed to customers — extracting a read-write
credential from a shipped executable is one `strings` invocation, and the
damage there is every customer's records, not just one bypassed check.

## Design

Seats are **leases, not a counter.** A session row counts against the limit
only while its `last_seen` is newer than `STALE_SECONDS` (default 180).
That is the whole reason the scheme survives contact with real pods:

- A counter needs a decrement on shutdown, and `SIGKILL`, an OOM kill, a
  hard pod terminate and a network drop at teardown all skip it. Every one
  of those leaks a seat permanently, and customers who spin pods up and
  down constantly hit them constantly.
- A lease needs nothing to run on the client. Stop heartbeating and the
  seat frees itself in three minutes.

`/v1/release` on clean shutdown is an optimisation on top — it returns the
seat immediately instead of after the window — and nothing depends on it
firing.

The TTL index is **housekeeping, not correctness.** Mongo's TTL monitor
runs roughly once a minute and its timing is not guaranteed, so acquire
filters on `last_seen` in the query. TTL only keeps the collection small.

Re-acquiring the same `instance_id` is always free, so a client that
restarts the app on the same pod reclaims its own seat rather than
spending a second one. The Python side prefers `RUNPOD_POD_ID` for this
exact reason.

## Plans and entitlements

Which tabs a key grants is the only thing that decides them — the app has
no environment variable that can switch a tab on, so a customer cannot
grant themselves Wan's ~49 GB by editing their pod template.

A license names a **plan** rather than a feature list. The server resolves
it at request time, so editing one plan document moves every customer on
that tier instead of requiring a bulk update across `licenses`.

```bash
npm run seed-catalog                                             # once, and after editing plans
npm run issue-key -- --name "Acme Corp" --plan creator --seats 2
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
```

### Shipped plans

| Plan | ₹/mo | Grants |
| --- | --- | --- |
| `starter` | 599 | krea_t2i, gallery, community_prompts |
| `creator` | 999 | + krea_v2_t2i, krea_edit, krea_v2_edit, json_batch |
| `studio` | 1799 | + wan_i2v, minimax_i2v, minimax_t2v |
| `admin` | 0 | everything, including the four withdrawn tabs; `is_public: false` |

Three public tiers since 2026-09-07. `pro` was deleted that day (no
license was on it), and `krea_inpaint`, `faceswap`, `flux_t2i` and
`klein_i2i` were **withdrawn**: on no public plan, and `enabled: false` in
the features collection so the pricing page does not list them. The app
still ships those tabs and `admin` still grants them, so a dev pod can
check one still works; putting one back on sale is a plan edit plus that
flag. `krea_t2i` is turbo-only by configuration (the Raw model is not in
`KREA2_MODELS`), which is why V2 — turbo and raw — starts at Creator.

Prices are **documentation, not enforcement.** Nothing here charges anyone;
`expires_at` is the only lever that actually stops a key working.

`seats` is deliberately not a plan field. Seat count lives on the license
because it is per-deal negotiable, and resolving it through the plan would
mean an edit to `studio` retroactively changed how many pods every studio
customer may run.

### Resolution order

| On the license | Result |
| --- | --- |
| `features` is an array | exactly that, plan ignored |
| `plan_id` is set | `plan.features` + `license.features_extra` |
| neither | `null` — the app's built-in defaults, with a warning |

Rule 1 is why this needed no migration: every key issued before plans
existed carries a `features` array and behaves exactly as it did. It is
also the escape hatch for a deal that fits no tier — but prefer
`features_extra`, which keeps the customer on a plan and so keeps them
moving when the plan moves. `--plan` on an `--update` clears any literal
array and says so, since a leftover one would silently win and make the
new plan a no-op.

An empty `features: []` is a deliberate "this key starts and does
nothing", not "no opinion" — it overrides a plan like any other array.

### Rules worth keeping

- **Plans never leave `seatPayload()`.** `/v1/acquire` answers with a flat
  `features` array exactly as before, so the Python client knows nothing
  about tiers and needs no rebuild for any of this.
- **A `plan_id` naming nothing is a 503**, not a downgrade. Falling back to
  the client's defaults would hand a Studio customer three tabs and look
  like their fault; granting everything would be worse. 503 is retryable,
  rides the client's grace window, and puts the reason in the log.
- **Never delete a plan that has licenses on it.** `seed-catalog` refuses
  to delete anything and reports orphans; `/v1/admin/plans` shows the
  license count per plan, which is the number to check before editing one.
- **The `FEATURES` registry in `src/features.js` is used at issue time
  only**, never to filter what `/v1/acquire` returns. The app and this
  service deploy separately, so a key granting a tab that shipped before
  this list was updated must still work. Typos are caught where they are
  made instead.
- **`all` and `none` are expanded when the key is written**, not stored as
  sentinels. Reading a license then never requires knowing what `all` meant
  on the day it was issued.
- **`plan_name` and `expires_at` on the payload are labels.** The app's own
  header shows the tier and the date next to the brand, and the pricing
  page marks the tier as "Your plan" — nothing branches on either. Expiry
  is enforced here, in `licenseProblem()`, on every acquire and every
  heartbeat, so a client that ignores the date (any build before this field
  existed) is no less bounded by it. `null` means the key never expires,
  and the header then shows no date rather than "expires never".

`features` rides on every 200 — acquire, where the client applies it, and
heartbeat, where a change makes a running instance log that it needs a
restart. It is deliberately not applied live: tabs are built once at
launch and a newly granted tab has no weights on disk behind it. Note this
now applies to **plan** edits too: changing `creator` tells every running Creator
customer, within a heartbeat, that they should restart.

## Feature keys

Keys are permanent; names are not. A key is written into license documents
and compiled into every shipped binary, so renaming one silently drops that
tab for anyone on an older build. `name`, `description` and `category` are
display-only — fix naming there.

Model-bound tabs use `<model>_<task>`; tabs that are not tied to a model
keep a plain name.

| Key | Name | Category |
| --- | --- | --- |
| `krea_t2i` | Single / Simple Batch | generation |
| `krea_v2_t2i` | Krea 2 V2 | generation |
| `flux_t2i` | Flux 2 — Text to Image | generation |
| `krea_edit` | Krea Edit — Instruction | editing |
| `krea_v2_edit` | Krea2 V2 Edit | editing |
| `krea_inpaint` | Krea Inpaint — Img2Img | editing |
| `klein_i2i` | Klein Edit — Image to Image | editing |
| `faceswap` | Face Swap (ReActor) | editing |
| `wan_i2v` | Wan 2.2 Video | video |
| `minimax_i2v` | MiniMax H3 Video (image-to-video, with sound) | video |
| `minimax_t2v` | MiniMax H3 Text to Video (with sound) | video |
| `gallery` | Gallery | tools |
| `json_batch` | JSON Advanced Batch | tools |

Seven of these were renamed from `single`, `v2`, `edit`, `inpaint`,
`flux`, `klein` and `wan` before any key was issued. There is **no alias
map** for the old names — nothing needed translating, and a permanent map
that translates nothing is a trap for whoever reads it next. An old key is
simply unknown: the client warns and ignores it. If a key ever has to
change after launch, reissue the affected licenses.

Feature keys and the app's *asset groups* are separate namespaces that
happen to overlap. A key names a tab; a group names a set of weights
several tabs share, and `downloads.py` is keyed on the latter — which is
why `krea_v2_t2i` still needs the group called `v2`. Renaming a tab never
touches a download.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/acquire` | Take a seat. `{license_key, instance_id, meta}` |
| `POST` | `/v1/heartbeat` | Keep it. Re-checks the license every call |
| `POST` | `/v1/release` | Give it back. Idempotent |
| `POST` | `/v1/build` | Which build this license gets + a signed R2 URL. `{license_key, instance_id, current_sha}`. Takes no seat |
| `GET` | `/v1/start.sh` | 302 to a signed URL for the start script. Unauthenticated — the RunPod template fetches it |
| `GET` | `/v1/plans` | Public catalogue — `is_public` plans, enabled features, enabled billing cycles |
| `POST` | `/v1/prompts` | A pod submitting one prompt + its settings. Private on arrival, unless `publish` and the license is `is_admin` |
| `GET` | `/v1/prompts` | Public library — approved prompts only. `?tab=&source=&q=&skip=&limit=` |
| `GET` | `/health` | Liveness + DB reachability |
| `GET` | `/v1/admin/licenses` | Every license with live usage and **resolved** features (needs `ADMIN_TOKEN`) |
| `GET` | `/v1/admin/plans` | Every plan with the number of licenses on it |
| `GET` | `/v1/admin/sessions` | Recent sessions, `?license_key=` to filter |
| `GET` | `/v1/admin/prompts` | The library, `?pending=1` for the review queue. Includes `license_key` |
| `POST` | `/v1/admin/builds` | Register an uploaded build. `build.sh` calls this. Idempotent on `sha256` |
| `GET` | `/v1/admin/builds` | Every build published, newest first — the list you roll back from |
| `POST` | `/v1/admin/builds/promote` | Point a channel at a build. `{sha256, channel}` |
| `POST` | `/v1/admin/prompts/review` | `{id, approve}` — publish or reject one prompt |
| `POST` | `/v1/admin/prompts` | Author an ⭐ official prompt. Public immediately |
| `POST` | `/tg/webhook/:path` | Telegram updates. The path is derived from `TELEGRAM_WEBHOOK_SECRET`, and the `X-Telegram-Bot-Api-Secret-Token` header must match it too. Always answers 200 |
| `GET` `POST` | `/internal/cron/sweep` | Retry stuck provisioning and delivery. Needs `CRON_SECRET` — **not** `ADMIN_TOKEN` |

`/v1/plans` is unauthenticated on purpose: it is a pricing page's data and
none of it is secret. Non-public plans are filtered out, so `admin` and any
tier you are still trialling never appear, and so are features marked
`enabled: false` — including from the feature lists of the plans that still
grant them. It also carries the enabled billing cycles and, per plan, the
price each of them works out to.

`/v1/admin/licenses` reports what the customer *actually gets*, resolved
the same way `/v1/acquire` resolves it — for a license on a plan that is
not written on the license at all. A row whose plan is missing reports
`plan_error` instead of taking down the whole listing.

The two `/v1/prompts` routes are the halves of the prompt library and they
do not mirror each other. The write is authenticated by the license key and
stores a row that **no client can read**; the read is unauthenticated,
serves only `is_public: true`, and projects `license_key` away — so who
submitted a prompt never leaves this service. Pods write to it silently and
customers are not told it happens, which is why the write answers `200` for
everything they could not have known was wrong: a duplicate fingerprint, or
a license that has hit its 200-pending cap. See the app's README for the
capture and deduplication rules, and `npm run prompts` for moderation.

The write takes one optional flag, `publish`, which asks for the row to be
created as a public ⭐ official prompt instead of a pending submission. It
is honoured **only when the license document has `is_admin: true`** —
`publish` is a request and the record is the grant. A non-admin sending it
is not rejected; the flag is ignored and the prompt is stored the ordinary
way. A published row also gets a freshly generated fingerprint rather than
the one the pod sent, because deduplicating it against an existing
community row would silently turn "publish this" into a counter bump.

`npm run seed-prompts` writes one starter prompt of each kind — an ⭐
official one that is live immediately, and a 👥 community one sitting in
the review queue so there is something there the first time you look.
`scripts/seed-prompts.js` carries the field-by-field notes; edit
`SEED_PROMPTS` and re-run to add more. It is idempotent (keyed on
`fingerprint`) and **never un-approves**: content is `$set`, moderation
state is `$setOnInsert`, so a prompt you have already ruled on keeps that
ruling across seed runs.

### Status codes are the contract

| Code | `error` | Client behaviour |
| --- | --- | --- |
| 200 | — | proceed |
| 400 | `bad_request` | client bug, no retry |
| 403 | `invalid_key`, `revoked`, `expired`, `seat_limit` | **stop the app** |
| 503 | `server_error` | transient — retry at startup, ride the grace window if running |

Keep the 403/503 split intact. Any database failure must surface as 503,
or an Atlas outage reads to your customers as a license violation; and a
real violation must never surface as 503, or the client's grace window
makes it survivable.

Revocation propagates because `/v1/heartbeat` re-reads the license on
every call — flipping `active` to false stops running instances within
about a heartbeat, rather than only blocking their next start.

## Build distribution

The app binary lives in a **private** Cloudflare R2 bucket. Pods never
address it: they send their key to `/v1/build` and get back a signed URL
that expires in 30 minutes.

Be clear about what that does and does not buy. It stops a lapsed or
revoked key pulling a **new** build, it lets a build be pinned or rolled
back per license from the server, and it records which machines pull on
which key. It does **not** stop a binary someone already has from being
copied — what limits who can *run* the app is still the seat check, same
as when the build sat in a public Hugging Face repo.

Objects are content-addressed at `builds/<sha256>/krea2app`, so publishing
never overwrites and every build stays available. A channel is just a name
in the `channels` array of one build document, which makes rolling forward
and rolling back the identical operation:

```bash
curl -s -H "Authorization: Bearer $KREA2_ADMIN_TOKEN" \
     https://<deployment>.vercel.app/v1/admin/builds

curl -s -X POST -H "Authorization: Bearer $KREA2_ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"sha256":"<older sha>","channel":"stable"}' \
     https://<deployment>.vercel.app/v1/admin/builds/promote
```

Which build a license resolves to, most specific first:

| On the license | Result |
| --- | --- |
| `build_sha` is set | exactly that build, channel ignored |
| `build_channel` is set | whichever build holds that channel |
| neither | whichever build holds `stable` |

Both fields are absent on an ordinary license, so the default needs no
edit. Set `build_sha` to hold one customer on a known-good build, or
`build_channel: "beta"` to put a willing customer on new builds first.

### Two R2 tokens, deliberately

| Where | Scope | Why |
| --- | --- | --- |
| this service | Object **Read** | public-facing; only ever signs GETs |
| `build.sh` | Object **Read & Write** | the only thing that uploads |

Giving the API a write token would mean any path to leaking it is a path
to replacing the binary every customer downloads. Keeping them apart is
what makes the read-only half actually read-only.

The builds bucket must also be **separate from the public showcase-images
bucket**. Public access on R2 is a per-bucket setting, so one bucket
cannot be both gated and world-readable.

### If the API is down

`scripts/runpod_start.sh` falls back to the binary already on the pod's
volume for *any* non-200 — unreachable, expired, revoked, rate limited,
nothing published. A pod that has everything it needs to run is not
bricked by this service having a bad afternoon, and the seat check that
follows delivers the real verdict with the message worth reading. A pod
with no cached binary and a failed call stops, and prints the server's
message when there is one.

## Setup

```bash
cd license-validator
npm install
cp .env.example .env          # fill in MONGODB_URI
npm run init-db               # creates indexes — run once per cluster
npm run seed-catalog          # writes the plans + features collections
npm run seed-prompts          # writes the starter prompt library (optional)
npm run issue-key -- --name "Acme Corp" --plan creator --seats 2
npm start
```

All three seed steps are idempotent, so re-running one is safe. Add
`--dry-run` to `seed-catalog` or `seed-prompts` to see what a run would
change before it changes it.

`issue-key` prints the `KREA2_LICENSE_KEY=...` line to hand the customer,
and the resolved feature list underneath it so you can see what they will
actually get before you send it.

```bash
npm run issue-key -- --name "Trial" --plan studio --seats 1 --days 30
npm run issue-key -- --name "Acme Corp" --seats 3 --update      # seats only
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan creator --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --revoke
```

`--update` only touches `features`, `features_extra` and `plan_id` when the
matching flag is passed, so an update about seats cannot silently wipe an
entitlement. The exception is `--plan`, which clears a literal `features`
array and prints that it did — leaving one would let it keep overriding
the plan you just assigned.

`--plan` and `--features` together are rejected rather than silently
resolved, since the literal array would win and the plan would do nothing.

**Re-run `npm run seed-catalog` after editing `DEFAULT_PLANS`** in
`src/plans.js`. It is an upsert and never deletes. Note the direction: once
seeded, the *collection* is what `/v1/acquire` reads, so a price or feature
list can be changed in Atlas without a redeploy — and a hand edit there is
reverted by the next seed run unless `DEFAULT_PLANS` is updated to match.
The same goes for the billing cycles in `DEFAULT_BILLING` — see
`plans/__billing` under [Data](#data).

## Deploying to Vercel

```bash
npm i -g vercel
vercel                # first deploy, links the project
vercel --prod
```

Set `MONGODB_URI`, `MONGODB_DB`, `ADMIN_TOKEN` and the four `R2_*`
variables in Project Settings → Environment Variables, then redeploy.
`vercel.json` rewrites every path to `api/index.js`, which exports the
same Express app `server.js` runs locally.

`GET /health` reports both halves of "can a pod start right now":

```json
{ "db": "connected", "r2": "configured", "stable_build": "<sha256>" }
```

`r2: "unset"` means the credentials are missing and every `/v1/build` will
answer 503. `stable_build: null` means nothing has been published yet.

Three things to know:

- **Run `npm run init-db` yourself.** The serverless path deliberately
  does not create indexes, because doing it per cold start would add
  latency to the request that gates a customer's app launch.
- **Allow Atlas network access from anywhere** (`0.0.0.0/0`) or use the
  Vercel integration. Vercel's function IPs are not fixed, so an IP
  allowlist will fail intermittently in a way that looks like a bug in
  this service.
- **Hobby is non-commercial.** Vercel's terms restrict the free plan to
  personal use, and a licensing backend for paying customers is not that.
  Budget for Pro, and check their current terms rather than taking this
  file's word for it.

The production URL (`<project>.vercel.app`) is stable across redeploys —
it only changes if you rename or delete the project.

The client does not hold that URL. It reads the subdomain alone from
`KREA2_NODE_TAG` on the pod and rebuilds `https://<tag>.vercel.app` itself
(`config.py`), so hand the customer the bare label — no scheme, no
`.vercel.app` — alongside their key. The label must be plain
`[a-z0-9-]`, 8–63 characters; anything with a dot or slash in it is
rejected at startup rather than used, which is what keeps the tag from
repointing the licence check at a server the customer controls.

Renaming the Vercel project therefore means reissuing the tag to every
pod, not just redeploying.

## CORS

Wide open, and largely moot: the app calls this server-side from Python,
so no `Origin` header is sent and CORS never applies to the real traffic.
It is enabled for anything browser-side you add later. Pinning an origin
would not work anyway — the Gradio share URL is regenerated on every run.

## Telegram bot

Licences are also sold directly in Telegram, paid in Telegram Stars. It is
the same deployment, the same database and the same licence documents — a
key bought in the bot is byte-indistinguishable from one issued with
`npm run issue-key`, which is why nothing on the pod side knows the bot
exists.

```text
customer → Telegram → POST /tg/webhook/<path> → orders.js → provision.js → Mongo
                      secret_token header        state       the only
                      + random path              machine     licence writer
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`, then register:

```bash
npm run set-webhook -- --url https://<node-tag>.vercel.app
npm run set-webhook -- --show     # what Telegram thinks, and its last error
npm run set-webhook -- --delete
```

**With either variable unset the whole bot answers 404**, exactly as
`/v1/admin/*` does without an `ADMIN_TOKEN`. Deploying this code to a
service that is not selling anything changes nothing.

### What a customer can do

`/start` · `/plans` · `/buy` · `/mykeys` · `/renew` · `/help`

Prices come from `price_stars_monthly` on the plan documents, read through
the same `allPlans()` the pricing page uses. **A plan with no Stars price
cannot be bought**, which is what keeps `admin`, `admin-minimal`,
`test-krea1-only` and `customer-admin` off the shelf without a second list
of what is for sale. `price_stars_monthly` is deliberately absent from
`/v1/plans`.

Tapping a tier means one of three things, decided by what the customer
already holds:

| They hold | They tap | What happens |
| --- | --- | --- |
| nothing | any tier | a new key, term starts today |
| Creator | Creator | **renew** — the term is added to what is left |
| Creator | Studio | **upgrade** — plan changes, term restarts today |

A revoked licence counts as no licence, so a payment can never quietly
reinstate somebody who was cut off.

### The delivery message carries two values

`KREA2_LICENSE_KEY` **and** `KREA2_NODE_TAG`. A pod with the key and no tag
exits with code 2 before it prints anything (`config.py:955-964` builds the
licence API URL from the tag), and there is no key-entry screen anywhere in
the app — both are environment variables. The bot refuses to sell at all
while `KREA2_NODE_TAG` is unset, checked before the invoice rather than
after the money.

### When something goes wrong

The webhook **always answers 200**. Telegram retries any non-2xx for 24
hours, which is the right thing before a payment and the wrong thing after
one, so failures live in the order's status and the log instead.

The charge is recorded before any licence work begins, so a purchase cannot
be lost. Anything left unfinished is picked up by the sweep — one cron every
fifteen minutes, calling the same code the webhook does:

```bash
npm run orders                                # the 20 most recent
npm run orders -- --status FAILED_PROVISION
npm run orders -- --show    <order-id>
npm run orders -- --retry   <order-id>        # provision + deliver
npm run orders -- --deliver <order-id>        # re-send the message only
npm run orders -- --sweep                     # one pass, right now
```

`--retry` calls the same `fulfillOrder()` the webhook calls, so it cannot
double-issue: every transition is conditional and matches nothing the second
time.

> **Vercel plan note.** `vercel.json` schedules the sweep every fifteen
> minutes. Hobby projects allow only one cron run per day — on Hobby, change
> the schedule to something like `"0 3 * * *"` or the deployment is
> rejected. Nothing else about the bot depends on the cron: it is recovery,
> not the happy path.

## Data

`licenses`

```js
{ key: "KREA2-XXXX-XXXX-XXXX", name: "Acme Corp", seats: 2,
  active: true, expires_at: ISODate | null,
  plan_id: "creator" | null,          // the normal route
  features: [...] | null,             // literal override; wins over plan_id
  features_extra: ["wan_i2v"] | null, // granted on top of the plan
  is_admin: false,                    // a role, not an entitlement
  created_at: ISODate }
```

`is_admin` grants **no tab and no capability** — what a license can run is
`features` and nothing else, so marking one admin cannot change what it
generates. It decides exactly one thing: an ordinary pod captures every new
prompt into the library silently, an admin pod captures nothing and
publishes only what its operator ticks the checkbox for. Put it on the keys
you generate from yourself (`--admin`), or your own testing fills the
review queue you are the one working through.

`plans` — `_id` is the plan key

```js
{ _id: "creator", name: "Creator", description: "...",
  price_monthly: 999, currency: "INR",
  discounts: { yearly: 25 },          // optional; overrides the cycle rate
  features: ["krea_t2i", ...], is_public: true, sort_order: 20 }
```

`price_monthly` is the **only** price stored. What a quarter or a year
costs is derived from it and the discount on the billing document below, so
there is no second figure to forget to update — see `cyclePrice()` in
`src/plans.js`.

`plans/__billing` — the billing cycles, as one lookup document in the same
collection. `__`-prefixed ids are filtered out of `allPlans()`, so it is
never mistaken for a tier by `/v1/plans` or by a license's `plan_id`.

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
edited; a catalogue with no base cycle has no price to show for anything.

Like the plans, it is upserted from the code on every seed run, so a toggle
flipped in Atlas is reverted by the next one unless `DEFAULT_BILLING` is
updated to match. Nothing bills anyone from this document — what a customer
pays is arranged by hand and how long their key lasts is `expires_at` on
the license — so it is display config, and gets no more protection than a
price does.

`features` — `_id` is the feature key. Seeded from the code registry for
reading alongside the plans in Atlas; `src/features.js` stays the source of
truth and is what `/v1/plans` serves.

```js
{ _id: "klein_i2i", name: "Klein Edit — Image to Image",
  description: "...", category: "editing", sort_order: 80,
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

There is deliberately **no `features_cache` on the license.** Denormalising
the resolved list would turn one plan edit into a fan-out write across
every license on that tier, and a half-failed fan-out leaves licenses
silently disagreeing with the plan they claim — the exact failure the
indirection exists to remove. There are a handful of plans, so the whole
collection is cached in module scope for 60s instead: a cold start pays one
small query, warm invocations pay nothing.

`sessions`

```js
{ license_key: "KREA2-...", instance_id: "<RUNPOD_POD_ID or uuid>",
  last_seen: ISODate, created_at: ISODate,
  meta: { ip, pod_id, hostname, version, gpu, seen_at } }
```

`prompts` — the prompt library

```js
{ fingerprint: "<64-char sha256>",   // unique; the deduplication key
  tab: "krea_t2i" | "krea_v2_t2i",
  source: "community" | "admin",
  is_public: false,                  // approval flips this; the read filters on it
  reviewed_at: null,                 // null = still in the queue
  title: null,                       // admin prompts only
  prompt: "...", negative: "...",
  settings: { ... },                 // the whole replay blob, stored verbatim
  license_key: "KREA2-...",          // never projected to any client
  seen_count: 3,                     // how many pods sent this same recipe
  created_at: ISODate, updated_at: ISODate }
```

`fingerprint` is computed by the pod over the prompt and every setting
*except* the seed, the randomize toggle and the batch count — see the app's
README for why. It is the one client-chosen unique key in this database,
which is why the write validates it is really 64 hex characters: anything
else would be a row that can never be deduplicated against.

`settings` is stored **verbatim and never interpreted here.** The shape
belongs to the app's tabs and changes with them, so validating it would
couple this service to a UI it should know nothing about. It is bounded
(8 KB) rather than checked, and the client guards every value against what
its own build offers before applying any of it.

`orders` — one document per purchase attempt, and the only reason a payment
webhook can be received twice without selling anything twice.

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
  license_key: "KREA2-...",         // absent until provisioned
  provision_attempts: 0, last_error: null,
  created_at, updated_at, invoiced_at, paid_at, provisioned_at,
  delivered_at }
```

`amount_stars` and `paid_amount` are stored separately and compared before
provisioning — a mismatch is `FAILED_PROVISION` with no licence written.
Every status change is one conditional update naming the status it must come
from, so a replayed webhook matches nothing.

`telegram_users` — who has talked to the bot. `_id` is the Telegram user id
itself, so every write is a plain upsert. Deliberately thin; nothing on the
licensing path reads it.

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

Indexes: unique `key`; `plan_id`; unique `(license_key, instance_id)`;
`(license_key, last_seen)`; TTL on `last_seen`; unique `fingerprint`;
`(is_public, tab, created_at)`; `(reviewed_at, created_at)`;
`(license_key, reviewed_at)`; sparse `telegram_user_id` on `licenses`;
and on `orders` a **unique sparse** `telegram_payment_charge_id` (two
orders cannot record one charge), `(telegram_user_id, created_at)`,
`(status, updated_at)` for the sweep and a sparse `license_key`.
`plans`, `features` and `telegram_users` are keyed by their `_id` and
need nothing beyond it.

`created_at` on a session row is never overwritten, so the gap between it
and `last_seen` is how long that instance has been up.

## Known limits

- **Acquire is count-then-insert, not atomic.** Two instances starting in
  the same millisecond can both pass the check. At these seat counts the
  window is negligible and the worst case is one extra seat — not worth a
  locking scheme.
- **Prompt submissions are trusted as far as their license key.** Any pod
  with a valid key can write to the library, and the fingerprint it sends
  is not recomputed here. The worst case is a duplicate row or a junk one,
  which is what the 200-pending cap per license and the approval step are
  for — nothing a customer submits is visible to anyone until you approve
  it.
- **This stops casual sharing, not a determined customer.** The check runs
  on a machine they control. Patching the binary defeats it, and that is a
  deliberate non-goal. Pair it with a license agreement, and treat
  `/v1/admin/licenses` as the real value: a 2-seat key with 40 distinct
  instance ids in a week is a conversation you can have with evidence.
