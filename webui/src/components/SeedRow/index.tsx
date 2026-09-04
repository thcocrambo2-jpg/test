import type { Field } from '@/api/types'
import { BoolField, NumberField, SliderField } from '@/components/fields'
import { Button } from '@/components/ui'
import { cx } from '@/lib/util'
import s from '@/components/SchemaForm/form.module.css'

/** Seed, "Random seed", Batch count.
 *
 *  This trio is byte-identical on eight of the ten tabs in ui.py — the same
 *  three constructor calls with the same arguments, copied. It is one
 *  component here, and the only reason it takes fields rather than hardcoding
 *  labels is that the labels still come from the parity baseline.
 *
 *  The dice button is the addition: with "Random seed" ticked the number box
 *  is dead weight, and with it unticked there was no way to get a fresh seed
 *  without typing sixteen digits. */
export function SeedRow({
  seed,
  randomize,
  batch,
  values,
  onChange,
}: {
  seed?: Field
  randomize?: Field
  batch?: Field
  values: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
}) {
  const isRandom = randomize ? Boolean(values[randomize.name]) : false

  return (
    <div className={cx(s.seedRow, isRandom && s.seedRowLocked)}>
      {seed && (
        <NumberField
          label={seed.label}
          value={Number(values[seed.name] ?? 0)}
          step={seed.step ?? 1}
          onChange={(next) => onChange(seed.name, next)}
          hint={isRandom ? 'Ignored while Random seed is on.' : undefined}
        />
      )}
      {seed && (
        <div className={s.seedDice}>
          <Button
            size="sm"
            onClick={() => {
              onChange(seed.name, Math.floor(Math.random() * 2 ** 32))
              if (randomize) onChange(randomize.name, false)
            }}
            title="Roll a seed and pin it"
          >
            Roll
          </Button>
        </div>
      )}
      {randomize && (
        <BoolField
          label={randomize.label}
          value={isRandom}
          wide
          onChange={(next) => onChange(randomize.name, next)}
        />
      )}
      {batch && (
        <div className={s.seedBatch}>
          <SliderField
            label={batch.label}
            value={Number(values[batch.name] ?? batch.min ?? 1)}
            min={batch.min}
            max={batch.max}
            step={batch.step}
            onChange={(next) => onChange(batch.name, next)}
          />
        </div>
      )}
    </div>
  )
}
