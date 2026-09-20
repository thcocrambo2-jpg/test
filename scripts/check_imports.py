#!/usr/bin/env python3
"""Import every app module on its own, and hold the layout's four rules.

    python scripts/check_imports.py             report and exit 1 on any failure
    python scripts/check_imports.py --list      print the modules it would import

Why this exists
---------------
The restructure turns 29 root modules into a package, and the two ways
that goes wrong are both invisible to the app itself.

The first is an import cycle. `import api` works today because something
imported earlier had already put half the graph in `sys.modules`; move the
files and the same pair of modules can end up waiting on each other. The
app would still start — its entry point happens to import them in the
lucky order — and the failure would surface in a script, a test, or the
Docker bake stage, weeks later. So every module is imported **alone, in a
fresh subprocess**, which is the only arrangement that has no earlier
import to lean on.

The second is import-time work that needs hardware. comfy.py detects GPUs
when it is imported and raises without one (comfy.detect_gpus), so a
package `__init__` that imported its siblings would drag GPU detection
into `docker/bake_nodes.py` and into every laptop script. Hence rule 2:
every `__init__.py` under the package is empty. The comfy module itself is
the one thing stubbed here, the same way scripts/golden.py and
scripts/dryrun.py stub it, and the stub is installed *before* the module
under test so that importing a module which imports comfy is a test of
that module rather than of this machine's GPU.

Rules 3 and 4 are the config split's half of the same problem. Once
settings.py owns the environment, a stray `os.environ.get` left behind in
another module is a value that no longer appears in check_config.py's
baseline and no longer gets validated — and it reads correctly on the
machine where it was written. An AST scan is the only thing that finds
those, because they do nothing wrong at runtime.

The flags
---------
Each rule below its own module-level constant, off until the phase that
makes it true. A later phase flips one value and changes nothing else.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

# One level up, and deliberately outside the package so Nuitka never sweeps
# it into the binary. Same reasoning as scripts/golden.py.
ROOT = Path(__file__).resolve().parent.parent

# ── the layout switch ────────────────────────────────────────────────────
# Where the app's modules live, relative to the repo root. Module
# discovery, the rule-2 scan and the rule-3 scan all follow it, so it is
# the only place in this file that knows the layout.
MODULE_ROOT = "ember"

# Modules under MODULE_ROOT that are not part of the app.
# ember/web/webui_bundle.py is deliberately absent: it is 316 KB of generated base64, but it declares
# two dicts and nothing else, and it imports in under 10 ms — there is no
# reason to leave the front end out of the check. Anything listed here
# needs a reason next to it.
NOT_APP_MODULES: frozenset[str] = frozenset()

# ── the rules ────────────────────────────────────────────────────────────
# Rule 1 is unconditional: every discovered module imports alone.

# Rule 2: every __init__.py under MODULE_ROOT holds nothing but whitespace
# or a docstring (context.md §5 rule 3).
CHECK_EMPTY_INITS = True

# Rule 3 — PHASE 2 TURNS THIS ON: settings.py is the only module that reads
# the environment (context.md §4, tier 1).
CHECK_ENV_READS = False

# Rule 4 — PHASE 2 TURNS THIS ON: config.py has been split, so nothing may
# import it any more.
CHECK_NO_CONFIG_IMPORT = False

# The one module allowed to read the environment, as a path under
# MODULE_ROOT. Rule 3 is off until Phase 2 splits config.py, so this names
# the file that will hold the env reads rather than the one that holds them
# today. Phase 2 turns the flag on in the same commit that makes this true.
ENV_HOME = "settings.py"

# `os.environ.copy()` is allowed anywhere: handing the whole environment to
# a child process is a pass-through, not a decision made from a named
# variable. The caller that needs it is comfy/server.py, which copies the
# environment to launch ComfyUI.
ENV_COPY_METHOD = "copy"

# The module stubbed before every import, as a dotted name under the
# package (see the docstring). Under the flat layout it is the top-level
# `comfy`; from Phase 1 it is `ember.comfy.server`, and the stub is also
# hung on `ember.comfy` as its `server` attribute, because a from-import of
# the parent package would otherwise not find it (context.md §6).
STUB_MODULE = "comfy" if MODULE_ROOT == "." else "ember.comfy.server"

# Environment variables cleared from every child, so that a developer's
# shell cannot change what this check sees. The same posture as
# scripts/check_config.py's collection subprocess.
CLEARED_PREFIXES = ("KREA2_", "RUNPOD_")
CLEARED_NAMES = ("HF_TOKEN", "CIVITAI_TOKEN", "HF_WRITE_TOKEN")

# The catalogue the Krea tabs read their models and LoRAs from — the seed
# document, so no child ever asks a licence server for it. Nothing here
# reads the catalogue's contents; it is set so that an import which touches
# catalog.py cannot reach the network.
SEED_CATALOG = ROOT / "license-validator" / "data" / "assets.json"


def _package_dir() -> Path:
    """The directory MODULE_ROOT names."""
    return (ROOT / MODULE_ROOT).resolve()


def discover() -> list[str]:
    """Every app module, as an importable dotted name, in import order.

    Discovered rather than listed, so that a module added later is covered
    without this file being edited — which is the only way a check like
    this survives a restructure. Sorted so the report reads the same on
    every machine and every platform.
    """
    base = _package_dir()
    if MODULE_ROOT == ".":
        return sorted(
            path.stem for path in base.glob("*.py")
            if path.stem not in NOT_APP_MODULES and not path.stem.startswith("_")
        )

    package = base.name
    names = {package}
    for path in sorted(base.rglob("*.py")):
        parts = path.relative_to(base).with_suffix("").parts
        if any(part.startswith(".") for part in parts):
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join((package,) + parts))
    return sorted(names)


def module_path(name: str) -> Path:
    """The file a discovered module name came from."""
    base = _package_dir()
    if MODULE_ROOT == ".":
        return base / ("%s.py" % name)
    parts = name.split(".")[1:]                # drop the package itself
    stem = base.joinpath(*parts) if parts else base
    return stem.with_suffix(".py") if stem.with_suffix(".py").exists() \
        else stem / "__init__.py"


# ── rule 1: each module imports alone ────────────────────────────────────

def child_env() -> dict:
    """A controlled environment for the child processes.

    Every KREA2_* and RUNPOD_* variable is dropped and the ones that decide
    where files land are set explicitly, so that this check answers the
    same on a pod, in CI and on the machine that wrote it. KREA2_BASE_DIR
    points at a throwaway directory because config.py makes its tree when
    it is imported and the shipped default is the pod path /workspace/krea2
    (config.py, the mkdir loop).
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(CLEARED_PREFIXES) and k not in CLEARED_NAMES}
    env["KREA2_BASE_DIR"] = tempfile.mkdtemp(prefix="check_imports-")
    env["KREA2_CATALOG_FILE"] = str(SEED_CATALOG)
    # A syntactically valid tag, so LICENSE_API_URL is a URL rather than the
    # empty string. Nothing here calls it.
    env["KREA2_NODE_TAG"] = "restructure-test"
    # Nothing imported here writes a .pyc worth keeping, and a stale one in
    # a half-moved tree is a class of confusion this check must not create.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _stub_comfy() -> None:
    """Make importing the GPU module work on a machine with no GPU.

    Lifted from scripts/golden.py's stub_comfy() and has to stay in step
    with it. The real import is attempted first, so on a GPU machine the
    module under test really does import the real thing — and so that this
    is also the one honest test of the comfy module itself: only the
    documented "no NVIDIA GPU visible" RuntimeError is tolerated, and any
    other failure inside it propagates and fails the check.
    """
    try:
        module = importlib.import_module(STUB_MODULE)
    except RuntimeError as exc:                # no NVIDIA GPU visible
        print("no GPU (%s) - faking %s" % (exc, STUB_MODULE), file=sys.stderr)
        module = types.ModuleType(STUB_MODULE)
        module.GPUS, module.GPU_COUNT = [], 1
        module.start_comfyui = lambda *a, **k: None
        module.wait_for_comfyui = lambda *a, **k: None
        module.verify_custom_node = lambda *a, **k: True
        module.verify_core_node = lambda *a, **k: True
        module.node_registered = lambda *a, **k: True
        module.log_tail = lambda *a, **k: "<check_imports>"
        module.ensure_alive = lambda *a, **k: (False, "not running")
        sys.modules[STUB_MODULE] = module
        parent, _, leaf = STUB_MODULE.rpartition(".")
        if parent:
            # A from-import of the parent package copies the binding, so the
            # stub has to be visible as an attribute too (context.md §6).
            setattr(importlib.import_module(parent), leaf, module)


