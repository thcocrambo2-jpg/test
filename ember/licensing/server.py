"""The licence server, as its read-only siblings talk to it.

`catalog.py`, `plans.py`, `presets.py` and `prompts.py` all ask the same
API the same way: one request, JSON in and JSON out, and an answer that
has to come back as data because not one of them is allowed to raise into
a page or a generate handler. That request lives here once, so the four
agree on what a refusal looks like rather than agreeing by coincidence.

What stays with the callers is everything they disagree about on purpose:
each one's timeout (the startup path can afford eight seconds, a price
list twelve), its TTL, its cache and its retries. `catalog.py` keeps its
own request entirely — it retries with a backoff and reads the document
rather than the status, which is a different shape from a single trip.

`seat.py` is not one of these either, and deliberately: acquiring and
holding a seat has its own retry, its own backoff and its own failure
modes, and it is the one piece of this app that must never be made to
behave like something simpler.

Stdlib-only, for the same reason as the modules that call it: a JSON
request needs nothing more, and it keeps the Nuitka build unchanged.
"""

import json
import urllib.error
import urllib.request

from ember.settings import LICENSE_API_URL


def post(path: str, payload: dict, timeout: int) -> tuple[int | None, dict]:
    """POST JSON. Returns (status, body).

    An error status is an answer, not an exception: it comes back as the
    status with its body parsed, or with `{}` when that body is not JSON,
    because the server explains a refusal in the body and the callers
    read that explanation. A transport failure — DNS, connection refused,
    TLS, timeout — raises instead, since there is no status to report
    when nothing answered. Every caller wraps this in a try/except for
    exactly that case.
    """
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


def get(path: str, timeout: int) -> tuple[int | None, dict]:
    """GET JSON. Returns (status, body).

    The same contract as post(): an error status comes back as a status
    with whatever body could be parsed, and only a transport failure
    raises.
    """
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


def is_admin() -> bool:
    """Whether this pod is on one of our own licences.

    False for any answer that is not a clear yes, because the fallback is
    what every customer pod does: not to write at all.

    `seat` is imported inside the call rather than at the top of this
    module, so that the two halves of the licence client never import
    each other.
    """
    try:
        from ember.licensing import seat as licensing
        return licensing.is_admin()
    except Exception:
        return False


def instance_id() -> str:
    """This pod's id, for the `instance_id` field a write carries.

    Imported late, like is_admin(). "unknown" is a real answer rather
    than a failure: a UI launched without a seat still reads presets and
    the prompt library, and the server only needs the field to be
    non-empty. Everything that calls this runs far downstream of the
    licence check, so a pod that has a seat has an id by then.
    """
    try:
        from ember.licensing import seat as licensing
        return licensing.instance_id() or "unknown"
    except Exception:
        return "unknown"
