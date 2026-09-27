"""Throwaway POC: run the Hearmeman24/comfyui-qwen-template workflows locally.

    python app.py                 # install everything, download all models, start ComfyUI
    python app.py --no-download   # skip model downloads
    python app.py --port 8190

First run clones ComfyUI + custom nodes into ./ComfyUI and downloads every model
the workflows reference (~230 GB). Later runs skip what exists.
Workflows appear in ComfyUI's Workflows sidebar (press W), grouped by model.
Set HF_TOKEN if a Hugging Face download returns 401.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMFY = HERE / "ComfyUI"
WORKFLOWS = HERE / "workflows"
REGISTRY = HERE / "models_registry.json"

CUSTOM_NODES = [
    "https://github.com/ltdrdata/ComfyUI-Manager",
    "https://github.com/kijai/ComfyUI-KJNodes",
    "https://github.com/cubiq/ComfyUI_essentials",
    "https://github.com/JPS-GER/ComfyUI_JPS-Nodes",
    "https://github.com/rgthree/rgthree-comfy",
    "https://github.com/M1kep/ComfyLiterals",
    "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes",
    "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
    "https://github.com/ssitu/ComfyUI_UltimateSDUpscale",
    "https://github.com/ClownsharkBatwing/RES4LYF",
    "https://github.com/spacepxl/ComfyUI-VAE-Utils",
    "https://github.com/Hearmeman24/ComfyUI-QwenImageRefPack",
]

# Not named in any workflow JSON, but the template downloads them alongside.
EXTRA_MODELS = [
    "Wan2.1_VAE_upscale2x_imageonly_real_v1.safetensors",
    "krea2_identity_edit_v1_2_r128.safetensors",
]

TORCH_INDEX = "https://download.pytorch.org/whl/cu128"


def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def pip(*args):
    run([sys.executable, "-m", "pip", "install", *args])


def pip_node_requirements(req):
    """A custom node's broken requirement shouldn't stop the whole setup.

    Some packages (e.g. pixeloe) carry dependency markers that crash pip on
    kernel versions like '6.8.0-101-generic', so fall back to one package at a
    time, then --no-deps, and just warn about anything that still fails.
    """
    try:
        pip("-r", req)
        return
    except subprocess.CalledProcessError:
        print(f"! {req} failed as a whole, installing its packages one by one", flush=True)
    for line in req.read_text().splitlines():
        pkg = line.split("#")[0].strip()
        if not pkg or pkg.startswith("-"):
            continue
        try:
            pip(pkg)
        except subprocess.CalledProcessError:
            try:
                pip("--no-deps", pkg)
            except subprocess.CalledProcessError:
                print(f"! could not install {pkg} (from {req.parent.name}), continuing", flush=True)


def install():
    if not COMFY.exists():
        run(["git", "clone", "--depth", "1", "https://github.com/comfyanonymous/ComfyUI", COMFY])
    try:
        import torch  # noqa: F401
    except ImportError:
        pip("torch", "torchvision", "torchaudio", "--index-url", TORCH_INDEX)
    pip("-r", COMFY / "requirements.txt")

    nodes = COMFY / "custom_nodes"
    for url in CUSTOM_NODES:
        dest = nodes / url.rstrip("/").split("/")[-1]
        if not dest.exists():
            run(["git", "clone", "--depth", "1", "--recursive", url, dest])
        req = dest / "requirements.txt"
        if req.exists():
            pip_node_requirements(req)

    # Workflows show up in the ComfyUI sidebar from user/default/workflows.
    shutil.copytree(WORKFLOWS, COMFY / "user" / "default" / "workflows", dirs_exist_ok=True)


def needed_models():
    registry = json.loads(REGISTRY.read_text())
    text = "".join(p.read_text(encoding="utf-8") for p in WORKFLOWS.rglob("*.json"))
    names = [n for n in registry if f'"{n}"' in text] + EXTRA_MODELS
    return {n: registry[n] for n in dict.fromkeys(names)}


def download(name, entry):
    dest = COMFY / "models" / entry["subdir"] / name
    if dest.exists():
        return f"have  {name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(name + ".part")
    have = part.stat().st_size if part.exists() else 0

    req = urllib.request.Request(entry["url"], headers={"User-Agent": "poc"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    token = os.environ.get("HF_TOKEN")
    if token and "huggingface.co" in entry["url"]:
        req.add_unredirected_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req) as r:
        if have and r.status != 206:
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        done, next_pct = have, 0
        with open(part, "ab" if have else "wb") as f:
            while chunk := r.read(8 << 20):
                f.write(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else 0
                if pct >= next_pct:
                    print(f"  {name}: {pct}% of {total / 1e9:.1f} GB", flush=True)
                    next_pct = pct + 10
    part.replace(dest)
    return f"got   {name}"


def download_all():
    models = needed_models()
    print(f"\n{len(models)} models needed", flush=True)
    failed = []
    with ThreadPoolExecutor(4) as pool:
        futures = {pool.submit(download, n, e): n for n, e in models.items()}
        for fut, name in futures.items():
            try:
                print(fut.result(), flush=True)
            except Exception as e:
                failed.append(name)
                print(f"FAIL  {name}: {e}", flush=True)
    if failed:
        print(f"\n{len(failed)} downloads failed (re-run to resume): {', '.join(failed)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--listen", default="127.0.0.1", help="0.0.0.0 to reach it from another machine")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--no-install", action="store_true")
    args = ap.parse_args()

    if not args.no_install:
        install()
    if not args.no_download:
        download_all()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\nStarting ComfyUI at {url} - open the Workflows sidebar (W) to pick one.\n", flush=True)
    subprocess.run([sys.executable, "main.py", "--listen", args.listen, "--port", str(args.port), "--auto-launch"], cwd=COMFY)


if __name__ == "__main__":
    main()
