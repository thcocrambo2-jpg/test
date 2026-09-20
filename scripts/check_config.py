#!/usr/bin/env python3
"""Freeze every configuration constant's value, and diff it.

    python scripts/check_config.py                  # exit 1 on any difference
    python scripts/check_config.py --write          # PHASE 0 ONLY, see below

What this is for
----------------
The restructure split the configuration into ember/settings.py,
ember/logs.py and five ember/pipelines/*/constants.py modules. None of
that was supposed to change a single value. But a constant is not like a route or a form: no
test fails when one moves and quietly loses its meaning, because nothing
in the app asserts what a number *is*. Drop a line while splitting a
390-line data module into six, keep a `PROJECT_DIR` that resolves one
directory too high, or land `WAN_RESERVE_VRAM` in the wrong pipeline's
constants and the app still starts — the pictures are just wrong, or the
weights are unpinned, or the showcase is empty.

So take the answer from the code while it is still the truth. This is the
complete list of what the configuration held before the split and what
each name's value `repr()`s to, and the check is that every one of them is
still findable and still the same.

**Where** a name lives is not what this proves. `SOURCES` below is the
list of modules the names are looked for in, and a phase that moves a
value rewrites it. A name is allowed to move between the modules in that
list. What is not allowed is for it to vanish, to change value, or to end
up in two of them at once — the last one being how a "shared" constant
becomes two constants that drift apart later.

New names are fine and are only listed: the environment reads that used
to sit in other modules arrived this way (context §4).

Determinism
-----------
Half of settings.py is derived from the environment, so collecting it in
this process would record this machine. Instead the values come from a
**subprocess** with a built environment: every `KREA2_*`, `HF_TOKEN`,
`CIVITAI_TOKEN` and `RUNPOD_*` variable is dropped, and four are set to
fixed values (see `_child_env`). `LICENSE_API_URL`, `LICENSE_KEY`,
`SHOWCASE_BASE_URL`, the tokens and every path under `BASE_DIR` are then
the same on any machine — and the two directories that still differ, the
temp base dir and the repo root, are written back out as `<BASE_DIR>` and
`<ROOT>`. `WindowsPath(...)` and `PosixPath(...)` both record as
`Path(...)` for the same reason.

A subprocess is also the only way to do this at all: the values are read
from the environment when the module is imported, so importing it under a
fabricated environment has to happen somewhere that is thrown away
afterwards.

The qualified names
-------------------
`QUALIFIED` is a second, separate section, looked up by module and
attribute rather than searched for across `SOURCES`. It holds the tab keys
that presets.py, prompts.py and handlers.py each restate for themselves.
They are recorded because Phase 3 rebuilds them from `features.Key`, and
a tab key is a contract with stored data: `.recipes.jsonl`, the licence
server's presets and prompt library are all filed under these exact
strings (context §5 rule 7). Its keys stay as they are written here even
when the modules move; the tuple beside each one is where the name lives
now.

Importing handlers needs the comfy stub, for the reason scripts/golden.py
documents: comfy.py detects GPUs at import and raises when there are none.

--write
-------
**`--write` IS FOR PHASE 0 ONLY.** After that, A FAILING CHECK IS A BUG IN
THE CODE, NOT A STALE BASELINE. Every phase from 1 onwards is defined as
changing no value, so a difference here is the mistake this file exists to
catch — find it and fix the code. Never regenerate the baseline to make
the check green again.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = Path(__file__).resolve().parent / "config_baseline.json"

# The modules the baseline's names are searched for, in order. One name has
# to be in exactly one of them. A phase that moves a value rewrites this
# list; the baseline's keys do not move with it.
SOURCES = [
    "ember.settings",
    "ember.pipelines.krea2.constants",
    "ember.pipelines.krea2_v2.constants",
    "ember.pipelines.krea2_v2_edit.constants",
    "ember.pipelines.wan.constants",
    "ember.pipelines.minimax.constants",
]

# Baseline key -> (module, attribute) it lives at today. The keys are the
# baseline's, so they do not move when the modules do; only the tuples are
# rewritten by a phase that moves a module. See the docstring.
QUALIFIED = {
    "presets.TAB_KREA2": ("ember.licensing.presets", "TAB_KREA2"),
    "presets.TAB_KREA2_V2": ("ember.licensing.presets", "TAB_KREA2_V2"),
    "prompts.TAB_KREA2": ("ember.licensing.prompts", "TAB_KREA2"),
    "prompts.TAB_KREA2_V2": ("ember.licensing.prompts", "TAB_KREA2_V2"),
    "handlers.KREA_T2I": ("ember.generation.handlers", "KREA_T2I"),
    "handlers.KREA_EDIT": ("ember.generation.handlers", "KREA_EDIT"),
    "handlers.KREA_V2_T2I": ("ember.generation.handlers", "KREA_V2_T2I"),
    "handlers.KREA_V2_EDIT": ("ember.generation.handlers", "KREA_V2_EDIT"),
    "handlers.MINIMAX_I2V": ("ember.generation.handlers", "MINIMAX_I2V"),
    "handlers.MINIMAX_T2V": ("ember.generation.handlers", "MINIMAX_T2V"),
}

# A public module-level constant: upper case, not starting with an
# underscore. The configuration's own convention (context §5 rule 5), so
# the rule can stay this blunt.
PUBLIC = re.compile(r"[A-Z][A-Z0-9_]*\Z")

# Environment the child is given, on top of a copy of this one stripped of
# everything in _WIPE. Four values, chosen so that nothing derived from
# them names this machine or this checkout: the node tag passes settings'
# DNS-label check, so LICENSE_API_URL is a URL rather than the empty
# string the malformed case produces, and the showcase URL passes the
# https check, so SHOWCASE_BASE_URL is the stripped value rather than "".
_FIXED_ENV = {
    "KREA2_NODE_TAG": "restructure-test",
    "KREA2_LICENSE_KEY": "KREA2-TEST-TEST-TEST",
    "KREA2_SHOWCASE_URL": "https://pub-test.r2.dev/x",
    # None of these values is set-valued today. This is here so that the
    # day one of the constants modules holds a set, its repr does not
    # depend on the child's hash seed and the check does not start
    # flickering for a reason that has nothing to do with the code.
    "PYTHONHASHSEED": "0",
}

# Anything that could reach one of these values. KREA2_* covers the base
# dir, the ports, the swap and attention flags, the Wan reserves, the
# licence key, tag and grace, and the showcase URL; the rest are the two
# download credentials and the RunPod ids licensing.py reads.
_WIPE = ("KREA2_", "RUNPOD_", "HF_TOKEN", "CIVITAI_TOKEN")

# The child's stdout also carries whatever a module decides to print when
# it is imported, so the payload says where it starts and ends rather than
# being the whole stream.
_BEGIN = "--- check_config values ---"
_END = "--- end check_config values ---"


def stub_comfy() -> None:
    """Make `import comfy` work on a machine ComfyUI cannot run on.

    Lifted from scripts/golden.py — which lifted it from scripts/dryrun.py
    — and has to stay in step with them: comfy.py detects GPUs at import
    and raises when there are none. handlers.py imports it, so without
    this the qualified section cannot be read on a laptop.

    Nothing here is ever called. This file only reads constants, so the
    stub exists to make an import succeed, not to fake a run.
    """
    def ensure_alive(*_args, **_kwargs):
        return True, ""

    try:
        from ember.comfy import server as comfy
    except RuntimeError:                   # no NVIDIA GPU visible
        comfy = types.ModuleType("ember.comfy.server")
        comfy.GPUS, comfy.GPU_COUNT = [], 1
        comfy.start_comfyui = lambda *a, **k: None
        comfy.wait_for_comfyui = lambda *a, **k: None
        comfy.verify_custom_node = lambda *a, **k: True
        comfy.node_registered = lambda *a, **k: True
        comfy.log_tail = lambda *a, **k: "<check_config>"
        sys.modules["ember.comfy.server"] = comfy
        # A from-import of the parent package copies the binding, so the
        # stub has to be visible as an attribute too (context.md §6).
        import ember.comfy
        ember.comfy.server = comfy
    comfy.ensure_alive = ensure_alive


def _scrub(text: str, base_dir: Path) -> str:
    """The two machine-specific directories, as placeholders.

    Three spellings of each, because a path reaches a repr by three
    routes: `Path.__repr__` uses `as_posix()`, a path formatted into a
    string carries the platform separator, and a string repr doubles a
    backslash. The base dir goes first in case a checkout ever sits above
    it.
    """
    for value, token in ((base_dir, "<BASE_DIR>"), (ROOT, "<ROOT>")):
        native = str(value)
        for form in (value.as_posix(), native, native.replace("\\", "\\\\")):
            text = text.replace(form, token)
    # PosixPath on the pod, WindowsPath on a dev box, one baseline. A path
    # that turned into a plain string still reads as a difference.
    return text.replace("WindowsPath(", "Path(").replace("PosixPath(", "Path(")


def collect() -> dict:
    """Every public constant in `SOURCES`, plus `QUALIFIED`, as reprs.

    Runs in the child, under the built environment. Modules are kept
    apart: a name in two of them is a duplicate the check has to report,
    not a value to overwrite.
    """
    stub_comfy()
    base_dir = Path(
        os.environ["KREA2_BASE_DIR"]).expanduser().resolve()

    names = {}
    for source in SOURCES:
        module = __import__(source, fromlist=["_"])
        names[source] = {
            name: _scrub(repr(value), base_dir)
            for name, value in sorted(vars(module).items())
            if PUBLIC.match(name)
        }

    qualified = {}
    for key, (source, attr) in QUALIFIED.items():
        module = __import__(source, fromlist=["_"])
        qualified[key] = _scrub(repr(getattr(module, attr)), base_dir)

    return {"sources": list(SOURCES), "names": names, "qualified": qualified}


def _child_env(base_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not any(k == w or k.startswith(w) for w in _WIPE)}
    env.update(_FIXED_ENV)
    env["KREA2_BASE_DIR"] = str(base_dir)
    return env


def gather() -> dict:
    """Run `collect()` in a subprocess and hand back what it printed.

    The base dir is a temp tree, because a child that calls
    `settings.ensure_dirs()` makes whatever it is pointed at.
    """
    base_dir = Path(tempfile.mkdtemp(prefix="check-config-")).resolve()
    try:
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--collect"],
            env=_child_env(base_dir), cwd=str(ROOT),
            capture_output=True, text=True, encoding="utf-8",
            # The payload is ASCII (json.dumps escapes), so this only ever
            # touches whatever the imported modules log on their way up,
            # and a console codepage that cannot spell "·" must not be the
            # reason this check fails.
            errors="replace",
        )
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
    if done.returncode != 0 or _BEGIN not in done.stdout:
        sys.stderr.write(done.stderr)
        sys.exit("could not import %s (exit %d)"
                 % (", ".join(SOURCES), done.returncode))
    payload = done.stdout.split(_BEGIN, 1)[1].split(_END, 1)[0]
    return json.loads(payload)


def compare(baseline: dict, current: dict) -> tuple:
    """Differences as four lists anybody can act on, plus the new names.

    Separate lists rather than one, because each means something
    different: a missing name is a line lost in a move, a duplicate is one
    constant that has become two, a changed value is the thing every later
    phase promises not to do, and a changed tab key is a break with data
    already on disk. New names are only listed.
    """
    per_module = current["names"]
    where = {}
    for source, values in per_module.items():
        for name in values:
            where.setdefault(name, []).append(source)

    missing, duplicated, changed = [], [], []
    for name, want in sorted(baseline["names"].items()):
        homes = where.get(name, [])
        if not homes:
            missing.append("%s   (not in %s)" % (name, ", ".join(SOURCES)))
            continue
        if len(homes) > 1:
            duplicated.append("%s   (in %s)" % (name, ", ".join(homes)))
            continue
        got = per_module[homes[0]][name]
        if got != want:
            changed.append("%s   %s -> %s   (in %s)"
                           % (name, want, got, homes[0]))

    qualified = []
    for key, want in sorted(baseline["qualified"].items()):
        if key not in current["qualified"]:
            qualified.append("%s   (the script no longer looks it up)" % key)
        elif current["qualified"][key] != want:
            qualified.append("%s   %s -> %s"
                             % (key, want, current["qualified"][key]))
    for key in sorted(set(current["qualified"]) - set(baseline["qualified"])):
        qualified.append("%s   (not in the baseline)" % key)

    new = sorted(set(where) - set(baseline["names"]))
    return missing, duplicated, changed, qualified, new


def _flatten(current: dict) -> dict:
    """The baseline's shape: one flat name -> repr map, not one per module.

    Flat on purpose. Which module a name sits in is the thing the phases
    are allowed to change, so recording it would make the baseline stale
    by design and turn every legitimate move into a diff.
    """
    names = {}
    for values in current["names"].values():
        names.update(values)
    return {
        "sources": current["sources"],
        "names": dict(sorted(names.items())),
        "qualified": dict(sorted(current["qualified"].items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diff the configuration constants against "
                    "scripts/config_baseline.json.",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="PHASE 0 ONLY: rewrite the baseline from today's code. A "
             "failing check afterwards is a bug in the code, not a stale "
             "baseline - fix the code instead.",
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--collect", action="store_true",
        help=argparse.SUPPRESS,      # the subprocess gather() runs
    )
    args = parser.parse_args()

    if args.collect:
        print(_BEGIN)
        print(json.dumps(collect(), sort_keys=True))
        print(_END)
        return

    current = gather()

    if args.write:
        frozen = _flatten(current)
        args.baseline.write_text(
            json.dumps(frozen, indent=2, ensure_ascii=False,
                       sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("wrote %d name(s) and %d qualified name(s) to %s"
              % (len(frozen["names"]), len(frozen["qualified"]),
                 args.baseline))
        return

    if not args.baseline.exists():
        sys.exit("no baseline at %s - run --write in Phase 0 first"
                 % args.baseline)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))

    missing, duplicated, changed, qualified, new = compare(baseline, current)

    for title, lines in (
        ("missing - in the baseline, nowhere in %s"
         % " / ".join(SOURCES), missing),
        ("duplicated - one constant is now two", duplicated),
        ("changed - this is what no phase is allowed to do", changed),
        ("qualified names (tab keys) - stored data is filed under these",
         qualified),
    ):
        if lines:
            print("%s: %d" % (title, len(lines)))
            for line in lines:
                print("  %s" % line)

    if new:
        print("new since the baseline (allowed): %d" % len(new))
        for name in new:
            print("  %s" % name)

    if missing or duplicated or changed or qualified:
        sys.exit(1)
    print("config OK - %d name(s) in %s and %d tab key(s) match %s"
          % (len(baseline["names"]), " / ".join(SOURCES),
             len(baseline["qualified"]), args.baseline.name))


if __name__ == "__main__":
    main()
