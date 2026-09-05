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
import { useTabState } from '@/store/tabState'
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

  /* Declared above the two things that write whole bags of values, because
   * both have to record the model they just wrote — see the model effect
   * below for what happens to a recipe when they do not. */
  const lastModel = useRef<string | null>(null)
  const lastKey = useRef(schema.key)

  /** A patch is about to be applied whole. If it names a model, that model
   *  was *chosen by the patch* and not by the customer, so it is not a
   *  change the model effect should react to. */
  const notePatchedModel = useCallback((patch: Record<string, unknown>) => {
    if (typeof patch.model === 'string') lastModel.current = patch.model
  }, [])

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
  const take = useHandoff((state) => state.take)
  const handed = useHandoff((state) => state.pending[schema.key])
  useEffect(() => {
    if (!handed) return
    take(schema.key)
    notePatchedModel(handed)
    setValues({ ...defaults, ...handed })
  }, [handed, schema.key, defaults, take, setValues, notePatchedModel])

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
      notePatchedModel(patch)
      setValues((previous) => ({ ...previous, ...patch }))
    },
    [setValues, notePatchedModel],
  )

  /* Picking a model resets the dials that belong to it.
   *
   * This is what `krea_model_changed`, `v2_model_changed`,
   * `flux_model_changed` and `klein_model_changed` did — roughly 150 lines of
   * Gradio `.change()` wiring between them, over data that already sat in
   * config.py. The registries arrive whole in `/catalog`, so it happens here
   * with no round trip, and the same four lines serve all four tabs.
   *
   * Only on an actual *change*, and only one the customer made. Applying a
   * preset that names the model already selected must not overwrite the
   * steps and CFG that preset just set — Gradio had that rule for the same
   * reason, by accident, since a value that does not change fires nothing.
   * A preset or a recipe naming a *different* model is the same case and
   * Gradio's accident did not cover it: "load these settings" would restore
   * a recipe and then, one render later, deal that model's default steps and
   * CFG over the recorded ones and rewrite the prompt's trigger word — so
   * the button loaded not quite the settings it was pointing at. Hence
   * `notePatchedModel`: a model that arrived inside a patch is recorded as
   * already seen, and only a hand on the dropdown gets here. */
  const model = String(values.model ?? '')
  useEffect(() => {
    const row = models.find((candidate) => candidate.name === model)
    const previous = lastModel.current
    // Arriving on a tab is not picking a model, and the model on the way in
    // is nearly always a different string — it belongs to a different tab.
    // That was free when the form was reset in the same commit anyway; now it
    // would deal one tab's steps and CFG over what you left on the next, and
    // rewrite its prompt's trigger word on the way past.
    const switched = lastKey.current !== schema.key
    lastKey.current = schema.key
    lastModel.current = model
    if (switched || !row || previous === null || previous === model) return
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
          {/* Keyed by tab, because a tab switch is a different form and
            * nothing else here remounts on one — React reconciles
            * `TabPage` with `TabPage`, so without this key every control
            * keeps its own *local* state: a negative prompt expanded on
            * Krea2 arrives expanded on V2, a LoRA stack opened on one tab
            * is open on the next. The values live per tab in `tabState`
            * and survive the remount; what it clears is the chrome around
            * them, which is the part that should clear. */}
          <SchemaForm
            key={schema.key}
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
            key={schema.key}
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
