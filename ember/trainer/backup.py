"""Back up a run's checkpoints to the licence server as musubi saves them.

A run trained with the consent tick sends every checkpoint it saves
(<name>-step00000200.safetensors, …) to this licence's checkpoint backups
— ember.trainer.publish.backup_checkpoint — so a pod terminated mid-run
loses only the steps since the last save. The 🎭 page's Recover fetches one
back on any pod that runs this licence.

The queue job polls scan() once a second while it trains, and once more
with final=True when training ends, however it ends. A file is sent only
once it is whole (its safetensors header accounts for every byte) and has
not changed for SETTLE seconds, so a checkpoint musubi is still writing,
or one cut short by a stop, is never sent. One thread sends them in order;
each is tried ATTEMPTS times before it is marked failed. A backup never
fails or slows the run itself.

Progress lives in run.state["backups"] — {file: {status, sent, total,
note}} — so the page shows it and it survives in state.json.
"""

import json
import queue
import struct
import threading
import time
from pathlib import Path

from ember.logs import log
from ember.trainer import publish

SETTLE = 5          # seconds a file must sit unchanged before it is sent
ATTEMPTS = 3
RETRY_WAIT = 30     # seconds, times the attempt number


def complete(path: Path) -> bool:
    """True when `path` is a whole safetensors file: an 8-byte header
    length, the JSON header, then exactly the bytes its tensors claim."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            (length,) = struct.unpack("<Q", handle.read(8))
            if length <= 0 or 8 + length > size:
                return False
            header = json.loads(handle.read(length))
    except (OSError, ValueError, struct.error):
        return False
    end = max((t["data_offsets"][1] for k, t in header.items()
               if k != "__metadata__"), default=0)
    return size == 8 + length + end


class Backup:
    """The checkpoint backups of one run."""

    def __init__(self, run):
        self.run = run
        self.trigger = str(run.options.get("trigger") or "")
        self.queued: set = set()
        self.seen: dict = {}            # file -> (size, mtime) at last scan
        self.todo: queue.Queue = queue.Queue()
        with run.lock:
            run.state.setdefault("backups", {})
        self.thread = threading.Thread(target=self._send_all, daemon=True)
        self.thread.start()

    def _set(self, file: str, save: bool = True, **fields) -> None:
        with self.run.lock:
            self.run.state["backups"].setdefault(file, {}).update(fields)
        if save:
            self.run.save()

    def scan(self, final: bool = False) -> None:
        """Queue every checkpoint that is whole and settled. `final`: the
        trainer has exited, so nothing is still being written."""
        out = self.run.dir / "output"
        now = time.time()
        for path in sorted(out.glob(self.run.name + "-step*.safetensors")):
            if path.name in self.queued:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            mark = (stat.st_size, stat.st_mtime)
            settled = final or (self.seen.get(path.name) == mark
                                and now - stat.st_mtime >= SETTLE)
            self.seen[path.name] = mark
            if not settled or not complete(path):
                continue
            self.queued.add(path.name)
            self._set(path.name, status="queued", sent=0, total=stat.st_size, note=None)
            self.todo.put(path)

    def close(self) -> None:
        """No more checkpoints: the thread ends once the queue is sent."""
        self.todo.put(None)

    def _send_all(self) -> None:
        while True:
            path = self.todo.get()
            if path is None:
                return
            self._send(path)

    def _send(self, path: Path) -> None:
        def step(what, sent=0, total=0):
            # Progress only; state.json is written on the next status change.
            self._set(path.name, save=False, status="sending", sent=sent,
                      total=total or path.stat().st_size)

        for attempt in range(1, ATTEMPTS + 1):
            try:
                note = publish.backup_checkpoint(path, self.run.name, self.trigger, step)
                self._set(path.name, status="done", note=note)
                return
            except Exception as exc:        # never a URL: see publish.py
                reason = (str(exc) if isinstance(exc, publish.PublishError)
                          else type(exc).__name__)
                if attempt == ATTEMPTS:
                    log.warning("Face LoRA backup of %s failed: %s", path.name, reason)
                    self._set(path.name, status="failed", note=reason)
                    return
                self._set(path.name, status="retrying",
                          note="%s — trying again (%d/%d)" % (reason, attempt + 1, ATTEMPTS))
                time.sleep(RETRY_WAIT * attempt)
