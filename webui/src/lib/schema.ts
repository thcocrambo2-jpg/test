import type { TabSchema } from '@/api/types'

/*
 * Schema utilities.
 *
 * These belong to the app, not to the mock: `defaultsFor` fills a form and
 * `toSubmission` builds the argument array, and both keep working unchanged
 * when the schemas start arriving from `/api/v1/schemas` instead of from
 * `parity_baseline.json`.
 */

/** Default values for one tab's form, keyed by field name.
 *
 *  LoRA slots are flattened into `lora.<index>.<part>` keys so they can live
 *  in the same flat value bag as everything else; `toSubmission` puts them
 *  back in order. */
export function defaultsFor(schema: TabSchema): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const field of schema.fields) values[field.name] = field.default
  if (schema.lora) {
    const none = schema.lora.choices[0] ?? 'None'
    for (let i = 0; i < schema.lora.count; i += 1) {
      values[`lora.${i}.name`] = none
      values[`lora.${i}.weight`] = schema.lora.weightDefault
      if (schema.lora.shape === 'triple') {
        values[`lora.${i}.enabled`] = schema.lora.enabledDefault
      }
    }
  }
  return values
}

/** Form values → the positional array the Python handler takes.
 *
 *  The one function in the UI that has to be exactly right: index i of the
 *  result is positional parameter i of the handler, and the LoRA tail is
 *  appended flat in slot order — as a pair or a triple, per `schema.lora`.
 *
 *  It walks `schema.fields`, which came from the parity baseline in
 *  submission order, so no amount of regrouping or column-moving in the
 *  layout can reorder it. That separation is the whole reason the schema
 *  carries `column` and `group` as data. */
export function toSubmission(
  schema: TabSchema,
  values: Record<string, unknown>,
): unknown[] {
  const args: unknown[] = schema.fields.map((field) => values[field.name])
  if (schema.lora) {
    const none = schema.lora.choices[0] ?? 'None'
    for (let i = 0; i < schema.lora.count; i += 1) {
      if (schema.lora.shape === 'triple') args.push(values[`lora.${i}.enabled`] ?? false)
      args.push(values[`lora.${i}.name`] ?? none)
      args.push(values[`lora.${i}.weight`] ?? schema.lora.weightDefault)
    }
  }
  return args
}

/** How many positional arguments the handler behind this schema takes. */
export function argumentCount(schema: TabSchema): number {
  const slot = schema.lora ? (schema.lora.shape === 'triple' ? 3 : 2) : 0
  return schema.fields.length + (schema.lora ? schema.lora.count * slot : 0)
}
