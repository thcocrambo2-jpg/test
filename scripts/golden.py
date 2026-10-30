#!/usr/bin/env python3
"""Freeze the workflow dict every generate_* handler builds, and diff it.

Operator tool, not part of the shipped app — the same posture as
scripts/parity.py and scripts/dryrun.py, and it borrows their weightless
boot so it runs on a laptop with no GPU, no ComfyUI and no models.

    python scripts/golden.py                  # write the snapshots
    python scripts/golden.py --check          # exit 1 on any difference

Why this exists
---------------
scripts/parity.py freezes the *forms*. This freezes what the handlers do
with them, and the two catch different things.

`generate_v2` takes **31 positional arguments**. The adapter that calls it
has to hand them over in exactly the right order, and the failure mode of getting it wrong is not an exception: swap
`cutoff_step` and `total_steps` and you have swapped two ints, every
handler still runs, every picture still arrives, and they are quietly
worse. Nothing in the app notices, and no human reading a 5,000-line diff
notices either.

So take the answer from the code while it is still the truth. The seam is
`_run_jobs` in ember.generation.runner, which calls
`builder(filename_prefix=..., **job)`
and then `client.run(workflow)` — patch `run`, keep the dict it was handed,
and that dict is the complete statement of what the arguments meant. A
swapped pair of ints moves a number inside it, and `--check` names the
JSON path it moved at.

What is stubbed, and why each one is necessary
----------------------------------------------
* `comfy_ensure_alive` -> `(True, "")`. scripts/dryrun.py's `stub_comfy()`
  makes it return `(False, note)`, which makes `_run_jobs` yield the note
  and return **before it builds anything at all**. Without this override
  every snapshot would be empty and the whole file would pass while
  proving nothing.
* the model / LoRA availability checks -> True. Every handler guards on
  "is this downloaded yet" and returns early when it is not — and a ticked
  LoRA whose file is missing is skipped rather than wired — so without
  this a laptop, which has none of the weights, would build graphs with
  no LoRA chain at all.
* `client.run` / `wan_client.run` -> capture and yield canned events.
* `client.upload_image` -> a fixed name. The real one POSTs to ComfyUI,
  and the name it returns lands *in* the workflow, so it has to be stable
  or every image handler's snapshot would differ on every run.
* `free_models` -> nothing. `_release_on_swap` calls it whenever two
  consecutive jobs need different weights, which is every handler after
  the first one here.
* `prompts.record` and `presets.save` -> nothing. Both POST to the licence
  server.

The models and LoRAs come from the catalogue (catalog.py). Unless
EMBER_CATALOG_FILE is already set, this points it at the seed document,
license-validator/data/assets.json, so the snapshots are built from the
same ids and files the licence server is seeded with — and never from
whatever a live server or a cached .catalog.json happens to hold.

Snapshots live in scripts/golden/, one JSON per handler, and are
committed. Regenerate them deliberately — a diff here is either a bug you
have just caught or a change you meant to make, and it is worth being
sure which before overwriting it.
"""

import argparse
import io
import json
import os
import sys
import types
from pathlib import Path

# One level up, and deliberately outside the package so Nuitka never sweeps
# it into the binary. Same reasoning as scripts/parity.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Before ensure_dirs() below makes the directory tree: the shipped default
# is the pod path /workspace/ember. Share .dryrun with the other scripts
# rather than making a third empty tree.
os.environ.setdefault("EMBER_BASE_DIR", str(ROOT / ".dryrun"))
# The catalogue the Krea tabs read their models and LoRAs from — the seed
# document, so a snapshot never depends on a server. See the docstring.
os.environ.setdefault("EMBER_CATALOG_FILE",
                      str(ROOT / "license-validator" / "data" / "assets.json"))

from ember import logs, settings  # noqa: E402
from ember.logs import log  # noqa: E402

# Importing a module configures nothing and creates nothing any more, so
# this entry point does it: the logger, the tree under EMBER_BASE_DIR,
# and the line saying where weights and images go.
logs.setup()
settings.ensure_dirs()
settings.log_startup()

SNAPSHOTS = Path(__file__).resolve().parent / "golden"

# A source image whose size is deliberately awkward: 653x431 is neither a
# multiple of 16 nor of 32, and is under every "never upscale" cap. Every
# tab that fits, snaps or caps a size therefore has to actually do it, and
# the number it lands on is in the snapshot.
IMAGE_SIZE = (653, 431)

# What the patched upload returns. The real one is named with a uuid, so
# without a fixed answer here every image handler's workflow would differ
# from itself between two runs.
UPLOADED = "golden_upload.png"

# The picture the refine cases refine, in place of a file on disk. Its
# size reaches only the uploaded start image, which is stubbed: the
# refine's graph starts from a VAEEncode and carries no width or height,
# so the snapshots cannot show what size it refines at.
REFINE_RENDER_SIZE = (1080, 1350)


