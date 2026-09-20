"""Taking a file off the browser and giving it a name to be called by.

Turning that name back into an image is `common`'s job, because the tab
routes are what need it. This module only writes the file down and sweeps
the ones nobody ever claimed.
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from ember.web.routes.common import UPLOADS, require_auth

# An upload nothing has claimed within this long is abandoned — a page
# closed between choosing an image and pressing Generate. Swept lazily on
# the next upload rather than by a timer: there is no idle cost, and the
# only moment the directory can grow is the moment something is added.
UPLOAD_TTL = 6 * 3600

# Anything larger than this is refused before it is written. A 24 MP phone
# photo is ~30 MB; the cap is generous enough for that and mean enough
# that a stuck client cannot fill an ephemeral disk.
MAX_UPLOAD = 64 * 1024 * 1024


def _sweep_uploads() -> None:
    """Drop uploads older than UPLOAD_TTL. Best effort, never raises."""
    cutoff = time.time() - UPLOAD_TTL
    try:
        for entry in UPLOADS.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def register(api: APIRouter) -> None:
    @api.post("/uploads", dependencies=[Depends(require_auth)])
    async def upload(file: UploadFile):
        """Take one file and hand back an id to reference it by.

        Separate from the generate route on purpose. A batch of
        four submissions against the same source image should not re-send
        it four times; an id costs nothing to repeat.
        """
        _sweep_uploads()
        upload_id = uuid.uuid4().hex
        target = UPLOADS / upload_id
        size = 0
        with open(target, "wb") as handle:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        413, "That file is larger than %d MB."
                             % (MAX_UPLOAD // (1024 * 1024)))
                handle.write(chunk)
        return {"id": upload_id, "size": size,
                "name": file.filename or upload_id}
