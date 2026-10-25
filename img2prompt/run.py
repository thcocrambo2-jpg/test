"""The img2prompt loop's bookkeeping and its one GPU step.

The Claude Code chat drives the loop (see .claude/skills/img2prompt); this
script is what it calls between agents. It owns state.json, which the
dashboard draws, so every time on the dashboard is measured here rather
than reported by an agent.

    python img2prompt/run.py init --folder D:\\refs
    python img2prompt/run.py begin  --image a.png
    python img2prompt/run.py phase  --image a.png --iter 1 --phase writing
    python img2prompt/run.py render --image a.png --iter 1
    python img2prompt/run.py judge  --image a.png --iter 1
    python img2prompt/run.py finish --image a.png [--status skipped --note ...]
    python img2prompt/run.py status

Every command after `init` works on the newest run (runs/latest.txt).
Each prints one JSON object, so the chat reads the answer, not a log.

Files per image, under runs/<run>/<image stem>/:
    reference.<ext>          the copied reference
    iterN_prompt.txt         the writer's two fields
    iterN_negative.txt       optional: the writer's full negative prompt
    iterN_1.png, ...         the renders, one per batch image
    iterN_verdict.json       the judge's ranking of every render so far
    best.png, best_prompt.txt, best_negative.txt   written by `finish`
"""

import argparse
import atexit
import ctypes
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
CONFIG = HERE / "config.json"
API = os.environ.get("IMG2PROMPT_API", "http://127.0.0.1:7870/api/v1")
TAB = "krea_v2_t2i"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
POLL_SECONDS = 5


# ───────────────────────────────────────────────────────────── helpers

def now() -> float:
    return round(time.time(), 1)


def out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def fail(message: str) -> None:
    out({"ok": False, "error": message})
    sys.exit(1)


def read_config() -> dict:
    """config.json, read fresh every time — the user edits it mid-run."""
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def free_ram_gb() -> float | None:
    """Free physical memory, Windows only (None elsewhere)."""
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return round(status.ullAvailPhys / 2**30, 1)


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as img:
        return img.size


# ─────────────────────────────────────────────────────────────── state

def run_dir() -> Path:
    latest = RUNS / "latest.txt"
    if not latest.exists():
        fail("No run yet — start one with `init --folder ...`.")
    return RUNS / latest.read_text(encoding="utf-8").strip()


# Two images run at once (one writing or judging while the other renders),
# so two run.py processes can read-modify-write state.json together. load()
# takes state.lock and save() gives it back; a process that dies holding it
# frees it at exit, and a lock older than LOCK_STALE is taken as abandoned.
LOCK_STALE = 120
LOCK_WAIT = 150
_locks: dict[str, Path] = {}
atexit.register(lambda: [p.unlink(missing_ok=True) for p in _locks.values()])


def lock(rdir: Path) -> None:
    path = rdir / "state.lock"
    if str(path) in _locks:
        return
    deadline = time.time() + LOCK_WAIT
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            _locks[str(path)] = path
            return
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > LOCK_STALE
            except FileNotFoundError:
                continue
            if stale:
                path.unlink(missing_ok=True)
                continue
            if time.time() > deadline:
                fail("state.json is locked by another run.py: %s" % path)
            time.sleep(0.05)


def unlock(rdir: Path) -> None:
    path = _locks.pop(str(rdir / "state.lock"), None)
    if path:
        path.unlink(missing_ok=True)


def load(rdir: Path | None = None, locked: bool = True) -> tuple[Path, dict]:
    """Read state.json; locked=False for a read that is never saved."""
    rdir = rdir or run_dir()
    if locked:
        lock(rdir)
    state = json.loads((rdir / "state.json").read_text(encoding="utf-8"))
    if "active" not in state:  # a run from before two images at once
        cur = state.get("current")
        state["active"] = {cur["image"]: cur} if cur else {}
    return rdir, state


