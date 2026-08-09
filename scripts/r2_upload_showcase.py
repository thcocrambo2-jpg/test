#!/usr/bin/env python3
"""Scaffold and upload the pricing page's showcase images to R2.

Operator tool, not part of the shipped app, and never bundled: build.sh
lists the data files Nuitka carries one by one and `scripts/` is not among
them, so nothing here — nor anything it writes under BASE_DIR — can reach a
binary. The staging tree lives at BASE_DIR/input, which is inside the
gitignored `tmp` on a dev checkout.

    r2_upload_showcase.py --scaffold      # build the tree + dummy images
    r2_upload_showcase.py --list          # every path the page will request
    r2_upload_showcase.py --dry-run       # what would upload, and from where
    r2_upload_showcase.py                 # upload everything present
    r2_upload_showcase.py --only krea_t2i # one feature at a time

The workflow it exists for: scaffold once, replace the dummies with real
pictures at your own pace, re-run to upload. Uploading is keyed on the
file, not on a record of what ran, so a half-finished folder is a normal
state — put in the four images you have and run it again next week.

**Keep the placeholder's name; ignore its extension.** The catalogue names
`before.webp` and that is the key the page requests, but the app writes
PNG, so dropping `before.png` beside it is the expected case: the stem is
what gets matched, and the file is converted to the catalogue's format on
the way up. Nothing has to be converted by hand, and the object in the
bucket is always named what showcase.json says it is. A file whose
extension already matches is uploaded byte-for-byte instead — re-encoding
a picture someone already optimised only costs quality.

One consequence worth knowing: a converted file is re-encoded from pixels,
so EXIF and text chunks do not survive it. A pass-through upload keeps
everything it had, and this warns when that happens — a source photograph
can carry a camera serial and a GPS fix onto a public bucket.

WHAT GETS UPLOADED IS NOT DECIDED HERE. showcase.json names every file the
page will ever ask for, and this script asks showcase.py to parse it rather
than reading the JSON itself. That matters more than it looks: the expected
paths are not written down anywhere: they are *derived*, by rules spread
across _leaf(), _items() and _media() — "a compare block with no `before`
means before.webp", "a collage with `placeholders: 12` means 01.webp
through 12.webp", "a `dir` of '.' means the feature's own folder". A second
implementation of those rules here would agree today and drift silently
the first time one of them changed, and the failure mode is a picture
uploaded to a path nothing requests, which looks exactly like success.

So the private names are imported on purpose. If showcase.py stops
exporting them this script must be updated with it — which is the coupling
working, not the coupling breaking.

Credentials come from the environment, alongside the ones build.sh already
uses (see r2_presign.py, whose SigV4 this borrows rather than repeating):

    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
    R2_SHOWCASE_BUCKET      the *public* bucket serving KREA2_SHOWCASE_URL

Note the write credential and the public URL are two different things. The
bucket is public to readers; these keys are how you put objects in it, and
they are not what the app or the customer's browser uses — neither ever
authenticates to it at all.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The app's modules live one level up; this script sits outside the package
# so build.sh / Nuitka never sweep it into the shipped binary.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import showcase                                              # noqa: E402
from config import BASE_DIR, SHOWCASE_BASE_URL, log          # noqa: E402
from r2_presign import presign                               # noqa: E402

# Where the pictures are staged before upload. Deliberately BASE_DIR/input
# and not COMFY_DIR/input — the latter is ComfyUI's own upload folder and
# is full of whatever the Edit tab was last given.
INPUT_DIR = BASE_DIR / "input"

BUCKET_ENV = "R2_SHOWCASE_BUCKET"
UPLOAD_RETRIES = 3
PRESIGN_TTL = 900          # seconds; one URL per object, used immediately

CONTENT_TYPES = {
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".avif": "image/avif",
    ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
}

# Extensions accepted in the staging tree for a file the catalogue names as
# something else, best first. The app's own output is PNG, so dropping
# `before.png` where `before.webp` is expected is the *normal* case, not an
# edge one — it is converted on the way up rather than skipped.
SOURCE_SUFFIXES = (".webp", ".png", ".jpg", ".jpeg", ".avif", ".tif",
                   ".tiff", ".bmp")

# Pillow format names for the extensions a catalogue entry might name.
TARGET_FORMATS = {".webp": "WEBP", ".png": "PNG", ".jpg": "JPEG",
                  ".jpeg": "JPEG", ".avif": "AVIF", ".gif": "GIF"}

DEFAULT_QUALITY = 82

# Cache hard. These are content-addressed by hand — a changed picture gets a
# new name or a purge — and the pricing page loads forty of them at once.
CACHE_CONTROL = "public, max-age=31536000, immutable"

# Dummy tile geometry. Big enough that the layout is exercised honestly at
# full width, small enough that scaffolding the whole set is instant.
DUMMY_WIDTH = 1280
DUMMY_MAX_HEIGHT = 1600


# ── The paths the page will ask for ───────────────────────────────────────────
def _walk(block) -> list:
    """Every Media in a block tree, containers included.

    `split` and `pair` carry other blocks rather than files, so the tree is
    two levels deep in places and a flat scan over `items` would miss the
    compares nested inside them.
    """
    found = list(block.items)
    if block.source is not None:
        found.append(block.source)
    for inner in block.blocks:
        found += _walk(inner)
    return found


def expected_media() -> list:
    """Every picture showcase.json names, as showcase.py resolves them.

    Built from the raw catalogue rather than from showcase.showcase(), which
    filters by the licence in force: that is right for a page selling
    upgrades and wrong for an upload, where a tab this pod cannot run still
    needs its screenshots in the bucket.
    """
    raw = showcase._load()
    features = raw.get("features")
    if not isinstance(features, dict):
        raise SystemExit(
            f"{showcase.CATALOGUE_PATH} has no 'features' map — nothing to "
            f"upload. Restore it from git.")

    media, seen = [], set()
    for key, entry in features.items():
        if not isinstance(entry, dict):
            continue
        blocks = entry.get("blocks")
        for raw_block in (blocks if isinstance(blocks, list) else []):
            block = showcase._block(key, raw_block)
            if block is None:
                continue
            for item in _walk(block):
                # A file named twice in one catalogue is one object, and
                # uploading it twice would be the only visible symptom.
                if item.path in seen:
                    continue
                seen.add(item.path)
                media.append(item)
    return media


# ── Dummy images ──────────────────────────────────────────────────────────────
def _dimensions(ratio: str | None) -> tuple[int, int]:
    """A block's declared aspect ratio → pixel size for its placeholder.

    Honouring the ratio is the point of generating these at all: a set of
    identical squares would let a layout bug through that a real 21/9 hero
    beside a 1/1 compare would have caught immediately.
    """
    num, den = 4, 3
    if ratio:
        try:
            parsed = tuple(int(part) for part in ratio.split("/"))
            if len(parsed) == 2 and all(part > 0 for part in parsed):
                num, den = parsed
        except ValueError:
            pass
    width = DUMMY_WIDTH
    height = round(width * den / num)
    # A tall shape (2/3, 3/4) overshoots the height budget. Clamping the
    # height alone would silently square the tile up, which is precisely
    # the distortion this function exists to avoid — so the width comes
    # down with it and the ratio survives.
    if height > DUMMY_MAX_HEIGHT:
        height = DUMMY_MAX_HEIGHT
        width = round(height * num / den)
    return max(width, 200), max(height, 200)


def _draw_dummy(path: Path, media, width: int, height: int) -> None:
    """A labelled placeholder tile, written to `path`.

    It carries its own bucket path in large type. When one of these makes it
    to production by mistake — the whole risk of shipping placeholders — the
    page says exactly which file was never replaced, instead of showing a
    grey rectangle that could be anything.
    """
    from PIL import Image, ImageDraw, ImageFont

    # The hue showcase.py picked for this path's tile, so the scaffold looks
    # like the placeholder it stands in for rather than an unrelated colour.
    image = Image.new("RGB", (width, height))
    top, bottom = _hue_rgb(media.hue, 0.30), _hue_rgb(media.hue, 0.14)
    draw = ImageDraw.Draw(image)
    for y in range(height):                       # cheap vertical gradient
        blend = y / max(height - 1, 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(a + (b - a) * blend) for a, b in zip(top, bottom)),
        )

    try:
        big = ImageFont.truetype("arial.ttf", 46)
        small = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        big = small = ImageFont.load_default()

    lines = [(media.path, big, (255, 255, 255)),
             (f"{width}x{height}  ·  {media.ratio or 'auto'}", small,
              (200, 205, 225)),
             ("PLACEHOLDER — replace before publishing", small,
              (255, 190, 120))]
    total = sum(_line_height(draw, text, font) + 18 for text, font, _ in lines)
    y = (height - total) // 2
    for text, font, colour in lines:
        span = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (span[2] - span[0])) // 2, y), text,
                  font=font, fill=colour)
        y += _line_height(draw, text, font) + 18

    draw.rectangle([(6, 6), (width - 7, height - 7)],
                   outline=(255, 255, 255), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=80, method=4)


def _line_height(draw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def _hue_rgb(hue: int, value: float) -> tuple[int, int, int]:
    """showcase.py's tile hue (0-360) at a fixed saturation → RGB."""
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360, 0.55, value + 0.18)
    return int(r * 255), int(g * 255), int(b * 255)


