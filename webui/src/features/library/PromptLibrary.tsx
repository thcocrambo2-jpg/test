import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { PromptCard, TabSchema } from '@/api/types'
import { Alert, Button, Card, EmptyState, Pill, Segmented, Skeleton, useToast } from '@/components/ui'
import { TextField } from '@/components/fields'
import { useHandoff } from '@/store/handoff'
import { useTabState } from '@/store/tabState'
import { copyText, useCopy } from '@/lib/util'
import { defaultsFor } from '@/lib/schema'
import { encodeToken, tokenData } from '@/lib/settingsToken'
import { BracesIcon, CheckIcon } from '@/features/gallery/icons'
import s from './library.module.css'

/*
 * The Prompt Library — the one tab that reaches into another.
 *
 * Cards come from the licence server (prompts.py) and a Use button writes a
 * whole recipe into the Krea2 or Krea2 V2 controls and takes you there. The
 * settings blob is the same one a preset carries, so the guarding is the same
 * too: it goes through `POST /schema/{tab}/apply`, which leaves alone a model
 * or a LoRA id this tab does not offer and clamps a number from a build
 * whose slider went further. A card written on someone else's pod is the
 * normal case, not the edge case.
 *
 * A card for a tab this licence does not grant still renders. It just cannot
 * be used, and it says so, which is better than pretending the prompt does
 * not exist.
 */

const PAGE = 12

const TABS = [
  { value: '', label: 'Everything' },
  { value: 'krea_t2i', label: '🎨 Krea2' },
  { value: 'krea_v2_t2i', label: '🔶 Krea2 V2' },
]

const SOURCES = [
  { value: '', label: 'All prompts' },
  { value: 'admin', label: '⭐ Official' },
  { value: 'community', label: '👥 Community' },
]

export function PromptLibrary() {
  /* All four in `tabState`, because all four are things you typed or chose and
   * this page unmounts as soon as you leave it — the search box most of
   * all. They are also the whole query key below, so restoring them inside the
   * minute repaints the same cards without asking the server again. */
  const [tab, setTab] = useTabState('prompts.tab', '')
  const [source, setSource] = useTabState('prompts.source', '')
  const [search, setSearch] = useTabState('prompts.search', '')
  const [skip, setSkip] = useTabState('prompts.skip', 0)

  const { data, isLoading, error } = useQuery({
    queryKey: ['prompts', tab, source, search, skip],
    queryFn: () => api.getPrompts({ tab, source, search, skip, limit: PAGE }),
    staleTime: 60_000,
  })

  function filter(next: () => void) {
    setSkip(0)
    next()
  }

  const cards = data?.prompts ?? []
  const total = data?.total ?? 0

  return (
    <>
      <div className={s.filters}>
        <Segmented
          value={tab}
          ariaLabel="Which tab"
          onChange={(value) => filter(() => setTab(value))}
          options={TABS}
        />
        <Segmented
          value={source}
          ariaLabel="Which source"
          onChange={(value) => filter(() => setSource(value))}
          options={SOURCES}
        />
        <div className={s.search}>
          <TextField
            label="Search"
            value={search}
            placeholder="a word from the prompt"
            onChange={(value) => filter(() => setSearch(value))}
          />
        </div>
      </div>

      {error && <Alert tone="error">{(error as Error).message}</Alert>}
      {data?.error && <Alert tone="warning">{data.error}</Alert>}

      {isLoading ? (
        <div className={s.grid}>
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} height={220} radius="var(--r-lg)" />
          ))}
        </div>
      ) : cards.length === 0 ? (
        <EmptyState icon="🌟" title="Nothing here yet">
          {search || tab || source
            ? 'No prompt matches that filter.'
            : 'Prompts published from a generation tab appear here.'}
        </EmptyState>
      ) : (
        <div className={s.grid}>
          {cards.map((card) => (
            <PromptTile key={card.id} card={card} />
          ))}
        </div>
      )}

      {total > PAGE && (
        <div className={s.pager}>
          <Button
            variant="ghost"
            disabled={skip === 0}
            onClick={() => setSkip(Math.max(0, skip - PAGE))}
          >
            ← Newer
          </Button>
          <span className={s.pagerCount}>
            {skip + 1}–{Math.min(skip + PAGE, total)} of {total}
          </span>
          <Button
            variant="ghost"
            disabled={skip + PAGE >= total}
            onClick={() => setSkip(skip + PAGE)}
          >
            Older →
          </Button>
        </div>
      )}
    </>
  )
}

function PromptTile({ card }: { card: PromptCard }) {
  const { data: schemas } = useSchemas()
  const navigate = useNavigate()
  const offer = useHandoff((state) => state.offer)
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const { copied, copy } = useCopy()
  const [tokenBusy, setTokenBusy] = useState(false)
  const [tokenCopied, setTokenCopied] = useState(false)

  const target: TabSchema | undefined = useMemo(
    () => schemas?.find((schema) => schema.key === card.tab),
    [schemas, card.tab],
  )

  async function use() {
    if (!target) return
    setBusy(true)
    try {
      // The settings go through the server so they are guarded against this
      // pod, exactly as a preset is. The two prompt boxes are added here
      // because they are the one thing a card carries and a preset does not.
      const values = await api.applySettings(target.key, card.settings)
      offer(target.key, {
        ...values,
        prompt: card.prompt,
        negative: card.negative ?? '',
      })
      navigate(target.route)
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  /* The card as a settings token, built on demand rather than stored: the
   * same guarded settings Use writes, plus the two prompt boxes, over the
   * tab's defaults, encoded exactly as the tab's own Settings token panel
   * would. Built, so it is always in the current token format. */
  async function copyToken() {
    if (!target) return
    setTokenBusy(true)
    try {
      const values = await api.applySettings(target.key, card.settings)
      const form = {
        ...defaultsFor(target),
        ...values,
        prompt: card.prompt,
        negative: card.negative ?? '',
      }
      if (await copyText(await encodeToken(target, tokenData(target, form)))) {
        setTokenCopied(true)
        setTimeout(() => setTokenCopied(false), 1600)
      } else {
        toast('Could not reach the clipboard.')
      }
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setTokenBusy(false)
    }
  }

  return (
    <Card
      title={card.title || 'Untitled'}
      subtitle={card.source === 'admin' ? '⭐ Official' : '👥 Community'}
      actions={
        <div className={s.actions}>
          {/* The prompt text alone — no settings, no tab switch. Offered
            * whether or not this licence has the card's tab. */}
          <Button
            size="sm"
            variant="ghost"
            title="Copy the prompt text"
            onClick={() =>
              void copy(card.prompt).then((landed) => {
                if (!landed) toast('Could not copy the prompt')
              })
            }
          >
            {copied ? 'Copied' : 'Copy'}
          </Button>
          {/* Needs the tab, like Use: the settings are guarded against
            * this pod's own lists on the way into the token. */}
          {target && (
            <Button
              size="sm"
              variant="ghost"
              iconOnly
              loading={tokenBusy}
              onClick={() => void copyToken()}
              aria-label="Copy settings token"
              title={tokenCopied ? 'Settings token copied' : 'Copy settings token'}
            >
              {tokenCopied ? <CheckIcon /> : <BracesIcon />}
            </Button>
          )}
          {target ? (
            <Button size="sm" variant="primary" loading={busy} onClick={() => void use()}>
              Use
            </Button>
          ) : (
            <Pill title="This tab is not part of this licence">not licensed</Pill>
          )}
        </div>
      }
    >
      <p className={s.prompt}>{card.prompt}</p>
      {card.negative && <p className={s.negative}>{card.negative}</p>}
    </Card>
  )
}
