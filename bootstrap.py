"""Environment setup — clone ComfyUI and install dependencies.

Installs current ComfyUI's own requirements.txt as-is on top of the pod's
base image, in a single pip resolver pass together with this app's own
requirements.txt (Gradio 6, websocket-client, huggingface_hub, ...).
Nothing is pinned or downgraded: current ComfyUI has no conflict with a
standard PyTorch base image's torch / transformers / safetensors / requests.
"""

import json
import shutil
import subprocess
import sys
import tarfile
from collections import deque
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

    # Which name to try first is platform-specific, and getting this
    # backwards is not cosmetic. "python3" is a Unix convention; on Windows
    # the interpreter a customer installs is python.exe, and python3.exe is
    # typically one of three things that are not it: absent, MSYS2's (which
    # ships no pip at all), or the zero-byte Microsoft Store alias stub that
    # is present by default on Windows 11. Asking for python3 first there
    # reaches straight past the right interpreter for one that cannot
    # install anything.
    names = ("python", "python3") if sys.platform == "win32" else ("python3", "python")

    # Validated rather than taken on trust. shutil.which only proves that a
    # name resolves, and both Windows impostors above resolve perfectly
    # well; what they cannot do is `-m pip install`, which is the only
    # reason this function exists. Left unchecked the failure surfaces as
    # "No module named pip" from inside the first install — after the
    # licence seat is taken and several log lines past anything that
    # mentions an interpreter.
    tried = []
    for name in names:
        found = shutil.which(name)
        if not found or found in tried:
            continue
        tried.append(found)
        # The Store alias is a zero-byte reparse point that opens the
        # Microsoft Store when executed. Skip it by shape rather than
        # running it, so probing never pops a shop window at a customer.
        try:
            if Path(found).stat().st_size == 0:
                continue
        except OSError:
            continue
        try:
            probe = subprocess.run(
                [found, "-c", "import pip"],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return found

    if tried:
        raise RuntimeError(
            "Found %s on PATH, but no interpreter there can import pip. "
            "ComfyUI runs as a separate Python process and is installed "
            "with pip, so the app cannot continue. Install Python 3.12 "
            "from python.org (tick \"Add python.exe to PATH\") and make "
            "sure it comes before any MSYS2 or Microsoft Store entry."
            % ", ".join(tried)
        )
    raise RuntimeError(
        "No Python interpreter found on PATH. The binary bundles this app, "
        "but ComfyUI still runs as a separate Python process and needs an "
        "interpreter — install Python 3.12 and re-run."
    )


def run_cmd(cmd: list, cwd=None, desc: str | None = None) -> None:
    """Run a command, echoing its output, and raise with the tail on failure.

    Streamed line by line rather than captured whole. The difference only
    matters for one command, but it matters a lot there: installing
    ComfyUI's requirements into an empty environment takes tens of minutes,
    and with the output swallowed there is nothing on screen for any of it.
    A customer reads a window that has printed nothing for twenty minutes
    as a hang, and closes it — halfway through a pip install, which is the
    one moment it is genuinely expensive to do.

    The last lines are still kept, so a failure reports the same tail it
    always did. Keeping both is the point: the output has to be visible
    while it works *and* summarised when it does not, and a caller that
    only ever saw the summary could not tell a slow install from a stuck
    one.
    """
    log.info("%s ...", desc or " ".join(map(str, cmd)))
    tail = deque(maxlen=25)
    # bufsize=1 is line buffering, so a long install appears as it happens
    # rather than in blocks whenever a pipe buffer happens to fill.
    process = subprocess.Popen(
        [str(c) for c in cmd], cwd=cwd, text=True, bufsize=1,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for line in process.stdout:
        line = line.rstrip()
        tail.append(line)
        print(line, flush=True)
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed ({desc or cmd[0]}), exit {process.returncode}:\n"
            + "\n".join(tail)
        )


# ── PyTorch ───────────────────────────────────────────────────────────────────
# On a pod, torch arrives with the base image and this whole section is a
# no-op. Off a pod — a local Windows or Linux box with nothing but Python —
# there is no base image, and something has to be it.
#
# These pins reproduce runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404, the
# image the app is tested on. They are used ONLY when torch is absent
# entirely: whatever is already installed always wins, because on a pod that
# is the tested configuration and replacing it would be replacing the thing
# this app is known to work on.
#
# cu128 rather than a default PyPI wheel because Blackwell cards (RTX 50xx,
# compute capability sm_120) have no kernels before CUDA 12.8. A default
# wheel installs cleanly, imports cleanly, reports cuda.is_available() as
# True, and then fails at the first generation with "no kernel image is
# available for execution on the device" — which is why _probe_torch runs a
# real matmul rather than trusting version strings.
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_STACK = ("torch==2.8.0", "torchvision==0.23.0", "torchaudio==2.8.0")

# Written next to the ComfyUI checkout and passed to every later pip call.
# ComfyUI's requirements.txt lists `torch` unpinned, so without this a
# ComfyUI release that asks for a newer torch would upgrade it underneath a
# working install and break the compiled extensions that link against it.
TORCH_CONSTRAINTS = "torch-constraints.txt"

# Probes the *runtime* interpreter, not this process: bootstrap never
# imports torch itself, and a torch imported before an install would be a
# stale module object afterwards. Isolating it in a subprocess also means a
# hard CUDA fault takes out the probe rather than the app.
_TORCH_PROBE = r"""
import json
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda"] = torch.version.cuda
    out["cuda_available"] = bool(torch.cuda.is_available())
    if out["cuda_available"]:
        major, minor = torch.cuda.get_device_capability(0)
        out["arch"] = "sm_%d%d" % (major, minor)
        out["arch_list"] = list(torch.cuda.get_arch_list())
        out["device"] = torch.cuda.get_device_name(0)
        try:
            a = torch.randn(256, 256, device="cuda", dtype=torch.float16)
            (a @ a).sum().item()
            torch.cuda.synchronize()
            out["matmul"] = True
        except Exception as exc:
            out["matmul"] = False
            out["matmul_error"] = "%s: %s" % (type(exc).__name__, exc)
except ModuleNotFoundError:
    out["missing"] = True
except Exception as exc:
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
for name in ("torchvision", "torchaudio"):
    try:
        out[name] = __import__(name).__version__
    except Exception as exc:
        out[name + "_error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
"""


# The last probe result ensure_torch accepted. Only a cache: every probe
# spins up a CUDA context to run its matmul, and the constraints file wants
# the same answer ensure_torch already paid for.
_torch_info: dict | None = None


def _probe_torch() -> dict:
    """What the runtime interpreter's torch stack actually is, and does."""
    result = subprocess.run(
        [runtime_python(), "-c", _TORCH_PROBE],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "probe produced no output"}


def _torch_index(info: dict) -> str:
    """The wheel index matching the *installed* torch's CUDA build.

    A repair has to come from the same CUDA line as the torch already on
    the machine — pulling a cu128 companion onto a cu126 torch swaps one
    ABI mismatch for another. Falls back to the pinned index when torch
    reports no CUDA version to derive from.
    """
    cuda = (info.get("cuda") or "").strip()
    if not cuda:
        return TORCH_INDEX
    return f"https://download.pytorch.org/whl/cu{cuda.replace('.', '')}"


def _pip_install(args: list, desc: str) -> None:
    run_cmd([runtime_python(), "-m", "pip", "install", *args], desc=desc)


def torch_constraints_file() -> Path | None:
    """Pin the installed torch stack so later pip passes cannot move it.

    Written from what is actually installed rather than from TORCH_STACK,
    so a pod running a different torch than these pins keeps its own.
    """
    info = _torch_info if _torch_info is not None else _probe_torch()
    lines = [f"{name}=={info[name].split('+')[0]}"
             for name in ("torch", "torchvision", "torchaudio")
             if isinstance(info.get(name), str)]
    if not lines:
        return None
    path = COMFY_DIR.parent / TORCH_CONSTRAINTS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        log.warning("Could not write %s (%s) — later pip passes are "
                    "unconstrained and could upgrade torch", path, exc)
        return None


def _describe(info: dict) -> str:
    return (f"torch {info.get('torch')} (CUDA {info.get('cuda')}) on "
            f"{info.get('device')} [{info.get('arch')}]")


def _fix_hint(packages, info: dict) -> str:
    return ("  " + runtime_python() + " -m pip install --force-reinstall "
            + " ".join(packages) + " --index-url " + _torch_index(info))


def ensure_torch() -> None:
    """Make sure the runtime interpreter has a torch that works on this GPU.

    Four outcomes, and the split between them is the point:

      * everything imports and a real GPU matmul runs  -> return silently.
        This is the pod path, every time.
      * torch absent entirely -> install TORCH_STACK from TORCH_INDEX.
        Nothing was there to have an opinion about.
      * torch present but unusable (no CUDA, or no kernels for this card)
        -> raise, naming the fix. Deliberately NOT repaired: on a pod that
        torch IS the tested base image, and silently replacing it would
        swap a known configuration for a guess.
      * torch fine, torchvision/torchaudio failing to import -> repair just
        those, with --no-deps so torch itself cannot be touched.

    That last case is the WinError 127 class: torchvision and torchaudio
    ship compiled extensions linked against torch's own C++ ABI, so a
    companion built for a different torch imports "successfully" as far as
    pip is concerned and then dies inside ctypes. torchaudio's version
    tracks torch's exactly, which is what makes the repair derivable.
    """
    global _torch_info
    info = _probe_torch()

    if info.get("missing"):
        log.info("No torch on the runtime interpreter — installing the "
                 "tested stack (%s)", ", ".join(TORCH_STACK))
        _pip_install([*TORCH_STACK, "--index-url", TORCH_INDEX],
                     desc="Installing PyTorch")
        info = _probe_torch()
        if info.get("missing") or info.get("error"):
            raise RuntimeError(
                "PyTorch still is not importable after installing it: "
                f"{info.get('error', 'module not found')}"
            )

    if info.get("error"):
        raise RuntimeError(
            f"PyTorch is installed but will not import: {info['error']}\n"
            "Reinstall it with:\n" + _fix_hint(TORCH_STACK, info)
        )

    # Torch itself: report, never replace.
    if not info.get("cuda"):
        raise RuntimeError(
            f"torch {info.get('torch')} is a CPU-only build — this app needs "
            "CUDA.\nInstall a CUDA build with:\n" + _fix_hint(TORCH_STACK, info)
        )
    if not info.get("cuda_available"):
        raise RuntimeError(
            f"torch {info.get('torch')} reports no usable CUDA device. Check "
            "the driver (nvidia-smi) and that a GPU is attached."
        )
    arch, arch_list = info.get("arch"), info.get("arch_list") or []
    if arch and arch not in arch_list:
        raise RuntimeError(
            f"This torch has no kernels for {info.get('device')} ({arch}). It "
            f"was built for: {', '.join(arch_list)}.\nBlackwell cards need a "
            "CUDA 12.8+ build. Install one with:\n"
            + _fix_hint(TORCH_STACK, info)
        )
    if info.get("matmul") is False:
        raise RuntimeError(
            f"torch loads but cannot run a kernel on {info.get('device')}: "
            f"{info.get('matmul_error')}\nThis is normally a torch built for "
            "a different CUDA than the card needs. Install the tested "
            "stack with:\n" + _fix_hint(TORCH_STACK, info)
        )

    # Companions: repairable, because they cannot break torch.
    broken = [name for name in ("torchvision", "torchaudio")
              if info.get(f"{name}_error")]
    if broken:
        torch_version = (info.get("torch") or "").split("+")[0]
        wanted = []
        for name in broken:
            log.warning("%s fails to import (%s) — its compiled extension "
                        "does not match torch %s",
                        name, info[f"{name}_error"], torch_version)
            if name == "torchaudio":
                # Version-locked to torch, so this is always derivable.
                wanted.append(f"torchaudio=={torch_version}")
            elif torch_version == "2.8.0":
                wanted.append("torchvision==0.23.0")
            else:
                raise RuntimeError(
                    f"torchvision fails to import against torch "
                    f"{torch_version}, and there is no rule to derive the "
                    "matching torchvision version (it numbers itself "
                    "separately). Install the pair that goes with your "
                    "torch from " + _torch_index(info)
                )
        # --no-deps is load-bearing: without it pip would resolve these
        # packages' own torch requirement and could move torch itself.
        _pip_install([*wanted, "--force-reinstall", "--no-deps",
                      "--index-url", _torch_index(info)],
                     desc=f"Repairing {', '.join(broken)}")
        info = _probe_torch()
        still = [n for n in ("torchvision", "torchaudio")
                 if info.get(f"{n}_error")]
        if still:
            raise RuntimeError(
                f"{', '.join(still)} still will not import after repair: "
                + "; ".join(info[f"{n}_error"] for n in still)
            )

    _torch_info = info
    log.info("PyTorch OK — %s", _describe(info))


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


def repin_checkout(dest, name: str) -> None:
    """Move an existing clone to the revision PINS.json records for `name`.

    clone_pinned only ever runs on an empty directory, so a pod volume, a
    Docker image's baked tree or a Windows install that already holds a
    checkout keeps whatever revision it was cloned at, for ever — a pin
    that moves in a release reaches new machines and nobody else. That is
    tolerable for a pin that only moves to change weights, and not for one
    that moves because a tab needs nodes the old checkout does not have:
    ComfyUI went from v0.29 to v0.34 for MiniMax H3, whose nodes are core
    ComfyUI, and every existing volume would otherwise refuse that tab with
    "node not found" until somebody deleted the folder by hand.

    Best-effort by design. A fetch that fails — no network, a server that
    will not serve the commit — logs and leaves the checkout where it is:
    the app still starts on the revision it has, and comfy.verify_core_node
    says which tab that costs. It never touches a tree that is not a git
    checkout, and never runs when there is no pin to move to.
    """
    sha = mirror.node_pin(name).get("sha")
    dest = Path(dest)
    if not sha or not (dest / ".git").exists():
        return
    try:
        head = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not read %s's revision (%s) — leaving it as is.",
                    name, exc)
        return
    if head == sha:
        return
    log.info("%s is at %s and this release pins %s — moving the checkout",
             name, head[:8] or "?", sha[:8])
    try:
        # `fetch origin <sha>` rather than a plain fetch: it is the one form
        # that works on both a full clone and the shallow one clone_pinned's
        # unpinned fallback makes, and it pulls exactly the commit wanted.
        run_cmd(["git", "-C", dest, "fetch", "--quiet", "origin", sha],
                desc=f"Fetching {name} {sha[:8]}")
        run_cmd(["git", "-C", dest, "checkout", "--quiet", sha],
                desc=f"Pinning {name} to {sha[:8]}")
    except Exception as exc:                  # noqa: BLE001
        log.error("Could not move %s to %s (%s) — continuing on %s. Tabs "
                  "that need the newer revision will say so at startup.",
                  name, sha[:8], exc, head[:8] or "?")


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
        # ... but not skipping the pin. An existing checkout is the one
        # thing clone_pinned never sees, so a pin that moved in a release
        # has to be applied here or it applies to nobody who already ran
        # the app. Best-effort; see repin_checkout.
        repin_checkout(COMFY_DIR, "ComfyUI")
    else:
        clone_pinned(COMFYUI_REPO, COMFY_DIR, "ComfyUI", "Cloning ComfyUI")
    # ComfyUI's requirements always install — they belong to the subprocess,
    # not to us. The app's own (fastapi, huggingface_hub, ...) are compiled
    # into the binary by build.sh, so re-installing them on someone else's
    # pod would only cost time and bandwidth.
    reqs = ["-r", COMFY_DIR / "requirements.txt"]
    desc = "Installing ComfyUI requirements"
    if not FROZEN:
        reqs += ["-r", PROJECT_DIR / "requirements.txt"]
        desc = "Installing ComfyUI + app requirements (single resolver pass)"
    # ComfyUI lists `torch` unpinned, so this pass is free to upgrade it —
    # which would leave torchvision/torchaudio compiled against the torch
    # that just went away. Constrain it to whatever ensure_torch settled on.
    constraints = torch_constraints_file()
    if constraints is not None:
        reqs += ["--constraint", constraints]
    run_cmd([runtime_python(), "-m", "pip", "install", *reqs], desc=desc)


