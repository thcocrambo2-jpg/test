import { useState } from 'react'
import type { MediaItem, TabSchema } from '@/api/types'
import { isLive, useQueue, type Job } from '@/store/queue'
import { Alert, Button, Card, EmptyState, Pill, ProgressBar, Skeleton } from '@/components/ui'
import { Lightbox } from '@/features/gallery/Lightbox'
import { cx, useCopy } from '@/lib/util'
import s from './tabs.module.css'

/*
 * The right-hand column: what happened, and what came out.
 *
 * Three fixes live here.
 *
 *   * A determinate progress bar. The backend has always emitted
 *     {"type":"progress","step","total"} (client.py:270); the old UI polled a
 *     status *string* once a second and rendered it in a textbox.
 *
 *   * A persistent, dismissible error, visually distinct from status and
 *     keyed to the run that produced it. The old one was a `❌ …` string in
 *     that same textbox, overwritten by the next poll.
 *
 *   * A run picker. Every previous run of this tab is still here, so a batch
 *     you liked is not lost the moment you press the button again.
 */
export function OutputPanel({
  schema,
  job,
  runs,
}: {
  schema: TabSchema
  job: Job | undefined
  runs: Job[]
}) {
  const [lightbox, setLightbox] = useState<number | null>(null)
  const dismissError = useQueue((state) => state.dismissError)
  const select = useQueue((state) => state.select)
  const cancel = useQueue((state) => state.cancel)

  const images = job?.images ?? []
  const running = job ? isLive(job) : false

  return (
    <div className={s.output}>
      {job?.error && !job.errorDismissed && (
        <Alert
          tone="error"
          title={`${job.tabLabel} run failed`}
          onDismiss={() => dismissError(job.id)}
        >
          {job.error}
        </Alert>
      )}

      <Card
        title="Output"
        subtitle={job ? job.statusText : 'Nothing run yet'}
        actions={
          <div style={{ display: 'flex', gap: 'var(--s-2)', alignItems: 'center' }}>
            {job?.seed != null && (
              <Pill className={s.seed} title="The seed this run used">
                seed <b>{job.seed}</b>
              </Pill>
            )}
            {running && (
              <Button size="sm" variant="danger" onClick={() => cancel(job!.id)}>
                Cancel
              </Button>
            )}
          </div>
        }
      >
        <div className={s.statusCard}>
          {running && (
            <ProgressBar
              step={job?.progress?.step}
              total={job?.progress?.total}
              label={job?.statusText}
              detail={job?.queuePosition ? `${job.queuePosition} ahead` : 'starting'}
            />
          )}

          {job?.preview && (
            <img className={s.preview} src={job.preview} alt="Live preview of the latent" />
          )}

          {runs.length > 1 && (
            <div className={s.runs}>
              {runs.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  className={cx(s.run, run.id === job?.id && s.runActive)}
                  onClick={() => select(schema.key, run.id)}
                  title={run.prompt || run.statusText}
                >
                  {new Date(run.startedAt).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                  })}
                  {run.status === 'error' ? ' · failed' : ` · ${run.images.length}`}
                </button>
              ))}
            </div>
          )}

          {running && images.length === 0 ? (
            <div className={s.skeletonGrid}>
              {Array.from({ length: 2 }, (_, index) => (
                <Skeleton key={index} style={{ aspectRatio: '2 / 3' }} radius="var(--r-md)" />
              ))}
            </div>
          ) : images.length > 0 ? (
            <div className={s.grid}>
              {images.map((item, index) =>
                item.kind === 'video' ? (
                  <video key={item.id} className={s.videoTile} src={item.url} controls />
                ) : (
                  <Tile key={item.id} item={item} onOpen={() => setLightbox(index)} />
                ),
              )}
            </div>
          ) : (
            !running && (
              <EmptyState
                icon={schema.icon}
                title={`No ${schema.output === 'video' ? 'clips' : 'images'} yet`}
              >
                {schema.blurb} Fill the form and press{' '}
                <strong>{schema.submitLabel}</strong>, or hit Ctrl+Enter from anywhere on
                this page.
              </EmptyState>
            )
          )}
        </div>
      </Card>

      {lightbox !== null && images[lightbox] && (
        <Lightbox
          items={images}
          index={lightbox}
          onIndex={setLightbox}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  )
}

function Tile({ item, onOpen }: { item: MediaItem; onOpen: () => void }) {
  const { copied, copy } = useCopy()
  return (
    <div
      className={s.tile}
      style={{ ['--ratio' as string]: `${item.width} / ${item.height}` }}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      {/* Thumbnail, for the same reason the gallery grid uses one — see the
          note there. The lightbox this opens still shows the original. */}
      <img
        className={s.tileImage}
        src={item.thumbUrl ?? item.url}
        alt={item.prompt ?? ''}
        loading="lazy"
      />
      <div className={s.tileBar}>
        <span className={s.tileMeta}>
          {item.width} × {item.height}
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={(event) => {
            event.stopPropagation()
            copy(item.path)
          }}
          title="Copy the file path"
        >
          {copied ? 'Copied' : 'Path'}
        </Button>
      </div>
    </div>
  )
}
