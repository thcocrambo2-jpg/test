"""Settings presets — the named starting points behind each tab's dropdown.

A preset is one tab's controls, saved under a name and served back to every
pod: the model, the steps, the resolution, the sampler, the LoRA stack —
everything the tab holds **except the prompt**. Prompts are the other
module's job (prompts.py), and the split is deliberate: a prompt carries
its settings with it and is about one image, a preset moves every dial at
once and is about how this pod generates from now on.

Two halves, same as prompts.py, and the same two-sentence contract for
each:

**Read.** catalogue() fetches the enabled presets — all tabs in one
request — and hands back a frozen dataclass carrying either rows or a
short human `error`, never an exception, cached with a TTL. Every tab's
dropdown is built from it, so a server that cannot be reached costs the
dropdown and nothing else: the controls keep the values compiled into
config.py, which is what they were built with anyway.

**Write.** save() is called from the generate handlers when an admin has
ticked "save these settings as a preset". Unlike prompts.record() this one
is **synchronous and reports what happened**, because the two writes are
not the same kind of thing at all:

    record()   silent capture the customer was never told about. Nothing
               is waiting on it, so it goes on a queue and every failure
               dies in a debug log.
    save()     a box someone deliberately ticked. "Did my preset save?"
               deserves an answer, and the person asking is us.

It is still bounded and still cannot raise: one POST with a short timeout
inside a try/except, returning (ok, message) for the caller to log or show.
A generate run is never failed by it.

Only an admin licence may write, which the server enforces against the
licence document rather than trusting the flag — see POST /v1/presets. The
check here is the same one prompts.record makes: not a security boundary,
just a reason not to send a request that is certain to be refused.

Stdlib-only, like licensing.py, plans.py and prompts.py. The four are
siblings talking to the same API, none of them needs more than urllib for
it, and it keeps the Nuitka build unchanged.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from config import LICENSE_API_URL, LICENSE_KEY, log

# Tabs a preset can be written for. The same two the prompt library
# replays into, and necessarily so: applying a preset means writing values
# into a specific set of form controls, which is exactly what replaying a
# prompt does. A tab joins this list in the commit that teaches ui.py to
# load settings back into it.
#
# Written for, not applied to: which panels *offer* a tab's presets is
# ui.py's business, and it offers each of these two in its generation tab
# and again in that tab's Edit tab, where the same dials exist under the
# same names. Nothing here changes for that — one row, read by more than
# one dropdown.
TAB_KREA2 = "krea_t2i"
TAB_KREA2_V2 = "krea_v2_t2i"
TABS = (TAB_KREA2, TAB_KREA2_V2)

# How long a fetched set is served without asking again. The same 300s as
# plans.py and prompts.py: presets change when we save one, which is not
# often, and both the tabs' 🔄 buttons and a just-saved preset bypass it.
TTL_SECONDS = 300

# How long a *failed* read is remembered. prompts.py caches no failure at
# all, on the grounds that a filter you are watching should retry — but
# this list is read on the startup path and again on every page load, and
# an uncached failure means each of those pays the full FETCH_TIMEOUT
# against a server that is down. Short enough that recovery is noticed
# within half a minute, long enough that one bad minute costs two requests
# rather than two per visitor.
ERROR_TTL_SECONDS = 30

# Short on purpose. The catalogue is read while ui.py is building its
# Blocks, i.e. on the pod's startup path — a licence server having a bad
# minute may cost the dropdowns, never the app coming up.
FETCH_TIMEOUT = 8
SAVE_TIMEOUT = 10

# Bounds mirroring the server's, applied here so a mistyped name is a
# message rather than a 400 nobody reads.
MAX_NAME_CHARS = 60


@dataclass(frozen=True)
class Preset:
    """One preset, as a tab's dropdown offers it."""

    id: str
    tab: str
    name: str
    description: str | None
    settings: dict
    is_default: bool


