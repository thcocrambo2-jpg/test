import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { cx } from '@/lib/util'
import s from './ui.module.css'

// ------------------------------------------------------------------- button

type ButtonVariant = 'default' | 'primary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  block?: boolean
  iconOnly?: boolean
}

export function Button({
  variant = 'default',
  size = 'md',
  loading = false,
  block = false,
  iconOnly = false,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={cx(
        s.button,
        variant !== 'default' && s[variant],
        size !== 'md' && s[size],
        block && s.block,
        iconOnly && s.iconOnly,
        className,
      )}
    >
      {loading && <span className={s.spinner} aria-hidden />}
      {children}
    </button>
  )
}

// --------------------------------------------------------------------- card

export function Card({
  title,
  subtitle,
  actions,
  children,
  padded = true,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  padded?: boolean
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cx(s.card, className)}>
      {(title || actions) && (
        <header className={s.cardHead}>
          <div>
            <div className={s.cardTitle}>{title}</div>
            {subtitle && <div className={s.cardSub}>{subtitle}</div>}
          </div>
          {actions}
        </header>
      )}
      <div className={cx(padded && s.cardBody, bodyClassName)}>{children}</div>
    </section>
  )
}

// --------------------------------------------------------------------- pill

export function Pill({
  tone = 'default',
  children,
  onClick,
  title,
  className,
}: {
  tone?: 'default' | 'accent' | 'success' | 'warning' | 'danger'
  children: ReactNode
  onClick?: () => void
  title?: string
  className?: string
}) {
  const toneClass =
    tone === 'accent'
      ? s.pillAccent
      : tone === 'success'
        ? s.pillSuccess
        : tone === 'warning'
          ? s.pillWarning
          : tone === 'danger'
            ? s.pillDanger
            : undefined
  if (onClick) {
    return (
      <button
        type="button"
        title={title}
        onClick={onClick}
        className={cx(s.pill, toneClass, s.pillButton, className)}
      >
        {children}
      </button>
    )
  }
  return (
    <span title={title} className={cx(s.pill, toneClass, className)}>
      {children}
    </span>
  )
}

// -------------------------------------------------------------------- alert

/** A persistent, dismissible message.
 *
 *  The Gradio app had no such thing: every error was a `❌ …` string written
 *  into the status textbox, which the next one-second poll overwrote. So an
 *  error you were not looking at when it happened did not exist. This stays
 *  until it is dismissed, and it is keyed to a job so two failures do not
 *  collapse into one line. */
export function Alert({
  tone = 'error',
  title,
  children,
  onDismiss,
}: {
  tone?: 'error' | 'warning' | 'info'
  title?: ReactNode
  children: ReactNode
  onDismiss?: () => void
}) {
  const toneClass =
    tone === 'warning' ? s.alertWarning : tone === 'info' ? s.alertInfo : s.alertError
  const glyph = tone === 'info' ? 'ℹ' : tone === 'warning' ? '⚠' : '✕'
  return (
    <div className={cx(s.alert, toneClass)} role="alert">
      <span className={s.alertIcon} aria-hidden>
        {glyph}
      </span>
      <div className={s.alertBody}>
        {title && <div className={s.alertTitle}>{title}</div>}
        <div className={s.alertText}>{children}</div>
      </div>
      {onDismiss && (
        <button
          type="button"
          className={s.alertClose}
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          ✕
        </button>
      )}
    </div>
  )
}

// ----------------------------------------------------------------- progress

/** Determinate whenever the backend has sent a {step, total}; indeterminate
 *  while queued or before the first one arrives. client.py has been yielding
 *  that pair all along — the old UI simply never rendered it. */
export function ProgressBar({
  step,
  total,
  label,
  detail,
}: {
  step?: number
  total?: number
  label?: ReactNode
  detail?: ReactNode
}) {
  const determinate = typeof step === 'number' && typeof total === 'number' && total > 0
  const percent = determinate ? Math.min(100, Math.round((step / total) * 100)) : 0
  return (
    <div className={cx(s.progress, !determinate && s.progressIndeterminate)}>
      {(label || detail || determinate) && (
        <div className={s.progressMeta}>
          <span>{label}</span>
          <span className={s.progressCount}>
            {determinate ? `${step}/${total} · ${percent}%` : detail}
          </span>
        </div>
      )}
      <div
        className={s.progressTrack}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={determinate ? total : undefined}
        aria-valuenow={determinate ? step : undefined}
      >
        <div className={s.progressFill} style={{ width: `${percent}%` }} />
      </div>
    </div>
  )
}

// ----------------------------------------------------------------- skeleton

export function Skeleton({
  width,
  height,
  radius,
  className,
  style,
}: {
  width?: string | number
  height?: string | number
  radius?: string
  className?: string
  style?: React.CSSProperties
}) {
  return (
    <div
      className={cx(s.skeleton, className)}
      style={{ width, height, borderRadius: radius, ...style }}
      aria-hidden
    />
  )
}

// --------------------------------------------------------------- emptystate

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode
  title: ReactNode
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className={s.empty}>
      {icon && (
        <div className={s.emptyIcon} aria-hidden>
          {icon}
        </div>
      )}
      <div className={s.emptyTitle}>{title}</div>
      {children && <p className={s.emptyText}>{children}</p>}
      {action}
    </div>
  )
}

