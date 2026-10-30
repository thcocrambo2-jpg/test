"""The picture libraries: a manifest and its files, for the picker.

Gated like a tab, because a library has no weights behind it and so no
downloads backstop either: a library opens only while the licence grants a
tab with an images field that names it. Today that is 🪄 Qwen Edit 2511's
`library="poses"`; switching it on for another tab is that one word in the
other tab's schema, and the gate follows without a change here. The licence
server applies its own version of the same rule before it signs the pack's
URL, so a modified client gets no further.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ember import features
from ember.web import library, tabschema
from ember.web.routes.common import require_auth


def _granted(name: str) -> bool:
    return any(
        features.enabled(schema.key)
        and any(field.library == name for field in schema.fields)
        for schema in tabschema.SCHEMAS
    )


def _open(name: str) -> None:
    if name not in library.NAMES:
        raise HTTPException(404, "There is no %s library." % name)
    if not _granted(name):
        raise HTTPException(403, "The %s library is not part of this "
                                 "licence." % name)


def register(api: APIRouter) -> None:
    @api.get("/library/{name}", dependencies=[Depends(require_auth)])
    def manifest(name: str):
        """Everything in one library — ids, names, categories, tags and
        which renderings each picture has — or, the first time on a machine,
        how far its download has got. The picker builds file URLs from the
        ids; it is never told a bucket path."""
        _open(name)
        try:
            return library.manifest(name)
        except library.Unavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @api.get("/library/{name}/{size}/{item_id}/{kind}",
             dependencies=[Depends(require_auth)])
    def picture(name: str, size: str, item_id: str, kind: str):
        """One picture: `thumbs` for the cards, `full` for the field."""
        _open(name)
        try:
            path = library.file(name, size, item_id, kind)
        except KeyError:
            raise HTTPException(404, "No such picture in the %s library."
                                % name) from None
        # The version is in the path on disk and a file never changes under
        # it, so the browser may keep it as long as it likes.
        return FileResponse(path, media_type="image/webp",
                            headers={"Cache-Control":
                                     "private, max-age=604800, immutable"})
