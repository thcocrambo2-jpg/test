# The checks

For someone changing the code. Eight scripts under
[`scripts/`](../../scripts/), each guarding one thing nothing else in the
repo notices. They have one shape in common: every defect they catch is
one that a **successful build hides**. Nothing raises, nothing 500s, the
app starts and serves — and it is quietly wrong.

## Running them

Python is the `krea2` conda environment.

**Git Bash**:

```bash
PY=/c/Users/Adarsh/miniconda3/envs/krea2/python.exe
```

**PS** (PowerShell):

```powershell
$PY = "$env:USERPROFILE\miniconda3\envs\krea2\python.exe"
```

Then, from the repo root:

```bash
$PY scripts/check_build_args.py
$PY scripts/check_webui.py
$PY scripts/check_routes.py
$PY scripts/check_schema.py --choices
$PY scripts/golden.py --check
$PY scripts/check_config.py
$PY scripts/check_imports.py
$PY scripts/check_docs.py
```

On a Windows console add `PYTHONIOENCODING=utf-8`; several of them print
the emoji that are part of the tab labels they check.

Five of them are also `make check-args`, which both `make compile` and
`make release` depend on — so the Linux build refuses to run while any of
them is unhappy. `make` is **WSL or POD** only; run the scripts directly
on Windows.

```bash
make check-args     # check_build_args, check_webui, check_routes,
                    # check_config, check_imports
```

Widening that one target, rather than giving each check its own
prerequisite, is what keeps `compile` and `release` on a single gate.

---

## check_build_args.py

**Guards:** that `build.sh` and `build.ps1` bundle the same things.

Each build script carries its own copy of the long Nuitka `--include-*`
list. The duplication is deliberate — `build.sh` produces the artifact
every customer runs today, and adding Windows was not worth editing it
for — and the price of not sharing the list is drift.

**The silent failure:** a flag present in one script and missing from the
other produces a binary that *compiles and runs* and is then wrong:

| Missing flag | What ships |
| --- | --- |
| `--include-data-files=scripts/PINS.json=PINS.json` | un-pinned weights, and CivitAI asking customers for a token |
| `--include-package=hf_xet` | every model download drops to single-stream HTTP |
| `--include-package=uvicorn` | uvicorn names its protocol and loop backends as *strings*, so the import graph never reaches them: the binary compiles, starts, and dies inside `uvicorn.run()` |
| `--include-module=ember.web.webui_bundle` | the app serves an API and a "front end not built" notice — no UI at all |

So a flag added to one script and not the other is one platform's
customers quietly getting a worse build than the other's.

**What it cannot prove.** This check compares the two scripts against
*each other* and nothing else. It passes whether or not a flag is
**correct** — it can show that both scripts say
`--include-module=ember.web.webui_bundle`, never that that is the right
module path. Rename the module and update both scripts to the same wrong
name and this check is perfectly happy; the binary then ships with no UI.
Only a real build and a real launch prove the flags themselves.

Flags that genuinely belong to one platform (`--jobs`, `--mingw64`,
`--static-libpython`, the output filename) are listed in
`PLATFORM_SPECIFIC`, each with its reason. Two things in the pair are
shared *logic* rather than a shared flag, so they cannot be diffed — the
hf_xet `.dist-info` discovery block, and the `strings | grep` sanity
check on the built binary — and the script asserts only that neither has
lost them.

```bash
$PY scripts/check_build_args.py          # report and exit 1 on drift
$PY scripts/check_build_args.py --list   # print the agreed list
```

## check_webui.py

**Guards:** that the committed `ember/web/webui_bundle.py` is not older
than `webui/src`.

**The silent failure:** "someone edited a `.tsx` and forgot to run
`make webui`". The build succeeds, the binary runs, and the app it serves
is whatever the front end looked like at the last regeneration.

It recomputes `SOURCE_HASH` over the front-end sources and compares it to
the one baked into the generated module. Pure stdlib and deliberately
**no Node**: this is what both build hosts run, and neither of them has
npm. Nothing is compiled and nothing is compared byte-for-byte against
`webui/dist/`, so the answer does not depend on which zlib or which Vite
the checking machine has. `gen_webui_bundle.py` is imported rather than
reimplemented, because the two hashes have to be computed the same way
forever.

