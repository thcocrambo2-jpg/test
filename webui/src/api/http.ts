import type {
  ApiClient,
  AppCatalog,
  Catalogue,
  DisplayResult,
  GalleryPage,
  PresetList,
  PromptPage,
  PromptQuery,
  QueueSnapshot,
  Session,
  Showcase,
  StoredRecipe,
  StreamEvent,
  SubmitResult,
  SubmitValues,
  TabSchema,
} from './types'

/*
 * The real client. This is the whole of what the app knows about the server.
 *
 * Section 1 wrote this file against a mock so that switching over would be a
 * deletion rather than an excavation, and that is what it turned out to be:
 * `src/mock/` is gone, the ternary in `client.ts` is gone, and no component
 * changed. What did change is here — the endpoint names, and three things the
 * mock could not have taught us.
 *
 *   * **Uploads are their own round trip.** An image goes up once, to
 *     `POST /uploads`, and the submission references it by id. The mask
 *     editor sends a background and one PNG per painted layer, and a batch of
 *     four runs against the same source should not re-send it four times.
 *
 *   * **Progress is one stream for the whole app, not one per job.** The
 *     server's queue is process-wide because there is one GPU behind it, so a
 *     connection per job would be several sockets watching the same object.
 *
 *   * **The positional call happens on the server.** The form sends a flat
 *     value bag and `tabschema.call_args` turns it into the handler's
 *     arguments, checked at import against `inspect.signature`. The browser
 *     no longer builds an argument array, which is one fewer place for a
 *     31-argument signature to be got wrong.
 */

const BASE = import.meta.env.VITE_API_BASE || '/api/v1'

/** How long to wait before rebuilding an EventSource that closed for good,
 *  and the ceiling that doubling walks up to. See `subscribe`. */
const RECONNECT_MIN_MS = 2_000
const RECONNECT_MAX_MS = 30_000

/** The access token, taken out of the URL fragment and traded for a cookie.
 *
 *  It arrives as `https://….trycloudflare.com/#k=<token>`. The fragment is
 *  the point: fragments are never sent to servers, so the token stays out of
 *  Cloudflare's logs, out of every proxy in between and out of `Referer`
 *  headers. A query parameter would be in all three.
 *
 *  Stripped from the address bar immediately afterwards so a screenshot or a
 *  shared URL does not carry it. */
