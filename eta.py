"""How long the running job has left — worked out here, because ComfyUI
never says.

Why this module exists
----------------------
The "ETA" ComfyUI shows is tqdm printing to its own console
(`12/20 [00:08<00:05, 1.6it/s]`). Nothing of it crosses the websocket: a
`progress` frame carries `{value, max, node}` and that is all. So an
estimate has to be made on this side, out of two things only this side
can know.

**What the sampler is doing right now.** The gaps between progress frames
are a seconds-per-step rate, and a rate times the steps left is a good
answer — but only while sampling. Everything around it sends nothing:
waking ComfyUI, loading or swapping the weights (often the biggest part of
an image run), encoding the prompt, decoding and saving. A step counter
alone is blind for exactly the part that takes longest.

**What the same kind of run took last time.** Every finished picture is
recorded here in phases — the silent stretch before the first step, each
sampler segment, the silent stretch after the last — under a key that says
which models it loaded and how big a thing it made. That is what gives the
customer a number the moment a job starts, before any step has arrived.

The two are blended: history until the sampler has been timed over a few
steps, the live rate for the rest of the segment it is in, history again
for whatever comes after it and for the pictures of the batch still to
come.

Where it plugs in
-----------------
One tracker per Generate click, made by `tracker()` in the executors in
handlers.py (`_run_jobs`, `_run_wan_jobs`) and fed the same client.run
events they already consume. It reports through `jobqueue.report_eta`.
Off the queue's worker threads — the golden snapshots and the dry run
drive the handlers directly — it is a tracker that does nothing, so those
neither see an estimate nor write a history file.

Nothing here may break a render. Every entry point swallows its own
failures and the worst outcome of a bug is a missing or wrong estimate.
"""

import hashlib
import json
import statistics
import threading
import time

import jobqueue
from config import WORKING_DIR, log

# Where the history lives: beside the images and the logs, so it survives
# restarts and is per-pod, which is right — an A40 and an RTX 5050 take
# very different times over the same graph.
HISTORY_FILE = WORKING_DIR / "eta_history.json"

# Per key, how many pictures are remembered. The median of the last few
# follows a changed GPU or ComfyUI within a handful of runs and shrugs off
# the one run that happened to share the card with something else.
SAMPLES = 7
# How many kinds of run are remembered at all. Least recently used goes.
MAX_KEYS = 200

# Frames needed in a sampler segment before its own rate is trusted over
# history. The first frame arrives *after* step one, whose time includes
# the sampler's warm-up, so rates are measured from it rather than from
# the segment's start — two frames is one clean interval, three is two.
# With no history to fall back on, one clean interval is enough: a rough
# number at step 2 beats none until step 3 on a card doing 10s a step.
MIN_FRAMES = 3
MIN_FRAMES_BLIND = 2

# How hard a fresh estimate is pulled toward the previous one while the
# same segment is being timed. Rates wobble step to step; a countdown that
# lurches by twenty seconds on every frame reads as a broken one.
SMOOTHING = 0.35

# The graph inputs that decide how long a run takes. Strings are left out
# on purpose — prompts, filenames and uploaded image names change on every
# click and cost nothing — except the weights, which model_signature
# already covers. Seeds are numbers but cost nothing either.
#
# Step counts are deliberately NOT here. Sampling time is proportional to
# them and nothing else is, so they are recorded with each sample and the
# history is rescaled to the run at hand (see _profile). Keying on them
# meant moving the steps slider threw every past timing away — including
# the model load, which does not care how many steps follow it.
_SHAPE_INPUTS = frozenset({
    "width", "height", "length", "batch_size", "num_frames",
    "frames", "megapixels", "fps", "duration", "upscale_by", "scale_by",
})

_LOCK = threading.Lock()
_HISTORY: dict | None = None        # loaded on first use
_LAST_SIG: dict[str, tuple] = {}     # ComfyUI base URL -> weights it holds


# ------------------------------------------------------------------ history

def _load() -> dict:
    """The history, read once. Caller holds _LOCK."""
    global _HISTORY
    if _HISTORY is None:
        try:
            _HISTORY = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if not isinstance(_HISTORY, dict):
                _HISTORY = {}
        except FileNotFoundError:
            _HISTORY = {}
        except Exception as exc:              # noqa: BLE001 - never fatal
            log.warning("ETA history unreadable, starting afresh: %s", exc)
            _HISTORY = {}
    return _HISTORY


