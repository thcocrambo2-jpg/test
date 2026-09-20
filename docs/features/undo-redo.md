# Prompt undo / redo

Every prompt box carries a pair of small **undo / redo** buttons under
it, plus Ctrl+Z and Ctrl+Y (or Ctrl+Shift+Z) while the box has focus.
For someone using the app, and for anyone tempted to delete this and let
the browser do it.

## Why it is not the browser's stack

A `<textarea>` has an undo stack of its own and Ctrl+Z reaches it. That
was measured against a real browser, and it covers one case of four:

| | native stack | `webui/src/lib/undo.ts` |
| --- | --- | --- |
| Ctrl+Z / Ctrl+Shift+Z on a desktop | yes | yes |
| one entry per typing burst | yes | yes |
| **buttons**, for a keyboard with no Ctrl | no | yes |
| **undo a preset / recipe / library load** | no | yes |

The last two are the ones that matter.

**Buttons, because phones.** A phone keyboard has no Ctrl key, and no
mobile browser exposes undo for a text field any other way, so without
them a prompt trimmed on a phone is not recoverable at all.

**Programmatic writes, because presets.** Loading a preset, a recipe or a
library card over a prompt assigns the value from script, and a scripted
assignment never enters the browser's own stack. Natively, Ctrl+Z after
loading a preset does nothing and the prompt you spent five minutes on is
gone. Verified, not assumed: typing a prompt, applying a preset over it
and pressing Ctrl+Z returns the typed prompt.

**And granularity is not free either.** A controlled React textarea
rewrites its value on every keystroke, which splits the native stack into
one entry per *character* — about 200 presses to undo a real prompt.

## How it works

The history is a React hook,
[`webui/src/lib/undo.ts`](../../webui/src/lib/undo.ts), sitting between
the textarea and its setter, so the buttons and the keys walk one stack
rather than two that disagree the moment either is used.

- `COALESCE_MS` is 450 ms. A pause that long, or finishing a word, closes
  the current entry and opens the next, which is what keeps a burst of
  typing to one entry.
- `DEPTH` is 100 entries per box, and each box has its own history.
  Prompts are small and the history is per mounted textarea.

One detail that is load-bearing: the buttons call `preventDefault()` on
`pointerdown`, so pressing one does not take focus. On a phone that is
what keeps the keyboard open and the caret in place between taps.

## Related

- [presets](presets.md) and [prompt library](prompt-library.md) — the
  programmatic writes row three and four are about.
- [Web UI](../architecture/web-ui.md) — the front end this hook lives in.
