# Steps/CFG a Krea form starts on when its feature lists no model at all —
# an empty catalogue, which the tab reports and cannot run anyway. Only
# here so the form still has numbers to draw; a model record always wins.
_NO_MODEL_DEFAULTS = {"steps": 10, "cfg": 1.0}


# ── The catalogue, as the forms see it ───────────────────────────────────────
# Read late, never at import: the catalogue is loaded once by main.py after
# the licence check, and a list captured at import would be whatever an
# earlier import happened to see. Values are ids; labels are names.

def model_choices(feature) -> list:
    """The Model dropdown's values: the feature's model ids, in order."""
    return [model.id for model in catalog.feature_models(feature)]


def model_labels(feature) -> dict:
    """{model id: name} — what the dropdown shows for each value."""
    return {model.id: model.name for model in catalog.feature_models(feature)}


def default_model(feature) -> str:
    """The feature's first model id — its default — or "" when it has none."""
    choices = model_choices(feature)
    return choices[0] if choices else ""


def model_settings(feature) -> dict:
    """{steps, cfg} of the feature's default model, for the form defaults."""
    models = catalog.feature_models(feature)
    if not models:
        return dict(_NO_MODEL_DEFAULTS)
    steps, cfg = model_defaults(models[0])
    return {"steps": steps, "cfg": cfg}


# The queue's lanes, and the ComfyUI instance each one is stopped through.
# One worker per lane, which is the same "one at a time" rule the
# concurrency_id on every click used to state — and the video lane is
# separate for the same reason it had its own concurrency_id: when
# KREA2_WAN_PARALLEL gives it a ComfyUI of its own, a five-minute render
# must not sit in front of a picture. Registered here, at import, because
# the workers only park on a condition variable until something is
# submitted; nothing touches ComfyUI before the first job runs.
COMFY_LANE = "comfy"
WAN_LANE = "wan" if WAN_PARALLEL else COMFY_LANE
jobqueue.register_lane(COMFY_LANE, client.interrupt)
if WAN_LANE != COMFY_LANE:
    jobqueue.register_lane(WAN_LANE, wan_client.interrupt)


# Base weights each ComfyUI instance currently has loaded, keyed by its
# base URL. Only ever read and written from _release_on_swap below.
_LAST_MODEL_SIG = {}


def _release_on_swap(comfy_client, workflow) -> str:
    """Unload the previous models when this graph needs different ones.

    Without this, a swap has a window where both model sets are resident —
    ComfyUI holds the old ones until memory pressure evicts them — and on
    a pod with three Krea UNets in rotation that window is where the
    process gets OOM-killed. Freeing at the boundary makes the peak one
    model set instead of two.

    Costs nothing on a repeat job (same signature, no call) and costs only
    the reload on a genuine swap, which was going to happen regardless.
    Returns a status note, or "" when nothing was done.
    """
    if not FREE_ON_SWAP:
        return ""
    signature = model_signature(workflow)
    previous = _LAST_MODEL_SIG.get(comfy_client.base)
    # Record first: a failed free must not make the next job think the old
    # models are still the loaded ones.
    _LAST_MODEL_SIG[comfy_client.base] = signature
    if previous is None or previous == signature:
        return ""
    log.info("Model swap detected — unloading the previous models first")
    comfy_client.free_models()
    return "♻️ Different models than the last job — unloading the old ones"


def _run_tag() -> str:
    """A short random token, so no two jobs can ever share a filename.

    ComfyUI does not remember how many files it has written. It derives
    the next counter by listing the output folder and taking the highest
    one it finds, plus one (`folder_paths.get_save_image_path`). Delete
    the three newest pictures and that maximum drops by three, so the
    next three generations are written under the *exact* names of the
    files that were just deleted.

    Nothing downstream survives that. `/media/Krea2_00042_.png` is the
    same URL for the old file and the new one, and api._send serves it
    with `max-age=86400`, so a browser that saw the deleted picture keeps
    showing it in place of the new one for a day. The recipe is filed
    under the same relative path too, so the caption ends up describing a
    picture that is not the one on screen.

    A token in the prefix takes the counter out of the argument: every
    job gets a namespace nothing has ever written to, the counter starts
    at 1 inside it, and the name is unique whatever has been deleted.
    Eight hex characters is 4 billion, drawn per job rather than per
    process, which for a folder of a few thousand is not worth a
    collision check.

    A function, and module-level, so scripts/golden.py can stub it to a
    fixed value the way it already stubs client.upload_image — for
    exactly this reason: it lands *in* the workflow, and a snapshot that
    changed on every run would freeze nothing.
    """
    return uuid.uuid4().hex[:8]


