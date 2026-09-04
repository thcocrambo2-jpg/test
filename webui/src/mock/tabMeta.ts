import type { FieldColumn, FieldCondition, TabCategory } from '@/api/types'

/*
 * Presentation metadata — the half of a tab that `parity_baseline.json` does
 * not describe.
 *
 * The baseline owns labels, types, defaults, ranges and choices, and this file
 * must never contradict it: no `label` overrides live here, deliberately, so a
 * reworded label in the Python app can never be masked by a hand-written one
 * in the React app. What lives here is grouping, column and conditional
 * visibility — decisions about layout, which the Gradio code had scattered
 * through 5,389 lines of nested gr.Row/gr.Column.
 *
 * Fields not named in `fields` fall through to { column: 'left' } with no
 * group, so a control added on the Python side shows up rather than vanishing.
 */

export interface FieldMeta {
  column?: FieldColumn
  group?: string
  hint?: string
  showIf?: FieldCondition
  wide?: boolean
  accept?: string
}

export type GroupRenderer = 'default' | 'seed' | 'sampler' | 'variance'

export interface GroupMeta {
  id: string
  title?: string
  /** Which component renders it. See GROUP_RENDERERS in SchemaForm. */
  renderer?: GroupRenderer
  collapsible?: boolean
  defaultOpen?: boolean
  /** Lay the group's fields out two-up where they fit. */
  dense?: boolean
  column?: FieldColumn
}

export interface TabMeta {
  key: string
  handler: string
  label: string
  icon: string
  blurb: string
  category: TabCategory
  route: string
  output: 'image' | 'video'
  submitLabel: string
  /** Stage A ships two tabs wired end to end; the rest carry a derived
   *  schema and a placeholder page until Stage B. */
  ready: boolean
  groups: GroupMeta[]
  fields: Record<string, FieldMeta>
}

const SEED_GROUP: GroupMeta = { id: 'seed', title: 'Seed & batch', renderer: 'seed' }

const SAVE_GROUP: GroupMeta = {
  id: 'save',
  title: 'Publish & presets',
  collapsible: true,
  defaultOpen: false,
}

/** seed / randomize / batch_count are byte-identical on eight tabs. One
 *  SeedRow renders all eight; this is the assignment that routes them to it. */
const seedFields = {
  seed: { group: 'seed' },
  randomize: { group: 'seed' },
  batch_count: { group: 'seed' },
} satisfies Record<string, FieldMeta>

const saveFields = {
  publish: { group: 'save' },
  publish_title: { group: 'save', showIf: { field: 'publish', equals: true } },
  save_preset: { group: 'save' },
  preset_name: { group: 'save', showIf: { field: 'save_preset', equals: true } },
} satisfies Record<string, FieldMeta>

const promptFields = {
  prompt: { group: 'prompt' },
  negative: { group: 'prompt' },
} satisfies Record<string, FieldMeta>

/** The ClownsharKSampler and variance blocks, duplicated between V2 and
 *  V2 Edit in ui.py. Declared once here, referenced twice below. */
const V2_SAMPLER_FIELDS = [
  'eta',
  'sampler_name',
  'scheduler',
  'steps',
  'denoise',
  'cfg',
  'sampler_mode',
  'bongmath',
]

const V2_VARIANCE_FIELDS = [
  'variance_preset',
  'fine_tune_variance',
  'variance_model_type',
  'variance_schedule',
  'cutoff_step',
  'total_steps',
  'cutoff_strength',
  'shift_strength',
]

function assign(names: string[], meta: FieldMeta): Record<string, FieldMeta> {
  return Object.fromEntries(names.map((name) => [name, meta]))
}

const KLEIN_SCALE_MODE = 'Scale image 1 to megapixels'
const KLEIN_CUSTOM_MODE = 'Custom width × height'
const WAN_14B = '14B two-expert (best quality, 16 fps)'

