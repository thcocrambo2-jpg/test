#!/usr/bin/env python3
"""Mirror the cloudflared release binaries to the public HF tools repo.

Operator tool, not part of the shipped app, and deliberately separate from
mirror_to_hf.py: that one is driven by mirror_manifest.json and is about
model weights under MODELS_DIR. cloudflared is neither. It is a tool the
*start scripts* fetch, before the app exists, from a plain URL that a
PowerShell script and a bash script can both understand.

Why mirror it at all
--------------------
serve.py downloads cloudflared from the GitHub "latest" release on first
launch, and since Gradio's share=True was removed the tunnel is the only
source of a public URL. That makes one GitHub endpoint a hard dependency
of every first start, and the failure mode is a customer with no link.

This applies the doctrine mirror.py already states for weights: mirror
first, upstream as the fallback, both pinned. Pinning matters as much as
mirroring here - "latest" is a moving target, and a start script that
verifies a checksum cannot verify a moving one.

Run it
------
    HF_WRITE_TOKEN=hf_... python3 scripts/mirror_cloudflared.py

It prints the three constants each start script needs. Paste them into
scripts/windows_start.ps1 and scripts/runpod_start.sh, then publish those
with `make start-ps1` and `make start-sh`. No app rebuild is involved:
serve.cloudflared_binary() returns early when the file is already at
BASE_DIR, so a pre-seeded binary is all it takes.

Refreshing to a newer cloudflared
---------------------------------
Bump RELEASE, run this again, paste the new constants. Paths are scoped by
version, so a refresh adds files and never overwrites - same rule
mirror_manifest.json states for weights. Worth doing occasionally:
Cloudflare does eventually stop accepting very old quick-tunnel clients,
and a frozen mirror is exactly the thing that would not notice.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError

# The pinned release. NOT "latest": a checksum can only be pinned to a
# specific build, and the whole point of the mirror is that the bytes stop
# moving. Published 2026-08-31.
RELEASE = "2026.8.3"

# Where it goes. Public, so a customer pod and a customer desktop both pull
# it anonymously - the same reason mirror_manifest.json sets mirror_public.
MIRROR_USER = os.environ.get("KREA2_MIRROR_USER", "thcocrambo2")
REPO_NAME = "krea2-tools"

UPSTREAM = ("https://github.com/cloudflare/cloudflared/releases/download/"
            "%s/%s")

# asset name on the GitHub release -> the name serve.RELEASES expects on
# disk. Kept in that order because the asset name is what identifies the
# bytes and the local name is only what makes them executable.
ASSETS = {
    "cloudflared-linux-amd64": "cloudflared",
    "cloudflared-windows-amd64.exe": "cloudflared.exe",
}


def path_in_repo(asset: str) -> str:
    return "cloudflared/%s/%s" % (RELEASE, asset)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(asset: str, into: Path) -> Path:
    """Download one release asset, or reuse the copy already here."""
    local = into / asset
    if local.exists():
        print("  have %s (%d bytes)" % (asset, local.stat().st_size))
        return local
    url = UPSTREAM % (RELEASE, asset)
    print("  downloading %s ..." % asset)
    # To .part and renamed, for the same reason the start scripts do it: a
    # truncated file that exists is worse than one that does not, because
    # the next run would trust it.
    partial = local.with_name(local.name + ".part")
    urllib.request.urlretrieve(url, partial)
    os.replace(partial, local)
    return local


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="download and hash, upload nothing")
    parser.add_argument("--work-dir", default=None,
                        help="where to keep the downloaded assets")
    args = parser.parse_args()

    work = Path(args.work_dir) if args.work_dir else Path.cwd() / ".cloudflared-mirror"
    work.mkdir(parents=True, exist_ok=True)

    print("cloudflared %s -> %s/%s" % (RELEASE, MIRROR_USER, REPO_NAME))
    local: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for asset in ASSETS:
        local[asset] = fetch(asset, work)
        digests[asset] = sha256(local[asset])
        print("  sha256 %s  %s" % (digests[asset], asset))

    if args.dry_run:
        print("\n--dry-run: nothing uploaded.")
        return 0

    # HF_WRITE_TOKEN rather than HF_TOKEN: the app's HF_TOKEN is a read
    # credential that ships in customer environments, and this is the only
    # script here that needs write. Keeping the names apart is what stops a
    # write token being the one a pod is handed.
    token = os.environ.get("HF_WRITE_TOKEN")
    if not token:
        print("ERROR: HF_WRITE_TOKEN is not set - it needs write access to "
              "%s." % MIRROR_USER, file=sys.stderr)
        return 1

    api = HfApi(token=token)
    repo_id = "%s/%s" % (MIRROR_USER, REPO_NAME)
    api.create_repo(repo_id, repo_type="model", private=False,
                    exist_ok=True)
    print("\nrepo %s ready" % repo_id)

    commit = None
    for asset in ASSETS:
        target = path_in_repo(asset)
        print("  uploading %s ..." % target)
        try:
            info = api.upload_file(
                path_or_fileobj=str(local[asset]),
                path_in_repo=target,
                repo_id=repo_id,
                repo_type="model",
                commit_message="cloudflared %s: %s" % (RELEASE, asset),
            )
        except HfHubHTTPError as exc:
            print("ERROR: uploading %s failed - %s" % (target, exc),
                  file=sys.stderr)
            return 1
        commit = getattr(info, "oid", None) or commit

    # The revision the start scripts pin to. A commit sha rather than
    # "main" for the reason PINS.json exists: mirroring protects against
    # deletion, pinning protects against change, and a mirror that can be
    # moved under the scripts is only the first half.
    if not commit:
        refs = api.list_repo_refs(repo_id, repo_type="model")
        for branch in refs.branches:
            if branch.name == "main":
                commit = branch.target_commit

    print("\n" + "=" * 70)
    print("Paste these into scripts/windows_start.ps1 and "
          "scripts/runpod_start.sh:")
    print("=" * 70)
    print("  revision  %s" % commit)
    for asset, name in ASSETS.items():
        print("\n  %s  (-> %s)" % (asset, name))
        print("    url  https://huggingface.co/%s/resolve/%s/%s"
              % (repo_id, commit, path_in_repo(asset)))
        print("    sha  %s" % digests[asset])
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
