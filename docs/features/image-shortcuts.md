# Image input shortcuts

The four ways a picture gets into an image field. For someone using the
app, and for someone changing `ImageDropField`.

Image inputs appear on **✨ Krea2 Edit** and **🔷 Krea2 V2 Edit** (a
source image, plus an optional second reference), on **🎬 Video
(Wan 2.2)** and on **🎥 MiniMax I2V** (a start frame). Every one of them
is the same component,
[`webui/src/components/fields/index.tsx`](../../webui/src/components/fields/index.tsx),
so every one of them takes all four routes in.

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

## Related

- [Gallery and recipes](../architecture/gallery-and-recipes.md) — the listing the strip
  reads.
- [krea2.md](../pipelines/krea2.md) and [minimax.md](../pipelines/minimax.md)
  — what the tabs do with the image once it is in.
