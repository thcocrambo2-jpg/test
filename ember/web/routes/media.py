"""Everything a generated file can be asked for: bytes, thumbnail, page,
deletion, and the settings it was made with.

`/media` and `/thumbs` are the only way a generated file reaches the
browser, and they contain themselves to OUTPUT_DIR through
`gallery_index.safe_path()` — the one implementation, shared with
`delete()`. A `path_id` is the OUTPUT_DIR-relative posix path, and no
absolute path ever crosses the wire.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ember import features
from ember.web import gallery_index
from ember.generation import recipes
from ember.web import tabschema
from ember.web.routes.common import (
    require_auth, require_feature, _media_list,
)

# The most files one bulk delete may name. A gallery page holds at most
# 100 (the `limit` on GET /gallery) and the selection is cleared when the
# page turns, so this is well past anything the UI can ask for — it is
# here to bound the loop, not to constrain the feature.
MAX_BULK_DELETE = 500

# mimetypes reads the Windows registry, where .webp is often absent and
# .mp4 sometimes is too — a thumbnail served as application/octet-stream
# downloads instead of rendering. Stated here rather than left to the
# machine the app happens to be running on.
_TYPES = {
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".mp4": "video/mp4", ".webm": "video/webm",
}


def _media_type(name: str) -> str:
    known = _TYPES.get(Path(name).suffix.lower())
    if known:
        return known
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def register(api: APIRouter) -> None:
    @api.get("/media/{path_id:path}", dependencies=[Depends(require_auth)])
    def media(path_id: str):
        """One generated file, contained to OUTPUT_DIR.

        Containment is gallery_index.safe_path() and nothing else — see
        the module docstring. Deliberately not reimplemented here: a
        second copy of a security check is a second chance to get it
        wrong, and this one has three callers.
        """
        return _send(path_id)

    @api.get("/thumbs/{path_id:path}", dependencies=[Depends(require_auth)])
    def thumb(path_id: str):
        """The 512px WebP for a generated file, or the file itself.

        Takes the **media** path_id rather than a thumbnail path, so the
        one containment check still applies: .thumbs/ is a dotted
        directory holding .webp files, and neither would pass safe_path.
        A file with no thumbnail — anything from before gallery_index
        existed, a video, one still encoding — falls back to the original,
        which is thumb_for()'s whole no-backfill policy.
        """
        return _send(path_id, thumb=True)

    def _send(path_id: str, thumb: bool = False):
        try:
            target = gallery_index.safe_path(path_id)
        except (ValueError, OSError):
            # 404 rather than 403: a path outside the output tree and a
            # path that is not there are the same answer to anyone who is
            # allowed to ask, and telling the difference is only useful to
            # someone who is not.
            raise HTTPException(404, "No such file.")
        if not target.exists():
            raise HTTPException(404, "No such file.")
        if thumb:
            target = Path(gallery_index.thumb_for(str(target)))
        media_type = _media_type(target.name)
        # Generated files never change under a name — ComfyUI counts up —
        # so they are safe to cache hard. The zip is the one thing that
        # would not be, and it is not indexed, so it cannot be served here.
        return FileResponse(target, media_type=media_type,
                            headers={"Cache-Control": "private, max-age=86400"})

    # ── gallery ─────────────────────────────────────────────────────

    @api.get("/gallery", dependencies=[Depends(require_auth),
                                       Depends(require_feature(
                                           features.Key.GALLERY))])
    def gallery(cursor: str = Query(default=""),
                limit: int = Query(default=50, ge=1, le=100),
                kind: str = Query(default="")):
        """One page of the listing, newest first.

        `kind=image` narrows it to stills, which is what the reuse strip
        under every upload field asks for. It is not a filter the caller
        could apply to the answer itself: the cursor is an offset into the
        listing, so dropping the clips out of a page taken on a video tab
        leaves a handful of pictures and no way to ask for more — and a
        clip is not something an image input can take in any case.

        An unrecognised `kind` is refused rather than rounded down to
        "everything": handing videos to a caller that asked for stills is
        the one wrong answer this route can give.
        """
        if kind not in ("", "image"):
            raise HTTPException(400, "kind must be 'image', or left off.")
        paths = (gallery_index.list_images() if kind == "image"
                 else gallery_index.list_media())
        start = int(cursor) if cursor.isdigit() else 0
        page = paths[start:start + limit]
        nxt = start + limit
        return {
            "items": _media_list(page),
            "nextCursor": str(nxt) if nxt < len(paths) else None,
            "total": len(paths),
        }

    @api.post("/gallery/delete",
              dependencies=[Depends(require_auth),
                            Depends(require_feature(features.Key.GALLERY))])
    def gallery_delete_many(body: dict = Body(default={})):
        """Delete a selection in one request.

        POST rather than DELETE-with-a-body, and registered above the
        single-file route so the two cannot be confused. A body on DELETE
        is permitted by the letter of the spec and dropped in practice by
        enough proxies — this app already has a Cloudflare tunnel in front
        of it — that it is not worth the elegance. `/gallery/delete` is an
        action path, which is what this is.

        Partial success is the normal outcome, not an exception: a file
        may already have gone, and one bad id in a selection of forty must
        not take the other thirty-nine with it. Each is tried, and the
        ones that would not go come back named so the UI can say which
        rather than "some".
        """
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            raise HTTPException(400, "Send an `ids` array.")
        if len(ids) > MAX_BULK_DELETE:
            raise HTTPException(
                400, "That is more than %d files in one request."
                % MAX_BULK_DELETE)

        deleted, failed = 0, []
        for raw in ids:
            path_id = str(raw)
            try:
                target = gallery_index.safe_path(path_id)
            except (ValueError, OSError):
                failed.append(path_id)
                continue
            if not gallery_index.delete(target):
                failed.append(path_id)
                continue
            recipes.forget(target)
            deleted += 1
        return {"deleted": deleted, "failed": failed}

    @api.delete("/gallery/{path_id:path}",
                dependencies=[Depends(require_auth),
                              Depends(require_feature(features.Key.GALLERY))])
    def gallery_delete(path_id: str):
        try:
            target = gallery_index.safe_path(path_id)
        except (ValueError, OSError):
            raise HTTPException(404, "No such file.")
        if not gallery_index.delete(target):
            raise HTTPException(500, "That file could not be deleted.")
        recipes.forget(target)
        return {"ok": True}

    @api.get("/recipe/{path_id:path}", dependencies=[Depends(require_auth)])
    def recipe(path_id: str):
        """The settings one generated file was made with, or nothing.

        None is the ordinary answer, not an error: anything generated
        before recipes.py existed, or copied into the output folder by
        hand, has none and every caller has to render that case anyway.
        """
        try:
            target = gallery_index.safe_path(path_id)
        except (ValueError, OSError):
            raise HTTPException(404, "No such file.")
        stored = recipes.for_path(target)
        if not stored:
            return {"recipe": None}
        return {"recipe": stored,
                "canLoad": str(stored.get("tab")) in tabschema.BY_KEY
                and features.enabled(str(stored.get("tab")))}
