"""GPU detection + ComfyUI server launch.

GPUs are detected dynamically — nothing is hardcoded to cuda:0/cuda:1.
With two GPUs the workflow (workflow.py) pins the text encoder + VAE to
gpu:1, leaving all of gpu:0 to the ~13 GB diffusion model. With one GPU,
ComfyUI's built-in dynamic VRAM management handles everything by itself,
so the app never fails because a second GPU is missing.

GPU detection runs at import time (mirroring the original startup order),
so importing this module on a machine without an NVIDIA GPU raises.
"""

import json
import os
import subprocess
import time
import urllib.request

from bootstrap import runtime_python
from config import (
    COMFY_DIR,
    COMFY_HOST,
    COMFY_LOG,
    COMFY_PORT,
    KREA_RESERVE_VRAM_GB,
    OUTPUT_DIR,
    TEMP_DIR,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
    WAN_RESERVE_VRAM_GB,
    log,
)


def detect_gpus() -> list[str]:
    """Return the names of visible NVIDIA GPUs (empty list if none)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


GPUS = detect_gpus()
GPU_COUNT = len(GPUS)
for _i, _name in enumerate(GPUS):
    log.info("GPU %d: %s", _i, _name)
if GPU_COUNT == 0:
    raise RuntimeError(
        "No NVIDIA GPU visible (nvidia-smi returned nothing). "
        "Deploy this pod with at least one NVIDIA GPU attached."
    )
log.info(
    "Placement plan: diffusion model → gpu:0%s",
    ", text encoder + VAE → gpu:1" if GPU_COUNT >= 2
    else " (single GPU: dynamic VRAM management handles offloading)",
)


def _server_alive(timeout: float = 3.0, port: int = COMFY_PORT) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{COMFY_HOST}:{port}/system_stats", timeout=timeout
        ):
            return True
    except Exception:
        return False


def start_comfyui(port: int = COMFY_PORT, log_path=COMFY_LOG, extra_args=()):
    """Start ComfyUI as a background process (reuses a live server on restart).

    `extra_args` lets callers pass flags like --reserve-vram when a second
    instance (the Wan video server, KREA2_WAN_PARALLEL=1) has to share the
    GPU with this one.
    """
    if _server_alive(port=port):
        log.info("ComfyUI already running on port %d — reusing it", port)
        return None
    env = os.environ.copy()
    env.pop("CUDA_VISIBLE_DEVICES", None)  # make sure ComfyUI sees every GPU
    # Not sys.executable: compiled with Nuitka that is this binary, and
    # ComfyUI needs a real interpreter (see bootstrap.runtime_python).
    cmd = [
        runtime_python(), "main.py",
        "--listen", COMFY_HOST,
        "--port", str(port),
        "--output-directory", str(OUTPUT_DIR),
        "--temp-directory", str(TEMP_DIR / "comfy_temp"),
        "--disable-auto-launch",
        *[str(a) for a in extra_args],
    ]
    log.info("Starting ComfyUI on port %d (logs → %s)", port, log_path)
    log_handle = open(log_path, "a")
    return subprocess.Popen(
        cmd, cwd=COMFY_DIR, env=env,
        stdout=log_handle, stderr=subprocess.STDOUT,
    )


def log_tail(log_path=COMFY_LOG, lines: int = 20) -> str:
    """The last `lines` of a ComfyUI log, for reporting why it stopped.

    When the server is killed rather than raising, its last words are the
    only evidence there is — and they live in a file nobody reads until
    something breaks. Surfacing them beats "Connection refused".
    """
    try:
        return "\n".join(
            log_path.read_text(errors="replace").splitlines()[-lines:]
        )
    except OSError:
        return f"<could not read {log_path}>"


def ensure_alive(port: int = COMFY_PORT, log_path=COMFY_LOG) -> tuple[bool, str]:
    """Check the server is up, restarting it once if it is not.

    ComfyUI is only waited for at startup, so a mid-session death (an OOM
    kill during a model swap, a segfault in a custom node) left every
    later job failing with a bare ECONNREFUSED and the app needing a
    manual restart. This makes that recoverable and, more importantly,
    legible: the returned message carries the log tail explaining the
    exit.

    Returns (alive, message). The message is empty while nothing was
    wrong, so a healthy path stays silent.
    """
    if _server_alive(port=port):
        return True, ""
    tail = log_tail(log_path)
    log.error("ComfyUI on port %d is not responding — it exited. "
              "Last log lines:\n%s", port, tail)
    # Restart with the same VRAM reservation the original instance had, or
    # the two parallel instances stop respecting each other's budget.
    extra_args = ()
    if WAN_PARALLEL:
        reserve = (WAN_RESERVE_VRAM_GB if port == WAN_COMFY_PORT
                   else KREA_RESERVE_VRAM_GB)
        extra_args = ("--reserve-vram", str(reserve))
    try:
        process = start_comfyui(port=port, log_path=log_path,
                                extra_args=extra_args)
        wait_for_comfyui(process, port=port, log_path=log_path)
    except Exception as exc:
        return False, (
            f"❌ ComfyUI (port {port}) died and could not be restarted: {exc}\n\n"
            f"Its last log lines were:\n{tail}"
        )
    log.info("ComfyUI on port %d restarted", port)
    return True, (
        f"⚠️ ComfyUI (port {port}) had died and was restarted — the first job "
        "will be slow while models reload. Its last log lines before the "
        f"exit were:\n{tail}"
    )


def node_registered(class_type: str, port: int = COMFY_PORT) -> bool:
    """True if the running ComfyUI has that node class registered.

    /object_info/<class> is the cheap form of the endpoint — it returns
    just that node's schema (an empty object when it is unknown) instead
    of the multi-megabyte full listing.
    """
    try:
        with urllib.request.urlopen(
            f"http://{COMFY_HOST}:{port}/object_info/{class_type}", timeout=30
        ) as resp:
            return bool(json.loads(resp.read()))
    except Exception:
        return False


def _custom_node_traceback(log_path, needle: str) -> str:
    """The traceback ComfyUI logged for a custom node it could not import.

    ComfyUI ends a failed import with a 'Cannot import <path> module for
    custom nodes: ...' line, so find that and walk back to the Traceback
    header above it.
    """
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    for i, line in enumerate(lines):
        if "Cannot import" in line and needle in line:
            start = i
            for j in range(i - 1, max(0, i - 80), -1):
                if lines[j].lstrip().startswith("Traceback"):
                    start = j
                    break
            return "\n".join(lines[start:i + 1])
    # No import error logged — fall back to anything mentioning the pack.
    hits = [ln for ln in lines if needle.lower() in ln.lower()]
    return "\n".join(hits[-15:])


def verify_custom_node(class_type: str, needle: str, node_dir,
                       log_path=COMFY_LOG, port: int = COMFY_PORT) -> bool:
    """Log whether a custom node loaded, with the traceback when it did not.

    Without this a failed import is silent until a workflow is submitted
    and ComfyUI rejects it with a bare "node not found" — the real cause
    only ever reaches comfyui.log, which nobody reads until something
    breaks. Purely diagnostic: it never raises and never blocks startup.
    """
    try:
        if node_registered(class_type, port):
            log.info("Custom node %s is registered", class_type)
            return True
        log.error(
            "Custom node %s did NOT load — ComfyUI will reject the workflow "
            "that uses it.", class_type,
        )
        log.error("  files on disk: %s (nodes.py present: %s)",
                  node_dir, (node_dir / "nodes.py").exists())
        detail = _custom_node_traceback(log_path, needle)
        log.error("  ComfyUI said:\n%s",
                  detail or f"<nothing about {needle} in {log_path}>")
        return False
    except Exception as exc:  # diagnostics must never break startup
        log.warning("Could not verify custom node %s: %s", class_type, exc)
        return False


def wait_for_comfyui(process, timeout: int = 300,
                     port: int = COMFY_PORT, log_path=COMFY_LOG) -> None:
    """Block until the ComfyUI API answers; raise with the log tail if it dies."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            tail = log_path.read_text()[-3000:] if log_path.exists() else "<no log>"
            raise RuntimeError(
                f"ComfyUI exited during startup (code {process.returncode}). "
                f"Log tail:\n{tail}"
            )
        if _server_alive(port=port):
            with urllib.request.urlopen(
                f"http://{COMFY_HOST}:{port}/system_stats", timeout=10
            ) as resp:
                stats = json.loads(resp.read())
            for dev in stats.get("devices", []):
                log.info(
                    "ComfyUI device: %s (%.1f GB VRAM)",
                    dev.get("name"), dev.get("vram_total", 0) / 1e9,
                )
            log.info("ComfyUI API on port %d is ready", port)
            return
        time.sleep(2)
    raise TimeoutError(f"ComfyUI did not answer within {timeout}s — check {log_path}")
