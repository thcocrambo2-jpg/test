#!/usr/bin/env python3
"""One-shot mirror: pod disk → your Hugging Face account.

Operator tool, not part of the shipped app. The intended run is:

    1. Boot a pod with every feature on. Features come from the license
       key, so this needs an operator key issued with everything granted:
           npm run issue-key -- --name "mirror operator" --features all
           KREA2_LICENSE_KEY=KREA2-... python3 app.py
       Let it finish downloading, then stop it.
    2. python3 scripts/mirror_to_hf.py --pins-only    # capture pod state
    3. python3 scripts/mirror_to_hf.py --audit        # what has no mirror
    4. python3 scripts/mirror_to_hf.py --dry-run      # read the checklist
    5. KREA2_ADMIN_TOKEN=... HF_WRITE_TOKEN=... \\
       python3 scripts/mirror_to_hf.py                # top up + upload

Steps 3-5 read the catalogue the way the pod does — POST /v1/catalog with
KREA2_LICENSE_KEY and KREA2_NODE_TAG, which the pod already carries — or,
with --catalog FILE, from a file in the same shape
(license-validator/data/assets.json is one). --audit and --dry-run need no
HF_WRITE_TOKEN and no KREA2_ADMIN_TOKEN, and neither writes anything to
the mirror or to the DB.

WHAT GETS MIRRORED IS NOT DECIDED HERE. There are two lists and neither is
in this file:

  • mirror_manifest.json — the PIPELINE assets: the merged text encoder,
    the VAEs, the Identity Edit LoRA, the node packs, the upstream repos
    to pin. Adding one of those means editing that JSON, never this
    module, because a list embedded in code drifts from the code that
    downloads it without anyone noticing.
  • the catalogue — the licence server's `loras` and `models` records.
    Each record carries its own `mirror` ({repo, path}, or null), which is
    what the pod downloads from first. A LoRA whose `mirror` is set is
    checked like a manifest item. A LoRA whose `mirror` is null is fetched
    from its `source` (disk first, never the mirror), uploaded to the
    `loras` repo at loras/<file>, and then the location is written back to
    its record with POST /v1/admin/loras, authorised by KREA2_ADMIN_TOKEN.
    Without that token (or without KREA2_NODE_TAG) the script prints the
    `npm run assets` command that records it by hand instead. Adding a
    LoRA is therefore a DB edit followed by a run of this script.

Step 1 leaves ~200 GB on disk. Only the ~18 GB that is actually at risk of
disappearing is mirrored — the CivitAI LoRAs, the community HF repos and
the GitHub node packs. The Comfy-Org repos stay upstream: they are
org-backed and built to serve that traffic. What they need instead is a
pinned revision, which this script records in scripts/PINS.json. The same
rule covers the catalogue: a record whose source is an hf repo that
pin_upstream pins and no manifest item mirrors from (today krea2-turbo and
both models, all from Comfy-Org/Krea-2) needs no mirror and is not
reported as missing one. Models are otherwise only audited — this script
uploads catalogue LoRAs, not catalogue models.

Everything is idempotent and resumable. The mirror repo, not the local
disk, is what decides the work:

  • already in the repo → nothing happens, whether or not the pod still
    has a local copy. The mirror holding it is the entire goal, so a
    fetch to reproduce a file it already has is pure transfer cost,
  • in the repo at a *different* size → re-uploaded from disk,
  • not in the repo, on disk → uploaded,
  • not in the repo, not on disk → downloaded from its original source
    (CivitAI / community HF repo — never from the mirror), then
    uploaded,
  • a file already on disk is not re-downloaded (the app's own fetchers
    key on the destination path),
  • a LoRA with no `mirror` whose file is already in the repo (an earlier
    run uploaded it but could not write the record) → only the record is
    written,
  • an interrupted run picks up where it stopped.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# The app's modules live one level up; this script is deliberately outside
# the package so build.sh / Nuitka never sweep it into the shipped binary.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# "Downloaded from its original source — never from the mirror" has to be
# enforced, not assumed. downloads.py's fetchers try the mirror first, and
# a LoRA whose record has no `mirror` is exactly the case where the mirror
# answer would be wrong: at best a copy this run is about to replace, at
# worst a stale file under the same name. KREA2_NO_MIRROR is mirror.py's
# own escape hatch and is read when it is imported, so it is set before
# anything below imports it.
os.environ["KREA2_NO_MIRROR"] = "1"

from huggingface_hub import HfApi  # noqa: E402
from huggingface_hub.utils import HfHubHTTPError  # noqa: E402

import catalog  # noqa: E402
import config  # noqa: E402
import downloads  # noqa: E402
import mirror  # noqa: E402
from config import COMFY_DIR, MODELS_DIR, log  # noqa: E402

# ── Credentials ───────────────────────────────────────────────────────────────
# A *write*-scoped token. Prefer the environment variable — this file is in
# git, and a token pasted below is a token that stays in the history even
# after you delete the line. If you do paste one, revoke it afterwards.
HF_WRITE_TOKEN = os.environ.get("HF_WRITE_TOKEN") or ""     # ← paste here if you must
# The licence server's ADMIN_TOKEN, for writing a record's `mirror` after
# its upload. Environment only: unlike the HF token there is no reason to
# ever paste it here, since without it the script prints the command that
# records the location by hand.
ADMIN_TOKEN = os.environ.get("KREA2_ADMIN_TOKEN") or ""

# What this script calls itself when it asks for the catalogue. The route
# checks the licence like /v1/acquire does; a readable id keeps the
# operator's requests recognisable among the pods' in the server's logs.
CATALOG_INSTANCE_ID = "mirror-operator"
ADMIN_TIMEOUT = 30

# How to record a mirror by hand, from license-validator/, when this script
# cannot (no KREA2_ADMIN_TOKEN, no KREA2_NODE_TAG, or the request failed).
ASSETS_COMMAND = ("npm run assets -- --mirror {id} "
                  "--repo {repo} --path {path}")

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
ACT_MIRRORED = "skip (in repo, not on disk)"
ACT_UPLOAD = "UPLOAD"
ACT_REUPLOAD = "RE-UPLOAD (size differs)"
ACT_FETCH = "DOWNLOAD+UPLOAD"
ACT_MISSING = "MISSING"

# The two actions that mean "the mirror already holds this" — nothing to
# download, nothing to upload. They differ only in whether the pod happens
# to have a local copy, which is not something the mirror cares about.
_ACT_DONE = (ACT_SKIP, ACT_MIRRORED)

# Where a catalogue record's file is mirrored, as --audit reports it.
MIR_RECORD = "mirror"          # the record's own `mirror` field is set
MIR_UPSTREAM = "upstream"      # pinned upstream repo, not mirrored on purpose
MIR_MANIFEST = "manifest"      # only the manifest lists it; record has none
MIR_NONE = "none"              # nothing — the next run of this script fixes it


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
    """Map a `source` block onto one of downloads.py's fetchers.

    Manifest items and catalogue records write their sources the same way
    ({kind: civitai, version} / {kind: hf, repo, path}); a catalogue record
    has no `subdir`, and its destination's folder supplies it.

    Reusing the fetchers rather than reimplementing is deliberate: the
    CivitAI resume/retry/HTML-error handling in downloads.py is already
    correct and already debugged.
    """
    kind = source.get("kind")
    if kind == "civitai":
        subdir = source.get("subdir") or dest.parent.name
        return lambda: downloads.fetch_civitai_file(
            source["version"], dest.name, subdir=subdir)
    if kind == "hf":
        return lambda: downloads.fetch_hf_file_to(
            source["repo"], source["path"], dest)
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
    # Catalogue LoRAs whose record has no `mirror` yet: once this file is
    # in the repo, each of them is pointed at it. Empty for everything
    # else, including a LoRA whose record already names its mirror.
    lora_ids: list[str] = field(default_factory=list)
    present: bool = field(default=False, init=False)
    size: int = field(default=0, init=False)
    remote_size: int | None = field(default=None, init=False)

    @property
    def action(self) -> str:
        if not self.present:
            # In the repo already, just not on this disk. Downloading it
            # would cost a full transfer to arrive at a file the mirror
            # holds byte-for-byte and would then decline to re-upload —
            # the fetch is pure waste, and on a CivitAI LoRA it is a waste
            # measured in gigabytes. This is the common case when the
            # script is re-run on a fresh box rather than on the fully
            # populated pod the workflow assumes.
            if self.remote_size is not None:
                return ACT_MIRRORED
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
    directory is flattened at plan time. An absent directory contributes
    no items, which would drop it from the checklist silently — so say so
    out loud instead.
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


# ── Catalogue ─────────────────────────────────────────────────────────────────
def load_catalogue(path: Path | None) -> catalog.Catalogue:
    """The LoRA and model records, from --catalog FILE or the live server.

    Not catalog.load(): that falls back to the copy an earlier run saved
    when the server does not answer, and it writes one. Right for a pod
    that has to start regardless, wrong here — this script writes `mirror`
    fields back from what it read, so it acts on the live records or not
    at all. The request and the validation are catalog.py's own, so the
    operator sees exactly the records a pod would.
    """
    if path is not None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"Could not read the catalogue file {path}: {exc}")
        origin = f"file {path}"
    else:
        if not config.LICENSE_API_URL or not config.LICENSE_KEY:
            raise SystemExit(
                "No catalogue to read. Set KREA2_LICENSE_KEY and "
                "KREA2_NODE_TAG as on a pod, or pass --catalog FILE "
                "(license-validator/data/assets.json has the same shape).")
        document = catalog._fetch(CATALOG_INSTANCE_ID)
        if document is None:
            raise SystemExit(
                f"The licence server at {config.LICENSE_API_URL} did not "
                f"answer the catalogue request (see the log above). Nothing "
                f"was mirrored.")
        origin = f"server {config.LICENSE_API_URL}"
    result = catalog.parse(document, origin)
    log.info("Catalogue — %d LoRA(s), %d model(s) from %s",
             len(result.loras), len(result.models), origin)
    return result


def upstream_only_repos(manifest: dict) -> set[str]:
    """HF repos that are pinned rather than mirrored, by design.

    Derived, not listed: a repo pin_upstream pins and no manifest item
    mirrors from. That is exactly how the Comfy-Org repos have always been
    treated — pinned, never copied — while the community repos next to
    them in pin_upstream are pinned *and* mirrored.
    """
    mirrored_from = {e.get("source", {}).get("repo")
                     for e in manifest["items"]}
    return {repo for repo, kind in manifest.get("pin_upstream", {}).items()
            if kind == "hf" and repo not in mirrored_from}


def _is_upstream_only(record, upstream: set[str]) -> bool:
    return (record.source.get("kind") == "hf"
            and record.source.get("repo") in upstream)


def repo_key_for(repos: dict, user: str, repo_id: str) -> str | None:
    """The manifest key of a record's `mirror.repo`, if it is one of ours."""
    for key, (name, _) in repos.items():
        if f"{user}/{name}" == repo_id:
            return key
    return None


