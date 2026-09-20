import type { TabSchema } from '@/api/types'
import { Alert, Card, Pill } from '@/components/ui'
import { controlCount } from '@/lib/schema'
import s from './tabs.module.css'

/*
 * A tab whose schema is derived but which is not marked ready to submit.
 *
 * Not a stub: it renders what the derivation already knows about the tab,
 * straight from `parity_baseline.json`. Seeing the real groups, field count
 * and argument count is what tells you whether the derivation is right
 * before anything is wired to it.
 */
export function StagePending({ schema }: { schema: TabSchema }) {
  const groups = schema.groups ?? []
  return (
    <div className={s.pending}>
      <Alert tone="info" title="Not wired up yet — Stage B">
        The form for this tab is already derived from the parity baseline; what is
        missing is the run against the real API. Krea2 is proving the
        contract first.
      </Alert>

      <Card
        title="What the baseline says about this tab"
        subtitle={`${schema.handler}() · tab id "${schema.tabId}"`}
      >
        <div style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap', marginBottom: 'var(--s-4)' }}>
          <Pill>
            <b>{schema.fields.length}</b> named controls
          </Pill>
          <Pill>
            <b>{controlCount(schema)}</b> controls in all
          </Pill>
          {schema.lora ? (
            <Pill tone="accent">
              <b>{schema.lora.count}</b> LoRA {schema.lora.shape}s
            </Pill>
          ) : (
            <Pill>no LoRA tail</Pill>
          )}
          <Pill>
            <b>{groups.length}</b> groups
          </Pill>
          <Pill>output: {schema.output}</Pill>
        </div>

        <div className={s.pendingGrid}>
          {schema.fields.map((field) => (
            <div key={field.name} className={s.pendingItem}>
              {field.label}
              <span>
                {field.name} · {field.type} · {field.column}
              </span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
