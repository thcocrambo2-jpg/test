import type { Field } from '@/api/types'
import s from '@/components/SchemaForm/form.module.css'
import { cx } from '@/lib/util'

/*
 * The ClownsharKSampler block, and the variance block beside it.
 *
 * Both are duplicated between Krea2 V2 and Krea2 V2 Edit in ui.py, and in
 * both tabs they were built into the *output* column — the only two tabs in
 * the app where Steps, CFG and Sampler are not with the other controls.
 * `tabMeta.ts` puts them back on the left; these two components are what
 * renders them once instead of twice.
 *
 * The fields themselves are ordinary and come from the baseline. What the
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
  // reversing those two is the exact silent regression parity.py was built to
  // catch (context.md §4.2).
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