def add_catalogue_loras(items: list[Item], cat: catalog.Catalogue,
                        repos: dict, user: str, upstream: set[str]) -> None:
    """Append one Item per catalogue LoRA file to the manifest's plan.

    A record that already names its mirror is checked exactly like a
    manifest item: skipped when the repo has it at the local size,
    re-uploaded when the size differs, fetched and uploaded when the repo
    lost it. A record with no mirror goes to the `loras` repo at
    loras/<file> and carries its id, so that the location is written back
    once the file is there.

    Items are keyed by (repo, path) so a file is planned once however many
    records or manifest entries name it — two records may share a file.
    """
    by_path = {(i.repo_key, i.path_in_repo): i for i in items}
    for lora in cat.loras.values():
        local = MODELS_DIR / "loras" / lora.file
        if lora.mirror:
            key = repo_key_for(repos, user, lora.mirror["repo"])
            if key is None:
                log.warning("LoRA %s is mirrored at %s, which is not one of "
                            "%s's mirror repos — leaving it alone.",
                            lora.id, lora.mirror["repo"], user)
                continue
            path, pending = lora.mirror["path"], False
        elif _is_upstream_only(lora, upstream):
            continue            # pinned upstream, like the Comfy-Org weights
        else:
            key, path, pending = "loras", f"loras/{lora.file}", True
        item = by_path.get((key, path))
        if item is None:
            note = lora.id
            if lora.source.get("kind") == "civitai":
                note += f" · CivitAI v{lora.source['version']}"
            item = Item(local, key, path,
                        fetch=make_fetcher(lora.source, local), note=note)
            items.append(item)
            by_path[(key, path)] = item
        if pending:
            item.lora_ids.append(lora.id)
            item.note += " · mirror not recorded"


