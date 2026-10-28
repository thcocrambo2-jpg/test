import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSchemas } from '@/api/queries'
import type { JobMove } from '@/api/types'
import { isLive, useQueue, waitingLine, type Job } from '@/store/queue'
import { Button, EmptyState, ProgressBar, useToast } from '@/components/ui'
import { cx, relativeTime, useEta } from '@/lib/util'
import s from './queue.module.css'

/** What a lane is called above its line, when there is more than one. */
const LANE_NAMES: Record<string, string> = { comfy: 'Main', wan: 'Wan Video' }

/*
 * The queue.
 *
 * There is no equivalent in the current app. A submit blocks the tab it came
 * from, the status string is polled once a second into a textbox, and a run
 * you navigated away from is gone. Here every job is addressable: its
 * progress is the {step, total} the backend has always sent, its failure
 * survives until dismissed, and clicking one takes you back to the tab that
 * produced it with that run's output on screen.
 */
export function QueuePanel() {
  const [open, setOpen] = useState(false)
  const jobs = useQueue((state) => state.jobs)
  const clearFinished = useQueue((state) => state.clearFinished)

  const live = jobs.filter(isLive)
  const failed = jobs.filter((job) => job.status === 'error' && !job.errorDismissed)

  /* Three sections, because one newest-first list cannot be reordered: its
   * top row is the job that runs *last*. Waiting jobs are shown in the order
   * they will run, one line per lane, since a lane only ever trades places
   * within itself. */
  const running = jobs.filter((job) => job.status === 'running')
  const lanes = [...new Set(jobs.filter((job) => job.status === 'queued').map((job) => job.lane))]
  const finished = jobs.filter((job) => !isLive(job))

  return (
    <>
      <button
        type="button"
        className={cx(s.launcher, live.length > 0 && s.launcherLive)}
        onClick={() => setOpen(true)}
        aria-label="Open the queue"
      >
        {live.length > 0 && <span className={s.dot} aria-hidden />}
        <span>Queue</span>
        {live.length > 0 && <span className={s.jobTime}>{live.length} running</span>}
        {failed.length > 0 && <span className={s.count}>{failed.length}</span>}
      </button>

      {open && (
        <>
          <div className={s.scrim} onClick={() => setOpen(false)} />
          <aside className={s.panel} aria-label="Job queue">
            <header className={s.panelHead}>
              <span className={s.panelTitle}>
                Queue{jobs.length > 0 ? ` · ${jobs.length}` : ''}
              </span>
              <Button variant="ghost" size="sm" iconOnly onClick={() => setOpen(false)}>
                ✕
              </Button>
            </header>

            <div className={s.list}>
              {jobs.length === 0 ? (
                <EmptyState icon="◷" title="Nothing queued">
                  Runs appear here the moment you submit one, and stay until you clear
                  them — including the ones that failed.
                </EmptyState>
              ) : (
                <>
                  {running.length > 0 && <div className={s.section}>Running</div>}
                  {running.map((job) => (
                    <JobRow key={job.id} job={job} onNavigate={() => setOpen(false)} />
                  ))}
                  {lanes.map((lane) => (
                    <WaitingLine
                      key={lane}
                      line={waitingLine(jobs, lane)}
                      name={lanes.length > 1 ? (LANE_NAMES[lane] ?? lane) : null}
                      onNavigate={() => setOpen(false)}
                    />
                  ))}
                  {finished.length > 0 && <div className={s.section}>Finished</div>}
                  {finished.map((job) => (
                    <JobRow key={job.id} job={job} onNavigate={() => setOpen(false)} />
                  ))}
                </>
              )}
            </div>

            {jobs.some((job) => !isLive(job)) && (
              <div className={s.panelFoot}>
                <Button size="sm" block onClick={clearFinished}>
                  Clear finished
                </Button>
              </div>
            )}
          </aside>
        </>
      )}
    </>
  )
}

/** Opens the tab a job came from, with that run on screen. */
function useOpenJob(job: Job, onNavigate: () => void) {
  const navigate = useNavigate()
  const { data: schemas } = useSchemas()
  const select = useQueue((state) => state.select)
  const route = schemas?.find((schema) => schema.key === job.tabKey)?.route
  return () => {
    select(job.tabKey, job.id)
    if (route) navigate(route)
    onNavigate()
  }
}

interface Drag {
  id: string
  /** Where the pointer went down, so the row follows it. */
  originY: number
  offsetY: number
  /** Index in the line the row would be dropped in front of; the line's
   *  length means the end. Null while over its own place. */
  dropAt: number | null
}

