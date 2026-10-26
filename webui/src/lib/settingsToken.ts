import type { TabSchema } from '@/api/types'

/*
 * A tab's whole form as one line of text, and back.
 *
 * `ember1.<tabId>.<data>`, where `data` is the JSON below, deflated and
 * base64url-encoded. Deflate because most of a form is repetition — field
 * names, LoRA ids, the boilerplate negative prompt — and base64url because
 * the result has to survive a chat message, a note or a spreadsheet cell as
 * one unbroken word. Both are built into the browser, so this costs no
 * library. Not a JWT: that is the same base64 *without* the compression plus
 * a signature, so it is longer, and the signature would need a secret. A
 * token only fills a form somebody looks at before pressing Generate, so
 * nothing about it needs proving.
 *
 * The tab id sits in the clear so a token says what it is for at a glance,
 * and pasting one into the wrong tab can offer the right one. `ember1` is the
 * format's version: a later shape gets a new prefix and this one still reads.
 *
 * What goes in is what a recipe records (`record` on the field): every
 * control by name, except the publish and save-preset tickboxes and the
 * uploaded pictures, which are files and not settings. The LoRA stack goes in
 * by id rather than by slot, as `[id, weight]` for every LoRA that is
 * switched on, so a token outlives a LoRA being added to the tab's list. A
 * LoRA that is off changes nothing about the picture, and every slot a token
 * does not name loads at its default, which is off — so they stay out. The
 * pod guards the way back in (`TabSchema.token_values`), because only the pod
 * knows its own LoRA list and ranges.
 */

export const TOKEN_PREFIX = 'ember1'

/** A switched-on LoRA. Tokens from before this carried a third element,
 *  `enabled`, and every LoRA; the pod still reads those. */
export type LoraRow = [id: string, weight: number, enabled?: boolean]

export interface TokenData {
  /** The tab's feature key, as `schema.key`. */
  tab: string
  values: Record<string, unknown>
  loras?: LoraRow[]
  /** The preset the form started from, when there was one. */
  preset?: string
  /** What moved off that preset: key -> [preset's value, this value]. A
   *  LoRA is keyed `lora:<id>`, or `LoRA <n>` when the slot changed LoRA. */
  changed?: Record<string, [unknown, unknown]>
}

/** The empty-slot LoRA value, as the schema sends it. */
const NONE = 'None'

function same(a: unknown, b: unknown): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) < 1e-9
  return Object.is(a, b)
}

/** Every control that moved off the preset, as [preset, now].
 *
 *  Only over the keys the preset actually set: a preset leaves the prompt
 *  boxes alone, so they can never have "changed" from it. The seed is left
 *  out while Random seed is on, because then it is not a setting anybody
 *  chose. */
export function changedFromPreset(
  schema: TabSchema,
  values: Record<string, unknown>,
  preset: Record<string, unknown> | null,
): Record<string, [unknown, unknown]> {
  const out: Record<string, [unknown, unknown]> = {}
  if (!preset) return out
  const lora = schema.lora
  const unrecorded = new Set(
    schema.fields.filter((field) => field.record === false).map((field) => field.name),
  )
  for (const [key, was] of Object.entries(preset)) {
    if (lora && key.startsWith(`${lora.key}.`)) continue
    if (unrecorded.has(key)) continue
    if (key === 'seed' && values.randomize === true) continue
    if (!same(values[key], was)) out[key] = [was, values[key]]
  }
  if (!lora) return out
  const slot = (bag: Record<string, unknown>, index: number) => ({
    name: String(bag[`${lora.key}.${index}.name`] ?? NONE),
    on: Boolean(bag[`${lora.key}.${index}.enabled`]),
    weight: Number(bag[`${lora.key}.${index}.weight`] ?? 0),
  })
  for (let index = 0; index < lora.count; index += 1) {
    if (!(`${lora.key}.${index}.name` in preset)) continue
    const was = slot(preset, index)
    const now = slot(values, index)
    if (was.name === now.name) {
      if (now.name === NONE) continue
      if (was.on !== now.on || (now.on && !same(was.weight, now.weight))) {
        out[`lora:${now.name}`] = [was.on ? was.weight : 'off', now.on ? now.weight : 'off']
      }
    } else {
      const say = (row: typeof was) =>
        row.name === NONE ? 'none' : row.on ? `${row.name} @ ${row.weight}` : `${row.name} (off)`
      out[`LoRA ${index + 1}`] = [say(was), say(now)]
    }
  }
  return out
}

