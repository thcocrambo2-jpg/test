import type { ReactNode } from 'react'
import s from '@/components/SchemaForm/form.module.css'

/** Controls left, output right.
 *
 *  Every tab's page is this one shell, so a layout change is a layout change
 *  and not one per tab, and no two tabs can drift apart. */
export function TwoColumn({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className={s.twoColumn}>
      <div className={s.left}>{left}</div>
      <div className={s.right}>{right}</div>
    </div>
  )
}
