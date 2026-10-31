#!/usr/bin/env python3
"""The prompt library's admin page, on http://127.0.0.1:7890.

    python scripts/prompt_library/admin.py [--port 7890]

Run it on your own machine, open the page, close it when done. It holds
the R2 *write* key from license-validator/.env, so it answers only to
this machine: it binds 127.0.0.1 and refuses any request whose Host is
not localhost — a tunnel pointed at it by mistake gets a 403, not an
upload form.

What it does, all against R2 (see store.py — there is no other copy):

  * shows every entry, enabled or not, with its picture;
  * switches an entry on for customers (`enabled`) and on the showcase
    (`public`), renames it, reorders the list, deletes it;
  * uploads: pictures paired with their settings tokens, one at a time,
    from a whole folder, or from a Gallery zip. A pair is a picture and a
    .txt with the same name — `Krea_00012_.png` + `Krea_00012_.txt`, which
    is exactly what Gallery's zip and auto-download write.

A new upload starts **disabled and not public**. Nothing reaches a
customer until you switch it on here.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

import store

HERE = Path(__file__).resolve().parent
MAX_BODY = 512 * 1024 * 1024        # a Gallery zip of a few hundred pictures


class Library:
    """The index in memory, and the one lock every change takes.

    Pictures are encoded and uploaded *outside* the lock, so a long batch
    does not freeze the toggles; only adding the finished entry to the
    index waits for it. Every change saves the index straight away — an
    upload batch interrupted halfway keeps everything it finished.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entries: list[dict] = []
        self.etag: str | None = None
        self.reload()

    def reload(self) -> None:
        with self.lock:
            self.entries, self.etag = store.load_index()

    def _save(self) -> None:
        try:
            self.etag = store.save_index(self.entries, self.etag)
        except store.Conflict:
            self.entries, self.etag = store.load_index()
            raise

    def find(self, entry_id: str) -> dict:
        for entry in self.entries:
            if entry.get("id") == entry_id:
                return entry
        raise store.StoreError("No such entry (reload the page).")

    def update(self, ids: list[str], changes: dict) -> list[dict]:
        """Apply the same switches to every id, in one save."""
        with self.lock:
            entries = [self.find(entry_id) for entry_id in ids]
            for entry in entries:
                for name in ("enabled", "public"):
                    if name in changes:
                        entry[name] = bool(changes[name])
                if "title" in changes:
                    entry["title"] = str(changes["title"] or "").strip()[:120]
            self._save()
            return entries

    def reorder(self, ids: list[str]) -> None:
        with self.lock:
            by_id = {entry.get("id"): entry for entry in self.entries}
            if sorted(ids) != sorted(by_id):
                raise store.StoreError("The list changed while you were "
                                       "dragging (reload the page).")
            self.entries = [by_id[entry_id] for entry_id in ids]
            self._save()

    def delete(self, ids: list[str]) -> None:
        """Take entries out of the index, then delete their pictures.

        Index first: if a picture delete fails afterwards, what is left is
        an unreferenced object nobody can reach, never an entry whose
        picture is gone.
        """
        with self.lock:
            doomed = [self.find(entry_id) for entry_id in ids]
            self.entries = [row for row in self.entries
                            if all(row is not entry for entry in doomed)]
            self._save()
        for entry in doomed:
            for item in entry.get("media") or []:
                if isinstance(item, dict) and item.get("key"):
                    store.delete_object(item["key"])

    def add(self, picture: bytes, token_text: str, title: str = "") -> dict:
        """One picture + token in: checked, cleaned, uploaded, indexed."""
        token, data = store.read_token(token_text)
        sha = store.token_sha(token)
        with self.lock:
            if any(entry.get("token_sha") == sha for entry in self.entries):
                return {"skipped": "already in the library"}
        encoded = store.pictures(picture)
        entry_id = store.new_id()
        entry_media = []
        for role in ("thumb", "full"):
            data_bytes, width, height = encoded[role]
            key = f"{store.PREFIX}/{entry_id}/{role}.webp"
            store.put_object(key, data_bytes, "image/webp")
            entry_media.append({"kind": "image", "role": role, "key": key,
                                "width": width, "height": height,
                                "bytes": len(data_bytes)})
        full = encoded["full"]
        entry = {
            "id": entry_id,
            "tab": data["tab"],
            "title": title.strip()[:120],
            "token": token,
            "token_sha": sha,
            "enabled": False,
            "public": False,
            "created_at": store.now_iso(),
            "width": full[1],
            "height": full[2],
            "media": entry_media,
        }
        with self.lock:
            # Checked again: a second copy of this token may have finished
            # uploading while this one was encoding.
            if any(row.get("token_sha") == sha for row in self.entries):
                for item in entry_media:
                    store.delete_object(item["key"])
                return {"skipped": "already in the library"}
            self.entries.insert(0, entry)
            try:
                self._save()
            except store.StoreError:
                for item in entry_media:
                    store.delete_object(item["key"])
                raise
        return {"entry": entry}


