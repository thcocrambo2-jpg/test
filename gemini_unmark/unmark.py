"""Remove the visible Gemini sparkle watermark from every image in a folder.

    python gemini_unmark/unmark.py <folder>

Cleaned copies go to <folder>/output/; the originals are never touched.
Uses `wmr remove` (froggeric/gemini-watermark-and-synthid-remover v1.17.1,
MIT), visible mark only - no SynthID or metadata stripping. Fetch it into
gemini_unmark/bin/wmr/ with `python gemini_unmark/unmark.py --setup`.
"""

import argparse
import hashlib
import io
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WMR_DIR = HERE / "bin" / "wmr"
EXE = WMR_DIR / "wmr-windows-x86_64" / "wmr.exe"
ZIP_URL = ("https://github.com/froggeric/gemini-watermark-and-synthid-remover/"
           "releases/download/v1.17.1/wmr-windows-x86_64.zip")
# The release publishes no checksums; this is wmr.exe as first downloaded.
EXE_SHA256 = "02c26273a983728c3fa2e1cb94216de4ed9868655d365b9afede364215d2e554"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def setup() -> None:
    data = urllib.request.urlopen(ZIP_URL).read()
    WMR_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(WMR_DIR)
    if hashlib.sha256(EXE.read_bytes()).hexdigest() != EXE_SHA256:
        EXE.unlink()
        sys.exit("wmr.exe checksum mismatch - removed it, not installing")
    print(f"installed {EXE}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--setup", action="store_true", help="download wmr")
    args = ap.parse_args()

    if args.setup:
        setup()
        return
    if not args.folder:
        ap.error("give the folder of images")
    if not EXE.exists():
        sys.exit(f"{EXE} missing - run: python {Path(__file__).name} --setup")

    src = Path(args.folder).resolve()
    images = [p for p in src.iterdir()
              if p.is_file() and p.suffix.lower() in EXTS]
    if not images:
        sys.exit(f"no images in {src}")
    out = src / "output"
    out.mkdir(exist_ok=True)

    # Folder mode, not recursive, so it never re-reads its own output/.
    rc = subprocess.run([str(EXE), "remove", str(src), "-o", str(out)]).returncode
    done = [p for p in images if (out / p.name).exists()]
    missing = [p.name for p in images if not (out / p.name).exists()]
    print(f"\nwmr exit {rc}: {len(done)}/{len(images)} written -> {out}")
    for name in missing:
        print(f"  not written: {name}")


if __name__ == "__main__":
    main()
