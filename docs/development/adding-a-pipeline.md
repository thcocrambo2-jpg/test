# Adding a pipeline

For someone changing the code. A new model family is not one file — it is
a tab, a graph, a set of weights, a licence entitlement, a catalogue
entry and a snapshot, and every one of them is in a different place.
This is the list, in the order it is least annoying to do them, and what
each one is for.

Nothing here is enforced by a framework. The only things that will tell
you that you missed a step are [the checks](checks.md), and most of them
only speak up once the step exists and disagrees with something.

## The touch points

### 1. `ember/pipelines/<name>/`

Three files: an **empty** `__init__.py`, a `constants.py`, and a
`workflow.py`.

`constants.py` holds facts about the model and its graph — repos,
filenames, node packs, sampler and variance defaults, aspect tables,
dropdown lists, negative prompts, frame grids. Nothing read from the
environment goes here; see [conventions](conventions.md#the-three-config-tiers).

`workflow.py` builds the ComfyUI graph as a plain dict. Anything shared
with another pipeline goes in `ember/pipelines/common.py` rather than
being imported sideways from a sibling.

### 2. A `generate_*` handler

In `ember/pipelines/<name>/handler.py`, beside the `workflow.py` it
drives. It takes the form's values as **positional parameters**, builds
jobs, and hands them to `runner._run_jobs` with its builder. That seam is also where
`golden.py` takes its snapshot.

### 3. A schema

A `TabSchema` in `ember/web/schema/tabs/<name>.py`, added to `SCHEMAS`
in [`ember/web/tabschema.py`](../../ember/web/tabschema.py). This is the
hand-written description of the form the React app renders: every
control, its label, its default and its choices.

`_assert_signatures()` runs at import and diffs each schema's declared
field names against `inspect.signature(handler)`. It is the most
important defensive measure here, because the failure it prevents is
silent: rename a parameter in the handler, forget it in the schema, and
every argument after it shifts by one. `generate_v2` has 31 of them.

### 4. A `features.Key` and a `Feature`

In [`ember/features.py`](../../ember/features.py). Add the member to
`Key` and the record to `FEATURES`.

The **key value is a wire contract** — it appears in licence documents,
in the licence server's catalogue and in the acquire response — so it can
never change once it has shipped. The label is the safe thing to reword.
`enabled=False` is the kill switch for a tab that is written but not
launched: no licence can turn it back on, so the code can sit in place
while the feature waits.

`needs` names **asset groups**, not features. A group is a set of weights
several tabs can share, so renaming a tab never touches a download.

### 5. A download group

In [`ember/weights/downloads.py`](../../ember/weights/downloads.py): a
`download_<name>_models()` function and an entry in `ASSET_GROUPS` under
the name the feature's `needs` uses. `download_everything()` works from
the union of the groups the enabled features asked for, so a shared file
is fetched once.

If the tab offers catalogue models or LoRAs, it also names the `catalog`
group — the one group whose contents are not fixed in code. Add the group
to `CIVITAI_GROUPS` if anything in it comes from CivitAI, so the missing
token is reported before the download rather than during it.

New weights that are not org-backed also need a mirror entry and a pin:
see [the mirror and the pins](../releasing/mirror-and-pins.md).

### 6. Catalogue records

In [`license-validator/data/assets.json`](../../license-validator/data/assets.json),
then re-seeded into the licence database. The database is the source of
truth; the JSON is the seed document, and it is also what
`check_schema.py` reads when `EMBER_CATALOG_FILE` is unset.

**If the tab has LoRAs but no models**, add its key to
`LORA_ONLY_FEATURES` in
[`license-validator/src/assets.js`](../../license-validator/src/assets.js).
Otherwise `emptyModelsProblem()` refuses the empty `models` list — the
default rule is that a tab needs at least one model, and the first one in
the list is its default. The database side is in
[catalogue data](../../license-validator/docs/catalogue-data.md).

### 7. A showcase entry

A block under `features` in
[`assets/showcase/showcase.json`](../../assets/showcase/showcase.json),
keyed by the feature key, plus the images it references. Without it the
pricing page has a tab it cannot describe.

### 8. A golden case

A case in `scripts/golden.py` naming the handler and the arguments to
call it with, then `$PY scripts/golden.py` to write
`scripts/golden/<handler>.json`. Commit the snapshot. From then on
`golden.py --check` fails on any change to the graph the handler builds.

### 9. `scripts/parity_baseline.json`

The frozen record of every control, label, default and choice. Adding a
tab adds a section; `check_schema.py --choices` diffs against it.

### 10. If the Docker bake stage imports it

The bake stage copies **individually named files**, never a directory,
because the image is public. A new constants module reached by what the
stage imports has to be named in both the `Dockerfile` `COPY` lines and
the `.dockerignore` allow-list. Most pipelines' constants are not reached
and are deliberately not copied.

---

## A worked example: the MiniMax LoRA stack

The MiniMax tabs gained an eight-slot LoRA stack across three commits,
and they land on the touch points cleanly enough to read as a map.

**`4994d9a` — "Let the MiniMax tabs carry a LoRA list with no models"**
is the licence server alone. Both MiniMax keys go into
`LORA_ONLY_FEATURES`; `emptyModelsProblem()` learns to skip them; the
seed document gains the LoRA records and the two features' lists; the
validation in `app.js` and the seeding scripts follow. Nothing in the app
changed, and nothing could have, because the server has to be able to
*describe* the tab before the app can ask it to.

**`b52b79f` — "Add a LoRA stack to the MiniMax tabs"** is the app half,
and it is seven files plus three artefacts:

| File | What changed |
| --- | --- |
| `ember/features.py` | both MiniMax features gain the `catalog` group, for their LoRA lists |
| `ember/weights/downloads.py` | the catalogue download learns to fetch those LoRAs per tab |
| `ember/licensing/catalog.py` | the LoRA list for a feature with no models |
| `ember/pipelines/minimax/workflow.py` | the slots chained as `LoraLoaderModelOnly` between the diffusion model and the turbo LoRA |
| `ember/pipelines/minimax/handler.py` | the two `generate_minimax_*` handlers take and resolve the slots |
| `ember/web/schema/tabs/minimax.py` | eight blank LoRA rows over the tab's catalogue list |
| `scripts/golden.py` | the two cases gain the new arguments |
| `scripts/golden/generate_minimax_*.json` | regenerated — the chain is visible in the diff |
| `scripts/parity_baseline.json` | the new controls, 1,000 lines of them |

The shape to copy is that the handler signature, the schema and the
golden case move together. Two of those three are checked against each
other automatically; the third is not, which is why the snapshot is
committed.

**`b7d4125` — "Point the MiniMax LoRAs at their Hugging Face mirror"** is
step 5's tail: once the LoRAs existed and had been uploaded, each
record's `mirror` was filled in, so pods fetch them from the mirror
rather than from upstream. That is the edit `mirror_to_hf.py` makes for
you when it has `EMBER_ADMIN_TOKEN`.

## Before you push

```bash
$PY scripts/check_imports.py     # the new package's __init__ is empty
$PY scripts/check_config.py      # no constant changed value on the way in
$PY scripts/check_routes.py      # the new tab's routes are gated
$PY scripts/check_schema.py --choices
$PY scripts/golden.py --check
```

A dry run is the cheapest way to see the tab: `$PY scripts/dryrun.py
--features all` serves everything on `:7860` with no GPU and no weights.
`check_routes.py` will not pass until the feature is gated, and it is the
only thing that will notice if it is not.
