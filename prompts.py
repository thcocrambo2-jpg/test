"""The prompt library — what gets captured, and what gets shown back.

Two halves that share a module because they share a collection, and
nothing else.

**Capture.** record() is called from the Krea 2 and Krea 2 V2 generate
handlers with the prompt and every setting behind it. It is the quietest
thing in this app and has to stay that way: the customer is not told
their prompts are saved, so no failure here may ever reach the UI, and
nothing here may make Generate slower or make it fail. Every call returns
in microseconds — the work happens on one background thread, and every
exception on it dies in a debug log.

Which generations get captured depends on whose licence the pod is on.
A customer's captures everything new, automatically. One of ours
(`is_admin` on the licence document) captures *nothing* unless the
operator ticks the publish checkbox, and what that produces is an
official prompt rather than a submission for review — our own test runs
should not fill the queue we are the ones working through. record()
owns that rule; the tabs only pass their checkbox through.

**Read.** library() fetches the approved prompts for the marketplace tab.
Same shape as plans.py: a frozen dataclass carrying either rows or a
short human `error`, never an exception, cached with a TTL.

── Why a fingerprint and not a row per click ──────────────────────────

People re-roll. The same prompt gets generated twenty times while its
seed changes on every one of them, and a POST per click would be twenty
requests and twenty rows for one idea. So the recipe is hashed and the
hash remembered, and the server is only called when the hash is one this
process has not sent.

What the hash covers is the whole design decision: everything *except*
the seed, the 🎲 toggle and the batch count. Those are stored, so a
prompt loaded out of the library comes back with the seed it was made
with — but they are not part of what makes a recipe *different*, because
if they were, 🎲 on would make every single click a new prompt and the
deduplication would do nothing at all.

The memory is per-process, which is fine: it is an optimisation, not the
correctness mechanism. The unique index on the server collapses whatever
gets through — a restart, a second pod on one licence, two customers who
typed the same thing.

Stdlib-only, like licensing.py and plans.py. The three are siblings
talking to the same API, none of them needs more than urllib for it, and
it keeps the Nuitka build unchanged.
"""

import hashlib
import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from config import LICENSE_API_URL, LICENSE_KEY, log

# Tab keys a prompt can be captured from and replayed into. These are the
# feature keys, and they are also the wire values the server validates
# against — a tab joins this list only once ui.py knows how to load a
# prompt back into its controls.
TAB_KREA2 = "krea_t2i"
TAB_KREA2_V2 = "krea_v2_t2i"

# How long a fetched page is served without asking again. Same 300s as
# plans.py, for the same reason: the library changes when an admin
# approves something, which is not often, and the tab has a Refresh
# button for the minute after they do.
TTL_SECONDS = 300

SUBMIT_TIMEOUT = 10
FETCH_TIMEOUT = 12

# How many fingerprints to remember. A pod generating flat out all day
# will not come close; the cap exists so a process that runs for a week
# has a bounded set rather than a slowly growing one. Oldest out first —
# a recipe not seen in the last 500 is one worth re-sending, and the
# server deduplicates it anyway.
MAX_REMEMBERED = 500

# Submissions waiting for the worker. Small on purpose: if the network is
# slow enough that 64 distinct recipes pile up, dropping the newest is
# strictly better than growing a queue nobody is draining. A dropped
# prompt costs nothing — the next generation with the same recipe is not
# re-sent (the fingerprint is already remembered), but any *other* recipe
# still is, and the library is not a system of record.
MAX_QUEUED = 64


@dataclass(frozen=True)
class Prompt:
    """One prompt from the library, as the tab renders it."""

    id: str
    tab: str
    source: str                 # "admin" | "community"
    title: str | None
    prompt: str
    negative: str
    settings: dict

    @property
    def is_official(self) -> bool:
        return self.source == "admin"


@dataclass(frozen=True)
class Library:
    """One page of the library, and how the fetch went.

    `error` is None on success and a short human sentence otherwise, in
    which case `prompts` is empty — the same contract as
    plans.Catalogue, so the tab can render without checking which case
    it got.
    """

    prompts: tuple[Prompt, ...] = ()
    total: int = 0
    skip: int = 0
    limit: int = 12
    error: str | None = None


# ── Capture ────────────────────────────────────────────────────────────

_seen: "OrderedDict[str, None]" = OrderedDict()
_seen_lock = threading.Lock()
_outbox: "queue.Queue[dict]" = queue.Queue(maxsize=MAX_QUEUED)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()

# Not part of what makes one recipe different from another. Stored, so a
# prompt loaded out of the library arrives with the seed it was made
# with; excluded from the hash, so re-rolling is not a new prompt. See
# the module docstring.
_VOLATILE = ("seed", "randomize", "batch_count")


