import type { Field, FieldType, LoraSpec, TabSchema } from '@/api/types'
import { TAB_META, type FieldMeta, type TabMeta } from './tabMeta'

// The parity contract itself, not a copy of it. `vite.config.ts` opens
// `server.fs.allow: ['..']` so the dev server can read it, and rollup inlines
// it at build time. Importing the real file means the forms cannot drift from
// `scripts/parity.py --check`: if the baseline is regenerated, the UI changes.
import rawBaseline from '../../../scripts/parity_baseline.json'

// --------------------------------------------------------------- raw shapes

interface RawControl {
  type: string
  label?: string | null
  value?: unknown
  minimum?: number | null
  maximum?: number | null
  step?: number | null
  /** Gradio normalises choices to [label, value] pairs. */
  choices?: [string, string][] | null
  lines?: number | null
  placeholder?: string | null
  visible?: boolean
  elem_id?: string | null
}

interface RawTab {
  tab_id: string
  controls: RawControl[]
}

interface RawHandler {
  positional: string[]
  varargs: string | null
  keyword_only: string[]
}

interface RawBaseline {
  version: number
  tabs: Record<string, RawTab>
  handlers: Record<string, RawHandler>
}

const baseline = rawBaseline as unknown as RawBaseline

// --------------------------------------------------------------- mapping

const TYPE_MAP: Record<string, FieldType> = {
  Textbox: 'text',
  Number: 'number',
  Slider: 'slider',
  Dropdown: 'select',
  Radio: 'radio',
  Checkbox: 'bool',
  Image: 'image',
  ImageEditor: 'mask',
  File: 'file',
}

function fieldType(control: RawControl): FieldType {
  const mapped = TYPE_MAP[control.type]
  if (!mapped) {
    throw new Error(
      `parity_baseline.json has a control type this UI does not render: ` +
        `${control.type} (${control.label ?? 'unlabelled'})`,
    )
  }
  // A Textbox with more than one line is a different control to the eye even
  // though Gradio calls both "Textbox".
  if (mapped === 'text' && (control.lines ?? 1) > 1) return 'textarea'
  return mapped
}

function choicesOf(control: RawControl): string[] | undefined {
  if (!control.choices) return undefined
  return control.choices.map((choice) =>
    Array.isArray(choice) ? String(choice[1] ?? choice[0]) : String(choice),
  )
}

/** The default a control submits when nobody touches it.
 *
 *  Gradio serialises "no value" as null for a Textbox, and the handler then
 *  receives an empty string. Reproducing that here keeps the submitted array
 *  identical for an untouched form, which is exactly what parity means. */
function defaultValue(control: RawControl, type: FieldType): unknown {
  const raw = control.value
  switch (type) {
    case 'text':
    case 'textarea':
      return typeof raw === 'string' ? raw : ''
    case 'bool':
      return raw === true
    case 'number':
    case 'slider':
      return typeof raw === 'number' ? raw : (control.minimum ?? 0)
    case 'select':
    case 'radio': {
      if (typeof raw === 'string') return raw
      const choices = choicesOf(control)
      return choices?.[0] ?? ''
    }
    default:
      return null
  }
}

function toField(control: RawControl, name: string, meta: FieldMeta | undefined): Field {
  const type = fieldType(control)
  const field: Field = {
    name,
    type,
    // Verbatim from the baseline. Never overridden in tabMeta.ts — a label
    // that disagrees with the Python app is the exact drift parity.py exists
    // to catch, and it would be pointless for this file to reintroduce it.
    label: control.label ?? name,
    default: defaultValue(control, type),
    column: meta?.column ?? 'left',
  }
  if (meta?.group) field.group = meta.group
  if (control.minimum != null) field.min = control.minimum
  if (control.maximum != null) field.max = control.maximum
  if (control.step != null) field.step = control.step
  const choices = choicesOf(control)
  if (choices) field.choices = choices
  if (control.lines != null) field.lines = control.lines
  if (control.placeholder) field.placeholder = control.placeholder
  if (meta?.hint) field.hint = meta.hint
  if (meta?.showIf) field.showIf = meta.showIf
  if (meta?.wide) field.wide = meta.wide
  if (meta?.accept) field.accept = meta.accept
  return field
}

