"""ReActor face-swap workflow builder (ComfyUI API format).

Face swapping is not a diffusion job: ReActor detects the face in a
finished image, takes the identity embedding of a reference face and
pastes a re-rendered face back in with an ONNX model (inswapper_128).
No UNet, text encoder or VAE is loaded, so this graph never touches the
Krea 2 stack — it only shares the ComfyUI queue.

The graph is deliberately three nodes: LoadImage ×2 → ReActorFaceSwap →
SaveImage on the SWAPPED_IMAGE output, so exactly one file is written and
it is the finished swap. ReActor returns the input image with only the
face region rewritten, so the output keeps the base image's resolution —
nothing here resizes, and the UI uploads both images untouched.

Everything the node needs is pre-downloaded by downloads.py (swap model,
buffalo_l analysis pack, CodeFormer, the RetinaFace/parsing weights and
the SFW classifier), so a swap makes no network calls.
"""

from config import (
    COMFY_DIR,
    MODELS_DIR,
    REACTOR_DEFAULT_DETECTOR,
    REACTOR_INSIGHTFACE_PACK,
    REACTOR_NODES_DIR,
    REACTOR_RESTORE_MODEL,
    REACTOR_SWAP_MODEL,
)

# ReActor lists "none" first in its own face_restore_model dropdown; the
# tab reuses the same convention so restoration stays optional.
NO_RESTORE = "none"


# ── Availability ──────────────────────────────────────────────────────────────

def reactor_nodes_installed() -> bool:
    """True once the ComfyUI-ReActor pack has been cloned."""
    return (COMFY_DIR / "custom_nodes" / REACTOR_NODES_DIR / "nodes.py").exists()


def list_swap_models() -> list[str]:
    """Swap models ReActor can see (models/insightface/*.onnx)."""
    folder = MODELS_DIR / "insightface"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob("*.onnx"))


def list_restore_models() -> list[str]:
    """Face-restoration models available, "none" first (as ReActor lists them)."""
    folder = MODELS_DIR / "facerestore_models"
    found = (sorted(p.name for p in folder.iterdir()
                    if p.suffix in (".pth", ".onnx"))
             if folder.is_dir() else [])
    return [NO_RESTORE, *found]


def default_swap_model() -> str:
    """The configured swap model when present, else whatever downloaded."""
    available = list_swap_models()
    if REACTOR_SWAP_MODEL in available:
        return REACTOR_SWAP_MODEL
    return available[0] if available else REACTOR_SWAP_MODEL


def default_restore_model() -> str:
    """CodeFormer when it downloaded, otherwise restoration stays off."""
    available = list_restore_models()
    return (REACTOR_RESTORE_MODEL if REACTOR_RESTORE_MODEL in available
            else NO_RESTORE)


def insightface_pack_available() -> bool:
    """True once the buffalo_l analysis pack is unpacked where ReActor looks."""
    pack = MODELS_DIR / "insightface" / "models" / REACTOR_INSIGHTFACE_PACK
    return (pack / "det_10g.onnx").exists()


def reactor_status() -> tuple[bool, str]:
    """(ready, message) — why the tab cannot run, in the order worth fixing.

    Keeps the "a missing piece disables one tab, never the app" contract:
    the UI calls this both to warn up front and to refuse a swap.
    """
    if not reactor_nodes_installed():
        return False, (
            "❌ The ComfyUI-ReActor nodes are not installed — restart the app "
            "so the bootstrap step can copy them from deps/ into "
            "custom_nodes (check the log if it does not)."
        )
    if not list_swap_models():
        return False, (
            f"❌ The swap model ({REACTOR_SWAP_MODEL}) is not downloaded yet "
            "— restart the app so the download step can fetch it."
        )
    if not insightface_pack_available():
        return False, (
            f"❌ The InsightFace {REACTOR_INSIGHTFACE_PACK} pack is missing — "
            "restart the app so the download step can fetch and unpack it."
        )
    return True, ""


# ── Workflow ──────────────────────────────────────────────────────────────────

def build_faceswap_workflow(
    *,
    base_image_name: str,
    face_image_name: str,
    swap_model: str | None = None,
    facedetection: str = REACTOR_DEFAULT_DETECTOR,
    face_restore_model: str = NO_RESTORE,
    face_restore_visibility: float = 1.0,
    codeformer_weight: float = 0.5,
    input_faces_index: str = "0",
    source_faces_index: str = "0",
    filename_prefix: str = "Krea2FaceSwap",
) -> dict:
    """Build a ReActor face-swap workflow in ComfyUI API format.

    `base_image_name` (the image whose face is replaced) and
    `face_image_name` (the reference face) both reference files already
    uploaded to ComfyUI's input folder via client.upload_image.

    The two index strings select which detected face to use when an image
    holds several — ReActor accepts "0", "0,1" or "0-2", counted left to
    right. `face_restore_model` of "none" skips restoration entirely;
    `codeformer_weight` only applies to the CodeFormer restorer.
    """
    return {
        "base": {
            "class_type": "LoadImage",
            "inputs": {"image": base_image_name},
        },
        "face": {
            "class_type": "LoadImage",
            "inputs": {"image": face_image_name},
        },
        "swap": {
            "class_type": "ReActorFaceSwap",
            "inputs": {
                "enabled": True,
                "input_image": ["base", 0],
                "source_image": ["face", 0],
                "swap_model": swap_model or default_swap_model(),
                "facedetection": facedetection,
                "face_restore_model": face_restore_model,
                "face_restore_visibility": float(face_restore_visibility),
                "codeformer_weight": float(codeformer_weight),
                "detect_gender_input": "no",
                "detect_gender_source": "no",
                "input_faces_index": str(input_faces_index),
                "source_faces_index": str(source_faces_index),
                "console_log_level": 1,
            },
        },
        # Output 0 is SWAPPED_IMAGE (1 is FACE_MODEL, 2 is ORIGINAL_IMAGE),
        # so only the finished swap is ever written to disk.
        "save": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix,
                       "images": ["swap", 0]},
        },
    }