def stub_comfy() -> None:
    """Make `import comfy` work on a machine ComfyUI cannot run on.

    Lifted from scripts/dryrun.py and has to stay in step with it:
    ember.comfy.server detects GPUs at import and raises when there are
    none, while ember.generation.runner binds `ensure_alive` at import
    time so a later patch is not seen.

    Note the difference from the other two scripts: theirs returns False,
    so nothing is ever built. This one returns **True** — see the module
    docstring.
    """
    def ensure_alive(*_args, **_kwargs):
        return True, ""

    try:
        from ember.comfy import server as comfy
    except RuntimeError as exc:            # no NVIDIA GPU visible
        log.warning("No GPU detected (%s) - faking the comfy module", exc)
        comfy = types.ModuleType("ember.comfy.server")
        comfy.GPUS, comfy.GPU_COUNT = [], 1
        comfy.start_comfyui = lambda *a, **k: None
        comfy.wait_for_comfyui = lambda *a, **k: None
        comfy.verify_custom_node = lambda *a, **k: True
        comfy.node_registered = lambda *a, **k: True
        comfy.log_tail = lambda *a, **k: "<golden>"
        sys.modules["ember.comfy.server"] = comfy
        # A from-import of the parent package copies the binding at import
        # time, so the stub has to be visible as that attribute too.
        import ember.comfy
        ember.comfy.server = comfy
    comfy.ensure_alive = ensure_alive


def handler_modules() -> tuple:
    """Every module a patched name can have been copied into, in one place.

    The seven pipeline handler modules hold the generate_* functions;
    ember.generation.handlers, .runner and .loras hold what they share;
    the workflow modules are here because the availability helpers are
    defined there and from-imported by the rest. Patching walks all of
    them, so moving a function between two of them leaves this file and
    the JSON snapshots untouched — which is the whole point of a pure
    move.
    """
    from ember.generation import handlers
    from ember.generation import loras
    from ember.generation import runner
    from ember.pipelines.krea2 import handler as krea2_handler
    from ember.pipelines.anima import handler as anima_handler
    from ember.pipelines.anima import workflow as anima
    from ember.pipelines.krea2 import workflow as krea2
    from ember.pipelines.krea2_edit_v2 import handler as krea2_edit_v2_handler
    from ember.pipelines.krea2_edit_v2 import workflow as krea2_edit_v2
    from ember.pipelines.krea2_v2 import handler as krea2_v2_handler
    from ember.pipelines.krea2_v2 import workflow as krea2_v2
    from ember.pipelines.krea2_v2_edit import handler as krea2_v2_edit_handler
    from ember.pipelines.krea2_v2_edit import workflow as krea2_v2_edit
    from ember.pipelines.minimax import handler as minimax_handler
    from ember.pipelines.minimax import workflow as minimax
    from ember.pipelines.qwen21 import handler as qwen21_handler
    from ember.pipelines.qwen21 import workflow as qwen21
    from ember.pipelines.qwen_edit import handler as qwen_edit_handler
    from ember.pipelines.qwen_edit import workflow as qwen_edit
    from ember.pipelines.wan import handler as wan_handler
    from ember.pipelines.wan import workflow as wan
    from ember.pipelines.zimage import handler as zimage_handler
    from ember.pipelines.zimage import refine as zimage_refine
    from ember.pipelines.zimage import workflow as zimage
    return (handlers, loras, runner,
            krea2_handler, krea2_v2_handler, krea2_v2_edit_handler,
            wan_handler, minimax_handler, zimage_handler, qwen21_handler,
            anima_handler, krea2_edit_v2_handler, qwen_edit_handler,
            krea2, krea2_v2, krea2_v2_edit, wan, minimax, zimage, qwen21,
            anima, krea2_edit_v2, qwen_edit, zimage_refine)


class Namespace:
    """One attribute lookup across every module handler_modules() names.

    The cases below and `snapshot()` want `default_model`, `KREA_T2I`,
    `MAX_LORA_SLOTS`, `v2_lora_slots` and each generate_* by name, and
    those now sit in different modules. Read-only: patching goes to the
    real modules, never through here, so nothing can be stubbed on a
    namespace no handler looks at.
    """

    def __init__(self, modules):
        self._modules = modules

    def __getattr__(self, name):
        for module in self._modules:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(
            "%s is on none of %s" % (name, [m.__name__ for m in self._modules])
        )


class Capture:
    """The patched `client.run`: keep the workflow, yield plausible events.

    The events are the shape client.py:270 documents — a couple of
    progress ticks and a done — so the handler's own generator loop runs to
    the end and a batch of two really does build two workflows.
    """

    def __init__(self):
        self.workflows = []
        self.settings = None      # the blob prompts.record was handed

    def run(self, workflow, timeout=None):
        self.workflows.append(workflow)
        yield {"type": "progress", "step": 1, "total": 8}
        yield {"type": "progress", "step": 8, "total": 8}
        yield {"type": "done", "images": [str(Path("golden") / "out.png")]}


def _image():
    """One synthesised source image, for the tabs that take an upload."""
    from PIL import Image
    return Image.new("RGB", IMAGE_SIZE, (90, 120, 200))


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _patch_all(users, name, value) -> None:
    """Set `name` on every module that has one, and insist at least one does.

    A from-import copies the binding, so the only namespace a stub is seen
    in is the one that reads the name —
    `from ember.pipelines.krea2.workflow import model_file_available` gives
    ember.pipelines.krea2.handler a name of its own, and patching the
    workflow module afterwards would not be seen there. Hence every user,
    not the definition.

    The assertion is the other half. A patch that lands nowhere does not
    fail: the snapshots still come out, built by the real function, and
    `--check` passes while proving nothing. So a name no module carries is
    a broken patch target, and this says so instead of going quiet.
    """
    patched = [user.__name__ for user in users if hasattr(user, name)]
    if not patched:
        raise SystemExit(
            "golden: nothing to patch — %r is on none of %s. A stub that "
            "lands nowhere leaves --check passing against the real code."
            % (name, [user.__name__ for user in users])
        )
    for user in users:
        if hasattr(user, name):
            setattr(user, name, value)


