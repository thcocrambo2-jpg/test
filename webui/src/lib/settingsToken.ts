import type { TabSchema } from '@/api/types'
import { decode as fromBase32768, encode as toBase32768 } from 'base32768'
import { saveBlob } from '@/lib/util'

/*
 * A tab's whole form as one line of text, and back.
 *
 * `ember2.<tabId>.<data>;`, where `data` is the JSON below, deflated and
 * base32768-encoded. Deflate because most of a form is repetition — field
 * names, LoRA ids, the boilerplate negative prompt — and it is built into
 * the browser. Not a JWT: that is base64 *without* the compression plus a
 * signature, so it is longer, and the signature would need a secret. A
 * token only fills a form somebody looks at before pressing Generate, so
 * nothing about it needs proving.
 *
 * base32768 because tokens travel through Telegram, which counts a message
 * in characters and splits anything over 4096 into several. base32768 packs
 * 15 bits into each character where base64url packs 6, so the same deflated
 * bytes take 2.5x fewer characters: a form carrying 13,000 characters of
 * prompt and negative is a 3,269-character token rather than an
 * 8,147-character one. Its characters are CJK-looking letters, so it is
 * longer in *bytes* — that does not matter to a chat app, but keep it out of
 * anything sized in bytes. It also has no `_`, `*`, `~` or backtick, which
 * Telegram reads as formatting: an `ember1` token lost every `_` that
 * happened to pair up into "italics", and deflate does not survive one
 * missing character.
 *
 * The tab id sits in the clear so a token says what it is for at a glance,
 * and pasting one into the wrong tab can offer the right one. `ember2` is the
 * format's version. `ember1` — the same JSON, deflated, in base64url — still
 * reads; a later shape gets a new prefix and these two keep reading.
 *
 * The `;` closes it. Neither alphabet has a `;`, so wherever a token is
 * kept — a spreadsheet cell with a note, a chat message — the token is
 * exactly `ember2.` up to the first `;`, and anything glued on after it,
 * even with no space, is plainly not part of it.
 *
 * What goes in is what a recipe records (`record` on the field): every
 * control by name, except the save-preset tickbox and its name and the
 * uploaded pictures, which are files and not settings. The LoRA stack goes in
 * by id rather than by slot, as `[id, weight]` for every LoRA that is
 * switched on, so a token outlives a LoRA being added to the tab's list. A
 * LoRA that is off changes nothing about the picture, and every slot a token
 * does not name loads at its default, which is off — so they stay out. The
 * pod guards the way back in (`TabSchema.token_values`), because only the pod
 * knows its own LoRA list and ranges.
 */

export const TOKEN_PREFIX = 'ember2'

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

/** `ember1` data, which still loads. */
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
  return `${TOKEN_PREFIX}.${schema.tabId}.${toBase32768(packed)};`
}

/** Telegram splits a message longer than this, in characters. */
export const MESSAGE_LIMIT = 4096

/* A token as a file, for when one message will not hold it.
 *
 * Telegram takes a .txt as a document: never split, never read as
 * formatting. The file holds the token and nothing else, so opening it and
 * copying everything gives a token that pastes straight back. */

/** `ember-<parts>.txt`, each part cut down to what a filename can hold. */
export function tokenFileName(...parts: (string | undefined)[]): string {
  const slug = parts
    .filter(Boolean)
    .map((part) => part!.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''))
    .filter(Boolean)
  return `ember-${slug.join('-')}.txt`
}

function tokenFile(token: string, name: string): File {
  return new File([`${token}\n`], name, { type: 'text/plain' })
}

export function saveToken(token: string, name: string): void {
  saveBlob(tokenFile(token, name), name)
}

let shareable: boolean | undefined

/** Whether this browser can hand a file to the system share sheet.
 *
 *  Only in a secure context — HTTPS or localhost — so a pod reached over
 *  plain HTTP says no, and Download is the way there. */
export function canShareFiles(): boolean {
  if (shareable === undefined) {
    try {
      shareable =
        typeof navigator.canShare === 'function' &&
        navigator.canShare({ files: [tokenFile('', 'token.txt')] })
    } catch {
      shareable = false
    }
  }
  return shareable
}

/** Open the share sheet with the token as a file. False when it could not
 *  open; backing out of the sheet is not a failure.
 *
 *  Call it straight from the click, with the token already built: Safari
 *  only opens the sheet while the tap still counts as the user's, and an
 *  await before this — a fetch, a deflate — can use that up. */
export async function shareToken(token: string, name: string): Promise<boolean> {
  try {
    await navigator.share({ files: [tokenFile(token, name)] })
    return true
  } catch (error) {
    return error instanceof DOMException && error.name === 'AbortError'
  }
}

export class TokenError extends Error {}

