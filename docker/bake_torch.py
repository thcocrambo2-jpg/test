"""Decide the image's PyTorch stack and SageAttention build, at build time.

Runs in the Dockerfile's first stage, after bake_nodes.py, and writes two
pip requirement files for the final stage to install:

    /opt/krea2/torch-stack.txt   the torch trio and its index, or nothing
    /opt/krea2/sage-wheel.txt    the SageAttention for that torch, or nothing

and records both in baked.json, which the entrypoint prints on every boot.

Driven by two build args (docker build --build-arg, or `make image
TORCH_VERSION=... TORCH_CUDA=...`):

    TORCH_VERSION   2.8.0 or 2.11.0 — any torch bootstrap.TORCHVISION_FOR_TORCH
                    knows the matching torchvision for
    TORCH_CUDA      the wheel index's CUDA build: cu128, cu130, ...

Both empty (the default) keeps the base image's own torch, which is the
image as it was before these args existed. One without the other is an
error rather than a guess.

A separate script from bake_nodes.py, and a separate RUN after it, so that
building another torch variant reuses the cached ComfyUI clone instead of
fetching it again. The choices come from bootstrap — TORCHVISION_FOR_TORCH
and sage_requirement — for the same reason bake_nodes takes pins from
there: an image and a pod booting without it must not disagree about which
SageAttention a torch gets.
"""

import json
import os
import re
import sys
from pathlib import Path

BAKE_ROOT = os.environ.get("KREA2_BAKE_ROOT", "/opt/krea2")
os.environ["KREA2_BASE_DIR"] = BAKE_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap                                             # noqa: E402
from config import log                                       # noqa: E402

TORCH_INDEX_BASE = "https://download.pytorch.org/whl/"


def main() -> int:
    version = os.environ.get("KREA2_TORCH_VERSION", "").strip()
    cuda = os.environ.get("KREA2_TORCH_CUDA", "").strip()
    root = Path(BAKE_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    if version or cuda:
        if not (version and cuda):
            log.error("TORCH_VERSION and TORCH_CUDA are set together or not "
                      "at all (got TORCH_VERSION=%r, TORCH_CUDA=%r)",
                      version, cuda)
            return 1
        vision = bootstrap.TORCHVISION_FOR_TORCH.get(version)
        if vision is None:
            log.error("No known torchvision for torch %s — known: %s. Add "
                      "the pair to bootstrap.TORCHVISION_FOR_TORCH first.",
                      version, ", ".join(bootstrap.TORCHVISION_FOR_TORCH))
            return 1
        match = re.fullmatch(r"cu(\d\d)(\d)", cuda)
        if match is None:
            log.error("TORCH_CUDA must look like cu128 or cu130, got %r", cuda)
            return 1
        # The local version label (+cu128) is what makes pip replace the
        # base image's torch when only the CUDA build differs.
        stack = [f"--index-url {TORCH_INDEX_BASE}{cuda}",
                 f"torch=={version}+{cuda}",
                 f"torchvision=={vision}+{cuda}",
                 f"torchaudio=={version}+{cuda}"]
        info = {"torch": f"{version}+{cuda}",
                "cuda": f"{match.group(1)}.{match.group(2)}"}
        pinned = True
    else:
        stack = []
        # The base image's torch, asked of the interpreter the final stage
        # shares. No GPU at build time, so only the version fields answer.
        info = bootstrap._probe_torch()
        if not info.get("torch"):
            log.error("The base image has no importable torch: %s",
                      info.get("error", "module not found"))
            return 1
        pinned = False

    sage = bootstrap.sage_requirement(info)
    (root / "torch-stack.txt").write_text(
        "".join(line + "\n" for line in stack), encoding="utf-8")
    (root / "sage-wheel.txt").write_text(
        (sage[1] + "\n") if sage else "", encoding="utf-8")

    manifest = root / "baked.json"
    baked = (json.loads(manifest.read_text(encoding="utf-8"))
             if manifest.exists() else {})
    baked["torch"] = {
        "version": info["torch"],
        "cuda": info.get("cuda"),
        "pinned_at_build": pinned,
        "sageattention": sage[0] if sage else None,
    }
    manifest.write_text(json.dumps(baked, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    log.info("Image torch: %s (CUDA %s, %s); SageAttention: %s",
             info["torch"], info.get("cuda"),
             "installed at build" if pinned else "the base image's own",
             sage[0] if sage else "none for this torch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
