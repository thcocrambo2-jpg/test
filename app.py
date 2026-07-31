"""Krea 2 on RunPod — ComfyUI + Gradio. Entry point: python app.py

Startup flow:
  1. Read configuration (config.py, imported below — also sets up logging).
  2. Take a license seat, or stop; resolve which tabs the key grants
     (features.py) from the entitlements it returned.
  3. Clone ComfyUI if it is missing.
  4. Install Python requirements (ComfyUI's + this app's, one resolver pass).
  5. Download the Hugging Face and CivitAI models the enabled features
     need — a feature that is off costs no disk and no download time.
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
import features
import licensing
from config import (
    COMFY_DIR,
    KREA_RESERVE_VRAM_GB,
    REACTOR_NODES_DIR,
    TEMP_DIR,
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

    # Which tabs this customer gets comes from the license and nothing
    # else, so it cannot be known any earlier than this line. Everything
    # below asks features.enabled() at call time rather than holding a
    # constant from import, which is what lets an answer that only exists
    # now reach modules that were imported before it.
    features.resolve(licensing.entitlements(), licensing.feature_labels())
    log.info("Features — %s", features.summary())

    # Where weights will come from. Worth saying out loud: when the mirror
    # cannot be reached every download quietly falls through to upstream,
    # so the app keeps working and nothing looks wrong until the day an
    # upstream file has actually vanished.
    import mirror
    from config import HF_TOKEN
    log.info("Assets — %s", mirror.describe())
    if mirror.MIRROR_ENABLED and not mirror.MIRROR_PUBLIC and not HF_TOKEN:
        log.warning(
            "HF_TOKEN is not set and the mirror repos are private, so every "
            "download will fall back to its original upstream source — the "
            "un-mirrored behaviour. Set HF_TOKEN to a token with read "
            "access to %s.", mirror.MIRROR_USER,
        )

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

    # A second instance is only worth its VRAM reservation when there is a
    # Video tab to serve — KREA2_WAN_PARALLEL on its own no longer buys one.
    wan_parallel = WAN_PARALLEL and features.enabled(features.Key.WAN_I2V)
    main_args = (
        ("--reserve-vram", str(KREA_RESERVE_VRAM_GB)) if wan_parallel else ()
    )
    comfy_process = comfy.start_comfyui(extra_args=main_args)
    comfy.wait_for_comfyui(comfy_process)
    if wan_parallel:
        wan_process = comfy.start_comfyui(
            port=WAN_COMFY_PORT, log_path=WAN_COMFY_LOG,
            extra_args=("--reserve-vram", str(WAN_RESERVE_VRAM_GB)),
        )
        comfy.wait_for_comfyui(wan_process, port=WAN_COMFY_PORT,
                               log_path=WAN_COMFY_LOG)

    # Custom nodes register at ComfyUI startup, and a failed import is only
    # reported in comfyui.log — surface it here instead of letting the
    # first face swap fail with a bare "node not found".
    if features.enabled(features.Key.FACESWAP):
        comfy.verify_custom_node(
            "ReActorFaceSwap", REACTOR_NODES_DIR,
            COMFY_DIR / "custom_nodes" / REACTOR_NODES_DIR,
        )
    if features.enabled(features.Key.KREA_V2_T2I):
        # Same reasoning for the Krea 2 V2 packs — two of these nodes have
        # no core equivalent, so a silent import failure would only show up
        # as "node not found" on the first generation.
        for dirname, _repo, class_type in V2_NODE_REPOS:
            comfy.verify_custom_node(
                class_type, dirname, COMFY_DIR / "custom_nodes" / dirname,
            )

    # 7 · Gradio UI (importing ui pulls in the workflow builder + API client).
    # ui builds its gr.Blocks at *import* time, so this import is where the
    # tabs are decided — it must stay below features.resolve() above, which
    # is the call that knows which ones this license grants.
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
