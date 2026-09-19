/*
 * The wire contract.
 *
 * Everything the UI knows about the server is in this file. `api/client.ts`
 * `api/client.ts` re-exports the one implementation of `ApiClient`, and no
 * component knows anything about the server beyond this file. Section 1 wrote
 * these types against a mock; Section 2 deleted the mock and left them
 * standing, which is what they were for.
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

/** Which column a field lands in.
 *
 *  This is a property of the *schema*, not of the layout code — which is the
 *  fix for the defect where V2 and V2 Edit put Steps/CFG/Sampler in the output
 *  column and the other five tabs did not. The layout renders what the schema
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
  /** The values a select or radio submits, in display order. */
  choices?: string[]
  /** What to *show* for each value, when that is not the value itself.
   *
   *  The Model dropdown submits a model id (`krea2-turbo-mxfp8`) because
   *  presets, prompts and recipes all store ids — a display name can be
   *  renamed in the DB and a file can be shared by two model records, so
   *  neither is a key. Nobody wants to pick from a list of slugs, though, so
   *  the pod sends the name alongside. Absent on every field whose values
   *  are already readable, and a value with no entry shows as itself. */
  choiceLabels?: Record<string, string>
  lines?: number
  placeholder?: string
  /** Sub-label shown under the control. Not from the baseline — ours. */
  hint?: string
  showIf?: FieldCondition
  /** Full-width inside a two-up group row. */
  wide?: boolean
  /** Folds away entirely rather than showing its first rows. The negative
   *  prompt wears this: two of the tabs default it to ~1,300 characters of
   *  boilerplate, and a couple of rows of that is a couple of rows of noise
   *  above the box anybody actually came for. */
  collapsed?: boolean
}

/** How a tab's LoRA tail is shaped.
 *
 *  Every stack submits triples (enabled, name, weight), because each row
 *  carries a per-row on/off checkbox (context.md §4.3). Getting the order
 *  wrong shifts every argument after it. The `name` part is a LoRA *id*
 *  from the licence server's catalogue, not a file name, or `"None"` for
 *  an empty slot. */
export interface LoraSpec {
  shape: 'triple'
  count: number
  /** The prefix values are sent under: `lora.0.weight`. */
  key: string
  /** The stack's heading, verbatim from ui.py (it names the folder). */
  title: string
  /** Submission order *within* one slot. `call_args` on the Python side
   *  flattens slots x parts, so this is the thing that must not be
   *  reordered — see context.md 4.3. */
  parts: string[]
  /** LoRA ids this tab's feature offers, in the catalogue's order, with
   *  `"None"` first. Not a listing of the pod's disk: a LoRA the catalogue
   *  lists is offered whether or not its file has arrived yet. */
  choices: string[]
  /** The LoRA names to show for those ids — same meaning as
   *  `Field.choiceLabels`. `"None"` is labelled `"None"`. */
  choiceLabels?: Record<string, string>
  slotLabel: string
  weightLabel: string
  weightMin: number
  weightMax: number
  weightStep: number
  weightDefault: number
  enabledLabel: string
  enabledDefault: boolean
  /** Per-slot defaults. Krea2 and Krea2 Edit have eight blank rows. The V2
   *  tabs have one row per LoRA in their feature's list, in list order,
   *  switched off, at that LoRA's default strength — so `count` is however
   *  many LoRAs the feature lists, and the tab's Default preset is what
   *  switches the usual ones on. */
  slots: Record<string, unknown>[]
}

export type TabCategory = 'generate' | 'edit' | 'video' | 'library'

/** How a tab's fields are grouped for the eye.
 *
 *  Part of the schema, not of the layout code: `SchemaForm` renders what it is
 *  given, and moving a control between groups (or columns) is a schema edit.
 *  Section 2 serves these from `tabschema.py`. */
