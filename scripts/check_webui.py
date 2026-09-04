#!/usr/bin/env python3
"""Fail when the committed webui_bundle.py is older than webui/src.

Pure stdlib, and deliberately **no Node**: this is what both build hosts
run, and neither of them has npm (context.md constraint 5). It recomputes
`SOURCE_HASH` over the front-end sources and compares it to the one baked
into the generated module. Nothing is compiled and nothing is compared
byte-for-byte against `webui/dist/`, so the answer does not depend on
which zlib or which Vite the checking machine has.

Without it, "someone edited a .tsx and forgot to run `make webui`" is a
silent ship: the build succeeds, the binary runs, and the app it serves is
whatever the front end looked like at the last regeneration. That is the
same failure shape `scripts/check_build_args.py` exists for — a defect a
successful compile hides — which is why both are gates on the build rather
than something to remember.

    python scripts/check_webui.py            report and exit 1 when stale
    python scripts/check_webui.py --quiet    only speak up on failure

`gen_webui_bundle.py` is imported rather than reimplemented: the two
hashes have to be computed the same way forever, and the cheapest
guarantee of that is one function.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_webui_bundle as gen                              # noqa: E402

STALE = """\
The React bundle is stale — webui_bundle.py was generated from a different
front end than the one in webui/src.

    committed  %s
    sources    %s

Regenerate it on a machine with Node, and commit the result:

    make webui

Neither build host has Node, which is why the generated module is
committed and this check is all the build itself does. Shipping without
regenerating would produce a binary that compiles, runs, and serves
whatever the UI looked like the last time somebody remembered.
"""

MISSING = """\
webui_bundle.py does not exist, so the binary would have no front end at
all — it would start, serve the API, and answer every page request with a
"front end not built" notice.

Generate it on a machine with Node, and commit the result:

    make webui
"""

NO_SOURCES = """\
webui/ is missing %s.

That is not a stale bundle, it is an incomplete checkout — this check
cannot tell whether the committed module is current, so it fails rather
than passing by default.
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify webui_bundle.py matches webui/src.")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing when the bundle is current")
    args = parser.parse_args()

    absent = gen.missing_sources()
    if absent:
        print(NO_SOURCES % ", ".join(absent), file=sys.stderr)
        return 1

    try:
        import webui_bundle
    except ImportError:
        print(MISSING, file=sys.stderr)
        return 1

    current = gen.source_hash()
    committed = getattr(webui_bundle, "SOURCE_HASH", None)
    if committed != current:
        print(STALE % (committed or "(none — regenerate it)", current),
              file=sys.stderr)
        return 1

    if not args.quiet:
        # ASCII on purpose, like check_build_args.py: this runs in a
        # Windows console as often as in WSL, and there it is decoded with
        # the OEM code page.
        print("webui bundle is current: %d assets, built %s"
              % (len(webui_bundle.ASSETS),
                 getattr(webui_bundle, "BUILT_AT", "?")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
