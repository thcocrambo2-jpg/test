import { useState } from 'react'
import type { LoraSpec } from '@/api/types'
import { BoolField, SelectField, SliderField } from '@/components/fields'
import { Button } from '@/components/ui'
import { cx } from '@/lib/util'
import s from '@/components/SchemaForm/form.module.css'

/*
 * One LoRA stack, both shapes.
 *
 * Two submission shapes still exist and they are not interchangeable:
 * **triples** (enabled, name, weight) on every tab but Flux, whose rows
 * carry a per-row on/off checkbox, and **pairs** (name, weight) on Flux.
 * Flattening that distinction shifts every argument after the stack,
 * silently, and the pictures come back subtly wrong rather than the
 * request failing. See context.md §4.3.
 *
 * `spec.shape` is read off the baseline in `fromBaseline.ts`, so this
 * component never guesses which one it has.
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
    if (spec.shape === 'triple') return Boolean(values[`lora.${index}.enabled`])
    return true
  })

  // A stack of eight "None" dropdowns is eight rows of nothing, which is what
  // the Gradio tabs show. Collapsed by default, with the count on the header.
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
              This pipeline has no LoRA slots configured. The count is static — see
              V2_LORA_STACK in workflow_krea2_v2.py.
            </p>
          ) : (
            <>
              <div className={s.loraList}>
                {slots.map((index) => {
                  const nameKey = `lora.${index}.name`
                  const weightKey = `lora.${index}.weight`
                  const enabledKey = `lora.${index}.enabled`
                  const chosen = String(values[nameKey] ?? none)
                  const enabled =
                    spec.shape === 'triple' ? Boolean(values[enabledKey]) : chosen !== none
                  return (
                    <div
                      key={index}
                      className={cx(
                        s.loraSlot,
                        spec.shape === 'pair' && s.loraSlotPair,
                        !enabled && s.loraSlotIdle,
                      )}
                    >
                      {spec.shape === 'triple' && (
                        <div className={s.loraToggle}>
                          <BoolField
                            label=""
                            value={Boolean(values[enabledKey])}
                            onChange={(next) => onChange(enabledKey, next)}
                          />
                        </div>
                      )}
                      <SelectField
                        label={`${spec.slotLabel} ${index + 1}`}
                        value={chosen}
                        choices={spec.choices}
                        onChange={(next) => {
                          onChange(nameKey, next)
                          // Choosing a LoRA in a triple row and leaving the row
                          // switched off is never what anyone meant.
                          if (spec.shape === 'triple' && next !== none) {
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
              {active.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    slots.forEach((index) => {
                      onChange(`lora.${index}.name`, none)
                      onChange(`lora.${index}.weight`, spec.weightDefault)
                      if (spec.shape === 'triple') onChange(`lora.${index}.enabled`, false)
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
                (index) =>
                  `${values[`lora.${index}.name`]} @ ${values[`lora.${index}.weight`]}`,
              )
              .join(' · ')}
          </div>
        </div>
      )}
    </div>
  )
}