# ── Audit ─────────────────────────────────────────────────────────────────────
@dataclass
class AuditRow:
    kind: str          # "LoRA" | "model"
    id: str
    file: str
    status: str        # one of MIR_*
    where: str


def audit(manifest: dict, cat: catalog.Catalogue) -> list[AuditRow]:
    """Every catalogue record, and where the mirror holds its file.

    The drift check this used to run against config.py's lists now runs
    against the records the pods actually download. The dangerous
    direction is unchanged: a LoRA added to the DB and never mirrored,
    because the newest one is the one least likely to still be on CivitAI
    when you need it back. A LoRA reported here is fixed by the next run of
    this script; a model is not (see the module docstring) and needs its
    `mirror` set by hand.
    """
    upstream = upstream_only_repos(manifest)
    manifest_locals = {e["local"]: e for e in manifest["items"]}
    rows = []
    for kind, subdir, records in (("LoRA", "loras", cat.loras),
                                  ("model", "diffusion_models", cat.models)):
        for rec in records.values():
            src = rec.source
            origin = (f"CivitAI v{src['version']}" if src["kind"] == "civitai"
                      else f"{src['repo']}:{src['path']}")
            entry = manifest_locals.get(f"{subdir}/{rec.file}")
            if rec.mirror:
                row = (MIR_RECORD,
                       f"{rec.mirror['repo']}:{rec.mirror['path']}")
            elif _is_upstream_only(rec, upstream):
                row = (MIR_UPSTREAM, f"{origin} (pinned upstream)")
            elif entry is not None:
                # The pod still finds it through the bundled manifest, but
                # the record is where the location belongs now.
                row = (MIR_MANIFEST, f"manifest {entry['repo']}:"
                                     f"{entry['path_in_repo']}")
            else:
                row = (MIR_NONE, origin)
            rows.append(AuditRow(kind, rec.id, rec.file, *row))
    return rows


