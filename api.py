"""The HTTP API the React UI talks to. A thin adapter, and nothing else.

Everything real happens elsewhere. handlers.py runs the generators,
jobqueue.py runs them one at a time per lane, tabschema.py says what a
form is and how its values become a positional call, gallery_index.py
lists and thumbnails the outputs, and licensing / plans / presets /
prompts / showcase talk to the licence server. This module is the layer
that turns those into routes, and it is deliberately the thinnest thing
in the repository: the ~2,600 lines it wraps encode VAE size snapping,
model-swap VRAM release, seat heartbeats, HF mirror pinning and crash
recovery, and none of that is why the UI looked dated.

Two things here are not thin, because they were invisible before and
would be gone if nobody wrote them down.

The licence gate
----------------
`ui.py:5139` reads `if not features.enabled(_key): continue`. It is a
**render** gate, and it worked only because a tab that is never built has
no Gradio endpoint either. Register FastAPI routes unconditionally and
that gate is not weakened, it is *removed*.

The partial backstop is downloads.py:706 — weights for ungranted features
are never fetched, so most tabs would fail at "the model is not
downloaded". Two tabs have no weights at all: `json_batch` and
`community_prompts` both declare `needs=()` (features.py:155,160), so
they would be fully working for a licence that does not include them.

Four layers, and each one alone would be enough on a good day:

  1. every per-tab route is registered inside `_mount_tabs()`, through a
     loop that attaches `Depends(require_feature(key))`. There is no
     other way to add one;
  2. `tabschema.submit()` checks again — that is the funnel every
     generation passes through, so a route registered by accident still
     cannot run one;
  3. `/catalog` and `/schema/{tab}` describe only entitled tabs, so the
     navigation never learns an ungranted tab exists;
  4. `scripts/check_routes.py` imports the app with `features.resolve([])`
     and asserts every `/api/v1/tabs/` path 403s, naming `json_batch` and
     `community_prompts` explicitly.

Path containment
----------------
`/media` and `/thumbs` replace Gradio's `allowed_paths=[OUTPUT_DIR]` plus
`gr.set_static_paths`, which did the containment invisibly. They do it
through `gallery_index.safe_path()` — the one implementation, shared with
`delete()`. A `path_id` is the OUTPUT_DIR-relative posix path, the same
key `recipes._key()` uses, and **no absolute path ever crosses the wire**.

Auth
----
There was none, on a public URL: possession of the *.gradio.live link was
the whole access control. A per-process `secrets.token_urlsafe(32)` is
printed in the URL **fragment**, and the SPA exchanges it for an
HttpOnly cookie and strips it with `history.replaceState`.

The fragment is the point. Fragments are never sent to servers, so the
token stays out of Cloudflare's logs, out of every proxy in between and
out of `Referer` headers. A query parameter would be in all three.
"""

import dataclasses
import io
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import anyio

