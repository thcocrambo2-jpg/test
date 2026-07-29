#!/usr/bin/env python3
"""One-shot mirror: pod disk → your Hugging Face account.

Operator tool, not part of the shipped app. The intended run is:

    1. Boot a pod with every feature on:
           KREA2_FEATURES="krea2 edit v2 flux wan faceswap" python3 app.py
       Let it finish downloading, then stop it.
    2. python3 scripts/mirror_to_hf.py --dry-run     # read the checklist
    3. python3 scripts/mirror_to_hf.py               # top up + upload

Step 1 leaves ~200 GB on disk. This script mirrors only the ~18 GB that
is actually at risk of disappearing — the CivitAI LoRAs, the community HF
repos and the GitHub node packs. The Comfy-Org repos stay upstream: they
are org-backed, built to serve that traffic, and in Flux 2's case carry a
licence that is better left un-redistributed. What they need instead is a
pinned revision, which this script records in PINS.json.

Everything here is idempotent and resumable:

  • a file already on disk is not re-downloaded (the app's own fetchers
    key on the destination path),
  • a file already in the mirror repo at the same size is not re-uploaded,
  • an interrupted run picks up where it stopped.

Extras — the commented-out entries in config.py — are downloaded here
even though the app never asked for them, so that enabling one later is a
config edit rather than a hope that CivitAI still has it.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# The app's modules live one level up; this script is deliberately outside
# the package so build.sh / Nuitka never sweep it into the shipped binary.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from huggingface_hub import HfApi  # noqa: E402
from huggingface_hub.utils import HfHubHTTPError  # noqa: E402

import config  # noqa: E402
import downloads  # noqa: E402
from config import (  # noqa: E402
    ABLITERATED_ENCODER_FILE,
    CIVITAI_LORAS,
    COMFY_DIR,
    EDIT_LORA_FILE,
    KREA2EDIT_NODES_REPO,
    MODELS_DIR,
    REACTOR_INSIGHTFACE_PACK,
    REACTOR_NSFW_DIR,
    V2_LORA_STACK,
    V2_NODE_REPOS,
    V2_VAE_FILE,
    log,
)

# ── Credentials ───────────────────────────────────────────────────────────────
# A *write*-scoped token. Prefer the environment variable — this file is in
# git, and a token pasted below is a token that stays in the history even
# after you delete the line. If you do paste one, revoke it when the
# migration is done.
HF_WRITE_TOKEN = os.environ.get("HF_WRITE_TOKEN") or ""     # ← paste here if you must

# Mirror repos are private by default: several of these weights carry
# no-redistribution terms, and private keeps the migration from doubling as
# a publication. Flip with --public if you have decided otherwise.
DEFAULT_PRIVATE = True

UPLOAD_RETRIES = 4

# The mirror's own memory. config.py says what the app uses *today*; this
# file says what has ever been mirrored, and it only ever grows. Deriving
# from config alone loses an entry the moment it is deleted rather than
# commented; a hand-kept list alone silently misses whatever you forget to
# add — and the entry you just added is the one least likely to still be
# on CivitAI next year. The union has neither failure mode.
# Commit it: it is the record of what your mirror is supposed to contain.
CATALOGUE_PATH = Path(__file__).resolve().parent / "mirror_catalogue.json"

# Action labels — the checklist prints them and the summary filters on them.
ACT_SKIP = "skip (in repo)"
ACT_UPLOAD = "UPLOAD"
ACT_REUPLOAD = "RE-UPLOAD (size differs)"
ACT_FETCH = "DOWNLOAD+UPLOAD"
ACT_MISSING = "MISSING"


# ── Repo layout ───────────────────────────────────────────────────────────────
# key → (repo name under your account, what it holds)
REPOS = {
    "loras":    ("krea2-loras",    "CivitAI LoRAs — highest churn, mirrored first"),
    "encoders": ("krea2-encoders", "Merged abliterated Qwen3-VL text encoder"),
    "assets":   ("krea2-assets",   "Community HF weights (edit LoRA, Wan 2.1 VAE)"),
    "reactor":  ("krea2-reactor",  "Face-swap ONNX/pth + buffalo_l + NSFW ViT"),
    "nodes":    ("krea2-nodes",    "Pinned custom-node tarballs"),
}


# ── Extras: things config.py has commented out ────────────────────────────────
# Only version ids that are NOT already pulled by an active entry. Six of
# the nine commented CIVITAI_LORAS share a version id with a live
# V2_LORA_STACK entry and are covered by ALIASES below instead.
EXTRA_CIVITAI_LORAS = [
    (3084588, "Krea2_NSFW_plus.safetensors"),
    (3075498, "nicegirls_krea2.safetensors"),
    (3066973, "Krea2-realism-V1.safetensors"),
]

# Same bytes, two filenames. config.py's active entry and its commented
# twin disagree on what to call the file, so uploading both would store the
# blob twice. The mirror keeps the canonical name and ships this map, which
# the download layer resolves when a config entry asks for an alias.
ALIASES = {
    # canonical (in the mirror)                 alias (commented in config.py)
    "krea2filterbypass3.safetensors":    "Krea2FilterBypass_3vector.safetensors",
    "RealisticSnapshotKrea2.safetensors": "Realistic_Snapshot_Krea2_v0.5.safetensors",
    "snofs_krea_v1.safetensors":         "snofs_krea_v1_1.safetensors",
}

# Extra ReActor restorer — commented in REACTOR_HF_FILES, ~340 MB, and it
# shows up in the Face Swap dropdown the moment the entry is uncommented.
EXTRA_REACTOR_FILES = [
    ("models/facerestore_models/GFPGANv1.4.pth",
     "facerestore_models/GFPGANv1.4.pth"),
]

# The commented KREA2_MODELS entry (FinePorn V2, CivitAI 3118978) is a
# ~12.2 GB UNet. It is CivitAI-hosted, so by the risk rule it belongs in
# the mirror — but it nearly doubles the loras repo and the app does not
# currently use it. Off by default; --include-finepn turns it on.
OPTIONAL_CIVITAI_MODELS = [
    (3118978, "Krea2_FinePornV2_FP8.safetensors", "diffusion_models"),
]

# Repos whose HEAD this script only *records*. Pinning these is what stops
# an upstream force-push from changing your product; mirroring them is not
# worth the storage.
UPSTREAM_TO_PIN = {
    "Comfy-Org/Krea-2": "hf",
    "Comfy-Org/Wan_2.2_ComfyUI_Repackaged": "hf",
    "Comfy-Org/flux2-dev": "hf",
    "Gourieff/ReActor": "hf-dataset",
}


# ── Item model ────────────────────────────────────────────────────────────────
@dataclass
class Item:
    """One file to mirror. `fetch` is how to obtain it if it is missing."""
    local: Path
    repo_key: str
    path_in_repo: str
    fetch: Callable[[], None] | None = None
    note: str = ""
    # Filled in during planning.
    present: bool = field(default=False, init=False)
    size: int = field(default=0, init=False)
    remote_size: int | None = field(default=None, init=False)

    @property
    def action(self) -> str:
        if not self.present:
            return ACT_FETCH if self.fetch else ACT_MISSING
        if self.remote_size == self.size:
            return ACT_SKIP
        if self.remote_size is not None:
            return ACT_REUPLOAD
        return ACT_UPLOAD


# config.py writes CivitAI entries in exactly two shapes, and a commented
# line is the same text behind a "#" — so scanning the *source* finds the
# disabled ones too, which importing the module never can.
#   CIVITAI_LORAS / FLUX_CIVITAI_LORAS:  (3070702, "name.safetensors")
#   V2_LORA_STACK:                       ("name.safetensors", 0.4, True, 3065628)
_RE_ID_FIRST = re.compile(r'\(\s*(\d{5,})\s*,\s*"([^"]+\.safetensors)"')
_RE_NAME_FIRST = re.compile(
    r'\(\s*"([^"]+\.safetensors)"\s*,\s*[\d.]+\s*,\s*(?:True|False)\s*,'
    r'\s*(\d{5,})\s*\)')

# Commented-out *examples* in config.py, which the scan cannot tell from
# commented-out real entries. Chasing these produces a download failure
# and a confusing MISSING row for a file that was never meant to exist.
SCAN_IGNORE_IDS = {
    1234567,    # FLUX_CIVITAI_LORAS placeholder: "some_flux2_lora.safetensors"
}


def scan_config_civitai() -> dict[int, str]:
    """Every CivitAI version id mentioned in config.py, enabled or not.

    The point is that commenting a LoRA out must not quietly drop it from
    the mirror — by the time you uncomment it, CivitAI may not have it.
    Additive only: the scan can add items to mirror, never remove them.
    """
    source = (config.PROJECT_DIR / "config.py").read_text(encoding="utf-8")
    found: dict[int, str] = {}
    for version_id, filename in _RE_ID_FIRST.findall(source):
        found.setdefault(int(version_id), filename)
    for filename, version_id in _RE_NAME_FIRST.findall(source):
        found.setdefault(int(version_id), filename)
    for placeholder in SCAN_IGNORE_IDS:
        found.pop(placeholder, None)
    return found


def load_catalogue() -> dict[int, str]:
    """Everything this mirror has ever been asked to hold."""
    if not CATALOGUE_PATH.exists():
        return {}
    try:
        raw = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
        return {int(k): v for k, v in raw.get("civitai_loras", {}).items()}
    except Exception as exc:
        # Better to mirror the config-derived set than to abort: a corrupt
        # catalogue costs coverage of deleted entries, nothing else.
        log.warning("Could not read %s (%s) — continuing without it.",
                    CATALOGUE_PATH.name, exc)
        return {}


def save_catalogue(entries: dict[int, str]) -> None:
    """Write the union back. Additive by construction — nothing is dropped."""
    payload = {
        "_note": ("Every CivitAI version id this mirror holds. Union of "
                  "config.py (active + commented) and previous runs. Only "
                  "grows — an id stays here after config.py stops "
                  "mentioning it, which is the point. Commit this file."),
        "civitai_loras": {str(k): entries[k] for k in sorted(entries)},
    }
    CATALOGUE_PATH.write_text(json.dumps(payload, indent=2) + "\n",
                              encoding="utf-8")


def _dedup_civitai(scan: bool = True) -> tuple[list[tuple[int, str]],
                                               list[int], list[int]]:
    """Every CivitAI LoRA the mirror should hold, each version id once.

    Three sources, unioned, in priority order — the first to claim a
    version id names the file, so the *enabled* filename wins when the
    same blob appears under two names (the direction ALIASES is keyed in):

        1. config.py's live lists   — what the app uses right now
        2. config.py's source text  — plus whatever is commented out
        3. mirror_catalogue.json    — plus whatever it ever used

    Returns the plan, the ids only the source scan found, and the ids only
    the catalogue remembers, so the checklist can distinguish them.
    """
    seen: dict[int, str] = {}
    for filename, _s, _e, version_id in V2_LORA_STACK:
        if version_id is not None:
            seen.setdefault(version_id, filename)
    for version_id, filename in CIVITAI_LORAS:
        seen.setdefault(version_id, filename)
    for version_id, filename in EXTRA_CIVITAI_LORAS:
        seen.setdefault(version_id, filename)
    live = set(seen)

    if scan:
        try:
            for version_id, filename in scan_config_civitai().items():
                seen.setdefault(version_id, filename)
        except Exception as exc:
            # Losing the scan costs coverage of commented entries, not the
            # migration — the explicit lists above still stand.
            log.warning("Could not scan config.py for commented CivitAI "
                        "entries (%s) — mirroring the active lists only.", exc)
    commented = sorted(set(seen) - live)

    for version_id, filename in load_catalogue().items():
        seen.setdefault(version_id, filename)
    retired = sorted(set(seen) - live - set(commented))

    save_catalogue(seen)
    return [(vid, name) for vid, name in seen.items()], commented, retired


def _origin_note(version_id: int, commented: list[int],
                 retired: list[int]) -> str:
    """Why this id is in the plan — shown in the checklist's note column."""
    if version_id in commented:
        return " · commented in config"
    if version_id in retired:
        return " · catalogue only"
    return ""