def save(rdir: Path, state: dict) -> None:
    """Write via a temp file so the dashboard never reads half a file."""
    state["updated_at"] = now()
    sync_current(state)
    tmp = rdir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    # On Windows the replace is refused while the dashboard has the file
    # open for its 2-second poll; the read is over in milliseconds.
    for attempt in range(50):
        try:
            os.replace(tmp, rdir / "state.json")
            unlock(rdir)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)


def find(state: dict, name: str) -> dict:
    for row in state["images"]:
        if row["name"] == name:
            return row
    fail("No image %r in this run." % name)


def iteration(row: dict, n: int, create: bool = False) -> dict:
    for it in row["iterations"]:
        if it["n"] == n:
            return it
    if not create:
        fail("Iteration %d of %s has not started." % (n, row["name"]))
    it = {"n": n, "started_at": now(), "finished_at": None,
          "render_started_at": None, "render_seconds": None,
          "renders": [], "negative_changed": False, "ram_free_gb": None}
    row["iterations"].append(it)
    return it


def set_current(state: dict, image=None, n=None, phase=None, **extra) -> None:
    """Mark what one image is doing now; with no image, clear them all."""
    active = state.setdefault("active", {})
    if image:
        active[image] = {"image": image, "iteration": n, "phase": phase,
                         "phase_started_at": now(), **extra}
    else:
        active.clear()
    sync_current(state)


def clear_current(state: dict, image: str) -> None:
    state.setdefault("active", {}).pop(image, None)
    sync_current(state)


def active_entry(state: dict, image: str, n: int, phase: str) -> dict:
    """The image's entry in state["active"], recreated if it went missing."""
    if image not in state.setdefault("active", {}):
        set_current(state, image, n, phase)
    return state["active"][image]


def sync_current(state: dict) -> None:
    """state["current"] is the one image the dashboard shows in full: the
    one rendering if any, else the one whose phase changed last."""
    active = list((state.get("active") or {}).values())
    state["current"] = (max(active, key=lambda e: (e["phase"] == "rendering",
                                                  e["phase_started_at"]))
                        if active else None)


# ─────────────────────────────────────────────────────────── the API

