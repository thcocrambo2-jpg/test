import { useState, type ReactNode } from 'react'
import type { Field, FieldColumn, GroupSpec, TabSchema } from '@/api/types'
import {
  BoolField,
  FileField,
  ImageDropField,
  NumberField,
  RadioField,
  SelectField,
  SliderField,
  TextAreaField,
  TextField,
} from '@/components/fields'
import { MaskEditor, type InpaintEditorValue } from '@/components/fields/MaskEditor'
import { SeedRow } from '@/components/SeedRow'
import { LoraStack } from '@/components/LoraStack'
import { SamplerPanel, VariancePanel } from '@/components/SamplerPanel'
import { cx } from '@/lib/util'
import s from './form.module.css'

/*
 * One form for nine of the twelve tabs.
 *
 * The schema says what the fields are, in what order they submit, which column
 * they belong to and how they group. This renders that. There is no per-tab
 * form code anywhere: Krea2 and Inpaint are the same component with different
 * schemas, and so will the other seven be.
 *
 * The important invariant is that rendering never touches submission order.
 * Groups reorder freely for the eye; `toSubmission()` walks `schema.fields`,
 * which is baseline order, which is the handler's positional order.
 */

export interface SchemaFormProps {
  schema: TabSchema
  values: Record<string, unknown>
  setValue: (name: string, value: unknown) => void
  column: FieldColumn
  /* Hints that are not a property of the field. There is exactly one today:
   * the model info line, which reads the registry *and this pod's disk* —
   * "not downloaded yet" is a fact the schema cannot carry because it is not
   * about the control. */
  hints?: Record<string, ReactNode>
}

/** Whether a field's `showIf` is satisfied. */
function visible(field: Field, values: Record<string, unknown>): boolean {
  if (!field.showIf) return true
  return values[field.showIf.field] === field.showIf.equals
}

export function SchemaForm({ schema, values, setValue, column, hints }: SchemaFormProps) {
  const groups = schema.groups ?? []

  const inColumn = schema.fields.filter((field) => field.column === column)

  function renderField(field: Field): ReactNode {
    if (!visible(field, values)) return null
    return (
      <FieldRenderer
        key={field.name}
        field={field}
        values={values}
        setValue={setValue}
        hint={hints?.[field.name] ?? field.hint}
      />
    )
  }

  // Fields with no group render first, in order, so a control added to the
  // Python side appears rather than disappearing into a group that does not
  // exist.
  const ungrouped = inColumn.filter((field) => !field.group)

  return (
    <>
      {ungrouped.length > 0 && (
        <div className={s.group}>
          <div className={s.groupBody} style={{ paddingTop: 'var(--s-4)' }}>
            {ungrouped.map(renderField)}
          </div>
        </div>
      )}

      {groups.map((group) => {
        const fields = inColumn.filter((field) => field.group === group.id)
        if (fields.length === 0) return null
        return (
          <FieldGroup
            key={group.id}
            group={group}
            fields={fields}
            values={values}
            setValue={setValue}
            renderField={renderField}
          />
        )
      })}

      {column === 'left' && schema.lora && (
        <LoraStack spec={schema.lora} values={values} onChange={setValue} />
      )}
    </>
  )
}