def print_audit(rows: list[AuditRow], origin: str) -> None:
    marks = {MIR_RECORD: "✓", MIR_UPSTREAM: "~", MIR_MANIFEST: "!",
             MIR_NONE: "✗"}
    width = max((len(r.id) for r in rows), default=10)
    print(f"\n  Catalogue ({origin}) against the mirror")
    for kind in ("LoRA", "model"):
        group = [r for r in rows if r.kind == kind]
        print(f"\n  {kind}s ({len(group)})")
        print("  " + "─" * (width + 60))
        for r in group:
            label = "NOT MIRRORED — " if r.status == MIR_NONE else ""
            print(f"  {marks[r.status]} {r.id:<{width}}  {label}{r.where}")
    counts = {s: sum(r.status == s for r in rows) for s in marks}
    print(f"\n  {counts[MIR_RECORD]} mirrored · {counts[MIR_UPSTREAM]} pinned "
          f"upstream (not mirrored on purpose) · {counts[MIR_MANIFEST]} "
          f"in the manifest only · {counts[MIR_NONE]} not mirrored")
    if counts[MIR_MANIFEST]:
        print("\n  ! The manifest lists these, but their records have no "
              "`mirror`. Run\n    this script to record it, then remove the "
              "manifest entries.")
    unmirrored = [r for r in rows if r.status == MIR_NONE]
    if any(r.kind == "LoRA" for r in unmirrored):
        print("\n  ✗ LoRAs: run this script (without --audit) to upload them "
              "and record\n    their mirror.")
    if any(r.kind == "model" for r in unmirrored):
        print("\n  ✗ Models: this script does not upload catalogue models. "
              "Mirror the file\n    and set the record's `mirror` by hand.")
    print()


# ── Records ───────────────────────────────────────────────────────────────────
def record_mirror(lora_id: str, repo: str, path: str) -> bool:
    """Write one LoRA record's `mirror` through the admin route.

    The route upserts by id and $sets only the fields sent, so this touches
    `mirror` and nothing else on the record. Never raises: a record left
    unwritten is reported with the command that writes it by hand, and the
    upload it follows is already done either way.
    """
    body = json.dumps({"id": lora_id,
                       "mirror": {"repo": repo, "path": path}}).encode()
    request = urllib.request.Request(
        f"{config.LICENSE_API_URL}/v1/admin/loras", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 # Exactly what requireAdmin compares against.
                 "Authorization": f"Bearer {ADMIN_TOKEN}"})
    try:
        with urllib.request.urlopen(request, timeout=ADMIN_TIMEOUT) as resp:
            answer = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        detail = err.read()[:200].decode("utf-8", "replace")
        log.error("Recording the mirror of %s was refused (HTTP %s): %s",
                  lora_id, err.code, detail)
        return False
    except Exception as exc:
        log.error("Could not reach the licence server to record the mirror "
                  "of %s: %s", lora_id, exc)
        return False
    if not answer.get("ok"):
        log.error("Recording the mirror of %s failed: %s", lora_id,
                  answer.get("message") or answer.get("error") or answer)
        return False
    return True


