# Ember branding

The app mark: a flame, for the name.

| File | What it is |
| --- | --- |
| `ember-logo.svg` | The mark on its own, in the brand rose (`#E11D48`), at its natural 396x572 proportions. **Source of truth** — everything else here is derived from it. |
| `ember-mark.svg` | The same outline with `fill="currentColor"`, for placing on a coloured background. |
| `ember-icon.svg` | App icon: the mark reversed out in white on the product's accent gradient, in a 100x100 / `rx=24` tile. |
| `ember-favicon.svg` | `ember-icon.svg` with the outline decimated (see below). |
| `ember-icon-{32,64,128,256,512}.png` | Rasterised `ember-icon.svg`, transparent outside the tile's rounded corners. |
| `ember-logo-{256,512}.png` | Rasterised `ember-logo.svg`, rose on transparency. |

## The two copies inside theme.py

The running app does **not** read these files. `theme.py` carries its own
inline copies of the outline — `_FAVICON` (the `<head>` data URI) and
`_MARK` (the header tile) — because a file path would be one more data
file for the Nuitka build to carry, and `build.sh` bundles only
`assets/showcase/showcase.json` out of this tree.

Both inline copies use a **decimated** outline: the curve is flattened and
run through Ramer-Douglas-Peucker at a 1.0-unit tolerance, which cuts ~4800
points to ~105 and the markup to about a fifth. They render at 16-32px
(favicon) and 22px (header), where the dropped detail is a small fraction
of a pixel. Anywhere the mark is drawn large, use `ember-logo.svg` — not
the inline path.

So: **editing a file in this directory does not change the app.** If the
mark itself ever changes, `_FAVICON` and `_MARK` in `theme.py` have to be
regenerated alongside it.

## Colour

`ember-logo.svg` keeps the mark's own rose. The app chrome does not use it:
the header tile and the favicon reverse the flame out in white over the
existing accent gradient (`--kx-accent` -> `--kx-accent-alt`, indigo to
violet), so the rename did not introduce a colour that appears nowhere else
in the UI. To make rose the product accent instead, change `ACCENT` /
`ACCENT_ALT` at the top of `theme.py` — that recolours the whole app, which
is a bigger decision than the logo.
