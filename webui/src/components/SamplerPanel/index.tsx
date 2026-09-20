import type { Field } from '@/api/types'
import s from '@/components/SchemaForm/form.module.css'
import { cx } from '@/lib/util'

/*
 * The ClownsharKSampler block, and the variance block beside it.
 *
 * Krea2 V2 and Krea2 V2 Edit both carry these two blocks, so one pair of
 * components renders them rather than a copy per tab, and `tabMeta.ts` keeps
 * them in the left column with every other control.
 *
 * The fields themselves are ordinary and come from the schema. What the
 * panels add is order: the things you change land above the things you set
 * once, rather than in signature order.
 */

interface PanelProps {
  fields: Field[]
  render: (field: Field) => React.ReactNode
}

/** Fields in the order given, with the named ones hoisted to the front. */
function ordered(fields: Field[], first: string[]): Field[] {
  const byName = new Map(fields.map((field) => [field.name, field]))
  const head = first.map((name) => byName.get(name)).filter((f): f is Field => Boolean(f))
  const rest = fields.filter((field) => !first.includes(field.name))
  return [...head, ...rest]
}

export function SamplerPanel({ fields, render }: PanelProps) {
  // Steps and CFG are the two dials anyone touches; sampler_mode and bongmath
  // get set once a year.
  // Both spellings of the dropdown: 'sampler_name' on the RES4LYF tabs,
  // 'sampler' on the KSampler ones, which share this panel since their
  // Steps and CFG moved into it.
  const list = ordered(fields, [
    'steps',
    'cfg',
    'denoise',
    'sampler_name',
    'sampler',
    'scheduler',
    'eta',
  ])
  return <div className={cx(s.groupBody, s.groupDense)}>{list.map(render)}</div>
}

export function VariancePanel({ fields, render }: PanelProps) {
  // The preset drives the rest, so it goes first and full width; the
  // step-window pair (cutoff_step / total_steps) stays adjacent because
  // reversing those two is exactly the silent regression
  // `scripts/check_schema.py` compares against `parity_baseline.json` to
  // catch — see `docs/development/checks.md`.
  const list = ordered(fields, [
    'variance_preset',
    'fine_tune_variance',
    'variance_model_type',
    'variance_schedule',
    'cutoff_step',
    'total_steps',
  ])
  return <div className={cx(s.groupBody, s.groupDense)}>{list.map(render)}</div>
}