def write_records(pending: list[tuple[str, str, str]], dry_run: bool) -> list:
    """Point each (lora id, repo, path) record at its mirror.

    Returns the ones left unrecorded. Anything that cannot go through the
    route is printed as the `npm run assets` command that records it by
    hand, so a run without the admin token still ends with a finished
    mirror and a list of exact next steps rather than a half-done job.
    """
    if not pending:
        return []
    if not ADMIN_TOKEN:
        why = "KREA2_ADMIN_TOKEN is not set"
    elif not config.LICENSE_API_URL:
        why = "KREA2_NODE_TAG is not set, so there is no server to call"
    else:
        why = ""
    if why:
        print(f"\n  {len(pending)} mirror location(s) to record by hand "
              f"({why}){' once uploaded' if dry_run else ''} — from "
              f"license-validator/:")
        for lora_id, repo, path in pending:
            print("    " + ASSETS_COMMAND.format(id=lora_id, repo=repo,
                                                 path=path))
        return pending
    if dry_run:
        print(f"\n  Would record {len(pending)} mirror location(s) with "
              f"POST {config.LICENSE_API_URL}/v1/admin/loras:")
        for lora_id, repo, path in pending:
            print(f"    {lora_id:<24} → {repo}:{path}")
        return pending
    failed = []
    for lora_id, repo, path in pending:
        if record_mirror(lora_id, repo, path):
            print(f"  ✓ recorded {lora_id} → {repo}:{path}")
        else:
            failed.append((lora_id, repo, path))
    if failed:
        print("\n  Record these by hand — from license-validator/:")
        for lora_id, repo, path in failed:
            print("    " + ASSETS_COMMAND.format(id=lora_id, repo=repo,
                                                 path=path))
    return failed


def pending_records(items: list[Item], user: str, repos: dict,
                    landed: Callable[[Item], bool]) -> list[tuple[str, str, str]]:
    """(lora id, repo id, path) for every record whose file `landed`."""
    return [(lora_id, f"{user}/{repos[it.repo_key][0]}", it.path_in_repo)
            for it in items if it.lora_ids and landed(it)
            for lora_id in it.lora_ids]