@dataclass(frozen=True)
class Catalogue:
    """Every enabled preset, and how the fetch went.

    `error` is None on success and a short human sentence otherwise, in
    which case `presets` is empty — the same contract as plans.Catalogue
    and prompts.Library, so a caller can render without checking which
    case it got.
    """

    presets: tuple[Preset, ...] = ()
    error: str | None = None

    def for_tab(self, tab: str) -> tuple[Preset, ...]:
        """This tab's presets, in the order the server sorted them."""
        return tuple(row for row in self.presets if row.tab == tab)


# ── Read ───────────────────────────────────────────────────────────────

_cache: tuple[float, Catalogue] | None = None
_cache_lock = threading.Lock()


def _preset(raw: dict) -> Preset | None:
    """One preset from the wire, or None if it is too broken to offer.

    An id, a known tab, a name and a settings object are the whole bar. A
    preset with odd values inside `settings` still applies fine — ui.py
    guards every value against what this pod offers — but one with no name
    has nothing to put in a dropdown, and one for a tab this build does
    not know has nowhere to be applied.
    """
    if not isinstance(raw, dict):
        return None
    preset_id = str(raw.get("id") or "").strip()
    tab = str(raw.get("tab") or "").strip()
    name = str(raw.get("name") or "").strip()
    settings = raw.get("settings")
    if not preset_id or tab not in TABS or not name:
        return None
    if not isinstance(settings, dict):
        return None
    description = str(raw.get("description") or "").strip() or None
    return Preset(
        id=preset_id,
        tab=tab,
        name=name,
        description=description,
        settings=settings,
        is_default=raw.get("is_default") is True,
    )


def _fetch() -> Catalogue:
    """One trip to /v1/presets, every failure turned into `error`."""
    if not LICENSE_API_URL:
        return Catalogue(
            error="This build has no node tag, so it cannot reach the "
                  "server to read the presets.",
        )
    try:
        status, body = _get("/v1/presets", timeout=FETCH_TIMEOUT)
    except Exception as exc:                # transport: DNS, refused, TLS
        log.warning("Could not fetch the presets (%s)", exc)
        return Catalogue(error=f"Could not reach the server ({exc}).")

    if status != 200 or not body.get("ok"):
        log.warning("Preset request answered HTTP %s", status)
        return Catalogue(
            error=f"The server answered HTTP {status} for the presets.",
        )

    rows = tuple(
        item for item in (_preset(raw) for raw in body.get("presets") or [])
        if item is not None
    )
    return Catalogue(presets=rows)


def catalogue(force: bool = False) -> Catalogue:
    """Every enabled preset, from cache when it is fresh enough.

    `force=True` is a tab's 🔄 button and the refresh that follows saving
    one: it skips the TTL so a preset written a moment ago is in the
    dropdown without waiting it out.

    Both outcomes are cached, each with its own TTL, and both for the same
    reason — this is read while ui.py builds its Blocks and again on every
    page load, so an answer that is not remembered is a request per
    visitor. An empty list is a perfectly normal steady state for a
    deployment that has seeded no presets, and a failure is remembered
    only briefly (ERROR_TTL_SECONDS) so a server that comes back is
    noticed without every page load paying FETCH_TIMEOUT while it is down.
    """
    global _cache

    with _cache_lock:
        hit = _cache
        if hit and not force:
            ttl = TTL_SECONDS if hit[1].error is None else ERROR_TTL_SECONDS
            if time.time() - hit[0] < ttl:
                return hit[1]

    # Fetched outside the lock: this is a network call, and holding the
    # lock across it would make a slow server serialise every page load
    # behind one request.
    result = _fetch()
    with _cache_lock:
        _cache = (time.time(), result)
    return result


def invalidate() -> None:
    """Drop the cached set, so the next read hits the server."""
    global _cache
    with _cache_lock:
        _cache = None


# ── Write ──────────────────────────────────────────────────────────────