export const TAB_META: TabMeta[] = [
  {
    key: 'krea_t2i',
    handler: 'generate_single',
    label: 'Krea2',
    icon: '\u{1F3A8}',
    blurb: 'Type a sentence, get a photograph.',
    category: 'generate',
    route: '/generate/krea2',
    output: 'image',
    submitLabel: 'Generate',
    ready: true,
    groups: [
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Output', dense: true },
      SEED_GROUP,
      SAVE_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      ...saveFields,
      resolution: { group: 'core', wide: true },
      model: { group: 'core', wide: true },
      steps: { group: 'core' },
      cfg: { group: 'core', hint: 'Above 1 turns the negative prompt on.' },
      sampler: { group: 'core', wide: true },
    },
  },

  {
    key: 'krea_inpaint',
    handler: 'generate_inpaint',
    label: 'Inpaint',
    icon: '\u{1F58C}️',
    blurb: 'Paint over what should change. Leave the rest alone.',
    category: 'edit',
    route: '/edit/inpaint',
    output: 'image',
    submitLabel: 'Inpaint',
    ready: true,
    groups: [
      { id: 'canvas', column: 'right' },
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Sampling', dense: true },
      { id: 'mask', title: 'Mask shaping', dense: true },
      SEED_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      // The canvas is the work surface, not a sidebar control — so it takes
      // the wide column and the results stack under it. Being able to say
      // that in the schema rather than in the layout is the point of `column`.
      editor_value: { group: 'canvas', column: 'right' },
      steps: { group: 'core' },
      cfg: { group: 'core', hint: 'Above 1 turns the negative prompt on.' },
      denoise: { group: 'core', wide: true },
      sampler: { group: 'core', wide: true },
      model: { group: 'core', wide: true },
      grow: { group: 'mask', hint: 'Dilates the painted region before blurring.' },
      blur: { group: 'mask', hint: 'Softens the edge so the seam disappears.' },
    },
  },

  {
    key: 'krea_v2_t2i',
    handler: 'generate_v2',
    label: 'Krea2 V2',
    icon: '\u{1F536}',
    blurb: 'The V2 pipeline, with the full ClownsharKSampler stack.',
    category: 'generate',
    route: '/generate/krea2-v2',
    output: 'image',
    submitLabel: 'Generate',
    ready: false,
    groups: [
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Output', dense: true },
      // Defect fix: in ui.py these sat in the *output* column, unlike every
      // other tab. They are controls, so they go with the controls.
      {
        id: 'sampler',
        title: 'Sampler',
        renderer: 'sampler',
        collapsible: true,
        defaultOpen: true,
      },
      {
        id: 'variance',
        title: 'Variance',
        renderer: 'variance',
        collapsible: true,
        defaultOpen: false,
      },
      {
        id: 'post',
        title: 'Post-processing',
        dense: true,
        collapsible: true,
        defaultOpen: false,
      },
      SEED_GROUP,
      SAVE_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      ...saveFields,
      model: { group: 'core', wide: true },
      aspect: { group: 'core', wide: true },
      megapixels: { group: 'core' },
      multiple: { group: 'core' },
      ...assign(V2_SAMPLER_FIELDS, { group: 'sampler', column: 'left' }),
      ...assign(V2_VARIANCE_FIELDS, { group: 'variance', column: 'left' }),
      sharpen: { group: 'post', wide: true },
      film_grain: { group: 'post', wide: true },
    },
  },

  {
    key: 'krea_edit',
    handler: 'generate_edit',
    label: 'Krea2 Edit',
    icon: '✨',
    blurb: 'Change one thing about a picture without touching the rest.',
    category: 'edit',
    route: '/edit/krea2-edit',
    output: 'image',
    submitLabel: 'Edit',
    ready: false,
    groups: [
      { id: 'inputs', title: 'Images', column: 'right' },
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Sampling', dense: true },
      SEED_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      image: { group: 'inputs', column: 'right' },
      use_image2: { group: 'inputs', column: 'right' },
      image2: {
        group: 'inputs',
        column: 'right',
        showIf: { field: 'use_image2', equals: true },
      },
      steps: { group: 'core' },
      cfg: { group: 'core' },
      sampler: { group: 'core', wide: true },
      grounding: { group: 'core', wide: true },
      ref_boost: { group: 'core', wide: true },
      ref_boost_a: { group: 'core', showIf: { field: 'ref_boost', equals: true } },
      model: { group: 'core', wide: true },
    },
  },

  {
    key: 'krea_v2_edit',
    handler: 'generate_v2_edit',
    label: 'Krea2 V2 Edit',
    icon: '\u{1F537}',
    blurb: 'Instruction editing on the V2 pipeline.',
    category: 'edit',
    route: '/edit/krea2-v2-edit',
    output: 'image',
    submitLabel: 'Edit',
    ready: false,
    groups: [
      { id: 'inputs', title: 'Images', column: 'right' },
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Output', dense: true },
      {
        id: 'sampler',
        title: 'Sampler',
        renderer: 'sampler',
        collapsible: true,
        defaultOpen: true,
      },
      {
        id: 'variance',
        title: 'Variance',
        renderer: 'variance',
        collapsible: true,
        defaultOpen: false,
      },
      SEED_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      image: { group: 'inputs', column: 'right' },
      use_image2: { group: 'inputs', column: 'right' },
      image2: {
        group: 'inputs',
        column: 'right',
        showIf: { field: 'use_image2', equals: true },
      },
      model: { group: 'core', wide: true },
      grounding: { group: 'core', wide: true },
      ref_boost: { group: 'core', wide: true },
      ref_boost_a: { group: 'core', showIf: { field: 'ref_boost', equals: true } },
      fit_mode: { group: 'core', wide: true },
      ...assign(V2_SAMPLER_FIELDS, { group: 'sampler', column: 'left' }),
      ...assign(V2_VARIANCE_FIELDS, { group: 'variance', column: 'left' }),
    },
  },

  {
    key: 'flux_t2i',
    handler: 'generate_flux',
    label: 'Flux2D',
    icon: '\u{1F30A}',
    blurb: 'The Flux pipeline, guidance-driven.',
    category: 'generate',
    route: '/generate/flux',
    output: 'image',
    submitLabel: 'Generate',
    ready: false,
    groups: [
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Output', dense: true },
      SEED_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      steps: { group: 'core' },
      guidance: { group: 'core' },
      resolution: { group: 'core', wide: true },
      sampler: { group: 'core', wide: true },
      model: { group: 'core', wide: true },
    },
  },

  {
    key: 'klein_i2i',
    handler: 'generate_klein_edit',
    label: 'Klein Edit',
    icon: '\u{1F9E9}',
    blurb: 'Flux 2 Klein, with up to two reference images.',
    category: 'edit',
    route: '/edit/klein',
    output: 'image',
    submitLabel: 'Edit',
    ready: false,
    groups: [
      { id: 'inputs', title: 'Images', column: 'right' },
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Sampling', dense: true },
      { id: 'size', title: 'Output size', dense: true },
      SEED_GROUP,
    ],
    fields: {
      ...seedFields,
      prompt: { group: 'prompt' },
      image: { group: 'inputs', column: 'right' },
      use_image2: { group: 'inputs', column: 'right' },
      image2: {
        group: 'inputs',
        column: 'right',
        showIf: { field: 'use_image2', equals: true },
      },
      model: { group: 'core', wide: true },
      steps: { group: 'core' },
      cfg: { group: 'core' },
      guidance: { group: 'core' },
      sampler: { group: 'core' },
      scheduler: { group: 'core' },
      reference_mp: { group: 'size', wide: true },
      output_mode: { group: 'size', wide: true },
      output_mp: { group: 'size', wide: true, showIf: { field: 'output_mode', equals: KLEIN_SCALE_MODE } },
      custom_width: { group: 'size', showIf: { field: 'output_mode', equals: KLEIN_CUSTOM_MODE } },
      custom_height: { group: 'size', showIf: { field: 'output_mode', equals: KLEIN_CUSTOM_MODE } },
    },
  },

  {
    key: 'faceswap',
    handler: 'generate_faceswap',
    label: 'Face Swap',
    icon: '\u{1F3AD}',
    blurb: 'Put one face into another photograph.',
    category: 'edit',
    route: '/edit/faceswap',
    output: 'image',
    submitLabel: 'Swap',
    ready: false,
    groups: [
      { id: 'inputs', title: 'Images', column: 'right' },
      { id: 'core', title: 'Detection', dense: true },
      { id: 'restore', title: 'Restoration', dense: true },
    ],
    fields: {
      base_image: { group: 'inputs', column: 'right' },
      face_image: { group: 'inputs', column: 'right' },
      swap_model: { group: 'core', wide: true },
      facedetection: { group: 'core', wide: true },
      input_index: { group: 'core' },
      source_index: { group: 'core' },
      restore_model: { group: 'restore', wide: true },
      restore_visibility: { group: 'restore', wide: true },
      codeformer_weight: { group: 'restore', wide: true },
    },
  },

  {
    key: 'wan_i2v',
    handler: 'generate_wan_video',
    label: 'Wan Video',
    icon: '\u{1F3AC}',
    blurb: 'Turn a still into a few seconds of video.',
    category: 'video',
    route: '/video/wan',
    output: 'video',
    submitLabel: 'Animate',
    ready: false,
    groups: [
      { id: 'inputs', title: 'Start frame', column: 'right' },
      { id: 'prompt', title: 'Prompt' },
      { id: 'core', title: 'Model', dense: true },
      { id: 'sampling', title: 'Sampling', dense: true },
      SEED_GROUP,
    ],
    fields: {
      ...promptFields,
      ...seedFields,
      image: { group: 'inputs', column: 'right' },
      model: { group: 'core', wide: true },
      mode: { group: 'core', wide: true, showIf: { field: 'model', equals: WAN_14B } },
      resolution: { group: 'core', wide: true },
      seconds: { group: 'sampling', wide: true },
      steps: { group: 'sampling' },
      cfg: { group: 'sampling' },
      sampler: { group: 'sampling', wide: true },
    },
  },

  {
    key: 'json_batch',
    handler: 'generate_from_json',
    label: 'Krea2 Batch',
    icon: '\u{1F4E6}',
    blurb: 'Run a JSON array of jobs straight through the graph.',
    category: 'library',
    route: '/library/batch',
    output: 'image',
    submitLabel: 'Run batch',
    ready: false,
    groups: [{ id: 'core', title: 'Batch source' }],
    fields: {
      json_file: { group: 'core', wide: true, accept: 'application/json,.json' },
      json_text: { group: 'core', wide: true },
    },
  },
]

export const TAB_META_BY_KEY: Record<string, TabMeta> = Object.fromEntries(
  TAB_META.map((meta) => [meta.key, meta]),
)

/** Tabs with no form at all — bespoke pages, not schema-driven. Listed here
 *  so the navigation can be built from one list. */
export interface BespokeTab {
  key: string
  label: string
  icon: string
  category: TabCategory
  route: string
  ready: boolean
}

export const BESPOKE_TABS: BespokeTab[] = [
  {
    key: 'gallery',
    label: 'Gallery',
    icon: '\u{1F5BC}️',
    category: 'library',
    route: '/library/gallery',
    ready: true,
  },
  {
    key: 'community_prompts',
    label: 'Prompt Library',
    icon: '\u{1F31F}',
    category: 'library',
    route: '/library/prompts',
    ready: false,
  },
]

export const CATEGORY_LABEL: Record<TabCategory, string> = {
  generate: 'Generate',
  edit: 'Edit',
  video: 'Video',
  library: 'Library',
}

export const CATEGORY_ORDER: TabCategory[] = ['generate', 'edit', 'video', 'library']
