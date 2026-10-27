"""The model and LoRA catalogue — what each Krea, Z-Image, Qwen 2.1,
Anima, MiniMax and Wan feature offers.

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

    EMBER_CATALOG_FILE   a JSON file in the same shape — dry runs and tests
                         (license-validator/data/assets.json is one)
    POST /v1/catalog     the live answer, saved to BASE_DIR/.catalog.json
    .catalog.json        the last live answer, if the server did not answer

With none of them the catalogue is empty: the Krea, Z-Image and Qwen 2.1
tabs report that they have no models, the MiniMax tabs offer no LoRAs but still run, and the
tabs that do not use it are unaffected.

Built-in models
---------------
MiniMax and Wan ran fixed weights before their models were catalogue
entries, and a server or database that predates that still lists none for
them. So those features carry the records they always ran, in each
pipeline's constants (`BUILTIN`), and fall back to them whenever the
catalogue lists no model for the feature. The ids are the ones the
database records use, so whatever names one — a preset, a recipe, a
token — means the same model before and after the database has it.

Stdlib-only, like the rest of this package — it runs before the pip
install that the heavier modules wait for.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ember import settings
from ember.logs import log
# Data modules: they import nothing, so this package stays stdlib-only.
from ember.pipelines.minimax.constants import MINIMAX_BUILTIN_MODEL
from ember.pipelines.wan.constants import WAN_BUILTIN_MODELS
from ember.settings import (
    BASE_DIR,
    CATALOG_FILE_ENV as FILE_ENV,
    LICENSE_API_URL,
    LICENSE_KEY,
)

CACHE_PATH = BASE_DIR / ".catalog.json"

FETCH_ATTEMPTS = 3
FETCH_BACKOFF = (2, 5)
FETCH_TIMEOUT = 15

# The same rules the server applies before it stores a record. Checked here
# again because a record edited by hand in Atlas never passed the server's
# check, and a file name becomes a path on this disk.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$")
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
SAMPLING_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")

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
class Part:
    """One more file a model needs beside its `file` — Wan 14B's second
    expert. Downloaded to diffusion_models/ like the model itself."""
    file: str
    source: dict
    mirror: dict | None = None


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
    # Which graph the model runs, for a feature with more than one (Wan:
    # "14b" or "5b"). Empty where a feature has a single graph.
    arch: str = ""
    # Extra files by role, e.g. {"low": Part} — Wan 14B's low-noise expert.
    parts: tuple = ()                     # ((role, Part), ...)
    # The negative prompt this model's tab starts on, and switches to when
    # the model is picked. Empty: the tab's own default.
    negative: str = ""
    # The sampling the model's tab switches to when it is picked, for the
    # tabs that read them (MiniMax). "" / None: the tab's own default.
    sampler: str = ""
    scheduler: str = ""
    shift: tuple | None = None            # (video, audio)
    # The MiniMax turbo LoRA's strength for this model — 0 for a checkpoint
    # with turbo merged in. None: the tab's own (0.8).
    turbo_strength: float | None = None

    def part(self, role: str) -> "Part | None":
        return next((part for name, part in self.parts if name == role), None)


@dataclass(frozen=True)
class Catalogue:
    loras: dict = field(default_factory=dict)      # id -> Lora, in order
    models: dict = field(default_factory=dict)     # id -> Model, in order
    features: dict = field(default_factory=dict)   # key -> (model ids, lora ids)
    origin: str = "empty"
    # key -> (Model, ...): what a feature runs when the catalogue lists no
    # model for it. See "Built-in models" in the module docstring.
    builtin: dict = field(default_factory=dict)

    def feature_models(self, key: str) -> tuple:
        ids = self.features.get(str(key), ((), ()))[0]
        listed = tuple(self.models[i] for i in ids if i in self.models)
        return listed or self.builtin.get(str(key), ())

    def feature_loras(self, key: str) -> tuple:
        ids = self.features.get(str(key), ((), ()))[1]
        return tuple(self.loras[i] for i in ids if i in self.loras)

    def lora(self, lora_id) -> Lora | None:
        return self.loras.get(lora_id) if isinstance(lora_id, str) else None

    def model(self, model_id) -> Model | None:
        if not isinstance(model_id, str):
            return None
        found = self.models.get(model_id)
        if found is None:
            found = next((m for models in self.builtin.values()
                          for m in models if m.id == model_id), None)
        return found


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


def _sampling_name(value) -> str:
    """A model's sampler or scheduler name, or "" when it names none."""
    return value if isinstance(value, str) and SAMPLING_NAME_RE.match(value) else ""


