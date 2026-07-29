#!/usr/bin/env python3
"""One-shot mirror: pod disk → your Hugging Face account.

Operator tool, not part of the shipped app. The intended run is:

    1. Boot a pod with every feature on. Features come from the license
       key, so this needs an operator key issued with everything granted:
           npm run issue-key -- --name "mirror operator" --features all
           KREA2_LICENSE_KEY=KREA2-... python3 app.py
       Let it finish downloading, then stop it.
    2. python3 scripts/mirror_to_hf.py --pins-only    # capture pod state
    3. python3 scripts/mirror_to_hf.py --dry-run      # read the checklist
    4. python3 scripts/mirror_to_hf.py                # top up + upload

WHAT GETS MIRRORED IS NOT DECIDED HERE. mirror_manifest.json is the only
list; this file is the machinery that acts on it. Adding a LoRA means
editing that JSON, never this module — which is the whole point, because a
list embedded in code drifts from config.py without anyone noticing.

Step 1 leaves ~200 GB on disk. The manifest mirrors only the ~18 GB that
is actually at risk of disappearing — the CivitAI LoRAs, the community HF
repos and the GitHub node packs. The Comfy-Org repos stay upstream: they
are org-backed, built to serve that traffic, and in Flux 2's case carry a
licence better left un-redistributed. What they need instead is a pinned
revision, which this script records in scripts/PINS.json.

Everything is idempotent and resumable:

  • a file already on disk is not re-downloaded (the app's own fetchers
    key on the destination path),
  • a file already in the mirror repo at the same size is not re-uploaded,
  • an interrupted run picks up where it stopped.
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
from config import COMFY_DIR, MODELS_DIR, log  # noqa: E402

# ── Credentials ───────────────────────────────────────────────────────────────
# A *write*-scoped token. Prefer the environment variable — this file is in
# git, and a token pasted below is a token that stays in the history even
# after you delete the line. If you do paste one, revoke it afterwards.
HF_WRITE_TOKEN = os.environ.get("HF_WRITE_TOKEN") or ""     # ← paste here if you must

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "mirror_manifest.json"
PINS_PATH = SCRIPT_DIR / "PINS.json"

# Mirror repos are private by default: several of these weights carry
# no-redistribution terms, and private keeps the migration from doubling as
# a publication. Flip with --public if you have decided otherwise.
DEFAULT_PRIVATE = True
UPLOAD_RETRIES = 4

# Action labels — the checklist prints them, the summary filters on them.
ACT_SKIP = "skip (in repo)"
ACT_UPLOAD = "UPLOAD"
ACT_REUPLOAD = "RE-UPLOAD (size differs)"
ACT_FETCH = "DOWNLOAD+UPLOAD"
ACT_MISSING = "MISSING"

# config.py writes CivitAI entries in exactly two shapes, and a commented
# line is the same text behind a "#". --audit scans the source so that a
# LoRA added to config.py but forgotten in the manifest gets reported
# rather than silently going unmirrored.
_RE_ID_FIRST = re.compile(r'\(\s*(\d{5,})\s*,\s*"([^"]+\.safetensors)"')
_RE_NAME_FIRST = re.compile(
    r'\(\s*"([^"]+\.safetensors)"\s*,\s*[\d.]+\s*,\s*(?:True|False)\s*,'
    r'\s*(\d{5,})\s*\)')


# ── Manifest ──────────────────────────────────────────────────────────────────
def load_manifest() -> dict:
    """Read mirror_manifest.json — the single source of truth.

    A missing or malformed manifest is fatal on purpose. Falling back to a
    built-in list is precisely the drift this design exists to prevent: it
    would mirror something plausible and let you believe it was complete.
    """
    if not MANIFEST_PATH.exists():
        raise SystemExit(
            f"No manifest at {MANIFEST_PATH}. It is the list of what the "
            f"mirror holds — this script has no built-in copy. Restore it "
            f"from git."
        )
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for required in ("repos", "items"):
        if required not in manifest:
            raise SystemExit(f"{MANIFEST_PATH.name} has no '{required}' key.")
    return manifest


def repo_table(manifest: dict) -> dict[str, tuple[str, str]]:
    """key → (repo name under your account, what it holds)."""
    return {k: (v["name"], v.get("description", ""))
            for k, v in manifest["repos"].items()}


def make_fetcher(source: dict, dest: Path) -> Callable[[], None] | None:
    """Map a manifest `source` block onto one of downloads.py's fetchers.

    Reusing them rather than reimplementing is deliberate: the CivitAI
    resume/retry/HTML-error handling in downloads.py is already correct and
    already debugged.
    """
    kind = source.get("kind")
    if kind == "civitai":
        subdir = source.get("subdir") or dest.parent.name
        return lambda: downloads.fetch_civitai_file(
            source["version"], dest.name, subdir=subdir)
    if kind == "hf":
        return lambda: downloads.fetch_hf_file_to(
            source["repo"], source["path"], dest)
    if kind == "hf_dataset":
        return lambda: downloads.fetch_dataset_file(
            source["repo"], source["path"], dest)
    if kind == "url":
        return lambda: downloads.fetch_url_file(source["url"], dest)
    if kind == "abliterated_merge":
        return downloads.fetch_abliterated_encoder
    # "dir" and "local" are produced by the app's own boot path; there is
    # nothing this script can call to conjure them.
    return None


# ── Item model ────────────────────────────────────────────────────────────────
@dataclass
class Item:
    """One file to mirror. `fetch` is how to obtain it if it is missing."""
    local: Path
    repo_key: str
    path_in_repo: str
    fetch: Callable[[], None] | None = None
    note: str = ""
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


# Junk that must never reach the mirror. `.cache/` is the one that bites:
# snapshot_download(local_dir=...) leaves a .cache/huggingface/ tree of
# lock files and download metadata *inside* the model folder, and the Hub
# rejects any commit touching a '.cache/' path outright. So these are not
# merely noise — they are guaranteed, unretryable upload failures.
_EXCLUDE_DIRS = {".cache", ".git", "__pycache__", ".locks", ".ipynb_checkpoints"}
_EXCLUDE_SUFFIXES = (".lock", ".incomplete", ".part", ".pyc", ".tmp")


def _is_junk(path: Path, root: Path) -> bool:
    if any(part in _EXCLUDE_DIRS for part in path.relative_to(root).parts):
        return True
    return path.name.endswith(_EXCLUDE_SUFFIXES)


def _expand_dir(root: Path, repo_key: str, prefix: str, note: str) -> list[Item]:
    """Turn a manifest "dir" entry into one Item per file.

    Folder-level uploads would defeat the per-file skip logic, so every
    directory (buffalo_l, the NSFW detector) is flattened at plan time. An
    absent directory contributes no items, which would drop it from the
    checklist silently — so say so out loud instead.
    """
    if not root.is_dir():
        log.warning("directory %s is absent — nothing from it will be "
                    "mirrored (was the owning feature enabled at boot?)", root)
        return []
    items, skipped = [], 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _is_junk(path, root):
            skipped += 1
            continue
        rel = path.relative_to(root).as_posix()
        items.append(Item(path, repo_key, f"{prefix}/{rel}", note=note))
    if skipped:
        log.info("%s: skipped %d cache/lock file(s)", root.name, skipped)
    return items


def build_plan(manifest: dict, args) -> list[Item]:
    """The checklist, built entirely from the manifest. Nothing is fetched."""
    items: list[Item] = []
    for entry in manifest["items"]:
        if entry.get("enabled") is False and not args.include_disabled:
            continue
        local = MODELS_DIR / entry["local"]
        note = entry.get("note", "")
        source = entry.get("source", {})
        if source.get("kind") == "dir":
            items += _expand_dir(local, entry["repo"],
                                 entry["path_in_repo"], note)
            continue
        if source.get("kind") == "civitai" and not note:
            note = f"CivitAI v{source['version']}"
        items.append(Item(
            local=local,
            repo_key=entry["repo"],
            path_in_repo=entry["path_in_repo"],
            fetch=make_fetcher(source, local),
            note=note,
        ))
    return items


def collect_aliases(manifest: dict) -> dict[str, list[str]]:
    """Filenames config.py uses for a blob the mirror stores under another.

    Uploading the same bytes twice to satisfy two spellings would be the
    obvious fix and the wrong one; the mirror ships the map instead and the
    download layer resolves alias → canonical.
    """
    return {Path(e["path_in_repo"]).name: e["aliases"]
            for e in manifest["items"] if e.get("aliases")}


# ── Audit ─────────────────────────────────────────────────────────────────────
def scan_config_civitai(ignore: Iterable[int]) -> dict[int, str]:
    """Every CivitAI version id mentioned in config.py, enabled or not."""
    source = (config.PROJECT_DIR / "config.py").read_text(encoding="utf-8")
    found: dict[int, str] = {}
    for version_id, filename in _RE_ID_FIRST.findall(source):
        found.setdefault(int(version_id), filename)
    for filename, version_id in _RE_NAME_FIRST.findall(source):
        found.setdefault(int(version_id), filename)
    for placeholder in ignore:
        found.pop(placeholder, None)
    return found


def audit(manifest: dict) -> list[tuple[int, str]]:
    """config.py entries the manifest does not cover.

    This is the drift check that makes a hand-maintained manifest safe: the
    dangerous direction is adding a LoRA to config.py and forgetting it
    here, because the newest entry is the one least likely to still be on
    CivitAI when you need it back.
    """
    known = {e["source"]["version"] for e in manifest["items"]
             if e.get("source", {}).get("kind") == "civitai"}
    scanned = scan_config_civitai(manifest.get("scan_ignore_ids", []))
    return sorted((v, f) for v, f in scanned.items() if v not in known)


# ── Node packs ────────────────────────────────────────────────────────────────
def _git_sha(repo_dir: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except Exception:
        return None


def _skip_junk(info: tarfile.TarInfo):
    """Drop VCS/build junk and normalise metadata.

    Zeroing mtime/uid/gid is what makes the tarball reproducible: without
    it, re-packing the same commit yields different bytes, the size check
    disagrees with the copy already in the repo, and a no-op run uploads
    every pack again.
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


