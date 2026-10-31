"""The prompt library's storage: one index and its pictures, all in R2.

Shared by the two local tools beside it — admin.py, which writes, and
showcase.py, which only reads. Operator code, never bundled: build.sh
lists what Nuitka carries file by file and `scripts/` is not among it.

**R2 is the only copy.** There is no database row behind an entry and no
local cache of the pictures. Everything lives in the private builds
bucket, under one prefix:

    library/prompts/index.json            every entry, in display order
    library/prompts/<id>/thumb.webp       the grid picture
    library/prompts/<id>/full.webp        the lightbox picture

The licence server reads the same index.json to answer pods
(POST /v1/prompt-library), so the admin page, the showcase and every
customer's Prompt Library tab are three views of one file.

An entry, as index.json holds it:

    {"id": "3f9c0a1b2d4e", "tab": "krea_v2_t2i", "title": "",
     "token": "ember2.…;", "token_sha": "<sha256 of token>",
     "enabled": false, "public": false, "created_at": "2026-10-31T…Z",
     "width": 1344, "height": 1728,
     "media": [{"kind": "image", "role": "thumb", "key": "…", …},
               {"kind": "image", "role": "full",  "key": "…", …}]}

`enabled` puts it in the customers' tab, `public` on the showcase; the two
are independent. `media` is a list, not two fixed fields, so a video and
its poster can join later without touching the entries already there.

**Nothing but pixels leaves this machine.** Every picture is decoded and
re-encoded from its pixels into a fresh image, so no EXIF, XMP, ICC
profile or PNG text chunk survives — and ComfyUI writes the whole prompt
and workflow into those chunks. The object names are random ids, not the
source file's name, which may itself carry a prompt or a seed. The
objects go up with a Content-Type and a Cache-Control and nothing else.

Credentials come from license-validator/.env, the same file `make` reads:
the WRITE pair for the admin page, the read pair for the showcase.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from r2_presign import presign                     # noqa: E402

PREFIX = "library/prompts"
INDEX_KEY = f"{PREFIX}/index.json"
INDEX_VERSION = 1

# Signed picture links last six hours, and are signed as if at the top of
# the current hour. So a link is the same string for the whole hour — the
# browser can cache the picture — and always has at least five hours left.
# The licence server signs the same way (src/promptLibrary.js).
URL_TTL_SECONDS = 6 * 3600

FULL_MAX_EDGE = 2048
THUMB_MAX_EDGE = 640
FULL_QUALITY = 90
THUMB_QUALITY = 82
CACHE_CONTROL = "private, max-age=86400"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

RETRIES = 4


class StoreError(Exception):
    """Something the page should say in a sentence."""


class Conflict(StoreError):
    """index.json changed since it was read — another admin page saved."""


# ── Credentials ────────────────────────────────────────────────────────

def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def load_credentials(write: bool) -> str:
    """Put one R2 key pair where r2_presign reads it, and name the bucket.

    `write` picks the R2_WRITE_* pair, which the admin page needs and the
    showcase must never hold. Variables already in the environment win
    over the file, so a one-off run can point somewhere else.
    """
    env = _read_env_file(ROOT / "license-validator" / ".env")

    def pick(name: str) -> str:
        return os.environ.get(name) or env.get(name, "")

    pair = ("R2_WRITE_ACCESS_KEY_ID", "R2_WRITE_SECRET_ACCESS_KEY") if write \
        else ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    values = {
        "R2_ACCOUNT_ID": pick("R2_ACCOUNT_ID"),
        "R2_BUILDS_BUCKET": pick("R2_BUILDS_BUCKET") or "krea2-builds",
        "R2_ACCESS_KEY_ID": pick(pair[0]),
        "R2_SECRET_ACCESS_KEY": pick(pair[1]),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SystemExit("prompt library: unset in license-validator/.env: "
                         + ", ".join(pair if "R2_ACCESS_KEY_ID" in missing
                                     else missing))
    os.environ.update(values)
    return values["R2_BUILDS_BUCKET"]


# ── R2 ─────────────────────────────────────────────────────────────────

def signed_url(key: str) -> str:
    """A GET link for one object, stable for the hour (see URL_TTL_SECONDS)."""
    hour = time.strftime("%Y%m%dT%H0000Z", time.gmtime())
    return presign("GET", key, URL_TTL_SECONDS, now=hour)


def _call(method: str, key: str, body: bytes | None = None,
          headers: dict[str, str] | None = None
          ) -> tuple[int, dict[str, str], bytes]:
    """One request to the bucket, retried on the failures worth retrying.

    Answers (status, headers, body) for every HTTP status, so the callers
    decide what a 404 or a 412 means; only the network failing every
    attempt raises.
    """
    for attempt in range(1, RETRIES + 1):
        request = urllib.request.Request(
            presign(method, key, 900), data=body, method=method,
            headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            data = exc.read() if exc.fp else b""
            if not (status in (408, 429) or status >= 500) \
                    or attempt == RETRIES:
                return status, dict(exc.headers or {}), data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == RETRIES:
                raise StoreError(f"Could not reach R2 ({exc}).") from exc
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _refused(what: str, status: int, data: bytes) -> StoreError:
    detail = data.decode("utf-8", "replace")[:300]
    return StoreError(f"R2 refused {what}: HTTP {status} {detail}".strip())


def put_object(key: str, data: bytes, content_type: str) -> None:
    status, _, body = _call("PUT", key, data, {
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "Cache-Control": CACHE_CONTROL,
    })
    if not 200 <= status < 300:
        raise _refused(f"the upload of {key}", status, body)


def delete_object(key: str) -> None:
    status, _, body = _call("DELETE", key)
    if status not in (200, 204, 404):
        raise _refused(f"deleting {key}", status, body)


# ── The index ──────────────────────────────────────────────────────────

def load_index() -> tuple[list[dict], str | None]:
    """Every entry, in order, and the ETag to save against.

    No index yet is an empty library with no ETag, not an error: it is
    what the bucket looks like before the first upload.
    """
    status, headers, body = _call("GET", INDEX_KEY)
    if status == 404:
        return [], None
    if status != 200:
        raise _refused("reading the index", status, body)
    try:
        document = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise StoreError(f"index.json does not parse ({exc}).") from exc
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise StoreError("index.json has no entries list.")
    etag = next((value for name, value in headers.items()
                 if name.lower() == "etag"), None)
    return [entry for entry in entries if isinstance(entry, dict)], etag


def save_index(entries: list[dict], etag: str | None) -> str | None:
    """Write the index, refusing if someone else wrote it since `etag`.

    A conditional PUT: If-Match the ETag that was read, or If-None-Match *
    for the very first save. Two admin pages open at once would otherwise
    each save their own copy and the second would erase the first's work.
    """
    document = {
        "version": INDEX_VERSION,
        "updated_at": now_iso(),
        "entries": entries,
    }
    data = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(data)),
        "Cache-Control": "no-store",
    }
    if etag:
        headers["If-Match"] = etag
    else:
        headers["If-None-Match"] = "*"
    status, answer, body = _call("PUT", INDEX_KEY, data, headers)
    if status == 412:
        raise Conflict("The library was changed from somewhere else since "
                       "this page loaded it. It has been reloaded; make "
                       "the change again.")
    if not 200 <= status < 300:
        raise _refused("saving the index", status, body)
    return next((value for name, value in answer.items()
                 if name.lower() == "etag"), None)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id() -> str:
    return secrets.token_hex(6)


def media(entry: dict, role: str) -> dict | None:
    return next((item for item in entry.get("media") or []
                 if isinstance(item, dict) and item.get("role") == role), None)


# ── Settings tokens ────────────────────────────────────────────────────
#
# A port of the reading half of webui/src/lib/settingsToken.ts, only so an
# upload can be checked and filed under the right tab. The browser stays
# the authority on the format; this refuses anything it does not follow.

_B32K_PAIRS = (
    "ҠҿԀԟڀڿݠޟ߀ߟကဟႠႿᄀᅟᆀᆟᇠሿበቿዠዿጠጿᎠᏟᐠᙟᚠᛟកសᠠᡟᣀᣟᦀᦟ᧠᧿ᨠᨿᯀᯟᰀᰟᴀᴟ⇠⇿⋀⋟⍀⏟␀␟─❟➀➿⠀⥿⦠⦿⨠⩟⪀⪿⫠⭟ⰀⰟⲀⳟⴀⴟⵀⵟ⺠⻟㇀㇟㐀䶟䷀龿ꀀꑿ꒠꒿ꔀꗿꙀꙟꚠꛟ꜀ꝟꞀꞟꡀꡟ",
    "ƀƟɀʟ",
)
_B32K: dict[str, tuple[int, int]] = {}
for _r, _pairs in enumerate(_B32K_PAIRS):
    _bits = 15 - 8 * _r
    _z = 0
    for _i in range(0, len(_pairs), 2):
        for _code in range(ord(_pairs[_i]), ord(_pairs[_i + 1]) + 1):
            _B32K[chr(_code)] = (_bits, _z)
            _z += 1


def _from_base32768(text: str) -> bytes:
    out = bytearray()
    byte = 0
    count = 0
    for index, char in enumerate(text):
        if char not in _B32K:
            raise ValueError("not base32768")
        bits, z = _B32K[char]
        if bits != 15 and index != len(text) - 1:
            raise ValueError("short character before the end")
        for shift in range(bits - 1, -1, -1):
            byte = (byte << 1) | ((z >> shift) & 1)
            count += 1
            if count == 8:
                out.append(byte)
                byte = count = 0
    if byte != (1 << count) - 1:
        raise ValueError("padding mismatch")
    return bytes(out)


_TOKEN_START = re.compile(r"ember([12])\.([A-Za-z0-9_-]+)\.")
_DATA_CLASS = {
    "1": r"[A-Za-z0-9_-]",
    "2": "[ƀ-῿⁰-⿿぀-ꣿ]",
}


def read_token(text: str) -> tuple[str, dict]:
    """The token inside `text`, cleaned up, and the settings it carries.

    Same two readings the app makes: up to the closing `;` with any
    whitespace a chat app wrapped into it dropped, or — with no `;` — up to
    the first character its alphabet does not use. Raises ValueError with
    a sentence when neither reading decodes to a tab and its values.
    """
    found = _TOKEN_START.search(text or "")
    if not found:
        raise ValueError("No settings token in it (one starts with ember2.).")
    version, tab_id = found.group(1), found.group(2)
    start = found.end()
    cls = _DATA_CLASS[version]
    readings = []
    semi = text.find(";", start)
    if semi >= 0:
        closed = re.sub(r"\s+", "", text[start:semi])
        if re.fullmatch(f"{cls}{{16,}}", closed):
            readings.append(closed)
    open_match = re.match(f"{cls}{{16,}}", text[start:])
    if open_match:
        readings.append(open_match.group(0))
    for data in readings:
        try:
            raw = (base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
                   if version == "1" else _from_base32768(data))
            decoded = json.loads(zlib.decompress(raw, -15).decode("utf-8"))
        except (ValueError, zlib.error, UnicodeDecodeError):
            continue
        if isinstance(decoded, dict) and isinstance(decoded.get("tab"), str) \
                and isinstance(decoded.get("values"), dict):
            return f"ember{version}.{tab_id}.{data};", decoded
    raise ValueError("The token is cut off or damaged.")


def token_sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Pictures ───────────────────────────────────────────────────────────

def _clean(raw: bytes):
    """The picture as bare pixels: upright, no metadata of any kind.

    EXIF orientation is applied *before* everything is dropped, or a phone
    photo would come out on its side. Then the pixels are copied into a
    brand-new image, which has an empty `info` — no EXIF, XMP, ICC, PNG
    text chunks or comment can ride along into the encoder.
    """
    from PIL import Image, ImageOps

    try:
        source = Image.open(io.BytesIO(raw))
        source.load()
    except Exception as exc:
        raise StoreError(f"Not a picture this can read ({exc}).") from exc
    source = ImageOps.exif_transpose(source)
    has_alpha = source.mode in ("RGBA", "LA", "PA") or (
        source.mode == "P" and "transparency" in source.info)
    if has_alpha:
        source = source.convert("RGBA")
        if source.getchannel("A").getextrema() == (255, 255):
            source = source.convert("RGB")
    else:
        source = source.convert("RGB")
    return Image.frombytes(source.mode, source.size, source.tobytes())


def _webp(picture, max_edge: int, quality: int) -> tuple[bytes, int, int]:
    from PIL import Image

    width, height = picture.size
    scale = min(1.0, max_edge / max(width, height))
    if scale < 1.0:
        picture = picture.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, "WEBP", quality=quality, method=6, exif=b"", xmp=b"")
    data = out.getvalue()
    check = Image.open(io.BytesIO(data))
    leaked = [name for name in ("exif", "xmp", "icc_profile", "XML:com.adobe.xmp")
              if check.info.get(name)]
    if leaked:
        raise StoreError(f"Metadata survived the re-encode: {', '.join(leaked)}.")
    return data, picture.size[0], picture.size[1]


def pictures(raw: bytes) -> dict[str, tuple[bytes, int, int]]:
    """`full` and `thumb` WebPs of one source picture, metadata-free."""
    picture = _clean(raw)
    return {
        "full": _webp(picture, FULL_MAX_EDGE, FULL_QUALITY),
        "thumb": _webp(picture, THUMB_MAX_EDGE, THUMB_QUALITY),
    }


# ── Tab names ──────────────────────────────────────────────────────────

def tab_labels() -> dict[str, str]:
    """Feature key -> the tab's title, from the app's own registry."""
    try:
        from ember import features
        return {str(feature.key): feature.label for feature in features.FEATURES}
    except Exception:
        return {}
