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
import sys
import time
import urllib.request

from config import (
    COMFY_DIR,
    COMFY_HOST,
    COMFY_LOG,
    COMFY_PORT,
    OUTPUT_DIR,
    TEMP_DIR,
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


def _server_alive(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{COMFY_HOST}:{COMFY_PORT}/system_stats", timeout=timeout
        ):
            return True
    except Exception:
        return False


def start_comfyui():
    """Start ComfyUI as a background process (reuses a live server on restart)."""
    if _server_alive():
        log.info("ComfyUI already running on port %d — reusing it", COMFY_PORT)
        return None
    env = os.environ.copy()
    env.pop("CUDA_VISIBLE_DEVICES", None)  # make sure ComfyUI sees every GPU
    cmd = [
        sys.executable, "main.py",
        "--listen", COMFY_HOST,
        "--port", str(COMFY_PORT),
        "--output-directory", str(OUTPUT_DIR),
        "--temp-directory", str(TEMP_DIR / "comfy_temp"),
        "--disable-auto-launch",
    ]
    log.info("Starting ComfyUI (logs → %s)", COMFY_LOG)
    log_handle = open(COMFY_LOG, "a")
    return subprocess.Popen(
        cmd, cwd=COMFY_DIR, env=env,
        stdout=log_handle, stderr=subprocess.STDOUT,
    )


def wait_for_comfyui(process, timeout: int = 300) -> None:
    """Block until the ComfyUI API answers; raise with the log tail if it dies."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            tail = COMFY_LOG.read_text()[-3000:] if COMFY_LOG.exists() else "<no log>"
            raise RuntimeError(
                f"ComfyUI exited during startup (code {process.returncode}). "
                f"Log tail:\n{tail}"
            )
        if _server_alive():
            with urllib.request.urlopen(
                f"http://{COMFY_HOST}:{COMFY_PORT}/system_stats", timeout=10
            ) as resp:
                stats = json.loads(resp.read())
            for dev in stats.get("devices", []):
                log.info(
                    "ComfyUI device: %s (%.1f GB VRAM)",
                    dev.get("name"), dev.get("vram_total", 0) / 1e9,
                )
            log.info("ComfyUI API is ready")
            return
        time.sleep(2)
    raise TimeoutError(f"ComfyUI did not answer within {timeout}s — check {COMFY_LOG}")
