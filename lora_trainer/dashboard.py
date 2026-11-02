"""The face-LoRA trainer page, on http://0.0.0.0:8888.

    python3 lora_trainer/dashboard.py [--port 8888] [--dry-run]

Run it on the pod, beside the running app, from a checkout of this repo
— the app's own python3, which already has torch and huggingface_hub.
On RunPod the page is https://<pod id>-8888.proxy.runpod.net.

Upload a few photos of one face, press Train, and watch the phases go by
(trainer.py). When it finishes the LoRA is copied into the app's loras/
folder, and the Render box makes pictures with it on one of the 🔶 Krea2
V2 presets through the app's ComfyUI (render.py). One training run at a
time; rendering waits for it.

--dry-run fakes every training command and the render, so the page can
be exercised off a GPU.

Training starts from the app's own Krea 2 Raw. HF_TOKEN matters only if
musubi refuses that and the official Raw has to be downloaded instead.
"""

import argparse
import json
import mimetypes
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import publish
import render
import trainer
from ember import settings

HERE = Path(__file__).resolve().parent
ROOT = settings.BASE_DIR / "trainer"
RUNS = ROOT / "runs"
RENDERS = ROOT / "renders"
MODELS = settings.MODELS_DIR
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,150}$")
MAX_UPLOAD = 40 * 1024 * 1024

DRY_RUN = False
_active: trainer.Run | None = None
_render = {"status": "idle", "done": 0, "total": 0, "images": [],
           "error": None, "started_at": None, "seconds_per_image": None}
_lock = threading.Lock()
# The last Publish: its steps, for the page. Never holds the R2 key or the
# admin token — those live only in the job's own arguments.
_publish = {"status": "idle", "step": None, "sent": 0, "total": 0,
            "done": [], "error": None, "lora": None}


def _runs() -> list:
    """Every run on disk, newest first, as its state.json says."""
    found = []
    for path in RUNS.glob("*/state.json"):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    found.sort(key=lambda s: s.get("started_at") or 0, reverse=True)
    return found


def _face_loras() -> list:
    return sorted(p.name for p in (MODELS / "loras").glob("face_*.safetensors"))


def _training() -> bool:
    return _active is not None and _active.state["status"] == "running"


def _app_busy() -> str | None:
    """Why training must not start now, or None.

    Training takes the whole GPU, and the app's queue would keep feeding
    ComfyUI underneath it — two jobs on 32 GB, both crawling or one
    dying. So it waits until the app has nothing running or waiting.
    """
    try:
        queue = render._call("GET", render.APP_API + "/queue", timeout=5)
    except Exception:
        return None                    # no app answering: nothing to fight
    running, waiting = queue.get("running") or 0, queue.get("waiting") or 0
    if running or waiting:
        return ("The app is busy — %d job(s) running and %d waiting in its "
                "queue. Training needs the whole GPU, so start it once the "
                "queue is empty." % (running, waiting))
    try:
        comfy = render._call("GET", render.COMFY + "/queue", timeout=5)
    except Exception:
        return None
    if comfy.get("queue_running") or comfy.get("queue_pending"):
        return "ComfyUI is still working on something — try again when it is idle."
    return None


def _publish_job(job: dict) -> None:
    """Upload, register, grant — publish.py does the work, this keeps score."""
    def step(name, sent=0, total=0):
        with _lock:
            _publish.update(step=name, sent=sent, total=total)

    def done(line):
        with _lock:
            _publish["done"].append(line)

    try:
        if DRY_RUN:
            size = 50_000_000
            for sent in range(0, size + 1, 5_000_000):
                step("uploading", sent, size)
                time.sleep(0.3)
            done("uploaded 50.0 MB (dry run)")
            done("created %s (dry run)" % job["id"])
            if job["keys"]:
                done("granted to %d key(s) (dry run)" % len(job["keys"]))
        else:
            done(publish.upload(MODELS / "loras" / job["file"], job["r2"], step))
            step("registering")
            done(publish.register(job["api"], {
                "id": job["id"], "file": job["file"], "name": job["name"],
                "trigger": job["trigger"], "default_strength": job["strength"]}))
            if job["keys"]:
                step("granting")
                done(publish.grant(job["api"], job["id"], job["keys"]))
            else:
                done("not granted to anyone yet — publish again with keys")
        with _lock:
            _publish.update(status="done", step=None)
    except publish.PublishError as exc:
        with _lock:
            _publish.update(status="failed", error=str(exc))
    except Exception as exc:              # never the URL: see publish.py
        with _lock:
            _publish.update(status="failed",
                            error="%s: %s" % (type(exc).__name__, exc))
    finally:
        job["r2"] = job["api"] = None     # drop the secrets with the job


