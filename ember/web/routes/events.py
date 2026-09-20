"""The one server-sent event stream the whole app rides on.

Queue changes, each run's latest output, and a preset dropdown going stale
— three event types on one connection, plus a heartbeat comment so that
nothing in between decides a quiet stream is a dead one.
"""

import json
import time

import anyio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ember.generation import queue as jobqueue
from ember.licensing import presets
from ember.web import tabschema
from ember.web.routes.common import (
    require_auth, _display_json, _queue_json,
)

# How often the SSE loop looks for something to say, and how often it
# says nothing out loud. jobqueue is thread-based and pull-oriented with
# a revision() counter, so this is a poll either way — see stream().
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15

# How much comment padding opens the stream. See _events — it exists to
# push past a size-triggered proxy buffer, not to say anything.
PREAMBLE_BYTES = 2048


def register(api: APIRouter) -> None:
    @api.get("/stream", dependencies=[Depends(require_auth)])
    def stream(request: Request):
        """One SSE connection for the whole app.

        SSE and not websockets, and the reason is jobqueue rather than
        taste: it is thread-based and pull-oriented with a `revision()`
        counter, so *something* has to poll it either way — a websocket
        would be a second protocol wrapped around the same loop. What
        EventSource adds for free is reconnection, which is the part a
        hand-written socket client always gets wrong, on a tunnel that
        drops idle connections.

        Three event types, and a heartbeat comment every 15 seconds so
        that neither Cloudflare nor a corporate proxy decides a quiet
        stream is a dead one.

        The headers below are the ones that decide whether any of it
        arrives. `X-Accel-Buffering` was here on its own and it is an
        **nginx** header: Cloudflare has never read it. Through a quick
        tunnel the connection opened, stayed open, and delivered nothing
        — DevTools showed one `stream` request with an empty EventStream
        panel — while everything the page displayed came from the single
        `GET /queue` that `connect()` fires on load. Statuses were right
        and no image ever appeared, because images only ride the
        `display` event.

        `no-transform` is the header that fixes it. It forbids an
        intermediary from recompressing the body, and recompressing is
        what makes an edge buffer it. `identity` says the same thing from
        the other side.
        """
        return StreamingResponse(
            _events(request), media_type="text/event-stream",
            headers={
                # no-transform is load-bearing; no-cache alone is not.
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # Belt to no-transform's braces: an edge that would have
                # gzipped the stream (and therefore buffered it) is being
                # told the body is already in its final encoding.
                "Content-Encoding": "identity",
                # Nginx and several corporate proxies buffer a response
                # body by default, which turns a live stream into one
                # delivery when it ends. Kept — it is the right header for
                # nginx, it is simply not the one Cloudflare reads.
                "X-Accel-Buffering": "no",
            },
        )


def _sse(event: str, payload) -> str:
    return "event: %s\ndata: %s\n\n" % (
        event, json.dumps(payload, ensure_ascii=False, default=str))


async def _events(request: Request):
    """The generator behind /stream. One connection, three event types.

    Sends everything once on connect — a page that has just loaded needs
    the current queue, not the next change to it — and then only what has
    moved. `seen` and `sent` below hold revision counters jobqueue already
    keeps, so "has this changed?" costs one integer comparison rather than
    a diff: `seen` for the queue as a whole, `sent` for each job's output.

    Async rather than sync, and that is not a style choice: Starlette runs
    a sync streaming generator on a threadpool worker, and a `time.sleep`
    in one holds that worker for as long as the connection lives. Two open
    browser tabs and the third request waits.
    """
    # Seeded from the same snapshot that is about to be sent, so the
    # first pass of the loop does not send it a second time.
    seen = {"queue": jobqueue.revision()}
    # What each *job* was last told about, by id.
    #
    # Per job and not per tab, which is the fix. Per tab meant asking
    # jobqueue for "the run this tab should show" and sending only that,
    # so a run that finished while the next one on its tab was starting
    # never had its final yield sent — and its revision was final, so
    # nothing later carried it either. Two runs queued on one tab, one
    # picture on screen.
    #
    # Seeded so that connecting still costs one display per tab — the run
    # each tab should show — rather than every job in the twenty-deep
    # history, whose images this browser has no row to hang on anyway.
    showing = set()
    for schema in tabschema.entitled():
        job_id, _, _ = jobqueue.display_for(str(schema.key))
        if job_id is not None:
            showing.add(job_id)
    sent = {job_id: revision
            for job_id, _, revision, _ in jobqueue.displays()
            if job_id not in showing}
    last_beat = 0.0
    # Padding, sent before anything that matters, and it is not
    # superstition. A proxy that buffers by *size* releases nothing until
    # its buffer fills, and the whole of this stream's first minute can be
    # a few hundred bytes — so the events sit in an intermediary that is
    # behaving exactly as configured. Two kilobytes of comment pushes past
    # the common thresholds. EventSource discards comment lines, so this
    # costs one write and reaches no application code.
    yield ":" + " " * PREAMBLE_BYTES + "\n\n"
    yield _sse("queue", _queue_json())

    while True:
        if await request.is_disconnected():
            return
        now = time.time()

        revision = jobqueue.revision()
        if seen.get("queue") != revision:
            seen["queue"] = revision
            yield _sse("queue", _queue_json())

        # Every run that has produced something, not one per tab. Rebuilt
        # each pass rather than added to, so ids that have aged out of
        # jobqueue's history leave with them instead of accumulating for
        # as long as the connection lives.
        allowed = {str(schema.key): schema for schema in tabschema.entitled()}
        fresh = {}
        for job_id, tab, stamp, result in jobqueue.displays():
            fresh[job_id] = stamp
            schema = allowed.get(tab)
            if schema is None or sent.get(job_id) == stamp:
                continue
            yield _sse("display", {"tab": tab, "job": job_id,
                                   "revision": stamp,
                                   "result": _display_json(schema, result)})
        sent = fresh

        # A preset saved by a background job — the save happens wherever
        # the job eventually runs, so nothing else is in a position to
        # notice the dropdown has gone stale. See jobqueue.note_preset_saved.
        for tab in presets.TABS:
            stamp = jobqueue.preset_revision(tab)
            if not stamp or seen.get(("preset", tab)) == stamp:
                continue
            seen[("preset", tab)] = stamp
            yield _sse("presets", {"tab": tab, "revision": stamp})

        if now - last_beat >= HEARTBEAT_SECONDS:
            last_beat = now
            # A comment, not an event: EventSource ignores it, and it is
            # the cheapest thing that keeps a tunnel from deciding a quiet
            # connection is a dead one.
            yield ": keep-alive\n\n"

        await anyio.sleep(POLL_SECONDS)
