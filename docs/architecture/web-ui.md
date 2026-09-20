# The web UI

The React front end and the Python that serves it: routing, the theme,
how the bundle reaches a compiled binary, and how a browser is let in. For
someone changing the code. Front-end dev commands are in
[`../../webui/README.md`](../../webui/README.md).

The front end is Vite + React 18 + TypeScript in `webui/`. It is **one
build for every licence** and learns what it may show from
`/api/v1/session` over the wire, so nothing about it is per-customer — the
gate is entirely server-side, in
[`ember/web/routes/`](../../ember/web/routes/). See
[licensing-and-features.md](licensing-and-features.md).

## Tab order and routing

Every generation tab is one entry in `tabschema.SCHEMAS`
([`ember/web/tabschema.py`](../../ember/web/tabschema.py)), written in
that pipeline's module under
[`ember/web/schema/tabs/`](../../ember/web/schema/tabs/), and the schema
carries its own `category` and `route`:

```python
TabSchema(key="krea_t2i", ..., category="generate", route="/generate/krea2")
TabSchema(key="krea_edit", ..., category="edit", route="/edit/krea2-edit")
TabSchema(key="wan_i2v", ..., category="video", route="/video/wan")
```

**The nav is grouped, not a flat strip.** Four groups — Generate, Edit,
Video, Library — in `CATEGORY_ORDER` (`webui/src/lib/nav.ts`), which tells
you which tabs make a picture from nothing and which change one you
already have.

**Every tab has a URL.** `/generate/krea2`, `/edit/krea2-edit`,
`/library/gallery`. They are bookmarkable, linkable and survive a reload,
and the browser Back button walks them.

A feature the licence does not grant is not in `/api/v1/session`'s
`features` array, so its nav entry is not rendered and its routes answer
403. The order is data, so changing it does not need a rebuild.

Two adjacent things are easy to confuse with `category`:

- The licence server stores a `category` per feature in its `features`
  collection and sends it on the acquire response, but the pod discards
  it: `seat._clean_feature_info()` keeps only the tab labels. So the
  category a tab renders under is the constant on its schema, not the
  one in the database.
- `sort_order` in that same collection orders the pricing page and the
  `features` array in an acquire response, and nothing else. Likewise
  `features.FEATURES` drives `summary()`, `assets()` and `enabled_keys()`
  only.

## The theme

The palette is CSS custom properties in `webui/src/theme/tokens.css`,
defined once on `:root` and redefined under `[data-theme="dark"]`. Light
and dark therefore come from one list and cannot drift apart. **No
component carries a colour literal**, which is checkable:

```bash
# bash, in webui/
grep -rnE "#[0-9a-f]{3,8}\b|rgba?\(" src --include=*.css --include=*.tsx \
  | grep -v src/theme/tokens.css
```

The theme follows the viewer: a `data-theme` attribute set before first
paint from `localStorage`, falling back to `prefers-color-scheme`, with a
toggle in the header.

Beyond the palette:

| | |
|---|---|
| Sticky application header | brand, the plan this licence is on, when it expires (amber inside the last month, red inside the last week), the theme toggle and the **Plans & pricing** link |
| Footer | the `Ctrl`+`Enter` hint, model/GPU counts, and the output path (click it to copy) |
| Grouped nav | four categories with a route each |
| One control card per tab | a 30-control tab reads as one form rather than thirty boxes |
| Sticky primary action | Generate / Edit / Animate stays reachable in columns that run past two screens |
| Determinate progress | a real bar driven by `{step, total}` off the event stream, not a status string |
| Persistent errors | keyed to a job id and dismissed by hand, so a failure cannot scroll away unseen |
| `Ctrl`/`Cmd` + `Enter` | runs the tab you are looking at; the footer says so |

## Submission order is load-bearing

`schema.fields` order **is** the Python handler's positional order.
`toSubmission()` (`webui/src/lib/schema.ts`) walks that list; rendering
regroups freely. Index `i` of the result is positional parameter `i`. The
LoRA tail is appended flat, as `(enabled, name, weight)` triples — getting
that order wrong shifts every argument after the stack.

On the Python side the same invariant is `Field.name` *is* the handler's
parameter name, and `tabschema._assert_signatures()` enforces it against
`inspect.signature(handler)` at import. It is the most important
defensive measure in the web layer: without it, a parameter renamed in
a pipeline's `handler.py` and not in the schema is a silent argument
shift, and the
Krea 2 V2 handler takes 31 of them.

**Values are ids; labels are for the eye.** Models and LoRAs come from the
licence server's catalogue and are referenced by id everywhere — presets,
prompt cards, recipes and the form itself. A select or radio `Field`, and
the LoRA stack's `LoraSpec`, may carry `choiceLabels` (`{value: label}`)
next to `choices`: the control shows the label and submits the value, and
a value with no label shows as itself. The `name` part of a LoRA triple is
a LoRA id, `"None"` for an empty slot. `/api/v1/catalog` keys `models` by
feature key, a tab's `modelRegistry` is that key, and a model row is
matched by `id` — never by `name`, which is only its label.

