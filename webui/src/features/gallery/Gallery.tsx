import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useGallery } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { Alert, Button, EmptyState, Segmented, Skeleton, useToast } from '@/components/ui'
import { relativeTime, useCopy } from '@/lib/util'
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

  async function remove(item: MediaItem) {
    await api.deleteMedia(item.id)
    await queryClient.invalidateQueries({ queryKey: ['gallery'] })
    setLightbox(null)
    toast('Deleted')
  }

  return (
    <>
      <div className={s.bar}>
        <span className={s.count}>
          {data ? `${data.total} file${data.total === 1 ? '' : 's'}` : ' '}
        </span>
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
            <Tile key={item.id} item={item} onOpen={() => setLightbox(index)} />
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

function Tile({ item, onOpen }: { item: MediaItem; onOpen: () => void }) {
  const { copied, copy } = useCopy()
  return (
    <div
      className={s.tile}
      style={{ ['--ratio' as string]: `${item.width} / ${item.height}` }}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      <img className={s.image} src={item.url} alt={item.prompt ?? ''} loading="lazy" />
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