def import_one(name: str) -> int:
    """The child half: stub comfy, then import `name` and nothing else."""
    sys.path.insert(0, str(ROOT))
    _stub_comfy()
    importlib.import_module(name)
    return 0


def import_alone(name: str, env: dict) -> str | None:
    """Import `name` in a fresh interpreter; the traceback, or None.

    Re-runs this file rather than building a `-c` program, so the stub has
    exactly one definition and the child is reading the same constants the
    parent is.
    """
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--import-one", name],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or
            "exited %d with no output" % result.returncode).rstrip()


# ── rule 2: every __init__.py is empty ───────────────────────────────────

def _is_empty_module(tree: ast.Module) -> bool:
    """True when the body is nothing, or one string expression."""
    if not tree.body:
        return True
    if len(tree.body) > 1:
        return False
    node = tree.body[0]
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def empty_inits(names: list[str]) -> list[str]:
    """Failures for every non-empty __init__.py the discovery found.

    Scoped to the discovered modules rather than to every `__init__.py`
    under the repo, so that deps/ComfyUI-ReActor and webui/node_modules are
    not the thing this reports on.
    """
    failures = []
    for name in names:
        path = module_path(name)
        if path.name != "__init__.py" or not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _is_empty_module(tree):
            continue
        first = next((node for node in tree.body
                      if not (isinstance(node, ast.Expr)
                              and isinstance(node.value, ast.Constant))),
                     tree.body[0])
        failures.append(
            "%s:%d: an __init__ that runs code drags its siblings - and GPU "
            "detection - into every importer"
            % (_rel(path), first.lineno))
    return failures


