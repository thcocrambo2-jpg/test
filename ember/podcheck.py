"""The pod check: is this machine any good? Measured at startup, logged.

A rented GPU pod can be slow in ways nothing else in the app notices until
the customer has paid for an hour of it: a slow internet link (the models
are ~90 GB), a GPU on a narrow PCIe riser or capped and throttling, too
little RAM, a slow disk. This module measures those once, in the first
seconds of a start, and prints each result as one log line:

    12:01:07 [INFO] ember: EMBER_PODCHECK {"v":1,"kind":"net","mbs":148.2,...}

The Runpod sniper (runpod_sniper/health.py) reads those lines through
Runpod's logs API, before the web server is even up, and warns when a
pod is worse than it should be. Anyone reading the pod's log sees the
same numbers. The lines are versioned ("v"); a reader that meets a
version it does not know says so instead of guessing.

Kinds, in the order they appear:

    host    RAM and CPUs this container really gets (cgroup), swap, free
            disk, disk write/read speed
    net     download speed from the Hugging Face CDN that serves the
            model mirror: 4 parallel ranged GETs, at most 8 s / 384 MiB
    gpu     bf16 matmul TFLOPS (first and last batch, to catch
            throttling), host<->GPU copy GB/s, the PCIe link and clocks,
            power and temperature read *under load* (an idle GPU drops its
            link to Gen 1), active throttle reasons
    rx      live download rate while the models download, every 30 s
    dl      the downloads' total: files, bytes, seconds, MB/s
    render  one per finished picture (from generation/eta.py): seconds
            per sampler step, so a slow card shows over time as well

Cost and timing. Only the net probe runs in the calling thread — it needs
the link to itself, so it goes before the downloads and costs at most 8 s.
The host and GPU checks run in one background thread while the downloads
run, and Check.join() stops a GPU test that is somehow still going before
ComfyUI starts, so the two never share the card. The GPU test runs in a
separate interpreter, like comfy.setup's torch probe: this app never
imports torch, and a CUDA fault takes out the probe, not the app.

On by default on RunPod pods only (RUNPOD_POD_ID set); EMBER_PODCHECK=0
turns it off, =1 turns it on anywhere. Nothing here may stop the app: every
probe catches its own failures and reports them as `"error"` in its line.

Standard library only: this runs before the pip install step.
"""

import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlparse

from ember import settings
from ember.logs import log

VERSION = 1
TOKEN = "EMBER_PODCHECK"
# A big public file on the model mirror: the same Hugging Face CDN (Xet)
# every mirrored model comes from, and no token needed.
PROBE_PATH = "krea2-encoders/resolve/main/text_encoders/qwen3vl_4b_abliterated.safetensors"
NET_SECONDS = 8.0
NET_BYTES = 384 << 20
NET_STREAMS = 4
NET_SLICE = 96 << 20
DISK_MB = 512
GPU_TIMEOUT = 45
RX_EVERY = 30
USER_AGENT = "ember-podcheck/1"

_lock = threading.Lock()
_records: dict = {}
_downloads = {"files": 0, "bytes": 0, "secs": 0.0, "first": None, "last": None}


def enabled() -> bool:
    value = settings.PODCHECK
    if value in ("0", "false", "no", "off"):
        return False
    if value:
        return True
    return bool(settings.pod_id())


