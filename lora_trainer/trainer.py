"""The trainer the standalone dashboard drives — the app's own, imported.

The training code lives in ember/trainer/run.py, which the 🎭 Face LoRA
page in the app runs too. This dashboard is for pods on a build that
predates that page; it reaches into the checkout for the same module
rather than keeping a second copy that would drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ember.trainer.run import (  # noqa: E402,F401
    DEFAULTS,
    IMAGE_SUFFIXES,
    NAME_RE,
    PHASES,
    Run,
    install,
)
