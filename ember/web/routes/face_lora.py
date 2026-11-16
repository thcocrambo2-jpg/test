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

Publish sends a trained LoRA to this pod's own licence, in a background
thread: the licence server signs a one-object upload link and grants the
LoRA to this licence alone — see ember.trainer.publish. No R2 key is ever
on the pod. Publishing needs the consent tick; the server stores it.

Ticked when Train is pressed, the same consent makes the run look after
itself: each checkpoint is backed up to the licence as musubi saves it
(ember.trainer.backup), and the finished LoRA publishes itself. If the pod
dies mid-run, Recover — on this pod or any other on the licence — fetches
a backed-up checkpoint, installs it as the run's LoRA and publishes it.
"""

import json
import re
import shutil
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ember import features
from ember.generation import queue as jobqueue
from ember.logs import log
from ember.settings import BASE_DIR, LICENSE_KEY, MODELS_DIR
from ember.trainer import backup as backups
from ember.trainer import loras, publish
from ember.trainer import run as trainer
from ember.web.routes.common import require_auth, require_feature

ROOT = BASE_DIR / "trainer"
RUNS = ROOT / "runs"
TAB = str(features.Key.FACE_LORA)
LABEL = "🎭 Face LoRA"
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,150}$")
MAX_PHOTO = 40 * 1024 * 1024
GB = 1024 ** 3
# What a run needs free on the volume: caches, checkpoints and the final
# file, plus the one-time bf16 copy of Krea 2 Raw when it is not there yet.
RUN_SPACE = 3 * GB
BASE_SPACE = 27 * GB
# The words beside the tick box (the server stores its own copy).
CONSENT_TEXT = ("I have this person's permission to train and use a LoRA of "
                "their face.")

_lock = threading.Lock()
# The run on the queue (or last on it), and the job carrying it.
_current: dict = {"run": None, "job": None}
# The last Publish, for the page.
_publish = {"status": "idle", "step": None, "sent": 0, "total": 0,
            "done": [], "error": None, "lora": None}
# This licence's published Face LoRAs, as the licence server last said.
_published = {"loras": [], "error": None, "loaded": False}
# This licence's checkpoint backups, by run, as the licence server last said.
_backed_up = {"runs": [], "error": None, "loaded": False}
# How often an automatic publish is tried, and the wait between tries.
AUTO_ATTEMPTS = 3
AUTO_RETRY_WAIT = 30


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


def _ask(fn, key: str) -> dict:
    """fn()'s answer[key] for the page, or the error, worded."""
    try:
        return {key: fn().get(key) or [], "error": None, "loaded": True}
    except publish.PublishError as exc:
        return {"error": str(exc), "loaded": True}
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc), "loaded": True}


def _refresh_published() -> None:
    """Ask the licence server which Face LoRAs and checkpoint backups this
    licence has. Blocking."""
    published = _ask(publish.mine, "loras")
    backed_up = _ask(publish.checkpoints, "runs")
    with _lock:
        _published.update(published)
        _backed_up.update(backed_up)