/** The form, as the token carries it. */
export function tokenData(
  schema: TabSchema,
  values: Record<string, unknown>,
  preset?: { name: string; values: Record<string, unknown> | null },
): TokenData {
  const kept: Record<string, unknown> = {}
  for (const field of schema.fields) {
    if (field.record === false) continue
    if (field.type === 'image' || field.type === 'images') continue
    const value = values[field.name]
    if (value === undefined || value === null) continue
    kept[field.name] = value
  }
  const data: TokenData = { tab: schema.key, values: kept }
  const lora = schema.lora
  if (lora) {
    const rows: LoraRow[] = []
    for (let index = 0; index < lora.count; index += 1) {
      const name = String(values[`${lora.key}.${index}.name`] ?? NONE)
      if (name === NONE || !values[`${lora.key}.${index}.enabled`]) continue
      rows.push([name, Number(values[`${lora.key}.${index}.weight`] ?? lora.weightDefault)])
    }
    data.loras = rows
  }
  if (preset?.name) {
    data.preset = preset.name
    const changed = changedFromPreset(schema, values, preset.values)
    if (Object.keys(changed).length > 0) data.changed = changed
  }
  return data
}

function toBase64Url(bytes: Uint8Array): string {
  let text = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function fromBase64Url(text: string): Uint8Array<ArrayBuffer> {
  const plain = atob(text.replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(plain, (char) => char.charCodeAt(0))
}

async function pipe(bytes: Uint8Array<ArrayBuffer>, stream: GenericTransformStream): Promise<Uint8Array<ArrayBuffer>> {
  const out = new Blob([bytes]).stream().pipeThrough(stream as TransformStream<Uint8Array, Uint8Array>)
  return new Uint8Array(await new Response(out).arrayBuffer())
}

export async function encodeToken(schema: TabSchema, data: TokenData): Promise<string> {
  const json = new TextEncoder().encode(JSON.stringify(data))
  const packed = await pipe(json, new CompressionStream('deflate-raw'))
  return `${TOKEN_PREFIX}.${schema.tabId}.${toBase64Url(packed)}`
}

export class TokenError extends Error {}

/** A pasted token — or the JSON "Copy JSON" gave — back into its data.
 *
 *  Whitespace anywhere is ignored, because a long token pasted out of a chat
 *  app often arrives wrapped. */
export async function decodeToken(text: string): Promise<TokenData> {
  const trimmed = text.trim()
  let data: unknown
  if (trimmed.startsWith('{')) {
    try {
      data = JSON.parse(trimmed)
    } catch {
      throw new TokenError('That JSON does not parse. Copy it again in full.')
    }
  } else {
    const match = /^ember1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)$/.exec(trimmed.replace(/\s+/g, ''))
    if (!match) {
      throw new TokenError(`That is not a settings token. One starts with ${TOKEN_PREFIX}.`)
    }
    try {
      const json = await pipe(fromBase64Url(match[2]), new DecompressionStream('deflate-raw'))
      data = JSON.parse(new TextDecoder().decode(json))
    } catch {
      throw new TokenError('That token is cut off or damaged. Copy it again in full.')
    }
  }
  const shaped = data as Partial<TokenData> | null
  if (!shaped || typeof shaped.tab !== 'string' || typeof shaped.values !== 'object') {
    throw new TokenError('That token holds no settings.')
  }
  return shaped as TokenData
}

/** One value as a person reads it: a model or LoRA by name, a tick as on/off. */
export function showValue(schema: TabSchema, key: string, value: unknown): string {
  const field = schema.fields.find((candidate) => candidate.name === key)
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  if (field?.choiceLabels && typeof value === 'string') return field.choiceLabels[value] ?? value
  if (key.startsWith('lora:')) return String(value)
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** A key from `changed` as a person reads it — the control's label. */
export function showKey(schema: TabSchema, key: string): string {
  if (key.startsWith('lora:')) {
    const id = key.slice(5)
    return `LoRA ${schema.lora?.choiceLabels?.[id] ?? id}`
  }
  return schema.fields.find((field) => field.name === key)?.label ?? key
}

/** The token's data laid out for reading: labels instead of field names,
 *  names instead of ids, and the preset's changes spelled out. Display only;
 *  "Copy JSON" copies the data itself, which is what loads. */
export function readable(schema: TabSchema, data: TokenData): Record<string, unknown> {
  const settings: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(data.values)) {
    const shown = showValue(schema, key, value)
    settings[showKey(schema, key)] = typeof value === 'number' ? value : shown
  }
  const changed: Record<string, { preset: unknown; now: unknown }> = {}
  for (const [key, [was, now]] of Object.entries(data.changed ?? {})) {
    changed[showKey(schema, key)] = {
      preset: showValue(schema, key, was),
      now: showValue(schema, key, now),
    }
  }
  const names = schema.lora?.choiceLabels ?? {}
  return {
    tab: `${schema.label} (${schema.key})`,
    preset: data.preset
      ? {
          name: data.preset,
          changed_from_preset: Object.keys(changed).length > 0 ? changed : 'nothing',
        }
      : 'none',
    settings,
    loras: (data.loras ?? [])
      .filter(([, , on]) => on !== false)
      .map(([id, weight]) => ({ name: names[id] ?? id, id, strength: weight })),
  }
}
