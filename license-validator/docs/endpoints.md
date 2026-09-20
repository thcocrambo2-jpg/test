# Endpoints

Every route the service serves, and the reasoning behind the ones that are
easy to get wrong. For whoever calls this API or changes it.

## The routes

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/acquire` | Take a seat. `{license_key, instance_id, meta}` |
| `POST` | `/v1/heartbeat` | Keep it. Re-checks the licence every call |
| `POST` | `/v1/release` | Give it back. Idempotent |
| `POST` | `/v1/build` | Which build this licence gets + a signed R2 URL. `{license_key, instance_id, current_sha, platform}`. Takes no seat |
| `POST` | `/v1/catalog` | The model and LoRA catalogue the Krea and MiniMax tabs are built from. `{license_key, instance_id}`, licence checked like acquire. Takes no seat |
| `GET` | `/v1/start.sh` | 302 to a signed URL for the Linux start script. Unauthenticated — the RunPod template fetches it |
| `GET` | `/v1/start.ps1` | The same for the Windows start script, fetched by a desktop shortcut |
| `GET` | `/v1/plans` | Public catalogue — `is_public` plans, enabled features, enabled billing cycles |
| `POST` | `/v1/prompts` | A pod submitting one prompt + its settings. Private on arrival, unless `publish` and the licence is `is_admin` |
| `GET` | `/v1/prompts` | Public library — approved prompts only. `?tab=&source=&q=&skip=&limit=` |
| `POST` | `/v1/presets` | The app saving one preset. Needs `is_admin`; answers a real error when it refuses |
| `GET` | `/v1/presets` | Enabled presets, all tabs or `?tab=` |
| `GET` | `/health` | Liveness + DB reachability |
| `GET` | `/v1/admin/licenses` | Every licence with live usage and **resolved** features (needs `ADMIN_TOKEN`) |
| `GET` | `/v1/admin/plans` | Every plan with the number of licences on it |
| `GET` | `/v1/admin/sessions` | Recent sessions, `?license_key=` to filter |
| `GET` | `/v1/admin/prompts` | The library, `?pending=1` for the review queue. Includes `license_key` |
| `POST` | `/v1/admin/prompts` | Author an ⭐ official prompt. Public immediately |
| `POST` | `/v1/admin/prompts/review` | `{id, approve}` — publish or reject one prompt |
| `GET` | `/v1/admin/presets` | Every preset, switched-off ones included |
| `POST` | `/v1/admin/presets` | Author or edit a preset, including `enabled`, `is_default` and `sort_order` |
| `POST` | `/v1/admin/presets/state` | `{id, enabled?, is_default?}` — flip one flag without restating the preset |
| `POST` | `/v1/admin/builds` | Register an uploaded build. The build scripts call this. Idempotent on `sha256` |
| `GET` | `/v1/admin/builds` | Every build published, newest first — the list you roll back from |
| `POST` | `/v1/admin/builds/promote` | Point a channel at a build. `{sha256, channel}` |
| `GET` | `/v1/admin/assets` | Every LoRA and model, disabled ones included, each with any validation `problems`, and every feature's lists as stored |
| `POST` | `/v1/admin/loras` | Create or update one LoRA by `id`. Only the fields sent are changed; creating needs `file` and `source` |
| `POST` | `/v1/admin/models` | The same, for a model |
| `POST` | `/v1/admin/feature-assets` | `{feature, models?, loras?}` — replace a feature's ordered lists. Every id must exist |
| `POST` | `/tg/webhook/:path` | Telegram updates. The path is derived from `TELEGRAM_WEBHOOK_SECRET`, and the `X-Telegram-Bot-Api-Secret-Token` header must match it too. Always answers 200 |
| `GET` `POST` | `/internal/cron/sweep` | Retry stuck provisioning and delivery. Needs `CRON_SECRET` — **not** `ADMIN_TOKEN` |

With no `ADMIN_TOKEN` set, every `/v1/admin/*` route answers 404 rather
than 401: a deployment that has not opted into the admin surface does not
advertise that it exists.

## The public reads

`/v1/plans` is unauthenticated on purpose: it is a pricing page's data and
none of it is secret. Non-public plans are filtered out, so `admin` and any
tier you are still trialling never appear, and so are features marked
`enabled: false` — including from the feature lists of the plans that still
grant them. It also carries the enabled billing cycles and, per plan, the
price each of them works out to.

`/v1/admin/licenses` reports what the customer *actually gets*, resolved
the same way `/v1/acquire` resolves it — for a licence on a plan that is
not written on the licence at all. A row whose plan is missing reports
`plan_error` instead of taking down the whole listing.

## The prompt library's two halves

The two `/v1/prompts` routes are the halves of the prompt library and they
do not mirror each other. The write is authenticated by the licence key and
stores a row that **no client can read**; the read is unauthenticated,
serves only `is_public: true`, and projects `license_key` away — so who
submitted a prompt never leaves this service. Pods write to it silently and
customers are not told it happens, which is why the write answers `200` for
everything they could not have known was wrong: a duplicate fingerprint, or
a licence that has hit its cap of `MAX_PENDING_PER_LICENSE` (200) pending
rows. See the app's docs for the capture and deduplication rules, and
`npm run prompts` for moderation.

The write takes one optional flag, `publish`, which asks for the row to be
created as a public ⭐ official prompt instead of a pending submission. It
is honoured **only when the licence document has `is_admin: true`** —
`publish` is a request and the record is the grant. A non-admin sending it
is not rejected; the flag is ignored and the prompt is stored the ordinary
way. A published row also gets a freshly generated fingerprint rather than
the one the pod sent, because deduplicating it against an existing
community row would silently turn "publish this" into a counter bump.

`POST /v1/presets` is the deliberate contrast. It is a button someone
pressed, so it answers a real error when it refuses — including `403
forbidden` when the licence is perfectly valid but simply not an admin.

## Catalogue ids are checked on every write

Every write that stores a settings blob — `POST /v1/prompts`,
`POST /v1/presets`, `POST /v1/admin/presets` and `POST /v1/admin/prompts` —
checks its model and LoRA ids against the catalogue and answers `400`
naming the bad value (see
[Settings blobs](catalogue-data.md#settings-blobs)). That includes the
silent pod write: a recipe whose ids name nothing is a malformed body, not
something the pod could not have known. A blob is also bounded at
`MAX_SETTINGS_BYTES` (8 KB) rather than validated field by field.

## Seeding the library

`npm run seed-prompts` writes one starter prompt of each kind — an ⭐
official one that is live immediately, and a 👥 community one sitting in
the review queue so there is something there the first time you look.
`scripts/seed-prompts.js` carries the field-by-field notes; edit
`SEED_PROMPTS` and re-run to add more. It is idempotent (keyed on
`fingerprint`) and **never un-approves**: content is `$set`, moderation
state is `$setOnInsert`, so a prompt you have already ruled on keeps that
ruling across seed runs.

## Status codes are the contract

The Python client branches on exactly this table — keep it stable.

| Code | `error` | Client behaviour |
| --- | --- | --- |
| 200 | — | proceed |
| 400 | `bad_request` | client bug, no retry |
| 403 | `invalid_key`, `revoked`, `expired`, `seat_limit` | **stop the app** |
| 403 | `forbidden` | `POST /v1/presets` only — valid licence, not an admin |
| 429 | `rate_limited` | `/v1/build` only — the per-licence hourly download cap |
| 503 | `server_error`, `no_build` | transient — retry at startup, ride the grace window if running |

Keep the 403/503 split intact. Any database failure must surface as 503,
or an Atlas outage reads to your customers as a licence violation; and a
real violation must never surface as 503, or the client's grace window
makes it survivable.

Revocation propagates because `/v1/heartbeat` re-reads the licence on
every call — flipping `active` to false stops running instances within
about a heartbeat, rather than only blocking their next start.
