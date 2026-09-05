import { cx } from '@/lib/util'
import s from './gallery.module.css'

/* The marks the gallery draws, inline.
 *
 * Inline because two paths of a dozen points each are cheaper than an icon
 * dependency that arrives with a thousand more, and in their own file
 * because both surfaces need them: the grid's selection bar and the
 * lightbox's header, and the lightbox is imported *by* the grid.
 */

/** The bin. */
export function TrashIcon() {
  return (
    <svg className={s.btnIcon} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M10 11v6M14 11v6" />
      <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  )
}

/** The tray with an arrow into it. */
export function DownloadIcon() {
  return (
    <svg className={s.btnIcon} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 4v10" />
      <path d="m8 11 4 4 4-4" />
      <path d="M5 19h14" />
    </svg>
  )
}

/** Two sheets, for "copy this". Drawn without the trailing margin the
 *  labelled icons carry, because it is the whole button. */
export function CopyIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M15 5H7a2 2 0 0 0-2 2v8" />
    </svg>
  )
}

/** The tick that stands in for it for a moment after a copy lands. */
export function CheckIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="m5 13 4 4 10-10" />
    </svg>
  )
}
