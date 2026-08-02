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

The one thing that can override the licence is `Feature.enabled` in the
registry below, and it can only ever say *no*: a tab switched off there is
not built even for a licence that grants it. That is a build-time kill
switch for a tab that is written but not launched — changing it means
shipping a new binary, so it is nothing a licence can be talked into.

Order of operations is why resolve() is called rather than run on import:
the answer is not known until the license check in app.py has returned,
which is after most modules have been imported. Every caller in the app
asks through enabled() at call time, so a value that only exists after
step 2 still reaches all of them.
"""

from dataclasses import dataclass
from enum import Enum

from config import log


class Key(str, Enum):
    """The feature keys, as constants rather than loose strings.

    Every one of these is also a wire value — it appears in license
    documents, in the licence server's catalogue, and in the acquire
    response — so the *value* is the contract and must never change. The
    member name is local and can be renamed freely.

    A `str` subclass on purpose, not a bare Enum: the keys arrive from the
    server as plain strings and land in plain-string dicts, and inheriting
    from str means `Key.WAN_I2V == "wan_i2v"`, `d["wan_i2v"]` and
    `d[Key.WAN_I2V]` are all interchangeable. Nothing has to convert at the
    boundary, and a caller that still passes a literal keeps working.

    `str, Enum` rather than 3.11's StrEnum because build.sh compiles with
    whatever python3 the pod has; the explicit __str__ is what makes
    f-strings and log output print "wan_i2v" instead of "Key.WAN_I2V" on
    every version, which StrEnum would have given for free.
    """

    KREA_T2I = "krea_t2i"
    KREA_V2_T2I = "krea_v2_t2i"
    GALLERY = "gallery"
    KREA_EDIT = "krea_edit"
    KREA_V2_EDIT = "krea_v2_edit"
    KREA_INPAINT = "krea_inpaint"
    FACESWAP = "faceswap"
    FLUX_T2I = "flux_t2i"
    KLEIN_I2I = "klein_i2i"
    WAN_I2V = "wan_i2v"
    JSON_BATCH = "json_batch"
    COMMUNITY_PROMPTS = "community_prompts"

    __str__ = str.__str__


@dataclass(frozen=True)
class Feature:
    """One gateable tab.

    key     stable id — used in the license document's `features` array,
            in the license-validator's FEATURE registry, and in the logs.
            Never change one once it ships: a key is compiled into every
            binary that has gone out, so renaming one drops that tab for
            anyone on an older build. The label is the safe thing to
            reword. There is no alias map — an old key is simply unknown.
    label   the tab title *this build ships with*. The server sends one
            too, from the features collection, and that wins — see
            label_for(). This is the fallback: what the tab is called on a
            dry run, against a server too old to send labels, or for a key
            the catalogue does not describe. Keep it readable rather than
            treating it as dead weight; it is what a customer sees whenever
            the network answer is missing.
    default whether it is on for a license that names no features at all.
    enabled whether this build will construct the tab **at all**. False is
            a kill switch for a tab that is written but not launched, or
            one being withdrawn: no licence can turn it back on, so it
            costs nothing to leave the code in place while the feature
            waits. It is deliberately not the same lever as the licence
            server's `enabled` flag on the features collection — that one
            decides what the pricing page advertises and never touches an
            entitlement, this one decides what this binary can build. A
            licence granting a disabled feature is honoured for everything
            else it grants and logs that this one was dropped.
    needs   asset groups download_everything must fetch for this tab.

    `key` and `needs` are separate namespaces that happen to overlap. A key
    names a *tab*; a group names a *set of weights* several tabs can share,
    and downloads.py is keyed on the latter. Renaming a tab therefore never
    touches a download — which is why "krea_v2_t2i" needs the group still
    called "v2".
    """

    key: Key
    label: str
    default: bool = False
    enabled: bool = True
    needs: tuple[str, ...] = ()


# Asset groups rather than a per-feature file list, because several
# features share one set of weights: Single, Edit and Inpaint all run the
# same Krea 2 base models, and V2 shares only the text encoder with them.
# downloads.py works from the union of the groups the enabled features
# asked for, so enabling Edit on its own still fetches the base models it
# cannot run without, and enabling both Single and Edit fetches them once.
FEATURES = (
    Feature(Key.KREA_T2I, "🎨 Krea2", default=True,
            needs=("text_encoder", "krea2")),
    Feature(Key.KREA_V2_T2I, "🔶 Krea2 V2", default=True,
            needs=("text_encoder", "v2")),
    Feature(Key.GALLERY, "🖼️ Gallery", default=True),
    Feature(Key.KREA_EDIT, "✨ Krea2 Edit",
            needs=("text_encoder", "krea2", "edit_lora")),
    # The same instruction-edit recipe on the V2 pipeline, so it needs the
    # "v2" weights rather than "krea2" — and the edit LoRA, which is the
    # one thing the two edit tabs do share.
    Feature(Key.KREA_V2_EDIT, "🔷 Krea2 V2 Edit",
            needs=("text_encoder", "v2", "edit_lora")),
    Feature(Key.KREA_INPAINT, "🖌️ Krea2 Inpaint",
            needs=("text_encoder", "krea2")),
    Feature(Key.FACESWAP, "🎭 Face Swap", needs=("reactor",)),
    Feature(Key.FLUX_T2I, "🌊 Flux2D", needs=("flux",)),
    Feature(Key.KLEIN_I2I, "🧩 Klein Edit", needs=("klein",)),
    Feature(Key.WAN_I2V, "🎬 Wan Video", needs=("wan",)),
    # Runs whatever graph is pasted into it, so it has no assets of its
    # own — it is only useful alongside the tabs whose models it names.
    Feature(Key.JSON_BATCH, "📦 Krea2 Batch"),
    # Reads a collection on the licence server, so it needs no weights of
    # its own either. Like JSON Batch it is only useful next to the tabs
    # it loads prompts into (Krea2 and Krea2 V2) — the cards for a tab
    # this licence does not grant still render, they just cannot be used.
    Feature(Key.COMMUNITY_PROMPTS, "🌟 Prompt Library", default=True),
)

BY_KEY = {feature.key: feature for feature in FEATURES}

# Every key in the enum must have a Feature behind it, or enabled() would
# quietly answer False for a tab that exists — the exact silent-empty-UI
# failure _state() refuses to allow. Checked at import because it can only
# ever be broken by editing this file.
_missing = [key for key in Key if key not in BY_KEY]
if _missing:
    raise RuntimeError(
        f"features.Key has no entry in FEATURES: {', '.join(_missing)}"
    )

# Populated by resolve(). Empty until then, which is a state the readers
# below refuse to answer from rather than guess at — see _state().
_enabled: dict[str, bool] = {}
_resolved = False
# Tab titles the server sent, by feature key. Empty on a dry run and
# against a server too old to send them, which is why every read goes
# through label_for() and falls back to the registry rather than reading
# this directly.
_labels: dict[str, str] = {}


def resolve(entitlements: list[str] | None,
            labels: dict[str, str] | None = None) -> None:
    """Work out which features are on from the license, and cache it.

    `entitlements` is the license document's `features` array as returned
    by the license server (licensing.entitlements()). None means the
    document said nothing, and the registry defaults apply.

    `labels` is the matching tab titles from the server's features
    collection (licensing.feature_labels()), which is the source of truth
    for what a tab is called — see label_for(). Optional because the dry
    run resolves features without ever contacting the server.

    Unknown keys are warned about and ignored rather than rejected: the
    server and this registry deploy separately, so a key issued for a tab
    this build does not have yet must not stop the app from starting.

    A feature marked `enabled=False` in the registry is forced off at the
    end, whatever the licence said — see Feature.enabled.
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

    # Applied after both branches, so it holds for the defaults as much as
    # for a licence: a tab switched off in this build is off, and saying so
    # in the log is the only way the difference between "not granted" and
    # "not shipped yet" is visible from a pod.
    withheld = [feature.key for feature in FEATURES
                if not feature.enabled and state.get(feature.key)]
    if withheld:
        log.warning(
            "Feature(s) %s are granted but disabled in this build — not "
            "building their tabs.", ", ".join(withheld),
        )
    for feature in FEATURES:
        if not feature.enabled:
            state[feature.key] = False

    _enabled.clear()
    _enabled.update(state)

    # Blank and non-string values are dropped rather than stored, so
    # label_for() never has to re-check them and a feature row with an
    # empty name in Atlas falls back to the registry instead of rendering
    # a nameless tab.
    _labels.clear()
    for key, label in (labels or {}).items():
        if isinstance(label, str) and label.strip():
            _labels[str(key)] = label.strip()

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


def enabled(key: str | Key) -> bool:
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


def label_for(key: str | Key) -> str:
    """What to title this feature's tab, server first.

    The features collection on the licence server is the source of truth,
    so a tab can be renamed in Atlas and every pod picks the new name up
    on its next start with nothing rebuilt. Three things have to keep
    working when that answer is not there, and all of them land on the
    registry label:

      * the dry run (`--features`), which never calls the server at all
      * a server too old to send `feature_info`
      * a key the catalogue does not describe, which is the same
        deploy-skew case the rest of this module already tolerates

    Unknown keys are title-cased rather than raising. This is called while
    the Blocks tree is being built, and a tab with an ugly name beats a UI
    that will not construct.
    """
    label = _labels.get(key)
    if label:
        return label
    known = BY_KEY.get(key)
    if known is not None:
        return known.label
    return key.replace("_", " ").title()


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


def enabled_keys() -> tuple[Key, ...]:
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
