"""Krea 2 Edit V2 — the 🔷 Krea2 V2 Edit graph on the V2 edit LoRAs.

There is no builder of its own. build_v2_edit_workflow takes the edit
LoRA chain and the reference placement as arguments, and this tab passes
them; everything else in the graph is the 🔷 tab's, so the two cannot
drift. What this module adds is the readiness check, because the files
it waits for are different ones.
"""

from ember.pipelines.krea2_edit_v2.constants import EDIT_V2_FILES, EDIT_V2_MODES
from ember.pipelines.krea2_v2.constants import V2_NODE_REPOS
from ember.pipelines.krea2_v2.workflow import blocking_problems, status_notes
from ember.pipelines.krea2_v2_edit.workflow import (
    build_v2_edit_workflow,
    fit_size,
)
from ember.settings import MODELS_DIR

__all__ = ["build_v2_edit_workflow", "edit_v2_lora_available", "fit_size",
           "status"]


def edit_v2_lora_available(filename: str) -> bool:
    """True once that V2 edit LoRA has been downloaded."""
    return (MODELS_DIR / "loras" / filename).exists()


def status(feature, enabled=None, mode=None) -> tuple[bool, str]:
    """(ready, message) for the tab — what is missing, in plain words.

    The krea2_v2_edit status with the V2 LoRAs in place of v1.2. `mode` is
    an Edit LoRA choice: only the files that choice runs are required, so
    a key whose General LoRA failed to download can still restage people.
    Without it, both files are checked.
    """
    problems = blocking_problems(feature)
    files = ([f for f, _s in EDIT_V2_MODES[mode]] if mode in EDIT_V2_MODES
             else list(EDIT_V2_FILES))
    for filename in files:
        if not edit_v2_lora_available(filename):
            problems.append(f"the edit LoRA `{filename}` has not downloaded")
    if problems:
        if len(problems) > 2:
            listed = ", ".join(problems[:-1]) + " and " + problems[-1]
        else:
            listed = " and ".join(problems)
        return False, ("❌ Krea 2 Edit V2 cannot run — " + listed
                       + " yet. Restart the app so the download step can "
                         "fetch it.")
    notes = status_notes(feature, enabled)
    if notes:
        return True, "⚠️ Ready, but " + "; ".join(notes)
    packs = ", ".join(f"`{d}`" for d, _r, _c in V2_NODE_REPOS)
    return True, (f"✅ Ready. Node packs required: {packs}, "
                  "`comfyui-krea2edit` 2.0.0 or later")
