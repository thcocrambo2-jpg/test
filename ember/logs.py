"""The application logger, and the one call that configures it.

One logger for the whole app, named "ember", so every line a pod prints
carries the same prefix whichever module wrote it. Importing this module
configures nothing: `setup()` is called by each entry point — app.py, the
Docker bake scripts, and the operator scripts under scripts/ — before it
logs anything.

That explicitness is the point. Logging used to be configured as a side
effect of importing the configuration module, which meant every importer
inherited it by accident and no entry point could choose otherwise. It
also meant a module that wanted a constant got a `basicConfig` with it.
"""

import logging

log = logging.getLogger("ember")

_configured = False


def setup() -> None:
    """Configure the root logger. Idempotent — the first call wins.

    `force=True` because a dependency imported earlier may already have
    installed a handler of its own, and the app's format is the one a pod's
    log is read with.
    """
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    _configured = True
