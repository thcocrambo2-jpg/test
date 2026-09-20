"""The model and LoRA catalogue — what each Krea and MiniMax feature offers.

The licence server holds three collections: `loras` and `models` (one
record per file, keyed by a readable id) and `feature_assets` (per
feature, the ordered model ids and LoRA ids its tab offers). POST
/v1/catalog answers all three in one document, and this module is the
pod's read of it. Nothing about models or style LoRAs is compiled into
the binary any more: adding a LoRA is a DB edit and a pod restart.

Ids are the contract. Presets, prompts and every form value carry ids;
the only place an id becomes a filename is here (`Lora.file`,
`Model.file`), when a download or a ComfyUI graph needs the name on disk.

Loaded once, right after the licence check and before downloads (app.py),
and then frozen for the life of the process. Downloads, dropdowns and the
V2 slot count are all derived from it, and a list that changed under a
running pod would put those out of step with each other.

Where the answer comes from, in order:

    KREA2_CATALOG_FILE   a JSON file in the same shape — dry runs and tests
                         (license-validator/data/assets.json is one)
    POST /v1/catalog     the live answer, saved to BASE_DIR/.catalog.json
    .catalog.json        the last live answer, if the server did not answer

With none of them the catalogue is empty: the Krea tabs report that they
have no models, the MiniMax tabs offer no LoRAs but still run, and the
tabs that do not use it are unaffected.

Stdlib-only, like licensing.py and presets.py — it runs before the pip
install that the heavier modules wait for.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from config import BASE_DIR, LICENSE_API_URL, LICENSE_KEY, log

CACHE_PATH = BASE_DIR / ".catalog.json"
FILE_ENV = "KREA2_CATALOG_FILE"

FETCH_ATTEMPTS = 3
FETCH_BACKOFF = (2, 5)
FETCH_TIMEOUT = 15

# The same rules the server applies before it stores a record. Checked here
# again because a record edited by hand in Atlas never passed the server's
# check, and a file name becomes a path on this disk.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$")
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")

# What an empty LoRA slot is called in the forms. Stored as null.
NONE = "None"


@dataclass(frozen=True)
class Lora:
    id: str
    name: str
    file: str
    source: dict
    mirror: dict | None
    default_strength: float
    trigger: str = ""


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    file: str
    source: dict
    mirror: dict | None
    variant: str
    steps: int
    cfg: float
    # {"lora": <lora id>, "strength": float} for a model whose recipe
    # switches a LoRA on (V2's raw), else None.
    turbo_lora: dict | None = None
    trigger: str = ""


@dataclass(frozen=True)
class Catalogue:
    loras: dict = field(default_factory=dict)      # id -> Lora, in order
    models: dict = field(default_factory=dict)     # id -> Model, in order
    features: dict = field(default_factory=dict)   # key -> (model ids, lora ids)
    origin: str = "empty"

    def feature_models(self, key: str) -> tuple:
        ids = self.features.get(str(key), ((), ()))[0]
        return tuple(self.models[i] for i in ids if i in self.models)

    def feature_loras(self, key: str) -> tuple:
        ids = self.features.get(str(key), ((), ()))[1]
        return tuple(self.loras[i] for i in ids if i in self.loras)

    def lora(self, lora_id) -> Lora | None:
        return self.loras.get(lora_id) if isinstance(lora_id, str) else None

    def model(self, model_id) -> Model | None:
        return self.models.get(model_id) if isinstance(model_id, str) else None


# ── Parsing ────────────────────────────────────────────────────────────

def _source_ok(source) -> bool:
    if not isinstance(source, dict):
        return False
    if source.get("kind") == "civitai":
        version = source.get("version")
        return isinstance(version, int) and not isinstance(version, bool) \
            and version > 0
    if source.get("kind") == "hf":
        path = source.get("path")
        return (isinstance(source.get("repo"), str)
                and bool(REPO_RE.match(source["repo"]))
                and isinstance(path, str) and bool(path)
                and not path.startswith("/") and ".." not in path.split("/"))
    return False


def _mirror(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    repo, path = value.get("repo"), value.get("path")
    if (isinstance(repo, str) and REPO_RE.match(repo) and isinstance(path, str)
            and path and not path.startswith("/")
            and ".." not in path.split("/")):
        return {"repo": repo, "path": path}
    return None


def _number(value, cast, default):
    if isinstance(value, bool):
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _record_id(raw: dict) -> str:
    return str(raw.get("id") or raw.get("_id") or "")


def _usable(raw, kind: str) -> bool:
    """Whether one record can be offered. Says why when it cannot."""
    if not isinstance(raw, dict):
        return False
    rid = _record_id(raw)
    problem = None
    if not ID_RE.match(rid):
        problem = "its id is not a valid id"
    elif raw.get("enabled") is False:
        return False                       # switched off: not a problem
    elif not isinstance(raw.get("file"), str) or not FILE_RE.match(raw["file"]):
        problem = "its file name is not a plain .safetensors name"
    elif not _source_ok(raw.get("source")):
        problem = "its source is not a CivitAI version or a Hugging Face path"
    if problem:
        log.warning("Catalogue: skipping %s %r — %s.", kind, rid, problem)
        return False
    return True


def parse(document: dict, origin: str) -> Catalogue:
    """A catalogue from the wire/file shape. Bad records are dropped, loudly."""
    loras = {}
    for raw in document.get("loras") or []:
        if not _usable(raw, "LoRA"):
            continue
        rid = _record_id(raw)
        loras[rid] = Lora(
            id=rid,
            name=str(raw.get("name") or rid),
            file=raw["file"],
            source=dict(raw["source"]),
            mirror=_mirror(raw.get("mirror")),
            default_strength=_number(raw.get("default_strength"), float, 1.0),
            trigger=str(raw.get("trigger") or ""),
        )
    models = {}
    for raw in document.get("models") or []:
        if not _usable(raw, "model"):
            continue
        rid = _record_id(raw)
        turbo = raw.get("turbo_lora")
        if isinstance(turbo, dict) and isinstance(turbo.get("lora"), str):
            turbo = {"lora": turbo["lora"],
                     "strength": _number(turbo.get("strength"), float, 1.0)}
        else:
            turbo = None
        models[rid] = Model(
            id=rid,
            name=str(raw.get("name") or rid),
            file=raw["file"],
            source=dict(raw["source"]),
            mirror=_mirror(raw.get("mirror")),
            variant=str(raw.get("variant") or "turbo"),
            steps=_number(raw.get("steps"), int, 10),
            cfg=_number(raw.get("cfg"), float, 1.0),
            turbo_lora=turbo,
            trigger=str(raw.get("trigger") or ""),
        )
    features = {}
    for key, lists in (document.get("features") or {}).items():
        if not isinstance(lists, dict):
            continue
        model_ids = tuple(i for i in lists.get("models") or [] if i in models)
        lora_ids = tuple(i for i in lists.get("loras") or [] if i in loras)
        features[str(key)] = (model_ids, lora_ids)
    return Catalogue(loras=loras, models=models, features=features,
                     origin=origin)


# ── Loading ────────────────────────────────────────────────────────────

_current: Catalogue | None = None
_lock = threading.Lock()


def _from_file(path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Could not read the catalogue file %s (%s).", path, exc)
        return None


def _fetch(instance_id: str | None) -> dict | None:
    """POST /v1/catalog, with retries. None when it never answered 200."""
    if not LICENSE_API_URL or not LICENSE_KEY:
        return None
    payload = json.dumps({"license_key": LICENSE_KEY,
                          "instance_id": instance_id or "unknown"}).encode()
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        request = urllib.request.Request(
            f"{LICENSE_API_URL}/v1/catalog", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as resp:
                body = json.loads(resp.read() or b"{}")
            if body.get("ok"):
                return body
            log.warning("The catalogue request was refused (%s).",
                        body.get("message") or body.get("error"))
            return None
        except urllib.error.HTTPError as err:
            if err.code in (400, 403):
                log.warning("The catalogue request was refused (HTTP %s).",
                            err.code)
                return None
            reason = f"HTTP {err.code}"
        except Exception as exc:              # transport: DNS, refused, TLS
            reason = str(exc)
        if attempt < FETCH_ATTEMPTS:
            wait = FETCH_BACKOFF[min(attempt - 1, len(FETCH_BACKOFF) - 1)]
            log.warning("Catalogue not reachable (%s) — retrying in %ds.",
                        reason, wait)
            time.sleep(wait)
    return None


def load(instance_id: str | None = None) -> Catalogue:
    """Fetch (or read) the catalogue and freeze it. Call once, from app.py."""
    global _current
    override = os.environ.get(FILE_ENV)
    document, origin = None, "empty"
    if override:
        document, origin = _from_file(override), f"file {override}"
    else:
        document = _fetch(instance_id)
        if document is not None:
            origin = "server"
            try:
                CACHE_PATH.write_text(json.dumps(document), encoding="utf-8")
            except OSError as exc:
                log.warning("Could not save the catalogue to %s (%s).",
                            CACHE_PATH, exc)
        elif CACHE_PATH.exists():
            document, origin = _from_file(CACHE_PATH), "last saved copy"
            log.warning("Using the catalogue saved by an earlier run — the "
                        "server did not answer.")
    result = parse(document or {}, origin)
    if not result.models:
        log.error("The catalogue has no models (%s). The Krea tabs will have "
                  "nothing to run until a later start can read it.", origin)
    with _lock:
        _current = result
    log.info("Catalogue — %d model(s), %d LoRA(s) from %s",
             len(result.models), len(result.loras), origin)
    return result


def get() -> Catalogue:
    """The frozen catalogue. Loads it on first use for scripts and tests,
    which never run app.py; the app itself has loaded it by then."""
    if _current is None:
        return load()
    return _current


def feature_models(key) -> tuple:
    return get().feature_models(key)


def feature_loras(key) -> tuple:
    return get().feature_loras(key)


def lora(lora_id) -> Lora | None:
    return get().lora(lora_id)


def model(model_id) -> Model | None:
    return get().model(model_id)
