import type { TabSchema } from '@/api/types'
import { Alert, Card, Pill } from '@/components/ui'
import { argumentCount } from '@/lib/schema'
import s from './tabs.module.css'

/*
 * A tab whose schema is derived but which has not been wired yet.
 *
 * Stage A ships two tabs on purpose. Mocks cannot validate an image upload,
 * SSE through a proxy, video playback or the mask contract, and finding out
 * that an assumption was wrong twelve tabs deep is the one way this project
 * goes badly wrong. So Krea2 and Inpaint go end to end against the real API
 * in Section 2 first, and the rest follow.
 *
 * The page is not a stub, though: it renders what the derivation already
 * knows about this tab, straight from `parity_baseline.json`. If the field
 * count and the argument count below are right, Stage B is mostly a flag.
 */
export function StagePending({ schema }: { schema: TabSchema }) {
  const groups = schema.groups ?? []
  return (
    <div className={s.pending}>
      <Alert tone="info" title="Not wired up yet — Stage B">
        The form for this tab is already derived from the parity baseline; what is
        missing is the run against the real API. Krea2 and Inpaint are proving the
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
            <b>{argumentCount(schema)}</b> positional arguments
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