function FieldGroup({
  group,
  fields,
  values,
  setValue,
  renderField,
}: {
  group: GroupSpec
  fields: Field[]
  values: Record<string, unknown>
  setValue: (name: string, value: unknown) => void
  renderField: (field: Field) => ReactNode
}) {
  const [open, setOpen] = useState(group.defaultOpen ?? true)
  const shown = group.collapsible ? open : true

  let body: ReactNode
  switch (group.renderer) {
    case 'seed':
      body = (
        <div className={s.groupBody}>
          <SeedRow
            seed={fields.find((field) => field.name === 'seed')}
            randomize={fields.find((field) => field.name === 'randomize')}
            batch={fields.find((field) => field.name === 'batch_count')}
            values={values}
            onChange={setValue}
          />
        </div>
      )
      break
    case 'sampler':
      body = <SamplerPanel fields={fields} render={renderField} />
      break
    case 'variance':
      body = <VariancePanel fields={fields} render={renderField} />
      break
    default:
      body = (
        <div className={cx(s.groupBody, group.dense && s.groupDense)}>
          {fields.map(renderField)}
        </div>
      )
  }

  // A group with no title is a bare container — the inpaint canvas, which
  // wants no chrome around it at all.
  if (!group.title) return <>{body}</>

  return (
    <section className={s.group}>
      {group.collapsible ? (
        <button
          type="button"
          className={cx(s.groupHead, s.groupHeadButton)}
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          <span>{group.title}</span>
          <span className={cx(s.groupCaret, open && s.groupCaretOpen)} aria-hidden>
            ▶
          </span>
        </button>
      ) : (
        <div className={s.groupHead}>
          <span>{group.title}</span>
        </div>
      )}
      {shown && body}
    </section>
  )
}

function FieldRenderer({
  field,
  values,
  setValue,
  hint,
}: {
  field: Field
  values: Record<string, unknown>
  setValue: (name: string, value: unknown) => void
  hint?: ReactNode
}) {
  const value = values[field.name]
  const set = (next: unknown) => setValue(field.name, next)

  switch (field.type) {
    case 'text':
      return (
        <TextField
          label={field.label}
          hint={hint}
          wide={field.wide}
          placeholder={field.placeholder}
          value={String(value ?? '')}
          onChange={set}
        />
      )
    case 'textarea':
      return (
        <TextAreaField
          label={field.label}
          hint={hint}
          wide
          lines={field.lines}
          placeholder={field.placeholder}
          value={String(value ?? '')}
          onChange={set}
        />
      )
    case 'number':
      return (
        <NumberField
          label={field.label}
          hint={hint}
          wide={field.wide}
          value={Number(value ?? 0)}
          step={field.step}
          min={field.min}
          max={field.max}
          onChange={set}
        />
      )
    case 'slider':
      return (
        <SliderFieldBound
          field={field}
          value={Number(value ?? field.min ?? 0)}
          onChange={set}
          hint={hint}
        />
      )
    case 'select':
      return (
        <SelectField
          label={field.label}
          hint={hint}
          wide={field.wide}
          value={String(value ?? '')}
          choices={field.choices ?? []}
          onChange={set}
        />
      )
    case 'radio':
      return (
        <RadioField
          label={field.label}
          hint={hint}
          wide
          value={String(value ?? '')}
          choices={field.choices ?? []}
          onChange={set}
        />
      )
    case 'bool':
      return (
        <BoolField
          label={field.label}
          hint={hint}
          wide={field.wide}
          value={Boolean(value)}
          onChange={set}
        />
      )
    case 'image':
      return (
        <ImageDropField
          label={field.label}
          hint={hint}
          wide
          value={(value as File | null) ?? null}
          onChange={set}
        />
      )
    case 'file':
      return (
        <FileField
          label={field.label}
          hint={hint}
          wide
          accept={field.accept}
          value={(value as File | null) ?? null}
          onChange={set}
        />
      )
    case 'mask':
      return (
        <MaskEditor
          value={(value as InpaintEditorValue) ?? { background: null, layers: [], revision: 0 }}
          onChange={set}
          grow={Number(values.grow ?? 0)}
          blur={Number(values.blur ?? 0)}
        />
      )
    default:
      return null
  }
}

function SliderFieldBound({
  field,
  value,
  onChange,
  hint,
}: {
  field: Field
  value: number
  onChange: (next: number) => void
  hint?: ReactNode
}) {
  return (
    <SliderField
      label={field.label}
      hint={hint}
      wide={field.wide}
      value={value}
      min={field.min}
      max={field.max}
      step={field.step}
      onChange={onChange}
    />
  )
}