from fastapi import (
    APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request,
    Response, UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image

import features
import gallery_index
import handlers
import jobqueue
import licensing
import plans
import presets
import prompts
import recipes
import showcase
import tabschema
from comfy import GPU_COUNT
from config import OUTPUT_DIR, TEMP_DIR, log

PREFIX = "/api/v1"

# The cookie the SPA trades the fragment token for. HttpOnly so no script
# on the page can read it back out, SameSite=Lax so another site cannot
# make the browser spend it, Secure because the tunnel is HTTPS. Named
# without "session" because it is not one — there is no server-side
# session, only this process's token.
COOKIE = "krea2_key"

# Off by default, on for scripts/dryrun.py --api-only. It exists because
# the alternative during development is pasting a token by hand on every
# restart, and the alternative to *that* is developers commenting the
# auth out, which is how auth stops existing.
ALLOW_ANON = bool(os.environ.get("KREA2_UI_ALLOW_ANON"))

TOKEN = secrets.token_urlsafe(32)

# Uploads land here and are read once, at submit. Under TEMP_DIR because
# that is already the ephemeral tree comfy.py and the tunnel binary use.
UPLOADS = TEMP_DIR / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

# An upload nothing has claimed within this long is abandoned — a page
# closed between choosing an image and pressing Generate. Swept lazily on
# the next upload rather than by a timer: there is no idle cost, and the
# only moment the directory can grow is the moment something is added.
UPLOAD_TTL = 6 * 3600

# Anything larger than this is refused before it is written. A 24 MP phone
# photo is ~30 MB; the cap is generous enough for that and mean enough
# that a stuck client cannot fill an ephemeral disk.
MAX_UPLOAD = 64 * 1024 * 1024

# How often the SSE loop looks for something to say, and how often it
# says nothing out loud. jobqueue is thread-based and pull-oriented with
# a revision() counter, so this is a poll either way — see stream().
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15


# ─────────────────────────────────────────────────────────────── auth

def _is_https(request: Request) -> bool:
    """Is this connection actually TLS, directly or behind the tunnel?

    Decides only whether the auth cookie is marked Secure. Marking it so
    over plain http would make the browser drop it silently, which reads
    as "the login does not work" on a developer machine; not marking it
    over https would let it travel in the clear on the day somebody
    reaches the pod on port 80.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _authorised(request: Request) -> bool:
    """Does this request carry the process token?

    Three ways in, and the order is deliberate. The cookie is the ordinary
    one. The header is for a client that would rather hold the token
    itself than have a browser hold it. The query parameter is accepted
    **only** on the exchange route below, never here.
    """
    if ALLOW_ANON:
        return True
    cookie = request.cookies.get(COOKIE)
    if cookie and secrets.compare_digest(cookie, TOKEN):
        return True
    header = request.headers.get("x-krea2-key", "")
    return bool(header) and secrets.compare_digest(header, TOKEN)


def require_auth(request: Request) -> None:
    if not _authorised(request):
        raise HTTPException(401, "This link is missing its access key.")


def require_feature(key):
    """A dependency that answers 403 unless the licence grants `key`.

    Layer one of four. Attached by `_mount_tabs()` to every per-tab route
    at registration time, which is the only place per-tab routes are
    created — see the module docstring on why "the route simply does not
    exist" stopped being true when Gradio went.
    """
    def check() -> None:
        if not features.enabled(key):
            raise HTTPException(
                403, "%s is not part of this licence." % features.label_for(key)
            )
    return check


# ───────────────────────────────────────────────────────── uploads

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


def _upload_path(upload_id: str) -> Path:
    """An upload id -> its file, or a 400.

    The id is generated here and is a bare hex string, so anything with a
    separator or a dot in it did not come from this server. Checked rather
    than sanitised: there is no legitimate upload id that needs cleaning
    up, and a check that rejects is easier to be sure of than one that
    repairs.
    """
    if not upload_id or not upload_id.isalnum():
        raise HTTPException(400, "That is not an upload id.")
    path = UPLOADS / upload_id
    if not path.is_file():
        raise HTTPException(
            400, "That upload has expired — choose the image again."
        )
    return path


def _pil(upload_id: str) -> Image.Image:
    """An upload id -> a PIL image, which is what the handlers take.

    Loaded eagerly (`.load()`), because the file behind it is temporary
    and the handler runs later, on a worker thread, after the request that
    supplied it is long gone.
    """
    with Image.open(_upload_path(upload_id)) as image:
        image.load()
        return image.convert("RGBA") if image.mode == "P" else image.copy()


def _resolve_uploads(schema, raw: dict) -> dict:
    """Turn every upload reference in a submitted bag into what it means.

    Done here, on the request thread, rather than in the worker: the
    values a job runs on are frozen the moment it is queued (jobqueue.Job)
    and that is the behaviour a queue has to have — changing a control, or
    closing the page, after pressing Generate must not reach a job already
    in the line. Gradio decoded the upload on the click for the same
    reason.

    Three kinds need it, and they want three different Python objects:

      image  a PIL image
      mask   the `{background, layers}` pair gr.ImageEditor produced, which
             _prepare_inpaint_inputs takes apart. The contract did not
             change in the rewrite — the React canvas uploads a background
             and one PNG per painted layer, and the union / dilate / blur /
             snap-to-16 all still happen in handlers.py, byte for byte
      file   a path on disk, because generate_from_json reads it with
             Path(...).read_text()
    """
    values = dict(raw)
    for field in schema.named():
        value = values.get(field.name)
        if field.kind == "image":
            values[field.name] = _pil(value) if value else None
        elif field.kind == "file":
            values[field.name] = str(_upload_path(value)) if value else None
        elif field.kind == "mask":
            values[field.name] = _mask_value(value)
    return values


def _mask_value(value):
    """`{background, layers}` of upload ids -> the same, as PIL images.

    None when there is no background, which is the shape
    _prepare_inpaint_inputs already refuses with "Upload an image first."
    — so the error stays one sentence written in one place.
    """
    if not isinstance(value, dict) or not value.get("background"):
        return None
    layers = value.get("layers") or []
    if not isinstance(layers, list):
        raise HTTPException(400, "The mask layers are not a list.")
    return {
        "background": _pil(value["background"]),
        "layers": [_pil(layer) for layer in layers],
    }


# ─────────────────────────────────────────────────────────── media

def _media(path, recipe=None) -> dict:
    """One generated file, as the UI's MediaItem.

    `id` and `path` are both the OUTPUT_DIR-relative posix key. The
    absolute path is deliberately not here: it names this pod's
    filesystem, and there is no version of the browser knowing
    /workspace/krea2/output/... that is worth the leak.
    """
    key = gallery_index.key_for(path) or Path(path).name
    quoted = quote(key)
    is_video = key.lower().endswith(gallery_index.VIDEO_EXT)
    row = {
        "id": key,
        "path": key,
        "url": "%s/media/%s" % (PREFIX, quoted),
        "thumbUrl": "%s/thumbs/%s" % (PREFIX, quoted),
        "kind": "video" if is_video else "image",
        "width": 0,
        "height": 0,
        "createdAt": "",
    }
    try:
        stat = Path(path).stat()
        row["createdAt"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime))
    except OSError:
        pass
    if not is_video:
        try:
            # Header-only: PIL does not decode pixels until you ask it to,
            # so a page of two dozen costs two dozen small reads.
            with Image.open(path) as image:
                row["width"], row["height"] = image.size
        except Exception:                        # noqa: BLE001
            pass
    if recipe is None:
        recipe = recipes.for_path(path)
    if recipe:
        row["seed"] = recipe.get("seed")
        row["tab"] = recipe.get("tab")
        row["tabLabel"] = recipe.get("tab_label")
        row["prompt"] = _recipe_prompt(recipe)
    return row


def _recipe_prompt(recipe) -> str:
    """The prompt out of a stored recipe, for the gallery caption.

    Taken by position from the tab's schema rather than by searching the
    rows for something long: `prompt_field` names it, and the recipe's
    fields are in submission order, so the two line up exactly.
    """
    schema = tabschema.BY_KEY.get(str(recipe.get("tab")))
    if schema is None or not schema.prompt_field:
        return ""
    names = [f.name for f in schema.named()]
    try:
        index = names.index(schema.prompt_field)
    except ValueError:
        return ""
    rows = recipe.get("fields") or []
    if index >= len(rows):
        return ""
    row = rows[index]
    value = row[1] if isinstance(row, (list, tuple)) and len(row) > 1 else None
    return value if isinstance(value, str) else ""


def _media_list(paths) -> list:
    return [_media(path) for path in paths]


def _display_json(schema, result) -> dict:
    """One job's latest yield, with paths turned into media rows.

    The keys are the tab's own result_keys, so the video tab's "videos"
    and "latest" arrive under those names rather than as positions 0 and
    1 — which is the whole reason jobqueue stopped carrying an int.
    """
    if not result:
        return {}
    row = {}
    for key, value in result.items():
        if key in ("images", "videos") and isinstance(value, list):
            row[key] = _media_list(value)
        elif key == "latest" and value:
            row[key] = _media(value)
        else:
            row[key] = value
    return row


# ───────────────────────────────────────────────────────────── app

def create_app() -> FastAPI:
    """Build the application. Called once by serve.py, and by the checks.

    A function rather than a module-level `app = FastAPI()` because
    scripts/check_routes.py has to build it against a *different* licence
    — `features.resolve([])` — and a module-level app would already have
    been built against whatever the import order gave it.
    """
    app = FastAPI(title="Ember", docs_url=None, redoc_url=None,
                  openapi_url=None)
    api = APIRouter(prefix=PREFIX)

    # ── auth ────────────────────────────────────────────────────────

    @api.post("/auth")
    def auth(request: Request, response: Response,
             body: dict = Body(default={})):
        """Trade the fragment token for a cookie.

        The only route that reads a token from the body, and the reason
        the token travels in the URL fragment: the SPA reads
        `location.hash`, POSTs it here, and calls `history.replaceState`
        to take it out of the address bar. Fragments are never sent to
        servers, so nothing between the browser and this process ever saw
        it — not the tunnel, not a proxy, not a Referer header.
        """
        supplied = str(body.get("token") or "")
        if not ALLOW_ANON and not (
                supplied and secrets.compare_digest(supplied, TOKEN)):
            raise HTTPException(401, "That access key is not for this app.")
        response.set_cookie(
            COOKIE, TOKEN, httponly=True, secure=_is_https(request),
            samesite="lax", max_age=30 * 24 * 3600, path="/",
        )
        return {"ok": True}

    # ── session and catalogue ───────────────────────────────────────

    @api.get("/session", dependencies=[Depends(require_auth)])
    def session():
        _plan_id, plan_name = licensing.plan()
        expires = licensing.expires_at()
        return {
            "brand": "Ember",
            "tagline": "ComfyUI generation suite",
            "planName": plan_name,
            "expiresAt": expires.isoformat() if expires else None,
            "outputDir": str(OUTPUT_DIR),
            "modelCount": len(handlers.MODEL_CHOICES),
            "gpuCount": GPU_COUNT,
            "isAdmin": licensing.is_admin(),
            # Layer three: the navigation is built from this, so a tab
            # this licence does not grant is not merely unreachable, it is
            # never mentioned.
            "features": [str(key) for key in features.enabled_keys()],
        }

    @api.get("/catalog", dependencies=[Depends(require_auth)])
    def catalog():
        """Every entitled tab's schema, plus the model registries.

        The registries are what the ~150 lines of `*_changed` handlers in
        ui.py were made of: picking a model resets Steps and CFG to that
        variant's defaults and swaps its trigger words into the prompt.
        Shipped as data so React does it locally — see tabschema.catalog.
        """
        return tabschema.catalog()

    @api.get("/schemas", dependencies=[Depends(require_auth)])
    def schemas():
        """Every entitled tab's schema on its own.

        A convenience over /catalog for the navigation, which needs the
        tab list before it needs any registry. Filtered the same way.
        """
        return [schema.to_json() for schema in tabschema.entitled()]

    # ── the queue ───────────────────────────────────────────────────

    @api.get("/queue", dependencies=[Depends(require_auth)])
    def queue():
        """The whole queue as JSON. The fallback under /stream.

        Kept deliberately: this *is* today's behaviour — ui.py polls
        jobqueue on a one-second gr.Timer — so if SSE turns out to be
        blocked by something between the pod and the browser, the worst
        case is the behaviour customers already have.
        """
        return _queue_json()

    @api.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
    def cancel(job_id: str):
        message = jobqueue.cancel(job_id)
        log.info("Queue: %s", message)
        return {"message": message, "queue": _queue_json()}

    @api.post("/jobs/clear", dependencies=[Depends(require_auth)])
    def clear():
        return {"message": jobqueue.clear_finished(), "queue": _queue_json()}

    # ── uploads ─────────────────────────────────────────────────────

    @api.post("/uploads", dependencies=[Depends(require_auth)])
    async def upload(file: UploadFile):
        """Take one file and hand back an id to reference it by.

        Separate from the generate route on purpose. The mask editor
        uploads a background and one PNG per painted layer, and a batch of
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

    # ── media ───────────────────────────────────────────────────────

    @api.get("/media/{path_id:path}", dependencies=[Depends(require_auth)])
    def media(path_id: str):
        """One generated file. Replaces Gradio's allowed_paths.

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
        media_type = mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
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
                limit: int = Query(default=24, ge=1, le=100)):
        paths = gallery_index.list_media()
        start = int(cursor) if cursor.isdigit() else 0
        page = paths[start:start + limit]
        nxt = start + limit
        return {
            "items": _media_list(page),
            "nextCursor": str(nxt) if nxt < len(paths) else None,
            "total": len(paths),
        }

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

    # ── the licence server's three catalogues ───────────────────────

    @api.get("/plans", dependencies=[Depends(require_auth)])
    def plan_catalogue(force: bool = Query(default=False)):
        cat = plans.catalogue(force=force)
        return {
            "plans": [dataclasses.asdict(row) for row in cat.plans],
            "features": {key: dataclasses.asdict(info)
                         for key, info in cat.features.items()},
            "cycles": [dataclasses.asdict(row) for row in cat.cycles],
            "error": cat.error,
            "contact_url": cat.contact_url,
            "owned": [str(key) for key in features.enabled_keys()],
        }

    @api.get("/showcase", dependencies=[Depends(require_auth)])
    def showcase_page():
        page = showcase.showcase()
        return dataclasses.asdict(page) if page is not None else None

    @api.get("/presets/{tab}", dependencies=[Depends(require_auth)])
    def preset_list(tab: str, force: bool = Query(default=False)):
        """One presets tab's dropdown, and which row is its default.

        `tab` is a presets tab id (presets.TABS), not a feature key: a
        preset is saved from a generation tab and belongs to it, but is
        applied wherever those dials exist — which includes the matching
        Edit tab. Checked against the list rather than trusted, so this
        cannot be pointed at an arbitrary string.
        """
        if tab not in presets.TABS:
            raise HTTPException(404, "No presets for that tab.")
        cat = presets.catalogue(force=force)
        rows = cat.for_tab(tab)
        return {
            "presets": [{"id": row.id, "name": row.name,
                         "description": row.description,
                         "isDefault": row.is_default} for row in rows],
            "default": next((row.name for row in rows if row.is_default),
                            None),
            "error": cat.error,
            "revision": jobqueue.preset_revision(tab),
        }

    @api.get("/prompts", dependencies=[Depends(require_auth),
                                       Depends(require_feature(
                                           features.Key.COMMUNITY_PROMPTS))])
    def prompt_library(tab: str = Query(default=""),
                       source: str = Query(default=""),
                       search: str = Query(default=""),
                       skip: int = Query(default=0, ge=0),
                       limit: int = Query(default=12, ge=1, le=48),
                       force: bool = Query(default=False)):
        page = prompts.library(tab=tab or None, source=source or None,
                               search=search, skip=skip, limit=limit,
                               force=force)
        return {
            "prompts": [dataclasses.asdict(row) for row in page.prompts],
            "total": page.total, "skip": page.skip, "limit": page.limit,
            "error": page.error,
        }

    # ── per-tab routes ──────────────────────────────────────────────

    _mount_tabs(api)

    # ── the event stream ────────────────────────────────────────────

    @api.get("/stream", dependencies=[Depends(require_auth)])
    def stream(request: Request):
        """One SSE connection for the whole app.

        SSE and not websockets, and the reason is jobqueue rather than
        taste: it is thread-based and pull-oriented with a `revision()`
        counter, so *something* has to poll it either way — a websocket
        would be a second protocol wrapped around the same loop. What
        EventSource adds for free is reconnection, which is the part a
        hand-written socket client always gets wrong, on a tunnel that
        drops idle connections.

        Three event types, and a heartbeat comment every 15 seconds so
        that neither Cloudflare nor a corporate proxy decides a quiet
        stream is a dead one.
        """
        return StreamingResponse(
            _events(request), media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Nginx and several corporate proxies buffer a response
                # body by default, which turns a live stream into one
                # delivery when it ends. This is the header that stops it,
                # and its absence is what makes an SSE bug look like "the
                # queue only updates when I reload".
                "X-Accel-Buffering": "no",
            },
        )

    app.include_router(api)
    _mount_spa(app)
    return app


def _queue_json() -> dict:
    revision, rows, waiting, running = jobqueue.snapshot()
    return {
        "revision": revision,
        "waiting": waiting,
        "running": running,
        "jobs": [{
            "id": row.id, "lane": row.lane, "tab": row.tab,
            "tabLabel": row.tab_label, "title": row.title,
            "status": row.status, "progress": row.progress,
            "submitted": row.submitted, "place": row.place,
        } for row in rows],
    }


def _mount_tabs(api: APIRouter) -> None:
    """Register the per-tab routes — the only place they are created.

    Layer one of the licence gate. Every route below is attached inside
    this loop with `Depends(require_feature(schema.key))`, so there is no
    way to add a tab route that is not gated; adding one by hand outside
    this function is the mistake scripts/check_routes.py exists to catch.

    Note the loop walks `tabschema.SCHEMAS` and not `entitled()`: the
    routes are registered for every tab and each one *refuses*, rather
    than not existing. That is deliberate — a licence upgrade takes effect
    on the next `features.resolve()` without rebuilding the app, and a
    403 with a sentence is a better answer than a 404 to a customer who
    has just paid for the tab.
    """
    for schema in tabschema.SCHEMAS:
        _mount_tab(api, schema)


def _mount_tab(api: APIRouter, schema) -> None:
    """One tab's three routes, closed over its schema."""
    gate = [Depends(require_auth), Depends(require_feature(schema.key))]
    key = str(schema.key)

    @api.get("/schema/" + key, dependencies=gate, name="schema_" + key)
    def get_schema():
        return schema.to_json()

    @api.post("/schema/%s/apply" % key, dependencies=gate,
              name="apply_" + key)
    def apply(body: dict = Body(default={})):
        """A preset name or a stored recipe -> values for the form.

        Server-side, and it has to be: guarding a value means knowing
        whether this pod has that LoRA file and what this build's slider
        range is, and the browser knows neither. Out of range is not an
        error here — an unknown choice leaves the control alone and a
        number is clamped, which is ui._pick and ui._num exactly.

        Answers `{"values": {...}}` holding only the controls it could
        set. Everything absent keeps whatever the customer had, which is
        the difference between "load what I can" and "reset the form".
        """
        if "preset" in body:
            name = str(body.get("preset") or "")
            settings = _preset_settings(schema.preset_tab, name)
            if settings is None:
                # A preset disabled or renamed while the page sat open.
                # Changing nothing is the honest answer.
                return {"values": {}, "applied": None}
            return {"values": schema.preset_values(settings),
                    "applied": name}
        if "recipe" in body:
            stored = body.get("recipe")
            if isinstance(stored, str):
                try:
                    target = gallery_index.safe_path(stored)
                except (ValueError, OSError):
                    raise HTTPException(404, "No such file.")
                stored = recipes.for_path(target)
            if not isinstance(stored, dict):
                return {"values": {}, "applied": None}
            if str(stored.get("tab")) != key:
                raise HTTPException(
                    400, "That recipe belongs to the %s tab."
                         % (stored.get("tab_label") or stored.get("tab")))
            values = schema.restore_recipe(stored.get("fields") or [])
            # Two values are deliberately not restored as recorded: Seed
            # becomes the seed the picture actually ran on rather than
            # whatever was in the box, and Random seed goes off. Together
            # they are the difference between "the same settings" and "the
            # same image", which is what someone clicking this is asking
            # for. ui._use_recipe does the same, by label.
            if stored.get("seed") is not None:
                values["seed"] = stored["seed"]
                values["randomize"] = False
            return {"values": values, "applied": stored.get("tab_label")}
        if "settings" in body:
            return {"values": schema.preset_values(body.get("settings") or {}),
                    "applied": None}
        raise HTTPException(400, "Send a preset name, a recipe or settings.")

    @api.get("/tabs/%s/display" % key, dependencies=gate,
             name="display_" + key)
    def display():
        """The latest yield of the job this tab should be showing.

        "Should be showing" is jobqueue.display_for's reading and not a
        new one: the newest job for this tab that has actually *begun*. So
        a tab switches to a new run the moment it starts rather than when
        it was queued, and goes on showing the last finished run while
        three more sit waiting behind it.
        """
        revision, result = jobqueue.display_for(key)
        return {"revision": revision,
                "result": _display_json(schema, result)}

    @api.post("/tabs/%s/generate" % key, dependencies=gate,
              name="generate_" + key)
    def generate(body: dict = Body(default={})):
        """Queue one click. Returns in microseconds, like the Gradio one.

        The click does not generate — it writes the click down and hands
        it to jobqueue, which runs one job at a time per lane on a worker
        thread. Everything about that is unchanged; this is the same
        `_enqueue` shape ui.py has, minus the components.
        """
        raw = body.get("values")
        if not isinstance(raw, dict):
            raise HTTPException(400, "Send a `values` object.")
        try:
            resolved = _resolve_uploads(schema, raw)
            args, values = schema.submit(resolved)
        except tabschema.Invalid as exc:
            # str(exc) is "steps: must be at most 60" — the field name is
            # half the message, and a form that cannot say which control
            # it is complaining about is a form nobody can fix.
            raise HTTPException(422, str(exc))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))

        view = jobqueue.submit(
            lane=schema.lane, tab=key, tab_label=schema.title(),
            title=_job_title(schema, values),
            fn=_recording(schema, values), args=args,
            result_keys=schema.result_keys,
        )
        ahead = max(0, view.place - 1)
        return {
            "jobId": view.id,
            "place": view.place,
            "message": ("🕑 Queued — it starts as soon as the GPU is free."
                        if ahead == 0 else
                        "🕑 Queued — %d job(s) ahead of it." % ahead),
            "queue": _queue_json(),
        }


