#!/usr/bin/env python3
"""Upload a private LoRA to the private builds bucket.

Operator tool, never bundled. Run on the machine that holds the R2 write
key (license-validator/.env, R2_WRITE_*), with a LoRA the trainer made:

    python scripts/r2_upload_lora.py face_sayara.safetensors \
        --name "Sayara" --trigger sayarax [--strength 1.0] [--dry-run]

The file goes to loras/private/<file> in the builds bucket — the private
one. Pods never address it; the licence server signs a short-lived link
for it in the /v1/catalog answer, and only for a licence whose
`private_loras` names the LoRA's id. Uploading does not offer it to
anyone. The DB rows do, and this prints the command that writes them:

    cd license-validator && npm run private-lora -- add face-sayara ...
    cd license-validator && npm run private-lora -- grant face-sayara --keys K1,K2

The key is the file name, so a retrained LoRA is uploaded under a new
file name (face_sayara_v2.safetensors) rather than over the old one: a
pod that already downloaded the old file keeps it by name and would never
fetch a replacement.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "prompt_library"))

from r2_presign import presign             # noqa: E402
from store import load_credentials         # noqa: E402

PREFIX = "loras/private/"
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
UPLOAD_RETRIES = 3
PRESIGN_TTL = 1800


def lora_id(file_name: str) -> str:
    """face_sayara.safetensors -> face-sayara, the catalogue id it gets."""
    stem = Path(file_name).stem.lower()
    return re.sub(r"[^a-z0-9-]+", "-", stem).strip("-")[:63]


def remote_size(key: str, bucket: str) -> int | None:
    """The object's size if it is already in the bucket, else None."""
    request = urllib.request.Request(presign("HEAD", key, 300, bucket=bucket),
                                     method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return int(response.headers.get("Content-Length") or -1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise SystemExit("Could not check %s: HTTP %s" % (key, exc.code))


def upload(body: bytes, key: str, bucket: str) -> None:
    for attempt in range(1, UPLOAD_RETRIES + 1):
        request = urllib.request.Request(
            presign("PUT", key, PRESIGN_TTL, bucket=bucket),
            data=body, method="PUT",
            headers={"Content-Type": "application/octet-stream",
                     "Content-Length": str(len(body))},
        )
        try:
            with urllib.request.urlopen(request, timeout=1800) as response:
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


def quote(text: str) -> str:
    return '"%s"' % text.replace('"', '\\"')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", type=Path, help="the trained .safetensors")
    parser.add_argument("--id", help="catalogue id (default: from the file name)")
    parser.add_argument("--name", help="what the LoRA dropdown shows")
    parser.add_argument("--trigger", default="", help="its trigger word")
    parser.add_argument("--strength", type=float, default=1.0,
                        help="the row's default strength")
    parser.add_argument("--dry-run", action="store_true",
                        help="check everything, upload nothing")
    args = parser.parse_args()

    path: Path = args.file
    if not path.is_file():
        raise SystemExit("No such file: %s" % path)
    if not FILE_RE.match(path.name):
        raise SystemExit("%s is not a plain .safetensors name (letters, digits,"
                         " . _ -)" % path.name)
    rid = args.id or lora_id(path.name)
    if not ID_RE.match(rid):
        raise SystemExit("id %r is not a valid catalogue id (a-z 0-9 -)" % rid)
    key = PREFIX + path.name

    body = path.read_bytes()
    print("%s: %.1f MB, sha256 %s -> %s"
          % (path.name, len(body) / 1e6,
             hashlib.sha256(body).hexdigest()[:16], key))
    if args.dry_run:
        print("  dry run: not uploaded")
    else:
        bucket = load_credentials(write=True)
        have = remote_size(key, bucket)
        if have == len(body):
            print("  already in the bucket at this size — not uploaded again")
        elif have is not None:
            raise SystemExit(
                "  %s is already in the bucket with a different size. Pods "
                "that have the old file never fetch it again, so a retrained "
                "LoRA needs a new file name (e.g. %s_v2.safetensors)."
                % (key, path.stem))
        else:
            upload(body, key, bucket)
            print("  uploaded")

    name = args.name or rid
    print("\nNext, write the DB rows (nothing is offered until then):\n")
    print("  cd license-validator")
    print("  npm run private-lora -- add %s --file %s --name %s --trigger %s "
          "--strength %g" % (rid, path.name, quote(name), quote(args.trigger),
                             args.strength))
    print("  npm run private-lora -- grant %s --keys <licence key>,<licence key>"
          % rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
