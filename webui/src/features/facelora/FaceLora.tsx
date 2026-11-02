import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useSchemas } from '@/api/queries'
import type { FaceLoraOptions, FaceLoraRun, FaceLoraState } from '@/api/types'
import { Alert, Button, Card, EmptyState, Pill, ProgressBar, useToast } from '@/components/ui'
import { NumberField, SelectField, TextAreaField, TextField } from '@/components/fields'
import { useTabState } from '@/store/tabState'
import { cx } from '@/lib/util'
import s from './facelora.module.css'

/*
 * 🎭 Face LoRA — train a LoRA on a few photos of one face, try it in the real
 * Krea2 V2 tab, then publish it as a private LoRA to chosen licences.
 *
 * Only a licence granting `face_lora` gets this page (and its routes — the
 * server refuses the rest). The work happens on the pod: training is a job
 * on the GPU queue (it shows in the queue panel, and its ✕ stops it), and a
 * finished LoRA is put on the Krea2 V2 tab's LoRA rows straight away as
 * "🎭 <name> (trained here)". See ember/web/routes/face_lora.py.
 *
 * Publish uploads to private R2 with a key typed in here, registers the
 * LoRA and grants it to the keys listed. The R2 secret and access key live
 * only in this component's state for as long as the page is open; nothing
 * stores them. The account id and bucket are remembered in this browser.
 */

const IMAGE = /\.(jpe?g|png|webp)$/i
const REMEMBER = { account: 'facelora.r2.account', bucket: 'facelora.r2.bucket' }

