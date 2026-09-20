"""Model + LoRA downloads.

Base models come from Hugging Face via huggingface_hub (which resumes
partial downloads automatically); LoRAs come from CivitAI with manual
resume (HTTP Range), retries and useful error messages. Everything is
idempotent — re-running only downloads what is missing.

Two kinds of weights, two sources of truth. The pipeline pieces every
build needs — VAEs, text encoders, the Identity Edit LoRA, the Wan and
MiniMax weights — are named in config.py and fetched by their own groups.
The Krea models and the Krea and MiniMax LoRAs a customer picks from are
named by the catalogue (catalog.py, from the licence server) and fetched by the
"catalog" group, which asks nothing of config.py at all.
"""

import time
from pathlib import Path

import requests
from huggingface_hub import hf_hub_download, snapshot_download

from ember.licensing import catalog
from ember import features
from ember.weights import mirror
from ember.config import (
    ABLITERATED_ENCODER_FILE,
    ABLITERATED_ENCODER_REPO,
    CIVITAI_TOKEN,
    EDIT_LORA_FILE,
    EDIT_LORA_REPO,
    HF_MODEL_FILES,
    HF_MODEL_REPO,
    HF_TOKEN,
    MINIMAX_HF_FILES,
    MINIMAX_HF_REPO,
    MINIMAX_TURBO_LORA,
    MINIMAX_TURBO_LORA_REPO,
    MODELS_DIR,
    TEXT_ENCODER_FILE,
    V2_VAE_FILE,
    V2_VAE_HF_PATH,
    V2_VAE_HF_REPO,
    WAN_HF_FILES,
    WAN_HF_REPO,
    log,
)

DOWNLOAD_CHUNK = 8 * 1024 * 1024
DOWNLOAD_RETRIES = 3


def _with_retries(fn, desc: str):
    """Call fn() with exponential-backoff retries."""
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == DOWNLOAD_RETRIES:
                raise
            wait = 5 * 2 ** (attempt - 1)
            log.warning(
                "%s failed (attempt %d/%d): %s — retrying in %ds",
                desc, attempt, DOWNLOAD_RETRIES, exc, wait,
            )
            time.sleep(wait)


def from_mirror(dest: Path, relpath: str) -> bool:
    """Try YOUR Hugging Face mirror for `relpath`. True if dest now exists.

    Never raises. A mirror miss — deleted file, expired token, HF outage —
    must degrade to the upstream fallback rather than abort a download that
    upstream could still satisfy. That is the whole point of having two
    sources; a mirror that can take the app down with it is not redundancy.

    The mirror itself is not pinned to a revision: it is your repo, only
    ever appended to, and pinning it would mean re-pinning after every
    upload. Upstream *is* pinned, because that is the one you do not
    control.
    """
    loc = mirror.location(relpath)
    if not loc:
        return False
    repo, path_in_repo = loc
    try:
        got = _with_retries(
            lambda: hf_hub_download(
                repo_id=repo, filename=path_in_repo,
                local_dir=MODELS_DIR, token=mirror.token(),
            ),
            desc=f"{relpath} (mirror)",
        )
        src = Path(got)
        if src.resolve() != dest.resolve():
            # The mirror's path in its repo need not be where ComfyUI
            # looks, so the file is moved into place (same filesystem).
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
        log.info("✓ %s (mirror: %s)", relpath, repo)
        return True
    except Exception as exc:
        log.warning("Mirror %s could not serve %s (%s) — falling back to "
                    "upstream.", repo, path_in_repo, exc)
        return False


def fetch_hf_file(relpath: str) -> None:
    """Download one Comfy-Org/Krea-2 file into MODELS_DIR, keeping its subfolder."""
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", relpath)
        return
    if from_mirror(dest, relpath):
        return
    log.info("↓ %s ...", relpath)
    _with_retries(
        lambda: hf_hub_download(
            repo_id=HF_MODEL_REPO,
            filename=relpath,
            local_dir=MODELS_DIR,
            token=HF_TOKEN,
            revision=mirror.revision(HF_MODEL_REPO),
        ),
        desc=relpath,
    )


