"""A visible, editable queue in front of every generation tab.

Why this module exists
----------------------
Generation used to happen *inside* the Gradio event the Generate button
fired: the click stayed open for the whole render, and Gradio's default
`trigger_mode="once"` left the button dead until it came back. A second
idea had to wait for the first render to finish and for a human to be
sitting there to click again — the pod idles between jobs for no reason
other than that nobody was watching.

Gradio's own queue does serialise the work (`concurrency_id="comfy"` still
expresses that, and this module keeps the same rule), but it is invisible
and nothing can be taken back out of it. A queue you cannot see and cannot
edit is a waiting room, not a queue.

So a click now only *records* the work — which function, with which
arguments, for which tab — and returns in microseconds. The button comes
straight back, and a worker thread per lane runs the recorded jobs one at
a time in arrival order. ui.py polls this module on a `gr.Timer` to draw
the queue and to carry each job's output back into the tab it came from.

Lanes
-----
A lane is one worker, so a lane is exactly the "only one of these at a
time" rule the `concurrency_id` used to state: image work runs on "comfy",
video work on "wan" when it has a ComfyUI instance of its own and on
"comfy" when it shares one. Registering a lane starts its worker, and
ui.py registers each with the ComfyUI client that serves it so a running
job can be interrupted.

Everything here is process-wide, like the ComfyUI server it feeds. Two
browser tabs open on the same pod see one queue, which is the truthful
picture: there is one GPU behind them.
"""

import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from config import log

# Job states. QUEUED and RUNNING are the live ones; the other three are
# terminal and differ only in what they tell the customer.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

FINISHED = (DONE, FAILED, CANCELLED)

# How many finished jobs stay on the list. They are kept at all because a
# tab reads its gallery back out of its last job (see display_for) — drop
# them on completion and the images vanish the moment they arrive. Twenty
# is well past what anyone scrolls and costs only the paths.
HISTORY = 20


@dataclass
class Job:
    """One queued unit of work — a whole Generate click, batch and all.

    `fn` and `args` are the tab's own handler and the exact values its
    inputs held at click time, so nothing about a queued job can be
    changed by touching the controls afterwards: the recipe is frozen the
    moment it is submitted, which is the behaviour a queue has to have.
    """

    id: str
    lane: str
    tab: str                    # features.Key value — routes results back
    tab_label: str              # what the tab is called on screen
    title: str                  # the prompt (or "—"), trimmed for the list
    fn: Callable
    args: tuple
    # What the handler's yield tuple *means*, position by position:
    # ("images", "status", "seed") for a picture tab, ("videos", "latest",
    # "status", "seed") for the video one. The tab says so when it
    # submits, so _freeze can hand back a dict instead of a tuple and
    # nothing downstream has to know that "the status is the second one,
    # except on the video tab where it is the third".
    result_keys: tuple = ()
    submitted: float = field(default_factory=time.time)
    status: str = QUEUED
    progress: str = "Waiting for its turn"
    # The last thing `fn` yielded, keyed by result_keys. None until the
    # job has produced anything.
    result: dict | None = None
    revision: int = 0
    started: float | None = None
    finished: float | None = None
    _stop: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class JobView:
    """A snapshot row. Frozen and free of `fn`/`args` on purpose: the UI
    renders these from a request thread while a worker mutates the real
    Job, and handing out the live object would be a data race with a
    friendly face."""

    id: str
    lane: str
    tab: str
    tab_label: str
    title: str
    status: str
    progress: str
    submitted: float
    place: int          # 1-based place among those waiting, 0 if not one


# One lock for everything. The critical sections are all "read or write a
# few fields", never IO — the ComfyUI calls that can block are made
# outside it (see cancel) — so a single lock costs nothing and removes
# every ordering question between the workers and the request threads.
_LOCK = threading.Lock()
_WAKE = threading.Condition(_LOCK)
_JOBS: list[Job] = []
_LANES: dict[str, Callable[[], None]] = {}
_WORKERS: dict[str, threading.Thread] = {}

# One counter behind both staleness checks the UI makes. Every change
# stamps the job it touched and the queue as a whole, so a poll can ask
# "newer than what I drew last time?" of either without diffing anything.
_TICKS = itertools.count(1)
_QUEUE_REVISION = 0

# Bumped whenever a background job writes a preset, so the tab's preset
# dropdown can be refilled by the same poll that carries the images back.
# The save no longer happens on the click that asked for it — it happens
# wherever the job eventually runs — so nothing else is in a position to
# notice it. Keyed by presets tab id.
_PRESET_REVISION: dict[str, int] = {}


def _stamp(job: Job | None = None) -> int:
    """Record a change. Caller holds _LOCK."""
    global _QUEUE_REVISION
    _QUEUE_REVISION = next(_TICKS)
    if job is not None:
        job.revision = _QUEUE_REVISION
    return _QUEUE_REVISION


def _trim() -> None:
    """Drop the oldest finished jobs past HISTORY. Caller holds _LOCK."""
    done = [job for job in _JOBS if job.status in FINISHED]
    for job in done[:max(0, len(done) - HISTORY)]:
        _JOBS.remove(job)