def _render_job(body: dict) -> None:
    lora = body["lora"]
    started = time.time()

    def progress(done, total):
        with _lock:
            _render.update(done=done, total=total)
            if done:
                _render["seconds_per_image"] = (time.time() - started) / done

    try:
        if DRY_RUN:
            made = []
            for i in range(body["count"]):
                progress(i, body["count"])
                time.sleep(2)
                made.append((i, b""))
            progress(body["count"], body["count"])
        else:
            made = render.render(
                preset=body["preset"], prompt=body["prompt"],
                negative=body.get("negative"), seed=body.get("seed"),
                count=body["count"], face_lora=lora,
                strength=body["strength"], trigger=body.get("trigger", ""),
                on_progress=progress)
        out = RENDERS / Path(lora).stem
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        urls = []
        for seed, png in made:
            name = "%s_%s.png" % (stamp, seed)
            (out / name).write_bytes(png)
            (out / (name[:-4] + ".json")).write_text(json.dumps(
                {**body, "seed": seed}, indent=1), encoding="utf-8")
            urls.append("/renders/%s/%s" % (out.name, name))
        with _lock:
            _render.update(status="done", images=urls + _render["images"])
    except Exception as exc:
        with _lock:
            _render.update(status="failed", error=str(exc))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # the page polls every 2 s
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code: int = 200) -> None:
        self.send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def error(self, message: str, code: int = 400) -> None:
        self.json({"error": message}, code)

    def body(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("Upload is larger than 40 MB.")
        return self.rfile.read(length)

    def file(self, base: Path, rel: str) -> None:
        path = (base / rel).resolve()
        if base.resolve() not in path.parents or not path.is_file():
            self.error("Not found.", 404)
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send(200, path.read_bytes(), ctype)

    # ── GET ─────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == "/":
            self.send(200, (HERE / "dashboard.html").read_bytes(),
                      "text/html; charset=utf-8")
        elif path == "/api/state":
            with _lock:
                rendering = dict(_render)
            active = _active.state if _active else None
            with _lock:
                publishing = dict(_publish, done=list(_publish["done"]))
            self.json({"active": active, "runs": _runs(),
                       "loras": _face_loras(), "render": rendering,
                       "publish": publishing,
                       "defaults": trainer.DEFAULTS, "phases": trainer.PHASES,
                       "dry_run": DRY_RUN, "now": time.time()})
        elif path == "/api/publish/defaults":
            self.json({"api_url": settings.LICENSE_API_URL,
                       "bucket": "krea2-builds"})
        elif path == "/api/presets":
            try:
                answer = ({"names": ["Dry run"], "default": "Dry run"}
                          if DRY_RUN else render.presets())
            except Exception as exc:
                answer = {"names": [], "default": None,
                          "error": "The app is not answering on %s (%s)"
                                   % (render.APP_API, exc)}
            self.json(answer)
        elif path == "/api/dataset":
            run = parse_qs(url.query).get("run", [""])[0]
            folder = RUNS / run / "dataset"
            names = sorted(p.name for p in folder.glob("*")
                           if p.suffix.lower() in trainer.IMAGE_SUFFIXES) \
                if trainer.NAME_RE.match(run) else []
            self.json({"images": names})
        elif path.startswith("/dataset/"):
            self.file(RUNS, path[len("/dataset/"):].replace("/", "/dataset/", 1))
        elif path.startswith("/renders/"):
            self.file(RENDERS, path[len("/renders/"):])
        elif path.startswith("/download/"):
            # A trained LoRA, to take to the machine that uploads it to
            # private R2 (scripts/r2_upload_lora.py). Only face_* files.
            name = path[len("/download/"):]
            if name not in _face_loras():
                return self.error("Not found.", 404)
            data = (MODELS / "loras" / name).read_bytes()
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("content-disposition",
                             'attachment; filename="%s"' % name)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.error("Not found.", 404)

    # ── POST ────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        global _active
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/api/upload":
                run = query.get("run", [""])[0]
                name = query.get("file", [""])[0]
                if not trainer.NAME_RE.match(run):
                    return self.error("Name: lowercase letters, digits, - and _.")
                if (not FILE_RE.match(name)
                        or Path(name).suffix.lower() not in trainer.IMAGE_SUFFIXES):
                    return self.error("%s is not a jpg, png or webp." % name)
                if _training() and _active.name == run:
                    return self.error("That run is training.")
                folder = RUNS / run / "dataset"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(self.body())
                return self.json({"ok": True})

            if url.path == "/api/remove":
                run = query.get("run", [""])[0]
                name = query.get("file", [""])[0]
                if trainer.NAME_RE.match(run) and FILE_RE.match(name) \
                        and not (_training() and _active.name == run):
                    (RUNS / run / "dataset" / name).unlink(missing_ok=True)
                return self.json({"ok": True})

            body = json.loads(self.body() or b"{}")

            if url.path == "/api/train":
                name = str(body.get("name") or "")
                if not trainer.NAME_RE.match(name):
                    return self.error("Name: lowercase letters, digits, - and _.")
                if _training():
                    return self.error("%s is still training." % _active.name)
                if _render["status"] == "running":
                    return self.error("Wait for the render to finish.")
                busy = None if DRY_RUN else _app_busy()
                if busy:
                    return self.error(busy)
                options = {k: body["options"][k]
                           for k in trainer.DEFAULTS
                           if k in (body.get("options") or {})}
                _active = trainer.Run(ROOT, MODELS, name, options,
                                      dry_run=DRY_RUN)
                _active.start()
                return self.json({"ok": True})

            if url.path == "/api/stop":
                if _training():
                    _active.stop()
                return self.json({"ok": True})

            if url.path == "/api/publish":
                with _lock:
                    if _publish["status"] == "running":
                        return self.error("A publish is already running.")
                lora = str(body.get("lora") or "")
                if lora not in _face_loras():
                    return self.error("Pick a trained LoRA (a checkpoint is "
                                      "publishable once it has been rendered with).")
                r2, api = body.get("r2") or {}, body.get("api") or {}
                job = {
                    "file": lora,
                    "id": str(body.get("id") or publish.lora_id(lora)).strip(),
                    "name": str(body.get("name") or "").strip(),
                    "trigger": str(body.get("trigger") or "").strip(),
                    "strength": max(0.0, min(2.0, float(body.get("strength") or 1.0))),
                    "keys": publish.licence_keys(body.get("keys")),
                    "r2": {k: str(r2.get(k) or "").strip()
                           for k in ("account", "access_key", "secret", "bucket")},
                    "api": {"url": str(api.get("url") or "").strip(),
                            "token": str(api.get("token") or "").strip()},
                }
                try:
                    publish.check(job)
                except publish.PublishError as exc:
                    return self.error(str(exc))
                with _lock:
                    _publish.update(status="running", step="starting", sent=0,
                                    total=0, done=[], error=None,
                                    lora="%s → %s" % (lora, job["id"]))
                threading.Thread(target=_publish_job, args=(job,),
                                 daemon=True).start()
                return self.json({"ok": True})

            if url.path == "/api/render":
                if _training():
                    return self.error("Training is using the GPU — wait for it.")
                with _lock:
                    if _render["status"] == "running":
                        return self.error("A render is already running.")
                lora = str(body.get("lora") or "")
                checkpoint = body.get("checkpoint")
                if checkpoint:
                    run, file = str(body.get("run") or ""), str(checkpoint)
                    source = RUNS / run / "output" / file
                    if not (trainer.NAME_RE.match(run) and FILE_RE.match(file)
                            and source.is_file()):
                        return self.error("No such checkpoint.")
                    lora = trainer.install(source, MODELS)
                if lora not in _face_loras():
                    return self.error("Pick a trained LoRA.")
                prompt = str(body.get("prompt") or "").strip()
                if not prompt:
                    return self.error("Write a prompt.")
                seed = body.get("seed")
                job = {
                    "lora": lora, "prompt": prompt,
                    "preset": str(body.get("preset") or ""),
                    "negative": body.get("negative") or None,
                    "seed": None if seed in (None, "") else int(seed),
                    "count": max(1, min(8, int(body.get("count") or 1))),
                    "strength": max(0.0, min(2.0, float(
                        body.get("strength") or 1.0))),
                    "trigger": str(body.get("trigger") or ""),
                }
                with _lock:
                    _render.update(status="running", done=0,
                                   total=job["count"], error=None,
                                   started_at=time.time())
                threading.Thread(target=_render_job, args=(job,),
                                 daemon=True).start()
                return self.json({"ok": True})
        except (ValueError, KeyError, TypeError) as exc:
            return self.error(str(exc))
        self.error("Not found.", 404)


def main() -> None:
    global DRY_RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    DRY_RUN = args.dry_run
    RUNS.mkdir(parents=True, exist_ok=True)
    print("LoRA trainer on http://%s:%d  (runs in %s)%s"
          % (args.host, args.port, ROOT, "  [dry run]" if DRY_RUN else ""))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
