# The pricing page

**💳 Plans & pricing** is a page of its own next to the tabs: every public
plan, what it costs, and the tabs it includes, with the customer's own
tier marked, followed by a showcase of what each feature does. For
someone running a pod, and for whoever edits the plans and the copy.

It is reachable from the **Plans & pricing** button in the app bar, next
to the plan and expiry that bar already shows.

## Where the plan data comes from

The licence server's own catalogue.
[`ember/licensing/plans.py`](../../ember/licensing/plans.py) reads
`GET /v1/plans` at page load and caches it for five minutes, so a price
or a feature list edited in Atlas shows up without redeploying the app or
restarting the pod.

Prices are quoted per billing cycle. The server sends the cycles it is
offering and what each one costs, and the page renders a tab per cycle —
Monthly, Quarterly, Yearly — with the saving badged on the tab and on the
card. Cycles that are switched off are not sent and get no tab, and with
only one cycle live there is no tab bar at all. Launching quarterly or
yearly pricing is therefore a flag on the licence server: no rebuild
here, and no new binary for the pods.

**Reading this page changes nothing.** Which tabs a pod builds still
comes only from the flat `features` array in the acquire response, so a
catalogue that is stale, empty or unreachable costs the page its cards
and nothing else — it renders a panel saying so, with a Refresh button,
and every tab keeps working.

## "Your plan", and the expiry pill

The tier marked "Your plan" comes from `plan_id` on the acquire response.
A licence that lists its features directly instead of naming a plan has
none, which is normal: the page then marks nothing and says so, and the
app bar calls it a "Custom licence".

The bar's expiry pill comes from `expires_at` on the same response
(`expires_at()` in
[`ember/licensing/seat.py`](../../ember/licensing/seat.py)), and it is
**display only**. The server refuses an expired key at acquire and stops
answering its heartbeat, so nothing in the app reads that date to decide
anything, and a pod with a wrong clock cannot lock a customer out of a
valid key. A key with no end date, and a licence server too old to send
the field, both render no pill at all.

## The feature showcase

Everything under the plan cards comes from two places:

- **the copy** — `assets/showcase/showcase.json`, compiled into the
  binary: a few kilobytes of text, and the thing that decides whether the
  section renders at all;
- **the pictures** — a public Cloudflare R2 bucket, fetched by the
  customer's browser.

[`ember/web/showcase.py`](../../ember/web/showcase.py) reads the JSON and
builds the URLs. **The app never downloads an image itself.** The URLs go
into the page and the browser fetches them lazily, so a page of forty
screenshots costs this app nothing and costs the binary nothing — which
is the whole reason the pictures live in a bucket. Nothing checks whether
a picture is actually there; the app cannot know without making the
request itself, and it will not.

### Pointing it at the bucket

Set one environment variable on the pod, or on your machine for a dev
preview:

```bash
# bash
export KREA2_SHOWCASE_URL=https://pub-<hash>.r2.dev
```

It must be `https://`; anything else is ignored with a warning at
startup. A trailing `/`, a query and a fragment are stripped. Leave it
unset and the section still renders in full — every picture becomes a
placeholder tile naming the file it wants, which is a perfectly good way
to work on the copy before the bucket is filled, and the quickest way to
see every path the page currently expects.

This is a **public URL, not a credential.** It belongs in the RunPod
template alongside the other non-secret settings, so every pod cloned
from it renders the page without the customer configuring anything. It is
deliberately *not* the private bucket the app binary is published to:
public access on R2 is a per-bucket setting, so one bucket cannot be both
world-readable and gated.

No CORS configuration is needed — plain `<img>` and `<video>` loads do
not require it; only `fetch()` would.

### The bucket layout

It mirrors the JSON exactly: **feature key → the block's `dir` →
filename.**

```
<bucket>/
  krea_t2i/hero/hero.webp
  krea_t2i/gallery/01.webp … 12.webp
  krea_edit/compare-a/before.webp, after.webp
  wan_i2v/clip-a/frame-01.webp, clip.webp   ← .mp4/.webm play as video
  gallery/screen.webp                        ← a block with "dir": "."
```

**Every file must be named in `showcase.json`, extension included.** A
bucket has no directory listing to walk, so "render whatever is in this
folder" is not something the app can offer — uploading a thirteenth
gallery image means adding one line. Filenames are restricted to letters,
digits, `.`, `_` and `-`; anything else is refused and shows a
placeholder instead.

`assets/showcase/README.md` is the fuller guide to authoring the bucket:
setting up public access in Cloudflare, the aspect-ratio and cropping
rules, video blocks, and the image sizes worth keeping to.

### What renders, for whom

Every shipped feature is described **whether or not the licence grants
it** — the section sits under the price list, and the reader is deciding
whether to upgrade, so a tab they do not have is exactly the one worth
showing them. Granted tabs get a green "In your plan" chip; the rest get
an upgrade line linking back to the plans. Nothing is dimmed, because a
washed-out screenshot is a poor argument for buying the thing in it.
Setting `showcase.SHOW_LOCKED = False` reverts to describing only what
this licence runs.

Two things are never rendered: a feature key with no entry in
`showcase.json`, and a feature switched off in the `ember/features.py`
registry (`Feature.enabled`). No plan can grant that one — it is a tab
this *build* cannot construct — so listing it would be selling vapour.

### Checking your work

```bash
# bash
python scripts/dryrun.py --features all
```

No pod, no GPU and no licence server needed. Open **💳 Plans & pricing**
and scroll.

## Related

- `docs/architecture/licensing-and-features.md` — the acquire response,
  `features`, and what a plan is.
- `docs/configuration.md` — `KREA2_SHOWCASE_URL` and the rest.
- `docs/running/dry-run.md` — the no-GPU preview.
