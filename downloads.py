"""Model + LoRA downloads.

Base models come from Hugging Face via huggingface_hub (which resumes
partial downloads automatically); LoRAs come from CivitAI with manual
resume (HTTP Range), retries and useful error messages. Everything is
idempotent — re-running only downloads what is missing.
"""

import shutil
import time
import zipfile
from pathlib import Path

import requests
from huggingface_hub import hf_hub_download, snapshot_download

import features
import mirror
from config import (
    ABLITERATED_ENCODER_FILE,
    ABLITERATED_ENCODER_REPO,
    CIVITAI_LORAS,
    CIVITAI_TOKEN,
    EDIT_LORA_FILE,
    EDIT_LORA_REPO,
    FLUX_CIVITAI_LORAS,
    FLUX_HF_FILES,
    FLUX_HF_REPO,
    FLUX_LORA_SUBDIR,
    FLUX_MODELS,
    HF_LORA_FILES,
    HF_MODEL_FILES,
    HF_MODEL_REPO,
    HF_TOKEN,
    KLEIN_LORA_STACK,
    KLEIN_LORA_SUBDIR,
    KLEIN_MODELS,
    KLEIN_TEXT_ENCODER,
    KLEIN_TEXT_ENCODER_REPO,
    KLEIN_VAE,
    KLEIN_VAE_HF_REPO,
    KREA2_MODELS,
    MODELS_DIR,
    REACTOR_FACEDETECTION_FILES,
    REACTOR_HF_FILES,
    REACTOR_HF_REPO,
    REACTOR_INSIGHTFACE_PACK,
    REACTOR_INSIGHTFACE_ZIP,
    REACTOR_NSFW_DIR,
    REACTOR_NSFW_REPO,
    TEXT_ENCODER_FILE,
    V2_LORA_STACK,
    V2_MODELS,
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
            # Alias case: the mirror stores one canonical filename and
            # config.py asked for the other spelling of the same blob.
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
        log.info("✓ %s (mirror: %s)", relpath, repo)
        return True
    except Exception as exc:
        log.warning("Mirror %s could not serve %s (%s) — falling back to "
                    "upstream.", repo, path_in_repo, exc)
        return False


def dir_from_mirror(local_prefix: str) -> bool:
    """Same, for a whole folder (buffalo_l, the NSFW detector)."""
    loc = mirror.location(local_prefix)
    if not loc:
        return False
    repo, path_in_repo = loc
    try:
        _with_retries(
            lambda: snapshot_download(
                repo_id=repo, local_dir=MODELS_DIR, token=mirror.token(),
                allow_patterns=[f"{path_in_repo}/*"],
            ),
            desc=f"{local_prefix} (mirror)",
        )
        log.info("✓ %s (mirror: %s)", local_prefix, repo)
        return True
    except Exception as exc:
        log.warning("Mirror %s could not serve %s (%s) — falling back to "
                    "upstream.", repo, local_prefix, exc)
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

    These repos (Wan 2.2, Flux 2) keep everything under split_files/,
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


def fetch_dataset_file(repo: str, relpath: str, dest: Path) -> None:
    """Download one file from a HF *dataset* repo to an exact local path.

    The ReActor asset repo is a dataset (hence repo_type), and it nests
    everything under models/ — a layout ComfyUI does not use — so unlike
    the model repos each file is placed explicitly rather than mirrored.
    """
    if dest.exists():
        log.info("✓ %s (cached)", dest.name)
        return
    if from_mirror(dest, dest.relative_to(MODELS_DIR).as_posix()):
        return
    log.info("↓ %s (from %s) ...", relpath, repo)

    def _download():
        path = hf_hub_download(
            repo_id=repo, filename=relpath, repo_type="dataset",
            local_dir=MODELS_DIR, token=HF_TOKEN,
            revision=mirror.revision(repo),
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        Path(path).rename(dest)

    _with_retries(_download, desc=relpath)


def fetch_url_file(url: str, dest: Path) -> None:
    """Download a plain HTTP file (a GitHub release asset) with resume.

    Same manual-resume scheme as fetch_civitai_file, minus the CivitAI
    token/HTML handling — used for the two facexlib/CodeFormer weights
    that ReActor would otherwise pull mid-swap.
    """
    if dest.exists():
        log.info("✓ %s (cached)", dest.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Both of these are GitHub *release* assets on effectively abandoned
    # repos, which is exactly the kind of URL that 404s one day.
    if from_mirror(dest, dest.relative_to(MODELS_DIR).as_posix()):
        return

    def _download():
        part = dest.with_suffix(dest.suffix + ".part")
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        with requests.get(url, headers=headers, stream=True,
                          timeout=(15, 120), allow_redirects=True) as resp:
            if resp.status_code == 416:  # the .part file is already complete
                part.rename(dest)
                return
            resp.raise_for_status()
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

    log.info("↓ %s ...", dest.name)
    _with_retries(_download, desc=dest.name)


def fetch_insightface_pack() -> None:
    """Download and unpack the buffalo_l face-analysis pack.

    ReActor's own analyzer (reactor_core/analyzer.py — the insightface
    package is not used) looks for the unzipped ONNX files in
    models/insightface/models/buffalo_l/ and downloads the archive itself
    when det_10g/w600k_r50/genderage are missing — exactly the mid-swap
    network call this pre-fetch exists to avoid.
    """
    dest_dir = (MODELS_DIR / "insightface" / "models"
                / REACTOR_INSIGHTFACE_PACK)
    if (dest_dir / "det_10g.onnx").exists():
        log.info("✓ %s (cached)", REACTOR_INSIGHTFACE_PACK)
        return
    # The mirror holds the ONNX files already unpacked, so a hit skips the
    # zip download, the flattening workaround and the temp disk it needs.
    if dir_from_mirror(dest_dir.relative_to(MODELS_DIR).as_posix()):
        return
    zip_path = MODELS_DIR / "insightface" / f"{REACTOR_INSIGHTFACE_PACK}.zip"
    fetch_dataset_file(REACTOR_HF_REPO, REACTOR_INSIGHTFACE_ZIP, zip_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        # Copies of this archive differ: some wrap the models in a
        # buffalo_l/ folder, some store them flat. Flattening every member
        # into dest_dir lands both layouts where ReActor expects them.
        for member in zf.infolist():
            if member.is_dir():
                continue
            with zf.open(member) as src, \
                    open(dest_dir / Path(member.filename).name, "wb") as out:
                shutil.copyfileobj(src, out)
    zip_path.unlink()
    log.info("Unpacked %s → %s", REACTOR_INSIGHTFACE_ZIP, dest_dir)


def fetch_nsfw_detector() -> None:
    """Download the ViT that ReActor's SFW check runs on every input image.

    The check is unconditional in this edition of the node pack, so the
    model is fetched here rather than left to download during the first
    swap.
    """
    dest = MODELS_DIR / REACTOR_NSFW_DIR
    if (dest / "config.json").exists():
        log.info("✓ %s (cached)", REACTOR_NSFW_REPO)
        return
    if dir_from_mirror(REACTOR_NSFW_DIR):
        return
    log.info("↓ %s (from %s) ...", REACTOR_NSFW_DIR, REACTOR_NSFW_REPO)
    _with_retries(
        lambda: snapshot_download(
            repo_id=REACTOR_NSFW_REPO, local_dir=dest, token=HF_TOKEN,
            revision=mirror.revision(REACTOR_NSFW_REPO),
            ignore_patterns=["*.h5", "*.msgpack", "*.onnx"],
        ),
        desc=REACTOR_NSFW_REPO,
    )


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


def download_v2_models() -> None:
    """Fetch the Krea 2 V2 tab's UNet, VAE and LoRA stack (~17 GB).

    Every item is independent: a missing file disables or degrades only the
    V2 tab, which names what it is waiting for, and the next run retries it.

    Self-sufficient on purpose. Slot 1 of the stack is the Krea 2 turbo
    LoRA, which also appears in HF_LORA_FILES — but that list belongs to
    the "krea2" asset group, and V2 can be the only enabled feature, so
    this fetches it rather than assuming another group already did.
    fetch_hf_file keys on the destination path, so when both groups are on
    whichever runs first downloads it and the other logs a cache hit.
    """
    for entry in V2_MODELS:
        # Krea 2 Raw's file was also in KREA2_MODELS before that tab went
        # turbo-only; this loop fetches it on its own regardless (see the
        # docstring above), so nothing here depends on that. A missing
        # model only greys out one dropdown choice.
        try:
            fetch_hf_file(entry["hf_path"])
        except Exception as exc:
            log.error("Krea 2 V2 model %s unavailable (%s) — that dropdown "
                      "choice will refuse to run until a later run fetches "
                      "it.", entry["file"], exc)
    try:
        fetch_hf_file_to(V2_VAE_HF_REPO, V2_VAE_HF_PATH,
                         MODELS_DIR / "vae" / V2_VAE_FILE)
    except Exception as exc:
        log.error("Krea 2 V2 VAE %s unavailable (%s) — the V2 tab will "
                  "refuse to run until a later run fetches it.",
                  V2_VAE_FILE, exc)
    for filename, _strength, _enabled, version_id in V2_LORA_STACK:
        try:
            if version_id is None:
                # No CivitAI version id means it comes from the Krea 2 HF
                # repo — currently just the turbo LoRA in slot 1.
                fetch_hf_file(f"loras/{filename}")
            else:
                fetch_civitai_file(version_id, filename)
        except Exception as exc:
            # One missing LoRA only empties one slot in the V2 stack.
            log.error("Skipping Krea 2 V2 LoRA %s: %s", filename, exc)


def download_reactor_models() -> None:
    """Fetch everything the Face Swap tab needs, before ComfyUI starts.

    Each item is independent: a failure disables or degrades only the Face
    Swap tab and is retried on the next run, exactly like the Wan and Flux
    downloads.
    """
    for relpath, localpath in REACTOR_HF_FILES:
        try:
            fetch_dataset_file(REACTOR_HF_REPO, relpath, MODELS_DIR / localpath)
        except Exception as exc:
            log.error("ReActor file %s unavailable (%s) — the Face Swap tab "
                      "will refuse to run until a later run fetches it.",
                      relpath, exc)
    try:
        fetch_insightface_pack()
    except Exception as exc:
        log.error("InsightFace %s pack unavailable (%s) — the Face Swap tab "
                  "will refuse to run until a later run fetches it.",
                  REACTOR_INSIGHTFACE_PACK, exc)
    for url in REACTOR_FACEDETECTION_FILES:
        name = url.rsplit("/", 1)[-1]
        try:
            fetch_url_file(url, MODELS_DIR / "facedetection" / name)
        except Exception as exc:
            log.error("Face-detection model %s unavailable (%s) — ReActor "
                      "would try to download it during the first swap.",
                      name, exc)
    try:
        fetch_nsfw_detector()
    except Exception as exc:
        log.error("NSFW detector unavailable (%s) — ReActor would try to "
                  "download it during the first swap.", exc)


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
    """Fetch the Krea 2 base models, VAE and LoRAs (~26 GB).

    Shared by the Single, Edit and Inpaint tabs — whichever of them is on
    pulls this group in, and it is fetched once however many of them are.
    """
    for relpath in HF_MODEL_FILES + HF_LORA_FILES:
        fetch_hf_file(relpath)
    for entry in KREA2_MODELS:
        # A missing model only greys out one dropdown choice; it must
        # never sink the whole setup.
        try:
            if entry.get("hf_path"):
                fetch_hf_file(entry["hf_path"])
            elif entry.get("civitai_version"):
                fetch_civitai_file(entry["civitai_version"], entry["file"],
                                   subdir="diffusion_models")
            else:
                log.warning(
                    "Model %r has no hf_path/civitai_version — expecting "
                    "%s to be placed in diffusion_models/ manually.",
                    entry["name"], entry["file"],
                )
        except Exception as exc:
            log.error("Skipping Krea 2 model %s: %s", entry["name"], exc)
    for version_id, filename in CIVITAI_LORAS:
        try:
            fetch_civitai_file(version_id, filename)
        except Exception as exc:
            # A missing LoRA must not sink the whole setup.
            log.error("Skipping LoRA %s: %s", filename, exc)


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


def download_flux_models() -> None:
    """Fetch the Flux 2 model, text encoder, VAE and LoRAs (~57 GB)."""
    for relpath in FLUX_HF_FILES:
        try:
            fetch_repackaged_file(FLUX_HF_REPO, relpath)
        except Exception as exc:
            log.error("Flux 2 file %s unavailable (%s) — the Flux tab "
                      "will refuse to run until a later run fetches it.",
                      relpath, exc)
    for entry in FLUX_MODELS:
        try:
            if entry.get("hf_path"):
                fetch_repackaged_file(FLUX_HF_REPO, entry["hf_path"])
            elif entry.get("civitai_version"):
                fetch_civitai_file(entry["civitai_version"], entry["file"],
                                   subdir="diffusion_models")
            else:
                log.warning(
                    "Flux model %r has no hf_path/civitai_version — "
                    "expecting %s to be placed in diffusion_models/ "
                    "manually.", entry["name"], entry["file"],
                )
        except Exception as exc:
            log.error("Skipping Flux 2 model %s: %s", entry["name"], exc)
    for version_id, filename in FLUX_CIVITAI_LORAS:
        try:
            fetch_civitai_file(version_id, filename,
                               subdir=f"loras/{FLUX_LORA_SUBDIR}")
        except Exception as exc:
            log.error("Skipping Flux LoRA %s: %s", filename, exc)


def download_klein_models() -> None:
    """Fetch the Klein Edit tab's UNet, text encoder, VAE and LoRAs (~19 GB).

    Self-sufficient on purpose: the VAE is the same file the Flux 2 tab
    downloads, but "klein_i2i" can be the only enabled feature, so this fetches
    it rather than assuming the flux group already did. Downloads key on
    the destination path, so when both are on whichever runs first fetches
    it and the other logs a cache hit.

    Every item is independent — a missing file disables or degrades only
    this tab, which names what it is waiting for, and the next run retries.
    """
    try:
        fetch_repackaged_file(KLEIN_VAE_HF_REPO, f"vae/{KLEIN_VAE}")
    except Exception as exc:
        log.error("Klein VAE %s unavailable (%s) — the Klein Edit tab will "
                  "refuse to run until a later run fetches it.",
                  KLEIN_VAE, exc)
    try:
        fetch_hf_file_to(KLEIN_TEXT_ENCODER_REPO, KLEIN_TEXT_ENCODER,
                         MODELS_DIR / "text_encoders" / KLEIN_TEXT_ENCODER)
    except Exception as exc:
        log.error("Klein text encoder %s unavailable (%s) — the Klein Edit "
                  "tab will refuse to run until a later run fetches it.",
                  KLEIN_TEXT_ENCODER, exc)
    for entry in KLEIN_MODELS:
        try:
            fetch_hf_file_to(entry["hf_repo"], entry["hf_path"],
                             MODELS_DIR / "diffusion_models" / entry["file"])
        except Exception as exc:
            log.error("Skipping Klein model %s: %s — that dropdown choice "
                      "will refuse to run until a later run fetches it.",
                      entry["name"], exc)
    for filename, _strength, _enabled, version_id in KLEIN_LORA_STACK:
        try:
            fetch_civitai_file(version_id, filename,
                               subdir=f"loras/{KLEIN_LORA_SUBDIR}")
        except Exception as exc:
            # One missing LoRA only empties one slot in the Klein stack.
            log.error("Skipping Klein LoRA %s: %s", filename, exc)


# Asset group → the function that fetches it. Iteration order is download
# order, so the cheap shared pieces land before the tens of gigabytes.
ASSET_GROUPS = {
    "text_encoder": download_text_encoder,
    "krea2": download_krea2_models,
    "edit_lora": download_edit_lora,
    "v2": download_v2_models,
    "flux": download_flux_models,
    "klein": download_klein_models,
    "wan": download_wan_models,
    "reactor": download_reactor_models,
}

# Groups that pull at least one file from CivitAI, which is the only
# source here that usually needs a token.
CIVITAI_GROUPS = {"krea2", "v2", "flux", "klein"}


def download_everything() -> None:
    """Fetch exactly what the enabled features need, and nothing else.

    Driven by features.assets() rather than a fixed sequence: a feature
    that is off never reaches its downloader, which is where the flags
    actually save money — Wan is ~49 GB and Flux ~57 GB.
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
