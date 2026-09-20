# Telegram bot

Selling licences in Telegram, paid in Telegram Stars. For whoever runs
sales and has to unstick an order.

It is the same deployment, the same database and the same licence
documents — a key bought in the bot is byte-indistinguishable from one
issued with `npm run issue-key`, which is why nothing on the pod side
knows the bot exists.

```text
customer → Telegram → POST /tg/webhook/<path> → orders.js → provision.js → Mongo
                      secret_token header        state       the only
                      + random path              machine     licence writer
```

## Setup

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`, then register:

```bash
npm run set-webhook -- --url https://<node-tag>.vercel.app
npm run set-webhook -- --show     # what Telegram thinks, and its last error
npm run set-webhook -- --delete
```

**With either variable unset the whole bot answers 404**, exactly as
`/v1/admin/*` does without an `ADMIN_TOKEN`. Deploying this code to a
service that is not selling anything changes nothing.

`TELEGRAM_WEBHOOK_SECRET` does two jobs from one value: Telegram sends it
back in the `X-Telegram-Bot-Api-Secret-Token` header, and the random path
segment the webhook is mounted at is a one-way hash of it (`webhookPath()`
in [`../src/telegram/router.js`](../src/telegram/router.js)). The path
will appear in proxy and access logs and reveals nothing about the header
token an attacker would also need. Rotating the variable rotates both.

`TELEGRAM_ADMIN_IDS` lists the Telegram user ids allowed to run the bot's
admin commands. It is config, not a secret — Telegram authenticates the
sender, so knowing an id grants nothing.

## What a customer can do

`/start` · `/plans` · `/buy` · `/mykeys` · `/renew` · `/help`

Prices come from `price_stars_monthly` on the plan documents, read through
the same `allPlans()` the pricing page uses. **A plan with no Stars price
cannot be bought**, which is what keeps the non-public plans off the shelf
without a second list of what is for sale. `price_stars_monthly` is
deliberately absent from `/v1/plans`.

Tapping a tier means one of three things, decided by what the customer
already holds:

| They hold | They tap | What happens |
| --- | --- | --- |
| nothing | any tier | a new key, term starts today |
| Creator | Creator | **renew** — the term is added to what is left |
| Creator | Studio | **upgrade** — plan changes, term restarts today |

A revoked licence counts as no licence, so a payment can never quietly
reinstate somebody who was cut off.

`ORDER_MAX_OPEN_PER_USER` (10) caps how many orders one Telegram user may
have open at once — `CREATED` or `INVOICED`. It exists to stop one user
filling the collection with invoices they never intend to pay, not to
police honest customers, who have one open order at a time.

## The delivery message carries two values

`KREA2_LICENSE_KEY` **and** `KREA2_NODE_TAG`. A pod with the key and no tag
exits with code 2 before it prints anything —
[`../../ember/settings.py`](../../ember/settings.py) builds
`LICENSE_API_URL` from the tag, validating it as a single DNS label — and
there is no key-entry screen anywhere in the app; both are environment
variables. The bot refuses to sell at all while `KREA2_NODE_TAG` is unset,
checked before the invoice rather than after the money.

## When something goes wrong

The webhook **always answers 200**. Telegram retries any non-2xx for 24
hours, which is the right thing before a payment and the wrong thing after
one, so failures live in the order's status and the log instead.

The charge is recorded before any licence work begins, so a purchase cannot
be lost. Anything left unfinished is picked up by the sweep, which calls
the same code the webhook does:

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

> **The sweep runs once a day, at 03:00 UTC** — the `crons` entry in
> [`../vercel.json`](../vercel.json), pointing at
> `/internal/cron/sweep`. Hobby projects allow only one cron run per day,
> and a shorter schedule is rejected at deploy time. That is survivable
> because the sweep is recovery and not the happy path — the webhook
> provisions and delivers within a second of the payment, and the sweep
> only exists for the invocation that died mid-flight. What it does mean
> is that an order the webhook failed to finish can sit for up to a day,
> so when something is known to be stuck, do not wait for it:
>
>     npm run orders -- --status FAILED_PROVISION
>     npm run orders -- --sweep
>
> Both run from a laptop, need no deployment, and do exactly what the cron
> would have done.

The cron route is guarded by `CRON_SECRET`, deliberately **not**
`ADMIN_TOKEN`. That token already authorises publishing a build, promoting
a release channel and moderating prompts; the sweep needs none of it, and
widening it so a cron job can retry a provisioning step would trade a real
blast radius for a saved environment variable.
