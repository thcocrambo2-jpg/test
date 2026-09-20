#!/usr/bin/env python3
"""One-off: rewrite stored presets and prompts to reference models and LoRAs by id.

Operator tool, not part of the shipped app (it lives in scripts/, which
build.sh never sweeps into the binary). Needs pymongo, which the app does
not:

    pip install pymongo

    python scripts/migrate_asset_ids.py                  # dry run: report only
    python scripts/migrate_asset_ids.py --write          # back up, then apply
    python scripts/migrate_asset_ids.py --env path/to/.env --assets path/to/assets.json

Before this change a settings blob named its model by dropdown label
("Krea 2 Raw fp8") and each LoRA by filename. Now both are ids from the
catalogue (see catalog.py and license-validator/data/assets.json), and the
licence server refuses a preset or prompt that names anything else. This
script converts what is already stored, so nothing in the `presets` and
`prompts` collections is left in the old shape:

  • settings.model   label → model id. The labels come from the `models`
                     collection when it has been seeded, else from the
                     assets file (the same records `npm run seed-assets`
                     writes). One retired label is renamed explicitly:
                     "Krea 2 Turbo (official)" was the Krea2 tab's own
                     model, and that tab now runs V2's turbo model.
  • settings.loras   every row becomes [on, lora_id | None, weight]. A
                     two-part [name, weight] row (the Krea2 tab's shape
                     before its stack grew an On column) is on when it
                     names a LoRA. "None" becomes None. The row count is
                     kept as stored.
  • krea_t2i steps   8 → 10, because the Krea2 tab now uses the V2 turbo
                     model at 10 steps / CFG 1. Only on rows still in the
                     old shape, only where it is 8; a row that is not
                     8 / CFG 1 is reported and its steps left alone.
  • every number and bool is cast to exactly the Python type the pod puts
    in the blob (krea2.handler._krea_settings / generate_v2). This is not
    cosmetic: the Node server stores a whole float as an int (1.0 comes
    back as 1), and the fingerprint is a JSON hash, where "1" and "1.0"
    are different recipes.
  • prompts.fingerprint is recomputed with the pod's own function
    (prompts._fingerprint, imported, not copied, so the two cannot
    drift) over the new settings — so a pod submitting the same recipe
    after the switch collapses into the existing row instead of making a
    second one. Only for community rows: an admin row's fingerprint is a
    random id the server generated (it must never dedupe against
    anything), and the two rows scripts/seed-prompts.js writes carry
    hand-written fingerprints that the seed upserts by — one of those is
    a *community* row, so they are kept by value, not by source.

Anything that cannot be converted — a label or filename the catalogue
does not know, a row that is not a LoRA row, a field of the wrong type, a
missing or unexpected key — is reported with the document id, and with
any such problem --write refuses to run. So does a fingerprint collision:
the new fingerprints must be unique among themselves and against every
fingerprint already stored on another row, because `fingerprint` has a
unique index and the updates go one document at a time.

Idempotent. A row already in the new shape converts to itself, so a second
run reports 0 changes, and --write on it writes nothing.

--write first snapshots both collections as canonical Extended JSON under
tmp/atlas-backup-<UTC timestamp>/ (the same layout as the earlier backups:
one <collection>.json array per collection plus manifest.json), reads the
files back and checks the counts before touching anything. Each update is
conditional on the settings still being what this run read, so a preset
edited in the meantime is skipped and reported rather than overwritten.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / "license-validator" / ".env"
DEFAULT_ASSETS = ROOT / "license-validator" / "data" / "assets.json"
COLLECTIONS = ("presets", "prompts")

# prompts.py pulls in settings, which derives BASE_DIR from the
# environment — a pod path (/workspace/krea2) by default. Somewhere
# disposable instead, set before the import below.
os.environ.setdefault("KREA2_BASE_DIR",
                      tempfile.mkdtemp(prefix="ember-migrate-"))
sys.path.insert(0, str(ROOT))
from ember.licensing.prompts import _fingerprint  # noqa: E402

KREA2 = "krea_t2i"
KREA2_V2 = "krea_v2_t2i"

# The Krea2 tab's own model before it switched to V2's turbo record. Not
# in any catalogue — the file it named is gone — so named here, once.
LEGACY_MODELS = {"Krea 2 Turbo (official)": "krea2-turbo-mxfp8"}

# Other spellings of three files that the stacks and CivitAI have used
# over time. Same weights as the canonical record, so they fold into it
# rather than getting records of their own.
LEGACY_LORA_FILES = {
    "Krea2FilterBypass_3vector.safetensors": "filter-bypass-3",
    "Realistic_Snapshot_Krea2_v0.5.safetensors": "realistic-snapshot",
    "snofs_krea_v1_1.safetensors": "snofs-krea-v1",
}

# The fingerprints scripts/seed-prompts.js writes by hand and upserts by.
# Recomputing either would make the next seed run insert a duplicate
# instead of updating its row, so they are kept whatever the row's source.
SEEDED_FINGERPRINTS = frozenset({
    "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0",
})

# The blob shapes, as the pod builds them (krea2.handler._krea_settings and
# generate_v2): key → the Python type it holds. "loras" and "model" are
# handled separately; a dict value is a nested blob of the same kind.
# Every key is required and no other key is allowed — a blob with a
# stray or missing field would hash differently from anything the pod
# sends, which is exactly what this migration is trying to avoid.
SHAPES = {
    KREA2: {
        "model": "model", "steps": int, "cfg": float, "resolution": str,
        "sampler": str, "seed": int, "randomize": bool, "batch_count": int,
        "loras": "loras",
    },
    KREA2_V2: {
        "model": "model", "aspect": str, "megapixels": float, "multiple": int,
        "seed": int, "randomize": bool, "batch_count": int,
        "sampler": {
            "eta": float, "sampler_name": str, "scheduler": str, "steps": int,
            "denoise": float, "cfg": float, "sampler_mode": str,
            "bongmath": bool,
        },
        "variance": {
            "variance_preset": str, "fine_tune_variance": int,
            "model_type": str, "variance_schedule": str, "cutoff_step": int,
            "total_steps": int, "cutoff_strength": float,
            "shift_strength": int,
        },
        "sharpen": bool, "film_grain": bool, "loras": "loras",
    },
}


class Unmappable(Exception):
    """One value this script cannot convert. The message says which."""


# ── Inputs ─────────────────────────────────────────────────────────────

def read_env(path: Path) -> dict:
    """KEY=VALUE lines, the way dotenv reads them: comments and blank lines
    skipped, one layer of matching quotes stripped. No interpolation — the
    licence server's .env does not use any."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def catalogue_maps(db, assets_path: Path):
    """(model label → id, lora file → id, where each came from).

    Each map from its DB collection when that has documents, else from the
    assets file — the collections are seeded separately from this run, and
    a dry run before the seed is the normal order of events. Ids map to
    themselves, which is what makes a second run a no-op.
    """
    assets = None

    def from_file():
        nonlocal assets
        if assets is None:
            assets = json.loads(assets_path.read_text(encoding="utf-8"))
        return assets

    if db.models.estimated_document_count():
        model_rows = [(d["_id"], d.get("name")) for d in db.models.find()]
        model_origin = "the models collection"
    else:
        model_rows = [(m["id"], m.get("name")) for m in from_file()["models"]]
        model_origin = str(assets_path)
    if db.loras.estimated_document_count():
        lora_rows = [(d["_id"], d.get("file")) for d in db.loras.find()]
        lora_origin = "the loras collection"
    else:
        lora_rows = [(r["id"], r.get("file")) for r in from_file()["loras"]]
        lora_origin = str(assets_path)

    models = {}
    for mid, name in model_rows:
        models[mid] = mid
        if name:
            models[name] = mid
    for label, mid in LEGACY_MODELS.items():
        if mid in models.values():
            models[label] = mid
    loras = {}
    for lid, file in lora_rows:
        loras[lid] = lid
        if file:
            loras[file] = lid
    for file, lid in LEGACY_LORA_FILES.items():
        if lid in loras.values():
            loras[file] = lid
    return models, loras, model_origin, lora_origin


