"""Ember settings tokens (`ember2.…;`), built in Python.

A port of what the web UI does when the Gallery saves a picture's token
(webui/src/components/SettingsToken recipeToken → lib/settingsToken.ts
tokenData + encodeToken, lib/schema.ts defaultsFor), so the auto downloader
needs no Node. Standard library only: zlib for the raw deflate the browser's
CompressionStream('deflate-raw') does, and a port of the base32768 package.

    token = build_token(schema, applied)

`schema` is the tab's entry from GET /api/v1/schemas; `applied` is the
answer of POST /api/v1/schema/<tab>/apply {"recipe": <file id>}. The token
decodes to the same settings the Gallery's own token does — it is checked
against the web UI's decodeToken in the tests (see README).
"""

import json
import math
import zlib

TOKEN_PREFIX = "ember2"
NONE = "None"            # the empty-slot LoRA value, as the schema sends it

# ── base32768 (npm package base32768 5.0.1, src/index.js) ────────────

_PAIR_STRINGS = [
    "ҠҿԀԟڀڿݠޟ߀ߟကဟ"
    "ႠႿᄀᅟᆀᆟᇠሿበቿዠዿ"
    "ጠጿᎠᏟᐠᙟᚠᛟកសᠠᡟ"
    "ᣀᣟᦀᦟ᧠᧿ᨠᨿᯀᯟᰀᰟ"
    "ᴀᴟ⇠⇿⋀⋟⍀⏟␀␟─❟"
    "➀➿⠀⥿⦠⦿⨠⩟⪀⪿⫠⭟"
    "ⰀⰟⲀⳟⴀⴟⵀⵟ⺠⻟㇀㇟"
    "㐀䶟䷀龿ꀀꑿ꒠꒿ꔀꗿꙀꙟ"
    "ꚠꛟ꜀ꝟꞀꞟꡀꡟ",
    "ƀƟɀʟ",
]
_BITS_PER_CHAR, _BITS_PER_BYTE = 15, 8
_LOOKUP = {}
for _r, _pairs in enumerate(_PAIR_STRINGS):
    _rep = []
    for _i in range(0, len(_pairs), 2):
        _rep.extend(chr(c) for c in range(ord(_pairs[_i]), ord(_pairs[_i + 1]) + 1))
    _LOOKUP[_BITS_PER_CHAR - _BITS_PER_BYTE * _r] = _rep      # 15 and 7 bits


def b32768_encode(data: bytes) -> str:
    out, z, nbits = [], 0, 0
    for byte in data:
        for j in range(_BITS_PER_BYTE - 1, -1, -1):     # most significant bit first
            z = (z << 1) | ((byte >> j) & 1)
            nbits += 1
            if nbits == _BITS_PER_CHAR:
                out.append(_LOOKUP[nbits][z])
                z = nbits = 0
    if nbits:
        while nbits not in _LOOKUP:                     # pad with 1s to 7 or 15 bits
            z = (z << 1) | 1
            nbits += 1
        out.append(_LOOKUP[nbits][z])
    return "".join(out)


# ── JavaScript semantics the port depends on ────────────────────────


def js_number(value) -> str:
    """A number as JavaScript's String() / JSON.stringify writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return "null" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)                                  # shortest round trip, like JS
    if "e" in text:
        mantissa, exp = text.split("e")
        sign = "-" if exp.startswith("-") else "+"
        text = "%se%s%d" % (mantissa, sign, abs(int(exp)))
    return text


def js_string(value) -> str:
    """String(value) for the JSON values a schema or recipe holds."""
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return js_number(value)
    if isinstance(value, list):
        return ",".join("" if v is None else js_string(v) for v in value)
    return "[object Object]"


def js_number_of(value) -> float:
    """Number(value)."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return float(text) if any(c in text for c in ".eE") else int(text, 0)
        except ValueError:
            return math.nan
    return math.nan


def js_truthy(value) -> bool:
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value) if not isinstance(value, (list, dict)) else True


def _coalesce(value, fallback):
    """value ?? fallback."""
    return fallback if value is None else value


def _same(a, b) -> bool:
    """settingsToken.ts same(): numbers within 1e-9, else Object.is —
    so two separately parsed arrays or objects are never the same."""
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if num(a) and num(b):
        return abs(a - b) < 1e-9
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a is b
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return type(a) is type(b) and a == b


