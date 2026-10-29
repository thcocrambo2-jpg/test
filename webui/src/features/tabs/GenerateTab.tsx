import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { ModelRow, SizeFromImage, TabSchema } from '@/api/types'
import { useNavigate } from 'react-router-dom'
import { useAppCatalog, useModels, useSchemas } from '@/api/queries'
import { useImageSize } from '@/components/fields'
import { TwoColumn } from '@/components/TwoColumn'
import { SchemaForm } from '@/components/SchemaForm'
import { PresetBar, presetValuesKey } from '@/components/PresetBar'
import {
  restoreTab,
  SettingsToken,
  snapshotTab,
  useTokenLoader,
} from '@/components/SettingsToken'
import t from '@/components/SettingsToken/token.module.css'
import { Button, Inline, useToast } from '@/components/ui'
import { defaultsFor } from '@/lib/schema'
import {
  changedFromPreset,
  hasToken,
  readToken,
  showValue,
} from '@/lib/settingsToken'
import { useActiveJob, useJobsForTab, useQueue, isLive } from '@/store/queue'
import { useHandoff } from '@/store/handoff'
import { useTabState } from '@/store/tabState'
import { useMediaQuery, useSubmitHotkey } from '@/lib/util'
import { AutoPromptPanel } from './AutoPrompt'
import { OutputPanel } from './OutputPanel'
import s from '@/components/SchemaForm/form.module.css'

/*
 * Thirteen of the fifteen tabs. All of them.
 *
 * There is no per-tab code anywhere in this app any more. Krea2 was built
 * first on purpose — it exercises the schema form, SSE, the queue panel and
 * the gallery plumbing — and once those worked end to end against the real
 * API the remaining seven arrived as data. Labels, types, defaults, ranges,
 * choices, grouping, column, conditional visibility and submission order all
 * come from `tabschema.py`, which is checked against the handler signatures
 * at import and against the parity baseline by `scripts/check_schema.py`.
 *
 * The two bespoke pages — Gallery and the Prompt Library — are not this
 * component, because neither submits a handler.
 */
/** Tabs a handoff has been applied to this session.
 *
 *  Module level for the reason `tabState` is: it has to outlive this
 *  component, which the Gallery unmounts — and it is read when a preset
 *  request comes back, so it has to be live rather than a render's copy. */
const HANDED_OFF = new Set<string>()

/** Where form.module.css stacks the two columns. Kept in step with its
 *  `max-width: 1100px` rule by hand; see `lift` below for the one thing JS
 *  does with it. */
const STACKED = '(max-width: 1100px)'

