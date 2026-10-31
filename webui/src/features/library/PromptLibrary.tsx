import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useCatalogue, useSchemas } from '@/api/queries'
import type { PromptCard, PromptFacet, TabSchema } from '@/api/types'
import { Alert, Button, EmptyState, Pill, Segmented, Skeleton, useToast } from '@/components/ui'
import { restoreTab, snapshotTab, useTokenLoader } from '@/components/SettingsToken'
import { useTabState } from '@/store/tabState'
import { copyText } from '@/lib/util'
import { readToken } from '@/lib/settingsToken'
import { BracesIcon, CheckIcon } from '@/features/gallery/icons'
import s from './library.module.css'

/*
 * The Prompt Library — pictures made in this app, and the settings behind
 * each one.
 *
 * A card is a picture and its settings token. The token is fetched only when
 * Use or Copy is pressed — a page carries none, which is what keeps switching
 * category quick. Use loads the token into the tab that made it, through the same loader as the Settings token panel, so
 * every value is guarded against this pod and a LoRA it does not have is
 * left out with a note. There is no prompt text to read or copy: a prompt
 * only makes that picture together with every other dial, and the token
 * carries all of them.
 *
 * The pictures come straight from R2 through signed links the licence
 * server hands out (prompts.py). A card for a tab this licence does not
 * grant still shows its picture — what that tab can make is exactly what is
 * worth seeing — but is not usable and says what it needs.
 */

const PAGE = 24

export function PromptLibrary() {
  /* In `tabState`, because these are things you chose and this page unmounts
   * as soon as you leave it. They are also the whole query key below, so
   * coming back repaints the same cards without asking the server again.
   * `tags` is the chosen tag keys, comma-separated, which is also how the
   * route takes them. */
  const [tab, setTab] = useTabState('prompts.tab', '')
  const [category, setCategory] = useTabState('prompts.category', '')
  const [tags, setTags] = useTabState('prompts.tags', '')
  const [skip, setSkip] = useTabState('prompts.skip', 0)
  const label = useTabLabel()

  const { data, isLoading, isFetching, isPlaceholderData, error } = useQuery({
    queryKey: ['prompts', tab, category, tags, skip],
    queryFn: () => api.getPrompts({ tab, category, tags, skip, limit: PAGE }),
    staleTime: 60_000,
    // The filters stay on screen while the next page loads, rather than
    // vanishing for a moment every time one is clicked.
    placeholderData: keepPreviousData,
  })

  // A filter was just changed and its page is on the way: the old cards
  // stay, dimmed, under a loading line, so the click visibly did something.
  const switching = isFetching && isPlaceholderData
  const cards = data?.prompts ?? []
  const total = data?.total ?? 0
  const tabs = useMemo(() => Object.keys(data?.tabs ?? {}), [data?.tabs])
  const chosenTags = useMemo(() => tags.split(',').filter(Boolean), [tags])
  const categoryLabel = (key: string | null) =>
    data?.categories.find((row) => row.key === key)?.label ?? null

  function filter(next: () => void) {
    setSkip(0)
    next()
  }

  function toggleTag(key: string) {
    const next = chosenTags.includes(key) ? chosenTags.filter((tag) => tag !== key) : [...chosenTags, key]
    filter(() => setTags(next.join(',')))
  }

  return (
    <>
      {tabs.length > 1 && (
        <div className={s.filters}>
          <Segmented
            value={tab}
            ariaLabel="Which tab"
            onChange={(value) => filter(() => setTab(value))}
            options={[
              { value: '', label: 'Everything' },
              ...tabs.map((key) => ({ value: key, label: label(key) })),
            ]}
          />
        </div>
      )}

      {data && data.categories.length > 0 && (
        <Chips
          ariaLabel="Category"
          rows={data.categories}
          isOn={(key) => key === category}
          all={{ on: !category, onClick: () => filter(() => setCategory('')) }}
          onClick={(key) => filter(() => setCategory(key === category ? '' : key))}
        />
      )}
      {data && data.tags.length > 0 && (
        <Chips
          ariaLabel="Tags"
          rows={data.tags}
          small
          isOn={(key) => chosenTags.includes(key)}
          onClick={toggleTag}
        />
      )}

      <div className={switching ? `${s.loadingBar} ${s.loadingOn}` : s.loadingBar} role="status" aria-live="polite">
        {switching ? 'Loading…' : ''}
      </div>

      {error && <Alert tone="error">{(error as Error).message}</Alert>}
      {data?.error && <Alert tone="warning">{data.error}</Alert>}

      {isLoading ? (
        <div className={s.grid}>
          {Array.from({ length: 8 }, (_, index) => (
            <Skeleton key={index} height={320} radius="var(--r-lg)" />
          ))}
        </div>
      ) : cards.length === 0 ? (
        <EmptyState icon="🌟" title="Nothing here yet">
          {tab || category || tags
            ? 'Nothing in the library matches that yet.'
            : 'New pictures and their settings appear here.'}
        </EmptyState>
      ) : (
        <Masonry cards={cards} dimmed={switching}>
          {(card) => (
            <PromptTile key={card.id} card={card} label={label(card.tab)} category={categoryLabel(card.category)} />
          )}
        </Masonry>
      )}

      {total > PAGE && (
        <div className={s.pager}>
          <Button variant="ghost" disabled={skip === 0} onClick={() => setSkip(Math.max(0, skip - PAGE))}>
            ← Previous
          </Button>
          <span className={s.pagerCount}>
            {skip + 1}–{Math.min(skip + PAGE, total)} of {total}
          </span>
          <Button variant="ghost" disabled={skip + PAGE >= total} onClick={() => setSkip(skip + PAGE)}>
            Next →
          </Button>
        </div>
      )}
    </>
  )
}

