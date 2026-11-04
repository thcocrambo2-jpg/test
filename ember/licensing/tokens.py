"""Reading a settings token on the pod — the Prompt Library's Random generate.

The browser is where tokens are made and read (webui/src/lib/settingsToken.ts);
Random generate is the one place the pod needs to read one itself, because it
turns library entries into queued jobs with no browser in the loop. This is the
same port scripts/prompt_library/store.py makes for the admin page: an ember2
token is deflate under base32768, an ember1 token deflate under base64url.

Stdlib-only, like the rest of this package.
"""

import base64
import json
import re
import zlib

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
