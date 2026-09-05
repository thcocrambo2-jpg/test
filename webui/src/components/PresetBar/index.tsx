import { useEffect, useState } from 'react'
import type { TabSchema } from '@/api/types'
import { api } from '@/api/client'
import { usePresets } from '@/api/queries'
import { SelectField } from '@/components/fields'
import { useToast } from '@/components/ui'
import { useTabState } from '@/store/tabState'
import s from './preset.module.css'

/*
 * The preset dropdown, above every tab that has one.
 *
 * A preset is a generation tab's control panel saved under a name on the
 * licence server. Picking one writes every control below it and leaves the
 * prompt boxes alone, which is the whole difference between a preset and a
 * prompt-library card: same settings blob, minus the words.
 *
 * Two things about it are easy to get wrong and are worth stating.
 *
 * **The guarding is server-side.** `POST /schema/{tab}/apply` answers with
 * only the values it could safely set. A preset written on someone else's pod
 * may name a model or a LoRA file this one does not have, and a select handed
 * a value outside its options is a broken control rather than a wrong one —
 * so an unknown choice leaves that control alone and a number out of this
 * build's range is clamped into it (tabschema.preset_values, which is
 * ui._pick and ui._num). The browser cannot do that: `choices` for a LoRA
 * dropdown is a listing of the pod's disk.
 *
 * **The default is applied on load.** The preset a tab marks default is what
 * a fresh session opens on, so the shipped defaults are a row in Atlas rather
 * than something compiled into the binary — seed the values as a preset, mark
 * it default, and every pod picks it up on its next start with nothing
 * rebuilt. ui._load_default_presets did this after the tab loop; here it is
 * one effect, and it runs once per tab because re-applying it would fight the
 * customer for the controls.
 *
 * An Edit tab offers its *generation* tab's presets — Krea2's fill Krea2
 * Edit, V2's fill V2 Edit — each applying the subset of the blob that tab has
 * controls for. Which is why `presetTab` is a presets tab id and not this
 * tab's own key.
 */
export function PresetBar({
  schema,
  onApply,
}: {
  schema: TabSchema
  onApply: (values: Record<string, unknown>) => void
}) {
  const { data, isLoading } = usePresets(schema.presetTab)
  /* Both of these are per tab and outlive it, and the second one is the
   * whole of what "runs once per tab" means.
   *
   * They were a `useState` and a `useRef`, on the reading that a tab switch
   * remounts this component. It does not: React Router renders the matched
   * route into the same position, so `TabPage` is reconciled with `TabPage`
   * and everything here is re-rendered with a different `schema` instead.
   * One ref therefore held one tab's answer at a time — Krea2 to V2 and
   * back left it naming V2, the guard below missed, and Krea2's default
   * preset was applied a second time over every dial the customer had
   * touched since. Which is exactly what a preset writes and a prompt is
   * not: the prompt stayed and everything under it snapped back. */
  const [chosen, setChosen] = useTabState(`preset.chosen.${schema.key}`, '')
  const [applied, setApplied] = useTabState(`preset.applied.${schema.key}`, false)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  async function apply(name: string) {
    setChosen(name)
    if (!name) return
    setBusy(true)
    try {
      onApply(await api.applyPreset(schema.key, name))
      toast(`Loaded the “${name}” preset`)
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (applied || !data) return
    setApplied(true)
    if (data.default) void apply(data.default)
    // `apply` is deliberately not a dependency: it is redefined on every
    // render and the flag above is what decides whether this runs at all.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applied, setApplied, data])

  if (!schema.presetTab) return null

  const rows = data?.presets ?? []
  // Nothing seeded on the licence server is the ordinary state of a fresh
  // deployment, and the tab is perfectly usable at its built-in defaults —
  // so an empty list is not an empty dropdown, it is no dropdown.
  if (!isLoading && rows.length === 0 && !data?.error) return null

  return (
    <div className={s.bar}>
      <SelectField
        label="Preset"
        hint={busy ? 'Loading…' : (data?.error ?? schema.presetNote)}
        wide
        value={chosen}
        choices={['', ...rows.map((row) => row.name)]}
        onChange={(value) => void apply(value)}
      />
    </div>
  )
}