# ── rule 3: only settings.py reads the environment ───────────────────────

def _env_reads(path: Path) -> list[str]:
    """Every read of the environment in one file, as file:line: source."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines()

    # `os.environ.copy()` is allowed, so the inner `os.environ` of a
    # `.copy` attribute is collected first and skipped below.
    passed_through = set()
    getenv_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == ENV_COPY_METHOD \
                and _is_os_environ(node.value):
            passed_through.add(id(node.value))
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in ("environ", "getenv"):
                    getenv_aliases.add(alias.asname or alias.name)

    found = []

    def report(node, what):
        source = lines[node.lineno - 1].strip() if node.lineno <= len(lines) \
            else ""
        # ASCII in the report on purpose: this runs in a Windows console as
        # often as in bash, and there an em-dash arrives as a replacement
        # character. Same reason as scripts/check_build_args.py.
        found.append((node.lineno, "%s:%d: %s - %s"
                      % (_rel(path), node.lineno, what, source)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if _is_os_environ(node) and id(node) not in passed_through:
                report(node, "os.environ")
            elif node.attr == "getenv" and isinstance(node.value, ast.Name) \
                    and node.value.id == "os":
                report(node, "os.getenv")
        elif isinstance(node, ast.Name) and node.id in getenv_aliases:
            report(node, "os.%s, imported by name" % node.id)
        elif isinstance(node, ast.ImportFrom) and node.module == "os" \
                and any(a.name in ("environ", "getenv") for a in node.names):
            report(node, "from os import environ/getenv")
    # ast.walk is breadth-first, so the raw order is by nesting depth. Sort
    # by line, because that is the order the person fixing them reads in.
    return [line for _lineno, line in sorted(found)]


def _is_os_environ(node) -> bool:
    """True for the expression `os.environ`."""
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def env_reads(names: list[str]) -> list[str]:
    """Failures for every env read outside MODULE_ROOT/ENV_HOME."""
    home = (_package_dir() / ENV_HOME).resolve()
    failures = []
    for name in names:
        path = module_path(name)
        if not path.exists() or path.resolve() == home:
            continue
        failures.extend(_env_reads(path))
    return failures


# ── rule 4: nothing imports the old config module ────────────────────────

def _config_module() -> tuple[str, str]:
    """(package, module) naming the config module Phase 2 removed."""
    package = _package_dir().name if MODULE_ROOT != "." else ""
    return package, "%s.config" % package if package else "config"


def config_imports(names: list[str]) -> list[str]:
    """Failures for every import of the split-up config module."""
    package, dotted = _config_module()
    failures = []
    for name in names:
        if name == dotted:
            continue
        path = module_path(name)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        lines = text.splitlines()
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.Import):
                hit = any(a.name == dotted or a.name.startswith(dotted + ".")
                          for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                hit = (node.module == dotted
                       or (node.module or "").startswith(dotted + ".")
                       or (node.module == package
                           and any(a.name == "config" for a in node.names)))
            if hit:
                source = lines[node.lineno - 1].strip()
                failures.append("%s:%d: %s - %s is gone; the values live in "
                                "settings.py and the pipelines' constants.py"
                                % (_rel(path), node.lineno, source, dotted))
    return failures


def _rel(path: Path) -> str:
    """A repo-relative, forward-slashed path, so reports match everywhere."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import every app module alone, and check the layout "
                    "rules the restructure depends on.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="print the modules that would be imported, and stop",
    )
    parser.add_argument(
        "--import-one", metavar="MODULE",
        help="internal: import one module and exit. This is what the check "
             "runs in each child process.",
    )
    args = parser.parse_args()

    if args.import_one:
        return import_one(args.import_one)

    names = discover()
    if args.list:
        for name in names:
            print(name)
        return 0
    if not names:
        print("no modules found under %s - MODULE_ROOT is wrong" % MODULE_ROOT)
        return 1

    failures = []
    env = child_env()
    try:
        for name in names:
            traceback = import_alone(name, env)
            if traceback is not None:
                failures.append("%s does not import on its own:\n%s"
                                % (name, _indent(traceback)))
    finally:
        # config.py made its tree inside this on every child's import.
        shutil.rmtree(env["KREA2_BASE_DIR"], ignore_errors=True)

    rules = ["%d module(s) import alone" % len(names)]
    if CHECK_EMPTY_INITS:
        failures.extend(empty_inits(names))
        rules.append("every __init__ empty")
    if CHECK_ENV_READS:
        failures.extend(env_reads(names))
        rules.append("%s owns the environment" % ENV_HOME)
    if CHECK_NO_CONFIG_IMPORT:
        failures.extend(config_imports(names))
        rules.append("nothing imports %s" % _config_module()[1])

    if failures:
        print("%d failure(s):" % len(failures))
        for line in failures:
            print("  - %s" % line)
        return 1
    print("imports OK - %s" % ", ".join(rules))
    return 0


def _indent(text: str) -> str:
    return "\n".join("      %s" % line for line in text.splitlines())


if __name__ == "__main__":
    sys.exit(main())
