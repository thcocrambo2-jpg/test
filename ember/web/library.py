"""Picture libraries an images field can pick from — poses today.

The pictures are not in the binary. Each library is one archive per
version in the PRIVATE builds bucket, and the licence server hands out a
short-lived URL for it (POST /v1/library) to a key whose plan includes a tab
that uses it. The archive is downloaded once per machine, checked against
its sha256 and unpacked here:

    <LIBRARY_DIR>/<library>/manifest.json                   what is in it
    <LIBRARY_DIR>/<library>/v<N>/thumbs/<id>/<kind>.webp    the picker's cards
    <LIBRARY_DIR>/<library>/v<N>/full/<id>/<kind>.webp      what goes in the field

From then on everything is served from the disk and the library works
offline. The archive holds the same tree without the `v<N>/` level, and
scripts/build_pose_library.py writes this one directly, so a dev checkout
needs neither the bucket nor the server.

**The first open downloads.** With nothing unpacked yet, asking for the
manifest starts the download in a thread and answers with its progress; the
picker polls until it is done. A machine that already has a version checks
for a newer one once per process, in the background, and keeps serving
what it has meanwhile — the new manifest takes over only once every file it
names is in place.

**Only what the manifest names is ever served.** A request names a pose id
and a rendering, both checked against the manifest, and the path is built
here from those. The archive is held to the same rule as it is unpacked:
a member whose name is not one this module would build is skipped, so a
crafted archive cannot write outside its folder.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ember import settings
from ember.licensing import seat, server
from ember.logs import log

# The libraries there are. A name that is not here is not a library,
# whatever a field's `library` says.
NAMES = ("poses",)

SIZES = ("thumbs", "full")
KINDS = ("skeleton", "depth", "lineart")

REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60
CHUNK = 256 * 1024
# Far above the pack (about 20 MB for 582 poses): a ceiling on what one
# download may write, not a limit anybody should meet.
MAX_PACK_BYTES = 500 * 1024 * 1024

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MEMBER = re.compile(r"^(thumbs|full)/([a-z0-9][a-z0-9-]{0,63})/"
                     r"(skeleton|depth|lineart)\.webp$")


class Unavailable(Exception):
    """The library cannot be read. The message is for the picker."""


@dataclass
class _State:
    lock: threading.Lock
    manifest: dict | None = None      # what is served now
    loaded: bool = False              # the disk was read
    busy: bool = False                # a download is running
    progress: float = 0.0             # 0..1 of that download
    error: str = ""                   # why the last download failed
    checked: bool = False             # this process asked for an update


_states = {name: _State(threading.Lock()) for name in NAMES}


def _dir(name: str) -> Path:
    return settings.LIBRARY_DIR / name


def _valid(manifest) -> bool:
    return (isinstance(manifest, dict)
            and isinstance(manifest.get("version"), int)
            and isinstance(manifest.get("items"), list))


def _read_cached(name: str) -> dict | None:
    try:
        stored = json.loads((_dir(name) / "manifest.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stored if _valid(stored) else None


# ── download ──────────────────────────────────────────────────────────────────
def _ask(name: str, current: int | None) -> dict:
    """The licence server's answer for this library: up to date, or a URL."""
    if not settings.LICENSE_KEY:
        raise Unavailable("The %s library downloads with a licence key, and "
                          "this machine has none." % name)
    try:
        status, body = server.post("/v1/library", {
            "license_key": settings.LICENSE_KEY,
            "instance_id": seat.instance_id() or "unknown",
            "name": name,
            "current_version": current,
        }, timeout=REQUEST_TIMEOUT)
    except Exception as exc:              # transport: DNS, refused, TLS
        raise Unavailable("The licence server could not be reached to "
                          "download the %s library." % name) from exc
    if status != 200 or not body.get("ok"):
        raise Unavailable(body.get("message")
                          or "The %s library could not be downloaded "
                             "(HTTP %s)." % (name, status))
    return body.get("library") or {}


