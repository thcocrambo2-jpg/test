"""Krea 2 on RunPod — ComfyUI + a React web app. Entry point: python app.py

Startup flow:
  1. Read configuration (settings.py), set up logging and make the
     output tree.
  2. Take a license seat, or stop; resolve which tabs the key grants
     (features.py) from the entitlements it returned; load the model and
     LoRA catalogue (catalog.py) — what each Krea tab offers — from the
     licence server.
  3. Clone ComfyUI if it is missing.
  4. Install Python requirements (ComfyUI's + this app's, one resolver pass).
  5. Download the Hugging Face and CivitAI models the enabled features
     need, including every model and LoRA in their catalogue lists — a
     feature that is off costs no disk and no download time.
  6. Start the ComfyUI server and wait until its API answers.
  7. Serve the React UI over uvicorn, open a Cloudflare tunnel for the
     public URL, and keep running until interrupted.

Modules that need third-party packages (huggingface_hub, fastapi,
uvicorn, websocket-client, ...) are imported only after step 3 has
installed them, so the app can bootstrap itself on a bare pod.
"""

import shutil
import sys

from ember.comfy import setup as bootstrap
from ember.licensing import catalog
from ember import features
from ember.licensing import seat as licensing
from ember import logs, settings
from ember.logs import log
from ember.settings import (
    COMFY_DIR,
    KREA_RESERVE_VRAM_GB,
    MODELS_DIR,
    TEMP_DIR,
    WAN_COMFY_LOG,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
    WAN_RESERVE_VRAM_GB,
)
from ember.pipelines.krea2_v2.constants import V2_NODE_REPOS
from ember.pipelines.minimax.constants import (
    MINIMAX_COMFYUI_MIN,
    MINIMAX_NODE,
)


def main() -> None:
    # 1 · Configuration. Nothing in ember/settings.py runs when it is
    # imported, so the logger, the output tree and the line that says where
    # both live are this function's first three statements — before
    # anything else can log or write.
    logs.setup()
    settings.ensure_dirs()
    settings.log_startup()

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

    # The catalogue: which models and LoRAs each Krea tab offers. It is the
    # download list as much as the dropdown list, so it has to be known
    # before step 5 — and it is frozen from here on, so the files fetched,
    # the choices offered and the V2 slot count can never disagree. Like
    # licensing it is stdlib-only, so it runs ahead of the pip install. A
    # server that does not answer degrades to the last saved copy, then to
    # an empty catalogue that only the Krea tabs notice.
    catalog.load(licensing.instance_id())

    # Where weights will come from. Worth saying out loud: when the mirror
    # cannot be reached every download quietly falls through to upstream,
    # so the app keeps working and nothing looks wrong until the day an
    # upstream file has actually vanished.
    from ember.weights import mirror
    from ember.settings import HF_TOKEN
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
    # Last of the installs, so nothing after it can move torch under the
    # build it picked for that torch, and before ComfyUI starts, which reads
    # the verdict through comfy.start_comfyui -> bootstrap.attention_args.
    bootstrap.install_sageattention()
    bootstrap.link_model_dirs()
    log.info("Environment ready (Python %s)", sys.version.split()[0])

    # 5 · Model + LoRA downloads (idempotent — only fetches what is missing).
    from ember.weights import downloads

    downloads.download_everything()
    log.info(
        "Downloads complete — %.1f GB free on %s",
        shutil.disk_usage(TEMP_DIR).free / 1e9, TEMP_DIR,
    )

    # 6 · GPU detection runs when comfy is imported; then start the server.
    # With KREA2_WAN_PARALLEL=1 a second ComfyUI instance serves video jobs
    # on its own port; each instance reserves VRAM for the other so they can
    # coexist on one GPU (defaults tuned for a 48 GB A40).
    from ember.comfy import server as comfy

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
    # reported in comfyui.log — surface it here instead of letting the first
    # generation fail with a bare "node not found".
    if (features.enabled(features.Key.KREA_V2_T2I)
            or features.enabled(features.Key.KREA_V2_EDIT)):
        # Two of the Krea 2 V2 packs' nodes have no core equivalent, so a
        # silent import failure would only show up as "node not found" on
        # the first generation. Krea 2 V2 Edit runs the same sampler and
        # variance nodes, so either feature is reason enough to check.
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
    # Where this import sits does not gate anything. The React client is
    # one bundle for every licence and learns what it may show from
    # /api/v1/session, over the wire, so import order cannot hide a tab.
    #
    # The gate is `api._mount_tabs()`, which hangs a `require_feature(key)`
    # dependency on every per-tab route, and `scripts/check_routes.py`,
    # which fails the build if any of them would answer a licence that
    # grants nothing. See docs/architecture/licensing-and-features.md,
    # "The licence gate on the API".
    from ember.web import serve

    # Counted from the catalogue rather than the loras/ folder: a file the
    # catalogue does not list is never offered, so the folder would count
    # things no tab can use. Each file once, however many tabs list it.
    lora_files = {
        lora.file
        for key in features.enabled_needing("catalog")
        for lora in catalog.feature_loras(key)
    }
    log.info(
        "Workflow builder ready — %d of %d catalogue LoRA file(s) on disk",
        sum((MODELS_DIR / "loras" / name).exists() for name in lora_files),
        len(lora_files),
    )
    if not settings.SKIP_LAUNCH:
        serve.serve()


if __name__ == "__main__":
    main()
