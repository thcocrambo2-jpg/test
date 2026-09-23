# The Gallery and recipes

How generated files are browsed, and how the settings behind one get
loaded back into the tab that made it. For someone changing the code.

## The Gallery, as a browser

The Gallery is **a reflowing masonry of tiles, plus a lightbox over it**.

- **The grid** packs `aspect-ratio` tiles into columns, with a density
  control — Large / Comfortable / Compact, 420 / 280 / 180 px column
  widths and a floor of 1 / 2 / 3 columns. "How many at once" is a
  preference, not a constant, so it reflows at every width down to a
  phone.
- **The filter** — All / Images / Videos — narrows the listing on the
  server (`GET /api/v1/gallery?kind=image|video`), not the page in hand:
  the cursor is an offset into the listing, so hiding clips from a loaded
  page would leave gaps that Older skips past. Changing it starts the
  listing from the top.
- **The lightbox** is the picture, large, over a dimmed page. **Left** and
  **Right** walk the list, **Esc** closes it, and a filmstrip scrolls the
  active thumbnail into view. Walking past the loaded page pulls the next
  one in, so a long walk does not stop at a page boundary. It warms the
  cache either side of the current picture.
- **The recipe panel** shows what the selected file was made with, and
  **Load these settings** hands them to the tab that made it — see below.
- **Videos** play in the lightbox through a real `<video>` element.
- **The bin** is under the picture it deletes, with a confirm step. Tiles
  can also be selected — Shift extends the selection the way every file
  manager does — and deleted in one request through
  `POST /api/v1/gallery/delete`.

Where you were and how you had it looking — the cursor, the filter and the density —
live in per-tab state, because the page unmounts the instant you glance at
a generate tab and paging to the fourth screen is work. The lightbox
deliberately is not among them: arriving on a page to find a full-screen
viewer already open is a jump-scare.

Tiles are **whole pictures shrunk into cells**, not square crops out of
the middle of them: the grid is how two generations of one prompt get told
apart, and a crop takes away the half that differs. They are served from
`/api/v1/thumbs/<path>`, which is a 512px WebP where one exists and the
original where it does not.

### The index and the thumbnails

[`ember/web/gallery_index.py`](../../ember/web/gallery_index.py) solves
two problems at once.

**Listing** is cached per *directory*, keyed on that directory's own
mtime. POSIX requires a directory's mtime to change when an entry is
created, deleted or renamed in it, so a directory nothing has written to
since the last look reuses its cached file list for the price of one
`stat()`. After a generation exactly one directory has changed, so a
refresh costs one `scandir` rather than a walk of the whole tree.

**Only finished files are listed.** ComfyUI writes a video *in place*,
under its final name, for the whole encode: `SaveVideo` hands `av.open()`
the real path, so the `.mp4` sits in the output folder from its first byte,
and mp4's `+faststart` then reopens the finished file and rewrites it end
to end to move the `moov` atom to the front. Nothing about the file says
which of those it is in the middle of. So the index shows a file once
either `note_new` has vouched for it — which every generated file goes
through the moment its prompt finishes, so a clip waits for nothing — or
nothing has touched it for `_WRITE_SETTLE`, which is what covers the files
this process did not write: a previous run's, and anything copied in by
hand. The test is applied when a listing is read rather than when a
directory is scanned, because "still being written" is a state a file grows
out of and a cached directory listing would otherwise hold a clip back
until something else landed beside it.

`/media` asks the same question, through `settled()`, and it is the half
that matters most: a finished file is served `max-age=86400` because it
never changes under its name, but an unfinished one is served `no-store`.
Handed a year's worth of `max-age`, a browser that fetched a half-written
clip keeps those unplayable bytes for a day without ever revalidating —
so one unlucky request, from a gallery that happened to be open while the
clip encoded, left a video that would not play in any tab until the cache
expired.

**Thumbnails** are written once, just after a workflow finishes, on a
background thread. A full-resolution PNG is several MB down a link to a
browser that is usually nowhere near the pod; a 512px WebP is 30–60 KB, so
a page of ten goes from ~25 MB to well under one. Videos get one from
their opening frame, which costs a call out to `ffmpeg` — the one thing in
the module that is not Pillow. A pod without `ffmpeg` on PATH is not an
error: it falls through to the same no-thumbnail path as everything else,
and the browser paints the first frame itself.

There is deliberately **no backfill**. Anything that existed before a
thumbnail could be made never gets one, and `thumb_for()` simply returns
the original. That keeps startup free and means a missing, a failed and a
still-encoding thumbnail all land in the same, already-working code path.

