#!/usr/bin/env python3
"""Build the pose library from the downloaded pose packs.

Operator tool, never bundled. Reads the packs as they were downloaded and
writes the tree ember/web/library.py serves and scripts/r2_upload_poses.py
uploads — the same layout in both places:

    <out>/manifest.json
    <out>/v<N>/thumbs/<id>/<kind>.webp      320 px tall, for the cards
    <out>/v<N>/full/<id>/<kind>.webp        source size, for the field

    build_pose_library.py                   tmp/poses -> the app's own cache
    build_pose_library.py --out tmp/pose-stage --version 2

The default --out is the app's cache folder for the library
(settings.LIBRARY_DIR/poses), so on a dev checkout one run is all it takes
for the picker to work with no bucket at all.

What it does with the packs
---------------------------
One pose is one item, however many renderings it comes in. The OpenPose
pack names them `<stem>.json`, `<stem>_bone_structure.png`,
`<stem>_depth.png` and `<stem>_lineart.png` (with `strucure` and `linart`
misspelt here and there, which are read as meant). The DS27 pack's
`Poses/` folder is skeletons only; its `Preview/` folder is pictures made
*from* the poses and is not read.

- **category** is the pack's folder, merged and renamed (CATEGORIES).
- **aspect** is the folder the pose sat in (512512, 512768, 768512), or the
  picture's own shape where there is no such folder.
- **tags** are read from the OpenPose keypoints where there is a JSON. Arms
  up and legs apart are reliable. Horizontal and back view depend on the
  camera, so they are filters, never categories.
- **name** is the category and a number counted within it, so two packs'
  "lying 10" cannot both be called that.
- Two poses with the same keypoints, to the pixel, are one pose.

Pictures are re-encoded, which also drops any metadata: the skeletons
losslessly (flat colour on black, where lossy WebP smears the limbs), depth
and line art at QUALITY.

A pack added later needs its own reader beside read_openpose_pack() and
read_ds27_pack(): each one yields the same Pose shape.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ember import settings  # noqa: E402

DEFAULT_SRC = ROOT / "tmp" / "poses"
OPENPOSE_PACK = "openposeNSFWPosePackage_final"
DS27_PACK = Path("unusualPosesByDS27_v10") / "Unusual_Poses_v1_DS27" / "Poses"

CATEGORIES = {
    "lying": "Lying", "NSFW_lying": "Lying", "NSFW_all_fours": "All fours",
    "NSFW_Kneeling": "Kneeling", "NSFW_sitting": "Sitting",
    "NSFW_split_leg": "Split legs", "NSFW_Squatting": "Squatting",
    "NSFW_standing": "Standing", "NSFW_suspended": "Suspended",
    "metalstocks": "Restraints",
}
ASPECTS = {"512512": "square", "512768": "portrait", "768512": "landscape"}
KINDS = ("skeleton", "depth", "lineart")
_SUFFIX = re.compile(
    r"^(?P<stem>.+?)(?:_(?P<kind>bone_struc?t?ure|depth|lin(?:e)?art))?"
    r"\.(?P<ext>png|json)$", re.IGNORECASE)

THUMB_HEIGHT = 320
QUALITY = 88
THUMB_QUALITY = 78


@dataclass
class Pose:
    id: str
    category: str
    source: str
    order: tuple
    aspect: str | None = None
    label: str | None = None             # a name the pack gave it
    keypoints: Path | None = None
    files: dict = field(default_factory=dict)   # kind -> source picture
    tags: list = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _number(stem: str) -> tuple[int, str]:
    """`NSFW_sitting026b` -> (26, "b"). The letter marks another body on
    the same skeleton: the pack draws `026a` and `026b` depth maps and line
    art for the one `026` skeleton and keypoints."""
    found = re.search(r"(\d+)([a-z]?)$", stem, re.IGNORECASE)
    return (int(found.group(1)), found.group(2).lower()) if found else (0, "")


def read_openpose_pack(src: Path):
    pack = src / OPENPOSE_PACK
    if not pack.is_dir():
        return
    for folder in sorted(p for p in pack.iterdir() if p.is_dir()):
        category = CATEGORIES.get(folder.name, folder.name.replace("_", " "))
        # (number, letter) -> {"aspect", "json", kind: path}
        found: dict[tuple, dict] = {}
        for shape in sorted(p for p in folder.iterdir() if p.is_dir()):
            for path in sorted(shape.iterdir()):
                match = _SUFFIX.match(path.name)
                if not match:
                    continue
                row = found.setdefault(_number(match["stem"]),
                                       {"aspect": ASPECTS.get(shape.name)})
                if match["ext"].lower() == "json":
                    row["json"] = path
                    continue
                kind = (match["kind"] or "").lower()
                kind = ("skeleton" if kind.startswith("bone") else
                        "lineart" if kind.startswith("lin") else
                        "depth" if kind == "depth" else None)
                if kind:
                    row[kind] = path

        lettered = {number for number, letter in found if letter}
        for (number, letter), row in sorted(found.items()):
            base = found.get((number, ""), {})
            files = {k: row[k] for k in KINDS if k in row}
            if letter:
                # The skeleton and keypoints are the base pose's.
                if "skeleton" not in files and "skeleton" in base:
                    files["skeleton"] = base["skeleton"]
            elif number in lettered and not ("depth" in files
                                             or "lineart" in files):
                # A bare skeleton whose bodies all carry letters: the
                # lettered items already include it.
                continue
            pose_id = "%s-%03d%s" % (_slug(folder.name), number, letter)
            yield Pose(id=pose_id, category=category, source="openpose-pack",
                       order=(folder.name.lower(), number, letter),
                       aspect=row.get("aspect") or base.get("aspect"),
                       keypoints=row.get("json") or base.get("json"),
                       files=files)


def read_ds27_pack(src: Path):
    folder = src / DS27_PACK
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.png")):
        label = re.sub(r"(\d+)$", r" \1", path.stem.replace("_", " ")).strip()
        yield Pose(id="unusual-" + _slug(path.stem), category="Unusual",
                   source="ds27", order=("unusual", path.stem.lower()),
                   label=label[:1].upper() + label[1:].lower(),
                   files={"skeleton": path})


READERS = (read_openpose_pack, read_ds27_pack)


# ── keypoint tags (COCO-18: x, y, confidence per point) ──────────────────────
def _tags(path: Path) -> tuple[list, str | None]:
    """The pose's tags, and a fingerprint of its keypoints for dedup."""
    try:
        people = json.loads(path.read_text(encoding="utf-8")).get("people") or []
    except (OSError, ValueError):
        return [], None
    if not people:
        return [], None
    flat = people[0].get("pose_keypoints_2d") or []
    if len(flat) < 54:
        return [], None
    point = [(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]) for i in range(18)]
    nose, neck, r_sh, r_wr, l_sh, l_wr = (point[i] for i in (0, 1, 2, 4, 5, 7))
    r_hip, r_knee, r_ank, l_hip, l_knee, l_ank = (point[i] for i in
                                                  (8, 9, 10, 11, 12, 13))
    r_eye, l_eye = point[14], point[15]
    tags = []

    hips = [h for h in (r_hip, l_hip) if h[2]]
    if neck[2] and hips:
        mid_x = sum(h[0] for h in hips) / len(hips)
        mid_y = sum(h[1] for h in hips) / len(hips)
        angle = abs(math.degrees(math.atan2(mid_x - neck[0], mid_y - neck[1])))
        if angle > 120:
            tags.append("upside down")
        elif angle > 35:
            tags.append("horizontal")
    if r_sh[2] and l_sh[2]:
        face = nose[2] and (r_eye[2] or l_eye[2])
        if abs(r_sh[0] - l_sh[0]) < 25:
            tags.append("side view")
        elif r_sh[0] > l_sh[0]:
            tags.append("looking back" if face else "back view")
    if not nose[2]:
        tags.append("no face")
    if nose[2] and any(w[2] and w[1] < nose[1] for w in (r_wr, l_wr)):
        tags.append("arms up")
    if r_hip[2] and l_hip[2]:
        width = math.dist(r_hip[:2], l_hip[:2])
        ends = ((r_ank, l_ank) if r_ank[2] and l_ank[2] else
                (r_knee, l_knee) if r_knee[2] and l_knee[2] else None)
        if width and ends and math.dist(ends[0][:2], ends[1][:2]) > 3.2 * width:
            tags.append("legs apart")

    rounded = [(round(x), round(y)) if c else None for x, y, c in point]
    fingerprint = hashlib.sha1(json.dumps(rounded).encode()).hexdigest()
    return tags, fingerprint