/* One lane's waiting jobs, in run order, and the drag that reorders them.
 *
 * Pointer events rather than HTML drag and drop, which never fires for touch,
 * and the handle is the only thing that starts one, so a tap anywhere else on
 * the row still opens its tab and the list still scrolls. A drop is sent as
 * "in front of that job" rather than an index: by the time it reaches the
 * server, the job at the top may have started, and an index would land one
 * place off. */
function WaitingLine({
  line,
  name,
  onNavigate,
}: {
  line: Job[]
  name: string | null
  onNavigate: () => void
}) {
  const move = useQueue((state) => state.move)
  const toast = useToast()
  const rows = useRef(new Map<string, HTMLDivElement>())
  const [drag, setDrag] = useState<Drag | null>(null)

  function send(id: string, target: JobMove) {
    void move(id, target).then((refusal) => {
      if (refusal) toast(refusal)
    })
  }

  function dropIndex(clientY: number, from: number): number | null {
    let at = line.length
    for (const [index, job] of line.entries()) {
      // The dragged row travels with the pointer, so it is never the mark.
      if (index === from) continue
      const rect = rows.current.get(job.id)?.getBoundingClientRect()
      if (rect && clientY < rect.top + rect.height / 2) {
        at = index
        break
      }
    }
    // In front of itself, or of the job just after it: that is where it is.
    return at === from || at === from + 1 ? null : at
  }

  function onDown(event: PointerEvent<HTMLSpanElement>, job: Job) {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrag({ id: job.id, originY: event.clientY, offsetY: 0, dropAt: null })
  }

  function onMove(event: PointerEvent<HTMLSpanElement>) {
    if (!drag) return
    const from = line.findIndex((job) => job.id === drag.id)
    setDrag({
      ...drag,
      offsetY: event.clientY - drag.originY,
      dropAt: dropIndex(event.clientY, from),
    })
  }

  function onUp() {
    if (!drag) return
    const { id, dropAt } = drag
    setDrag(null)
    if (dropAt === null) return
    send(id, dropAt < line.length ? { before: line[dropAt].id } : { to: 'bottom' })
  }

  return (
    <>
      <div className={s.section}>
        <span>
          Up next{name && <span className={s.laneTag}>{name}</span>}
        </span>
        <span className={s.sectionHint}>{line.length} waiting</span>
      </div>
      {line.map((job, index) => (
        <WaitingRow
          key={job.id}
          job={job}
          place={index + 1}
          count={line.length}
          rowRef={(element) => {
            if (element) rows.current.set(job.id, element)
            else rows.current.delete(job.id)
          }}
          dragging={drag?.id === job.id ? drag.offsetY : null}
          dropBefore={drag?.dropAt === index}
          dropAfter={drag?.dropAt === line.length && index === line.length - 1}
          onHandleDown={(event) => onDown(event, job)}
          onHandleMove={onMove}
          onHandleUp={onUp}
          onMove={(target) => send(job.id, target)}
          onNavigate={onNavigate}
        />
      ))}
    </>
  )
}

