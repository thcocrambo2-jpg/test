"""Pod health checks: is the pod we just won (or any running pod) any good?

Everything is read, nothing is changed:

- the pod's logs, through Runpod's v2 logs stream (SSE). The *system* part
  has the image pull and the container's start/stop/restart lines; the
  *container* part is what the pod prints: the start script, Ember's own
  log, and — from Ember builds that have ember/podcheck.py — one
  `EMBER_PODCHECK {json}` line per measurement (net, gpu, host, ...);
- the pod (v2) and its machine (v1 with includeMachine): status, RAM,
  vCPU, the host's country, machine id and disk speed;
- the app itself, through the pod's proxy URL, once Ember says the UI is
  ready.

A build without podcheck still gets the log-only signals: image pull
speed, download speed worked out from the gaps between its "✓ file"
lines, errors, stuck states, the app coming up.

This module never terminates a pod. It warns (a finding, a sound, a
banner on the page) and the page asks; termination goes through the
normal pod action after the person says so.

Standard library only (Python 3.10+): friends run the sniper from a zip.
"""

import json
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HISTORY_FILE = HERE / "pods-history.jsonl"     # git-ignored
LAYERS_FILE = HERE / "image-layers.json"       # git-ignored: layer sizes per image digest
HOSTS_FILE = HERE / "hosts.json"               # git-ignored: blocked machines
GPUS_FILE = HERE / "gpus.json"                 # committed: optional seed baselines

V2 = "https://api.runpod.io/v2"
USER_AGENT = "curl/8.5.0"
IMAGE_REPO = "emberai/ember"
MIRROR_USER = "thcocrambo2"
MIRROR_REPOS = ("krea2-assets", "krea2-loras", "krea2-encoders")
FULL_DOWNLOAD_GB = 90           # what a fresh pod downloads, for the ETA on the card
PODCHECK_VERSION = 1

# Thresholds. Seconds are wall-clock seconds; speeds are MB/s (10^6 bytes).
T = {
    "net_warn": 50, "net_bad": 25,          # Ember's probe of the HF CDN
    "dl_warn": 50,                          # real download speed (Ember, or log gaps)
    "rx_warn": 30,                          # two live samples in a row below this
    "image_warn": 30,                       # image pull: a rough hint only
    "host_disk_warn": 2000,                 # Runpod's figure for the host disk
    "disk_read_warn": 1000,                 # Ember's own read test
    "ram_warn": 60,                         # GB; a 35 GB model swaps on a 41 GB host
    "h2d_warn": 10,                         # GB/s pinned host->GPU copy (Gen4 x8 ≈ 12)
    "gpu_score_warn": 0.85,                 # TFLOPS vs the learned baseline
    "tflops_drop_warn": 0.15,               # first vs last batch of the GPU test
    "power_cap_warn": 0.90,                 # power limit vs default
    "temp_warn": 87,                        # °C under load
    "render_slow": 1.2,                     # s/step vs other pods, same GPU + model
    "retry_warn": 5,                        # download retries
    "baseline_min": 3,                      # pods before a baseline is trusted
    "image_stuck_warn": 600, "image_stuck_bad": 1200,
    "no_start": 720,                        # no container start this long after the win
    "app_silent": 180,                      # container started, nothing printed
    "ui_late": 2700,                        # app started, no UI after this long
    "app_down": 180,                        # UI announced, proxy not answering
    "big_file": 300e6,                      # gap speed only from files this big
    "own_action": 120,                      # a start this soon after our own action is ours
}
GPU_FLAGS = {"gpu_slow", "gpu_throttle", "gpu_drop", "power_capped", "gpu_hot", "pcie_slow"}
BAD_THROTTLE = {"hw_slowdown", "hw_thermal", "sw_thermal", "hw_power_brake"}

# Set by server.py. Each is a plain function so tests and the dry run can
# swap them; none may raise for an HTTP status.
HOOKS = {
    "pod": None,       # (acct, pid) -> pod view dict, {"gone": True} on 404, or None
    "machine": None,   # (acct, pid) -> {"country", "machine", ...} or None
    "stream": None,    # (acct, url, headers, timeout) -> iterator of text lines
    "ext_get": None,   # (url, headers) -> (code, headers dict, text); outside Runpod
    "app_up": None,    # (pid) -> True / False
    "sound": None,     # () -> None, the soft warning sound
}


# ── small helpers ───────────────────────────────────────────────────

_TS_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?")


def _ts(text) -> float:
    """Runpod's "2026-11-06T11:39:55Z" as epoch seconds (UTC). 0 if unreadable.
    Written by hand: fromisoformat only takes "Z" from 3.11 on."""
    m = _TS_RE.match(str(text or ""))
    if not m:
        return 0.0
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    frac = float("0." + m.group(7)) if m.group(7) else 0.0
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() + frac


def _iso(t) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None


_REDACT = [
    (re.compile(r"#k=[^\s\"'<>]+"), "#k=…"),                    # the UI token
    (re.compile(r"\bhf_[A-Za-z0-9]{16,}"), "hf_…"),
    (re.compile(r"\brpa_[A-Za-z0-9]{16,}"), "rpa_…"),
    (re.compile(r"(license key )\S+", re.I), r"\1…"),
    (re.compile(r"\bEMBER-[A-Z0-9][A-Z0-9-]{3,}"), "EMBER-…"),
    (re.compile(r"(token=)[^\s&\"']+", re.I), r"\1…"),
]


def redact(text: str) -> str:
    """What may be kept from a log line: no UI token, no keys."""
    text = str(text or "")
    for rx, repl in _REDACT:
        text = rx.sub(repl, text)
    return text[:400]


def _num(v, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default            # NaN -> default


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(path)


# ── the log parser ──────────────────────────────────────────────────

LAYER_RE = re.compile(r"^([0-9a-f]{12}) (Already exists|Pulling fs layer|Waiting|Downloading|"
                      r"Verifying Checksum|Download complete|Extracting|Pull complete)(.*)$")
PROGRESS_RE = re.compile(r"([\d.]+)\s*([kKMG]?B)\s*/\s*([\d.]+)\s*([kKMG]?B)")
UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9}
DL_RE = re.compile(r"ember: (✓|↓) (\S+)(.*)$")
SUFFIX_RE = re.compile(r"— ([\d.]+) (GB|MB|kB|B) in ([\d.]+) s, ([\d.]+) MB/s\s*$")
PODCHECK = "EMBER_PODCHECK "

MILESTONES = [
    ("app_banner", re.compile(r"^\[ember-image\] built")),
    ("app_starting", re.compile(r"^\[ember\] starting")),
    ("app_build", re.compile(r"^\[ember\] (downloaded and verified|already have this build)")),
    ("features", re.compile(r"ember: Features — ")),
    ("downloads_start", re.compile(r"ember: (Downloading asset groups|No feature needs any model files)")),
    ("downloads_done", re.compile(r"ember: Downloads complete")),
    ("comfy_start", re.compile(r"ember: Starting ComfyUI")),
    ("ui_ready", re.compile(r">>> OPEN THE UI HERE")),
]