def _run_jobs(jobs, builder=build_workflow, prefix="Krea2"):
    """Shared executor: yields (gallery_paths, status_text) as work progresses."""
    images = []
    total = len(jobs)
    alive, note = comfy_ensure_alive()
    if not alive:
        yield images, note
        return
    if note:
        eta.forget(client.base)            # restarted: nothing is loaded
        yield images, note
    timer = eta.tracker(getattr(builder, "__name__", prefix), total,
                        client.base)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        job_prefix = prefix
        if job["loras"]:
            job_prefix += "_" + Path(job["loras"][0][0]).stem
        # Last, so the readable part of the name — the tab, and which LoRA
        # it ran with — still comes first in a directory listing.
        job_prefix += "_" + _run_tag()
        workflow = builder(filename_prefix=job_prefix, **job)
        # Started before the swap, so the unload it may wait on counts as
        # part of this picture's time — it is, from where the customer sits.
        signature = model_signature(workflow)
        timer.start(workflow, signature, eta.is_cold(client.base, signature))
        swap_note = _release_on_swap(client, workflow)
        if swap_note:
            yield images, f"{swap_note} — job {label} will be slower"
        size = f", {job['width']}×{job['height']}" if "width" in job else ""
        # The seed this particular picture runs on, which is the one thing
        # the controls cannot be read back for: a batch walks consecutive
        # seeds, and with "🎲 Random seed" ticked none of them is the
        # number in the box. Stamped per job, so a batch of four files
        # four recipes that differ in exactly the field that matters.
        recipes.stamp(seed=job["seed"])
        yield images, f"⏳ Job {label} — queued (seed {job['seed']}{size})"
        try:
            for event in client.run(workflow):
                timer.event(event)
                if event["type"] == "progress" and event["total"]:
                    yield images, f"⏳ Job {label} — step {event['step']}/{event['total']}"
                elif event["type"] == "done":
                    images.extend(event["images"])
                    yield images, f"✅ Job {label} finished"
        except ComfyUIError as exc:
            yield images, f"❌ Job {label} failed: {exc}"
            return
    yield images, f"✅ All {total} job(s) done"


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _run_wan_jobs(jobs, builder=build_wan_i2v_workflow, comfy_client=None):
    """Video executor: yields (all_videos, latest_video, status_text).

    `comfy_client` is the instance the clips run on — wan_client unless a
    caller says otherwise. The MiniMax tabs say otherwise: they run on the
    main instance whatever KREA2_WAN_PARALLEL says, because their int8
    model plus 32B text encoder is ~48 GB of weights and does not fit
    beside a second instance holding VRAM back for Wan. The port and log
    that have to be alive follow the client, since ensure_alive must ask
    the instance the job is actually going to.
    """
    videos = []
    total = len(jobs)
    latest = None
    comfy_client = comfy_client or wan_client
    # Only Wan's own client points at the second instance, and only when
    # that instance exists — without the flag wan_client *is* client.
    on_wan_instance = comfy_client is wan_client and WAN_PARALLEL
    alive, note = comfy_ensure_alive(
        port=WAN_COMFY_PORT if on_wan_instance else COMFY_PORT,
        log_path=WAN_COMFY_LOG if on_wan_instance else COMFY_LOG,
    )
    if not alive:
        yield videos, latest, note
        return
    if note:
        eta.forget(comfy_client.base)      # restarted: nothing is loaded
        yield videos, latest, note
    timer = eta.tracker(getattr(builder, "__name__", "video"), total,
                        comfy_client.base)
    for idx, job in enumerate(jobs, start=1):
        label = f"{idx}/{total}"
        workflow = builder(**job)
        signature = model_signature(workflow)          # see _run_jobs
        timer.start(workflow, signature,
                    eta.is_cold(comfy_client.base, signature))
        swap_note = _release_on_swap(comfy_client, workflow)
        if swap_note:
            yield videos, latest, f"{swap_note} — video {label} will be slower"
        recipes.stamp(seed=job["seed"])       # see _run_jobs
        yield videos, latest, (
            f"⏳ Video {label} — queued (seed {job['seed']}, "
            f"{job['width']}×{job['height']}, {job['length']} frames)"
        )
        try:
            # Raw 720p renders can take the better part of an hour on an
            # A40, so the video timeout is far above the image one.
            for event in comfy_client.run(workflow, timeout=7200):
                timer.event(event)
                if event["type"] == "progress" and event["total"]:
                    yield videos, latest, (
                        f"⏳ Video {label} — step "
                        f"{event['step']}/{event['total']}"
                    )
                elif event["type"] == "done":
                    videos.extend(event["images"])
                    latest = videos[-1] if videos else None
                    yield videos, latest, f"✅ Video {label} finished"
        except ComfyUIError as exc:
            yield videos, latest, f"❌ Video {label} failed: {exc}"
            return
    yield videos, latest, f"✅ All {total} video(s) done"


# Every finished prompt tells the index what it wrote, which both keeps the
# listing correct without a rescan and is what queues the new files'
# thumbnails. Registered against the client rather than the two separate
# places that consume its "done" event (_run_jobs, _run_wan_jobs), so a
# third executor gets this for free.
on_output(gallery_index.note_new)
# And the same for the recipe behind them, for the same reason: one
# producer of the "done" event, so a third executor gets this free too.
on_output(recipes.note_output)


def _swap_trigger(text, entry, feature) -> str:
    """Put the selected model's trigger words into the prompt text.

    Any other model's trigger in the same feature's list is removed first,
    so switching models swaps triggers instead of stacking them. The text
    stays fully editable — whatever ends up in the box is used verbatim
    (nothing is added silently at generation time).
    """
    text = text or ""
    for other in catalog.feature_models(feature):
        trig = (other.trigger or "").strip()
        if not trig:
            continue
        idx = text.lower().find(trig.lower())
        if idx >= 0:
            text = text[:idx] + text[idx + len(trig):]
    text = text.strip().strip(",").strip()
    trigger = (entry.trigger or "").strip()
    if trigger:
        return f"{trigger}, {text}" if text else trigger
    return text


def _model_info_text(entry) -> str:
    """One-line summary shown under the Model dropdown."""
    steps, cfg = model_defaults(entry)
    info = (f"**{entry.variant.title()}** · "
            f"defaults: {steps} steps, CFG {cfg:g}")
    if entry.trigger:
        info += " · trigger words are inserted into the prompt (editable)"
    if not model_file_available(entry):
        info += " · ⚠️ **not downloaded yet** — restart the app to fetch it"
    return info
