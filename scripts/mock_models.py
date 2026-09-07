"""Create empty placeholder weights for everything the downloader would fetch.

    KREA2_BASE_DIR=tmp2 python scripts/mock_models.py            # create them
    KREA2_BASE_DIR=tmp2 python scripts/mock_models.py --check    # what would still download?

Runs every asset group in downloads.ASSET_GROUPS — whatever the licence
says — with the network stubbed out, so each "download" is a zero-byte
file at exactly the path the real code checks (`dest.exists()`). The next
real run of the app then logs "✓ (cached)" for all of it and downloads
nothing, which is what a dev box testing every tab wants: the tabs build,
the dropdowns fill, and no weight is fetched. A generation would of course
fail on an empty file; this is for exercising the app, not the models.

Existing files are never touched, so a real weight that is already in
place stays a real weight, and re-running only adds what a config change
introduced. What was created is appended to <KREA2_BASE_DIR>/mock-manifest.txt
so the placeholders can be told apart from real downloads later.

It uses the real fetch functions rather than a copy of their path logic,
so a new model in config.py is covered the day it is added: the only
things replaced are the two mirror lookups, huggingface_hub's two download
calls, `requests.get` (CivitAI and GitHub release assets), and the
abliterated-encoder merge, which cannot run on empty shards.

--check installs stubs that *fail* instead, runs the same groups, and lists
every file a real run would have gone to the network for. Exit status 1 if
there is any — so it doubles as "is this base dir complete?".
"""

import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if not os.environ.get("KREA2_BASE_DIR"):
    sys.exit("Set KREA2_BASE_DIR to the base directory to mock into "
             "(e.g. KREA2_BASE_DIR=tmp2).")

import downloads  # noqa: E402
from config import (  # noqa: E402
    ABLITERATED_ENCODER_FILE,
    BASE_DIR,
    MODELS_DIR,
    REACTOR_INSIGHTFACE_PACK,
)

# The unzipped buffalo_l pack, as fetch_insightface_pack expects to find
# it inside the archive. det_10g.onnx is the one it checks for.
BUFFALO_MEMBERS = ("det_10g.onnx", "w600k_r50.onnx", "genderage.onnx",
                   "1k3d68.onnx", "2d106det.onnx")

# What snapshot_download would leave for the NSFW detector; config.json is
# the one fetch_nsfw_detector checks for.
NSFW_MEMBERS = ("config.json", "preprocessor_config.json", "model.safetensors")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


# ── stubs that create ───────────────────────────────────────────────────

def fake_hf_hub_download(repo_id, filename, local_dir=None, token=None,
                         revision=None, repo_type=None, **_):
    """The file lands where the real call would put it, empty.

    The one exception is a .zip, which must be a real archive because
    fetch_insightface_pack unpacks it with zipfile before deleting it.
    """
    base = Path(local_dir) if local_dir else MODELS_DIR / ".hf-fake"
    dest = base / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        if dest.suffix == ".zip":
            with zipfile.ZipFile(dest, "w") as zf:
                for name in BUFFALO_MEMBERS:
                    zf.writestr(f"{REACTOR_INSIGHTFACE_PACK}/{name}", b"")
        else:
            dest.touch()
    return str(dest)


def fake_snapshot_download(repo_id, local_dir=None, token=None, revision=None,
                           allow_patterns=None, ignore_patterns=None, **_):
    """Only the NSFW detector reaches this (the encoder merge is stubbed)."""
    if not local_dir:
        raise RuntimeError(f"unexpected snapshot_download({repo_id}) with "
                           "no local_dir — nothing to fake")
    for name in NSFW_MEMBERS:
        _touch(Path(local_dir) / name)
    return str(local_dir)


def fake_abliterated_encoder():
    """The real one merges shards with safetensors, which empty files cannot."""
    dest = MODELS_DIR / "text_encoders" / ABLITERATED_ENCODER_FILE
    if dest.exists():
        downloads.log.info("✓ %s (cached)", ABLITERATED_ENCODER_FILE)
        return
    downloads.log.info("↓ %s (placeholder)", ABLITERATED_ENCODER_FILE)
    _touch(dest)


