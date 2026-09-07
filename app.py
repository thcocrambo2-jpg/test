"""Krea 2 on RunPod — ComfyUI + a React web app. Entry point: python app.py

Startup flow:
  1. Read configuration (config.py, imported below — also sets up logging).
  2. Take a license seat, or stop; resolve which tabs the key grants
     (features.py) from the entitlements it returned.
  3. Clone ComfyUI if it is missing.
  4. Install Python requirements (ComfyUI's + this app's, one resolver pass).
  5. Download the Hugging Face and CivitAI models the enabled features
     need — a feature that is off costs no disk and no download time.
  6. Start the ComfyUI server and wait until its API answers.
  7. Serve the React UI over uvicorn, open a Cloudflare tunnel for the
     public URL, and keep running until interrupted.

Modules that need third-party packages (huggingface_hub, fastapi,
uvicorn, websocket-client, ...) are imported only after step 3 has
installed them, so the app can bootstrap itself on a bare pod.
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
    MINIMAX_COMFYUI_MIN,
    MINIMAX_NODE,
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
    # PyTorch first, and before install_comfyui specifically: ComfyUI's own
    # requirements.txt lists `torch` unpinned, so on a machine without one
    # that pip pass would pull whatever PyPI defaults to — which on a
    # Blackwell card is a torch with no kernels for it. On a pod this is a
    # no-op; the base image already satisfies it.
    bootstrap.ensure_torch()
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
    if (features.enabled(features.Key.KREA_V2_T2I)
            or features.enabled(features.Key.KREA_V2_EDIT)):
        # Same reasoning for the Krea 2 V2 packs — two of these nodes have
        # no core equivalent, so a silent import failure would only show up
        # as "node not found" on the first generation. Krea 2 V2 Edit runs
        # the same sampler and variance nodes, so either feature is reason
        # enough to check.
        for dirname, _repo, class_type in V2_NODE_REPOS:
            comfy.verify_custom_node(
                class_type, dirname, COMFY_DIR / "custom_nodes" / dirname,
            )
    if (features.enabled(features.Key.MINIMAX_I2V)
            or features.enabled(features.Key.MINIMAX_T2V)):
        # The MiniMax nodes are core ComfyUI rather than a pack, so the
        # failure to catch is a checkout older than the release that
        # carries them — which is what an existing volume has until
        # bootstrap.repin_checkout has moved it to the pin.
        comfy.verify_core_node(MINIMAX_NODE, MINIMAX_COMFYUI_MIN)
    if (features.enabled(features.Key.KREA_EDIT)
            or features.enabled(features.Key.KREA_V2_EDIT)):
        # Krea2Edit is cloned rather than vendored, so it fails the same
        # way and deserves the same check. One class proves the pack
        # loaded; the other lives in the same module.
        comfy.verify_custom_node(
            "Krea2EditModelPatch", "comfyui-krea2edit",
            COMFY_DIR / "custom_nodes" / "comfyui-krea2edit",
        )

    # 7 · The web app: uvicorn, the React bundle, and the tunnel that gives
    # it a public URL.
    #
    # This import used to be load-bearing. `ui` built its gr.Blocks at
    # *import* time and skipped any tab the licence did not grant, so where
    # this line sat — below features.resolve() — was the licence gate: an
    # unbuilt tab had no endpoint. Nothing about that survives here. The
    # React client is one bundle for every licence and learns what it may
    # show from /api/v1/session, over the wire, so a gate made of import
    # order would gate nothing at all.
    #
    # What replaces it is `api._mount_tab`, which hangs a features.enabled()
    # dependency on every per-tab route, and `scripts/check_routes.py`, which
    # fails the build if any of them would answer a licence that grants
    # nothing. See context.md §4.5.
    import serve
    import workflow

    log.info(
        "Workflow builder ready — %d LoRA file(s) available",
        len(workflow.list_lora_files()),
    )
    if not os.environ.get("KREA2_SKIP_LAUNCH"):
        serve.serve()


if __name__ == "__main__":
    main()
