"""What the licence server knows, passed through to the browser.

Plans and their entitlements, the showcase page, each tab's presets and
the community prompt library. Every one of these is a cached read in
`ember.licensing` or `ember.web.showcase`; the routes here only unpack a
dataclass into JSON and say who is allowed to ask.
"""


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