def fetch_hf_repo_file(repo: str, relpath: str) -> None:
    """Download one file from any HF model repo laid out the way ComfyUI is.

    fetch_hf_file is this with the repo fixed to Comfy-Org/Krea-2, and
    fetch_hf_file_to is for a repo whose layout is *not* ComfyUI's. The
    MiniMax repo keeps diffusion_models/, text_encoders/ and vae/ exactly
    where ComfyUI wants them, so the file lands in place with no rename —
    mirror first, then upstream at the pinned revision, like the rest.
    """
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", relpath)
        return
    if from_mirror(dest, relpath):
        return
    log.info("↓ %s (from %s) ...", relpath, repo)
    _with_retries(
        lambda: hf_hub_download(
            repo_id=repo,
            filename=relpath,
            local_dir=MODELS_DIR,
            token=HF_TOKEN,
            revision=mirror.revision(repo),
        ),
        desc=relpath,
    )


def fetch_abliterated_encoder() -> None:
    """Download the abliterated Qwen3-VL shards and merge them into one file."""
    relpath = f"text_encoders/{ABLITERATED_ENCODER_FILE}"
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", ABLITERATED_ENCODER_FILE)
        return
    # The mirror stores the *merged* file, so a hit here skips the shard
    # download and the merge below entirely — several minutes and a large
    # transient disk+RAM spike on every cold pod.
    if from_mirror(dest, relpath):
        return
    log.info("↓ %s (from %s) ...", ABLITERATED_ENCODER_FILE, ABLITERATED_ENCODER_REPO)
    snap = _with_retries(
        lambda: snapshot_download(
            repo_id=ABLITERATED_ENCODER_REPO, token=HF_TOKEN,
            ignore_patterns=["*.bin", "*.gguf", "*.json", "*.txt", "*.md",
                             "tokenizer*", "special_tokens*", "vocab*",
                             "merges*", "config*", "preprocessor*"],
        ),
        desc=ABLITERATED_ENCODER_FILE,
    )
    from safetensors.torch import load_file, save_file

    shards = sorted(Path(snap).glob("**/*.safetensors"))
    if not shards:
        raise FileNotFoundError(f"No safetensors shards found in {snap}")
    state_dict = {}
    for shard in shards:
        state_dict.update(load_file(str(shard)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    save_file(state_dict, str(dest))
    log.info("Merged abliterated encoder → %s", dest)


def fetch_edit_lora() -> None:
    """Download the Krea 2 Identity Edit LoRA into the loras folder."""
    relpath = f"loras/{EDIT_LORA_FILE}"
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", EDIT_LORA_FILE)
        return
    if from_mirror(dest, relpath):
        return
    log.info("↓ %s (from %s) ...", EDIT_LORA_FILE, EDIT_LORA_REPO)
    _with_retries(
        lambda: hf_hub_download(
            repo_id=EDIT_LORA_REPO,
            filename=EDIT_LORA_FILE,
            local_dir=MODELS_DIR / "loras",
            token=HF_TOKEN,
            revision=mirror.revision(EDIT_LORA_REPO),
        ),
        desc=EDIT_LORA_FILE,
    )


def fetch_repackaged_file(repo: str, relpath: str) -> None:
    """Download one file from a Comfy-Org repackaged repo into MODELS_DIR.

    The Wan 2.2 repo keeps everything under split_files/,
    which local_dir downloads would mirror — so the file is moved up one
    level afterwards (a same-filesystem rename, no extra disk needed).
    """
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", relpath)
        return
    if from_mirror(dest, relpath):
        return
    log.info("↓ %s (from %s) ...", relpath, repo)

    def _download():
        path = hf_hub_download(
            repo_id=repo,
            filename=f"split_files/{relpath}",
            local_dir=MODELS_DIR,
            token=HF_TOKEN,
            revision=mirror.revision(repo),
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        Path(path).rename(dest)

    _with_retries(_download, desc=relpath)


def fetch_civitai_file(version_id: int, filename: str,
                       subdir: str = "loras") -> None:
    """Download a CivitAI model version with resume support and retries."""
    dest = MODELS_DIR / subdir / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        log.info("✓ %s (cached)", filename)
        return
    # CivitAI is the source most likely to have deleted the file by now,
    # so the mirror matters more here than anywhere else — and it needs no
    # CIVITAI_TOKEN.
    if from_mirror(dest, f"{subdir}/{filename}"):
        return

    def _download():
        part = dest.with_suffix(dest.suffix + ".part")
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        params = {"token": CIVITAI_TOKEN} if CIVITAI_TOKEN else {}
        with requests.get(
            f"https://civitai.com/api/download/models/{version_id}",
            params=params, headers=headers, stream=True,
            timeout=(15, 120), allow_redirects=True,
        ) as resp:
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"CivitAI refused the download (HTTP {resp.status_code}). "
                    "Set the CIVITAI_TOKEN environment variable."
                )
            if resp.status_code == 416:  # the .part file is already complete
                part.rename(dest)
                return
            resp.raise_for_status()
            if "text/html" in resp.headers.get("content-type", ""):
                raise RuntimeError(
                    "CivitAI returned a web page instead of a file — the "
                    "version id may be wrong, or the file requires login "
                    "(set CIVITAI_TOKEN)."
                )
            resuming = resume_from > 0 and resp.status_code == 206
            with open(part, "ab" if resuming else "wb") as fh:
                for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK):
                    fh.write(chunk)
            expected = resp.headers.get("content-length")
            received = part.stat().st_size - (resume_from if resuming else 0)
            if expected and received != int(expected):
                raise IOError(
                    f"Truncated download: got {received} of {expected} bytes"
                )
        part.rename(dest)

    log.info("↓ %s (CivitAI version %d) ...", filename, version_id)
    _with_retries(_download, desc=filename)


def fetch_hf_file_to(repo: str, relpath: str, dest: Path) -> None:
    """Download one file from a HF *model* repo to an exact local path.

    fetch_hf_file mirrors the repo's layout under MODELS_DIR, which only
    works when that layout already matches ComfyUI's. The V2 VAE lives at
    vae/wan/ upstream and has to land in vae/, so it is placed explicitly.
    """
    if dest.exists():
        log.info("✓ %s (cached)", dest.name)
        return
    if from_mirror(dest, dest.relative_to(MODELS_DIR).as_posix()):
        return
    log.info("↓ %s (from %s) ...", relpath, repo)

    def _download():
        path = hf_hub_download(repo_id=repo, filename=relpath,
                               local_dir=MODELS_DIR, token=HF_TOKEN,
                               revision=mirror.revision(repo))
        dest.parent.mkdir(parents=True, exist_ok=True)
        Path(path).rename(dest)

    _with_retries(_download, desc=relpath)


def _from_record_mirror(dest: Path, record) -> bool:
    """Try the mirror a catalogue record names itself. True if dest now exists.

    The record's own `mirror` ({repo, path}) comes first because it is the
    one place a LoRA added in the DB can say where its copy lives: the
    bundled mirror_manifest.json is compiled into the binary, so it can only
    ever know about files that existed when this build was made.

    Same posture as from_mirror: never raises, not pinned (the mirror is
    ours and only appended to), and anonymous when the mirror is public —
    mirror.token() decides, so a stale HF_TOKEN cannot turn a public file
    into a 401. KREA2_NO_MIRROR turns this off along with the manifest.
    """
    if not record.mirror or not mirror.MIRROR_ENABLED:
        return False
    repo, path_in_repo = record.mirror["repo"], record.mirror["path"]
    try:
        got = _with_retries(
            lambda: hf_hub_download(
                repo_id=repo, filename=path_in_repo,
                local_dir=MODELS_DIR, token=mirror.token(),
            ),
            desc=f"{record.file} (mirror)",
        )
        src = Path(got)
        if src.resolve() != dest.resolve():
            # The mirror's layout is its own business — a path in the repo
            # that is not where ComfyUI looks lands under MODELS_DIR at that
            # path, and is moved into place (same filesystem, no copy).
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
        log.info("✓ %s (mirror: %s)", dest.relative_to(MODELS_DIR).as_posix(),
                 repo)
        return True
    except Exception as exc:
        log.warning("Mirror %s could not serve %s (%s) — falling back.",
                    repo, path_in_repo, exc)
        return False


def fetch_catalog_file(record, subdir: str) -> None:
    """Fetch one catalogue model or LoRA into MODELS_DIR/<subdir>/<file>.

    Tried in order, first hit wins:

      1. already on disk                  cached
      2. the record's own `mirror`        _from_record_mirror
      3. the bundled manifest's mirror    from_mirror, inside step 4's call
      4. the record's `source`            CivitAI, or HF at the pinned revision

    Step 3 is not called here because both upstream fetchers already try it
    first, keyed on the same relative path. Calling it here as well would
    make a failing manifest mirror run its whole retry cycle twice per file.

    Raises when every source failed; download_catalog decides what that
    costs, which is one dropdown choice rather than the setup.
    """
    relpath = f"{subdir}/{record.file}"
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", relpath)
        return
    if _from_record_mirror(dest, record):
        return
    source = record.source
    if source["kind"] == "civitai":
        fetch_civitai_file(source["version"], record.file, subdir=subdir)
    else:
        # Upstream's layout (e.g. vae/wan/ or a repo root) need not be
        # ComfyUI's, so the file is placed at dest explicitly.
        fetch_hf_file_to(source["repo"], source["path"], dest)


def download_catalog() -> None:
    """Fetch every model and LoRA the enabled catalogue features offer.

    What to fetch is the union of the catalogue lists of the features that
    need this group and are on — the four Krea tabs and the two MiniMax
    tabs, which list LoRAs only. A tab that is off contributes nothing, so
    a V2-only licence never downloads a model only Krea2 offers, and vice
    versa.

    Each file once. The same LoRA sits in every tab's list and two model
    records may share one file (same weights, different steps/CFG), so the
    union is keyed on the destination path, not on the id — that is the
    thing that actually costs bandwidth and disk.

    Every item is independent: a missing file greys out one dropdown choice
    (the tab reports it as not downloaded) and the next run retries it.
    Models first, because a tab with no model cannot run at all and a tab
    with a missing LoRA only loses one row.
    """
    keys = features.enabled_needing("catalog")
    cat = catalog.get()
    wanted: dict[str, tuple] = {}          # relpath -> (record, subdir, kind)
    for key in keys:
        for model in cat.feature_models(key):
            wanted.setdefault(f"diffusion_models/{model.file}",
                              (model, "diffusion_models", "model"))
    for key in keys:
        for lora in cat.feature_loras(key):
            wanted.setdefault(f"loras/{lora.file}", (lora, "loras", "LoRA"))
    if not wanted:
        log.warning("The catalogue lists no models or LoRAs for %s — "
                    "nothing to download for those tabs.",
                    ", ".join(keys) or "(no enabled feature)")
        return
    log.info("Catalogue files for %s: %d", ", ".join(keys), len(wanted))
    for relpath, (record, subdir, kind) in wanted.items():
        try:
            fetch_catalog_file(record, subdir)
        except Exception as exc:
            log.error("Catalogue %s %s (%s) unavailable (%s) — that "
                      "dropdown choice will refuse to run until a later run "
                      "fetches it.", kind, record.id, relpath, exc)


def download_v2_models() -> None:
    """Fetch the Wan 2.1 VAE the Krea 2 V2 pipeline decodes with (~0.25 GB).

    The V2 tabs' models and LoRAs are catalogue entries, fetched by the
    "catalog" group; this is the one V2-only pipeline file the catalogue
    does not describe. A failure disables only the V2 tabs, which name what
    they are waiting for, and the next run retries it.
    """
    try:
        fetch_hf_file_to(V2_VAE_HF_REPO, V2_VAE_HF_PATH,
                         MODELS_DIR / "vae" / V2_VAE_FILE)
    except Exception as exc:
        log.error("Krea 2 V2 VAE %s unavailable (%s) — the V2 tab will "
                  "refuse to run until a later run fetches it.",
                  V2_VAE_FILE, exc)


def download_text_encoder() -> None:
    """Fetch the Qwen3-VL text encoder every Krea 2 pipeline shares.

    The abliterated build is preferred and the stock one is the fallback,
    so a failure here costs prompt latitude rather than a working tab.
    """
    try:
        fetch_abliterated_encoder()
    except Exception as exc:
        log.error(
            "Abliterated encoder unavailable (%s) — "
            "falling back to the standard encoder.", exc,
        )
        fetch_hf_file(f"text_encoders/{TEXT_ENCODER_FILE}")


def download_krea2_models() -> None:
    """Fetch the Qwen image VAE the Krea2 and Krea2 Edit tabs decode with
    (~0.25 GB).

    Shared by both tabs — whichever of them is on pulls this group in, and
    it is fetched once however many of them are. Their models and LoRAs
    are catalogue entries, fetched by the "catalog" group; this is only the
    pipeline file the catalogue does not describe.
    """
    for relpath in HF_MODEL_FILES:
        fetch_hf_file(relpath)


def download_edit_lora() -> None:
    """Fetch the Krea 2 Identity Edit LoRA (~1.9 GB) for the Edit tab."""
    try:
        fetch_edit_lora()
    except Exception as exc:
        # The Edit tab warns when this file is missing; everything else works.
        log.error("Identity Edit LoRA unavailable (%s) — the Edit tab will "
                  "stay disabled until it downloads on a later run.", exc)


def download_wan_models() -> None:
    """Fetch the Wan 2.2 image-to-video models (~49 GB)."""
    for relpath in WAN_HF_FILES:
        try:
            fetch_repackaged_file(WAN_HF_REPO, relpath)
        except Exception as exc:
            # A missing Wan file only degrades the Video tab; the Krea
            # tabs must never be affected by it.
            log.error("Wan 2.2 file %s unavailable (%s) — the Video tab "
                      "will refuse to run until a later run fetches it.",
                      relpath, exc)


def download_minimax_models() -> None:
    """Fetch the MiniMax H3 weights (~56 GB) — both MiniMax tabs share them.

    Every item is independent, the same posture as the Wan downloader: a
    missing file degrades only the two MiniMax tabs, which name what they
    are waiting for, and the next run retries it. The four Comfy-Org files
    download straight into place; the turbo LoRA sits at the root of
    lightx2v's repo and has to be placed under loras/ by hand.
    """
    for relpath in MINIMAX_HF_FILES:
        try:
            fetch_hf_repo_file(MINIMAX_HF_REPO, relpath)
        except Exception as exc:
            log.error("MiniMax H3 file %s unavailable (%s) — the MiniMax "
                      "tabs will refuse to run until a later run fetches "
                      "it.", relpath, exc)
    try:
        fetch_hf_file_to(MINIMAX_TURBO_LORA_REPO, MINIMAX_TURBO_LORA,
                         MODELS_DIR / "loras" / MINIMAX_TURBO_LORA)
    except Exception as exc:
        log.error("MiniMax H3 turbo LoRA %s unavailable (%s) — the MiniMax "
                  "tabs will refuse to run until a later run fetches it.",
                  MINIMAX_TURBO_LORA, exc)


# Asset group → the function that fetches it. Iteration order is download
# order, so the cheap shared pieces land before the tens of gigabytes.
ASSET_GROUPS = {
    "text_encoder": download_text_encoder,
    "krea2": download_krea2_models,
    "edit_lora": download_edit_lora,
    "v2": download_v2_models,
    "catalog": download_catalog,
    "wan": download_wan_models,
    "minimax": download_minimax_models,
}

# Groups that can pull a file from CivitAI, which is the only source here
# that usually needs a token. Only the catalogue does now — whether it
# actually will depends on the records, but a warning that is sometimes
# unnecessary beats a silent run of refused downloads.
CIVITAI_GROUPS = {"catalog"}


def download_everything() -> None:
    """Fetch exactly what the enabled features need, and nothing else.

    Driven by features.assets() rather than a fixed sequence: a feature
    that is off never reaches its downloader, which is where the flags
    actually save money — Wan is ~49 GB.
    """
    groups = features.assets()
    if not groups:
        log.info("No feature needs any model files — skipping downloads")
        return

    # A group named in features.py with no downloader here would otherwise
    # fetch nothing at all and only surface as an empty model dropdown much
    # later, so say it plainly at the point the two lists disagree.
    unknown = groups - set(ASSET_GROUPS)
    if unknown:
        log.error(
            "No downloader for asset group(s): %s — the features needing "
            "them will have no models. features.py and downloads.py "
            "disagree; this is a bug, not a configuration problem.",
            ", ".join(sorted(unknown)),
        )

    log.info("Downloading asset groups: %s", ", ".join(sorted(groups)))
    if CIVITAI_GROUPS & groups and not CIVITAI_TOKEN:
        log.warning(
            "CivitAI LoRAs are configured but no CIVITAI_TOKEN environment "
            "variable is set — trying anonymously (many downloads will be "
            "refused)."
        )
    for name, fetch in ASSET_GROUPS.items():
        if name in groups:
            fetch()
