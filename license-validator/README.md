# license-validator

Seat-limited licence server for the Krea 2 app. One customer gets one key;
the key allows N concurrent running instances **and decides which tabs
they get**. It exists so the Atlas connection string stays on a server
instead of inside a binary handed to customers — extracting a read-write
credential from a shipped executable is one `strings` invocation, and the
damage there is every customer's records, not just one bypassed check.

## The rest of the docs

| Page | What is in it |
| --- | --- |
| [Plans and entitlements](docs/plans-and-entitlements.md) | Plans, how a key resolves to a feature list, the feature-key registry |
| [Endpoints](docs/endpoints.md) | Every route, and the status-code contract |
| [Catalogue data](docs/catalogue-data.md) | Models, LoRAs, per-tab lists, settings blobs, validation |
| [Build distribution](docs/build-distribution.md) | R2, channels, per-platform builds, pinning and rollback |
| [Telegram bot](docs/telegram-bot.md) | Selling licences in Telegram Stars, and unsticking an order |
| [Data](docs/data.md) | Every collection, field by field, and the indexes |

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

`HEARTBEAT_SECONDS` (default 60) rides on the acquire response, so the
cadence every deployed binary uses can be slowed from here without
reshipping anything.

## Setup

```bash
cd license-validator
npm install
cp .env.example .env          # fill in MONGODB_URI
npm run init-db               # creates indexes — run once per cluster
npm run seed-catalog          # writes the plans + features collections
npm run seed-assets           # writes the models, LoRAs and per-tab lists
npm run seed-presets          # writes each tab's Default preset (needs seed-assets)
npm run seed-prompts          # writes the starter prompt library (optional; needs seed-assets)
npm run issue-key -- --name "Acme Corp" --plan creator --seats 2
npm start
```

Every seed step is idempotent, so re-running one is safe. Add `--dry-run`
to any of them to see what a run would change before it changes it.

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
[`src/plans.js`](src/plans.js). It is an upsert and never deletes. Note the
direction: once seeded, the *collection* is what `/v1/acquire` reads, so a
price or feature list can be changed in Atlas without a redeploy — and a
hand edit there is reverted by the next seed run unless `DEFAULT_PLANS` is
updated to match. The same goes for the billing cycles in
`DEFAULT_BILLING` — see [`plans/__billing`](docs/data.md).

## Deploying to Vercel

```bash
npm i -g vercel
vercel                # first deploy, links the project
vercel --prod
```

Set `MONGODB_URI`, `MONGODB_DB`, `ADMIN_TOKEN` and the four `R2_*`
variables in Project Settings → Environment Variables, then redeploy.
[`vercel.json`](vercel.json) rewrites every path to `api/index.js`, which
exports the same Express app `server.js` runs locally, and registers the
[sweep cron](docs/telegram-bot.md). Every variable this service reads is
declared in [`src/config.js`](src/config.js).

`GET /health` reports both halves of "can a pod start right now":

```json
{ "db": "connected", "r2": "configured",
  "stable_build": "<sha256>",
  "stable_builds": { "linux": "<sha256>", "windows": "<sha256>" } }
```

`r2: "unset"` means the credentials are missing and every `/v1/build` will
answer 503. A `null` under `stable_builds` means nothing has been published
for that platform yet; `stable_build` is the Linux one.

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
([`../ember/settings.py`](../ember/settings.py)), so hand the customer the
bare label — no scheme, no `.vercel.app` — alongside their key. The label
must be plain `[a-z0-9-]`, 8–63 characters; anything with a dot or slash in
it is rejected at startup rather than used, which is what keeps the tag
from repointing the licence check at a server the customer controls.

Renaming the Vercel project therefore means reissuing the tag to every
pod, not just redeploying.

## CORS

Wide open, and largely moot: the app calls this server-side from Python,
so no `Origin` header is sent and CORS never applies to the real traffic.
It is enabled for anything browser-side you add later. There is no origin
worth pinning anyway — each pod serves the app's own web UI from a tunnel
URL that is regenerated on every run.

## Known limits

- **Acquire is count-then-insert, not atomic.** Two instances starting in
  the same millisecond can both pass the check. At these seat counts the
  window is negligible and the worst case is one extra seat — not worth a
  locking scheme.
- **Prompt submissions are trusted as far as their licence key.** Any pod
  with a valid key can write to the library, and the fingerprint it sends
  is not recomputed here. The worst case is a duplicate row or a junk one,
  which is what the 200-pending cap per licence and the approval step are
  for — nothing a customer submits is visible to anyone until you approve
  it.
- **This stops casual sharing, not a determined customer.** The check runs
  on a machine they control. Patching the binary defeats it, and that is a
  deliberate non-goal. Pair it with a licence agreement, and treat
  `/v1/admin/licenses` as the real value: a 2-seat key with 40 distinct
  instance ids in a week is a conversation you can have with evidence.
