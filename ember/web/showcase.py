"""The "what this app does" section of the pricing page — copy and pictures.

The prose lives in `assets/showcase/showcase.json`, which is bundled — a
few kilobytes of text, and the thing that decides whether the section
renders at all. The pictures do not: they are served from the public R2
bucket at config.SHOWCASE_BASE_URL, in the same folder layout the JSON
names, and this module only ever builds URLs for them.

Nothing here downloads an image. The URLs go into the markup and the
customer's browser fetches them lazily, so a page of forty screenshots
costs this app nothing and costs the binary nothing — which is the whole
reason the pictures moved out of the build.

**Every shipped feature is described, granted or not.** This section sits
under the plan cards, and the reader is deciding whether to upgrade — so a
tab they do not have is exactly the one worth showing them. Granted tabs
are flagged as theirs and the rest carry an upgrade prompt; nothing is
dimmed, because a washed-out screenshot is a poor argument for buying the
thing in it. `SHOW_LOCKED` below turns that off, leaving a page that
describes only what this licence runs.

The one thing never advertised is a feature switched off in the registry
(`Feature.enabled`), which is a tab this *build* cannot construct — it is
not for sale at any tier, so listing it would be selling vapour.

The bucket mirrors the JSON's own shape — feature key, then the block's
`dir`, then the filename the block names:

    <bucket>/krea_t2i/hero/hero.webp
    <bucket>/krea_t2i/gallery/01.webp … 12.webp
    <bucket>/krea_edit/compare-a/before.webp, after.webp

**Every file is named in the JSON, extension included.** A bucket has no
directory listing to walk, so "render whatever is in this folder" is not
something this module can offer any more — uploading a thirteenth image
means adding a line to showcase.json, which is one more reason the copy
stayed in the build where it can be reviewed in a diff.

Nothing checks whether a picture is actually there. The app cannot know
without making the request itself, and it will not: that is the browser's
job and it happens on someone else's machine. So every picture is rendered
over a placeholder tile naming the path it expects — if the upload has not
happened yet, or the name is a typo, what shows is the tile with the path
on it rather than a broken-image icon. A block with no items at all
renders the same tiles from its `placeholders` count, which is what an
unpopulated bucket looks like end to end.

Everything is best-effort. A missing catalogue, a malformed one, an
unknown block type: each is logged once and comes back as a smaller
section or as None, because a decorative panel must never be able to take
the pricing page down with it.
"""

import json
import zlib
from dataclasses import dataclass
from urllib.parse import quote

import features
from config import ASSETS_DIR, SHOWCASE_BASE_URL, log

CATALOGUE_PATH = ASSETS_DIR / "showcase" / "showcase.json"

# Whether features this licence does *not* grant are described too, flagged
# and carrying an upgrade prompt. On: the page is a sales argument first,
# and the tab a customer cannot open is the one that makes it. Turn it off
# for a page that only describes what this pod runs. A constant rather than
# a setting because it is an editorial decision, not a per-pod one.
SHOW_LOCKED = True

# Rendered as a looping <video> rather than an <img>. Matched on the name
# the JSON gives, since there is no file here to inspect.
VIDEO_EXTENSIONS = (".mp4", ".webm")

