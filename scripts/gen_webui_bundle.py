#!/usr/bin/env python3
"""Turn `webui/dist/` into `webui_bundle.py` — the SPA as a Python module.

Run it on the dev machine, after `npm run build`, and **commit the result**:

    make webui          # npm ci && npm run build && this script

Why a module and not data files
-------------------------------
Nuitka follows imports. A module named by `--include-module=webui_bundle`
comes along whole, while a data file needs its own `--include-data-files`
entry, a correct relative destination, and a runtime path that resolves
inside a onefile extraction. The deleted `theme.py:9-11` said the same
thing about the CSS and JS it held as Python strings, and it was right for
the same reason.

The bigger prize is constraint 5 in context.md: **neither build host has
Node.** The Linux build runs on a RunPod pod, the Windows build on a
Windows box, and neither will ever have a toolchain that can turn TSX into
JavaScript. Committing the generated module moves that requirement onto
the one machine that already has Node — this one — and leaves both build
hosts with nothing to do but verify freshness (`scripts/check_webui.py`).
It also means a fresh clone can run `scripts/dryrun.py` without touching
npm at all.

gzip bytes, not base64
----------------------
Base64 inflates by 4/3 and costs a decode of every asset at import time.
A `bytes` literal is lifted straight into Nuitka's constants blob, so the
compressed size is what ships and there is nothing to decode until a
request asks for it — and `webui.py` mostly hands the gzip stream to the
browser untouched, so usually not even then.

Brotli was rejected: ~15% better on a sub-megabyte payload, in exchange
for a pip dependency on **both** build hosts that
`scripts/check_build_args.py` structurally cannot see — it diffs flags
between the two build scripts, and a missing Python package is not a flag.

Determinism
-----------
The generated file is committed, so regenerating it on Windows and in WSL
must not produce a spurious diff:

  * written `encoding="utf-8"`, **no BOM**, `newline="\\n"`;
  * the gzip container is assembled by hand rather than taken from
    `gzip.compress`, which stamps a platform-dependent OS byte into the
    header (0x0a here, 0x03 on Linux) and the mtime unless told not to;
  * media types come from the table below rather than `mimetypes`, which
    on Windows answers from the registry — where `.js` can be
    `text/plain`, and a `text/plain` module script is refused by every
    browser;
  * `SOURCE_HASH` normalises CRLF to LF, so it does not depend on how the
    checkout was configured. `.gitattributes` pins `webui/**` to LF as
    well; this is the half that still holds for a tree checked out before
    that rule existed.

What is NOT deterministic is the deflate stream itself — a different zlib
build can emit different bytes for the same input. That only makes a noisy
diff, never a wrong one: freshness is decided by `SOURCE_HASH`, which is
computed over the *sources*, and never by comparing compressed output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "webui"
DIST = WEBUI / "dist"
OUTPUT = ROOT / "webui_bundle.py"

# What SOURCE_HASH covers, and therefore what check_webui.py calls a stale
# bundle. The source tree, plus every input that changes the output of
# `vite build` without being in it: the HTML entry point, the build config,
# and both halves of the dependency pin — package.json because it names the
# versions, package-lock.json because it decides them.
#
# Deliberately absent: tsconfig.json (type-checking only — it cannot change
# a byte of the emitted bundle) and webui/test/ (a fixture harness that
# never ships).
SOURCE_DIRS = ("src",)
SOURCE_FILES = ("index.html", "package.json", "package-lock.json",
                "vite.config.ts")

# Media types, spelled out rather than guessed. See the Determinism note
# above: `mimetypes` reads HKEY_CLASSES_ROOT on Windows, so the answer
# depends on what other software the dev box happens to have installed.
#
# webui.py carries the same table for the `webui/dist/` path it serves in
# development. Kept separate on purpose — importing it here would import
# fastapi and config, and config creates directories at import time.
MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
FALLBACK_TYPE = "application/octet-stream"

# How many raw bytes go into each b"..." fragment in the emitted file. One
# 400 KB line is valid Python and Nuitka does not care, but it makes the
# file unopenable in editors that do not stream, and some linters walk a
# line character by character. Adjacent bytes literals are folded by the
# parser at compile time, so this costs nothing at run time — and splitting
# the *bytes* rather than their repr is what keeps a split from ever
# landing in the middle of an escape sequence.
CHUNK = 4096


def media_type(name: str) -> str:
    return MEDIA_TYPES.get(Path(name).suffix.lower(), FALLBACK_TYPE)


def gzip_bytes(raw: bytes) -> bytes:
    """A gzip stream with no timestamp and no platform in its header.

    `gzip.compress(raw, 9, mtime=0)` would be one line, but it writes the
    build platform into byte 9 of the header — which turns "regenerate on
    the other machine" into a whole-file diff.
    """
    deflate = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = deflate.compress(raw) + deflate.flush()
    header = b"\x1f\x8b\x08\x00" + struct.pack("<L", 0) + b"\x02\xff"
    trailer = struct.pack("<LL", zlib.crc32(raw) & 0xFFFFFFFF,
                          len(raw) & 0xFFFFFFFF)
    return header + body + trailer


def _normalised(path: Path) -> bytes:
    """File bytes with CRLF collapsed to LF. See the Determinism note."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def iter_sources():
    """Every hash input, as (posix name, path), in a stable order.

    Sorted by name rather than left to the filesystem: `rglob` returns
    NTFS's ordering on Windows and ext4's in WSL, and an unsorted hash
    would differ between them for reasons that have nothing to do with the
    source.
    """
    found = []
    for name in SOURCE_FILES:
        path = WEBUI / name
        if path.is_file():
            found.append((name, path))
    for directory in SOURCE_DIRS:
        for path in (WEBUI / directory).rglob("*"):
            if path.is_file():
                found.append((path.relative_to(WEBUI).as_posix(), path))
    return sorted(found, key=lambda pair: pair[0])