def _save(history: dict) -> None:
    """Write through a temp file so a crash mid-write cannot leave half a
    JSON document for the next start to trip on. Caller holds _LOCK."""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(history, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(HISTORY_FILE)
    except Exception as exc:                  # noqa: BLE001 - never fatal
        log.warning("Could not save ETA history: %s", exc)


def shape_key(prefix: str, workflow: dict, signature: tuple,
              sizes_count: bool = True) -> str:
    """Which runs count as 'the same kind' for timing purposes.

    Derived from the graph rather than from the tab's own arguments, for
    the reason model_signature is: every tab gets it for free and none can
    forget to declare a field. The node types are in it so an edit graph
    and a text-to-image graph over the same weights stay apart.

    With `sizes_count=False` it is the coarse key: same graph and weights,
    any size. What a first click at a new resolution falls back on, with
    the timings rescaled by pixel count (see _profile).
    """
    types, sizes = set(), []
    for node_id, node in sorted(workflow.items(), key=lambda kv: str(kv[0])):
        if not isinstance(node, dict):
            continue
        types.add(str(node.get("class_type", "")))
        for name, value in sorted((node.get("inputs") or {}).items()):
            if name in _SHAPE_INPUTS and isinstance(value, (int, float)) \
                    and not isinstance(value, bool):
                sizes.append(f"{name}={value}")
    raw = repr((sorted(types), sizes if sizes_count else [], signature))
    if not sizes_count:
        prefix += "~any"
    return f"{prefix}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


def workflow_steps(workflow: dict) -> int:
    """How many sampler steps the graph asks for, as one number to scale
    history by. A two-stage graph (KSamplerAdvanced twice, each told the
    whole schedule and a start/end) counts the span each stage runs; a
    plain sampler counts its steps."""
    total = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        steps = inputs.get("steps")
        if not isinstance(steps, int) or isinstance(steps, bool):
            continue
        start, end = inputs.get("start_at_step"), inputs.get("end_at_step")
        if isinstance(start, int) and isinstance(end, int):
            total += max(0, min(end, steps) - max(0, start))
        else:
            total += steps
    return total


def workflow_area(workflow: dict) -> float:
    """Pixels the graph renders — times frames, for video — as one number
    to rescale history by when only the coarse key has any. 0 when the
    graph does not say (an edit graph sized from its upload), which turns
    the rescaling off rather than guessing."""
    width = height = frames = 0
    megapixels = 0.0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}

        def num(name):
            value = inputs.get(name)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            return value if ok else 0
        width, height = max(width, num("width")), max(height, num("height"))
        megapixels = max(megapixels, num("megapixels"))
        frames = max(frames, num("length"), num("num_frames"), num("frames"))
    pixels = width * height if width and height else megapixels * 1e6
    return float(pixels * (frames or 1))


def _record(key: str, sample: dict) -> None:
    with _LOCK:
        history = _load()
        entry = history.pop(key, None) or {"samples": []}
        entry["samples"] = (entry.get("samples") or [])[-(SAMPLES - 1):] + [sample]
        entry["used"] = time.time()
        history[key] = entry                   # re-inserted: newest last
        while len(history) > MAX_KEYS:
            oldest = min(history, key=lambda k: history[k].get("used", 0))
            history.pop(oldest)
        _save(history)


def _profile(key: str, cold: bool, steps: int = 0,
             area: float = 0.0) -> dict | None:
    """What one picture of this kind is expected to cost, phase by phase.

    Medians over the remembered samples. Segments are only combined across
    samples that had the same number of them — a graph that grew a second
    sampler is a different shape of run, and the newest is the one that
    counts. The silent stretch before the first step is taken from runs
    that were as cold as this one where there are any: a swap can make it
    ten times longer, and averaging the two would be wrong both ways.

    Segment times are rescaled from the step count each sample ran to
    `steps`, the count this run asks for, so a 4-step run's timings predict
    an 11-step one. Only the segments scale; loading and decoding do not
    depend on steps. Likewise by pixel count (`area`), which only ever
    differs under the coarse key: segments and decoding scale with it,
    loading does not. Linear in pixels is an approximation — attention is
    worse than linear — and the live rate corrects it within two steps.
    """
    with _LOCK:
        entry = _load().get(key)
        samples = list((entry or {}).get("samples") or [])
    if not samples:
        return None
    segments = len(samples[-1].get("segs") or [])
    same = [s for s in samples if len(s.get("segs") or []) == segments]
    alike = [s for s in same if bool(s.get("cold")) == cold] or same

    def by_area(sample) -> float:
        was = float(sample.get("area") or 0)
        return area / was if area > 0 and was > 0 else 1.0

    def scale(sample) -> float:
        ran = int(sample.get("steps") or 0)
        return (steps / ran if steps > 0 and ran > 0 else 1.0) * by_area(sample)

    try:
        return {
            "pre": statistics.median(float(s["pre"]) for s in alike),
            "segs": [
                [statistics.median(round(int(s["segs"][i][0]) * scale(s))
                                   for s in same),
                 statistics.median(float(s["segs"][i][1]) * scale(s)
                                   for s in same)]
                for i in range(segments)
            ],
            "post": statistics.median(float(s["post"]) * by_area(s)
                                      for s in same),
        }
    except Exception:                          # noqa: BLE001 - a bad file
        return None


