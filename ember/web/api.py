"""The HTTP API the React UI talks to. A thin adapter, and nothing else.

Everything real happens elsewhere. ember.generation.handlers runs the
generators, ember.generation.queue runs them one at a time per lane,
tabschema.py says what a form is and how its values become a positional
call, gallery_index.py lists and thumbnails the outputs, and
ember.licensing plus showcase.py talk to the licence server. This module is the layer
that turns those into routes, and it is deliberately the thinnest thing
in the repository: the ~2,600 lines it wraps encode VAE size snapping,
model-swap VRAM release, seat heartbeats, HF mirror pinning and crash
recovery, and none of that is why the UI looked dated.

Two things here are not thin, because they were invisible before and
would be gone if nobody wrote them down.

The licence gate
----------------
Hiding a tab the licence does not grant is a **render** gate, and the
front end is a bundle the browser holds — so a tab that is not drawn is
still a route anyone can call by hand. Registering FastAPI routes
unconditionally therefore needs a gate of its own.

The partial backstop is `ember.weights.downloads` — weights for ungranted
features are never fetched, so most tabs would fail at "the model is not
downloaded". One tab has no weights at all: `community_prompts` declares
`needs=()` in `features.FEATURES`, so it would be fully working for a
licence that does not include it.

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
     and asserts every `/api/v1/tabs/` path 403s, naming
     `community_prompts` explicitly.

Path containment
----------------
`/media` and `/thumbs` are the only way a generated file reaches the
browser, and they contain themselves to OUTPUT_DIR through
`gallery_index.safe_path()` — the one implementation, shared with
`delete()`. A `path_id` is the OUTPUT_DIR-relative posix path, the same
key `recipes._key()` uses, and **no absolute path ever crosses the wire**.

Auth
----
The app is served over a public tunnel URL, so the link cannot be the
access control. A per-process `secrets.token_urlsafe(32)` is
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
import re
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

from ember.licensing import catalog as assets  # the route below is called catalog()
from ember import features
from ember.web import gallery_index
from ember.generation import queue as jobqueue
from ember.licensing import seat as licensing
from ember.licensing import plans
from ember.licensing import presets
from ember.licensing import prompts
from ember.generation import recipes
from ember.web import showcase
from ember.web import tabschema
from ember.comfy.server import GPU_COUNT
from ember.logs import log
from ember.settings import TEMP_DIR, UI_REQUIRE_TOKEN


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
            # Every model the catalogue offers this pod, counted once however
            # many tabs list it.
            "modelCount": len(assets.get().models),
            "gpuCount": GPU_COUNT,
            "isAdmin": licensing.is_admin(),
            # Layer three: the navigation is built from this, so a tab
            # this licence does not grant is not merely unreachable, it is
            # never mentioned.
            "features": [str(key) for key in features.enabled_keys()],
        }

    @api.get("/catalog", dependencies=[Depends(require_auth)])
    def catalog():
        """Every entitled tab's schema, plus each Krea feature's model list.

        The model lists drive the dependent-control behaviour: picking a
        model resets Steps and CFG to that model record's defaults and
        swaps its trigger words into the prompt.
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

    app.include_router(api)
    _mount_spa(app)
    return app


# ─────────────────────────────────────────────────────────────── spa

def _mount_spa(app: FastAPI) -> None:
    """Serve the React bundle, if this build has one.

    `/` and `/assets/*` are open, and have to be: the shell is what reads
    the token out of the URL fragment, so requiring the token to fetch the
    shell would be a lock whose key is inside the room. It is also
    useless without one — every route it calls is gated — so what an
    unauthenticated visitor gets is a page that immediately says so.

    This is the whole front end now. webui.mount() always registers its
    routes — when there is no bundle it serves a notice naming `make
    webui` rather than leaving `/` to 404, because "the page is blank"
    and "the route does not exist" look identical from a browser.
    """
    try:
        from ember.web import spa as webui
    except ImportError:
        log.info("No webui module — serving the API only "
                 "(run `npm run dev` in webui/ for the front end)")
        return
    webui.mount(app)
