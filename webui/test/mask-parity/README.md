# Mask parity check

`src/components/fields/MaskEditor/prepare.ts` is a port of
`_prepare_inpaint_inputs` (`ui.py:926`). This checks the port against the thing
it ports, rather than asserting it in a comment.

The editor's *output contract* is `{ background, layers }` — the same pair
`gr.ImageEditor` produced — and the Python transform is unchanged, so nothing
here can change a generated picture. The port exists for the live mask preview
and the output-size readout. This check is what says the preview is telling the
truth.

Run it from `webui/`:

```bash
npx esbuild src/components/fields/MaskEditor/prepare.ts \
    --format=esm --outfile=test/mask-parity/prepare.mjs
node test/mask-parity/fixtures.mjs        # writes .raw files next to itself
conda run -n krea2 python test/mask-parity/check.py
```

Exits non-zero if the dilation or the snap-to-16 arithmetic drifts.

## Last measured

| Stage | Result |
|---|---|
| `MaxFilter(grow*2+1)` at grow 0 / 3 / 8 / 32 | **bit-exact** |
| snap-to-16 + 2048 cap, six sizes incl. 50×40 and 4096² | **exact** |
| `GaussianBlur` at sigma 1 / 4 / 8 / 32 | max delta **2/255** (11 px at sigma 1), else ≤1 |

The blur difference is expected and bounded: Pillow runs the three box passes
in 24-bit fixed point, this runs them in float. Same algorithm, same box radius
derivation. It is not made exact because the exact one already runs, in Python,
on the layers this editor uploads.
