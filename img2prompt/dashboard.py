"""The img2prompt progress page, on http://127.0.0.1:7880.

    python img2prompt/dashboard.py [--port 7880]

Read-only. Serves dashboard.html, a run's state.json (the newest, or
?run=<name>) plus the live config.json, the list of runs, and the files
under runs/. Every number on the page comes from state.json, which only
run.py writes.
"""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # the page polls every 2 s
        pass

    def send(self, code: int, body: bytes, ctype: str,
             cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", cache)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == "/":
            self.send(200, (HERE / "dashboard.html").read_bytes(),
                      "text/html; charset=utf-8")
        elif path == "/api/state":
            run = parse_qs(url.query).get("run", [None])[0]
            self.send(200, json.dumps(self.state(run)).encode("utf-8"),
                      "application/json")
        elif path == "/api/runs":
            self.send(200, json.dumps(self.runs()).encode("utf-8"),
                      "application/json")
        elif path.startswith("/runs/"):
            self.file(path.removeprefix("/runs/"))
        else:
            self.send(404, b"not found", "text/plain")

    def runs(self) -> list:
        """Every run, newest first, for the history section's picker."""
        rows = []
        for sdir in sorted(RUNS.iterdir(), reverse=True):
            try:
                state = json.loads((sdir / "state.json").read_text("utf-8"))
            except (OSError, ValueError):
                continue
            rows.append({"run": sdir.name, "folder": state["folder"],
                         "tab": state.get("tab", "krea_v2_t2i"),
                         "images": len(state["images"]),
                         "closed": sum(r["status"] not in ("pending", "running")
                                       for r in state["images"])})
        return rows

    def state(self, run: str | None = None) -> dict:
        """The newest run, or `run` when it names one of the run folders."""
        config = json.loads((HERE / "config.json").read_text("utf-8"))
        latest = RUNS / "latest.txt"
        if run and (RUNS / run / "state.json").is_file() \
                and RUNS.resolve() in (RUNS / run).resolve().parents:
            name = run
        elif latest.exists():
            name = latest.read_text("utf-8").strip()
        else:
            return {"run": None, "config": config}
        try:
            state = json.loads((RUNS / name / "state.json").read_text("utf-8"))
        except (OSError, ValueError):
            return {"run": None, "config": config}
        return {"run": name, "config": config, "state": state}

    def file(self, rel: str) -> None:
        target = (RUNS / rel).resolve()
        if RUNS.resolve() not in target.parents or not target.is_file():
            self.send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        # Renders and references never change once written, so the page's
        # 2-second redraw costs nothing; a text file may still be rewritten.
        cache = "no-store" if ctype.startswith("text/") else "max-age=86400"
        self.send(200, target.read_bytes(), ctype, cache)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7880)
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("img2prompt dashboard on http://127.0.0.1:%d" % args.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