// --------------------------------------------------------------- LoRA tail

/** Read the LoRA stack off the tail of a tab's controls.
 *
 *  Everything past the handler's named positional parameters belongs to the
 *  `*lora_slots` varargs, and its shape is legible from the controls
 *  themselves: a tail that starts with a Checkbox is the Power-Lora-Loader
 *  triple `(enabled, name, weight)`; anything else is the plain pair
 *  `(name, weight)`. Both exist in this app and the difference shifts every
 *  argument after it — see context.md §4.3.
 */
function deriveLora(tail: RawControl[]): LoraSpec | null {
  if (tail.length === 0) return null

  const triple = tail[0].type === 'Checkbox' && tail.length % 3 === 0
  const stride = triple ? 3 : 2
  if (tail.length % stride !== 0) {
    throw new Error(
      `LoRA tail of ${tail.length} controls does not divide into ` +
        `${triple ? 'triples' : 'pairs'} — the handler signature and the ` +
        `baseline disagree.`,
    )
  }

  const dropdown = tail[triple ? 1 : 0]
  const weight = tail[triple ? 2 : 1]
  const enabled = triple ? tail[0] : null

  return {
    shape: triple ? 'triple' : 'pair',
    count: tail.length / stride,
    choices: choicesOf(dropdown) ?? ['None'],
    // "LoRA 1" → "LoRA"; the slot number is supplied by the renderer.
    slotLabel: (dropdown.label ?? 'LoRA').replace(/\s*\d+\s*$/, ''),
    // "Weight" on the pair tabs, "Strength" on the triple ones. Kept apart
    // because they are different words in the app people already use.
    weightLabel: weight.label ?? 'Weight',
    weightMin: weight.minimum ?? 0,
    weightMax: weight.maximum ?? 2,
    weightStep: weight.step ?? 0.05,
    weightDefault: typeof weight.value === 'number' ? weight.value : 0.8,
    enabledDefault: enabled?.value === true,
  }
}

// --------------------------------------------------------------- assembly

function buildTab(meta: TabMeta): TabSchema {
  const tab = baseline.tabs[meta.key]
  if (!tab) throw new Error(`parity_baseline.json has no tab "${meta.key}"`)
  const handler = baseline.handlers[meta.handler]
  if (!handler) throw new Error(`parity_baseline.json has no handler "${meta.handler}"`)

  const names = handler.positional
  const head = tab.controls.slice(0, names.length)
  const tail = tab.controls.slice(names.length)

  if (head.length !== names.length) {
    throw new Error(
      `${meta.key}: ${names.length} positional parameters but only ` +
        `${head.length} controls — the baseline is stale.`,
    )
  }
  if (tail.length > 0 && !handler.varargs) {
    throw new Error(
      `${meta.key}: ${tail.length} controls past the end of a handler with ` +
        `no *varargs tail.`,
    )
  }

  // Field order is control order is positional-argument order. Rendering
  // reorders freely by group; submission never does. See toSubmission().
  const fields = head.map((control, index) =>
    toField(control, names[index], meta.fields[names[index]]),
  )

  return {
    key: meta.key,
    tabId: tab.tab_id,
    handler: meta.handler,
    label: meta.label,
    icon: meta.icon,
    blurb: meta.blurb,
    category: meta.category,
    route: meta.route,
    output: meta.output,
    fields,
    groups: meta.groups,
    lora: deriveLora(tail),
    ready: meta.ready,
    submitLabel: meta.submitLabel,
  }
}

let cached: TabSchema[] | null = null

/** Every tab's schema, derived from the parity baseline. */
export function buildSchemas(): TabSchema[] {
  if (!cached) cached = TAB_META.map(buildTab)
  return cached
}
