# Krea 2 — the app's environment, pre-built.
#
# What this image is: everything a pod spends its first several minutes
# doing, done once at build time. The ComfyUI checkout at its pinned SHA,
# the custom node packs and ComfyUI's Python requirements.
#
# What it deliberately is NOT: the app. The binary is still fetched at boot
# by scripts/runpod_start.sh, unmodified and baked in at /opt/krea2/bin.
# That keeps three things true — publishing a build still reaches these
# containers through /v1/build, `make promote` can still roll them back,
# and the image holds no secret and no licensed code, so it is safe to
# publish. An app release needs no image rebuild.
#
# Boot then looks like this:
#
#     /entrypoint.sh  ->  /opt/krea2/bin/start.sh  ->  the binary
#      (symlinks the        (fetches + verifies       (bootstrap finds
#       baked ComfyUI)       the build)                everything present)
#
# Models are never baked: ~90 GB, licence-gated, and they belong on the
# volume. See docker-compose.yml.
#
#     docker build -t krea2:latest .
#     docker compose up

# CUDA 13.0 and cuDNN on Ubuntu 24.04, and nothing else — the same family
# MiniMax template v8 is built on. Deliberately NOT runpod/pytorch: that
# image carries its own torch (2.8.0+cu128, ~7 GB) and the CUDA dev
# toolchain (~9 GB more), all of it dead weight once docker/bake_torch.py's
# torch 2.11.0+cu130 is installed over it. Starting slim is the difference
# between a ~40 GB image and a ~15 GB one, which is what a pod pulls on
# every cold start and what has to be pushed to a registry at all.
#
# Python comes from apt below rather than from the base: Ubuntu 24.04 ships
# 3.12, which is what the SageAttention wheel (cp312) and the app's Nuitka
# build target.
#
# CUDA 13 needs an R580+ driver on the host. On RunPod, set the template's
# CUDA version filter to 13.0, as template v8 does.
ARG BASE_IMAGE=nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04


# ── Stage 0: Python and the system libraries both stages need ─────────────
# One stage so the clone stage and the image itself resolve `python3` and
# `git` the same way, and share these layers.
#
# A venv rather than the system interpreter because Ubuntu 24.04 marks it
# externally managed (PEP 668): `pip install` into it refuses outright.
# /opt/venv/bin first on PATH makes that venv what `python3` means for
# every RUN below, for the app (bootstrap.runtime_python resolves python3
# from PATH) and for ComfyUI, which the app launches the same way.
FROM ${BASE_IMAGE} AS python-base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# What runpod_start.sh checks for (curl, coreutils, python3, git), what
# opencv and ffmpeg-based nodes need, and libgomp1, which torch's own
# kernels link against and a runtime CUDA image does not ship.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv \
        curl coreutils git ca-certificates \
        ffmpeg libgl1 libglib2.0-0 libgomp1 && \
    python3.12 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    python3 -c "import sys; print('python', sys.version)"


# ── Stage 1: clone ────────────────────────────────────────────────────────
# Separate purely so the app's source does not reach the published image.
# bake_nodes.py needs config.py/bootstrap.py/mirror.py to resolve the pins,
# and those are exactly the files build.sh compiles into a binary rather
# than shipping. They stay in this stage; only /opt/krea2 is copied out.
FROM python-base AS nodes

# huggingface_hub for the mirror tarball path in bootstrap.node_pack_from_mirror;
# hf_xet because without it a Xet-backed download falls back to single-stream
# HTTP, which is the difference between seconds and many minutes.
RUN python3 -m pip install --no-cache-dir huggingface_hub hf_xet

WORKDIR /src
# scripts/ first: PINS.json and mirror_manifest.json change far more often
# than the four modules, and mirror.py looks in PROJECT_DIR/scripts for both.
COPY scripts/PINS.json scripts/mirror_manifest.json /src/scripts/
COPY ember/__init__.py ember/config.py ember/features.py /src/ember/
COPY ember/weights/__init__.py ember/weights/mirror.py /src/ember/weights/
COPY ember/comfy/__init__.py ember/comfy/setup.py /src/ember/comfy/
COPY docker/bake_nodes.py /src/bake_nodes.py

RUN python3 /src/bake_nodes.py

# After the clone rather than inside it, so a change to the torch pins
# reuses the cached ComfyUI and node packs.
COPY docker/bake_torch.py /src/bake_torch.py
RUN python3 /src/bake_torch.py


# ── Stage 2: the image ────────────────────────────────────────────────────
FROM python-base AS final

ENV HF_XET_HIGH_PERFORMANCE=1 \
    KREA2_BASE_DIR=/workspace/krea2

