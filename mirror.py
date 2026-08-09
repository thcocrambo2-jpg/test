"""Mirror-first, pinned asset resolution.

Every weight this app downloads has two possible sources and one correct
answer:

    1. YOUR Hugging Face mirror   — tried first, at the pinned revision
    2. the original upstream repo — fallback, at the SAME pinned revision

Mirroring protects against deletion; pinning protects against change. The
second is the one that bites sooner, and it is the reason both sources must
resolve to the same revision: a fallback that silently returns *different*
weights than the mirror is worse than no fallback at all, because nothing
in the logs says the image you just generated came from other bytes.

Two data files drive this, both produced by scripts/mirror_to_hf.py:

    mirror_manifest.json   what lives in the mirror, and where
    PINS.json              the revision every source must resolve to

This module only answers questions about them. Nothing here downloads —
downloads.py and bootstrap.py do that, so the policy stays in one place and
the transport stays in theirs.
"""

import json
import os
from pathlib import Path

from config import HF_TOKEN, PROJECT_DIR, log

# ── Data files ────────────────────────────────────────────────────────────────
# A dev checkout keeps both under scripts/ (deliberately outside the package
# so Nuitka never sweeps the operator tooling into the shipped binary).
# build.sh copies them next to the code for the frozen build, so look in
# both places rather than making the two layouts disagree.
_SEARCH_DIRS = (PROJECT_DIR, PROJECT_DIR / "scripts")

MIRROR_USER = os.environ.get("KREA2_MIRROR_USER", "thcocrambo2")
# KREA2_NO_MIRROR=1 goes straight to upstream — the escape hatch for
# debugging a suspected bad mirror without editing anything.
MIRROR_ENABLED = not os.environ.get("KREA2_NO_MIRROR")


def _load(name: str) -> dict:
    for directory in _SEARCH_DIRS:
        path = directory / name
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.error("%s is present but unreadable (%s) — continuing "
                          "without it.", path, exc)
                return {}
    return {}


_MANIFEST = _load("mirror_manifest.json")
_PINS = _load("PINS.json")

# local path under MODELS_DIR → (repo key, path inside that repo)
_INDEX: dict[str, tuple[str, str]] = {}
# a filename config.py uses → the local path the mirror actually stores
_ALIASES: dict[str, str] = {}

for _entry in _MANIFEST.get("items", []):
    _INDEX[_entry["local"]] = (_entry["repo"], _entry["path_in_repo"])
    for _alias in _entry.get("aliases", []):
        _ALIASES[_alias] = _entry["local"]

_REPO_NAMES = {k: v["name"] for k, v in _MANIFEST.get("repos", {}).items()}

# Public mirrors need no credential at all — a customer pod can pull every
# mirrored weight anonymously. This is what makes HF_TOKEN optional again;
# it was mandatory only while the repos were private.
MIRROR_PUBLIC = bool(_MANIFEST.get("mirror_public"))

if not _MANIFEST:
    log.warning("No mirror_manifest.json found — every download will go "
                "straight upstream. This is the un-mirrored behaviour.")
if not _PINS:
    log.warning("No PINS.json found — downloads will not be pinned to a "
                "revision, so upstream can change weights under you.")


# ── Queries ───────────────────────────────────────────────────────────────────
def repo_id(repo_key: str) -> str | None:
    """`loras` → `youruser/krea2-loras`."""
    name = _REPO_NAMES.get(repo_key)
    return f"{MIRROR_USER}/{name}" if name else None


def resolve(relpath: str) -> str:
    """Map a filename config.py asks for onto what the mirror stores.

    Some blobs are referenced by two names — config.py's active entry and
    its commented twin disagree on spelling. Storing the bytes twice would
    be the obvious fix and the wrong one, so the mirror keeps one canonical
    copy and the alias is resolved here.
    """
    if relpath in _INDEX:
        return relpath
    alias_target = _ALIASES.get(Path(relpath).name)
    if alias_target:
        log.info("%s is an alias for %s in the mirror",
                 Path(relpath).name, alias_target)
        return alias_target
    return relpath


def location(relpath: str) -> tuple[str, str] | None:
    """(mirror repo id, path in repo) for a path under MODELS_DIR, or None.

    None means "not mirrored, go upstream" — which is the correct answer
    for the ~185 GB of Comfy-Org weights that are deliberately pinned-only.
    """
    if not MIRROR_ENABLED:
        return None
    entry = _INDEX.get(resolve(relpath))
    if not entry:
        return None
    repo = repo_id(entry[0])
    return (repo, entry[1]) if repo else None


def revision(upstream_repo: str) -> str | None:
    """The pinned revision for an upstream repo, or None if unpinned."""
    pin = _PINS.get(upstream_repo)
    return pin.get("sha") if isinstance(pin, dict) else None


def node_pin(dirname: str) -> dict:
    """{url, sha, tarball} for a custom-node pack, or {} if unpinned."""
    pin = _PINS.get(dirname)
    return pin if isinstance(pin, dict) else {}


def comfyui_sha() -> str | None:
    return node_pin("ComfyUI").get("sha")


def token() -> str | None:
    """Credential for mirror reads, or None when the mirror is public.

    Passing a customer's token to a public repo would work but is worse
    than not: a stale or malformed HF_TOKEN turns a download that needs no
    credential into a 401, so the mirror would appear to be missing files
    it plainly has.
    """
    return None if MIRROR_PUBLIC else HF_TOKEN


def describe() -> str:
    """One line for the startup log."""
    if not MIRROR_ENABLED:
        return "mirror disabled (KREA2_NO_MIRROR) — upstream only"
    if not _MANIFEST:
        return "no manifest — upstream only"
    return (f"mirror {MIRROR_USER} ({'public' if MIRROR_PUBLIC else 'private'})"
            f" · {len(_INDEX)} files across {len(_REPO_NAMES)} repos · "
            f"{len(_PINS) - 1} pins")
