"""What the route modules in this package share.

Auth and the licence gate, the upload resolvers, and the two translations
between what the rest of the app holds and what the browser is sent: one
generated file as the UI's MediaItem, and the job queue as JSON.

Nothing here registers a route. Each sibling module does that for its own
area, and `ember.web.api` calls them in turn.
"""

PREFIX = "/api/v1"

# The cookie the SPA trades the fragment token for. HttpOnly so no script
# on the page can read it back out, SameSite=Lax so another site cannot
# make the browser spend it, Secure because the tunnel is HTTPS. Named
# without "session" because it is not one — there is no server-side
# session, only this process's token.
COOKIE = "krea2_key"

# On by default: whoever has the URL can use the app, and nothing has to
# be pasted or kept. The token below is still generated and still works —
# it is simply not demanded, so a link that lost its fragment (a chat app
# that ate it, a copy-paste, a bookmark) still opens.
#
# Set KREA2_UI_REQUIRE_TOKEN=1 to put the gate back. Worth doing wherever
# the URL travels further than the people meant to use it: the tunnel
# hostname is the only thing standing between a stranger and this app's
# GPU while the gate is down.
ALLOW_ANON = not UI_REQUIRE_TOKEN

TOKEN = secrets.token_urlsafe(32)

# Uploads land here and are read once, at submit. Under TEMP_DIR because
# that is already the ephemeral tree ember/comfy/server.py and the tunnel
# binary use.
UPLOADS = TEMP_DIR / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────── auth