/** One row of filter chips with their counts. A category row has an "All"
 *  chip and picks one; a tag row picks any number, and none is all. */
function Chips({
  ariaLabel,
  rows,
  isOn,
  onClick,
  all,
  small,
}: {
  ariaLabel: string
  rows: PromptFacet[]
  isOn: (key: string) => boolean
  onClick: (key: string) => void
  all?: { on: boolean; onClick: () => void }
  small?: boolean
}) {
  return (
    <div className={small ? `${s.chips} ${s.chipsSmall}` : s.chips} role="group" aria-label={ariaLabel}>
      {all && (
        <button type="button" className={all.on ? `${s.chip} ${s.chipOn}` : s.chip} aria-pressed={all.on} onClick={all.onClick}>
          All
        </button>
      )}
      {rows.map((row) => {
        const on = isOn(row.key)
        return (
          <button
            key={row.key}
            type="button"
            className={on ? `${s.chip} ${s.chipOn}` : s.chip}
            aria-pressed={on}
            onClick={() => onClick(row.key)}
          >
            {row.label}
            <span className={s.chipCount}>{row.count}</span>
          </button>
        )
      })}
    </div>
  )
}

/* Narrowest a column may get, and the gap between columns — the gap is
 * `--s-4` in library.module.css, read here as its pixel value. */
const COLUMN_MIN = 220
const COLUMN_GAP = 16
/* The caption under each picture, as a share of the column width, so a card's
 * height can be estimated before anything has loaded. */
const CAPTION = 0.2

/** Cards in masonry columns, in reading order.
 *
 *  Each card goes to whichever column is shortest so far, judged from the
 *  picture's stored width and height — so the layout is settled on the first
 *  paint and nothing moves as pictures arrive, and the library's order still
 *  reads left to right rather than down one column and then the next, which
 *  is what CSS `columns` would give. The column count follows the width of
 *  the page. */
