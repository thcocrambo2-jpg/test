"""Publish a trained LoRA as a private one, to this pod's own licence.

The 🎭 Face LoRA page's Publish, in three steps, each safe to repeat:

  1. ask      POST /v1/face-lora/upload — the licence server checks the
              name, size and the consent tick, and
              answers with a PUT link for exactly one object in its face
              bucket (or `already`: this file is published)
  2. upload   the file to that link, straight to R2 — no R2 key is on the
              pod, typed in or otherwise
  3. register POST /v1/face-lora/register — the server checks what landed
              (size and MD5) and saves the record, granted to this licence
              and to no other

All three are authorised by this pod's licence key, which must grant
`face_lora`. The link is never logged, stored, or put in an error (urllib's
errors do not carry the URL).

Names are scoped to the licence: the server publishes run `anna` as
face_<tag>_anna.safetensors, the name run.install already gave it. A file
installed before the tag existed is renamed to match once it is published.

Duplicates are the server's to refuse (license-validator/src/faceLoras.js):
the same file again is `already`; a *different* file under a name already
published is refused, because pods that have the old one keep it by name.
"""

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

from ember.settings import LICENSE_API_URL, LICENSE_KEY


class PublishError(Exception):
    """A refusal or failure, worded for the page."""


def _md5(path: Path, step) -> str:
    digest = hashlib.md5()
    size, done = path.stat().st_size, 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
            done += len(block)
            step("checking the file", done, size)
    return digest.hexdigest()


class _Reader:
    """A file body that reports how much of it has gone."""

    def __init__(self, path: Path, on_bytes):
        self.handle = path.open("rb")
        self.on_bytes = on_bytes
        self.sent = 0

    def read(self, size=-1):
        chunk = self.handle.read(size if size and size > 0 else 1 << 20)
        self.sent += len(chunk)
        self.on_bytes(self.sent)
        return chunk

    def close(self):
        self.handle.close()


def _server(path: str, body: dict) -> dict:
    """POST to the licence server as this pod's licence; its answer."""
    if not LICENSE_API_URL or not LICENSE_KEY:
        raise PublishError("This pod has no licence server to publish to.")
    request = urllib.request.Request(
        LICENSE_API_URL + path,
        data=json.dumps({"license_key": LICENSE_KEY, **body}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = {}
        if exc.code == 404 and not detail.get("message"):
            raise PublishError(
                "The licence server has no %s yet — it needs the version with "
                "Face LoRA publishing." % path)
        raise PublishError(detail.get("message") or detail.get("error")
                           or "The licence server answered HTTP %d." % exc.code)
    except urllib.error.URLError as exc:
        raise PublishError("Could not reach the licence server (%s)." % exc.reason)


def _put(url: str, path: Path, size: int, step) -> None:
    reader = _Reader(path, lambda sent: step("uploading", sent, size))
    request = urllib.request.Request(url, data=reader, method="PUT", headers={
        "Content-Type": "application/octet-stream",
        "Content-Length": str(size)})
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            if not 200 <= response.status < 300:
                raise PublishError("Storage answered HTTP %d to the upload." % response.status)
    except urllib.error.HTTPError as exc:
        raise PublishError("Storage refused the upload (HTTP %d) — publish again."
                           % exc.code)
    except urllib.error.URLError as exc:
        raise PublishError("The upload failed (%s) — publish again." % exc.reason)
    finally:
        reader.close()


def publish(path: Path, name: str, trigger: str, consent: bool, step, done) -> dict:
    """All three steps for one installed file. `step(what, sent, total)`
    reports progress and `done(line)` each finished step. Returns the
    server's {id, file} for it."""
    size = path.stat().st_size
    md5 = _md5(path, step)
    body = {"name": name, "trigger": trigger, "size": size, "md5": md5,
            "consent": consent is True}
    step("asking the licence server")
    ticket = _server("/v1/face-lora/upload", body)
    if ticket.get("already"):
        done("already in storage (same file) — not uploaded again")
    else:
        _put(ticket["url"], path, size, step)
        done("uploaded %.1f MB" % (size / 1e6))
    step("registering")
    answer = _server("/v1/face-lora/register", body)
    done("%s %s — on this licence's 🔶 Krea2 V2 and 🎨 Krea2 tabs"
         % ("published" if answer.get("created") else "already published as",
            answer.get("id")))
    return {"id": answer.get("id"), "file": answer.get("file") or ticket.get("file")}


def mine() -> dict:
    """The Face LoRAs this licence has now, its own and any shared with it."""
    return _server("/v1/face-lora/mine", {})
