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
import tarfile
from pathlib import Path

import features
import mirror
from config import (
    COMFY_DIR,
    FROZEN,
    KREA2EDIT_NODES_REPO,
    MODELS_DIR,
    PROJECT_DIR,
    REACTOR_LOCAL_NODES,
    REACTOR_NODES_DIR,
    REACTOR_NODES_REPO,
    V2_NODE_REPOS,
    log,
)

COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"

# Model folders ComfyUI must see. The last four are ReActor's and are only
# linked when the Face Swap tab is enabled.
MODEL_DIRS = ("diffusion_models", "text_encoders", "vae", "loras")
REACTOR_MODEL_DIRS = ("insightface", "facerestore_models", "facedetection",
                      "nsfw_detector")

# PyPI's current onnxruntime-gpu wheel links CUDA 13, so on a CUDA 12 pod it
# installs cleanly and then dies at import with "libcudart.so.13: cannot open
# shared object file". 1.22.0 is the last CUDA 12 line; Microsoft's CUDA 12
# feed (the one ReActor's own install.py uses) is added as a second source.
ONNXRUNTIME_CUDA12_PIN = "onnxruntime-gpu==1.22.0"
ONNXRUNTIME_CUDA12_INDEX = (
    "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/"
    "onnxruntime-cuda-12/pypi/simple/"
)


def runtime_python() -> str:
    """The interpreter used for pip and for launching ComfyUI.

    This app never imports torch or ComfyUI — it pip-installs them and runs
    ComfyUI as a separate process, which only works while there is a real
    interpreter to hand. Compiled with Nuitka there is not: sys.executable
    is this binary, so `-m pip install ...` and `main.py ...` would just be
    nonsense arguments to ourselves. Fall back to the system python3 that
    the pod already ships.

    Uncompiled this returns sys.executable, so `python3 app.py` behaves
    exactly as before — that has to stay true, it is how the app is
    developed.
    """
    if not FROZEN:
        return sys.executable
    found = shutil.which("python3") or shutil.which("python")
    if not found:
        raise RuntimeError(
            "No python3 found on PATH. The binary bundles this app, but "
            "ComfyUI still runs as a separate Python process and needs an "
            "interpreter — install python3 and re-run."
        )
    return found


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


def clone_pinned(url: str, dest, name: str, desc: str) -> None:
    """Clone `url` at the revision PINS.json records for `name`.

    An unpinned `git clone --depth 1` means "whatever is on main today",
    which is how a pod that worked yesterday breaks with no change on your
    side — ComfyUI moves constantly and RES4LYF renames node parameters.
    Pinning is what makes two pods byte-identical.

    Falls back to shallow-cloning HEAD when there is no pin, because an
    unpinned checkout still beats no app at all; the log says which you got.
    """
    sha = mirror.node_pin(name).get("sha")
    if not sha:
        log.warning("No pin recorded for %s — cloning HEAD, which may not "
                    "be the revision this release was tested against.", name)
        run_cmd(["git", "clone", "--depth", "1", url, dest], desc=desc)
        return
    # Not --depth 1: a shallow clone of main cannot check out an arbitrary
    # older commit, which is exactly what a pin usually is.
    run_cmd(["git", "clone", url, dest], desc=desc)
    run_cmd(["git", "-C", dest, "checkout", "--quiet", sha],
            desc=f"Pinning {name} to {sha[:8]}")


def node_pack_from_mirror(dirname: str, dest) -> bool:
    """Install a custom-node pack from the mirror's pinned tarball.

    Preferred over cloning because the tarball IS the pin — it was packed
    from the commit that was verified working, so there is no window where
    a rename upstream and a stale pin disagree. Cloning stays the fallback.
    """
    pin = mirror.node_pin(dirname)
    tarball, repo = pin.get("tarball"), mirror.repo_id("nodes")
    if not (mirror.MIRROR_ENABLED and tarball and repo):
        return False
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=repo, filename=tarball,
                               token=mirror.token())
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path) as tf:
            try:
                # Refuses absolute paths and ../ escapes; a mirror is still
                # a remote archive being unpacked into ComfyUI's tree.
                tf.extractall(dest.parent, filter="data")
            except TypeError:
                tf.extractall(dest.parent)      # Python < 3.12
        if dest.exists():
            log.info("Installed %s from mirror tarball (%s)",
                     dirname, (pin.get("sha") or "")[:8])
            return True
        log.warning("Mirror tarball for %s did not contain %s — cloning "
                    "instead.", dirname, dest.name)
        return False
    except Exception as exc:
        log.warning("Mirror has no usable tarball for %s (%s) — cloning "
                    "from GitHub.", dirname, exc)
        return False


