"""The HTTP API the React UI talks to. A thin adapter, and nothing else.

Everything real happens elsewhere. ember.generation.handlers runs the
generators, ember.generation.queue runs them one at a time per lane,
ember.web.tabschema says what a form is and how its values become a
positional call, gallery_index.py lists and thumbnails the outputs, and
ember.licensing plus showcase.py talk to the licence server. This module
is the layer that turns those into routes, and it is deliberately the
thinnest thing in the repository: the ~2,600 lines it wraps encode VAE
size snapping, model-swap VRAM release, seat heartbeats, HF mirror
pinning and crash recovery, and none of that is why the UI looked dated.

What is here is the assembly: `create_app()` builds the router, hangs the
session and catalogue routes on it, and hands it to each module in
`ember.web.routes` in turn. Each of those owns one area and says in its
own docstring what it owns — `common` holds what they share, including
auth and path containment.

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

  1. every per-tab route is registered inside `routes.tabs._mount_tabs()`,
     through a loop that attaches `Depends(require_feature(key))`. There
     is no other way to add one;
  2. `tabschema.submit()` checks again — that is the funnel every
     generation passes through, so a route registered by accident still
     cannot run one;
  3. `/catalog` and `/schema/{tab}` describe only entitled tabs, so the
     navigation never learns an ungranted tab exists;
  4. `scripts/check_routes.py` imports the app with `features.resolve([])`
     and asserts every `/api/v1/tabs/` path 403s, naming
     `community_prompts` explicitly.
"""

import secrets

from fastapi import (
    APIRouter, Body, Depends, FastAPI, HTTPException, Request, Response,
)

from ember.licensing import catalog as assets  # the route below is called catalog()
from ember import features
from ember.licensing import seat as licensing
from ember.web import tabschema
from ember.comfy.server import GPU_COUNT
from ember.logs import log
from ember.web.routes import events
from ember.web.routes import licence
from ember.web.routes import media
from ember.web.routes import queue
from ember.web.routes import tabs
from ember.web.routes import uploads
from ember.web.routes.common import (
    ALLOW_ANON, COOKIE, PREFIX, TOKEN, _is_https, require_auth,
)


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

    # Registration order is the route table's order, and a path like
    # /gallery/delete only reaches its own handler because it is
    # registered above /gallery/{path_id:path}. Each call below is where
    # one area's routes go in; moving a call moves its whole block.

    queue.register(api)
    uploads.register(api)
    media.register(api)
    licence.register(api)
    tabs._mount_tabs(api)
    events.register(api)

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
