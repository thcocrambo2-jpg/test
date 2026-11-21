"""Text to prompt for the sniper page's third tab: a short brief, plus any
reference pictures each with a note on what to take from it ("background
like this"), and the same Bedrock models as Image to prompt write a
detailed image-generation prompt.

Built on imgprompt.py (models, prices, the Bedrock call, sending to an
instance's queue, negative.json). Its own things live in txt2prompt/ next
to this file: the base prompts (every base_prompt_<name>.md, read fresh
each time, so a new v3 is a new file and nothing else), the reference
pictures in images/ (named by content hash) and history.json with every
generation (brief, references, tokens, cost, the prompt it wrote).
"""

import hashlib
import time

import imgprompt as ip

DATA = ip.HERE / "txt2prompt"
PREFIX = "base_prompt_"
LOG = ip.Log(DATA / "history.json", DATA / "images")
MAX_TOKENS = 4000          # the base prompts ask for seven sections plus the specs
MAX_BRIEF = 4000
MAX_REFS = 6
MAX_NOTE = 300
DEFAULT_PROMPT = "v1"

ASK = "Write the prompt for this brief."
NO_NOTE = "(no note: take its setting, light and camera feel, not its people)"


def config() -> dict:
    found = ip.prompts(DATA, PREFIX)
    try:
        neg = {"text": ip.negative(), "file": str(ip.NEGATIVE_FILE)}
    except ValueError as exc:
        neg = {"text": "", "file": str(ip.NEGATIVE_FILE), "error": str(exc)}
    return {"models": ip.MODELS, "default_model": ip.DEFAULT_MODEL, "prompts": found,
            "default_prompt": DEFAULT_PROMPT if DEFAULT_PROMPT in found else next(iter(found), None),
            "prompt_dir": str(DATA), "totals": LOG.totals(), "records": LOG.recent(),
            "negative": neg, "max_refs": MAX_REFS}


def upload(body: dict) -> dict:
    return ip.upload(body, LOG)


def image_file(iid: str):
    return ip.image_file(iid, LOG)


def _refs(raw) -> list:
    """[{id, note}] from the page -> [{id, name, note, data, mime}], every picture checked."""
    if not isinstance(raw, list):
        raw = []
    if len(raw) > MAX_REFS:
        raise ValueError("At most %d reference pictures." % MAX_REFS)
    out = []
    for r in raw:
        iid = str((r or {}).get("id") or "")
        found = image_file(iid)
        if not found:
            raise ValueError("A reference picture isn't stored here any more: add it again.")
        LOG.touch(iid)
        note = " ".join(str(r.get("note") or "").split())[:MAX_NOTE]
        out.append({"id": iid, "name": LOG.image(iid)["name"], "note": note,
                    "data": found[0], "mime": found[1]})
    return out


def generate(session, account: str, body: dict) -> dict:
    """One brief (+ references), one model, one base prompt -> one prompt.
    session: boto3 session for the account in imgprompt.REGION."""
    model = str(body.get("model") or ip.DEFAULT_MODEL)
    version = str(body.get("prompt") or DEFAULT_PROMPT)
    if model not in ip.MODELS:
        raise ValueError("Unknown model %s." % model)
    system = ip.base_prompt(version, DATA, PREFIX)
    brief = "\n".join(l.rstrip() for l in str(body.get("brief") or "").strip().splitlines())
    if not brief:
        raise ValueError("Write a brief first.")
    if len(brief) > MAX_BRIEF:
        raise ValueError("The brief is over %d characters." % MAX_BRIEF)
    refs = _refs(body.get("refs"))

    content = [{"type": "text", "text": "BRIEF:\n" + brief}]
    for n, r in enumerate(refs, 1):
        content.append({"type": "text", "text": "REFERENCE %d, take from it: %s" % (n, r["note"] or NO_NOTE)})
        content.append(ip.image_part(r["data"], r["mime"]))
    content.append({"type": "text", "text": ASK})
    out = ip.ask_model(session, account, model, system, content, MAX_TOKENS)
    first = brief.splitlines()[0]
    rec = {"time": time.time(), "account": account, "name": first[:60] + ("…" if len(first) > 60 else ""),
           "brief": brief, "refs": [{k: r[k] for k in ("id", "name", "note")} for r in refs],
           "model": model, "prompt": version,
           "prompt_sha": hashlib.sha256(system.encode()).hexdigest()[:8], **out}
    LOG.add(rec)
    return rec


def send_to_queue(origin: str, key: str, instance: str, body: dict) -> dict:
    rec = LOG.find(body.get("time"))
    if not rec:
        raise ValueError("That prompt isn't in the history any more.")
    if not rec["text"] or rec["text"].startswith("SKIP:"):
        raise ValueError("The model skipped this brief: there is no prompt to queue.")
    result = ip.queue_text(origin, key, instance, rec["text"], body)
    LOG.mark_queued(rec["time"], result["sent"])
    return result
