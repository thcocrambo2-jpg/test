import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { Button, Pill, useToast } from '@/components/ui'
import { useHandoff } from '@/store/handoff'
import { cx, fileName, relativeTime, saveFile, useCopy } from '@/lib/util'
import { CheckIcon, CopyIcon, DownloadIcon } from './icons'
import s from './gallery.module.css'

/*
 * One picture, a filmstrip, and nothing in the way.
 *
 * The pricing page's lightbox is CSS-only — a `:target` hack — because Gradio
 * strips `<script>` out of `gr.HTML` (theme.py:1724). That constraint is gone
 * here, so this one has arrow keys, Escape, a filmstrip that scrolls the
 * active thumb into view, and a delete that asks first.
 */
/** How far either side of the current picture to warm the cache.
 *
 *  Two, not ten: these are multi-megabyte lossless PNGs, and a lookahead
 *  wider than anyone arrows in the time one takes to arrive is bandwidth
 *  spent on pictures that will be evicted before they are looked at. */
const PREFETCH_AHEAD = 2

/** URLs already handed to the browser to fetch, so no picture is asked for
 *  twice however fast the arrow key is held.
 *
 *  Bookkeeping only — a set of strings. The bytes live in the HTTP cache,
 *  where `/media` puts them for a day (`Cache-Control: private,
 *  max-age=86400`), which is both a better cache than anything kept here
 *  and one that does not hold decoded bitmaps at ~4 bytes a pixel.
 *
 *  Capped so a long browse does not grow it without end. Evicting a URL
 *  does not evict the file: the worst an eviction costs is one re-request
 *  that the HTTP cache answers immediately. */
const PREFETCHED = new Set<string>()
const PREFETCH_MEMORY = 200