class _FakeResponse:
    """An empty 200 — the manual-resume downloaders write 0 bytes and rename."""
    status_code = 200
    headers = {"content-length": "0", "content-type": "application/octet-stream"}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=None):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeRequests:
    @staticmethod
    def get(*_, **__):
        return _FakeResponse()


def install_create_stubs():
    downloads.from_mirror = lambda dest, relpath: False
    downloads.dir_from_mirror = lambda local_prefix: False
    downloads.hf_hub_download = fake_hf_hub_download
    downloads.snapshot_download = fake_snapshot_download
    downloads.fetch_abliterated_encoder = fake_abliterated_encoder
    downloads.requests = _FakeRequests


# ── stubs that refuse ───────────────────────────────────────────────────

class NetworkCalled(RuntimeError):
    pass


def _refuse(*_, **__):
    raise NetworkCalled("a real run would download this")


class _RefusingRequests:
    get = staticmethod(_refuse)


def install_check_stubs():
    downloads.from_mirror = lambda dest, relpath: False
    downloads.dir_from_mirror = lambda local_prefix: False
    downloads.hf_hub_download = _refuse
    downloads.snapshot_download = _refuse
    downloads.requests = _RefusingRequests


class _WouldFetch(logging.Handler):
    """Collects the "↓ …" lines every fetch function logs before it fetches."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith("↓"):
            self.lines.append(msg[1:].strip())


# ── run ─────────────────────────────────────────────────────────────────

def snapshot():
    files = {p for p in MODELS_DIR.rglob("*") if p.is_file()}
    dirs = {p for p in MODELS_DIR.rglob("*") if p.is_dir()}
    return files, dirs


def prune_empty_dirs(before_dirs):
    """Drop the staging folders the fakes left behind (split_files/, models/…).

    Only directories that did not exist before this run and are empty now:
    a real download's layout is never touched.
    """
    for d in sorted((p for p in MODELS_DIR.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        if d not in before_dirs and not any(d.iterdir()):
            d.rmdir()


def run_groups():
    for name, fetch in downloads.ASSET_GROUPS.items():
        print(f"── {name}")
        try:
            fetch()
        except Exception as exc:  # an unguarded fetch inside a group
            print(f"   group stopped early: {exc}")


def check():
    # No retries, no sleeps: every refusal is final.
    downloads.DOWNLOAD_RETRIES = 1
    install_check_stubs()
    seen = _WouldFetch()
    downloads.log.addHandler(seen)
    run_groups()
    downloads.log.removeHandler(seen)

    if not seen.lines:
        print("\nnothing would be downloaded — every file the downloader "
              "looks for is present")
        return 0
    print(f"\n{len(seen.lines)} file(s) a real run would download:")
    for line in seen.lines:
        print(f"  {line}")
    return 1


def create():
    install_create_stubs()
    before_files, before_dirs = snapshot()
    print(f"models dir  {MODELS_DIR}")
    print(f"present     {len(before_files)} file(s) before\n")

    run_groups()

    prune_empty_dirs(before_dirs)
    after_files, _ = snapshot()
    created = sorted(after_files - before_files)
    nonempty = [p for p in created if p.stat().st_size]

    print(f"\ncreated     {len(created)} placeholder(s)")
    for p in created:
        print(f"  {p.relative_to(MODELS_DIR).as_posix()}")
    if nonempty:
        print("\nWARNING these new files are not empty (unexpected):")
        for p in nonempty:
            print(f"  {p}")

    if created:
        manifest = BASE_DIR / "mock-manifest.txt"
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(f"# {datetime.now().isoformat(timespec='seconds')} "
                     f"scripts/mock_models.py\n")
            for p in created:
                fh.write(p.relative_to(BASE_DIR).as_posix() + "\n")
        print(f"\nmanifest    {manifest}")
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stdout)
    # The retry helper sleeps between attempts; nothing here should wait.
    downloads.time.sleep = lambda *_: None
    return check() if "--check" in sys.argv[1:] else create()


if __name__ == "__main__":
    sys.exit(main())
