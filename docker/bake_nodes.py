"""Populate the image's ComfyUI tree at build time.

Runs inside the Dockerfile's first stage, never on a pod. It exists so the
image and the app cannot disagree about which revision of anything is
correct: rather than repeating ComfyUI's SHA and the node pack list in
shell, it calls bootstrap.py's own helpers against scripts/PINS.json and
scripts/mirror_manifest.json — the same two files mirror.py reads at
runtime. Bump a pin and the next image build follows it with no edit here.

What this does NOT do is install anything with pip. The clones land in a
build stage that is thrown away; only /opt/krea2 is copied forward, and the
Dockerfile does the pip passes in the final stage where they will survive.

Which packs get baked, and why each is treated differently:

    ComfyUI                 pinned in PINS.json -> full clone + checkout
    comfyui-krea2edit       has a mirror tarball -> that, exactly as
                            bootstrap.install_node_pack prefers it
    the V2 packs            unpinned today, so bootstrap clones HEAD at
                            boot; here they are cloned at build time
                            instead, which freezes them per image tag.
                            That is a small improvement over the current
                            behaviour rather than a change of policy: two
                            pods from one image now agree, where two pods
                            booted a week apart did not.

ComfyUI-ReActor is absent on purpose — it is vendored in deps/ and the
Dockerfile copies it in directly, the same way install_reactor() does.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Set before importing config: BASE_DIR is read at import time, and
# everything downstream (COMFY_DIR in particular) is derived from it. This
# is the whole trick that lets the app's own installers write to the image
# path instead of a pod path.
BAKE_ROOT = os.environ.get("KREA2_BAKE_ROOT", "/opt/krea2")
os.environ["KREA2_BASE_DIR"] = BAKE_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap                                             # noqa: E402
from config import (                                         # noqa: E402
    COMFY_DIR,
    KREA2EDIT_NODES_REPO,
    V2_NODE_REPOS,
    log,
)
import mirror                                                # noqa: E402


def git_sha(path: Path) -> str | None:
    """The checked-out revision of `path`, or None if it is not a checkout."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() or None


def strip_git(path: Path) -> None:
    """Drop the .git directory once the revision is recorded.

    ComfyUI has to be cloned in full rather than shallow — a pin is usually
    an older commit and `--depth 1` cannot check one out — and that history
    is several hundred megabytes in an image that nobody will run `git pull`
    in. Nothing at runtime reads it: ComfyUI reports its version from
    comfyui_version.py, and ComfyUI-Manager (the one thing that would want a
    working tree) is not installed here.

    The SHA it would have told you is written to baked.json instead, which
    the entrypoint prints on every boot.
    """
    shutil.rmtree(path / ".git", ignore_errors=True)


def main() -> int:
    baked: dict[str, object] = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bake_root": BAKE_ROOT,
        "custom_nodes": {},
    }

    # ── ComfyUI ──────────────────────────────────────────────────────────
    # Fatal if it fails. Every other pack degrades to "that tab does not
    # work"; without ComfyUI there is no image worth publishing, and a
    # broken build is enormously cheaper to notice here than on a pod.
    log.info("Baking ComfyUI into %s", COMFY_DIR)
    bootstrap.clone_pinned(bootstrap.COMFYUI_REPO, COMFY_DIR, "ComfyUI",
                           "Cloning ComfyUI")
    baked["comfyui_sha"] = git_sha(COMFY_DIR)
    baked["comfyui_pin"] = mirror.comfyui_sha()
    if baked["comfyui_pin"] and baked["comfyui_sha"] != baked["comfyui_pin"]:
        log.error("ComfyUI checked out %s but PINS.json asks for %s",
                  baked["comfyui_sha"], baked["comfyui_pin"])
        return 1
    strip_git(COMFY_DIR)

    # ── Custom node packs ────────────────────────────────────────────────
    # Not fatal, and deliberately: install_custom_nodes() and
    # install_v2_nodes() both survive a pack that will not install, leaving
    # one tab reporting a missing node and the rest untouched. An image that
    # refuses to build because a third-party repo is having a bad morning
    # would be stricter than the app it ships.
    #
    # A pack that fails here is simply left absent, which puts it back on
    # the boot-time path it takes today — the image is then a partial
    # pre-warm rather than a broken one.
    packs = [("comfyui-krea2edit", KREA2EDIT_NODES_REPO)]
    packs += [(dirname, repo) for dirname, repo, _cls in V2_NODE_REPOS]

    for dirname, repo in packs:
        dest = COMFY_DIR / "custom_nodes" / dirname
        try:
            bootstrap.install_node_pack(dirname, repo, dest,
                                        f"Baking {dirname}")
        except Exception as exc:
            log.error("Could not bake %s (%s) — leaving it out of the "
                      "image; the app will install it at boot as it does "
                      "today.", dirname, exc)
            shutil.rmtree(dest, ignore_errors=True)
            baked["custom_nodes"][dirname] = None
            continue
        sha = git_sha(dest) or mirror.node_pin(dirname).get("sha")
        baked["custom_nodes"][dirname] = sha
        strip_git(dest)
        log.info("Baked %s (%s)", dirname, (sha or "unpinned")[:12])

    # config.py creates models/ and output/ under BASE_DIR at import time.
    # Here that is the image path, and the real ones live on the volume —
    # leaving empty twins next to the baked ComfyUI would only invite
    # someone to wonder which of the two ComfyUI is reading from.
    for stray in ("models", "output"):
        try:
            (Path(BAKE_ROOT) / stray).rmdir()
        except OSError:
            pass

    # Read by docker/entrypoint.sh on every boot, so what a running
    # container is made of is answerable from its own log rather than from
    # remembering which tag was deployed.
    manifest = Path(BAKE_ROOT) / "baked.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(baked, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    log.info("Wrote %s", manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
