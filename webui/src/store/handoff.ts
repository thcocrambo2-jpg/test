import { create } from 'zustand'

/*
 * One page handing a form to another.
 *
 * Three things do it: the Prompt Library's Use button, the Gallery's "load
 * these settings", and a preset applied from an Edit tab. All of them mean
 * "put these values into *that* tab and take me there", and none of them can
 * call into a component that is not mounted.
 *
 * So it is a value bag keyed by tab, dropped off by whoever navigates and
 * picked up once by the tab that receives it — which keeps the sender from
 * having to name the receiver's controls at all. `take` clears as it reads,
 * so a later remount cannot re-apply a handoff over whatever the customer has
 * typed since.
 */

interface HandoffState {
  pending: Record<string, Record<string, unknown> | undefined>
  offer(tabKey: string, values: Record<string, unknown>): void
  take(tabKey: string): Record<string, unknown> | undefined
}

export const useHandoff = create<HandoffState>((set, get) => ({
  pending: {},

  offer(tabKey, values) {
    set((state) => ({ pending: { ...state.pending, [tabKey]: values } }))
  },

  take(tabKey) {
    const values = get().pending[tabKey]
    if (values) {
      set((state) => {
        const next = { ...state.pending }
        delete next[tabKey]
        return { pending: next }
      })
    }
    return values
  },
}))