# ── Conversion ─────────────────────────────────────────────────────────

def cast(value, kind, where: str):
    """`value` as exactly `kind`, or Unmappable.

    Deliberately narrow: an int is accepted for a float field and a whole
    float for an int field (Node and BSON move between the two freely),
    but a bool is never a number and a string is never either — those mean
    the blob is not what the pod wrote, and guessing would be wrong.
    """
    if kind is bool:
        if isinstance(value, bool):
            return value
    elif kind is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return int(value)             # also drops bson's Int64 subclass
        if isinstance(value, float) and value.is_integer():
            return int(value)
    elif kind is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif kind is str:
        if isinstance(value, str):
            return value
    raise Unmappable(f"{where} is {value!r} ({type(value).__name__}), "
                     f"expected {kind.__name__}")


def convert_lora_row(row, lora_ids: dict, where: str) -> list:
    """[on, id | None, weight] from either stored row shape."""
    if not isinstance(row, list) or len(row) not in (2, 3):
        raise Unmappable(f"{where} is not a LoRA row: {row!r}")
    if len(row) == 2:
        name, weight = row
        on = None                          # decided by the name, below
    else:
        on, name, weight = row
        on = cast(on, bool, f"{where}[0]")
    if name is None or name == "None":
        lora_id = None
    elif isinstance(name, str) and name in lora_ids:
        lora_id = lora_ids[name]
    else:
        raise Unmappable(f"{where} names an unknown LoRA {name!r}")
    if on is None:
        on = lora_id is not None
    return [on, lora_id, cast(weight, float, f"{where}[2]")]


