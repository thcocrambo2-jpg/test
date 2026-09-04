import { create } from 'zustand'

/*
 * One page handing a form to another.
 *
 * Three things do it, and they were all the same awkward shape in Gradio: the
 * Prompt Library's Use button, the Gallery's "load these settings", and a
 * preset applied from an Edit tab. All of them mean "put these values into
 * *that* tab and take me there", and none of them can call into a component
 * that is not mounted.
 *
 * Gradio's answer was to declare the wiring after the tab loop, naming every
 * target tab's components in one enormous `outputs=` list, and to return a
 * `gr.update()` per control — over 200 of them, most of them no-ops. That is
 * why ui._use_recipe returns a tuple as long as every registered tab's
 * controls put together.
 *
 * Here it is a value bag keyed by tab, dropped off by whoever navigates and
 * picked up once by the tab that receives it. `take` clears as it reads, so a
 * later remount does not re-apply a handoff over whatever the customer has
 * typed since — which is the bug the Gradio version could not have, and this
 * one could.
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
