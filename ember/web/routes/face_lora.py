"""The 🎭 Face LoRA page: train a face LoRA, try it, publish it privately.

Everything is gated on `face_lora`, which only hand-picked licences hold.
It has no weights of its own (needs=()), so the route gate is the whole of
it on this side — scripts/check_routes.py probes it with a licence that
grants nothing. The licence server checks the same feature again before it
registers or grants anything (/v1/face-lora/*).

Training is a job on the GPU queue's image lane, so it waits for the
renders ahead of it and the renders queued behind it wait for it; it shows
in the queue panel like any other job and its ✕ stops it. The run itself
is ember.trainer.run, polled once a second for the queue's status line.

What the page holds between visits lives on the volume, beside the app's
own models: BASE_DIR/trainer/runs/<name>/ (photos, caches, checkpoints,
state.json). A finished LoRA is copied into the app's loras/ folder and
offered on the Krea tabs straight away (ember.trainer.loras).

Publish takes the R2 key in the request body, uses it in a background
thread for one upload and drops it — see ember.trainer.publish.
"""

import json
import re
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ember import features
from ember.generation import queue as jobqueue
from ember.logs import log
from ember.settings import BASE_DIR, LICENSE_KEY, MODELS_DIR
from ember.trainer import loras, publish
from ember.trainer import run as trainer
from ember.web.routes.common import require_auth, require_feature

ROOT = BASE_DIR / "trainer"
RUNS = ROOT / "runs"
TAB = str(features.Key.FACE_LORA)
LABEL = "🎭 Face LoRA"
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,150}$")
MAX_PHOTO = 40 * 1024 * 1024

_lock = threading.Lock()
# The run on the queue (or last on it), and the job carrying it.
_current: dict = {"run": None, "job": None}
# The last Publish, for the page. Never holds the R2 key.
_publish = {"status": "idle", "step": None, "sent": 0, "total": 0,
            "done": [], "error": None, "lora": None}


def _gate():
    return [Depends(require_auth), Depends(require_feature(features.Key.FACE_LORA))]


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
    return sorted(p.name for p in (MODELS_DIR / "loras").glob(loras.PREFIX + "*.safetensors"))


def _job_status(job_id: str | None) -> str | None:
    if not job_id:
        return None
    _rev, views, _running, _waiting = jobqueue.snapshot()
    return next((v.status for v in views if v.id == job_id), None)


def _busy() -> bool:
    """True while a run is queued or training. A job taken off the queue
    before it started leaves its run marked running; that is not busy."""
    run, job = _current["run"], _current["job"]
    if run is None or run.state["status"] != "running":
        return False
    status = _job_status(job)
    return status in (jobqueue.QUEUED, jobqueue.RUNNING) or (
        status is None and run.state["phase"] != "setup")


def _status_line(run) -> str:
    state = run.state
    progress = state.get("progress")
    line = "🎭 %s · %s" % (run.name, state["phase"].replace("_", " "))
    if progress:
        line += " %d/%d" % (progress["done"], progress["total"])
        if progress.get("eta") is not None:
            line += " · %d:%02d left" % divmod(int(progress["eta"]), 60)
    if state.get("loss") is not None and state["phase"] == "train":
        line += " · loss %.3f" % state["loss"]
    return line


def _train_job(run):
    """The queue job: start the run, report on it every second, stop it if
    the job is stopped. A generator, like every handler the queue drives."""
    run.start()
    try:
        while run.state["status"] == "running":
            progress = run.state.get("progress")
            jobqueue.report_eta(
                time.time() + progress["eta"]
                if run.state["phase"] == "train" and progress
                and progress.get("eta") is not None else None)
            yield (_status_line(run),)
            time.sleep(1)
        if run.state["status"] != "done":
            raise RuntimeError(run.state.get("error") or "the run stopped")
        loras.offer(run.state["lora_file"], run.options.get("trigger", ""))
        yield ("✅ %s trained — it is on the 🔶 Krea2 V2 tab's LoRA rows now"
               % run.name,)
    finally:
        # Stopped from the queue: close() lands here at the last yield.
        if run.state["status"] == "running":
            run.stop()


def _publish_job(job: dict) -> None:
    def step(name, sent=0, total=0):
        with _lock:
            _publish.update(step=name, sent=sent, total=total)

    def done(line):
        with _lock:
            _publish["done"].append(line)

    try:
        done(publish.upload(MODELS_DIR / "loras" / job["file"], job["r2"], step))
        step("registering")
        done(publish.register({
            "id": job["id"], "file": job["file"], "name": job["name"],
            "trigger": job["trigger"], "default_strength": job["strength"]}))
        if job["keys"]:
            step("granting")
            done(publish.grant(job["id"], job["keys"]))
        else:
            done("not granted to anyone yet — publish again with keys")
        with _lock:
            _publish.update(status="done", step=None)
    except publish.PublishError as exc:
        with _lock:
            _publish.update(status="failed", error=str(exc))
    except Exception as exc:              # never a URL: see publish.py
        log.warning("Face LoRA publish failed: %s", type(exc).__name__)
        with _lock:
            _publish.update(status="failed",
                            error="%s: %s" % (type(exc).__name__, exc))
    finally:
        job["r2"] = None                  # drop the key with the job


