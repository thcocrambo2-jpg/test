# Image input shortcuts

The five ways a picture gets into an image field. For someone using the
app, and for someone changing `ImageDropField` or `ImagesField`.

Image inputs appear on **✨ Krea2 Edit** and **🔷 Krea2 V2 Edit** (a
source image, plus an optional second reference), on **🎬 Video
(Wan 2.2)** and on **🎥 MiniMax I2V** (a start frame). Every one of them
is the same component,
[`webui/src/components/fields/index.tsx`](../../webui/src/components/fields/index.tsx),
so every one of them takes all five routes in.

**🧩 Qwen 2.1 Reference** has the one multi-picture input, `ImagesField`
([`webui/src/components/fields/ImagesField`](../../webui/src/components/fields/ImagesField/index.tsx)),
for up to 10 numbered references. It takes the same five routes, several
pictures at a time — see [below](#several-pictures-at-once).

## Click, drop, paste

- **Click** the drop zone to open a file picker.
- **Drag and drop** a file onto it.
- **Paste** with Ctrl+V. Six of the labels say "paste with Ctrl+V"
  outright, because pasting is how a screenshot gets in.

The paste listener is on the window but it only acts while that drop zone
is **hovered or holds focus**. That is what lets a tab carry two image
inputs — the Edit tabs' source and second reference — without both
claiming the same paste.

Only the first file is taken, and only if its type is an image.

## Recent generations

Under the other three sits a strip of the app's own recent output:
`RecentStrip`. Clicking a thumbnail loads that image straight into the
field, with no download and no re-upload round trip.

It is there because the picture you want next is very often one this app
just made — an edit of an edit, a rendered still into Wan or MiniMax —
and the alternative is: open the gallery, download the file, come back,
find it in a file picker. Three of those four steps exist only because
the picture was on the far side of a browser dialog. It is already on
this machine, in a listing the app already serves.

Two details:

- **Stills only**, from the newest page. `useRecentImages` asks the
  server for a listing already narrowed to images rather than filtering
  the answer in the browser, because a page of everything taken on a
  video tab is mostly clips, and a clip is not something an image input
  can accept.
- **Errors are not reported.** `/gallery` is gated on the Gallery
  feature, so a licence without it answers 403 — and the honest rendering
  of that under an upload field is nothing at all. The strip simply does
  not appear.

The strip lights up the thumbnail it handed over, and stops the instant
something else is dropped, pasted or chosen: whether the field holds the
picked file is derived, not tracked, so nothing has to notice the change.
A second click while the first fetch is still in flight is guarded, so
the field cannot end up holding whichever request happened to finish
last.

## Recent uploads

A second strip sits under the first: **Recent uploads**, the user's own
pictures that earlier runs were given, newest first. It is the same
`RecentStrip` with `source="uploads"`, so it picks, lights up and greys
out exactly as the one above does.

The photo you edited yesterday is otherwise a trip back through a file
picker, and the app already has it: every submitted image is kept for its
recipe (see [Source images](../architecture/gallery-and-recipes.md#source-images)).
This strip lists the part of that store that was the user's own.

- **Only the user's own files.** A gallery picture fed back in and a pose
  from the library are kept too, and both have a strip of their own. So
  the routes in that bring a user's file — click, drop, paste, and a pick
  from this strip — mark it in
  [`lib/ownFiles.ts`](../../webui/src/lib/ownFiles.ts), and the upload
  says `origin=own`. Only those go on the list.
- **Only what was submitted.** A picture is listed once a run has been
  queued with it, not when it is dropped; the strip refetches after every
  submit. Using one again moves it to the front.
- **As long as its source is kept.** The list follows the source store's
  sweep: a picture no recipe names any more, and not used for a day,
  leaves the strip when it leaves the disk.
- **512px thumbnails.** A phone photo can be 30 MB, so the strip draws
  `/sources/<name>/thumb`, made on first ask; the full file is fetched
  only when one is picked.
- **Not gated on the Gallery feature**, unlike the strip above: these are
  the user's own files, which every tab with an image input takes.

## Several pictures at once

`ImagesField` takes every route the single input does, and adds rather
than replaces:

- the file picker allows several files, and a drop or a paste can carry
  several;
- a click in either recent strip adds that picture as the next number;
  the strip says which ("click one to add it as image 4") and is greyed out
  once the field is full;
- a file that is not a picture is skipped, and one past the limit is
  refused; both are named in a warning inside the field, not a toast,
  and the rest of the drop still goes in.

While files are dragged over it the field lights up and shows the
numbers they will get. The paste rule is the single input's: it acts
only while the field is hovered or holds focus.

## Related

- [Gallery and recipes](../architecture/gallery-and-recipes.md) — the listing the strip
  reads.
- [krea2.md](../pipelines/krea2.md) and [minimax.md](../pipelines/minimax.md)
  — what the tabs do with the image once it is in.