def _js_json(value) -> str:
    """JSON.stringify(value): compact, non-ASCII kept, JS number format."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_js_json(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join("%s:%s" % (json.dumps(str(k), ensure_ascii=False), _js_json(v))
                              for k, v in value.items()) + "}"
    raise TypeError("cannot serialise %r" % type(value))


# ── the port: schema.ts defaultsFor, settingsToken.ts tokenData ──────


def defaults_for(schema: dict) -> dict:
    values = {}
    for field in schema.get("fields") or []:
        values[field["name"]] = field.get("default")
    lora = schema.get("lora")
    if lora:
        slots = lora.get("slots") or []
        for i in range(int(lora.get("count") or 0)):
            slot = slots[i] if i < len(slots) and slots[i] else {}
            for part in lora.get("parts") or []:
                values["%s.%d.%s" % (lora["key"], i, part)] = slot.get(part)
    return values


def changed_from_preset(schema: dict, values: dict, preset) -> dict:
    out = {}
    if not preset:
        return out
    lora = schema.get("lora")
    unrecorded = {f["name"] for f in schema.get("fields") or [] if f.get("record") is False}
    for key, was in preset.items():
        if lora and key.startswith(lora["key"] + "."):
            continue
        if key in unrecorded:
            continue
        if key == "seed" and values.get("randomize") is True:
            continue
        if not _same(values.get(key), was):
            out[key] = [was, values.get(key)]
    if not lora:
        return out

    def slot(bag, index):
        prefix = "%s.%d." % (lora["key"], index)
        return {"name": js_string(_coalesce(bag.get(prefix + "name"), NONE)),
                "on": js_truthy(bag.get(prefix + "enabled")),
                "weight": js_number_of(_coalesce(bag.get(prefix + "weight"), 0))}

    def say(row):
        if row["name"] == NONE:
            return "none"
        if row["on"]:
            return "%s @ %s" % (row["name"], js_number(row["weight"]))
        return "%s (off)" % row["name"]

    for index in range(int(lora.get("count") or 0)):
        if "%s.%d.name" % (lora["key"], index) not in preset:
            continue
        was, now = slot(preset, index), slot(values, index)
        if was["name"] == now["name"]:
            if now["name"] == NONE:
                continue
            if was["on"] != now["on"] or (now["on"] and not _same(was["weight"], now["weight"])):
                out["lora:" + now["name"]] = [was["weight"] if was["on"] else "off",
                                              now["weight"] if now["on"] else "off"]
        else:
            out["LoRA %d" % (index + 1)] = [say(was), say(now)]
    return out


def token_data(schema: dict, values: dict, preset=None) -> dict:
    kept = {}
    for field in schema.get("fields") or []:
        if field.get("record") is False:
            continue
        if field.get("type") in ("image", "images"):
            continue
        value = values.get(field["name"])
        if value is None:
            continue
        kept[field["name"]] = value
    data = {"tab": schema["key"], "values": kept}
    lora = schema.get("lora")
    if lora:
        rows = []
        for index in range(int(lora.get("count") or 0)):
            prefix = "%s.%d." % (lora["key"], index)
            name = js_string(_coalesce(values.get(prefix + "name"), NONE))
            if name == NONE or not js_truthy(values.get(prefix + "enabled")):
                continue
            rows.append([name, js_number_of(_coalesce(values.get(prefix + "weight"),
                                                      lora.get("weightDefault")))])
        data["loras"] = rows
    if preset and preset.get("name"):
        data["preset"] = preset["name"]
        changed = changed_from_preset(schema, values, preset.get("values"))
        if changed:
            data["changed"] = changed
    return data


def encode_token(schema: dict, data: dict) -> str:
    packed = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    raw = packed.compress(_js_json(data).encode("utf-8")) + packed.flush()
    return "%s.%s.%s;" % (TOKEN_PREFIX, schema["tabId"], b32768_encode(raw))


def build_token(schema: dict, applied: dict):
    """recipeToken(): the token for one file, or None when it has no recipe.

    `applied` is the apply route's answer: {values, preset, presetValues}."""
    values = applied.get("values") or {}
    if not values:
        return None
    preset = None
    if applied.get("preset") and applied.get("presetValues"):
        preset = {"name": applied["preset"], "values": applied["presetValues"]}
    merged = {**defaults_for(schema), **values}
    return encode_token(schema, token_data(schema, merged, preset))
