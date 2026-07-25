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

from config import (
    COMFY_DIR,
    KREA2EDIT_NODES_REPO,
    MODELS_DIR,
    PROJECT_DIR,
    REACTOR_ENABLED,
    REACTOR_LOCAL_NODES,
    REACTOR_NODES_DIR,
    REACTOR_NODES_REPO,
    log,
)

COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"

# Model folders ComfyUI must see. The last four are ReActor's and are only
# linked when the Face Swap tab is enabled.
MODEL_DIRS = ("diffusion_models", "text_encoders", "vae", "loras")
REACTOR_MODEL_DIRS = ("insightface", "facerestore_models", "facedetection",
                      "nsfw_detector")


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


def install_reactor() -> None:
    """Install ComfyUI-ReActor into custom_nodes + its Python dependencies.

    The node pack is vendored in deps/, so this normally just copies it
    into place — no network. Cloning is only a fallback for a checkout
    that does not carry deps/ (it is copied rather than symlinked so the
    '../../models/...' paths inside r_facelib resolve against the ComfyUI
    install, not against this project directory).

    Nothing here is fatal: ReActor only powers the Face Swap tab, so a
    failure leaves that one tab disabled (workflow_reactor.reactor_status
    reports why) and every other tab untouched — the same contract the Wan
    and Flux downloads follow.

    Note there is deliberately no insightface install. This pack vendors
    its own face analysis in reactor_core/ (ReActorFaceAnalysis, SCRFD,
    ArcFaceONNX ...) and drives the buffalo_l ONNX files through
    onnxruntime directly — `import insightface` appears nowhere in it, and
    it is absent from its requirements.txt. So nothing here needs a C++
    toolchain: every dependency installs as a wheel.
    """
    if not REACTOR_ENABLED:
        return
    dest = COMFY_DIR / "custom_nodes" / REACTOR_NODES_DIR
    if dest.exists():
        log.info("ReActor nodes already present at %s — skipping install", dest)
    elif (REACTOR_LOCAL_NODES / "nodes.py").exists():
        # Tested on nodes.py, not just the directory: cloning this repo
        # without its submodule content leaves deps/ComfyUI-ReActor as an
        # empty directory, and copying that would look like a success.
        log.info("Installing vendored ReActor nodes %s → %s",
                 REACTOR_LOCAL_NODES, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REACTOR_LOCAL_NODES, dest,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
    else:
        log.warning("No vendored node pack at %s — falling back to cloning "
                    "it from GitHub", REACTOR_LOCAL_NODES)
        try:
            run_cmd(["git", "clone", "--depth", "1", REACTOR_NODES_REPO, dest],
                    desc="Cloning ComfyUI-ReActor nodes")
        except RuntimeError as exc:
            log.error("Could not clone ComfyUI-ReActor (%s) — the Face Swap "
                      "tab will stay disabled.", exc)
            return

    # onnxruntime runs the swap model; the GPU build is worth having but a
    # CPU fallback still works (a swap is seconds either way).
    try:
        run_cmd([sys.executable, "-m", "pip", "install", "-q",
                 "onnxruntime-gpu"], desc="Installing onnxruntime-gpu")
    except RuntimeError as exc:
        log.warning("onnxruntime-gpu unavailable (%s) — falling back to the "
                    "CPU build", exc)
        try:
            run_cmd([sys.executable, "-m", "pip", "install", "-q",
                     "onnxruntime"], desc="Installing onnxruntime (CPU)")
        except RuntimeError as exc2:
            log.error("No onnxruntime could be installed (%s) — the Face "
                      "Swap tab will stay disabled.", exc2)
            return

    # ReActor's own requirements (onnx, opencv, albumentations, plus
    # segment_anything/ultralytics for its masking nodes — the pack fails to
    # import without them, even though this tab does not use those nodes).
    reqs = dest / "requirements.txt"
    if reqs.exists():
        try:
            run_cmd([sys.executable, "-m", "pip", "install", "-q", "-r", reqs],
                    desc="Installing ReActor requirements")
        except RuntimeError as exc:
            log.error("ReActor requirements failed to install (%s) — the "
                      "Face Swap tab may not load.", exc)


def link_model_dirs() -> None:
    """Point ComfyUI's model folders at MODELS_DIR via symlinks."""
    names = MODEL_DIRS + (REACTOR_MODEL_DIRS if REACTOR_ENABLED else ())
    for name in names:
        src = MODELS_DIR / name
        src.mkdir(parents=True, exist_ok=True)
        dst = COMFY_DIR / "models" / name
        if dst.is_symlink():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        dst.symlink_to(src, target_is_directory=True)
        log.info("Linked %s → %s", dst, src)