```bash
$PY scripts/check_webui.py
$PY scripts/check_webui.py --quiet    # only speak up on failure
```

The fix is `make webui` (**WSL or POD**, the only target that needs
Node), then commit the regenerated module.

## check_routes.py

**Guards:** the licence gate. An ungranted feature must have no working
route.

**The silent failure:** the worst shape there is. Everything looks right,
every tab a customer paid for works — and so does one they did not.

The partial backstop is that weights for ungranted features are never
fetched, so most ungranted tabs would fail at "that model is not
downloaded yet". One would not: `community_prompts` declares `needs=()`
because it has no weights of its own — the Prompt Library reads a
collection on the licence server — so it would be **fully functional**
for a licence that does not include it. It is named explicitly in the
check so that a future refactor cannot quietly drop it by making the loop
cleverer.

How it works: build the app against `features.resolve([])` — a licence
that grants nothing at all — and drive it through Starlette's
`TestClient`. Every per-tab route must answer **403, not 404**. The
routes are registered for every tab and each one refuses, so a licence
upgrade takes effect on the next `resolve()` without rebuilding the app,
and a customer who has just paid gets a sentence rather than a dead link.
Then it resolves one feature and proves exactly one tab's routes opened,
which is the half that catches a gate wired to the wrong key.

```bash
$PY scripts/check_routes.py
```

## check_schema.py

**Guards:** every control, label, default and choice in
`ember/web/tabschema.py`, against a frozen baseline.

**The silent failure:** a reworded label. `.recipes.jsonl` on every live
pod stores `[[label, value], …]` positionally and reads it back by
matching the label against the control at that index. A label changed by
one character does not fail — that one control silently keeps whatever it
had while the rest of the recipe loads. So the comparison is
character-for-character, including the emoji.

Defaults matter for a quieter reason: a default is what an untouched form
submits, so a default that drifts changes every picture made by someone
who did not touch that control.

The four Krea tabs' Model and LoRA dropdowns hold catalogue **ids** — the
feature's lists as the licence server answers them. Unless
`KREA2_CATALOG_FILE` is already set, the check points it at the seed
document, `license-validator/data/assets.json`, which is what those
baseline entries were written from; point it at another catalogue and
they differ for reasons that are not regressions. That is also why
`--choices` is off by default: a LoRA added to the database moves a
choice list without anything being wrong.

```bash
$PY scripts/check_schema.py             # structure only
$PY scripts/check_schema.py --choices   # choices too
```

## golden.py

**Guards:** that every `generate_*` handler builds a byte-identical
workflow dict.

**The silent failure:** `generate_v2` takes **31 positional arguments**.
The adapter that hands them over has to do it in exactly the right order,
and getting it wrong is not an exception: swap `cutoff_step` and
`total_steps` and you have swapped two ints, every handler still runs,
every picture still arrives, and they are quietly worse. Nothing in the
app notices, and no human reading a 5,000-line diff notices either.

The seam is `_run_jobs` in `ember/generation/handlers.py`, which calls
`builder(filename_prefix=…, **job)` and then `client.run(workflow)` —
patch `run`, keep the dict it was handed, and that dict is the complete
statement of what the arguments meant. `--check` names the JSON path a
number moved at. Snapshots live in `scripts/golden/`, one per handler.

It is an operator tool, not part of the shipped app, and it borrows the
weightless boot so it runs on a laptop with no GPU, no ComfyUI and no
models. The aliveness probe, the "is this downloaded yet" guards,
`client.run` and `client.upload_image` are all stubbed — each because
without it the check would pass while proving nothing.

```bash
$PY scripts/golden.py            # write the snapshots
$PY scripts/golden.py --check    # exit 1 on any difference
```

## check_config.py

**Guards:** every configuration constant's value, wherever the module
holding it now lives.

**The silent failure:** nothing in the app asserts what a number *is*.
Drop a line while splitting a data module, keep a `PROJECT_DIR` that
resolves one directory too high, or land `WAN_RESERVE_VRAM` in the wrong
pipeline's constants, and the app still starts — the pictures are just
wrong, or the weights are unpinned, or the showcase is empty.