# code, level, source (None = either), pattern, what to tell the person
ERRORS = [
    ("pull_rate_limit", "bad", None, r"toomanyrequests", "Docker Hub's pull rate limit stopped the image pull"),
    ("pull_failed", "bad", "system", r"error pulling image|manifest unknown|failed to register layer|"
                                     r"unexpected EOF", "The image pull failed"),
    ("disk_full", "bad", None, r"no space left on device", "The disk is full"),
    ("nvidia_runtime", "bad", None, r"nvidia-container-cli|driver/library version mismatch|"
                                    r"unsatisfied condition: cuda|OCI runtime create failed",
     "NVIDIA runtime / driver error on the host"),
    ("start_failed", "bad", "container", r"^\[ember\] ERROR:", "The start script stopped with an error"),
    ("no_gpu", "bad", "container", r"No NVIDIA GPU visible", "No GPU is visible in the container"),
    ("cuda_error", "bad", "container", r"CUDA error|CUDA driver version is insufficient|"
                                       r"no kernel image is available", "CUDA error"),
    ("vram_oom", "warn", "container", r"OutOfMemoryError|CUDA out of memory", "Out of GPU memory"),
    ("killed", "bad", "container", r"^Killed$|\d+ Killed\s", "A process was killed (out of RAM?)"),
    ("model_unavailable", "warn", "container", r"\[ERROR\].* unavailable \(",
     "A model or LoRA could not be downloaded"),
    ("civitai_refused", "warn", "container", r"CivitAI refused the download", "CivitAI refused a download"),
    ("traceback", "warn", "container", r"^Traceback \(most recent call last\):", "Python crash in the app"),
    ("download_retry", "info", "container", r"failed \(attempt \d+/\d+\)", "A download had to be retried"),
    ("mirror_miss", "info", "container", r"Mirror \S+ could not serve", "The mirror missed a file (upstream used)"),
]
# Case-insensitive: most of these texts are not ours, and their capitals vary.
ERRORS = [(c, lvl, src, re.compile(rx, re.I), text) for c, lvl, src, rx, text in ERRORS]
IGNORE_RE = re.compile(r"container is unhealthy: dead")


