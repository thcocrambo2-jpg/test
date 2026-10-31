import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useCatalogue, useSchemas } from '@/api/queries'
import type { PromptCard, TabSchema } from '@/api/types'
import { Alert, Button, EmptyState, Modal, Pill, Segmented, Skeleton, useToast } from '@/components/ui'
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
 * A card is a picture and its settings token. Use loads the token into the
 * tab that made it, through the same loader as the Settings token panel, so
 * every value is guarded against this pod and a LoRA it does not have is
 * left out with a note. There is no prompt text to read or copy: a prompt
 * only makes that picture together with every other dial, and the token
 * carries all of them.
 *
 * The pictures come straight from R2 through signed links the licence
 * server hands out (prompts.py). A card for a tab this licence does not
 * grant still shows its picture — what that tab can make is exactly what is
 * worth seeing — but carries no token and says what it needs.
 */

const PAGE = 24

export function PromptLibrary() {
  /* In `tabState`, because both are things you chose and this page unmounts
   * as soon as you leave it. They are also the whole query key below, so
   * coming back repaints the same cards without asking the server again. */
  const [tab, setTab] = useTabState('prompts.tab', '')
  const [skip, setSkip] = useTabState('prompts.skip', 0)
  const [open, setOpen] = useState<PromptCard | null>(null)
  const label = useTabLabel()

  const { data, isLoading, error } = useQuery({
    queryKey: ['prompts', tab, skip],
    queryFn: () => api.getPrompts({ tab, skip, limit: PAGE }),
    staleTime: 60_000,
  })

  const cards = data?.prompts ?? []
  const total = data?.total ?? 0
  const tabs = useMemo(() => Object.keys(data?.tabs ?? {}), [data?.tabs])

  return (
    <>
      {tabs.length > 1 && (
        <div className={s.filters}>
          <Segmented
            value={tab}
            ariaLabel="Which tab"
            onChange={(value) => {
              setSkip(0)
              setTab(value)
            }}
            options={[
              { value: '', label: 'Everything' },
              ...tabs.map((key) => ({ value: key, label: label(key) })),
            ]}
          />
        </div>
      )}

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
          {tab ? 'Nothing in the library for that tab yet.' : 'New pictures and their settings appear here.'}
        </EmptyState>
      ) : (
        <div className={s.grid}>
          {cards.map((card) => (
            <PromptTile key={card.id} card={card} label={label(card.tab)} onOpen={() => setOpen(card)} />
          ))}
        </div>
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

      <Modal open={open !== null} onClose={() => setOpen(null)} title={open ? cardTitle(open, label) : ''} width={1100}>
        {open && (
          <div className={s.full}>
            <img src={open.full} alt="" />
            <div className={s.fullActions}>
              <CardActions card={open} label={label(open.tab)} onUsed={() => setOpen(null)} />
            </div>
          </div>
        )}
      </Modal>
    </>
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

function cardTitle(card: PromptCard, label: (key: string) => string): string {
  return card.title ? `${card.title} · ${label(card.tab)}` : label(card.tab)
}

function PromptTile({ card, label, onOpen }: { card: PromptCard; label: string; onOpen: () => void }) {
  return (
    <figure className={s.card}>
      <button type="button" className={s.picture} onClick={onOpen} aria-label="Open the full picture">
        <img
          src={card.thumb}
          alt=""
          loading="lazy"
          width={card.width ?? undefined}
          height={card.height ?? undefined}
        />
      </button>
      <figcaption className={s.caption}>
        <span className={s.label} title={card.title ? `${card.title} · ${label}` : label}>
          {card.title ? (
            <>
              <span className={s.title}>{card.title}</span> · {label}
            </>
          ) : (
            label
          )}
        </span>
        <CardActions card={card} label={label} />
      </figcaption>
    </figure>
  )
}

/** Copy token and Use for one card, or what the card needs when this
 *  licence does not have its tab. */
function CardActions({ card, label, onUsed }: { card: PromptCard; label: string; onUsed?: () => void }) {
  const { data: schemas } = useSchemas()
  const navigate = useNavigate()
  const toast = useToast()
  const loadToken = useTokenLoader()
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const target: TabSchema | undefined = useMemo(
    () => schemas?.find((schema) => schema.key === card.tab),
    [schemas, card.tab],
  )

  if (!card.token || !target) {
    return <Pill title="This tab is not part of this licence">🔒 Needs {label}</Pill>
  }
  const token = card.token

  /* Loads exactly as pasting the token into the Settings token panel would,
   * and says so the same way: a toast naming the tab, the count of notes
   * the guard left, and Undo, which puts the tab back and returns here. */
  async function use() {
    if (!target) return
    setBusy(true)
    try {
      const { data } = await readToken(token)
      const before = snapshotTab(target.key)
      const outcome = await loadToken(target, data)
      onUsed?.()
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

  async function copy() {
    if (await copyText(token)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } else {
      toast('Could not reach the clipboard.')
    }
  }

  return (
    <div className={s.actions}>
      <Button
        size="sm"
        variant="ghost"
        iconOnly
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
