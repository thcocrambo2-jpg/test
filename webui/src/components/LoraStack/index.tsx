import { useState } from 'react'
import type { LoraSpec } from '@/api/types'
import { BoolField, SelectField, SliderField, choiceLabel } from '@/components/fields'
import { Button } from '@/components/ui'
import { cx } from '@/lib/util'
import s from '@/components/SchemaForm/form.module.css'

/*
 * One LoRA stack.
 *
 * Every stack submits **triples** (enabled, name, weight), and each row
 * carries a per-row on/off checkbox. Getting that order wrong shifts every
 * argument after the stack, silently, and the pictures come back subtly
 * wrong rather than the request failing. See "Submission order is
 * load-bearing" in `docs/architecture/web-ui.md`.
 *
 * The `name` in each triple is a LoRA id from the licence server's catalogue
 * and the dropdown shows that LoRA's name (`spec.choiceLabels`). An id is
 * what a preset or a recipe stores, so it is also what the form holds —
 * translating to a name and back on every keystroke would be one more place
 * for a renamed record to go missing. `"None"` is still the empty slot.
 */
export function LoraStack({
  spec,
  values,
  onChange,
}: {
  spec: LoraSpec
  values: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
}) {
  const [open, setOpen] = useState(false)

  const none = spec.choices[0] ?? 'None'
  const slots = Array.from({ length: spec.count }, (_, index) => index)

  const active = slots.filter((index) => {
    const name = String(values[`lora.${index}.name`] ?? none)
    if (name === none) return false
    return Boolean(values[`lora.${index}.enabled`])
  })

  // A stack of eight "None" dropdowns is eight rows of nothing, so the tail
  // is collapsed by default, with the count on the header.
  const nothingToConfigure = spec.count === 0

  return (
    <div className={s.group}>
      <button
        type="button"
        className={cx(s.groupHead, s.groupHeadButton)}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span>
          {spec.title}{' '}
          <span className={s.groupCount}>
            {nothingToConfigure ? 'no slots' : `${active.length}/${spec.count} active`}
          </span>
        </span>
        <span className={cx(s.groupCaret, open && s.groupCaretOpen)} aria-hidden>
          ▶
        </span>
      </button>

      {open && (
        <div className={s.groupBody}>
          {nothingToConfigure ? (
            <p className={s.loraEmpty}>
              This tab offers no LoRAs. The V2 tabs get one row per LoRA in their
              feature's list in the licence server's catalogue, and that list is
              empty — or the catalogue could not be loaded when the pod started.
            </p>
          ) : (
            <>
              <div className={s.loraList}>
                {slots.map((index) => {
                  const nameKey = `lora.${index}.name`
                  const weightKey = `lora.${index}.weight`
                  const enabledKey = `lora.${index}.enabled`
                  const chosen = String(values[nameKey] ?? none)
                  const enabled = Boolean(values[enabledKey])
                  return (
                    <div
                      key={index}
                      className={cx(s.loraSlot, !enabled && s.loraSlotIdle)}
                    >
                      <div className={s.loraToggle}>
                        <BoolField
                          label=""
                          value={Boolean(values[enabledKey])}
                          onChange={(next) => onChange(enabledKey, next)}
                        />
                      </div>
                      <SelectField
                        label={`${spec.slotLabel} ${index + 1}`}
                        value={chosen}
                        choices={spec.choices}
                        labels={spec.choiceLabels}
                        onChange={(next) => {
                          onChange(nameKey, next)
                          // Choosing a LoRA and leaving the row switched off
                          // is never what anyone meant.
                          if (next !== none) {
                            onChange(enabledKey, true)
                          }
                        }}
                      />
                      <div className={s.loraWeight}>
                        <SliderField
                          label={spec.weightLabel}
                          value={Number(values[weightKey] ?? spec.weightDefault)}
                          min={spec.weightMin}
                          max={spec.weightMax}
                          step={spec.weightStep}
                          onChange={(next) => onChange(weightKey, next)}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
              {/* Back to the rows the tab ships with, not to eight blanks.
                * On Krea2 those are the same thing. On V2 each row *is* one
                * LoRA from the feature's list, at that LoRA's own strength,
                * and blanking them would leave a stack of "None" rows that
                * only a reload could refill. */}
              {active.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    slots.forEach((index) => {
                      const slot = spec.slots[index] ?? {}
                      onChange(`lora.${index}.name`, slot.name ?? none)
                      onChange(`lora.${index}.weight`, slot.weight ?? spec.weightDefault)
                      onChange(`lora.${index}.enabled`, slot.enabled ?? false)
                    })
                  }
                >
                  Reset all slots
                </Button>
              )}
            </>
          )}
        </div>
      )}

      {!open && active.length > 0 && (
        <div className={s.groupBody}>
          <div className={s.loraSummary}>
            {active
              .map(
                (index) => {
                  const name = String(values[`lora.${index}.name`])
                  const label = choiceLabel(spec.choiceLabels, name)
                  return `${label} @ ${values[`lora.${index}.weight`]}`
                },
              )
              .join(' · ')}
          </div>
        </div>
      )}
    </div>
  )
}
