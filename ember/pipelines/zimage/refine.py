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
the picture it came from, plus `_refined`.

Each refine is a run the Ember Lite tab could have made, and it is filed
as one. Its recipe is an Ember Lite recipe — the original as the start
image (kept in the source store), the refine's prompt, denoise and seed,
12 steps at CFG 1 on er_sde, no LoRAs — so "Load these settings" on a
refined picture opens Ember Lite ready to refine the same original again
at another denoise, and only for a licence with that tab, since the
Gallery offers the button only for a tab the licence has. For that to
make the same picture, the refine renders at a size the form can say:
REFINE_RESOLUTION times a multiplier on the slider's grid, which is the
original's pixel count to within a few percent, in its own shape. The
original's recipe stays its own tab's, with the tick off.

Gated by the zimage_refine feature, not by the Ember Lite tab's, so a key
can have the one without the other; the model is that feature's own
catalogue list. The Refine is duck-typed by the runner — `job`, `builder`,
`recipe`, `recipe_label` — so ember.generation.runner never imports this
module, which imports it.
"""

import uuid
from dataclasses import dataclass

from PIL import Image

from ember import features
from ember.comfy.client import client
from ember.generation import recipes
from ember.generation import sources
from ember.generation.handlers import ZIMAGE_REFINE, _check_model
from ember.generation.runner import _png_bytes
from ember.pipelines.zimage.constants import (
    REFINE_CFG,
    REFINE_EDIT_PROMPT,
    REFINE_FIELD_LABEL,
    REFINE_RESOLUTION,
    REFINE_SAMPLER,
    REFINE_STEPS,
    ZIMAGE_RESOLUTIONS,
)
from ember.pipelines.zimage.handler import prepare_init
from ember.pipelines.zimage.workflow import (
    build_zimage_workflow,
    output_size,
    zimage_missing,
)

# The Ember Lite tab's Resolution multiplier slider: its range and step.
# The refine's multiplier is snapped to it, so the form can hold it.
_MULTIPLIER_LO, _MULTIPLIER_HI, _MULTIPLIER_STEP = 0.5, 2.0, 0.05


def refine_multiplier(width: int, height: int) -> float:
    """The multiplier that gives REFINE_RESOLUTION a picture's pixel count.

    On the slider's grid and inside its range, so a refine of a 2048×1152
    Krea2 V2 picture runs at 1328² × 1.15² — within a few percent of its
    own area — and a recipe can put the number back on the slider.
    """
    side = ZIMAGE_RESOLUTIONS[REFINE_RESOLUTION][0]
    scale = (width * height) ** 0.5 / side
    snapped = round(scale / _MULTIPLIER_STEP) * _MULTIPLIER_STEP
    return round(min(_MULTIPLIER_HI, max(_MULTIPLIER_LO, snapped)), 2)


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
    model_id: str
    unet_file: str
    recipe_label: str = REFINE_FIELD_LABEL

    @staticmethod
    def builder(**job) -> dict:
        return build_zimage_workflow(**job)

    def job(self, path, seed: int) -> dict:
        """The Z-Image job that refines the picture at `path`.

        Sized as generate_zimage sizes a start image: REFINE_RESOLUTION
        times refine_multiplier, then the picture's own shape at that
        area. On the seed it was made with. Uploads it to ComfyUI's input
        folder, so this raises what upload_image raises.
        """
        image = open_render(path)
        width, height = output_size(*ZIMAGE_RESOLUTIONS[REFINE_RESOLUTION],
                                    refine_multiplier(*image.size))
        prepared = prepare_init(image, width, height)
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

    def recipe(self, path, seed: int) -> dict:
        """The refined picture's recipe: the Ember Lite run it amounts to.

        What recipes.begin would have filed had the Ember Lite tab made
        it, with the original kept as its start image. Without a kept copy
        the start image slot is empty and loading it leaves the drop zone
        alone, as for any recipe whose source is gone. `refined_from` names
        the original.
        """
        # Late: tabschema imports every tab's handler, and those import
        # this module.
        from ember.web.schema.tabs.zimage import ZIMAGE_SCHEMA

        with Image.open(path) as image:
            fmt = image.format
            multiplier = refine_multiplier(*image.size)
        kept = sources.keep(path, fmt)
        values = ZIMAGE_SCHEMA.defaults()
        values.update(
            prompt=self.prompt, negative="", seed=int(seed), randomize=False,
            steps=REFINE_STEPS, cfg=REFINE_CFG, resolution=REFINE_RESOLUTION,
            multiplier=multiplier, sampler=REFINE_SAMPLER,
            model=self.model_id, upscale=False, batch_count=1,
            denoise=self.denoise)
        return {
            "tab": str(ZIMAGE_SCHEMA.key), "tab_label": ZIMAGE_SCHEMA.title(),
            "tab_id": ZIMAGE_SCHEMA.tab_id,
            "fields": ZIMAGE_SCHEMA.recipe_fields(
                values, {"image": kept} if kept else {}),
            "seed": int(seed), "preset": "",
            "refined_from": recipes.key_for(path),
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
    return Refine(text, float(denoise), entry.id, entry.file), None