def install_node_pack(dirname: str, url: str, dest, desc: str) -> None:
    """Mirror tarball first, pinned clone second. Raises if both fail."""
    if not node_pack_from_mirror(dirname, dest):
        clone_pinned(url, dest, dirname, desc)


def install_comfyui() -> None:
    """Clone ComfyUI at its pinned revision (idempotent) and install reqs."""
    if (COMFY_DIR / "main.py").exists():
        log.info("ComfyUI already present at %s — skipping clone", COMFY_DIR)
    else:
        clone_pinned(COMFYUI_REPO, COMFY_DIR, "ComfyUI", "Cloning ComfyUI")
    # ComfyUI's requirements always install — they belong to the subprocess,
    # not to us. The app's own (gradio, huggingface_hub, ...) are compiled
    # into the binary by build.sh, so re-installing them on someone else's
    # pod would only cost time and bandwidth.
    reqs = ["-r", COMFY_DIR / "requirements.txt"]
    desc = "Installing ComfyUI requirements"
    if not FROZEN:
        reqs += ["-r", PROJECT_DIR / "requirements.txt"]
        desc = "Installing ComfyUI + app requirements (single resolver pass)"
    run_cmd([runtime_python(), "-m", "pip", "install", "-q", *reqs], desc=desc)


def install_custom_nodes() -> None:
    """Clone the ComfyUI-Krea2Edit node pack (idempotent).

    Provides the Krea2EditModelPatch / Krea2EditGroundedEncode nodes the
    instruction-edit workflow needs. Must run before the ComfyUI server
    starts so the nodes register; the pack has no extra Python deps.

    Only the Edit tab uses those two nodes, so this is skipped entirely
    when that feature is off — it used to run unconditionally.
    """
    if not features.enabled(features.Key.KREA_EDIT):
        return
    dest = COMFY_DIR / "custom_nodes" / "comfyui-krea2edit"
    if dest.exists():
        log.info("Krea2Edit nodes already present at %s — skipping clone", dest)
        return
    install_node_pack("comfyui-krea2edit", KREA2EDIT_NODES_REPO, dest,
                      "Cloning ComfyUI-Krea2Edit nodes")


def install_v2_nodes() -> None:
    """Clone the node packs the Krea 2 V2 tab needs, plus their requirements.

    RES4LYF (ClownsharKSampler_Beta) and RBG Smart Seed Variance both change
    the image and have no core equivalent; the post-processing pack supplies
    FilmGrain for that tab's optional grain toggle.

    Nothing here is fatal, and each pack is independent: a failed clone or a
    failed requirements install leaves the V2 tab reporting which node is
    missing and every other tab untouched — the same contract install_reactor
    follows. app.py verifies afterwards that each class actually registered.
    """
    if not features.enabled(features.Key.KREA_V2_T2I):
        return
    for dirname, repo, _class_type in V2_NODE_REPOS:
        dest = COMFY_DIR / "custom_nodes" / dirname
        if dest.exists():
            log.info("%s already present at %s — skipping clone", dirname, dest)
        else:
            try:
                install_node_pack(dirname, repo, dest, f"Cloning {dirname}")
            except RuntimeError as exc:
                log.error("Could not clone %s (%s) — the Krea 2 V2 tab will "
                          "refuse to run until it is installed.", dirname, exc)
                continue
        reqs = dest / "requirements.txt"
        if not reqs.exists():
            continue
        try:
            run_cmd([runtime_python(), "-m", "pip", "install", "-q", "-r", reqs],
                    desc=f"Installing {dirname} requirements")
        except RuntimeError as exc:
            log.error("%s requirements failed to install (%s) — the pack may "
                      "not load in ComfyUI.", dirname, exc)