def scaffold(media_list: list, force: bool) -> tuple[int, int]:
    """Create the folder tree and a dummy for every path not already there.

    Existing files are left alone unless --force. That is what makes this
    safe to re-run once you have started dropping real pictures in: the
    command that creates the scaffold must never be the command that
    destroys the work.
    """
    made = kept = 0
    for media in media_list:
        dest = INPUT_DIR / media.path
        if dest.exists() and not force:
            kept += 1
            continue
        width, height = _dimensions(media.ratio)
        _draw_dummy(dest, media, width, height)
        made += 1
    return made, kept


# ── Staged files ──────────────────────────────────────────────────────────────
def staged_file(media) -> Path | None:
    """The file standing in for `media.path`, whatever extension it wears.

    The catalogue names `before.webp` and that is the key the page will
    request, but what actually lands in the staging folder is whatever came
    out of the app — PNG, most often. Matching on the stem and converting
    later is what lets "keep the placeholder's name, ignore its extension"
    be true, which is the only rule worth asking anyone to follow.

    Preference order is SOURCE_SUFFIXES, exact match first, so a folder
    holding both before.webp and before.png resolves the same way twice
    rather than on filesystem order.
    """
    exact = INPUT_DIR / media.path
    if exact.is_file():
        return exact
    for suffix in SOURCE_SUFFIXES:
        candidate = exact.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def body_for(local: Path, key: str, quality: int) -> tuple[bytes, str]:
    """The bytes to PUT for `key`, converting the format if it differs.

    Returns (payload, content_type). A file whose extension already matches
    is passed through untouched — re-encoding an image someone has already
    optimised only loses quality and changes nothing else.

    Conversion re-encodes from pixels, so nothing the source carried in
    EXIF or a text chunk survives it: no camera, no timestamps, no GPS on a
    public marketing page. A pass-through upload keeps whatever it had,
    which is why the caller warns about it.
    """
    target = "." + key.rsplit(".", 1)[-1].lower()
    content_type = CONTENT_TYPES.get(target, "application/octet-stream")
    if local.suffix.lower() == target:
        return local.read_bytes(), content_type

    fmt = TARGET_FORMATS.get(target)
    if fmt is None:
        # A video, or something else Pillow has no business rewriting.
        # Uploading the bytes as-is under the catalogue's key is still the
        # right move; only the conversion is refused.
        log.warning("%s: cannot convert %s to %s — uploading as-is",
                    key, local.suffix, target)
        return local.read_bytes(), content_type

    import io
    from PIL import Image

    with Image.open(local) as image:
        if fmt == "JPEG" and image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGB")       # JPEG has no alpha channel
        elif image.mode == "P":
            image = image.convert("RGBA")
        options = {"quality": quality}
        if fmt == "WEBP":
            options["method"] = 6              # slowest encode, smallest file
        elif fmt == "PNG":
            options = {"optimize": True}       # quality means nothing to PNG
        buffer = io.BytesIO()
        image.save(buffer, fmt, **options)
    return buffer.getvalue(), content_type


