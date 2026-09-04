import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import { api } from '@/api/client'
import type {
  DisplayResult,
  MediaItem,
  QueueJob,
  StreamEvent,
  SubmitValues,
  TabSchema,
} from '@/api/types'

/*
 * The job queue.
 *
 * Three of the six UX defects this rewrite exists to fix live in this file:
 *
 *   * There is no progress bar today. Status is a string in a textbox polled
 *     once a second, even though client.py:270 has been yielding
 *     {"type":"progress","step","total"} the whole time. `progress` below is
 *     that pair and the bar is determinate as a result. The server parses it
 *     back out of the status line for now — see api._progress_pair, which
 *     says plainly that it is a bridge.
 *
 *   * Errors are invisible today. Every failure is a `❌ …` string written
 *     into the same textbox the next poll overwrites. Here an error is a
 *     field on the job, it survives until someone dismisses it, and the job
 *     stays in the list carrying it.
 *
 *   * The queue itself was never visible. Jobs are addressable by id, so an
 *     alert can be keyed to the run that produced it.
 *
 * What changed in Section 2: the queue is no longer this store's own idea.
 * It is process-wide on the server, because there is one GPU behind it, and
 * two browser tabs open on the same pod see one queue — which is the truthful
 * picture. So `connect()` opens one SSE stream, the `queue` event *is* the
 * job list, and this store's job is to merge the server's truth with the two
 * things only the browser knows: which run each tab is looking at, and which
 * errors have been dismissed.
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
  connected: boolean
  /** Per-tab pointer at the run whose output the tab is showing. */
  activeByTab: Record<string, string | undefined>
  /** Bumped when a background job saves a preset, so a dropdown showing a
   *  stale list can refill. The save happens on the worker thread, so
   *  nothing else is in a position to notice. */
  presetRevision: Record<string, number>
  connect(): () => void
  submit(input: {
    schema: TabSchema
    prompt: string
    values: SubmitValues
  }): Promise<string>
  cancel(id: string): void
  dismissError(id: string): void
  remove(id: string): void
  clearFinished(): void
  select(tabKey: string, jobId: string): void
}

/** Cap the list so a long session does not accumulate hundreds of finished
 *  jobs (and their image URLs) in memory. The server keeps 20 (jobqueue.
 *  HISTORY); this is looser because a job the server has forgotten is still
 *  worth showing in the run picker until the page is reloaded. */
const MAX_JOBS = 40

const STATUS: Record<QueueJob['status'], JobStatus> = {
  queued: 'queued',
  running: 'running',
  done: 'done',
  failed: 'error',
  cancelled: 'cancelled',
}

function trim(jobs: Job[]): Job[] {
  if (jobs.length <= MAX_JOBS) return jobs
  return jobs.filter(
    (job, index) => index < MAX_JOBS || job.status === 'running' || job.status === 'queued',
  )
}

/** One server row plus whatever the browser already knew about that job.
 *
 *  `errorDismissed`, `images`, `seed` and `prompt` are kept from the local
 *  copy: the queue event does not carry images (they arrive on `display`,
 *  which is per tab), and whether an alert has been dismissed is nobody's
 *  business but this browser's. */
function merge(row: QueueJob, previous: Job | undefined): Job {
  const status = STATUS[row.status] ?? 'queued'
  return {
    id: row.id,
    tabKey: row.tab,
    tabLabel: row.tabLabel,
    status: row.error ? 'error' : status,
    statusText: row.progress,
    progress: row.step,
    preview: previous?.preview ?? null,
    queuePosition: row.status === 'queued' ? row.place : null,
    images: previous?.images ?? [],
    error: row.error,
    errorDismissed: previous?.errorDismissed ?? false,
    seed: previous?.seed ?? null,
    prompt: previous?.prompt ?? (row.title === '—' ? '' : row.title),
    startedAt: previous?.startedAt ?? row.submitted * 1000,
    endedAt:
      status === 'queued' || status === 'running' ? null : (previous?.endedAt ?? Date.now()),
  }
}

/** The media out of one tab's latest yield.
 *
 *  Keyed by the tab's own `resultKeys`, so the video tab's "videos" arrives
 *  under that name rather than as position 0 — which is the whole reason
 *  jobqueue stopped carrying a status_index int. */
