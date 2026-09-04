import { useCallback, useEffect, useMemo } from 'react'
import { useForm } from 'react-hook-form'
import type { TabSchema } from '@/api/types'
import { TwoColumn } from '@/components/TwoColumn'
import { SchemaForm } from '@/components/SchemaForm'
import { Button } from '@/components/ui'
import { serializeEditor, type InpaintEditorValue } from '@/components/fields/MaskEditor'
import { defaultsFor } from '@/lib/schema'
import { useActiveJob, useJobsForTab, useQueue, isLive } from '@/store/queue'
import { useSubmitHotkey } from '@/lib/util'
import { OutputPanel } from './OutputPanel'
import s from '@/components/SchemaForm/form.module.css'

/*
 * Nine of the twelve tabs.
 *
 * Krea2 and Inpaint are this component with different schemas, and so are the
 * seven still to come. The only per-tab code anywhere is `tabMeta.ts`, which
 * says how a tab's fields group and which column they take — everything else
 * (labels, types, defaults, ranges, choices, submission order) comes from
 * `parity_baseline.json`.
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

  // A tab switch is a different form. Without this, react-hook-form keeps the
  // previous tab's values under the same field names.
  useEffect(() => {
    reset(defaults)
  }, [schema.key, defaults, reset])

  const set = useCallback(
    (name: string, value: unknown) => {
      setValue(name, value, { shouldDirty: true })
    },
    [setValue],
  )

  const submit = useCallback(async () => {
    const payload = await buildPayload(schema, values)
    await submitJob({
      tabKey: schema.key,
      tabLabel: schema.label,
      prompt: String(values.prompt ?? ''),
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
          <SchemaForm schema={schema} values={values} setValue={set} column="left" />
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
          <SchemaForm schema={schema} values={values} setValue={set} column="right" />
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
 *  become PNG blobs here so the wire carries exactly the `{background,
 *  layers}` pair `gr.ImageEditor` produced — `_prepare_inpaint_inputs` on the
 *  Python side is unchanged and still does the union, the dilation, the blur
 *  and the snap to 16. */
async function buildPayload(
  schema: TabSchema,
  values: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const payload: Record<string, unknown> = { ...values }
  for (const field of schema.fields) {
    if (field.type !== 'mask') continue
    const editor = values[field.name] as InpaintEditorValue | undefined
    const serialized = editor ? await serializeEditor(editor) : null
    if (!serialized) {
      payload[field.name] = null
      continue
    }
    payload[field.name] = null
    payload[`${field.name}.background`] = serialized.background
    payload[`${field.name}.layers`] = serialized.layers
  }
  return payload
}