# ── Node packs ────────────────────────────────────────────────────────────────
def _git_sha(repo_dir: Path) -> str | None:
    """HEAD of the checkout *at* `repo_dir`, or None if it is not its root.

    `git -C <dir> rev-parse HEAD` walks UP the tree when <dir> has no .git
    of its own, and every pack lives inside ComfyUI's own checkout — so a
    pack installed from a mirror tarball (which _skip_junk deliberately
    strips .git from) answered with *ComfyUI's* HEAD. That stamped one
    wrong SHA onto every pack in the same run, and it was self-reinforcing:
    the next pod bootstrapped from those tarballs had no .git either.

    So confirm the repository git discovered is actually this directory
    before believing its answer, and return None rather than a lie when it
    is not — pack_nodes then leaves the pin alone instead of overwriting a
    good SHA with ComfyUI's.
    """
    def _git(*args) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(repo_dir), *args],
                                 capture_output=True, text=True, timeout=30)
        except Exception:
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    top = _git("rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != Path(repo_dir).resolve():
        log.warning("%s is not a git checkout root (no .git of its own) — "
                    "refusing to read a SHA that would belong to the "
                    "enclosing repo. Installed from a mirror tarball? Its "
                    "pin has to be set by hand.", repo_dir)
        return None
    return _git("rev-parse", "HEAD")


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
    # PINS.json is rebuilt from scratch every run, so anything a given pod
    # cannot re-derive silently vanishes from it. That is how three packs
    # lost their pins — their features were off on the boot run, so their
    # directories did not exist — and losing a pin does more than unpin:
    # node_pack_from_mirror looks the tarball up BY PIN, so a pack with no
    # pin never uses the mirror copy that is sitting right there, and
    # bootstrap falls back to cloning HEAD unpinned. Carry the previous
    # file forward and overwrite only what this run can actually prove.
    previous = {}
    if PINS_PATH.exists():
        try:
            previous = json.loads(PINS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("existing %s is unreadable (%s) — starting from "
                        "scratch, so unbuilt packs will lose their pins.",
                        PINS_PATH, exc)

    for pack in manifest.get("node_packs", []):
        dirname = pack["dir"]
        src = custom_nodes / dirname
        kept = previous.get(dirname)
        kept_sha = kept.get("sha") if isinstance(kept, dict) else None
        if not src.is_dir():
            log.warning("node pack %s not present at %s — skipping (was its "
                        "feature enabled on the boot run?)", dirname, src)
            if kept_sha:
                pins[dirname] = kept
                log.info("  ... keeping its recorded pin (%s)", kept_sha[:8])
            continue
        sha = _git_sha(src)
        if sha is None:
            # _git_sha refused rather than answering with the enclosing
            # ComfyUI checkout's HEAD. An unverifiable pin must not be
            # invented: keep the one already on record and leave the
            # tarball that matches it alone on the mirror.
            if kept_sha:
                pins[dirname] = kept
                log.warning("%s: cannot confirm this checkout's SHA — "
                            "keeping the recorded pin (%s) and not "
                            "re-packing.", dirname, kept_sha[:8])
            else:
                log.error("%s: cannot confirm its SHA and there is no pin on "
                          "record — leaving it unpinned. Re-run from a real "
                          "git clone of the pack, or set its pin by hand.",
                          dirname)
            continue
        short = sha[:8]
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
    # the list to break you, so its SHA is always recorded. Same rule as
    # the packs: a SHA that cannot be confirmed must not replace one that
    # was.
    comfy_sha = _git_sha(COMFY_DIR)
    if comfy_sha is None and isinstance(previous.get("ComfyUI"), dict):
        pins["ComfyUI"] = previous["ComfyUI"]
        log.warning("cannot confirm ComfyUI's SHA at %s — keeping the "
                    "recorded pin (%s).", COMFY_DIR,
                    (pins["ComfyUI"].get("sha") or "?")[:8])
    else:
        pins["ComfyUI"] = {
            "url": "https://github.com/comfyanonymous/ComfyUI.git",
            "sha": comfy_sha,
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
            info = api.model_info(repo_id)
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
def probe_remote(api: HfApi, user: str, repos: dict, items: list[Item],
                 strict: bool = True) -> None:
    """Fill in remote_size so the plan can say what will actually upload.

    `strict` is off only for --dry-run. A real run must stop when the Hub
    cannot be reached, or it would read "not in the repo" for every file
    and re-upload the whole mirror the moment the network came back; a
    checklist is still worth printing without it.
    """
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
        except Exception as exc:
            if strict:
                raise
            log.warning("Could not read %s (%s) — its files are listed as "
                        "not in the repo.", repo_id, exc)
            continue
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
        mark = {ACT_SKIP: "✓", ACT_MIRRORED: "✓",
                ACT_MISSING: "✗"}.get(it.action, "↑")
        size = human(it.size) if it.present else "—"
        print(f"  {mark} {it.path_in_repo:<{width}}  {size:>10}  "
              f"{it.action}" + (f"   [{it.note}]" if it.note else ""))


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the checklist and exit; touch nothing")
    ap.add_argument("--audit", action="store_true",
                    help="report catalogue LoRAs and models with no mirror, "
                         "then exit. Needs no HF token.")
    ap.add_argument("--catalog", metavar="FILE", type=Path,
                    help="read the catalogue from FILE (the shape of "
                         "license-validator/data/assets.json) instead of "
                         "POST /v1/catalog")
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

    # Before the catalogue is read: the node-pack SHAs are the one thing a
    # destroyed pod cannot give back, so capturing them must not be able to
    # fail over a licence key or an unreachable licence server.
    if args.pins_only:
        return capture_pins_only(manifest, args.staging)

    cat = load_catalogue(args.catalog)
    rows = audit(manifest, cat)
    unmirrored = [r for r in rows if r.status == MIR_NONE]
    if args.audit:
        print_audit(rows, cat.origin)
        return 1 if unmirrored else 0
    if unmirrored:
        log.warning("%d catalogue record(s) have no mirror: %s",
                    len(unmirrored), ", ".join(r.id for r in unmirrored))

    unknown = set(args.only or []) - set(repos)
    if unknown:
        raise SystemExit(f"--only: no such repo key {sorted(unknown)}. "
                         f"Manifest defines: {sorted(repos)}")

    # A dry run must be readable by anyone who can run --audit: the mirror
    # is public, so it is probed anonymously as the account the pods read
    # from (mirror.py's MIRROR_USER). A real run needs the write token and
    # takes the account from it.
    api, user = None, None
    if HF_WRITE_TOKEN:
        api = HfApi(token=HF_WRITE_TOKEN)
        try:
            user = api.whoami()["name"]
            log.info("Authenticated as %s", user)
        except Exception as exc:
            log.error("Could not authenticate to Hugging Face: %s", exc)
            if not args.dry_run:
                return 2
    elif not args.dry_run:
        log.error("No HF write token. Set HF_WRITE_TOKEN (a token with "
                  "*write* scope) or fill in the constant at the top of "
                  "this file.")
        return 2
    if user is None:
        api, user = HfApi(), mirror.MIRROR_USER
        log.info("Dry run without a usable HF_WRITE_TOKEN — reading the "
                 "mirror anonymously as %s%s.", user,
                 "" if args.public else " (the repos are private, so every "
                 "file will look absent)")

    items = build_plan(manifest, args)
    add_catalogue_loras(items, cat, repos, user,
                        upstream_only_repos(manifest))
    node_items, pins = pack_nodes(manifest, args.staging)
    items += node_items
    if args.only:
        items = [i for i in items if i.repo_key in args.only]

    for it in items:
        it.present = it.local.exists()
        it.size = it.local.stat().st_size if it.present else 0
    probe_remote(api, user, repos, items, strict=not args.dry_run)
    print_checklist(repos, items)

    to_fetch = [i for i in items if i.action == ACT_FETCH]
    stranded = [i for i in items if i.action == ACT_MISSING]
    to_upload = [i for i in items if i.action in (ACT_UPLOAD, ACT_REUPLOAD)]
    already = [i for i in items if i.action in _ACT_DONE]
    print(f"\n  {len(items)} items · {len(already)} already mirrored · "
          f"{len(to_fetch)} to download · "
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
        will_land = (ACT_UPLOAD, ACT_REUPLOAD, *_ACT_DONE) + (
            () if args.skip_downloads else (ACT_FETCH,))
        write_records(pending_records(items, user, repos,
                                      lambda i: i.action in will_land),
                      dry_run=True)
        print("\n  --dry-run: nothing downloaded, no repos created, no "
              "records written.\n")
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

    # ── Catalogue records ────────────────────────────────────────────────
    # Only once the file is in the repo: a record pointing at a mirror
    # that does not hold the file sends every pod to a 404 first. A file
    # that was already there counts — that is an earlier run whose upload
    # finished and whose record write did not.
    unrecorded = write_records(pending_records(
        items, user, repos,
        lambda i: i in already or (i in live and i not in failed)),
        dry_run=False)

    # ── Manifests ────────────────────────────────────────────────────────
    add_upstream_revisions(api, manifest, pins)
    write_pins(pins)
    try:
        ensure_repos(api, user, repos, ["nodes"], not args.public)
        upload_json(api, user, repos, "nodes", "PINS.json", pins)
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
                                    if i.action in _ACT_DONE],
        "missing_no_fetcher": [str(i.local) for i in stranded],
        "catalogue_not_mirrored_at_start": [r.id for r in unmirrored],
        "mirror_not_recorded": [
            {"id": lora_id, "repo": repo, "path": path}
            for lora_id, repo, path in unrecorded],
        "bytes_uploaded": sum(i.size for i in live if i not in failed),
    }, indent=2))

    print(f"\n  Uploaded {ok}/{len(live)} · {len(failed)} failed")
    if failed:
        for it in failed:
            print(f"    ✗ {it.path_in_repo}")
        print("  Re-run to retry — completed uploads are skipped by size.")
    if unrecorded:
        print(f"  {len(unrecorded)} mirror location(s) not recorded — run "
              f"the commands above, or re-run with KREA2_ADMIN_TOKEN set.")
    print(f"  PINS.json  → {PINS_PATH}  (commit this)")
    print(f"  report     → {report_path}\n")
    return 1 if failed or unrecorded else 0


if __name__ == "__main__":
    sys.exit(main())
