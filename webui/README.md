# webui — the React front-end

Vite + React 18 + TypeScript. This file covers the front-end development
loop only. How the app is routed, themed, served and shipped is in
[`../docs/architecture/web-ui.md`](../docs/architecture/web-ui.md).

## Running it

There is no mock: the dev server talks to the real Python API through a
proxy, so start that first.

```bash
# bash or PowerShell, from the repo root — the API, with no GPU
python scripts/dryrun.py --features all --api-only
```

```bash
# bash, in webui/
npm ci
npm run dev        # http://localhost:5173, proxying /api, /media, /thumbs to :7860
npm run build      # -> dist/, one JS chunk, no sourcemap
npm run typecheck
```

`make webui-dev` is the same `npm run dev`. `/media` and `/thumbs` are
proxied as well as `/api` because a generated image is served by the
Python side too, so there is no static directory for Vite to serve them
from. The proxy also strips buffering off `text/event-stream`, without
which the SSE queue updates arrive in one lump when the connection closes.

## Where things are

```
src/
  theme/tokens.css   every colour in the application, and nothing else has any
  api/types.ts       the wire contract
  api/client.ts      the seam every component imports `api` from
  api/http.ts        the real client
  api/queries.ts     the react-query hooks
  lib/schema.ts      defaults + the positional-argument builder
  lib/nav.ts         the four nav categories and their order
  components/        TwoColumn, SeedRow, LoraStack, PresetBar, SamplerPanel,
                     VariancePanel, SchemaForm, fields/, ui/
  features/          shell/, queue/, gallery/, library/, pricing/, tabs/, terms/
  store/             cross-page state: queue.ts, handoff.ts, tabState.ts
```

## Two rules that bite

**Submission order.** `schema.fields` order is the Python handler's
positional order, and `toSubmission()` (`lib/schema.ts`) walks that list.
Index `i` of the result is positional parameter `i`; the LoRA tail is
appended flat as `(enabled, name, weight)` triples. Getting it wrong
shifts every argument after the stack.

**Colours live in one file.** Every colour is a custom property on
`:root` in `theme/tokens.css`, redefined under `[data-theme="dark"]`.
Nothing else in `src/` may contain a colour literal:

```bash
# bash, in webui/
grep -rnE "#[0-9a-f]{3,8}\b|rgba?\(" src --include=*.css --include=*.tsx \
  | grep -v src/theme/tokens.css
```

## Before you commit

The built bundle is committed as a Python module, so an edit here that
nobody rebuilt would ship silently. From the repo root:

```bash
make webui                       # npm ci && npm run build, then regenerate the module
python scripts/check_webui.py    # must pass
```

`make webui` is the only target that needs Node. Why the bundle is
committed at all, and why `build.sourcemap: false` and
`manualChunks: undefined` are not negotiable, is in
[`../docs/architecture/web-ui.md`](../docs/architecture/web-ui.md).
