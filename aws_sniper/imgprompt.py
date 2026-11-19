"""Image to prompt for the sniper page's second tab: a vision model on
Bedrock writes an image-generation prompt for a photo.

Bedrock's OpenAI-compatible endpoint (bedrock-mantle), signed with the
account's AWS keys; the regular Converse route is refused for this account.
Same request as tmp/gpt-img2prompt/run.py.

Everything lives in img2prompt/ next to this file, so copying the
aws_sniper folder carries it along: the base prompts
(base_prompt_gpt_v1.md, _v2.md), the uploaded pictures in images/ (named
by content hash, so the same picture is stored once, and any can be
picked again), and history.json with every generation (which picture,
tokens, cost, the prompt it wrote), so totals survive restarts. Prices are
Bedrock's standard on-demand rates in us-east-1 from the AWS Pricing API
(2026-11-19).
"""

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

HERE = Path(__file__).resolve().parent
DATA = HERE / "img2prompt"
LOG_FILE = DATA / "history.json"
IMAGES = DATA / "images"
REGION = "us-east-1"
MAX_TOKENS = 3000
MAX_IMAGE = 15 * 1024 * 1024
STARTED = time.time()            # "this session" = since the server started

# $ per million tokens, input / output.
MODELS = {
    "qwen": {"id": "qwen.qwen3-vl-235b-a22b-instruct", "label": "Qwen3-VL 235B",
             "in": 0.53, "out": 2.66},
    "kimi": {"id": "moonshotai.kimi-k2.5", "label": "Kimi K2.5",
             "in": 0.60, "out": 3.00},
}
DEFAULT_MODEL = "qwen"
PROMPTS = {
    "v1": "Detailed rules; copies the photo's body exactly",
    "v2": "Flowing photographer-style prose",
}
DEFAULT_PROMPT = "v1"
FORMATS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
MIME = {ext: mime for mime, ext in FORMATS.items()}
ID_RE = re.compile(r"^[0-9a-f]{16}$")

ASK = "Write the prompt for this photograph."
CHANGES = ("\n\nThe user's changes, in their own words:\n%s\n\n"
           "These changes have the highest priority and override both the photo and the "
           "rule to copy the body exactly. Where a change conflicts with the photo (body "
           "shape or size, outfit, setting, lighting, pose, hair, anything), follow the "
           "change and use its wording on the scales the rules give (e.g. 'small bust' "
           "means bust size small, with shape and garment fit to match). Describe "
           "the new version in the same detail and style the rules ask for, as if it were in "
           "the photo; drop every detail of the replaced part. Keep everything the changes "
           "don't mention exactly as in the photo. Never mention that anything was changed.")


def _prompt_file(version: str) -> Path:
    path = DATA / ("base_prompt_gpt_%s.md" % version)
    if not path.is_file():
        raise ValueError("Base prompt %s not found: %s is missing." % (version, path))
    return path


def cost(model: str, tokens_in: int, tokens_out: int) -> float:
    m = MODELS[model]
    return ((tokens_in or 0) * m["in"] + (tokens_out or 0) * m["out"]) / 1e6


class Log:
    """img2prompt.json: {"records": [...], "images": {id: {...}}}."""

    def __init__(self):
        self.lock = threading.Lock()
        try:
            saved = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        self.records = saved.get("records", [])
        self.images = saved.get("images", {})

    def _save(self) -> None:
        DATA.mkdir(exist_ok=True)
        tmp = LOG_FILE.with_suffix(".json.part")
        tmp.write_text(json.dumps({"records": self.records, "images": self.images}, indent=1),
                       encoding="utf-8")
        tmp.replace(LOG_FILE)

    def add(self, rec: dict) -> None:
        with self.lock:
            self.records.append(rec)
            self._save()

    def add_image(self, iid: str, entry: dict) -> dict:
        with self.lock:
            old = self.images.get(iid)
            if old:                       # the same picture again: just move it to the front
                old["used"] = time.time()
            else:
                self.images[iid] = entry
            self._save()
            return dict(self.images[iid], id=iid)

    def find(self, t):
        with self.lock:
            return next((dict(r) for r in self.records if str(r["time"]) == str(t)), None)

    def mark_queued(self, t, sent: dict) -> None:
        with self.lock:
            for r in self.records:
                if r["time"] == t:
                    r.setdefault("queued", []).append(sent)
            self._save()

    def touch(self, iid: str) -> None:
        with self.lock:
            if iid in self.images:
                self.images[iid]["used"] = time.time()
                self._save()

    def image(self, iid: str):
        with self.lock:
            e = self.images.get(iid)
            return dict(e, id=iid) if e else None

    def image_list(self) -> list:
        with self.lock:
            counts = {}
            for r in self.records:
                counts[r.get("image")] = counts.get(r.get("image"), 0) + 1
            return sorted((dict(e, id=i, prompts=counts.get(i, 0)) for i, e in self.images.items()),
                          key=lambda e: -e.get("used", e["added"]))

    def spent(self, account: str = None, since: float = 0) -> float:
        with self.lock:
            return sum(r["cost"] for r in self.records
                       if r["time"] >= since and (account is None or r["account"] == account))

    def totals(self) -> dict:
        with self.lock:
            session = [r for r in self.records if r["time"] >= STARTED]
            return {"session": sum(r["cost"] for r in session), "session_count": len(session),
                    "total": sum(r["cost"] for r in self.records), "count": len(self.records),
                    "since": self.records[0]["time"] if self.records else None,
                    "session_started": STARTED}

    def recent(self, n: int = 300) -> list:
        with self.lock:
            return list(reversed(self.records[-n:]))