def _is_https(request: Request) -> bool:
    """Is this connection actually TLS, directly or behind the tunnel?

    Decides only whether the auth cookie is marked Secure. Marking it so
    over plain http would make the browser drop it silently, which reads
    as "the login does not work" on a developer machine; not marking it
    over https would let it travel in the clear on the day somebody
    reaches the pod on port 80.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _authorised(request: Request) -> bool:
    """Does this request carry the process token?

    Three ways in, and the order is deliberate. The cookie is the ordinary
    one. The header is for a client that would rather hold the token
    itself than have a browser hold it. The query parameter is accepted
    **only** on the exchange route below, never here.
    """
    if ALLOW_ANON:
        return True
    cookie = request.cookies.get(COOKIE)
    if cookie and secrets.compare_digest(cookie, TOKEN):
        return True
    header = request.headers.get("x-krea2-key", "")
    return bool(header) and secrets.compare_digest(header, TOKEN)


def require_auth(request: Request) -> None:
    if not _authorised(request):
        raise HTTPException(401, "This link is missing its access key.")


def require_feature(key):
    """A dependency that answers 403 unless the licence grants `key`.

    Layer one of four. Attached by `_mount_tabs()` to every per-tab route
    at registration time, which is the only place per-tab routes are
    created — see the module docstring on why not drawing a tab is not by
    itself a gate.
    """
    def check() -> None:
        if not features.enabled(key):
            raise HTTPException(
                403, "%s is not part of this licence." % features.label_for(key)
            )
    return check


# ─────────────────────────────────────────────────────── upload ids

def _upload_path(upload_id: str) -> Path:
    """An upload id -> its file, or a 400.

    The id is generated here and is a bare hex string, so anything with a
    separator or a dot in it did not come from this server. Checked rather
    than sanitised: there is no legitimate upload id that needs cleaning
    up, and a check that rejects is easier to be sure of than one that
    repairs.
    """
    if not upload_id or not upload_id.isalnum():
        raise HTTPException(400, "That is not an upload id.")
    path = UPLOADS / upload_id
    if not path.is_file():
        raise HTTPException(
            400, "That upload has expired — choose the image again."
        )
    return path


def _pil(upload_id: str) -> Image.Image:
    """An upload id -> a PIL image, which is what the handlers take.

    Loaded eagerly (`.load()`), because the file behind it is temporary
    and the handler runs later, on a worker thread, after the request that
    supplied it is long gone.
    """
    with Image.open(_upload_path(upload_id)) as image:
        image.load()
        return image.convert("RGBA") if image.mode == "P" else image.copy()


def _resolve_uploads(schema, raw: dict) -> dict:
    """Turn every upload reference in a submitted bag into what it means.

    Done here, on the request thread, rather than in the worker: the
    values a job runs on are frozen the moment it is queued (jobqueue.Job)
    and that is the behaviour a queue has to have — changing a control, or
    closing the page, after pressing Generate must not reach a job already
    in the line.

    Only `image` fields need it; each upload id becomes a PIL image.
    """
    values = dict(raw)
    for field in schema.named():
        value = values.get(field.name)
        if field.kind == "image":
            values[field.name] = _pil(value) if value else None
    return values


# ─────────────────────────────────────────────────────────── media

def _media(path, recipe=None) -> dict:
    """One generated file, as the UI's MediaItem.

    `id` and `path` are both the OUTPUT_DIR-relative posix key. The
    absolute path is deliberately not here: it names this pod's
    filesystem, and there is no version of the browser knowing
    /workspace/krea2/output/... that is worth the leak.
    """
    key = gallery_index.key_for(path) or Path(path).name
    quoted = quote(key)
    is_video = key.lower().endswith(gallery_index.VIDEO_EXT)
    row = {
        "id": key,
        "path": key,
        "url": "%s/media/%s" % (PREFIX, quoted),
        "kind": "video" if is_video else "image",
        # A placeholder ratio for video, so a grid of tiles does not
        # collapse to nothing while the browser loads metadata. Replaced
        # below by the thumbnail's own shape when there is one — reading
        # the real dimensions still needs a container parse, and the
        # thumbnail already answers the only question the grid asks.
        "width": 16 if is_video else 0,
        "height": 9 if is_video else 0,
        "createdAt": "",
    }
    try:
        stat = Path(path).stat()
        row["createdAt"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime))
    except OSError:
        pass
    # Only when there is really a thumbnail on disk. /thumbs falls back to
    # the original for anything gallery_index never encoded — anything from
    # before the module existed, or a clip on a pod with no ffmpeg — and a
    # caller told "here is a thumbnail" fetches it, so the fallback would
    # hand the same multi-megabyte file down a second time, under a second
    # cache key, to be shown at 512px. Absent is the honest answer, and
    # `thumbUrl?` on the TypeScript side has always said it was one of the
    # possible ones.
    has_thumb = gallery_index.has_thumb(path)
    if has_thumb:
        row["thumbUrl"] = "%s/thumbs/%s" % (PREFIX, quoted)
    # Header-only: PIL does not decode pixels until you ask it to, so a
    # page of two dozen costs two dozen small reads.
    #
    # A still is measured from the original, because that is both its true
    # size and the shape the grid wants. A clip cannot be — the container
    # is not a picture — so it is measured from its thumbnail instead,
    # which was fitted inside a 512px box without being reshaped and so
    # carries the clip's aspect ratio even though it does not carry its
    # dimensions. The grid asks only about shape; the lightbox, which asks
    # about size, does not print these for video.
    if not is_video or has_thumb:
        try:
            measure = gallery_index.thumb_path(path) if is_video else path
            with Image.open(measure) as image:
                row["width"], row["height"] = image.size
        except Exception:                        # noqa: BLE001
            pass
    if recipe is None:
        recipe = recipes.for_path(path)
    if recipe:
        row["seed"] = recipe.get("seed")
        row["tab"] = recipe.get("tab")
        row["tabLabel"] = recipe.get("tab_label")
        row["prompt"] = _recipe_prompt(recipe)
    return row


def _recipe_prompt(recipe) -> str:
    """The prompt out of a stored recipe, for the gallery caption.

    Taken by position from the tab's schema rather than by searching the
    rows for something long: `prompt_field` names it, and the recipe's
    fields are in submission order, so the two line up exactly.
    """
    schema = tabschema.BY_KEY.get(str(recipe.get("tab")))
    if schema is None:
        return ""
    names = [f.name for f in schema.named()]
    try:
        index = names.index(schema.prompt_field)
    except ValueError:
        return ""
    rows = recipe.get("fields") or []
    if index >= len(rows):
        return ""
    row = rows[index]
    value = row[1] if isinstance(row, (list, tuple)) and len(row) > 1 else None
    return value if isinstance(value, str) else ""


def _media_list(paths) -> list:
    return [_media(path) for path in paths]


def _display_json(schema, result) -> dict:
    """One job's latest yield, with paths turned into media rows.

    The keys are the tab's own result_keys, so the video tab's "videos"
    and "latest" arrive under those names rather than as positions 0 and
    1 — which is the whole reason jobqueue stopped carrying an int.
    """
    if not result:
        return {}
    row = {}
    for key, value in result.items():
        if key in ("images", "videos") and isinstance(value, list):
            row[key] = _media_list(value)
        elif key == "latest" and value:
            row[key] = _media(value)
        else:
            row[key] = value
    return row


# ─────────────────────────────────────────────────────────── the queue

# "step 3/8" inside a handler's status line. The handlers build that text
# in _run_jobs / _run_wan_jobs, so the format is this app's own and not a
# guess about somebody else's output.
_STEP = re.compile(r"step (\d+)\s*/\s*(\d+)")


def _progress_pair(text: str):
    """(step, total) out of a status line, or None.

    A bridge, and worth naming as one. `ember.comfy.client` already
    yields `{"type": "progress", "step", "total"}` as structured data;
    `_run_jobs` formats it into a sentence and the queue records the
    sentence. Reading it back out here is a regex over text this
    repository writes, which is safe but is not where this wants to end
    up.

    The structural version is to carry step/total on the Job alongside
    the line, which is also what a live latent preview would ride on.
    Until then, one regex in one place beats a determinate progress bar
    that nobody can have.
    """
    found = _STEP.search(text or "")
    if not found:
        return None
    step, total = int(found.group(1)), int(found.group(2))
    return {"step": step, "total": total} if total else None


def _failure(status: str, text: str):
    """The error out of a job, or None.

    Not simply `status == FAILED`. A handler that refuses a run — no
    model downloaded, nothing painted, ComfyUI not up — yields a line
    beginning with a cross and then *returns normally*, so the job
    finishes DONE carrying a message that is plainly a failure. jobqueue
    is right not to call that a crash; the UI would be wrong to render it
    as a success. FAILED itself is the narrower case: the handler raised.

    The cross is the whole convention and it is used consistently across
    all seven handlers, so matching on it is matching on a rule this app
    already keeps rather than on a coincidence.
    """
    line = (text or "").strip()
    if status == jobqueue.FAILED:
        return line or "The handler raised."
    return line if line.startswith("❌") else None


def _queue_json() -> dict:
    revision, rows, waiting, running = jobqueue.snapshot()
    return {
        "revision": revision,
        "waiting": waiting,
        "running": running,
        "jobs": [{
            "id": row.id, "lane": row.lane, "tab": row.tab,
            "tabLabel": row.tab_label, "title": row.title,
            "status": row.status, "progress": row.progress,
            "step": _progress_pair(row.progress),
            "error": _failure(row.status, row.progress),
            "submitted": row.submitted, "place": row.place,
            "revision": row.revision,
            # Seconds left, or null — see eta.py. Rounded: the browser
            # counts down between snapshots itself.
            "eta": None if row.eta is None else round(row.eta, 1),
        } for row in rows],
    }