def register_lane(lane: str, interrupt: Callable[[], None]) -> None:
    """Declare a lane and start its worker.

    `interrupt` is how a *running* job on this lane is stopped — for both
    of ours, the ComfyUI /interrupt of the instance that serves it. Called
    at import time from ui.py; the worker parks on the condition variable
    until something is submitted, so starting it before ComfyUI is up
    costs nothing.
    """
    with _LOCK:
        _LANES[lane] = interrupt
        if lane in _WORKERS:
            return
        worker = threading.Thread(target=_worker, args=(lane,),
                                  name=f"jobqueue-{lane}", daemon=True)
        _WORKERS[lane] = worker
    worker.start()
    log.info("Job queue: %s lane ready", lane)


def submit(*, lane: str, tab: str, tab_label: str, title: str,
           fn: Callable, args: tuple,
           result_keys: tuple = ("images", "status")) -> JobView:
    """Record a click as a job and return its place in the queue.

    Returns a JobView rather than an id because the caller's next act is
    always to tell the customer where their work landed.
    """
    job = Job(
        id=uuid.uuid4().hex[:12], lane=lane, tab=str(tab),
        tab_label=tab_label, title=title or "—",
        fn=fn, args=tuple(args), result_keys=tuple(result_keys),
    )
    with _WAKE:
        _JOBS.append(job)
        _stamp(job)
        _trim()
        _WAKE.notify_all()
        view = _view(job)
    log.info("Queued %s job %s — %s", tab_label, job.id, job.title)
    return view


def cancel(job_id: str) -> str:
    """Take a job out of the queue, or stop it if it has already started.

    Returns a line for the status box. Both halves are needed and they
    are genuinely different acts: a queued job is simply forgotten, while
    a running one has a prompt on the ComfyUI server that has to be
    interrupted or it would carry on rendering into a job nobody is
    waiting for any more.

    Stopping is asynchronous by nature. The interrupt aborts the prompt
    ComfyUI is executing *now*; the worker sees the flag at its next yield
    and stops feeding it the rest of the batch. So a multi-image job can
    still finish the picture it is on before the queue lets go of it —
    which is why this says "stopping" rather than "stopped".
    """
    with _LOCK:
        job = next((j for j in _JOBS if j.id == job_id), None)
        if job is None:
            return "⚠️ That job is no longer in the queue."
        title = job.title
        if job.status == QUEUED:
            job.status = CANCELLED
            job.progress = "Removed before it started"
            job.finished = time.time()
            _stamp(job)
            return f"🗑️ Removed “{title}” from the queue."
        if job.status in FINISHED:
            _JOBS.remove(job)
            _stamp()
            return f"🗑️ Cleared “{title}”."
        job._stop.set()
        job.progress = "🛑 Stopping — interrupting ComfyUI…"
        _stamp(job)
        interrupt = _LANES.get(job.lane)
    # Outside the lock: this is an HTTP round trip to ComfyUI and must not
    # hold up the poll that is about to redraw the queue.
    if interrupt is not None:
        try:
            interrupt()
        except Exception as exc:      # noqa: BLE001 - reported, never fatal
            log.warning("Could not interrupt %s: %s", job_id, exc)
    return f"🛑 Stopping “{title}” …"


def clear_finished() -> str:
    """Drop every finished job from the list.

    The galleries showing their images are untouched — a Gradio component
    keeps the value it was last given — so this only tidies the list.
    """
    with _LOCK:
        gone = [job for job in _JOBS if job.status in FINISHED]
        for job in gone:
            _JOBS.remove(job)
        if gone:
            _stamp()
    return f"🧹 Cleared {len(gone)} finished job(s)." if gone else ""


def revision() -> int:
    """The queue's version. Changes on every submit, state change or
    removal, so a poll can skip redrawing when nothing has happened."""
    with _LOCK:
        return _QUEUE_REVISION


def snapshot() -> tuple[int, list[JobView], int, int]:
    """(revision, rows, waiting, running) — everything the panel draws.

    Oldest first, which is the order they will run in. Taken in one lock
    so the counts cannot disagree with the rows they are counting.
    """
    with _LOCK:
        return (_QUEUE_REVISION,
                [_view(job) for job in _JOBS],
                sum(1 for job in _JOBS if job.status == QUEUED),
                sum(1 for job in _JOBS if job.status == RUNNING))


def _view(job: Job) -> JobView:
    """One row. Caller holds _LOCK."""
    place = 0
    if job.status == QUEUED:
        place = 1 + sum(1 for other in _JOBS
                        if other.status == QUEUED
                        and other.submitted < job.submitted)
    return JobView(id=job.id, lane=job.lane, tab=job.tab,
                   tab_label=job.tab_label, title=job.title,
                   status=job.status, progress=job.progress,
                   submitted=job.submitted, place=place)