def convert_blob(value: dict, shape: dict, maps, where: str) -> dict:
    """One settings dict (or a nested sampler/variance dict) in the new
    shape. Keeps the stored key order; the fingerprint sorts keys anyway."""
    if not isinstance(value, dict):
        raise Unmappable(f"{where} is not an object: {value!r}")
    missing = [k for k in shape if k not in value]
    extra = [k for k in value if k not in shape]
    if missing or extra:
        raise Unmappable(f"{where} has missing keys {missing} / unexpected "
                         f"keys {extra}")
    models, loras = maps
    out = {}
    for key, raw in value.items():
        kind, at = shape[key], f"{where}.{key}"
        if kind == "model":
            if not isinstance(raw, str) or raw not in models:
                raise Unmappable(f"{at} names an unknown model {raw!r}")
            out[key] = models[raw]
        elif kind == "loras":
            if not isinstance(raw, list):
                raise Unmappable(f"{at} is not a list: {raw!r}")
            out[key] = [convert_lora_row(row, loras, f"{at}[{i}]")
                        for i, row in enumerate(raw)]
        elif isinstance(kind, dict):
            out[key] = convert_blob(raw, kind, maps, at)
        else:
            out[key] = cast(raw, kind, at)
    return out


def already_new(settings: dict, maps) -> bool:
    """Whether the stored blob was already id-shaped before this run: the
    model is an id and every LoRA row is a triple naming an id or None.
    (Types may still need normalising; that is counted as a change.)"""
    models, loras = maps
    model = settings.get("model")
    if not isinstance(model, str) or models.get(model) != model:
        return False
    for row in settings.get("loras") or []:
        if not (isinstance(row, list) and len(row) == 3
                and (row[1] is None or loras.get(row[1]) == row[1])):
            return False
    return True


def convert_settings(tab: str, settings, maps, notes: list, where: str):
    """(new settings, was already new). Raises Unmappable."""
    if tab not in SHAPES:
        raise Unmappable(f"{where}: tab {tab!r} is not one this script knows")
    was_new = isinstance(settings, dict) and already_new(settings, maps)
    new = convert_blob(settings, SHAPES[tab], maps, f"{where} settings")
    if tab == KREA2 and not was_new:
        # The old Krea2 recipe was 8 steps at CFG 1 on the retired turbo
        # model; the tab now runs V2's turbo at 10. A row that is not
        # exactly the old default was set by hand and is left as it is.
        if new["steps"] == 8 and new["cfg"] == 1.0:
            new["steps"] = 10
        else:
            notes.append(f"{where}: krea_t2i row is steps {new['steps']} / "
                         f"cfg {new['cfg']:g}, not 8 / 1 — steps left as is")
    return new, was_new


def typed(value):
    """A comparable form that tells 1 from 1.0 from True — `==` does not,
    and a changed type is a change this script has to write."""
    if isinstance(value, dict):
        return ("d", tuple((k, typed(v)) for k, v in value.items()))
    if isinstance(value, list):
        return ("l", tuple(typed(v) for v in value))
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, int):
        return ("i", int(value))
    if isinstance(value, float):
        return ("f", value)
    return (type(value).__name__, value)


# ── Planning ───────────────────────────────────────────────────────────