def _can_import(module: str) -> bool:
    """True if `module` imports cleanly in a fresh interpreter.

    pip reporting success is not enough for onnxruntime: a wheel built
    against the wrong CUDA installs fine and only fails when something
    imports it — which, for ReActor, means ComfyUI silently skipping the
    node pack and the first face swap failing with "node not found".
    """
    return subprocess.run(
        [runtime_python(), "-c", f"import {module}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _cuda_major() -> int | None:
    """torch's CUDA major version, or None if torch has no CUDA build."""
    result = subprocess.run(
        [runtime_python(), "-c", "import torch; print(torch.version.cuda or '')"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        return int(result.stdout.strip().split(".")[0])
    except ValueError:
        return None


def _uninstall_onnxruntime() -> None:
    """Drop any onnxruntime build so the next install is not 'already met'."""
    try:
        run_cmd([runtime_python(), "-m", "pip", "uninstall", "-y", "-q",
                 "onnxruntime-gpu", "onnxruntime"],
                desc="Removing the existing onnxruntime")
    except RuntimeError as exc:
        log.debug("onnxruntime uninstall reported: %s", exc)


def install_onnxruntime() -> bool:
    """Install an onnxruntime that actually imports. True on success.

    Each candidate is verified by importing it, because that is the exact
    failure this works around, and a build that installs but cannot import
    is removed before trying the next one (pip would otherwise call the
    replacement "already satisfied"). The CPU build is the last resort:
    inswapper_128 is small, so a CPU swap still takes seconds — much
    better than no Face Swap tab.
    """
    if _can_import("onnxruntime"):
        log.info("onnxruntime already imports cleanly — skipping install")
        return True

    cuda = _cuda_major()
    log.info("Selecting an onnxruntime build for CUDA %s",
             cuda if cuda is not None else "<none detected>")
    if cuda == 12:
        candidates = [
            ([ONNXRUNTIME_CUDA12_PIN, "--extra-index-url",
              ONNXRUNTIME_CUDA12_INDEX], f"{ONNXRUNTIME_CUDA12_PIN} (CUDA 12)"),
            (["onnxruntime"], "onnxruntime (CPU)"),
        ]
    elif cuda is not None:
        candidates = [(["onnxruntime-gpu"], "onnxruntime-gpu"),
                      (["onnxruntime"], "onnxruntime (CPU)")]
    else:
        candidates = [(["onnxruntime"], "onnxruntime (CPU)")]

    # A previous run can leave a broken build behind — clear it first.
    _uninstall_onnxruntime()
    for args, desc in candidates:
        try:
            run_cmd([runtime_python(), "-m", "pip", "install", "-q", *args],
                    desc=f"Installing {desc}")
        except RuntimeError as exc:
            log.warning("%s would not install (%s) — trying the next option",
                        desc, exc)
            continue
        if _can_import("onnxruntime"):
            log.info("%s installed and imports cleanly", desc)
            return True
        log.warning("%s installed but does not import (usually a CUDA "
                    "runtime mismatch) — trying the next option", desc)
        _uninstall_onnxruntime()
    log.error("No usable onnxruntime could be installed — the Face Swap tab "
              "will stay disabled.")
    return False


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
    if not features.enabled(features.Key.FACESWAP):
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

    if not install_onnxruntime():
        return

    # ReActor's own requirements (onnx, opencv, albumentations, plus
    # segment_anything/ultralytics for its masking nodes — the pack fails to
    # import without them, even though this tab does not use those nodes).
    reqs = dest / "requirements.txt"
    if reqs.exists():
        try:
            run_cmd([runtime_python(), "-m", "pip", "install", "-q", "-r", reqs],
                    desc="Installing ReActor requirements")
        except RuntimeError as exc:
            log.error("ReActor requirements failed to install (%s) — the "
                      "Face Swap tab may not load.", exc)

    # Same trap as onnxruntime: these can install cleanly and still fail to
    # import (ABI or numpy mismatches). Checking here names the culprit,
    # instead of ComfyUI reporting it much later as a missing node.
    broken = [m for m in ("cv2", "onnx", "albumentations", "segment_anything",
                          "ultralytics") if not _can_import(m)]
    if broken:
        log.error("ReActor dependencies installed but NOT importable: %s — "
                  "the node pack will fail to load in ComfyUI.",
                  ", ".join(broken))


def link_model_dirs() -> None:
    """Point ComfyUI's model folders at MODELS_DIR via symlinks."""
    names = MODEL_DIRS + (REACTOR_MODEL_DIRS
                          if features.enabled(features.Key.FACESWAP) else ())
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
