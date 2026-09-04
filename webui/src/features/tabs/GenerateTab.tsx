import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
   * SchemaForm's own onChange. */
  const [values, setValues] = useState<Record<string, unknown>>(defaults)

  const submitJob = useQueue((state) => state.submit)
  const job = useActiveJob(schema.key)
  const runs = useJobsForTab(schema.key)
  const models = useModels(schema)

  /* The button is disabled while the *submission* is in flight, and for no
   * other reason.
   *
   * It used to be disabled for as long as the active run was live, which
   * quietly reinstated the behaviour the queue was built to remove:
   * Gradio's `trigger_mode="once"` left the button dead for the whole
   * render, so a second idea had to wait for the first to finish and for
   * somebody to be sitting there to click again. jobqueue exists to end
   * that — a click only records the work and returns in microseconds, and
   * a lane runs its jobs one at a time in arrival order. Refusing the
   * second click is refusing to let anything queue behind the first.
   *
   * What remains is the round trip itself: uploads go up before the job is
   * recorded, so on the mask editor this is a real wait and a double click
   * would submit twice. */
  const [submitting, setSubmitting] = useState(false)
  const waiting = runs.filter(isLive).length

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
    setValues(handed ? { ...defaults, ...handed } : defaults)
  }, [schema.key, defaults, take])

  const set = useCallback((name: string, value: unknown) => {
    setValues((previous) =>
      Object.is(previous[name], value) ? previous : { ...previous, [name]: value },
    )
  }, [])

  /* A whole patch in one update rather than one per key.
   *
   * A preset writes ~30 values including all eight LoRA slots, and applying
   * them through `set` in a loop is 30 renders of a form that is not cheap to
   * draw. It also has to be a single merge for a second reason: `apply` is
   * what `PresetBar` hands the server's answer to, and a preset means "these
   * values, together". */
  const apply = useCallback((patch: Record<string, unknown>) => {
    setValues((previous) => ({ ...previous, ...patch }))
  }, [])

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
    setSubmitting(true)
    try {
      const payload = await buildPayload(schema, values)
      await submitJob({
        schema,
        prompt: String(schema.promptField ? (values[schema.promptField] ?? '') : ''),
        values: payload,
      })
    } finally {
      // `submitJob` reports a rejected submission as a job carrying the
      // error rather than by throwing, so this is belt and braces — but a
      // button that is dead because an await never settled is the one
      // failure this state can cause, and it costs a try/finally to make
      // impossible.
      setSubmitting(false)
    }
  }, [schema, submitJob, values])

  // The shortcut the footer advertises. It was injected JS in theme.py; here
  // it is bound while this tab is mounted and unbound when it is not.
  useSubmitHotkey(() => {
    if (!submitting) void submit()
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
              loading={submitting}
              onClick={() => void submit()}
            >
              {submitting ? 'Queueing' : schema.submitLabel}
            </Button>
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
