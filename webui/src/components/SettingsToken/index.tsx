import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { MediaItem, TabSchema } from '@/api/types'
import { presetValuesKey } from '@/components/PresetBar'
import { Button, Pill, Segmented, useToast } from '@/components/ui'
import { defaultsFor } from '@/lib/schema'
import {
  readToken,
  encodeToken,
  readable,
  tokenData,
  TokenError,
  TOKEN_PREFIX,
  MESSAGE_LIMIT,
  canShareFiles,
  saveToken,
  shareToken,
  tokenFileName,
  type TokenData,
} from '@/lib/settingsToken'
import { DownloadIcon, ShareIcon } from '@/features/gallery/icons'
import { copyText, cx, fileName, useCopy } from '@/lib/util'
import { useHandoff } from '@/store/handoff'
import { readTabState, useTabState, writeTabState } from '@/store/tabState'
import f from '@/components/SchemaForm/form.module.css'
import s from './token.module.css'

/*
 * The last group on every generate tab: the whole form as one line, and a
 * box to paste one back.
 *
 * The token is built from the live form, so it is always "what Generate
 * would run right now". A finished picture has its own token too — the
 * "Copy settings" button on the output tiles and in the lightbox — built
 * from the recipe filed beside it, with the seed it actually ran on.
 *
 * Loading goes through the pod (`TabSchema.token_values`) and then the
 * handoff store, which is exactly the Gallery's "Load these settings": it
 * replaces the whole form rather than merging into it — all but an uploaded
 * picture, which a token cannot carry — and works the same whether the
 * token is for this tab or another one.
 */

type View = 'readable' | 'raw'

export interface Outcome {
  tone: 'ok' | 'warn' | 'error'
  title: string
  notes: string[]
}

/** Where a tab keeps the last load's result. Per tab and outside the
 *  component, because a token for another tab is loaded *from* this one and
 *  its result belongs to the panel of the tab it went to. */
const outcomeKey = (tabKey: string) => `token.outcome.${tabKey}`
const openKey = (tabKey: string) => `token.open.${tabKey}`

