"""The job queue as the browser sees it: read it, cancel one, clear it.

Running a job is `ember.generation.queue`'s business, and shaping a
snapshot into JSON is `common._queue_json`. This is only the three routes.
"""


def register(api: APIRouter) -> None:
    @api.get("/queue", dependencies=[Depends(require_auth)])
    def queue():
        """The whole queue as JSON. The fallback under /stream.

        Kept deliberately: polling the queue once a second is a complete
        answer on its own, so if SSE turns out to be blocked by something
        between the pod and the browser, the worst case is a slightly
        coarser progress line.
        """
        return _queue_json()

    @api.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
    def cancel(job_id: str):
        message = jobqueue.cancel(job_id)
        log.info("Queue: %s", message)
        return {"message": message, "queue": _queue_json()}

    @api.post("/jobs/clear", dependencies=[Depends(require_auth)])
    def clear():
        return {"message": jobqueue.clear_finished(), "queue": _queue_json()}
