# Prompt library

The 🌟 Prompt Library tab: a catalogue of working recipes for the 🎨 Krea2
and 🔶 Krea2 V2 tabs. For someone using it, for the person reviewing
submissions, and for someone changing how capture works.

Every card carries a prompt and the whole settings blob behind it.
**Use** writes all of it into that tab's controls and switches to it.
Two kinds of card:

- **⭐ Official** — written by you, with `npm run prompts -- --add`.
  Public the moment they exist.
- **👥 Community** — captured from customers' own generations. **Private
  on arrival**, and invisible until approved.

The pod's side is [`ember/licensing/prompts.py`](../../ember/licensing/prompts.py);
the collection is `community_prompts` on the licence server.

## Capture is silent, and deduplicated

`generate_single` in
[`ember/pipelines/krea2/handler.py`](../../ember/pipelines/krea2/handler.py)
and `generate_v2` in
[`ember/pipelines/krea2_v2/handler.py`](../../ember/pipelines/krea2_v2/handler.py)
call `prompts.record()` with what they were given, right after their
validation guards and before any work. That call cannot slow generation
down and cannot fail it: it fingerprints the recipe, drops it on a
bounded queue, and one daemon thread does the HTTP. Every error on that
path dies in a `debug` log — **nothing about this is ever shown to the
customer.**

### The fingerprint

A sha256 over the prompt, the negative and every setting **except the
seed, the 🎲 toggle and the batch count**. That exclusion is the point:
with a random seed every click would otherwise look like a new prompt,
and twenty re-rolls of one idea would be twenty requests and twenty rows.

Those three values are still *stored*, so a loaded prompt arrives with
the seed it was made with — they just do not decide whether two recipes
are the same one.

The pod remembers the last 500 fingerprints; the unique index on the
server collapses whatever gets past that — a restart, a second pod on one
key, two customers who typed the same thing.

## Your own pods do not capture (`is_admin`)

A licence marked `is_admin` behaves differently, and only here. It is a
**role, not an entitlement**: it grants no tab and changes nothing about
what a pod can generate.

```bash
# bash, in license-validator/
npm run issue-key -- --name "Internal" --plan admin --seats 3 --admin
npm run issue-key -- --key EMBER-XXXX-XXXX-XXXX --admin --update
npm run issue-key -- --key EMBER-XXXX-XXXX-XXXX --no-admin --update
```

On an admin pod **nothing is captured automatically.** Instead the 🎨
Krea2 and 🔶 Krea2 V2 tabs grow a `⭐ Publish this prompt to the library`
checkbox above Generate, off by default, with an optional card title next
to it. Tick it, press Generate, and that prompt goes live as an **⭐
official** card immediately — no review, because you are the reviewer.
Untick it and nothing is stored at all.

The box unticks itself once the run finishes, clearing the title with it:
publishing is a per-image decision, so it cannot stay armed into the next
generation by accident.

That split exists so your own testing does not fill the review queue you
are the one working through, and so an official prompt is something
chosen rather than something collected.

Two details worth knowing:

- The checkbox is **hidden, not disabled**, for customers. A greyed-out
  "publish to the library" box would tell them their prompts are being
  saved, which is the one thing this feature must not do. The control is
  still built — a tab's input list is fixed at build time — and a hidden
  checkbox sends `False`, so a customer pod captures exactly as before.
- Publishing **bypasses the deduplication**. It is one deliberate tick of
  a checkbox, not a recipe the app happened to notice, so publishing the
  same prompt twice really does create two cards.

The server enforces both halves against the licence document rather than
trusting the pod: `publish: true` from a licence without `is_admin` is
not an error, it is simply ignored and stored as an ordinary community
submission.

## Approval

Nothing a customer submits is public until you say so:

```bash
# bash
cd license-validator
npm run prompts                      # the review queue
npm run prompts -- --show <id>       # prompt, settings, which licence
npm run prompts -- --approve <id>
npm run prompts -- --reject  <id>    # decided, and never queued again
```

`--reject` is not a delete. It sets `reviewed_at` and leaves `is_public`
false, so the row leaves the queue for good — and because the fingerprint
stays unique, the same recipe submitted again tomorrow collapses onto the
row you already ruled on instead of coming back as new work.

One licence may have at most **200 un-reviewed prompts** waiting. Past
that its writes are dropped and answered `200`: a customer who was never
told their prompts are saved cannot be told they have been throttled.

## Cross-pod safety

A prompt written on one pod is loaded on another that may have different
models and LoRA files. Every value is checked against what *this* build
offers before it is applied:

- an unknown model, resolution or sampler leaves its control alone;
- a LoRA id this tab does not offer resets that slot to `None` and
  switches it off;
- numbers are clamped into their slider's range.

Models and LoRAs are stored by **catalogue id, never by file name** — see
[the catalogue](../pipelines/catalogue.md). A card always loads; worst
case it loads slightly less of itself.

A card for a tab this licence does not grant still renders, with its
button reading `🔒 Needs Krea2 V2` instead of being hidden. What the tab
you have not bought can do is exactly the thing worth seeing.

## Related

- [presets](presets.md) — the same settings blob and the same guarding,
  minus the words.
- [Licensing and features](../architecture/licensing-and-features.md) — what `is_admin` is not.