def install_custom_nodes() -> None:
    """Clone the ComfyUI-Krea2Edit node pack (idempotent).

    Provides the Krea2EditModelPatch / Krea2EditGroundedEncode nodes the
    instruction-edit workflow needs. Must run before the ComfyUI server
    starts so the nodes register; the pack has no extra Python deps.

    Only the two Edit tabs use those two nodes, so this is skipped
    entirely when both features are off — it used to run unconditionally.

    A failure here is logged, not raised, which is the contract
    install_v2_nodes already follows: the two Edit tabs then report the
    missing nodes and refuse to run, and every other tab starts normally.
    It used to abort the whole bootstrap, so one unreachable node pack
    took the entire app down with it — including the tabs that never
    needed those nodes.
    """
    if not (features.enabled(features.Key.KREA_EDIT)
            or features.enabled(features.Key.KREA_V2_EDIT)):
        return
    dest = COMFY_DIR / "custom_nodes" / "comfyui-krea2edit"
    if dest.exists():
        log.info("Krea2Edit nodes already present at %s — skipping clone", dest)
        return
    try:
        install_node_pack("comfyui-krea2edit", KREA2EDIT_NODES_REPO, dest,
                          "Cloning ComfyUI-Krea2Edit nodes")
    except RuntimeError as exc:
        log.error("Could not install comfyui-krea2edit (%s) — the Edit tabs "
                  "will refuse to run until it is installed.", exc)