# What a filename may contain. The value is pasted into a URL and into the
# markup, so it is validated rather than escaped: a picture is `01.webp` or
# `before.png`, and anything with a slash, a space or a colon in it is
# either a typo or an attempt to point the page somewhere else. Rejected
# names fall back to the placeholder tile, which shows what was asked for.
_NAME_OK = ("abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

# Hues for the placeholder tiles, stepped so that neighbours in a collage do
# not collide. Picked from the path rather than the position, so a tile keeps
# its colour when one is added before it.
_HUES = (248, 285, 200, 330, 160, 22, 262, 300, 186, 45)

# Shapes for a collage tile, which is the one block that declares no aspect
# ratio of its own. A local file could be measured; a URL cannot be, short
# of asking the bucket, so the shape is either stated in the JSON or taken
# from here. Cycling through them is also what keeps the masonry uneven
# rather than a dozen identical 4:3 tiles.
_PLACEHOLDER_SHAPES = ("3/4", "1/1", "4/3", "16/9", "2/3", "1/1", "3/2", "3/4")

# The picture-carrying block shapes this module understands; "split" and
# "pair" are handled before this is consulted because they carry other
# blocks rather than files. Anything else in the JSON is dropped with a
# warning rather than raised on: the copy is data, and a typo in it should
# cost one block and not the page.
_LEAVES = ("hero", "shot", "compare", "fan", "row", "strip", "collage")


@dataclass(frozen=True)
class Media:
    """One picture in the bucket — or one nothing has been uploaded for.

    `url` is None in the second case and the tile renders as a placeholder
    naming `path`. It being set says only that the JSON asked for a file and
    a bucket is configured, *not* that the file is there: nothing in this
    process ever asks. The renderer draws every picture over its placeholder
    for exactly that reason, so an upload that has not happened yet still
    shows the path rather than a broken image.
    """

    path: str                      # the file's path within the bucket
    url: str | None = None         # None → nothing to load, tile only
    label: str | None = None       # corner pill: "Before", "Source", …
    caption: str | None = None     # revealed on hover
    ratio: str | None = None       # "4/3", always set for a remote picture
    is_video: bool = False
    accent: bool = False           # pill in the accent colour, not neutral
    hue: int = 248                 # placeholder tint

    @property
    def missing(self) -> bool:
        return self.url is None


@dataclass(frozen=True)
class Block:
    """One layout unit inside a section.

    Leaf blocks carry `items` (and `source`, for a fan). Containers carry
    `blocks`, which is what lets a `split` put prose beside a compare and a
    `pair` sit two compares side by side.
    """

    type: str
    items: tuple[Media, ...] = ()
    source: Media | None = None
    blocks: tuple["Block", ...] = ()
    title: str | None = None
    body: str | None = None
    note: str | None = None
    caption: str | None = None
    reverse: bool = False
    flat: bool = False


@dataclass(frozen=True)
class Section:
    """One feature, in prose and pictures."""

    key: str
    label: str                     # the tab's own title, from features
    headline: str
    body: tuple[str, ...] = ()
    highlights: tuple[str, ...] = ()
    blocks: tuple[Block, ...] = ()
    locked: bool = False


@dataclass(frozen=True)
class Stat:
    value: str
    label: str


@dataclass(frozen=True)
class Showcase:
    """The whole section: the intro, the features, the closing panel."""

    eyebrow: str = "What you are running"
    title: str = ""
    title_accent: str = ""
    body: tuple[str, ...] = ()
    stats: tuple[Stat, ...] = ()
    sections: tuple[Section, ...] = ()
    cta_title: str | None = None
    cta_body: str | None = None


# --------------------------------------------------------------- helpers
def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(value) -> tuple[str, ...]:
    """A list-of-strings field, blank entries dropped."""
    if not isinstance(value, list):
        return ()
    return tuple(_text(item) for item in value if _text(item))


def _ratio(value) -> str | None:
    """`"4/3"` → `"4/3"`, anything else → None.

    The value is written straight into an inline `aspect-ratio`, so it is
    validated as two small integers rather than escaped: the vocabulary is
    a handful of shapes, and refusing everything else is what stops a
    hand-edited JSON from putting arbitrary text into a style attribute.
    """
    if not isinstance(value, str):
        return None
    parts = value.replace(" ", "").split("/")
    if len(parts) != 2:
        return None
    if not all(part.isdigit() and 0 < len(part) <= 2 and int(part) for part in parts):
        return None
    return f"{parts[0]}/{parts[1]}"


def _hue(path: str) -> int:
    """A stable tint for a placeholder, keyed on the path it is waiting for.

    crc32 rather than hash(): the built-in is salted per process, so the
    same empty folder would change colour on every restart.
    """
    return _HUES[zlib.crc32(path.encode("utf-8")) % len(_HUES)]


def _folder(feature: str, raw_dir) -> str:
    """The bucket path a block's files sit under, e.g. `krea_edit/fan-a`.

    A `dir` of "." (or none at all) means the feature's own folder, which is
    how the Gallery's single screenshot avoids a folder of one.
    """
    name = _text(raw_dir)
    if not name or name == ".":
        return feature
    return f"{feature}/{name}"


def _clean_name(value: str) -> str | None:
    """A filename fit for a URL, or None. See _NAME_OK for the reasoning."""
    name = _text(value)
    if not name or name in (".", "..") or name.startswith("."):
        return None
    if any(char not in _NAME_OK for char in name):
        return None
    return name


# ---------------------------------------------------------------- parsing
def _media(folder: str, spec, ratio: str | None) -> Media:
    """One entry in the JSON → one picture, with the URL it will load from.

    `spec` is either a bare filename or an object carrying its label and
    caption alongside one, because most slots need nothing but a name.

    Two things produce a placeholder rather than a picture: no bucket is
    configured, and a filename that could not be used in a URL. A file that
    simply has not been uploaded yet is *not* one of them — this cannot know
    that, and the renderer covers it by drawing every picture over its tile.
    """
    if isinstance(spec, str):
        spec = {"file": spec}
    if not isinstance(spec, dict):
        spec = {}

    raw_name = _text(spec.get("file"))
    name = _clean_name(raw_name)
    if raw_name and name is None:
        log.warning("showcase: %r is not a usable filename — showing a "
                    "placeholder for it instead", raw_name)

    path = f"{folder}/{name or 'image.webp'}"
    common = {
        "path": path,
        "label": _text(spec.get("label")) or None,
        "caption": _text(spec.get("caption")) or None,
        "ratio": _ratio(spec.get("ratio")) or ratio,
        "accent": spec.get("accent") is True,
        "hue": _hue(path),
        "is_video": path.lower().endswith(VIDEO_EXTENSIONS),
    }
    if name is None or not SHOWCASE_BASE_URL:
        return Media(**common)
    return Media(url=f"{SHOWCASE_BASE_URL}/{quote(path, safe='/')}", **common)


def _items(folder: str, raw: dict, ratio: str | None) -> tuple[Media, ...]:
    """A block's pictures, in the order the JSON lists them.

    A bucket has no listing, so this is exactly what was declared and
    nothing more. A block that declares nothing falls back to its
    `placeholders` count, which is what an empty folder used to render as
    and is still the right thing to show before anything is uploaded.
    """
    declared = raw.get("items")
    declared = declared if isinstance(declared, list) else []

    def shape(index: int) -> str:
        return ratio or _PLACEHOLDER_SHAPES[index % len(_PLACEHOLDER_SHAPES)]

    if declared:
        return tuple(_media(folder, spec, shape(index))
                     for index, spec in enumerate(declared))

    count = raw.get("placeholders")
    count = count if isinstance(count, int) and 0 < count <= 24 else 0
    return tuple(
        Media(path=f"{folder}/{index + 1:02d}.webp", ratio=shape(index),
              hue=_hue(f"{folder}/{index + 1:02d}"))
        for index in range(count)
    )


def _leaf(feature: str, raw: dict, kind: str) -> Block | None:
    """One picture-carrying block."""
    folder = _folder(feature, raw.get("dir"))
    ratio = _ratio(raw.get("ratio"))
    caption = _text(raw.get("caption")) or None
    note = _text(raw.get("note")) or None

    # The defaults carry an extension for the same reason every name in the
    # JSON does: it is going into a URL, and there is no folder to search
    # for the rest of it.
    if kind in ("hero", "shot"):
        image = _media(folder, _text(raw.get("image")) or "hero.webp",
                       ratio or ("21/9" if kind == "hero" else "16/9"))
        return Block(type=kind, items=(image,), caption=caption,
                     title=_text(raw.get("title")) or None, note=note)

    if kind == "compare":
        shape = ratio or "4/3"
        before = _media(folder, {
            "file": _text(raw.get("before")) or "before.webp",
            "label": _text(raw.get("before_label")) or "Before",
        }, shape)
        after = _media(folder, {
            "file": _text(raw.get("after")) or "after.webp",
            "label": _text(raw.get("after_label")) or "After",
            "accent": True,
        }, shape)
        return Block(type=kind, items=(before, after), caption=caption)

    if kind == "fan":
        source = _media(folder, {
            "file": _text(raw.get("source")) or "source.webp",
            "label": _text(raw.get("source_label")) or "Source",
            "accent": True,
        }, _ratio(raw.get("source_ratio")) or ratio or "3/4")
        items = _items(folder, raw, ratio or "3/4")
        if not items:
            return None
        return Block(type=kind, source=source, items=items, caption=caption)

    if kind in ("row", "strip"):
        items = _items(folder, raw, ratio or ("1/1" if kind == "row" else "16/9"))
        return Block(type=kind, items=items, caption=caption, note=note) \
            if items else None

    # collage — the one block that does not force a shape on its pictures,
    # so an item's own `ratio` (or the cycled fallback) decides each tile.
    items = _items(folder, raw, ratio)
    if not items:
        return None
    return Block(type=kind, items=items, caption=caption,
                 flat=raw.get("flat") is True)


def _block(feature: str, raw) -> Block | None:
    """One block of any kind, containers included."""
    if not isinstance(raw, dict):
        return None
    kind = _text(raw.get("type"))

    if kind == "split":
        inner = _block(feature, raw.get("block"))
        if inner is None:
            return None
        return Block(type=kind, blocks=(inner,),
                     title=_text(raw.get("title")) or None,
                     body=_text(raw.get("body")) or None,
                     reverse=raw.get("reverse") is True)

    if kind == "pair":
        raw_blocks = raw.get("blocks")
        inner = tuple(
            block for block in
            (_block(feature, item) for item in
             (raw_blocks if isinstance(raw_blocks, list) else []))
            if block is not None
        )
        return Block(type=kind, blocks=inner) if inner else None

    if kind in _LEAVES:
        return _leaf(feature, raw, kind)

    log.warning("showcase: %s has a block of unknown type %r — dropping it",
                feature, kind)
    return None


def _section(key: str, raw: dict, locked: bool) -> Section | None:
    """One feature's entry, or None if it says nothing worth rendering."""
    headline = _text(raw.get("headline"))
    body = _texts(raw.get("body"))
    if not headline and not body:
        return None

    raw_blocks = raw.get("blocks")
    blocks = tuple(
        block for block in
        (_block(key, item) for item in
         (raw_blocks if isinstance(raw_blocks, list) else []))
        if block is not None
    )
    return Section(
        key=key,
        label=features.label_for(key),
        headline=headline,
        body=body,
        highlights=_texts(raw.get("highlights")),
        blocks=blocks,
        locked=locked,
    )


def _load() -> dict:
    """showcase.json, or {} with one warning. Never raises."""
    try:
        return json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning(
            "No %s — the pricing page will show the plans without the "
            "feature showcase. In a frozen build this means build.sh did "
            "not bundle it (see --include-data-files).", CATALOGUE_PATH,
        )
    except Exception as exc:
        log.error("%s is present but unusable (%s) — skipping the feature "
                  "showcase.", CATALOGUE_PATH, exc)
    return {}


_cached: Showcase | None = None
_loaded = False


def showcase() -> Showcase | None:
    """The section, built once, or None if there is nothing to render.

    Built lazily and cached: it walks the asset folders, which is cheap but
    pointless to repeat, and it must not run at import time — the sections
    are filtered by features.enabled(), which is only resolved after the
    licence check in app.py.

    Nothing that happens in here is allowed to reach the caller. ui.py
    builds this into its Blocks tree at import, so an exception would not
    cost the pricing page a decorative panel — it would stop the app from
    starting at all, over a folder walk.
    """
    global _cached, _loaded
    if _loaded:
        return _cached

    _loaded = True
    try:
        _cached = _build()
    except Exception as exc:
        log.error("The feature showcase could not be built (%s) — the "
                  "pricing page will show the plans alone.", exc)
        _cached = None
    return _cached


def _build() -> Showcase | None:
    raw = _load()
    if not isinstance(raw, dict) or not raw:
        return None

    entries = raw.get("features")
    entries = entries if isinstance(entries, dict) else {}

    sections = []
    for feature in features.FEATURES:
        if not feature.enabled:
            # Not shipped in this build, so no plan can grant it and no
            # upgrade would produce it. Never advertised, whatever
            # SHOW_LOCKED says.
            continue
        granted = features.enabled(feature.key)
        if not granted and not SHOW_LOCKED:
            continue
        entry = entries.get(feature.key)
        if not isinstance(entry, dict):
            # An ordinary state, not an error: a tab can ship before anyone
            # has written its copy, and the page simply does not describe it.
            continue
        section = _section(feature.key, entry, locked=not granted)
        if section is not None:
            sections.append(section)

    if not sections:
        return None

    intro = raw.get("app")
    intro = intro if isinstance(intro, dict) else {}
    stats = tuple(
        Stat(value=_text(item.get("value")), label=_text(item.get("label")))
        for item in (intro.get("stats") or [])
        if isinstance(item, dict) and _text(item.get("value"))
    )
    cta = raw.get("cta")
    cta = cta if isinstance(cta, dict) else {}

    log.info("Feature showcase: %d section(s) — %s", len(sections),
             ", ".join(section.key for section in sections))
    if SHOWCASE_BASE_URL:
        log.info("Showcase images: %s", SHOWCASE_BASE_URL)
    else:
        log.warning(
            "KREA2_SHOWCASE_URL is not set — the pricing page's showcase "
            "will render every picture as a placeholder tile naming the "
            "file it wants. Set it to the public R2 bucket URL.",
        )
    return Showcase(
        eyebrow=_text(intro.get("eyebrow")) or "What you are running",
        title=_text(intro.get("title")),
        title_accent=_text(intro.get("title_accent")),
        body=_texts(intro.get("body")),
        stats=stats,
        sections=tuple(sections),
        cta_title=_text(cta.get("title")) or None,
        cta_body=_text(cta.get("body")) or None,
    )
