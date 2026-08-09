"""Seat-limited license check.

One customer key allows N concurrent running instances. The seat is taken
at startup, kept alive by a background heartbeat, and given back on a
clean exit.

The acquire response also carries the key's *entitlements* — which tabs
this customer has paid for. They are read here and handed to features.py
by app.py; this module deliberately knows nothing about what any feature
key means, so adding a tab never touches it.

It carries the plan those entitlements were resolved from too (see
plan()), which the app bar and the Pricing page use to say which tier the
customer is on, and the key's expiry date (see expires_at()), which the
app bar shows next to it. Both are labels and nothing else: the tabs come
from the flat `features` array, never from the plan name, and expiry is
enforced by the server — it refuses the acquire and stops answering the
heartbeat — never by that date being read here.

Seats are leases, not a counter: the server only counts a session while
its last heartbeat is recent, so an instance that dies without releasing
(SIGKILL, an OOM kill, a hard pod terminate, a network drop at teardown)
frees its own seat by going quiet. Nothing has to run here for that to
happen — release() just returns the seat sooner than the timeout would.

Deliberately stdlib-only. app.py defers third-party imports until pip has
run, and this module is called before any of that, so it cannot depend on
requests. It also keeps the Nuitka build unchanged.

This stops a customer from passing the binary around. It does not stop a
customer who patches the binary, which is a known and accepted non-goal.
"""

import atexit
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from config import (
    LICENSE_API_URL,
    LICENSE_GRACE_SECONDS,
    LICENSE_KEY,
    log,
)

CLIENT_VERSION = "1"

# A pod's network is not always up the instant the process starts, so the
# first call gets a few attempts before we call it a failure.
ACQUIRE_ATTEMPTS = 4
ACQUIRE_BACKOFF = (3, 6, 12)

# Overwritten from the acquire response — the server owns the cadence, so
# it can be changed for every deployed binary without reshipping one.
_heartbeat_seconds = 60

_instance_id = None
_stop = threading.Event()
_thread = None
_released = threading.Event()

# The feature keys this license grants, or None when the license document
# says nothing and features.py should fall back to its own defaults. Set
# once, from the acquire response — see entitlements().
_entitlements = None
# Tab titles for those keys, by key, from the server's features
# collection — display only, and empty against a server too old to send
# them. features.label_for() falls back to the built-in labels, so an
# empty dict here is an ordinary answer and not a missing one.
_feature_labels: dict[str, str] = {}
# The plan the license sits on, as (id, name); either may be None. Also
# from the acquire response, and **display only** — the Pricing page marks
# the current tier with it. What the license actually grants is always the
# resolved `features` array above, which the server has already flattened
# out of the plan. Reading a tab's availability off this instead would put
# the tiering rules back in the client, which is the thing that indirection
# exists to avoid.
_plan_id = None
_plan_name = None
# Whether this licence is one of ours rather than a customer's. A role and
# not an entitlement: it grants no tab, and what the app can run still
# comes from the `features` array alone. It decides one thing — whether
# prompts.py captures every new recipe into the library on its own (a
# customer pod) or only what the operator explicitly publishes (ours).
# Defaults to False, so a server too old to send the field, and a dry run
# that never asks one, both behave like a customer.
_is_admin = False
# When this license stops working, as a UTC datetime, or None for a key
# with no end date. Display only for the same reason as the plan above:
# the server checks it on every acquire and every heartbeat, so nothing
# here has to — and a client clock that is wrong must not be able to lock
# a customer out of a key that is perfectly valid.
_expires_at = None
# The last changed value the heartbeat has already complained about, so a
# customer editing a license mid-run is told once rather than every minute.
# Its own sentinel rather than None, which already means "the license
# grants no particular set" — sharing the two would swallow the warning
# for a license whose features were cleared.
_UNSET = object()
_entitlements_notified = _UNSET


def _resolve_instance_id() -> str:
    """A stable id for this pod, falling back to a per-run random one.

    Re-acquiring the same id is free on the server, so preferring RunPod's
    pod id means restarting the app on one pod reclaims its own seat
    instead of spending a second one while the first goes stale.
    """
    for var in ("RUNPOD_POD_ID", "RUNPOD_POD_HOSTNAME"):
        value = os.environ.get(var)
        if value:
            return value.strip()
    try:
        host = socket.gethostname().strip()
        if host:
            return f"host-{host}"
    except OSError:
        pass
    return f"rand-{uuid.uuid4().hex[:12]}"