export async function exchangeToken(): Promise<void> {
  const match = /[#&]k=([^&]+)/.exec(window.location.hash)
  if (!match) return
  const token = decodeURIComponent(match[1])
  history.replaceState(null, '', window.location.pathname + window.location.search)
  try {
    await fetch(`${BASE}/auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
  } catch {
    // A failed exchange is not fatal here: every route answers 401 and the
    // shell renders that as one sentence, which is more useful than a blank
    // page thrown from a bootstrap step.
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await errorText(response))
  return (await response.json()) as T
}

async function send<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new Error(await errorText(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function errorText(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (body && typeof body.detail === 'string') return body.detail
  } catch {
    /* not JSON — fall through to the status line */
  }
  if (response.status === 401) return 'This link is missing its access key.'
  return `${response.status} ${response.statusText}`
}

/** Send one blob and get the id the submission references it by. */
async function upload(blob: Blob, name: string): Promise<string> {
  const form = new FormData()
  form.append('file', blob, name)
  const response = await fetch(`${BASE}/uploads`, { method: 'POST', body: form })
  if (!response.ok) throw new Error(await errorText(response))
  const body = (await response.json()) as { id: string }
  return body.id
}

/** Replace every blob in a submission with the id of its upload.
 *
 *  Driven off the schema rather than off the shape of each value: `field.type`
 *  already says which of the three kinds a control is, and guessing from the
 *  value would mean deciding what a `{background, layers}` object is by
 *  looking at it.
 *
 *  The mask contract is the one to be careful with. `{background, layers}` is
 *  the same pair `gr.ImageEditor` produced, and `_prepare_inpaint_inputs`
 *  (ui.py:926, now handlers.py) is unchanged on the other side: it still
 *  takes the union of the layers' alpha channels, dilates, blurs, caps the
 *  long side at 2048 and snaps both images to multiples of 16 for the VAE.
 *  `prepare.ts` in the mask editor is for the preview and the size readout
 *  only. */
async function resolveUploads(
  schema: TabSchema,
  values: SubmitValues,
): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = { ...values }
  for (const field of schema.fields) {
    const value = values[field.name]
    if (field.type === 'image' || field.type === 'file') {
      out[field.name] = value instanceof Blob ? await upload(value, field.name) : null
    } else if (field.type === 'mask') {
      out[field.name] = await uploadMask(value)
    }
  }
  return out
}

async function uploadMask(value: unknown): Promise<unknown> {
  if (!value || typeof value !== 'object') return null
  const editor = value as { background?: Blob; layers?: Blob[] }
  if (!(editor.background instanceof Blob)) return null
  const background = await upload(editor.background, 'background.png')
  const layers: string[] = []
  for (const [index, layer] of (editor.layers ?? []).entries()) {
    if (layer instanceof Blob) layers.push(await upload(layer, `layer-${index}.png`))
  }
  return { background, layers }
}

export const httpClient: ApiClient = {
  getSession: () => get<Session>('/session'),
  getSchemas: () => get<TabSchema[]>('/schemas'),
  getCatalogue: () => get<Catalogue>('/plans'),
  getAppCatalog: () => get<AppCatalog>('/catalog'),
  getShowcase: () => get<Showcase | null>('/showcase'),
  getQueue: () => get<QueueSnapshot>('/queue'),

  getDisplay: (tab, job) =>
    get<{ job: string | null; revision: number; result: DisplayResult }>(
      `/tabs/${encodeURIComponent(tab)}/display${
        job ? `?job=${encodeURIComponent(job)}` : ''
      }`,
    ),
  getPresets: (tab) => get<PresetList>(`/presets/${encodeURIComponent(tab)}`),

  getGallery: (cursor, kind) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    if (kind) params.set('kind', kind)
    const suffix = params.toString()
    return get<GalleryPage>(`/gallery${suffix ? `?${suffix}` : ''}`)
  },

  async deleteMedia(id: string) {
    await send<void>(`/gallery/${encodePathId(id)}`, undefined, 'DELETE')
  },

  /* One request for a whole selection, and a POST rather than a DELETE
   * carrying a body: bodies on DELETE are permitted by the letter of the
   * spec and dropped in practice by enough proxies — there is a Cloudflare
   * tunnel in front of this app — that it is not worth the elegance. */
  deleteMediaMany(ids: string[]) {
    return send<{ deleted: number; failed: string[] }>('/gallery/delete', { ids })
  },

  async submit(schema: TabSchema, values: SubmitValues): Promise<SubmitResult> {
    const resolved = await resolveUploads(schema, values)
    return send<SubmitResult>(`/tabs/${encodeURIComponent(schema.key)}/generate`, {
      values: resolved,
    })
  },

  async cancel(jobId: string) {
    await send<unknown>(`/jobs/${encodeURIComponent(jobId)}/cancel`)
  },

  async clearFinished() {
    await send<unknown>('/jobs/clear')
  },

  async applyPreset(tabKey: string, preset: string) {
    const body = await send<{ values: Record<string, unknown> }>(
      `/schema/${encodeURIComponent(tabKey)}/apply`,
      { preset },
    )
    return body.values
  },

  async applySettings(tabKey: string, settings: Record<string, unknown>) {
    const body = await send<{ values: Record<string, unknown> }>(
      `/schema/${encodeURIComponent(tabKey)}/apply`,
      { settings },
    )
    return body.values
  },

  getPrompts(query: PromptQuery) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    const suffix = params.toString()
    return get<PromptPage>(`/prompts${suffix ? `?${suffix}` : ''}`)
  },

  getRecipe(pathId: string) {
    return get<{ recipe: StoredRecipe | null; canLoad?: boolean }>(
      `/recipe/${encodePathId(pathId)}`,
    )
  },

  async applyRecipe(tabKey: string, pathId: string) {
    const body = await send<{ values: Record<string, unknown> }>(
      `/schema/${encodeURIComponent(tabKey)}/apply`,
      { recipe: pathId },
    )
    return body.values
  },

  /* One EventSource for the whole application.
   *
   * SSE and not websockets, and the reason is the server rather than taste:
   * `jobqueue` is thread-based and pull-oriented with a `revision()` counter,
   * so something polls it either way and a socket would be a second protocol
   * around the same loop. What EventSource brings is reconnection, which is
   * the part a hand-written client always gets wrong, on a tunnel that drops
   * idle connections.
   *
   * Named events rather than one `onmessage`, so a stream that gains a fourth
   * kind does not break the three that exist.
   *
   * EventSource's own retry covers a connection that *drops*. It does not
   * cover one that never opened — a non-2xx first response is terminal by
   * spec, `readyState` goes CLOSED and nothing tries again — which is a
   * frozen page with no way back. So a terminal close schedules its own
   * reconnect, with backoff so a server that is genuinely down is not being
   * hammered by every open tab.
   *
   * What this deliberately does NOT try to solve is a connection that opens
   * and then delivers nothing, which is what a buffering proxy produces. The
   * socket is healthy; reconnecting to it changes nothing. That case belongs
   * to the watchdog in `store/queue.ts`, which watches for *events* rather
   * than for a socket. */
  subscribe(onEvent: (event: StreamEvent) => void) {
    let source: EventSource | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let backoff = RECONNECT_MIN_MS
    let stopped = false

    function open() {
      if (stopped) return
      const stream = new EventSource(`${BASE}/stream`)
      source = stream

      function on<T>(name: string, build: (data: T) => StreamEvent) {
        stream.addEventListener(name, (message) => {
          // A frame that arrives is proof the path works, whatever the
          // last error said, so the next outage starts from the bottom of
          // the backoff again rather than from wherever this one ended.
          backoff = RECONNECT_MIN_MS
          try {
            onEvent(build(JSON.parse((message as MessageEvent).data) as T))
          } catch {
            /* A frame this build cannot read is not a reason to tear the
             * connection down — the next one is probably fine. */
          }
        })
      }

      on<QueueSnapshot>('queue', (data) => ({ type: 'queue', queue: data }))
      on<{ tab: string; job?: string | null; revision: number; result: DisplayResult }>(
        'display',
        (data) => ({
          type: 'display',
          tab: data.tab,
          job: data.job ?? null,
          revision: data.revision,
          result: data.result,
        }),
      )
      on<{ tab: string; revision: number }>('presets', (data) => ({
        type: 'presets',
        tab: data.tab,
        revision: data.revision,
      }))

      stream.onerror = () => {
        // CONNECTING means EventSource is already retrying this one itself,
        // which it does better than this code would. Only a terminal close
        // is ours to answer.
        if (stream.readyState !== EventSource.CLOSED) return
        stream.close()
        if (source === stream) source = null
        if (stopped) return
        timer = setTimeout(open, backoff)
        backoff = Math.min(backoff * 2, RECONNECT_MAX_MS)
      }
    }

    open()
    return () => {
      stopped = true
      if (timer !== undefined) clearTimeout(timer)
      source?.close()
      source = null
    }
  },
}

/** A path_id is an OUTPUT_DIR-relative posix path, so it can contain `/`.
 *  `encodeURIComponent` would escape those into %2F, which the server's
 *  `{path_id:path}` converter then hands back as one segment — correct, but
 *  it also means a proxy that normalises %2F breaks it. Encoding each segment
 *  keeps the slashes as slashes. */
function encodePathId(id: string): string {
  return id.split('/').map(encodeURIComponent).join('/')
}
