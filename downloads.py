"""Model + LoRA downloads.

Base models come from Hugging Face via huggingface_hub (which resumes
partial downloads automatically); LoRAs come from CivitAI with manual
resume (HTTP Range), retries and useful error messages. Everything is
idempotent — re-running only downloads what is missing.
"""

import time
from pathlib import Path

import requests
from huggingface_hub import hf_hub_download, snapshot_download

from config import (
    ABLITERATED_ENCODER_FILE,
    ABLITERATED_ENCODER_REPO,
    CIVITAI_LORAS,
    CIVITAI_TOKEN,
    EDIT_LORA_FILE,
    EDIT_LORA_REPO,
    FLUX_CIVITAI_LORAS,
    FLUX_ENABLED,
    FLUX_HF_FILES,
    FLUX_HF_REPO,
    FLUX_LORA_SUBDIR,
    FLUX_MODELS,
    HF_LORA_FILES,
    HF_MODEL_FILES,
    HF_MODEL_REPO,
    HF_TOKEN,
    KREA2_MODELS,
    MODELS_DIR,
    TEXT_ENCODER_FILE,
    WAN_ENABLED,
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


def fetch_hf_file(relpath: str) -> None:
    """Download one Comfy-Org/Krea-2 file into MODELS_DIR, keeping its subfolder."""
    dest = MODELS_DIR / relpath
    if dest.exists():
        log.info("✓ %s (cached)", relpath)
        return
    log.info("↓ %s ...", relpath)
    _with_retries(
        lambda: hf_hub_download(
            repo_id=HF_MODEL_REPO,
            filename=relpath,
            local_dir=MODELS_DIR,
            token=HF_TOKEN,
        ),
        desc=relpath,
    )


def fetch_abliterated_encoder() -> None:
    """Download the abliterated Qwen3-VL shards and merge them into one file."""
    dest = MODELS_DIR / "text_encoders" / ABLITERATED_ENCODER_FILE
    if dest.exists():
        log.info("✓ %s (cached)", ABLITERATED_ENCODER_FILE)
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
    dest = MODELS_DIR / "loras" / EDIT_LORA_FILE
    if dest.exists():
        log.info("✓ %s (cached)", EDIT_LORA_FILE)
        return
    log.info("↓ %s (from %s) ...", EDIT_LORA_FILE, EDIT_LORA_REPO)
    _with_retries(
        lambda: hf_hub_download(
            repo_id=EDIT_LORA_REPO,
            filename=EDIT_LORA_FILE,
            local_dir=MODELS_DIR / "loras",
            token=HF_TOKEN,
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
    log.info("↓ %s (from %s) ...", relpath, repo)

    def _download():
        path = hf_hub_download(
            repo_id=repo,
            filename=f"split_files/{relpath}",
            local_dir=MODELS_DIR,
            token=HF_TOKEN,
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


def download_everything() -> None:
    """Fetch base models, registry models, style LoRAs and CivitAI LoRAs."""
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
    try:
        fetch_abliterated_encoder()
    except Exception as exc:
        log.error(
            "Abliterated encoder unavailable (%s) — "
            "falling back to the standard encoder.", exc,
        )
        fetch_hf_file(f"text_encoders/{TEXT_ENCODER_FILE}")
    try:
        fetch_edit_lora()
    except Exception as exc:
        # The Edit tab warns when this file is missing; everything else works.
        log.error("Identity Edit LoRA unavailable (%s) — the Edit tab will "
                  "stay disabled until it downloads on a later run.", exc)
    if WAN_ENABLED:
        for relpath in WAN_HF_FILES:
            try:
                fetch_repackaged_file(WAN_HF_REPO, relpath)
            except Exception as exc:
                # A missing Wan file only degrades the Video tab; the Krea
                # tabs must never be affected by it.
                log.error("Wan 2.2 file %s unavailable (%s) — the Video tab "
                          "will refuse to run until a later run fetches it.",
                          relpath, exc)
    if FLUX_ENABLED:
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
                    fetch_civitai_file(entry["civitai_version"],
                                       entry["file"],
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
    if CIVITAI_LORAS and not CIVITAI_TOKEN:
        log.warning(
            "CIVITAI_LORAS configured but no CIVITAI_TOKEN environment "
            "variable set — trying anonymously (many downloads will be refused)."
        )
    for version_id, filename in CIVITAI_LORAS:
        try:
            fetch_civitai_file(version_id, filename)
        except Exception as exc:
            # A missing LoRA must not sink the whole setup.
            log.error("Skipping LoRA %s: %s", filename, exc)
