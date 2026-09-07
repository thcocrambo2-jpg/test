import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { Button, Pill, useToast } from '@/components/ui'
import { useHandoff } from '@/store/handoff'
import { cx, fileName, relativeTime, saveFile, useCopy } from '@/lib/util'
import { CheckIcon, CopyIcon, DownloadIcon, TrashIcon } from './icons'
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
  /* Said out loud when a copy does not land. A phone on plain HTTP has no
   * clipboard API at all (see `copyText`), and a button that fails silently
   * there is one you go on pressing while the paste keeps handing back
   * whatever the phone was holding before. */
  const toast = useToast()
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
              aria-label={`Load the settings this was made with into ${reuse.label}`}
              title={`Load the settings this was made with into ${reuse.label}`}
            >
              <span aria-hidden="true">▶️</span>
              <span className={s.btnLabel}>Load these settings</span>
            </Button>
          )}
          {item.prompt && (
            <Button
              size="sm"
              variant="ghost"
              iconOnly
              onClick={() => {
                void promptCopy.copy(item.prompt ?? '').then((landed) => {
                  if (!landed) toast('Could not reach the clipboard.')
                })
              }}
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
            aria-label={`Download ${fileName(item.id)}`}
            title={`Download ${fileName(item.id)}`}
          >
            <DownloadIcon />
            <span className={s.btnLabel}>Download</span>
          </Button>
          {onDelete && (
            <Button
              size="sm"
              variant="danger"
              onClick={() => onDelete(item)}
              aria-label="Delete"
              title="Delete"
            >
              <TrashIcon />
              <span className={s.btnLabel}>Delete</span>
            </Button>
          )}
          <Button size="sm" variant="ghost" iconOnly onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
      </header>

      <div className={s.lightboxStage} onClick={onClose}>
        {/* The ends of the stage, as prev and next.
         *
         * Nothing is drawn — see `.edge` in the stylesheet for why, and for
         * why they are not offered over a video. They stop propagation
         * because the stage they sit in closes the dialog on a click, which
         * is the behaviour the middle of the stage keeps.
         *
         * Absent at the ends of the strip rather than present and inert: a
         * pointer cursor over a button that does nothing is a worse answer
         * than the backdrop underneath, which at least closes. */}
        {item.kind !== 'video' && index > 0 && (
          <button
            type="button"
            className={cx(s.edge, s.edgeLeft)}
            aria-label="Previous image"
            title="Previous image"
            onClick={(event) => {
              event.stopPropagation()
              onIndex(index - 1)
            }}
          />
        )}
        {item.kind !== 'video' && index < items.length - 1 && (
          <button
            type="button"
            className={cx(s.edge, s.edgeRight)}
            aria-label="Next image"
            title="Next image"
            onClick={(event) => {
              event.stopPropagation()
              onIndex(index + 1)
            }}
          />
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
          /* Two layers, and each one has a box it can be right about.
           *
           * Underneath, the 512px thumbnail, stretched across the whole
           * stage and fitted with `object-fit` — so it paints exactly the
           * rectangle the original will land on, and it has that box from
           * the first frame rather than waiting on a size it has not
           * downloaded yet. It is already in cache, because the grid tile
           * that opened this used the same URL. It cannot be right-clicked:
           * `pointer-events: none` in the stylesheet, so "Copy image" in the
           * moment before the full-resolution file lands cannot quietly hand
           * over a 512px WebP. The only picture on offer is the original.
           *
           * On top, the original, revealed once it has decoded. It is sized
           * by its own dimensions against the same box, so the two agree on
           * where the picture goes to within a fraction of a pixel — it is a
           * sharpen, not a reflow. */
          <div className={s.stageFrame} onClick={(event) => event.stopPropagation()}>
            {item.thumbUrl && (
              <img className={s.stageThumb} src={item.thumbUrl} alt="" aria-hidden="true" />
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
        <div className={cx(s.lightboxHead, s.lightboxFoot)}>
          <div className={s.lightboxMeta}>
            {/* Stills only. A clip's real size is not on hand — the API
             *  measures a video by its thumbnail, which carries its shape
             *  and not its dimensions — and "512 × 288" under a 1280 × 720
             *  clip is worse than saying nothing. */}
            {item.kind !== 'video' && (
              <span>
                {item.width} × {item.height}
              </span>
            )}
            {item.seed != null && <span>seed {item.seed}</span>}
            <span>{relativeTime(item.createdAt)}</span>
          </div>
          <Pill
            tone={copied ? 'success' : 'default'}
            onClick={() => {
              void copy(item.path).then((landed) => {
                if (!landed) toast('Could not reach the clipboard.')
              })
            }}
          >
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
 *  control comes back as recorded, and Seed comes back as the number that
 *  picture actually ran on — but Random seed is left alone, so the next
 *  Generate makes *another* picture like it rather than that one again. The
 *  seed is there for the case where the exact frame is what was wanted:
 *  untick Random seed and run. See the apply route for why pinning it was
 *  the wrong default.
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
      /* Names the seed, because the form does not: the box now holds the
       * number this picture ran on and the control above it says, quite
       * correctly, that it is being ignored. Somebody who came here for
       * that exact frame would otherwise have no way of knowing they are
       * one tick away from it. */
      toast('Settings loaded.')
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