/* A token found inside other text.
 *
 * With its closing `;`, a token is `ember2.` up to that `;`, and whitespace
 * inside that stretch is dropped, so one a chat app wrapped over lines is
 * whole again. Everything outside the stretch is somebody's note.
 *
 * Without the `;` — copied short, or typed out — it ends at the first
 * character its alphabet does not use: a space, a line break, a quote, a
 * full stop. No whitespace is rejoined then, or the next word of a note
 * would be glued onto the data. base32768 draws only on blocks from U+0180
 * to U+A85F, so its class leaves out all of ASCII, Latin-1's « » and é, and
 * the punctuation blocks (curly quotes, the ellipsis, the CJK full stop) —
 * everything a note is likely to put straight after a token.
 *
 * Both readings are offered, closed first, because a token without its `;`
 * may still have one further on, in the note. The 16-character floor keeps a
 * prompt that merely mentions "ember2." from counting. */
const START = /ember[12]\./
const DATA = {
  ember1: '[A-Za-z0-9_-]',
  ember2: '[\\u0180-\\u1fff\\u2070-\\u2fff\\u3040-\\ua8ff]',
}
type Version = keyof typeof DATA
const CLOSED = Object.fromEntries(
  Object.entries(DATA).map(([v, c]) => [v, new RegExp(`^${v}\\.([A-Za-z0-9_-]+)\\.(${c}{16,})$`)]),
) as Record<Version, RegExp>
const OPEN = Object.fromEntries(
  Object.entries(DATA).map(([v, c]) => [v, new RegExp(`^${v}\\.([A-Za-z0-9_-]+)\\.(${c}{16,})`)]),
) as Record<Version, RegExp>

interface Candidate {
  version: Version
  data: string
  closed: boolean
  /** Whether anything but whitespace sits outside the token. */
  extra: boolean
}

function candidates(text: string): Candidate[] {
  const found = START.exec(text)
  if (!found) return []
  const start = found.index
  const version = found[0].slice(0, -1) as Version
  const out: Candidate[] = []
  const before = text.slice(0, start)
  const semi = text.indexOf(';', start)
  if (semi >= 0) {
    const match = CLOSED[version].exec(text.slice(start, semi).replace(/\s+/g, ''))
    if (match) {
      out.push({ version, data: match[2], closed: true, extra: (before + text.slice(semi + 1)).trim() !== '' })
    }
  }
  const match = OPEN[version].exec(text.slice(start))
  if (match) {
    out.push({
      version,
      data: match[2],
      closed: false,
      extra: (before + text.slice(start + match[0].length)).trim() !== '',
    })
  }
  return out
}

/** Whether `text` holds a settings token anywhere in it.
 *
 *  What the Prompt box asks of a paste or a Replace, and Generate of the
 *  prompt, before treating it as settings rather than as words. */
export function hasToken(text: string): boolean {
  return candidates(text).length > 0
}

/** A pasted token — or the JSON "Copy JSON" gave — back into its data, and
 *  whether there was other text around it that was left out.
 *
 *  The token may sit anywhere in the text: "my note ember2.…; more notes"
 *  loads the token and drops the rest, since the token carries its own
 *  prompt. */
export async function readToken(text: string): Promise<{ data: TokenData; extra: boolean }> {
  const trimmed = text.trim()
  let data: unknown
  let extra = false
  if (trimmed.startsWith('{')) {
    try {
      data = JSON.parse(trimmed)
    } catch {
      throw new TokenError('That JSON does not parse. Copy it again in full.')
    }
  } else {
    const found = candidates(trimmed)
    if (found.length === 0) {
      throw new TokenError(`That is not a settings token. One starts with ${TOKEN_PREFIX}.`)
    }
    for (const candidate of found) {
      try {
        const bytes = candidate.version === 'ember1' ? fromBase64Url(candidate.data) : fromBase32768(candidate.data)
        const json = await pipe(bytes, new DecompressionStream('deflate-raw'))
        data = JSON.parse(new TextDecoder().decode(json))
        extra = candidate.extra
        break
      } catch {
        /* the next reading, if there is one */
      }
    }
    if (data === undefined) {
      // Without its `;`, letters stuck straight onto the end read as part of
      // the token, and that is the likelier cause than damage.
      throw new TokenError(
        found.some((candidate) => candidate.closed)
          ? 'That token is cut off or damaged. Copy it again in full.'
          : 'That token is cut off, or has text stuck to its end. Copy it again with its closing ;',
      )
    }
  }
  const shaped = data as Partial<TokenData> | null
  if (!shaped || typeof shaped.tab !== 'string' || typeof shaped.values !== 'object') {
    throw new TokenError('That token holds no settings.')
  }
  return { data: shaped as TokenData, extra }
}

export async function decodeToken(text: string): Promise<TokenData> {
  return (await readToken(text)).data
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