# ── pictures ──────────────────────────────────────────────────────────────────
def _encode(source: Path, dest: Path, kind: str, thumb: bool) -> int:
    with Image.open(source) as image:
        image = image.convert("RGB")
        if thumb and image.height > THUMB_HEIGHT:
            width = round(image.width * THUMB_HEIGHT / image.height)
            image = image.resize((width, THUMB_HEIGHT), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if kind == "skeleton" and not thumb:
            image.save(dest, "WEBP", lossless=True, method=6)
        else:
            image.save(dest, "WEBP", method=6,
                       quality=THUMB_QUALITY if thumb else QUALITY)
    return dest.stat().st_size


def _aspect(path: Path) -> str:
    with Image.open(path) as image:
        ratio = image.width / image.height
    return "landscape" if ratio > 1.15 else "portrait" if ratio < 0.87 else "square"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out", type=Path,
                        default=settings.LIBRARY_DIR / "poses")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--force", action="store_true",
                        help="re-encode pictures that are already there")
    args = parser.parse_args()

    poses = [pose for reader in READERS for pose in reader(args.src)
             if pose.files]
    if not poses:
        raise SystemExit("No poses found under %s" % args.src)
    poses.sort(key=lambda p: (p.category, p.order))

    # A duplicate is the same keypoints, in the same category, with the same
    # letter. The restraint poses reuse sitting skeletons on purpose, and
    # the lettered bodies share one skeleton on purpose; neither is one.
    seen: dict[tuple, str] = {}
    kept, dropped = [], []
    for pose in poses:
        if pose.keypoints:
            pose.tags, fingerprint = _tags(pose.keypoints)
            if fingerprint:
                key = (pose.category, fingerprint, pose.order[-1])
                if key in seen:
                    dropped.append("%s (same as %s)" % (pose.id, seen[key]))
                    continue
                seen[key] = pose.id
        kept.append(pose)

    counters: dict[str, int] = {}
    items, written = [], {"thumbs": 0, "full": 0}
    base = args.out / ("v%d" % args.version)
    for number, pose in enumerate(kept, 1):
        counters[pose.category] = counters.get(pose.category, 0) + 1
        name = pose.label or "%s %d" % (pose.category, counters[pose.category])
        kinds = [k for k in KINDS if k in pose.files]
        for kind in kinds:
            for size in ("thumbs", "full"):
                dest = base / size / pose.id / ("%s.webp" % kind)
                if dest.is_file() and not args.force:
                    written[size] += dest.stat().st_size
                    continue
                written[size] += _encode(pose.files[kind], dest, kind,
                                         thumb=size == "thumbs")
        items.append({
            "id": pose.id,
            "name": name,
            "category": pose.category,
            "aspect": pose.aspect or _aspect(pose.files[kinds[0]]),
            "source": pose.source,
            "tags": pose.tags,
            "variants": kinds,
        })
        if number % 50 == 0:
            print("  %d / %d" % (number, len(kept)))

    manifest = {"version": args.version, "items": items}
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    by_category: dict[str, int] = {}
    for item in items:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    print("%d poses -> %s" % (len(items), args.out))
    print("  " + ", ".join("%s %d" % row for row in
                           sorted(by_category.items(), key=lambda r: -r[1])))
    print("  thumbs %.1f MB, full %.1f MB" % (written["thumbs"] / 1e6,
                                             written["full"] / 1e6))
    if dropped:
        print("  %d duplicate(s) dropped: %s" % (len(dropped),
                                                 ", ".join(dropped[:8])
                                                 + (" …" if len(dropped) > 8
                                                    else "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