def patch(users, capture) -> None:
    """Stub everything that would touch a GPU, a disk or the network.

    `users` is handler_modules() — every namespace a patched name can have
    been copied into. See _patch_all for why it is the users and not the
    definitions, and scripts/dryrun.py for the same reason applied to
    `ensure_alive`, which it patches before anything imports it.
    """
    from ember.comfy import client
    from ember.licensing import presets
    from ember.licensing import prompts

    for name in (
        "model_file_available", "lora_file_available", "edit_lora_available",
        "v2_turbo_lora_available",
        "wan_models_available", "wan_5b_available", "wan_lightning_available",
        "wan_rife_available",
        "minimax_models_available",
    ):
        _patch_all(users, name, lambda *a, **k: True)

    for name in ("v2_status", "v2_edit_status", "edit_v2_status"):
        _patch_all(users, name, lambda *a, **k: (True, ""))

    # Z-Image's fixed-file check answers with what is missing, so nothing
    # missing is an empty list rather than True. Its Upscale tick also asks
    # ComfyUI whether the UltimateSDUpscale node is registered.
    _patch_all(users, "zimage_missing", lambda *a, **k: [])
    # Qwen 2.1's is the same shape. Its two core nodes are asked for
    # through node_registered, patched above.
    _patch_all(users, "qwen21_missing", lambda *a, **k: [])
    _patch_all(users, "anima_missing", lambda *a, **k: [])
    # Qwen Edit 2511's too; it takes the Lightning file it is asked about.
    _patch_all(users, "qwen_edit_missing", lambda *a, **k: [])
    _patch_all(users, "node_registered", lambda *a, **k: True)

    _patch_all(users, "comfy_ensure_alive", lambda *a, **k: (True, ""))

    # The ⚡ Ember Lite refine opens each picture the batch made, and the
    # stubbed ComfyUI makes none, so it is handed this one instead.
    from PIL import Image
    _patch_all(users, "open_render",
               lambda path: Image.new("RGB", REFINE_RENDER_SIZE,
                                      (180, 140, 120)))

    # The per-job filename token. Random by design — that is the whole
    # point of it — and it lands *in* the workflow as filename_prefix, so
    # without this every snapshot would differ on every run. Same reason
    # upload_image is pinned to UPLOADED below.
    _patch_all(users, "_run_tag", lambda: "golden00")

    # Silent by contract in the app; a POST to the licence server here.
    # Kept rather than discarded: the fourth positional is the settings
    # blob the licence server stores, and it is the thing tabschema has to
    # reproduce byte for byte (see check_settings).
    def record(tab, prompt, negative, settings, **_kwargs):
        capture.settings = settings

    # The MiniMax tabs keep no prompt library, so their blob is only ever
    # seen by a preset save — captured the same way, for the same check.
    def save(tab, name, settings):
        capture.settings = settings
        return True, "stubbed"

    prompts.record = record
    presets.save = save

    for comfy_client in (client.client, client.wan_client):
        comfy_client.run = capture.run
        comfy_client.upload_image = lambda data, name: UPLOADED
        comfy_client.free_models = lambda *a, **k: True
        comfy_client.interrupt = lambda *a, **k: None


