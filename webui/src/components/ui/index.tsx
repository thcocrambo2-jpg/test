import {
  createContext,
  useContext,
  useEffect,
  useId,
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
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  width?: number
}) {
  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [open, onClose])

  const titleId = useId()
  if (!open) return null

  return createPortal(
    <div className={s.backdrop} onMouseDown={onClose}>
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
