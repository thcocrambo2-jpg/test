"""What every generated file was made with, so it can be made again.

The problem
-----------
An image in the Gallery tab is a dead end. It is the one that worked, and
the settings behind it — the prompt, the model, the LoRA stack, and above
all the *seed* a random tick threw away — went out of the UI the moment
the next click overwrote them. "Do that again, but taller" was not a
thing anyone could do.

So every finished prompt writes a **recipe** beside its output: the value
of every control on the tab that submitted it, in that tab's own order,
each one labelled. The Gallery tab shows it for the selected file and can
push the whole lot back into the controls it came from.

Shape
-----
A recipe is deliberately the *UI's* values and not the resolved ComfyUI
graph — the same call `prompts.py` and `presets.py` make, for the same
reason. The dropdown label is what goes back into a dropdown; the
filename it resolved to on this pod is not, and would be wrong on the
next one. It is stored as an ordered list of `[label, value]` pairs
rather than a dict, because a LoRA stack has eight controls all labelled
"Weight" and their order is the only thing that says which slot each
belongs to.

Storage
-------
One append-only JSONL file next to the images, `OUTPUT_DIR/.recipes.jsonl`.

Append-only because the alternative is rewriting the whole map after every
picture, and a pod with a few thousand generations behind it would spend
real time on that. Later lines win, so an update is just another line and
compaction is a rewrite that happens at most once per process.

Beside the images, and named with a leading dot, because both halves are
load-bearing: a recipe is worth exactly as long as the file it describes,
so they should live and die on the same disk — and `gallery_index` already
skips dotted entries, which keeps this file out of the gallery and out of
the zip for free.

Keyed by the path *relative* to OUTPUT_DIR, so moving the output tree, or
mounting it somewhere else on the next pod, does not orphan every recipe
in it.

Imports nothing from `ui` or `gradio`: like `gallery_index`, this has to
be callable from `client.py`'s output hook without dragging the UI in
behind it.
"""

import json
import os
import threading
import time
from pathlib import Path

from config import OUTPUT_DIR, log

STORE = OUTPUT_DIR / ".recipes.jsonl"

# How many recipes to keep. Old ones are dropped oldest-first when the
# store is compacted. A recipe is a few hundred bytes, so this is a couple
# of MB — far more history than the gallery can page through, and bounded
# so a long-lived pod cannot grow the file without limit.
MAX_RECIPES = 5000

# Compact when the file holds this many times more lines than live
# recipes. Rewrites are O(everything), so they have to be rare; at 2× the
# file is at worst twice the size it needs to be, which is nothing.
COMPACT_RATIO = 2

_LOCK = threading.RLock()
_RECIPES: dict[str, dict] | None = None     # None until first read
_LINES = 0                                   # lines on file, for compaction

# The recipe the *current thread* is generating for. Set by the tab
# handler's wrapper before the work starts and read by the output hook
# when ComfyUI reports what it wrote.
#
# Thread-local rather than passed down: the three executors (stills,
# face swap, video) all reach ComfyUI through client.run, and the hook
# fires inside it. Threading a recipe through every one of those call
# chains would touch every generation handler in the app to carry
# something none of them has any other use for. One worker per lane runs
# one job at a time, so "the current thread's job" is exact.
_CURRENT = threading.local()


def _key(path) -> str | None:
    """The store's key for a path: relative to OUTPUT_DIR, forward slashes.

    Returns None for anything outside the output tree, which is the signal
    not to record it at all.
    """
    try:
        return Path(path).resolve().relative_to(Path(OUTPUT_DIR).resolve()) \
            .as_posix()
    except (ValueError, OSError):
        return None


