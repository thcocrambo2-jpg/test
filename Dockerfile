# Krea 2 — the app's environment, pre-built.
#
# What this image is: everything a pod spends its first several minutes
# doing, done once at build time. The ComfyUI checkout at its pinned SHA,
# the custom node packs, ComfyUI's Python requirements, ReActor's heavy
# dependency set, a working onnxruntime.
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

# The OS, Python 3.12 and system libraries the app is tested on. Its torch
# (2.8.0+cu128) is not the one that runs: the final stage installs MiniMax
# template v8's torch 2.11.0+cu130 over it (docker/bake_torch.py), because
# RunPod publishes no torch 2.11 image. Overriding this is choosing a
# configuration nobody has run the app on; the assertions in the final
# stage are the floor, not permission.
#
# CUDA 13 needs an R580+ driver on the host. On RunPod, set the template's
# CUDA version filter to 13.0, as template v8 does.
ARG BASE_IMAGE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404


# ── Stage 1: clone ────────────────────────────────────────────────────────
# Separate purely so the app's source does not reach the published image.
# bake_nodes.py needs config.py/bootstrap.py/mirror.py to resolve the pins,
# and those are exactly the files build.sh compiles into a binary rather
# than shipping. They stay in this stage; only /opt/krea2 is copied out.
FROM ${BASE_IMAGE} AS nodes

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1

# git is what clone_pinned shells out to, and the apt-get that guarantees it
# is in the final stage, which this one does not inherit.
RUN command -v git >/dev/null 2>&1 || ( \
        apt-get update && \
        apt-get install -y --no-install-recommends git ca-certificates && \
        apt-get clean && rm -rf /var/lib/apt/lists/* )

# huggingface_hub for the mirror tarball path in bootstrap.node_pack_from_mirror;
# hf_xet because without it a Xet-backed download falls back to single-stream
# HTTP, which is the difference between seconds and many minutes.
RUN python3 -m pip install --no-cache-dir huggingface_hub hf_xet

WORKDIR /src
# scripts/ first: PINS.json and mirror_manifest.json change far more often
# than the four modules, and mirror.py looks in PROJECT_DIR/scripts for both.
COPY scripts/PINS.json scripts/mirror_manifest.json /src/scripts/
COPY config.py features.py mirror.py bootstrap.py /src/
COPY docker/bake_nodes.py /src/bake_nodes.py

RUN python3 /src/bake_nodes.py

# After the clone rather than inside it, so a change to the torch pins
# reuses the cached ComfyUI and node packs.
COPY docker/bake_torch.py /src/bake_torch.py
RUN python3 /src/bake_torch.py


# ── Stage 2: the image ────────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS final

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1 \
    HF_XET_HIGH_PERFORMANCE=1 \
    KREA2_BASE_DIR=/workspace/krea2

# `python3` on PATH is not a detail here: compiled with Nuitka the app's
# sys.executable is the binary itself, so bootstrap.runtime_python() falls
# back to `shutil.which("python3")` for every pip call and for launching
# ComfyUI. Whatever that resolves to IS the app's interpreter, so every
# install below goes through `python3 -m pip` rather than bare `pip` —
# installing into a different interpreter than the app uses would produce
# an image that looks complete and behaves like an empty one.
#
# Failing here rather than at boot is the point: if a future base image
# hides torch from `python3`, this line says so in the build log.
RUN python3 -c "import torch, sys; \
    print('torch', torch.__version__, 'CUDA', torch.version.cuda); \
    sys.exit(0 if torch.version.cuda else 'base image has no CUDA torch')"

# What runpod_start.sh checks for (curl, coreutils, python3, git) plus the
# two libraries opencv needs. All of these are already in the base image;
# they are named anyway so a base image change cannot quietly remove one
# and turn it into an apt-get on a customer's first boot.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl coreutils git ca-certificates \
        ffmpeg libgl1 libglib2.0-0 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

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

# ReActor is vendored rather than cloned, and copied rather than symlinked,
# for the reason install_reactor() gives: the '../../models/...' paths
# inside r_facelib resolve against the ComfyUI install, not against wherever
# the pack came from.
COPY deps/ComfyUI-ReActor /opt/krea2/ComfyUI/custom_nodes/ComfyUI-ReActor

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

# onnxruntime last, and uninstall-then-install, because several packs list
# plain `onnxruntime` (CPU) in their requirements and both packages provide
# the same `onnxruntime` module — last install wins. PyPI's current
# onnxruntime-gpu, because it links CUDA 13 like the torch above; it is
# also what template v8 installs. (bootstrap.py's ONNXRUNTIME_CUDA12_* pin
# is for the CUDA 12 torch of a plain pod, not for this image.)
RUN python3 -m pip uninstall -y -q onnxruntime onnxruntime-gpu || true; \
    python3 -m pip install --no-cache-dir onnxruntime-gpu

# SageAttention 2.2.0 for that torch, hash-pinned, from the file bake_torch
# wrote. Only installed here: there is no GPU at build time, so the kernel
# probe runs at boot in bootstrap.install_sageattention, which finds it
# present and only has to prove it runs before ComfyUI gets
# --use-sage-attention.
RUN python3 -m pip install --no-cache-dir --no-deps \
        -r /opt/krea2/sage-wheel.txt && \
    python3 -c "import importlib.metadata as m; \
        print('sageattention', m.version('sageattention'))"

# The same modules install_reactor() verifies after installing them, for
# the same reason: these can install cleanly and still fail to import on an
# ABI or numpy mismatch, and ComfyUI reports that much later as a missing
# node rather than as a broken dependency. Importing them for real is the
# check — a metadata lookup would pass on exactly the wheels this catches.
# onnxruntime is in the list so a CUDA-mismatched build cannot reach a
# customer.
RUN python3 -c "import cv2, onnx, onnxruntime, albumentations, \
        segment_anything, ultralytics; \
    print('node dependencies import OK'); \
    print('onnxruntime providers:', onnxruntime.get_available_providers())"

# The start script, unmodified and byte-identical to the one published to
# R2 by build.sh. The entrypoint execs it; everything about how a build is
# fetched, verified and run stays in that one file.
COPY scripts/runpod_start.sh /opt/krea2/bin/start.sh
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /opt/krea2/bin/start.sh /entrypoint.sh

# Gradio. ComfyUI listens on 127.0.0.1 (comfy.py) and is not exposed.
EXPOSE 7860

CMD ["/entrypoint.sh"]
