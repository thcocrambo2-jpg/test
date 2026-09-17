"""Write the image's PyTorch stack and SageAttention build, at build time.

Runs in the Dockerfile's first stage, after bake_nodes.py, and writes two
pip requirement files for the final stage to install:

    /opt/krea2/torch-stack.txt   the torch trio and its wheel index
    /opt/krea2/sage-wheel.txt    the SageAttention for that torch

and records both in baked.json, which the entrypoint prints on every boot.

The stack is fixed: exactly what MiniMax template v8
(hearmeman/comfyui-minimax-template:v8) runs — torch 2.11.0, torchvision
0.26.0, torchaudio 2.11.0, all +cu130, with SageAttention 2.2.0. One
configuration rather than a menu of them, so there is one image to test.
CUDA 13 needs an R580+ driver on the host; the RunPod template's CUDA
filter set to 13.0 is what guarantees one, as it does for the template.

A separate script from bake_nodes.py, and a separate RUN after it, so a
change here reuses the cached ComfyUI clone. The torchvision pairing and
the SageAttention come from bootstrap — TORCHVISION_FOR_TORCH and
sage_requirement — for the same reason bake_nodes takes its pins from
there: the image and the app must not disagree about which SageAttention
a torch gets.
"""

import json
import os
import sys
from pathlib import Path

BAKE_ROOT = os.environ.get("KREA2_BAKE_ROOT", "/opt/krea2")
os.environ["KREA2_BASE_DIR"] = BAKE_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap                                             # noqa: E402
from config import log                                       # noqa: E402

TORCH_VERSION = "2.11.0"
TORCH_CUDA = "cu130"
TORCH_INDEX = f"https://download.pytorch.org/whl/{TORCH_CUDA}"


def main() -> int:
    root = Path(BAKE_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    vision = bootstrap.TORCHVISION_FOR_TORCH[TORCH_VERSION]
    # The local version label (+cu130) is what makes pip replace the base
    # image's torch rather than call a same-numbered build satisfied.
    stack = [f"--index-url {TORCH_INDEX}",
             f"torch=={TORCH_VERSION}+{TORCH_CUDA}",
             f"torchvision=={vision}+{TORCH_CUDA}",
             f"torchaudio=={TORCH_VERSION}+{TORCH_CUDA}"]
    info = {"torch": f"{TORCH_VERSION}+{TORCH_CUDA}", "cuda": "13.0"}
    sage = bootstrap.sage_requirement(info)
    if sage is None:
        log.error("bootstrap.sage_requirement has no SageAttention for %s — "
                  "the image and the app disagree", info["torch"])
        return 1

    (root / "torch-stack.txt").write_text(
        "".join(line + "\n" for line in stack), encoding="utf-8")
    (root / "sage-wheel.txt").write_text(sage[1] + "\n", encoding="utf-8")

    manifest = root / "baked.json"
    baked = (json.loads(manifest.read_text(encoding="utf-8"))
             if manifest.exists() else {})
    baked["torch"] = {"version": info["torch"], "cuda": info["cuda"],
                      "sageattention": sage[0]}
    manifest.write_text(json.dumps(baked, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    log.info("Image torch: %s (CUDA %s); SageAttention: %s",
             info["torch"], info["cuda"], sage[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
