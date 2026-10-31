"""The prompt library — example pictures and the settings behind them.

Each entry is a picture made in this app and the settings token that made
it. The tab shows the picture; Use loads the token into its tab, exactly
as pasting it into the Settings token panel would. There is no prompt text
to read or copy on its own: a prompt only makes that picture together with
every other setting, and the token carries all of them.

The library is curated by the operator and lives in R2. This module only
reads it, through the licence server: POST /v1/prompt-library for a page
— the entries switched on, a signed link to each picture, and whether
each card is usable on this licence — and POST /v1/prompt-library/token
for one card's token, fetched only when its Use or Copy is pressed. A
page carries no tokens, which is what keeps switching category quick. The pictures themselves go from R2 straight to the browser;
nothing here downloads one.

Same shape as plans.py: a frozen dataclass carrying either rows or a short
human `error`, never an exception, cached with a TTL.

Stdlib-only, like the rest of this package: it keeps the Nuitka build
unchanged.
"""

import threading
import time
from dataclasses import dataclass, field

from ember.licensing import server
from ember.logs import log
from ember.settings import LICENSE_API_URL, LICENSE_KEY

# How long a fetched page is served without asking again. The library
# changes when the operator switches an entry on, which is not often, and
# the tab has a Refresh button for the minute after. Well inside the
# five hours every picture link has left (see the server's
# src/promptLibrary.js).
TTL_SECONDS = 300

FETCH_TIMEOUT = 12


@dataclass(frozen=True)
class Prompt:
    """One entry, as the tab renders it.

    `usable` is False for an entry whose tab this licence does not have:
    the picture still shows, it just cannot be loaded.
    """

    id: str
    tab: str
    title: str | None
    category: str | None
    tags: tuple[str, ...]
    width: int | None
    height: int | None
    thumb: str
    full: str
    usable: bool


@dataclass(frozen=True)
class Library:
    """One page of the library, and how the fetch went.

    `error` is None on success and a short human sentence otherwise, in
    which case `prompts` is empty — the same contract as
    plans.Catalogue, so the tab can render without checking which case
    it got. `tabs` counts the entries per tab across the whole library.
    `categories` and `tags` are the filters' rows, {key, label, count},
    each counted under the other filters chosen — see the server's
    POST /v1/prompt-library.
    """

    prompts: tuple[Prompt, ...] = ()
    total: int = 0
    skip: int = 0
    limit: int = 24
    tabs: dict[str, int] = field(default_factory=dict)
    categories: tuple[dict, ...] = ()
    tags: tuple[dict, ...] = ()
    error: str | None = None


_cache: dict[tuple, tuple[float, Library]] = {}
_cache_lock = threading.Lock()


def _size(value) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _prompt(raw: dict) -> Prompt | None:
    """One entry from the wire, or None if it is too broken to show.

    An id, a tab and both picture links are the whole bar. A card that is
    not usable still renders — that is the not-licensed case, not damage.
    """
    if not isinstance(raw, dict):
        return None
    entry_id = str(raw.get("id") or "").strip()
    tab = str(raw.get("tab") or "").strip()
    thumb = str(raw.get("thumb_url") or "").strip()
    full = str(raw.get("full_url") or "").strip()
    if not (entry_id and tab and thumb and full):
        return None
    title = str(raw.get("title") or "").strip() or None
    category = str(raw.get("category") or "").strip() or None
    tags = raw.get("tags")
    return Prompt(
        id=entry_id,
        tab=tab,
        title=title,
        category=category,
        tags=tuple(str(tag) for tag in tags if isinstance(tag, str))
        if isinstance(tags, list) else (),
        width=_size(raw.get("width")),
        height=_size(raw.get("height")),
        thumb=thumb,
        full=full,
        usable=raw.get("usable") is True,
    )