## Serving the bundle

[`ember/web/spa.py`](../../ember/web/spa.py) serves the compiled app from
two sources with one behaviour, and the order matters:

1. `webui_bundle` — the generated, committed Python module (below). This
   is what a shipped binary uses.
2. `webui/dist/` — what `npm run build` writes. Read when the module is
   absent, which is a checkout that has run `npm run build` but not `make
   webui`, and is also what the generator reads.

If neither exists the routes are still mounted and every page answers with
a short "front end not built" notice. That is deliberate: a fresh clone
with no `node_modules` has to be able to run `scripts/dryrun.py`, and the
failure it should see is a page naming the fix, not a 404 from a route
that was never registered.

**Deliberately not `StaticFiles`.** That class is filesystem-backed, and
inside a onefile extraction there is no directory to point it at. Serving
by hand is also what makes the two sources interchangeable and what makes
the gzip pass-through possible — the bundle's bytes go to the browser
already compressed.

Caching is asymmetric on purpose. Vite content-addresses everything under
`assets/` — `index-B21swU-0.js` changes its name whenever its bytes change
— so those are `immutable` for a year. `index.html` cannot be: it is what
names them, and a browser holding yesterday's copy would ask for assets
that no longer exist. So it is `no-cache` plus an `ETag`, and a
conditional request gets a 304. Note `no-cache`, **not** `no-store`:
"no-store" would forbid the browser from keeping the copy at all, which
makes it send no `If-None-Match`, which makes the 304 path dead code.

Media types for the `webui/dist/` path are spelled out rather than taken
from `mimetypes`, which on Windows answers from `HKEY_CLASSES_ROOT` where
`.js` is `text/plain` often enough to matter. A module script served as
`text/plain` is refused outright by every browser, so the development loop
would break on one machine and not another.

## Why the React bundle is committed

**Neither build host has Node, and neither ever will.** The Linux build
runs on a RunPod pod and the Windows build on a Windows box; installing a
JS toolchain on both — kept in step, so both emit identical JavaScript —
is a worse trade than committing what one dev machine produces. That
constraint also decided the tunnel: localtunnel was rejected for needing
Node on the target machine.

So `ember/web/webui_bundle.py` is **generated and committed**: every built
asset as a gzip `bytes` literal, in a Python module rather than a data
file, because Nuitka follows imports — a module comes along for free while
a data file needs its own `--include-data-files` entry to be right.

```bash
# bash — the only target that needs Node. Commit the result.
make webui
```

Both build scripts then do nothing but *verify* it.
`scripts/check_webui.py` recomputes a hash over `webui/src`,
`index.html`, `package.json`, `package-lock.json` and `vite.config.ts` and
compares it to the `SOURCE_HASH` baked into the module. Without that,
"someone edited a `.tsx` and forgot to rebuild" is a silent ship rather
than a build failure.

`.gitattributes` marks the file `linguist-generated -diff -merge`: a
conflict in it is **always** resolved by regenerating, never by editing,
because merging two halves of a compressed stream produces a file that
still parses as Python and serves a corrupt asset.

Two build settings in `webui/vite.config.ts` follow from the same place
and are not negotiable: `build.sourcemap: false` and
`rollupOptions.output.manualChunks: undefined`. The bundle is embedded in
the binary and served from process memory, so code-splitting buys nothing
— there is no CDN, no HTTP cache and no second visit — a runtime
`import()` of a chunk the embedder did not register is a hard 404, and a
sourcemap would ship the whole source tree inside a commercial binary.

## Letting a browser in

The public URL is a Cloudflare quick tunnel, which means possession of the
link cannot be the whole access control. A per-process
`secrets.token_urlsafe(32)` is printed in the URL **fragment**, and the
SPA exchanges it for an `HttpOnly` cookie and strips it with
`history.replaceState`.

The fragment is the point: fragments are never sent to servers, so the
token stays out of Cloudflare's logs, out of every proxy in between and
out of `Referer` headers. A query parameter would be in all three.

`/` and `/assets/*` are deliberately open. The shell is what reads the
token out of the fragment, so demanding the token before handing over the
shell would be a lock whose key is inside the room. It is useless without
one in any case — every route it calls is gated — so an unauthenticated
visitor gets a page that immediately says so.

`KREA2_UI_REQUIRE_TOKEN=1` makes the token mandatory rather than
advisory.

## Paths, uploads and media

`/api/v1/media` and `/api/v1/thumbs` both go through
`gallery_index.safe_path()` — one implementation, shared with `delete()`.
A `path_id` is the output-dir-relative posix path, the same key recipes
use, and **no absolute path ever crosses the wire**. Uploads are capped at
`MAX_UPLOAD` (64 MB). See
[gallery-and-recipes.md](gallery-and-recipes.md).