# `python3` on PATH is not a detail here: compiled with Nuitka the app's
# sys.executable is the binary itself, so bootstrap.runtime_python() falls
# back to `shutil.which("python3")` for every pip call and for launching
# ComfyUI. Whatever that resolves to IS the app's interpreter — here the
# venv python-base put first on PATH — so every install below goes through
# `python3 -m pip` rather than bare `pip`. Installing into a different
# interpreter than the app uses would produce an image that looks complete
# and behaves like an empty one.
#
# Failing here rather than at boot is the point: a base image whose python3
# cannot install anything says so in the build log instead of on a pod.
RUN python3 -c "import sys, pip; \
    print('python', sys.version.split()[0], 'at', sys.executable, \
          '- pip', pip.__version__); \
    sys.exit(0 if sys.version_info[:2] == (3, 12) else \
             'python 3.12 is what the wheels here are built for')"

COPY --from=nodes /opt/krea2 /opt/krea2

# Template v8's torch (2.11.0+cu130), over the base image's, before anything
# that installs against it. Asserted against baked.json, so a wheel index
# that quietly served a different build fails here rather than as a
# SageAttention kernel that will not load on a customer's pod.
RUN python3 -m pip install --no-cache-dir -r /opt/krea2/torch-stack.txt && \
    python3 -c "import json, sys, torch; \
    want = json.load(open('/opt/krea2/baked.json'))['torch']['version']; \
    print('torch', torch.__version__, 'CUDA', torch.version.cuda); \
    sys.exit(0 if torch.__version__ == want else 'torch %s is not the %s bake_torch chose' % (torch.__version__, want))"

# Freeze the torch trio before anything else installs, exactly as
# bootstrap.torch_constraints_file() does and at the same path it writes to
# (COMFY_DIR.parent/torch-constraints.txt). ComfyUI's requirements.txt lists
# `torch` unpinned, so without this a ComfyUI release wanting a newer torch
# would upgrade it underneath torchvision/torchaudio and break the compiled
# extensions that link against it.
RUN python3 -c "\
import torch, torchvision, torchaudio; \
open('/opt/krea2/torch-constraints.txt', 'w').write(''.join( \
    '%s==%s\n' % (n, m.__version__.split('+')[0]) \
    for n, m in (('torch', torch), ('torchvision', torchvision), \
                 ('torchaudio', torchaudio))))" && \
    cat /opt/krea2/torch-constraints.txt

RUN python3 -m pip install --no-cache-dir \
        -r /opt/krea2/ComfyUI/requirements.txt \
        --constraint /opt/krea2/torch-constraints.txt

# Every baked pack's requirements, under the torch constraint. A pack that
# fails here fails the build — unlike at boot, where install_v2_nodes()
# logs it and leaves one tab broken. An image is built once and run by
# everyone, so the tradeoff points the other way.
RUN set -eu; \
    for reqs in /opt/krea2/ComfyUI/custom_nodes/*/requirements.txt; do \
        [ -e "$reqs" ] || continue; \
        echo ">>> $reqs"; \
        python3 -m pip install --no-cache-dir -r "$reqs" \
            --constraint /opt/krea2/torch-constraints.txt; \
    done

# SageAttention 2.2.0 for that torch, hash-pinned, from the file bake_torch
# wrote. Only installed here: there is no GPU at build time, so the kernel
# probe runs at boot in bootstrap.install_sageattention, which finds it
# present and only has to prove it runs before ComfyUI gets
# --use-sage-attention.
RUN python3 -m pip install --no-cache-dir --no-deps \
        -r /opt/krea2/sage-wheel.txt && \
    python3 -c "import importlib.metadata as m; \
        print('sageattention', m.version('sageattention'))"

# The node packs' compiled dependency: RES4LYF and the post-processing pack
# import cv2, and it can install cleanly and still fail to import on an ABI
# or numpy mismatch, which ComfyUI reports much later as a missing node
# rather than as a broken dependency. Importing it for real is the check —
# a metadata lookup would pass on exactly the wheels this catches.
RUN python3 -c "import cv2; \
    print('node dependencies import OK - opencv', cv2.__version__)"

# The start script, unmodified and byte-identical to the one published to
# R2 by build.sh. The entrypoint execs it; everything about how a build is
# fetched, verified and run stays in that one file.
COPY scripts/runpod_start.sh /opt/krea2/bin/start.sh
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /opt/krea2/bin/start.sh /entrypoint.sh

# Gradio. ComfyUI listens on 127.0.0.1 (comfy.py) and is not exposed.
EXPOSE 7860

CMD ["/entrypoint.sh"]