def save(tab: str, name: str, settings: dict) -> tuple[bool, str]:
    """Save these settings under this name. Returns (ok, message).

    Admin only, and **always a new preset**. Ticking the box means "keep
    these settings", and what is on screen is almost always a preset that
    was loaded and then changed — so a name that is already taken becomes
    "Portrait (2)" on the server rather than overwriting the row it came
    from. The message names whichever it got, because that is not always
    the name that was typed. Editing one in place is `npm run presets`.

    An empty name is not a refusal either. The name box is optional in the
    same way the publish title is: leave it blank and the preset is
    stamped with the time instead, which is a worse label than a real one
    but a much better outcome than a ticked box that silently saved
    nothing. Rename it later with `npm run presets -- --rename`.

    Never raises: it is called from inside a generate handler, which must
    not be able to fail because a preset did not save. Every unhappy path
    comes back as (False, "a sentence"), which the caller logs.
    """
    try:
        name = (name or "").strip()[:MAX_NAME_CHARS] or _stamped_name()
        if tab not in TABS:
            return False, f"{tab} is not a tab presets can be saved for."
        if not LICENSE_API_URL or not LICENSE_KEY:
            return False, "This build cannot reach the server to save presets."
        if not _is_admin():
            # Not a security boundary — the server checks the licence
            # document — just a request there is no point sending.
            return False, "Only an admin license can save presets."

        status, body = _post("/v1/presets", {
            "license_key": LICENSE_KEY,
            "instance_id": _instance_id(),
            "tab": tab,
            "name": name,
            "settings": settings,
        }, SAVE_TIMEOUT)

        if status == 200 and body.get("ok"):
            # Whatever every pod reads next has just changed, including
            # this one's own dropdown — so the cached set is stale by
            # definition and the next read must go out.
            invalidate()
            # The server's name, not the one that was sent: it steps past
            # a name already in use, and reporting what was typed would
            # name a preset that is not the one just written.
            stored = str(body.get("name") or "").strip() or name
            renamed = "" if stored == name else " (that name was taken)"
            return True, f"Preset “{stored}” saved.{renamed}"
        message = body.get("message") or f"the server answered HTTP {status}"
        return False, f"Could not save the preset — {message}"
    except Exception as exc:                # never fails a generation
        log.warning("Could not save a preset (%s)", exc)
        return False, f"Could not save the preset ({exc})."


def _stamped_name() -> str:
    """The fallback name for a preset saved with the box left empty.

    A timestamp because it is the one label that is always true, always
    unique enough to be found again, and sorts sensibly next to its
    siblings. The pod's clock, so it reads as the local time of whoever
    was at the keyboard — this is a label, and nothing keys on it.
    """
    return time.strftime("Preset %Y-%m-%d %H:%M")


def _is_admin() -> bool:
    """Whether this pod is on one of our own licences.

    Imported late for the same reason as _instance_id, and False for any
    answer that is not a clear yes — the fallback is what every customer
    pod does, which is not to write at all.
    """
    try:
        import licensing
        return licensing.is_admin()
    except Exception:
        return False


def _instance_id() -> str:
    """This pod's id, from licensing — imported late to stay acyclic.

    Same as prompts._instance_id: "unknown" is a real answer for a UI
    launched without a seat, and the server only needs it non-empty.
    """
    try:
        import licensing
        return licensing.instance_id() or "unknown"
    except Exception:
        return "unknown"


# ── HTTP ───────────────────────────────────────────────────────────────

def _post(path: str, payload: dict, timeout: int) -> tuple[int | None, dict]:
    """POST JSON. Returns (status, body); raises only on transport error."""
    request = urllib.request.Request(
        f"{LICENSE_API_URL}{path}",
        data=json.dumps(payload, default=str).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except Exception:
            return err.code, {}


def _get(path: str, timeout: int) -> tuple[int | None, dict]:
    """GET JSON. Returns (status, body); raises only on transport error."""
    request = urllib.request.Request(
        f"{LICENSE_API_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except Exception:
            return err.code, {}
