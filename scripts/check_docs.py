#!/usr/bin/env python3
"""Hold the documentation to the three promises it cannot keep by itself.

    python scripts/check_docs.py            report and exit 1 on any failure
    python scripts/check_docs.py --list     print what each rule scans

Why this exists
---------------
Prose rots in ways nothing else in the repo notices. Python never imports
a markdown file, so a link that stopped resolving, a variable that was
added to settings.py and never written down, and a paragraph still naming
a module deleted two phases ago all read exactly like working
documentation. Each rule below is one of those, and each is the only
thing that will catch it.

Rule 1 - every relative link resolves. Splitting one 2,371-line README
into a tree of pages turned every cross-reference into a path, and a path
is a thing that can be wrong. Links are checked as far as the file, not
the heading: a wrong `#anchor` is a nuisance, a wrong file name is a dead
end.

Rule 2 - every environment variable is documented. settings.py's
docstring is the list of everything the app reads from the environment,
and docs/configuration.md is where somebody running the app looks one up.
A variable added to the first and not the second is a setting nobody
outside this repo can discover. The docstring is the source of truth;
this rule only asks that the page has heard of each name.

Rule 3 - nothing still names a file that is gone. Phase 1 moved 29 root
modules into `ember/` and Phase 2 split `config.py`. A comment that still
says "see config.py" sends the next reader to a file that has not existed
since, and no compiler will tell them. The scan covers tracked files
only, so anything git-ignored is out of scope by construction.

The flags
---------
Each rule sits behind its own module-level constant, so retiring one is a
single value rather than a rewrite.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# One level up, and deliberately outside the package so Nuitka never
# sweeps it into the binary. Same reasoning as scripts/check_imports.py.
ROOT = Path(__file__).resolve().parent.parent

CHECK_LINKS = True
CHECK_ENV_DOCUMENTED = True
CHECK_DEAD_NAMES = True

# -- rule 1: links --------------------------------------------------------
# Every markdown file whose links are load-bearing. webui/README.md is in
# because it is the one front-end page that links back into docs/.
#
# Path.match reads "**" as exactly one component, so "docs/**/*.md"
# matches docs/running/runpod.md and misses docs/configuration.md. The
# flat "docs/*.md" is what covers the top-level pages. A pattern with no
# slash matches the basename at any depth, which is why the bare
# README.md line also reaches license-validator/, webui/ and assets/.
LINK_GLOBS = ("README.md", "docs/*.md", "docs/**/*.md",
              "license-validator/**/*.md")

# A markdown inline link, and the three kinds of target that are not
# paths: anything with a scheme, a bare anchor, and a protocol-relative
# URL.
LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|//)")

# -- rule 2: the environment table ----------------------------------------
# The docstring block in settings.py that lists every variable, and the
# page that has to carry the same names. A row is indented four spaces
# with the name first and two or more spaces before its description,
# which is what stops ordinary prose from matching.
ENV_SOURCE = "ember/settings.py"
ENV_PAGE = "docs/configuration.md"
ENV_ROW = re.compile(r"^ {4}([A-Z][A-Z0-9_]*(?:, *[A-Z][A-Z0-9_]*)*) {2,}\S")

# -- rule 3: names of files that no longer exist --------------------------
# Every module the restructure removed whose basename now exists nowhere
# in the tree, plus the two Gradio modules deleted before it started.
# Names that still live somewhere under ember/ (workflow.py, client.py,
# catalog.py, ...) are deliberately absent: a reference to one of those is
# ambiguous rather than wrong, and flagging it would teach the reader to
# ignore this check.
DEAD_NAMES = (
    "bootstrap.py", "comfy.py", "config.py", "jobqueue.py", "licensing.py",
    "webui.py", "workflow_krea2_v2.py", "workflow_krea2_v2_edit.py",
    "workflow_minimax.py", "workflow_wan.py",
    "ui.py", "theme.py",
)

# The old design note that comments used to cite. It was git-ignored,
# never committed, and no copy survives, so a citation of it points at
# nothing anyone can read.
DEAD_DOC = "context.md"

# Matched with a left boundary, because every one of these names is a
# suffix of a name that is perfectly current: check_webui.py ends in
# "webui.py", check_config.py in "config.py", and webui.py itself in
# "ui.py". Without it the rule reports the scripts that enforce it.
DEAD = {name: re.compile(r"(?<![A-Za-z0-9_])%s" % re.escape(name))
        for name in DEAD_NAMES + (DEAD_DOC,)}

# This file names every one of them, which is not a stale reference but
# the list itself.
SELF = "scripts/check_docs.py"

# Where else those strings are allowed to stand, and why. Both are real
# uses of the name rather than stale references, and neither should be
# "fixed".
ALLOWED = {
    # An illustrative path-traversal target. The point is a path the
    # gallery must refuse to serve, and it makes that point precisely
    # because no such file exists.
    ("ember/web/spa.py", "config.py"),
    # The ignore rule that kept context.md out of the repo to begin with.
    (".gitignore", "context.md"),
}

# Generated files carry whatever their input carried.
GENERATED = ("ember/web/webui_bundle.py",)

# Extensions worth reading as prose. Everything else tracked here is
# either binary or data that nobody reads for instructions.
TEXT_SUFFIXES = {".py", ".md", ".sh", ".ps1", ".js", ".mjs", ".cjs", ".ts",
                 ".tsx", ".jsx", ".yml", ".yaml", ".toml", ".cfg", ".ini",
                 ".txt", ".json", ".html", ".css"}
TEXT_NAMES = {"Dockerfile", "Dockerfile.dev", "Makefile", ".gitattributes",
              ".dockerignore", ".gitignore", ".git-blame-ignore-revs"}


def tracked() -> list[Path]:
    """Every file git knows about, as repo-relative paths."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [Path(name) for name in out.split("\0") if name]


