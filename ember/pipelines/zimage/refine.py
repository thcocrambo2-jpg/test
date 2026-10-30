"""The ⚡ Ember Lite refine: Z-Image Turbo over another tab's results.

The image tabs' refine section (`_refine_fields` in web/schema/fields.py)
is three controls: the tick, the denoise and an optional prompt. A handler
hands the three to refine_spec(), and when the tick is on it passes the
Refine it gets back to _run_jobs. That runs the batch as it always has,
then every picture once more through build_zimage_workflow, as the start
image at the form's denoise: the same image to image the Ember Lite tab
runs, with the img2prompt sweep's settings (constants.py, "The ⚡ Ember
Lite refine").

After the whole batch rather than after each picture, so a batch is one
model swap to Z-Image and not one per picture — the two models never sit
in VRAM together, which is what _release_on_swap is for. Both files are
saved and each is its own Gallery entry: the refined one is named after
the picture it came from, plus `_refined`, and its recipe has the tick on,
while the original's has it off, so loading either makes that picture
again.

Gated by the zimage_refine feature, not by the Ember Lite tab's, so a key
can have the one without the other; the model is that feature's own
catalogue list. The Refine is duck-typed by the runner — `job`, `builder`,
`recipe_label` — so ember.generation.runner never imports this module,
which imports it.
"""

import uuid
from dataclasses import dataclass

from PIL import Image

from ember import features
from ember.comfy.client import client
from ember.generation.handlers import ZIMAGE_REFINE, _check_model
from ember.generation.runner import _png_bytes
from ember.pipelines.zimage.constants import (
    REFINE_CFG,
    REFINE_EDIT_PROMPT,
    REFINE_FIELD_LABEL,
    REFINE_SAMPLER,
    REFINE_STEPS,
)
from ember.pipelines.zimage.handler import prepare_init
from ember.pipelines.zimage.workflow import build_zimage_workflow, zimage_missing


def open_render(path) -> Image.Image:
    """A finished picture off the disk, read in full so the file is closed.

    Module-level so scripts/golden.py can hand back a synthesised picture:
    its stubbed ComfyUI writes no files.
    """
    with Image.open(path) as image:
        image.load()
        return image.copy()


@dataclass(frozen=True)
class Refine:
    """One click's refine settings, resolved: what each refine job runs."""

    prompt: str
    denoise: float
    unet_file: str
    recipe_label: str = REFINE_FIELD_LABEL

    @staticmethod
    def builder(**job) -> dict:
        return build_zimage_workflow(**job)

    def job(self, path, seed: int) -> dict:
        """The Z-Image job that refines the picture at `path`.

        At the picture's own size (init_size of its own width and height
        is that size, on the grid of 16), on the seed it was made with.
        Uploads it to ComfyUI's input folder, so this raises what
        upload_image raises.
        """
        image = open_render(path)
        prepared = prepare_init(image, *image.size)
        name = client.upload_image(
            _png_bytes(prepared), f"refine_{uuid.uuid4().hex[:8]}.png")
        width, height = prepared.size
        return {
            "prompt": self.prompt, "negative": "", "seed": int(seed),
            "steps": REFINE_STEPS, "cfg": REFINE_CFG, "width": width,
            "height": height, "sampler": REFINE_SAMPLER, "loras": (),
            "unet_file": self.unet_file, "upscale": False,
            "init_image": name, "denoise": self.denoise,
        }


def refine_spec(on, denoise, prompt, main_prompt, edit=False):
    """The form's refine controls -> (Refine or None, error or None).

    None and no error when the tick is off, or when this licence does not
    have the refine (the form never showed it, and the submit path holds
    it at off anyway). An error when it is on and cannot run, so the click
    is refused before anything renders rather than half way through.

    The prompt is the box's when it has one. Empty, the generate tabs
    refine with their own `main_prompt` — a description of the picture —
    and the edit tabs (`edit`) with REFINE_EDIT_PROMPT, because theirs is
    an instruction.
    """
    if not on or not features.enabled(features.Key.ZIMAGE_REFINE):
        return None, None
    entry, error = _check_model(ZIMAGE_REFINE, None)
    if error:
        return None, error.replace("❌ ", "❌ Ember Lite refine: ", 1)
    missing = zimage_missing()
    if missing:
        return None, ("❌ The Ember Lite refine's files are not downloaded "
                      "yet (missing: %s) — restart the app so the download "
                      "step can fetch them, or untick the refine."
                      % ", ".join(missing))
    text = str(prompt or "").strip()
    if not text:
        text = (REFINE_EDIT_PROMPT if edit
                else str(main_prompt or "").strip() or REFINE_EDIT_PROMPT)
    return Refine(text, float(denoise), entry.file), None