export function SettingsToken({
  schema,
  values,
}: {
  schema: TabSchema
  values: Record<string, unknown>
}) {
  const [open, setOpen] = useTabState(openKey(schema.key), false)
  const [view, setView] = useTabState<View>('token.view', 'readable')
  const [chosen] = useTabState(`preset.chosen.${schema.key}`, '')
  const [baseline] = useTabState<Record<string, unknown> | null>(
    presetValuesKey(schema.key),
    null,
  )
  const [pasted, setPasted] = useTabState(`token.pasted.${schema.key}`, '')
  const [token, setToken] = useState('')
  const [outcome, setOutcome] = useTabState<Outcome | null>(outcomeKey(schema.key), null)
  const [loading, setLoading] = useState(false)
  const [elsewhere, setElsewhere] = useState<{
    schema: TabSchema
    data: TokenData
    extra: boolean
  } | null>(null)
  const tokenCopy = useCopy()
  const jsonCopy = useCopy()
  const toast = useToast()
  const load = useTokenLoader()
  const { data: schemas } = useSchemas()

  const data = useMemo(
    () => tokenData(schema, values, chosen ? { name: chosen, values: baseline } : undefined),
    [schema, values, chosen, baseline],
  )
  const changes = Object.keys(data.changed ?? {}).length
  const shareable = canShareFiles()

  /** `ember-krea2v2-test-1-2026-10-28-1542.txt`: the tab, the preset, and
   *  when, so a folder of them sorts and says what each one is. */
  function fileFor() {
    const now = new Date()
    const two = (n: number) => String(n).padStart(2, '0')
    const stamp = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`
    return tokenFileName(schema.tabId, chosen, stamp)
  }

  // Encoding is async (CompressionStream) and a slider drag is a stream of
  // changes, so it waits for the form to settle — and only while open.
  useEffect(() => {
    if (!open) return
    let live = true
    const timer = setTimeout(() => {
      void encodeToken(schema, data).then((next) => {
        if (live) setToken(next)
      })
    }, 150)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [open, schema, data])

  async function loadPasted() {
    setOutcome(null)
    setElsewhere(null)
    let decoded: TokenData
    let extra = false
    try {
      ;({ data: decoded, extra } = await readToken(pasted))
    } catch (error) {
      setOutcome({
        tone: 'error',
        title: error instanceof TokenError ? error.message : String(error),
        notes: [],
      })
      return
    }
    if (decoded.tab !== schema.key) {
      const target = schemas?.find((candidate) => candidate.key === decoded.tab)
      if (!target) {
        setOutcome({
          tone: 'error',
          title: 'That token is for a tab this licence does not include.',
          notes: [],
        })
        return
      }
      setElsewhere({ schema: target, data: decoded, extra })
      return
    }
    await run(schema, decoded, extra)
  }

  async function run(target: TabSchema, decoded: TokenData, extra: boolean) {
    setLoading(true)
    try {
      await load(target, decoded, extra)
      if (target.key !== schema.key) setPasted('')
    } catch (error) {
      setOutcome({
        tone: 'error',
        title: error instanceof Error ? error.message : String(error),
        notes: [],
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className={f.group}>
      <button
        type="button"
        className={cx(f.groupHead, f.groupHeadButton)}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span>Settings token</span>
        <span className={s.headRight}>
          {chosen && (
            <Pill tone={changes > 0 ? 'accent' : 'default'}>
              {chosen}
              {changes > 0 ? ` · ${changes} changed` : ''}
            </Pill>
          )}
          <span className={cx(f.groupCaret, open && f.groupCaretOpen)} aria-hidden>
            ▶
          </span>
        </span>
      </button>

      {open && (
        <div className={cx(f.groupBody, s.body)}>
          <div className={s.block}>
            <div className={s.label}>This tab right now</div>
            <div className={s.token} aria-live="polite">
              {token ? (
                <>
                  <span className={s.prefix}>{token.slice(0, token.indexOf('.', 7) + 1)}</span>
                  {token.slice(token.indexOf('.', 7) + 1)}
                </>
              ) : (
                '…'
              )}
            </div>
            {/* Two halves so a phone can stack them: sending the token, then
              * the rest (token.module.css). */}
            <div className={s.actions}>
              <div className={s.send}>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!token}
                  onClick={() => {
                    void tokenCopy.copy(token).then((landed) => {
                      if (!landed) toastFail()
                    })
                  }}
                >
                  {tokenCopy.copied ? 'Copied' : 'Copy token'}
                </Button>
                <Button
                  size="sm"
                  disabled={!token}
                  onClick={() => saveToken(token, fileFor())}
                  title="Save the token as a .txt file"
                >
                  <DownloadIcon />
                  Download
                </Button>
                {shareable && (
                  <Button
                    size="sm"
                    disabled={!token}
                    onClick={() => {
                      void shareToken(token, fileFor()).then((opened) => {
                        if (!opened) toast('Could not open the share sheet.')
                      })
                    }}
                    title="Send the token as a .txt file"
                  >
                    <ShareIcon />
                    Share…
                  </Button>
                )}
              </div>
              <span className={s.sep} aria-hidden />
              <div className={s.rest}>
                <Button
                  size="sm"
                  onClick={() => {
                    void jsonCopy.copy(JSON.stringify(data, null, 2)).then((landed) => {
                      if (!landed) toastFail()
                    })
                  }}
                >
                  {jsonCopy.copied ? 'Copied' : 'Copy JSON'}
                </Button>
                <span className={s.count}>{token.length.toLocaleString()} characters</span>
              </div>
            </div>
            {token.length > MESSAGE_LIMIT && (
              <div className={s.long}>
                <strong>Longer than one Telegram message ({MESSAGE_LIMIT.toLocaleString()}).</strong>{' '}
                {shareable ? 'Share or Download sends' : 'Download it and send'} it as a file
                instead, which stays whole.
              </div>
            )}
            <Segmented<View>
              value={view}
              onChange={setView}
              ariaLabel="How to show the settings"
              options={[
                { value: 'readable', label: 'Readable' },
                { value: 'raw', label: 'As stored' },
              ]}
            />
            <pre className={s.json}>
              {JSON.stringify(view === 'readable' ? readable(schema, data) : data, null, 2)}
            </pre>
          </div>

          <div className={s.block}>
            <label className={s.label} htmlFor={`token-paste-${schema.key}`}>
              Load settings
            </label>
            <textarea
              id={`token-paste-${schema.key}`}
              className={s.paste}
              rows={3}
              spellCheck={false}
              placeholder={`Paste a token (${TOKEN_PREFIX}.${schema.tabId}.…) or its JSON`}
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
            />
            <div className={s.row}>
              <Button
                size="sm"
                variant="primary"
                loading={loading}
                disabled={!pasted.trim()}
                onClick={() => void loadPasted()}
              >
                Load into this tab
              </Button>
            </div>
            {elsewhere && (
              <div className={cx(s.outcome, s.warn)}>
                <strong>
                  That token is for {elsewhere.schema.icon} {elsewhere.schema.label}.
                </strong>
                <div className={s.row}>
                  <Button
                    size="sm"
                    loading={loading}
                    onClick={() => {
                      const target = elsewhere
                      setElsewhere(null)
                      void run(target.schema, target.data, target.extra)
                    }}
                  >
                    Open {elsewhere.schema.label} and load it
                  </Button>
                </div>
              </div>
            )}
            {outcome && (
              <div className={cx(s.outcome, s[outcome.tone])}>
                <strong>{outcome.title}</strong>
                {outcome.notes.length > 0 && (
                  <ul>
                    {outcome.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            <p className={s.hint}>
              Loading replaces the whole form with the token's settings, except an
              uploaded image, which a token cannot carry and so stays. Random seed is
              switched on; untick it to make the exact same image again.
            </p>
          </div>
        </div>
      )}
    </section>
  )

  function toastFail() {
    toast('Could not reach the clipboard.')
  }
}

/** The state a token load writes for one tab, so Undo can put it back.
 *
 *  The form, the preset dropdown and its baseline, whether the default
 *  preset has landed, and the panel's last result. A slot that held nothing
 *  goes back to holding nothing, which reads as its default. */
export function snapshotTab(tabKey: string): [string, unknown][] {
  return [
    `form.${tabKey}`,
    `preset.chosen.${tabKey}`,
    presetValuesKey(tabKey),
    `preset.applied.${tabKey}`,
    outcomeKey(tabKey),
  ].map((key) => [key, readTabState<unknown>(key, undefined)])
}

export function restoreTab(snapshot: [string, unknown][]): void {
  for (const [key, value] of snapshot) writeTabState<unknown>(key, undefined, () => value)
}

/** Load a decoded token into a tab, taking you there if it is another one.
 *
 *  Its result is filed on that tab's panel, which is opened when something
 *  was skipped, so the notes are there to read wherever the load came from.
 *
 *  Also points the tab's preset dropdown at the preset the token started
 *  from, with that preset's values as the baseline, so "changed from preset"
 *  still means something after the load. A preset since deleted leaves the
 *  dropdown empty. Marks the tab's default preset as already applied, or it
 *  would land on a tab being opened for the first time over the token. */
export function useTokenLoader() {
  const offer = useHandoff((state) => state.offer)
  const navigate = useNavigate()

  /** `ignoredText`: the token came with other words around it — a note
   *  saved beside it in a spreadsheet — which were left out. Said in
   *  passing, not as a warning: that is the normal way to keep tokens. */
  return async function load(
    target: TabSchema,
    data: TokenData,
    ignoredText = false,
  ): Promise<Outcome> {
    const { values, notes } = await api.applyToken(target.key, data as unknown as Record<string, unknown>)

    let presetName = ''
    let presetValues: Record<string, unknown> | null = null
    if (data.preset && target.presetTab) {
      try {
        const answer = await api.applyPreset(target.key, data.preset)
        if (Object.keys(answer).length > 0) {
          presetName = data.preset
          presetValues = answer
        }
      } catch {
        /* The preset is context, not a setting — the load goes on. */
      }
    }
    writeTabState(`preset.applied.${target.key}`, false, () => true)
    writeTabState(`preset.chosen.${target.key}`, '', () => presetName)
    writeTabState<Record<string, unknown> | null>(presetValuesKey(target.key), null, () => presetValues)

    offer(target.key, values)
    navigate(target.route)

    const loras = (data.loras ?? []).filter((row) => row[2] !== false).length
    const extra = [...notes]
    if (data.preset && !presetName) {
      extra.push(`The preset “${data.preset}” is gone; every setting still loaded.`)
    }
    // A picture already on the tab stays (GenerateTab's handoff), so this is
    // only for a picture field that is still empty.
    const held = readTabState<Record<string, unknown>>(`form.${target.key}`, {})
    const empty = (value: unknown) => value == null || (Array.isArray(value) && value.length === 0)
    if (target.fields.some((field) => (field.type === 'image' || field.type === 'images') && empty(held[field.name]))) {
      extra.push('Tokens carry no pictures, so add the input image.')
    }
    const outcome: Outcome = {
      tone: extra.length > 0 ? 'warn' : 'ok',
      title:
        `Loaded ${Object.keys(data.values).length} settings and ${loras} active ` +
        `LoRA${loras === 1 ? '' : 's'}` +
        (presetName ? `, based on “${presetName}”.` : '.') +
        (ignoredText ? ' Other text around the token was ignored.' : ''),
      notes: extra,
    }
    writeTabState<Outcome | null>(outcomeKey(target.key), null, () => outcome)
    if (extra.length > 0) writeTabState(openKey(target.key), false, () => true)
    return outcome
  }
}

/** The settings token of one generated file: its recipe, as a token.
 *
 *  Built from the recipe rather than the form, so it is the settings that
 *  picture was made with — seed included — even after the form has moved
 *  on. Nothing to copy for a file with no recipe.
 *
 *  `prepare` builds it ahead of the click — the token menu calls it on
 *  opening — because Share has to be called while the tap still counts as
 *  the user's, and a recipe fetch and a deflate in between can use that up.
 *  Copy is safer for it too; Safari holds the clipboard to the same rule. */
export function useImageToken(item: MediaItem | undefined) {
  const { data: schemas } = useSchemas()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [built, setBuilt] = useState<{ id: string; token: string } | null>(null)
  const schema = schemas?.find((candidate) => candidate.key === item?.tab)
  // Held against the file it was built for, so flipping to the next picture
  // in the lightbox never hands over the last one's token.
  const token = built && built.id === item?.id ? built.token : null
  const name = item ? tokenFileName(fileName(item.id).replace(/\.[^.]+$/, '')) : ''

  /** The token, or null with the reason already said. */
  async function prepare(): Promise<string | null> {
    if (token) return token
    if (!item || !schema) return null
    setBusy(true)
    try {
      const { values, preset } = await api.applyRecipe(schema.key, item.id)
      if (Object.keys(values).length === 0) {
        toast('No settings on file for that one.')
        return null
      }
      const next = await encodeToken(
        schema,
        tokenData(schema, { ...defaultsFor(schema), ...values }, preset ?? undefined),
      )
      setBuilt({ id: item.id, token: next })
      return next
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
      return null
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    const ready = await prepare()
    if (ready === null) return
    if (await copyText(ready)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } else {
      toast('Could not reach the clipboard.')
    }
  }

  function download() {
    if (token) saveToken(token, name)
  }

  async function share() {
    if (token && !(await shareToken(token, name))) toast('Could not open the share sheet.')
  }

  return { can: Boolean(item && schema), busy, copied, token, prepare, copy, download, share }
}