function WaitingRow({
  job,
  place,
  count,
  rowRef,
  dragging,
  dropBefore,
  dropAfter,
  onHandleDown,
  onHandleMove,
  onHandleUp,
  onMove,
  onNavigate,
}: {
  job: Job
  place: number
  count: number
  rowRef: (element: HTMLDivElement | null) => void
  /** How far the row has been dragged, or null when it is not. */
  dragging: number | null
  dropBefore: boolean
  dropAfter: boolean
  onHandleDown: (event: PointerEvent<HTMLSpanElement>) => void
  onHandleMove: (event: PointerEvent<HTMLSpanElement>) => void
  onHandleUp: () => void
  onMove: (target: JobMove) => void
  onNavigate: () => void
}) {
  const cancel = useQueue((state) => state.cancel)
  const open = useOpenJob(job, onNavigate)
  // A row the server has not named a lane for yet is the click from a
  // moment ago; it gets its controls with the next snapshot.
  const movable = job.lane !== ''

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter') {
      open()
      return
    }
    if (!event.altKey || !movable) return
    const to = ({ ArrowUp: 'up', ArrowDown: 'down', Home: 'top', End: 'bottom' } as const)[
      event.key as 'ArrowUp' | 'ArrowDown' | 'Home' | 'End'
    ]
    if (!to) return
    event.preventDefault()
    onMove({ to })
  }

  return (
    <div
      ref={rowRef}
      className={cx(
        s.job,
        s.jobQueued,
        dragging !== null && s.dragging,
        dropBefore && s.dropBefore,
        dropAfter && s.dropAfter,
      )}
      style={dragging !== null ? { transform: `translateY(${dragging}px)` } : undefined}
      role="button"
      tabIndex={0}
      aria-label={`${job.tabLabel}, ${place} of ${count} waiting. Alt and arrow keys move it.`}
      onClick={open}
      onKeyDown={onKeyDown}
    >
      <div className={s.qRow}>
        {movable && count > 1 && (
          <span
            className={s.handle}
            title="Drag to reorder"
            aria-hidden
            onPointerDown={onHandleDown}
            onPointerMove={onHandleMove}
            onPointerUp={onHandleUp}
            onPointerCancel={onHandleUp}
            onClick={(event) => event.stopPropagation()}
          >
            ⋮⋮
          </span>
        )}
        <span className={cx(s.place, place === 1 && s.placeNext)}>{place}</span>
        <div className={s.qBody}>
          <div className={s.jobHead}>
            <span className={s.jobTab}>{job.tabLabel}</span>
            <span className={s.jobTime}>
              {relativeTime(new Date(job.startedAt).toISOString())}
            </span>
          </div>
          {job.prompt && <div className={s.jobPrompt}>{job.prompt}</div>}
          <div className={s.qActions}>
            {movable && place > 1 && (
              <Button
                size="sm"
                variant="ghost"
                className={s.runNext}
                onClick={(event) => {
                  event.stopPropagation()
                  onMove({ to: 'top' })
                }}
              >
                ⤒ Run next
              </Button>
            )}
            <span className={s.spacer} />
            <Button
              size="sm"
              variant="ghost"
              onClick={(event) => {
                event.stopPropagation()
                cancel(job.id)
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function JobRow({ job, onNavigate }: { job: Job; onNavigate: () => void }) {
  const cancel = useQueue((state) => state.cancel)
  const eta = useEta(job.status === 'running', job.etaAt)
  const open = useOpenJob(job, onNavigate)

  const dot =
    job.status === 'running'
      ? s.dotRunning
      : job.status === 'done'
        ? s.dotDone
        : job.status === 'error'
          ? s.dotError
          : job.status === 'cancelled'
            ? s.dotCancelled
            : s.dotQueued

  return (
    <div
      className={cx(s.job, isLive(job) && s.jobLive, job.status === 'error' && s.jobFailed)}
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === 'Enter') open()
      }}
    >
      <div className={s.jobHead}>
        <span className={s.jobTab}>{job.tabLabel}</span>
        <span className={s.jobTime}>
          {relativeTime(new Date(job.startedAt).toISOString())}
        </span>
      </div>

      {job.prompt && <div className={s.jobPrompt}>{job.prompt}</div>}

      {isLive(job) && (
        <ProgressBar
          step={job.progress?.step}
          total={job.progress?.total}
          detail={job.statusText}
          eta={eta}
        />
      )}

      <div className={s.jobStatus}>
        <span className={s.jobStatusText}>
          <span className={cx(s.statusDot, dot)} aria-hidden />
          <span className={s.jobStatusLine}>{job.error ?? job.statusText}</span>
        </span>
        {isLive(job) ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={(event) => {
              event.stopPropagation()
              cancel(job.id)
            }}
          >
            Cancel
          </Button>
        ) : (
          /* One thumbnail, not the batch. The row is 320px wide and its
           * job is to say which run this is, not to be a gallery — the run
           * picker on the tab shows the whole batch, and clicking anywhere
           * on this row goes there with this run selected. The thumbnail,
           * not the original: three full-resolution PNGs to draw 34px
           * squares was the other thing wrong with this row.
           *
           * A clip usually has no thumbnail yet when its run finishes —
           * ffmpeg is still pulling the frame on gallery_index's pool — and
           * a finished job's output is never restated, so this row never
           * hears of the thumbnail once it lands. The fallback is then the
           * .mp4 itself, which an <img> cannot draw; it gets the same
           * <video> the gallery grid uses for the same case. */
          job.images.length > 0 && (
            <span className={s.jobThumbs}>
              {job.images[0].kind === 'video' && !job.images[0].thumbUrl ? (
                <video
                  src={`${job.images[0].url}#t=0.1`}
                  preload="metadata"
                  muted
                  playsInline
                  className={s.jobThumb}
                />
              ) : (
                <img
                  src={job.images[0].thumbUrl ?? job.images[0].url}
                  alt=""
                  className={s.jobThumb}
                />
              )}
              {job.images.length > 1 && (
                <span className={s.jobMore}>+{job.images.length - 1}</span>
              )}
            </span>
          )
        )}
      </div>
    </div>
  )
}