def plan(db, maps):
    """Read both collections and work out every change. Writes nothing.

    Returns (changes, stats, problems, notes); a change is
    (collection, doc, new settings, new fingerprint or None).
    """
    changes, problems, notes = [], [], []
    stats = defaultdict(Counter)            # (collection, tab) -> counts
    new_fps = {}                             # fingerprint -> prompt _id
    all_fps = {}                             # every stored fingerprint -> _id
    prompt_rows = []

    for name in COLLECTIONS:
        for doc in db[name].find().sort("_id", 1):
            tab = doc.get("tab")
            where = f"{name} {doc['_id']} ({tab})"
            counts = stats[(name, tab)]
            counts["rows"] += 1
            if name == "prompts":
                all_fps[doc.get("fingerprint")] = doc["_id"]
            try:
                settings, was_new = convert_settings(
                    tab, doc.get("settings"), maps, notes, where)
            except Unmappable as exc:
                problems.append(str(exc))
                counts["unmappable"] += 1
                continue
            if was_new:
                counts["already new shape"] += 1
            fingerprint = None
            if name == "prompts":
                keep = (doc.get("source") != "community"
                        or doc.get("fingerprint") in SEEDED_FINGERPRINTS)
                if keep:
                    fingerprint = doc.get("fingerprint")
                else:
                    fingerprint = _fingerprint(tab, doc.get("prompt") or "",
                                               doc.get("negative") or "",
                                               settings)
                prompt_rows.append((doc, fingerprint, keep))
                if fingerprint != doc.get("fingerprint"):
                    counts["new fingerprint"] += 1
            changed = typed(settings) != typed(doc.get("settings")) or (
                fingerprint is not None and fingerprint != doc.get("fingerprint"))
            counts["changed" if changed else "unchanged"] += 1
            if changed:
                changes.append((name, doc, settings,
                                fingerprint if name == "prompts" else None))

    # Uniqueness, checked against the state every update will run into:
    # the new fingerprints among themselves, and each against every
    # fingerprint stored on a *different* row right now — updates go one
    # at a time under a unique index, so taking another row's current
    # value would fail half way even if that row was about to move.
    for doc, fingerprint, keep in prompt_rows:
        if keep:
            continue
        other = new_fps.get(fingerprint)
        if other is not None:
            problems.append(f"fingerprint collision: prompts {doc['_id']} and "
                            f"{other} both become {fingerprint}")
        new_fps[fingerprint] = doc["_id"]
    for doc, fingerprint, keep in prompt_rows:
        if keep:
            continue
        holder = all_fps.get(fingerprint)
        if holder is not None and holder != doc["_id"]:
            problems.append(f"fingerprint collision: prompts {doc['_id']} "
                            f"would become {fingerprint}, which prompts "
                            f"{holder} already has")
    kept = {fp: doc["_id"] for doc, fp, keep in prompt_rows if keep}
    for fingerprint, pid in new_fps.items():
        if fingerprint in kept and kept[fingerprint] != pid:
            problems.append(f"fingerprint collision: prompts {pid} would "
                            f"become {fingerprint}, kept by prompts "
                            f"{kept[fingerprint]}")
    return changes, stats, problems, notes