*Where* a name lives is not what it proves. `SOURCES` is the list of
modules the names are looked for in, and a change that moves a value
rewrites it. A name is allowed to move between those modules. What is not
allowed is for it to vanish, to change value, or to end up in two of them
at once — that last one being how a "shared" constant becomes two
constants that drift apart later. New names are fine and are only listed.

Half of `ember/settings.py` is derived from the environment, so the
values are collected in a **subprocess** with a built environment — every
`KREA2_*`, `HF_TOKEN`, `CIVITAI_TOKEN` and `RUNPOD_*` variable dropped, a
few set to fixed values, the two paths that still differ written back out
as `<BASE_DIR>` and `<ROOT>` — so the answer is the same on any machine.

```bash
$PY scripts/check_config.py
```

**Never run it with `--write`.** A failing check is a bug in the code,
not a stale baseline. To retarget it, edit `SOURCES` and the `QUALIFIED`
tuples; the dict keys are deliberately stable labels, so no rewrite is
needed.

## check_imports.py

**Guards:** four layout rules, by importing every app module **alone, in
a fresh subprocess**.

**The silent failures:**

1. **An import cycle.** A module imports today only because something
   earlier had already put half the graph in `sys.modules`. The app would
   still start — its entry point happens to import things in the lucky
   order — and the failure would surface in a script, a test, or the
   Docker bake stage, weeks later. A fresh subprocess is the only
   arrangement that has no earlier import to lean on.
2. **Import-time work that needs hardware.** `ember/comfy/server.py`
   detects GPUs when it is imported and raises without one, so a package
   `__init__` that imported its siblings would drag GPU detection into
   `docker/bake_nodes.py` and into every laptop script. Hence the rule
   that **every `__init__.py` under `ember/` is empty**.
3. **A stray `os.environ.get`.** `ember/settings.py` owns the
   environment. A read left behind in another module is a value that
   never appears in `check_config.py`'s baseline and never gets
   validated — and it reads correctly on the machine where it was
   written. An AST scan is the only thing that finds those, because they
   do nothing wrong at runtime.
4. **Nothing imports the module the configuration split removed.**

The comfy module is stubbed, the same way `golden.py` and `dryrun.py`
stub it, and the stub is installed *before* the module under test, so
importing a module that imports comfy is a test of that module rather
than of this machine's GPU.

```bash
$PY scripts/check_imports.py
$PY scripts/check_imports.py --list
```

## check_docs.py

**Guards:** three promises the documentation cannot keep by itself.

**The silent failure:** prose rots in ways nothing else notices. Python
never imports a markdown file, so a link that stopped resolving, a
variable nobody wrote down, and a paragraph naming a module deleted two
changes ago all read exactly like working documentation.

1. **Every relative link in the documentation resolves.** Checked as far
   as the file, not the heading: a wrong `#anchor` is a nuisance, a wrong
   file name is a dead end. `LINK_GLOBS` in the script is the list of
   what counts as documentation; a new page outside it is not a page the
   rule protects, so add to it rather than assuming.
2. **Every environment variable is documented.** `ember/settings.py`'s
   docstring table is the list of everything the app reads from the
   environment, and [Configuration](../configuration.md) is where somebody running the
   app looks one up. The docstring is the source of truth; the rule only
   asks that the page has heard of each name.
3. **Nothing still names a file that is gone.** A comment that points at
   a module that no longer exists sends the next reader nowhere, and no
   compiler will tell them. Tracked files only, so anything git-ignored
   is out of scope by construction. `ALLOWED` holds the genuine uses that
   must never be "fixed".

Each rule sits behind its own module-level constant, so retiring one is a
single value rather than a rewrite.

```bash
$PY scripts/check_docs.py
$PY scripts/check_docs.py --list    # what each rule scans
```

---

## What the checks do not cover

Everything above runs on a laptop, in seconds, against source. Three
things can only be proved by a real build and a real pod:

- the Nuitka flags being *correct* rather than merely agreeing;
- `PROJECT_DIR` resolving to the onefile extraction root, which is where
  Nuitka puts the bundled data files;
- the Docker bake stage's list of copied files.

A dry run covers the layer between: `scripts/dryrun.py --features all`
serves the whole app on `:7860` with no GPU, no ComfyUI and no weights.
See [running a dry run](../running/dry-run.md).
