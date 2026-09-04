import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import { api } from '@/api/client'
import type { JobEvent, MediaItem, SubmitValues } from '@/api/types'

/*
 * The job queue.
 *
 * Three of the six UX defects this rewrite is meant to fix live in this file:
 *
 *   * There is no progress bar today. Status is a string in a textbox polled
 *     once a second, even though client.py:270 has been yielding
 *     {"type":"progress","step","total"} the whole time. `progress` below is
 *     that pair, kept per job, and the bar is determinate as a result.
 *
 *   * Errors are invisible today. Every failure is a `❌ …` string written
 *     into the same textbox the next poll overwrites. Here an error is a
 *     field on the job, it survives until someone dismisses it, and the job
 *     stays in the list carrying it.
 *
 *   * The queue itself was never visible. Jobs are addressable by id, so an
 *     alert can be keyed to the run that produced it.
 */

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export interface Job {
  id: string
  tabKey: string
  tabLabel: string
  status: JobStatus
  /** The last human sentence the backend sent. */
  statusText: string
  progress: { step: number; total: number } | null
  /** Decoded latent preview, when the backend is sending them
   *  (context.md §4.12 — available and currently unused). */
  preview: string | null
  queuePosition: number | null
  images: MediaItem[]
  error: string | null
  /** Set when the user dismisses the alert; the job stays, the alert goes. */
  errorDismissed: boolean
  seed: number | null
  prompt: string
  startedAt: number
  endedAt: number | null
}

interface QueueState {
  jobs: Job[]
  /** Per-tab pointer at the run whose output the tab is showing. */
  activeByTab: Record<string, string | undefined>
  submit(input: {
    tabKey: string
    tabLabel: string
    prompt: string
    values: SubmitValues
  }): Promise<string>
  cancel(id: string): void
  dismissError(id: string): void
  remove(id: string): void
  clearFinished(): void
  select(tabKey: string, jobId: string): void
}

const unsubscribers = new Map<string, () => void>()

/** Cap the list so a long session does not accumulate hundreds of finished
 *  jobs (and their image URLs) in memory. Running jobs are never dropped. */
const MAX_JOBS = 40

function trim(jobs: Job[]): Job[] {
  if (jobs.length <= MAX_JOBS) return jobs
  const keep: Job[] = []
  for (const job of jobs) {
    if (keep.length < MAX_JOBS || job.status === 'running' || job.status === 'queued') {
      keep.push(job)
    }
  }
  return keep
}

export const useQueue = create<QueueState>((set, get) => {
  function patch(id: string, changes: Partial<Job>) {
    set((state) => ({
      jobs: state.jobs.map((job) => (job.id === id ? { ...job, ...changes } : job)),
    }))
  }

  function apply(id: string, event: JobEvent) {
    switch (event.type) {
      case 'queued':
        patch(id, {
          status: 'queued',
          queuePosition: event.position,
          statusText: event.position > 0 ? `${event.position} job(s) ahead` : 'Waiting for the GPU',
        })
        break
      case 'status':
        patch(id, { statusText: event.text, status: 'running', queuePosition: null })
        break
      case 'progress':
        patch(id, {
          status: 'running',
          queuePosition: null,
          progress: { step: event.step, total: event.total },
          preview: event.preview ?? null,
        })
        break
      case 'done':
        patch(id, {
          status: 'done',
          statusText: `${event.images.length} image${event.images.length === 1 ? '' : 's'}`,
          images: event.images,
          seed: event.seed ?? null,
          preview: null,
          endedAt: Date.now(),
        })
        release(id)
        break
      case 'error': {
        const cancelled = event.message === 'Cancelled.'
        patch(id, {
          status: cancelled ? 'cancelled' : 'error',
          statusText: cancelled ? 'Cancelled' : 'Failed',
          error: cancelled ? null : event.message,
          preview: null,
          endedAt: Date.now(),
        })
        release(id)
        break
      }
    }
  }

  function release(id: string) {
    unsubscribers.get(id)?.()
    unsubscribers.delete(id)
  }

  return {
    jobs: [],
    activeByTab: {},

    async submit({ tabKey, tabLabel, prompt, values }) {
      let jobId: string
      try {
        const result = await api.submit(tabKey, values)
        jobId = result.jobId
      } catch (error) {
        // A submit that never became a job still has to be visible, or the
        // button just does nothing — which is what the old UI did.
        const id = `local-${Date.now()}`
        set((state) => ({
          jobs: trim([
            {
              id,
              tabKey,
              tabLabel,
              status: 'error',
              statusText: 'Failed',
              progress: null,
              preview: null,
              queuePosition: null,
              images: [],
              error: error instanceof Error ? error.message : String(error),
              errorDismissed: false,
              seed: null,
              prompt,
              startedAt: Date.now(),
              endedAt: Date.now(),
            },
            ...state.jobs,
          ]),
          activeByTab: { ...state.activeByTab, [tabKey]: id },
        }))
        return id
      }

      set((state) => ({
        jobs: trim([
          {
            id: jobId,
            tabKey,
            tabLabel,
            status: 'queued',
            statusText: 'Queued',
            progress: null,
            preview: null,
            queuePosition: null,
            images: [],
            error: null,
            errorDismissed: false,
            seed: null,
            prompt,
            startedAt: Date.now(),
            endedAt: null,
          },
          ...state.jobs,
        ]),
        activeByTab: { ...state.activeByTab, [tabKey]: jobId },
      }))

      unsubscribers.set(
        jobId,
        api.subscribe(jobId, (event) => apply(jobId, event)),
      )
      return jobId
    },

    cancel(id) {
      const job = get().jobs.find((candidate) => candidate.id === id)
      if (!job || job.status === 'done' || job.status === 'error') return
      void api.cancel(id)
    },

    dismissError(id) {
      patch(id, { errorDismissed: true })
    },

    remove(id) {
      release(id)
      set((state) => ({ jobs: state.jobs.filter((job) => job.id !== id) }))
    },

    clearFinished() {
      set((state) => ({
        jobs: state.jobs.filter((job) => job.status === 'running' || job.status === 'queued'),
      }))
    },

    select(tabKey, jobId) {
      set((state) => ({ activeByTab: { ...state.activeByTab, [tabKey]: jobId } }))
    },
  }
})

/** The run a tab is currently showing: the one explicitly selected, else the
 *  newest for that tab. */
export function useActiveJob(tabKey: string): Job | undefined {
  return useQueue((state) => {
    const pinned = state.activeByTab[tabKey]
    if (pinned) {
      const found = state.jobs.find((job) => job.id === pinned)
      if (found) return found
    }
    return state.jobs.find((job) => job.tabKey === tabKey)
  })
}

export function useJobsForTab(tabKey: string): Job[] {
  // useShallow, not a bare selector: the filter allocates a new array on every
  // store notification, and zustand compares with Object.is.
  return useQueue(useShallow((state) => state.jobs.filter((job) => job.tabKey === tabKey)))
}

export function isLive(job: Job): boolean {
  return job.status === 'queued' || job.status === 'running'
}