def diff(old, new, path="") -> list:
    """Human-readable differences, typed (so 1 → 1.0 shows)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in dict.fromkeys(list(old) + list(new)):
            out += diff(old.get(key), new.get(key), f"{path}.{key}")
        return out
    if (isinstance(old, list) and isinstance(new, list)
            and len(old) == len(new) and path.endswith("loras")):
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            out += diff(a, b, f"{path}[{i}]")
        return out
    if typed(old) != typed(new):
        return [f"{path.lstrip('.')}: {old!r} → {new!r}"]
    return []


# ── Output ─────────────────────────────────────────────────────────────

def summary(stats, changes, problems, notes, samples: int):
    print("\nPer collection and tab:")
    print(f"  {'collection':<10} {'tab':<13} {'rows':>5} {'changed':>8} "
          f"{'unchanged':>10} {'already new':>12} {'unmappable':>11} "
          f"{'new fingerprint':>16}")
    for (name, tab), c in sorted(stats.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        print(f"  {name:<10} {str(tab):<13} {c['rows']:>5} {c['changed']:>8} "
              f"{c['unchanged']:>10} {c['already new shape']:>12} "
              f"{c['unmappable']:>11} "
              f"{c['new fingerprint'] if name == 'prompts' else '-':>16}")
    if notes:
        print(f"\nNotes ({len(notes)}):")
        for line in notes:
            print("  " + line)
    if problems:
        print(f"\nPROBLEMS ({len(problems)}) — --write will refuse:")
        for line in problems:
            print("  " + line)
    else:
        print("\nUnmappable values / collisions: 0")

    shown = set()
    picks = []
    for change in changes:               # one per (collection, tab) first
        key = (change[0], change[1].get("tab"))
        if key not in shown:
            shown.add(key)
            picks.append(change)
    for change in changes:
        if len(picks) >= samples:
            break
        if change not in picks:
            picks.append(change)
    for name, doc, settings, fingerprint in picks[:samples]:
        print(f"\nSample — {name} {doc['_id']} ({doc.get('tab')}"
              + (f", {doc.get('source')}" if name == "prompts" else "") + "):")
        lines = diff(doc.get("settings"), settings, "settings")
        if fingerprint and fingerprint != doc.get("fingerprint"):
            lines.append(f"fingerprint: {doc.get('fingerprint')} → {fingerprint}")
        for line in lines[:14]:
            print("    " + line)
        if len(lines) > 14:
            print(f"    … and {len(lines) - 14} more")


# ── Writing ────────────────────────────────────────────────────────────

def backup(db) -> Path:
    """Canonical Extended JSON of both collections, read back and counted.
    Raises SystemExit if a file does not round-trip."""
    from bson import json_util
    from bson.json_util import CANONICAL_JSON_OPTIONS

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "tmp" / f"atlas-backup-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    counts = {}
    for name in COLLECTIONS:
        docs = list(db[name].find().sort("_id", 1))
        path = folder / f"{name}.json"
        path.write_text(json_util.dumps(docs, json_options=CANONICAL_JSON_OPTIONS,
                                        indent=2) + "\n", encoding="utf-8")
        reread = json_util.loads(path.read_text(encoding="utf-8"),
                                 json_options=CANONICAL_JSON_OPTIONS)
        live = db[name].count_documents({})
        ok = len(reread) == len(docs) == live and reread == docs
        print(f"  {name:<10} written {len(docs):>5}  re-read {len(reread):>5}  "
              f"live {live:>5}  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            raise SystemExit(f"Backup of {name} did not verify — nothing written.")
        counts[name] = len(docs)
    (folder / "manifest.json").write_text(json.dumps({
        "db": db.name,
        "taken": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                 .replace("+00:00", "Z"),
        "counts": counts,
        "why": "before scripts/migrate_asset_ids.py --write",
    }, indent=2) + "\n", encoding="utf-8")
    return folder


def apply(db, changes) -> Counter:
    """One update_one per document, each conditional on the settings (and
    for a prompt, the fingerprint) still being what plan() read."""
    result = Counter()
    for name, doc, settings, fingerprint in changes:
        where = {"_id": doc["_id"], "settings": doc.get("settings")}
        fields = {"settings": settings}
        if name == "prompts":
            where["fingerprint"] = doc.get("fingerprint")
            fields["fingerprint"] = fingerprint
        outcome = db[name].update_one(where, {"$set": fields})
        if outcome.matched_count:
            result[f"{name} updated"] += 1
        else:
            result[f"{name} skipped (changed since read)"] += 1
            print(f"  skipped {name} {doc['_id']}: it changed after this run "
                  "read it — run again to pick it up")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV,
                        help="the licence server's .env (MONGODB_URI, MONGODB_DB)")
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS,
                        help="catalogue file, used when the collections are empty")
    parser.add_argument("--write", action="store_true",
                        help="back up, then apply (default: dry run)")
    parser.add_argument("--samples", type=int, default=4,
                        help="before/after samples to print (default 4)")
    args = parser.parse_args()

    from pymongo import MongoClient

    env = read_env(args.env)
    if not env.get("MONGODB_URI"):
        raise SystemExit(f"MONGODB_URI is not set in {args.env}")
    client = MongoClient(env["MONGODB_URI"])
    db = client[env.get("MONGODB_DB") or "krea2_license"]

    models, loras, model_origin, lora_origin = catalogue_maps(db, args.assets)
    print(f"Database {db.name} — {'WRITE' if args.write else 'dry run, nothing is written'}")
    print(f"Model ids from {model_origin}; LoRA ids from {lora_origin}")

    changes, stats, problems, notes = plan(db, (models, loras))
    summary(stats, changes, problems, notes, args.samples)
    print(f"\nTotal: {len(changes)} document(s) to change")

    if not args.write:
        return
    if problems:
        raise SystemExit("\nRefusing to write: fix the problems above first.")
    if not changes:
        print("\nNothing to write.")
        return
    if str(args.assets) in (model_origin, lora_origin):
        print("\nNote: the ids came from the assets file, not the DB. Run "
              "`npm run seed-assets` too, or the server will refuse edits "
              "to these rows until it has.")
    print("\nBacking up:")
    folder = backup(db)
    print(f"  → {folder}")
    print("\nApplying:")
    for key, count in sorted(apply(db, changes).items()):
        print(f"  {key}: {count}")


if __name__ == "__main__":
    main()
