import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { Button, Pill, useToast } from '@/components/ui'
import { useHandoff } from '@/store/handoff'
import { cx, relativeTime, useCopy } from '@/lib/util'
import s from './gallery.module.css'

/*
 * One picture, a filmstrip, and nothing in the way.
 *
 * The pricing page's lightbox is CSS-only — a `:target` hack — because Gradio
 * strips `<script>` out of `gr.HTML` (theme.py:1724). That constraint is gone
 * here, so this one has arrow keys, Escape, a filmstrip that scrolls the
 * active thumb into view, and a delete that asks first.
 */
export function Lightbox({
  items,
  index,
  onIndex,
  onClose,
  onDelete,
}: {
  items: MediaItem[]
  index: number
  onIndex: (next: number) => void
  onClose: () => void
  onDelete?: (item: MediaItem) => void
}) {
  const item = items[index]
  const stripRef = useRef<HTMLDivElement>(null)
  const { copied, copy } = useCopy()
  const reuse = useReuse(item)

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowLeft') onIndex(Math.max(0, index - 1))
      if (event.key === 'ArrowRight') onIndex(Math.min(items.length - 1, index + 1))
    }
    window.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [index, items.length, onClose, onIndex])

  // Scroll the active thumb into view by asking the DOM where it is, rather
  // than by computing a pixel offset from a thumb width we would then have to
  // keep in sync with the stylesheet.
  useEffect(() => {
    const strip = stripRef.current
    const active = strip?.children[index] as HTMLElement | undefined
    active?.scrollIntoView({ block: 'nearest', inline: 'center' })
  }, [index])

  if (!item) return null

  return createPortal(
    <div className={s.lightbox} role="dialog" aria-modal="true" aria-label="Image viewer">
      <header className={s.lightboxHead}>
        <span className={s.lightboxTitle}>{item.prompt || item.path}</span>
        <div style={{ display: 'flex', gap: 'var(--s-2)', alignItems: 'center' }}>
          <Pill>
            {index + 1} / {items.length}
          </Pill>
          {reuse.can && (
            <Button
              size="sm"
              variant="primary"
              loading={reuse.busy}
              onClick={() => void reuse.load()}
              title={`Load the settings this was made with into ${reuse.label}`}
            >
              ▶️ Load these settings
            </Button>
          )}
          {onDelete && (
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                if (window.confirm('Delete this file permanently?')) onDelete(item)
              }}
            >
              Delete
            </Button>
          )}
          <Button size="sm" variant="ghost" iconOnly onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
      </header>

      <div className={s.lightboxStage} onClick={onClose}>
        {index > 0 && (
          <button
            type="button"
            className={cx(s.arrow, s.arrowLeft)}
            aria-label="Previous"
            onClick={(event) => {
              event.stopPropagation()
              onIndex(index - 1)
            }}
          >
            ‹
          </button>
        )}
        {item.kind === 'video' ? (
          <video
            className={s.lightboxImage}
            src={item.url}
            controls
            autoPlay
            onClick={(event) => event.stopPropagation()}
          />
        ) : (
          <img
            className={s.lightboxImage}
            src={item.url}
            alt={item.prompt ?? ''}
            onClick={(event) => event.stopPropagation()}
          />
        )}
        {index < items.length - 1 && (
          <button
            type="button"
            className={cx(s.arrow, s.arrowRight)}
            aria-label="Next"
            onClick={(event) => {
              event.stopPropagation()
              onIndex(index + 1)
            }}
          >
            ›
          </button>
        )}
      </div>

      <div>
        <div className={s.strip} ref={stripRef}>
          {items.map((entry, position) => (
            <button
              key={entry.id}
              type="button"
              className={cx(s.thumb, position === index && s.thumbActive)}
              onClick={() => onIndex(position)}
              aria-label={`Image ${position + 1}`}
            >
              <img src={entry.thumbUrl ?? entry.url} alt="" />
            </button>
          ))}
        </div>
        <div className={s.lightboxHead}>
          <div className={s.lightboxMeta}>
            <span>
              {item.width} × {item.height}
            </span>
            {item.seed != null && <span>seed {item.seed}</span>}
            <span>{relativeTime(item.createdAt)}</span>
          </div>
          <Pill tone={copied ? 'success' : 'default'} onClick={() => copy(item.path)}>
            {copied ? 'Copied' : item.path}
          </Pill>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** "Load these settings" for one generated file.
 *
 *  Every finished prompt files a recipe under the path it wrote (recipes.py),
 *  so a picture can be traced back to the controls that made it. Two values
 *  are deliberately not restored as recorded, and it is the whole point of
 *  the button: **Seed** becomes the seed that picture actually ran on rather
 *  than whatever was in the box, and **Random seed** goes off. Together they
 *  are the difference between "the same settings" and "the same image", and
 *  the server applies both (see the apply route).
 *
 *  Nothing is the ordinary answer. Anything generated before this pod started
 *  keeping recipes, or copied into the output folder by hand, has none — and
 *  a recipe for a tab this licence does not grant can be read but not loaded,
 *  which is what `canLoad` says.
 */
function useReuse(item: MediaItem | undefined) {
  const { data: schemas } = useSchemas()
  const navigate = useNavigate()
  const offer = useHandoff((state) => state.offer)
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  const schema = schemas?.find((candidate) => candidate.key === item?.tab)
  const can = Boolean(item && schema)

  async function load() {
    if (!item || !schema) return
    setBusy(true)
    try {
      const values = await api.applyRecipe(schema.key, item.id)
      if (Object.keys(values).length === 0) {
        toast('No recipe on file for that one.')
        return
      }
      offer(schema.key, values)
      navigate(schema.route)
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  return { can, busy, load, label: schema?.label ?? '' }
}