def _preset_settings(tab, name):
    """One preset's settings blob by name, or None if it is not on offer."""
    if not tab or not name:
        return None
    for row in presets.catalogue().for_tab(tab):
        if row.name == name:
            return row.settings
    return None


def _job_title(schema, values) -> str:
    """A one-line name for a queued job — its prompt, where it has one.

    Truncated hard: the queue is a list to scan, not a place to read a
    prompt back. Tabs whose work has no prompt at all (Face Swap, the JSON
    batch) get a dash, which is honest — what identifies those jobs is
    their tab and their place in the line.
    """
    if not schema.prompt_field:
        return "—"
    text = " ".join(str(values.get(schema.prompt_field) or "").split())
    if not text:
        return "—"
    return text[:59] + "…" if len(text) > 60 else text


def _recording(schema, values):
    """The handler, wrapped so whatever it writes is filed under a recipe.

    The wrapper exists to move one line onto the *worker* thread. The
    values were read on the request, but the recipe has to be announced
    from the thread that will do the generating — recipes keys it by
    thread, so the output hook deep inside client.run can find it without
    every executor having to pass it down (see recipes.py).

    A generator, because the handlers are: `yield from` keeps the tab's
    own progress reporting exactly as it was, and the `finally` runs on a
    cancelled job too, since closing a generator raises GeneratorExit at
    its current yield.
    """
    fields = schema.recipe_fields(values)
    key = str(schema.key)
    tab_id = schema.tab_id
    label = schema.title()
    handler = schema.handler

    def run(*args):
        recipes.begin(key, label, tab_id, fields)
        try:
            yield from handler(*args)
        finally:
            recipes.end()

    return run


