import type {
  ApiClient,
  Catalogue,
  GalleryPage,
  JobEvent,
  MediaItem,
  Session,
  Showcase,
  SubmitResult,
  SubmitValues,
  TabSchema,
} from '@/api/types'
import { buildSchemas } from './fromBaseline'
import { argumentCount, toSubmission } from '@/lib/schema'
import { MOCK_CATALOGUE } from './fixtures/plans'
import { makeMedia } from './fixtures/placeholder'
import rawShowcase from './fixtures/showcase.json'

/*
 * The mock API.
 *
 * This module, plus the single `VITE_USE_MOCK` branch in `api/client.ts`, is
 * the whole of the fake. No component imports anything from `src/mock/`, and
 * nothing here is imported anywhere but by that branch — so Section 2 deletes
 * this directory, drops the branch, and the UI does not notice.
 *
 * It is a *behavioural* mock, not a fixture dump: submitting really does
 * produce a job that sits in a queue, starts, emits {step, total} progress at
 * a plausible rate, and finishes with pictures — or fails. That is the only
 * way the progress bar, the queue panel and the error surface get exercised
 * before there is a backend, and those three are the UX defects this rewrite
 * exists to fix.
 */

const SCHEMAS = buildSchemas()

/** A little latency on everything, so the loading skeletons are visible in
 *  dev rather than being dead code nobody ever looks at. */
const LATENCY = 260

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// ------------------------------------------------------------------ session

const SESSION: Session = {
  brand: 'Ember',
  tagline: 'ComfyUI generation suite · RunPod',
  planName: 'Studio',
  expiresAt: new Date(Date.now() + 41 * 864e5).toISOString(),
  outputDir: 'C:\\workspace\\krea2\\output',
  modelCount: 7,
  gpuCount: 1,
  features: MOCK_CATALOGUE.owned,
}

// ------------------------------------------------------------------ gallery

const GALLERY_PROMPTS = [
  'a rain-slick Tokyo alley at 2am, neon reflections, 35mm',
  'portrait of a glassblower, golden hour, shallow depth of field',
  'a brutalist library interior, volumetric light, wide angle',
  'still life with pomegranates and a cracked ceramic bowl',
  'aerial view of terraced rice fields in fog',
  'a vintage racing bicycle against a whitewashed wall',
]

const GALLERY: MediaItem[] = Array.from({ length: 34 }, (_, index) =>
  makeMedia({
    tab: index % 3 === 0 ? 'inpaint' : 'krea2',
    index,
    seed: 42 + index,
    prompt: GALLERY_PROMPTS[index % GALLERY_PROMPTS.length],
    createdAt: new Date(Date.now() - index * 37 * 60000).toISOString(),
  }),
)

const PAGE_SIZE = 12

// ------------------------------------------------------------------ jobs

type Listener = (event: JobEvent) => void
type JobState = 'queued' | 'running' | 'finished'

interface MockJob {
  id: string
  tabKey: string
  values: SubmitValues
  prompt: string
  batch: number
  steps: number
  seed: number
  output: 'image' | 'video'
  state: JobState
  timers: ReturnType<typeof setTimeout>[]
  listeners: Set<Listener>
  /** Events emitted before anyone subscribed, replayed on subscribe so a
   *  job that finishes fast cannot finish into an empty room. */
  backlog: JobEvent[]
  cancelled: boolean
}

const jobs = new Map<string, MockJob>()
let jobCounter = 0

function emit(job: MockJob, event: JobEvent) {
  job.backlog.push(event)
  job.listeners.forEach((listener) => listener(event))
}

function schedule(job: MockJob, ms: number, fn: () => void) {
  job.timers.push(setTimeout(fn, ms))
}

/** One failure in roughly every seven submissions.
 *
 *  Deliberate. Every error in the Gradio app is a `❌ …` string in a textbox
 *  that the next one-second poll overwrites, so nobody ever had to design for
 *  the failed state. Making failure routine in the mock means the alert
 *  surface gets built and looked at rather than assumed. Typing "fail" into a
 *  prompt forces one. */
function failureFor(values: SubmitValues): string | null {
  if (String(values.prompt ?? '').toLowerCase().includes('fail')) {
    return 'ComfyUI rejected the graph: node 14 (KSampler) received an empty conditioning.'
  }
  return Math.random() < 0.14
    ? 'CUDA out of memory — tried to allocate 2.44 GiB. Lower the batch count or the resolution and run it again.'
    : null
}