def _fetch_pack(name: str, pack: dict, state: _State) -> Path:
    """Stream the archive to the disk, checking size and sha256."""
    target = _dir(name) / (".v%d.tar.part" % pack["version"])
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = pack.get("size") or 0
    digest = hashlib.sha256()
    written = 0
    request = urllib.request.Request(pack["url"],
                                     headers={"User-Agent": "ember"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, \
            open(target, "wb") as out:
        expected = expected or int(response.headers.get("Content-Length") or 0)
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_PACK_BYTES:
                raise Unavailable("The %s library download was larger than "
                                  "expected and was stopped." % name)
            digest.update(chunk)
            out.write(chunk)
            if expected:
                state.progress = min(written / expected, 0.99)
    if pack.get("sha256") and digest.hexdigest() != pack["sha256"]:
        target.unlink(missing_ok=True)
        raise Unavailable("The %s library download was damaged. Try again."
                          % name)
    return target


def _unpack(name: str, archive: Path, version: int) -> dict:
    """Unpack into v<version>/ and return the archive's manifest.

    Only the names _MEMBER allows are written, as regular files, to paths
    built here — never to a path taken from the archive."""
    root = _dir(name) / ("v%d" % version)
    manifest = None
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            if not member.isfile():
                continue
            if member.name == "manifest.json":
                manifest = json.loads(tar.extractfile(member).read())
                continue
            match = _MEMBER.match(member.name)
            if not match:
                log.warning("Skipping %r in the %s library archive",
                            member.name, name)
                continue
            dest = root / match[1] / match[2] / ("%s.webp" % match[3])
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, \
                    open(dest, "wb") as out:
                shutil.copyfileobj(source, out)
    if not _valid(manifest) or manifest["version"] != version:
        raise Unavailable("The %s library archive has no usable manifest."
                          % name)
    return manifest


def _download(name: str, current: int | None) -> None:
    """The thread body: ask, fetch, unpack, then swap the manifest in."""
    state = _states[name]
    archive = None
    try:
        pack = _ask(name, current)
        if pack.get("up_to_date") or not pack.get("url"):
            return
        version = int(pack["version"])
        log.info("Downloading the %s library v%d (%s bytes)", name, version,
                 pack.get("size") or "?")
        archive = _fetch_pack(name, pack, state)
        manifest = _unpack(name, archive, version)
        cached = _dir(name) / "manifest.json"
        partial = cached.with_name("manifest.json.part")
        partial.write_text(json.dumps(manifest), encoding="utf-8")
        partial.replace(cached)
        with state.lock:
            state.manifest = manifest
            state.error = ""
        log.info("✓ %s library v%d: %d pictures", name, version,
                 len(manifest["items"]))
    except Unavailable as exc:
        state.error = str(exc)
        log.warning("%s", exc)
    except Exception as exc:
        state.error = "The %s library could not be downloaded." % name
        log.warning("The %s library download failed: %s", name, exc)
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)
        with state.lock:
            state.busy = False
            state.progress = 0.0


def _start(name: str, current: int | None) -> None:
    """Start a download unless one is running. Call with the lock held."""
    state = _states[name]
    if state.busy:
        return
    state.busy, state.progress, state.error = True, 0.0, ""
    threading.Thread(target=_download, args=(name, current),
                     name="library-%s" % name, daemon=True).start()


# ── what the routes call ──────────────────────────────────────────────────────
def manifest(name: str) -> dict:
    """{"status": "ready", "version", "items"} or
    {"status": "downloading", "progress"}. Unavailable when the first
    download failed — the picker shows why and asking again retries."""
    if name not in NAMES:
        raise KeyError(name)
    state = _states[name]
    with state.lock:
        if not state.loaded:
            state.manifest, state.loaded = _read_cached(name), True
        current = state.manifest
        if current is None:
            if state.error and not state.busy:
                error, state.error = state.error, ""
                raise Unavailable(error)
            _start(name, None)
            return {"status": "downloading", "progress": state.progress}
        if not state.checked and settings.LICENSE_KEY:
            state.checked = True
            _start(name, current["version"])
    return {"status": "ready", "version": current["version"],
            "items": current["items"]}


def file(name: str, size: str, item_id: str, kind: str) -> Path:
    """One picture, from the unpacked pack. KeyError for anything the
    manifest does not name, or that is named and missing on the disk."""
    if (name not in NAMES or size not in SIZES or kind not in KINDS
            or not _ID.match(item_id)):
        raise KeyError(item_id)
    state = _states[name]
    with state.lock:
        if not state.loaded:
            state.manifest, state.loaded = _read_cached(name), True
        current = state.manifest
    if current is None:
        raise KeyError(item_id)
    item = next((row for row in current["items"] if row.get("id") == item_id),
                None)
    if item is None or kind not in item.get("variants", ()):
        raise KeyError(item_id)
    path = (_dir(name) / ("v%d" % current["version"]) / size / item_id
            / ("%s.webp" % kind))
    if not path.is_file():
        raise KeyError(item_id)
    return path
