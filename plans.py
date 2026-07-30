"""The public plan catalogue — the data behind the Pricing page.

`GET /v1/plans` on the license server answers with every plan whose
`is_public` is not false, plus the feature registry that names the tabs
those plans grant. It is unauthenticated on purpose: it is a price list,
not an entitlement.

**Nothing here decides what this pod can run.** That stays with
licensing.py → features.py, which act on the flat `features` array in the
acquire response and never learn that tiers exist. This module is read
only and its answer reaches nothing but the markup — so a plan renamed,
repriced or added on the server shows up on the page with no rebuild, and
a wrong answer here cannot switch a tab on or off.

Best-effort by design. Every failure — no node tag, a server that is
down, a body that does not parse — comes back as `Catalogue.error` rather
than an exception, because a price list that cannot be fetched must not
take the page down with it. The caller renders the error and offers a
retry.

Stdlib-only, like licensing.py: the two are siblings talking to the same
API, a GET returning JSON needs nothing more, and it keeps the Nuitka
build unchanged.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from config import LICENSE_API_URL, log

# How long a fetched catalogue is served without asking again. The server
# caches the collection for 60s of its own (see plans.js), so anything
# below that only costs round trips. Five minutes is well inside how often
# a price actually changes, and the page's Refresh button forces a read
# for when it just did.
TTL_SECONDS = 300

FETCH_TIMEOUT = 12


@dataclass(frozen=True)
class Plan:
    """One purchasable tier, as served by /v1/plans."""

    id: str
    name: str
    description: str | None
    price_monthly: float | None
    price_yearly: float | None
    currency: str
    features: tuple[str, ...]
    sort_order: int


@dataclass(frozen=True)
class FeatureInfo:
    """What a feature key is called in prose, from the server's registry.

    The server serves this from its code registry rather than its Mongo
    copy, so it can never describe a tab in terms that deployment does not
    know. Keys the app has and the server does not are handled by the
    caller — see `Catalogue.describe`.
    """

    key: str
    name: str
    description: str
    category: str


@dataclass(frozen=True)
class Catalogue:
    """The whole answer: plans, the feature registry, and how it went.

    `error` is None on success and a short human sentence otherwise, in
    which case `plans` is empty. Both are always present, so a caller can
    render without checking which case it got.
    """

    plans: tuple[Plan, ...] = ()
    features: dict[str, FeatureInfo] = field(default_factory=dict)
    error: str | None = None

    def describe(self, key: str) -> FeatureInfo:
        """Prose for a feature key, invented from the key if unregistered.

        The app and the license server deploy separately, so a plan may
        well grant a tab this registry has not caught up with. Showing
        "Wan I2v" beats dropping a line the customer is paying for.
        """
        known = self.features.get(key)
        if known is not None:
            return known
        return FeatureInfo(key=key, name=key.replace("_", " ").title(),
                           description="", category="")


_lock = threading.Lock()
_cached: Catalogue | None = None
_cached_at = 0.0


def _get(path: str, timeout: int) -> tuple[int | None, dict]:
    """GET JSON. Returns (status, body); status is None on transport error."""
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


def _plan(raw: dict) -> Plan | None:
    """One plan from the wire, or None if it is too broken to show.

    An id and a name are the whole bar: a card with no price still says
    what the tier includes, but one with no name has nothing to render.
    """
    plan_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not plan_id or not name:
        return None

    def money(key):
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    features = tuple(
        item.strip() for item in raw.get("features") or []
        if isinstance(item, str) and item.strip()
    )
    description = str(raw.get("description") or "").strip() or None
    sort_order = raw.get("sort_order")
    return Plan(
        id=plan_id,
        name=name,
        description=description,
        price_monthly=money("price_monthly"),
        price_yearly=money("price_yearly"),
        currency=str(raw.get("currency") or "USD").strip() or "USD",
        features=features,
        sort_order=int(sort_order) if isinstance(sort_order, int) else 0,
    )


def _feature(raw: dict) -> FeatureInfo | None:
    key = str(raw.get("key") or "").strip()
    if not key:
        return None
    return FeatureInfo(
        key=key,
        name=str(raw.get("name") or key).strip(),
        description=str(raw.get("description") or "").strip(),
        category=str(raw.get("category") or "").strip(),
    )


def _fetch() -> Catalogue:
    """One trip to /v1/plans, every failure turned into `error`."""
    if not LICENSE_API_URL:
        # Empty when KREA2_NODE_TAG is unset or malformed — see config.py.
        # A pod in that state never got past licensing.acquire_or_exit(),
        # so in practice this is the offline dry run (`--features ...`).
        return Catalogue(error="This build has no node tag, so it cannot "
                               "reach the licence server to read the plans.")

    try:
        status, body = _get("/v1/plans", timeout=FETCH_TIMEOUT)
    except Exception as exc:                # transport: DNS, refused, TLS
        log.warning("Could not fetch the plan catalogue (%s)", exc)
        return Catalogue(error=f"Could not reach the licence server ({exc}).")

    if status != 200 or not body.get("ok"):
        log.warning("Plan catalogue request answered HTTP %s", status)
        return Catalogue(
            error=f"The licence server answered HTTP {status} for the plan "
                  "list."
        )

    plans = [plan for plan in (_plan(raw) for raw in body.get("plans") or [])
             if plan is not None]
    features = {info.key: info for info in
                (_feature(raw) for raw in body.get("features") or [])
                if info is not None}

    if not plans:
        return Catalogue(features=features,
                         error="The licence server has no public plans to "
                               "show yet.")

    # The server sorts already; re-sorting here means a hand-edited plan
    # document with no sort_order still lands somewhere sensible instead
    # of wherever Mongo returned it.
    plans.sort(key=lambda plan: (plan.sort_order, plan.name))
    log.info("Plan catalogue: %d plan(s) — %s", len(plans),
             ", ".join(plan.id for plan in plans))
    return Catalogue(plans=tuple(plans), features=features)


def catalogue(force: bool = False) -> Catalogue:
    """The catalogue, from cache when it is fresh enough.

    `force=True` is the page's Refresh button: it skips the TTL so a price
    edited a minute ago can be seen without waiting it out.

    A failed fetch is *not* cached — the next view retries — so a server
    that was briefly down does not leave the page broken for five minutes.
    """
    global _cached, _cached_at

    with _lock:
        fresh = _cached is not None and time.time() - _cached_at < TTL_SECONDS
        if fresh and not force and _cached.error is None:
            return _cached

        result = _fetch()
        if result.error is None:
            _cached, _cached_at = result, time.time()
        return result
