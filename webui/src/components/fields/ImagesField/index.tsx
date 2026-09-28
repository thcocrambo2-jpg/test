import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import type { MediaItem } from '@/api/types'
import { Alert } from '@/components/ui'
import { Lightbox } from '@/features/gallery/Lightbox'
import { cx } from '@/lib/util'
import { RecentStrip } from '../RecentStrip'
import f from '../fields.module.css'
import s from './images.module.css'

/*
 * A numbered list of pictures: the `images` field.
 *
 * The number is the point. The prompt names pictures by it ("the jacket from
 * image 2"), and it is the slot the handler reads them into, so every tile
 * shows it as text and it only ever changes when the list is reordered or a
 * picture is removed. Image 1 is the main one — it gets an accent frame and
 * the word, so colour is never the only signal.
 *
 * Every way into the single image input works here too, several at a time:
 * click (multi-select), drop (several files), paste, and the recent strip.
 * What cannot go in — a file that is not a picture, one past the limit — is
 * said inside the field, next to what it is about, and the rest still goes in.
 *
 * The value is a `File[]` and nothing else, for the reason the single input's
 * is a `File`: the preview, the size probe and `resolveUploads` all read it
 * the same way whatever it came from.
 */

type Verb = 'dropped' | 'pasted' | 'chose'

interface Notice {
  id: number
  title: ReactNode
  text: ReactNode
}

interface Move {
  from: number
  to: number
}

/* Object URLs by file. Module level, so a tab switch — which unmounts the
 * field while its files stay in `tabState` — does not re-read every picture;
 * a URL is revoked when its file leaves the field, not when the field goes. */
const URLS = new Map<File, string>()
const SIZES = new WeakMap<File, { width: number; height: number }>()
const KEYS = new WeakMap<File, number>()
let nextKey = 0

function urlOf(file: File): string {
  let url = URLS.get(file)
  if (!url) {
    url = URL.createObjectURL(file)
    URLS.set(file, url)
  }
  return url
}

function keyOf(file: File): number {
  let key = KEYS.get(file)
  if (key === undefined) {
    key = nextKey++
    KEYS.set(file, key)
  }
  return key
}

