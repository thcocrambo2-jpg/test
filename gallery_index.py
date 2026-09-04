"""An index of everything in OUTPUT_DIR, plus the thumbnails the grids show.

Two problems, one module.

**Listing.** The gallery used to answer "what has been generated?" with three
`rglob` sweeps of the whole output tree and a `stat()` per file, on every
refresh. Here the answer is cached per *directory*, keyed on that directory's
own mtime: POSIX requires a directory's mtime to change when an entry is
created, deleted or renamed in it, so a directory that has not been written to
since the last look can reuse its cached file list for the price of one
`stat()`. After a generation exactly one directory has changed, so a refresh
costs one `scandir` of that directory instead of a walk of everything.

**Thumbnails.** The grids used to be handed full-resolution PNGs — several MB
each, down a link to a browser that is usually nowhere near the pod. A 512px
WebP is 30-60 KB, so a page of ten goes from ~25 MB to well under one. Thumbs
are written once, just after a workflow finishes, on a background thread.

There is deliberately **no backfill**: images that already existed before this
module arrived never get a thumbnail, and `thumb_for()` simply returns the
original for them. That keeps startup free and means a missing, failed or
still-encoding thumbnail all land in the same, already-working code path.

Imports nothing from `ui` or `gradio` — the index is testable headless, and
`client.py` can call `note_new` without dragging the UI in behind it.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from config import OUTPUT_DIR, log

# What counts as a generated output. Anything else in OUTPUT_DIR — notably
# the zip zip_outputs() writes there — is not indexed and so cannot end up
# in a gallery or, via list_media(), inside the next zip.
IMAGE_EXT = (".png",)
VIDEO_EXT = (".mp4", ".webm")
MEDIA_EXT = IMAGE_EXT + VIDEO_EXT

# Thumbnails live under OUTPUT_DIR so Gradio's allowed_paths (see
# ui.launch_ui) and set_static_paths already cover them, and under a *dotted*
# name so the one rule that skips them from the index — "ignore entries
# starting with a dot" — also keeps them out of the zip.
THUMBS_DIR = OUTPUT_DIR / ".thumbs"
THUMB_LONG_EDGE = 512
THUMB_QUALITY = 80

# A directory whose mtime is younger than this is re-read every time rather
# than cached. Some mounts still expose whole-second mtimes, and on those a
# file landing in the same tick as the scan that cached the directory would
# otherwise stay invisible indefinitely. Costs one extra scandir of whichever
# directory was just written to, and only until it settles.
_SETTLE_NS = 2_000_000_000


class _Dir:
    """One directory's cached contents, valid while its mtime is unchanged."""

    __slots__ = ("mtime_ns", "files", "subdirs")

    def __init__(self, mtime_ns, files, subdirs):
        self.mtime_ns = mtime_ns
        self.files = files          # [(path, mtime), ...] — media only
        self.subdirs = subdirs      # [path, ...]


# Guards _DIRS and _ORDER together. Gradio serves handlers from a thread
# pool, so concurrent readers are the normal case, not the edge case. The
# thumbnail workers never take it — encoding must not block a page load.
_LOCK = threading.RLock()
_DIRS: dict[str, _Dir] = {}
_ORDER: list[str] | None = None     # every media path, newest first


def _rescan() -> None:
    """Bring _DIRS and _ORDER up to date. Caller holds _LOCK."""
    global _ORDER
    dirty = _ORDER is None
    now_ns = time.time_ns()
    files: list[tuple[str, float]] = []
    seen: set[str] = set()
    stack = [str(OUTPUT_DIR)]
    while stack:
        path = stack.pop()
        try:
            st = os.stat(path)
        except OSError:              # vanished between listing and stat
            dirty |= _DIRS.pop(path, None) is not None
            continue
        seen.add(path)
        cached = _DIRS.get(path)
        if cached is not None and cached.mtime_ns == st.st_mtime_ns:
            files.extend(cached.files)
            stack.extend(cached.subdirs)
            continue
        dirty = True
        entries: list[tuple[str, float]] = []
        subdirs: list[str] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    # The one rule that excludes .thumbs, and any other
                    # bookkeeping directory added later, for free.
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            subdirs.append(entry.path)
                        elif entry.name.lower().endswith(MEDIA_EXT):
                            # scandir already cached this stat — it is not
                            # the per-file syscall rglob+stat used to pay.
                            entries.append((entry.path, entry.stat().st_mtime))
                    except OSError:
                        continue
        except OSError:
            _DIRS.pop(path, None)
            continue
        files.extend(entries)
        stack.extend(subdirs)
        if now_ns - st.st_mtime_ns > _SETTLE_NS:
            _DIRS[path] = _Dir(st.st_mtime_ns, entries, subdirs)
        else:
            _DIRS.pop(path, None)    # too fresh to trust — look again next time
    for gone in set(_DIRS) - seen:
        del _DIRS[gone]
        dirty = True
    if dirty:
        _ORDER = [path for path, _ in
                  sorted(files, key=lambda item: item[1], reverse=True)]


