# license-validator

Seat-limited license server for the Krea 2 app. One customer gets one key;
the key allows N concurrent running instances. It exists so the Atlas
connection string stays on a server instead of inside a binary handed to
customers — extracting a read-write credential from a shipped executable is
one `strings` invocation, and the damage there is every customer's records,
not just one bypassed check.

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

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/acquire` | Take a seat. `{license_key, instance_id, meta}` |
| `POST` | `/v1/heartbeat` | Keep it. Re-checks the license every call |
| `POST` | `/v1/release` | Give it back. Idempotent |
| `GET` | `/health` | Liveness + DB reachability |
| `GET` | `/v1/admin/licenses` | Every license with live usage (needs `ADMIN_TOKEN`) |
| `GET` | `/v1/admin/sessions` | Recent sessions, `?license_key=` to filter |

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
npm run issue-key -- --name "Acme Corp" --seats 2
npm start
```

`issue-key` prints the `KREA2_LICENSE_KEY=...` line to hand the customer.

```bash
npm run issue-key -- --name "Trial" --seats 1 --days 30   # time-limited
npm run issue-key -- --name "Acme Corp" --seats 3 --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --revoke
```

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
  active: true, expires_at: ISODate | null, created_at: ISODate }
```

`sessions`

```js
{ license_key: "KREA2-...", instance_id: "<RUNPOD_POD_ID or uuid>",
  last_seen: ISODate, created_at: ISODate,
  meta: { ip, pod_id, hostname, version, gpu, seen_at } }
```

Indexes: unique `key`; unique `(license_key, instance_id)`;
`(license_key, last_seen)`; TTL on `last_seen`.

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