def _shift(value) -> tuple | None:
    """A model's {video, audio} shift as (video, audio), or None."""
    if not isinstance(value, dict):
        return None
    pair = tuple(_number(value.get(key), float, 0.0) for key in ("video", "audio"))
    return pair if all(v > 0 for v in pair) else None


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


def _parts(value, rid: str) -> tuple | None:
    """A model's `parts` from the wire, or None when any part is unusable.

    All or nothing: a two-expert model missing its second expert cannot
    run, so a record with a bad part is dropped whole rather than offered
    half-built.
    """
    if value in (None, {}):
        return ()
    if not isinstance(value, dict):
        log.warning("Catalogue: skipping model %r — its parts are not "
                    "{role: {file, source}}.", rid)
        return None
    out = []
    for role, raw in value.items():
        if not (isinstance(role, str) and ID_RE.match(role)
                and isinstance(raw, dict)
                and isinstance(raw.get("file"), str)
                and FILE_RE.match(raw["file"])
                and _source_ok(raw.get("source"))):
            log.warning("Catalogue: skipping model %r — its part %r is not "
                        "a plain .safetensors file with a CivitAI or Hugging "
                        "Face source.", rid, role)
            return None
        out.append((role, Part(file=raw["file"], source=dict(raw["source"]),
                               mirror=_mirror(raw.get("mirror")))))
    return tuple(out)


def _model(raw: dict) -> Model | None:
    """One model record, already checked by `_usable`, or None if its parts
    are not usable."""
    rid = _record_id(raw)
    parts = _parts(raw.get("parts"), rid)
    if parts is None:
        return None
    turbo = raw.get("turbo_lora")
    if isinstance(turbo, dict) and isinstance(turbo.get("lora"), str):
        turbo = {"lora": turbo["lora"],
                 "strength": _number(turbo.get("strength"), float, 1.0)}
    else:
        turbo = None
    turbo_strength = raw.get("turbo_strength")
    turbo_strength = (None if turbo_strength is None
                      else max(0.0, _number(turbo_strength, float, 0.0)))
    return Model(
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
        arch=str(raw.get("arch") or ""),
        parts=parts,
        negative=str(raw.get("negative") or ""),
        sampler=_sampling_name(raw.get("sampler")),
        scheduler=_sampling_name(raw.get("scheduler")),
        shift=_shift(raw.get("shift")),
        turbo_strength=turbo_strength,
    )


def _builtin() -> dict:
    """Each feature's built-in models, parsed the way a served record is."""
    minimax = (_model(MINIMAX_BUILTIN_MODEL),)
    wan = tuple(_model(raw) for raw in WAN_BUILTIN_MODELS)
    return {"minimax_i2v": minimax, "minimax_t2v": minimax, "wan_i2v": wan}


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
        model = _model(raw)
        if model is not None:
            models[model.id] = model
    features = {}
    for key, lists in (document.get("features") or {}).items():
        if not isinstance(lists, dict):
            continue
        model_ids = tuple(i for i in lists.get("models") or [] if i in models)
        lora_ids = tuple(i for i in lists.get("loras") or [] if i in loras)
        features[str(key)] = (model_ids, lora_ids)
    return Catalogue(loras=loras, models=models, features=features,
                     origin=origin, builtin=_builtin())


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
    override = settings.catalog_file()
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
    for key in sorted(result.builtin):
        if not result.features.get(key, ((), ()))[0]:
            log.info("Catalogue lists no model for %s — using the built-in "
                     "one(s): %s", key,
                     ", ".join(m.id for m in result.builtin[key]))
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