function start(job: MockJob) {
  job.state = 'running'
  const failure = failureFor(job.values)
  const stepMs = 90 + Math.random() * 130
  const total = job.steps * job.batch

  emit(job, { type: 'status', text: 'Loading the checkpoint' })

  let step = 0
  const tick = () => {
    if (job.cancelled) return
    step += 1
    if (failure && step === Math.max(2, Math.floor(total * 0.4))) {
      emit(job, { type: 'error', message: failure })
      settle(job)
      return
    }
    emit(job, { type: 'progress', step, total })
    if (step === 1) emit(job, { type: 'status', text: 'Sampling' })
    if (step < total) {
      schedule(job, stepMs, tick)
      return
    }
    emit(job, { type: 'status', text: 'Decoding and saving' })
    schedule(job, 420, () => {
      if (job.cancelled) return
      const images = Array.from({ length: job.batch }, (_, index) =>
        makeMedia({
          tab: job.tabKey,
          prompt: job.prompt,
          seed: job.seed + index,
          resolution: job.values.resolution,
          kind: job.output,
        }),
      )
      GALLERY.unshift(...images)
      emit(job, { type: 'done', images, seed: job.seed })
      settle(job)
    })
  }

  schedule(job, 700, tick)
}

function settle(job: MockJob) {
  job.state = 'finished'
  job.timers.forEach(clearTimeout)
  job.timers = []
  pump()
}

/** One job at a time, like the real queue. */
function pump() {
  for (const job of jobs.values()) {
    if (job.state === 'running' && !job.cancelled) return
  }
  for (const job of jobs.values()) {
    if (job.state === 'queued' && !job.cancelled) {
      start(job)
      return
    }
  }
}

function queuePosition(job: MockJob): number {
  let position = 0
  for (const other of jobs.values()) {
    if (other === job) break
    if (!other.cancelled && other.state === 'queued') position += 1
  }
  return position
}

// ------------------------------------------------------------------ client

export const mockClient: ApiClient = {
  async getSession() {
    await delay(LATENCY)
    return SESSION
  },

  async getSchemas(): Promise<TabSchema[]> {
    await delay(LATENCY)
    return SCHEMAS
  },

  async getCatalogue(): Promise<Catalogue> {
    await delay(LATENCY + 180)
    return MOCK_CATALOGUE
  },

  async getShowcase(): Promise<Showcase | null> {
    await delay(LATENCY)
    return rawShowcase as unknown as Showcase
  },

  async getGallery(cursor: string | null): Promise<GalleryPage> {
    await delay(LATENCY)
    const start_ = cursor ? Number(cursor) : 0
    const items = GALLERY.slice(start_, start_ + PAGE_SIZE)
    const next = start_ + PAGE_SIZE
    return {
      items,
      nextCursor: next < GALLERY.length ? String(next) : null,
      total: GALLERY.length,
    }
  },

  async deleteMedia(id: string) {
    await delay(180)
    const index = GALLERY.findIndex((item) => item.id === id)
    if (index >= 0) GALLERY.splice(index, 1)
  },

  async submit(tabKey: string, values: SubmitValues): Promise<SubmitResult> {
    const schema = SCHEMAS.find((tab) => tab.key === tabKey)
    if (!schema) throw new Error(`Unknown tab: ${tabKey}`)

    // Built and then thrown away, but built on purpose: it is the one thing a
    // mock can genuinely verify — that the form produces an argument list of
    // the length the Python handler declares. A field renamed in tabMeta.ts or
    // a LoRA shape misread shows up here, not in Section 2.
    const args = toSubmission(schema, values)
    const expected = argumentCount(schema)
    if (args.length !== expected) {
      throw new Error(
        `${tabKey}: the form built ${args.length} arguments, the handler takes ${expected}.`,
      )
    }

    await delay(140)
    jobCounter += 1
    const id = `job-${jobCounter}`
    const job: MockJob = {
      id,
      tabKey,
      values,
      prompt: String(values.prompt ?? values.json_text ?? ''),
      batch: Math.max(1, Number(values.batch_count ?? 1)),
      steps: Math.max(1, Number(values.steps ?? 8)),
      seed:
        values.randomize === true
          ? Math.floor(Math.random() * 2 ** 32)
          : Number(values.seed ?? 42),
      output: schema.output,
      state: 'queued',
      timers: [],
      listeners: new Set(),
      backlog: [],
      cancelled: false,
    }
    jobs.set(id, job)
    emit(job, { type: 'queued', position: queuePosition(job) })
    pump()
    return { jobId: id }
  },

  async cancel(jobId: string) {
    const job = jobs.get(jobId)
    if (!job || job.state === 'finished') return
    job.cancelled = true
    job.timers.forEach(clearTimeout)
    job.timers = []
    job.state = 'finished'
    job.listeners.forEach((listener) => listener({ type: 'error', message: 'Cancelled.' }))
    pump()
  },

  subscribe(jobId: string, onEvent: (event: JobEvent) => void) {
    const job = jobs.get(jobId)
    if (!job) return () => {}
    // Replay first: a job that finished between submit() and this call must
    // not leave the panel stuck on "queued".
    job.backlog.forEach(onEvent)
    job.listeners.add(onEvent)
    return () => {
      job.listeners.delete(onEvent)
    }
  },
}