# ─────────────────────────────────────────────────────────── stream

def _sse(event: str, payload) -> str:
    return "event: %s\ndata: %s\n\n" % (
        event, json.dumps(payload, ensure_ascii=False, default=str))


async def _events(request: Request):
    """The generator behind /stream. One connection, three event types.

    Sends everything once on connect — a page that has just loaded needs
    the current queue, not the next change to it — and then only what has
    moved. Each `seen` entry is a revision counter jobqueue already keeps,
    so "has this changed?" costs one integer comparison rather than a
    diff.

    Async rather than sync, and that is not a style choice: Starlette runs
    a sync streaming generator on a threadpool worker, and a `time.sleep`
    in one holds that worker for as long as the connection lives. Two open
    browser tabs and the third request waits.
    """
    # Seeded from the same snapshot that is about to be sent, so the
    # first pass of the loop does not send it a second time.
    seen = {"queue": jobqueue.revision()}
    last_beat = 0.0
    yield _sse("queue", _queue_json())

    while True:
        if await request.is_disconnected():
            return
        now = time.time()

        revision = jobqueue.revision()
        if seen.get("queue") != revision:
            seen["queue"] = revision
            yield _sse("queue", _queue_json())

        for schema in tabschema.entitled():
            key = str(schema.key)
            stamp, result = jobqueue.display_for(key)
            if result is None or seen.get(("tab", key)) == stamp:
                continue
            seen[("tab", key)] = stamp
            yield _sse("display", {"tab": key, "revision": stamp,
                                   "result": _display_json(schema, result)})

        # A preset saved by a background job — the save happens wherever
        # the job eventually runs, so nothing else is in a position to
        # notice the dropdown has gone stale. See jobqueue.note_preset_saved.
        for tab in presets.TABS:
            stamp = jobqueue.preset_revision(tab)
            if not stamp or seen.get(("preset", tab)) == stamp:
                continue
            seen[("preset", tab)] = stamp
            yield _sse("presets", {"tab": tab, "revision": stamp})

        if now - last_beat >= HEARTBEAT_SECONDS:
            last_beat = now
            # A comment, not an event: EventSource ignores it, and it is
            # the cheapest thing that keeps a tunnel from deciding a quiet
            # connection is a dead one.
            yield ": keep-alive\n\n"

        await anyio.sleep(POLL_SECONDS)


# ─────────────────────────────────────────────────────────────── spa

def _mount_spa(app: FastAPI) -> None:
    """Serve the React bundle, if this build has one.

    `/` and `/assets/*` are open, and have to be: the shell is what reads
    the token out of the URL fragment, so requiring the token to fetch the
    shell would be a lock whose key is inside the room. It is also
    useless without one — every route it calls is gated — so what an
    unauthenticated visitor gets is a page that immediately says so.

    Absent in this section: `npm run dev` serves the SPA and proxies /api
    here. Section 4 adds webui_bundle.py and this becomes the whole
    front end.
    """
    try:
        import webui
    except ImportError:
        log.info("No webui bundle — serving the API only "
                 "(run `npm run dev` in webui/ for the front end)")
        return
    webui.mount(app)
