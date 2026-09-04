import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { PromptCard, TabSchema } from '@/api/types'
import { Alert, Button, Card, EmptyState, Pill, Segmented, Skeleton, useToast } from '@/components/ui'
import { TextField } from '@/components/fields'
import { useHandoff } from '@/store/handoff'
import s from './library.module.css'

/*
 * The Prompt Library — the one tab that reaches into another.
 *
 * Cards come from the licence server (prompts.py) and a Use button writes a
 * whole recipe into the Krea2 or Krea2 V2 controls and takes you there. The
 * settings blob is the same one a preset carries, so the guarding is the same
 * too: it goes through `POST /schema/{tab}/apply`, which leaves a model or a
 * LoRA file this pod does not have alone and clamps a number from a build
 * whose slider went further. A card written on someone else's pod is the
 * normal case, not the edge case.
 *
 * A card for a tab this licence does not grant still renders. It just cannot
 * be used, and it says so — which is what the Gradio version did, and is
 * better than pretending the prompt does not exist.
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
  const [tab, setTab] = useState('')
  const [source, setSource] = useState('')
  const [search, setSearch] = useState('')
  const [skip, setSkip] = useState(0)

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

  return (
    <Card
      title={card.title || 'Untitled'}
      subtitle={card.source === 'admin' ? '⭐ Official' : '👥 Community'}
      actions={
        target ? (
          <Button size="sm" variant="primary" loading={busy} onClick={() => void use()}>
            Use
          </Button>
        ) : (
          <Pill title="This tab is not part of this licence">not licensed</Pill>
        )
      }
    >
      <p className={s.prompt}>{card.prompt}</p>
      {card.negative && <p className={s.negative}>{card.negative}</p>}
    </Card>
  )
}
