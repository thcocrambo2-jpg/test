# Plans and entitlements

Which tabs a licence key grants, and how the server works that out. For
whoever issues keys and edits tiers.

Which tabs a key grants is the only thing that decides them — the app has
no environment variable that can switch a tab on, so a customer cannot
grant themselves Wan's ~49 GB by editing their pod template.

A licence names a **plan** rather than a feature list. The server resolves
it at request time, so editing one plan document moves every customer on
that tier instead of requiring a bulk update across `licenses`.

```bash
npm run seed-catalog                                             # once, and after editing plans
npm run issue-key -- --name "Acme Corp" --plan creator --seats 2
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update
npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
```

## Shipped plans

`DEFAULT_PLANS` in [`../src/plans.js`](../src/plans.js) is the seed list.
Three tiers are public:

| Plan | ₹/mo | ⭐/mo | Grants |
| --- | --- | --- | --- |
| `starter` | 599 | 500 | `krea_t2i`, `gallery`, `community_prompts` |
| `creator` | 999 | 850 | + `krea_v2_t2i`, `krea_edit`, `krea_v2_edit` |
| `studio` | 1799 | 1550 | + `wan_i2v`, `minimax_i2v`, `minimax_t2v` |

Three more carry `is_public: false` and never reach the pricing page or
the bot: `admin` (every tab the binary can build, and where a new tab
lands first), `admin-minimal`, and `test-krea1-only`.

`krea_t2i` is turbo-only by its model list — the Raw model is not in
`feature_assets.krea_t2i`, see [Catalogue data](catalogue-data.md) — which
is why V2, turbo and raw, starts at Creator.

Prices are **documentation, not enforcement.** Nothing on this side
charges anyone outside the [Telegram bot](telegram-bot.md); `expires_at`
is the only lever that actually stops a key working.

`seats` is deliberately not a plan field. Seat count lives on the licence
because it is per-deal negotiable, and resolving it through the plan would
mean an edit to `studio` retroactively changed how many pods every studio
customer may run.

## Resolution order

| On the licence | Result |
| --- | --- |
| `features` is an array | exactly that, plan ignored |
| `plan_id` is set | `plan.features` + `license.features_extra` |
| neither | `null` — the app's built-in defaults, with a warning |

Rule 1 is why this needed no migration: a key carrying a literal
`features` array behaves exactly as it always did. It is also the escape
hatch for a deal that fits no tier — but prefer `features_extra`, which
keeps the customer on a plan and so keeps them moving when the plan moves.
`--plan` on an `--update` clears any literal array and says so, since a
leftover one would silently win and make the new plan a no-op.

An empty `features: []` is a deliberate "this key starts and does
nothing", not "no opinion" — it overrides a plan like any other array.

## Rules worth keeping

- **Plans never leave `seatPayload()`.** `/v1/acquire` answers with a flat
  `features` array, so the Python client knows nothing about tiers and
  needs no rebuild for any of this.
- **A `plan_id` naming nothing is a 503**, not a downgrade. Falling back to
  the client's defaults would hand a Studio customer three tabs and look
  like their fault; granting everything would be worse. 503 is retryable,
  rides the client's grace window, and puts the reason in the log.
- **Never delete a plan that has licences on it.** `seed-catalog` refuses
  to delete anything and reports orphans; `/v1/admin/plans` shows the
  licence count per plan, which is the number to check before editing one.
- **The `FEATURES` registry in [`../src/features.js`](../src/features.js)
  is used at issue time only**, never to filter what `/v1/acquire` returns.
  The app and this service deploy separately, so a key granting a tab that
  shipped before this list was updated must still work. Typos are caught
  where they are made instead.
- **`all` and `none` are expanded when the key is written**, not stored as
  sentinels. Reading a licence then never requires knowing what `all` meant
  on the day it was issued.
- **`plan_name` and `expires_at` on the payload are labels.** The app's own
  header shows the tier and the date next to the brand, and the pricing
  page marks the tier as "Your plan" — nothing branches on either. Expiry
  is enforced here, in `licenseProblem()`, on every acquire and every
  heartbeat, so a client that ignores the date is no less bounded by it.
  `null` means the key never expires, and the header then shows no date
  rather than "expires never".

`features` rides on every 200 — acquire, where the client applies it, and
heartbeat, where a change makes a running instance log that it needs a
restart. It is deliberately not applied live: tabs are built once at
launch and a newly granted tab has no weights on disk behind it. This
applies to **plan** edits too: changing `creator` tells every running
Creator customer, within a heartbeat, that they should restart. The plans
collection is cached in module scope for 60 s (`PLAN_TTL_MS`), so an edit
reaches a running pod after that plus one heartbeat.

## Feature keys

Keys are permanent; names are not. A key is written into licence documents
and compiled into every shipped binary, so renaming one silently drops that
tab for anyone on an older build. `name`, `description` and `category` are
display-only — fix naming there.

Model-bound tabs use `<model>_<task>`; tabs that are not tied to a model
keep a plain name. The registry, in `sort_order`:

| Key | Name | Category |
| --- | --- | --- |
| `krea_t2i` | Krea2 | generation |
| `krea_v2_t2i` | Krea2 V2 | generation |
| `gallery` | Gallery | tools |
| `krea_edit` | Krea2 Edit | editing |
| `community_prompts` | Prompt Library | tools |
| `krea_v2_edit` | Krea2 V2 Edit | editing |
| `wan_i2v` | Wan Video | video |
| `minimax_i2v` | MiniMax Video | video |
| `minimax_t2v` | MiniMax Text to Video | video |

There is **no alias map** for keys that were named differently before any
key was issued — nothing needed translating, and a permanent map that
translates nothing is a trap for whoever reads it next. An unknown key is
simply unknown: the client warns and ignores it. If a key ever has to
change after launch, reissue the affected licences.

Feature keys and the app's *asset groups* are separate namespaces that
happen to overlap. A key names a tab; a group names a set of weights
several tabs share, and
[`../../ember/weights/downloads.py`](../../ember/weights/downloads.py) is
keyed on the latter — which is why `krea_v2_t2i` still needs the group
called `v2`. Renaming a tab never touches a download.
