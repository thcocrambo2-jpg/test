#!/usr/bin/env python3
"""Assert the licence gate is enforced by the API, not just the render.

    python scripts/check_routes.py

Why this is a separate file
---------------------------
Hiding a tab the licence does not grant is a **render** gate, and a
render gate alone proves nothing: the front end is a React bundle the
browser holds, so a tab that is not drawn is still a route anyone can
call by hand.

Registering FastAPI routes unconditionally does not weaken that gate. It
**removes** it. And the failure is silent in the worst way — everything
looks right, every tab a customer paid for works, and so does one they
did not.

The partial backstop is downloads.py:706: weights for ungranted features
are never fetched, so most ungranted tabs would fail at "that model is
not downloaded yet". One would not. `community_prompts` declares
`needs=()` (features.py:160) because it has no weights of its own — the
Prompt Library reads a collection on the licence server. It would be
**fully functional** for a licence that does not include it, and it is
named explicitly below so that a future refactor cannot quietly drop
it from the check by making the loop cleverer.

How it works
------------
Build the app against `features.resolve([])` — a licence that grants
nothing at all — and drive it through Starlette's TestClient. Every
per-tab route must answer 403. Not 404: the routes are registered for
every tab and each one refuses, so that a licence upgrade takes effect on
the next resolve() without rebuilding the app, and so a customer who has
just paid gets a sentence rather than a dead link.

Then resolve one feature and prove exactly one tab's routes opened, which
is the half that catches a gate wired to the wrong key.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KREA2_BASE_DIR", str(ROOT / ".dryrun"))
# The model and LoRA catalogue the Krea tabs read (catalog.py): the seed
# document, so this never asks a licence server for it. Without a
# catalogue the Krea tabs would still be gated, but /catalog's per-feature
# model lists — which the checks below read — would be empty and prove
# nothing.
os.environ.setdefault("KREA2_CATALOG_FILE",
                      str(ROOT / "license-validator" / "data" / "assets.json"))
# No token is set here: the auth gate is open by default, and it is the
# feature gate that is under test. A request that 401s before it reaches
# the feature check would make every assertion below pass for the wrong
# reason, so KREA2_UI_REQUIRE_TOKEN must stay unset.

from ember import logs, settings  # noqa: E402
from ember.logs import log  # noqa: E402

# Importing a module configures nothing and creates nothing any more, so
# this entry point does it: the logger, the tree under KREA2_BASE_DIR,
# and the line saying where weights and images go.
logs.setup()
settings.ensure_dirs()
settings.log_startup()

# Named in the source, not derived. See the module docstring: this is
# the feature with no weights behind it, so it is the one the
# downloads.py backstop does not cover, so it is the one that must
# never be reachable by accident.
# The endpoint it is reachable through: the Prompt Library is bespoke
# and its whole surface is /prompts.
# Written out rather than derived, so that making the loop cleverer can
# never quietly stop covering it.
NO_WEIGHTS_BACKSTOP = {
    "community_prompts": "/api/v1/prompts",
}


def _stub_comfy() -> None:
    """Same weightless boot as the other scripts — see scripts/dryrun.py."""
    import types

    try:
        from ember.comfy import server as comfy
    except RuntimeError as exc:
        log.warning("No GPU detected (%s) - faking the comfy module", exc)
        comfy = types.ModuleType("ember.comfy.server")
        comfy.GPUS, comfy.GPU_COUNT = [], 1
        comfy.start_comfyui = lambda *a, **k: None
        comfy.wait_for_comfyui = lambda *a, **k: None
        comfy.verify_custom_node = lambda *a, **k: True
        comfy.node_registered = lambda *a, **k: True
        comfy.log_tail = lambda *a, **k: "<check_routes>"
        sys.modules["ember.comfy.server"] = comfy
        # A from-import of the parent package copies the binding at import
        # time, so the stub has to be visible as that attribute too.
        import ember.comfy
        ember.comfy.server = comfy
    comfy.ensure_alive = lambda *a, **k: (False, "not running")


def _routes(app):
    """(path, methods) for every route, including nested routers.

    FastAPI does not flatten an `include_router()` into `app.routes` — it
    keeps a wrapper object — so a check that walks the top level alone
    finds nothing and passes for the emptiest possible reason. Which is
    exactly what the first version of this file did.
    """
    seen = []
    visited = set()

    def walk(routes):
        for route in routes:
            if id(route) in visited:
                continue
            visited.add(id(route))
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path is not None and methods:
                seen.append((path, set(methods)))
            # `original_router` is what this FastAPI version hangs an
            # included router off; the other two cover the shapes older
            # and newer releases use. Tried in turn rather than pinned,
            # because a check that silently finds nothing is worse than no
            # check, and "no per-tab routes at all" is asserted separately
            # for exactly that reason.
            for attr in ("routes", "router", "original_router", "app"):
                nested = getattr(route, attr, None)
                if nested is None or nested is route:
                    continue
                nested = getattr(nested, "routes", nested)
                if isinstance(nested, (list, tuple)):
                    walk(nested)

    walk(app.routes)
    return seen


def _tab_paths(app) -> list:
    """Every registered path that belongs to one tab.

    Matched on the prefix rather than on a list of names, so a route added
    later is covered without this file being edited — which is the only
    way a check like this survives.
    """
    prefixes = ("/api/v1/tabs/", "/api/v1/schema/")
    return sorted({path for path, _methods in _routes(app)
                   if path.startswith(prefixes)})


def _probe(client, path: str, methods):
    """Call `path` with whatever verb it accepts; return the status."""
    if "POST" in methods:
        return client.post(path, json={"values": {}}).status_code
    return client.get(path).status_code


def main() -> None:
    from fastapi.testclient import TestClient

    from ember import features

    _stub_comfy()

    # ── every feature key must own a route prefix ───────────────────
    features.resolve([f.key for f in features.FEATURES])
    from ember.web import tabschema
    from ember.web import api

    failures = []

    schema_keys = set(tabschema.BY_KEY) | {row["key"] for row in
                                           tabschema.BESPOKE}
    for feature in features.FEATURES:
        if str(feature.key) not in schema_keys:
            failures.append(
                "features.Key.%s has no schema and no bespoke entry — it "
                "would be gated by nothing at all" % feature.key
            )
    for key in NO_WEIGHTS_BACKSTOP:
        if key not in schema_keys:
            failures.append(
                "%s is not in tabschema — it has needs=() so the downloads "
                "backstop does not cover it either" % key
            )

    granted = api.create_app()
    granted_paths = _tab_paths(granted)
    if not granted_paths:
        failures.append("no per-tab routes were registered at all")
    for key, path in NO_WEIGHTS_BACKSTOP.items():
        if path.startswith("/api/v1/tabs/") and key not in str(granted_paths):
            failures.append(
                "%s has no per-tab route, so this file is not actually "
                "checking it" % key
            )
        if not any(row[0] == path for row in _routes(granted)):
            failures.append(
                "%s: this file checks %s, and no such route exists — the "
                "check would pass for the wrong reason" % (key, path)
            )

    # ── a licence that grants nothing ───────────────────────────────
    features.resolve([])
    empty = api.create_app()
    client = TestClient(empty, raise_server_exceptions=False)

    checked = 0
    for path, methods in _routes(empty):
        if not path.startswith(("/api/v1/tabs/", "/api/v1/schema/")):
            continue
        if "{" in path:
            continue                     # nothing to call without a value
        status = _probe(client, path, methods)
        checked += 1
        if status != 403:
            failures.append(
                "%s %s answered %d for a licence that grants nothing — "
                "it must be 403" % (sorted(methods), path, status)
            )

    # The one with no weights behind it, called by name.
    for key, path in NO_WEIGHTS_BACKSTOP.items():
        methods = next((m for p, m in _routes(empty) if p == path), {"GET"})
        status = _probe(client, path, methods)
        if status != 403:
            failures.append(
                "%s answered %d. %s has needs=() — there is no weights "
                "backstop behind it, so a licence without it would get a "
                "working tab." % (path, status, key)
            )

    # /catalog must not describe an ungranted tab either — layer three.
    body = client.get("/api/v1/catalog").json()
    if body.get("tabs"):
        failures.append(
            "/catalog described %d tab(s) for a licence that grants "
            "nothing: %s" % (len(body["tabs"]),
                             [t["key"] for t in body["tabs"]])
        )
    # The model lists are keyed by feature key, so an ungranted feature's
    # list is as much a description of that tab as its schema is.
    if body.get("models"):
        failures.append("/catalog listed models for %s under a licence that "
                        "grants nothing" % sorted(body["models"]))
    if client.get("/api/v1/schemas").json():
        failures.append("/schemas listed tabs for an empty licence")
    session = client.get("/api/v1/session").json()
    if session.get("features"):
        failures.append("/session listed features for an empty licence: %s"
                        % session["features"])

    # ── and one that grants exactly one tab ─────────────────────────
    features.resolve(["krea_t2i"])
    one = api.create_app()
    client = TestClient(one, raise_server_exceptions=False)
    opened = client.get("/api/v1/schema/krea_t2i").status_code
    closed = client.get("/api/v1/schema/krea_v2_t2i").status_code
    if opened != 200:
        failures.append(
            "a licence granting krea_t2i still could not read its schema "
            "(%d) — the gate is wired to the wrong key" % opened)
    if closed != 403:
        failures.append(
            "a licence granting only krea_t2i could read krea_v2_t2i's "
            "schema (%d)" % closed)
    body = client.get("/api/v1/catalog").json()
    tabs = [t["key"] for t in body["tabs"]]
    if tabs != ["krea_t2i"]:
        failures.append("/catalog described %s for a krea_t2i-only licence"
                        % tabs)
    if sorted(body.get("models") or {}) != ["krea_t2i"]:
        failures.append("/catalog listed models for %s under a krea_t2i-only "
                        "licence" % sorted(body.get("models") or {}))

    if failures:
        print("%d licence-gate failure(s):" % len(failures))
        for line in failures:
            print("  - %s" % line)
        sys.exit(1)
    print("routes OK - %d per-tab route(s) refuse a licence that grants "
          "nothing, %s named explicitly"
          % (checked, " and ".join(NO_WEIGHTS_BACKSTOP)))


if __name__ == "__main__":
    main()
