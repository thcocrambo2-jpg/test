#!/usr/bin/env python3
"""Fail when build.sh and build.ps1 disagree about what goes in the binary.

The two build scripts each carry their own copy of the Nuitka
--include-* list. That duplication is deliberate: build.sh produces the
artifact every customer runs today, and adding Windows was not worth
editing it for. The price of not sharing the list is drift, and this is
what is paid instead.

Drift here does not look like a broken build. Every flag in that list
exists because leaving it out produces a binary that COMPILES AND RUNS and
is then wrong in a way nobody notices for a while:

    --include-data-files=scripts/PINS.json      un-pinned weights, and
                                                CivitAI asking customers
                                                for a token
    --include-package=hf_xet                    every model download drops
                                                to single-stream HTTP
    --include-package-data=safehttpx            an import-time crash, but
                                                only after the models have
                                                downloaded
    --include-distribution-metadata=gradio      gradio cannot find its own
                                                version in a frozen app

So a flag added to one script and not the other is not a cosmetic
inconsistency: it is one platform's customers quietly getting a worse
build than the other's.

Run it directly, or as `make check-args` — which `make compile` and
`make release` both depend on, so the Linux build refuses to run while the
two are out of step.

    python scripts/check_build_args.py          report and exit 1 on drift
    python scripts/check_build_args.py --list   print the agreed list
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_SH = ROOT / "build.sh"
BUILD_PS1 = ROOT / "build.ps1"

# Flags that are SUPPOSED to differ, and why. Compared as prefixes, since
# each carries a platform-specific value.
#
# Kept explicit rather than inferred: a list of what may differ is a list
# someone has to add to on purpose, so a new platform-specific flag is a
# decision rather than something that slips through because the checker
# could not tell.
PLATFORM_SPECIFIC = {
    "--jobs": "nproc on Linux, NUMBER_OF_PROCESSORS on Windows",
    "--output-dir": "same value, set from a variable on each side",
    "--output-filename": "krea2app vs krea2app.exe — Windows cannot execute an extensionless file",
    "--static-libpython": "Unix-only linking choice; an error on Windows",
    "--mingw64": "Windows only, and only when MSVC is absent",
    "--windows-icon-from-ico": "Windows only",
    "--standalone": "identical on both, but set outside the shared list",
    "--onefile": "identical on both, but set outside the shared list",
    "--assume-yes-for-downloads": "identical on both, but set outside the shared list",
    "--remove-output": "identical on both, but set outside the shared list",
    # Both scripts locate hf_xet's .dist-info at build time, so the value is
    # an absolute path on the machine doing the building and can never
    # match. That the discovery still HAPPENS on both sides is checked
    # separately below, because it is the part that silently degrades.
    "--include-data-dir=/": "absolute path, computed at build time",
}


def flag_name(flag: str) -> str:
    """The part before the first '=', which is what identifies a flag."""
    return flag.split("=", 1)[0]


def is_platform_specific(flag: str) -> bool:
    if flag_name(flag) in PLATFORM_SPECIFIC:
        return True
    # The computed hf_xet metadata directory, on either platform's path
    # shape (/home/... or C:\...).
    return bool(re.match(r"^--include-data-dir=(/|[A-Za-z]:)", flag))


def read_sh_flags(path: Path) -> list[str]:
    """Every --include-* flag in build.sh's nuitka invocation.

    Scoped to the invocation rather than the whole file so a flag named in
    a comment elsewhere — the header, an error message — is not mistaken
    for one that is passed.
    """
    text = path.read_text(encoding="utf-8")
    start = text.find("-m nuitka")
    if start == -1:
        sys.exit(f"{path.name}: could not find the 'python -m nuitka' call")
    end = text.find("\n    app.py", start)
    if end == -1:
        sys.exit(f"{path.name}: could not find the end of the nuitka call "
                 "(expected a line '    app.py')")
    body = text[start:end]

    flags = []
    for line in body.splitlines():
        line = line.strip()
        # Shell comments inside the invocation are written as `# ... ` —
        # backtick-quoted so they survive line continuations. Skip them, or
        # a flag *described* in a comment would count as one passed.
        if line.startswith("`#") or line.startswith("#"):
            continue
        line = line.rstrip("\\").strip()
        if line.startswith("--include-"):
            flags.append(line)
    return flags


def read_ps1_flags(path: Path) -> list[str]:
    """Every --include-* flag in build.ps1's $sharedArgs array."""
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"\$sharedArgs\s*=\s*@\(", text)
    if not match:
        sys.exit(f"{path.name}: could not find the $sharedArgs array")
    # Walk to the closing paren of that array, so a later @( ... ) is not
    # swallowed.
    depth = 0
    start = match.end() - 1
    end = None
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        sys.exit(f"{path.name}: $sharedArgs array is not closed")
    body = text[start:end]

    flags = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        found = re.match(r"^'(--include-[^']+)'", line)
        if found:
            flags.append(found.group(1))
    return flags