def list_media() -> list[str]:
    """Every generated image and video under OUTPUT_DIR, newest first."""
    with _LOCK:
        _rescan()
        return list(_ORDER or ())


def list_images(limit: int | None = None) -> list[str]:
    """The newest generated stills, for the pick-a-previous-image galleries."""
    stills = [p for p in list_media() if p.lower().endswith(IMAGE_EXT)]
    return stills if limit is None else stills[:limit]


def note_new(paths) -> None:
    """A workflow just wrote these files — index them and thumbnail them.

    Registered against client.on_output, so it runs for every finished
    prompt regardless of which tab asked for it.

    Dropping the containing directories rather than patching their cached
    entry lists is deliberate: the next listing re-reads exactly the
    directories that changed and nothing else, which is both the cheapest
    correct answer and immune to the whole-second mtime granularity
    _SETTLE_NS otherwise has to guess around.

    Returns immediately — the WebP encoding happens on _THUMB_POOL, so the
    caller's generator can keep streaming progress to the UI.
    """
    global _ORDER
    paths = [str(p) for p in paths]
    with _LOCK:
        for directory in {os.path.dirname(p) for p in paths}:
            _DIRS.pop(directory, None)
        _ORDER = None
    for path in paths:
        _submit_thumb(path)


def key_for(path) -> str | None:
    """`path` as the store's key: relative to OUTPUT_DIR, forward slashes.

    The same spelling `recipes._key` uses, deliberately — a generated file
    has exactly one name across the index, its recipe and the API, and
    that name is a *relative* one. None for anything outside the tree.

    This is the only form of a path that is allowed to cross the wire.
    An absolute path names the pod's filesystem, and there is no version
    of a browser knowing `/workspace/krea2/output/...` that is useful
    enough to be worth handing out.
    """
    try:
        return Path(path).resolve().relative_to(
            Path(OUTPUT_DIR).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def safe_path(path) -> Path:
    """A caller-supplied path, resolved and proven to be a generated output.

    Two rules, and they are the same two the index uses to decide what it
    lists in the first place: the path must resolve to somewhere *inside*
    OUTPUT_DIR, and it must end in a media extension. Accepts either an
    absolute path or one relative to OUTPUT_DIR, so a wire `path_id` and
    an internal path go through the same check.

    **Raises** ValueError or OSError. Every caller here handles that, and
    the raising is the point — this is the one containment check in the
    app, and a version that returned None would be one `if` away from
    letting a forged path through.

    Extracted from delete() because two more callers arrived that need
    exactly this and nothing else: the API serves generated files at
    /media and /thumbs, replacing Gradio's `allowed_paths=` and
    `gr.set_static_paths`, which did this containment invisibly. Three
    call sites and one implementation, rather than three implementations
    and a hope.
    """
    target = Path(path)
    if not target.is_absolute():
        target = Path(OUTPUT_DIR) / target
    target = target.resolve()
    target.relative_to(Path(OUTPUT_DIR).resolve())      # raises if outside
    if not target.name.lower().endswith(MEDIA_EXT):
        raise ValueError("not a generated output")
    return target


def delete(path) -> bool:
    """Delete one generated file and its thumbnail. Returns whether it went.

    Permanent — there is no trash to fish it back out of, which is what
    the caller's confirm step is for.

    Deliberately narrow about what it will touch — see safe_path, which is
    the whole of that narrowness. The caller resolves a gallery click
    against client-supplied data, so a stale or forged path must not be
    able to reach `.recipes.jsonl`, the zip, or anything outside the
    output tree at all.

    Never raises. A file that has already gone counts as success — the
    caller asked for it not to be there — and every other OSError comes
    back as False for the caller to report.
    """
    try:
        target = safe_path(path)
    except (ValueError, OSError) as exc:
        log.warning("Refusing to delete %s (%s)", path, exc)
        return False

    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not delete %s: %s", target, exc)
        return False
    try:
        thumb_path(target).unlink(missing_ok=True)
    except (ValueError, OSError) as exc:
        # Nothing lists .thumbs, so a leftover thumbnail is invisible
        # rather than wrong — not worth failing a delete that has already
        # happened and cannot be undone.
        log.warning("Could not delete the thumbnail for %s: %s", target, exc)

    # The whole cache rather than the one directory: _DIRS is keyed on the
    # paths os.scandir built walking down from OUTPUT_DIR, and `target` has
    # been through resolve(), so the two spellings are not guaranteed to
    # match. A delete is a rare, deliberate gesture — one extra walk of the
    # tree is the right price for not having to reason about that.
    invalidate()
    log.info("Gallery: deleted %s", target)
    return True


def invalidate() -> None:
    """Forget everything — the next listing walks the tree from scratch."""
    global _ORDER
    with _LOCK:
        _DIRS.clear()
        _ORDER = None


# ── Thumbnails ────────────────────────────────────────────────────────────────

def thumb_path(path) -> Path:
    """Where `path`'s thumbnail lives, whether or not it has been made yet.

    The original's full filename is kept and `.webp` *appended* rather than
    substituted, so `Krea2_1_.png` and `Krea2_1_.mp4` cannot collide on one
    thumbnail — and going either direction stays a pure string operation.
    The subfolder tree is mirrored, which keeps `wan/`'s videos out of a
    single flat directory of thousands of entries.

        output/Krea2_00042_.png  →  output/.thumbs/Krea2_00042_.png.webp
        output/wan/W_00003_.mp4  →  output/.thumbs/wan/W_00003_.mp4.webp
    """
    rel = Path(path).relative_to(OUTPUT_DIR)
    return THUMBS_DIR / rel.parent / (rel.name + ".webp")


def thumb_for(path) -> str:
    """`path`'s thumbnail if one exists, else `path` itself.

    The fallback is the whole no-backfill policy in one line: nothing is
    generated on the browse path, so a pre-existing image, a video, a
    thumbnail that failed to encode and one that is still encoding all
    render the same way — as the original.
    """
    path = str(path)
    try:
        thumb = thumb_path(path)
    except ValueError:              # not under OUTPUT_DIR
        return path
    return str(thumb) if thumb.exists() else path


def has_thumb(path) -> bool:
    """Whether `path` has a thumbnail encoded, as opposed to a fallback.

    The question `thumb_for` answers implicitly and the API has to answer
    out loud: a caller handed a thumbnail URL will fetch it, so offering
    one that resolves back to the original is offering to send the whole
    file twice.
    """
    try:
        return thumb_path(path).exists()
    except ValueError:              # not under OUTPUT_DIR
        return False


def thumbs_for(paths) -> list[str]:
    """thumb_for over a page of paths, order preserved."""
    return [thumb_for(p) for p in paths]


# Two workers: WebP encoding is CPU work on a box whose CPU is also busy
# feeding the GPU, and a generator produces an image every few seconds at
# best, so more would only compete with the thing being waited on.
_THUMB_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumb")


def _write_thumb(src: str) -> None:
    """Encode one thumbnail. Never raises — see _submit_thumb."""
    dst = thumb_path(src)
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Written aside and renamed into place: thumb_for() checks exists() on
    # every render and would otherwise happily serve a half-written file.
    # os.replace is atomic within a filesystem.
    tmp = dst.with_name(dst.name + f".{os.getpid()}.part")
    try:
        with Image.open(src) as img:
            # WebP does support alpha; flattening is a choice. ComfyUI writes
            # RGB, and a stray RGBA image would render as a hole against the
            # gallery's dark tile — drop the convert to keep transparency.
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.LANCZOS)
            img.save(tmp, "WEBP", quality=THUMB_QUALITY, method=4)
        os.replace(tmp, dst)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _submit_thumb(src: str) -> None:
    """Queue a thumbnail for `src`, if it is a still we can open."""
    if not src.lower().endswith(IMAGE_EXT):
        # Videos need a frame grab, not Pillow. Until that exists the tile
        # serves the .mp4 itself, which is what thumb_for already does.
        return

    def task():
        try:
            _write_thumb(src)
        except Exception as exc:
            # A thumbnail is a nicety. Failing to write one must never look
            # like a failed generation — the tile just shows the original.
            log.warning("Could not thumbnail %s: %s", src, exc)

    try:
        _THUMB_POOL.submit(task)
    except RuntimeError:            # pool shut down during interpreter exit
        pass
