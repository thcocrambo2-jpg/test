# Settings presets

The **⚙️ Preset** dropdown on the 🎨 Krea2 and 🔶 Krea2 V2 tabs, what
the two Edit tabs do with it, and the one list the two MiniMax tabs share. For someone using the tabs, for the admin
who writes presets, and for someone changing how they apply.

Picking a preset writes every control below it — model, steps, CFG,
resolution, sampler, seed, batch count, the whole LoRA stack — and
**leaves the prompt boxes alone.** That is the entire difference between
a preset and a [prompt-library](prompt-library.md) card: same settings
blob, same guarding, minus the words.

Presets live on the licence server, so changing one changes it for every
customer without shipping a binary. The pod's side is
[`ember/licensing/presets.py`](../../ember/licensing/presets.py), whose
`TABS` is `krea_t2i`, `krea_v2_t2i` and `minimax_i2v`.

## The Edit tabs read the same list

**✨ Krea2 Edit** shows 🎨 Krea2's presets and **🔷 Krea2 V2 Edit** shows
🔶 Krea2 V2's — the same dropdown, at the top of the same column, filled
from the generation tab it shares a pipeline with. A recipe is a recipe
whether the pixels come from noise or from an uploaded image, and
dialling one back in by hand to edit with it was the whole friction.

Nothing is saved twice. A preset still belongs to the tab it was saved
from — one row in one collection — and an Edit tab simply reads it and
writes the part it has controls for:

| Not applied on ✨ Krea2 Edit | Not applied on 🔷 Krea2 V2 Edit |
| --- | --- |
| `resolution` — the source image sets the output size | `aspect`, `megapixels`, `multiple` — likewise |
| | `denoise` — the source reaches the model through conditioning, not the starting latent |
| | Sharpen and film grain — generation-tab controls |

Everything else transfers as stored: model, steps, CFG, sampler, seed,
randomize, batch count and the LoRA stack on ✨ Krea2 Edit; the whole
ClownsharKSampler and Smart Seed Variance blocks and the LoRA rows on 🔷
Krea2 V2 Edit. The controls that belong to editing alone — grounding,
reference fidelity, the second-reference switch, the fit mode — are left
exactly where you set them, because no preset carries them.

The `is_default` preset is applied to the Edit tabs on page load too, so
a pod whose Krea2 default says 12 steps does not open its Edit tab at 8.
The 🔄 button and the save-from-the-queue refill work there as they do on
the generation tabs.

Saving is unchanged: the `💾 Save these settings as a preset` tickbox is
a generation-tab control, so an Edit tab reads presets and never writes
one.

## The MiniMax tabs share one list