def call(method: str, path: str, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def call_retrying(method: str, path: str, body=None, tries=12):
    """A poll survives the backend being briefly busy or restarted."""
    for attempt in range(tries):
        try:
            return call(method, path, body)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
            time.sleep(POLL_SECONDS)


def nearest_aspect(width: int, height: int, choices: list[str]) -> str:
    """The tab's size choice closest to the reference's shape, by log ratio
    so 2:3 and 3:2 are equally far from 1:1.

    Reads the first "W:H" or "W×H" in each choice, which covers Krea V2's
    "3:4 (Portrait Standard)" and Z-Image's "1140×1472 (3:4)" alike.
    """
    target = math.log(width / height)

    def distance(choice: str) -> float:
        w, h = re.search(r"(\d+)\s*[:×x]\s*(\d+)", choice).groups()
        return abs(math.log(int(w) / int(h)) - target)

    return min(choices, key=distance)


def size_field(fields: dict) -> str:
    """The name of the tab's shape control: `aspect` on Krea, `resolution`
    on Z-Image."""
    return "aspect" if "aspect" in fields else "resolution"


# ──────────────────────────────────────────────────────────── commands

def cmd_init(args) -> None:
    """Start a run on a folder, or resume the newest unfinished one on it."""
    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        fail("Not a folder: %s" % folder)
    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in IMAGE_EXT and p.is_file())
    if not files:
        fail("No images in %s" % folder)

    RUNS.mkdir(exist_ok=True)
    cfg = read_config()
    tab = cfg.get("tab", TAB)
    # A run belongs to one folder on one model: the same folder on another
    # model is a separate run, so a paused Krea run is never resumed onto
    # Z-Image or the other way round.
    resumed = None
    for sdir in sorted(RUNS.iterdir(), reverse=True):
        sfile = sdir / "state.json"
        if not sfile.exists():
            continue
        prior = json.loads(sfile.read_text(encoding="utf-8"))
        if (prior["folder"] == str(folder) and not prior.get("finished_at")
                and (prior.get("tab") or TAB) == tab
                and (prior.get("overrides") or {}) == cfg.get("overrides", {})):
            resumed = sdir
            break

    if resumed:
        rdir, state = load(resumed)
        known = {row["name"] for row in state["images"]}
        for path in files:          # images added to the folder since
            if path.name not in known:
                state["images"].append(_new_image(rdir, path))
        for row in state["images"]:  # an image cut off mid-loop resumes
            if row["status"] == "running":
                _drop_unjudged(rdir, row)
                if not row["iterations"]:
                    row["status"] = "pending"
        state["resumed_at"] = now()
    else:
        rdir = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
        rdir.mkdir()
        state = {"folder": str(folder), "tab": tab, "preset": cfg["preset"],
                 # Form values set on top of the preset for every render,
                 # e.g. {"lora.0.enabled": false}. Part of what makes a run
                 # a run, like the tab.
                 "overrides": cfg.get("overrides", {}),
                 "started_at": now(), "finished_at": None, "current": None,
                 "images": [_new_image(rdir, p) for p in files]}
    # Names in config "skip" are never handed to an agent.
    for row in state["images"]:
        if row["name"] in cfg.get("skip", []) and row["status"] == "pending":
            row.update(status="skipped", note="listed in config skip")

    try:
        call("GET", "/schema/" + (state.get("tab") or TAB))
        backend = "up"
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        backend = "DOWN (%s) — start img2prompt/serve_backend.py" % exc
    state["backend"] = API
    save(rdir, state)
    (RUNS / "latest.txt").write_text(rdir.name, encoding="utf-8")
    out({"ok": True, "run": str(rdir), "resumed": bool(resumed),
         "backend": backend,
         "pending": [r["name"] for r in state["images"]
                     if r["status"] == "pending"],
         "done": [r["name"] for r in state["images"]
                  if r["status"] != "pending"]})


def _drop_unjudged(rdir: Path, row: dict) -> None:
    """Forget an iteration the judge never finished, keeping its files.

    Its prompt, negative and render move to superseded/ under a timestamp
    rather than being deleted, so the redone iteration cannot overwrite
    them — every prompt the loop ever wrote stays on disk.
    """
    idir = rdir / row["dir"]
    for it in [it for it in row["iterations"] if not it.get("finished_at")]:
        stale = list(idir.glob("iter%d_*" % it["n"]))
        if stale:
            keep = idir / "superseded" / datetime.now().strftime(
                "%Y%m%d-%H%M%S")
            keep.mkdir(parents=True, exist_ok=True)
            for path in stale:
                shutil.move(str(path), str(keep / path.name))
        row["iterations"].remove(it)


def _new_image(rdir: Path, path: Path) -> dict:
    idir = rdir / path.stem
    idir.mkdir(exist_ok=True)
    ref = idir / ("reference" + path.suffix.lower())
    shutil.copy2(path, ref)
    width, height = image_size(ref)
    return {"name": path.name, "dir": path.stem,
            "reference": ref.relative_to(rdir).as_posix(),
            "width": width, "height": height,
            # Fixed per image, so iterations differ by prompt alone.
            "seed": zlib.crc32(path.name.encode("utf-8")),
            "status": "pending", "config": None, "aspect": None,
            "started_at": None, "finished_at": None,
            "iterations": [], "best": None, "note": None}