def _post(path: str, payload: dict, timeout: int = 15) -> tuple[int, dict]:
    """POST JSON and return (status, body). Raises only on transport error."""
    request = urllib.request.Request(
        f"{LICENSE_API_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        # An HTTP error still carries the server's reason — read it rather
        # than collapsing every non-200 into "unreachable".
        try:
            return err.code, json.loads(err.read() or b"{}")
        except Exception:
            return err.code, {}


def _payload(**extra) -> dict:
    return {"license_key": LICENSE_KEY, "instance_id": _instance_id, **extra}


def _meta() -> dict:
    return {
        "pod_id": os.environ.get("RUNPOD_POD_ID", ""),
        "hostname": socket.gethostname(),
        "version": CLIENT_VERSION,
    }


def _clean_features(value) -> list[str] | None:
    """The server's `features` field as a list, or None for "no opinion".

    None and a malformed value are treated the same on purpose: the point
    of the null case is that features.py falls back to its defaults, and a
    server that answered something unexpected should land there too rather
    than leaving a customer with no tabs at all.
    """
    if not isinstance(value, list):
        return None
    return [item.strip() for item in value
            if isinstance(item, str) and item.strip()]


def _clean_feature_info(value) -> dict[str, str]:
    """The server's `feature_info` as {key: tab title}.

    Every unusable shape collapses to an empty dict, which is the same
    thing an older server sends by sending nothing — features.label_for()
    then uses the built-in labels and the tabs are named as they were
    before this field existed. A label that is missing or blank for one
    key drops just that key for the same reason.

    `tab_label` before `name`: the catalogue carries both because the tab
    strip wants something short ("Inpaint / Img2Img") where the pricing
    page wants prose ("Krea Inpaint — Img2Img"). Taking `name` when
    `tab_label` is unset is what lets a new feature row be described once
    and still title its tab sensibly.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, info in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(info, dict):
            continue
        for field in ("tab_label", "name"):
            label = info.get(field)
            if isinstance(label, str) and label.strip():
                out[key.strip()] = label.strip()
                break
    return out


def _clean_expiry(value) -> datetime | None:
    """The server's `expires_at` as a UTC datetime, or None for "no end".

    Every unusable answer lands on None: a key that never expires sends
    null, and a server too old to send the field sends nothing at all.
    Both are ordinary, and so is a value that does not parse — the app bar
    then shows no expiry, which is the same thing it shows for a perpetual
    key and strictly better than a wrong date.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # fromisoformat takes what Mongo serialises ("...T00:00:00.000Z")
        # once the Z is spelled as an offset.
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        log.debug("Could not read the license expiry %r", value)
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_admin() -> bool:
    """True if this licence is one of ours rather than a customer's.

    False until acquire_or_exit() has returned, and False for a dry run —
    both are the safe default, because the only thing this decides is
    whether the prompt library captures generations automatically, and
    "behave like a customer" is the behaviour every pod already had.
    """
    return _is_admin


def instance_id() -> str | None:
    """This pod's id for the seat it holds, or None before acquire ran.

    Only meaningful after acquire_or_exit() — a dry run never sets it,
    and callers outside the licensing path (prompts.py) treat None as an
    ordinary answer rather than a reason to fail.
    """
    return _instance_id


def entitlements() -> list[str] | None:
    """Feature keys this license grants, or None to use the app defaults.

    Only meaningful after acquire_or_exit() has returned; app.py passes
    the result straight to features.resolve().
    """
    return _entitlements


def feature_labels() -> dict[str, str]:
    """Tab titles for the granted features, by key. Display only.

    Empty is an ordinary answer — a dry run, or a server that does not
    send them — and features.label_for() handles it by falling back to the
    registry. app.py passes this to features.resolve() alongside
    entitlements().
    """
    return dict(_feature_labels)


def plan() -> tuple[str | None, str | None]:
    """The (id, name) of the plan this license is on. Display only.

    Both are None for a license that lists its features directly instead
    of naming a plan, and against a server too old to send them — so a
    caller must treat "unknown" as a normal answer and simply mark
    nothing, never as a reason to withhold the page.
    """
    return _plan_id, _plan_name


def expires_at() -> datetime | None:
    """When this license expires (UTC), or None when it never does.

    Display only — see plan() and _expires_at. None is a normal answer in
    three different ways (a perpetual key, a server that does not send the
    field, a date that did not parse), so a caller shows nothing rather
    than treating it as missing information.
    """
    return _expires_at


def _fail(message: str, code: int) -> None:
    """Log the reason plainly and stop. Called before anything is installed."""
    log.error("=" * 68)
    for line in message.splitlines():
        log.error("%s", line)
    log.error("=" * 68)
    sys.exit(code)


def acquire_or_exit() -> None:
    """Take a seat, or stop the app. Call this before any other work.

    Placed ahead of the ComfyUI clone and the model downloads on purpose:
    a customer who cannot take a seat should be told in seconds, not after
    ~90 GB of downloads.
    """
    global _instance_id, _heartbeat_seconds, _entitlements, _feature_labels
    global _plan_id, _plan_name, _expires_at, _is_admin

    if not LICENSE_KEY:
        _fail(
            "No license key found.\n"
            "Set KREA2_LICENSE_KEY in this pod's environment variables to "
            "the key you were given, then start the app again.",
            2,
        )

    # Empty when KREA2_NODE_TAG is unset or is not a single DNS label — see
    # config.py. Both cases are the same fix, and neither is worth spelling
    # out further here: a customer types the value they were handed.
    if not LICENSE_API_URL:
        _fail(
            "This pod is missing its node tag.\n"
            "Set KREA2_NODE_TAG in this pod's environment variables to the "
            "value issued with your license key, then start the app again.",
            2,
        )

    _instance_id = _resolve_instance_id()
    log.info("Checking license (instance %s) ...", _instance_id)

    last_error = "unknown"
    for attempt in range(1, ACQUIRE_ATTEMPTS + 1):
        try:
            status, body = _post(
                "/v1/acquire", _payload(meta=_meta()), timeout=20
            )
        except Exception as exc:           # transport: DNS, refused, TLS
            last_error = str(exc)
            status, body = None, {}

        if status == 200 and body.get("ok"):
            _heartbeat_seconds = int(body.get("heartbeat_seconds", 60)) or 60
            _entitlements = _clean_features(body.get("features"))
            _feature_labels = _clean_feature_info(body.get("feature_info"))
            _plan_id = body.get("plan_id") or None
            _plan_name = body.get("plan_name") or None
            _expires_at = _clean_expiry(body.get("expires_at"))
            # `is True` rather than truthiness: anything other than the
            # boolean the server sends — a string, a 1, a missing field —
            # means "not admin", which is the behaviour every pod had
            # before this flag existed.
            _is_admin = body.get("is_admin") is True
            log.info(
                "License OK — %s%s, seat %d of %d, expires %s%s",
                body.get("license_name") or "licensed",
                f" on {_plan_name}" if _plan_name else "",
                body.get("seats_in_use", 1), body.get("seats", 1),
                _expires_at.date() if _expires_at else "never",
                " · admin" if _is_admin else "",
            )
            _start_heartbeat()
            _install_exit_hooks()
            return

        # A refusal is final: retrying will not free a seat or un-revoke a
        # key, and retrying an invalid key just delays a clear message.
        if status == 403:
            _fail(body.get("message") or "This license cannot be used.", 3)

        if status == 400:
            _fail(body.get("message") or "The license server rejected the "
                  "request.", 3)

        if status is not None:
            last_error = f"HTTP {status} {body.get('error', '')}".strip()

        if attempt < ACQUIRE_ATTEMPTS:
            wait = ACQUIRE_BACKOFF[min(attempt - 1, len(ACQUIRE_BACKOFF) - 1)]
            log.warning(
                "License server not reachable (%s) — retrying in %ds "
                "(attempt %d/%d)",
                last_error, wait, attempt, ACQUIRE_ATTEMPTS,
            )
            time.sleep(wait)

    _fail(
        f"Could not reach the license server ({last_error}).\n"
        "Check this pod has outbound internet access, then start the app "
        "again. If it keeps failing, contact your supplier.",
        4,
    )


def _start_heartbeat() -> None:
    global _thread
    _thread = threading.Thread(
        target=_heartbeat_loop, name="license-heartbeat", daemon=True
    )
    _thread.start()


def _heartbeat_loop() -> None:
    """Keep the seat alive; stop the app if the license stops being valid.

    Two failure classes, handled differently on purpose. A 403 means the
    license really is gone (revoked, expired, seat taken) and the app stops
    at once. Anything else is treated as transient and tolerated for
    LICENSE_GRACE_SECONDS, so an Atlas blip or a flaky pod network does not
    kill a video render that is 40 minutes in.
    """
    last_ok = time.time()
    while not _stop.wait(_heartbeat_seconds):
        try:
            status, body = _post("/v1/heartbeat", _payload(), timeout=15)
        except Exception as exc:
            status, body = None, {}
            log.warning("License heartbeat failed (%s)", exc)

        if status == 200 and body.get("ok"):
            last_ok = time.time()
            if body.get("reacquired"):
                log.info("License session re-established")
            _note_entitlement_change(body)
            continue

        if status == 403:
            _shutdown(
                body.get("message") or "This license is no longer valid.",
                release_seat=True,
            )
            return

        offline = time.time() - last_ok
        if offline > LICENSE_GRACE_SECONDS:
            _shutdown(
                "Could not reach the license server for "
                f"{int(offline // 60)} minutes. Check this pod's internet "
                "access and start the app again.",
                release_seat=False,
            )
            return
        log.warning(
            "License server unreachable for %ds — continuing (grace %ds)",
            int(offline), LICENSE_GRACE_SECONDS,
        )


def _note_entitlement_change(body: dict) -> None:
    """Say so when a license's features changed under a running instance.

    Not applied live, and deliberately so: tabs are built once at launch
    and the weights a feature needs are downloaded before that, so a tab
    switched on now has no models behind it. A restart is the honest fix,
    and the alternative — silently ignoring the change — is a support
    ticket about a paid-for tab that never appeared.
    """
    global _entitlements_notified

    if "features" not in body:
        return                      # older server; nothing to compare against

    def shape(value):
        if value is _UNSET or value is None:
            return value
        return sorted(set(value))

    current = _clean_features(body.get("features"))
    if shape(current) in (shape(_entitlements), shape(_entitlements_notified)):
        return
    _entitlements_notified = current
    log.warning(
        "This license's features changed (now: %s; running with: %s) — "
        "restart the app to apply them.",
        ", ".join(current) if current else "the app defaults",
        ", ".join(_entitlements) if _entitlements else "the app defaults",
    )


def _shutdown(message: str, release_seat: bool) -> None:
    """Stop the whole app from the heartbeat thread."""
    log.error("=" * 68)
    log.error("%s", message)
    log.error("Stopping.")
    log.error("=" * 68)
    if release_seat:
        release()
    # os._exit, not sys.exit: SystemExit raised here would only unwind this
    # thread and leave Gradio serving without a valid license.
    os._exit(5)


def release() -> None:
    """Give the seat back. Idempotent, never raises, never blocks for long.

    Best-effort by design — the server's stale window returns the seat
    anyway. This just makes it immediate, which matters when a customer
    stops one pod and starts another straight away.
    """
    if _instance_id is None or _released.is_set():
        return
    _released.set()
    _stop.set()
    try:
        _post("/v1/release", _payload(), timeout=5)
        log.info("License seat released")
    except Exception as exc:
        # Teardown is exactly when the network is going away; the stale
        # window covers this, so it is not worth a retry or a loud error.
        log.debug("Could not release the license seat: %s", exc)


def _install_exit_hooks() -> None:
    """Release on the exits we can see.

    SIGINT is left alone: it already raises KeyboardInterrupt, which
    ui.launch_ui() catches for its own shutdown, and that path ends in a
    normal exit where atexit fires. Converting SIGTERM into a clean exit
    routes container stops through the same place. SIGKILL is not covered
    and does not need to be — that is what the lease is for.
    """
    atexit.register(release)

    def _on_signal(signum, _frame):
        log.info("Received signal %d — shutting down", signum)
        sys.exit(0)

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass  # not the main thread, or not supported here
