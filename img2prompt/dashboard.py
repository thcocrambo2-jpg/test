"""The img2prompt progress page, on http://127.0.0.1:7880.

    python img2prompt/dashboard.py [--port 7880]

Read-only. Serves dashboard.html, a run's state.json (the newest, or
?run=<name>) plus the live config.json, the list of runs, and the files
under runs/. /i2v lists a run's image-to-video prompts (each image
folder's i2v.json). Every number on the page comes from state.json, which only
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
        elif path == "/i2v":
            self.send(200, (HERE / "i2v.html").read_bytes(),
                      "text/html; charset=utf-8")
        elif path == "/api/i2v":
            run = parse_qs(url.query).get("run", [None])[0]
            self.send(200, json.dumps(self.i2v(run)).encode("utf-8"),
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

    def i2v(self, run: str | None = None) -> dict:
        """The i2v.json prompts of `run`, or of the newest run that has any.

        Each card shows the image the prompt was written for: the i2v.json
        "source" when it names one, else the run's best render.
        """
        runs = [d for d in sorted(RUNS.iterdir(), reverse=True)
                if d.is_dir() and any(d.glob("*/i2v.json"))]
        names = [d.name for d in runs]
        if run not in names:
            run = names[0] if names else None
        if run is None:
            return {"run": None, "runs": names, "items": []}
        try:
            state = json.loads((RUNS / run / "state.json").read_text("utf-8"))
        except (OSError, ValueError):
            state = {"images": []}
        best = {r["dir"]: r["best"]["render"]
                for r in state["images"] if r.get("best")}
        order = [r["dir"] for r in state["images"]]
        items = []
        for f in (RUNS / run).glob("*/i2v.json"):
            try:
                data = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            d = f.parent.name
            image = data.get("source") or best.get(d) or "reference.png"
            items.append({"dir": d, "image": "%s/%s/%s" % (run, d, image),
                          "kind": data.get("kind", ""),
                          "idea": data.get("idea", ""),
                          "prompt": data.get("prompt", "")})
        items.sort(key=lambda i: order.index(i["dir"])
                   if i["dir"] in order else len(order))
        return {"run": run, "runs": names, "items": items}

    def file(self, rel: str) -> None:
        target = (RUNS / rel).resolve()
        if RUNS.resolve() not in target.parents or not target.is_file():
            self.send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/"):  # may still be rewritten
            self.send(200, target.read_bytes(), ctype)
            return
        # An image is revalidated on every request: a sweep can put a new
        # render under an old name, so a long max-age showed stale pictures.
        # An unchanged file costs only a 304, so the 2-second redraw stays
        # cheap.
        st = target.stat()
        etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
        if self.headers.get("if-none-match") == etag:
            self.send_response(304)
            self.send_header("etag", etag)
            self.send_header("cache-control", "no-cache")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-cache")
        self.send_header("etag", etag)
        body = target.read_bytes()
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
