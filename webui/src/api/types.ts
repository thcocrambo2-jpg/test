/*
 * The wire contract.
 *
 * Everything the UI knows about the server is in this file. `api/client.ts`
 * picks an implementation of `ApiClient` — the mock in `src/mock/`, or the
 * real one — and no component ever learns which it got. Section 2 replaces
 * the implementation; these types are what it has to satisfy.
 */

// --------------------------------------------------------------- schema

export type FieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'slider'
  | 'select'
  | 'radio'
  | 'bool'
  | 'image'
  | 'mask'
  | 'file'

/** Which column a field lands in.
 *
 *  This is a property of the *schema*, not of the layout code — which is the
 *  fix for the defect where V2 and V2 Edit put Steps/CFG/Sampler in the output
 *  column and the other eight tabs did not. The layout renders what the schema
 *  says; moving a control is a one-word edit in `tabMeta.ts`. */
export type FieldColumn = 'left' | 'right'

/** Show this field only when another field holds a given value. Gradio did
 *  this with `.change()` handlers wiring `gr.update(visible=…)`; here it is
 *  declarative and lives with the field it governs. */
export interface FieldCondition {
  field: string
  equals: unknown
}

export interface Field {
  /** Stable key. Taken from the Python handler's parameter name, so the
   *  submission array and the handler signature cannot disagree. */
  name: string
  type: FieldType
  label: string
  default: unknown
  column: FieldColumn
  /** Group id — see `GROUP_RENDERERS` in SchemaForm. Ungrouped fields render
   *  in order in the column's main body. */
  group?: string
  min?: number
  max?: number
  step?: number
  choices?: string[]
  lines?: number
  placeholder?: string
  /** Sub-label shown under the control. Not from the baseline — ours. */
  hint?: string
  showIf?: FieldCondition
  /** Full-width inside a two-up group row. */
  wide?: boolean
  accept?: string
}

/** How a tab's LoRA tail is shaped.
 *
 *  Two shapes exist and the difference is real (context.md §4.3): `pair`
 *  slots submit (name, weight); `triple` slots submit (enabled, name, weight)
 *  because the Power-Lora-Loader tabs carry a per-row on/off checkbox.
 *  Getting this wrong shifts every argument after it. */
export interface LoraSpec {
  shape: 'pair' | 'triple'
  count: number
  choices: string[]
  slotLabel: string
  weightLabel: string
  weightMin: number
  weightMax: number
  weightStep: number
  weightDefault: number
  enabledDefault: boolean
}

export type TabCategory = 'generate' | 'edit' | 'video' | 'library'

/** How a tab's fields are grouped for the eye.
 *
 *  Part of the schema, not of the layout code: `SchemaForm` renders what it is
 *  given, and moving a control between groups (or columns) is a schema edit.
 *  Section 2 serves these from `tabschema.py`. */
export interface GroupSpec {
  id: string
  title?: string
  /** Which component renders the group's body. */
  renderer?: 'default' | 'seed' | 'sampler' | 'variance'
  collapsible?: boolean
  defaultOpen?: boolean
  /** Two-up where the fields fit. */
  dense?: boolean
}

export interface TabSchema {
  /** Feature key — the same string `features.enabled()` gates on. */
  key: string
  /** Gradio `elem_id`-ish tab id from the baseline, kept for traceability. */
  tabId: string
  /** Python handler this tab submits to. */
  handler: string
  label: string
  icon: string
  blurb: string
  category: TabCategory
  route: string
  output: 'image' | 'video'
  /** Submission order. Never reorder: index i is positional arg i. */
  fields: Field[]
  groups: GroupSpec[]
  lora: LoraSpec | null
  /** False while a tab is still Stage B. The schema is derived either way. */
  ready: boolean
  submitLabel: string
}

// --------------------------------------------------------------- session

export interface Session {
  brand: string
  tagline: string
  planName: string | null
  expiresAt: string | null
  outputDir: string
  modelCount: number
  gpuCount: number
  /** Feature keys this licence grants. The nav renders from this. */
  features: string[]
}

// --------------------------------------------------------------- jobs

export interface MediaItem {
  id: string
  url: string
  thumbUrl?: string
  /** Path on the machine that produced it — the thing people scp. */
  path: string
  width: number
  height: number
  kind: 'image' | 'video'
  createdAt: string
  seed?: number
  prompt?: string
  tab?: string
}

export type JobEvent =
  | { type: 'queued'; position: number }
  | { type: 'status'; text: string }
  /** Mirrors client.py's {"type":"progress","step","total"} exactly.
   *  `preview` is the decoded latent frame — unused today (context.md §4.12),
   *  wired here so turning it on later is a backend-only change. */
  | { type: 'progress'; step: number; total: number; preview?: string }
  | { type: 'done'; images: MediaItem[]; seed?: number }
  | { type: 'error'; message: string }

export interface SubmitResult {
  jobId: string
}

/** What the form hands the client. Blobs are the image/mask payloads. */
export type SubmitValues = Record<string, unknown>

// --------------------------------------------------------------- pricing

export interface Cycle {
  id: string
  label: string
  months: number
  discount_percent: number
}

export interface CyclePrice {
  cycle: string
  months: number
  total: number
  per_month: number
  discount_percent: number
  saving: number
}

export interface Plan {
  id: string
  name: string
  description: string | null
  price_monthly: number | null
  currency: string
  features: string[]
  sort_order: number
  prices: Record<string, CyclePrice>
  is_popular: boolean
}

export interface FeatureInfo {
  key: string
  name: string
  description: string
  category: string
}

export interface Catalogue {
  plans: Plan[]
  features: Record<string, FeatureInfo>
  cycles: Cycle[]
  error: string | null
  contact_url: string | null
  /** Keys the running licence already grants — the page badges these. */
  owned: string[]
}

// --------------------------------------------------------------- showcase

export interface ShowcaseMedia {
  path: string
  url: string | null
  label: string | null
  caption: string | null
  ratio: string | null
  is_video: boolean
  accent: boolean
  hue: number
}

export interface ShowcaseBlock {
  type: string
  items: ShowcaseMedia[]
  source: ShowcaseMedia | null
  blocks: ShowcaseBlock[]
  title: string | null
  body: string | null
  note: string | null
  caption: string | null
  reverse: boolean
  flat: boolean
}

export interface ShowcaseSection {
  key: string
  label: string
  headline: string
  body: string[]
  highlights: string[]
  blocks: ShowcaseBlock[]
  locked: boolean
}

export interface ShowcaseStat {
  value: string
  label: string
}

export interface Showcase {
  eyebrow: string
  title: string
  title_accent: string
  body: string[]
  stats: ShowcaseStat[]
  sections: ShowcaseSection[]
  cta_title: string | null
  cta_body: string | null
}

// --------------------------------------------------------------- gallery

export interface GalleryPage {
  items: MediaItem[]
  nextCursor: string | null
  total: number
}

// --------------------------------------------------------------- client

export interface ApiClient {
  getSession(): Promise<Session>
  getSchemas(): Promise<TabSchema[]>
  getCatalogue(): Promise<Catalogue>
  getShowcase(): Promise<Showcase | null>
  getGallery(cursor: string | null): Promise<GalleryPage>
  deleteMedia(id: string): Promise<void>
  submit(tabKey: string, values: SubmitValues): Promise<SubmitResult>
  cancel(jobId: string): Promise<void>
  /** Server-sent events for one job. Returns an unsubscribe. */
  subscribe(jobId: string, onEvent: (event: JobEvent) => void): () => void
}