def install_v2_nodes() -> None:
    """Clone the node packs the Krea 2 V2 tab needs, plus their requirements.

    RES4LYF (ClownsharKSampler_Beta) and RBG Smart Seed Variance both change
    the image and have no core equivalent; the post-processing pack supplies
    FilmGrain for that tab's optional grain toggle.

    Nothing here is fatal, and each pack is independent: a failed clone or a
    failed requirements install leaves the V2 tab reporting which node is
    missing and every other tab untouched — the same contract install_reactor
    follows. app.py verifies afterwards that each class actually registered.

    Krea 2 V2 Edit runs the same sampler and variance nodes, so it pulls
    these packs in too — either feature on its own is enough.
    """
    if not (features.enabled(features.Key.KREA_V2_T2I)
            or features.enabled(features.Key.KREA_V2_EDIT)):
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
            run_cmd([runtime_python(), "-m", "pip", "install", "-r", reqs],
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
            run_cmd([runtime_python(), "-m", "pip", "install", *args],
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
            run_cmd([runtime_python(), "-m", "pip", "install", "-r", reqs],
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
    """Point ComfyUI's model folders at MODELS_DIR via symlinks.

    The links are absolute, so moving or renaming the base directory leaves
    every one of them dangling. That is worth repairing rather than
    skipping: ComfyUI does not treat an unreadable model folder as an
    error, it just sees no models — and the first sign of trouble is every
    generation failing validation with "Prompt outputs failed validation",
    which says nothing about symlinks.

    So the test is where a link *points*, not merely that it is a link. An
    existing symlink is left alone only when it already resolves to src;
    one pointing anywhere else, or at a path that no longer exists, is
    replaced.
    """
    names = MODEL_DIRS + (REACTOR_MODEL_DIRS
                          if features.enabled(features.Key.FACESWAP) else ())
    for name in names:
        src = MODELS_DIR / name
        src.mkdir(parents=True, exist_ok=True)
        dst = COMFY_DIR / "models" / name
        if dst.is_symlink():
            # resolve(strict=False): a dangling link resolves to the target
            # it names, so a stale one compares unequal rather than raising.
            if dst.resolve() == src.resolve():
                continue
            log.warning("Relinking %s — it pointed at %s, which is not %s",
                        dst, dst.resolve(), src)
            dst.unlink()
        elif dst.exists():
            shutil.rmtree(dst)
        dst.symlink_to(src, target_is_directory=True)
        log.info("Linked %s → %s", dst, src)