export function GenerateTab({ schema }: { schema: TabSchema }) {
  const defaults = useMemo(() => defaultsFor(schema), [schema])

  /* A plain value bag, deliberately not react-hook-form.
   *
   * RHF was here and it silently broke the LoRA stack. Its `setValue` reads a
   * field name as a *path*: `setValue("lora.0.name", x)` writes
   * `{lora: [{name: x}]}` and leaves the flat `"lora.0.name"` key — the one
   * `defaultsFor` created and the one `tabschema.call_args` reads back —
   * untouched. So the dropdown snapped back to None, the header stayed
   * "0/8 active", and every generation went to ComfyUI with no LoRAs on it.
   * `tmp/output/.recipes.jsonl` recorded exactly that: `loras: []`.
   *
   * The dots cannot move — they are the wire format `RepeatSpec.value_key`
   * defines and `call_args` reassembles the *varargs tail from. So the form
   * library goes instead, which costs nothing: this component only ever used
   * watch/setValue/reset. It never registered an input, never validated and
   * never took a ref — every control here is driven through `setValue` from
   * SchemaForm's own onChange.
   *
   * The bag itself is held in `tabState` rather than in this component, keyed
   * by tab, so that leaving a tab no longer costs you what you set on it. The
   * key is `schema.key`: Krea2 and Krea2 V2 name their fields identically and
   * must not share a bag.
   *
   * Note what a tab switch actually is here, because two things below depend
   * on it: React Router renders the matched route into the same position, so
   * it reconciles `TabPage` with `TabPage` and this component is *not*
   * unmounted — it is re-rendered with a different `schema`. Every ref and
   * every closure in it survives the switch. */
  const [values, setValues] = useTabState<Record<string, unknown>>(
    `form.${schema.key}`,
    defaults,
  )

  /* Groups marked `stackFirst` move to the top of the form, under the
   * preset bar, while the page is stacked — rather than landing under the
   * whole left column with the rest of the right one. For inputs the left
   * column's controls refer to: Qwen 2.1's numbered references, which the
   * prompt names. Opt-in, so every other tab renders exactly as it did.
   * JS rather than CSS `order`, because the left column has to stay one box
   * for its sticky submit bar. */
  const stacked = useMediaQuery(STACKED)
  const lift = stacked && (schema.groups ?? []).some((group) => group.stackFirst)

  /* An images field that needs pictures before it can run keeps the button
   * off and says why under it — rather than red on a form just opened. The
   * handler refuses the same case; this saves the round trip. */
  const short = schema.fields.find(
    (field) =>
      field.type === 'images' &&
      (field.min ?? 0) > 0 &&
      (Array.isArray(values[field.name]) ? (values[field.name] as unknown[]).length : 0) <
        (field.min ?? 0),
  )

  const submitJob = useQueue((state) => state.submit)
  const job = useActiveJob(schema.key)
  const runs = useJobsForTab(schema.key)
  const models = useModels(schema)

  /* The button is disabled while the *submission* is in flight, and for no
   * other reason.
   *
   * Disabling it for as long as the active run is live would undo the whole
   * point of the queue: a second idea would have to wait for the first to
   * finish and for somebody to be sitting there to click again. A click only
   * records the work and returns in microseconds, and a lane runs its jobs
   * one at a time in arrival order — so refusing the second click is
   * refusing to let anything queue behind the first.
   *
   * What remains is the round trip itself: uploads go up before the job is
   * recorded, so on an image tab this is a real wait and a double click
   * would submit twice. */
  const [submitting, setSubmitting] = useState(false)
  const waiting = runs.filter(isLive).length

  /* The line under the Model dropdown — variant, its step and CFG defaults,
   * whether the weights are actually on this pod. It is a hint rather than a
   * field because "not downloaded yet" is a fact about the disk, not about
   * the control.
   *
   * Looked up by id, which is what the dropdown holds. Its label is the
   * model's name, but a name is not a key: it is free text in the DB, and
   * two records can share one weights file at different steps and CFG. */
  /* The line under an output size that takes its shape from a picture —
   * Qwen 2.1's "Same as reference 1" — naming the size the pod will
   * render, which moves as pictures are dropped or reordered. */
  const sizeRule = useAppCatalog().data?.sizeFromImage?.[schema.key]
  const firstImage = sizeRule
    ? (values[sizeRule.images] as File[] | undefined)?.[0]
    : undefined
  const firstSize = useImageSize(firstImage)

  /* What the chosen preset set, kept by PresetBar. A control that has moved
   * off it says what the preset had, under it, so a tweak is visible
   * without opening the settings token. */
  const [presetValues] = useTabState<Record<string, unknown> | null>(
    presetValuesKey(schema.key),
    null,
  )

  const hints = useMemo(() => {
    const out: Record<string, ReactNode> = {}
    const row = models.find((candidate) => candidate.id === values.model)
    if (row) out.model = <Inline text={row.info} />
    if (sizeRule && values[sizeRule.field] === sizeRule.choice) {
      out[sizeRule.field] = sizeHint(sizeRule, firstSize)
    }
    for (const [name, [was]] of Object.entries(changedFromPreset(schema, values, presetValues))) {
      const field = schema.fields.find((candidate) => candidate.name === name)
      if (!field) continue
      const shown = showValue(schema, name, was)
      const note = (
        <span className={t.presetWas}>
          Preset: {shown.length > 40 ? `${shown.slice(0, 40)}…` : shown}
        </span>
      )
      const before = out[name] ?? field.hint
      out[name] = before ? (
        <>
          {before} {note}
        </>
      ) : (
        note
      )
    }
    return Object.keys(out).length > 0 ? out : undefined
  }, [models, values, sizeRule, firstSize, schema, presetValues])

  // A handoff — the Prompt Library's Use button, the Gallery's "load these
  // settings" — is drained on arrival and applied *over the defaults*, never
  // over whatever this tab was left holding: a recipe means "these values,
  // together", and merged into a half-filled form it would produce a set
  // nobody chose. `take` clears as it reads, so coming back later does not
  // re-apply it over what has been typed since.
  //
  // Without one this does nothing at all. It used to re-seed the whole bag
  // from `defaults` on every mount, which is exactly what threw away the form
  // you had filled in the moment you looked at another tab.
  //
  // Subscribed to rather than drained on mount, and that is the whole of it:
  // whoever offers a handoff then navigates to the tab it is for, and when
  // that tab is the one already on screen — "load these settings" from this
  // page's own lightbox — the navigation is to the route we are on, so React
  // Router re-renders `TabPage` with `TabPage`, nothing remounts, and an
  // effect keyed on `schema.key` never runs again. The values sat in the
  // store unread and the button looked broken from the output panel while
  // working from the gallery. Watching `pending` makes arrival the trigger,
  // which is what it always meant. `take` clears it, so this settles in one
  // extra pass and a later remount does not re-apply it over what has been
  // typed since.
  //
  // The one exception is a picture the handoff does not bring. A settings
  // token and a library entry carry none, so the image you had uploaded
  // went back to its default — empty — the moment you pasted a token over
  // it. A picture is the input, not a setting, so it stays unless the
  // handoff brings its own (the Gallery's, from the run's kept source).
  //
  // It also retires the tab's default preset, which is applied once per tab
  // and asynchronously — see `shouldApplyDefault` below.
  const take = useHandoff((state) => state.take)
  const handed = useHandoff((state) => state.pending[schema.key])
  useEffect(() => {
    if (!handed) return
    take(schema.key)
    HANDED_OFF.add(schema.key)
    setValues((previous) => {
      const next = { ...defaults, ...handed }
      for (const field of schema.fields) {
        if (field.type !== 'image' && field.type !== 'images') continue
        if (field.name in handed || previous[field.name] == null) continue
        next[field.name] = previous[field.name]
      }
      return next
    })
  }, [handed, schema.key, schema.fields, defaults, take, setValues])

  /* Whether the tab's default preset may still land.
   *
   * Not once a handoff has. The default preset is fetched the first time a
   * tab is shown in a session, and "load these settings" from the Gallery is
   * very often that first time: the recipe went in on arrival, the preset
   * answer came back a moment later, and `apply` merged it over every control
   * but the prompt — so the tab looked like it had loaded its defaults
   * instead. Asked when the answer arrives rather than when it was requested,
   * so a handoff that lands while the request is in flight still wins. */
  const shouldApplyDefault = useCallback(() => !HANDED_OFF.has(schema.key), [schema.key])

  /* `setValues` is a dependency, and has to be.
   *
   * It was `[]` when the bag came from `useState`, whose setter never
   * changes. `tabState`'s does: it carries the key it writes to. Since this
   * component is re-rendered rather than remounted on a tab switch, a setter
   * captured once would go on writing into the tab you left — which is
   * exactly how V2's defaults landed in Krea2's bag and emptied it. */
  const set = useCallback(
    (name: string, value: unknown) => {
      setValues((previous) =>
        Object.is(previous[name], value) ? previous : { ...previous, [name]: value },
      )
    },
    [setValues],
  )

  /* A whole patch in one update rather than one per key.
   *
   * A preset writes ~30 values including all eight LoRA slots, and applying
   * them through `set` in a loop is 30 renders of a form that is not cheap to
   * draw. It also has to be a single merge for a second reason: `apply` is
   * what `PresetBar` hands the server's answer to, and a preset means "these
   * values, together". */
  const apply = useCallback(
    (patch: Record<string, unknown>) => {
      setValues((previous) => ({ ...previous, ...patch }))
    },
    [setValues],
  )

  /* Picking a model resets the dials that belong to it.
   *
   * The models come from the licence server's catalogue, and each tab's list
   * arrives whole in `/catalog` under its feature key, so this happens in the
   * browser with no round trip, and the same four lines serve every tab with
   * a Model dropdown. `model` below is the selected model's *id*, and the row
   * is found by id for the reason given at `hints`.
   *
   * Only on an actual *change*, and only one the customer made — which is
   * why this is the dropdown's change handler and not an effect watching
   * `values.model`. A preset or a recipe naming a different model means
   * "these steps and CFG with that model"; dealing the model's defaults over
   * them would load not quite the settings the button pointed at.
   *
   * It was an effect, with refs recording which model a patch had written so
   * the effect could tell the two apart. That bookkeeping lost to effect
   * order: a handoff is applied from an effect in the same commit as the
   * model effect, which then read the model from the render *before* the
   * recipe and took the recipe's model for a hand on the dropdown. So
   * whenever a recipe named a different model from the one the tab was
   * holding, "load these settings" dealt the old model's steps and CFG over
   * the recipe, put the old prompt back, then dealt the recipe model's
   * defaults over that. A handler has nothing to tell apart: only the
   * dropdown calls it.
   *
   * One functional update, so the prompt it rewrites is the live one. */
  const setField = useCallback(
    (name: string, value: unknown) => {
      if (name !== 'model') {
        set(name, value)
        return
      }
      setValues((previous) => {
        if (Object.is(previous.model, value)) return previous
        const next: Record<string, unknown> = { ...previous, model: value }
        const row = models.find((candidate) => candidate.id === value)
        if (!row) return next
        for (const [key, fallback] of Object.entries(row.defaults)) {
          if (fallback !== null && fallback !== undefined) next[key] = fallback
        }
        if (schema.fields.some((field) => field.name === 'prompt')) {
          next.prompt = swapTrigger(String(previous.prompt ?? ''), row, models)
        }
        // The negative prompt follows the model, but only while it is the
        // one the outgoing model brought: a negative somebody wrote stays.
        const outgoing = models.find((candidate) => candidate.id === previous.model)
        if (
          row.negative !== undefined &&
          outgoing?.negative !== undefined &&
          previous.negative === outgoing.negative
        ) {
          next.negative = row.negative
        }
        return next
      })
    },
    [models, schema, set, setValues],
  )

  /* A settings token pasted into the Prompt box — or left there and
   * submitted — loads the whole form instead of becoming the prompt.
   *
   * Through the same loader as the Settings token panel, so it replaces the
   * form rather than merging into it, and a token for another tab takes you
   * there. The toast carries Undo, which puts back everything the load
   * wrote on the tab it went to and returns you here if it moved you. */
  const loadToken = useTokenLoader()
  const { data: schemas } = useSchemas()
  const navigate = useNavigate()
  const toast = useToast()
  const fromToken = useCallback(
    async (text: string) => {
      try {
        const { data, extra } = await readToken(text)
        const target =
          data.tab === schema.key ? schema : schemas?.find((candidate) => candidate.key === data.tab)
        if (!target) {
          toast('That token is for a tab this licence does not include.')
          return
        }
        const before = snapshotTab(target.key)
        const outcome = await loadToken(target, data, extra)
        const where = target.key === schema.key ? '' : ` into ${target.label}`
        const around = extra ? ' (other text ignored)' : ''
        const notes = outcome.notes.length
        toast(
          notes > 0
            ? `Settings loaded${where}${around} · ${notes} note${notes === 1 ? '' : 's'} in Settings token`
            : `Settings loaded${where}${around}`,
          {
            label: 'Undo',
            run: () => {
              restoreTab(before)
              if (target.key !== schema.key) navigate(schema.route)
              toast('Back as it was.')
            },
          },
        )
      } catch (error) {
        toast(error instanceof Error ? error.message : String(error))
      }
    },
    [schema, schemas, loadToken, navigate, toast],
  )
  const onPromptPaste = useCallback(
    (text: string) => {
      if (!hasToken(text)) return false
      void fromToken(text)
      return true
    },
    [fromToken],
  )

  const submit = useCallback(async () => {
    // Somebody who got a token into the box another way — dropped it in,
    // typed it — means the settings, not a prompt made of base64.
    const prompt = String(values[schema.promptField] ?? '')
    if (hasToken(prompt)) {
      await fromToken(prompt)
      return
    }
    setSubmitting(true)
    try {
      await submitJob({
        schema,
        prompt: String(values[schema.promptField] ?? ''),
        values,
      })
    } finally {
      // `submitJob` reports a rejected submission as a job carrying the
      // error rather than by throwing, so this is belt and braces — but a
      // button that is dead because an await never settled is the one
      // failure this state can cause, and it costs a try/finally to make
      // impossible.
      setSubmitting(false)
    }
  }, [schema, submitJob, values, fromToken])

  // The shortcut the footer advertises. Bound while this tab is mounted and
  // unbound when it is not, so it always runs the tab you are looking at.
  useSubmitHotkey(() => {
    if (!submitting && !short) void submit()
  })

  return (
    <TwoColumn
      left={
        <>
          <PresetBar
            schema={schema}
            onApply={apply}
            shouldApplyDefault={shouldApplyDefault}
          />
          {/* Keyed by tab, because a tab switch is a different form and
            * nothing else here remounts on one — React reconciles
            * `TabPage` with `TabPage`, so without this key every control
            * keeps its own *local* state: a negative prompt expanded on
            * Krea2 arrives expanded on V2, a LoRA stack opened on one tab
            * is open on the next. The values live per tab in `tabState`
            * and survive the remount; what it clears is the chrome around
            * them, which is the part that should clear. */}
          {/* Above the form, so it sits over the Prompt group it writes into.
            * Keyed for the reason SchemaForm is — its local chrome belongs to
            * one tab — but not with the same key: they are siblings, and two
            * siblings sharing `schema.key` let React match the I2V panel to
            * the T2V page on a switch, props and all. */}
          {schema.autoprompt && (
            <AutoPromptPanel key={`autoprompt.${schema.key}`} schema={schema} values={values} />
          )}
          {lift &&
            (['left', 'right'] as const).map((column) => (
              <SchemaForm
                key={`${schema.key}.first.${column}`}
                schema={schema}
                values={values}
                setValue={setField}
                column={column}
                hints={hints}
                placement="stackFirst"
                onPromptPaste={onPromptPaste}
              />
            ))}
          <SchemaForm
            key={schema.key}
            schema={schema}
            values={values}
            setValue={setField}
            column="left"
            hints={hints}
            placement={lift ? 'rest' : 'all'}
            onPromptPaste={onPromptPaste}
          />
          {/* Last in the form, so it reads as "everything above, as one
            * line". Keyed for the reason SchemaForm is. */}
          <SettingsToken key={`token.${schema.key}`} schema={schema} values={values} />
          <div className={s.submitBar}>
            <Button
              variant="primary"
              size="lg"
              block
              loading={submitting}
              disabled={Boolean(short)}
              aria-describedby={short ? `${schema.key}-short` : undefined}
              onClick={() => void submit()}
            >
              {submitting ? 'Queueing' : schema.submitLabel}
            </Button>
            {short && (
              <div className={s.submitHint} id={`${schema.key}-short`}>
                {short.emptyNote ?? `Add at least ${short.min} to ${short.label}.`}
              </div>
            )}
            {/* Only the queue depth. The Ctrl+Enter reminder that used to
             *  sit here as well is in the footer, where it is stated once for
             *  the whole app rather than under every tab's button.
             *
             *  Nothing when the queue is empty, so the button keeps its own
             *  spacing on a form that has just loaded. */}
            {waiting > 0 && (
              <div className={s.submitHint}>{waiting} in the queue</div>
            )}
          </div>
        </>
      }
      right={
        <>
          <SchemaForm
            key={schema.key}
            schema={schema}
            values={values}
            setValue={setField}
            column="right"
            hints={hints}
            placement={lift ? 'rest' : 'all'}
            onPromptPaste={onPromptPaste}
          />
          <OutputPanel schema={schema} job={job} runs={runs} />
        </>
      }
    />
  )
}