def pack_nodes(manifest: dict, staging: Path) -> tuple[list[Item], dict]:
    """Tar every custom-node pack and record the SHA it was taken at.

    A GitHub fork does not protect you here — a fork cloned at HEAD breaks
    exactly as fast as the original. The tarball plus the recorded SHA is
    what actually pins the app.
    """
    staging.mkdir(parents=True, exist_ok=True)
    custom_nodes = COMFY_DIR / "custom_nodes"
    items, pins = [], {}
    for pack in manifest.get("node_packs", []):
        dirname = pack["dir"]
        src = custom_nodes / dirname
        if not src.is_dir():
            log.warning("node pack %s not present at %s — skipping (was its "
                        "feature enabled on the boot run?)", dirname, src)
            continue
        sha = _git_sha(src)
        short = (sha or "unknown")[:8]
        # The SHA is in the *filename*, not just PINS.json. A bare
        # `{dirname}.tar.gz` meant a second run after the pack updated
        # reused the stale tarball (it still existed) while PINS.json
        # recorded the new SHA — a mirror quietly disagreeing with its own
        # manifest. New commit, new artifact; old ones stay for rollback.
        name = f"{dirname}-{short}.tar.gz"
        pins[dirname] = {"url": pack["url"], "sha": sha, "tarball": name}
        tarball = staging / name
        if not tarball.exists():
            log.info("packing %s (%s)", dirname, short)
            _pack_reproducible(src, dirname, tarball)
        items.append(Item(tarball, pack.get("repo", "nodes"), name,
                          note=f"@{short}"))

    # ComfyUI itself is not mirrored — but it is the most likely thing on
    # the list to break you, so its SHA is always recorded.
    pins["ComfyUI"] = {
        "url": "https://github.com/comfyanonymous/ComfyUI.git",
        "sha": _git_sha(COMFY_DIR),
    }
    return items, pins


