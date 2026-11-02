"""Publish a trained LoRA as a private one: R2, the catalogue, the grants.

The 🎭 Face LoRA page's Publish, in three steps, each safe to repeat:

  1. upload   the file to loras/private/<file> in the private builds
              bucket, signed with the R2 key typed into the page (r2.py)
  2. register the `loras` record and its place on the Krea tabs' lists —
              POST /v1/face-lora/register on the licence server
  3. grant    it to the licence keys typed in — POST /v1/face-lora/grant

Steps 2 and 3 are authorised by this pod's own licence key, which must
grant `face_lora`; no admin token is involved. The R2 key exists only in
the running job: never written to disk, logged or kept in any state the
page can read back, and no error message carries a signed URL (urllib's
do not).

Duplicates:

  • the same file under the same name (ETag = its MD5) is not uploaded
    again — step 1 just says so;
  • a *different* file under a name already in the bucket is refused: a
    pod that has the old file keeps it by name and would never fetch the
    new one, so a retrained LoRA needs a new name;
  • the record and the grants are idempotent on the server
    (license-validator/src/privateLoras.js): the same id again updates
    it, a key that has it already keeps it, a public LoRA's id is refused.
"""

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from ember.settings import LICENSE_API_URL, LICENSE_KEY
from ember.trainer.r2 import presign

PREFIX = "loras/private/"
DEFAULT_BUCKET = "krea2-builds"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$")
KEY_SPLIT = re.compile(r"[\s,;]+")


class PublishError(Exception):
    """A refusal or failure, worded for the page."""


def lora_id(file_name: str) -> str:
    """face_sayara.safetensors -> face-sayara, the id it is published as."""
    stem = Path(file_name).stem.lower()
    return re.sub(r"[^a-z0-9-]+", "-", stem).strip("-")[:63]


def licence_keys(text) -> list:
    """Keys typed one per line, or separated by commas or spaces."""
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    return list(dict.fromkeys(k for k in KEY_SPLIT.split(str(text or "")) if k))


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 << 20), b""):
            digest.update(block)
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


def _r2(method, key, r2, data=None, headers=None, timeout=60):
    url = presign(method, key, 3600, **r2)
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
    return urllib.request.urlopen(request, timeout=timeout)


def _remote(key: str, r2: dict):
    """(size, etag) of the object, or None when it is not there."""
    try:
        with _r2("HEAD", key, r2) as response:
            return (int(response.headers.get("Content-Length") or -1),
                    (response.headers.get("ETag") or "").strip('"').lower())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise PublishError(
                "R2 refused the key (HTTP %d) — check the account id, access "
                "key and secret, and that it can read and write %s."
                % (exc.code, r2["bucket"]))
        raise PublishError("R2 answered HTTP %d checking the bucket." % exc.code)
    except urllib.error.URLError as exc:
        raise PublishError("Could not reach R2 (%s) — is the account id right?"
                           % exc.reason)


def upload(path: Path, r2: dict, step) -> str:
    """Step 1. `step(name, sent, total)` reports progress. Returns what happened."""
    key = PREFIX + path.name
    size = path.stat().st_size
    step("checking the bucket", 0, size)
    found = _remote(key, r2)
    if found is not None:
        have_size, etag = found
        if have_size == size and etag == _md5(path):
            return "already in R2 (same file) — not uploaded again"
        raise PublishError(
            "%s is already in R2 but it is a different file. Pods that have "
            "the old one never fetch it again by the same name, so train this "
            "one under a new name (e.g. %s_v2) and publish that."
            % (key, path.stem.replace("face_", "", 1)))
    reader = _Reader(path, lambda sent: step("uploading", sent, size))
    try:
        with _r2("PUT", key, r2, data=reader, timeout=3600, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size)}) as response:
            if not 200 <= response.status < 300:
                raise PublishError("R2 answered HTTP %d to the upload." % response.status)
    except urllib.error.HTTPError as exc:
        raise PublishError("R2 refused the upload (HTTP %d)." % exc.code)
    except urllib.error.URLError as exc:
        raise PublishError("The upload failed (%s)." % exc.reason)
    finally:
        reader.close()
    found = _remote(key, r2)
    if not found or found[0] != size:
        raise PublishError("The upload finished but R2 does not have the "
                           "whole file — publish again.")
    return "uploaded %.1f MB" % (size / 1e6)


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
        if exc.code == 404:
            raise PublishError(
                "The licence server has no %s yet — deploy the version with "
                "Face LoRA first." % path)
        raise PublishError(detail.get("message") or detail.get("error")
                           or "The licence server answered HTTP %d." % exc.code)
    except urllib.error.URLError as exc:
        raise PublishError("Could not reach the licence server (%s)." % exc.reason)


def register(lora: dict) -> str:
    """Step 2."""
    answer = _server("/v1/face-lora/register", lora)
    lists = ", ".join("%s %s" % (k, v) for k, v in (answer.get("lists") or {}).items())
    return "%s %s (%s)" % ("created" if answer.get("created") else "updated",
                           answer.get("id"), lists)


def grant(lora_id_: str, keys: list) -> str:
    """Step 3."""
    answer = _server("/v1/face-lora/grant", {"id": lora_id_, "keys": keys})
    who = ", ".join("%s (%s)" % (l.get("name") or "?", l.get("key"))
                    for l in answer.get("licences") or [])
    return "granted to %s — %d new" % (who, answer.get("newly", 0))


def check(job: dict) -> None:
    """Everything that can be refused before anything is sent."""
    if not FILE_RE.match(job["file"]):
        raise PublishError("%s is not a plain .safetensors name." % job["file"])
    if not ID_RE.match(job["id"]):
        raise PublishError("The id may hold a-z, 0-9 and - only.")
    if not all(job["r2"].get(k) for k in ("account", "access_key", "secret", "bucket")):
        raise PublishError("Fill in the R2 account id, access key, secret and bucket.")