def _in_background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def _space() -> dict:
    """Free space on the volume, and what a run needs of it now."""
    need = RUN_SPACE
    if not (ROOT / "models" / trainer.CONVERTED_FILE).exists():
        need += BASE_SPACE
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(ROOT).free
    except OSError:
        free = None
    return {"free": free, "need": need}


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
    the job is stopped. A generator, like every handler the queue drives.
    With the consent tick, it backs up each checkpoint as it is saved and
    publishes the finished LoRA."""
    auto = run.state.get("auto_publish") is True
    backup = backups.Backup(run) if auto else None
    run.start()
    try:
        while run.state["status"] == "running":
            if backup:
                backup.scan()
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
        if auto:
            _start_publish({"file": run.state["lora_file"],
                            "name": loras.run_name(run.state["lora_file"]),
                            "trigger": str(run.options.get("trigger") or ""),
                            "consent": True}, attempts=AUTO_ATTEMPTS)
        yield ("✅ %s trained — it is on the 🔶 Krea2 V2 tab's LoRA rows now%s"
               % (run.name, ", and publishing to this licence" if auto else ""),)
    finally:
        # Stopped from the queue: close() lands here at the last yield.
        if run.state["status"] == "running":
            run.stop()
        if backup:
            # Whatever it saved before it ended, however it ended.
            if run.proc is not None:
                try:
                    run.proc.wait(timeout=30)
                except Exception:
                    pass
            backup.scan(final=True)
            backup.close()


def _step(name, sent=0, total=0):
    with _lock:
        _publish.update(step=name, sent=sent, total=total)


def _done(line):
    with _lock:
        _publish["done"].append(line)


def _publish_job(job: dict, attempts: int = 1) -> None:
    """Publish one installed file, in the slot _start_publish took.
    With job["recover"] (a backed-up checkpoint's file name) it fetches
    that first and installs it as job["file"]."""
    try:
        for attempt in range(1, attempts + 1):
            try:
                if job.get("recover") and not job.get("recovered"):
                    _recover(job)
                    job["recovered"] = True
                answer = publish.publish(MODELS_DIR / "loras" / job["file"], job["name"],
                                         job["trigger"], job["consent"], _step, _done)
                break
            except Exception as exc:
                if attempt == attempts:
                    raise
                wait = AUTO_RETRY_WAIT * attempt
                _done("%s — trying again in %d s (%d/%d)" % (
                    exc if isinstance(exc, publish.PublishError) else type(exc).__name__,
                    wait, attempt + 1, attempts))
                time.sleep(wait)
        if answer.get("file") and answer["file"] != job["file"]:
            loras.rename(job["file"], answer["file"], job["trigger"])
            _done("renamed %s → %s to match" % (job["file"], answer["file"]))
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
        _refresh_published()


def _recover(job: dict) -> None:
    """Fetch backed-up checkpoint job["recover"] of run job["name"] and
    install it as job["file"], offered on the Krea tabs."""
    target = MODELS_DIR / "loras" / job["file"]
    if not target.exists():
        part = RUNS / job["name"] / "output" / job["recover"]
        if not (part.exists() and backups.complete(part)):
            _step("asking for the checkpoint")
            publish.download_checkpoint(job["name"], job["recover"], part, _step)
            _done("downloaded %s" % job["recover"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(part, target)
    loras.offer(job["file"], job["trigger"])
    _done("installed as %s — on this pod's Krea tabs" % job["file"])


def _start_publish(job: dict, attempts: int = 1, wait: bool = True) -> bool:
    """Take the one publish slot and run `job` in it, in the background.
    `wait`: if another publish has the slot, queue behind it (in its own
    thread) instead of refusing. False when refused."""
    def take() -> bool:
        with _lock:
            if _publish["status"] == "running":
                return False
            _publish.update(status="running", step="starting", sent=0, total=0,
                            done=[], error=None, lora=job["file"])
            return True

    if take():
        _in_background(_publish_job, job, attempts)
        return True
    if not wait:
        return False

    def later():
        while not take():
            time.sleep(2)
        _publish_job(job, attempts)

    _in_background(later)
    return True


def register(api: APIRouter) -> None:
    if features.enabled(features.Key.FACE_LORA):
        count = loras.offer_all()
        if count:
            log.info("Face LoRA: %d trained LoRA(s) offered on the Krea tabs", count)
        _in_background(_refresh_published)

    @api.get("/face-lora/state", dependencies=_gate())
    def state():
        """Everything the page draws, in one poll."""
        run = _current["run"]
        with _lock:
            publishing = dict(_publish, done=list(_publish["done"]))
            published = dict(_published, loras=list(_published["loras"]))
            backed_up = dict(_backed_up, runs=list(_backed_up["runs"]))
        return {
            "active": run.state if run is not None else None,
            "busy": _busy(),
            "job": _current["job"],
            "runs": _runs(),
            "loras": [{"file": f, "name": loras.run_name(f)} for f in _face_loras()],
            "publish": publishing,
            "published": published,
            "backedUp": backed_up,
            "consentText": CONSENT_TEXT,
            "space": _space(),
            "defaults": trainer.DEFAULTS,
            "phases": trainer.PHASES,
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
        space = _space()
        if space["free"] is not None and space["free"] < space["need"]:
            raise HTTPException(
                507, "Training needs %.0f GB free on the volume; it has %.0f GB."
                % (space["need"] / GB, space["free"] / GB))
        # The consent tick at Train: back up each checkpoint and publish
        # the finished LoRA to this licence, unattended.
        auto = body.get("consent") is True and bool(LICENSE_KEY)
        with _lock:
            if _busy():
                raise HTTPException(409, "%s is already queued or training."
                                    % _current["run"].name)
            options = {k: (body.get("options") or {})[k] for k in trainer.DEFAULTS
                       if k in (body.get("options") or {})}
            run = trainer.Run(ROOT, MODELS_DIR, name, options)
            run.state["auto_publish"] = auto
            view = jobqueue.submit(
                lane="comfy", tab=TAB, tab_label=LABEL,
                title="Train %s (%s steps)" % (name, run.options["steps"]),
                fn=_train_job, args=(run,), result_keys=("status",))
            _current.update(run=run, job=view.id)
        return {"ok": True, "jobId": view.id, "place": view.place}

    @api.post("/face-lora/publish", dependencies=_gate())
    def publish_lora(body: dict = Body(default={})):
        """Publish to this licence in the background; the page polls."""
        with _lock:
            if _publish["status"] == "running":
                raise HTTPException(409, "A publish is already running.")
        lora = str(body.get("lora") or "")
        if lora not in _face_loras():
            raise HTTPException(400, "Pick a trained LoRA.")
        if body.get("consent") is not True:
            raise HTTPException(400, "Tick the box confirming you have this "
                                     "person's permission.")
        if not LICENSE_KEY:
            raise HTTPException(400, "This pod has no licence to publish to.")
        name = loras.run_name(lora)
        if not trainer.NAME_RE.match(name):
            raise HTTPException(400, "%s does not name a run this page can "
                                     "publish." % lora)
        job = {"file": lora, "name": name, "consent": True,
               "trigger": str(body.get("trigger") or "").strip()[:200]}
        if not _start_publish(job, wait=False):
            raise HTTPException(409, "A publish is already running.")
        return {"ok": True}

    @api.post("/face-lora/recover", dependencies=_gate())
    def recover(body: dict = Body(default={})):
        """Fetch a backed-up checkpoint, install it as its run's LoRA and
        publish it — for a run whose pod died before it finished."""
        name = str(body.get("name") or "")
        checkpoint = str(body.get("checkpoint") or "")
        if not trainer.NAME_RE.match(name):
            raise HTTPException(400, "Name: lowercase letters, digits, - and _.")
        if not (checkpoint.startswith(name + "-step")
                and checkpoint.endswith(".safetensors") and FILE_RE.match(checkpoint)):
            raise HTTPException(400, "Pick one of that run's checkpoints.")
        if body.get("consent") is not True:
            raise HTTPException(400, "Tick the box confirming you have this "
                                     "person's permission.")
        if not LICENSE_KEY:
            raise HTTPException(400, "This pod has no licence to publish to.")
        file = trainer.lora_file_name(name)
        if file in _face_loras():
            raise HTTPException(409, "%s already has a LoRA on this pod — "
                                     "publish that one instead." % name)
        if _busy() and _current["run"].name == name:
            raise HTTPException(409, "That run is training.")
        job = {"file": file, "name": name, "consent": True, "recover": checkpoint,
               "trigger": str(body.get("trigger") or "").strip()[:200]}
        if not _start_publish(job, wait=False):
            raise HTTPException(409, "A publish is already running.")
        return {"ok": True}
