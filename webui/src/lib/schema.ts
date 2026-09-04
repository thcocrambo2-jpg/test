import type { TabSchema } from '@/api/types'

/*
 * Schema utilities.
 *
 * `defaultsFor` fills a form from the schema the server sent.
 *
 * What used to live here as well was `toSubmission`, which built the
 * positional argument array in the browser. It is gone: `tabschema.call_args`
 * does it on the server, next to the `inspect.signature` assertion that keeps
 * `Field.name` equal to the handler's parameter name. A 31-argument signature
 * should be reassembled in the one place that can check itself against it.
 */

/** Default values for one tab's form, keyed by field name.
 *
 *  LoRA slots are flattened into `<lora.key>.<index>.<part>` so they live in
 *  the same flat bag as everything else, which is exactly the shape
 *  `tabschema.call_args` reads them back out of.
 *
 *  The per-slot defaults come from `lora.slots` rather than from one default
 *  repeated N times, because V2 and Klein take their rows from the source
 *  workflow's own stack — and a row whose file did not download comes back
 *  off and blank, which only the pod's disk can say. */
export function defaultsFor(schema: TabSchema): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const field of schema.fields) values[field.name] = field.default
  const lora = schema.lora
  if (lora) {
    for (let i = 0; i < lora.count; i += 1) {
      const slot = lora.slots[i] ?? {}
      for (const part of lora.parts) {
        values[`${lora.key}.${i}.${part}`] = slot[part]
      }
    }
  }
  return values
}

/** How many controls this tab has, form plus LoRA stack.
 *
 *  Display only. The argument *count* that matters is asserted on the Python
 *  side against `inspect.signature`, which is the only place it can be
 *  checked rather than believed. */
export function controlCount(schema: TabSchema): number {
  const lora = schema.lora
  return schema.fields.length + (lora ? lora.count * lora.parts.length : 0)
}
