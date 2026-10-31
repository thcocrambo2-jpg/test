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
| `POST` | `/v1/catalog` | The model and LoRA catalogue the Krea, Ember Lite, Qwen 2.1 and MiniMax tabs are built from. `{license_key, instance_id}`, licence checked like acquire. Takes no seat |
| `POST` | `/v1/library` | A picture library's pack (the pose library) + a signed R2 URL for it. `{license_key, instance_id, name, current_version}`; the licence must grant a tab the `libraries` row lists. Takes no seat |
| `GET` | `/v1/start.sh` | 302 to a signed URL for the Linux start script. Unauthenticated — the RunPod template fetches it |
| `GET` | `/v1/start.ps1` | The same for the Windows start script, fetched by a desktop shortcut |
| `GET` | `/v1/plans` | Public catalogue — `is_public` plans, enabled features, enabled billing cycles |
| `POST` | `/v1/prompt-library` | The Prompt Library: enabled entries with signed picture links and whether each is `usable` on this licence — no tokens. `{license_key, instance_id, tab?, category?, tags?, skip?, limit?}`. Takes no seat |
| `POST` | `/v1/prompt-library/token` | One card's settings token, when its Use or Copy is pressed. `{license_key, instance_id, id}`; the entry must be enabled and its tab granted. Takes no seat |
| `POST` | `/v1/presets` | The app saving one preset. Needs `is_admin`; answers a real error when it refuses |
| `GET` | `/v1/presets` | Enabled presets, all tabs or `?tab=` |
| `GET` | `/health` | Liveness + DB reachability |
| `GET` | `/v1/admin/licenses` | Every licence with live usage and **resolved** features (needs `ADMIN_TOKEN`) |
| `GET` | `/v1/admin/plans` | Every plan with the number of licences on it |
| `GET` | `/v1/admin/sessions` | Recent sessions, `?license_key=` to filter |
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

## The Prompt Library

`POST /v1/prompt-library` has no collection behind it. The library is one
index file and its pictures in the private builds bucket:

```
library/prompts/index.json          every entry, in display order
library/prompts/<id>/thumb.webp     the grid picture
library/prompts/<id>/full.webp      the full-size picture
library/prompts/<id>/token.txt      its settings token
```

The index carries each entry's category key and tag keys, and the two
lists (key and label, display order) beside the entries. A token is a
file of its own so that a page is answered from a small index alone; the
token route fetches one token.txt, and each token read is kept for the
life of the instance, since it never changes.

Only the operator's local admin page writes them (the app repo's
`scripts/prompt_library/admin.py`); this service only reads, with the read
key, and reuses one read of the index for a minute (`src/promptLibrary.js`).

The licence must be valid and grant `community_prompts`. Only entries
switched on (`enabled: true`) are served, each with signed links to its two
pictures and `usable` — whether this licence has the entry's tab. The
token itself comes from `/v1/prompt-library/token`, **only for a licence
that has that entry's tab**. A tab they have not bought still shows
its pictures; they just cannot load the recipe. `tabs` counts the enabled
entries per tab, for the filter.

`category` narrows to one category key and `tags` to entries carrying
every tag key listed. `categories` and `tags` in the answer are the
filters' rows, `{key, label, count}`, each counted with the *other*
filters applied — the category counts under the chosen tab and tags, the
tag counts under the chosen tab, category and other tags — so a chip says
how many pictures picking it would give. Rows with nothing behind them
are left out, except the ones chosen.

Picture links last six hours and are signed as if at the top of the hour,
so a picture keeps the same link for the whole hour (the browser caches
it) and every link handed out has at least five hours left. An index that
cannot be read answers `503`, never an empty library.

## Catalogue ids are checked on every write

Every write that stores a settings blob — `POST /v1/presets` and
`POST /v1/admin/presets` — checks its model and LoRA ids against the
catalogue and answers `400` naming the bad value (see
[Settings blobs](catalogue-data.md#settings-blobs)). A blob is also bounded
at `MAX_SETTINGS_BYTES` (8 KB) rather than validated field by field.

`POST /v1/presets` answers a real error when it refuses — including `403
forbidden` when the licence is perfectly valid but simply not an admin.

## Status codes are the contract

The Python client branches on exactly this table — keep it stable.

| Code | `error` | Client behaviour |
| --- | --- | --- |
| 200 | — | proceed |
| 400 | `bad_request` | client bug, no retry |
| 403 | `invalid_key`, `revoked`, `expired`, `seat_limit` | **stop the app** |
| 403 | `forbidden` | `POST /v1/presets` only — valid licence, not an admin |
| 403 | `not_granted` | `/v1/library` only — valid licence, no tab that uses the library. The app shows the message in the picker and keeps running |
| 404 | `no_library` | `/v1/library` only — no `libraries` row by that name |
| 429 | `rate_limited` | `/v1/build` only — the per-licence hourly download cap |
| 503 | `server_error`, `no_build` | transient — retry at startup, ride the grace window if running |

Keep the 403/503 split intact. Any database failure must surface as 503,
or an Atlas outage reads to your customers as a licence violation; and a
real violation must never surface as 503, or the client's grace window
makes it survivable.

Revocation propagates because `/v1/heartbeat` re-reads the licence on
every call — flipping `active` to false stops running instances within
about a heartbeat, rather than only blocking their next start.