def _fingerprint(tab: str, prompt: str, negative: str, settings: dict) -> str:
    """A stable sha256 over the parts of a recipe that identify it.

    sort_keys so a dict built in a different order hashes the same, and
    default=str so an unexpected value (a numpy float from a slider, say)
    is stringified rather than raising inside a generate handler.
    """
    recipe = {key: value for key, value in settings.items()
              if key not in _VOLATILE}
    canonical = json.dumps(
        {"tab": tab, "prompt": prompt.strip(), "negative": negative.strip(),
         "settings": recipe},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _remember(fingerprint: str) -> bool:
    """True if this is new to us — and mark it seen in the same breath.

    One lock around both halves so two Generate clicks landing together
    cannot both come away thinking they were the first. Marked *before*
    the request goes out, not after it succeeds: a burst of identical
    clicks should produce one submission even while the first is still
    in flight, and a failed submission is not worth retrying for
    something nobody is waiting on.
    """
    with _seen_lock:
        if fingerprint in _seen:
            return False
        _seen[fingerprint] = None
        while len(_seen) > MAX_REMEMBERED:
            _seen.popitem(last=False)
        return True


def record(tab: str, prompt: str, negative: str, settings: dict,
           publish: bool = False, title: str | None = None) -> None:
    """Queue this recipe for the library, if it is one we should send.

    Two capture policies, decided here so the rule lives in one place
    and the tabs only have to pass their checkbox through:

      customer pod   every new recipe, automatically and silently.
                     `publish` is meaningless and ignored — a customer
                     has no checkbox and nothing to publish with.
      admin pod      nothing at all, unless `publish` is set. Our own
                     test generations must not fill the review queue we
                     are the ones working through, and an official
                     prompt is something chosen rather than collected.

    `title` names an admin publication and is ignored otherwise. The
    server enforces both of these against the licence document rather
    than trusting the flags — see POST /v1/prompts.

    Returns immediately and never raises — it is called from inside the
    generate handlers, which must not be slowed down and must not be
    able to fail because of it.
    """
    try:
        if not LICENSE_API_URL or not LICENSE_KEY:
            return                          # dry run, or a pod with no tag
        if tab not in (TAB_KREA2, TAB_KREA2_V2):
            return

        publish = bool(publish) and _is_admin()
        if _is_admin() and not publish:
            return                          # ours, and not asked to publish

        prompt = (prompt or "").strip()
        if not prompt:
            return                          # nothing to save

        negative = (negative or "").strip()
        fingerprint = _fingerprint(tab, prompt, negative, settings)
        # A publish skips the seen-set entirely. It is one deliberate
        # tick of a checkbox, not a recipe we happened to notice, and
        # silently dropping it because the same prompt was generated an
        # hour ago would be the one failure nobody could explain.
        if not publish and not _remember(fingerprint):
            return                          # same recipe as last time

        _ensure_worker()
        _outbox.put_nowait({
            "license_key": LICENSE_KEY,
            "instance_id": _instance_id(),
            "tab": tab,
            "fingerprint": fingerprint,
            "prompt": prompt[:4000],
            "negative": negative[:4000],
            "settings": settings,
            "publish": publish,
            "title": (title or "").strip()[:120] or None,
        })
    except queue.Full:
        log.debug("Prompt library: outbox full, dropping one submission")
    except Exception as exc:                # never reaches the customer
        log.debug("Prompt library: could not queue a prompt (%s)", exc)


def _is_admin() -> bool:
    """Whether this pod is on one of our own licences — see record().

    Imported late for the same reason as _instance_id, and False for any
    answer that is not a clear yes: the fallback is the behaviour every
    customer pod has, which is the one that must never break.
    """
    try:
        import licensing
        return licensing.is_admin()
    except Exception:
        return False


def _instance_id() -> str:
    """This pod's id, from licensing — imported late to stay acyclic.

    licensing imports config and nothing else; this module is imported by
    ui.py, which is far downstream of the licence check, so by the time
    anything calls record() the id is set. "unknown" is a real answer for
    a UI launched without a seat, and the server only needs the field to
    be non-empty.
    """
    try:
        import licensing
        return licensing.instance_id() or "unknown"
    except Exception:
        return "unknown"


def _ensure_worker() -> None:
    """Start the single drain thread, once, on the first submission.

    Lazily rather than at import: a pod whose customer never generates
    anything should not carry a thread for it, and importing this module
    must stay free.
    """
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_drain, name="prompt-library",
                                   daemon=True)
        _worker.start()


