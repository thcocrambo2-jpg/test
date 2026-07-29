"""Krea 2 on RunPod — ComfyUI + Gradio. Entry point: python app.py

Startup flow:
  1. Read configuration (config.py, imported below — also sets up logging).
  2. Take a license seat, or stop.
  3. Clone ComfyUI if it is missing.
  4. Install Python requirements (ComfyUI's + this app's, one resolver pass).
  5. Download Hugging Face and CivitAI models that are missing.
  6. Start the ComfyUI server and wait until its API answers.
  7. Launch the Gradio UI and keep running until interrupted.

Modules that need third-party packages (huggingface_hub, gradio,
websocket-client, ...) are imported only after step 3 has installed them,
so the app can bootstrap itself on a bare pod.
"""

import os
import shutil
import sys

import bootstrap
import licensing
from config import (
    COMFY_DIR,
    KREA_RESERVE_VRAM_GB,
    REACTOR_ENABLED,
    REACTOR_NODES_DIR,
    TEMP_DIR,
    V2_ENABLED,
    V2_NODE_REPOS,
    WAN_COMFY_LOG,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
    WAN_RESERVE_VRAM_GB,
    log,
)


def main() -> None:
    # 2 · License seat. First, before anything expensive: a customer who
    # cannot take a seat finds out in seconds rather than after ~90 GB of
    # downloads. licensing uses only the stdlib, so it runs fine here —
    # ahead of the pip install that the modules below wait for.
    licensing.acquire_or_exit()

    # 3-4 · Clone ComfyUI (idempotent) and install all requirements.
    bootstrap.install_comfyui()
    bootstrap.install_custom_nodes()
    bootstrap.install_v2_nodes()
    bootstrap.install_reactor()
    bootstrap.link_model_dirs()
    log.info("Environment ready (Python %s)", sys.version.split()[0])

    # 5 · Model + LoRA downloads (idempotent — only fetches what is missing).
    import downloads

    downloads.download_everything()
    log.info(
        "Downloads complete — %.1f GB free on %s",
        shutil.disk_usage(TEMP_DIR).free / 1e9, TEMP_DIR,
    )

    # 6 · GPU detection runs when comfy is imported; then start the server.
    # With KREA2_WAN_PARALLEL=1 a second ComfyUI instance serves video jobs
    # on its own port; each instance reserves VRAM for the other so they can
    # coexist on one GPU (defaults tuned for a 48 GB A40).
    import comfy

    main_args = (
        ("--reserve-vram", str(KREA_RESERVE_VRAM_GB)) if WAN_PARALLEL else ()
    )
    comfy_process = comfy.start_comfyui(extra_args=main_args)
    comfy.wait_for_comfyui(comfy_process)
    if WAN_PARALLEL:
        wan_process = comfy.start_comfyui(
            port=WAN_COMFY_PORT, log_path=WAN_COMFY_LOG,
            extra_args=("--reserve-vram", str(WAN_RESERVE_VRAM_GB)),
        )
        comfy.wait_for_comfyui(wan_process, port=WAN_COMFY_PORT,
                               log_path=WAN_COMFY_LOG)

    # Custom nodes register at ComfyUI startup, and a failed import is only
    # reported in comfyui.log — surface it here instead of letting the
    # first face swap fail with a bare "node not found".
    if REACTOR_ENABLED:
        comfy.verify_custom_node(
            "ReActorFaceSwap", REACTOR_NODES_DIR,
            COMFY_DIR / "custom_nodes" / REACTOR_NODES_DIR,
        )
    if V2_ENABLED:
        # Same reasoning for the Krea 2 V2 packs — two of these nodes have
        # no core equivalent, so a silent import failure would only show up
        # as "node not found" on the first generation.
        for dirname, _repo, class_type in V2_NODE_REPOS:
            comfy.verify_custom_node(
                class_type, dirname, COMFY_DIR / "custom_nodes" / dirname,
            )

    # 7 · Gradio UI (importing ui pulls in the workflow builder + API client).
    import workflow
    from ui import launch_ui

    log.info(
        "Workflow builder ready — %d LoRA file(s) available",
        len(workflow.list_lora_files()),
    )
    if not os.environ.get("KREA2_SKIP_LAUNCH"):
        launch_ui()


if __name__ == "__main__":
    main()