def _rows(raw) -> tuple[dict, ...]:
    """A filter's rows from the wire: {key, label, count}, well-formed only."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        {"key": row["key"], "label": row["label"],
         "count": row["count"] if isinstance(row.get("count"), int) else 0}
        for row in raw
        if isinstance(row, dict) and isinstance(row.get("key"), str)
        and isinstance(row.get("label"), str)
    )


def _fetch(tab, category, tags, skip, limit) -> Library:
    """One trip to /v1/prompt-library, every failure turned into `error`."""
    if not LICENSE_API_URL or not LICENSE_KEY:
        return Library(
            skip=skip, limit=limit,
            error="This build has no licence server to read the prompt "
                  "library from.",
        )

    payload = {
        "license_key": LICENSE_KEY,
        "instance_id": server.instance_id(),
        "skip": skip,
        "limit": limit,
    }
    if tab:
        payload["tab"] = tab
    if category:
        payload["category"] = category
    if tags:
        payload["tags"] = list(tags)

    try:
        status, body = server.post("/v1/prompt-library", payload, FETCH_TIMEOUT)
    except Exception as exc:                # transport: DNS, refused, TLS
        log.warning("Could not fetch the prompt library (%s)", exc)
        return Library(skip=skip, limit=limit,
                       error=f"Could not reach the server ({exc}).")

    if status != 200 or not body.get("ok"):
        log.warning("Prompt library request answered HTTP %s", status)
        return Library(
            skip=skip, limit=limit,
            error=body.get("message")
            or f"The server answered HTTP {status} for the prompt library.",
        )

    rows = tuple(
        item for item in (_prompt(raw) for raw in body.get("prompts") or [])
        if item is not None
    )
    total = body.get("total")
    tabs = body.get("tabs")
    return Library(
        prompts=rows,
        total=total if isinstance(total, int) else len(rows),
        skip=skip, limit=limit,
        tabs={str(key): count for key, count in tabs.items()
              if isinstance(count, int)} if isinstance(tabs, dict) else {},
        categories=_rows(body.get("categories")),
        tags=_rows(body.get("tags")),
    )


def library(tab: str | None = None, category: str | None = None,
            tags: tuple[str, ...] = (), skip: int = 0, limit: int = 24,
            force: bool = False) -> Library:
    """One page of the library, from cache when it is fresh enough.

    `force=True` is the tab's Refresh button: it skips the TTL so an entry
    switched on a minute ago can be seen without waiting it out.

    Keyed on the whole query, so changing the filter is a fresh read rather
    than a stale page from a different one.

    Two answers are deliberately **not** cached, and for the same
    reason — both are states you are actively waiting to come out of:

      a failed fetch    the next view retries, so a server that was
                        briefly down does not leave the tab broken.
      an empty page     caching "nothing here yet" would make an entry
                        just switched on look missing for five minutes.
    """
    tags = tuple(sorted(set(tags)))
    key = (tab, category, tags, skip, limit)

    with _cache_lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit[0] < TTL_SECONDS:
            return hit[1]

    # Fetched outside the lock: this is a network call, and holding the
    # lock across it would make a slow server serialise every other
    # filter change behind it.
    result = _fetch(tab, category, tags, skip, limit)

    if result.error is None and result.prompts:
        with _cache_lock:
            _cache[key] = (time.time(), result)
            # The tab pages and filters, so the key space is small but not
            # fixed. Trimmed rather than bounded properly — this is a
            # cache, and dropping all of it costs one refetch.
            if len(_cache) > 64:
                _cache.clear()
                _cache[key] = (time.time(), result)
    return result


def token(entry_id: str) -> tuple[str | None, str | None]:
    """One card's settings token, or (None, a short human reason).

    Not cached here: it is asked for once per press of Use or Copy, and
    the server keeps every token it has read.
    """
    if not LICENSE_API_URL or not LICENSE_KEY:
        return None, "This build has no licence server to read the library from."
    payload = {"license_key": LICENSE_KEY, "instance_id": server.instance_id(),
               "id": entry_id}
    try:
        status, body = server.post("/v1/prompt-library/token", payload,
                                   FETCH_TIMEOUT)
    except Exception as exc:                # transport: DNS, refused, TLS
        log.warning("Could not fetch a prompt library token (%s)", exc)
        return None, f"Could not reach the server ({exc})."
    value = body.get("token")
    if status == 200 and body.get("ok") and isinstance(value, str) and value:
        return value, None
    return None, (body.get("message")
                  or f"The server answered HTTP {status} for that card.")
