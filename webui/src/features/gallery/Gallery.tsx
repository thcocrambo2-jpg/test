import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useGallery } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { Alert, Button, EmptyState, Segmented, Skeleton, useToast } from '@/components/ui'
import { cx, relativeTime, useCopy } from '@/lib/util'
import { Lightbox } from './Lightbox'
import s from './gallery.module.css'

type Density = 'comfortable' | 'compact' | 'large'

const TILE: Record<Density, string> = {
  large: '320px',
  comfortable: '220px',
  compact: '150px',
}

/** Everything that has been made, browsable.
 *
 *  The Gradio gallery is `gr.Gallery(height=600)` — a fixed pixel height no
 *  media query can reach, on a page that is otherwise fluid. This is a grid
 *  of `aspect-ratio` tiles that reflows, plus a density control, because
 *  "how many at once" is a preference and not a constant. */
export function Gallery() {
  const [cursor, setCursor] = useState<string | null>(null)
  const [stack, setStack] = useState<string[]>([])
  const [density, setDensity] = useState<Density>('comfortable')
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
   * count nobody can act on. */
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

  async function remove(item: MediaItem) {
    await api.deleteMedia(item.id)
    await queryClient.invalidateQueries({ queryKey: ['gallery'] })
    setLightbox(null)
    toast('Deleted')
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

/** The bin. Inline because it is the only icon this page has, and one
 *  24-line path is cheaper than a dependency that would arrive with a
 *  thousand more. */
function TrashIcon() {
  return (
    <svg className={s.trash} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M10 11v6M14 11v6" />
      <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  )
}
