"""Feature flags — which tabs are built and which assets are downloaded.

Every tab is a feature with a stable key. A feature that is off costs
nothing at all: its tab is never constructed (so its handlers are never
registered on Gradio's HTTP API either), its custom node packs are not
installed, and its weights are not downloaded.

Defaults are deliberately narrow — only `single`, `v2` and `gallery` are
on. That is what a bare pod should boot with; everything else is opt-in,
so nobody pays for Wan's ~49 GB or Flux's ~57 GB without asking. This is
the opposite of the old KREA2_DISABLE_* scheme, where a pod with no
environment variables downloaded everything.

One variable turns things on:

    KREA2_FEATURES="wan,flux"     defaults, plus Wan and Flux
    KREA2_FEATURES="-v2"          defaults, without Krea 2 V2
    KREA2_FEATURES="none,wan"     Wan and nothing else
    KREA2_FEATURES="all"          everything

Tokens are applied left to right, which is what makes `none` first the
way to spell "exactly this set". Per-feature variables
(KREA2_ENABLE_WAN / KREA2_DISABLE_WAN) are applied afterwards and win —
that keeps a one-off override readable in a RunPod template without
having to restate the whole list.

resolve() is the seam for phase 2. Once entitlements come from the
license server they are passed here and nothing else has to change,
because every caller in the app asks through enabled() at call time
rather than importing a constant — a value that is only known after the
license check still reaches every consumer.
"""

import os
from dataclasses import dataclass

from config import log

ENV_LIST = "KREA2_FEATURES"


@dataclass(frozen=True)
class Feature:
    """One gateable tab.

    key     stable id — used in KREA2_FEATURES, in the license document
            later, and in the logs. Never change one once it ships; the
            label is the thing that is safe to reword.
    label   the tab title, so ui.py and the startup log agree on naming.
    default whether it is on when nothing says otherwise.
    needs   asset groups download_everything must fetch for this tab.
    """

    key: str
    label: str
    default: bool = False
    needs: tuple[str, ...] = ()


# Asset groups rather than a per-feature file list, because several
# features share one set of weights: Single, Edit and Inpaint all run the
# same Krea 2 base models, and V2 shares only the text encoder with them.
# downloads.py works from the union of the groups the enabled features
# asked for, so enabling Edit on its own still fetches the base models it
# cannot run without, and enabling both Single and Edit fetches them once.
FEATURES = (
    Feature("single", "Single / Simple Batch", default=True,
            needs=("text_encoder", "krea2")),
    Feature("v2", "🔶 Krea 2 V2", default=True,
            needs=("text_encoder", "v2")),
    Feature("gallery", "Gallery", default=True),
    Feature("edit", "✨ Edit (Instruction)",
            needs=("text_encoder", "krea2", "edit_lora")),
    Feature("inpaint", "Inpaint / Img2Img",
            needs=("text_encoder", "krea2")),
    Feature("faceswap", "🎭 Face Swap (ReActor)", needs=("reactor",)),
    Feature("flux", "🌊 Flux 2", needs=("flux",)),
    Feature("wan", "🎬 Video (Wan 2.2)", needs=("wan",)),
    # Runs whatever graph is pasted into it, so it has no assets of its
    # own — it is only useful alongside the tabs whose models it names.
    Feature("json_batch", "JSON Advanced Batch"),
)

BY_KEY = {feature.key: feature for feature in FEATURES}

# Populated by resolve(), which runs on import (below) so that importing
# this module is enough to read flags — no caller has to remember to
# initialise it first.
_enabled: dict[str, bool] = {}


def _env_flag(name: str) -> bool | None:
    """Tri-state read of a per-feature override: True, False or unset.

    Unset and empty both mean "no opinion" so that KREA2_ENABLE_WAN= in a
    template behaves like the variable not being there at all, which is
    how RunPod renders a field someone cleared.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _apply_list(state: dict[str, bool], raw: str) -> None:
    """Apply the KREA2_FEATURES token list to `state`, left to right."""
    for token in raw.replace(";", ",").split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token == "all":
            state.update({key: True for key in state})
            continue
        if token == "none":
            state.update({key: False for key in state})
            continue
        turn_on = True
        if token[0] in "+-":
            turn_on = token[0] == "+"
            token = token[1:].strip()
        # Tolerate the shapes people actually type for the two-word keys.
        key = token.replace("-", "_").replace(" ", "_")
        if key not in state:
            log.warning(
                "%s lists unknown feature %r — ignoring it. Known features: %s",
                ENV_LIST, token, ", ".join(BY_KEY),
            )
            continue
        state[key] = turn_on


def resolve(entitlements: dict | None = None) -> None:
    """Work out which features are on and cache the answer.

    Order, lowest precedence first: the registry defaults, then the
    KREA2_FEATURES list, then the per-feature variables.

    `entitlements` is unused in phase 1 and exists so that wiring the
    license server later is a change to this function alone. When it
    arrives it goes *above* the env layer for turning things on and below
    it for turning them off: env must be able to switch a tab off (a
    customer trimming their own pod is harmless) but never on, or the
    licence is one environment variable away from being bypassed.
    """
    state = {feature.key: feature.default for feature in FEATURES}

    raw_list = os.environ.get(ENV_LIST)
    if raw_list:
        _apply_list(state, raw_list)

    for key in state:
        override = _env_flag(f"KREA2_ENABLE_{key.upper()}")
        if override is not None:
            state[key] = override
        if _env_flag(f"KREA2_DISABLE_{key.upper()}"):
            state[key] = False

    _enabled.clear()
    _enabled.update(state)


def enabled(key: str) -> bool:
    """True if `key` is switched on. Unknown keys are off, and say so.

    Call this rather than caching the result in a module constant: phase 2
    resolves entitlements after the license check, i.e. after most modules
    have already been imported, and a constant captured at import time
    would still be holding the default.
    """
    if key not in BY_KEY:
        log.warning("Unknown feature %r treated as disabled", key)
        return False
    return _enabled.get(key, False)


def assets() -> frozenset[str]:
    """The union of asset groups the enabled features need downloaded."""
    return frozenset(
        group
        for feature in FEATURES
        if _enabled.get(feature.key)
        for group in feature.needs
    )


def needs(group: str) -> bool:
    """True if any enabled feature needs this asset group."""
    return group in assets()


def enabled_keys() -> tuple[str, ...]:
    """Enabled feature keys, in registry order."""
    return tuple(f.key for f in FEATURES if _enabled.get(f.key))


def summary() -> str:
    """One line naming what is on and what is off, for the startup log."""
    on = [f.key for f in FEATURES if _enabled.get(f.key)]
    off = [f.key for f in FEATURES if not _enabled.get(f.key)]
    return (f"on: {', '.join(on) or '(nothing)'}"
            f" · off: {', '.join(off) or '(nothing)'}")


resolve()
