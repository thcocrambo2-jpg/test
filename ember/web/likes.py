"""Which generated files have been liked — the Gallery's favourites.

Kept on the pod rather than in the browser, because the same gallery is
opened from a desktop and from a phone, and a heart given on one has to be
there on the other. localStorage would have made it a per-device list.

Storage
-------
One small JSON object beside the images, `OUTPUT_DIR/.likes.json`, mapping
each liked file's key to when it was liked. Dotted, so `gallery_index`
skips it and it stays out of the gallery and the zip, for the same reason
`.recipes.jsonl` is. Rewritten whole on every change: a like is a
deliberate gesture, a few at a time, and the file is a few dozen bytes per
entry — there is no append-only log worth having here.

Keyed by the path relative to OUTPUT_DIR (`gallery_index.key_for`), the one
name a generated file has across the index, its recipe and the API.

A key whose file has gone — deleted outside the app — is harmless: the
filter intersects with the listing, so it can never show, and it is
dropped the next time anything is written.

Imports nothing from the API layer, like `gallery_index`.
"""

import json
import os
import threading
import time

from ember.logs import log
from ember.settings import OUTPUT_DIR

STORE = OUTPUT_DIR / ".likes.json"

_LOCK = threading.RLock()
_LIKES: dict[str, float] | None = None       # None until first read


def _load() -> dict[str, float]:
    """The store, read once per process. Caller holds _LOCK."""
    global _LIKES
    if _LIKES is not None:
        return _LIKES
    _LIKES = {}
    try:
        with open(STORE, encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            _LIKES = {str(key): float(when) for key, when in raw.items()
                      if isinstance(when, (int, float))}
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        # An unreadable store is an empty one, not a broken gallery. It is
        # not overwritten until the next like, so a file somebody is about
        # to fix by hand is not clobbered by merely opening the page.
        log.warning("Could not read %s: %s", STORE, exc)
    return _LIKES


def _save() -> None:
    """Write the store out, pruned to files still there. Caller holds _LOCK."""
    likes = _load()
    for key in [k for k in likes if not (OUTPUT_DIR / k).exists()]:
        del likes[key]
    # Written aside and renamed into place, so a crash mid-write leaves the
    # previous list rather than half of one.
    tmp = STORE.with_name(STORE.name + ".%d.part" % os.getpid())
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(likes, handle, separators=(",", ":"))
        os.replace(tmp, STORE)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        log.warning("Could not write %s: %s", STORE, exc)
        raise


def is_liked(key: str) -> bool:
    with _LOCK:
        return key in _load()


def liked_keys() -> set[str]:
    """Every liked key, for filtering a listing."""
    with _LOCK:
        return set(_load())


def set_liked(keys, liked: bool) -> int:
    """Like or unlike `keys`. Returns how many actually changed.

    The caller has already proven each key names a generated file. Raises
    OSError when the store cannot be written, so a heart is never shown as
    given when it would be gone on the next restart.
    """
    now = time.time()
    with _LOCK:
        likes = _load()
        before = dict(likes)
        changed = 0
        for key in keys:
            if liked and key not in likes:
                likes[key] = now
                changed += 1
            elif not liked and key in likes:
                del likes[key]
                changed += 1
        if changed:
            try:
                _save()
            except OSError:
                # Back to what is on disk, so the next listing does not
                # report a heart the restart would take away.
                likes.clear()
                likes.update(before)
                raise
        return changed


def forget(key: str | None) -> None:
    """Drop a deleted file's like. Never raises — the delete has happened."""
    if key is None:
        return
    with _LOCK:
        if _load().pop(key, None) is None:
            return
        try:
            _save()
        except OSError:
            pass
