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

Recent uploads
--------------
The same store answers the "Recent uploads" strip under every image
input. Not everything kept here belongs in it: a gallery picture fed back
in, or a pose from the library, is kept too, and both already have a strip
of their own. So the browser says which uploads were the user's own files
(`routes/uploads.py`), and only those are written to `uploads.json` here,
with when each was last used. That list is a view, not an owner: a name
on it whose file the sweep removed is simply not listed.

Imports nothing from the web layer, like `recipes`: the recipe store has
to be able to call `sweep()`.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from PIL import Image

from ember.logs import log
from ember.settings import OUTPUT_DIR

SOURCES_DIR = OUTPUT_DIR / ".sources"

# {kept name: when it was last submitted} for the user's own uploads. See
# "Recent uploads" above. Not a name `_NAME` matches, so a sweep skips it.
UPLOADS_FILE = SOURCES_DIR / "uploads.json"

# The strip's thumbnails, made on first ask. The gallery's size and
# quality, so the two strips under an input are drawn alike.
THUMBS_DIR = SOURCES_DIR / "thumbs"
THUMB_LONG_EDGE = 512
THUMB_QUALITY = 80

# Submits run on request threads; the list is read, changed and rewritten.
_LOCK = threading.Lock()

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
            if not (isinstance(field, (list, tuple)) and len(field) > 1):
                continue
            # A list of names is an images field's value.
            values = field[1] if isinstance(field[1], list) else [field[1]]
            wanted.update(value for value in values
                          if isinstance(value, str) and _NAME.match(value))
    cutoff = time.time() - GRACE
    try:
        entries = list(SOURCES_DIR.iterdir())
    except OSError:
        return                           # nothing kept yet
    for entry in entries:
        # uploads.json, thumbs/ and a half-written .part are not sources.
        if not _NAME.match(entry.name):
            continue
        try:
            if entry.name in wanted or entry.stat().st_mtime >= cutoff:
                continue
            entry.unlink(missing_ok=True)
            (THUMBS_DIR / (entry.name + ".webp")).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove the source image %s: %s",
                        entry, exc)
    _prune_uploads()


# ─────────────────────────────────────────────────────── recent uploads

def _read_uploads() -> dict:
    """uploads.json, or {} when there is none or it is not readable."""
    try:
        data = json.loads(UPLOADS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: at for name, at in data.items()
            if _NAME.match(str(name)) and isinstance(at, (int, float))}


def _write_uploads(data: dict) -> None:
    """Rewrite uploads.json, aside and renamed like every other write here."""
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPLOADS_FILE.with_name(UPLOADS_FILE.name + ".%d.part" % os.getpid())
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, UPLOADS_FILE)


def note_upload(name: str) -> None:
    """Put a kept name on the Recent uploads list, or move it to the front.

    Never raises, like `keep()`: a strip that misses one picture must not
    cost the run it was submitted with.
    """
    if not _NAME.match(str(name)):
        return
    try:
        with _LOCK:
            data = _read_uploads()
            data[name] = time.time()
            _write_uploads(data)
    except OSError as exc:
        log.warning("Could not note the upload %s: %s", name, exc)


def recent_uploads(limit: int) -> list[tuple[str, float]]:
    """(name, last used) for the user's own uploads still kept, newest first."""
    with _LOCK:
        data = _read_uploads()
    rows = sorted(data.items(), key=lambda row: row[1], reverse=True)
    return [(name, at) for name, at in rows
            if (SOURCES_DIR / name).is_file()][:limit]


def _prune_uploads() -> None:
    """Drop names whose file a sweep has removed. Best effort."""
    try:
        with _LOCK:
            data = _read_uploads()
            kept = {name: at for name, at in data.items()
                    if (SOURCES_DIR / name).is_file()}
            if len(kept) != len(data):
                _write_uploads(kept)
    except OSError as exc:
        log.warning("Could not tidy the uploads list: %s", exc)


def thumb(name) -> Path | None:
    """A kept source's 512px WebP, made the first time it is asked for.

    Falls back to the source itself when it cannot be encoded — a bigger
    download, but still the right picture. None when the name is not one.
    """
    source = path(name)
    if source is None:
        return None
    target = THUMBS_DIR / (source.name + ".webp")
    if target.is_file():
        return target
    tmp = target.with_name(target.name + ".%d.part" % os.getpid())
    try:
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image = image if image.mode in ("RGB", "L") else image.convert("RGB")
            image.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.LANCZOS)
            image.save(tmp, "WEBP", quality=THUMB_QUALITY, method=4)
        os.replace(tmp, target)
        return target
    except Exception as exc:                     # noqa: BLE001
        tmp.unlink(missing_ok=True)
        log.warning("Could not make a thumbnail for %s: %s", name, exc)
        return source