function prefetch(url: string): void {
  if (PREFETCHED.has(url)) return
  if (PREFETCHED.size >= PREFETCH_MEMORY) {
    // A Set iterates in insertion order, so the first key is the oldest.
    const oldest = PREFETCHED.values().next().value
    if (oldest !== undefined) PREFETCHED.delete(oldest)
  }
  PREFETCHED.add(url)
  const image = new Image()
  image.src = url
}

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
  const stageRef = useRef<HTMLImageElement>(null)
  const { copied, copy } = useCopy()
  /* A second copier, not the one the path pill uses: copying the prompt
   * should not light up the path's "Copied", or the other way round. */
  const promptCopy = useCopy()
  const reuse = useReuse(item)
  const [saving, setSaving] = useState(false)

  /** Save the file on screen. The button exists because the alternative is
   *  right-click → Save image as…, which is not a gesture a phone has. */
  async function save() {
    setSaving(true)
    try {
      await saveFile(item.url, fileName(item.id))
    } catch {
      /* Nothing to say that the browser's own failed-download row does not
       * already say, and this dialog has no toast of its own. */
    } finally {
      setSaving(false)
    }
  }

  /* Which full-resolution file is actually on screen.
   *
   * Held as the URL rather than a boolean so it falsifies itself in the
   * same render that `index` changes: a `useEffect` resetting a flag runs
   * *after* paint, which is one frame of the previous picture's `ready`
   * state applied to the next picture's `src` — a flash of nothing. */
  const [loaded, setLoaded] = useState<string | null>(null)
  const ready = item?.kind === 'video' || loaded === item?.url

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

  // A cached picture can finish loading before React attaches `onLoad`, and
  // then the event never fires and the stage stays at opacity 0 forever.
  // `complete` is the same question asked after the fact.
  useEffect(() => {
    if (item && stageRef.current?.complete) setLoaded(item.url)
  }, [item])

  /* Fetch the neighbours, so arrowing lands on something already in the
   * browser's cache instead of starting a multi-megabyte download.
   *
   * Deliberately *after* the current picture has loaded: these share one
   * connection with the thing being looked at, and starving that to warm
   * something nobody has asked for yet would trade the visible wait for a
   * longer one. `ready` is also set on error, so a file that 404s does not
   * wedge the lookahead.
   *
   * Nothing is cancelled on the way out. A prefetch that is still in flight
   * when you arrow past it is one you very likely still want — the browser
   * caps its own concurrency, and `prefetch` refuses to ask twice. */
  useEffect(() => {
    if (!ready) return
    for (let offset = -PREFETCH_AHEAD; offset <= PREFETCH_AHEAD; offset += 1) {
      if (offset === 0) continue
      const neighbour = items[index + offset]
      // A video's `url` is the whole file and its `thumbUrl` is that same
      // file again (gallery_index does not frame-grab), so prefetching one
      // means pulling the entire clip to show a tile. Left to the <video>.
      if (!neighbour || neighbour.kind === 'video') continue
      prefetch(neighbour.url)
    }
  }, [ready, index, items])

  if (!item) return null

  return createPortal(
    <div className={s.lightbox} role="dialog" aria-modal="true" aria-label="Image viewer">
      <header className={s.lightboxHead}>
        <span className={s.lightboxTitle}>{item.prompt || item.path}</span>
        <div className={s.lightboxActions}>
          <Pill>
            {index + 1} / {items.length}
          </Pill>
          {reuse.can && (
            <Button
              size="sm"
              variant="primary"
              loading={reuse.busy}
              onClick={() => {
                void reuse.load().then((loaded) => {
                  if (loaded) onClose()
                })
              }}
              title={`Load the settings this was made with into ${reuse.label}`}
            >
              ▶️ Load these settings
            </Button>
          )}
          {item.prompt && (
            <Button
              size="sm"
              variant="ghost"
              iconOnly
              onClick={() => void promptCopy.copy(item.prompt ?? '')}
              aria-label="Copy prompt"
              title={promptCopy.copied ? 'Prompt copied' : 'Copy prompt'}
            >
              {promptCopy.copied ? <CheckIcon /> : <CopyIcon />}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            loading={saving}
            onClick={() => void save()}
            title={`Download ${fileName(item.id)}`}
          >
            <DownloadIcon /> Download
          </Button>
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
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m15 5-7 7 7 7" />
            </svg>
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
          /* Two layers, and the order matters.
           *
           * Underneath, the 512px thumbnail as a *background* — already in
           * cache because the grid tile that opened this used the same URL,
           * so it paints on the first frame and the stage is never blank.
           * A background and not an <img> on purpose: backgrounds are not
           * hit-test targets, so "Copy image" in the moment before the
           * full-resolution file lands cannot quietly hand over a 512px
           * WebP. The only thing right-clickable here is the original.
           *
           * On top, the original, revealed once it has decoded. The frame
           * carries the aspect ratio so neither layer moves during the
           * swap — it is a sharpen, not a reflow. */
          <div
            className={s.stageFrame}
            style={{ ['--ratio' as string]: `${item.width} / ${item.height}` }}
            onClick={(event) => event.stopPropagation()}
          >
            {item.thumbUrl && (
              <div
                className={s.stageThumb}
                style={{ backgroundImage: `url("${item.thumbUrl}")` }}
                aria-hidden="true"
              />
            )}
            <img
              ref={stageRef}
              className={cx(s.lightboxImage, s.stageFull, ready && s.stageReady)}
              src={item.url}
              alt={item.prompt ?? ''}
              onLoad={() => setLoaded(item.url)}
              onError={() => setLoaded(item.url)}
            />
          </div>
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
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 5 7 7-7 7" />
            </svg>
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
 *  so a picture can be traced back to the controls that made it. Every
 *  control comes back as recorded, Seed and Random seed included — the
 *  button means "set the dials the way they were", and the next Generate
 *  makes a new picture with them. It used to pin the seed to the one that
 *  file ran on as well, which turned the next Generate into a byte-for-byte
 *  reproduction that ComfyUI answered out of its cache; see the apply route
 *  for why that stopped. The seed itself is still on the picture, in the
 *  meta line below the stage.
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

  /** True when the values actually went somewhere, so the caller can shut
   *  the dialog on the way out. Worth reporting rather than assuming: from
   *  a tab's own output panel the navigation below is to the route already
   *  on screen and moves nothing, so closing is the only thing that says
   *  the click was heard. */
  async function load(): Promise<boolean> {
    if (!item || !schema) return false
    setBusy(true)
    try {
      const values = await api.applyRecipe(schema.key, item.id)
      if (Object.keys(values).length === 0) {
        toast('No recipe on file for that one.')
        return false
      }
      offer(schema.key, values)
      navigate(schema.route)
      return true
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
      return false
    } finally {
      setBusy(false)
    }
  }

  return { can, busy, load, label: schema?.label ?? '' }
}