def _load() -> dict:
    """The store, read once per process. Caller holds _LOCK."""
    global _RECIPES, _LINES
    if _RECIPES is not None:
        return _RECIPES
    _RECIPES, _LINES = {}, 0
    try:
        with open(STORE, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                _LINES += 1
                try:
                    row = json.loads(line)
                    key = row["key"]
                except (ValueError, KeyError, TypeError):
                    # One corrupt line — a half-written append killed by a
                    # pod shutdown — must not cost the whole history.
                    continue
                _RECIPES[key] = row
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not read %s: %s", STORE, exc)
    if _RECIPES:
        log.info("Recipes: %d generation(s) on file", len(_RECIPES))
    return _RECIPES


def _append(row: dict) -> None:
    """Add one line to the store. Caller holds _LOCK."""
    global _LINES
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        _LINES += 1
    except OSError as exc:
        log.warning("Could not write %s: %s", STORE, exc)
        return
    if _LINES > max(64, COMPACT_RATIO * len(_RECIPES or {})):
        _compact()


def _compact() -> None:
    """Rewrite the store as one line per live recipe. Caller holds _LOCK.

    Also where MAX_RECIPES is enforced, because this is the only moment
    the whole file is being written anyway. Newest kept, by the time each
    recipe was recorded.
    """
    global _LINES
    rows = sorted((_RECIPES or {}).values(),
                  key=lambda row: row.get("at", 0))[-MAX_RECIPES:]
    tmp = STORE.with_name(STORE.name + f".{os.getpid()}.part")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, STORE)          # atomic within a filesystem
    except OSError as exc:
        log.warning("Could not compact %s: %s", STORE, exc)
        Path(tmp).unlink(missing_ok=True)
        return
    _RECIPES.clear()
    _RECIPES.update({row["key"]: row for row in rows})
    _LINES = len(rows)
    log.info("Recipes: compacted to %d", _LINES)


# ── the current job ───────────────────────────────────────────────────────

def begin(tab: str, tab_label: str, tab_id: str, fields) -> None:
    """This thread is now generating for `tab`, with these control values.

    `fields` is [[label, value], ...] in the tab's own input order — see
    the module docstring on why it is a list and not a dict.
    """
    _CURRENT.recipe = {
        "tab": str(tab), "tab_label": tab_label, "tab_id": tab_id,
        "fields": [[str(label), value] for label, value in fields],
        "seed": None,
    }


def end() -> None:
    """This thread has finished; stop attributing outputs to that job."""
    _CURRENT.recipe = None


def stamp(**extra) -> None:
    """Record something only the executor knows — in practice, the seed.

    The seed on the *controls* is a starting point: a batch of four runs
    on four consecutive seeds, and with "🎲 Random seed" ticked none of
    them is the number in the box. What makes a picture reproducible is
    the seed that particular prompt actually ran with, and the executor
    is the only place that is known.

    A no-op off a generation thread, so an executor calling it is never a
    question of who its caller was.
    """
    recipe = getattr(_CURRENT, "recipe", None)
    if recipe is not None:
        recipe.update(extra)


# ── recording and reading ────────────────────────────────────────────────

def note_output(paths) -> None:
    """A prompt finished and wrote these files — file the recipe under each.

    Registered against client.on_output, so it covers every executor for
    the same reason gallery_index.note_new is: there is one producer of
    the "done" event and no reason for each consumer to be wired
    separately.

    Called once per ComfyUI prompt, which is once per seed — so a batch of
    four writes four recipes that differ in exactly the field that matters.
    """
    recipe = getattr(_CURRENT, "recipe", None)
    if recipe is None:
        return          # not a queued generation — a dry run, or a test
    stamped = time.time()
    with _LOCK:
        store = _load()
        for path in paths:
            key = _key(path)
            if key is None:
                continue
            row = dict(recipe, key=key, at=stamped)
            store[key] = row
            _append(row)


def for_path(path) -> dict | None:
    """The recipe behind one generated file, or None if it has none.

    None is the ordinary answer for anything generated before this
    existed, or copied into the output tree by hand — every caller has to
    render that case anyway, so it is not worth an exception.
    """
    key = _key(path)
    if key is None:
        return None
    with _LOCK:
        return _load().get(key)


def count() -> int:
    with _LOCK:
        return len(_load())