def _drain() -> None:
    """Send queued prompts, one at a time, forever. Never raises.

    A daemon thread that blocks on get(): it costs nothing while idle and
    dies with the process. There is no retry — a submission nobody is
    waiting for is not worth a backoff schedule, and the next distinct
    recipe will be along shortly.
    """
    while True:
        payload = _outbox.get()
        try:
            status, body = _post("/v1/prompts", payload, SUBMIT_TIMEOUT)
            if status == 200 and body.get("ok"):
                log.debug("Prompt library: submitted (stored=%s)",
                          body.get("stored"))
            else:
                log.debug("Prompt library: server answered HTTP %s", status)
        except Exception as exc:
            log.debug("Prompt library: submission failed (%s)", exc)
        finally:
            _outbox.task_done()


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


# ── Read ───────────────────────────────────────────────────────────────

_cache: dict[tuple, tuple[float, Library]] = {}
_cache_lock = threading.Lock()


def _prompt(raw: dict) -> Prompt | None:
    """One prompt from the wire, or None if it is too broken to show.

    An id and prompt text are the whole bar. A card with odd settings
    still reads fine and still loads — the tab guards every value against
    what this pod actually offers before applying it — but one with no
    text has nothing to render.
    """
    if not isinstance(raw, dict):
        return None
    prompt_id = str(raw.get("id") or "").strip()
    text = str(raw.get("prompt") or "").strip()
    if not prompt_id or not text:
        return None
    settings = raw.get("settings")
    source = str(raw.get("source") or "community").strip()
    title = str(raw.get("title") or "").strip() or None
    return Prompt(
        id=prompt_id,
        tab=str(raw.get("tab") or "").strip(),
        source=source if source in ("admin", "community") else "community",
        title=title,
        prompt=text,
        negative=str(raw.get("negative") or ""),
        settings=settings if isinstance(settings, dict) else {},
    )


def _fetch(tab, source, search, skip, limit) -> Library:
    """One trip to /v1/prompts, every failure turned into `error`."""
    if not LICENSE_API_URL:
        return Library(
            skip=skip, limit=limit,
            error="This build has no node tag, so it cannot reach the "
                  "server to read the prompt library.",
        )

    params = {"skip": str(skip), "limit": str(limit)}
    if tab:
        params["tab"] = tab
    if source:
        params["source"] = source
    if search:
        params["q"] = search
    path = "/v1/prompts?" + urllib.parse.urlencode(params)

    try:
        status, body = _get(path, timeout=FETCH_TIMEOUT)
    except Exception as exc:                # transport: DNS, refused, TLS
        log.warning("Could not fetch the prompt library (%s)", exc)
        return Library(skip=skip, limit=limit,
                       error=f"Could not reach the server ({exc}).")

    if status != 200 or not body.get("ok"):
        log.warning("Prompt library request answered HTTP %s", status)
        return Library(
            skip=skip, limit=limit,
            error=f"The server answered HTTP {status} for the prompt library.",
        )

    rows = tuple(
        item for item in (_prompt(raw) for raw in body.get("prompts") or [])
        if item is not None
    )
    total = body.get("total")
    return Library(
        prompts=rows,
        total=int(total) if isinstance(total, int) else len(rows),
        skip=skip, limit=limit,
    )


def library(tab: str | None = None, source: str | None = None,
            search: str = "", skip: int = 0, limit: int = 12,
            force: bool = False) -> Library:
    """One page of approved prompts, from cache when it is fresh enough.

    `force=True` is the tab's Refresh button: it skips the TTL so a
    prompt approved a minute ago can be seen without waiting it out.

    Keyed on the whole query, so changing a filter is a fresh read rather
    than a stale page from a different one.

    Two answers are deliberately **not** cached, and for the same
    reason — both are states you are actively waiting to come out of, so
    holding either for five minutes hides the very change you are
    watching for:

      a failed fetch    the next view retries, so a server that was
                        briefly down does not leave the tab broken.
      an empty page     "nothing here yet" is what a filter says before
                        anything is approved. Caching it means approving
                        a prompt appears to do nothing until the TTL
                        expires, which reads as a broken filter rather
                        than a stale one. Re-asking is one small query
                        and only happens while a filter is empty.
    """
    key = (tab, source, search, skip, limit)

    with _cache_lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit[0] < TTL_SECONDS:
            return hit[1]

    # Fetched outside the lock: this is a network call, and holding the
    # lock across it would make a slow server serialise every other
    # filter change behind it.
    result = _fetch(tab, source, search, skip, limit)

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


def invalidate() -> None:
    """Drop the cached pages, so the next read hits the server."""
    with _cache_lock:
        _cache.clear()
