import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useGallery } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { Alert, Button, EmptyState, Segmented, Skeleton, useToast } from '@/components/ui'
import { cx, fileName, relativeTime, saveFile, useCopy } from '@/lib/util'
import { useQueue } from '@/store/queue'
import { useTabState } from '@/store/tabState'
import { DownloadIcon, TrashIcon } from './icons'
import { Lightbox } from './Lightbox'
import s from './gallery.module.css'

type Density = 'comfortable' | 'compact' | 'large'

const TILE: Record<Density, string> = {
  large: '320px',
  comfortable: '220px',
  compact: '150px',
}

// Hoisted because `useTabState` holds its initial value in a dependency list.
const NO_STACK: string[] = []

/** Everything that has been made, browsable.
 *
 *  The Gradio gallery is `gr.Gallery(height=600)` — a fixed pixel height no
 *  media query can reach, on a page that is otherwise fluid. This is a grid
 *  of `aspect-ratio` tiles that reflows, plus a density control, because
 *  "how many at once" is a preference and not a constant. */
export function Gallery() {
  /* Where you were, and how you had it looking.
   *
   * Paging to the fourth screen and picking a density is work, and this page
   * unmounts the instant you glance at a generate tab, so both live in
   * `tabState`. The cursor pays for itself twice: `useGallery` is keyed by it,
   * so returning inside the 30s staleTime repaints from cache with no request
   * at all.
   *
   * The lightbox is deliberately not among them. Arriving on a page to find a
   * full-screen viewer already open is a jump-scare, and it would re-take the
   * `document.body` scroll lock on the way in. */
  const [cursor, setCursor] = useTabState<string | null>('gallery.cursor', null)
  const [stack, setStack] = useTabState<string[]>('gallery.stack', NO_STACK)
  const [density, setDensity] = useTabState<Density>('gallery.density', 'comfortable')
  const [lightbox, setLightbox] = useState<number | null>(null)
  const { data, isLoading, error } = useGallery(cursor)
  const queryClient = useQueryClient()
  const toast = useToast()

  /* The selection.
   *
   * A set of path ids rather than indices, because the list under them
   * moves: a delete renumbers every page, and the cursor is an offset into
   * the whole listing. An id names one file for as long as it exists.
   *
   * Cleared when the page turns. Ids are absolute so a selection *could*
   * span pages, but "3 selected" over a screen showing none of them is a
   * count nobody can act on.
   *
   * Not kept across tab switches either, unlike the cursor and the density
   * above: a selection is a gesture about to be acted on, not a setting. */
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [busy, setBusy] = useState(false)
  const anchor = useRef<number | null>(null)

  useEffect(() => {
    setSelected(new Set())
    anchor.current = null
  }, [cursor])

  const items = data?.items
  const selecting = selected.size > 0

  /** Toggle one tile, or — with Shift — add everything between it and the
   *  last one touched. The range is what makes this worth having over
   *  clicking forty checkboxes, and it is the gesture every file manager
   *  uses.
   *
   *  Shift always *adds*. It is tempting to let the anchor's own state
   *  decide, so that shift-dragging back over a selection clears it, but
   *  that is drag-select's rule and not shift-click's: here it turns the
   *  ordinary "click the first, shift-click the last" into a range that
   *  deselects itself, because the first one is by then selected. Removing
   *  is what the checkboxes and Clear are for. */
  const toggle = useCallback(
    (index: number, range: boolean) => {
      if (!items) return
      const from = anchor.current
      setSelected((previous) => {
        const next = new Set(previous)
        if (range && from !== null) {
          const [lo, hi] = from <= index ? [from, index] : [index, from]
          for (let at = lo; at <= hi; at += 1) {
            const id = items[at]?.id
            if (id) next.add(id)
          }
          return next
        }
        const id = items[index]?.id
        if (!id) return previous
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
      anchor.current = index
    },
    [items],
  )

  /* Noticing that a generation happened while this page was open.
   *
   * `useGallery` caches for 30 seconds and nothing invalidated it but a
   * delete, so a picture finished with the gallery open never appeared —
   * while `refetchOnWindowFocus` is off (main.tsx) on the strength of a
   * comment claiming this already worked. It does now: the store counts
   * the files the stream tells it about, and this watches that number.
   *
   * Refreshing is not always the kind thing to do, though, so there are
   * three states it waits out rather than yanking the list:
   *
   *   * the lightbox is open — the list *is* its `items` and the position
   *     into it is an index, so one new file at the front silently changes
   *     which picture you are looking at. This is the one that would be a
   *     bug rather than a rudeness.
   *   * something is selected — ids survive a refetch, but a grid
   *     reflowing under a half-made selection is its own small hostility.
   *   * you have paged back — `cursor` is an offset into the whole
   *     listing, so a file arriving at the front shifts every page after
   *     it, and refreshing where someone is reading deals a different
   *     hand mid-sentence.
   *
   * In those, the count is held and offered as a button. Everywhere else —
   * the ordinary case, page one, nothing selected — the picture simply
   * appears, which is what a gallery left open during a batch is for. */
  const mediaRevision = useQueue((state) => state.mediaRevision)
  const counted = useRef(mediaRevision)
  const [pending, setPending] = useState(0)
  const holding = lightbox !== null || selected.size > 0 || cursor !== null

  useEffect(() => {
    const delta = mediaRevision - counted.current
    counted.current = mediaRevision
    if (delta > 0) setPending((count) => count + delta)
  }, [mediaRevision])

  useEffect(() => {
    if (pending === 0 || holding) return
    setPending(0)
    void queryClient.invalidateQueries({ queryKey: ['gallery'] })
  }, [pending, holding, queryClient])

  /** Take the held refresh, and go to the front of the listing to do it:
   *  that is where the new files are, because the whole listing is newest
   *  first. */
  function showNew() {
    setPending(0)
    setLightbox(null)
    setSelected(new Set())
    anchor.current = null
    setStack([])
    setCursor(null)
    void queryClient.invalidateQueries({ queryKey: ['gallery'] })
  }

  async function remove(item: MediaItem) {
    await api.deleteMedia(item.id)
    await queryClient.invalidateQueries({ queryKey: ['gallery'] })
    setLightbox(null)
    toast('Deleted')
  }

  /** Save the selection, one file at a time.
   *
   *  Serially and not `Promise.all`: a browser handed forty downloads in one
   *  tick drops most of them, and it also means `busy` clears when the last
   *  file has been taken rather than when the first fetch resolved. The
   *  browser asks once, up front, whether this site may save several files —
   *  answering no is why the count is reported rather than assumed.
   *
   *  Only what is on this page can be saved, which is the same rule the
   *  selection itself follows: it is cleared when the page turns. */
  async function downloadSelected() {
    const chosen = (items ?? []).filter((item) => selected.has(item.id))
    if (chosen.length === 0 || busy) return
    setBusy(true)
    let saved = 0
    try {
      for (const item of chosen) {
        try {
          await saveFile(item.url, fileName(item.id))
          saved += 1
        } catch {
          /* Counted, not thrown: one unreadable file should not cost the
           * other thirty-nine. */
        }
      }
    } finally {
      setBusy(false)
    }
    toast(
      saved === chosen.length
        ? `Saved ${saved} file${saved === 1 ? '' : 's'}`
        : `Saved ${saved} of ${chosen.length}; the rest could not be read`,
    )
  }

  /** Delete the selection. One request, one confirmation, one refetch. */
  async function removeSelected() {
    const ids = [...selected]
    if (ids.length === 0 || busy) return
    const what = ids.length === 1 ? 'this file' : `these ${ids.length} files`
    if (!window.confirm(`Delete ${what} permanently? This cannot be undone.`)) return
    setBusy(true)
    try {
      const result = await api.deleteMediaMany(ids)
      await queryClient.invalidateQueries({ queryKey: ['gallery'] })
      setSelected(new Set())
      anchor.current = null
      setLightbox(null)
      // Partial success is a normal outcome here, so it gets said out loud
      // rather than rounded up to "Deleted".
      toast(
        result.failed.length === 0
          ? `Deleted ${result.deleted} file${result.deleted === 1 ? '' : 's'}`
          : `Deleted ${result.deleted}; ${result.failed.length} could not be removed`,
      )
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className={s.bar}>
        <div className={s.barLeft}>
          {selecting ? (
            /* The count's slot, taken over while there is a selection. A
             * second bar would push the pictures down the moment you touched
             * one, and the thing being counted is the same thing. */
            <div className={s.selectBar}>
              <span className={s.count}>{selected.size} selected</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Set((items ?? []).map((item) => item.id)))}
                disabled={!items || selected.size === items.length}
                /* "all" only when there is nothing else to be all of. It
                 * selects what is on screen, and a gallery with an Older
                 * button has more than that. */
                title="Select every file on this page"
              >
                {data?.nextCursor || stack.length > 0 ? 'Select page' : 'Select all'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                Clear
              </Button>
              <Button
                size="sm"
                variant="ghost"
                loading={busy}
                onClick={() => void downloadSelected()}
                title={`Download ${selected.size} file${selected.size === 1 ? '' : 's'}`}
              >
                <DownloadIcon /> Download
              </Button>
              <Button
                size="sm"
                variant="danger"
                loading={busy}
                onClick={() => void removeSelected()}
                title={`Delete ${selected.size} file${selected.size === 1 ? '' : 's'}`}
              >
                <TrashIcon /> Delete
              </Button>
            </div>
          ) : (
            <span className={s.count}>
              {data ? `${data.total} file${data.total === 1 ? '' : 's'}` : ' '}
            </span>
          )}
          {pending > 0 && (
            <button
              type="button"
              className={s.newWork}
              onClick={showNew}
              title="Reload the gallery and go back to the newest files"
            >
              {pending === 1 ? '1 new file' : `${pending} new files`}
            </button>
          )}
        </div>
        <Segmented
          value={density}
          ariaLabel="Tile size"
          onChange={setDensity}
          options={[
            { value: 'compact', label: 'Compact' },
            { value: 'comfortable', label: 'Comfortable' },
            { value: 'large', label: 'Large' },
          ]}
        />
      </div>

      {error && <Alert tone="error">{(error as Error).message}</Alert>}

      {isLoading ? (
        <div className={s.skeletonGrid}>
          {Array.from({ length: 8 }, (_, index) => (
            <Skeleton key={index} style={{ aspectRatio: '3 / 4' }} radius="var(--r-md)" />
          ))}
        </div>
      ) : data && data.items.length > 0 ? (
        <div className={s.grid} style={{ ['--tile' as string]: TILE[density] }}>
          {data.items.map((item, index) => (
            <Tile
              key={item.id}
              item={item}
              selected={selected.has(item.id)}
              anySelected={selecting}
              onSelect={(range) => toggle(index, range)}
              onOpen={() => setLightbox(index)}
            />
          ))}
        </div>
      ) : (
        <EmptyState icon="🖼️" title="Nothing here yet">
          Everything you generate lands here automatically, newest first.
        </EmptyState>
      )}

      {data && (data.nextCursor || stack.length > 0) && (
        <div className={s.pager}>
          <Button
            disabled={stack.length === 0}
            onClick={() => {
              const previous = [...stack]
              const back = previous.pop() ?? null
              setStack(previous)
              setCursor(back)
            }}
          >
            ‹ Newer
          </Button>
          <Button
            disabled={!data.nextCursor}
            onClick={() => {
              setStack([...stack, cursor ?? ''])
              setCursor(data.nextCursor)
            }}
          >
            Older ›
          </Button>
        </div>
      )}

      {lightbox !== null && data && (
        <Lightbox
          items={data.items}
          index={lightbox}
          onIndex={setLightbox}
          onClose={() => setLightbox(null)}
          onDelete={(item) => void remove(item)}
        />
      )}
    </>
  )
}

function Tile({
  item,
  selected,
  anySelected,
  onSelect,
  onOpen,
}: {
  item: MediaItem
  selected: boolean
  /** Something on this page is selected, so every checkbox stays visible —
   *  hunting for a hover target while picking a set is the wrong game. */
  anySelected: boolean
  onSelect: (range: boolean) => void
  onOpen: () => void
}) {
  const { copied, copy } = useCopy()
  return (
    <div
      className={cx(s.tile, selected && s.tileSelected)}
      style={{ ['--ratio' as string]: `${item.width} / ${item.height}` }}
      role="button"
      tabIndex={0}
      /* A plain click still opens the picture — that is what a gallery tile
       * is for, and putting the whole grid into a "selection mode" to delete
       * two files is a mode nobody asked for. The checkbox is the selection
       * gesture, and Shift+click anywhere on a tile is the shortcut for it
       * once a range is what you mean. */
      onClick={(event) => {
        if (event.shiftKey || anySelected) {
          event.preventDefault()
          onSelect(event.shiftKey)
          return
        }
        onOpen()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
        if (event.key === ' ') {
          event.preventDefault()
          onSelect(false)
        }
      }}
    >
      {/* The 512px WebP, not the multi-megabyte original: a page of tiles is
          the one place the size difference is measured in tens of megabytes.
          `?? item.url` is gallery_index's no-backfill policy showing through
          — a video, or anything generated before thumbnails existed, has no
          thumb and serves the original, exactly as it did before. */}
      <img
        className={s.image}
        src={item.thumbUrl ?? item.url}
        alt={item.prompt ?? ''}
        loading="lazy"
      />
      <label
        className={cx(s.pick, (selected || anySelected) && s.pickShown)}
        onClick={(event) => event.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={selected}
          aria-label={`Select ${item.path}`}
          onClick={(event) => {
            event.stopPropagation()
            onSelect(event.shiftKey)
          }}
          onChange={() => {}}
        />
      </label>
      <div className={s.tileBar}>
        <span className={s.meta}>{relativeTime(item.createdAt)}</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={(event) => {
            event.stopPropagation()
            copy(item.path)
          }}
        >
          {copied ? 'Copied' : 'Path'}
        </Button>
      </div>
    </div>
  )
}
