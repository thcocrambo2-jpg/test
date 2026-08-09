#!/usr/bin/env python3
"""Dump every scrap of metadata carried by the images in a folder.

Operator tool, not part of the shipped app. The question it answers is
"what rides along inside these files that I did not intend to put there" —
the generation prompt, the camera that took the original, where it was
standing when it did.

    python3 scripts/inspect_image_metadata.py                  # ComfyUI's input dir
    python3 scripts/inspect_image_metadata.py path/to/dir      # anywhere
    python3 scripts/inspect_image_metadata.py a.png b.jpg      # named files
    python3 scripts/inspect_image_metadata.py --full           # no truncation
    python3 scripts/inspect_image_metadata.py --json           # machine-readable

Four separate metadata systems can coexist in one file and none of them
knows about the others, so all four are read rather than whichever the
format "normally" uses:

  • PNG text chunks — where ComfyUI writes `prompt` (the API graph it
    executed) and `workflow` (the editor graph), and where A1111-lineage
    tools write `parameters`. This is the one that carries prompts.
  • EXIF — camera, lens, timestamps, and GPS. Survives a crop, survives a
    re-save by most tools, and is the one that leaks a location.
  • XMP / IPTC — the publishing world's sidecar data: creator, rights,
    captions, and whatever an editing suite decided to staple on.
  • ICC profile — not identifying, but its presence changes how the image
    renders elsewhere, so its absence is worth knowing about too.

Anything found under a key this script does not recognise is printed
verbatim rather than dropped. An unknown key is exactly the interesting
case: it is something a tool in the chain added without being asked.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path

# The app's modules live one level up; this script sits outside the package
# so build.sh / Nuitka never sweep it into the shipped binary.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ExifTags, IptcImagePlugin, PngImagePlugin  # noqa: E402

from config import COMFY_DIR  # noqa: E402

# A ComfyUI workflow graph is routinely several hundred KB of JSON in a
# single tEXt chunk. Pillow's default ceiling silently drops anything
# larger, which would make this tool report "no prompt" on precisely the
# files whose prompt is most worth seeing.
PngImagePlugin.MAX_TEXT_CHUNK = 64 * 1024 * 1024
PngImagePlugin.MAX_TEXT_MEMORY = 256 * 1024 * 1024

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff",
                  ".bmp", ".gif", ".avif", ".heic", ".heif"}

# Text-chunk keys known to carry a generation prompt, in the spelling each
# ecosystem uses. Matched case-insensitively — the casing is not stable
# across tools even within one ecosystem.
PROMPT_KEYS = {"prompt", "workflow", "parameters", "negative_prompt",
               "description", "comment", "usercomment", "dream",
               "sd-metadata", "generation_data", "aiassistant"}

# EXIF tags that identify a person, a device, or a place. Everything else
# EXIF holds is exposure trivia; these are the ones that answer "can this
# file be traced back to me".
SENSITIVE_EXIF = {"Make", "Model", "BodySerialNumber", "LensMake",
                  "LensModel", "LensSerialNumber", "CameraOwnerName",
                  "Artist", "Copyright", "Software", "HostComputer",
                  "DateTimeOriginal", "DateTimeDigitized", "DateTime",
                  "ImageDescription", "UserComment", "XPAuthor",
                  "XPComment", "XPTitle", "XPSubject", "XPKeywords",
                  "GPSInfo"}

TRUNCATE_AT = 600

C_RESET, C_DIM, C_BOLD = "\033[0m", "\033[2m", "\033[1m"
C_CYAN, C_YELLOW, C_RED, C_GREEN = ("\033[36m", "\033[33m",
                                    "\033[31m", "\033[32m")


def _supports_colour() -> bool:
    return sys.stdout.isatty()


class Fmt:
    """Colour that turns itself off when the output is not a terminal.

    Piping this into a file or a pager is the normal way to read a 300 KB
    workflow dump, and escape codes in that file help nobody.
    """

    def __init__(self, enabled: bool):
        self.on = enabled

    def __call__(self, text: str, colour: str) -> str:
        return f"{colour}{text}{C_RESET}" if self.on else text


# ── Value rendering ───────────────────────────────────────────────────────────
def _jsonable(value):
    """Coerce Pillow's exotic scalar types into something json can hold.

    getexif() hands back IFDRational, bytes and tuples freely, and every
    one of them raises inside json.dumps. Bytes get a best-effort utf-8
    decode first because UserComment — the tag most likely to hold a
    prompt — is bytes with a 8-byte encoding prefix.
    """
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace").strip("\x00")
        # EXIF UserComment prefixes the payload with an 8-byte charset id.
        for prefix in ("ASCII\x00\x00\x00", "UNICODE\x00", "JIS\x00\x00\x00\x00\x00"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text.strip("\x00") or f"<{len(value)} bytes>"
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return float(value) if value.denominator else 0.0
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _render(value, full: bool) -> str:
    """One metadata value as console text, pretty-printing embedded JSON.

    A ComfyUI `prompt` chunk is JSON stored as a string. Printed raw it is
    one unreadable line; re-indented it is the graph that produced the
    image, which is the entire reason someone runs this.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[" and stripped[-1:] in "}]":
            try:
                value = json.dumps(json.loads(stripped), indent=2)
            except (json.JSONDecodeError, ValueError):
                pass
    text = value if isinstance(value, str) else json.dumps(
        _jsonable(value), indent=2, default=str)
    if not full and len(text) > TRUNCATE_AT:
        head = text[:TRUNCATE_AT].rstrip()
        return f"{head}\n… [{len(text):,} chars total — rerun with --full]"
    return text