def _total(profile: dict | None) -> float | None:
    if not profile:
        return None
    return profile["pre"] + sum(d for _, d in profile["segs"]) + profile["post"]


# ------------------------------------------------------------------ warmth

def is_cold(base: str, signature: tuple) -> bool:
    """Whether this ComfyUI has to load weights before it can start.

    Kept here rather than read from handlers._LAST_MODEL_SIG, which only
    moves when KREA2_KEEP_MODELS_LOADED is off — the load happens either
    way. Marks the signature as loaded as a side effect.
    """
    with _LOCK:
        cold = _LAST_SIG.get(base) != signature
        _LAST_SIG[base] = signature
    return cold


def forget(base: str) -> None:
    """That ComfyUI has restarted: whatever it held is gone."""
    with _LOCK:
        _LAST_SIG.pop(base, None)


# ------------------------------------------------------------------ tracker

class Tracker:
    """Times one Generate click — every picture in its batch — and keeps
    the queue's estimate of when it will finish up to date.

    The executor calls, in order, per picture:
        start(workflow, cold)   before client.run
        event(e)                for every event client.run yields
    and the tracker reports after each. Pictures it never saw finish — a
    failure, a cancel — are simply not recorded.
    """

    def __init__(self, prefix: str, total: int, base: str, report):
        self.prefix = prefix
        self.total = max(1, int(total))
        self.base = base
        self.report = report                # jobqueue.report_eta
        self.index = 0                      # pictures started
        self.done: list[float] = []         # wall times of finished ones
        self.key = None
        self.profile = None                 # history for this picture
        self.warm_profile = None            # ...and for the ones after it
        self.cold = False
        self.steps = 0
        self.area = 0.0
        self.coarse_key = None
        self._reset_picture()

    def _reset_picture(self) -> None:
        self.t_start = time.time()
        self.t_first = None                 # first progress frame
        self.t_last = None                  # latest progress frame
        self.segs: list[list] = []          # finished [max, seconds]
        self.seg_node = None
        self.seg_max = 0
        self.seg_value = 0
        self.seg_t0 = None                  # when the segment began
        self.seg_frames: list[tuple] = []   # (time, value) in this segment
        self.finish_at = None               # smoothed, absolute

    # ---- inputs

    def start(self, workflow: dict, signature: tuple, cold: bool) -> None:
        try:
            self.index += 1
            self._reset_picture()
            self.cold = cold
            self.key = shape_key(self.prefix, workflow, signature)
            self.coarse_key = shape_key(self.prefix, workflow, signature,
                                        sizes_count=False)
            self.steps = workflow_steps(workflow)
            self.area = workflow_area(workflow)

            def profile(cold_run):
                return (_profile(self.key, cold_run, self.steps, self.area)
                        or _profile(self.coarse_key, cold_run, self.steps,
                                    self.area))
            self.profile = profile(cold)
            self.warm_profile = profile(False)
            self._publish()
        except Exception as exc:              # noqa: BLE001
            log.debug("ETA start failed: %s", exc)

    def event(self, event: dict) -> None:
        try:
            now = time.time()
            if event.get("type") == "progress" and event.get("total"):
                self._progress(now, event)
            elif event.get("type") == "done":
                self._finish(now)
            self._publish()
        except Exception as exc:              # noqa: BLE001
            log.debug("ETA event failed: %s", exc)

    def _progress(self, now: float, event: dict) -> None:
        value, top = int(event.get("step") or 0), int(event["total"])
        node = event.get("node")
        new_segment = (self.seg_t0 is None or node != self.seg_node
                       or top != self.seg_max or value < self.seg_value)
        if new_segment:
            if self.seg_t0 is not None:
                self._close_segment(self.t_last or now)
            # Where the segment began: the end of the one before, or — for
            # the first — the moment the silence before it ended, which is
            # one step's worth before this frame. That step is attributed
            # to the segment, not to the "pre" phase.
            self.seg_t0 = self.t_last if self.t_last is not None else now
            self.seg_node, self.seg_max = node, top
            self.seg_frames = []
            self.finish_at = None           # a new phase: no smoothing across
        if self.t_first is None:
            self.t_first = now
        self.t_last = now
        self.seg_value = value
        self.seg_frames.append((now, value))

    def _close_segment(self, end: float) -> None:
        self.segs.append([self.seg_max, max(0.0, end - self.seg_t0)])
        self.seg_t0 = None

    def _finish(self, now: float) -> None:
        if self.seg_t0 is not None:
            self._close_segment(self.t_last or now)
        wall = now - self.t_start
        self.done.append(wall)
        if self.t_first is not None:
            # The first segment's first step happened before its first
            # frame — seg_t0 was set at that frame — so pull that step's
            # share out of "pre", where it would otherwise sit.
            first_step = 0.0
            if self.segs and self.segs[0][0] > 1:
                first_step = self.segs[0][1] / max(1, self.segs[0][0] - 1)
                self.segs[0][1] += first_step
            pre = max(0.0, self.t_first - self.t_start - first_step)
            post = max(0.0, now - (self.t_last or now))
        else:
            # A graph that never reported a step: all of it is one silence.
            pre, post = wall, 0.0
        if self.key:
            sample = {"pre": round(pre, 2),
                      "segs": [[m, round(d, 2)] for m, d in self.segs],
                      "post": round(post, 2), "steps": self.steps,
                      "area": self.area, "cold": self.cold, "at": round(now)}
            _record(self.key, sample)
            if self.coarse_key:
                _record(self.coarse_key, sample)
        self.key = None                     # this picture is accounted for
        self.seg_frames = []
        self.finish_at = None

    # ---- output

    def _current_remaining(self, now: float) -> float | None:
        """Seconds left on the picture being rendered, or None."""
        if self.key is None:                 # between pictures
            return 0.0
        profile = self.profile
        segs_hist = (profile or {}).get("segs") or []
        post = (profile or {}).get("post")
        if self.t_first is None:
            # Still in the silence before the first step.
            if profile is None:
                return None
            left = _total(profile) - (now - self.t_start)
            # Past what history expected, the picture is not "done in 0s":
            # it is late by an unknown amount. Hold a small floor instead
            # of counting down through zero.
            return max(left, 0.1 * _total(profile))

        index = len(self.segs)               # the segment being run
        hist = segs_hist[index] if index < len(segs_hist) else None
        left_here = None
        needed = MIN_FRAMES if hist is not None else MIN_FRAMES_BLIND
        if len(self.seg_frames) >= needed and self.seg_value > self.seg_frames[0][1]:
            t0, v0 = self.seg_frames[0]
            rate = (self.t_last - t0) / (self.seg_value - v0)
            left_here = (self.seg_max - self.seg_value) * rate \
                - (now - self.t_last)
            left_here = max(0.0, left_here)
        elif hist is not None:
            left_here = max(0.0, hist[1] - (now - self.seg_t0))
        if left_here is None:
            return None
        after = sum(d for _, d in segs_hist[index + 1:])
        return left_here + after + (post if post is not None else 0.0)

    def _per_picture(self) -> float | None:
        """What each picture still to come should take. They run on the
        weights this one loaded, so warm history is the right basis; this
        batch's own finished pictures are better still once there are
        any beyond a cold first one."""
        warm_done = self.done[1:] if self.cold else self.done
        if warm_done:
            return statistics.median(warm_done)
        guess = _total(self.warm_profile) or _total(self.profile)
        if guess is not None:
            return guess
        return self.done[0] if self.done else None

    def remaining(self) -> float | None:
        now = time.time()
        current = self._current_remaining(now)
        if current is None:
            return None
        still = self.total - self.index      # pictures not yet started
        if still > 0:
            each = self._per_picture()
            if each is None:
                # Nothing known about a whole picture yet. The one being
                # rendered is the best guide there is, once it can be
                # projected end to end.
                if self.key is None:
                    return None
                each = (now - self.t_start) + current
            current += still * each
        return current

    def _publish(self) -> None:
        left = self.remaining()
        if left is None:
            self.report(None)
            return
        target = time.time() + left
        # Smoothed only while one segment's rate is being measured; a new
        # segment or picture resets finish_at, and a phase change is a
        # real change the number should show at once.
        if self.finish_at is not None and len(self.seg_frames) > MIN_FRAMES_BLIND:
            target = self.finish_at + SMOOTHING * (target - self.finish_at)
        self.finish_at = target
        self.report(target)


class _Idle:
    """The tracker a handler gets when it is not running as a queued job."""

    def start(self, *_args, **_kwargs) -> None:
        pass

    def event(self, *_args, **_kwargs) -> None:
        pass


def tracker(prefix: str, total: int, base: str):
    """A Tracker for the job this thread is running, or one that does
    nothing when there is no such job."""
    if not jobqueue.on_worker():
        return _Idle()
    return Tracker(prefix, total, base, jobqueue.report_eta)