The module imports nothing from the web layer, so the ComfyUI client can
call `note_new` without dragging the API in behind it.

## Recipes — "how was this made?"

An image on its own is a dead end. It is the one that worked, and
everything behind it — the prompt, the model, the LoRA stack, and above
all the **seed** that a ticked *🎲 Random seed* threw away — left the UI
the moment the next click overwrote the controls.

So every finished prompt writes a **recipe** beside its output. Click a
tile and a *how this was made* panel opens under it, showing the prompt as
a quote and the settings as a table, with the seed the picture actually
ran on in the heading. **Load these settings** puts the whole lot back
into the tab it came from and switches to that tab.

Two values are deliberately not restored as recorded: **Seed** becomes the
seed that particular picture ran on rather than whatever was in the box,
and **🎲 Random seed** is switched off. Together they are the difference
between "the same settings" and "the same image", which is what someone
clicking that button is asking for.

### What is in one

A recipe is the *UI's* values, not the resolved ComfyUI graph — the same
call the prompt library and presets make. The dropdown label is what goes
back into a dropdown; the filename it resolved to on this pod is not, and
would be wrong on the next one.

It is stored as an ordered list of `[label, value]` pairs rather than a
dict, because a LoRA stack has eight controls all labelled "Weight" and
their order is the only thing saying which slot each belongs to.

**A tab's recipe is its schema's field list** — that is the trick that
makes this one implementation rather than ten.
`TabSchema.recipe_fields()` in
[`ember/web/schema/model.py`](../../ember/web/schema/model.py) zips that list
against the values the request carried, and restoring writes them back
through the same list. A tab that grows a control gets it in its recipes
with no change anywhere.

Two things are deliberately left out:

- **The publish and save-preset boxes.** They are per-run decisions rather
  than settings, so putting them in a recipe would mean loading one
  silently re-arms a publish. Both are stored as `None`.
- **Empty LoRA slots**, from the *panel* only. They are recorded — a slot
  has to restore whole — but a dropdown reading "None" beside a weight of
  0.8 is a row of noise.