// ---------------------------------------------------------------- segmented

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T
  options: { value: T; label: ReactNode }[]
  onChange: (next: T) => void
  ariaLabel?: string
}) {
  return (
    <div className={s.segmented} role="tablist" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={cx(s.segment, option.value === value && s.segmentActive)}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

// -------------------------------------------------------------------- modal

/** A real modal — with a real Escape handler and a real backdrop click.
 *
 *  Worth noting because the current pricing page's modals are CSS-only
 *  `:target` hacks, and only because Gradio strips `<script>` out of
 *  `gr.HTML` (theme.py:1724). In React the constraint is gone. */
export function Modal({
  open,
  onClose,
  title,
  children,
  width,
  zIndex,
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  width?: number
  /** Overrides `--z-modal`, for a modal opened on top of another layer that
   *  already sits at it — see `ConfirmHost`. */
  zIndex?: number | string
}) {
  /* Keys are taken in the capture phase and stopped there.
   *
   * Every other global shortcut in the app listens on `window` in the bubble
   * phase — the lightbox's Escape and arrows, for two — and window capture runs
   * before all of them. Without this, one Escape pressed on a confirmation
   * opened over the lightbox would answer the question *and* close the picture
   * behind it, and an arrow key would flip the picture underneath the dialog.
   * An open modal should be the only thing listening.
   *
   * The one thing to know when putting a field in a modal: React's own
   * listeners never see these keys either, so an `onKeyDown` on modal content
   * will not fire. Typing is unaffected — characters, Tab and Enter on a
   * focused button are default actions, and `onChange` rides `input`, none of
   * which propagation touches. Only an explicit key handler needs rethinking. */
  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) {
      event.stopPropagation()
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown, true)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = previous
    }
  }, [open, onClose])

  const titleId = useId()
  if (!open) return null

  return createPortal(
    <div className={s.backdrop} style={zIndex ? { zIndex } : undefined} onMouseDown={onClose}>
      <div
        className={s.modal}
        style={width ? { width: `min(${width}px, 100%)` } : undefined}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {title && (
          <header className={s.modalHead}>
            <h2 className={s.modalTitle} id={titleId}>
              {title}
            </h2>
            <Button variant="ghost" size="sm" iconOnly onClick={onClose} aria-label="Close">
              ✕
            </Button>
          </header>
        )}
        <div className={s.modalBody}>{children}</div>
      </div>
    </div>,
    document.body,
  )
}

// ------------------------------------------------------------------- toasts

interface Toast {
  id: number
  text: string
}

const ToastContext = createContext<(text: string) => void>(() => {})

export function useToast() {
  return useContext(ToastContext)
}

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  function push(text: string) {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, text }])
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 2600)
  }

  return (
    <ToastContext.Provider value={push}>
      {children}
      {toasts.length > 0 &&
        createPortal(
          <div className={s.toastHost} role="status" aria-live="polite">
            {toasts.map((toast) => (
              <div key={toast.id} className={s.toast}>
                {toast.text}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </ToastContext.Provider>
  )
}

// ------------------------------------------------------------------ confirm

export interface ConfirmOptions {
  title?: string
  body: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'danger' | 'default'
}

const ConfirmContext = createContext<(options: ConfirmOptions) => Promise<boolean>>(
  () => Promise.resolve(false),
)

/** Ask a yes/no question and await the answer.
 *
 *  A promise and not a `<ConfirmDialog>` the caller renders, because the
 *  callers are already `async` functions that ask in the middle of doing
 *  something — `if (!(await confirm(…))) return` is the same shape the
 *  `window.confirm` it replaces had, where a declarative dialog would split
 *  each delete into a pending-state half and a do-it half. */
export function useConfirm() {
  return useContext(ConfirmContext)
}

export function ConfirmHost({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  // Held in a ref rather than in state: settling the promise is not something
  // the render output depends on, and a re-render must not lose it.
  const settle = useRef<((answer: boolean) => void) | null>(null)

  const ask = useCallback((next: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      // A second question asked while one is open answers the first as "no"
      // rather than abandoning a caller mid-`await` forever.
      settle.current?.(false)
      settle.current = resolve
      setOptions(next)
    })
  }, [])

  const answer = useCallback((value: boolean) => {
    setOptions(null)
    const resolve = settle.current
    settle.current = null
    resolve?.(value)
  }, [])

  // Escape, the backdrop and the ✕ all arrive here, so every way out of the
  // dialog is a "no" and none of them leaves the promise pending.
  const cancel = useCallback(() => answer(false), [answer])

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      <Modal
        open={options !== null}
        onClose={cancel}
        title={options?.title}
        zIndex="var(--z-confirm)"
      >
        <div>{options?.body}</div>
        <div className={s.modalFoot}>
          <Button variant="ghost" onClick={cancel} autoFocus>
            {options?.cancelLabel ?? 'Cancel'}
          </Button>
          <Button
            variant={options?.tone === 'danger' ? 'danger' : 'primary'}
            onClick={() => answer(true)}
          >
            {options?.confirmLabel ?? 'Confirm'}
          </Button>
        </div>
      </Modal>
    </ConfirmContext.Provider>
  )
}

// ------------------------------------------------------------------- inline

/** `**bold**` and `` `code` `` inside one line, and nothing else.
 *
 *  Not a markdown library. Exactly one string in this application arrives
 *  with markup in it: the model info line, which handlers._model_info_text
 *  and its three siblings build for a `gr.Markdown` under the dropdown —
 *  "**Turbo** · defaults: 8 steps, CFG 1 · ⚠️ **not downloaded yet**". It is
 *  worth rendering rather than stripping, because the emphasis is on the half
 *  that matters, and it is not worth 40 KB of parser. */
export function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={index}>{part.slice(2, -2)}</strong>
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 1) {
          return <code key={index}>{part.slice(1, -1)}</code>
        }
        return <span key={index}>{part}</span>
      })}
    </>
  )
}