const minutes = (seconds: number | null | undefined) => {
  if (seconds == null) return '—'
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const sec = total % 60
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(sec).padStart(2, '0')}`
}

const fileToId = (file: string) =>
  file.replace(/\.safetensors$/, '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '')

const safeName = (name: string) => name.replace(/[^A-Za-z0-9._ ()-]/g, '_')

export function FaceLora() {
  const { data, error } = useQuery({
    queryKey: ['face-lora'],
    queryFn: () => api.getFaceLora(),
    refetchInterval: 2000,
  })

  if (error) return <Alert tone="error">{(error as Error).message}</Alert>
  if (!data) return <EmptyState title="Loading…" />
  return (
    <div className={s.page}>
      <TrainCard state={data} />
      <ProgressCard state={data} />
      <TryCard state={data} />
      <PublishCard state={data} />
    </div>
  )
}

// ------------------------------------------------------------ 1 · train

function TrainCard({ state }: { state: FaceLoraState }) {
  const toast = useToast()
  const client = useQueryClient()
  const [name, setName] = useTabState('facelora.name', '')
  const [options, setOptions] = useTabState<FaceLoraOptions | null>('facelora.options', null)
  const opts = options ?? state.defaults
  const set = <K extends keyof FaceLoraOptions>(key: K, value: FaceLoraOptions[K]) =>
    setOptions({ ...opts, [key]: value })
  const [uploading, setUploading] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const files = useRef<HTMLInputElement>(null)
  const folder = useRef<HTMLInputElement>(null)
  const validName = /^[a-z0-9][a-z0-9_-]{0,40}$/.test(name)

  const photos = useQuery({
    queryKey: ['face-lora', 'photos', name],
    queryFn: () => api.getFaceLoraPhotos(name),
    enabled: validName,
  })

  async function upload(list: File[]) {
    if (!validName) {
      toast('Give the run a name first (a-z, 0-9, - and _).')
      return
    }
    const picked = list.filter((file) => IMAGE.test(file.name))
    if (!picked.length) {
      toast('No jpg, png or webp photos in that selection.')
      return
    }
    try {
      for (const [index, file] of picked.entries()) {
        setUploading(`${index + 1}/${picked.length}`)
        await api.addFaceLoraPhoto(name, file, safeName(file.name))
      }
    } catch (err) {
      toast((err as Error).message)
    } finally {
      setUploading(null)
      client.invalidateQueries({ queryKey: ['face-lora', 'photos', name] })
    }
  }

  async function train() {
    try {
      const answer = await api.trainFaceLora(name, opts)
      toast(answer.place > 1 ? `Queued — ${answer.place - 1} job(s) ahead of it.` : 'Training started.')
      client.invalidateQueries({ queryKey: ['face-lora'] })
    } catch (err) {
      toast((err as Error).message)
    }
  }

  const count = photos.data?.length ?? 0
  return (
    <Card title="1 · Photos and settings" subtitle="One face, 5–20 photos. More angles and lighting hold the face better.">
      <div className={s.grid}>
        <TextField label="Name" value={name} onChange={(v) => setName(v.toLowerCase())} placeholder="e.g. sayara" mono />
        <TextField
          label="Trigger word"
          value={opts.trigger}
          onChange={(v) => set('trigger', v)}
          hint="A made-up word, e.g. sayarax. Put it in your prompts."
        />
        <TextField label="Caption" value={opts.caption} onChange={(v) => set('caption', v)} mono />
        <NumberField label="Steps" value={opts.steps} onChange={(v) => set('steps', v)} min={50} max={6000} step={50} />
        <SelectField label="Rank" value={String(opts.rank)} onChange={(v) => set('rank', Number(v))} choices={['8', '16', '32']} />
        <SelectField
          label="Train resolution"
          value={String(opts.resolution)}
          onChange={(v) => set('resolution', Number(v))}
          choices={['512', '768', '1024']}
        />
        <NumberField
          label="Learning rate"
          value={opts.learning_rate}
          onChange={(v) => set('learning_rate', v)}
          step={0.00001}
        />
        <NumberField label="Save every (steps)" value={opts.save_every} onChange={(v) => set('save_every', v)} min={50} step={50} />
        <NumberField
          label="Blocks to swap"
          value={opts.blocks_to_swap}
          onChange={(v) => set('blocks_to_swap', v)}
          min={0}
          max={26}
          hint="0 keeps it all on the GPU. Raise only if it runs out of VRAM."
        />
      </div>

      <div
        className={cx(s.drop, over && s.dropOver)}
        onDragOver={(event) => {
          event.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={async (event) => {
          event.preventDefault()
          setOver(false)
          upload(await droppedFiles(event.dataTransfer.items))
        }}
      >
        <Button size="sm" onClick={() => files.current?.click()}>
          Choose photos…
        </Button>
        <Button size="sm" onClick={() => folder.current?.click()}>
          Choose a folder…
        </Button>
        <span className={s.muted}>
          {uploading ? `Uploading ${uploading}…` : validName ? `${count} photo(s) in ${name} — or drop photos / a folder here` : 'Name the run, then add photos'}
        </span>
        <input
          ref={files}
          type="file"
          accept=".jpg,.jpeg,.png,.webp"
          multiple
          hidden
          onChange={(event) => {
            upload(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />
        <input
          ref={folder}
          type="file"
          multiple
          hidden
          // A whole folder, subfolders included; only the photos go up.
          {...({ webkitdirectory: '' } as Record<string, string>)}
          onChange={(event) => {
            upload(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />
      </div>

      {count > 0 && (
        <div className={s.thumbs}>
          {photos.data!.map((photo) => (
            <figure key={photo} className={s.thumb}>
              <img src={`/api/v1/face-lora/photos/${encodeURIComponent(name)}/${encodeURIComponent(photo)}`} alt={photo} loading="lazy" />
              <button
                className={s.thumbRemove}
                title="Remove"
                onClick={async () => {
                  await api.removeFaceLoraPhoto(name, photo)
                  client.invalidateQueries({ queryKey: ['face-lora', 'photos', name] })
                }}
              >
                ✕
              </button>
            </figure>
          ))}
        </div>
      )}

      <div className={s.actions}>
        <Button variant="primary" onClick={train} disabled={!validName || count === 0 || state.busy}>
          Train
        </Button>
        {state.busy && <span className={s.muted}>A run is queued or training — see Progress.</span>}
      </div>
    </Card>
  )
}

/** Everything under a dropped folder, recursively. */
async function droppedFiles(items: DataTransferItemList): Promise<File[]> {
  // Read every entry before the first await: the browser empties the drop's
  // item list as soon as the handler yields.
  const entries = Array.from(items)
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => Boolean(entry))
  const out: File[] = []
  async function walk(entry: FileSystemEntry) {
    if (entry.isFile) {
      out.push(await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail)))
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail))
        if (!batch.length) break
        for (const child of batch) await walk(child)
      }
    }
  }
  for (const entry of entries) await walk(entry)
  return out
}

// --------------------------------------------------------- 2 · progress

function ProgressCard({ state }: { state: FaceLoraState }) {
  const run: FaceLoraRun | undefined = state.active ?? state.runs[0]
  if (!run) {
    return (
      <Card title="2 · Progress">
        <p className={s.muted}>Nothing trained yet.</p>
      </Card>
    )
  }
  const at = state.phases.indexOf(run.phase)
  const progress = run.progress
  const running = run.status === 'running'
  const queued = running && state.busy && run.phase === 'setup' && !run.progress
  return (
    <Card
      title="2 · Progress"
      subtitle={`${run.name} · ${queued ? 'queued' : run.status}`}
      actions={
        running && state.job ? (
          <Button size="sm" variant="danger" onClick={() => api.cancel(state.job!)}>
            Stop
          </Button>
        ) : undefined
      }
    >
      <div className={s.phases}>
        {state.phases.map((phase, index) => {
          const done = run.phase === 'done' || index < at
          const now = index === at && running
          const bad = index === at && (run.status === 'failed' || run.status === 'stopped')
          const took = run.phases[phase]
          return (
            <Pill key={phase} tone={bad ? 'danger' : now ? 'accent' : done ? 'success' : 'default'}>
              {phase.replace('_', ' ')}
              {took != null ? ` ${minutes(took)}` : ''}
            </Pill>
          )
        })}
      </div>
      {running && (
        <ProgressBar
          step={progress?.done}
          total={progress?.total}
          label={run.phase.replace('_', ' ')}
          eta={progress?.eta != null ? `${minutes(progress.eta)} left in this phase` : null}
        />
      )}
      <div className={s.stats}>
        <Stat label="speed" value={progress?.seconds_per_it ? `${progress.seconds_per_it.toFixed(2)} s/it` : '—'} />
        <Stat label="loss" value={run.loss != null ? run.loss.toFixed(4) : '—'} />
        <Stat label="total time" value={minutes((run.finished_at ?? state.now) - run.started_at)} />
        <Stat label="photos" value={run.images != null ? String(run.images) : '—'} />
      </div>
      {run.error && <Alert tone="error">{run.error}</Alert>}
      {run.log_tail.length > 0 && <pre className={s.log}>{run.log_tail.slice(-12).join('\n')}</pre>}
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.stat}>
      <b>{value}</b>
      <span>{label}</span>
    </div>
  )
}

// ----------------------------------------------------------- 3 · try it

function TryCard({ state }: { state: FaceLoraState }) {
  const navigate = useNavigate()
  const { data: schemas } = useSchemas()
  const v2 = schemas?.find((schema) => schema.key === 'krea_v2_t2i')
  const triggers = useMemo(() => triggerByFile(state.runs), [state.runs])
  return (
    <Card
      title="3 · Try it"
      subtitle="Trained LoRAs are on the 🔶 Krea2 V2 tab's LoRA rows on this pod, before you publish them."
      actions={
        v2 ? (
          <Button size="sm" onClick={() => navigate(v2.route)}>
            Open {v2.icon} Krea2 V2
          </Button>
        ) : undefined
      }
    >
      {state.loras.length === 0 ? (
        <p className={s.muted}>None yet.</p>
      ) : (
        <ul className={s.loraList}>
          {state.loras.map((file) => (
            <li key={file}>
              <code>🎭 {file.replace(/^face_/, '').replace(/\.safetensors$/, '')} (trained here)</code>
              {triggers[file] && (
                <span className={s.muted}>
                  {' '}
                  — switch its row on and use <b>{triggers[file]}</b> in the prompt
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

const triggerByFile = (runs: FaceLoraRun[]) =>
  Object.fromEntries(runs.map((run) => [`face_${run.name}.safetensors`, run.options?.trigger ?? '']))

// ---------------------------------------------------------- 4 · publish

function PublishCard({ state }: { state: FaceLoraState }) {
  const toast = useToast()
  const triggers = useMemo(() => triggerByFile(state.runs), [state.runs])
  const [lora, setLora] = useState('')
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState('')
  const [strength, setStrength] = useState(1)
  // This pod's own key by default; edit the list to grant others.
  const [keys, setKeys] = useState(state.licenceKey)
  const [account, setAccount] = useState(() => localStorage.getItem(REMEMBER.account) ?? '')
  const [bucket, setBucket] = useState(() => localStorage.getItem(REMEMBER.bucket) ?? state.bucket)
  // Held here only: never written to storage, cleared after each publish.
  const [accessKey, setAccessKey] = useState('')
  const [secret, setSecret] = useState('')

  const pick = (file: string) => {
    setLora(file)
    setId(fileToId(file))
    const stem = file.replace(/^face_/, '').replace(/\.safetensors$/, '')
    setName(stem.charAt(0).toUpperCase() + stem.slice(1))
    setTrigger(triggers[file] ?? '')
  }
  useEffect(() => {
    if (!lora && state.loras.length) pick(state.loras[state.loras.length - 1])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.loras])

  const p = state.publish
  const running = p.status === 'running'

  async function publish() {
    localStorage.setItem(REMEMBER.account, account)
    localStorage.setItem(REMEMBER.bucket, bucket)
    try {
      await api.publishFaceLora({
        lora, id, name, trigger, strength, keys,
        r2: { account, accessKey, secret, bucket },
      })
      setSecret('')
      setAccessKey('')
    } catch (err) {
      toast((err as Error).message)
    }
  }

  return (
    <Card
      title="4 · Publish as a private LoRA"
      subtitle="Uploads it to private R2, adds it to the Krea2 V2 and Krea2 LoRA lists, and grants it to the keys below — nobody else is sent it. Safe to press again: the same file is not uploaded twice."
    >
      {state.loras.length === 0 ? (
        <p className={s.muted}>Train one first.</p>
      ) : (
        <>
          <div className={s.grid}>
            <SelectField label="LoRA" value={lora} onChange={pick} choices={state.loras} />
            <TextField label="Id" value={id} onChange={setId} mono />
            <TextField label="Name in the app" value={name} onChange={setName} />
            <TextField label="Trigger" value={trigger} onChange={setTrigger} />
            <NumberField label="Default strength" value={strength} onChange={setStrength} min={0} max={2} step={0.05} />
          </div>
          <div className={s.split}>
            <TextAreaField
              label="Licence keys"
              value={keys}
              onChange={setKeys}
              lines={4}
              mono
              hint="One per line, or comma separated. This pod's own key is filled in."
            />
            <div className={s.grid2}>
              <TextField label="R2 account id" value={account} onChange={setAccount} mono />
              <TextField label="Bucket" value={bucket} onChange={setBucket} mono />
              <TextField label="R2 access key id" value={accessKey} onChange={setAccessKey} mono secret />
              <TextField
                label="R2 secret access key"
                value={secret}
                onChange={setSecret}
                mono
                secret
                hint="Used for this publish only, never saved."
              />
            </div>
          </div>
          <div className={s.actions}>
            <Button variant="primary" onClick={publish} loading={running} disabled={running || !lora}>
              Publish
            </Button>
            {p.status === 'done' && <span className={s.muted}>✅ Published {p.lora}</span>}
          </div>
          {running && p.step === 'uploading' && p.total > 0 ? (
            <ProgressBar
              step={Math.round(p.sent / 1e6)}
              total={Math.round(p.total / 1e6)}
              label="Uploading (MB)"
            />
          ) : running ? (
            <ProgressBar label={`${p.step ?? 'starting'}…`} />
          ) : null}
          {p.error && <Alert tone="error">{p.error}</Alert>}
          {p.done.length > 0 && (
            <ul className={s.doneList}>
              {p.done.map((line) => (
                <li key={line}>✓ {line}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  )
}
