"""TEMPORARY. Deleted before Phase 2 ends — do not import this module.

The configuration now lives in ember/settings.py (the environment),
ember/logs.py (the logger) and ember/pipelines/<name>/constants.py (each
model's facts). This re-export keeps the tree runnable while the importers
are rewritten, and keeps the two side effects importing the old module had:
logging is configured and the output tree is made. The startup line it also
used to print is now settings.log_startup(), called by each entry point.
"""

from ember import logs, settings
from ember.logs import log                                   # noqa: F401
from ember.settings import *                                 # noqa: F401,F403
from ember.pipelines.krea2.constants import *                # noqa: F401,F403
from ember.pipelines.krea2_v2.constants import *             # noqa: F401,F403
from ember.pipelines.krea2_v2_edit.constants import *        # noqa: F401,F403
from ember.pipelines.wan.constants import *                  # noqa: F401,F403
from ember.pipelines.minimax.constants import *              # noqa: F401,F403

logs.setup()
settings.ensure_dirs()