export interface GroupSpec {
  id: string
  title: string
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
  ready: boolean
  submitLabel: string
  /** Which field names the job in the queue. */
  promptField: string
  /** What this tab's handler yields, position by position. */
  resultKeys: string[]
  /** The presets tab this tab's dropdown reads, or null. A preset is
   *  saved from a generation tab and belongs to it, but is applied
   *  wherever those dials exist — which includes the matching Edit tab. */
  presetTab: string | null
  presetNote: string
  /** Which list in `AppCatalog.models` this tab's Model dropdown offers —
   *  the tab's own feature key, since each feature's models are a list in
   *  the licence server's `feature_assets` — or null for a tab with no
   *  model control. It is what lets the browser reproduce
   *  krea_model_changed and its three siblings locally. */
  modelRegistry: string | null
}

/** One model a tab offers, flattened for the browser.
 *
 *  `id` is what the Model dropdown submits and what every stored blob
 *  names; `name` is only its label. Rows are matched by id and never by
 *  name or file: two records may share one file (the same weights at
 *  different steps and CFG), and a name is free text somebody can edit.
 *
 *  `defaults` is per-family: (steps, cfg) for Krea2 and Krea2 Edit,
 *  (steps, cfg, turbo_lora) for the V2 tabs. The families genuinely differ
 *  and pretending otherwise would mean guessing which of the two a row
 *  means. */
export interface ModelRow {
  id: string
  name: string
  file: string | null
  variant: string
  trigger: string
  defaults: Record<string, number | boolean | null>
  /** Whether the weights are on this pod's disk. Only the server knows. */
  available: boolean
  /** The markdown line the Gradio app printed under the dropdown. */
  info: string
}

/** Everything the forms need that is not a field: each feature's models, keyed
 *  by feature key, and the per-variant defaults the ~150 lines of `*_changed` handlers in ui.py
 *  were made of. Served whole so React applies them with no round trip. */
export interface AppCatalog {
  tabs: TabSchema[]
  models: Record<string, ModelRow[]>
  wan: {
    modes: Record<string, Record<string, number | boolean>>
    fiveB: Record<string, number>
    fps: number
    maxSeconds: number
    /** The model whose radio has no turbo/raw split — the 5B has no
     *  Lightning distillation, so Mode does not apply to it. */
    modeless: string
    resolutions: Record<string, number>
  }
  resolutions: Record<string, number[]>
  samplers: string[]
}

/** A field this app renders but the schema declares with `allowCustom`.
 *  RES4LYF builds its sampler and scheduler lists at load time, so a name
 *  this build does not list is still one the node may accept. */
export interface PresetRow {
  id: string
  name: string
  description: string | null
  isDefault: boolean
}

export interface PresetList {
  presets: PresetRow[]
  default: string | null
  error: string | null
  revision: number
}

// --------------------------------------------------------------- session