function isImage(file: File): boolean {
  return file.type.startsWith('image/')
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

/** "4", "4 and 5", "4, 5 and 6". */
function listNumbers(numbers: number[]): string {
  if (numbers.length < 2) return numbers.join('')
  return `${numbers.slice(0, -1).join(', ')} and ${numbers[numbers.length - 1]}`
}

function ordinal(n: number): string {
  const tens = n % 100
  if (tens >= 11 && tens <= 13) return `${n}th`
  return `${n}${{ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th'}`
}

/** `order` with the entry at `from` moved to `to`. */
function moved<T>(order: readonly T[], from: number, to: number): T[] {
  const next = [...order]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

/** How many pictures a drag is carrying, read while it is still in the air.
 *  Browsers expose each item's type before the drop but not its name. */
function carried(transfer: DataTransfer): number {
  const files = Array.from(transfer.items).filter((item) => item.kind === 'file')
  const images = files.filter((item) => item.type.startsWith('image/'))
  return images.length || files.length
}

function hasFiles(transfer: DataTransfer): boolean {
  return Array.from(transfer.types).includes('Files')
}

const GripIcon = (
  <svg className={cx(s.icon, s.iconFill)} viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="9" cy="6" r="1.8" />
    <circle cx="15" cy="6" r="1.8" />
    <circle cx="9" cy="12" r="1.8" />
    <circle cx="15" cy="12" r="1.8" />
    <circle cx="9" cy="18" r="1.8" />
    <circle cx="15" cy="18" r="1.8" />
  </svg>
)

const RemoveIcon = (
  <svg className={s.icon} viewBox="0 0 24 24" aria-hidden="true">
    <path d="M17 7 7 17" />
    <path d="m7 7 10 10" />
  </svg>
)

export function ImagesField({
  label,
  hint,
  value,
  onChange,
  max = 10,
}: {
  label: string
  hint?: ReactNode
  value: File[]
  onChange: (next: File[]) => void
  max?: number
}) {
  const labelId = useId()
  const hintId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const zoneRef = useRef<HTMLDivElement>(null)
  const [notices, setNotices] = useState<Notice[]>([])
  const [incoming, setIncoming] = useState(0)
  const [move, setMove] = useState<Move | null>(null)
  // The drag in progress, as the window listeners read it on pointer up.
  const moveRef = useRef<Move | null>(null)
  moveRef.current = move
  const [viewing, setViewing] = useState<number | null>(null)
  const [, setProbed] = useState(0)
  const depth = useRef(0)
  const noticeId = useRef(0)
  const focusNext = useRef<number | null>(null)
  const previous = useRef<File[]>(value)

  const room = Math.max(0, max - value.length)
  const full = room === 0

  // A file that has left the field gives its object URL back.
  useEffect(() => {
    for (const file of previous.current) {
      if (!value.includes(file)) {
        const url = URLS.get(file)
        if (url) URL.revokeObjectURL(url)
        URLS.delete(file)
      }
    }
    previous.current = value
  }, [value])

  // Each picture's size, for the chip and the label. Read once per file.
  useEffect(() => {
    let live = true
    for (const file of value) {
      if (SIZES.has(file)) continue
      const probe = new Image()
      probe.onload = () => {
        SIZES.set(file, { width: probe.naturalWidth, height: probe.naturalHeight })
        if (live) setProbed((n) => n + 1)
      }
      probe.src = urlOf(file)
    }
    return () => {
      live = false
    }
  }, [value])

  // Alt+arrow moves a tile; focus follows it to where it went.
  useEffect(() => {
    if (focusNext.current === null) return
    const index = focusNext.current
    focusNext.current = null
    zoneRef.current
      ?.querySelector<HTMLButtonElement>(`[data-ref-pick="${index}"]`)
      ?.focus()
  }, [value])

  const add = useCallback(
    (files: File[], verb: Verb) => {
      if (files.length === 0) return
      const images = files.filter(isImage)
      const others = files.filter((file) => !isImage(file))
      const taken = images.slice(0, Math.max(0, max - value.length))
      const left = images.slice(taken.length)
      if (taken.length > 0) onChange([...value, ...taken])

      const next: Notice[] = []
      if (others.length > 0) {
        const added =
          taken.length > 0
            ? `the other ${plural(taken.length, 'file')} ${taken.length === 1 ? 'was' : 'were'} added`
            : 'nothing else was added'
        next.push({
          id: noticeId.current++,
          title:
            others.length === 1
              ? `“${others[0].name}” isn't an image`
              : `${plural(others.length, 'file')} aren't images`,
          text: `${others.length === 1 ? 'It was' : 'They were'} skipped; ${added}. References can be PNG, JPEG or WebP.`,
        })
      }
      if (left.length > 0) {
        next.push({
          id: noticeId.current++,
          title: `Up to ${max} references`,
          text: (
            <>
              {left.length} of the {plural(images.length, 'file')} you {verb}{' '}
              {left.length === 1 ? "wasn't" : "weren't"} added:{' '}
              {left.map((file, index) => (
                <span key={index}>
                  {index > 0 && ', '}
                  <code>{file.name}</code>
                </span>
              ))}
              . Remove a reference to make room.
            </>
          ),
        })
      }
      setNotices(next)
    },
    [max, onChange, value],
  )

  const moveTo = useCallback(
    (from: number, to: number) => {
      if (from === to || to < 0 || to >= value.length) return
      onChange(moved(value, from, to))
    },
    [onChange, value],
  )

  const remove = useCallback(
    (index: number) => {
      onChange(value.filter((_, at) => at !== index))
      setNotices([])
    },
    [onChange, value],
  )

  // Paste, while the pointer or the focus is on this field — the same rule
  // the single input uses, so two image inputs cannot both claim one paste.
  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      const zone = zoneRef.current
      if (!zone || !zone.matches(':hover, :focus-within')) return
      const files = Array.from(event.clipboardData?.items ?? [])
        .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
        .map((item) => item.getAsFile())
        .filter((file): file is File => file !== null)
      if (files.length === 0) return
      event.preventDefault()
      add(files, 'pasted')
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [add])

  // Reordering by pointer, so a finger works as well as a mouse. Window
  // listeners rather than pointer capture: the tile being dragged is moved
  // around the DOM while it is dragged, and capture does not survive that.
  useEffect(() => {
    if (!move) return
    const from = move.from
    function onMove(event: PointerEvent) {
      const slot = document
        .elementFromPoint(event.clientX, event.clientY)
        ?.closest<HTMLElement>('[data-ref-slot]')
      if (!slot || !zoneRef.current?.contains(slot)) return
      const to = Number(slot.dataset.refSlot)
      setMove((current) => (current && current.to !== to ? { from, to } : current))
    }
    function onUp() {
      const current = moveRef.current
      setMove(null)
      if (current) moveTo(current.from, current.to)
    }
    function onCancel() {
      setMove(null)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
    }
  }, [move, moveTo])

  function keyMove(event: React.KeyboardEvent, index: number) {
    if (!event.altKey) return
    const step =
      event.key === 'ArrowLeft' || event.key === 'ArrowUp'
        ? -1
        : event.key === 'ArrowRight' || event.key === 'ArrowDown'
          ? 1
          : 0
    if (step === 0) return
    event.preventDefault()
    const to = index + step
    if (to < 0 || to >= value.length) return
    focusNext.current = to
    moveTo(index, to)
  }

  // Files dragged over the field. Counted in and out, because dragenter and
  // dragleave fire for every child the pointer crosses on the way.
  const dropHandlers = {
    onDragEnter(event: React.DragEvent) {
      if (!hasFiles(event.dataTransfer)) return
      event.preventDefault()
      depth.current += 1
      setIncoming(carried(event.dataTransfer) || 1)
    },
    onDragOver(event: React.DragEvent) {
      if (!hasFiles(event.dataTransfer)) return
      event.preventDefault()
      event.dataTransfer.dropEffect = 'copy'
    },
    onDragLeave(event: React.DragEvent) {
      if (!hasFiles(event.dataTransfer)) return
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setIncoming(0)
    },
    onDrop(event: React.DragEvent) {
      if (!hasFiles(event.dataTransfer)) return
      event.preventDefault()
      depth.current = 0
      setIncoming(0)
      add(Array.from(event.dataTransfer.files), 'dropped')
    },
  }

  const choose = () => inputRef.current?.click()

  // What is on screen: the list, or the list as it would be after the drag
  // in progress. Numbers are positions in this, so they change as you drag
  // and what you see when you let go is what the handler gets.
  const indices = value.map((_, index) => index)
  const shown = move ? moved(indices, move.from, move.to) : indices
  const arriving = Math.min(incoming, room)

  const items: MediaItem[] = value.map((file, index) => {
    const size = SIZES.get(file)
    return {
      id: `${index + 1}/${file.name}`,
      url: urlOf(file),
      path: file.name,
      width: size?.width ?? 0,
      height: size?.height ?? 0,
      kind: 'image',
      createdAt: new Date(file.lastModified).toISOString(),
    }
  })

  function tile(index: number, position: number, flying = false): ReactNode {
    const file = value[index]
    const size = SIZES.get(file)
    const number = position + 1
    const main = number === 1
    const renumbered = number !== index + 1
    const badge = (
      <span className={s.num} aria-hidden="true">
        {renumbered ? (
          <>
            <span className={s.old}>{index + 1}</span>→ {number}
          </>
        ) : (
          number
        )}
        {main && !renumbered && <small>Main</small>}
      </span>
    )
    const sizeText = size ? `${size.width} × ${size.height}` : ''

    if (flying) {
      return (
        <div className={cx(s.tile, main && s.main)}>
          <div className={s.pick}>
            <img src={urlOf(file)} alt="" draggable={false} />
          </div>
          {badge}
          <span className={s.grip}>{GripIcon}</span>
        </div>
      )
    }

    return (
      <>
        <button
          type="button"
          className={s.pick}
          title="Open full size"
          aria-label={`Image ${number}${main ? ', main reference' : ''}: ${file.name}${sizeText ? `, ${sizeText}` : ''}`}
          data-ref-pick={index}
          onClick={() => setViewing(index)}
          onKeyDown={(event) => keyMove(event, index)}
        >
          <img src={urlOf(file)} alt="" draggable={false} />
        </button>
        <span className={s.scrim} aria-hidden="true" />
        {badge}
        <button
          type="button"
          className={s.grip}
          title="Drag to reorder"
          aria-label={`Reorder image ${number}: drag, or Alt+arrow keys`}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.preventDefault()
            setMove({ from: index, to: index })
          }}
          onKeyDown={(event) => keyMove(event, index)}
        >
          {GripIcon}
        </button>
        <button
          type="button"
          className={s.remove}
          title="Remove"
          aria-label={`Remove image ${number}`}
          onClick={() => remove(index)}
        >
          {RemoveIcon}
        </button>
        {sizeText && (
          <span className={s.size} aria-hidden="true">
            {sizeText}
          </span>
        )}
      </>
    )
  }

  const displaced = move && move.from !== move.to ? shown[move.from] : null

  return (
    <div className={cx(f.field, f.fieldWide, s.field)} ref={zoneRef}>
      <div className={f.labelRow}>
        <span className={f.label} id={labelId}>
          {label}
        </span>
      </div>

      {value.length === 0 ? (
        <div
          className={cx(f.drop, incoming > 0 && f.dropActive)}
          role="button"
          tabIndex={0}
          aria-labelledby={labelId}
          aria-describedby={hint ? hintId : undefined}
          onClick={choose}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              choose()
            }
          }}
          {...dropHandlers}
        >
          <div>
            <span className={f.dropIcon} aria-hidden>
              ⬆
            </span>
            Drop up to {max} images, or click to choose
            <div className={f.dropHint}>
              Pick several at once · Ctrl+V pastes from the clipboard
            </div>
          </div>
        </div>
      ) : (
        <div className={cx(s.target, incoming > 0 && s.targetActive)} {...dropHandlers}>
          <ol className={s.grid} aria-labelledby={labelId}>
            {shown.map((index, position) =>
              move && index === move.from ? (
                <li key={keyOf(value[index])} className={s.gap} data-ref-slot={position} aria-hidden="true">
                  {tile(index, position, true)}
                </li>
              ) : (
                <li
                  key={keyOf(value[index])}
                  className={cx(s.tile, position === 0 && s.main)}
                  data-ref-slot={position}
                >
                  {tile(index, position)}
                </li>
              ),
            )}
            {Array.from({ length: arriving }, (_, offset) => (
              <li key={`incoming-${offset}`} className={s.incoming} aria-hidden="true">
                <div>
                  {value.length + offset + 1}
                  <span>new</span>
                </div>
              </li>
            ))}
            {!full && arriving === 0 && (
              <li>
                <button
                  type="button"
                  className={s.add}
                  aria-label={`Add reference images: ${value.length} of ${max} used. Choose files, drop them here, or paste with Ctrl+V.`}
                  onClick={choose}
                >
                  <span className={s.addIcon} aria-hidden="true">
                    +
                  </span>
                  <span>Add images</span>
                  <span className={s.addCount}>
                    {value.length} / {max}
                  </span>
                  <span className={s.addHint}>drop or paste</span>
                </button>
              </li>
            )}
          </ol>
        </div>
      )}

      {arriving > 0 && (
        <p className={f.hint} aria-live="polite">
          <strong>
            Drop to add {plural(arriving, 'image')}
            {incoming > arriving ? ` of ${incoming}` : ''}
          </strong>
          :{' '}
          {arriving === 1
            ? `it becomes image ${value.length + 1}.`
            : `they become ${listNumbers(
                Array.from({ length: arriving }, (_, offset) => value.length + offset + 1),
              )}.`}
        </p>
      )}
      {move && move.from !== move.to && displaced !== null && (
        <p className={f.hint} aria-live="polite">
          <strong>
            Moving image {move.from + 1} ({value[move.from].name}) to {ordinal(move.to + 1)}.
          </strong>{' '}
          {value[displaced].name} becomes image {move.from + 1}.
        </p>
      )}

      {notices.map((notice) => (
        <Alert
          key={notice.id}
          tone="warning"
          title={notice.title}
          onDismiss={() => setNotices((all) => all.filter((n) => n.id !== notice.id))}
        >
          {notice.text}
        </Alert>
      ))}

      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(event) => {
          add(Array.from(event.target.files ?? []), 'chose')
          // Cleared so choosing the same file again is still a change.
          event.target.value = ''
        }}
      />

      <RecentStrip
        value={value}
        onPick={(file) => add([file], 'chose')}
        note={
          full
            ? 'full: remove a reference to add one'
            : `click one to add it as image ${value.length + 1}`
        }
        disabled={full}
      />

      {hint && (
        <div className={f.hint} id={hintId}>
          {hint}
        </div>
      )}

      {viewing !== null && items[viewing] && (
        <Lightbox
          items={items}
          index={viewing}
          onIndex={setViewing}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  )
}
