import { useCallback, useEffect, useId, useState, type ReactNode } from 'react'
import type { TabSchema } from '@/api/types'
import { api } from '@/api/client'
import { useAutoPromptConfig } from '@/api/queries'
import { Button, useToast } from '@/components/ui'
import { FieldShell, TextAreaField } from '@/components/fields'
import { copyText, cx } from '@/lib/util'
import { useTabState } from '@/store/tabState'
import {
  CONTROLS_DEFAULT,
  controlsKey,
  useAutoPrompt,
  type AutoPromptControls,
  type AutoPromptRun,
} from '@/store/autoprompt'
import f from '@/components/fields/fields.module.css'
import s from './autoprompt.module.css'

/*
 * The MiniMax tabs' Auto prompt panel: a short idea in, the Prompt box filled.
 *
 * It only ever writes the prompt. Generating is still the tab's own button,
 * pressed after the prompt has been read and edited — unless "Generate when
 * ready" is ticked, in which case the prompt is queued the moment it arrives.
 * The writing itself is `store/autoprompt.ts`, so it carries on if this panel
 * is unmounted by a look at another tab.
 */
export function AutoPromptPanel({
  schema,
  values,
}: {
  schema: TabSchema
  values: Record<string, unknown>
}) {
  const [controls, setControls] = useTabState<AutoPromptControls>(
    controlsKey(schema.key),
    CONTROLS_DEFAULT,
  )
  const patch = useCallback(
    (next: Partial<AutoPromptControls>) => setControls((previous) => ({ ...previous, ...next })),
    [setControls],
  )
  const config = useAutoPromptConfig(schema.key, controls.on).data
  const run = useAutoPrompt((state) => state.runs[schema.key])
  const start = useAutoPrompt((state) => state.start)
  const cancel = useAutoPrompt((state) => state.cancel)
  const dismiss = useAutoPrompt((state) => state.dismiss)
  const toggleId = useId()
  const keyId = useId()
  const modelId = useId()
  const [showKey, setShowKey] = useState(false)
  const [copying, setCopying] = useState(false)
  const toast = useToast()

  const podKey = config?.podKey ?? ''
  const model = controls.model || config?.defaultModel || ''
  const chosen = config?.models.find((row) => row.id === model)

  // The key box arrives filled in with the machine's key, once, the first
  // time it is shown. After that it is whatever was typed; emptying it puts
  // the machine's key back when the box is left.
  useEffect(() => {
    if (config && controls.key === null) patch({ key: config.podKey })
  }, [config, controls.key, patch])

  const typedKey = controls.key ?? ''
  const fromPod = podKey !== '' && typedKey === podKey
  const busy = Boolean(run?.starting) || isLive(run)

  const write = () => {
    void start(schema, values, {
      idea: controls.idea,
      model,
      // The machine's own key stays on the machine: an empty key means it.
      key: fromPod ? '' : typedKey,
    })
  }

  /* The same text Write prompt would send, for somebody who would rather put
   * it to their own chat LLM and paste the answer into the Prompt box. It
   * comes from the server because the size block in it is the canvas the
   * clip will render at, which is the server's arithmetic. */
  const copy = async () => {
    setCopying(true)
    try {
      const landed = await copyText(await api.getAutoPromptText(schema, values, controls.idea))
      toast(
        !landed
          ? 'Could not reach the clipboard.'
          : schema.key === 'minimax_i2v'
            ? 'Copied. Paste it into any chat LLM with the start image attached, then paste its answer into the Prompt box.'
            : 'Copied. Paste it into any chat LLM, then paste its answer into the Prompt box.',
      )
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error))
    } finally {
      setCopying(false)
    }
  }

  return (
    <div className={cx(s.panel, !controls.on && s.off)}>
      <label className={s.head} htmlFor={toggleId}>
        <input
          id={toggleId}
          type="checkbox"
          className={f.checkBox}
          checked={controls.on}
          onChange={(event) => patch({ on: event.target.checked })}
        />
        <span>
          <span className={s.title}>Auto prompt</span>
          <span className={s.blurb}>
            Type a short idea and let an LLM write the full prompt: shots, camera, expressions,
            sound and music.
          </span>
        </span>
      </label>

      {controls.on && (
        <div className={s.body}>
          <div className={s.settings}>
            <FieldShell
              id={modelId}
              label="Model"
              hint={
                chosen?.free
                  ? 'Shared by everyone, so it can be busy for a few minutes before it answers.'
                  : chosen
                    ? 'About $0.002 a prompt. Answers in seconds.'
                    : undefined
              }
              trailing={
                chosen && (
                  <span className={cx(s.badge, chosen.free ? s.free : s.paid)}>
                    {chosen.free ? 'Free' : 'Paid'}
                  </span>
                )
              }
            >
              <select
                id={modelId}
                className={f.select}
                value={model}
                onChange={(event) => patch({ model: event.target.value })}
              >
                {(config?.models ?? []).map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.label}
                  </option>
                ))}
              </select>
            </FieldShell>

            <FieldShell
              id={keyId}
              label="OpenRouter API key"
              hint={
                fromPod ? (
                  <>
                    ✓ Filled in from <code>OPENROUTER_API_KEY</code> on this machine. Paste
                    another key to use that one instead.
                  </>
                ) : typedKey ? (
                  'Using the key you pasted.' +
                  (podKey ? ' Clear the box to go back to the one from OPENROUTER_API_KEY.' : '')
                ) : (
                  <>
                    Free models need a key too.{' '}
                    <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer">
                      Get one
                    </a>
                    , or set <code>OPENROUTER_API_KEY</code> on the machine Ember runs on.
                  </>
                )
              }
            >
              <div className={s.keyRow}>
                <input
                  id={keyId}
                  className={cx(f.input, s.keyInput)}
                  type={showKey ? 'text' : 'password'}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="sk-or-v1-…"
                  value={typedKey}
                  onChange={(event) => patch({ key: event.target.value })}
                  onBlur={() => {
                    if (!typedKey.trim() && podKey) patch({ key: podKey })
                  }}
                />
                <button type="button" className={s.show} onClick={() => setShowKey(!showKey)}>
                  {showKey ? 'Hide' : 'Show'}
                </button>
              </div>
            </FieldShell>
          </div>

          <TextAreaField
            label="Your idea"
            value={controls.idea}
            onChange={(idea) => patch({ idea })}
            lines={3}
            placeholder={
              schema.key === 'minimax_i2v'
                ? 'she puts the drinks down and dances'
                : 'a golden retriever running on a beach at sunset, barking at the waves'
            }
          />

          <div className={s.actions}>
            <Button onClick={write} loading={Boolean(run?.starting)} disabled={busy || !config}>
              ✍️ Write prompt
            </Button>
            {isLive(run) && <Button onClick={() => cancel(schema.key)}>Cancel</Button>}
            <Button onClick={() => void copy()} loading={copying}>
              📋 Copy LLM prompt
            </Button>
            <label className={f.check}>
              <input
                type="checkbox"
                className={f.checkBox}
                checked={controls.generateWhenReady}
                onChange={(event) => patch({ generateWhenReady: event.target.checked })}
              />
              <span className={f.checkLabel}>Generate when ready</span>
            </label>
          </div>

          <Status run={run} submitLabel={schema.submitLabel} onDismiss={() => dismiss(schema.key)} />
        </div>
      )}
    </div>
  )
}

