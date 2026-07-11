"""Environment setup — clone ComfyUI and install dependencies.

Installs current ComfyUI's own requirements.txt as-is on top of the pod's
base image, in a single pip resolver pass together with this app's own
requirements.txt (Gradio 6, websocket-client, huggingface_hub, ...).
Nothing is pinned or downgraded: current ComfyUI has no conflict with a
standard PyTorch base image's torch / transformers / safetensors / requests.
"""

import shutil
import subprocess
import sys

from config import COMFY_DIR, KREA2EDIT_NODES_REPO, MODELS_DIR, PROJECT_DIR, log

COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"


def run_cmd(cmd: list, cwd=None, desc: str | None = None) -> None:
    """Run a command, raising with the captured output tail on failure."""
    log.info("%s ...", desc or " ".join(map(str, cmd)))
    result = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stdout.splitlines()[-25:])
        raise RuntimeError(
            f"Command failed ({desc or cmd[0]}), exit {result.returncode}:\n{tail}"
        )


def install_comfyui() -> None:
    """Clone current ComfyUI (idempotent) and install its requirements."""
    if (COMFY_DIR / "main.py").exists():
        log.info("ComfyUI already present at %s — skipping clone", COMFY_DIR)
    else:
        run_cmd(
            ["git", "clone", "--depth", "1", COMFYUI_REPO, COMFY_DIR],
            desc="Cloning ComfyUI",
        )
    run_cmd(
        [sys.executable, "-m", "pip", "install", "-q",
         "-r", COMFY_DIR / "requirements.txt",
         "-r", PROJECT_DIR / "requirements.txt"],
        desc="Installing ComfyUI + app requirements (single resolver pass)",
    )


def install_custom_nodes() -> None:
    """Clone the ComfyUI-Krea2Edit node pack (idempotent).

    Provides the Krea2EditModelPatch / Krea2EditGroundedEncode nodes the
    instruction-edit workflow needs. Must run before the ComfyUI server
    starts so the nodes register; the pack has no extra Python deps.
    """
    dest = COMFY_DIR / "custom_nodes" / "comfyui-krea2edit"
    if dest.exists():
        log.info("Krea2Edit nodes already present at %s — skipping clone", dest)
        return
    run_cmd(
        ["git", "clone", "--depth", "1", KREA2EDIT_NODES_REPO, dest],
        desc="Cloning ComfyUI-Krea2Edit nodes",
    )


def link_model_dirs() -> None:
    """Point ComfyUI's model folders at MODELS_DIR via symlinks."""
    for name in ("diffusion_models", "text_encoders", "vae", "loras"):
        src = MODELS_DIR / name
        src.mkdir(parents=True, exist_ok=True)
        dst = COMFY_DIR / "models" / name
        if dst.is_symlink():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        dst.symlink_to(src, target_is_directory=True)
        log.info("Linked %s → %s", dst, src)
