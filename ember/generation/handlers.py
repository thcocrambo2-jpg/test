"""What every tab's generator has in common, and the pipelines do not own.

The feature keys the catalogue files each tab's models and LoRAs under,
the model guard every Krea generator opens with, the preset tickbox they
all honour, the Krea 2 resolution parser, and the zip of everything this
pod has rendered. The generators themselves are in
ember/pipelines/<name>/handler.py, beside the workflow.py each one
builds through, and what they share with each other is in
ember.generation.runner and ember.generation.loras.

zip_outputs() returns `(path_or_None, message)` rather than raising, so a
caller with nothing to zip has a sentence to show.
"""

import re
import time
import zipfile
from pathlib import Path

from ember.web import gallery_index
from ember.generation import queue as jobqueue
from ember.licensing import presets
from ember.features import Key
from ember.logs import log
from ember.settings import OUTPUT_DIR
from ember.pipelines.common import (
    model_file_available,
    resolve_model,
)
from ember.pipelines.krea2.constants import (
    DEFAULT_RESOLUTION,
    RESOLUTION_PRESETS,
)

# The four tabs whose models and LoRAs come from the catalogue, by the
# feature key the catalogue files their lists under. Each generator
# reads its own, so Krea2 and Krea2 Edit (and the two V2 tabs) can offer
# different lists without a second code path.
KREA_T2I = str(Key.KREA_T2I)
KREA_EDIT = str(Key.KREA_EDIT)
KREA_V2_T2I = str(Key.KREA_V2_T2I)
KREA_V2_EDIT = str(Key.KREA_V2_EDIT)
# The MiniMax tabs take LoRAs from the catalogue too, but no model: their
# weights are fixed in pipelines/minimax/constants.py, so only the LoRA
# list is read.
MINIMAX_I2V = str(Key.MINIMAX_I2V)
MINIMAX_T2V = str(Key.MINIMAX_T2V)


def _snap(value, lo: int = 512, hi: int = 2048) -> int:
    """Clamp to [lo, hi] and round to a multiple of 16 (Krea 2 requirement)."""
    return max(lo, min(hi, int(round(int(value) / 16)) * 16))


def parse_resolution(value) -> tuple:
    """Accept a preset label or free-form 'WxH' / 'W×H' text."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return _snap(value[0]), _snap(value[1])
    text = str(value or "").strip()
    if text in RESOLUTION_PRESETS:
        return RESOLUTION_PRESETS[text]
    match = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)
    if match:
        return _snap(match.group(1)), _snap(match.group(2))
    compact = text.lower().replace(" ", "")
    for label, wh in RESOLUTION_PRESETS.items():
        if compact and compact.split("(")[0] in label.lower().replace(" ", ""):
            return wh
    return RESOLUTION_PRESETS[DEFAULT_RESOLUTION]


def _check_model(feature, model_id):
    """Resolve the dropdown value; return (model, error_message_or_None)."""
    model = resolve_model(feature, model_id)
    if model is None:
        return None, ("❌ The model catalogue lists no model for this tab — "
                      "restart the app once the licence server can be "
                      "reached.")
    if not model_file_available(model):
        return model, (f"❌ Model “{model.name}” is not downloaded yet — "
                       "restart the app so the download step can fetch it.")
    return model, None


def _save_preset(tab, save, name, settings) -> str:
    """Save these settings as a preset if asked to; return a status prefix.

    The counterpart of `publish` for the settings half: one tickbox, read
    on the click that generates, admin-only and enforced on the server
    against the licence document.

    Every tick is a **new** preset. Loading one, adjusting it and saving is
    the ordinary gesture, and it must not destroy the preset it started
    from — so the server steps a repeated name to "… (2)" rather than
    overwriting, and an empty name is stamped with the time rather than
    refused. See presets.save; both decisions are why the message below
    names the preset that was actually written.

    It reports, where publishing deliberately does not. Publishing is
    invisible by design — the customer is never told prompts are saved, so
    nothing about it may appear in the UI — but a preset is a thing an
    admin is *waiting on*, and "did it save?" is a fair question. The
    answer rides on the status box as a first line, so it costs no
    component and is gone on the next run.

    Never raises and never blocks the generation: presets.save returns
    (ok, message) for everything that can go wrong, including a licence
    the server refuses.

    It also tells the job queue, because this runs on a worker thread now
    rather than on the click that asked for it — so the dropdown listing
    the presets has no other way to learn that what it is showing just
    went stale. See jobqueue.note_preset_saved and _queue_tick.
    """
    if not save:
        return ""
    ok, message = presets.save(tab, name, settings)
    (log.info if ok else log.warning)("Preset: %s", message)
    if ok:
        jobqueue.note_preset_saved(tab)
    return ("✅ " if ok else "⚠️ ") + message + "\n"


def list_output_images() -> list[str]:
    """Every generated image and video in OUTPUT_DIR, newest first."""
    return gallery_index.list_media()
def zip_outputs():
    """Bundle all generated media into one zip (the pod disk is ephemeral).

    Returns (path_or_None, message). The path is None when there is
    nothing to zip, which is what the caller renders as "hide the
    download panel" rather than showing an empty drop zone on every visit
    for the sake of the one that asks for a zip.
    """
    images = list_output_images()
    if not images:
        return None, "No images to zip yet."
    # A fresh name per zip, and the previous one deleted. OUTPUT_DIR is
    # served static (see the set_static_paths call further down), which
    # assumes a path's contents never change — reusing "all_outputs.zip"
    # would let a browser hand the user the *previous* zip from its cache.
    # The bare "all_outputs.zip" in the glob is the pre-timestamp name, so a
    # pod that has been running since before this change loses its copy too
    # rather than leaving a stale several-GB file on an ephemeral disk.
    for stale in OUTPUT_DIR.glob("all_outputs*.zip"):
        stale.unlink(missing_ok=True)
    # PNGs/MP4s are already compressed — store instead of deflating (faster).
    zip_path = OUTPUT_DIR / f"all_outputs_{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for img in images:
            zf.write(img, Path(img).relative_to(OUTPUT_DIR))
    size_mb = zip_path.stat().st_size / 1e6
    return (str(zip_path),
            f"📦 Zipped {len(images)} file(s) ({size_mb:.0f} MB)")
