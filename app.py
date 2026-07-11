"""Krea 2 on RunPod — ComfyUI + Gradio. Entry point: python app.py

Startup flow:
  1. Read configuration (config.py, imported below — also sets up logging).
  2. Clone ComfyUI if it is missing.
  3. Install Python requirements (ComfyUI's + this app's, one resolver pass).
  4. Download Hugging Face and CivitAI models that are missing.
  5. Start the ComfyUI server and wait until its API answers.
  6. Launch the Gradio UI and keep running until interrupted.

Modules that need third-party packages (huggingface_hub, gradio,
websocket-client, ...) are imported only after step 3 has installed them,
so the app can bootstrap itself on a bare pod.
"""

import os
import shutil
import sys

import bootstrap
from config import TEMP_DIR, log


def main() -> None:
    # 2-3 · Clone ComfyUI (idempotent) and install all requirements.
    bootstrap.install_comfyui()
    bootstrap.install_custom_nodes()
    bootstrap.link_model_dirs()
    log.info("Environment ready (Python %s)", sys.version.split()[0])

    # 4 · Model + LoRA downloads (idempotent — only fetches what is missing).
    import downloads

    downloads.download_everything()
    log.info(
        "Downloads complete — %.1f GB free on %s",
        shutil.disk_usage(TEMP_DIR).free / 1e9, TEMP_DIR,
    )

    # 5 · GPU detection runs when comfy is imported; then start the server.
    import comfy

    comfy_process = comfy.start_comfyui()
    comfy.wait_for_comfyui(comfy_process)

    # 6 · Gradio UI (importing ui pulls in the workflow builder + API client).
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