def register(api: APIRouter) -> None:
    if features.enabled(features.Key.FACE_LORA):
        count = loras.offer_all()
        if count:
            log.info("Face LoRA: %d trained LoRA(s) offered on the Krea tabs", count)

    @api.get("/face-lora/state", dependencies=_gate())
    def state():
        """Everything the page draws, in one poll."""
        run = _current["run"]
        with _lock:
            publishing = dict(_publish, done=list(_publish["done"]))
        return {
            "active": run.state if run is not None else None,
            "busy": _busy(),
            "job": _current["job"],
            "runs": _runs(),
            "loras": _face_loras(),
            "publish": publishing,
            "defaults": trainer.DEFAULTS,
            "phases": trainer.PHASES,
            "licenceKey": LICENSE_KEY or "",
            "bucket": publish.DEFAULT_BUCKET,
            "now": time.time(),
        }

    @api.get("/face-lora/photos/{run}", dependencies=_gate())
    def photos(run: str):
        if not trainer.NAME_RE.match(run):
            raise HTTPException(400, "Name: lowercase letters, digits, - and _.")
        folder = RUNS / run / "dataset"
        return {"photos": sorted(p.name for p in folder.glob("*")
                                 if p.suffix.lower() in trainer.IMAGE_SUFFIXES)}

    @api.get("/face-lora/photos/{run}/{name}", dependencies=_gate())
    def photo(run: str, name: str):
        path = RUNS / run / "dataset" / name
        if not (trainer.NAME_RE.match(run) and FILE_RE.match(name) and path.is_file()):
            raise HTTPException(404, "No such photo.")
        return FileResponse(path)

    @api.post("/face-lora/photos/{run}/{name}", dependencies=_gate())
    async def add_photo(run: str, name: str, request: Request):
        """One photo, as the raw request body."""
        if not trainer.NAME_RE.match(run):
            raise HTTPException(400, "Name: lowercase letters, digits, - and _.")
        if not FILE_RE.match(name) or not name.lower().endswith(trainer.IMAGE_SUFFIXES):
            raise HTTPException(400, "%s is not a jpg, png or webp." % name)
        if _busy() and _current["run"].name == run:
            raise HTTPException(409, "That run is training.")
        data = await request.body()
        if len(data) > MAX_PHOTO:
            raise HTTPException(413, "Photos are limited to 40 MB.")
        folder = RUNS / run / "dataset"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        return {"ok": True}

    @api.delete("/face-lora/photos/{run}/{name}", dependencies=_gate())
    def remove_photo(run: str, name: str):
        if not (trainer.NAME_RE.match(run) and FILE_RE.match(name)):
            raise HTTPException(400, "No such photo.")
        if _busy() and _current["run"].name == run:
            raise HTTPException(409, "That run is training.")
        (RUNS / run / "dataset" / name).unlink(missing_ok=True)
        return {"ok": True}

    @api.post("/face-lora/train", dependencies=_gate())
    def train(body: dict = Body(default={})):
        """Queue a run. It starts when the image lane is free."""
        name = str(body.get("name") or "")
        if not trainer.NAME_RE.match(name):
            raise HTTPException(400, "Name: lowercase letters, digits, - and _.")
        with _lock:
            if _busy():
                raise HTTPException(409, "%s is already queued or training."
                                    % _current["run"].name)
            options = {k: (body.get("options") or {})[k] for k in trainer.DEFAULTS
                       if k in (body.get("options") or {})}
            run = trainer.Run(ROOT, MODELS_DIR, name, options)
            view = jobqueue.submit(
                lane="comfy", tab=TAB, tab_label=LABEL,
                title="Train %s (%s steps)" % (name, run.options["steps"]),
                fn=_train_job, args=(run,), result_keys=("status",))
            _current.update(run=run, job=view.id)
        return {"ok": True, "jobId": view.id, "place": view.place}

    @api.post("/face-lora/publish", dependencies=_gate())
    def publish_lora(body: dict = Body(default={})):
        """Upload, register and grant, in the background; the page polls."""
        with _lock:
            if _publish["status"] == "running":
                raise HTTPException(409, "A publish is already running.")
        lora = str(body.get("lora") or "")
        if lora not in _face_loras():
            raise HTTPException(400, "Pick a trained LoRA.")
        r2 = body.get("r2") or {}
        job = {
            "file": lora,
            "id": str(body.get("id") or publish.lora_id(lora)).strip(),
            "name": str(body.get("name") or "").strip(),
            "trigger": str(body.get("trigger") or "").strip(),
            "strength": max(0.0, min(2.0, float(body.get("strength") or 1.0))),
            "keys": publish.licence_keys(body.get("keys")),
            "r2": {"account": str(r2.get("account") or "").strip(),
                   "access_key": str(r2.get("accessKey") or "").strip(),
                   "secret": str(r2.get("secret") or "").strip(),
                   "bucket": str(r2.get("bucket") or publish.DEFAULT_BUCKET).strip()},
        }
        try:
            publish.check(job)
        except publish.PublishError as exc:
            raise HTTPException(400, str(exc)) from None
        with _lock:
            _publish.update(status="running", step="starting", sent=0, total=0,
                            done=[], error=None,
                            lora="%s → %s" % (lora, job["id"]))
        threading.Thread(target=_publish_job, args=(job,), daemon=True).start()
        return {"ok": True}
