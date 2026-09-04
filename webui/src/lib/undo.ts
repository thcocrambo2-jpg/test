import { useCallback, useEffect, useRef, useState } from 'react'

/*
 * Undo/redo history for a controlled textarea.
 *
 * The browser gives a `<textarea>` its own undo stack, and with Gradio no
 * longer re-rendering the box, Ctrl+Z reaches it again. That covers one case
 * of four, which is why this exists — measured against `theme.py`'s injected
 * version (:2881-3037) before that file was deleted:
 *
 *   | capability                          | native | here |
 *   | Ctrl+Z / Ctrl+Shift+Z on a desktop  |  yes   | yes  |
 *   | one entry per typing burst          |  yes   | yes  |
 *   | buttons, for a keyboard with no Ctrl|  no    | yes  |
 *   | undo a preset / recipe / library    |  no    | yes  |
 *
 * Rows three and four are the ones that matter. A phone keyboard has no Ctrl
 * key and no mobile browser exposes undo for a text field any other way, so
 * without buttons a prompt edited on a phone is not recoverable at all. And
 * the native stack knows nothing about a value React assigned, so loading a
 * preset over a prompt you spent five minutes on is, natively, permanent.
 *
 * Row two is not theoretical either. A controlled React textarea rewrites its
 * value on every keystroke, which splits the native stack into one entry per
 * *character* — 200 presses to undo a prompt. `theme.py:2996` called that
 * "worse than no undo at all" and it was right; the same 450 ms coalesce is
 * kept here for the same reason.
 */

// A pause this long, or a word boundary, closes the current entry.
const COALESCE_MS = 450

// Entries kept per box. Prompts are small and this is per-mounted-textarea.
const DEPTH = 100

export type UndoHistory = {
  onChange: (next: string) => void
  onKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void
  undo: () => void
  redo: () => void
  canUndo: boolean
  canRedo: boolean
}

/**
 * Wrap a controlled value in an undo history.
 *
 * `value` and `setValue` are the field's existing pair; the returned
 * `onChange` replaces the one passed to the textarea. Everything else on the
 * form keeps working unchanged — this only sits between the box and the
 * setter.
 */
export function useUndoHistory(
  value: string,
  setValue: (next: string) => void,
): UndoHistory {
  const stack = useRef<string[]>([value ?? ''])
  const at = useRef(0)
  const typedAt = useRef(0)
  // Set while we are the ones writing, so the resulting render is not
  // mistaken for somebody else's edit by the sync below.
  const applying = useRef(false)
  const [, bump] = useState(0)

  /*
   * A value that arrived from outside — a preset, a recipe, a library card,
   * a hand-off from another tab — is not in the stack, so it becomes its own
   * entry. That is the whole of what makes "undo the preset I just loaded"
   * work, and it is the case the browser cannot see: React assigns `.value`
   * directly, which the native stack does not record.
   */
  useEffect(() => {
    if (applying.current) {
      applying.current = false
      return
    }
    if (value === stack.current[at.current]) return
    stack.current.length = at.current + 1
    stack.current.push(value ?? '')
    if (stack.current.length > DEPTH) stack.current.shift()
    at.current = stack.current.length - 1
    typedAt.current = 0
    bump((n) => n + 1)
  }, [value])

  const step = useCallback(
    (delta: number) => {
      const next = at.current + delta
      if (next < 0 || next >= stack.current.length) return
      at.current = next
      typedAt.current = 0 // never coalesce onto a jump
      applying.current = true
      setValue(stack.current[next])
      bump((n) => n + 1)
    },
    [setValue],
  )

  const onChange = useCallback(
    (next: string) => {
      const now = Date.now()
      if (now - typedAt.current > COALESCE_MS) {
        stack.current.length = at.current + 1
        stack.current.push(next)
        if (stack.current.length > DEPTH) stack.current.shift()
        at.current = stack.current.length - 1
      } else {
        stack.current[at.current] = next
      }
      // A trailing space closes the entry, so undo steps back by words the
      // way an editor does rather than by whatever the pause happened to be.
      typedAt.current = /\s$/.test(next) ? 0 : now
      applying.current = true
      setValue(next)
      bump((n) => n + 1)
    },
    [setValue],
  )

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return
      const key = (event.key || '').toLowerCase()
      let delta = 0
      if (key === 'z') delta = event.shiftKey ? 1 : -1
      else if (key === 'y') delta = 1
      else return
      // Ours rather than the browser's, so the buttons and the keys walk one
      // history instead of two that disagree the moment either is used.
      event.preventDefault()
      step(delta)
    },
    [step],
  )

  return {
    onChange,
    onKeyDown,
    undo: () => step(-1),
    redo: () => step(1),
    canUndo: at.current > 0,
    canRedo: at.current < stack.current.length - 1,
  }
}
