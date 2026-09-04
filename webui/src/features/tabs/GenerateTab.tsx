import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useForm } from 'react-hook-form'
import type { ModelRow, TabSchema } from '@/api/types'
import { useModels } from '@/api/queries'
import { TwoColumn } from '@/components/TwoColumn'
import { SchemaForm } from '@/components/SchemaForm'
import { PresetBar } from '@/components/PresetBar'
import { Button, Inline } from '@/components/ui'
import { serializeEditor, type InpaintEditorValue } from '@/components/fields/MaskEditor'
import { defaultsFor } from '@/lib/schema'
import { useActiveJob, useJobsForTab, useQueue, isLive } from '@/store/queue'
import { useHandoff } from '@/store/handoff'
import { useSubmitHotkey } from '@/lib/util'
import { OutputPanel } from './OutputPanel'
import s from '@/components/SchemaForm/form.module.css'

/*
 * Ten of the twelve tabs. All of them.
 *
 * There is no per-tab code anywhere in this app any more. Krea2 and Inpaint
 * were built first on purpose — they exercise the schema form, uploads, the
 * mask editor's output contract, SSE, the queue panel and the gallery
 * plumbing between them — and once those worked end to end against the real
 * API the remaining eight arrived as data. Labels, types, defaults, ranges,
 * choices, grouping, column, conditional visibility and submission order all
 * come from `tabschema.py`, which is checked against the handler signatures
 * at import and against the parity baseline by `scripts/check_schema.py`.
 *
 * The two bespoke pages — Gallery and the Prompt Library — are not this
 * component, because neither submits a handler.
 */
