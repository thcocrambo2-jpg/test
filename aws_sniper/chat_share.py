"""A Cloudflare link for the Chat page only, e.g. to use it from a phone.

    python aws_sniper/chat_share.py [--port 7897] [--sniper 7896] [--no-tunnel]
    python aws_sniper/server.py --share-chat      (starts this along with the server)

The sniper server (server.py) must be running. This starts a small gateway
on 127.0.0.1:<port> that passes on only what the Chat page needs:

    GET  /chat, /tokens.css, /api/chat..., /api/config, /api/status
    POST /api/chat/...

Everything else (launching, stopping, restarting instances, the other tabs)
answers 404, so the link can't do more than the chat. /api/status goes out
without each pod's app key (the #k= part of its link) or IP: the chat only
needs to know which pods are running, and the sniper adds the key itself
when it queues. It then runs
`cloudflared tunnel --url http://127.0.0.1:<port>` and prints the link,
https://<random>.trycloudflare.com/chat. There is no sign-in: anyone with
the link can use the chat, and the link changes each time this starts.
cloudflared comes from --cloudflared, PATH, or the repo's
tmp/cloudflared.exe (winget install Cloudflare.cloudflared puts it on PATH).
"""

import argparse
import http.client
import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO_CLOUDFLARED = HERE.parent / "tmp" / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")
GET_OK = re.compile(r"^/(chat|tokens\.css|api/config|api/status|api/chat(/[a-z0-9/_-]*)?)$")
POST_OK = re.compile(r"^/api/chat/[a-z0-9/_-]+$")
PASS_BACK = ("content-type", "cache-control", "x-content-type-options", "content-length",
             "last-modified", "etag")
MAX_BODY = 23_000_000                   # the sniper's own limit for uploads is 22 MB

SNIPER = ("127.0.0.1", 7896)


class Gate(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"       # each reply ends by closing: streamed chat replies pass as they come
    server_version = "chat-share"

    def log_message(self, fmt, *args):  # quiet
        pass

    def deny(self, code=404, text="Not found."):
        body = text.encode()
        self.send_response(code)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_response(303)
            self.send_header("location", "/chat")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if not GET_OK.match(path):
            return self.deny()
        self.forward("GET", None)

    def do_POST(self):
        path = urlparse(self.path).path
        # The page's fetch() sends x-sniper: 1; a form on another site can't.
        if not POST_OK.match(path) or self.headers.get("x-sniper") != "1":
            return self.deny()
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            return self.deny(413, "Too big.")
        self.forward("POST", self.rfile.read(length))

    def forward(self, method, body):
        conn = http.client.HTTPConnection(*SNIPER, timeout=600)
        headers = {"host": "%s:%d" % SNIPER}
        for k in ("content-type", "accept", "if-none-match", "if-modified-since"):
            if self.headers.get(k):
                headers[k] = self.headers[k]
        if method == "POST":
            headers["x-sniper"] = "1"
            headers["content-length"] = str(len(body))
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError:
            conn.close()
            return self.deny(502, "The sniper server isn't running on port %d." % SNIPER[1])
        if urlparse(self.path).path == "/api/status" and resp.status == 200:
            return self.send_status(resp, conn)
        try:
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in PASS_BACK:
                    self.send_header(k, v)
            self.send_header("x-accel-buffering", "no")
            self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass                                 # the page went away (Stop): closing below stops the reply
        finally:
            conn.close()

    def send_status(self, resp, conn):
        try:
            data = json.loads(resp.read())
        except (OSError, ValueError):
            conn.close()
            return self.deny(502, "Bad answer from the sniper.")
        conn.close()
        for row in data.get("instances") or []:
            if row.get("url"):
                row["url"] = row["url"].split("#", 1)[0]
            for k in ("ip", "host", "private_ip"):
                row.pop(k, None)
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def find_cloudflared(given: str = None):
    for c in (given, shutil.which("cloudflared"), str(REPO_CLOUDFLARED) if REPO_CLOUDFLARED.is_file() else None):
        if c and Path(c).is_file():
            return c
    return None


def tunnel(port: int, given: str = None):
    exe = find_cloudflared(given)
    if not exe:
        print("No cloudflared: install it (winget install Cloudflare.cloudflared), put it at %s, "
              "or pass --cloudflared PATH. The gateway runs anyway on port %d." % (REPO_CLOUDFLARED, port), flush=True)
        return None
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")

    def watch():
        shown = False
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not shown:
                shown = True
                print("\nChat link:\n    %s/chat\n" % m.group(0), flush=True)
            elif "ERR" in line and not shown:
                print("cloudflared: " + line.strip(), flush=True)
        print("cloudflared stopped (exit %s)." % proc.wait(), flush=True)
    threading.Thread(target=watch, daemon=True).start()
    return proc


def main() -> None:
    global SNIPER
    ap = argparse.ArgumentParser(description="Cloudflare link for the sniper's Chat page only")
    ap.add_argument("--port", type=int, default=7897, help="gateway port (default 7897)")
    ap.add_argument("--sniper", type=int, default=7896, help="the sniper server's port (default 7896)")
    ap.add_argument("--no-tunnel", action="store_true", help="only the gateway, no cloudflared")
    ap.add_argument("--cloudflared", help="path to cloudflared(.exe)")
    a = ap.parse_args()
    SNIPER = ("127.0.0.1", a.sniper)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Gate)
    server.daemon_threads = True
    print("Chat gateway on http://127.0.0.1:%d -> sniper on %d (chat routes only)." % (a.port, a.sniper), flush=True)
    proc = None if a.no_tunnel else tunnel(a.port, a.cloudflared)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
