"""What the licence server knows, passed through to the browser.

Plans and their entitlements, the showcase page, each tab's presets and
the prompt library. Every one of these is a cached read in
`ember.licensing` or `ember.web.showcase`; the routes here only unpack a
dataclass into JSON and say who is allowed to ask.
"""

import dataclasses

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ember import features
from ember.generation import queue as jobqueue
from ember.licensing import plans
from ember.licensing import presets
from ember.licensing import prompts
from ember.licensing import tokens
from ember.logs import log
from ember.web import showcase
from ember.web import tabschema
from ember.web.routes.common import require_auth, require_feature, _queue_json
from ember.web.routes.tabs import preset_offered, queue_click

# The tabs Random generate may queue for. Picture tabs that need no input
# image only: a token carries no pictures, so an edit or video tab's job
# would have nothing to start from.
RANDOM_TABS = ("krea_v2_t2i",)
RANDOM_MAX = 50


def register(api: APIRouter) -> None:
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

        Only the presets whose model this pod offers, and the default is
        the flagged one for the tab's default model — see
        presets.Catalogue.offered and default_for.
        """
        if tab not in presets.TABS:
            raise HTTPException(404, "No presets for that tab.")
        cat = presets.catalogue(force=force)
        rows = cat.offered(tab)
        default = cat.default_for(tab)
        return {
            "presets": [{"id": row.id, "name": row.name,
                         "description": row.description,
                         "isDefault": row is default} for row in rows],
            "default": default.name if default else None,
            "error": cat.error,
            "revision": jobqueue.preset_revision(tab),
        }

    @api.get("/prompts", dependencies=[Depends(require_auth),
                                       Depends(require_feature(
                                           features.Key.COMMUNITY_PROMPTS))])
    def prompt_library(tab: str = Query(default=""),
                       category: str = Query(default=""),
                       tags: str = Query(default=""),
                       skip: int = Query(default=0, ge=0),
                       limit: int = Query(default=24, ge=1, le=48),
                       force: bool = Query(default=False),
                       shuffle: int | None = Query(default=None, ge=0)):
        # `tags` is comma-separated: every one of them must be on a card.
        # `shuffle` is the page's order seed (prompts.library).
        page = prompts.library(tab=tab or None, category=category or None,
                               tags=tuple(t for t in tags.split(",") if t),
                               skip=skip, limit=limit, force=force,
                               shuffle=shuffle)
        return {
            "prompts": [dataclasses.asdict(row) for row in page.prompts],
            "total": page.total, "skip": page.skip, "limit": page.limit,
            "tabs": page.tabs, "categories": list(page.categories),
            "tags": list(page.tags), "error": page.error,
        }

    @api.get("/prompts/{entry_id}/token",
             dependencies=[Depends(require_auth),
                           Depends(require_feature(
                               features.Key.COMMUNITY_PROMPTS))])
    def prompt_token(entry_id: str):
        """One card's settings token, for its Use or Copy button."""
        value, problem = prompts.token(entry_id)
        if value is None:
            raise HTTPException(502, problem)
        return {"token": value}

    @api.get("/prompts/random", dependencies=[Depends(require_auth),
                                              Depends(require_feature(
                                                  features.Key.COMMUNITY_PROMPTS))])
    def random_tabs():
        """The tabs Random generate can queue for on this licence."""
        rows = []
        for key in RANDOM_TABS:
            schema = tabschema.BY_KEY.get(key)
            if schema is None or not features.enabled(schema.key):
                continue
            batch = schema.field("batch_count")
            rows.append({"key": key, "label": schema.title(), "icon": schema.icon,
                         "batchMax": int(batch.hi) if batch else 1})
        return {"tabs": rows, "max": RANDOM_MAX}

    @api.post("/prompts/random", dependencies=[Depends(require_auth),
                                               Depends(require_feature(
                                                   features.Key.COMMUNITY_PROMPTS))])
    def random_generate(body: dict = Body(default={})):
        """🎲 Random generate: `count` library entries, each queued as a job.

        The licence server picks them at random from every switched-on
        entry for `tab` that the chosen `category` and `tags` match — the
        whole library, not the page on screen. Each one's token is loaded
        as Use would load it (token_values, so a LoRA this pod lacks is
        left out with a note), with Batch count set to `batch` and Random
        seed on whatever the token said, and queued down the same path as
        a Generate click (tabs.queue_click).

        All on the server, in one request, so closing the page halfway
        cannot leave half of them queued.
        """
        key = str(body.get("tab") or "")
        if key not in RANDOM_TABS or key not in tabschema.BY_KEY:
            raise HTTPException(400, "Random generate is not offered for that tab.")
        schema = tabschema.BY_KEY[key]
        if not features.enabled(schema.key):
            raise HTTPException(403, "%s is not part of this licence." % schema.title())
        batch_field = schema.field("batch_count")
        batch_max = int(batch_field.hi) if batch_field else 1
        count, batch = body.get("count"), body.get("batch", 2)
        if not isinstance(count, int) or not 1 <= count <= RANDOM_MAX:
            raise HTTPException(400, "Prompts must be between 1 and %d." % RANDOM_MAX)
        if not isinstance(batch, int) or not 1 <= batch <= batch_max:
            raise HTTPException(400, "Images per prompt must be between 1 and %d."
                                % batch_max)
        category = str(body.get("category") or "") or None
        tags = body.get("tags")
        tags = tuple(t for t in tags if isinstance(t, str) and t)             if isinstance(tags, list) else ()

        picked, available, problem = prompts.pick_random(key, count, category, tags)
        if problem:
            raise HTTPException(502, problem)
        if not picked:
            raise HTTPException(404, "Nothing in the library matches that yet.")

        queued, skipped, notes = [], 0, 0
        for entry in picked:
            try:
                _, data = tokens.read_token(entry.token)
                if data.get("tab") != key:
                    raise ValueError("token is for %s" % data.get("tab"))
                values, entry_notes = schema.token_values(data)
                raw = {**schema.defaults(), **values,
                       "batch_count": batch, "randomize": True}
                preset = str(data.get("preset") or "")
                if not preset_offered(schema.preset_tab, preset):
                    preset = ""
                view = queue_click(schema, raw, preset)
            except (ValueError, tabschema.Invalid) as exc:
                log.warning("Random generate skipped %s (%s)", entry.id, exc)
                skipped += 1
                continue
            except PermissionError as exc:
                raise HTTPException(403, str(exc))
            queued.append(view.id)
            notes += len(entry_notes)

        log.info("Random generate: %d %s job(s) × %d image(s) queued, "
                 "%d skipped, %d matched", len(queued), key, batch, skipped,
                 available)
        if not queued:
            raise HTTPException(422, "None of the picked entries could be loaded.")
        message = "🎲 Queued %d %s job%s × %d image%s." % (
            len(queued), schema.title(), "" if len(queued) == 1 else "s",
            batch, "" if batch == 1 else "s")
        if available < count:
            message += " Only %d match%s the filters." % (
                available, "es" if available == 1 else "")
        if skipped:
            message += " %d could not be loaded." % skipped
        if notes:
            message += " %d setting%s left out (missing LoRA or out of range)." % (
                notes, "" if notes == 1 else "s")
        return {"jobIds": queued, "skipped": skipped, "notes": notes,
                "available": available, "message": message,
                "queue": _queue_json()}
