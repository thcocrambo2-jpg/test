import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSchemas } from '@/api/queries'
import { isLive, useQueue, type Job } from '@/store/queue'
import { Button, EmptyState, ProgressBar } from '@/components/ui'
import { cx, relativeTime } from '@/lib/util'
import s from './queue.module.css'

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
                jobs.map((job) => (
                  <JobRow key={job.id} job={job} onNavigate={() => setOpen(false)} />
                ))
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

function JobRow({ job, onNavigate }: { job: Job; onNavigate: () => void }) {
  const navigate = useNavigate()
  const { data: schemas } = useSchemas()
  const select = useQueue((state) => state.select)
  const cancel = useQueue((state) => state.cancel)

  const route = schemas?.find((schema) => schema.key === job.tabKey)?.route

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
      onClick={() => {
        select(job.tabKey, job.id)
        if (route) navigate(route)
        onNavigate()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          select(job.tabKey, job.id)
          if (route) navigate(route)
          onNavigate()
        }
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
           * squares was the other thing wrong with this row. */
          job.images.length > 0 && (
            <span className={s.jobThumbs}>
              <img
                src={job.images[0].thumbUrl ?? job.images[0].url}
                alt=""
                className={s.jobThumb}
              />
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