function mediaOf(result: DisplayResult): MediaItem[] {
  if (Array.isArray(result.videos)) return result.videos
  if (Array.isArray(result.images)) return result.images
  return []
}

export const useQueue = create<QueueState>((set, get) => {
  function patch(id: string, changes: Partial<Job>) {
    set((state) => ({
      jobs: state.jobs.map((job) => (job.id === id ? { ...job, ...changes } : job)),
    }))
  }

  function onEvent(event: StreamEvent) {
    if (event.type === 'queue') {
      set((state) => {
        const known = new Map(state.jobs.map((job) => [job.id, job]))
        // Newest first, which is the order every list in the UI reads in;
        // the server sends oldest first because that is the order they run.
        const jobs = [...event.queue.jobs]
          .reverse()
          .map((row) => merge(row, known.get(row.id)))
        // A job this browser submitted a moment ago may not be in the
        // snapshot yet, and a job the server has aged out of its 20-deep
        // history is still worth showing in the run picker.
        const seen = new Set(jobs.map((job) => job.id))
        const orphans = state.jobs.filter((job) => !seen.has(job.id))
        return { jobs: trim([...jobs, ...orphans]) }
      })
      return
    }

    if (event.type === 'display') {
      const images = mediaOf(event.result)
      set((state) => {
        // `display_for` points at the newest job for that tab that has
        // actually begun — not the newest queued one — so the target here
        // is the same job the server meant.
        const target = state.jobs.find(
          (job) => job.tabKey === event.tab && job.status !== 'queued',
        )
        if (!target) return {}
        return {
          jobs: state.jobs.map((job) =>
            job.id === target.id
              ? {
                  ...job,
                  images,
                  seed: typeof event.result.seed === 'number' ? event.result.seed : job.seed,
                  statusText: event.result.status ?? job.statusText,
                }
              : job,
          ),
        }
      })
      return
    }

    if (event.type === 'presets') {
      set((state) => ({
        presetRevision: { ...state.presetRevision, [event.tab]: event.revision },
      }))
    }
  }

  return {
    jobs: [],
    connected: false,
    activeByTab: {},
    presetRevision: {},

    connect() {
      const stop = api.subscribe(onEvent)
      set({ connected: true })
      // Belt and braces: the stream sends everything on connect, but a
      // reconnect after a dropped tunnel might land mid-flight. GET /queue
      // is the documented fallback and costs one request.
      void api
        .getQueue()
        .then((snapshot) => onEvent({ type: 'queue', queue: snapshot }))
        .catch(() => {})
      return () => {
        stop()
        set({ connected: false })
      }
    },

    async submit({ schema, prompt, values }) {
      try {
        const result = await api.submit(schema, values)
        // Optimistic, and replaced by the next `queue` event a moment
        // later. Without it the button appears to do nothing for up to
        // half a second, which is exactly the feedback gap this rewrite
        // is meant to close.
        set((state) => ({
          jobs: trim([
            {
              id: result.jobId,
              tabKey: schema.key,
              tabLabel: schema.label,
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
            ...state.jobs.filter((job) => job.id !== result.jobId),
          ]),
          activeByTab: { ...state.activeByTab, [schema.key]: result.jobId },
        }))
        return result.jobId
      } catch (error) {
        // A submit that never became a job still has to be visible, or the
        // button just does nothing — which is what the old UI did. A 422
        // from the schema arrives here naming the control it refused.
        const id = `local-${Date.now()}`
        set((state) => ({
          jobs: trim([
            {
              id,
              tabKey: schema.key,
              tabLabel: schema.label,
              status: 'error',
              statusText: 'Rejected',
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
          activeByTab: { ...state.activeByTab, [schema.key]: id },
        }))
        return id
      }
    },

    cancel(id) {
      const job = get().jobs.find((candidate) => candidate.id === id)
      if (!job || job.status === 'done' || job.status === 'error') return
      void api.cancel(id).catch(() => {})
    },

    dismissError(id) {
      patch(id, { errorDismissed: true })
    },

    remove(id) {
      set((state) => ({ jobs: state.jobs.filter((job) => job.id !== id) }))
    },

    clearFinished() {
      void api.clearFinished().catch(() => {})
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
