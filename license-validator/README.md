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
npm run issue-key -- --name "Acme Corp" --plan pro --seats 2
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
```

### Shipped plans

| Plan | $/mo | Grants |
| --- | --- | --- |
| `starter` | 19 | krea_t2i, krea_v2_t2i, gallery |
| `creator` | 39 | + krea_edit, krea_inpaint, faceswap |
| `pro` | 59 | + flux_t2i, klein_i2i |
| `studio` | 89 | + wan_i2v, json_batch |
| `admin` | 0 | everything, `is_public: false` |

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

`features` rides on every 200 — acquire, where the client applies it, and
heartbeat, where a change makes a running instance log that it needs a
restart. It is deliberately not applied live: tabs are built once at
launch and a newly granted tab has no weights on disk behind it. Note this
now applies to **plan** edits too: changing `pro` tells every running Pro
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
| `krea_inpaint` | Krea Inpaint — Img2Img | editing |
| `klein_i2i` | Klein Edit — Image to Image | editing |
| `faceswap` | Face Swap (ReActor) | editing |
| `wan_i2v` | Wan 2.2 Video | video |
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
| `GET` | `/v1/plans` | Public catalogue — `is_public` plans + feature metadata |
| `GET` | `/health` | Liveness + DB reachability |
| `GET` | `/v1/admin/licenses` | Every license with live usage and **resolved** features (needs `ADMIN_TOKEN`) |
| `GET` | `/v1/admin/plans` | Every plan with the number of licenses on it |
| `GET` | `/v1/admin/sessions` | Recent sessions, `?license_key=` to filter |

`/v1/plans` is unauthenticated on purpose: it is a pricing page's data and
none of it is secret. Non-public plans are filtered out, so `admin` and any
tier you are still trialling never appear. Its feature metadata comes from
the code registry rather than the `features` collection, so a pricing page
can never describe a tab in terms the deployment does not know.

`/v1/admin/licenses` reports what the customer *actually gets*, resolved
the same way `/v1/acquire` resolves it — for a license on a plan that is
not written on the license at all. A row whose plan is missing reports
`plan_error` instead of taking down the whole listing.

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

## Setup

```bash
cd license-validator
npm install
cp .env.example .env          # fill in MONGODB_URI
npm run init-db               # creates indexes — run once per cluster
npm run seed-catalog          # writes the plans + features collections
npm run issue-key -- --name "Acme Corp" --plan pro --seats 2
npm start
```

`issue-key` prints the `KREA2_LICENSE_KEY=...` line to hand the customer,
and the resolved feature list underneath it so you can see what they will
actually get before you send it.

```bash
npm run issue-key -- --name "Trial" --plan studio --seats 1 --days 30
npm run issue-key -- --name "Acme Corp" --seats 3 --update      # seats only
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan pro --update
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

## Deploying to Vercel

```bash
npm i -g vercel
vercel                # first deploy, links the project
vercel --prod
```

Set `MONGODB_URI`, `MONGODB_DB` and `ADMIN_TOKEN` in Project Settings →
Environment Variables, then redeploy. `vercel.json` rewrites every path to
`api/index.js`, which exports the same Express app `server.js` runs
locally.

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
it only changes if you rename or delete the project — so it is safe to
compile into the client.

## CORS

Wide open, and largely moot: the app calls this server-side from Python,
so no `Origin` header is sent and CORS never applies to the real traffic.
It is enabled for anything browser-side you add later. Pinning an origin
would not work anyway — the Gradio share URL is regenerated on every run.

## Data

`licenses`

```js
{ key: "KREA2-XXXX-XXXX-XXXX", name: "Acme Corp", seats: 2,
  active: true, expires_at: ISODate | null,
  plan_id: "pro" | null,              // the normal route
  features: [...] | null,             // literal override; wins over plan_id
  features_extra: ["wan_i2v"] | null, // granted on top of the plan
  created_at: ISODate }
```

`plans` — `_id` is the plan key

```js
{ _id: "pro", name: "Pro", description: "...",
  price_monthly: 59, price_yearly: 590, currency: "USD",
  features: ["krea_t2i", ...], is_public: true, sort_order: 30 }
```

`features` — `_id` is the feature key. Seeded from the code registry for
reading alongside the plans in Atlas; `src/features.js` stays the source of
truth and is what `/v1/plans` serves.

```js
{ _id: "klein_i2i", name: "Klein Edit — Image to Image",
  description: "...", category: "editing", sort_order: 80 }
```

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

Indexes: unique `key`; `plan_id`; unique `(license_key, instance_id)`;
`(license_key, last_seen)`; TTL on `last_seen`. `plans` and `features` are
keyed by their string `_id` and need nothing beyond it.

`created_at` on a session row is never overwritten, so the gap between it
and `last_seen` is how long that instance has been up.

## Known limits

- **Acquire is count-then-insert, not atomic.** Two instances starting in
  the same millisecond can both pass the check. At these seat counts the
  window is negligible and the worst case is one extra seat — not worth a
  locking scheme.
- **This stops casual sharing, not a determined customer.** The check runs
  on a machine they control. Patching the binary defeats it, and that is a
  deliberate non-goal. Pair it with a license agreement, and treat
  `/v1/admin/licenses` as the real value: a 2-seat key with 40 distinct
  instance ids in a week is a conversation you can have with evidence.
