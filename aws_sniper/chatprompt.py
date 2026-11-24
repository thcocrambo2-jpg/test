"""Prompt chat for the sniper's Chat page (chat.html): talk to Qwen on Bedrock
to write and edit image-generation prompts, with pictures uploaded or
picked from a library and mentioned by @handle.

Every message goes out on its own: no chat history is sent. To change an
earlier prompt you pick it (Edit on its card); then only that prompt is
sent as context, with the mode's base prompt plus the edit sub-template
(edit.md, or edit_<mode>.md when a mode has its own).

Its own things live in chat2prompt/ next to this file: the base prompts
(every base_prompt_<name>.md is a mode, read fresh on every send), the
edit sub-template, the chat uploads in images/ (named by content hash)
and chats.json with every chat, the @handles and the spend. The library
also shows the Image to prompt and Text to prompt pictures, read from
their stores without writing anything to them.

Built on imgprompt.py (model, prices, image upload and lookup, sending a
prompt to an instance's queue, negative.json) by import only; the two
prompt tabs are not changed. The Bedrock call here is its own, streamed.
"""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

import imgprompt as ip
import txtprompt

DATA = ip.HERE / "chat2prompt"
PREFIX = "base_prompt_"
STORE_FILE = DATA / "chats.json"
NEGATIVE_FILE = DATA / "negative.json"           # every chat mode's negative
MODEL = "qwen"                    # only Qwen for now
MAX_COUNT = 4                     # replies one message can ask for at once (the page's ×N pill)
MAX_TOKENS = 4000                 # a full prompt is ~1,400 tokens; edits rewrite all of it
MAX_TEXT = 8000
MAX_IMAGES = 6
MAX_REQUEST = 3_400_000           # Bedrock refuses requests over about 3.5 MB
DEFAULT_MODE = "scene"
FREE = "free"
FREE_SYSTEM = (
    "You are a helpful assistant for someone who writes prompts for photorealistic image "
    "generation. Answer questions about photography, light, posing, styling and prompt "
    "wording concisely and concretely. Pictures arrive introduced as \"Picture N\", numbered "
    "in the order sent, and the user names them the same way. When you are asked to write an image prompt, put "
    "the complete prompt between <prompt> and </prompt>. Every person in a prompt you write "
    "must be an adult and an invented person.")
FREE_DESC = "No base prompt: ask questions, brainstorm"
HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MENTION_RE = re.compile(r"@([A-Za-z0-9_-]{1,32})")
PROMPT_RE = re.compile(r"<prompt>\s*(.*?)\s*(?:</prompt>|\Z)", re.S)
SOURCES = ("chat", "img", "txt")


def split_reply(text: str):
    """(talk, prompt) of a reply: the first <prompt> block (also when cut
    off before its closing tag) and whatever is around it. prompt is None
    when the reply has no block."""
    m = PROMPT_RE.search(text or "")
    if not m:
        return (text or "").strip(), None
    talk = ((text[:m.start()] + " " + text[m.end():]).strip())
    return talk, m.group(1).strip()


def _slug(name: str) -> str:
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name or "").lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:24].strip("-")
    return base or "img"