class LogParser:
    """Turns log events ({"source", "line", "ts"}) into state. Not thread
    safe on its own: Health holds its lock around feed() and evaluate()."""

    def __init__(self):
        self._seen = {}                 # ts -> set of (source, line), last few seconds only
        self.lines = 0
        self.last_ts = 0.0
        self.first_ts = 0.0
        # image pull
        self.cached = set()
        self.fetch = set()
        self.layer_done = {}            # layer -> ts of "Download complete"
        self.progress = {}              # layer -> [first ts, last ts, done bytes, total bytes]
        self.pull_first = None          # first "Pulling fs layer"
        self.pull_end = None            # "Status: Downloaded newer image" / "up to date"
        self.digest = None
        self.fetching_since = None      # first "still fetching image"
        # lifecycle
        self.creates, self.starts, self.stops, self.restarts, self.removes = [], [], [], [], []
        # container side
        self.first_container = None
        self.milestones = {}
        self.dl = []                    # (ts, kind, file, tag, size, secs, mbs)
        self.pc = {}                    # kind -> latest record (with "_ts")
        self.rx = deque(maxlen=40)
        self.renders = deque(maxlen=200)
        self.pc_unknown = None          # a podcheck version this sniper does not know
        self.pc_bad = 0
        self.errors = {}                # code -> {"n", "first", "last", "text", "excerpt"}
        self._trace = None              # [code, lines] while collecting a traceback

    # -- input

    def feed(self, ev: dict) -> bool:
        """One event. False if it was a duplicate (a reconnect replays the
        second it resumed at)."""
        line = str(ev.get("line") or "").rstrip("\r\n")
        source = str(ev.get("source") or "")
        ts = _ts(ev.get("ts")) or self.last_ts
        key = (source, line)
        bucket = self._seen.setdefault(ts, set())
        if key in bucket:
            return False
        bucket.add(key)
        if ts > self.last_ts:
            self.last_ts = ts
            for old in [t for t in self._seen if t < ts - 5]:
                del self._seen[old]
        if not self.first_ts:
            self.first_ts = ts
        self.lines += 1
        if self._trace is not None:
            self._collect_trace(line)
        if source == "system":
            self._system(ts, line)
        else:
            self._container(ts, line)
        if not IGNORE_RE.search(line):
            self._errors(ts, source, line)
        return True

    def _system(self, ts, line):
        m = LAYER_RE.match(line)
        if m:
            layer, what, rest = m.groups()
            if what == "Already exists":
                self.cached.add(layer)
            elif what == "Pulling fs layer":
                self.fetch.add(layer)
                self.pull_first = self.pull_first or ts
            elif what == "Downloading":
                self.fetch.add(layer)
                p = PROGRESS_RE.search(rest)
                if p:
                    done = float(p.group(1)) * UNITS.get(p.group(2), 1)
                    total = float(p.group(3)) * UNITS.get(p.group(4), 1)
                    rec = self.progress.setdefault(layer, [ts, ts, 0.0, total])
                    rec[1], rec[2], rec[3] = ts, max(rec[2], done), total
            elif what == "Download complete":
                self.layer_done.setdefault(layer, ts)
                if layer in self.progress:
                    rec = self.progress[layer]
                    rec[1], rec[2] = ts, rec[3] or rec[2]
            return
        if line.startswith("Digest: sha256:"):
            self.digest = line.split("Digest: ", 1)[1].strip()
        elif line.startswith("Status: Downloaded newer image") or line.startswith("Status: Image is up to date"):
            self.pull_end = self.pull_end or ts
        elif line.startswith("create container: still fetching image"):
            self.fetching_since = self.fetching_since or ts
        elif line.startswith("create container"):
            self.creates.append(ts)
        elif line.startswith("start container") and line.rstrip().endswith("begin"):
            self.starts.append(ts)
        elif line.startswith("stop container"):
            self.stops.append(ts)
        elif line.startswith("restart container"):
            self.restarts.append(ts)
        elif line.startswith("remove container"):
            self.removes.append(ts)

    def _container(self, ts, line):
        self.first_container = self.first_container or ts
        i = line.find(PODCHECK)
        if i >= 0:
            self._podcheck(ts, line[i + len(PODCHECK):])
            return
        for name, rx in MILESTONES:
            if name not in self.milestones and rx.search(line):
                self.milestones[name] = ts
                if name == "downloads_start":
                    self.dl.append((ts, "mark", None, None, None, None, None))
                break
        m = DL_RE.search(line)
        if m:
            arrow, path, rest = m.groups()
            tag = ("cached" if "(cached)" in rest else "mirror" if "(mirror" in rest
                   else "private" if "(private)" in rest else "civitai" if "(CivitAI" in rest
                   else "upstream" if "(from " in rest else "other")
            size = secs = mbs = None
            s = SUFFIX_RE.search(rest)
            if s:
                size = float(s.group(1)) * UNITS.get(s.group(2), 1)
                secs, mbs = float(s.group(3)), float(s.group(4))
            kind = "start" if arrow == "↓" else ("cached" if tag == "cached" else "done")
            self.dl.append((ts, kind, path, tag, size, secs, mbs))
        elif " unavailable (" in line and "[ERROR]" in line:
            self.dl.append((ts, "fail", None, None, None, None, None))

    def _podcheck(self, ts, text):
        try:
            rec = json.loads(text)
        except ValueError:
            self.pc_bad += 1
            return
        if not isinstance(rec, dict) or "kind" not in rec:
            self.pc_bad += 1
            return
        if rec.get("v") != PODCHECK_VERSION:
            self.pc_unknown = rec.get("v")
            return
        rec["_ts"] = ts
        kind = rec["kind"]
        if kind == "rx":
            self.rx.append(rec)
        elif kind == "render":
            self.renders.append(rec)
        else:
            self.pc[kind] = rec

    def _errors(self, ts, source, line):
        for code, _level, src, rx, _text in ERRORS:
            if src and src != source:
                continue
            if rx.search(line):
                e = self.errors.setdefault(code, {"n": 0, "first": ts, "excerpt": []})
                e["n"] += 1
                e["last"] = ts
                if len(e["excerpt"]) < 3:
                    e["excerpt"].append(redact(line))
                if code in ("traceback", "start_failed"):
                    self._trace = [code, 0]
                return

    def _collect_trace(self, line):
        code, n = self._trace
        e = self.errors.get(code)
        if not e or n >= 15:
            self._trace = None
            return
        if line.strip():
            e["excerpt"].append(redact(line))
        self._trace[1] = n + 1

    # -- derived numbers

    def image(self, layer_sizes=None) -> dict:
        """The image pull: cached / fetched layers and, when it can be
        worked out, the download speed."""
        out = {"cached": len(self.cached), "fetched": len(self.fetch), "done": bool(self.pull_end),
               "digest": self.digest}
        if self.cached and not self.fetch and self.pull_end:
            out["method"] = "cached"
            return out
        if self.progress:
            first = min(r[0] for r in self.progress.values())
            last = max(r[1] for r in self.progress.values())
            got = sum(r[2] for r in self.progress.values())
            if last - first >= 2 and got > 50e6:
                out.update(method="progress", bytes=got, secs=last - first,
                           mbs=round(got / 1e6 / (last - first), 1))
                return out
        if layer_sizes and self.layer_done and self.pull_first:
            sizes = [layer_sizes.get(layer) for layer in self.layer_done]
            if all(s is not None for s in sizes):
                secs = max(self.layer_done.values()) - self.pull_first
                got = float(sum(sizes))
                if secs >= 2 and got > 50e6:
                    out.update(method="manifest", bytes=got, secs=secs,
                               mbs=round(got / 1e6 / secs, 1))
                    return out
        out["method"] = "unknown" if self.fetch else ("cached" if self.cached else "none")
        return out

    def needs_layer_sizes(self) -> bool:
        return bool(self.fetch and self.layer_done and not self.progress and self.digest
                    and self.pull_end)

    def mirror_files_without_size(self) -> bool:
        return any(k == "done" and tag == "mirror" and size is None
                   for _t, k, _f, tag, size, _s, _m in self.dl)

    def download(self, file_sizes=None) -> dict:
        """Download progress, and the speed: Ember's own figures when the
        build reports them, else the gaps between consecutive download
        lines (downloads run one after another) times the mirror's sizes."""
        done = sum(1 for e in self.dl if e[1] == "done")
        cached = sum(1 for e in self.dl if e[1] == "cached")
        failed = sum(1 for e in self.dl if e[1] == "fail")
        out = {"done": done, "cached": cached, "failed": failed}
        summary = self.pc.get("dl")
        if summary and _num(summary.get("mbs")):
            out.update(mbs=_num(summary["mbs"]), bytes=_num(summary.get("bytes")),
                       source="ember")
            return out
        got = secs = 0.0
        prev = None
        for ts, kind, path, tag, size, s, _mbs in self.dl:
            if kind == "done":
                if size is not None and s:
                    if size >= T["big_file"]:
                        got, secs = got + size, secs + s
                elif tag == "mirror" and prev is not None and file_sizes:
                    size = file_sizes.get(path.rsplit("/", 1)[-1])
                    gap = ts - prev
                    if size and size >= T["big_file"] and gap >= 1:
                        got, secs = got + size, secs + gap
            prev = ts
        if secs >= 2 and got:
            out.update(mbs=round(got / 1e6 / secs, 1), bytes=got,
                       source="ember" if any(e[4] is not None for e in self.dl) else "log gaps")
        return out


# ── judging ─────────────────────────────────────────────────────────


def baselines(records: list, seeds: dict = None, exclude: str = None) -> dict:
    """Per GPU id: the median TFLOPS and H2D GB/s of the last 10 clean
    pods (no GPU flag, verdict not bad). Under baseline_min pods, a seed
    from gpus.json is used if there is one; otherwise none ("learning")."""
    by_gpu = {}
    for rec in sorted(records, key=lambda r: r.get("won_at") or ""):
        if rec.get("pod") == exclude or not rec.get("gpu") or not _num(rec.get("gpu_tflops")):
            continue
        if GPU_FLAGS & set(rec.get("flags") or []) or rec.get("verdict") == "bad":
            continue
        by_gpu.setdefault(rec.get("gpu"), []).append(rec)
    out = {}
    for gpu, recs in by_gpu.items():
        recs = recs[-10:]
        out[gpu] = {"tflops": _median([_num(r.get("gpu_tflops")) for r in recs]),
                    "h2d_gbs": _median([_num(r.get("h2d_gbs")) for r in recs]),
                    "n": len(recs), "from": "history"}
    for gpu, seed in (seeds or {}).items():
        if (gpu not in out or out[gpu]["n"] < T["baseline_min"]) and _num(seed.get("tflops")):
            n = out.get(gpu, {}).get("n", 0)
            out[gpu] = {"tflops": _num(seed["tflops"]), "h2d_gbs": _num(seed.get("h2d_gbs")),
                        "n": n, "from": "seed"}
    return out