def display_for(tab: str) -> tuple[int, dict | None]:
    """(revision, latest yield) for the job that tab should be showing.

    "Should be showing" is the newest job for that tab that has actually
    *begun* — so a tab switches to a new run the moment it starts, not
    when it was queued, and goes on showing the last finished run while
    three more sit waiting behind it. That is what the tab did before
    there was a queue, and it is the only reading under which the gallery
    and the status line always describe the same run.
    """
    with _LOCK:
        for job in reversed(_JOBS):
            if str(job.tab) == str(tab) and job.status != QUEUED:
                return job.revision, job.result
    return 0, None


def note_preset_saved(tab: str) -> int:
    """Called by the preset save itself — see _PRESET_REVISION."""
    with _LOCK:
        _PRESET_REVISION[tab] = next(_TICKS)
        return _PRESET_REVISION[tab]


def preset_revision(tab: str) -> int:
    with _LOCK:
        return _PRESET_REVISION.get(tab, 0)


# ------------------------------------------------------------------ workers

def _worker(lane: str) -> None:
    """Run this lane's jobs, one at a time, forever."""
    while True:
        job = _claim(lane)
        try:
            _run(job)
        except Exception:                       # noqa: BLE001
            # _run handles its own failures; anything reaching here is a
            # bug in this module, and a dead worker would silently stop
            # the whole lane.
            log.exception("Job queue: %s worker recovered from a crash", lane)


def _claim(lane: str) -> Job:
    """Block until this lane has a queued job, and mark it running."""
    with _WAKE:
        while True:
            for job in _JOBS:
                if job.lane == lane and job.status == QUEUED:
                    job.status = RUNNING
                    job.started = time.time()
                    job.progress = "Starting…"
                    _stamp(job)
                    return job
            _WAKE.wait()


def _run(job: Job) -> None:
    """Drive one job's handler to the end, recording what it yields.

    The handlers are the same generators the click used to consume, so
    every partial render — "job 2/4", a half-finished gallery — still
    arrives here exactly as it used to arrive in the browser. It is
    recorded on the job instead, and the UI's poll picks it up.
    """
    outcome = None
    failure = None
    try:
        outcome = job.fn(*job.args)
        if hasattr(outcome, "__next__"):
            for value in outcome:
                with _LOCK:
                    job.result = _freeze(job, value)
                    job.progress = _progress_of(job, job.result)
                    _stamp(job)
                if job._stop.is_set():
                    break
        else:
            with _LOCK:
                job.result = _freeze(job, outcome)
                job.progress = _progress_of(job, job.result)
                _stamp(job)
    except Exception as exc:                    # noqa: BLE001
        # A handler that raises is a bug, but it must not take the lane
        # down with it — the next job is someone else's work.
        log.exception("Job %s (%s) failed", job.id, job.tab_label)
        failure = f"❌ {type(exc).__name__}: {exc}"
    finally:
        # Closing the generator is what actually abandons a batch: the
        # handler is parked on its last yield, and close() raises
        # GeneratorExit there so its `for job in jobs` loop unwinds
        # instead of queueing the rest of the batch to a ComfyUI nobody
        # is waiting on.
        if outcome is not None and hasattr(outcome, "close"):
            outcome.close()

    with _LOCK:
        job.finished = time.time()
        if failure is not None:
            job.status, job.progress = FAILED, failure
        elif job._stop.is_set():
            job.status, job.progress = CANCELLED, "🛑 Stopped"
        else:
            job.status = DONE
            job.progress = _progress_of(job, job.result) or "✅ Finished"
        _stamp(job)


def _freeze(job: Job, value) -> dict:
    """A yield, named and copied so it cannot change under its reader.

    **Named**, because a positional tuple was the one Gradio-shaped thing
    left in this module: `result[1]` is the status on nine tabs and
    `result[2]` on the video one, and every consumer had to be told which.
    Zipping `job.result_keys` on removes the question.

    **Copied**, and that half is load-bearing in a way that looks like
    tidiness. _run_jobs builds one gallery list and extends it in place,
    so the list it yields after job 1 is the *same object* it yields after
    job 4. Recorded as-is, a job would rewrite its own history — and
    worse, the poll asking "has this tab's gallery changed since I last
    drew it?" would be comparing a list against itself and always
    answering no. The images from a finished batch would then never reach
    the screen at all.

    Shallow on purpose: the members are paths, strings and numbers.

    A handler that yields fewer values than it has keys — an early "that
    model is not downloaded" bails before it knows a seed — simply
    contributes fewer keys, and the reader treats a missing key the way it
    used to treat a short tuple.
    """
    row = value if isinstance(value, tuple) else (value,)
    return {key: (list(item) if isinstance(item, list) else item)
            for key, item in zip(job.result_keys, row)}


def _progress_of(job: Job, result: dict | None) -> str:
    """The status line out of a handler's yield.

    One lookup on every tab now. It used to be an index the tab had to
    supply — second for most, third for the video tab, which yields two
    output components before its status — and an index is exactly the kind
    of thing that stays right until somebody adds an output.
    """
    value = (result or {}).get("status")
    return value.strip() if isinstance(value, str) and value.strip() \
        else job.progress