def _expand_dir(root: Path, repo_key: str, prefix: str) -> list[Item]:
    """Turn a directory into one Item per file.

    Folder-level uploads would defeat the per-file skip logic, so every
    directory (buffalo_l, the NSFW detector) is flattened at plan time.

    An absent directory contributes no items, which would silently drop it
    from the checklist rather than reporting it — so say so out loud. The
    usual cause is booting without the feature that fetches it.
    """
    if not root.is_dir():
        log.warning("directory %s is absent — nothing from it will be "
                    "mirrored (was the owning feature enabled at boot?)",
                    root)
        return []
    items = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = path.relative_to(root).as_posix()
            items.append(Item(path, repo_key, f"{prefix}/{rel}"))
    return items


def build_plan(args) -> list[Item]:
    """The checklist. Nothing is downloaded or uploaded here."""
    items: list[Item] = []

    # ── CivitAI LoRAs ────────────────────────────────────────────────────
    loras, commented, retired = _dedup_civitai(scan=not args.no_scan_config)
    if commented:
        log.info("config.py source scan added %d commented-out CivitAI "
                 "entr%s: %s", len(commented),
                 "y" if len(commented) == 1 else "ies",
                 ", ".join(str(v) for v in commented))
    if retired:
        log.info("%s remembers %d entr%s config.py no longer mentions at "
                 "all: %s", CATALOGUE_PATH.name, len(retired),
                 "y" if len(retired) == 1 else "ies",
                 ", ".join(str(v) for v in retired))
    for version_id, filename in loras:
        origin = _origin_note(version_id, commented, retired)
        items.append(Item(
            local=MODELS_DIR / "loras" / filename,
            repo_key="loras",
            path_in_repo=f"loras/{filename}",
            fetch=(lambda v=version_id, f=filename:
                   downloads.fetch_civitai_file(v, f)),
            note=f"CivitAI v{version_id}{origin}",
        ))

    if args.include_finepn:
        for version_id, filename, subdir in OPTIONAL_CIVITAI_MODELS:
            items.append(Item(
                local=MODELS_DIR / subdir / filename,
                repo_key="loras",
                path_in_repo=f"{subdir}/{filename}",
                fetch=(lambda v=version_id, f=filename, s=subdir:
                       downloads.fetch_civitai_file(v, f, subdir=s)),
                note=f"CivitAI v{version_id} · ~12.2 GB",
            ))

    # ── Text encoder ─────────────────────────────────────────────────────
    # The *merged* file, not the upstream shards: mirroring the finished
    # artifact deletes the download-shards-and-merge step from every cold
    # boot (downloads.py fetch_abliterated_encoder).
    items.append(Item(
        local=MODELS_DIR / "text_encoders" / ABLITERATED_ENCODER_FILE,
        repo_key="encoders",
        path_in_repo=f"text_encoders/{ABLITERATED_ENCODER_FILE}",
        fetch=downloads.fetch_abliterated_encoder,
        note="merged — skips the runtime merge",
    ))

    # ── Community HF weights ─────────────────────────────────────────────
    items.append(Item(
        local=MODELS_DIR / "loras" / EDIT_LORA_FILE,
        repo_key="assets",
        path_in_repo=f"loras/{EDIT_LORA_FILE}",
        fetch=downloads.fetch_edit_lora,
        note="conradlocke/krea2-identity-edit",
    ))
    items.append(Item(
        local=MODELS_DIR / "vae" / V2_VAE_FILE,
        repo_key="assets",
        path_in_repo=f"vae/{V2_VAE_FILE}",
        fetch=(lambda: downloads.fetch_hf_file_to(
            config.V2_VAE_HF_REPO, config.V2_VAE_HF_PATH,
            MODELS_DIR / "vae" / V2_VAE_FILE)),
        note="wangkanai/wan21-vae",
    ))

    # ── ReActor ──────────────────────────────────────────────────────────
    for relpath, localpath in config.REACTOR_HF_FILES + EXTRA_REACTOR_FILES:
        items.append(Item(
            local=MODELS_DIR / localpath,
            repo_key="reactor",
            path_in_repo=localpath,
            fetch=(lambda r=relpath, lp=localpath: downloads.fetch_dataset_file(
                config.REACTOR_HF_REPO, r, MODELS_DIR / lp)),
            note="Gourieff/ReActor (dataset)",
        ))
    # buffalo_l, unpacked — mirroring the ONNX files rather than the zip
    # also retires the "some copies nest, some are flat" workaround.
    items += _expand_dir(
        MODELS_DIR / "insightface" / "models" / REACTOR_INSIGHTFACE_PACK,
        "reactor", f"insightface/models/{REACTOR_INSIGHTFACE_PACK}",
    )
    # GitHub release assets — a fork would never have carried these.
    for url in config.REACTOR_FACEDETECTION_FILES:
        name = url.rsplit("/", 1)[-1]
        items.append(Item(
            local=MODELS_DIR / "facedetection" / name,
            repo_key="reactor",
            path_in_repo=f"facedetection/{name}",
            fetch=(lambda u=url, n=name: downloads.fetch_url_file(
                u, MODELS_DIR / "facedetection" / n)),
            note="GitHub release asset",
        ))
    items += _expand_dir(MODELS_DIR / REACTOR_NSFW_DIR,
                         "reactor", REACTOR_NSFW_DIR)

    return items