def check_xet_discovery() -> list[str]:
    """Both scripts must still locate hf_xet's .dist-info at build time.

    This is the one piece of shared *logic* rather than a shared flag: each
    script computes the path in its own language. It cannot be diffed, so
    the check is that neither side has lost it — a build without it
    downloads every weight over plain HTTP and says nothing.
    """
    problems = []
    for path, needle in ((BUILD_SH, "hf_xet-*.dist-info"),
                         (BUILD_PS1, "hf_xet-*.dist-info")):
        text = path.read_text(encoding="utf-8-sig")
        if needle not in text:
            problems.append(
                f"{path.name} no longer looks for {needle}. Without it the "
                f"build silently loses Xet acceleration."
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="print the agreed flag list and exit")
    args = parser.parse_args()

    sh = [f for f in read_sh_flags(BUILD_SH) if not is_platform_specific(f)]
    ps1 = [f for f in read_ps1_flags(BUILD_PS1) if not is_platform_specific(f)]

    if not sh:
        sys.exit("build.sh: parsed zero --include-* flags, which cannot be "
                 "right — the parser and the script have diverged.")
    if not ps1:
        sys.exit("build.ps1: parsed zero --include-* flags, which cannot be "
                 "right — the parser and the script have diverged.")

    if args.list:
        for flag in sorted(set(sh) & set(ps1)):
            print(flag)
        return 0

    only_sh = sorted(set(sh) - set(ps1))
    only_ps1 = sorted(set(ps1) - set(sh))
    problems = check_xet_discovery()

    if not only_sh and not only_ps1 and not problems:
        print(f"build args agree: {len(sh)} shared flags, "
              f"{len(set(sh))} distinct")
        return 0

    print("BUILD ARGUMENTS HAVE DRIFTED", file=sys.stderr)
    print(file=sys.stderr)
    if only_sh:
        print("  in build.sh (Linux) but NOT build.ps1 (Windows):", file=sys.stderr)
        for flag in only_sh:
            print(f"      {flag}", file=sys.stderr)
        print("  -> Windows customers get a build missing this.", file=sys.stderr)
        print(file=sys.stderr)
    if only_ps1:
        print("  in build.ps1 (Windows) but NOT build.sh (Linux):", file=sys.stderr)
        for flag in only_ps1:
            print(f"      {flag}", file=sys.stderr)
        print("  -> Linux pods get a build missing this.", file=sys.stderr)
        print(file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    if problems:
        print(file=sys.stderr)
    # ASCII on purpose: this runs in a Windows console as often as in WSL,
    # and there it is decoded with the OEM code page, where an em-dash
    # arrives as a replacement character.
    print("  Add the flag to the other script, or - if it genuinely belongs", file=sys.stderr)
    print("  to one platform - name it in PLATFORM_SPECIFIC in this file", file=sys.stderr)
    print("  with the reason.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
