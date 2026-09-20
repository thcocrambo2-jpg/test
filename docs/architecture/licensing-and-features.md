# Licensing and features

How a licence key decides what a pod can do, and what happens when the
licence server is slow, unreachable or says no. For someone changing the
code, and for whoever issues keys.

## Features

Every tab is a feature, switched on or off before the app downloads
anything. A feature that is off costs nothing: no tab, no custom nodes, no
weights on disk.

**Which tabs a pod gets is decided by its licence key, and by nothing
else.** There is no environment variable for this. The licence names a
**plan**, the licence server resolves that to a feature list, the app
reads it off the acquire response, and a customer cannot switch a tab on
by editing their pod template. Set it when you issue the key:

```bash
# bash, in license-validator/
npm run issue-key -- --name "Acme Corp" --plan creator --seats 2
```

The keys are `features.Key` in [`ember/features.py`](../../ember/features.py),
and each one has an entry in `features.FEATURES` naming its label and the
asset groups it needs.

| Key | Tab | Extra download |
| --- | --- | --- |
| `krea_t2i` | 🎨 Krea2 | ~26 GB (Krea 2 base, shared) |
| `krea_v2_t2i` | 🔶 Krea2 V2 | ~17 GB |
| `gallery` | 🖼️ Gallery | none |
| `krea_edit` | ✨ Krea2 Edit | ~1.9 GB + base |
| `krea_v2_edit` | 🔷 Krea2 V2 Edit | ~1.9 GB + V2 |
| `wan_i2v` | 🎬 Wan Video | ~49 GB |
| `minimax_i2v` | 🎥 MiniMax I2V (video with sound) | ~56 GB, shared with `minimax_t2v` |
| `minimax_t2v` | 🎞️ MiniMax T2V (video with sound) | shared with `minimax_i2v` |
| `community_prompts` | 🌟 Prompt Library | none |

The three shipped plans stack: `starter` is `krea_t2i`, the gallery and
the prompt library; `creator` adds Krea 2 V2 and both edit tabs; `studio`
adds Wan and both MiniMax video tabs. `license-validator/README.md` has
the full table and how to change it.

**Shared weights are handled for you.** The `needs` tuple on each
`Feature` names asset groups, not files, so granting `krea_edit` fetches
the same Krea 2 base models as `krea_t2i` and granting both fetches them
once. `krea_v2_edit` is the same trick one level up: it shares the
Identity Edit LoRA with `krea_edit` and the ~17 GB of V2 weights with
`krea_v2_t2i`, so its own cost is only whichever of those two is not
already granted. The MiniMax pair name one group between them, which is
why the second tab is free.

A new tab lands on the internal `admin` plan first and is added to a
public plan in `license-validator/src/plans.js` when it should be
something a customer can buy.

**Keys are permanent and names are not.** A key is compiled into every
shipped binary, so renaming one drops that tab for anyone on an older
build — the client warns and ignores it. There is no alias map for old
names. `Key` is a `str` subclass so the value is the wire contract and the
member name is local; rename the member freely, never the value.

**The one override only ever says no.** `Feature.enabled` in the registry
can switch a tab off even for a licence that grants it — a build-time kill
switch for a tab that is written but not launched. Changing it means
shipping a new binary, so it is nothing a licence can be talked into.

A licence with **no plan and no features** falls back to the registry
defaults — the features declared `default=True`, today `krea_t2i`,
`krea_v2_t2i`, `gallery` and `community_prompts` — and logs a warning
saying so. That exists for keys issued before entitlements did; put
anything current on a plan, because "the build's defaults" is a moving
target across releases.

Changing what a key grants takes effect on the customer's **next start**,
whether you changed the licence or the plan it sits on. A running instance
notices within a heartbeat and logs that a restart is needed, but does not
apply it: the tabs are resolved once at launch and the weights a newly
granted tab needs were never downloaded.

## Licensing

The app takes a **licence seat** before it does anything else and will not
start without one. Set `KREA2_LICENSE_KEY` on the pod to the key you were
given, and `KREA2_NODE_TAG` to the node tag issued with it; one key allows
a fixed number of instances running at the same time. Both are required —
the tag names the deployment the key checks in against and there is no
built-in default, so a pod missing either one stops at startup.

The node tag is validated as a single DNS label and nothing more. A dot, a
slash, a colon or a port is how a tag would smuggle in a different host, so
a malformed value is rejected outright rather than stripped down to
whatever is left.

The same check returns the key's **entitlements** — the tabs it grants.
They are applied immediately after the seat is taken, before the ComfyUI
clone and the downloads, which is what makes a feature that is off cost
nothing rather than be hidden after the fact. See
[overview.md](overview.md) for where that sits in the startup order.

Seats are **leases rather than a counter**, so an instance that dies
without releasing — `SIGKILL`, an OOM kill, a hard pod terminate — frees
its own seat within a few minutes with nothing to clean up. Stopping the
app cleanly returns it immediately. Restarting on the same pod reclaims
the same seat rather than spending a second one, because `RUNPOD_POD_ID`
is used as the instance identity when it is present, with the pod hostname
as the fallback.

If the licence server becomes unreachable *while* the app is running, a
background heartbeat keeps it going for `KREA2_LICENSE_GRACE` seconds
(default 1800) so an outage does not kill a long video render. Past that,
the app stops itself.

Exit codes:

| Code | Meaning |
| --- | --- |
| 2 | no key, or no node tag (or a malformed one) |
| 3 | key rejected — invalid, revoked, expired, or all seats in use |
| 4 | licence server unreachable at startup, after the retries |
| 5 | the licence stopped being valid mid-run |

## The licence gate on the API

The entitlement list is not only a download list; it is enforced on every
route. The React bundle is one build for every licence and learns what it
may show from `/api/v1/session` over the wire, so there is nothing about
import order or rendering that could gate anything. Four layers do it
instead, across [`ember/web/routes/`](../../ember/web/routes/):

1. every per-tab route is registered inside `_mount_tabs()` in
   [`routes/tabs.py`](../../ember/web/routes/tabs.py), through a loop that
   attaches `Depends(require_feature(key))` — the dependency itself lives
   in [`routes/common.py`](../../ember/web/routes/common.py). There is no
   other way to add one;
2. `tabschema.submit()` checks again — the funnel every generation passes
   through, so a route registered by accident still cannot run one;
3. `/api/v1/catalog` and `/api/v1/schema/{tab}` describe only entitled
   tabs, so the navigation never learns an ungranted tab exists;
4. `scripts/check_routes.py` imports the app with `features.resolve([])`
   and asserts every `/api/v1/tabs/` path answers 403.

The partial backstop underneath all of that is that weights for ungranted
features are never fetched, so most tabs would fail at "the model is not
downloaded" anyway. One tab has no weights at all: `community_prompts`
declares `needs=()`, so for it the four layers are the whole of the gate.

The server lives in `license-validator/` — see its README for issuing keys
and deploying, and [Plans and entitlements](../../license-validator/docs/plans-and-entitlements.md) for
how a plan resolves into a feature list.