# ── Node packs ────────────────────────────────────────────────────────────────
def _git_sha(repo_dir: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except Exception:
        return None


def pack_nodes(staging: Path) -> tuple[list[Item], dict]:
    """Tar every custom-node pack and record the SHA it was taken at.

    A GitHub fork does not protect you here — a fork cloned at HEAD breaks
    exactly as fast as the original. The tarball plus the recorded SHA is
    what actually pins the app.
    """
    staging.mkdir(parents=True, exist_ok=True)
    custom_nodes = COMFY_DIR / "custom_nodes"
    packs = [(name, url) for name, url, _cls in V2_NODE_REPOS]
    packs.append(("comfyui-krea2edit", KREA2EDIT_NODES_REPO))

    items, pins = [], {}
    for dirname, url in packs:
        src = custom_nodes / dirname
        if not src.is_dir():
            log.warning("node pack %s not present at %s — skipping "
                        "(was its feature enabled on the boot run?)",
                        dirname, src)
            continue
        sha = _git_sha(src)
        short = (sha or "unknown")[:8]
        # The SHA is in the *filename*, not just PINS.json. Naming it
        # `{dirname}.tar.gz` meant a second run after the pack updated
        # would reuse the stale tarball (it still existed) while PINS.json
        # recorded the new SHA — a mirror that quietly disagreed with its
        # own manifest. A new commit is now a new artifact, old ones stay
        # put, and rolling back is picking a different filename.
        name = f"{dirname}-{short}.tar.gz"
        pins[dirname] = {"url": url, "sha": sha, "tarball": name}
        tarball = staging / name
        if not tarball.exists():
            log.info("packing %s (%s)", dirname, short)
            _pack_reproducible(src, dirname, tarball)
        items.append(Item(tarball, "nodes", name, note=f"@{short}"))

    # ComfyUI itself is not mirrored — but it is the single most likely
    # thing on the list to break you, so its SHA is always recorded.
    comfy_sha = _git_sha(COMFY_DIR)
    pins["ComfyUI"] = {"url": config.__dict__.get("COMFYUI_REPO",
                       "https://github.com/comfyanonymous/ComfyUI.git"),
                       "sha": comfy_sha}
    return items, pins


def _skip_junk(info: tarfile.TarInfo):
    """Drop VCS/build junk and normalise metadata.

    Zeroing mtime/uid/gid is what makes the tarball reproducible: without
    it, re-packing the same commit yields different bytes, the size check
    disagrees with the copy already in the repo, and a no-op run uploads
    the pack again.
    """
    parts = Path(info.name).parts
    if ".git" in parts or "__pycache__" in parts:
        return None
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _pack_reproducible(src: Path, arcname: str, dest: Path) -> None:
    """tar.gz `src` so the same commit always produces the same bytes.

    tarfile's "w:gz" stamps the current time into the gzip header, so the
    gzip layer is driven explicitly with mtime=0.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tf:
                for path in sorted(src.rglob("*")):
                    tf.add(path, arcname=f"{arcname}/"
                           f"{path.relative_to(src).as_posix()}",
                           recursive=False, filter=_skip_junk)
    tmp.rename(dest)


# ── Upload ────────────────────────────────────────────────────────────────────
def probe_remote(api: HfApi, user: str, items: list[Item]) -> None:
    """Fill in remote_size so the plan can say what will actually upload."""
    by_repo: dict[str, list[Item]] = {}
    for it in items:
        by_repo.setdefault(it.repo_key, []).append(it)
    for repo_key, group in by_repo.items():
        repo_id = f"{user}/{REPOS[repo_key][0]}"
        try:
            infos = api.get_paths_info(
                repo_id, [i.path_in_repo for i in group], repo_type="model")
        except HfHubHTTPError:
            continue        # repo does not exist yet — everything uploads
        sizes = {i.path: getattr(i, "size", None) for i in infos}
        for it in group:
            it.remote_size = sizes.get(it.path_in_repo)


def ensure_repos(api: HfApi, user: str, keys: Iterable[str], private: bool) -> None:
    for key in sorted(set(keys)):
        name, blurb = REPOS[key]
        repo_id = f"{user}/{name}"
        api.create_repo(repo_id, repo_type="model", private=private,
                        exist_ok=True)
        log.info("repo ready: %s (%s) — %s", repo_id,
                 "private" if private else "PUBLIC", blurb)


def upload(api: HfApi, user: str, item: Item) -> None:
    repo_id = f"{user}/{REPOS[item.repo_key][0]}"
    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            api.upload_file(
                path_or_fileobj=str(item.local),
                path_in_repo=item.path_in_repo,
                repo_id=repo_id,
                repo_type="model",
                commit_message=f"mirror: {item.path_in_repo}",
            )
            return
        except Exception as exc:
            if attempt == UPLOAD_RETRIES:
                raise
            wait = 10 * 2 ** (attempt - 1)
            log.warning("upload of %s failed (%d/%d): %s — retrying in %ds",
                        item.path_in_repo, attempt, UPLOAD_RETRIES, exc, wait)
            time.sleep(wait)


def upload_json(api: HfApi, user: str, repo_key: str, name: str,
                payload: dict) -> None:
    blob = json.dumps(payload, indent=2, sort_keys=True).encode()
    api.upload_file(
        path_or_fileobj=io.BytesIO(blob), path_in_repo=name,
        repo_id=f"{user}/{REPOS[repo_key][0]}", repo_type="model",
        commit_message=f"mirror: {name}",
    )


# ── Reporting ─────────────────────────────────────────────────────────────────
def human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:,.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GB"


def print_checklist(items: list[Item]) -> None:
    width = max((len(i.path_in_repo) for i in items), default=20)
    current = None
    for it in sorted(items, key=lambda i: (i.repo_key, i.path_in_repo)):
        if it.repo_key != current:
            current = it.repo_key
            name, blurb = REPOS[current]
            print(f"\n  {name}  —  {blurb}")
            print("  " + "─" * (width + 34))
        mark = {ACT_SKIP: "✓", ACT_MISSING: "✗"}.get(it.action, "↑")
        size = human(it.size) if it.present else "—"
        print(f"  {mark} {it.path_in_repo:<{width}}  {size:>10}  "
              f"{it.action}" + (f"   [{it.note}]" if it.note else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the checklist and exit; touch nothing")
    ap.add_argument("--public", action="store_true",
                    help="create public repos (default: private)")
    ap.add_argument("--skip-downloads", action="store_true",
                    help="upload only what is already on disk")
    ap.add_argument("--include-finepn", action="store_true",
                    help="also mirror the commented FinePorn V2 UNet (~12.2 GB)")
    ap.add_argument("--no-scan-config", action="store_true",
                    help="do not scan config.py for commented-out CivitAI "
                         "entries (mirror the active lists only)")
    ap.add_argument("--only", metavar="KEY", action="append",
                    choices=sorted(REPOS),
                    help="limit to one repo key; repeatable")
    ap.add_argument("--staging", type=Path,
                    default=Path(config.BASE_DIR) / "mirror_staging",
                    help="where node tarballs are built")
    args = ap.parse_args()

    token = HF_WRITE_TOKEN
    if not token:
        log.error("No HF write token. Set HF_WRITE_TOKEN (a token with "
                  "*write* scope) or fill in the constant at the top of "
                  "this file.")
        return 2

    api = HfApi(token=token)
    try:
        user = api.whoami()["name"]
    except Exception as exc:
        log.error("Could not authenticate to Hugging Face: %s", exc)
        return 2
    log.info("Authenticated as %s", user)

    items = build_plan(args)
    node_items, pins = pack_nodes(args.staging)
    items += node_items

    if args.only:
        items = [i for i in items if i.repo_key in args.only]

    for it in items:
        it.present = it.local.exists()
        it.size = it.local.stat().st_size if it.present else 0

    probe_remote(api, user, items)
    print_checklist(items)

    to_fetch = [i for i in items if not i.present and i.fetch]
    stranded = [i for i in items if not i.present and not i.fetch]
    to_upload = [i for i in items if i.action in (ACT_UPLOAD, ACT_REUPLOAD)]
    bytes_up = sum(i.size for i in to_upload)

    print(f"\n  {len(items)} items · {len(to_fetch)} to download · "
          f"{len(to_upload)} to upload ({human(bytes_up)}) · "
          f"{len(stranded)} missing with no fetcher")

    if stranded:
        print("\n  Missing and not fetchable by this script — these come "
              "from the app's own boot path:")
        for it in stranded:
            print(f"    ✗ {it.local}")
        print("  Re-run the pod with the matching feature enabled, or pass "
              "--skip-downloads to mirror everything else.")

    if args.dry_run:
        print("\n  --dry-run: nothing downloaded, no repos created.\n")
        return 0

    # ── Download the gaps ────────────────────────────────────────────────
    if to_fetch and not args.skip_downloads:
        for it in to_fetch:
            try:
                it.fetch()
                it.present = it.local.exists()
                it.size = it.local.stat().st_size if it.present else 0
            except Exception as exc:
                # One dead CivitAI link must not sink the migration —
                # same rule the app's own downloaders follow.
                log.error("Could not fetch %s: %s", it.path_in_repo, exc)

    # ── Create repos + upload ────────────────────────────────────────────
    live = [i for i in items if i.present and i.remote_size != i.size]
    ensure_repos(api, user, (i.repo_key for i in live), not args.public)

    failed = []
    for n, it in enumerate(live, 1):
        log.info("[%d/%d] ↑ %s (%s)", n, len(live), it.path_in_repo,
                 human(it.size))
        try:
            upload(api, user, it)
        except Exception as exc:
            log.error("Upload failed for %s: %s", it.path_in_repo, exc)
            failed.append(it)

    # ── Manifests ────────────────────────────────────────────────────────
    # PINS.json is the deliverable that protects the *unmirrored* 185 GB:
    # the revisions bootstrap.py and downloads.py should be locked to.
    pins["_note"] = ("SHAs/revisions captured at mirror time. Feed these to "
                     "bootstrap.py clones and hf_hub_download(revision=...).")
    for repo_id, kind in UPSTREAM_TO_PIN.items():
        try:
            info = (api.dataset_info(repo_id) if kind == "hf-dataset"
                    else api.model_info(repo_id))
            pins[repo_id] = {"kind": kind, "sha": info.sha}
        except Exception as exc:
            log.warning("Could not read revision of %s: %s", repo_id, exc)

    try:
        ensure_repos(api, user, ["nodes"], not args.public)
        upload_json(api, user, "nodes", "PINS.json", pins)
        (args.staging / "PINS.json").write_text(
            json.dumps(pins, indent=2, sort_keys=True))
    except Exception as exc:
        log.error("Could not publish PINS.json: %s", exc)

    if any(i.repo_key == "loras" for i in items):
        try:
            ensure_repos(api, user, ["loras"], not args.public)
            upload_json(api, user, "loras", "aliases.json", {
                "_note": ("config.py refers to some of these blobs by a "
                          "second filename. Resolve alias → canonical "
                          "instead of storing the file twice."),
                "canonical_to_alias": ALIASES,
            })
        except Exception as exc:
            log.error("Could not publish aliases.json: %s", exc)

    # ── Report ───────────────────────────────────────────────────────────
    # A 20 GB upload outlives the terminal it was started in, so the
    # outcome goes to a file as well as the console.
    ok = len(live) - len(failed)
    report = {
        "account": user,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "private": not args.public,
        "repos": {k: f"{user}/{REPOS[k][0]}" for k in sorted(REPOS)},
        "uploaded": [i.path_in_repo for i in live if i not in failed],
        "failed": [i.path_in_repo for i in failed],
        "skipped_already_in_repo": [
            i.path_in_repo for i in items if i.action == ACT_SKIP],
        "missing_no_fetcher": [str(i.local) for i in stranded],
        "bytes_uploaded": sum(i.size for i in live if i not in failed),
    }
    report_path = args.staging / "mirror_report.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(f"\n  Uploaded {ok}/{len(live)} · {len(failed)} failed")
    if failed:
        for it in failed:
            print(f"    ✗ {it.path_in_repo}")
        print("  Re-run to retry — completed uploads are skipped by size.")
    print(f"  PINS.json  → {user}/{REPOS['nodes'][0]}")
    print(f"  report     → {report_path}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
