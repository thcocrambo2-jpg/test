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

/** The triangle that says a tile is a clip and not a still.
 *
 *  Filled, unlike every mark above it. Those are all 14px on a button, where
 *  a 1.8px stroke reads cleanly; this one sits over a picture that may be any
 *  colour at all, and an outlined triangle at that size is a smudge. */
export function PlayIcon() {
  return (
    <svg className={s.playMark} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 6.3v11.4L18.4 12z" />
    </svg>
  )
}

/* The filter's marks, each beside its word in the toolbar. */

/** A stack of sheets, for "everything". Not a grid of squares: that is
 *  what the tile-size control beside it draws. */
export function AllIcon() {
  return (
    <svg className={s.btnIcon} viewBox="0 0 24 24" aria-hidden="true">
      <path d="m12 3 9 5-9 5-9-5z" />
      <path d="m3 12 9 5 9-5" />
      <path d="m3 16 9 5 9-5" />
    </svg>
  )
}

/** A frame with a hill and a sun. */
export function ImageIcon() {
  return (
    <svg className={s.btnIcon} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="m21 16-5-5-9 8" />
    </svg>
  )
}

/** A camera body with its lens cone. */
export function VideoIcon() {
  return (
    <svg className={s.btnIcon} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="6" width="13" height="12" rx="2" />
      <path d="m16 10 5-3v10l-5-3" />
    </svg>
  )
}

/* The tile sizes, drawn as the grid each one gives: nine small tiles, four
 * middling ones, one big one. The whole segment, so no trailing margin. */

export function CompactIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 4h4v4H4zM10 4h4v4h-4zM16 4h4v4h-4zM4 10h4v4H4zM10 10h4v4h-4zM16 10h4v4h-4zM4 16h4v4H4zM10 16h4v4h-4zM16 16h4v4h-4z" />
    </svg>
  )
}

export function ComfortableIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  )
}

export function LargeIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
  )
}

/* The view tools. All of these are the whole button, so none carries the
 * labelled icons' trailing margin. */

/** Two sliders, for "show the view tools". */
export function ToolsIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="10" cy="17" r="2" />
    </svg>
  )
}

export function ZoomInIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="M8 11h6M11 8v6M20 20l-4-4" />
    </svg>
  )
}

export function ZoomOutIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="M8 11h6M20 20l-4-4" />
    </svg>
  )
}

/** A clockwise arc. */
export function RotateIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 12a8 8 0 1 1-2.3-5.7L20 8.6" />
      <path d="M20 3v5.6h-5.6" />
    </svg>
  )
}

/** An undo arrow, for "back to how it was". A hook rather than an arc, so
 *  it cannot be mistaken for rotate beside it. */
export function ResetIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
    </svg>
  )
}

/** Two arrowheads either side of a vertical mirror line. */
export function FlipHIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3v18" />
      <path d="M8 7 3 12l5 5V7zM16 7l5 5-5 5V7z" />
    </svg>
  )
}

/** The same, about a horizontal line. */
export function FlipVIcon() {
  return (
    <svg className={cx(s.btnIcon, s.btnIconAlone)} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 12h18" />
      <path d="M7 8l5-5 5 5H7zM7 16l5 5 5-5H7z" />
    </svg>
  )
}