LOG = Log()


def config() -> dict:
    try:
        neg = {"text": negative(), "file": str(NEGATIVE_FILE)}
    except ValueError as exc:
        neg = {"text": "", "file": str(NEGATIVE_FILE), "error": str(exc)}
    return {"models": MODELS, "default_model": DEFAULT_MODEL, "prompts": PROMPTS,
            "default_prompt": DEFAULT_PROMPT, "region": REGION, "totals": LOG.totals(),
            "records": LOG.recent(), "images": LOG.image_list(),
            "negative": neg}


def upload(body: dict) -> dict:
    mime = str(body.get("type") or "")
    if mime not in FORMATS:
        raise ValueError("Use a JPEG, PNG, WebP or GIF image (got %s)." % (mime or "unknown type"))
    data = base64.b64decode(str(body.get("data") or ""), validate=True)
    if not data:
        raise ValueError("Empty image.")
    if len(data) > MAX_IMAGE:
        raise ValueError("Image over %d MB." % (MAX_IMAGE // 2 ** 20))
    iid = hashlib.sha256(data).hexdigest()[:16]
    ext = FORMATS[mime]
    IMAGES.mkdir(parents=True, exist_ok=True)
    path = IMAGES / ("%s.%s" % (iid, ext))
    if not path.exists():
        path.write_bytes(data)
    now = time.time()
    return LOG.add_image(iid, {"name": str(body.get("name") or "image")[:200], "ext": ext,
                               "size": len(data), "added": now, "used": now})


def image_file(iid: str):
    """(bytes, mime) of a stored picture, or None."""
    e = LOG.image(iid) if ID_RE.match(iid or "") else None
    path = e and IMAGES / ("%s.%s" % (iid, e["ext"]))
    if not path or not path.is_file():
        return None
    return path.read_bytes(), MIME[e["ext"]]


def generate(session, account: str, body: dict) -> dict:
    """One stored picture, one model, one base prompt -> one prompt.
    session: boto3 session for the account in REGION."""
    model = str(body.get("model") or DEFAULT_MODEL)
    version = str(body.get("prompt") or DEFAULT_PROMPT)
    if model not in MODELS:
        raise ValueError("Unknown model %s." % model)
    if version not in PROMPTS:
        raise ValueError("Unknown base prompt %s." % version)
    iid = str(body.get("image") or "")
    found = image_file(iid)
    if not found:
        raise ValueError("That picture isn't stored here any more: upload it again.")
    data, mime = found
    LOG.touch(iid)
    changes = "\n".join(l.strip() for l in str(body.get("changes") or "").splitlines()
                        if l.strip() and not l.strip().startswith("#"))

    ask = ASK + (CHANGES % changes if changes else "")
    req = {"model": MODELS[model]["id"], "max_tokens": MAX_TOKENS, "messages": [
        {"role": "system", "content": _prompt_file(version).read_text(encoding="utf-8")},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())}},
            {"type": "text", "text": ask},
        ]},
    ]}
    creds = session.get_credentials()
    if creds is None:
        raise ValueError("No AWS keys for account %s." % account)
    t = time.time()
    try:
        r = _chat(creds.get_frozen_credentials(), req)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Bedrock said HTTP %d: %s" % (
            exc.code, exc.read()[:400].decode(errors="replace"))) from None
    except (OSError, ValueError) as exc:     # no network, timeout, a reply that isn't JSON
        raise RuntimeError("Bedrock didn't answer: %s" % exc) from None
    choice = r["choices"][0]
    usage = r.get("usage") or {}
    tin, tout = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
    rec = {"time": time.time(), "account": account, "image": iid, "name": LOG.image(iid)["name"],
           "model": model, "prompt": version, "changes": changes,
           "tokens_in": tin, "tokens_out": tout, "cost": cost(model, tin, tout),
           "seconds": round(time.time() - t, 1), "stop": choice.get("finish_reason"),
           "text": (choice["message"].get("content") or "").strip()}
    LOG.add(rec)
    return rec