function isLive(run: AutoPromptRun | undefined): boolean {
  const state = run?.task?.state
  return state === 'writing' || state === 'waiting'
}

/** The one line under the buttons that says where the write has got to. */
function Status({
  run,
  submitLabel,
  onDismiss,
}: {
  run: AutoPromptRun | undefined
  submitLabel: string
  onDismiss: () => void
}) {
  if (!run) return null
  const task = run.task
  const seconds = task ? Math.round(task.elapsed) : 0

  let tone: 'info' | 'warn' | 'ok' | 'err' = 'info'
  let text: ReactNode = null
  if (run.error) {
    tone = 'err'
    text = run.error
  } else if (run.starting) {
    text = 'Starting…'
  } else if (!task) {
    return null
  } else if (task.state === 'writing') {
    text = (
      <>
        Writing with <b>{task.modelLabel}</b>… <b>{seconds} s</b>
      </>
    )
  } else if (task.state === 'waiting') {
    tone = 'warn'
    text = `The free model is busy. Trying again in ${task.retryIn ?? 0} s… (try ${Math.min(
      task.call + 1,
      task.maxCalls,
    )} of ${task.maxCalls})`
  } else if (task.state === 'done') {
    tone = 'ok'
    text = run.queued ? (
      <>
        Prompt written in <b>{seconds} s</b> by {task.modelLabel} and queued, because{' '}
        <b>Generate when ready</b> is ticked.
      </>
    ) : (
      <>
        Prompt written in <b>{seconds} s</b> by {task.modelLabel}. Check it below, edit anything,
        then press <b>{submitLabel}</b>.
      </>
    )
  } else if (task.state === 'cancelled') {
    text = 'Cancelled. The Prompt box was left as it was.'
  } else {
    tone = 'err'
    text = task.error
  }

  const settled = run.error || (task && !isLive(run))
  return (
    <div className={cx(s.status, s[tone])} role="status">
      <span className={s.statusText}>{text}</span>
      {settled && (
        <button type="button" className={s.dismiss} onClick={onDismiss} aria-label="Dismiss">
          ✕
        </button>
      )}
    </div>
  )
}