def cmd_begin(args) -> None:
    """Take an image on: snapshot config.json for the rest of its loop."""
    rdir, state = load()
    row = find(state, args.image)
    idir = rdir / row["dir"]
    if row["status"] == "running" and row["iterations"]:
        # Resumed after an interruption: carry on from the last judged
        # iteration with the config it started with. The coordinator
        # starts a fresh writer on the revision step.
        # Resume after the last *judged* iteration. One rendered but not
        # judged (or written but not rendered) is handed back as-is, so
        # the chat judges (or renders) it instead of writing over it.
        judged = [it["n"] for it in row["iterations"] if it.get("scores")]
        last = judged[-1] if judged else 0
        pending = [it for it in row["iterations"] if it["n"] > last]
        best_n = row["best"]["iteration"] if row["best"] else None
        if pending:
            it = pending[0]
            step = "judge" if it["renders"] else (
                "render" if (idir / ("iter%d_prompt.txt" % it["n"])).exists()
                else "write")
            set_current(state, row["name"], it["n"],
                        {"judge": "judging", "render": "writing",
                         "write": "writing"}[step])
            save(rdir, state)
            out({"ok": True, "image": row["name"], "resumed": True,
                 "next_step": step, "iteration": it["n"],
                 "image_dir": str(idir), "reference": str(rdir / row["reference"]),
                 "default_negative": str(idir / "default_negative.txt"),
                 "config": row["config"], "aspect": row["aspect"],
                 "seed": row["seed"], "best": row["best"],
                 "best_prompt": (str(idir / ("iter%d_prompt.txt" % best_n))
                                 if best_n else None),
                 "verdict": (str(idir / ("iter%d_verdict.json" % last))
                             if last else None)})
            return
        set_current(state, row["name"], last + 1, "writing")
        iteration(row, last + 1, create=True)
        save(rdir, state)
        out({"ok": True, "image": row["name"], "resumed": True,
             "next_step": "write",
             "image_dir": str(idir), "reference": str(rdir / row["reference"]),
             "default_negative": str(idir / "default_negative.txt"),
             "config": row["config"], "aspect": row["aspect"],
             "seed": row["seed"], "next_iteration": last + 1,
             "best": row["best"],
             "best_prompt": str(idir / ("iter%d_prompt.txt" % best_n)),
             "verdict": str(idir / ("iter%d_verdict.json" % last))})
        return
    cfg = read_config()
    schema = call("GET", "/schema/" + (state.get("tab") or TAB))
    fields = {f["name"]: f for f in schema["fields"]}
    row.update(status="running", config=cfg, started_at=now(),
               finished_at=None, iterations=[], best=None, note=None,
               aspect=nearest_aspect(row["width"], row["height"],
                                     fields[size_field(fields)]["choices"]))
    state["default_negative"] = fields["negative"]["default"]
    (rdir / row["dir"] / "default_negative.txt").write_text(
        fields["negative"]["default"], encoding="utf-8")
    set_current(state, row["name"], 1, "writing")
    iteration(row, 1, create=True)
    save(rdir, state)
    out({"ok": True, "image": row["name"], "image_dir": str(idir),
         "reference": str(rdir / row["reference"]),
         "default_negative": str(idir / "default_negative.txt"),
         "tab": (state.get("tab") or TAB), "preset": state["preset"],
         "config": cfg, "aspect": row["aspect"], "seed": row["seed"],
         "size": [row["width"], row["height"]]})


def cmd_phase(args) -> None:
    """Mark what the loop is doing now: writing or judging."""
    rdir, state = load()
    row = find(state, args.image)
    iteration(row, args.iter, create=(args.phase == "writing"))
    set_current(state, row["name"], args.iter, args.phase)
    save(rdir, state)
    out({"ok": True})


