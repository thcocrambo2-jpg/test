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

## What the running app uses

Only one thing here reaches a customer: **`ember-icon-256.png` is the
Windows executable's icon.** `build.ps1` converts it to a multi-frame
`.ico` (16 through 256) and passes it to Nuitka as
`--windows-icon-from-ico`. Sizes rather than one large frame, because
Windows downscales badly and a shortcut at 32x32 off a single 256x256
frame looks blurred next to everything else on the desktop. If the
conversion fails the build carries on with no icon rather than stopping.

Nothing else in this directory is read at run time. The React UI draws its
own header tile in CSS — a letter on `--c-accent`, in
`webui/src/features/shell/shell.module.css` — and `webui/index.html`
declares no favicon, so the browser shows its default. The Linux build
bundles only `assets/showcase/showcase.json` out of this tree.

So: **editing a file here changes the Windows icon and nothing else.**
Redraw the mark and `ember-icon-256.png` has to be re-rasterised from
`ember-logo.svg` alongside it, or the next Windows build ships the old one.

## Colour

`ember-logo.svg` keeps the mark's own rose, and the app chrome does not use
it. The product accent is `--c-accent` in `webui/src/theme/tokens.css`,
which every surface derives from — there are no hex digits in component
stylesheets by design. Making rose the accent means changing that one token
(and its `-hover`, `-soft` and `-line` companions), which recolours the
whole UI: a bigger decision than the logo.