export function GenerateTab({ schema }: { schema: TabSchema }) {
  const defaults = useMemo(() => defaultsFor(schema), [schema])
  const { watch, setValue, reset } = useForm<Record<string, unknown>>({
    defaultValues: defaults,
  })
  const values = watch()

  const submitJob = useQueue((state) => state.submit)
  const job = useActiveJob(schema.key)
  const runs = useJobsForTab(schema.key)
  const busy = job ? isLive(job) : false
  const models = useModels(schema)

  /* The line under the Model dropdown — variant, its step and CFG defaults,
   * whether the weights are actually on this pod. The Gradio app had it and
   * the React one did not; it is a hint rather than a field because
   * "not downloaded yet" is a fact about the disk, not about the control. */
  const hints = useMemo(() => {
    const row = models.find((candidate) => candidate.name === values.model)
    return row ? { model: <Inline text={row.info} /> } : undefined
  }, [models, values.model])

  // A tab switch is a different form. Without this, react-hook-form keeps the
  // previous tab's values under the same field names.
  //
  // A handoff — the Prompt Library's Use button, the Gallery's "load these
  // settings" — is drained in the same effect and applied *over* the
  // defaults, so arriving on a tab with a recipe in hand is one render rather
  // than a form that flashes its defaults first. `take` clears as it reads,
  // so coming back later does not re-apply it over what has been typed since.
  const take = useHandoff((state) => state.take)
  useEffect(() => {
    const handed = take(schema.key)
    reset(handed ? { ...defaults, ...handed } : defaults)
  }, [schema.key, defaults, reset, take])

  const set = useCallback(
    (name: string, value: unknown) => {
      setValue(name, value, { shouldDirty: true })
    },
    [setValue],
  )

  const apply = useCallback(
    (patch: Record<string, unknown>) => {
      for (const [name, value] of Object.entries(patch)) set(name, value)
    },
    [set],
  )

  /* Picking a model resets the dials that belong to it.
   *
   * This is what `krea_model_changed`, `v2_model_changed`,
   * `flux_model_changed` and `klein_model_changed` did — roughly 150 lines of
   * Gradio `.change()` wiring between them, over data that already sat in
   * config.py. The registries arrive whole in `/catalog`, so it happens here
   * with no round trip, and the same four lines serve all four tabs.
   *
   * Only on an actual *change*: applying a preset that names the model
   * already selected must not overwrite the steps and CFG that preset just
   * set. Gradio had the same rule for the same reason, by accident — a value
   * that does not change fires nothing. */
  const lastModel = useRef<string | null>(null)
  const model = String(values.model ?? '')
  useEffect(() => {
    const row = models.find((candidate) => candidate.name === model)
    const previous = lastModel.current
    lastModel.current = model
    if (!row || previous === null || previous === model) return
    for (const [name, value] of Object.entries(row.defaults)) {
      if (value !== null && value !== undefined) set(name, value)
    }
    if (schema.fields.some((field) => field.name === 'prompt')) {
      set('prompt', swapTrigger(String(values.prompt ?? ''), row, models))
    }
    // `values.prompt` is deliberately not a dependency: this must run when
    // the model changes and not on every keystroke in the prompt box.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, models, schema, set])

  const submit = useCallback(async () => {
    const payload = await buildPayload(schema, values)
    await submitJob({
      schema,
      prompt: String(schema.promptField ? (values[schema.promptField] ?? '') : ''),
      values: payload,
    })
  }, [schema, submitJob, values])

  // The shortcut the footer advertises. It was injected JS in theme.py; here
  // it is bound while this tab is mounted and unbound when it is not.
  useSubmitHotkey(() => {
    if (!busy) void submit()
  })

  return (
    <TwoColumn
      left={
        <>
          <PresetBar schema={schema} onApply={apply} />
          <SchemaForm
            schema={schema}
            values={values}
            setValue={set}
            column="left"
            hints={hints}
          />
          <div className={s.submitBar}>
            <Button
              variant="primary"
              size="lg"
              block
              loading={busy}
              onClick={() => void submit()}
            >
              {busy ? 'Running' : schema.submitLabel}
            </Button>
            <div className={s.submitHint}>
              <kbd className={s.kbd}>Ctrl</kbd>
              <span>+</span>
              <kbd className={s.kbd}>Enter</kbd>
            </div>
          </div>
        </>
      }
      right={
        <>
          <SchemaForm
            schema={schema}
            values={values}
            setValue={set}
            column="right"
            hints={hints}
          />
          <OutputPanel schema={schema} job={job} runs={runs} />
        </>
      }
    />
  )
}

/** Form values → the submit payload.
 *
 *  Almost everything passes through untouched; the exception is the mask
 *  editor, whose value is a background File plus a stack of canvases. Those
 *  become PNG blobs here, as the `{background, layers}` pair `gr.ImageEditor`
 *  produced, and `http.ts` uploads each one and swaps in its id.
 *
 *  The contract on the far side is unchanged, deliberately:
 *  `_prepare_inpaint_inputs` still takes the union of the painted layers'
 *  alpha channels, dilates by `grow`, blurs by `blur`, caps the long side at
 *  2048 and snaps both images to multiples of 16 because Krea 2's VAE
 *  requires it. `prepare.ts` in the editor reproduces that for the preview
 *  and the size readout only — the mask that is actually used is still built
 *  in Python, from these two PNGs. */
async function buildPayload(
  schema: TabSchema,
  values: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const payload: Record<string, unknown> = { ...values }
  for (const field of schema.fields) {
    if (field.type !== 'mask') continue
    const editor = values[field.name] as InpaintEditorValue | undefined
    payload[field.name] = editor ? await serializeEditor(editor) : null
  }
  return payload
}

/** The selected model's trigger words, swapped into the prompt.
 *
 *  ui._swap_trigger, character for character in effect: any *other*
 *  registered model's trigger is removed first, so switching models swaps
 *  triggers instead of stacking them, and the text stays fully editable —
 *  whatever ends up in the box is what gets used, with nothing added silently
 *  at generation time. Most registries ship no triggers at all, in which case
 *  this is the identity function. */
function swapTrigger(text: string, entry: ModelRow, registry: ModelRow[]): string {
  let out = text ?? ''
  for (const other of registry) {
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
