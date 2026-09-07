import { useRef, useState } from 'react'
import { useRecentImages } from '@/api/queries'
import type { MediaItem } from '@/api/types'
import { useToast } from '@/components/ui'
import { cx, fileName } from '@/lib/util'
import s from './recent.module.css'

/*
 * The reel under every image input.
 *
 * The lightbox has a filmstrip of everything in the gallery; this is that
 * shape put where the work actually chains. Half of what these tabs are for
 * is feeding one tab's output into the next — a Krea 2 still into Inpaint,
 * an edited frame into Wan or MiniMax — and until now that meant: go to the
 * gallery, download the file, come back, find it in a file picker. Three of
 * those four steps exist only because the picture was on the far side of a
 * browser dialog. It is on this machine, in the listing this app already
 * serves, so it can simply be one click.
 *
 * Stills only, from the newest page. `useRecentImages` asks the server for a
 * narrowed listing rather than filtering the answer here, because a page of
 * everything taken on a video tab is mostly clips — and a clip is not
 * something an image input can take.
 */
export function RecentStrip({
  value,
  onPick,
}: {
  /** What the field holds now, so the strip can light up the thumb it handed
   *  over — and stop lighting it up the instant something else is dropped,
   *  pasted or chosen. Derived rather than tracked: the picked file either is
   *  the field's value or it is not, and nothing has to notice the change. */
  value: File | null
  onPick: (file: File) => void
}) {
  const { data } = useRecentImages()
  const toast = useToast()
  const [picked, setPicked] = useState<{ id: string; file: File } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  /* The guard is a ref and the spinner is state, because they answer at
   * different times: a second click lands before React has re-rendered with
   * `busy` set, and two fetches racing means the field ends up holding
   * whichever one happened to finish last. */
  const inFlight = useRef(false)

  const items = data?.items ?? []
  const activeId = picked && picked.file === value ? picked.id : null

  async function pick(item: MediaItem) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(item.id)
    try {
      const file = await fileFromMedia(item)
      setPicked({ id: item.id, file })
      onPick(file)
    } catch {
      // Ordinary, and worth saying: the gallery page in hand can name a file
      // that has since been deleted from another tab, or from the disk.
      toast('That picture could not be read — it may have been deleted.')
    } finally {
      inFlight.current = false
      setBusy(null)
    }
  }

  // Nothing yet made, or a licence with no Gallery feature to ask. Either way
  // an empty strip is a row of chrome around nothing.
  if (items.length === 0) return null

  return (
    <div className={s.wrap}>
      <div className={s.head}>
        <span className={s.label}>Recent generations</span>
        <span className={s.note}>click one to reuse it</span>
      </div>
      <div className={s.strip}>
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className={cx(s.thumb, item.id === activeId && s.thumbActive)}
            onClick={() => void pick(item)}
            aria-pressed={item.id === activeId}
            aria-label={`Use ${fileName(item.id)}`}
            title={item.prompt || item.path}
          >
            {/* The 512px WebP, as everywhere else a thumbnail is drawn. The
                full-resolution file is fetched only when one is picked. */}
            <img src={item.thumbUrl ?? item.url} alt="" loading="lazy" />
            {busy === item.id && (
              <span className={s.busy} aria-hidden>
                <span className={s.spinner} />
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

/** One generated file, as a File the form can submit.
 *
 *  Fetched and then re-uploaded on submit, rather than referenced by the path
 *  the server already has it under. That is a real cost — several megabytes
 *  down and straight back up, over a tunnel — and it buys an invariant worth
 *  more than the bytes: an image field holds a File and nothing else. The
 *  preview, the size probe, the mask editor's canvas and `resolveUploads` all
 *  read the value the same way whether it was dropped, pasted or picked here,
 *  and none of them grows a second case. `/media` is served with
 *  `Cache-Control: private, max-age=86400`, so picking the same picture twice
 *  costs one request that the browser answers itself.
 *
 *  The name is the one ComfyUI gave the file. Nothing on the wire reads it —
 *  `resolveUploads` names every upload after the *field* it came from — but a
 *  File has to be called something, and `krea2_00042_.png` is a better answer
 *  to that than a placeholder if it is ever shown. */
async function fileFromMedia(item: MediaItem): Promise<File> {
  const response = await fetch(item.url)
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  const blob = await response.blob()
  return new File([blob], fileName(item.id), { type: blob.type || 'image/png' })
}
