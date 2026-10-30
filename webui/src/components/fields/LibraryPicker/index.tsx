import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal, ProgressBar, useToast } from '@/components/ui'
import { cx } from '@/lib/util'
import s from './library.module.css'

/*
 * A shared library of pictures an images field can draw from: poses today.
 *
 * The backend serves it from /api/v1/library/<name>: a manifest, then each
 * picture by id, rendering and size. The pack is downloaded to the machine
 * running the app the first time the picker opens there — the manifest says
 * "downloading" with a progress figure until it is unpacked — and served
 * from its disk after that (see ember/web/library.py).
 *
 * One pose is one card with up to three renderings of it — the OpenPose
 * skeleton, a depth map and line art. "Use" picks which one goes into the
 * field — one, never several, so a pose costs one image slot — and each card
 * can switch its own. A pick is fetched at full size, optionally mirrored,
 * and handed over as a plain File: from there on it is an ordinary
 * reference, numbered, reorderable and uploaded like any other.
 */

export type LibraryKind = 'skeleton' | 'depth' | 'lineart'
type Kind = LibraryKind
type Shape = '' | 'match' | 'portrait' | 'landscape' | 'square'

interface Item {
  id: string
  name: string
  category: string
  aspect?: 'portrait' | 'landscape' | 'square'
  source: string
  tags: string[]
  variants: Kind[]
}

const KINDS: { value: Kind; label: string; short: string; title: string }[] = [
  { value: 'skeleton', label: 'Skeleton', short: 'Skel', title: 'OpenPose skeleton: the clearest pose signal' },
  { value: 'depth', label: '3D depth', short: '3D', title: 'Depth map: body shape and camera angle as well as the pose' },
  { value: 'lineart', label: 'Line art', short: 'Line', title: 'The outline of a body' },
]
const FAVOURITES = 'Favourites'
const PAGE = 60

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`ember.library.${key}`)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(`ember.library.${key}`, JSON.stringify(value))
  } catch {
    /* private window: favourites last as long as the tab */
  }
}

function shapeOf(size: { width: number; height: number } | null): Item['aspect'] | undefined {
  if (!size) return undefined
  const ratio = size.width / size.height
  return ratio > 1.15 ? 'landscape' : ratio < 0.87 ? 'portrait' : 'square'
}

/** The rendering a card adds: the one asked for, else the first it has. */
function pick(item: Item, kind: Kind): Kind {
  return item.variants.includes(kind) ? kind : (item.variants[0] ?? 'skeleton')
}

function isKind(value: unknown): value is Kind {
  return KINDS.some((k) => k.value === value)
}

