#!/usr/bin/env python3
"""Pack the pose library and upload it to the private builds bucket.

Operator tool, never bundled. Reads the tree scripts/build_pose_library.py
wrote, puts exactly what its manifest names into one archive, and PUTs it
to the builds bucket — the private one; pods never address it, they get a
short-lived URL for the archive from the licence server (POST /v1/library):

    r2_upload_poses.py --dir tmp/pose-stage --dry-run   # build and check only
    r2_upload_poses.py --dir tmp/pose-stage             # build and upload

The object key carries the version, so a published pack is never changed
in place: a new pack is a new --version from build_pose_library.py and a
new key. Uploading does not publish it. The licence server hands out
whatever its `libraries` row names, so the last step is that row, and this
prints the command that writes it:

    cd license-validator && npm run library -- set poses --version 1 ...

The archive is a plain tar (the pictures are WebP already, so compressing
it again buys nothing) with fixed metadata, so packing the same tree twice
gives the same sha256.

Credentials are build.sh's, from the environment:

    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (read & write)
    R2_BUILDS_BUCKET

`make` loads them from license-validator/.env and swaps the write key in;
run by hand, export R2_WRITE_* as R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ember import settings              # noqa: E402
from r2_presign import presign          # noqa: E402

NAME = "poses"
FEATURES = ("qwen_edit_2511",)          # the tabs whose field names it
UPLOAD_RETRIES = 3
PRESIGN_TTL = 900


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    info.uname = info.gname = ""
    tar.addfile(info, io.BytesIO(data))


def pack(source: Path, dest: Path) -> tuple[dict, int]:
    """Write the archive; returns (manifest, number of pictures)."""
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    root = source / ("v%d" % version)
    missing, count = [], 0
    with tarfile.open(dest, "w", format=tarfile.USTAR_FORMAT) as tar:
        _add(tar, "manifest.json",
             json.dumps(manifest, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8"))
        for item in sorted(manifest["items"], key=lambda row: row["id"]):
            for kind in item["variants"]:
                for size in ("thumbs", "full"):
                    rel = "%s/%s/%s.webp" % (size, item["id"], kind)
                    path = root / rel
                    if not path.is_file():
                        missing.append(rel)
                        continue
                    _add(tar, rel, path.read_bytes())
                    count += 1
    if missing:
        raise SystemExit("%d file(s) the manifest names are missing, e.g. %s"
                         % (len(missing), ", ".join(missing[:5])))
    return manifest, count


def upload(local: Path, key: str, bucket: str) -> None:
    body = local.read_bytes()
    for attempt in range(1, UPLOAD_RETRIES + 1):
        request = urllib.request.Request(
            presign("PUT", key, PRESIGN_TTL, bucket=bucket),
            data=body, method="PUT",
            headers={"Content-Type": "application/x-tar",
                     "Content-Length": str(len(body))},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                if 200 <= response.status < 300:
                    return
                raise urllib.error.HTTPError(key, response.status,
                                             response.reason, {}, None)
        except urllib.error.HTTPError as exc:
            retriable = exc.code in (408, 429) or exc.code >= 500
            if not retriable or attempt == UPLOAD_RETRIES:
                detail = exc.read().decode("utf-8", "replace")[:400] \
                    if exc.fp else ""
                raise SystemExit("Upload refused: HTTP %s %s %s"
                                 % (exc.code, exc.reason, detail))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == UPLOAD_RETRIES:
                raise SystemExit("Upload failed: %s" % exc)
        wait = 3 * 2 ** (attempt - 1)
        print("  upload failed (%d/%d), retrying in %ds"
              % (attempt, UPLOAD_RETRIES, wait))
        time.sleep(wait)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", type=Path,
                        default=settings.LIBRARY_DIR / NAME,
                        help="the tree build_pose_library.py wrote")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and check the archive, upload nothing")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "pack.tar"
        manifest, count = pack(args.dir, archive)
        version = manifest["version"]
        data = archive.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        key = "libraries/%s/v%d/pack.tar" % (NAME, version)
        print("%s v%d: %d poses, %d pictures, %.1f MB, sha256 %s"
              % (NAME, version, len(manifest["items"]), count,
                 len(data) / 1e6, sha256[:16]))
        if args.dry_run:
            print("  dry run: not uploaded (would be %s)" % key)
            return 0
        bucket = os.environ.get("R2_BUILDS_BUCKET", "").strip()
        if not bucket:
            raise SystemExit("R2_BUILDS_BUCKET is not set.")
        upload(archive, key, bucket)
        print("  uploaded to %s/%s" % (bucket, key))

    print("\nPublish it (the licence server signs whatever this row names):\n"
          "  cd license-validator && npm run library -- set %s --version %d "
          "--key %s --sha256 %s --size %d --features %s"
          % (NAME, version, key, sha256, len(data), ",".join(FEATURES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
