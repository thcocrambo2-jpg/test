# webui — the React front-end

Vite + React 18 + TypeScript. Replaces the Gradio UI in `ui.py` / `theme.py`.
Read `../context.md` first; this file only covers what is specific to the
front-end.

```bash
npm install
npm run dev        # http://localhost:5173, against the mock API
npm run build      # → dist/, one JS chunk, no sourcemap
npm run typecheck
```

## Stage

Stage A is done: the spine plus **Krea2 t2i**, the pricing and
showcase page, the header/footer, the queue panel, the gallery and the theme
toggle. The other six schema-driven tabs have their schemas derived already
but render a "Stage B" page instead of a form — deliberately, because a mock
cannot validate an image upload, SSE through a proxy or video playback, and
finding out an assumption was wrong six tabs later is
expensive. Section 2 wires it to the real API first.

## Where things are

```
src/
  theme/tokens.css          every colour in the application, and nothing else has any
  api/types.ts              the wire contract Section 2 has to satisfy
  api/client.ts             THE seam — the only place that knows about the mock
  api/http.ts               the real client, written against the same interface
  mock/                     built to be deleted; see below
  lib/schema.ts             defaults + the positional-argument builder
  components/               TwoColumn, SeedRow, LoraStack, SamplerPanel,
                            VariancePanel, SchemaForm, fields/
  features/                 shell/, queue/, gallery/, pricing/, tabs/
```

## The mock

`src/mock/` derives every tab, field, default, range and choice list from
`../scripts/parity_baseline.json` — the parity contract itself, imported, not
copied, so the forms cannot drift from `scripts/parity.py --check`.

It is behavioural, not a fixture dump: a submit produces a job that queues,
runs, emits `{step, total}` progress and finishes with pictures — or fails,
about one time in seven, so the error surface gets exercised. Put "fail" in a
prompt to force one.

Everything is behind `VITE_USE_MOCK` and one ternary in `api/client.ts`. No
component imports anything from `src/mock/`. Section 2 deletes the directory,
deletes the ternary, and nothing else changes.

`src/mock/tabMeta.ts` is the exception worth knowing about: it holds the
*presentation* metadata the baseline does not describe — grouping, column,
conditional visibility. It deliberately contains **no label overrides**, so a
reworded label in the Python app can never be masked here. In Section 2 it
becomes `tabschema.py`.

## One thing that is load-bearing

**Submission order.** `schema.fields` is baseline order is the Python
handler's positional order. `toSubmission()` (`lib/schema.ts`) walks that list;
rendering regroups freely. Index `i` of the result is positional parameter `i`.
The LoRA tail is appended flat, as (enabled, name, weight) triples — getting
that order wrong shifts every argument after the stack (context.md §4.3).

## Colours

Every colour is a custom property on `:root` in `theme/tokens.css`, redefined
under `[data-theme="dark"]`. Nothing else in `src/` contains a colour literal.

To check:

```bash
grep -rnE "#[0-9a-f]{3,8}\b|rgba?\(" src --include=*.css --include=*.tsx \
  | grep -v src/theme/tokens.css
```

## Build settings that are not negotiable

`build.sourcemap: false` and `rollupOptions.output.manualChunks: undefined`.
The bundle is embedded into the Nuitka binary and served from process memory
(context.md §4.7, §4.8), so code-splitting buys nothing, a runtime `import()`
of an unregistered chunk is a hard 404, and a sourcemap would ship the source
tree inside a commercial binary.
