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
  /** jobqueue's stamp on this job's last change — what makes "is the
   *  output I hold for this job the output it has now?" answerable. */
  revision: number
}

/** How the app is learning about the queue right now.
 *
 *  `live` is the stream working. `polling` is the fallback carrying the app
 *  because the stream is open but silent, or shut. `off` is before connect
 *  and after unmount. The distinction is shown to the user, because a page
 *  that has quietly stopped hearing from the server must not look identical
 *  to one that is up to date — that is the failure this whole fallback
 *  exists to answer. */
export type Transport = 'off' | 'live' | 'polling'

interface QueueState {
  jobs: Job[]
  connected: boolean
  transport: Transport
  /** Per-tab pointer at the run whose output the tab is showing. */
  activeByTab: Record<string, string | undefined>
  /** Bumped when a background job saves a preset, so a dropdown showing a
   *  stale list can refill. The save happens on the worker thread, so
   *  nothing else is in a position to notice. */
  presetRevision: Record<string, number>
  /** How many generated files this session has been told about — the same
   *  trick as `presetRevision`, for the gallery.
   *
   *  A finished generation writes a file into a listing the gallery has
   *  already fetched and cached, and nothing in a `useQuery` can see that
   *  happen. The stream can: a `display` event carries the new media, so
   *  counting what arrives gives a list of files a number that only ever
   *  goes up when there is genuinely something new to show. */
  mediaRevision: number
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

/* The watchdog, and why it is a watchdog rather than an error handler.
 *
 * Through a Cloudflare quick tunnel this app's stream opened, stayed open and
 * delivered nothing: one `stream` request in DevTools, no retries, no error,
 * and an empty EventStream panel. The socket was healthy. The bytes were in
 * an intermediary's buffer. Nothing the client could ask the EventSource
 * would have revealed that, because by every measure it had, it was fine.
 *
 * So the client stops trusting the socket and watches for *events*. Silence
 * past the deadline means the stream is not delivering, whatever it claims,
 * and the app switches to polling the two endpoints the events duplicate.
 * The first event to arrive switches it straight back.
 *
 * The deadline is comfortably past the server's 15s heartbeat, so an idle
 * app — nothing queued, nothing running — is not mistaken for a broken one.
 * The heartbeat is a comment and EventSource never surfaces it, which is why
 * this is 25 seconds and not 20: the newest thing this can see is the queue
 * event that follows the next real change. */
const SILENCE_MS = 25_000
const WATCHDOG_MS = 5_000
const POLL_MS = 1_500

const STATUS: Record<QueueJob['status'], JobStatus> = {
  queued: 'queued',
  running: 'running',
  done: 'done',
  failed: 'error',
  cancelled: 'cancelled',
}

/** The server statuses that are over — jobqueue.FINISHED, in the browser's
 *  spelling of the wire values. */
const FINISHED = new Set<QueueJob['status']>(['done', 'failed', 'cancelled'])

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
    revision: row.revision,
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

/* The revision of the newest output this browser has actually applied,
 * per *job*.
 *
 * Per job, and that is the fix. It used to be per tab, and a tab is a
 * moving target: the only question the server could be asked was "what
 * output should this tab be showing?", and the answer becomes the *next*
 * run the moment that run starts. A run that finished while its successor
 * was starting had its final yield fall straight down that gap — and a
 * finished job's revision never changes again, so no later event could
 * carry it either. From the outside that was a picture which generated
 * and then simply never appeared: not in the tab, and not as the
 * thumbnail on its row in the queue drawer. Queue two runs on one tab and
 * it was reliably one of the two.
 *
 * So `display` now names the job it is about, every job can be asked for
 * by name, and output is attached to the run that made it. Which run a
 * tab *shows* was never this map's business and is not inferred from it:
 * that is `activeByTab`, which the queue has always stated outright.
 *
 * The revision is jobqueue's own counter for that job — the same integer
 * the queue snapshot carries — which is what makes "is the output I hold
 * for this job the output it has now?" answerable without asking, and
 * what orders two answers about one run that arrive out of order.
 *
 * Pruned against the queue on every snapshot, so ids the server has
 * forgotten leave with it. That also covers the case the old code needed
 * a separate "did the revision go backwards?" check for: a restarted
 * server begins its counter at 1 again, but it also hands out an entirely
 * new set of ids, and none of the old marks survive the first snapshot.
 */
const DISPLAY_SEEN = new Map<string, number>()

/** How many runs one polling round will fetch output for.
 *
 *  The round after the fallback engages finds every run it knows stale,
 *  and a page that has been open a while knows up to MAX_JOBS of them.
 *  Newest first, so the runs somebody is plausibly looking at catch up in
 *  the first round and the rest follow over the next few. */
const MAX_DISPLAY_FETCH = 4

/* Which files this session has already counted.
 *
 * `display` is not an announcement that something was made — it is one
 * tab's *latest* output, restated. The polling fallback asks for it every
 * 1.5 seconds, and the stream replays every tab's on connect, so counting
 * events rather than files would have the gallery refetching itself on a
 * timer for as long as anything was running.
 *
 * Capped, and eviction is safe: a display event only ever carries the
 * newest job's output, so an evicted id is one that will not be offered
 * again. If one somehow were, the cost is a single redundant refetch. */
const COUNTED = new Set<string>()
const COUNTED_MEMORY = 500

function countNew(images: MediaItem[]): number {
  let fresh = 0
  for (const image of images) {
    if (COUNTED.has(image.id)) continue
    if (COUNTED.size >= COUNTED_MEMORY) {
      // A Set iterates in insertion order, so the first key is the oldest.
      const oldest = COUNTED.values().next().value
      if (oldest !== undefined) COUNTED.delete(oldest)
    }
    COUNTED.add(image.id)
    fresh += 1
  }
  return fresh
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
          /* A job that was already over the first time this browser heard
           * of it is not shown at all.
           *
           * The server keeps the last 20 finished jobs (jobqueue.HISTORY)
           * so that a tab can read its gallery back out of them, and sends
           * the lot on connect. But the images live only in this browser —
           * the queue event carries none, and `display` replays exactly one
           * job per tab — so after a reload those rows arrived as run
           * buttons that could never show anything, and as finished rows in
           * the drawer with no thumbnails. A run picker whose entries are
           * empty is worse than no entry: the work is in the gallery, which
           * is the page that lists the disk.
           *
           * `known` is the test rather than a timestamp, and it is the
           * right one for the other case this has to survive: a job
           * submitted from a second browser on the same pod is seen here
           * while it is still queued or running, so by the time it finishes
           * it is known and it stays. Only a run that began and ended
           * entirely between two of our snapshots is dropped, and that one
           * genuinely has nothing here to show. */
          .filter((row) => known.has(row.id) || !FINISHED.has(row.status))
          .map((row) => merge(row, known.get(row.id)))
        // A job this browser submitted a moment ago may not be in the
        // snapshot yet, and a job the server has aged out of its 20-deep
        // history is still worth showing in the run picker.
        const seen = new Set(jobs.map((job) => job.id))
        const orphans = state.jobs.filter((job) => !seen.has(job.id))
        return { jobs: trim([...jobs, ...orphans]) }
      })
      /* Drop the applied-output marks for runs this browser no longer
       * holds — trimmed away here, cleared by the user, or gone with a
       * server that restarted. Nothing will ask about them again, and the
       * map would otherwise grow for the life of the page. */
      const held = new Set(get().jobs.map((job) => job.id))
      for (const id of DISPLAY_SEEN.keys()) {
        if (!held.has(id)) DISPLAY_SEEN.delete(id)
      }
      return
    }

    if (event.type === 'display') {
      /* Keyed by the run the server named. The tab is the fallback for a
       * server one commit behind this build, whose answer is about
       * whichever run the tab should be showing — the assumption this
       * whole store used to be built on. */
      const key = event.job ?? `tab:${event.tab}`
      // Older than what has already been applied for this run — see the
      // note on DISPLAY_SEEN.
      if (event.revision <= (DISPLAY_SEEN.get(key) ?? 0)) return
      const images = mediaOf(event.result)
      /* Counted before the job lookup below and independently of it. A
       * display event with no job to attach to is not a non-event: two
       * browsers open on one pod see one queue, so the run that wrote
       * these files may have been started somewhere this tab never saw.
       * The files are on disk either way, and the gallery lists the
       * disk. */
      const fresh = countNew(images)
      if (fresh > 0) set((state) => ({ mediaRevision: state.mediaRevision + fresh }))
      let applied = false
      set((state) => {
        const target = event.job
          ? state.jobs.find((job) => job.id === event.job)
          : // The old reading, kept only for the older server above:
            // `display_for` pointed at the newest job for that tab that
            // had begun, so this is the same job the server meant.
            state.jobs.find((job) => job.tabKey === event.tab && job.status !== 'queued')
        if (!target) return {}
        applied = true
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
      /* Marked only once it has landed on a row. An event this browser
       * had nowhere to put is not an event it has seen: the row can still
       * arrive (a reconnect replays the queue and the displays, and they
       * are separate events), and a mark set here would turn that replay
       * into a no-op — losing the output for good, because a finished
       * job's revision never moves again. */
      if (applied) DISPLAY_SEEN.set(key, event.revision)
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
    transport: 'off',
    activeByTab: {},
    presetRevision: {},
    mediaRevision: 0,

    connect() {
      let lastEvent = Date.now()
      let stopped = false
      let polling: ReturnType<typeof setInterval> | undefined
      /* One round at a time. `setInterval` does not wait for the previous
       * callback, and a round is a queue request plus one display request
       * per run that has moved — comfortably past POLL_MS on a slow link.
       * Overlapping rounds are how a display answer arrives after a newer
       * one; the revision guard drops it, and this stops it being asked
       * for. */
      let inFlight = false

      DISPLAY_SEEN.clear()

      /* Every event, from either transport, lands here.
       *
       * It is also the only thing that resets the watchdog, which is the
       * point: an event is the one piece of evidence that the path from the
       * server to this tab actually carries bytes. */
      function arrived(event: StreamEvent) {
        lastEvent = Date.now()
        if (get().transport !== 'live') {
          if (polling !== undefined) {
            clearInterval(polling)
            polling = undefined
          }
          set({ transport: 'live' })
        }
        onEvent(event)
      }

      /* One round of the fallback.
       *
       * Two kinds of request, because the two stream events are not
       * interchangeable: `/queue` carries every job's status and no media
       * whatsoever, and the images live only behind
       * `/tabs/{key}/display`. Polling the first and not the second is
       * exactly the half-working state the bug produced — statuses
       * correct, "No images yet" underneath them.
       *
       * The queue answer is applied first on purpose: it is what tells
       * `displaysNeeded` which runs have moved since the last round. */
      async function pollOnce() {
        // `live` as well as `stopped`: the stream coming back mid-round
        // clears the interval, but the requests already out still land, and
        // what they are carrying is by then the older account.
        if (stopped || inFlight || get().transport === 'live') return
        inFlight = true
        try {
          try {
            const snapshot = await api.getQueue()
            if (!stopped) onEvent({ type: 'queue', queue: snapshot })
          } catch {
            return
          }
          for (const [tab, jobId, revision] of displaysNeeded(get().jobs)) {
            if (stopped || get().transport === 'live') return
            try {
              const body = await api.getDisplay(tab, jobId)
              if (body.job) {
                if (!stopped) {
                  onEvent({
                    type: 'display',
                    tab,
                    job: body.job,
                    revision: body.revision,
                    result: body.result,
                  })
                }
              } else {
                /* The server has nothing under that id: the run has aged
                 * out of jobqueue's twenty-deep history, or it never got
                 * there at all. Marked at the revision the request was
                 * decided under so the next round stops asking — without
                 * this the round would repeat forever, once every 1.5
                 * seconds, for as long as the page stayed open. What this
                 * browser already holds for that run is what it keeps. */
                DISPLAY_SEEN.set(jobId, revision)
              }
            } catch {
              /* One run's display failing is not a reason to skip the rest. */
            }
          }
        } finally {
          inFlight = false
        }
      }

      const stop = api.subscribe(arrived)
      set({ connected: true, transport: 'live' })

      // The stream sends everything on connect, but this costs one request
      // and means the page is populated even when the stream turns out to
      // be delivering nothing — which is the whole failure mode below.
      void api
        .getQueue()
        .then((snapshot) => onEvent({ type: 'queue', queue: snapshot }))
        .catch(() => {})

      const watchdog = setInterval(() => {
        const silent = Date.now() - lastEvent > SILENCE_MS
        if (silent && polling === undefined) {
          // Demote, and start polling immediately rather than one interval
          // from now — the user has already waited SILENCE_MS.
          set({ transport: 'polling' })
          void pollOnce()
          polling = setInterval(() => void pollOnce(), POLL_MS)
        }
      }, WATCHDOG_MS)

      return () => {
        stopped = true
        clearInterval(watchdog)
        if (polling !== undefined) clearInterval(polling)
        stop()
        set({ connected: false, transport: 'off' })
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
              revision: 0,
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
              revision: 0,
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

/** Which runs the polling fallback should fetch output for, and the
 *  revision each request is being decided under. Newest first.
 *
 *  One question with an exact answer, where this used to be a heuristic.
 *  jobqueue stamps every change to a job with a revision and the queue
 *  snapshot carries it; `DISPLAY_SEEN` holds the revision whose output
 *  this browser has actually applied. Different means there is something
 *  to collect. That is a running job on every round as its batch grows, a
 *  job that has just settled exactly once, and — once a quiet queue has
 *  caught up — nothing at all, which is fewer requests than the old rule
 *  made as well as more of the right ones.
 *
 *  What it fixes: the old rule asked per *tab*, for the newest run on it,
 *  keyed on `id:status`. One question per tab per round meant that when
 *  one run finished as the next was starting, the round that should have
 *  collected the first one's last picture asked about the second instead,
 *  and the first was never askable again. Asking per run removes the
 *  contention entirely.
 *
 *  Two runs are skipped. A queued one has produced nothing, and asking
 *  costs a request to be told so. One still at revision 0 was never a job
 *  on the server — see submit()'s catch, where a rejected click becomes a
 *  local row — and there is nothing there to ask about, ever.
 */
function displaysNeeded(jobs: Job[]): [string, string, number][] {
  const wanted: [string, string, number][] = []
  for (const job of jobs) {
    if (job.status === 'queued' || job.revision === 0) continue
    if (DISPLAY_SEEN.get(job.id) === job.revision) continue
    wanted.push([job.tabKey, job.id, job.revision])
    if (wanted.length >= MAX_DISPLAY_FETCH) break
  }
  return wanted
}
