import type { ReactNode } from 'react'
import s from '@/components/SchemaForm/form.module.css'

/** Controls left, output right.
 *
 *  This shell is retyped ten times in ui.py — `with gr.Row(): with
 *  gr.Column(scale=…)` — and every copy drifted slightly. One component, so a
 *  layout change is a layout change and not ten of them. */
export function TwoColumn({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className={s.twoColumn}>
      <div className={s.left}>{left}</div>
      <div className={s.right}>{right}</div>
    </div>
  )
}