def emit(kind: str, **fields) -> dict:
    """Log one result line, and keep it in <base>/podcheck.json."""
    rec = {"v": VERSION, "kind": kind, **fields}
    log.info("%s %s", TOKEN, json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
    with _lock:
        if kind == "render":
            _records.setdefault("render", []).append(rec)
            _records["render"] = _records["render"][-50:]
        else:
            _records[kind] = rec
        snapshot = dict(_records)
    try:
        path = Path(settings.WORKING_DIR) / "podcheck.json"
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return rec


# ── net ─────────────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _resolve(url: str) -> tuple:
    """(the CDN URL the mirror redirects to, the file's size)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=10) as res:
            return url, int(res.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
            size = exc.headers.get("X-Linked-Size") or exc.headers.get("Content-Length") or 0
            return urljoin(url, exc.headers["Location"]), int(size)
        raise


def net_probe(url: str = None, seconds: float = NET_SECONDS, budget: int = NET_BYTES,
              streams: int = NET_STREAMS) -> dict:
    """Download speed from the mirror's CDN: `streams` ranged GETs of
    different parts of one big file, until `seconds` or `budget` bytes."""
    url = url or "https://huggingface.co/%s/%s" % (settings.MIRROR_USER, PROBE_PATH)
    t0 = time.time()
    try:
        target, size = _resolve(url)
        if size < NET_SLICE * streams:
            raise ValueError("the probe file's size is unknown (%s)" % size)
        ttfb = int((time.time() - t0) * 1000)
        state = {"got": 0, "first": None, "last": None, "errors": []}
        lock = threading.Lock()
        deadline = t0 + seconds             # the whole probe, the redirect included

        def worker(offset):
            req = urllib.request.Request(target, headers={
                "User-Agent": USER_AGENT, "Range": "bytes=%d-%d" % (offset, offset + NET_SLICE - 1)})
            try:
                with urllib.request.urlopen(req, timeout=max(1.0, deadline - time.time())) as res:
                    with lock:
                        state["first"] = state["first"] or time.time()
                    while time.time() < deadline:
                        chunk = res.read(256 << 10)     # small, so a slow link stops on time
                        if not chunk:
                            return
                        with lock:
                            state["got"] += len(chunk)
                            state["last"] = time.time()
                            if state["got"] >= budget:
                                return
            except Exception as exc:              # noqa: BLE001 - reported, never raised
                with lock:
                    state["errors"].append("%s: %s" % (type(exc).__name__, exc))

        span = (size - NET_SLICE) // streams
        offsets = [i * span + random.randrange(0, max(1, span - NET_SLICE)) for i in range(streams)]
        threads = [threading.Thread(target=worker, args=(o,), daemon=True) for o in offsets]
        for th in threads:
            th.start()
        for th in threads:
            th.join(max(0.0, deadline - time.time()) + 3)
        got, first, last = state["got"], state["first"], state["last"]
        if not got or not first or not last or last <= first:
            raise RuntimeError(state["errors"][0] if state["errors"] else "nothing downloaded")
        secs = last - first
        return emit("net", mbs=round(got / 1e6 / secs, 1), bytes=got, secs=round(secs, 2),
                    streams=streams, ttfb_ms=ttfb, cdn=urlparse(target).netloc, error=None)
    except Exception as exc:                      # noqa: BLE001
        return emit("net", mbs=None, secs=round(time.time() - t0, 1),
                    error="%s: %s" % (type(exc).__name__, str(exc)[:160]))


# ── host ────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _meminfo() -> dict:
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        m = re.match(r"(\w+):\s+(\d+) kB", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * 1024
    return out


def _cgroup_ram() -> float:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        value = _read(path)
        if value.isdigit() and int(value) < 1 << 60:   # "max" or a huge number = no limit
            return int(value) / 1e9
    return None


def _cpus() -> float:
    quota = _read("/sys/fs/cgroup/cpu.max").split()
    if len(quota) == 2 and quota[0].isdigit() and int(quota[1]) > 0:
        return round(int(quota[0]) / int(quota[1]), 1)
    q, p = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if q.lstrip("-").isdigit() and int(q) > 0 and p.isdigit() and int(p) > 0:
        return round(int(q) / int(p), 1)
    try:
        return float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return float(os.cpu_count() or 0) or None


def _dd(args: list) -> float:
    """Seconds a dd run took, or None if dd refused (no O_DIRECT here)."""
    t0 = time.time()
    try:
        res = subprocess.run(["dd", *args], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return time.time() - t0 if res.returncode == 0 else None


def _disk_test(folder: Path, mb: int = DISK_MB) -> dict:
    """Write then read `mb` MB past the page cache. dd with O_DIRECT when
    the filesystem takes it, else plain writes + fsync and a dropped cache."""
    path = folder / ".podcheck.tmp"
    size = mb * 1024 * 1024
    out = {}
    try:
        w = _dd(["if=/dev/zero", "of=%s" % path, "bs=16M", "count=%d" % (mb // 16),
                 "oflag=direct", "conv=fsync"])
        if w is None:
            t0 = time.time()
            block = b"\0" * (16 << 20)
            with open(path, "wb") as f:
                for _ in range(mb // 16):
                    f.write(block)
                f.flush()
                os.fsync(f.fileno())
            w = time.time() - t0
        out["disk_write_mbs"] = round(size / 1e6 / max(w, 1e-3))
        r = _dd(["if=%s" % path, "of=/dev/null", "bs=16M", "iflag=direct"])
        if r is None:
            with open(path, "rb") as f:
                if hasattr(os, "posix_fadvise"):
                    os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                t0 = time.time()
                while f.read(16 << 20):
                    pass
                r = time.time() - t0
        out["disk_read_mbs"] = round(size / 1e6 / max(r, 1e-3))
    except OSError as exc:
        out["disk_error"] = str(exc)[:120]
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return out


def host_check() -> dict:
    t0 = time.time()
    try:
        mem = _meminfo()
        folder = Path(settings.MODELS_DIR)
        if not folder.is_dir():
            folder = Path(settings.BASE_DIR)
        free = None
        try:
            free = round(shutil.disk_usage(folder).free / 1e9, 1)
        except OSError:
            pass
        fields = {"ram_gb": round(_cgroup_ram() or mem.get("MemTotal", 0) / 1e9, 1) or None,
                  "ram_host_gb": round(mem["MemTotal"] / 1e9, 1) if "MemTotal" in mem else None,
                  "swap_gb": round(mem.get("SwapTotal", 0) / 1e9, 1),
                  "cpus": _cpus(), "disk_free_gb": free, **_disk_test(folder)}
        return emit("host", **fields, secs=round(time.time() - t0, 1))
    except Exception as exc:                      # noqa: BLE001
        return emit("host", error="%s: %s" % (type(exc).__name__, str(exc)[:160]))


# ── gpu ─────────────────────────────────────────────────────────────

# Run as `python3 -c _GPU_PROBE` with the runtime interpreter (the one that
# has torch). Prints one JSON line. Every timed loop synchronises each
# step: CUDA calls return at once, and a loop bounded by wall time but not
# synchronised queues minutes of work.
_GPU_PROBE = r"""
import json, subprocess, threading, time
out = {}
t_all = time.time()
STATIC = ["name", "driver_version", "pcie.link.gen.max", "pcie.link.width.max",
          "power.default_limit", "power.max_limit", "clocks.max.sm", "ecc.mode.current"]
LOAD = ["pcie.link.gen.gpucurrent", "pcie.link.width.current", "power.limit", "power.draw",
        "clocks.sm", "temperature.gpu", "clocks_event_reasons.active"]
REASONS = {0x2: "app_clocks", 0x4: "sw_power_cap", 0x8: "hw_slowdown", 0x10: "sync_boost",
           0x20: "sw_thermal", 0x40: "hw_thermal", 0x80: "hw_power_brake", 0x100: "display_clock"}
def smi(fields):
    r = subprocess.run(["nvidia-smi", "-i", "0", "--query-gpu=" + ",".join(fields),
                        "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    vals = [v.strip() for v in r.stdout.strip().splitlines()[0].split(",")]
    return dict(zip(fields, vals))
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
static, load = {}, {}
try:
    static = smi(STATIC)
except Exception as exc:
    out["smi_error"] = str(exc)[:120]
import torch
dev = torch.device("cuda")
x = torch.empty(256 * 1024 * 1024, dtype=torch.float32).pin_memory()
y = torch.empty_like(x, device=dev)
y.copy_(x, non_blocking=True); torch.cuda.synchronize()
def sample(name, delay):
    time.sleep(delay)
    try:
        load[name] = smi(LOAD)
    except Exception:
        pass
th = threading.Thread(target=sample, args=("copy", 0.3)); th.start()
t = time.time(); n = 0
while time.time() - t < 1.5:
    y.copy_(x, non_blocking=True); torch.cuda.synchronize(); n += 1
h2d = n * x.numel() * 4 / (time.time() - t) / 1e9
th.join()
t = time.time(); m = 0
while time.time() - t < 1.0:
    x.copy_(y, non_blocking=True); torch.cuda.synchronize(); m += 1
d2h = m * x.numel() * 4 / (time.time() - t) / 1e9
del x, y
torch.cuda.empty_cache()
N = 8192
a = torch.randn(N, N, device=dev, dtype=torch.bfloat16)
b = torch.randn(N, N, device=dev, dtype=torch.bfloat16)
t = time.time()
while time.time() - t < 1.0:
    a @ b; torch.cuda.synchronize()
th = threading.Thread(target=sample, args=("matmul", 2.5)); th.start()
t = time.time(); k = 0; per = []
while time.time() - t < 4.0:
    s = time.time()
    for _ in range(5):
        a @ b
    torch.cuda.synchronize(); k += 5
    per.append(5 * 2 * N ** 3 / (time.time() - s) / 1e12)
tflops = k * 2 * N ** 3 / (time.time() - t) / 1e12
th.join()
cp, mm = load.get("copy") or {}, load.get("matmul") or {}
mask = mm.get("clocks_event_reasons.active") or "0"
try:
    bits = int(mask, 16)
except ValueError:
    bits = 0
first = sum(per[:3]) / max(1, len(per[:3]))
last = sum(per[-3:]) / max(1, len(per[-3:]))
gen, width = num(cp.get("pcie.link.gen.gpucurrent")), num(cp.get("pcie.link.width.current"))
out.update({
    "name": static.get("name") or torch.cuda.get_device_name(0),
    "driver": static.get("driver_version"), "cuda": torch.version.cuda,
    "tflops": round(tflops, 1), "tflops_first": round(first, 1), "tflops_last": round(last, 1),
    "h2d_gbs": round(h2d, 1), "d2h_gbs": round(d2h, 1),
    "pcie_gen": int(gen) if gen else None, "pcie_gen_max": num(static.get("pcie.link.gen.max")),
    "pcie_width": int(width) if width else None, "pcie_width_max": num(static.get("pcie.link.width.max")),
    "sm_mhz": num(mm.get("clocks.sm")), "sm_mhz_max": num(static.get("clocks.max.sm")),
    "power_w": num(mm.get("power.draw")), "power_limit_w": num(mm.get("power.limit")),
    "power_default_w": num(static.get("power.default_limit")),
    "power_max_w": num(static.get("power.max_limit")), "temp_c": num(mm.get("temperature.gpu")),
    "throttle": [name for bit, name in sorted(REASONS.items()) if bits & bit],
    "ecc": static.get("ecc.mode.current"),
    "vram_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1),
    "secs": round(time.time() - t_all, 1), "error": None,
})
print(json.dumps(out))
"""


class Check:
    """The background half of the pod check: host, then GPU."""

    def __init__(self):
        self.proc = None
        self.thread = threading.Thread(target=self._run, name="podcheck", daemon=True)

    def _run(self) -> None:
        host_check()
        self._gpu()

    def _gpu(self) -> None:
        from ember.comfy import setup as bootstrap   # the interpreter that has torch
        try:
            self.proc = subprocess.Popen(
                [bootstrap.runtime_python(), "-c", _GPU_PROBE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace")
            try:
                out, err = self.proc.communicate(timeout=GPU_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate()
                emit("gpu", error="the GPU test did not finish in %d s" % GPU_TIMEOUT)
                return
            lines = [ln for ln in (out or "").strip().splitlines() if ln.startswith("{")]
            if not lines:
                tail = (err or "").strip().splitlines()[-1:] or ["no output"]
                emit("gpu", error=tail[0][:200])
                return
            emit("gpu", **json.loads(lines[-1]))
        except Exception as exc:                  # noqa: BLE001
            emit("gpu", error="%s: %s" % (type(exc).__name__, str(exc)[:160]))

    def join(self, timeout: float = 30) -> None:
        """Wait for the background checks; stop the GPU test if it is
        still running, so it never shares the card with ComfyUI."""
        self.thread.join(timeout)
        proc = self.proc
        if self.thread.is_alive() and proc and proc.poll() is None:
            log.warning("Pod check: stopping the GPU test before ComfyUI starts")
            proc.kill()


class _Off:
    def join(self, timeout: float = 30) -> None:
        pass


def start():
    """Run the pod check: host and GPU in the background, the net probe
    here (≤ 8 s). Returns something with .join()."""
    if not enabled():
        return _Off()
    check = Check()
    check.thread.start()
    net_probe()
    return check


# ── during the downloads ────────────────────────────────────────────


def _rx_bytes():
    total = None
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if name.strip() != "lo" and fields and fields[0].isdigit():
            total = (total or 0) + int(fields[0])
    return total


@contextmanager
def rx_meter(every: float = RX_EVERY):
    """While the block runs, log the container's receive rate every
    `every` seconds (all interfaces but lo) — the live download speed."""
    if not enabled() or _rx_bytes() is None:
        yield
        return
    stop = threading.Event()

    def run():
        prev, t = _rx_bytes(), time.time()
        while not stop.wait(every):
            cur, now = _rx_bytes(), time.time()
            if cur is not None and prev is not None and now > t:
                emit("rx", mbs=round((cur - prev) / 1e6 / (now - t), 1), secs=round(now - t))
            prev, t = cur, now

    th = threading.Thread(target=run, name="podcheck-rx", daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()


def note_download(size: int, secs: float) -> None:
    """One finished model download (weights/downloads.py)."""
    now = time.time()
    with _lock:
        d = _downloads
        d["files"] += 1
        d["bytes"] += size
        d["secs"] += secs
        d["first"] = d["first"] or now - secs
        d["last"] = now


def emit_download_summary() -> None:
    if not enabled():
        return
    with _lock:
        d = dict(_downloads)
    if not d["files"]:
        emit("dl", files=0, bytes=0, secs=0, mbs=None)
        return
    emit("dl", files=d["files"], bytes=d["bytes"], secs=round(d["secs"]),
         wall_secs=round(d["last"] - d["first"]), mbs=round(d["bytes"] / 1e6 / max(d["secs"], 1e-3), 1))


def render(key: str, steps: int, mp: float, s_per_step: float, pre_s: float, cold: bool) -> None:
    """One finished picture's sampler speed (generation/eta.py)."""
    if enabled():
        emit("render", key=key, steps=steps, mp=round(mp, 3), s_per_step=round(s_per_step, 4),
             pre_s=round(pre_s, 2), cold=bool(cold))