function Masonry({
  cards,
  dimmed,
  children,
}: {
  cards: PromptCard[]
  dimmed?: boolean
  children: (card: PromptCard) => ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [count, setCount] = useState(4)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      setCount(Math.max(1, Math.floor((width + COLUMN_GAP) / (COLUMN_MIN + COLUMN_GAP))))
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const columns = useMemo(() => {
    const out: PromptCard[][] = Array.from({ length: count }, () => [])
    const heights = new Array<number>(count).fill(0)
    for (const card of cards) {
      const ratio = card.width && card.height ? card.height / card.width : 4 / 3
      const shortest = heights.indexOf(Math.min(...heights))
      out[shortest].push(card)
      heights[shortest] += ratio + CAPTION
    }
    return out
  }, [cards, count])

  return (
    <div ref={ref} className={dimmed ? `${s.masonry} ${s.dimmed}` : s.masonry} aria-busy={dimmed}>
      {columns.map((column, index) => (
        <div key={index} className={s.column}>
          {column.map(children)}
        </div>
      ))}
    </div>
  )
}

/** A tab's name for a feature key, granted or not: the schema's for a tab
 *  this licence has, the catalogue's for one it does not. */
function useTabLabel(): (key: string) => string {
  const { data: schemas } = useSchemas()
  const { data: catalogue } = useCatalogue()
  return (key: string) => {
    const schema = schemas?.find((candidate) => candidate.key === key)
    if (schema) return `${schema.icon} ${schema.label}`
    return catalogue?.features[key]?.name ?? key
  }
}

function PromptTile({ card, label, category }: { card: PromptCard; label: string; category: string | null }) {
  const name = category ?? label
  return (
    <figure className={s.card}>
      <div className={s.picture}>
        <img
          src={card.thumb}
          alt=""
          loading="lazy"
          width={card.width ?? undefined}
          height={card.height ?? undefined}
        />
      </div>
      <figcaption className={s.caption}>
        <span className={s.label} title={card.title ? `${card.title} · ${name}` : name}>
          {card.title ? (
            <>
              <span className={s.title}>{card.title}</span> · {name}
            </>
          ) : (
            name
          )}
        </span>
        <CardActions card={card} label={label} />
      </figcaption>
    </figure>
  )
}

/** Copy token and Use for one card, or what the card needs when this
 *  licence does not have its tab. */
function CardActions({ card, label }: { card: PromptCard; label: string }) {
  const { data: schemas } = useSchemas()
  const navigate = useNavigate()
  const toast = useToast()
  const loadToken = useTokenLoader()
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copyBusy, setCopyBusy] = useState(false)

  const target: TabSchema | undefined = useMemo(
    () => schemas?.find((schema) => schema.key === card.tab),
    [schemas, card.tab],
  )

  if (!card.usable || !target) {
    return <Pill title="This tab is not part of this licence">🔒 Needs {label}</Pill>
  }

  /* Loads exactly as pasting the token into the Settings token panel would,
   * and says so the same way: a toast naming the tab, the count of notes
   * the guard left, and Undo, which puts the tab back and returns here. */
  async function use() {
    if (!target) return
    setBusy(true)
    try {
      const { data } = await readToken(await api.getPromptToken(card.id))
      const before = snapshotTab(target.key)
      const outcome = await loadToken(target, data)
      const notes = outcome.notes.length
      toast(
        notes > 0
          ? `Settings loaded into ${target.label} · ${notes} note${notes === 1 ? '' : 's'} in Settings token`
          : `Settings loaded into ${target.label}`,
        {
          label: 'Undo',
          run: () => {
            restoreTab(before)
            navigate('/library/prompts')
            toast('Back as it was.')
          },
        },
      )
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  /* The token is fetched first, so the clipboard write happens after an
   * await. Safari only lets a page write the clipboard inside the click
   * itself, so where ClipboardItem exists the write starts in the click and
   * is handed the token as a promise; elsewhere it is an ordinary copy. */
  async function copy() {
    setCopyBusy(true)
    try {
      let landed: boolean
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        const blob = api
          .getPromptToken(card.id)
          .then((token) => new Blob([token], { type: 'text/plain' }))
        try {
          await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })])
          landed = true
        } catch {
          landed = await copyText(await api.getPromptToken(card.id))
        }
      } else {
        landed = await copyText(await api.getPromptToken(card.id))
      }
      if (landed) {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      } else {
        toast('Could not reach the clipboard.')
      }
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setCopyBusy(false)
    }
  }

  return (
    <div className={s.actions}>
      <Button
        size="sm"
        variant="ghost"
        iconOnly
        loading={copyBusy}
        onClick={() => void copy()}
        aria-label="Copy settings token"
        title={copied ? 'Settings token copied' : 'Copy settings token'}
      >
        {copied ? <CheckIcon /> : <BracesIcon />}
      </Button>
      <Button size="sm" variant="primary" loading={busy} onClick={() => void use()}>
        Use
      </Button>
    </div>
  )
}
