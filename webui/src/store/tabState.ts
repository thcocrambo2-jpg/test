import { useCallback } from 'react'
import { create } from 'zustand'

/*
 * State that outlives the tab it was set on.
 *
 * Every page in this app is a route, and `App.tsx` renders routes inside
 * `<Routes>` — so switching tabs unmounts the one you left and every `useState`
 * in it dies with it. That is correct for a lightbox and wrong for everything a
 * customer actually set: the prompt and the sliders on Krea2, the page and the
 * density on the Gallery, the search box and both filters on the Prompt
 * Library, the billing cycle on Pricing. All of it used to come back blank
 * after a glance at another tab.
 *
 * The two stores next to this one already have the property that fixes it —
 * they are module level, so the router cannot reach them. `useTabState` is that
 * trick with `useState`'s signature, so a call site changes by one word:
 *
 *   const [density, setDensity] = useTabState<Density>('gallery.density', 'comfortable')
 *
 * Keys are namespaced by page, and the generate tabs namespace by `schema.key`
 * on top of that — which is what keeps Krea2 and Krea2 V2 apart even though
 * their fields are named identically. Session only: nothing here is written to
 * storage, so a reload starts clean, and values that cannot be serialized at
 * all (an uploaded `File`) are kept as
 * happily as a number is.
 *
 * Three things to know before using it:
 *
 *   - `undefined` means "nothing stored yet", so a slot cannot hold it. Store
 *     `null`, `''` or `0` instead.
 *   - `initial` must be a stable reference — it is the fallback a functional
 *     update reads from. Primitives are free; hoist arrays and objects to a
 *     module constant or memoize them.
 *   - Updates may be functional, and read the live bag rather than whatever the
 *     render that created the setter happened to see.
 */

interface TabStateStore {
  bags: Record<string, unknown>
  put(key: string, value: unknown): void
}

const useStore = create<TabStateStore>((set) => ({
  bags: {},

  put(key, value) {
    set((state) =>
      Object.is(state.bags[key], value)
        ? state
        : { bags: { ...state.bags, [key]: value } },
    )
  },
}))

export type TabStateSetter<T> = (next: T | ((previous: T) => T)) => void

/** `useState`, keyed, and kept outside the router tree. */
export function useTabState<T>(key: string, initial: T): [T, TabStateSetter<T>] {
  const stored = useStore((state) => state.bags[key]) as T | undefined
  const put = useStore((state) => state.put)

  const set = useCallback<TabStateSetter<T>>(
    (next) => {
      const held = useStore.getState().bags[key] as T | undefined
      const previous = held === undefined ? initial : held
      put(key, typeof next === 'function' ? (next as (previous: T) => T)(previous) : next)
    },
    [key, initial, put],
  )

  return [stored === undefined ? initial : stored, set]
}

/** One slot read from outside React, or `initial` when nothing is stored.
 *
 *  For work that finishes after the component that started it may have gone:
 *  Auto prompt writes a prompt a minute or more after it was asked for, and by
 *  then the tab may be one somebody has navigated away from. */
export function readTabState<T>(key: string, initial: T): T {
  const held = useStore.getState().bags[key] as T | undefined
  return held === undefined ? initial : held
}

/** `readTabState`'s twin: a functional update from outside React. */
export function writeTabState<T>(key: string, initial: T, next: (previous: T) => T): void {
  useStore.getState().put(key, next(readTabState(key, initial)))
}
