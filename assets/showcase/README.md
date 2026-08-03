# The pricing page's feature showcase

Everything under the plan cards on **💳 Plans & pricing** comes from two
places:

* **the copy** — `showcase.json`, right here, compiled into the binary
* **the pictures** — a public Cloudflare R2 bucket, fetched by the
  customer's browser

`showcase.py` reads the JSON and builds the URLs; `theme.showcase_html`
renders it. The app never downloads an image itself.

## Pointing it at the bucket

Set one environment variable on the pod (or on your machine, for a dev
preview):

    KREA2_SHOWCASE_URL=https://<your-bucket>.r2.dev

Must be `https://`. Anything else is ignored with a warning. Leave it unset
and the section still renders in full — every picture becomes a placeholder
tile naming the file it wants, which is a perfectly good way to work on the
copy before the bucket is filled.

The bucket has to be **publicly readable** (an r2.dev subdomain or a custom
domain). No CORS configuration is needed: plain `<img>` and `<video>` loads
don't require it — only `fetch()` would.

## The bucket layout

It mirrors the JSON exactly: **feature key → the block's `dir` → filename**.

```
<bucket>/
  krea_t2i/hero/hero.webp
  krea_t2i/gallery/01.webp … 12.webp
  krea_edit/compare-a/before.webp
  krea_edit/compare-a/after.webp
  krea_edit/fan-a/source.webp, v1.webp, v2.webp, v3.webp
  wan_i2v/clip-a/frame-01.webp, clip.webp   ← .mp4/.webm play as video
  gallery/screen.webp                        ← a block with "dir": "."
```

To see every path the page currently expects, open Plans & pricing with
`KREA2_SHOWCASE_URL` unset — each tile prints its own path.

## Adding a picture

**Every file must be named in `showcase.json`, extension included.** A
bucket has no directory listing, so "render whatever is in this folder"
isn't something the app can do any more — uploading a thirteenth gallery
image means adding one line:

```json
{ "file": "13.webp", "caption": "“a caption shown on hover”" }
```

Filenames are restricted to letters, digits, `.`, `_` and `-`. Anything
else (a slash, a space) is refused and shows a placeholder instead.

### Videos

`.mp4` and `.webm` render as muted, looping, autoplaying video instead of
an image — used in `wan_i2v/clip-a/`. In the zoom view they get controls.

## Shapes and cropping

Every picture is placed in a fixed aspect box and **cropped to fill it**,
because the app can't measure a remote file. Each block declares a sensible
default; collage tiles cycle through a set of shapes to keep the masonry
uneven.

If a picture is being cropped badly, give it its own ratio:

```json
{ "file": "07.webp", "ratio": "16/9", "caption": "…" }
```

## Sizes

These come out of the bucket, not the binary, so the constraint is now the
customer's bandwidth rather than the build. Still worth keeping sane:

* **WebP**, quality ~80.
* **Max 1600px** on the long edge — the widest anything is shown is about
  1100px, and the zoom view caps at 1000px.

Everything is lazy-loaded and the panel starts hidden, so nothing is
fetched until someone opens the pricing page and scrolls.

## What renders, for whom

Every shipped feature is described **whether or not the licence grants
it** — the section sits under the price list to argue for the upgrade.
Granted tabs get a green "In your plan" chip; the rest get an upgrade line
linking back to the plans. (`showcase.SHOW_LOCKED = False` reverts to
describing only what is granted.)

Two things are never rendered: a feature key with no entry in
`showcase.json`, and a feature switched off in the `features.py` registry —
no plan can grant that one, so advertising it would be dishonest.

## Checking your work

    python scripts/dryrun.py --features all

No pod, no GPU and no licence server needed. Open **💳 Plans & pricing** and
scroll.