**Uploaded files** — the source image of an edit or an image-to-video run
— are not stored *in* the recipe, but they do come back. See
[Source images](#source-images) below.

Because `.recipes.jsonl` on live pods is keyed positionally and read back
by label, **a reworded label or a moved control orphans history
silently.** That is why `scripts/check_schema.py --choices` guards every
label; see [Checks](../development/checks.md).

### Where it is kept

One append-only JSONL file next to the images: `<output>/.recipes.jsonl`.
No database, no licence server, nothing to configure — and it survives a
restart because it is a file.

- **Append-only**, because the alternative is rewriting the whole map
  after every picture, and a pod with a few thousand generations behind it
  would spend real time on that. Later lines win, so an update is just
  another line, and the file is compacted at most once per process.
- **Beside the images**, so a recipe lives and dies on the same disk as
  the file it describes. The leading dot means `gallery_index` already
  skips it, which keeps it out of the gallery grid and out of the Zip
  button's archive for free.
- **Keyed by the path relative to the output dir**, so moving the tree, or
  mounting it somewhere else on the next pod, does not orphan everything
  in it.
- **Bounded** at `MAX_RECIPES` (5000), oldest dropped on compaction.

### Source images

An upload is a temporary file, swept after six hours
([`routes/uploads.py`](../../ember/web/routes/uploads.py)), so it is long
gone by the time anybody loads the recipe of what it made. So at submit,
`_keep_sources` in
[`routes/common.py`](../../ember/web/routes/common.py) copies each one
into [`ember/generation/sources.py`](../../ember/generation/sources.py)'s
store, and the recipe records the copy's name in that image field's slot.

- **Kept at `<output>/.sources/<sha256>.<ext>`**, named after the file's
  own bytes. The ordinary case is one source used many times — a batch of
  four, or twenty tweaked re-runs over one photo — and that is still one
  file. The bytes are kept as uploaded, not re-encoded. Dotted, so the
  gallery and the Zip skip it, for the same reason as the recipe store.
- **Restored as a URL.** `restore()` accepts a name only if the file is
  still there; the apply route turns it into `/api/v1/sources/<name>`,
  and the Lightbox fetches that into a `File` before the handoff, because
  an image field holds a `File` and nothing else. A recipe from before
  this, or one whose source has gone, leaves the drop zone empty.
- **Served only by name shape.** `/sources/{name}` resolves nothing but a
  64-character hex digest with a `.png`, `.jpg` or `.webp` suffix, which
  cannot name anything outside the store.
- **Swept with the recipes.** Whenever the recipe store compacts — a
  delete, or the `MAX_RECIPES` cap — every source that no remaining
  recipe names goes too. A source kept for a run still in the queue has
  no recipe yet, so anything used in the last `GRACE` (a day) is left
  alone; `keep()` touches the file on every reuse.

### How the seed is captured

The executors in
[`ember/generation/runner.py`](../../ember/generation/runner.py) call
`recipes.stamp(seed=...)` before each prompt, because the executor is the
only place the real seed is known — a batch of four walks four consecutive
seeds, and a random tick ignores the box entirely. Since the client's
output hook fires once per ComfyUI prompt, a batch of four writes four
recipes differing in exactly the field that matters.

The recipe itself is announced from the *worker* thread: `_recording` in
[`ember/web/routes/tabs.py`](../../ember/web/routes/tabs.py) wraps the tab's handler in a
generator that calls `recipes.begin()` and, in a `finally`,
`recipes.end()`. `recipes.py` keys the open recipe by thread, which is
what lets the output hook deep inside the client find it without every
executor having to pass it down. A generator rather than a plain wrapper
because the handlers are generators: `yield from` keeps the tab's own
progress reporting exactly as it was, and the `finally` runs on a
cancelled job too, since closing a generator raises `GeneratorExit` at its
current yield.

### Reading one back safely

A recipe is a file on a pod's disk that outlives the build that wrote it,
so `POST /api/v1/schema/{tab}/apply` treats every stored row as data of
unknown shape. `TabSchema.restore()` is the path it uses, and it is
deliberately *not* the same path as a form submission:

| | Submit — `coerce()` | Apply — `restore()` |
| --- | --- | --- |
| Where the value came from | a form this server just described | another pod, possibly an older build |
| Out of range | 422 | clamped into this build's range |
| Unknown choice | 422 | the control is left alone |

So a build that reordered its controls loses one field rather than
scrambling the tab; a dropdown handed a choice this pod does not have is
left as it was; and a recipe for a tab this licence does not grant can be
read but not loaded, and the panel says so (`canLoad` on the
`/api/v1/recipe/...` response).

`restore()` runs server-side and has to: guarding a value means knowing
whether this pod has that LoRA file and what this build's slider range is,
and the browser knows neither. The answer holds only the controls it could
set — everything absent keeps whatever the customer had, which is the
difference between "load what I can" and "reset the form".

### Deleting a file

The bar under the picture carries a **🗑**, which removes the file being
shown for good. The pod's disk is ephemeral and there is no trash to fish
anything back out of, so it is two clicks: the bin arms it, and a confirm
button that only exists while it is armed does it. Both live in that same
bar, so arming does not resize the tab. Anything that changes the
selection disarms, because arming is a property of the selection rather
than of the tab.

Three things go, in that order:

1. **The file**, via `gallery_index.delete()`. It will only touch a path
   that resolves to a media file *inside* the output dir — the same two
   rules `safe_path()` uses to decide what the index lists. A gallery
   click resolves against a path the browser sent, i.e. client-supplied
   data, so a stale or forged path must not be able to reach
   `.recipes.jsonl`, the zip, or anything outside the tree at all.
2. **Its thumbnail**, best-effort. Nothing lists `.thumbs`, so a leftover
   is invisible rather than wrong, and it is not worth failing a delete
   that has already happened and cannot be undone.
3. **Its recipe**, via `recipes.forget()`, which compacts the store on the
   spot. The JSONL is append-only and later lines win, so rewriting the
   file without the key is the only way to make it stop existing —
   otherwise it would come back on the next restart describing a picture
   that is gone. A rewrite is O(everything), which is affordable for a
   deliberate gesture behind a confirm step.

The strip is then rebuilt from the state list minus that one path rather
than by re-scanning, for the same reason **Load more** pages out of state:
a rescan jumps back to page one, and someone who has paged four screens
down to tidy up would lose their place on every delete.

**The cursor does not move**, which is the point. Culling a batch is
look-bin-look-bin, and a viewer that empties itself after every delete
turns each of those into a click back into the strip. Keeping the index
means the file that was below the deleted one slides up into it — and at
the end of the list it steps back rather than off.

A file that was already gone from disk counts as a success: it is not
there, which is what was asked for. A path the index refuses, or one the
OS will not let go of, leaves the list alone and says so.
