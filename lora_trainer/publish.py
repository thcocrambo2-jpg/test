"""Publish a trained LoRA as a private one: R2, the catalogue, the grants.

Three steps, each safe to repeat:

  1. upload   the file to loras/private/<file> in the private builds
              bucket, signed with the R2 key typed into the page
  2. register the `loras` record and its place on the Krea tabs' lists,
              through the licence server's POST /v1/admin/private-loras
  3. grant    it to the licence keys typed in, through
              POST /v1/admin/private-loras/grant

The R2 key and the admin token come from the page for this one publish.
They are held only in the running job, never written to disk, logged or
put in the page's state, and an error message never carries a signed URL
(urllib's do not). The licence server's own R2 key stays read-only: the
upload is signed here, which is why the key has to be typed in at all.

Duplicates:

  • the same file under the same name (ETag = its MD5) is not uploaded
    again — step 1 just says so;
  • a *different* file under a name already in the bucket is refused: a
    pod that has the old file keeps it by name and would never fetch the
    new one, so a retrained LoRA needs a new name;
  • the record and the grants are idempotent on the server
    (src/privateLoras.js): same id again updates it, a key that has it
    already keeps it, an id that is a public LoRA is refused.
"""

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from r2_presign import presign  # noqa: E402

PREFIX = "loras/private/"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$")
KEY_SPLIT = re.compile(r"[\s,;]+")


class PublishError(Exception):
    """A refusal or failure, worded for the page."""


def lora_id(file_name: str) -> str:
    """face_sayara.safetensors -> face-sayara."""
    stem = Path(file_name).stem.lower()
    return re.sub(r"[^a-z0-9-]+", "-", stem).strip("-")[:63]


def licence_keys(text) -> list:
    """Keys typed one per line, or separated by commas or spaces."""
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    return [k for k in KEY_SPLIT.split(str(text or "")) if k]


def md5_of(path: Path) -> str:
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
    url = presign(method, key, 3600, bucket=r2["bucket"], creds=r2)
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
    return urllib.request.urlopen(request, timeout=timeout)


def remote(key: str, r2: dict):
    """(size, etag) of the object, or None when it is not there."""
    try:
        with _r2("HEAD", key, r2) as response:
            return (int(response.headers.get("Content-Length") or -1),
                    (response.headers.get("ETag") or "").strip('"').lower())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise PublishError("R2 refused the key (HTTP %d) — check the "
                               "account id, access key and secret, and that "
                               "it can read and write %s." % (exc.code, r2["bucket"]))
        raise PublishError("R2 answered HTTP %d checking the bucket." % exc.code)
    except urllib.error.URLError as exc:
        raise PublishError("Could not reach R2 (%s) — is the account id right?"
                           % exc.reason)


def upload(path: Path, r2: dict, step) -> str:
    """Step 1. Returns what happened, in words."""
    key = PREFIX + path.name
    size = path.stat().st_size
    step("checking the bucket", 0, size)
    local_md5 = md5_of(path)
    found = remote(key, r2)
    if found is not None:
        have_size, etag = found
        if have_size == size and etag == local_md5:
            return "already in R2 (same file) — not uploaded again"
        raise PublishError(
            "%s is already in R2 but it is a different file. Pods that have "
            "the old one never fetch it again by the same name, so give this "
            "LoRA a new name (train it as e.g. %s_v2) and publish that."
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
    found = remote(key, r2)
    if not found or found[0] != size:
        raise PublishError("The upload finished but R2 does not have the "
                           "whole file — publish again.")
    return "uploaded %.1f MB" % (size / 1e6)


def _admin(api: dict, path: str, body: dict) -> dict:
    """POST to the licence server's admin API; its answer, or a PublishError."""
    request = urllib.request.Request(
        api["url"].rstrip("/") + path, data=json.dumps(body).encode(),
        method="POST", headers={"Content-Type": "application/json",
                                "Authorization": "Bearer " + api["token"]})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = {}
        if exc.code == 401:
            raise PublishError("The licence server refused the admin token.")
        if exc.code == 404:
            raise PublishError(
                "The licence server has no %s — deploy the version with "
                "private LoRAs first (or ADMIN_TOKEN is unset there)." % path)
        raise PublishError("Licence server: %s" % (
            detail.get("message") or detail.get("error") or "HTTP %d" % exc.code))
    except urllib.error.URLError as exc:
        raise PublishError("Could not reach the licence server (%s)." % exc.reason)


def register(api: dict, lora: dict) -> str:
    """Step 2."""
    answer = _admin(api, "/v1/admin/private-loras", lora)
    lists = ", ".join("%s %s" % (k, v) for k, v in (answer.get("lists") or {}).items())
    return "%s %s (%s)" % ("created" if answer.get("created") else "updated",
                           answer.get("id"), lists)


def grant(api: dict, lora_id_: str, keys: list) -> str:
    """Step 3."""
    answer = _admin(api, "/v1/admin/private-loras/grant",
                    {"id": lora_id_, "keys": keys})
    who = ", ".join("%s (%s)" % (l.get("name") or "?", l.get("key"))
                    for l in answer.get("licences") or [])
    return "granted to %s — %d new" % (who, answer.get("newly", 0))


def check(job: dict) -> None:
    """Everything that can be refused before anything is sent."""
    if not FILE_RE.match(job["file"]):
        raise PublishError("%s is not a plain .safetensors name." % job["file"])
    if not ID_RE.match(job["id"]):
        raise PublishError("The id may hold a-z, 0-9 and - only.")
    r2, api = job["r2"], job["api"]
    if not all(r2.get(k) for k in ("account", "access_key", "secret", "bucket")):
        raise PublishError("Fill in the R2 account id, access key, secret and bucket.")
    if not api.get("url", "").startswith("https://") or not api.get("token"):
        raise PublishError("Fill in the licence server URL (https://…) and the admin token.")