def cmd_render(args) -> None:
    """Queue one iteration's render, wait for it, save the images.

    Long — about 11 minutes on Krea V2 and 1.3 on Z-Image on an 8 GB card —
    so the chat runs this in the background and is woken when it exits.
    The run's own tab and preset are used, not config.json's, so a paused
    run resumes on the model it started with.
    """
    rdir, state = load()
    row = find(state, args.image)
    cfg = row["config"]
    idir = rdir / row["dir"]
    prompt_file = idir / ("iter%d_prompt.txt" % args.iter)
    if not prompt_file.exists():
        fail("Missing %s" % prompt_file)
    negative_file = idir / ("iter%d_negative.txt" % args.iter)
    default_negative = state["default_negative"]
    negative = (negative_file.read_text(encoding="utf-8").strip()
                if cfg.get("agent_edits_negative") and negative_file.exists()
                else default_negative)

    tab = (state.get("tab") or TAB)
    fields = {f["name"]: f for f in call("GET", "/schema/" + tab)["fields"]}
    values = call("POST", "/schema/%s/apply" % tab,
                  {"preset": state["preset"]})["values"]
    if not values:
        fail("Preset %r is not on offer." % state["preset"])
    values.update((state.get("overrides") or {}))
    values.update(prompt=prompt_file.read_text(encoding="utf-8").strip(),
                  negative=negative, seed=row["seed"], randomize=False,
                  batch_count=int(cfg.get("batch_count", 1)))
    values[size_field(fields)] = row["aspect"]
    if cfg.get("steps"):  # null keeps the preset's own step count
        values["steps"] = int(cfg["steps"])

    it = iteration(row, args.iter, create=True)
    it.update(render_started_at=now(), renders=[],
              negative_changed=(negative != default_negative))
    set_current(state, row["name"], args.iter, "rendering")
    save(rdir, state)

    job = call("POST", "/tabs/%s/generate" % tab, {"values": values})["jobId"]
    lowest_ram = free_ram_gb()
    while True:
        time.sleep(POLL_SECONDS)
        queue = call_retrying("GET", "/queue")
        mine = next((j for j in queue["jobs"] if j["id"] == job), None)
        if mine is None:
            fail("Job %s left the queue without finishing." % job)
        ram = free_ram_gb()
        if ram is not None:
            lowest_ram = ram if lowest_ram is None else min(lowest_ram, ram)
        rdir, state = load(rdir)
        # Queued behind the other image's render: time it from when it runs.
        if mine["status"] in ("queued", "waiting"):
            iteration(find(state, args.image), args.iter).update(
                render_started_at=now())
        active_entry(state, args.image, args.iter, "rendering").update(
            job=job, status=mine["status"], progress=mine["progress"],
            step=mine["step"], backend_eta=mine["eta"], ram_free_gb=ram)
        save(rdir, state)
        if mine["status"] in ("done", "failed", "cancelled"):
            break

    if mine["status"] != "done":
        fail("Render %s: %s" % (mine["status"],
                                mine.get("error") or mine["progress"]))
    shown = call_retrying("GET", "/tabs/%s/display?job=%s" % (tab, job))
    images = (shown.get("result") or {}).get("images") or []
    if not images:
        fail("The job finished but produced no images: %s" % mine["progress"])

    saved = []
    for k, media in enumerate(images, start=1):
        target = idir / ("iter%d_%d.png" % (args.iter, k))
        with urllib.request.urlopen(API.rsplit("/api/v1", 1)[0]
                                    + media["url"], timeout=120) as resp:
            target.write_bytes(resp.read())
        saved.append(target.name)

    rdir, state = load(rdir)
    row = find(state, args.image)
    it = iteration(row, args.iter)
    it.update(renders=saved, ram_free_gb=lowest_ram,
              render_seconds=round(now() - it["render_started_at"], 1))
    set_current(state, row["name"], args.iter, "judging")
    save(rdir, state)
    out({"ok": True, "renders": [str(idir / name) for name in saved],
         "render_minutes": round(it["render_seconds"] / 60, 1),
         "lowest_free_ram_gb": lowest_ram})