def render_baselines(records: list, exclude: str = None) -> dict:
    """(gpu, model key) -> median s/step over other pods with >= 3 renders,
    once at least 2 pods have one."""
    per = {}
    for rec in records:
        if rec.get("pod") == exclude:
            continue
        for key, r in (rec.get("render") or {}).items():
            if r.get("n", 0) >= 3 and _num(r.get("s_per_step")):
                per.setdefault((rec.get("gpu"), key), []).append(_num(r["s_per_step"]))
    return {k: _median(v) for k, v in per.items() if len(v) >= 2}


def guard_check(machine: dict, rules: dict, blocked: dict) -> tuple:
    """The location guard. ("ok" | "block" | "unknown", reason)."""
    machine = machine or {}
    mid = machine.get("machine")
    if mid and mid in (blocked or {}):
        return "block", "host %s is on your blocked list" % mid
    countries = [c.upper() for c in (rules or {}).get("countries") or []]
    if not countries:
        return "ok", ""
    country = (machine.get("country") or "").upper()
    if not country:
        return "unknown", "country not known"
    mode = (rules or {}).get("country_mode") or "block"
    if mode == "allow" and country not in countries:
        return "block", "%s is not in the allowed countries (%s)" % (country, ", ".join(countries))
    if mode == "block" and country in countries:
        return "block", "%s is a blocked country" % country
    return "ok", ""


def _finding(code, level, text):
    return {"code": code, "level": level, "text": text}


def _decode_pcie(gpu: dict) -> str:
    g, w = gpu.get("pcie_gen"), gpu.get("pcie_width")
    if g is None and w is None:
        return None
    return "Gen%s x%s" % (g if g is not None else "?", w if w is not None else "?")


