import type {
  ApiClient,
  Catalogue,
  GalleryPage,
  JobEvent,
  Session,
  Showcase,
  SubmitResult,
  SubmitValues,
  TabSchema,
} from './types'

/*
 * The real client — the thing `serve.py` answers in Section 2.
 *
 * It is written now, against the same `ApiClient` interface the mock
 * implements, so that switching over is a flag and not a refactor. Nothing in
 * here is exercised while VITE_USE_MOCK=1; what it is for is to pin down the
 * endpoint shapes the FastAPI adapter has to provide, in the same file the
 * mock's behaviour was specified against.
 */

const BASE = import.meta.env.VITE_API_BASE || '/api/v1'

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(await errorText(response))
  return (await response.json()) as T
}

async function errorText(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (body && typeof body.detail === 'string') return body.detail
  } catch {
    /* not JSON — fall through to the status line */
  }
  return `${response.status} ${response.statusText}`
}

/** Split the form values into a JSON part and the blobs.
 *
 *  Images and masks go up as multipart because they are megabytes and base64
 *  in a JSON body would inflate them by a third for no reason. Everything
 *  else rides in one `values` field so the server sees exactly the object the
 *  form produced. */
function toFormData(values: SubmitValues): FormData {
  const form = new FormData()
  const plain: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values)) {
    if (value instanceof Blob) {
      form.append(`file:${key}`, value, key)
    } else if (Array.isArray(value) && value.every((item) => item instanceof Blob)) {
      value.forEach((blob, index) => form.append(`file:${key}[${index}]`, blob, `${key}-${index}`))
    } else {
      plain[key] = value
    }
  }
  form.append('values', JSON.stringify(plain))
  return form
}

export const httpClient: ApiClient = {
  getSession: () => get<Session>('/session'),
  getSchemas: () => get<TabSchema[]>('/schemas'),
  getCatalogue: () => get<Catalogue>('/plans'),
  getShowcase: () => get<Showcase | null>('/showcase'),
  getGallery: (cursor) =>
    get<GalleryPage>(`/gallery${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),

  async deleteMedia(id: string) {
    const response = await fetch(`${BASE}/gallery/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw new Error(await errorText(response))
  },

  async submit(tabKey: string, values: SubmitValues): Promise<SubmitResult> {
    const response = await fetch(`${BASE}/tabs/${encodeURIComponent(tabKey)}/run`, {
      method: 'POST',
      body: toFormData(values),
    })
    if (!response.ok) throw new Error(await errorText(response))
    return (await response.json()) as SubmitResult
  },

  async cancel(jobId: string) {
    const response = await fetch(`${BASE}/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error(await errorText(response))
  },

  /** SSE, because client.py:270 already yields exactly these dicts — the
   *  stream is a re-encoding of a generator that exists, not a new protocol. */
  subscribe(jobId: string, onEvent: (event: JobEvent) => void) {
    const source = new EventSource(`${BASE}/jobs/${encodeURIComponent(jobId)}/events`)
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data) as JobEvent)
      } catch {
        onEvent({ type: 'error', message: 'The server sent an event this app could not read.' })
      }
    }
    source.onerror = () => {
      // EventSource retries on its own; a socket that closes after `done` is
      // ordinary, so only a stream that never delivered anything is an error
      // worth showing — the queue store decides that, not this transport.
      source.close()
    }
    return () => source.close()
  },
}
