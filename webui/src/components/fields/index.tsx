import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cx } from '@/lib/util'
import { Button } from '@/components/ui'
import s from './fields.module.css'

/*
 * The field kit.
 *
 * Every control the parity baseline names has exactly one implementation
 * here, and each takes the same `{ value, onChange }` shape so `SchemaForm`
 * can pick one by `field.type` without a special case. Labels arrive from the
 * baseline verbatim — see `fromBaseline.ts`.
 */

interface Common {
  id?: string
  label: string
  hint?: string
  wide?: boolean
  children?: ReactNode
}

export function FieldShell({
  id,
  label,
  hint,
  wide,
  trailing,
  children,
}: Common & { trailing?: ReactNode }) {
  return (
    <div className={cx(s.field, wide && s.fieldWide)}>
      <div className={s.labelRow}>
        <label className={s.label} htmlFor={id}>
          {label}
        </label>
        {trailing}
      </div>
      {children}
      {hint && <div className={s.hint}>{hint}</div>}
    </div>
  )
}

// -------------------------------------------------------------------- text

export function TextField({
  label,
  hint,
  wide,
  value,
  onChange,
  placeholder,
  mono,
}: Common & {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  mono?: boolean
}) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} wide={wide}>
      <input
        id={id}
        className={cx(s.input, mono && s.mono)}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </FieldShell>
  )
}

export function TextAreaField({
  label,
  hint,
  wide,
  value,
  onChange,
  placeholder,
  lines = 3,
  mono,
}: Common & {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  lines?: number
  mono?: boolean
}) {
  const id = useId()
  const [expanded, setExpanded] = useState(false)
  // Gradio's `lines` is a minimum, not a cap. Long negative prompts (the V2
  // default is 1,300 characters) get a collapse toggle rather than a box that
  // eats the sidebar.
  const long = (value ?? '').length > 320
  const rows = expanded ? Math.max(lines, 14) : lines
  return (
    <FieldShell
      id={id}
      label={label}
      hint={hint}
      wide={wide}
      trailing={
        long && (
          <Button variant="ghost" size="sm" onClick={() => setExpanded((open) => !open)}>
            {expanded ? 'Collapse' : 'Expand'}
          </Button>
        )
      }
    >
      <textarea
        id={id}
        rows={rows}
        className={cx(s.textarea, mono && s.mono)}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </FieldShell>
  )
}

// ------------------------------------------------------------------ number

export function NumberField({
  label,
  hint,
  wide,
  value,
  onChange,
  step = 1,
  min,
  max,
}: Common & {
  value: number
  onChange: (next: number) => void
  step?: number
  min?: number
  max?: number
}) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} wide={wide}>
      <input
        id={id}
        type="number"
        className={cx(s.input, s.mono)}
        value={Number.isFinite(value) ? value : ''}
        step={step}
        min={min}
        max={max}
        onChange={(event) => {
          const next = event.target.value === '' ? 0 : Number(event.target.value)
          onChange(Number.isNaN(next) ? 0 : next)
        }}
      />
    </FieldShell>
  )
}

// ------------------------------------------------------------------ slider

/** Slider plus a number box.
 *
 *  The number box is the addition. Gradio's slider shows its value but will
 *  not let you type one, so setting Steps to exactly 23 is a drag-and-squint
 *  exercise on a control whose whole range is 1-60. It sits in the label row
 *  rather than beside the track, so a slider is one full-width row and two of
 *  them fit side by side in a dense group. */
export function SliderField({
  label,
  hint,
  wide,
  value,
  onChange,
  min = 0,
  max = 1,
  step = 0.01,
}: Common & {
  value: number
  onChange: (next: number) => void
  min?: number
  max?: number
  step?: number
}) {
  const id = useId()
  const safe = Number.isFinite(value) ? value : min
  const fill = max > min ? ((safe - min) / (max - min)) * 100 : 0
  return (
    <FieldShell
      id={id}
      label={label}
      hint={hint}
      wide={wide}
      trailing={
        <input
          type="number"
          aria-label={`${label} value`}
          className={cx(s.input, s.sliderNumber)}
          value={safe}
          min={min}
          max={max}
          step={step}
          onChange={(event) => {
            const next = Number(event.target.value)
            if (Number.isNaN(next)) return
            onChange(Math.min(max, Math.max(min, next)))
          }}
        />
      }
    >
      <input
        id={id}
        type="range"
        className={s.slider}
        style={{ ['--fill' as string]: `${fill}%` }}
        value={safe}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <div className={s.ticks}>
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </FieldShell>
  )
}

// ------------------------------------------------------------------ choice

export function SelectField({
  label,
  hint,
  wide,
  value,
  onChange,
  choices,
}: Common & {
  value: string
  onChange: (next: string) => void
  choices: string[]
}) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} wide={wide}>
      <select
        id={id}
        className={s.select}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value)}
      >
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    </FieldShell>
  )
}