def _carries_metadata(local: Path) -> bool:
    """Whether a pass-through upload would publish EXIF or text chunks.

    Only asked about files going up untouched. Worth one line of warning:
    these end up on a public bucket, and a source photograph's EXIF can
    carry a camera serial and a GPS fix (see inspect_image_metadata.py).
    """
    try:
        from PIL import Image
        with Image.open(local) as image:
            return bool(image.getexif()) or bool(getattr(image, "text", None))
    except Exception:
        return False


# ── Upload ────────────────────────────────────────────────────────────────────
def _bucket() -> str:
    name = os.environ.get(BUCKET_ENV, "").strip()
    if not name:
        raise SystemExit(
            f"{BUCKET_ENV} is not set. It is the *public* R2 bucket serving "
            f"KREA2_SHOWCASE_URL"
            + (f" ({SHOWCASE_BASE_URL})" if SHOWCASE_BASE_URL else "")
            + ".\n  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and "
              "R2_SECRET_ACCESS_KEY are needed too — the same Object Read & "
              "Write token build.sh uses.")
    return name


def upload_one(local: Path, key: str, bucket: str,
               quality: int = DEFAULT_QUALITY) -> int:
    """PUT one object, retrying the failures worth retrying.

    A fresh URL is signed per attempt rather than per file: the signature
    carries an expiry, and a retry after a long backoff on a slow link is
    exactly when a reused one would have gone stale.

    Returns the number of bytes actually sent, which is not the file's size
    when the format was converted on the way up.
    """
    body, content_type = body_for(local, key, quality)
    for attempt in range(1, UPLOAD_RETRIES + 1):
        request = urllib.request.Request(
            presign("PUT", key, PRESIGN_TTL, bucket=bucket),
            data=body, method="PUT",
            headers={"Content-Type": content_type,
                     "Content-Length": str(len(body)),
                     "Cache-Control": CACHE_CONTROL},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if 200 <= response.status < 300:
                    return len(body)
                raise urllib.error.HTTPError(
                    key, response.status, response.reason, {}, None)
        except urllib.error.HTTPError as exc:
            # 4xx that is not 408/429 is a rejection: wrong key, wrong
            # bucket, no permission. Retrying spends 30s to be told the
            # same thing three more times.
            retriable = exc.code in (408, 429) or exc.code >= 500
            if not retriable or attempt == UPLOAD_RETRIES:
                detail = exc.read().decode("utf-8", "replace")[:400] \
                    if exc.fp else ""
                raise RuntimeError(
                    f"HTTP {exc.code} {exc.reason}"
                    + (f" — {detail}" if detail else "")) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == UPLOAD_RETRIES:
                raise RuntimeError(str(exc)) from exc
        wait = 3 * 2 ** (attempt - 1)
        log.warning("upload of %s failed (%d/%d) — retrying in %ds",
                    key, attempt, UPLOAD_RETRIES, wait)
        __import__("time").sleep(wait)


# ── Reporting ─────────────────────────────────────────────────────────────────
def human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:,.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GB"


def parse_args():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scaffold", action="store_true",
                    help=f"create {INPUT_DIR} and a dummy image for every "
                         f"path showcase.json names, then exit")
    ap.add_argument("--force", action="store_true",
                    help="with --scaffold: overwrite files that already "
                         "exist (destroys real pictures — deliberate)")
    ap.add_argument("--list", action="store_true",
                    help="print every expected path and exit. No credentials")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would upload; touch nothing")
    ap.add_argument("--only", metavar="FEATURE", action="append",
                    help="limit to one showcase.json feature key; repeatable")
    ap.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                    metavar="N",
                    help=f"encode quality when a file has to be converted "
                         f"(default: {DEFAULT_QUALITY})")
    return ap.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:                                  # cp1252 consoles choke on ·/★
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args()
    media_list = expected_media()
    if args.only:
        wanted = set(args.only)
        media_list = [m for m in media_list
                      if m.path.split("/")[0] in wanted]
        if not media_list:
            raise SystemExit(f"--only: nothing matches {sorted(wanted)}")

    if args.list:
        for media in media_list:
            print(f"  {media.path:<44} {media.ratio or 'auto'}")
        print(f"\n  {len(media_list)} file(s) named by "
              f"{showcase.CATALOGUE_PATH.name}\n")
        return 0

    if args.scaffold:
        made, kept = scaffold(media_list, args.force)
        print(f"\n  Staging tree: {INPUT_DIR}")
        print(f"  {made} dummy image(s) written · {kept} existing file(s) "
              f"left alone")
        if kept and not args.force:
            print("  (--force overwrites those too — it will destroy real "
                  "pictures)")
        print("\n  Replace the dummies with real images, keeping the names, "
              "then re-run\n  without --scaffold to upload.\n")
        return 0

    resolved = [(m, staged_file(m)) for m in media_list]
    ready = [(m, p) for m, p in resolved if p is not None]
    absent = [m for m, p in resolved if p is None]
    converting = [(m, p) for m, p in ready
                  if p.suffix.lower() != "." + m.path.rsplit(".", 1)[-1].lower()]

    print(f"\n  {len(media_list)} file(s) named by "
          f"{showcase.CATALOGUE_PATH.name}")
    print(f"  {len(ready)} staged in {INPUT_DIR}"
          + (f" · {len(absent)} not staged" if absent else ""))
    if converting:
        print(f"  {len(converting)} will be converted to the format the "
              f"catalogue names")
    if absent:
        print("\n  Not staged — these will stay as placeholder tiles on the "
              "page:")
        for media in absent[:20]:
            print(f"    ✗ {media.path}")
        if len(absent) > 20:
            print(f"    … and {len(absent) - 20} more")
        print("  Run --scaffold to generate dummies for them.")
    if not ready:
        print("\n  Nothing to upload.\n")
        return 1

    total = sum(p.stat().st_size for _, p in ready)
    if args.dry_run:
        print(f"\n  Would upload {len(ready)} object(s), {human(total)} "
              f"on disk:")
        for media, path in ready:
            source = "" if path.name == Path(media.path).name \
                else f"   ← {path.name}"
            print(f"    ↑ {media.path:<44} "
                  f"{human(path.stat().st_size):>10}{source}")
        print("\n  --dry-run: nothing uploaded.\n")
        return 0

    bucket = _bucket()
    print(f"  → bucket {bucket}\n")
    failed, sent = [], 0
    for n, (media, path) in enumerate(ready, 1):
        note = "" if path.name == Path(media.path).name \
            else f" (from {path.name})"
        log.info("[%d/%d] ↑ %s%s", n, len(ready), media.path, note)
        if not note and _carries_metadata(path):
            log.warning("%s is uploaded byte-for-byte and carries EXIF or "
                        "text chunks — it will be public. Check it with "
                        "inspect_image_metadata.py", media.path)
        try:
            sent += upload_one(path, media.path, bucket, args.quality)
        except Exception as exc:
            log.error("Upload failed for %s: %s", media.path, exc)
            failed.append(media.path)

    ok = len(ready) - len(failed)
    print(f"\n  Uploaded {ok}/{len(ready)} · {human(sent)} sent · "
          f"{len(failed)} failed")
    for path in failed:
        print(f"    ✗ {path}")
    if ok and SHOWCASE_BASE_URL:
        print(f"  Live at {SHOWCASE_BASE_URL}/{ready[0][0].path}  (and "
              f"{ok - 1} more)" if ok > 1 else
              f"  Live at {SHOWCASE_BASE_URL}/{ready[0][0].path}")
    elif ok:
        print("  KREA2_SHOWCASE_URL is unset here, so the app would still "
              "render placeholders — set it on the pod.")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