def pairs_from_zip(raw: bytes) -> tuple[list[tuple[str, bytes, str]], list[str]]:
    """(name, picture, token text) for every pair in a zip, and the leftovers.

    Paired by path minus extension, so a folder inside the zip pairs within
    itself. A picture with no .txt beside it, or a .txt with no picture, is
    reported rather than guessed at.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise store.StoreError(f"Not a zip this can open ({exc}).") from exc
    pictures: dict[str, tuple[str, bytes]] = {}
    tokens: dict[str, str] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename)
        if any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
            continue
        stem = str(path.with_suffix("")).lower()
        suffix = path.suffix.lower()
        if suffix in store.IMAGE_SUFFIXES:
            pictures[stem] = (path.name, archive.read(info))
        elif suffix == ".txt":
            tokens[stem] = archive.read(info).decode("utf-8", "replace")
    found = [(pictures[stem][0], pictures[stem][1], tokens[stem])
             for stem in sorted(pictures) if stem in tokens]
    alone = sorted([pictures[stem][0] + " (no token)"
                    for stem in pictures if stem not in tokens]
                   + [stem + ".txt (no picture)"
                      for stem in tokens if stem not in pictures])
    return found, alone


def wire(entry: dict, labels: dict[str, str]) -> dict:
    """An entry as the page reads it, with signed picture links."""
    thumb = store.media(entry, "thumb")
    full = store.media(entry, "full")
    return {
        **{name: entry.get(name) for name in (
            "id", "tab", "title", "token", "enabled", "public", "created_at",
            "width", "height")},
        "tabLabel": labels.get(entry.get("tab", ""), entry.get("tab", "")),
        "thumb": store.signed_url(thumb["key"]) if thumb else None,
        "full": store.signed_url(full["key"]) if full else None,
    }


class Handler(BaseHTTPRequestHandler):
    library: Library
    labels: dict[str, str]
    port: int

    def log_message(self, *_args) -> None:
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, code: int, payload: dict) -> None:
        self.send(code, json.dumps(payload).encode("utf-8"),
                  "application/json")

    def local(self) -> bool:
        """Only a browser on this machine, addressed as localhost.

        The Host check is what stops a tunnel or a DNS-rebinding page: both
        reach this socket with someone else's hostname in the header.
        """
        host = (self.headers.get("host") or "").lower()
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if host not in allowed or self.headers.get("cf-connecting-ip"):
            self.send(403, b"This page only answers on this machine.",
                      "text/plain")
            return False
        return True

    def body(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            raise store.StoreError("That upload is too large for one request.")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        if not self.local():
            return
        path = unquote(urlparse(self.path).path)
        if path == "/":
            self.send(200, (HERE / "admin.html").read_bytes(),
                      "text/html; charset=utf-8")
        elif path == "/api/state":
            if "reload" in (urlparse(self.path).query or ""):
                self.library.reload()
            with self.library.lock:
                entries = [wire(entry, self.labels)
                           for entry in self.library.entries]
            self.json(200, {"entries": entries, "labels": self.labels})
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if not self.local():
            return
        # A JSON POST from another site cannot set this header without a
        # preflight this server never answers, so it marks our own page.
        if self.headers.get("x-library-admin") != "1":
            self.send(403, b"missing header", "text/plain")
            return
        path = unquote(urlparse(self.path).path)
        try:
            raw = self.body()
            if path == "/api/upload-zip":
                found, alone = pairs_from_zip(raw)
                results = []
                for name, picture, token in found:
                    results.append(self.one(name, picture, token, ""))
                self.json(200, {"results": results, "unpaired": alone})
                return
            body = json.loads(raw.decode("utf-8") or "{}")
            if path == "/api/upload":
                picture = base64.b64decode(body.get("image") or "")
                self.json(200, self.one(str(body.get("name") or "picture"),
                                        picture, str(body.get("token") or ""),
                                        str(body.get("title") or "")))
            elif path == "/api/update":
                entries = self.library.update(
                    [str(value) for value in body.get("ids") or []],
                    body.get("changes") or {})
                self.json(200, {"entries": [wire(entry, self.labels)
                                            for entry in entries]})
            elif path == "/api/delete":
                self.library.delete([str(value) for value in body.get("ids") or []])
                self.json(200, {"ok": True})
            elif path == "/api/order":
                self.library.reorder([str(value) for value in body.get("ids") or []])
                self.json(200, {"ok": True})
            else:
                self.send(404, b"not found", "text/plain")
        except store.StoreError as exc:
            self.json(409 if isinstance(exc, store.Conflict) else 400,
                      {"error": str(exc)})
        except ValueError as exc:
            self.json(400, {"error": str(exc)})

    def one(self, name: str, picture: bytes, token: str, title: str) -> dict:
        """One upload's outcome, never an exception: a batch keeps going."""
        try:
            outcome = self.library.add(picture, token, title)
        except (store.StoreError, ValueError) as exc:
            return {"name": name, "error": str(exc)}
        if "skipped" in outcome:
            return {"name": name, "skipped": outcome["skipped"]}
        return {"name": name, "entry": wire(outcome["entry"], self.labels)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7890)
    args = parser.parse_args()

    bucket = store.load_credentials(write=True)
    Handler.library = Library()
    Handler.labels = store.tab_labels()
    Handler.port = args.port
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Prompt library admin: http://127.0.0.1:{args.port}  "
          f"(r2://{bucket}/{store.PREFIX}, "
          f"{len(Handler.library.entries)} entries)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