class Store:
    """chat2prompt/chats.json: {"chats": {id: chat}, "images": {id: entry},
    "handles": {"<src>:<id>": handle}, "removed": [{time, account, cost}]}.
    Has the dir / add_image / image shape imgprompt.upload and
    imgprompt.image_file expect, so chat uploads reuse them."""

    def __init__(self, path=STORE_FILE, images=DATA / "images"):
        self.lock = threading.Lock()
        self.path, self.dir = path, images
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        self.chats = saved.get("chats", {})
        self.images = saved.get("images", {})
        self.handles = saved.get("handles", {})
        self.removed = saved.get("removed", [])
        self.busy = set()             # chats with a reply being written

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.part")
        tmp.write_text(json.dumps({"chats": self.chats, "images": self.images, "handles": self.handles,
                                   "removed": self.removed}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # imgprompt.upload / image_file use these three
    def add_image(self, iid: str, entry: dict) -> dict:
        with self.lock:
            old = self.images.get(iid)
            if old:
                old["used"] = time.time()
            else:
                self.images[iid] = entry
            self._handle("chat", iid, self.images[iid]["name"])
            self._save()
            return dict(self.images[iid], id=iid, src="chat", handle=self.handles["chat:" + iid])

    def image(self, iid: str):
        with self.lock:
            e = self.images.get(iid)
            return dict(e, id=iid) if e else None

    def _handle(self, src: str, iid: str, name: str) -> str:
        """The picture's handle, made from its name the first time (lock held)."""
        key = "%s:%s" % (src, iid)
        if key not in self.handles:
            taken, base = set(self.handles.values()), _slug(name)
            h, n = base, 2
            while h in taken:
                h, n = "%s-%d" % (base, n), n + 1
            self.handles[key] = h
        return self.handles[key]

    # ── chats ──
    def _chat(self, cid) -> dict:
        c = self.chats.get(str(cid))
        if not c:
            raise ValueError("That chat isn't here any more.")
        return c

    def summary(self) -> list:
        with self.lock:
            out = [{"id": cid, "title": c["title"], "mode": c["mode"], "pinned": c.get("pinned", False),
                    "created": c["created"], "updated": c["updated"], "count": len(c["messages"]),
                    "writing": cid in self.busy} for cid, c in self.chats.items()]
        return sorted(out, key=lambda c: -c["updated"])

    def get(self, cid) -> dict:
        with self.lock:
            c = self._chat(cid)
            return json.loads(json.dumps(dict(c, id=str(cid), writing=str(cid) in self.busy)))

    def new(self, mode: str) -> dict:
        now = time.time()
        cid = "%d%03d" % (now * 1000, len(self.chats) % 1000)
        with self.lock:
            self.chats[cid] = {"title": "New chat", "mode": mode, "created": now, "updated": now,
                               "pinned": False, "seq": 0, "messages": []}
            self._save()
        return self.get(cid)

    def update(self, cid, **fields) -> dict:
        with self.lock:
            c = self._chat(cid)
            c.update(fields)
            self._save()
        return self.get(cid)

    def delete(self, cid) -> None:
        with self.lock:
            if str(cid) in self.busy:
                raise ValueError("That chat is still writing: stop it first.")
            c = self.chats.pop(str(cid), None)
            if c is None:
                raise ValueError("That chat isn't here any more.")
            self.removed.extend(_spend(m) for m in c["messages"] if m.get("cost"))
            self._save()

    def append(self, cid, msg: dict, cut_from=None) -> dict:
        """Add a message (giving it the next id); cut_from = id of a message
        to drop along with everything after it first (Edit & resend).
        Dropped replies keep their spend in "removed"."""
        with self.lock:
            c = self._chat(cid)
            if cut_from is not None:
                ids = [m["id"] for m in c["messages"]]
                if cut_from in ids:
                    gone = c["messages"][ids.index(cut_from):]
                    c["messages"] = c["messages"][:ids.index(cut_from)]
                    self.removed.extend(_spend(m) for m in gone if m.get("cost"))
            c["seq"] = c.get("seq", 0) + 1
            msg = dict(msg, id=c["seq"])
            c["messages"].append(msg)
            c["updated"] = msg["time"]
            if msg["role"] == "user" and c["title"] == "New chat":
                c["title"] = _title(msg)
            self._save()
            return dict(msg)

    def message(self, cid, mid) -> dict:
        with self.lock:
            m = next((m for m in self._chat(cid)["messages"] if m["id"] == mid), None)
            if not m:
                raise ValueError("That message isn't in the chat any more.")
            return dict(m)

    def mark_queued(self, cid, mid, sent: dict) -> None:
        with self.lock:
            for m in self._chat(cid)["messages"]:
                if m["id"] == mid:
                    m.setdefault("queued", []).append(sent)
            self._save()

    def next_version(self, cid) -> int:
        with self.lock:
            return 1 + max([m.get("v") or 0 for m in self._chat(cid)["messages"]] or [0])

    # ── spend, the same shape as imgprompt.Log ──
    def _costed(self) -> list:
        return [m for c in self.chats.values() for m in c["messages"] if m.get("cost")] + self.removed

    def spent(self, account: str = None, since: float = 0) -> float:
        with self.lock:
            return sum(r["cost"] for r in self._costed()
                       if r["time"] >= since and (account is None or r["account"] == account))

    def totals(self) -> dict:
        with self.lock:
            paid = self._costed()
            session = [r for r in paid if r["time"] >= ip.STARTED]
            return {"session": sum(r["cost"] for r in session), "session_count": len(session),
                    "total": sum(r["cost"] for r in paid), "count": len(paid),
                    "since": min((r["time"] for r in paid), default=None),
                    "session_started": ip.STARTED}


def _spend(m: dict) -> dict:
    return {"time": m["time"], "account": m.get("account", ""), "cost": m["cost"]}


def _title(msg: dict) -> str:
    text = MENTION_RE.sub(lambda x: x.group(0)[1:], msg.get("text") or "").strip()
    first = (text.splitlines() or [""])[0].strip()
    if not first and msg.get("images"):
        first = "Pictures: " + " ".join("@" + i["handle"] for i in msg["images"])
    return (first[:60] + ("…" if len(first) > 60 else "")) or "New chat"


STORE = Store()


def _logs() -> dict:
    """The three picture stores the library shows."""
    return {"chat": STORE, "img": ip.LOG, "txt": txtprompt.LOG}


# ── library ──────────────────────────────────────────────────────────

def library() -> list:
    """Every picture: chat uploads plus the Image and Text to prompt ones
    (read only), each with its source and @handle, last used first."""
    rows = [("img", e) for e in ip.LOG.image_list()] + [("txt", e) for e in txtprompt.LOG.image_list()]
    with STORE.lock:
        rows += [("chat", dict(e, id=i)) for i, e in STORE.images.items()]
        out = [{"src": src, "id": e["id"], "name": e.get("name") or "image", "size": e.get("size"),
                "added": e.get("added"), "used": e.get("used", e.get("added")),
                "handle": STORE._handle(src, e["id"], e.get("name") or "image")} for src, e in rows]
        STORE._save()
    return sorted(out, key=lambda r: -(r["used"] or 0))


def image_file(src: str, iid: str):
    """(bytes, mime) of a picture in any of the three stores, or None."""
    log = _logs().get(src)
    return ip.image_file(iid, log) if log else None


def upload(body: dict) -> dict:
    return ip.upload(body, STORE)


def rename_handle(body: dict) -> dict:
    src, iid, new = str(body.get("src") or ""), str(body.get("id") or ""), str(body.get("handle") or "").strip().lstrip("@")
    if not HANDLE_RE.match(new):
        raise ValueError("A handle is 1-32 letters, digits, - or _.")
    key = "%s:%s" % (src, iid)
    with STORE.lock:
        if key not in STORE.handles:
            raise ValueError("That picture isn't in the library.")
        if any(h.lower() == new.lower() and k != key for k, h in STORE.handles.items()):
            raise ValueError("@%s is already taken." % new)
        STORE.handles[key] = new
        STORE._save()
    return {"src": src, "id": iid, "handle": new}


def delete_image(body: dict) -> dict:
    """Delete a chat upload (the other tabs' pictures are theirs to delete).
    Chats that mentioned it keep their text."""
    iid = str(body.get("id") or "")
    if str(body.get("src") or "") != "chat" or not ip.ID_RE.match(iid):
        raise ValueError("Only pictures uploaded in a chat can be deleted here.")
    with STORE.lock:
        e = STORE.images.pop(iid, None)
        if e is None:
            raise ValueError("That picture isn't stored here any more.")
        handle = STORE.handles.pop("chat:" + iid, None)
        STORE._save()
    try:
        (STORE.dir / ("%s.%s" % (iid, e["ext"]))).unlink()
    except FileNotFoundError:
        pass
    return {"id": iid, "name": e["name"], "handle": handle}


# ── modes ────────────────────────────────────────────────────────────

def modes() -> dict:
    """{name: description}: every base_prompt_<name>.md, then Free chat."""
    found = ip.prompts(DATA, PREFIX)
    found[FREE] = FREE_DESC
    return found


def is_video(mode: str) -> bool:
    """Video modes (base_prompt_video*.md) write image-to-video prompts: they
    are copied into a video tab by hand, never queued as Krea 2 pictures."""
    return str(mode or "").startswith("video")


def _edit_template(mode: str) -> str:
    """edit_<mode>.md when the mode has one, else edit.md; without its description line."""
    for name in ("edit_%s.md" % mode, "edit.md"):
        path = DATA / name
        if path.is_file():
            text = ip.DESC_RE.sub("", path.read_text(encoding="utf-8"), count=1).strip()
            if text:
                return text
    raise ValueError("The edit sub-template %s is missing or empty." % (DATA / "edit.md"))


def config() -> dict:
    try:
        neg = {"text": ip.negative(NEGATIVE_FILE), "file": str(NEGATIVE_FILE)}
    except ValueError as exc:
        neg = {"text": "", "file": str(NEGATIVE_FILE), "error": str(exc)}
    found = modes()
    return {"model": dict(ip.MODELS[MODEL], key=MODEL), "modes": found,
            "default_mode": DEFAULT_MODE if DEFAULT_MODE in found else next(iter(found)),
            "prompt_dir": str(DATA), "totals": STORE.totals(), "chats": STORE.summary(),
            "negative": neg, "max_images": MAX_IMAGES, "max_text": MAX_TEXT,
            "video_modes": [k for k in found if is_video(k)]}


# ── one message -> one reply ─────────────────────────────────────────

def _pictures(text: str, picked) -> list:
    """The message's pictures, in the order the text mentions them, then
    any picked but not mentioned: [{src, id, handle, data, mime}]."""
    with STORE.lock:
        by_handle = {h.lower(): k for k, h in STORE.handles.items()}
    keys = []
    for h in MENTION_RE.findall(text or ""):
        k = by_handle.get(h.lower())
        if k and k not in keys:
            keys.append(k)
    for p in picked if isinstance(picked, list) else []:
        k = "%s:%s" % ((p or {}).get("src"), (p or {}).get("id"))
        if k not in keys:
            keys.append(k)
    if len(keys) > MAX_IMAGES:
        raise ValueError("At most %d pictures in one message." % MAX_IMAGES)
    out = []
    for k in keys:
        src, _, iid = k.partition(":")
        found = image_file(src, iid)
        if not found:
            raise ValueError("A picture in this message isn't stored any more: pick it again.")
        with STORE.lock:
            handle = STORE.handles.get(k) or STORE._handle(src, iid, iid)
            if src == "chat" and iid in STORE.images:
                STORE.images[iid]["used"] = time.time()
        out.append({"src": src, "id": iid, "handle": handle, "data": found[0], "mime": found[1]})
    return out


def by_number(text: str, pictures: list) -> str:
    """The message as Qwen gets it: each @handle of a sent picture becomes
    "Picture N", its place in the order sent. Handles stay on the page; the
    model sees one name per picture, so a handle like @image-3 can't be read
    as the third picture. An @word that isn't a sent picture stays as typed."""
    num = {p["handle"].lower(): n for n, p in enumerate(pictures, 1)}
    return MENTION_RE.sub(lambda m: "Picture %d" % num[m.group(1).lower()] if m.group(1).lower() in num else m.group(0),
                          text or "")


def build_request(mode: str, text: str, pictures: list, context=None) -> tuple:
    """(system, content) for one message. context = the picked earlier
    prompt when editing, else None."""
    text = by_number(text, pictures)
    if mode == FREE:
        system = FREE_SYSTEM
    else:
        system = ip.base_prompt(mode, DATA, PREFIX)
        if context is not None:
            system += "\n\n" + _edit_template(mode)
    content = []
    if context is not None:
        label = "CONTEXT (an earlier reply in this chat):" if mode == FREE else "CURRENT PROMPT:"
        content.append({"type": "text", "text": "%s\n%s" % (label, context)})
        content.append({"type": "text", "text": ("CHANGES:\n" if mode != FREE else "") + (text or "(no changes written)")})
    else:
        content.append({"type": "text", "text": text or "(no text: use the pictures)"})
    for n, p in enumerate(pictures, 1):
        content.append({"type": "text", "text": "Picture %d:" % n})
        content.append(ip.image_part(p["data"], p["mime"]))
    return system, content


def send(session, account: str, body: dict, emit) -> list:
    """One message -> n streamed replies (body["n"], 1 to MAX_COUNT, written
    side by side as separate Qwen calls), all saved in the chat.
    emit(event: dict) -> bool sends a line to the page; False means the
    page went away (Stop), which stops every reply and keeps what each
    wrote. Each line about a reply carries k, its index. Returns the saved
    replies in k order."""
    cid = str(body.get("chat") or "")
    chat = STORE.get(cid)
    mode = str(body.get("mode") or chat["mode"])
    if mode not in modes():
        raise ValueError("Unknown mode %s." % mode)
    text = "\n".join(l.rstrip() for l in str(body.get("text") or "").strip().splitlines())
    if len(text) > MAX_TEXT:
        raise ValueError("The message is over %d characters." % MAX_TEXT)
    pictures = _pictures(text, body.get("images"))
    if not text and not pictures:
        raise ValueError("Write a message or add a picture.")
    context, edit_of = None, body.get("edit_of")
    if edit_of is not None:
        target = STORE.message(cid, int(edit_of))
        context = target.get("prompt") or (target.get("talk") if mode == FREE else None)
        if not context:
            raise ValueError("That reply has no prompt to edit.")
    resend_of = body.get("resend_of")
    try:
        n = max(1, min(MAX_COUNT, int(body.get("n") or 1)))
    except (TypeError, ValueError):
        n = 1
    system, content = build_request(mode, text, pictures, context)
    req = {"model": ip.MODELS[MODEL]["id"], "max_tokens": MAX_TOKENS, "stream": True,
           "stream_options": {"include_usage": True},
           "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    data = json.dumps(req)
    if len(data) > MAX_REQUEST:
        raise ValueError("Too big for Bedrock (%.1f MB, the limit is about 3.5 MB): use fewer pictures, "
                         "or upload them again so the page shrinks them." % (len(data) / 2 ** 20))
    creds = session.get_credentials()
    if creds is None:
        raise ValueError("No AWS keys for account %s." % account)

    with STORE.lock:
        if cid in STORE.busy:
            raise ValueError("This chat is still writing a reply.")
        STORE.busy.add(cid)
    try:
        user = STORE.append(cid, {
            "role": "user", "time": time.time(), "text": text, "mode": mode,
            "images": [{k: p[k] for k in ("src", "id", "handle")} for p in pictures],
            "edit_of": int(edit_of) if edit_of is not None else None},
            cut_from=int(resend_of) if resend_of is not None else None)
        gate, gone, saving = threading.Lock(), threading.Event(), threading.Lock()

        def say(event: dict) -> bool:          # lines from several replies go out one at a time
            with gate:
                if gone.is_set():
                    return False
                if not emit(event):
                    gone.set()
                    return False
                return True

        say({"t": "user", "msg": user, "n": n})
        frozen = creds.get_frozen_credentials()

        def one(k: int) -> dict:
            t, parts, usage, stop, stopped, error = time.time(), [], None, None, False, None
            try:
                for kind, value in _stream(frozen, data):
                    if kind == "text":
                        parts.append(value)
                        if not say({"t": "delta", "k": k, "text": value}):
                            stopped = True
                            break
                    elif kind == "stop":
                        stop = value
                    elif kind == "usage":
                        usage = value
            except RuntimeError as exc:
                error = str(exc)
            raw = "".join(parts)
            talk, prompt = split_reply(raw)
            tin = (usage or {}).get("prompt_tokens") or 0
            tout = (usage or {}).get("completion_tokens") or 0
            estimated = not usage and bool(raw)
            if estimated:                     # stopped before Bedrock sent the counts
                tin = (len(system) + len(text) + len(context or "")) // 4 + 1200 * len(pictures)
                tout = max(1, len(raw) // 4)
            reply = {"role": "assistant", "time": time.time(), "account": account, "model": MODEL, "mode": mode,
                     "prompt_sha": hashlib.sha256(system.encode()).hexdigest()[:8],
                     "edit_of": user["edit_of"], "reply_to": user["id"],
                     "text": raw, "talk": talk, "prompt": prompt,
                     "tokens_in": tin, "tokens_out": tout, "cost": ip.cost(MODEL, tin, tout),
                     "seconds": round(time.time() - t, 1), "stop": stop}
            if n > 1:
                reply["take"] = k + 1         # "take 2 of 3" on the page
            if estimated:
                reply["estimated"] = True
            if stopped:
                reply["stopped"] = True
            if error:
                reply["error"] = error
            if prompt is not None and stop == "length":
                reply["cut"] = True           # hit MAX_TOKENS before </prompt>
            with saving:                      # versions number in the order replies finish
                reply["v"] = STORE.next_version(cid) if prompt else None
                reply = STORE.append(cid, reply)
            if n > 1:
                say({"t": "reply", "k": k, "msg": reply})
            return reply

        if n == 1:
            return [one(0)]
        out = [None] * n

        def worker(k):
            try:
                out[k] = one(k)
            except Exception as exc:          # one take failing never sinks the others
                out[k] = {"role": "assistant", "error": str(exc), "take": k + 1}
        threads = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return out
    finally:
        with STORE.lock:
            STORE.busy.discard(cid)


def _stream(cred, data: str):
    """Bedrock's chat completions with stream: true -> ("text", s),
    ("stop", reason) and ("usage", {...}) as they arrive."""
    url = "https://bedrock-mantle.%s.api.aws/v1/chat/completions" % ip.REGION
    req = AWSRequest(method="POST", url=url, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(cred, "bedrock-mantle", ip.REGION).add_auth(req)
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, data=data.encode(), headers=dict(req.headers),
                                                             method="POST"), timeout=300)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Bedrock said HTTP %d: %s" % (exc.code, exc.read()[:400].decode(errors="replace"))) from None
    except (OSError, ValueError) as exc:
        raise RuntimeError("Bedrock didn't answer: %s" % exc) from None
    with resp:
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    yield "usage", chunk["usage"]
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield "text", piece
                    if choice.get("finish_reason"):
                        yield "stop", choice["finish_reason"]
        except OSError as exc:
            raise RuntimeError("The reply broke off: %s" % exc) from None


# ── chat housekeeping and the app queue ──────────────────────────────

def new_chat(body: dict) -> dict:
    mode = str(body.get("mode") or DEFAULT_MODE)
    if mode not in modes():
        mode = DEFAULT_MODE
    return STORE.new(mode)


def edit_chat(body: dict) -> dict:
    """Rename, pin or change the mode of a chat."""
    cid, fields = str(body.get("chat") or ""), {}
    if "title" in body:
        title = " ".join(str(body["title"] or "").split())[:60]
        if not title:
            raise ValueError("A chat needs a name.")
        fields["title"] = title
    if "pinned" in body:
        fields["pinned"] = bool(body["pinned"])
    if "mode" in body:
        if body["mode"] not in modes():
            raise ValueError("Unknown mode %s." % body["mode"])
        fields["mode"] = body["mode"]
    return STORE.update(cid, **fields)


def delete_chat(body: dict) -> dict:
    STORE.delete(str(body.get("chat") or ""))
    return {"totals": STORE.totals()}


def send_to_queue(origin: str, key: str, instance: str, body: dict) -> dict:
    """A reply's prompt -> a Krea 2 V2 job at the top of the instance's queue,
    with the chat's own steps / aspect and chat2prompt/negative.json. The
    page sends the aspect as a bare ratio ("3:4"); the app's own label for
    it ("3:4 (Portrait Standard)") is looked up here."""
    cid, mid = str(body.get("chat") or ""), int(body.get("msg") or 0)
    m = STORE.message(cid, mid)
    if not m.get("prompt") or m["prompt"].startswith("SKIP:"):
        raise ValueError("That reply has no prompt to queue.")
    if is_video(m.get("mode")):
        raise ValueError("A video prompt can't be queued as a picture: copy it into a video tab.")
    want = str(body.get("aspect") or "").strip()
    if want:
        choices = ip._aspects(origin, key)
        label = next((c for c in choices if c == want or c.split(" ", 1)[0] == want), None)
        if not label:
            raise ValueError("The app has no %s aspect (it offers %s)."
                             % (want, ", ".join(c.split(" ", 1)[0] for c in choices) or "none"))
        body = dict(body, aspect=label)
    result = ip.queue_text(origin, key, instance, m["prompt"], body, NEGATIVE_FILE)
    STORE.mark_queued(cid, mid, result["sent"])
    return result