def cases(module) -> dict:
    """handler name -> the exact argument tuple it is called with.

    Every value is deliberately *different* from its neighbours and from
    the control's default. Two adjacent ints that are both 8 would hide
    the swap this file exists to catch, so `cutoff_step` is 7 and
    `total_steps` is 23 rather than both being what the slider ships with.

    The LoRA tails are triples `(enabled, lora id, weight)` — every tab's
    row carries an on/off column; see docs/architecture/web-ui.md,
    "Submission order is load-bearing". Models and LoRAs are
    catalogue ids, and each case switches a few rows on so the snapshot
    holds a real LoRA chain — the file names in it are the one place the
    id -> file mapping is visible. A named row left *off* is in each tail
    too, so a regression that wires unticked rows shows up as a new node.
    """
    model = module.default_model(module.KREA_T2I)
    edit_model = module.default_model(module.KREA_EDIT)
    v2_model = module.default_model(module.KREA_V2_T2I)
    v2_edit_model = module.default_model(module.KREA_V2_EDIT)
    edit_v2_model = module.default_model(module.KREA_EDIT_V2)
    zimage_model = module.default_model(module.ZIMAGE_T2I)
    qwen21_model = module.default_model(module.QWEN21_EDIT)
    qwen21_t2i_model = module.default_model(module.QWEN21_T2I)
    anima_model = module.default_model(module.ANIMA_T2I)
    qwen_edit_model = module.default_model(module.QWEN_EDIT_2511)
    image = _image()

    # Krea2: eight blank slots. The first three name LoRAs — two on, one
    # off — and the rest stay "None".
    krea_named = {0: (True, "hmbody-d-e10"),
                  1: (True, "realism-engine-v2-0"),
                  2: (False, "galaxyace")}
    krea_triples = tuple(
        v for i in range(module.MAX_LORA_SLOTS)
        for v in (*krea_named.get(i, (False, "None")),
                  round(0.35 + i / 100, 2)))
    # Krea2 Edit: its own eight blank slots over its own feature's list,
    # so the snapshot shows the Identity Edit LoRA and the stack landing
    # on the same chain. Different rows and weights from the Krea2 tail,
    # so a tail copied from the wrong tab shows up.
    edit_named = {0: (True, "realism-v2"),
                  2: (True, "purelens"),
                  3: (False, "lenovo")}
    edit_triples = tuple(
        v for i in range(module.MAX_LORA_SLOTS)
        for v in (*edit_named.get(i, (False, "None")),
                  round(0.75 + i / 100, 2)))
    # MiniMax: the same eight blank slots over its own list, so the
    # snapshots show the chain landing between the model and the turbo
    # LoRA. The image tab has two on and one named but off; the text tab
    # one of each.
    def minimax_triples(named):
        return tuple(
            v for i in range(module.MAX_LORA_SLOTS)
            for v in (*named.get(i, (False, "None")),
                      round(0.55 + i / 100, 2)))
    # Z-Image: eight blank slots over its own list, which is empty in the
    # seed catalogue. The first row ticks a Krea LoRA, which this tab does
    # not offer, so the snapshots show it is skipped rather than chained.
    zimage_triples = tuple(
        v for i in range(module.MAX_LORA_SLOTS)
        for v in (*({0: (True, "hmbody-d-e10")}.get(i, (False, "None"))),
                  round(0.65 + i / 100, 2)))
    # Qwen 2.1 Reference: four blank slots over its own list, which is
    # empty in the seed catalogue. The second row ticks a Krea LoRA, which
    # this tab does not offer, so the snapshots show it skipped.
    qwen21_triples = tuple(
        v for i in range(4)
        for v in (*({1: (True, "realism-v2")}.get(i, (False, "None"))),
                  round(0.85 + i / 100, 2)))
    # Qwen 2.1: the same four slots over its own empty list, a different
    # row ticked, so a tail copied from the Reference tab shows up.
    qwen21_t2i_triples = tuple(
        v for i in range(4)
        for v in (*({2: (True, "hmbody-d-e10")}.get(i, (False, "None"))),
                  round(0.95 - i / 100, 2)))
    # Anima: the same four slots over its own empty list, another row
    # ticked, so a tail copied from Qwen 2.1 shows up.
    anima_triples = tuple(
        v for i in range(4)
        for v in (*({3: (True, "hmbody-d-e10")}.get(i, (False, "None"))),
                  round(0.9 - i / 100, 2)))
    # Qwen Edit 2511: eight blank slots over its own list, which holds the
    # Multiple Angles LoRA. The first row ticks it, the third ticks a Krea
    # LoRA this tab does not offer, so the snapshots show one chained after
    # Lightning and the other skipped.
    qwen_edit_triples = tuple(
        v for i in range(module.MAX_LORA_SLOTS)
        for v in (*({0: (True, "qwen-edit-2511-multiple-angles"),
                     2: (True, "hmbody-d-e10")}.get(i, (False, "None"))),
                  round(0.8 + i / 100, 2)))
    # References of different shapes, so "Same as reference 1" has a shape
    # to take and the order is visible in the sizes.
    from PIL import Image
    references = [Image.new("RGB", size, (40 * n % 255, 90, 160))
                  for n, size in enumerate(
                      [(1536, 2048), (1080, 1350), (1920, 1080),
                       (2000, 2000), (1600, 1600), (1024, 1536),
                       (1500, 1000), (1200, 1200), (2560, 1440),
                       (1080, 1080)], start=1)]

    # V2: one row per LoRA in the feature's list, as the form has them.
    # Rows 1-4 on (filter bypass, enhancer, realism v2, realism engine
    # v3.1), every other row off but still naming its LoRA.
    # Each V2 tab reads its own feature's list, so each gets its own tail.
    def v2_triples(feature, on=frozenset({1, 2, 3, 4})):
        return tuple(
            v for i, (_on, lora_id, _strength)
            in enumerate(module.v2_lora_slots(feature))
            for v in (i in on, lora_id, round(0.45 + i / 100, 2)))

    # The refine controls (tick, denoise, prompt), off: what every case
    # below sends unless it is a refine case, so their graphs show the
    # tick off adds nothing.
    REFINE_OFF = (False, 0.25, "")

    return {
        "generate_single": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            1234567, False, 9, 1.7, "1216×832 (Landscape)", "euler",
            model, 2, False, "", False, "",
        ) + REFINE_OFF + krea_triples,

        # The two-input edit: the second reference is on, so the snapshot
        # holds both LoadImage nodes and the dual conditioning. It is the
        # only case that exercises `edit_lora_available`, which is the
        # only patched name ember/pipelines/krea2/handler.py carries.
        "generate_edit": (
            image, True, image, "put a red scarf on her",
            "blurry, watermark", 2345678, False, 14, 2.3, "dpmpp_2m",
            576, 4.5, 1.5, edit_model, 2,
        ) + REFINE_OFF + edit_triples,

        "generate_v2": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            4567890, False, v2_model, "16:9 (Landscape Wide)", 2.25, 32,
            0.35, "res_2m", "beta57", 13, 0.85, 2.4, "unsample", False,
            "🌿 Balanced", 37, "🖼️ Qwen-Image", "decreasing",
            7, 23, 0.65, 140, True, True, 2, False, "", False, "",
        ) + REFINE_OFF + v2_triples(module.KREA_V2_T2I),

        # The ⚡ Ember Lite refine on a generate tab, its prompt box empty:
        # the two V2 graphs, then one Z-Image image-to-image graph per
        # picture, at 0.3 on the picture's own seed, refining with the
        # tab's prompt, with no LoRAs, and each saved as its original's
        # prefix plus "_refined". The blob prompts.record keeps has the
        # tick on.
        "generate_v2_refine": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            4567891, False, v2_model, "16:9 (Landscape Wide)", 2.25, 32,
            0.35, "res_2m", "beta57", 13, 0.85, 2.4, "unsample", False,
            "🌿 Balanced", 37, "🖼️ Qwen-Image", "decreasing",
            7, 23, 0.65, 140, False, False, 2, False, "", False, "",
            True, 0.3, "",
        ) + v2_triples(module.KREA_V2_T2I),

        "generate_v2_edit": (
            image, True, image, "put a red hat on the person",
            "blurry, watermark", 5678901, False, v2_edit_model,
            640, 3.5, 2.5, "crop (legacy)", 0.35, "res_3m", "normal",
            13, 2.4, "resample", False,
            "🪴 Creative", 61, "🔮 Flux (Dev/Schnell)", "step_cutoff",
            7, 23, 0.65, 140, 2,
        ) + REFINE_OFF + v2_triples(module.KREA_V2_EDIT),

        # Krea2 Edit V2, the author's restage recipe: one image, Identity
        # Edit V2 at 1.0, the person placed beside, ref_boost 2, 8 steps at
        # CFG 1. The scene placement is set but not emitted, because there
        # is no second reference.
        "generate_edit_v2": (
            image, False, None, "she sits cross-legged on a sofa, full body",
            "", 6789012, False, edit_v2_model,
            "🧑 Identity — restage the person (pose, scene, outfit, light)",
            "beside (right of target)", "bag of features",
            768, 2.0, 1.0, 0.5, "linear/euler", "bong_tangent",
            8, 1.0, "standard", True,
            "❌ Disabled", 50, "📸 Krea2 (SingleStream)", "step_cutoff",
            5, 20, 0.5, 100, 2,
        ) + REFINE_OFF + v2_triples(module.KREA_EDIT_V2),

        # Two references and both LoRAs at 0.6. The person is aligned and
        # the scene beside, the reverse of the author's compositing recipe,
        # so ref_position / ref_position_a swapping over shows; every other
        # setting differs from the case above for the same reason.
        "generate_edit_v2_both": (
            image, True, image, "put the woman from image 2 on the beach",
            "blurry, watermark", 7890123, False, edit_v2_model,
            "🧑✂️ Both at 0.6",
            "aligned (trained)", "beside (right of target)",
            640, 3.0, 1.5, 0.35, "res_3m", "normal",
            12, 2.2, "resample", False,
            "🪴 Creative", 61, "🔮 Flux (Dev/Schnell)", "step_cutoff",
            7, 23, 0.65, 140, 1,
        ) + REFINE_OFF + v2_triples(module.KREA_EDIT_V2, on=frozenset({0, 5})),

        # The refine on an edit tab, its prompt box empty: the instruction
        # is not a description, so the refine prompts with the built-in
        # REFINE_EDIT_PROMPT instead.
        "generate_edit_v2_refine": (
            image, False, None, "she sits cross-legged on a sofa, full body",
            "", 6789013, False, edit_v2_model,
            "🧑 Identity — restage the person (pose, scene, outfit, light)",
            "beside (right of target)", "bag of features",
            768, 2.0, 1.0, 0.5, "linear/euler", "bong_tangent",
            8, 1.0, "standard", True,
            "❌ Disabled", 50, "📸 Krea2 (SingleStream)", "step_cutoff",
            5, 20, 0.5, 100, 1, True, 0.25, "",
        ) + v2_triples(module.KREA_EDIT_V2),

        # Anima, the model card's graph at the BAS:NSFW record's 8 steps and
        # CFG 1: no negative, so it is the positive's ConditioningZeroOut,
        # at 2:3's 832×1216. Saves a preset, so its blob is checked against
        # settings(). A Krea LoRA ticked on the stack is skipped — the
        # feature's own list is empty.
        "generate_anima": (
            "masterpiece, best quality, 1girl, solo, red scarf, snowy "
            "street, night, lanterns", "", "2:3 · 832×1216", anima_model,
            8, 1.0, "euler", "simple", 8642097, False, 2, True, "Golden",
        ) + anima_triples,

        # With a negative and CFG 4.5 on er_sde/beta at 30 steps, the base
        # model's own settings: the negative is encoded instead of zeroed.
        # 16:9 is 1344×768.
        "generate_anima_negative": (
            "year 2025, newest, 1boy, solo, samurai, rain, katana",
            "worst quality, low quality, score_1, score_2, score_3",
            "16:9 · 1344×768", anima_model, 30, 4.5, "er_sde", "beta",
            9753108, False, 1, False, "",
        ) + anima_triples,

        "generate_wan_video": (
            image, "the waves roll in", "static, blurry",
            "14B two-expert (best quality, 16 fps)", "Raw (20 steps)",
            6789012, False, 17, 3.1, "720p (sharper, ~3× slower)", 3.25,
            "dpmpp_2m", 2, False,
        ),

        # Turbo with Smooth motion: the two lightx2v LoRAs on the experts,
        # and RIFE between the decode and the video, which is saved at
        # 16 x 4 = 64 fps. 4.5 s at 16 fps is 73 frames.
        "generate_wan_video_smooth": (
            image, "a cat stretches and yawns", "",
            "14B two-expert (best quality, 16 fps)",
            "Turbo (Lightning, 4 steps)", 2468013, False, 5, 1.0,
            "480p (faster)", 4.5, "euler", 1, True,
        ),

        # The 5B with Smooth motion: its 24 fps x 2 = 48. The mode radio
        # is ignored for this model, so Turbo here must not wire a LoRA.
        "generate_wan_video_5b_smooth": (
            image, "leaves drift across the lawn", "washed out",
            "5B TI2V (lighter, 24 fps)", "Turbo (Lightning, 4 steps)",
            1357924, False, 19, 4.5, "480p (faster)", 2.75, "uni_pc", 1,
            True,
        ),

        # The two MiniMax tabs share one builder; the snapshots prove the
        # image one wires a LoadImage in and the text one does not, and
        # that 7 s lands on 175 frames and 6 s on 158 — the 17k+5 grid,
        # which is the one arithmetic here that is easy to get subtly
        # wrong and impossible to see in a finished clip.
        "generate_minimax_video": (
            image, "the tide comes in and the gulls cry", "minimax-h3-int8",
            7890123, False, 9, "Native 768p (short edge 768, slower)", 7,
            "dpmpp_2m", "simple", 12.0, 3.0, 0.8, 2, True, "Golden",
        ) + minimax_triples({0: (True, "hmnsfw-aio-v2-5"),
                             1: (False, "vgna"),
                             2: (True, "hmmisdog")}),

        "generate_minimax_t2v": (
            "a kettle comes to the boil and whistles", "minimax-h3-int8",
            "16:9 (Widescreen)", 8901234, False, 11, "Standard (0.7 MP — the template default)", 6,
            "euler_ancestral", "simple", 12.0, 3.0, 0.8, 2, True, "Golden",
        ) + minimax_triples({0: (False, "hmbrst"),
                             1: (True, "hmcshot-v1-0")}),

        # Eros Max's turbo-hybrid at its own recipe: LCM, turbo LoRA at 0,
        # so the graph has no turbo node and the LoRA chain feeds the
        # guider directly; the shift is the model's own, so no shift node.
        "generate_minimax_video_eros": (
            image, "she turns to the window as the rain starts",
            "eros-max-beta5", 7890321, False, 6,
            "Standard (0.7 MP — the template default)", 5,
            "lcm", "simple", 12.0, 3.0, 0.0, 1, False, "",
        ) + minimax_triples({0: (True, "mysticxxx-mmh3-v4")}),

        # A shift other than the model's 12/3 and another scheduler: the
        # MiniMaxH3SigmaShift node goes in after the turbo LoRA, and the
        # scheduler reaches BasicScheduler.
        "generate_minimax_t2v_shift": (
            "waves break on a pebble beach at dusk", "minimax-h3-int8",
            "16:9 (Widescreen)", 8901432, False, 8,
            "Standard (0.7 MP — the template default)", 5,
            "euler", "beta", 8.0, 6.0, 0.7, 1, False, "",
        ) + minimax_triples({}),

        # Z-Image, the Upscale tick off: the template's Turbo graph and
        # nothing else. 1472×1140 at 1.15 is 1692.8×1311.0, which the
        # SimpleMath+ rounding puts at 1693×1311.
        "generate_zimage": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            9012345, False, 11, 1.4, "1472×1140 (4:3)", 1.15, "euler",
            zimage_model, False, 2, True, "Golden", None, 0.42,
        ) + zimage_triples,

        # And on: the same graph plus 4xLSDIR and UltimateSDUpscale, tiled
        # at the base size (1080×1920 at 0.9 is 972×1728) on the job's seed.
        "generate_zimage_upscale": (
            "a lighthouse in a storm, 35mm", "",
            9123456, False, 13, 1.6, "1080×1920 (1080p portrait)", 0.9,
            "dpmpp_2m", zimage_model, True, 2, False, "", None, 0.35,
        ) + zimage_triples,

        # With a start image: LoadImage and VAEEncode instead of the empty
        # latent, the KSampler at the form's denoise. The 653×431 image
        # keeps its shape at 1328×1328's area — 1634.6×1078.9, which the
        # grid of 16 puts at 1632×1072 — and the upscale pass tiles at
        # that size.
        "generate_zimage_i2i": (
            "a lighthouse in a storm, 35mm", "",
            9234567, False, 12, 1.0, "1328×1328 (1:1)", 1.0, "er_sde",
            zimage_model, True, 2, False, "", image, 0.38,
        ) + zimage_triples,

        # Qwen 2.1 Reference with one reference and "Same as reference 1":
        # 653×431 at about 2048² pixels, on the 32 grid, is 2528×1664.
        # Saves a preset, so its blob is checked against settings().
        "generate_qwen21_ref": (
            [image], "the woman from image 1 on a beach at dusk",
            "blurry, watermark", "Same as reference 1", qwen21_model,
            27, 1.3, "euler_ancestral", "beta", 896, 1357913, False, 2,
            True, "Golden",
        ) + REFINE_OFF + qwen21_triples,

        # Three references and a preset size: 16:9 is 2752×1536 whatever
        # reference 1 is, and image_1..image_3 are wired in order.
        "generate_qwen21_ref_three": (
            references[:3], "the woman from image 1 in the jacket from "
            "image 2 outside the café in image 3", "", "16:9 · 2752×1536",
            qwen21_model, 25, 1.0, "euler", "simple", 1024, 2468024, False,
            2, False, "",
        ) + REFINE_OFF + qwen21_triples,

        # All ten, the most the tab takes: image_1..image_10.
        "generate_qwen21_ref_ten": (
            references, "the woman from image 1 wearing every item from "
            "images 2 to 10", "", "Same as reference 1", qwen21_model, 31,
            1.1, "dpmpp_2m", "simple", 0, 3579135, False, 2, False, "",
        ) + REFINE_OFF + qwen21_triples,

        # Qwen 2.1 text to image, the template's graph: no negative, so it
        # is the positive's ConditioningZeroOut, at 3:2's 2528×1696. Saves
        # a preset, so its blob is checked against settings().
        "generate_qwen21_t2i": (
            "a neon sign that reads \"OPEN\" above a noodle bar, rain",
            "", "3:2 · 2528×1696", qwen21_t2i_model, 26, 1.0, "euler",
            "simple", 4681357, False, 2, True, "Golden",
        ) + REFINE_OFF + qwen21_t2i_triples,

        # With a negative and CFG above 1: the negative is encoded instead
        # of zeroed. 9:16 is 1536×2752.
        "generate_qwen21_t2i_negative": (
            "a lighthouse in a storm, 35mm", "blurry, watermark",
            "9:16 · 1536×2752", qwen21_t2i_model, 33, 3.5, "res_multistep",
            "beta", 5791357, False, 2, False, "",
        ) + REFINE_OFF + qwen21_t2i_triples,

        # The refine with a prompt of its own, which wins over the tab's,
        # at 0.45. Saves a preset, so the refine block of its blob is
        # checked against settings() with the tick on.
        "generate_qwen21_t2i_refine": (
            "a lighthouse in a storm, 35mm", "", "3:2 · 2528×1696",
            qwen21_t2i_model, 26, 1.0, "euler", "simple", 4681358, False, 1,
            True, "Golden", True, 0.45,
            "an unretouched 35 mm film photograph, fine grain",
        ) + qwen21_t2i_triples,

        # Qwen Edit 2511 on one image at Lightning 4 steps: the LoRA after
        # CFGNorm, 4 steps at CFG 1 whatever the sliders hold. Saves a
        # preset, so its blob is checked against settings().
        "generate_qwen_edit": (
            references[:1], "turn her around, looking back over her "
            "shoulder", "", "⚡ Lightning · 4 steps", qwen_edit_model, 40,
            4.0, "euler", "simple", 1928374, False, 2, True, "Golden",
        ) + REFINE_OFF + qwen_edit_triples,

        # Two images at Lightning 8 steps: image1 and image2 on both
        # encoders, the 8-step LoRA.
        "generate_qwen_edit_lightning8": (
            references[:2], "the woman in image 1 wearing the dress from "
            "image 2", "", "⚡ Lightning · 8 steps", qwen_edit_model, 40,
            4.0, "euler", "simple", 2837465, False, 1, False, "",
        ) + REFINE_OFF + qwen_edit_triples,

        # All three images on Normal: no Lightning node, and the sliders'
        # steps and CFG reach the KSampler, with the negative encoded.
        "generate_qwen_edit_normal": (
            references[:3], "the woman in image 1 in the pose from image 2 "
            "in the room from image 3", "blurry, extra fingers",
            "Normal (steps and CFG below)", qwen_edit_model, 36, 4.5,
            "res_multistep", "beta", 3746556, False, 1, False, "",
        ) + REFINE_OFF + qwen_edit_triples,
    }