async function fileFrom(url: string, item: Item, kind: Kind, mirror: boolean): Promise<File> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${response.status}`)
  let blob = await response.blob()
  if (mirror) {
    const bitmap = await createImageBitmap(blob)
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const ctx = canvas.getContext('2d')!
    ctx.translate(bitmap.width, 0)
    ctx.scale(-1, 1)
    ctx.drawImage(bitmap, 0, 0)
    blob = await new Promise<Blob>((done, fail) =>
      canvas.toBlob((b) => (b ? done(b) : fail(new Error('encode'))), 'image/png'),
    )
  }
  const extension = blob.type === 'image/png' ? 'png' : 'webp'
  return new File([blob], `pose-${item.id}-${kind}${mirror ? '-mirrored' : ''}.${extension}`, {
    type: blob.type || 'image/webp',
  })
}

export function LibraryPicker({
  library,
  open,
  onClose,
  onPick,
  nextNumber,
  room,
  mainSize,
}: {
  library: string
  open: boolean
  onClose: () => void
  onPick: (file: File, kind: Kind) => void
  /** The number the picked picture becomes, for the card's button. */
  nextNumber: number
  room: number
  /** Image 1's size, for "Match image 1". */
  mainSize: { width: number; height: number } | null
}) {
  const base = `/api/v1/library/${library}`
  const url = (size: 'thumbs' | 'full', item: Item, kind: Kind) => `${base}/${size}/${item.id}/${kind}`
  const toast = useToast()
  const { data, isLoading, error } = useQuery({
    queryKey: ['library', library],
    queryFn: async () => {
      const response = await fetch(base, { headers: { Accept: 'application/json' } })
      if (!response.ok) {
        let detail = `The ${library} library could not be loaded.`
        try {
          const body = await response.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch {
          /* not JSON: keep the generic sentence */
        }
        throw new Error(detail)
      }
      return (await response.json()) as
        | { status: 'ready'; version: number; items: Item[] }
        | { status: 'downloading'; progress: number }
    },
    staleTime: Infinity,
    retry: 1,
    enabled: open,
    // The first open on a machine downloads the pack: ask again every
    // second until it is unpacked, and never again after that.
    refetchInterval: (q) => (q.state.data?.status === 'downloading' ? 1000 : false),
  })
  const ready = data?.status === 'ready' ? data : null
  const downloading = data?.status === 'downloading' ? data : null
  const items = ready?.items ?? []

  const [category, setCategory] = useState('All')
  const [kind, setKind] = useState<Kind>(() => {
    const stored = read<unknown>('kind', 'skeleton')
    return isKind(stored) ? stored : 'skeleton'
  })
  const [shape, setShape] = useState<Shape>('')
  const [tags, setTags] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [shown, setShown] = useState(PAGE)
  const [mirror, setMirror] = useState(false)
  const [cardKind, setCardKind] = useState<Record<string, Kind>>({})
  const [favourites, setFavourites] = useState<string[]>(() => read('favourites', []))
  const [recent, setRecent] = useState<string[]>(() => read('recent', []))
  const [busy, setBusy] = useState<string | null>(null)
  const inFlight = useRef(false)
  const gridRef = useRef<HTMLDivElement>(null)

  const mainShape = shapeOf(mainSize)
  const byId = useMemo(() => new Map(items.map((item) => [item.id, item])), [items])

  const categories = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of items) counts.set(item.category, (counts.get(item.category) ?? 0) + 1)
    return [...counts].sort((a, b) => b[1] - a[1])
  }, [items])

  // The tag chips are whatever the manifest uses, commonest first, so a
  // new pack's tags show up without a release.
  const tagList = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of items) for (const tag of item.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1)
    return [...counts].sort((a, b) => b[1] - a[1]).map(([tag]) => tag)
  }, [items])

  const matching = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean)
    const wantShape = shape === 'match' ? mainShape : shape || undefined
    return items.filter((item) => {
      if (category === FAVOURITES ? !favourites.includes(item.id) : category !== 'All' && item.category !== category)
        return false
      if (wantShape && item.aspect !== wantShape) return false
      if (tags.some((tag) => !item.tags.includes(tag))) return false
      if (words.length) {
        const hay = `${item.name} ${item.category} ${item.tags.join(' ')}`.toLowerCase()
        if (!words.every((word) => hay.includes(word))) return false
      }
      return true
    })
  }, [items, category, favourites, shape, mainShape, tags, query])

  function reset(next: () => void) {
    next()
    setShown(PAGE)
    gridRef.current?.scrollTo({ top: 0 })
  }

  const kindFor = (item: Item) => pick(item, cardKind[item.id] ?? kind)

  function chooseKind(next: Kind) {
    setKind(next)
    setCardKind({})
    write('kind', next)
  }

  function toggleFavourite(id: string) {
    const next = favourites.includes(id) ? favourites.filter((f) => f !== id) : [id, ...favourites]
    setFavourites(next)
    write('favourites', next)
  }

  async function add(item: Item) {
    if (inFlight.current) return
    if (room <= 0) {
      toast('The images are full: remove one to add a pose.')
      return
    }
    inFlight.current = true
    setBusy(item.id)
    const chosen = kindFor(item)
    try {
      const file = await fileFrom(url('full', item, chosen), item, chosen, mirror)
      const nextRecent = [item.id, ...recent.filter((r) => r !== item.id)].slice(0, 16)
      setRecent(nextRecent)
      write('recent', nextRecent)
      onPick(file, chosen)
      onClose()
    } catch {
      toast('That pose could not be downloaded. Check the connection and try again.')
    } finally {
      inFlight.current = false
      setBusy(null)
    }
  }

  const recentItems = recent.map((id) => byId.get(id)).filter((item): item is Item => Boolean(item))

  return (
    <Modal open={open} onClose={onClose} title="Pose library" className={s.sheet}>
      <div className={s.body}>
        <div className={s.toolbar}>
          <div className={s.row}>
            <input
              className={s.search}
              type="search"
              placeholder="Search: kneeling, arms up…"
              value={query}
              onChange={(event) => reset(() => setQuery(event.target.value))}
              aria-label="Search poses"
            />
            <div className={s.use} role="radiogroup" aria-label="Rendering to add">
              <span className={s.useLabel}>Use</span>
              {KINDS.map((k) => (
                <button
                  key={k.value}
                  type="button"
                  role="radio"
                  title={k.title}
                  className={cx(s.useButton, kind === k.value && s.useOn)}
                  aria-checked={kind === k.value}
                  onClick={() => chooseKind(k.value)}
                >
                  {k.label}
                </button>
              ))}
            </div>
          </div>

          <div className={s.chips} role="group" aria-label="Category">
            {[['All', items.length] as const, [FAVOURITES, favourites.length] as const, ...categories].map(
              ([name, count]) => (
                <button
                  key={name}
                  type="button"
                  className={cx(s.chip, category === name && s.chipOn)}
                  aria-pressed={category === name}
                  onClick={() => reset(() => setCategory(name))}
                >
                  {name === FAVOURITES ? '★ ' : ''}
                  {name}
                  <small>{count}</small>
                </button>
              ),
            )}
          </div>

          <div className={s.chips} role="group" aria-label="Filters">
            <select
              className={s.select}
              value={shape}
              aria-label="Shape"
              onChange={(event) => reset(() => setShape(event.target.value as Shape))}
            >
              <option value="">Any shape</option>
              <option value="match" disabled={!mainShape}>
                {mainShape ? `Match image 1 (${mainShape})` : 'Match image 1'}
              </option>
              <option value="portrait">Portrait</option>
              <option value="landscape">Landscape</option>
              <option value="square">Square</option>
            </select>
            {tagList.map((tag) => (
              <button
                key={tag}
                type="button"
                className={cx(s.chip, s.tagChip, tags.includes(tag) && s.chipOn)}
                aria-pressed={tags.includes(tag)}
                onClick={() =>
                  reset(() => setTags((all) => (all.includes(tag) ? all.filter((t) => t !== tag) : [...all, tag])))
                }
              >
                {tag}
              </button>
            ))}
            <button
              type="button"
              className={cx(s.chip, s.tagChip, mirror && s.chipOn)}
              aria-pressed={mirror}
              title="Flip the pose left to right when it is added"
              onClick={() => setMirror((m) => !m)}
            >
              ⇋ Mirror
            </button>
          </div>

          {recentItems.length > 0 && (
            <div className={s.recent}>
              <span className={s.recentLabel}>Recent</span>
              <div className={s.recentStrip}>
                {recentItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={s.recentThumb}
                    title={`${item.name}: add as image ${nextNumber}`}
                    disabled={room <= 0}
                    onClick={() => void add(item)}
                  >
                    <img src={url('thumbs', item, kindFor(item))} alt="" loading="lazy" />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className={s.scroll} ref={gridRef}>
          {isLoading && <p className={s.empty}>Loading the pose library…</p>}
          {downloading && (
            <div className={s.download}>
              <ProgressBar
                label="Downloading the pose library to this machine. This happens once."
                step={downloading.progress > 0 ? Math.round(downloading.progress * 100) : undefined}
                total={downloading.progress > 0 ? 100 : undefined}
              />
            </div>
          )}
          {error && <p className={s.empty}>{error.message}</p>}
          {ready && matching.length === 0 && <p className={s.empty}>No pose matches these filters.</p>}
          <ul className={s.grid}>
            {matching.slice(0, shown).map((item) => {
              const k = kindFor(item)
              const peek = k !== 'depth' && item.variants.includes('depth')
              const fav = favourites.includes(item.id)
              return (
                <li key={item.id} className={s.card}>
                  <button
                    type="button"
                    className={s.thumb}
                    onClick={() => void add(item)}
                    disabled={room <= 0}
                    aria-label={`Add ${item.name} as image ${nextNumber}`}
                  >
                    <img src={url('thumbs', item, k)} alt="" loading="lazy" draggable={false} />
                    {peek && (
                      <img
                        className={s.peek}
                        src={url('thumbs', item, 'depth')}
                        alt=""
                        loading="lazy"
                        draggable={false}
                      />
                    )}
                    <span className={s.addLabel}>{busy === item.id ? 'Adding…' : `Add as image ${nextNumber}`}</span>
                  </button>
                  <button
                    type="button"
                    className={cx(s.star, fav && s.starOn)}
                    aria-pressed={fav}
                    aria-label={fav ? `Unfavourite ${item.name}` : `Favourite ${item.name}`}
                    onClick={() => toggleFavourite(item.id)}
                  >
                    ★
                  </button>
                  <div className={s.meta}>
                    <div className={s.name}>
                      <span>{item.name}</span>
                      {item.aspect && <em>{item.aspect}</em>}
                    </div>
                    {item.tags.length > 0 && (
                      <div className={s.tags}>
                        {item.tags.slice(0, 3).map((tag) => (
                          <span key={tag}>{tag}</span>
                        ))}
                      </div>
                    )}
                    <div className={s.variants} role="radiogroup" aria-label={`${item.name}: rendering to add`}>
                      {KINDS.map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          role="radio"
                          title={option.title}
                          disabled={!item.variants.includes(option.value)}
                          className={cx(option.value === k && s.variantOn)}
                          aria-checked={option.value === k}
                          onClick={() => setCardKind((all) => ({ ...all, [item.id]: option.value }))}
                        >
                          {option.short}
                        </button>
                      ))}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
          {matching.length > shown && (
            <button type="button" className={s.more} onClick={() => setShown((n) => n + PAGE)}>
              Show more ({matching.length - shown} left)
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}