# ── Pins ──────────────────────────────────────────────────────────────────────
def add_upstream_revisions(api: HfApi, manifest: dict, pins: dict) -> None:
    """Record the current revision of the repos we deliberately do NOT mirror.

    Those ~185 GB are always fetched from upstream, so a revision is the
    only thing between you and a maintainer replacing weights under a
    filename you already ship. It also keeps the mirror and its upstream
    fallback honest: both must resolve to the same revision, or the
    fallback silently serves different weights than the mirror.
    """
    pins["_note"] = ("SHAs/revisions captured at mirror time. Feed these to "
                     "bootstrap.py clones and hf_hub_download(revision=...). "
                     "The mirror and the upstream fallback MUST resolve to "
                     "the same revision.")
    for repo_id, kind in manifest.get("pin_upstream", {}).items():
        if kind == "git":
            continue        # captured from the local checkout by pack_nodes
        try:
            info = (api.dataset_info(repo_id) if kind == "hf-dataset"
                    else api.model_info(repo_id))
            pins[repo_id] = {"kind": kind, "sha": info.sha}
        except Exception as exc:
            log.warning("Could not read revision of %s: %s", repo_id, exc)


def write_pins(pins: dict) -> Path:
    """Write PINS.json into the repo checkout, next to the manifest.

    Not into the staging dir: this is what bootstrap.py and downloads.py
    will read, so it has to be versioned with the code that consumes it. It
    also records the one thing that cannot be recreated once the pod is
    destroyed — which commit of each node pack worked.
    """
    PINS_PATH.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    log.info("pins → %s  (commit this)", PINS_PATH)
    return PINS_PATH