def cmd_judge(args) -> None:
    """Record the judge's verdict and say whether the loop goes on.

    The decision is made here rather than by an agent: at least
    min_iterations, then stop at stop_score or max_iterations.
    """
    rdir, state = load()
    row = find(state, args.image)
    idir = rdir / row["dir"]
    vfile = idir / ("iter%d_verdict.json" % args.iter)
    if not vfile.exists():
        fail("Missing %s" % vfile)
    verdict = json.loads(vfile.read_text(encoding="utf-8"))
    scores = {r["render"]: float(r["score"]) for r in verdict["ranking"]}
    best_name = max(scores, key=scores.get)
    best_iter = int(best_name.split("_")[0].removeprefix("iter"))

    it = iteration(row, args.iter)
    it.update(finished_at=now(), scores=scores,
              differences=verdict.get("differences", []),
              categories=verdict.get("categories", {}))
    row["best"] = {"render": best_name, "score": scores[best_name],
                   "iteration": best_iter}

    cfg = row["config"]
    done = args.iter
    if done < cfg["min_iterations"]:
        go, why = True, "below min_iterations (%d)" % cfg["min_iterations"]
    elif scores[best_name] >= cfg["stop_score"]:
        go, why = False, "best score %.1f reached stop_score %s" % (
            scores[best_name], cfg["stop_score"])
    elif done >= cfg["max_iterations"]:
        go, why = False, "max_iterations (%d) reached" % cfg["max_iterations"]
    else:
        go, why = True, "best score %.1f below stop_score %s" % (
            scores[best_name], cfg["stop_score"])

    if go:
        set_current(state, row["name"], done + 1, "writing")
        iteration(row, done + 1, create=True)
    save(rdir, state)
    out({"ok": True, "continue": go, "why": why,
         "next_iteration": done + 1 if go else None,
         "best": row["best"],
         "best_prompt": str(idir / ("iter%d_prompt.txt" % best_iter)),
         "verdict": str(vfile)})


def cmd_finish(args) -> None:
    """Close an image: copy its best render and prompt, or mark it skipped."""
    rdir, state = load()
    row = find(state, args.image)
    idir = rdir / row["dir"]
    # An iteration opened for writing but never rendered is not one.
    row["iterations"] = [it for it in row["iterations"] if it["renders"]]
    row.update(status=args.status, finished_at=now(), note=args.note)
    if args.status == "done" and row["best"]:
        n = row["best"]["iteration"]
        shutil.copy2(idir / row["best"]["render"], idir / "best.png")
        shutil.copy2(idir / ("iter%d_prompt.txt" % n),
                     idir / "best_prompt.txt")
        negative = idir / ("iter%d_negative.txt" % n)
        (idir / "best_negative.txt").write_text(
            negative.read_text(encoding="utf-8") if negative.exists()
            else state["default_negative"], encoding="utf-8")
    clear_current(state, row["name"])
    if all(r["status"] != "pending" for r in state["images"]):
        state["finished_at"] = now()
    save(rdir, state)
    out({"ok": True, "image": row["name"], "status": row["status"],
         "best": row["best"],
         "remaining": sum(r["status"] == "pending" for r in state["images"])})


def cmd_status(_args) -> None:
    rdir, state = load(locked=False)
    out({"run": str(rdir), "folder": state["folder"],
         "tab": (state.get("tab") or TAB), "preset": state["preset"],
         "overrides": (state.get("overrides") or {}),
         "active": list(state["active"].values()),
         "images": [{"name": r["name"], "status": r["status"],
                     "iterations": len([i for i in r["iterations"]
                                        if i["renders"]]),
                     "best": r["best"], "note": r["note"]}
                    for r in state["images"]]})


def main() -> None:
    # The backend's status lines carry emoji; a Windows console is cp1252.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="img2prompt loop control")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--folder", required=True)
    for name in ("begin", "render", "judge", "finish", "phase"):
        p = sub.add_parser(name)
        p.add_argument("--image", required=True)
        if name in ("render", "judge", "phase"):
            p.add_argument("--iter", type=int, required=True)
        if name == "phase":
            p.add_argument("--phase", required=True,
                           choices=("writing", "judging"))
        if name == "finish":
            p.add_argument("--status", default="done",
                           choices=("done", "skipped", "failed"))
            p.add_argument("--note")
    sub.add_parser("status")
    args = parser.parse_args()
    {"init": cmd_init, "begin": cmd_begin, "phase": cmd_phase,
     "render": cmd_render, "judge": cmd_judge, "finish": cmd_finish,
     "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
