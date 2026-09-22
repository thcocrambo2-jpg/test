"""The images a run was given, kept so its recipe can hand them back.

The problem
-----------
A recipe restores every control on a tab except the one an edit or an
image-to-video run cannot do without: the picture it started from. An
upload is a temporary file (`routes/uploads.py` sweeps them after six
hours), so by the time anybody clicks "Load these settings" on the result,
the source it was made from is long gone and the tab comes back with an
empty drop zone.

So the first time a source is used, a copy is kept here, and the recipe
records the copy's name in that field's slot instead of None.

Storage
-------
`OUTPUT_DIR/.sources/<sha256>.<ext>` — named after the file's own bytes.

Content-addressed because the ordinary case is one source used many times:
a batch of four is four recipes and one picture, and a run tweaked and
re-run twenty times over one photo is twenty recipes and still one
picture. Storing it per recipe would be the "second copy of every input"
the recipe store was careful not to be. The bytes are kept as uploaded,
not re-encoded: a JPEG from a phone stays the size it was.

Under OUTPUT_DIR and dotted for the reasons `.recipes.jsonl` is: it lives
and dies on the same disk as the files whose recipes point at it, and
`gallery_index` skips dotted entries, so it never shows up in the gallery
or the zip.

Lifetime
--------
A source is worth keeping exactly as long as some recipe names it. The
recipe store calls `sweep()` whenever it rewrites itself — which is when
recipes are dropped, by a delete or by the MAX_RECIPES cap — and anything
no recipe names is removed then.

Except a recent one. A source is kept at *submit*, and its recipe is only
written when the run finishes, so a run sitting in the queue has a source
that no recipe names yet. `keep()` touches the file every time it is used,
and `sweep()` leaves anything touched within GRACE alone, which is far
longer than any job waits.

Imports nothing from the web layer, like `recipes`: the recipe store has
to be able to call `sweep()`.
"""

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from ember.logs import log
from ember.settings import OUTPUT_DIR

SOURCES_DIR = OUTPUT_DIR / ".sources"

# A source no recipe names survives a sweep for this long after it was
# last used. See "Lifetime" above.
GRACE = 24 * 3600

# The formats an image field takes, by what PIL calls them. Anything else
# is not kept — the recipe records None, exactly as it always did.
_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}

# The only names this module ever writes, and so the only names `path()`
# will resolve. The whole containment check: a hex digest and one of three
# suffixes cannot name anything outside SOURCES_DIR.
_NAME = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")


def keep(src, fmt: str | None) -> str | None:
    """Keep a copy of the image file `src`; return the name it is kept as.

    `fmt` is PIL's name for its format, which the caller already has from
    opening it. None when the file could not be kept — an unsupported
    format, or a disk that said no. Never raises: losing the source from
    a recipe is what used to happen every time, and must not now cost the
    run itself.
    """
    ext = _EXT.get(str(fmt or "").upper())
    if ext is None:
        return None
    try:
        digest = hashlib.sha256()
        with open(src, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        name = digest.hexdigest() + ext
        target = SOURCES_DIR / name
        if target.exists():
            os.utime(target)             # used again — see "Lifetime"
            return name
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        # Copied aside and renamed into place, so a reader never finds
        # half a picture under a name that promises the whole one.
        tmp = target.with_name(name + ".%d.part" % os.getpid())
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
        return name
    except OSError as exc:
        log.warning("Could not keep the source image %s: %s", src, exc)
        return None


def path(name) -> Path | None:
    """The file a kept name refers to, or None if it is not one or is gone."""
    if not isinstance(name, str) or not _NAME.match(name):
        return None
    target = SOURCES_DIR / name
    return target if target.is_file() else None


def sweep(recipes) -> None:
    """Remove every kept source that none of `recipes` names.

    `recipes` is the recipe store's rows. A name is looked for in every
    field value rather than by working out which fields are images: that
    would mean reading tab schemas from here, and a stray match can only
    ever keep a file, never lose one.
    """
    wanted = set()
    for row in recipes:
        for field in row.get("fields") or []:
            if isinstance(field, (list, tuple)) and len(field) > 1 \
                    and isinstance(field[1], str) and _NAME.match(field[1]):
                wanted.add(field[1])
    cutoff = time.time() - GRACE
    try:
        entries = list(SOURCES_DIR.iterdir())
    except OSError:
        return                           # nothing kept yet
    for entry in entries:
        try:
            if entry.name in wanted or entry.stat().st_mtime >= cutoff:
                continue
            entry.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove the source image %s: %s",
                        entry, exc)