# ── sending a prompt to the app's queue ──────────────────────────────

APP_TAB = "krea_v2_t2i"
APP_PRESET = "Natural Raw 2"        # every queued job: this preset, batch 1
NEGATIVE_FILE = DATA / "negative.json"
APP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")    # the tunnel turns away python-urllib


def negative() -> str:
    """negative.json's "negative"; '' (keep the preset's own) while it's empty or missing."""
    try:
        text = NEGATIVE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        value = json.loads(text).get("negative")
    except (ValueError, AttributeError):
        raise ValueError("%s isn't valid JSON: fix it, or nothing can be queued." % NEGATIVE_FILE) from None
    return value.strip() if isinstance(value, str) else ""


def _app(origin: str, key: str, method: str, path: str, body=None):
    headers = {"User-Agent": APP_UA, "Accept": "application/json"}
    if key:
        headers["x-ember-key"] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(origin + "/api/v1" + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        if exc.code == 401:
            raise RuntimeError("The app asks for its access key (401): the instance's link has no working #k= key.") from None
        if exc.code == 403:
            raise RuntimeError("This instance's licence has no Krea 2 V2 tab (403).") from None
        raise RuntimeError("The app said HTTP %d: %s" % (exc.code, detail)) from None
    except (OSError, ValueError) as exc:
        raise RuntimeError("Can't reach the app at %s (%s)." % (origin, getattr(exc, "reason", exc))) from None


def app_options(origin: str, key: str) -> dict:
    """Whether the instance's Krea 2 V2 tab offers the preset."""
    p = _app(origin, key, "GET", "/presets/" + APP_TAB)
    return {"preset": APP_PRESET, "has_preset": APP_PRESET in [r["name"] for r in p.get("presets") or []]}


def send_to_queue(origin: str, key: str, instance: str, body: dict) -> dict:
    """The record's prompt -> a Krea 2 V2 job (Natural Raw 2, batch 1), moved to the top."""
    rec = LOG.find(body.get("time"))
    if not rec:
        raise ValueError("That prompt isn't in the history any more.")
    if not rec["text"] or rec["text"].startswith("SKIP:"):
        raise ValueError("The model skipped this picture: there is no prompt to queue.")
    preset = APP_PRESET
    applied = _app(origin, key, "POST", "/schema/%s/apply" % APP_TAB, {"preset": preset})
    values = applied.get("values") or {}
    if not values:
        raise ValueError("Preset %s isn't on offer on this instance." % preset)
    values.update(prompt=rec["text"], batch_count=1)      # everything else: the preset's own
    neg = negative()
    if neg:
        values["negative"] = neg
    job = _app(origin, key, "POST", "/tabs/%s/generate" % APP_TAB, {"values": values})["jobId"]
    moved = _app(origin, key, "POST", "/jobs/%s/move" % job, {"to": "top"})
    sent = {"time": time.time(), "instance": instance, "job": job, "preset": preset,
            "aspect": values.get("aspect"), "negative": "negative.json" if neg else "preset"}
    LOG.mark_queued(rec["time"], sent)
    return {"sent": sent, "message": (moved or {}).get("message") or ""}


def _chat(cred, body: dict) -> dict:
    url = "https://bedrock-mantle.%s.api.aws/v1/chat/completions" % REGION
    data = json.dumps(body)
    req = AWSRequest(method="POST", url=url, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(cred, "bedrock-mantle", REGION).add_auth(req)
    with urllib.request.urlopen(urllib.request.Request(url, data=data.encode(), headers=dict(req.headers),
                                                       method="POST"), timeout=300) as resp:
        return json.load(resp)