# Cases that call a handler under another name, because one handler has
# more than one graph worth freezing. Every other case is named after its
# handler.
CASE_HANDLERS = {"generate_zimage_upscale": "generate_zimage",
                 "generate_v2_refine": "generate_v2",
                 "generate_edit_v2_refine": "generate_edit_v2",
                 "generate_qwen21_t2i_refine": "generate_qwen21_t2i",
                 "generate_zimage_i2i": "generate_zimage",
                 "generate_qwen21_ref_three": "generate_qwen21_ref",
                 "generate_qwen21_ref_ten": "generate_qwen21_ref",
                 "generate_qwen21_t2i_negative": "generate_qwen21_t2i",
                 "generate_qwen_edit_lightning8": "generate_qwen_edit",
                 "generate_qwen_edit_normal": "generate_qwen_edit",
                 "generate_anima_negative": "generate_anima",
                 "generate_edit_v2_both": "generate_edit_v2",
                 "generate_wan_video_smooth": "generate_wan_video",
                 "generate_wan_video_5b_smooth": "generate_wan_video",
                 "generate_minimax_video_eros": "generate_minimax_video",
                 "generate_minimax_t2v_shift": "generate_minimax_t2v"}


def check_settings(name, args, settings) -> None:
    """tabschema.settings() == what the handler stored. Raises on drift.

    The other half of the preset contract, and the half that cannot be
    checked at import: the two tabs that write a settings blob are the two
    whose handlers guard on `v2_status()` and on the weights being
    downloaded, so reaching the `prompts.record` call at all needs the
    stubs this file already installs.

    tabschema asserts the *Krea 2* blob against krea2._krea_settings at
    import, because that one is a plain function. The V2 blob is built
    inline inside generate_v2, so the only way to see it is to run the
    handler — which is what happens here. The two MiniMax cases and the
    first Z-Image, Qwen 2.1 Reference, Qwen 2.1, Anima and Qwen Edit 2511
    cases tick the save-preset box, so their blobs (_minimax_settings,
    _zimage_settings, _qwen21_settings, _qwen21_t2i_settings,
    _anima_settings, _qwen_edit_settings) are checked too.

    The inversion in the middle is worth reading twice. `Field.name` is
    the handler parameter name and `fields` is submission order, so
    zipping the two reconstructs exactly the value bag the API would have
    produced from a form. If that invariant ever stops holding, this
    reconstruction is wrong and the comparison fails, which is the
    behaviour you want from a check whose whole premise is the invariant.
    """
    from ember.web import tabschema

    schema = next((s for s in tabschema.SCHEMAS
                   if s.handler.__name__ == name), None)
    if schema is None or settings is None:
        return
    values = {f.name: value for f, value in zip(schema.named(), args)}
    tail = schema.tail()
    if tail is not None:
        rest = args[len(schema.named()):]
        for index in range(tail.count()):
            for offset, part in enumerate(tail.parts):
                key = tail.value_key(index, part.name)
                values[key] = rest[index * len(tail.parts) + offset]
    ours = schema.settings(values)
    if ours != settings:
        keys = set(ours) | set(settings)
        rows = ["  %s: %r != %r" % (k, ours.get(k), settings.get(k))
                for k in sorted(keys) if ours.get(k) != settings.get(k)]
        raise SystemExit(
            "%s: tabschema.settings() does not match the blob the handler "
            "stores. Every preset on the licence server is in that shape."
            % name + "\n" + "\n".join(rows)
        )