def capture_pins_only(manifest: dict, staging: Path) -> int:
    """--pins-only: record pod state and stop. No token, no uploads.

    Separated from the migration because the two have very different
    deadlines. Weights can be re-downloaded whenever; the node-pack SHAs
    live only in the git checkouts on the running pod, so capturing them
    must not be able to fail over an HF credential.
    """
    _, pins = pack_nodes(manifest, staging)
    try:
        add_upstream_revisions(HfApi(), manifest, pins)   # public repos
    except Exception as exc:
        log.warning("Could not reach Hugging Face for upstream revisions "
                    "(%s) — node-pack SHAs are still captured.", exc)
    path = write_pins(pins)
    captured = len([k for k in pins if not k.startswith("_")])
    print(f"\n  Captured {captured} pins → {path}")
    print("  Commit this before destroying the pod — the node-pack SHAs "
          "cannot be recovered afterwards.\n")
    return 0


# ── Upload ────────────────────────────────────────────────────────────────────
def probe_remote(api: HfApi, user: str, repos: dict, items: list[Item]) -> None:
    """Fill in remote_size so the plan can say what will actually upload."""
    by_repo: dict[str, list[Item]] = {}
    for it in items:
        by_repo.setdefault(it.repo_key, []).append(it)
    for repo_key, group in by_repo.items():
        repo_id = f"{user}/{repos[repo_key][0]}"
        try:
            infos = api.get_paths_info(
                repo_id, [i.path_in_repo for i in group], repo_type="model")
        except HfHubHTTPError:
            continue        # repo does not exist yet — everything uploads
        sizes = {i.path: getattr(i, "size", None) for i in infos}
        for it in group:
            it.remote_size = sizes.get(it.path_in_repo)


def ensure_repos(api: HfApi, user: str, repos: dict, keys: Iterable[str],
                 private: bool) -> None:
    for key in sorted(set(keys)):
        name, blurb = repos[key]
        api.create_repo(f"{user}/{name}", repo_type="model", private=private,
                        exist_ok=True)
        log.info("repo ready: %s/%s (%s) — %s", user, name,
                 "private" if private else "PUBLIC", blurb)


# Rejections the Hub will never accept on a retry. Backing off four times
# over 70s for a path the server has already refused by name is pure delay,
# and it buries the real cause under a wall of identical warnings.
_FATAL_UPLOAD_MARKERS = (
    "Invalid `path_in_repo`",
    "cannot update files under",
    "is not a valid",
)


def _is_fatal_upload(exc: Exception) -> bool:
    if isinstance(exc, ValueError):
        return True
    return any(marker in str(exc) for marker in _FATAL_UPLOAD_MARKERS)


