"""Feature flags — which tabs are built and which assets are downloaded.

Every tab is a feature with a stable key. A feature that is off costs
nothing at all: its tab is never constructed (so its handlers are never
registered on Gradio's HTTP API either), its custom node packs are not
installed, and its weights are not downloaded.

**The license decides, and nothing else.** The set comes from the
`features` array on the license document, returned by the license server
in the acquire response. There is no environment variable that can switch
a tab on or off — a licence one variable away from being bypassed is not
a licence, and an env override that could only turn things *off* still
left the download bill and the entitlement disagreeing about what a pod
was for.

    features: ["krea_t2i", "gallery", "wan_i2v"]   exactly those tabs
    features: []                                   nothing
    field absent / null                            the defaults below

The server resolves that array from the license's plan before sending it,
so what arrives here is always a flat list of keys. This module knows
nothing about plans, prices or tiers, and should not learn.

The absent case exists for keys issued before entitlements did. It is a
fallback, not a mode: it logs a warning, because "whatever this build
happens to default to" is a moving target and every real key should say
what it grants.

Order of operations is why resolve() is called rather than run on import:
the answer is not known until the license check in app.py has returned,
which is after most modules have been imported. Every caller in the app
asks through enabled() at call time, so a value that only exists after
step 2 still reaches all of them.
"""

from dataclasses import dataclass

from config import log


@dataclass(frozen=True)
class Feature:
    """One gateable tab.

    key     stable id — used in the license document's `features` array,
            in the license-validator's FEATURE registry, and in the logs.
            Never change one once it ships: a key is compiled into every
            binary that has gone out, so renaming one drops that tab for
            anyone on an older build. The label is the safe thing to
            reword. There is no alias map — an old key is simply unknown.
    label   the tab title, so ui.py and the startup log agree on naming.
    default whether it is on for a license that names no features at all.
    needs   asset groups download_everything must fetch for this tab.

    `key` and `needs` are separate namespaces that happen to overlap. A key
    names a *tab*; a group names a *set of weights* several tabs can share,
    and downloads.py is keyed on the latter. Renaming a tab therefore never
    touches a download — which is why "krea_v2_t2i" needs the group still
    called "v2".
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
    Feature("krea_t2i", "Single / Simple Batch", default=True,
            needs=("text_encoder", "krea2")),
    Feature("krea_v2_t2i", "🔶 Krea 2 V2", default=True,
            needs=("text_encoder", "v2")),
    Feature("gallery", "Gallery", default=True),
    Feature("krea_edit", "✨ Edit (Instruction)",
            needs=("text_encoder", "krea2", "edit_lora")),
    Feature("krea_inpaint", "Inpaint / Img2Img",
            needs=("text_encoder", "krea2")),
    Feature("faceswap", "🎭 Face Swap (ReActor)", needs=("reactor",)),
    Feature("flux_t2i", "🌊 Flux 2", needs=("flux",)),
    Feature("klein_i2i", "🧩 Klein Edit", needs=("klein",)),
    Feature("wan_i2v", "🎬 Video (Wan 2.2)", needs=("wan",)),
    # Runs whatever graph is pasted into it, so it has no assets of its
    # own — it is only useful alongside the tabs whose models it names.
    Feature("json_batch", "JSON Advanced Batch"),
)

BY_KEY = {feature.key: feature for feature in FEATURES}

# Populated by resolve(). Empty until then, which is a state the readers
# below refuse to answer from rather than guess at — see _state().
_enabled: dict[str, bool] = {}
_resolved = False


def resolve(entitlements: list[str] | None) -> None:
    """Work out which features are on from the license, and cache it.

    `entitlements` is the license document's `features` array as returned
    by the license server (licensing.entitlements()). None means the
    document said nothing, and the registry defaults apply.

    Unknown keys are warned about and ignored rather than rejected: the
    server and this registry deploy separately, so a key issued for a tab
    this build does not have yet must not stop the app from starting.
    """
    global _resolved

    if entitlements is None:
        state = {feature.key: feature.default for feature in FEATURES}
        log.warning(
            "This license does not list any features — falling back to the "
            "built-in defaults (%s). Set a `features` array on the license "
            "to control this.",
            ", ".join(key for key, on in state.items() if on),
        )
    else:
        state = {feature.key: False for feature in FEATURES}
        for raw in entitlements:
            key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
            if not key:
                continue
            if key not in state:
                log.warning(
                    "This license grants unknown feature %r — ignoring it. "
                    "This build knows: %s", raw, ", ".join(BY_KEY),
                )
                continue
            state[key] = True

    _enabled.clear()
    _enabled.update(state)
    _resolved = True


def _state() -> dict[str, bool]:
    """The resolved flags, or a loud failure if nothing resolved them.

    Raising beats returning "everything off": the symptom of the latter is
    a pod that boots, downloads nothing and shows an empty UI, which reads
    as a licensing problem and is really a call-order bug in app.py.
    """
    if not _resolved:
        raise RuntimeError(
            "features.resolve() has not been called — the license "
            "entitlements are not known yet. app.py must call it straight "
            "after licensing.acquire_or_exit()."
        )
    return _enabled


def enabled(key: str) -> bool:
    """True if `key` is switched on. Unknown keys are off, and say so.

    Call this rather than caching the result in a module constant:
    entitlements are resolved after the license check, i.e. after most
    modules have already been imported, and a constant captured at import
    time would still be holding a default.
    """
    state = _state()
    if key not in BY_KEY:
        log.warning("Unknown feature %r treated as disabled", key)
        return False
    return state.get(key, False)


def assets() -> frozenset[str]:
    """The union of asset groups the enabled features need downloaded."""
    state = _state()
    return frozenset(
        group
        for feature in FEATURES
        if state.get(feature.key)
        for group in feature.needs
    )


def needs(group: str) -> bool:
    """True if any enabled feature needs this asset group."""
    return group in assets()


def enabled_keys() -> tuple[str, ...]:
    """Enabled feature keys, in registry order."""
    state = _state()
    return tuple(f.key for f in FEATURES if state.get(f.key))


def summary() -> str:
    """One line naming what is on and what is off, for the startup log."""
    state = _state()
    on = [f.key for f in FEATURES if state.get(f.key)]
    off = [f.key for f in FEATURES if not state.get(f.key)]
    return (f"on: {', '.join(on) or '(nothing)'}"
            f" · off: {', '.join(off) or '(nothing)'}")