def snapshot() -> dict:
    """Run every case and collect the workflows each one built."""
    modules = handler_modules()
    module = Namespace(modules)
    result = {}
    for name, args in cases(module).items():
        capture = Capture()
        patch(modules, capture)
        handler = getattr(module, CASE_HANDLERS.get(name, name))
        # Drained, not just started: a generator that is never iterated
        # builds nothing, and the batch counts above are 2 precisely so
        # that stopping early would show up as a missing workflow.
        statuses = []
        for yielded in handler(*args):
            row = yielded if isinstance(yielded, tuple) else (yielded,)
            statuses.append(next((v for v in row if isinstance(v, str)), ""))
        if not capture.workflows:
            raise SystemExit(
                "%s built no workflow — it bailed out before the seam. "
                "Last status: %s"
                % (name, statuses[-1] if statuses else "(nothing)")
            )
        check_settings(handler.__name__, args, capture.settings)
        result[name] = capture.workflows
    return result


def _walk(node, path=()):
    """Flatten a nested document to {dotted path: leaf}. See parity.py."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (str(index),))
    else:
        yield ".".join(path), node


def diff(baseline, current) -> list:
    """Human-readable differences, missing/added/changed, in that order."""
    old = dict(_walk(baseline))
    new = dict(_walk(current))
    lines = []
    for key in sorted(set(old) - set(new)):
        lines.append("  - %s = %r   (gone)" % (key, old[key]))
    for key in sorted(set(new) - set(old)):
        lines.append("  + %s = %r   (new)" % (key, new[key]))
    for key in sorted(set(old) & set(new)):
        if old[key] != new[key]:
            lines.append("  ~ %s: %r -> %r" % (key, old[key], new[key]))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze or verify the workflow dict every handler builds.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="compare against the committed snapshots and exit 1 on any "
             "difference",
    )
    parser.add_argument("--out", type=Path, default=SNAPSHOTS)
    args = parser.parse_args()

    from ember import features
    features.resolve([f.key for f in features.FEATURES])

    stub_comfy()
    current = snapshot()
    log.info("Captured %d handlers, %d workflows",
             len(current), sum(len(v) for v in current.values()))

    if not args.check:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, workflows in current.items():
            (args.out / ("%s.json" % name)).write_text(
                json.dumps(workflows, indent=2, ensure_ascii=False,
                           sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print("wrote %d snapshot(s) to %s" % (len(current), args.out))
        return

    failures = 0
    for name, workflows in current.items():
        path = args.out / ("%s.json" % name)
        if not path.exists():
            print("no snapshot at %s - run without --check first" % path)
            failures += 1
            continue
        lines = diff(json.loads(path.read_text(encoding="utf-8")), workflows)
        if lines:
            failures += 1
            print("%s: %d difference(s)" % (path.name, len(lines)))
            print("\n".join(lines))
    if failures:
        sys.exit(1)
    print("golden OK - %d handler(s) build the same workflows as %s"
          % (len(current), args.out.name))


if __name__ == "__main__":
    main()