🎥 MiniMax I2V and 🎞️ MiniMax T2V run one model over one LoRA list, so
they share one preset list, filed under `minimax_i2v`. Both are
generation tabs, so both read it and both have the save tickbox (without
the publish one — there is no prompt library behind them). A MiniMax
preset has **no `model`**: the licence server's `MODELLESS_TABS` accepts
a blob without one for this tab, and refuses one that has one. A preset
saved from T2V carries an aspect ratio that I2V skips. See
[minimax.md](../pipelines/minimax.md#presets).

## Only an admin can write one

Same gesture as publishing a prompt, and the same rule behind it. An
admin pod grows a `💾 Save these settings as a preset` checkbox next to
the publish one, with an optional name box beside it. Tick it, press
Generate: whatever the controls hold at that moment is saved and appears
in the dropdown for every pod on its next start or 🔄. The box unticks
itself when the run finishes, exactly like publishing.

Unlike publishing, this one **reports back** — a first line on the status
box saying `✅ Preset "Portrait · soft light" saved.` or why it did not.
Prompt capture is silent because the customer was never told it happens;
a preset is a box you deliberately ticked and are waiting on.

The customer's half of the tab is read-only: the checkbox is hidden
(their input list is fixed at build time, and a hidden checkbox sends
`False`), and `POST /v1/presets` refuses `403 forbidden` for any licence
the document does not mark `is_admin`. The server checks the record,
never the flag on the request.

## Every tick is a new preset

Load `Default`, change six things, tick, Generate — you get a **new**
preset and `Default` is untouched. That is the ordinary gesture, so
saving never overwrites:

- **A name already in use** becomes `Portrait (2)`, then `(3)`. The
  status line names whichever it got (`✅ Preset "Portrait (2)" saved.
  (that name was taken)`), because it is not always the name you typed.
  Twenty of one name is the cap, and past it the save is refused rather
  than looping.
- **A blank name** is stamped with the time — `Preset 2026-08-09 15:19` —
  rather than refused. A ticked box that silently saved nothing is the
  worse outcome; rename it afterwards with
  `npm run presets -- --rename <id> --to "…"`.

The dropdown selection is left where it is unless the name you typed is
exactly what got stored, so it never claims to be showing a preset that
is not the one just written.

Editing a preset **in place** is deliberately the admin tools' job, where
naming an existing preset is the whole point of the call:

```bash
# bash, in license-validator/
npm run presets -- --add --tab … --name "Portrait" --settings ./x.json
```

That overwrites `Portrait`'s settings and leaves its flags alone.

The `(tab, name)` unique index is what makes all of this safe: it is the
only race-free answer to "is this name free", so two admins saving the
same name in the same second get two presets rather than one landing on
the other's row.

## The enable flag, and the default

```bash
# bash
cd license-validator
npm run seed-presets                           # the shipped defaults, as presets
npm run presets                                # list everything, on and off
npm run presets -- --show <id>                 # one preset's whole settings blob
npm run presets -- --disable <id>              # out of every dropdown, kept in the db
npm run presets -- --enable  <id>
npm run presets -- --default <id>              # what a fresh session opens on
npm run presets -- --order <id> --to 10        # where it sits in the dropdown
npm run presets -- --rename <id> --to "Portrait, soft"
npm run presets -- --delete <id>
```

Two flags decide what customers see:

- **`enabled`** — `GET /v1/presets` returns nothing else. `--disable` is
  the tool for a preset that turns out to be wrong: it leaves the
  dropdowns immediately and the row stays put, so `--enable` brings it
  back with nothing retyped. `--delete` is for the preset that should
  never have existed.
- **`is_default`** — at most one per tab, and it is the one a **fresh
  page load applies**, to that tab and to its Edit tab. Setting it on one
  clears it on every other preset for that tab, so there is only ever one
  answer to "what does this tab open on".

`npm run seed-presets` writes each tab's usual settings as a preset
called `Default` on each tab and marks it `is_default`. On the V2 tabs
that preset is also what switches the usual LoRA rows on — every row
starts off, one per LoRA in the tab's catalogue list. Nothing about what
anyone gets changes on the day you run it: it makes what everyone already
gets nameable, and therefore editable from Atlas without a rebuild. The
compiled values stay the floor — they are what the controls are built
with, and what a pod that cannot reach the server keeps.

Re-running the seed updates the settings and **never touches the two
flags** — a preset you switched off stays off — the same `$set` /
`$setOnInsert` split `seed-prompts` makes.

## What a preset costs at startup

The list is fetched once, for all tabs in one request, after the seat
check, so the licence server is already on that path. A server that is
slow or down costs the dropdown and nothing else: every control keeps its
compiled default and the tab generates normally. A fetched set is served
for `TTL_SECONDS` (300 s) without asking again, and failures are cached
for `ERROR_TTL_SECONDS` (30 s), so a bad minute does not put a timeout on
every page load.

## Cross-pod safety

Identical to the prompt library's, and the same code: an unknown model,
resolution or sampler leaves its control alone, a LoRA id the tab does
not offer resets that slot to `None`, numbers are clamped into their
slider's range, and a preset name that has since been disabled or
renamed applies nothing rather than blanking the tab.

One wrinkle worth knowing, shared with the library's **Use** button:
applying a preset that names a *different* model fires the Model
dropdown's own change handler, so Steps and CFG land on that model's
variant defaults rather than the preset's. Everything else applies as
stored, and a preset for the model already selected — the ordinary case,
and the only one when a tab's catalogue list holds a single model — is
unaffected.

## Related

- [prompt library](prompt-library.md) — the same blob, with the words.
- [the catalogue](../pipelines/catalogue.md) — why ids and not file names.
