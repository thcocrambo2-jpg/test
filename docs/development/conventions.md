# Conventions

For someone changing the code. Short list, and each entry exists because
breaking it costs somebody a bad afternoon rather than because it looks
tidier.

## The three config tiers

Every value in the app belongs to exactly one of three homes, and the
question that picks the home is not "what kind of value is it" but "who
reads it".

| Tier | Home | What goes there |
| --- | --- | --- |
| 1 | [`ember/settings.py`](../../ember/settings.py) | Anything read from the environment, and anything derived from it: `BASE_DIR` and the paths under it, ports, the licence key, the node tag and URL, HF and CivitAI tokens, the showcase URL, `FROZEN`. Validation stays with the value. |
| 2 | `ember/pipelines/<name>/constants.py` | Facts about a model or its graph: repos, filenames, node packs, sampler and variance defaults, aspect tables, dropdown lists, negative prompts, frame grids. |
| 3 | the top of the module that uses it | Tuning with exactly one reader: timeouts, TTLs, retries, upload limits, thumbnail sizes. Leave these where they are. |

**`ember/settings.py` is the only module that reads `os.environ`.** Not a
style preference: a stray `os.environ.get` elsewhere is a value that
never appears in `check_config.py`'s baseline and never gets validated,
and it reads correctly on the machine where it was written.
`check_imports.py` scans the AST for them, because at runtime they do
nothing wrong. `os.environ.copy()` is allowed anywhere — handing the
whole environment to a child process is a pass-through, not a decision
made from a named variable.

A value that several modules share goes to the lowest module they all
import. Tab keys come from `features.Key`, never from string literals.

The catalogue — models, LoRAs, plans, presets, prompts — is **not
config**. It lives in the licence-server database, is fetched at runtime,
and belongs in none of the three tiers.

The style is deliberately plain: module-level `UPPER_CASE` constants,
`from … import NAME`, one logger. No settings class, no pydantic, no new
dependency.

## Every `__init__.py` is empty

`ember/comfy/server.py` detects GPUs when it is imported and raises
without one. A package `__init__` that imported its siblings would drag
that into the Docker bake stage and into every laptop script — and the
symptom would be an import error in a place that has nothing to do with
GPUs.

So every `__init__.py` under `ember/` holds nothing but whitespace or a
docstring, and importing `ember.web` gets you `ember.web` and not
`ember.web.api`. `check_imports.py` enforces it, and also imports every
module **alone, in a fresh subprocess**, which is the only arrangement
that cannot lean on an earlier import having already filled `sys.modules`.

## Moves and edits go in separate commits

A `git mv`-only commit first — identical blob hashes, nothing else in it
— then the edit. The tree may not run between the two, and that is fine.

The reason is review and blame. A commit that both moves 29 files and
rewrites 92 import statements is a commit nobody can read, and the one
line that changed meaning hides in it perfectly.

## `.git-blame-ignore-revs`

Bulk rewrites that moved every line without changing any of them are
listed in [`.git-blame-ignore-revs`](../../.git-blame-ignore-revs), so
`git blame` skips them and lands on the commit that last changed the code
itself.

Turn it on for your checkout once:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

Two things do **not** belong in that file: a commit that changed
behaviour, ever; and a pure `git mv`, which blame already follows with
rename detection.

## "license" in identifiers, "licence" in prose

The code spells it **license** — `KREA2_LICENSE_KEY`, `license_key`,
`license-validator/`, the `license` field on a document — because those
are wire values, file names and API paths, and they are contracts. Prose
spells it **licence**: "the licence server", "a licence that grants two
tabs", "a lapsed licence".

The rule is not about which spelling is correct. It is that identifiers
are the half that cannot be changed later, so they stay in one spelling
forever and the writing is free to use the other.

## Identifiers that can never change

A short list, and all of it is a contract with data that already exists
on customers' disks or in the database:

- **feature keys** (`krea_t2i`, `minimax_i2v`, …) — compiled into every
  binary that has shipped. There is no alias map; an old key is simply
  unknown, so renaming one drops that tab for anyone on an older build.
- **catalogue ids** (`krea2-turbo`, …) and **LoRA filenames**.
- **tab labels**, character for character, emoji included — `.recipes.jsonl`
  on every live pod stores `[[label, value], …]` positionally and reads
  it back by matching the label against the control at that index.
- **output filename prefixes**.
- **on-pod state files**: `.catalog.json`, `.recipes.jsonl`,
  `eta_history.json`, `.thumbs/`.

Labels *are* the safe thing to reword on a `Feature` — the server sends
one too and that wins — but the tab labels a schema declares are matched
by `check_schema.py` and are not.

## Leave no trace

A removed thing should look like it never existed. No compatibility shim
left behind once a change is finished, no "moved from" or "used to be"
comments, no dated history notes, and counts and lists updated to match.
Git holds the history, and it holds it better than a comment does.

## Branches and commits

- One branch per task, named like the rest of the log: the next number
  and a short name.
- Subject in the imperative. The body says *why*, not what — the diff
  already says what.
- Never `--no-verify`. Never amend a commit that is already on a shared
  branch.
- Never push and never merge to `main` without being asked.

## Before you push

[The checks](checks.md). Five of them are `make check-args`, and both
build targets depend on it.
