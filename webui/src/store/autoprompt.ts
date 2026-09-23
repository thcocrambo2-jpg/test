import { create } from 'zustand'
import { api } from '@/api/client'
import type { AutoPromptRequest, AutoPromptTask, SubmitValues, TabSchema } from '@/api/types'
import { defaultsFor } from '@/lib/schema'
import { useQueue } from './queue'
import { readTabState, writeTabState } from './tabState'

/*
 * Auto prompt's writes, one per tab, kept outside the component.
 *
 * A write can take minutes — the free model refuses as busy for a while and
 * then answers slowly — and nothing stops somebody looking at another tab in
 * the meantime. The panel unmounts when they do, so the polling and what
 * happens at the end live here, where the router cannot reach them: the
 * prompt still lands in the right tab's Prompt box, and "Generate when ready"
 * still queues it, with nobody watching.
 *
 * The panel's own controls (on or off, model, idea, key, Generate when ready)
 * are in `tabState` under `autoprompt.<tab>`, read here when the prompt
 * arrives, so a box ticked while the model was busy counts.
 */

/** The panel's controls, as `tabState` holds them for one tab. */
export interface AutoPromptControls {
  on: boolean
  model: string
  idea: string
  /** What the key box holds. `null` until the box is first shown, which is
   *  when it is filled in from the machine's key if it has one. */
  key: string | null
  generateWhenReady: boolean
}

export const CONTROLS_DEFAULT: AutoPromptControls = {
  on: false,
  model: '',
  idea: '',
  key: null,
  generateWhenReady: false,
}

export const controlsKey = (tabKey: string) => `autoprompt.${tabKey}`

/** One tab's write. `task` is the server's last word on it. */
export interface AutoPromptRun {
  starting: boolean
  task: AutoPromptTask | null
  /** A refusal to start, or a lost connection, as one sentence. */
  error: string | null
  /** True once "Generate when ready" has queued the prompt. */
  queued: boolean
}

interface AutoPromptState {
  runs: Record<string, AutoPromptRun | undefined>
  start(schema: TabSchema, values: SubmitValues, request: AutoPromptRequest): Promise<void>
  cancel(tabKey: string): void
  dismiss(tabKey: string): void
}

const POLL_MS = 1_000
/** Polls that may fail in a row before the page stops asking. A tunnel that
 *  drops for a few seconds should not lose a prompt that is still coming. */
const POLL_FAILURES = 10

const live = (task: AutoPromptTask | null) =>
  task !== null && (task.state === 'writing' || task.state === 'waiting')

export const useAutoPrompt = create<AutoPromptState>((set, get) => {
  function update(tabKey: string, patch: Partial<AutoPromptRun>) {
    set((state) => {
      const previous = state.runs[tabKey] ?? { starting: false, task: null, error: null, queued: false }
      return { runs: { ...state.runs, [tabKey]: { ...previous, ...patch } } }
    })
  }

  /** The prompt, into the tab's form, and queued if the box says so. */
  function land(schema: TabSchema, prompt: string) {
    const formKey = `form.${schema.key}`
    const defaults = defaultsFor(schema)
    writeTabState<Record<string, unknown>>(formKey, defaults, (previous) => ({
      ...previous,
      [schema.promptField]: prompt,
    }))
    const controls = readTabState(controlsKey(schema.key), CONTROLS_DEFAULT)
    if (!controls.generateWhenReady) return
    update(schema.key, { queued: true })
    void useQueue.getState().submit({
      schema,
      prompt,
      values: readTabState(formKey, defaults),
    })
  }

  function poll(schema: TabSchema, taskId: string, failures = 0) {
    setTimeout(async () => {
      const run = get().runs[schema.key]
      // Superseded by a newer write, or cancelled from here.
      if (run?.task?.id !== taskId || !live(run.task)) return
      try {
        const task = await api.getAutoPrompt(schema.key, taskId)
        if (get().runs[schema.key]?.task?.id !== taskId) return
        update(schema.key, { task })
        if (task.state === 'done') land(schema, task.prompt)
        else if (live(task)) poll(schema, taskId)
      } catch (error) {
        if (failures + 1 < POLL_FAILURES) {
          poll(schema, taskId, failures + 1)
          return
        }
        update(schema.key, {
          task: null,
          error: `Lost touch with the server while the prompt was being written: ${
            error instanceof Error ? error.message : String(error)
          }`,
        })
      }
    }, POLL_MS)
  }

  return {
    runs: {},

    async start(schema, values, request) {
      update(schema.key, { starting: true, task: null, error: null, queued: false })
      try {
        const task = await api.startAutoPrompt(schema, values, request)
        update(schema.key, { starting: false, task })
        poll(schema, task.id)
      } catch (error) {
        update(schema.key, {
          starting: false,
          error: error instanceof Error ? error.message : String(error),
        })
      }
    },

    cancel(tabKey) {
      const task = get().runs[tabKey]?.task
      if (!task || !live(task)) return
      update(tabKey, { task: { ...task, state: 'cancelled', retryIn: null } })
      api.cancelAutoPrompt(tabKey, task.id).catch(() => {
        /* Stopped here either way: whatever the server finishes is ignored,
         * because the poll only reads a task that is still live. */
      })
    },

    dismiss(tabKey) {
      update(tabKey, { task: null, error: null, queued: false })
    },
  }
})