def source_hash() -> str:
    """`sha256:...` over the front-end sources.

    The name is hashed alongside the content so that renaming a file, or
    deleting one whose bytes appear elsewhere, still moves the hash.
    """
    digest = hashlib.sha256()
    for name, path in iter_sources():
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_normalised(path))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def missing_sources() -> list[str]:
    """Hash inputs that are not on disk — an incomplete checkout."""
    absent = [name for name in SOURCE_FILES if not (WEBUI / name).is_file()]
    absent += [name for name in SOURCE_DIRS if not (WEBUI / name).is_dir()]
    return absent


def iter_assets():
    """Every built file under webui/dist, as (posix name, bytes)."""
    for path in sorted(DIST.rglob("*"), key=lambda p: p.as_posix()):
        if path.is_file():
            yield path.relative_to(DIST).as_posix(), path.read_bytes()


def _literal(raw: bytes, indent: str) -> str:
    """`raw` as one or more adjacent bytes literals, one per line."""
    chunks = [raw[i:i + CHUNK] for i in range(0, len(raw), CHUNK)] or [b""]
    if len(chunks) == 1:
        return repr(chunks[0])
    return "(" + ("\n" + indent).join(repr(c) for c in chunks) + ")"


HEADER = '''# GENERATED by scripts/gen_webui_bundle.py — do not edit.
#
# The React front end, built by Vite and gzipped, as a Python module: that
# is how it reaches the Nuitka binary, because Nuitka follows imports and a
# data file would need its own --include-data-files entry. Regenerate with
# `make webui`. A conflict here is resolved by regenerating, never by
# editing (see .gitattributes).
"""The built React bundle. Generated — see scripts/gen_webui_bundle.py."""

# Over webui/src, index.html, package.json, package-lock.json and
# vite.config.ts, with CRLF normalised. scripts/check_webui.py recomputes
# it and fails the build when it no longer matches, which is what stops a
# stale front end from shipping.
SOURCE_HASH = {source_hash!r}
BUILT_AT = {built_at!r}

# name -> (gzip bytes, media type, ETag). Served by webui.py, which hands
# the gzip stream to the browser as-is whenever it will take it.
ASSETS = {{
'''


def render(assets, built_at: str) -> str:
    total = sum(len(raw) for _name, raw in assets)
    out = [HEADER.format(source_hash=source_hash(), built_at=built_at)]
    for name, raw in assets:
        blob = gzip_bytes(raw)
        etag = 'W/"%s"' % hashlib.sha256(raw).hexdigest()[:32]
        out.append("    # %s — %d bytes, %d gzipped\n"
                   % (name, len(raw), len(blob)))
        out.append("    %r: (\n        %s,\n        %r,\n        %r,\n    ),\n"
                   % (name, _literal(blob, " " * 8), media_type(name), etag))
    out.append("}\n")
    out.append("\n# %d files, %d bytes uncompressed.\n" % (len(assets), total))
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package webui/dist into the committed webui_bundle.py.")
    parser.add_argument("--check", action="store_true",
                        help="print the current source hash and exit")
    args = parser.parse_args()

    if args.check:
        print(source_hash())
        return 0

    absent = missing_sources()
    if absent:
        sys.exit("webui/ is missing %s — is this a complete checkout?"
                 % ", ".join(absent))

    if not (DIST / "index.html").is_file():
        sys.exit(
            "No webui/dist/index.html. This script packages a build, it does\n"
            "not make one:\n"
            "    cd webui && npm ci && npm run build\n"
            "or just `make webui`, which does both."
        )

    assets = list(iter_assets())
    built_at = dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")

    # newline="\n" and no BOM: regenerating in WSL must produce the same
    # bytes, or the committed module diffs on every platform switch. See
    # the Determinism note in the module docstring.
    OUTPUT.write_text(render(assets, built_at), encoding="utf-8",
                      newline="\n")

    print("wrote %s — %d assets, %.0f KB of Python"
          % (OUTPUT.relative_to(ROOT).as_posix(), len(assets),
             OUTPUT.stat().st_size / 1024))
    for name, raw in assets:
        print("    %-40s %7d -> %6d" % (name, len(raw), len(gzip_bytes(raw))))
    print("  SOURCE_HASH %s" % source_hash())
    return 0


if __name__ == "__main__":
    sys.exit(main())