def upload(api: HfApi, user: str, repos: dict, item: Item) -> None:
    repo_id = f"{user}/{repos[item.repo_key][0]}"
    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            api.upload_file(
                path_or_fileobj=str(item.local),
                path_in_repo=item.path_in_repo,
                repo_id=repo_id, repo_type="model",
                commit_message=f"mirror: {item.path_in_repo}",
            )
            return
        except Exception as exc:
            if attempt == UPLOAD_RETRIES or _is_fatal_upload(exc):
                raise
            wait = 10 * 2 ** (attempt - 1)
            log.warning("upload of %s failed (%d/%d): %s — retrying in %ds",
                        item.path_in_repo, attempt, UPLOAD_RETRIES, exc, wait)
            time.sleep(wait)


def upload_json(api: HfApi, user: str, repos: dict, repo_key: str,
                name: str, payload: dict) -> None:
    blob = json.dumps(payload, indent=2, sort_keys=True).encode()
    api.upload_file(
        path_or_fileobj=io.BytesIO(blob), path_in_repo=name,
        repo_id=f"{user}/{repos[repo_key][0]}", repo_type="model",
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


def print_checklist(repos: dict, items: list[Item]) -> None:
    width = max((len(i.path_in_repo) for i in items), default=20)
    current = None
    for it in sorted(items, key=lambda i: (i.repo_key, i.path_in_repo)):
        if it.repo_key != current:
            current = it.repo_key
            name, blurb = repos[current]
            print(f"\n  {name}  —  {blurb}")
            print("  " + "─" * (width + 34))
        mark = {ACT_SKIP: "✓", ACT_MISSING: "✗"}.get(it.action, "↑")
        size = human(it.size) if it.present else "—"
        print(f"  {mark} {it.path_in_repo:<{width}}  {size:>10}  "
              f"{it.action}" + (f"   [{it.note}]" if it.note else ""))


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the checklist and exit; touch nothing")
    ap.add_argument("--audit", action="store_true",
                    help="report config.py entries the manifest is missing, "
                         "then exit. Needs no HF token.")
    ap.add_argument("--pins-only", action="store_true",
                    help="capture node-pack SHAs + upstream revisions to "
                         "scripts/PINS.json and exit. Needs no HF token. "
                         "Run this before destroying the pod.")
    ap.add_argument("--public", action="store_true",
                    help="create public repos (default: private)")
    ap.add_argument("--skip-downloads", action="store_true",
                    help="upload only what is already on disk")
    ap.add_argument("--include-disabled", action="store_true",
                    help='also mirror manifest entries marked "enabled": false')
    ap.add_argument("--only", metavar="KEY", action="append",
                    help="limit to one manifest repo key; repeatable")
    ap.add_argument("--staging", type=Path,
                    default=Path(config.BASE_DIR) / "mirror_staging",
                    help="where node tarballs are built")
    return ap.parse_args()


def run_uploads(api, user, repos, live, args) -> list[Item]:
    ensure_repos(api, user, repos, (i.repo_key for i in live), not args.public)
    failed = []
    for n, it in enumerate(live, 1):
        log.info("[%d/%d] ↑ %s (%s)", n, len(live), it.path_in_repo,
                 human(it.size))
        try:
            upload(api, user, repos, it)
        except Exception as exc:
            log.error("Upload failed for %s: %s", it.path_in_repo, exc)
            failed.append(it)
    return failed


def main() -> int:
    args = parse_args()
    manifest = load_manifest()
    repos = repo_table(manifest)
    # Visibility is a property of the mirror, not of how you invoked the
    # script — the app reads the same flag to decide whether it needs a
    # token. Keeping both off one key stops a --public-less re-run from
    # quietly creating a private repo the customer pods cannot read.
    if manifest.get("mirror_public"):
        args.public = True

    missing = audit(manifest)
    if missing:
        log.warning(
            "%d CivitAI entr%s in config.py are NOT in %s — they will not be "
            "mirrored: %s", len(missing), "y is" if len(missing) == 1 else
            "ies are", MANIFEST_PATH.name,
            ", ".join(f"{v} ({f})" for v, f in missing))
    if args.audit:
        if not missing:
            print(f"\n  {MANIFEST_PATH.name} covers every CivitAI entry in "
                  f"config.py.\n")
        else:
            print(f"\n  Add these to {MANIFEST_PATH.name}:")
            for version_id, filename in missing:
                print(f'    {{"repo": "loras", "local": "loras/{filename}", '
                      f'"path_in_repo": "loras/{filename}", "source": '
                      f'{{"kind": "civitai", "version": {version_id}}}}}')
            print()
        return 1 if missing else 0

    if args.pins_only:
        return capture_pins_only(manifest, args.staging)

    unknown = set(args.only or []) - set(repos)
    if unknown:
        raise SystemExit(f"--only: no such repo key {sorted(unknown)}. "
                         f"Manifest defines: {sorted(repos)}")

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

    items = build_plan(manifest, args)
    node_items, pins = pack_nodes(manifest, args.staging)
    items += node_items
    if args.only:
        items = [i for i in items if i.repo_key in args.only]

    for it in items:
        it.present = it.local.exists()
        it.size = it.local.stat().st_size if it.present else 0
    probe_remote(api, user, repos, items)
    print_checklist(repos, items)

    to_fetch = [i for i in items if not i.present and i.fetch]
    stranded = [i for i in items if not i.present and not i.fetch]
    to_upload = [i for i in items if i.action in (ACT_UPLOAD, ACT_REUPLOAD)]
    print(f"\n  {len(items)} items · {len(to_fetch)} to download · "
          f"{len(to_upload)} to upload "
          f"({human(sum(i.size for i in to_upload))}) · "
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

    if to_fetch and not args.skip_downloads:
        for it in to_fetch:
            try:
                it.fetch()
                it.present = it.local.exists()
                it.size = it.local.stat().st_size if it.present else 0
            except Exception as exc:
                # One dead CivitAI link must not sink the migration — the
                # same rule the app's own downloaders follow.
                log.error("Could not fetch %s: %s", it.path_in_repo, exc)

    live = [i for i in items if i.present and i.remote_size != i.size]
    failed = run_uploads(api, user, repos, live, args)

    # ── Manifests ────────────────────────────────────────────────────────
    add_upstream_revisions(api, manifest, pins)
    write_pins(pins)
    try:
        ensure_repos(api, user, repos, ["nodes"], not args.public)
        upload_json(api, user, repos, "nodes", "PINS.json", pins)
        aliases = collect_aliases(manifest)
        if aliases:
            upload_json(api, user, repos, "loras", "aliases.json", {
                "_note": ("config.py refers to these blobs by a second "
                          "filename. Resolve alias → canonical instead of "
                          "storing the file twice."),
                "canonical_to_aliases": aliases,
            })
    except Exception as exc:
        log.error("Could not publish manifests to the mirror (%s) — the "
                  "local %s is written and is the one that matters.",
                  exc, PINS_PATH.name)

    # ── Report ───────────────────────────────────────────────────────────
    # A 20 GB upload outlives the terminal it was started in, so the
    # outcome goes to a file as well as the console.
    ok = len(live) - len(failed)
    report_path = args.staging / "mirror_report.json"
    report_path.write_text(json.dumps({
        "account": user,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "private": not args.public,
        "repos": {k: f"{user}/{v[0]}" for k, v in repos.items()},
        "uploaded": [i.path_in_repo for i in live if i not in failed],
        "failed": [i.path_in_repo for i in failed],
        "skipped_already_in_repo": [i.path_in_repo for i in items
                                    if i.action == ACT_SKIP],
        "missing_no_fetcher": [str(i.local) for i in stranded],
        "config_entries_not_in_manifest": [v for v, _ in missing],
        "bytes_uploaded": sum(i.size for i in live if i not in failed),
    }, indent=2))

    print(f"\n  Uploaded {ok}/{len(live)} · {len(failed)} failed")
    if failed:
        for it in failed:
            print(f"    ✗ {it.path_in_repo}")
        print("  Re-run to retry — completed uploads are skipped by size.")
    print(f"  PINS.json  → {PINS_PATH}  (commit this)")
    print(f"  report     → {report_path}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
