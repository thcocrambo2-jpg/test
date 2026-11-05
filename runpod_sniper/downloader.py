"""Auto download: save everything a running Ember pod generates.

One Download per pod. Its thread polls the pod's Gallery
(GET /api/v1/gallery, newest first) every few seconds, and saves each file
it has not saved yet into the folder chosen for that pod, with its settings
token beside it as <name>.txt — the same pair the Gallery's own download
gives. Pictures and videos both; a file the pod is still writing (a video
mid-encode) is left for the next round, which /api/v1/media says by
answering it Cache-Control: no-store.

Nothing is downloaded twice: each pod keeps a record in its folder,
.ember-downloads-<pod id>.json, written after every file. A file already in
the folder under the same name and size counts as saved. Two pods can share
a folder: a name clash with different content gets the pod id added.

Files land under a temporary name and are renamed once complete, so the
folder never holds half a file.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import quote

import ember_token

POLL = 8                 # seconds between Gallery reads
SLOW_POLL = 30           # while the pod is not answering
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "EmberAutoDownload/1.0")   # the Runpod proxy refuses urllib's own
CHUNK = 256 * 1024
MAX_TRIES = 5            # a file that fails this often is skipped
API = "/api/v1"
NAME_OK = re.compile(r"[^A-Za-z0-9 ._()\-]+")

# Set by server.py in a dry run: (pod_id) -> fake app, or None.
FAKE_APP = None


def proxy_url(pod_id: str) -> str:
    return "https://%s-7860.proxy.runpod.net" % pod_id


class Unreachable(Exception):
    pass


class NeedsKey(Exception):
    pass


class Download:
    def __init__(self, pod_id: str, pod_name: str, folder: str, app_url: str = "",
                 access_key: str = "", account: str = ""):
        self.pod_id = pod_id
        self.pod_name = pod_name or pod_id
        self.account = account
        self.folder = Path(folder).expanduser()
        self.app_url = (app_url or proxy_url(pod_id)).rstrip("/")
        self.access_key = access_key
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "starting"     # starting/running/unreachable/needs-key/error/stopped
        self.error = None
        self.started = time.time()
        self.last_poll = None
        self.total_on_pod = 0
        self.saved = 0               # saved by this run
        self.already = 0             # in the folder before
        self.waiting = 0             # on the pod, still being written
        self.failed = 0
        self.tokens = 0
        self.bytes = 0
        self.current = None          # {name, done, total, speed}
        self.last_saved = None
        self.events = deque(maxlen=60)
        self.schemas = {}
        self.record_path = self.folder / (".ember-downloads-%s.json" % pod_id)
        self.record = {"pod": pod_id, "name": self.pod_name, "files": {}}
        self.token_tries = {}
        self.item_fails = {}

    # ── bookkeeping ─────────────────────────────────────────────────

    def event(self, text: str) -> None:
        with self.lock:
            self.events.append([time.time(), text])

    def _load_record(self) -> None:
        try:
            data = json.loads(self.record_path.read_text(encoding="utf-8"))
            if isinstance(data.get("files"), dict):
                self.record["files"] = data["files"]
        except (OSError, ValueError):
            pass

    def _save_record(self) -> None:
        tmp = self.record_path.with_suffix(".json.part")
        tmp.write_text(json.dumps(self.record, indent=1), encoding="utf-8")
        os.replace(tmp, self.record_path)

    def view(self) -> dict:
        with self.lock:
            return {
                "pod_id": self.pod_id, "pod_name": self.pod_name,
                "account": self.account, "folder": str(self.folder),
                "app_url": self.app_url, "status": self.status,
                "error": self.error, "started": self.started,
                "last_poll": self.last_poll, "total_on_pod": self.total_on_pod,
                "have": len(self.record["files"]), "saved": self.saved,
                "already": self.already, "waiting": self.waiting,
                "failed": self.failed, "tokens": self.tokens,
                "bytes": self.bytes,
                "current": dict(self.current) if self.current else None,
                "last_saved": self.last_saved, "events": list(self.events),
            }

    def config(self) -> dict:
        """What server.py keeps to resume this after a restart."""
        return {"pod_id": self.pod_id, "pod_name": self.pod_name,
                "folder": str(self.folder), "app_url": self.app_url,
                "access_key": self.access_key, "account": self.account}

    # ── HTTP to the pod's app ───────────────────────────────────────

    def _open(self, method: str, path: str, body=None, timeout=60):
        url = self.app_url + API + path
        fake = FAKE_APP is not None and self.app_url == proxy_url(self.pod_id)
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.access_key:
            headers["x-ember-key"] = self.access_key
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            if fake:                    # dry run: same errors, same handling
                return FAKE_APP(self.pod_id, method, API + path, body)
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise NeedsKey("The app asks for its access key (401).")
            if exc.code in (502, 503, 504, 530, 404) and path.startswith("/gallery"):
                raise Unreachable("The app is not answering (%s)." % exc.code)
            raise
        except (urllib.error.URLError, OSError) as exc:
            raise Unreachable("Cannot reach %s (%s)."
                              % (self.app_url, getattr(exc, "reason", exc)))

    def _json(self, method, path, body=None):
        with self._open(method, path, body, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))

    # ── the loop ────────────────────────────────────────────────────

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._load_record()
        self.event("Saving %s into %s (%d already recorded)."
                   % (self.pod_name, self.folder, len(self.record["files"])))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            if self.status not in ("error",):
                self.status = "stopped"
        self.event("Stopped.")

    def _run(self) -> None:
        while not self.stop_event.is_set():
            wait = POLL
            try:
                self._round()
                with self.lock:
                    if self.status != "stopped":
                        self.status, self.error = "running", None
            except Unreachable as exc:
                wait = SLOW_POLL
                with self.lock:
                    was = self.status
                    self.status, self.error = "unreachable", str(exc)
                if was != "unreachable":
                    self.event(str(exc) + " Retrying every %d s." % SLOW_POLL)
            except NeedsKey as exc:
                wait = SLOW_POLL
                with self.lock:
                    was = self.status
                    self.status, self.error = "needs-key", str(exc)
                if was != "needs-key":
                    self.event(str(exc))
            except Exception as exc:                     # keep going: log it
                wait = SLOW_POLL
                with self.lock:
                    self.error = "%s: %s" % (type(exc).__name__, exc)
                self.event("Error: %s" % self.error)
            with self.lock:
                self.last_poll = time.time()
            self.stop_event.wait(wait)

    def _gallery(self) -> list:
        """Every item on the pod, newest first. Pages until it reaches
        files already saved (new files are always on top)."""
        items, cursor = [], ""
        have = self.record["files"]
        while True:
            page = self._json("GET", "/gallery?limit=100" +
                              ("&cursor=" + quote(cursor) if cursor else ""))
            batch = page.get("items") or []
            items.extend(batch)
            with self.lock:
                self.total_on_pod = int(page.get("total") or len(items))
            cursor = page.get("nextCursor")
            if not cursor or self.stop_event.is_set():
                break
            if batch and all(i.get("id") in have for i in batch) and \
                    len(have) >= self.total_on_pod:
                break
        return items

    def _round(self) -> None:
        items = self._gallery()
        have = self.record["files"]
        todo = [i for i in items if i.get("id") and i["id"] not in have]
        waiting = 0
        for item in reversed(todo):                      # oldest first
            if self.stop_event.is_set():
                return
            tries = self.item_fails.get(item["id"], 0)
            if tries >= MAX_TRIES:
                continue                                 # given up on; said so once
            try:
                result = self._save(item)
            except (NeedsKey, Unreachable):
                raise
            except Exception as exc:                     # this file only
                self.item_fails[item["id"]] = tries + 1
                self.event("Could not save %s (%s)%s." % (
                    item["id"], exc, "" if tries + 1 < MAX_TRIES
                    else " — giving up on it after %d tries" % MAX_TRIES))
                continue
            if result == "waiting":
                waiting += 1
        # tokens that failed earlier get a few more tries
        for item in items:
            rec = have.get(item.get("id"))
            if rec and rec.get("token") is False and self.token_tries.get(item["id"], 0) < 3 \
                    and not self.stop_event.is_set():
                if self._write_token(item, self.folder / rec["file"]):
                    rec["token"] = True
                    self._save_record()
        with self.lock:
            self.waiting = waiting

    # ── one file ────────────────────────────────────────────────────

    def _target(self, item, size):
        """Where this file goes, or (path, True) when it is already there."""
        name = NAME_OK.sub("_", item["id"].split("/")[-1]).strip() or "file"
        path = self.folder / name
        if not path.exists():
            return path, False
        if size is not None and path.stat().st_size == size:
            return path, True                            # the same file, saved before
        stem, ext = os.path.splitext(name)
        for n in range(1, 1000):
            alt = self.folder / ("%s_%s%s%s" % (stem, self.pod_id[:6],
                                                "" if n == 1 else "_%d" % n, ext))
            if not alt.exists():
                return alt, False
            if size is not None and alt.stat().st_size == size:
                return alt, True
        raise OSError("No free name for %s" % name)

    def _save(self, item) -> str:
        key = item["id"]
        try:
            res = self._open("GET", "/media/" + quote(key), timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:                          # deleted on the pod meanwhile
                return "gone"
            raise
        with res:
            cache = (res.headers.get("Cache-Control") or "").lower()
            if "no-store" in cache:                      # still being written
                return "waiting"
            size = res.headers.get("Content-Length")
            size = int(size) if size and size.isdigit() else None
            path, there = self._target(item, size)
            if there:
                with self.lock:
                    self.already += 1
                self._remember(key, path, size, token=None)
                if not path.with_suffix(".txt").exists():
                    ok = self._write_token(item, path)
                    self.record["files"][key]["token"] = ok
                    self._save_record()
                return "already"
            part = path.with_name(path.name + ".part")
            done, t0 = 0, time.time()
            with self.lock:
                self.current = {"name": path.name, "done": 0, "total": size, "speed": None}
            try:
                with open(part, "wb") as out:
                    while True:
                        if self.stop_event.is_set():
                            raise InterruptedError
                        chunk = res.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        with self.lock:
                            self.bytes += len(chunk)
                            self.current.update(done=done, speed=done / max(0.001, time.time() - t0))
                if size is not None and done != size:
                    raise OSError("got %d of %d bytes" % (done, size))
                os.replace(part, path)
            except BaseException:
                part.unlink(missing_ok=True)
                with self.lock:
                    self.current = None
                    if not self.stop_event.is_set():
                        self.failed += 1
                if self.stop_event.is_set():
                    return "stopped"
                raise
        token = self._write_token(item, path)
        self._remember(key, path, done, token)
        with self.lock:
            self.saved += 1
            self.current = None
            self.last_saved = {"name": path.name, "at": time.time(), "bytes": done,
                               "seed": item.get("seed")}
        return "saved"

    def _remember(self, key, path, size, token) -> None:
        self.record["files"][key] = {"file": path.name, "size": size, "token": token,
                                     "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._save_record()

    def _write_token(self, item, path) -> bool:
        """<name>.txt beside the file. True if written; None if the file has
        no recipe; False if it failed (retried later)."""
        tab = item.get("tab")
        if not tab:
            return None
        self.token_tries[item["id"]] = self.token_tries.get(item["id"], 0) + 1
        try:
            if tab not in self.schemas:
                self.schemas = {s["key"]: s for s in self._json("GET", "/schemas")}
            schema = self.schemas.get(tab)
            if schema is None:
                return None
            applied = self._json("POST", "/schema/%s/apply" % quote(tab),
                                 {"recipe": item["id"]})
            token = ember_token.build_token(schema, applied)
            if token is None:
                return None
            txt = path.with_suffix(".txt")
            txt.write_text(token + "\n", encoding="utf-8")
            with self.lock:
                self.tokens += 1
            return True
        except (NeedsKey, Unreachable):
            raise
        except Exception as exc:
            self.event("No token for %s yet (%s)." % (path.name, exc))
            return False