/** The size a picture's shape comes to under `rule` — the handler's
 *  `reference_size`, rounded half up on both sides of the wire. */
function sizeFrom(rule: SizeFromImage, width: number, height: number): [number, number] {
  const ratio = width / height
  const side = (length: number) =>
    Math.max(rule.multiple, Math.floor(length / rule.multiple + 0.5) * rule.multiple)
  return [side(Math.sqrt(rule.pixels * ratio)), side(Math.sqrt(rule.pixels / ratio))]
}

function sizeHint(
  rule: SizeFromImage,
  size: { width: number; height: number } | null,
): ReactNode {
  if (!size) {
    const edge = Math.round(Math.sqrt(rule.pixels))
    return `The shape of reference 1, at about ${edge} × ${edge} pixels in all.`
  }
  const [width, height] = sizeFrom(rule, size.width, size.height)
  return (
    <>
      Output will be{' '}
      <strong>
        {width} × {height}
      </strong>
      , the shape of reference 1. Drag another image to the front to change it.
    </>
  )
}

/** The selected model's trigger words, swapped into the prompt.
 *
 *  The trigger of any
 *  *other* model this tab offers is removed first, so switching models swaps
 *  triggers instead of stacking them, and the text stays fully editable —
 *  whatever ends up in the box is what gets used, with nothing added silently
 *  at generation time. Most models carry no trigger at all, in which case
 *  this is the identity function. */
function swapTrigger(text: string, entry: ModelRow, offered: ModelRow[]): string {
  let out = text ?? ''
  for (const other of offered) {
    const trigger = (other.trigger ?? '').trim()
    if (!trigger) continue
    const index = out.toLowerCase().indexOf(trigger.toLowerCase())
    if (index >= 0) out = out.slice(0, index) + out.slice(index + trigger.length)
  }
  out = out.trim().replace(/^,+|,+$/g, '').trim()
  const trigger = (entry.trigger ?? '').trim()
  if (!trigger) return out
  return out ? `${trigger}, ${out}` : trigger
}