def is_text(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix in TEXT_SUFFIXES


def read(path: Path) -> str | None:
    """File text, or None for anything that is not decodable UTF-8."""
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def scanned(files: list[Path]) -> list[Path]:
    """The files rule 3 reads: tracked, text, not generated, not this one."""
    return [path for path in files
            if is_text(path) and path.as_posix() not in GENERATED
            and path.as_posix() != SELF]


def pages(files: list[Path]) -> list[Path]:
    """The markdown rule 1 reads, in a stable order."""
    return sorted({path for path in files
                   for pattern in LINK_GLOBS if path.match(pattern)})


def broken_links(paths: list[Path]) -> list[str]:
    """Rule 1. Every relative link that does not resolve to a file."""
    failures = []
    for path in paths:
        text = read(path)
        if text is None:
            continue
        for match in LINK.finditer(text):
            target = match.group(1)
            if EXTERNAL.match(target):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            if not ((ROOT / path).parent / target).exists():
                failures.append("%s links to %s, which does not exist"
                                % (path.as_posix(), target))
    return failures


def undocumented_variables() -> list[str]:
    """Rule 2. Variables in settings.py's table missing from the page."""
    source = read(Path(ENV_SOURCE))
    page = read(Path(ENV_PAGE))
    if source is None:
        return ["%s is unreadable" % ENV_SOURCE]
    if page is None:
        return ["%s does not exist" % ENV_PAGE]
    names = env_variables(source)
    if not names:
        return ["found no variable table in %s" % ENV_SOURCE]
    return ["%s is not in %s" % (name, ENV_PAGE)
            for name in names if name not in page]


def env_variables(source: str) -> list[str]:
    """The names in settings.py's docstring table, in the order listed."""
    names = []
    for line in source.split('"""')[1].splitlines():
        row = ENV_ROW.match(line)
        if row:
            names.extend(part.strip() for part in row.group(1).split(","))
    return names


def dead_names(paths: list[Path]) -> list[str]:
    """Rule 3. Tracked text still naming a file that is gone."""
    failures = []
    for path in paths:
        name = path.as_posix()
        text = read(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for dead, pattern in DEAD.items():
                if pattern.search(line) and (name, dead) not in ALLOWED:
                    failures.append("%s:%d names %s" % (name, number, dead))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true",
                        help="print what each rule scans")
    args = parser.parse_args()

    files = tracked()
    markdown = pages(files)
    text = scanned(files)

    if args.list:
        print("rule 1 - links, %d page(s):" % len(markdown))
        for path in markdown:
            print("  %s" % path.as_posix())
        source = read(Path(ENV_SOURCE)) or ""
        print("rule 2 - %d variable(s) in %s, against %s"
              % (len(env_variables(source)), ENV_SOURCE, ENV_PAGE))
        print("rule 3 - %d tracked text file(s), %d dead name(s)"
              % (len(text), len(DEAD_NAMES) + 1))
        return 0

    failures, rules = [], []
    if CHECK_LINKS:
        failures.extend(broken_links(markdown))
        rules.append("%d page(s) link cleanly" % len(markdown))
    if CHECK_ENV_DOCUMENTED:
        failures.extend(undocumented_variables())
        rules.append("%s documents every variable" % ENV_PAGE)
    if CHECK_DEAD_NAMES:
        failures.extend(dead_names(text))
        rules.append("nothing names a removed module")

    if failures:
        print("%d failure(s):" % len(failures))
        for line in failures:
            print("  - %s" % line)
        return 1
    print("docs OK - %s" % ", ".join(rules))
    return 0


if __name__ == "__main__":
    sys.exit(main())