export interface Session {
  brand: string
  tagline: string
  planName: string | null
  expiresAt: string | null
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

export interface QueueJob {
  id: string
  lane: string
  tab: string
  tabLabel: string
  title: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  /** The handler's own status line. */
  progress: string
  /** Parsed out of that line by the server, so the bar can be
   *  determinate. See api._progress_pair for why it is a bridge. */
  step: { step: number; total: number } | null
  /** Set when the line is a refusal or the handler raised. A handler that
   *  refuses yields a cross-prefixed line and returns normally, so the job
   *  is DONE and still a failure — see api._failure. */
  error: string | null
  submitted: number
  place: number
  /** jobqueue's own counter, stamped on this job's last change. The same
   *  one `display` carries, so a reader can tell whether the output it
   *  holds for the job is the output the job has now. */
  revision: number
}

export interface QueueSnapshot {
  revision: number
  waiting: number
  running: number
  jobs: QueueJob[]
}

/** One tab's latest yield, keyed by that tab's `resultKeys`. */
export interface DisplayResult {
  images?: MediaItem[]
  videos?: MediaItem[]
  latest?: MediaItem
  status?: string
  seed?: number
}

/** What GET /api/v1/stream sends. One connection for the whole app. */
export type StreamEvent =
  | { type: 'queue'; queue: QueueSnapshot }
  | {
      type: 'display'
      tab: string
      /** The run this output belongs to. Carried so output is attached to
       *  the job that made it rather than guessed from the tab — see the
       *  note on DISPLAY_SEEN in store/queue.ts. Null only from a server
       *  that predates it, or when the tab has produced nothing. */
      job: string | null
      revision: number
      result: DisplayResult
    }
  | { type: 'presets'; tab: string; revision: number }

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

/** What the form hands the client. Blobs are the image payloads. */
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

// --------------------------------------------------------------- prompts

/** One card in the prompt library, as prompts.Prompt sends it. */
export interface PromptCard {
  id: string
  tab: string
  source: 'admin' | 'community'
  title: string | null
  prompt: string
  negative: string
  /** The same blob a preset carries — the dials without the words. Opaque
   *  here: it is guarded and unpacked by `POST /schema/{tab}/apply`, because
   *  the stored shape is not the form's — an empty LoRA slot is null there
   *  and `"None"` here, and a V2 row is found by its LoRA id — and tabschema
   *  already owns that mapping. */
  settings: Record<string, unknown>
}

export interface PromptPage {
  prompts: PromptCard[]
  total: number
  skip: number
  limit: number
  error: string | null
}

export interface PromptQuery {
  tab?: string
  source?: string
  search?: string
  skip?: number
  limit?: number
}

// --------------------------------------------------------------- gallery

export interface GalleryPage {
  items: MediaItem[]
  nextCursor: string | null
  total: number
}

/** A recipe as recipes.py stores it: the tab it came from, the seed the
 *  picture actually ran on, and `[[label, value], ...]` in that tab's own
 *  control order. Positional, and it has to be — a LoRA stack is eight
 *  controls all labelled "Weight". */
export interface StoredRecipe {
  tab: string
  tab_label: string
  tab_id: string
  seed: number | null
  fields: [string, unknown][]
  at?: number
}

// --------------------------------------------------------------- client

export interface ApiClient {
  getSession(): Promise<Session>
  getSchemas(): Promise<TabSchema[]>
  getCatalogue(): Promise<Catalogue>
  getAppCatalog(): Promise<AppCatalog>
  getShowcase(): Promise<Showcase | null>
  /** One page of the listing. `kind` narrows it: 'image' lists stills
   *  only, which is what the reuse strip under an upload field wants — a
   *  page of the whole listing taken on a video tab is mostly clips, and
   *  an image input cannot take one. */
  getGallery(cursor: string | null, kind?: 'image'): Promise<GalleryPage>
  deleteMedia(id: string): Promise<void>
  submit(schema: TabSchema, values: SubmitValues): Promise<SubmitResult>
  cancel(jobId: string): Promise<void>
  clearFinished(): Promise<void>
  getQueue(): Promise<QueueSnapshot>
  /** Delete a selection in one request. Partial success is normal — the
   *  ids that would not go come back in `failed`. */
  deleteMediaMany(ids: string[]): Promise<{ deleted: number; failed: string[] }>
  /** One run's latest yield, over plain HTTP. The `display` stream event's
   *  twin, and the half of the polling fallback that carries images —
   *  `getQueue` carries statuses and no media at all.
   *
   *  Naming a `job` asks for that run in particular. Without one the answer
   *  is about whichever run the tab should be showing, which is the wrong
   *  run for collecting the last picture of the one before it. */
  getDisplay(
    tab: string,
    job?: string,
  ): Promise<{ job: string | null; revision: number; result: DisplayResult }>
  getPresets(tab: string): Promise<PresetList>
  /** A preset name or a stored recipe -> the values it is safe to write
   *  into this tab. Server-side, because guarding a value means knowing
   *  which model and LoRA ids this tab's feature offers, and turning the
   *  stored blob into form values is tabschema's job (see PromptCard). */
  applyPreset(tabKey: string, preset: string): Promise<Record<string, unknown>>
  applyRecipe(tabKey: string, pathId: string): Promise<Record<string, unknown>>
  applySettings(tabKey: string, settings: Record<string, unknown>): Promise<Record<string, unknown>>
  getPrompts(query: PromptQuery): Promise<PromptPage>
  getRecipe(pathId: string): Promise<{ recipe: StoredRecipe | null; canLoad?: boolean }>
  /** One SSE connection for the whole app. Returns an unsubscribe. */
  subscribe(onEvent: (event: StreamEvent) => void): () => void
}
