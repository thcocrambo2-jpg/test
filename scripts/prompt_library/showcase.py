#!/usr/bin/env python3
"""The showcase: the library's public pictures, for a client to look at.

    python scripts/prompt_library/showcase.py [--port 7891] [--code 4821]

Runs on your laptop, only while you are showing it. In person, open
http://127.0.0.1:7891. For someone remote, put a free quick tunnel in
front of it and send them the link and the code:

    cloudflared tunnel --url http://127.0.0.1:7891

Stop both when the demo is over and the link is dead.

What a client can and cannot get from it:

  * Only entries switched **on and public** in the admin page.
  * **Never a token or a prompt.** The entries are trimmed to a picture,
    a tab name, a title, a category and tags before they leave this process, so nothing a
    browser could inspect carries a recipe — and the pictures themselves
    are metadata-free WebPs (store.py).
  * The pictures load straight from R2 through signed links that last
    six hours (store.URL_TTL_SECONDS), so a remote client is not limited
    by this laptop's upload speed. A link opens that one picture, cannot
    list anything, and simply stops working when it expires.

The page asks for an access code first: the one given with --code, or a
fresh six-digit one printed at start-up. It holds the R2 *read* key only.
"""

from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import categories
import store

HERE = Path(__file__).resolve().parent
INDEX_TTL = 60                  # seconds; a toggle in the admin page shows
                                # up here within a minute
MAX_WRONG = 10                  # wrong codes before the page stops asking
COOKIE = "showcase"


class Showcase:
    def __init__(self, code: str) -> None:
        self.code = code
        self.sessions: set[str] = set()
        self.wrong = 0
        self.lock = threading.Lock()
        self.cached: tuple[float, list[dict]] | None = None

    def entries(self) -> list[dict]:
        with self.lock:
            if self.cached and time.time() - self.cached[0] < INDEX_TTL:
                return self.cached[1]
        rows, _ = store.load_index()
        labels = store.tab_labels()
        category_labels = dict(categories.CATEGORIES)
        tag_labels = dict(categories.TAGS)
        shown = []
        for entry in rows:
            if not (entry.get("enabled") and entry.get("public")):
                continue
            thumb = store.media(entry, "thumb")
            full = store.media(entry, "full")
            if not thumb or not full:
                continue
            # Allow-listed, field by field. `token` and `token_sha` are
            # never copied, so no later change to the index can leak one.
            shown.append({
                "id": entry.get("id"),
                "tab": labels.get(entry.get("tab", ""), ""),
                "title": entry.get("title") or "",
                "category": category_labels.get(entry.get("category") or "", ""),
                "tags": [tag_labels[tag] for tag in entry.get("tags") or []
                         if tag in tag_labels],
                "width": entry.get("width"),
                "height": entry.get("height"),
                "thumb": store.signed_url(thumb["key"]),
                "full": store.signed_url(full["key"]),
            })
        with self.lock:
            self.cached = (time.time(), shown)
        return shown


class Handler(BaseHTTPRequestHandler):
    showcase: Showcase

    def log_message(self, *_args) -> None:
        pass

    def send(self, code: int, body: bytes, ctype: str,
             extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("x-robots-tag", "noindex, nofollow")
        self.send_header("referrer-policy", "no-referrer")
        self.send_header("x-frame-options", "DENY")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def signed_in(self) -> bool:
        for part in (self.headers.get("cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value in self.showcase.sessions:
                return True
        return False

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/":
            page = "showcase.html" if self.signed_in() else "showcase-code.html"
            self.send(200, (HERE / page).read_bytes(),
                      "text/html; charset=utf-8")
        elif path == "/api/entries":
            if not self.signed_in():
                self.send(401, b'{"error":"code needed"}', "application/json")
                return
            try:
                body = {"entries": self.showcase.entries()}
            except store.StoreError as exc:
                body = {"entries": [], "error": str(exc)}
            self.send(200, json.dumps(body).encode("utf-8"),
                      "application/json")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if unquote(urlparse(self.path).path) != "/api/code":
            self.send(404, b"not found", "text/plain")
            return
        length = min(int(self.headers.get("content-length") or 0), 1024)
        try:
            code = str(json.loads(self.rfile.read(length) or b"{}").get("code", ""))
        except ValueError:
            code = ""
        showcase = self.showcase
        with showcase.lock:
            locked = showcase.wrong >= MAX_WRONG
            right = not locked and secrets.compare_digest(
                code.strip().encode(), showcase.code.encode())
            if not right and not locked:
                showcase.wrong += 1
        if right:
            session = secrets.token_urlsafe(24)
            showcase.sessions.add(session)
            self.send(200, b'{"ok":true}', "application/json", {
                "set-cookie": f"{COOKIE}={session}; Path=/; HttpOnly; "
                              "SameSite=Strict; Max-Age=43200"})
            return
        time.sleep(1)
        message = ("Too many wrong codes. Restart the showcase for a new one."
                   if locked else "That code is not right.")
        self.send(403, json.dumps({"error": message}).encode("utf-8"),
                  "application/json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7891)
    parser.add_argument("--code", help="access code (default: a random one)")
    args = parser.parse_args()

    store.load_credentials(write=False)
    code = (args.code or f"{secrets.randbelow(10 ** 6):06d}").strip()
    Handler.showcase = Showcase(code)
    count = len(Handler.showcase.entries())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Showcase: http://127.0.0.1:{args.port}   code {code}   "
          f"({count} pictures)\n"
          f"Remote client: cloudflared tunnel --url http://127.0.0.1:{args.port}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