def _indent(text: str, pad: str = "        ") -> str:
    return "\n".join(pad + line for line in text.splitlines())


# ── GPS ───────────────────────────────────────────────────────────────────────
def _dms_to_degrees(dms, ref: str | None) -> float | None:
    """EXIF degrees/minutes/seconds → signed decimal degrees."""
    try:
        degrees, minutes, seconds = (float(v) for v in dms)
    except (TypeError, ValueError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return -value if ref in ("S", "W") else value


def read_gps(exif) -> dict:
    """Decode the GPS IFD into something a human can act on.

    Reported separately from the rest of EXIF and with a map link, because
    "GPSLatitude: (12, 58, 43.2)" does not read as a disclosure and
    "12.978667, 77.594000" does.
    """
    try:
        raw = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except (AttributeError, KeyError, OSError):
        return {}
    if not raw:
        return {}
    gps = {ExifTags.GPSTAGS.get(k, f"Unknown_{k}"): v for k, v in raw.items()}
    out = {k: _jsonable(v) for k, v in gps.items()}
    lat = _dms_to_degrees(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
    lon = _dms_to_degrees(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
    if lat is not None and lon is not None:
        out["_decimal"] = f"{lat:.6f}, {lon:.6f}"
        out["_maps"] = f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
    return out


# ── Extraction ────────────────────────────────────────────────────────────────
def _text_chunks(img) -> dict:
    """PNG/WebP text chunks, minus the keys Pillow puts there itself.

    img.info mixes decoder state (dpi, gamma, transparency) in with the
    actual embedded text. Filtering by type rather than by an allowlist of
    names keeps unknown-but-textual keys, which are the interesting ones.
    """
    structural = {"dpi", "gamma", "transparency", "aspect", "icc_profile",
                  "exif", "xmp", "photoshop", "adobe", "adobe_transform",
                  "jfif", "jfif_version", "jfif_unit", "jfif_density",
                  "progression", "progressive", "chromaticity", "srgb",
                  "interlace", "compression", "loop", "duration",
                  "background", "palette", "transparency", "bits",
                  "signed", "encoderinfo", "encoderconfig"}
    found = dict(getattr(img, "text", {}) or {})
    for key, value in (img.info or {}).items():
        if key.lower() in structural or key in found:
            continue
        if isinstance(value, (str, bytes)):
            found[key] = value
    return found


def _iptc(img) -> dict:
    try:
        raw = IptcImagePlugin.getiptcinfo(img)
    except Exception:
        return {}
    return {f"{a}:{b}": _jsonable(v) for (a, b), v in (raw or {}).items()}


def _xmp(img) -> str:
    for key in ("XML:com.adobe.xmp", "xmp"):
        value = (img.info or {}).get(key)
        if value:
            return value.decode("utf-8", "replace") if isinstance(
                value, bytes) else str(value)
    return ""


def inspect(path: Path) -> dict:
    """Everything one file carries. Never raises — a bad file is a result.

    A folder scan that dies on the first unreadable file is useless for
    the job this does, so the failure is recorded as data and the walk
    continues.
    """
    stat = path.stat()
    record = {
        "file": str(path),
        "name": path.name,
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(" ", "seconds"),
        "error": None,
    }
    try:
        with Image.open(path) as img:
            record.update({
                "format": img.format,
                "mode": img.mode,
                "dimensions": f"{img.width} × {img.height}",
                "text_chunks": {k: _jsonable(v)
                                for k, v in _text_chunks(img).items()},
                "iptc": _iptc(img),
                "xmp": _xmp(img),
                "icc_profile_bytes": len((img.info or {}).get("icc_profile") or b""),
            })
            exif = img.getexif()
            decoded = {}
            for tag_id, value in (exif or {}).items():
                name = ExifTags.TAGS.get(tag_id, f"Unknown_{tag_id}")
                if name == "GPSInfo":
                    continue            # reported on its own, decoded
                decoded[name] = _jsonable(value)
            # The Exif sub-IFD is where DateTimeOriginal, LensModel and
            # UserComment actually live; the top-level IFD alone looks
            # almost empty and would read as "no EXIF here".
            try:
                for tag_id, value in (exif.get_ifd(ExifTags.IFD.Exif) or {}).items():
                    decoded.setdefault(
                        ExifTags.TAGS.get(tag_id, f"Unknown_{tag_id}"),
                        _jsonable(value))
            except (AttributeError, KeyError, OSError):
                pass
            record["exif"] = decoded
            record["gps"] = read_gps(exif) if exif else {}
    except Exception as exc:              # unreadable, truncated, not an image
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


# ── Reporting ─────────────────────────────────────────────────────────────────
def human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:,.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GB"


def print_record(rec: dict, fmt: Fmt, full: bool) -> None:
    print()
    print(fmt(f"── {rec['name']} ", C_BOLD) + fmt(
        "─" * max(4, 62 - len(rec["name"])), C_DIM))
    if rec["error"]:
        print("   " + fmt(f"could not read: {rec['error']}", C_RED))
        return
    print(f"   {rec['format']} · {rec['dimensions']} · {rec['mode']} · "
          f"{human(rec['bytes'])} · modified {rec['modified']}")

    chunks = rec["text_chunks"]
    if chunks:
        prompts = [k for k in chunks if k.lower() in PROMPT_KEYS]
        label = "text chunks"
        if prompts:
            label += fmt(f"  ← prompt data in: {', '.join(prompts)}", C_YELLOW)
        print("\n   " + fmt(label, C_CYAN))
        for key, value in chunks.items():
            marker = fmt(" ★", C_YELLOW) if key.lower() in PROMPT_KEYS else ""
            print(f"     {fmt(key, C_BOLD)}{marker}:")
            print(_indent(_render(value, full)))
    else:
        print("\n   " + fmt("text chunks: none", C_DIM))

    exif = rec["exif"]
    if exif:
        risky = sorted(set(exif) & SENSITIVE_EXIF)
        label = f"EXIF ({len(exif)} tags)"
        if risky:
            label += fmt(f"  ← identifying: {', '.join(risky)}", C_YELLOW)
        print("\n   " + fmt(label, C_CYAN))
        for key, value in sorted(exif.items()):
            marker = fmt(" ★", C_YELLOW) if key in SENSITIVE_EXIF else ""
            print(f"     {fmt(key, C_BOLD)}{marker}: "
                  f"{_render(value, full).replace(chr(10), ' ')}")
    else:
        print("   " + fmt("EXIF: none", C_DIM))

    if rec["gps"]:
        print("\n   " + fmt("GPS  ← THIS FILE CARRIES A LOCATION", C_RED))
        for key, value in rec["gps"].items():
            print(f"     {fmt(key, C_BOLD)}: {value}")
    else:
        print("   " + fmt("GPS: none", C_DIM))

    if rec["iptc"]:
        print("\n   " + fmt("IPTC", C_CYAN))
        for key, value in rec["iptc"].items():
            print(f"     {fmt(key, C_BOLD)}: {_render(value, full)}")
    if rec["xmp"]:
        print("\n   " + fmt("XMP", C_CYAN))
        print(_indent(_render(rec["xmp"], full)))
    if rec["icc_profile_bytes"]:
        print("   " + fmt(
            f"ICC profile: {human(rec['icc_profile_bytes'])} embedded", C_DIM))


def print_summary(records: list[dict], fmt: Fmt) -> None:
    ok = [r for r in records if not r["error"]]
    with_prompt = [r for r in ok
                   if any(k.lower() in PROMPT_KEYS for k in r["text_chunks"])]
    with_exif = [r for r in ok if r["exif"]]
    with_gps = [r for r in ok if r["gps"]]
    with_id = [r for r in ok if set(r["exif"]) & SENSITIVE_EXIF]
    bad = [r for r in records if r["error"]]

    print("\n" + fmt("═" * 64, C_DIM))
    print(f"  {len(records)} file(s) · {len(ok)} readable"
          + (f" · {len(bad)} unreadable" if bad else ""))
    print(f"  {len(with_prompt)} carry prompt/workflow data")
    print(f"  {len(with_exif)} carry EXIF · {len(with_id)} carry "
          f"identifying EXIF")
    line = f"  {len(with_gps)} carry GPS coordinates"
    print(fmt(line, C_RED) if with_gps else fmt(line, C_GREEN))
    for rec in with_gps:
        print(fmt(f"      {rec['name']}  {rec['gps'].get('_decimal', '')}", C_RED))
    for rec in bad:
        print(fmt(f"      {rec['name']}: {rec['error']}", C_RED))
    print()


def collect(targets: list[Path]) -> list[Path]:
    """Expand the arguments into a sorted file list.

    A directory is walked recursively: ComfyUI's input dir has a 3d/
    subfolder, and metadata does not stop being interesting one level
    down.
    """
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files += [p for p in target.rglob("*")
                      if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
        elif target.is_file():
            files.append(target)
        else:
            print(f"  no such path: {target}", file=sys.stderr)
    return sorted(set(files))


def parse_args():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path,
                    help="image files or directories "
                         f"(default: {COMFY_DIR / 'input'})")
    ap.add_argument("--full", action="store_true",
                    help=f"print values in full (default: truncate at "
                         f"{TRUNCATE_AT} chars)")
    ap.add_argument("--json", action="store_true",
                    help="emit one JSON array instead of the report")
    ap.add_argument("--no-color", action="store_true",
                    help="disable colour even on a terminal")
    return ap.parse_args()


def main() -> int:
    # The Windows console defaults to cp1252, which cannot encode the box
    # rules or the ★ markers — and XMP/IPTC payloads routinely carry
    # characters no 8-bit codepage has. Without this the tool dies on its
    # first heading, on the platform this project now ships to.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args()
    targets = args.paths or [COMFY_DIR / "input"]
    files = collect(targets)
    if not files:
        print(f"\n  No images found under: "
              f"{', '.join(str(t) for t in targets)}\n", file=sys.stderr)
        return 1

    records = [inspect(p) for p in files]
    if args.json:
        json.dump(records, sys.stdout, indent=2, default=str)
        print()
        return 0

    fmt = Fmt(_supports_colour() and not args.no_color)
    print(f"\n  Scanning {len(files)} image(s) under "
          f"{', '.join(str(t) for t in targets)}")
    for rec in records:
        print_record(rec, fmt, args.full)
    print_summary(records, fmt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