export function RadioField({
  label,
  hint,
  wide,
  value,
  onChange,
  choices,
}: Common & {
  value: string
  onChange: (next: string) => void
  choices: string[]
}) {
  const name = useId()
  return (
    <FieldShell label={label} hint={hint} wide={wide}>
      <div className={s.radioGroup} role="radiogroup" aria-label={label}>
        {choices.map((choice) => (
          <label
            key={choice}
            className={cx(s.radio, choice === value && s.radioChecked)}
          >
            <input
              type="radio"
              name={name}
              className={s.radioDot}
              checked={choice === value}
              onChange={() => onChange(choice)}
            />
            <span>{choice}</span>
          </label>
        ))}
      </div>
    </FieldShell>
  )
}

// ---------------------------------------------------------------- checkbox

export function BoolField({
  label,
  hint,
  wide,
  value,
  onChange,
}: Common & { value: boolean; onChange: (next: boolean) => void }) {
  const id = useId()
  return (
    <div className={cx(s.field, wide && s.fieldWide)}>
      <label className={s.check} htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          className={s.checkBox}
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className={s.checkLabel}>{label}</span>
      </label>
      {hint && <div className={s.hint}>{hint}</div>}
    </div>
  )
}

// -------------------------------------------------------------- image drop

/** An image input: click, drop, or paste.
 *
 *  Paste matters — six of the baseline's Image labels literally say "paste
 *  with Ctrl+V", because that is how people get a screenshot in. Gradio
 *  bound it globally; here it is bound to the drop zone while it holds focus
 *  or the pointer, so two image inputs on one tab cannot both claim a paste. */
export function ImageDropField({
  label,
  hint,
  wide,
  value,
  onChange,
}: Common & { value: File | null; onChange: (next: File | null) => void }) {
  const [url, setUrl] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [size, setSize] = useState<{ width: number; height: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const zoneRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!value) {
      setUrl(null)
      setSize(null)
      return
    }
    const objectUrl = URL.createObjectURL(value)
    setUrl(objectUrl)
    const probe = new Image()
    probe.onload = () => setSize({ width: probe.naturalWidth, height: probe.naturalHeight })
    probe.src = objectUrl
    return () => URL.revokeObjectURL(objectUrl)
  }, [value])

  const accept = useCallback(
    (files: FileList | null) => {
      const file = files?.[0]
      if (file && file.type.startsWith('image/')) onChange(file)
    },
    [onChange],
  )

  useEffect(() => {
    const zone = zoneRef.current
    if (!zone) return
    function onPaste(event: ClipboardEvent) {
      if (!zone || !zone.matches(':hover, :focus-within')) return
      const item = Array.from(event.clipboardData?.items ?? []).find((entry) =>
        entry.type.startsWith('image/'),
      )
      const file = item?.getAsFile()
      if (file) {
        event.preventDefault()
        onChange(file)
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [onChange])

  return (
    <div className={cx(s.field, wide && s.fieldWide)} ref={zoneRef}>
      <div className={s.labelRow}>
        <span className={s.label}>{label}</span>
        {value && (
          <Button variant="ghost" size="sm" onClick={() => onChange(null)}>
            Remove
          </Button>
        )}
      </div>

      {url ? (
        <div className={s.dropPreview}>
          <img src={url} alt="" />
          <div className={s.dropBar}>
            <span className={s.dropMeta}>
              {size ? `${size.width} × ${size.height}` : ''}
            </span>
            <Button size="sm" onClick={() => inputRef.current?.click()}>
              Replace
            </Button>
          </div>
        </div>
      ) : (
        <div
          className={cx(s.drop, dragging && s.dropActive)}
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click()
          }}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            accept(event.dataTransfer.files)
          }}
        >
          <div>
            <span className={s.dropIcon} aria-hidden>
              ⬆
            </span>
            Drop an image, or click to choose
            <div className={s.dropHint}>Ctrl+V pastes from the clipboard</div>
          </div>
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(event) => accept(event.target.files)}
      />
      {hint && <div className={s.hint}>{hint}</div>}
    </div>
  )
}

// -------------------------------------------------------------------- file

export function FileField({
  label,
  hint,
  wide,
  value,
  onChange,
  accept,
}: Common & { value: File | null; onChange: (next: File | null) => void; accept?: string }) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <FieldShell label={label} hint={hint} wide={wide}>
      <div className={s.file}>
        <Button size="sm" onClick={() => inputRef.current?.click()}>
          Choose file
        </Button>
        <span className={s.fileName}>{value ? value.name : 'No file selected'}</span>
        {value && (
          <Button variant="ghost" size="sm" onClick={() => onChange(null)}>
            Clear
          </Button>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
    </FieldShell>
  )
}
