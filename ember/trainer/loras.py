"""The LoRAs this pod has trained, offered on the Krea tabs.

A trained LoRA is copied to MODELS_DIR/loras/face_<name>.safetensors
(run.install). From then on it is a row on the Krea2 V2 and Krea2 LoRA
stacks on this pod — catalog.add_extra — so it can be tried in the real
tab, with any preset or token, before it is published. Published and
granted to this licence, the catalogue lists it itself on the next start
and its row takes over (catalog.feature_loras drops the extra).

Only for a licence granting `face_lora`: the routes that call this are
registered only then (web/routes/face_lora.py).
"""

import json
import re
from pathlib import Path

from ember.licensing import catalog
from ember.settings import BASE_DIR, MODELS_DIR

# The tabs a face LoRA is offered on, here and when it is published
# (license-validator/src/privateLoras.js DEFAULT_FEATURES).
FEATURES = ("krea_v2_t2i", "krea_t2i")
PREFIX = "face_"
RUNS = BASE_DIR / "trainer" / "runs"


def lora_id(file_name: str) -> str:
    """face_sayara.safetensors -> face-sayara: the id it is published as,
    so a preset or token made with it before publishing still names it."""
    stem = Path(file_name).stem.lower()
    return re.sub(r"[^a-z0-9-]+", "-", stem).strip("-")[:63]


def _trigger(run_name: str) -> str:
    try:
        state = json.loads((RUNS / run_name / "state.json").read_text(encoding="utf-8"))
        return str((state.get("options") or {}).get("trigger") or "")
    except (OSError, ValueError):
        return ""


def offer(file_name: str, trigger: str | None = None) -> None:
    """Put one trained file on the Krea tabs' LoRA lists."""
    stem = Path(file_name).stem
    run_name = stem[len(PREFIX):] if stem.startswith(PREFIX) else stem
    if trigger is None:
        trigger = _trigger(run_name.split("-step")[0])
    catalog.add_extra(FEATURES, catalog.Lora(
        id=lora_id(file_name),
        name="🎭 %s (trained here)" % run_name,
        file=file_name,
        source={"kind": "local"},
        mirror=None,
        default_strength=1.0,
        trigger=trigger,
    ))


def offer_all() -> int:
    """Every face_* file already on disk — run at startup. How many."""
    files = sorted((MODELS_DIR / "loras").glob(PREFIX + "*.safetensors"))
    for path in files:
        offer(path.name)
    return len(files)