def evaluate(p: LogParser, *, now: float, won_at: float = None, pod: dict = None,
             machine: dict = None, app: dict = None, actions=(), layer_sizes=None,
             file_sizes=None, baseline: dict = None, render_base: dict = None) -> dict:
    """Everything known about one pod -> metrics, findings, phase.
    Pure: same inputs, same answer, so the rules are unit-tested."""
    pod, machine, app = pod or {}, machine or {}, app or {}
    findings, m = [], {}

    # -- host, from Runpod
    m["country"] = machine.get("country")
    m["machine"] = machine.get("machine")
    m["host_dl_mbps"] = machine.get("host_dl_mbps")
    m["host_disk_mbs"] = machine.get("host_disk_mbs")
    host = p.pc.get("host") or {}
    ram = _num(host.get("ram_gb")) or _num(pod.get("ram"))
    m["ram_gb"], m["vcpu"] = ram, _num(host.get("cpus")) or _num(pod.get("vcpu"))
    if ram is not None and ram < T["ram_warn"]:
        findings.append(_finding("ram_low", "warn", "Only %d GB of RAM: a model over ~35 GB "
                                 "will swap while it loads" % ram))
    hd = _num(machine.get("host_disk_mbs"))
    if hd is not None and hd < T["host_disk_warn"]:
        findings.append(_finding("host_disk_slow", "warn", "Slow host disk: Runpod lists %d MB/s "
                                 "(most hosts ~7000), so model loads take longer" % hd))

    # -- image pull and lifecycle
    img = p.image(layer_sizes)
    m["image"] = img
    if _num(img.get("mbs")) is not None and img["mbs"] < T["image_warn"]:
        findings.append(_finding("image_slow", "warn", "The image pulled at only %.0f MB/s"
                                 % img["mbs"]))
    started = p.starts[0] if p.starts else None
    if not started and p.fetching_since:
        stuck = now - p.fetching_since
        if stuck > T["image_stuck_bad"]:
            findings.append(_finding("image_stuck", "bad", "Still fetching the image after %d min"
                                     % (stuck // 60)))
        elif stuck > T["image_stuck_warn"]:
            findings.append(_finding("image_stuck", "warn", "Still fetching the image after %d min"
                                     % (stuck // 60)))
    elif not started and won_at and now - won_at > T["no_start"] and pod.get("status") != "EXITED":
        findings.append(_finding("no_start", "warn", "The container has not started %d min after "
                                 "the win" % ((now - won_at) // 60)))
    if started and not p.first_container and now - started > T["app_silent"]:
        findings.append(_finding("app_silent", "warn", "The container started %d min ago and has "
                                 "printed nothing" % ((now - started) // 60)))
    explained = list(p.restarts) + list(p.removes) + list(actions)
    unexplained = [t for t in p.starts[1:]
                   if not any(0 <= t - e <= T["own_action"] for e in explained)]
    m["restarts"] = len(unexplained)
    if unexplained:
        findings.append(_finding("restarts", "bad" if len(unexplained) >= 3 else "warn",
                                 "The container restarted %d time(s) without a restart from you"
                                 % len(unexplained)))

    # -- the app's own measurements (Ember builds with podcheck)
    old_build = ("downloads_start" in p.milestones and "net" not in p.pc
                 and not any(k in p.pc for k in ("host", "gpu")))
    m["old_build"] = old_build
    if old_build:
        findings.append(_finding("old_build", "info", "This Ember build does not report pod "
                                 "checks: judging from the logs only (GPU not measured)"))
    if p.pc_unknown is not None:
        findings.append(_finding("podcheck_version", "info", "The app reports pod checks in "
                                 "version %s, which this sniper does not read — update the "
                                 "sniper" % p.pc_unknown))
    net = p.pc.get("net") or {}
    m["net_mbs"] = _num(net.get("mbs"))
    if net.get("error"):
        findings.append(_finding("net_error", "info", "Net probe failed: %s" % redact(net["error"])))
    elif m["net_mbs"] is not None:
        if m["net_mbs"] < T["net_bad"]:
            findings.append(_finding("net_slow", "bad", "Internet is very slow: %.0f MB/s "
                                     "(≈ %d min for %d GB)" % (m["net_mbs"], _eta_min(m["net_mbs"]),
                                                                FULL_DOWNLOAD_GB)))
        elif m["net_mbs"] < T["net_warn"]:
            findings.append(_finding("net_slow", "warn", "Internet is slow: %.0f MB/s "
                                     "(≈ %d min for %d GB)" % (m["net_mbs"], _eta_min(m["net_mbs"]),
                                                                FULL_DOWNLOAD_GB)))
    dl = p.download(file_sizes)
    m["download"] = dl
    if _num(dl.get("mbs")) is not None and dl["mbs"] < T["dl_warn"]:
        findings.append(_finding("dl_slow", "warn", "Models are downloading at %.0f MB/s (%s)"
                                 % (dl["mbs"], dl.get("source"))))
    downloading = "downloads_start" in p.milestones and "downloads_done" not in p.milestones
    rx = list(p.rx)
    m["rx_mbs"] = _num(rx[-1].get("mbs")) if rx else None
    if downloading and len(rx) >= 2 and all(_num(r.get("mbs"), 0) < T["rx_warn"] for r in rx[-2:]):
        findings.append(_finding("rx_slow", "warn", "Downloads have slowed to %.0f MB/s"
                                 % m["rx_mbs"]))
    if _num(host.get("disk_read_mbs")) is not None and host["disk_read_mbs"] < T["disk_read_warn"]:
        findings.append(_finding("disk_slow", "warn", "Disk reads at only %.0f MB/s"
                                 % host["disk_read_mbs"]))
    m["disk_read_mbs"] = _num(host.get("disk_read_mbs"))

    gpu = p.pc.get("gpu") or {}
    m["gpu"] = None
    if gpu.get("error") and not _num(gpu.get("tflops")):
        findings.append(_finding("gpu_error", "info", "GPU test failed: %s" % redact(gpu["error"])))
    if _num(gpu.get("tflops")):
        g = {"tflops": _num(gpu["tflops"]), "h2d_gbs": _num(gpu.get("h2d_gbs")),
             "pcie": _decode_pcie(gpu), "throttle": gpu.get("throttle") or [],
             "power_limit_w": _num(gpu.get("power_limit_w")), "temp_c": _num(gpu.get("temp_c")),
             "name": gpu.get("name")}
        base = baseline or {}
        if _num(base.get("tflops")) and (base.get("n", 0) >= T["baseline_min"]
                                         or base.get("from") == "seed"):
            g["score"] = round(g["tflops"] / base["tflops"], 3)
            g["baseline"] = base
            if g["score"] < T["gpu_score_warn"]:
                findings.append(_finding("gpu_slow", "warn", "GPU at %d%% of normal for this card "
                                         "(%.0f vs %.0f TFLOPS)" % (g["score"] * 100, g["tflops"],
                                                                     base["tflops"])))
        else:
            g["learning"] = "%d/%d" % ((base or {}).get("n", 0), T["baseline_min"])
        bad = sorted(BAD_THROTTLE & set(g["throttle"]))
        if bad:
            findings.append(_finding("gpu_throttle", "warn", "The GPU is throttling under load (%s)"
                                     % ", ".join(bad)))
        first, last = _num(gpu.get("tflops_first")), _num(gpu.get("tflops_last"))
        if first and last and (first - last) / first > T["tflops_drop_warn"]:
            findings.append(_finding("gpu_drop", "warn", "The GPU slowed down during the test "
                                     "(%.0f → %.0f TFLOPS): power or heat" % (first, last)))
        plim, pdef = _num(gpu.get("power_limit_w")), _num(gpu.get("power_default_w"))
        if plim and pdef and plim < T["power_cap_warn"] * pdef:
            findings.append(_finding("power_capped", "warn", "The host has capped the GPU's power "
                                     "at %.0f W (default %.0f W)" % (plim, pdef)))
        if g["temp_c"] is not None and g["temp_c"] >= T["temp_warn"]:
            findings.append(_finding("gpu_hot", "warn", "GPU at %.0f °C under load" % g["temp_c"]))
        if g["h2d_gbs"] is not None and g["h2d_gbs"] < T["h2d_warn"]:
            findings.append(_finding("pcie_slow", "warn", "Slow PCIe link: %.1f GB/s to the GPU "
                                     "(%s) — model loads and offloading suffer"
                                     % (g["h2d_gbs"], g["pcie"] or "link unknown")))
        m["gpu"] = g

    # -- renders
    keys = {}
    for r in p.renders:
        if _num(r.get("s_per_step")):
            keys.setdefault(r.get("key") or "?", []).append(_num(r["s_per_step"]))
    m["render"] = {k: {"n": len(v), "s_per_step": round(_median(v[-5:]), 3)} for k, v in keys.items()}
    for k, r in m["render"].items():
        ref = (render_base or {}).get(k)
        if ref and r["n"] >= 3 and r["s_per_step"] > T["render_slow"] * ref:
            findings.append(_finding("render_slow", "warn", "Renders are slow: %.2f s/step vs "
                                     "%.2f on other pods (%s)" % (r["s_per_step"], ref, k)))

    # -- errors from the logs
    for code, level, _src, _rx, text in ERRORS:
        e = p.errors.get(code)
        if not e:
            continue
        if code == "download_retry":
            level = "warn" if e["n"] >= T["retry_warn"] else "info"
        f = _finding(code, level, "%s%s" % (text, " (×%d)" % e["n"] if e["n"] > 1 else ""))
        f["excerpt"] = e["excerpt"][:18]
        findings.append(f)

    # -- the app
    ui = p.milestones.get("ui_ready")
    app_start = p.milestones.get("app_starting")
    m["app_up"] = app.get("up")
    if app_start and not ui and now - app_start > T["ui_late"] and pod.get("status") == "RUNNING":
        findings.append(_finding("ui_late", "warn", "No UI yet, %d min after the app started"
                                 % ((now - app_start) // 60)))
    if ui and app.get("up") is False and app.get("down_since") and now - app["down_since"] > T["app_down"]:
        findings.append(_finding("app_down", "warn", "The app says it is ready, but its page "
                                 "(port 7860) has not answered for %d min"
                                 % ((now - app["down_since"]) // 60)))

    # -- the pod itself
    status = pod.get("status")
    last = machine.get("last_status") or ""
    ended = None
    if pod.get("gone") or status == "TERMINATED":
        ended = "terminated"
    elif status == "EXITED":
        if last and not last.lower().startswith("exited by user"):
            findings.append(_finding("pod_exited", "bad", "The pod stopped on its own: %s"
                                     % redact(last)))
            ended = "exited (host)"
        else:
            ended = "stopped by you"

    # -- where it is up to
    dl_files = dl["done"] + dl["cached"]
    phase = ("terminated" if ended == "terminated" else "stopped" if ended
             else "app answering" if ui and app.get("up")
             else "UI ready" if ui
             else "ComfyUI starting" if "comfy_start" in p.milestones
             else "downloading %d files%s" % (dl_files, " · %.0f MB/s" % m["rx_mbs"] if m["rx_mbs"] else "")
             if "downloads_start" in p.milestones
             else "app starting" if p.first_container
             else "container started" if started
             else "pulling image" if p.creates or p.fetch
             else "created")
    measured_net = m["net_mbs"] is not None or net.get("error") or _num(dl.get("mbs")) is not None \
        or ("downloads_done" in p.milestones and not dl["done"])
    measured_gpu = m["gpu"] is not None or gpu.get("error") or old_build
    done = bool(ui and app.get("up") and measured_net and measured_gpu)
    return {"metrics": m, "findings": findings, "phase": phase, "done": done,
            "ended": ended, "milestones": {k: _iso(v) for k, v in p.milestones.items()}}


def _eta_min(mbs: float) -> int:
    return int(FULL_DOWNLOAD_GB * 1000 / max(mbs, 0.1) / 60)


# ── lookups outside Runpod (cached) ─────────────────────────────────

_layers_lock = threading.Lock()
_files_cache = {"at": 0.0, "sizes": None}


def _ext_json(url, headers=None):
    code, _h, text = HOOKS["ext_get"](url, {"User-Agent": USER_AGENT, **(headers or {})})
    if code != 200:
        raise ValueError("%s answered %s" % (url.split("?")[0], code))
    return json.loads(text)


def layer_sizes(digest: str) -> dict:
    """Compressed layer sizes of the image at `digest`, short id -> bytes.
    Docker Hub, anonymously; one read per image release, kept in
    image-layers.json."""
    with _layers_lock:
        cache = _read_json(LAYERS_FILE, {})
        if digest in cache:
            return cache[digest]
    tok = _ext_json("https://auth.docker.io/token?service=registry.docker.io&scope=repository:%s:pull"
                    % IMAGE_REPO)["token"]
    auth = {"Authorization": "Bearer " + tok}
    accept = ("application/vnd.oci.image.index.v1+json, "
              "application/vnd.docker.distribution.manifest.list.v2+json, "
              "application/vnd.oci.image.manifest.v1+json, "
              "application/vnd.docker.distribution.manifest.v2+json")
    base = "https://registry-1.docker.io/v2/%s/manifests/" % IMAGE_REPO
    man = _ext_json(base + digest, {**auth, "Accept": accept})
    if "manifests" in man:
        amd = [d for d in man["manifests"] if (d.get("platform") or {}).get("architecture") == "amd64"]
        if not amd:
            raise ValueError("no amd64 image in %s" % digest)
        man = _ext_json(base + amd[0]["digest"], {**auth, "Accept": accept})
    sizes = {layer["digest"].split(":", 1)[1][:12]: int(layer["size"]) for layer in man.get("layers", [])}
    with _layers_lock:
        cache = _read_json(LAYERS_FILE, {})
        cache[digest] = sizes
        _write_json(LAYERS_FILE, cache)
    return sizes


def mirror_file_sizes() -> dict:
    """File name -> bytes for every file in the public mirror repos (HF tree
    API), cached 6 h. Only needed for builds that don't log sizes."""
    if _files_cache["sizes"] is not None and time.time() - _files_cache["at"] < 6 * 3600:
        return _files_cache["sizes"]
    sizes = {}
    for repo in MIRROR_REPOS:
        items = _ext_json("https://huggingface.co/api/models/%s/%s/tree/main?recursive=true"
                          % (MIRROR_USER, repo))
        for it in items if isinstance(items, list) else []:
            if it.get("type") == "file" and it.get("size"):
                sizes[str(it.get("path", "")).rsplit("/", 1)[-1]] = int(it["size"])
    _files_cache.update(at=time.time(), sizes=sizes)
    return sizes


# ── history, blocked hosts ──────────────────────────────────────────

_hist_lock = threading.Lock()


def history_append(rec: dict) -> None:
    line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
    with _hist_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")


_hist_cache = {"key": None, "recs": []}


def history_load() -> list:
    """The latest record of every pod, newest first. Re-read only when the
    file has changed (every watched pod asks every few seconds)."""
    latest = {}
    with _hist_lock:
        try:
            st = HISTORY_FILE.stat()
            key = (str(HISTORY_FILE), st.st_mtime_ns, st.st_size)
            if key == _hist_cache["key"]:
                return list(_hist_cache["recs"])
            lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("pod"):
            latest[rec["pod"]] = rec
    recs = sorted(latest.values(), key=lambda r: r.get("won_at") or "", reverse=True)
    with _hist_lock:
        _hist_cache.update(key=key, recs=recs)
    return list(recs)


def history_compact() -> None:
    """Rewrite the file with one line per pod when it has grown."""
    with _hist_lock:
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                n = sum(1 for _ in f)
        except OSError:
            return
    recs = history_load()
    if n <= max(200, 3 * len(recs)):
        return
    tmp = HISTORY_FILE.with_suffix(".jsonl.part")
    with _hist_lock:
        tmp.write_text("".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"
                               for r in reversed(recs)), encoding="utf-8", newline="\n")
        tmp.replace(HISTORY_FILE)


def seed_baselines() -> dict:
    return {g["id"]: g["baseline"] for g in _read_json(GPUS_FILE, {}).get("gpus", [])
            if isinstance(g.get("baseline"), dict)}


def blocked_hosts() -> dict:
    return _read_json(HOSTS_FILE, {}).get("blocked", {})


def block_host(mid: str, info: dict) -> dict:
    data = _read_json(HOSTS_FILE, {})
    data.setdefault("blocked", {})[mid] = {**info, "at": _iso(time.time())}
    _write_json(HOSTS_FILE, data)
    return data["blocked"]


def unblock_host(mid: str) -> dict:
    data = _read_json(HOSTS_FILE, {})
    data.setdefault("blocked", {}).pop(mid, None)
    _write_json(HOSTS_FILE, data)
    return data["blocked"]


def history_summary(records: list) -> dict:
    """By country and by machine: how many pods, medians, how many went bad."""
    def group(key):
        out = {}
        for r in records:
            k = r.get(key)
            if not k:
                continue
            g = out.setdefault(k, {"pods": 0, "bad": 0, "warned": 0, "net": [], "gpu": [], "gpus": set()})
            g["pods"] += 1
            g["bad"] += r.get("verdict") == "bad"
            g["warned"] += bool(r.get("flags"))
            g["net"].append(_num(r.get("net_mbs")) or _num(r.get("dl_mbs")))
            g["gpu"].append(_num(r.get("gpu_score")))
            g["gpus"].add(r.get("gpu"))
        return {k: {"pods": g["pods"], "bad": g["bad"], "warned": g["warned"],
                    "net_mbs": _median(g["net"]), "gpu_score": _median(g["gpu"]),
                    "gpus": sorted(x for x in g["gpus"] if x)} for k, g in out.items()}
    return {"by_country": group("country"), "by_machine": group("machine")}


# ── one pod's watcher ───────────────────────────────────────────────


class Health:
    """Watches one pod: machine, status, logs, app. Started on a win (and
    by the page's Check button for any running pod). Ends when the pod
    stops or is terminated, or when stop() is called."""

    def __init__(self, acct, pid: str, source: str = "snipe", won_at: float = None,
                 preset: dict = None, machine: dict = None, pod: dict = None):
        self.acct = acct
        self.pid = pid
        self.source = source
        self.won_at = won_at or time.time()
        self.preset = preset or {}
        self.machine = machine
        self.pod = pod or {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.parser = LogParser()
        self.started = time.time()
        self.updated = None
        self.ended = None
        self.status = "checking"            # checking / good / warning / bad / unknown / ended
        self.result = {"metrics": {}, "findings": [], "phase": "created", "done": False}
        self.acked = set()
        self.warned = set()
        self.warn_seq = 0                   # bumps on every new warning, for the page's beep
        self.decision = None                # kept / terminated
        self.actions = []                   # our own pod actions (epoch), for restarts
        self.app = {"up": None, "since": None, "down_since": None, "checked": 0.0}
        self.events = deque(maxlen=30)
        self.stream_requests = 0
        self.layer_sizes = None
        self.file_sizes = None
        self._ext_tried = set()
        self._late_reads = 0
        self._saved_sig = None
        self._saved_at = 0.0
        self.threads = []

    # -- control

    def event(self, text: str) -> None:
        with self.lock:
            self.events.append([time.time(), text])

    def start(self) -> "Health":
        self.event("Watching %s (%s)." % (self.pid, self.source))
        for fn in (self._status_loop, self._log_loop, self._eval_loop):
            t = threading.Thread(target=fn, daemon=True)
            self.threads.append(t)
            t.start()
        return self

    def stop(self) -> None:
        self.stop_event.set()

    def note_action(self, action: str) -> None:
        """One of our own pod actions, just before it is sent: a container
        start right after it is ours, not the host's."""
        with self.lock:
            self.actions.append(time.time())

    def keep(self) -> None:
        """"Keep": every warning shown now is accepted. A new kind of
        warning later still warns."""
        self._recompute()
        with self.lock:
            self.acked |= {f["code"] for f in self.result["findings"] if f["level"] in ("warn", "bad")}
        self.set_decision("kept")
        self._recompute()

    def set_decision(self, decision: str) -> None:
        with self.lock:
            had_warning = bool(self.warned)
            if decision == "terminated" and not had_warning:
                decision = "terminated (no warning)"
            self.decision = decision
        self._save(force=True)

    # -- threads

    def _wait(self, seconds: float) -> bool:
        """Sleep; True when told to stop."""
        return self.stop_event.wait(seconds)

    def _read_machine(self, tries: int) -> None:
        """The host's country and machine id. Right after a create the
        machine can still be blank, so try a few times."""
        for i in range(tries):
            m = HOOKS["machine"](self.acct, self.pid)
            if m:
                with self.lock:
                    self.machine = {**(self.machine or {}), **{k: v for k, v in m.items()
                                                               if v is not None}}
            if (self.machine or {}).get("country") or i == tries - 1:
                return
            if self._wait(2):
                return

    def _status_loop(self) -> None:
        if not (self.machine or {}).get("country"):
            self._read_machine(4)
        while not self.stop_event.is_set():
            view = HOOKS["pod"](self.acct, self.pid)
            if view:
                with self.lock:
                    self.pod = {**self.pod, **{k: v for k, v in view.items() if v is not None}}
                status = view.get("status")
                if view.get("gone") or status == "TERMINATED":
                    self._end("terminated")
                    return
                if status == "EXITED":
                    self._read_machine(1)          # lastStatusChange says who stopped it
                    self._end("stopped")
                    return
                if (status == "RUNNING" and not (self.machine or {}).get("country")
                        and self._late_reads < 5):
                    self._late_reads += 1
                    self._read_machine(1)
            running = self.pod.get("status") == "RUNNING" and self.pod.get("cuda")
            gap = 10 if not running else (300 if self.result.get("done") else 60)
            if self._wait(gap):
                return

    def _end(self, why: str) -> None:
        self._recompute()
        with self.lock:
            self.ended = time.time()
        self.event("Pod %s — watch ended." % why)
        self._save(force=True)
        self.stop_event.set()

    def _log_loop(self) -> None:
        url = "%s/pods/%s/logs?tail=5000" % (V2, self.pid)
        last_id, quiet = None, 0
        while not self.stop_event.is_set():
            headers = {"Accept": "text/event-stream"}
            if last_id:
                headers["Last-Event-ID"] = last_id
            got = 0
            data = []
            try:
                self.stream_requests += 1
                for raw in HOOKS["stream"](self.acct, url, headers, 300):
                    if self.stop_event.is_set():
                        break
                    line = raw.rstrip("\r\n")
                    if not line:
                        got += self._dispatch(data)
                        data = []
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "id":
                        last_id = value
                    elif field == "data":
                        data.append(value)
                got += self._dispatch(data)
            except Exception as exc:              # noqa: BLE001 - a stream may die any way
                if got == 0:
                    self.event("Log stream: %s" % redact(str(exc))[:160])
            quiet = 0 if got else quiet + 1
            # Busy logs reconnect at once; a silent stream backs off.
            if self._wait(2 if got else min(60, 5 * 2 ** min(quiet, 4))):
                return

    def _dispatch(self, data: list) -> int:
        if not data:
            return 0
        try:
            ev = json.loads("\n".join(data))
        except ValueError:
            return 0
        if not isinstance(ev, dict):
            return 0
        with self.lock:
            return 1 if self.parser.feed(ev) else 0

    def _eval_loop(self) -> None:
        while not self._wait(3):
            self._lookups()
            self._check_app()
            self._recompute()
            self._save()

    def _lookups(self) -> None:
        """Layer sizes and mirror file sizes, only when the logs need them."""
        with self.lock:
            need_layers = self.layer_sizes is None and self.parser.needs_layer_sizes()
            digest = self.parser.digest
            need_files = self.file_sizes is None and self.parser.mirror_files_without_size()
        if need_layers and ("layers", digest) not in self._ext_tried:
            self._ext_tried.add(("layers", digest))
            try:
                self.layer_sizes = layer_sizes(digest)
            except Exception as exc:              # noqa: BLE001
                self.event("Image layer sizes unavailable: %s" % redact(str(exc))[:120])
        if need_files and "files" not in self._ext_tried:
            self._ext_tried.add("files")
            try:
                self.file_sizes = mirror_file_sizes()
            except Exception as exc:              # noqa: BLE001
                self.event("Mirror file sizes unavailable: %s" % redact(str(exc))[:120])

    def _check_app(self) -> None:
        with self.lock:
            ui = self.parser.milestones.get("ui_ready")
        if not ui or self.pod.get("status") != "RUNNING":
            return
        now = time.time()
        every = 300 if self.app["up"] else 30
        if now - self.app["checked"] < every:
            return
        self.app["checked"] = now
        try:
            up = bool(HOOKS["app_up"](self.pid))
        except Exception:                         # noqa: BLE001
            up = False
        if up:
            if not self.app["up"]:
                self.event("The app answers on port 7860.")
            self.app.update(up=True, since=self.app["since"] or now, down_since=None)
        else:
            self.app.update(up=False, down_since=self.app["down_since"] or now)

    def _recompute(self) -> None:
        records = history_load()
        gpu_id = self.pod.get("gpu")
        base = baselines(records, seed_baselines(), exclude=self.pid).get(gpu_id)
        rbase = {k[1]: v for k, v in render_baselines(records, exclude=self.pid).items()
                 if k[0] == gpu_id}
        with self.lock:
            res = evaluate(self.parser, now=time.time(), won_at=self.won_at, pod=self.pod,
                           machine=self.machine, app=self.app, actions=self.actions,
                           layer_sizes=self.layer_sizes, file_sizes=self.file_sizes,
                           baseline=base, render_base=rbase)
            res["findings"] += self._guard_findings()
            levels = [f["level"] for f in res["findings"] if f["code"] not in self.acked]
            new = [f for f in res["findings"]
                   if f["level"] in ("warn", "bad") and f["code"] not in self.warned
                   and f["code"] not in self.acked]
            for f in new:
                self.warned.add(f["code"])
            if new:
                self.warn_seq += 1
            self.status = ("ended" if self.ended and not ("bad" in levels or "warn" in levels)
                           else "bad" if "bad" in levels
                           else "warning" if "warn" in levels
                           else "good" if res["done"]
                           else "checking")
            if self.ended and self.status == "checking":
                self.status = "ended"
            self.result = res
            self.updated = time.time()
        if new and HOOKS["sound"] and not self.ended:
            threading.Thread(target=HOOKS["sound"], daemon=True).start()
            for f in new:
                self.event("⚠ %s" % f["text"])

    def _guard_findings(self) -> list:
        """The location guard in "warn" mode (and any blocked host): a
        finding instead of a termination. Lock held by the caller."""
        if not self.machine:
            return []
        verdict, why = guard_check(self.machine, self.preset, blocked_hosts())
        if verdict == "block":
            return [_finding("location", "warn", "Location: " + why)]
        if verdict == "unknown" and (self.preset.get("countries") or []):
            return [_finding("location", "info", "Location guard: " + why)]
        return []

    # -- output

    def view(self) -> dict:
        with self.lock:
            res = self.result
            return {
                "pod": self.pid, "account": getattr(self.acct, "name", None), "source": self.source,
                "status": self.status, "phase": res.get("phase"), "done": res.get("done"),
                "metrics": res.get("metrics"), "milestones": res.get("milestones"),
                "findings": [{**f, "acked": f["code"] in self.acked} for f in res.get("findings", [])],
                "warn_seq": self.warn_seq, "decision": self.decision,
                "started": self.started, "won_at": self.won_at, "updated": self.updated,
                "ended": self.ended, "lines": self.parser.lines,
                "stream_requests": self.stream_requests, "events": list(self.events),
                "pod_view": {k: self.pod.get(k) for k in ("status", "gpu", "cost", "ram", "vcpu",
                                                          "cuda", "dc", "cloud", "gpu_count")},
            }

    def record(self) -> dict:
        """The history line: no env, no tokens, no UI link."""
        with self.lock:
            m = self.result.get("metrics") or {}
            g = m.get("gpu") or {}
            img = m.get("image") or {}
            dl = m.get("download") or {}
            pod = self.pod
            return {
                "v": 1, "pod": self.pid, "account": getattr(self.acct, "name", None),
                "source": self.source, "won_at": _iso(self.won_at),
                "preset": self.preset.get("name"), "gpu": pod.get("gpu"),
                "gpu_count": pod.get("gpu_count"), "cloud": pod.get("cloud"),
                "dc": pod.get("dc"), "country": m.get("country"), "machine": m.get("machine"),
                "cost": pod.get("cost"), "ram_gb": m.get("ram_gb"), "vcpu": m.get("vcpu"),
                "cuda": pod.get("cuda"), "host_dl_mbps": m.get("host_dl_mbps"),
                "host_disk_mbs": m.get("host_disk_mbs"),
                "image": {k: img.get(k) for k in ("cached", "fetched", "method", "mbs", "secs")},
                "net_mbs": m.get("net_mbs"), "dl_mbs": dl.get("mbs"), "dl_source": dl.get("source"),
                "gpu_tflops": g.get("tflops"), "gpu_score": g.get("score"),
                "h2d_gbs": g.get("h2d_gbs"), "pcie": g.get("pcie"), "throttle": g.get("throttle"),
                "power_limit_w": g.get("power_limit_w"), "disk_read_mbs": m.get("disk_read_mbs"),
                "old_build": m.get("old_build"), "render": m.get("render") or {},
                "app_ready": (self.result.get("milestones") or {}).get("ui_ready"),
                "flags": sorted({f["code"] for f in self.result.get("findings", [])
                                 if f["level"] in ("warn", "bad")}),
                "errors": [{"code": f["code"], "text": f["text"]} for f in self.result.get("findings", [])
                           if f["code"] in {e[0] for e in ERRORS}][:10],
                "verdict": self.status, "decision": self.decision,
                "ended": _iso(self.ended), "updated": _iso(time.time()),
            }

    def _save(self, force: bool = False) -> None:
        rec = self.record()
        sig = json.dumps({k: v for k, v in rec.items() if k != "updated"}, sort_keys=True)
        now = time.time()
        if not force and (sig == self._saved_sig or now - self._saved_at < 15):
            return
        self._saved_sig, self._saved_at = sig, now
        try:
            history_append(rec)
        except OSError as exc:
            self.event("Could not write the history: %s" % exc)


# ── the registry server.py talks to ─────────────────────────────────

_reg_lock = threading.Lock()
_watched = {}                         # pod id -> Health


def watch(acct, pid: str, source: str = "snipe", restart: bool = False, **kw) -> Health:
    """Watch a pod. A re-check (restart) starts the logs over but keeps
    what the old watch knew that the logs can't tell again: the machine,
    when it was won, its preset, what you kept, your decision, and which
    warnings already sounded."""
    with _reg_lock:
        old = _watched.get(pid)
        if old and not old.stop_event.is_set() and not restart:
            return old
        if old:
            old.stop()
            kw.setdefault("machine", old.machine)
            kw["won_at"] = kw.get("won_at") or old.won_at
            kw["preset"] = kw.get("preset") or old.preset
            source = old.source if old.source == "snipe" else source
        h = Health(acct, pid, source=source, **kw)
        if old:
            h.acked, h.warned, h.decision = set(old.acked), set(old.warned), old.decision
        _watched[pid] = h
    return h.start()


def get(pid: str):
    with _reg_lock:
        return _watched.get(pid)


def views() -> list:
    with _reg_lock:
        hs = list(_watched.values())
    return [h.view() for h in hs]


def forget(pid: str) -> None:
    with _reg_lock:
        h = _watched.pop(pid, None)
    if h:
        h.stop()


def note_action(pid: str, action: str) -> None:
    h = get(pid)
    if h:
        h.note_action(action)
