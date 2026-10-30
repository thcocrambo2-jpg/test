import { useRef, useState } from 'react'
import { useRecentImages, useRecentUploads } from '@/api/queries'
import { useToast } from '@/components/ui'
import { markOwn } from '@/lib/ownFiles'
import { cx, fileName } from '@/lib/util'
import s from './recent.module.css'

/*
 * The reel under every image input.
 *
 * The lightbox has a filmstrip of everything in the gallery; this is that
 * shape put where the work actually chains. Half of what these tabs are for
 * is feeding one tab's output into the next — a Krea 2 still into Edit,
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
 *
 * `source="uploads"` is the second row under it: the user's own pictures
 * that earlier runs were given, newest first, so a photo used yesterday is
 * one click away instead of a trip through a file picker. Same tiles, same
 * picking; only the list differs — see sources.py for what is on it.
 */

/** One tile, whichever list it came from. */
interface Tile {
  id: string
  url: string
  thumbUrl: string
  title: string
}

const HEADINGS = { generations: 'Recent generations', uploads: 'Recent uploads' }

function useTiles(source: 'generations' | 'uploads'): Tile[] {
  // Both hooks every render, as hooks must be; each strip reads its own.
  const generations = useRecentImages()
  const uploads = useRecentUploads()
  if (source === 'uploads') {
    return (uploads.data?.items ?? []).map((item) => ({
      id: item.name,
      url: item.url,
      thumbUrl: item.thumbUrl,
      title: `Uploaded, last used ${item.usedAt.replace('T', ' ')}`,
    }))
  }
  return (generations.data?.items ?? []).map((item) => ({
    id: item.id,
    url: item.url,
    // The 512px WebP, as everywhere else a thumbnail is drawn.
    thumbUrl: item.thumbUrl ?? item.url,
    title: item.prompt || item.path,
  }))
}
export function RecentStrip({
  value,
  onPick,
  note = 'click one to reuse it',
  disabled = false,
  source = 'generations',
}: {
  /** What the field holds now, so the strip can light up the thumb it handed
   *  over — and stop lighting it up the instant something else is dropped,
   *  pasted or chosen. Derived rather than tracked: the picked file either is
   *  the field's value or it is not, and nothing has to notice the change.
   *  A list for a field that holds several: the thumb lights while its file
   *  is any of them. */
  value: File | readonly File[] | null
  onPick: (file: File) => void
  /** The line beside the heading. A field that adds rather than replaces
   *  says where the picture will go. */
  note?: string
  /** Shown but not clickable — a field that is full. */
  disabled?: boolean
  /** Which list: what this app made, or what the user uploaded before. */
  source?: 'generations' | 'uploads'
}) {
  const items = useTiles(source)
  const toast = useToast()
  const [picked, setPicked] = useState<{ id: string; file: File } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  /* The guard is a ref and the spinner is state, because they answer at
   * different times: a second click lands before React has re-rendered with
   * `busy` set, and two fetches racing means the field ends up holding
   * whichever one happened to finish last. */
  const inFlight = useRef(false)

  const held = Array.isArray(value) ? value : value ? [value] : []
  const activeId = picked && held.includes(picked.file) ? picked.id : null

  async function pick(item: Tile) {
    if (inFlight.current || disabled) return
    inFlight.current = true
    setBusy(item.id)
    try {
      const file = await fileFromUrl(item.url, fileName(item.id))
      // Picked again, it is still the user's own: it stays on the list and
      // moves to the front when this run is submitted.
      if (source === 'uploads') markOwn([file])
      setPicked({ id: item.id, file })
      onPick(file)
    } catch {
      // Ordinary, and worth saying: the list in hand can name a file that
      // has since been deleted from another tab, or from the disk.
      toast('That picture could not be read — it may have been deleted.')
    } finally {
      inFlight.current = false
      setBusy(null)
    }
  }

  // Nothing yet made or uploaded, or a licence with no Gallery feature to
  // ask. Either way an empty strip is a row of chrome around nothing.
  if (items.length === 0) return null

  return (
    <div className={s.wrap}>
      <div className={s.head}>
        <span className={s.label}>{HEADINGS[source]}</span>
        <span className={s.note}>{note}</span>
      </div>
      <div className={s.strip}>
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className={cx(s.thumb, item.id === activeId && s.thumbActive)}
            disabled={disabled}
            onClick={() => void pick(item)}
            aria-pressed={item.id === activeId}
            aria-label={`Use ${fileName(item.id)}`}
            title={item.title}
          >
            {/* The full-resolution file is fetched only when one is picked. */}
            <img src={item.thumbUrl} alt="" loading="lazy" />
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

/** One listed file, as a File the form can submit.
 *
 *  Fetched and then re-uploaded on submit, rather than referenced by the path
 *  the server already has it under. That is a real cost — several megabytes
 *  down and straight back up, over a tunnel — and it buys an invariant worth
 *  more than the bytes: an image field holds a File and nothing else. The
 *  preview, the size probe and `resolveUploads` all
 *  read the value the same way whether it was dropped, pasted or picked here,
 *  and none of them grows a second case. `/media` is served with
 *  `Cache-Control: private, max-age=86400`, so picking the same picture twice
 *  costs one request that the browser answers itself.
 *
 *  The name is the one ComfyUI gave the file. Nothing on the wire reads it —
 *  `resolveUploads` names every upload after the *field* it came from — but a
 *  File has to be called something, and `krea2_00042_.png` is a better answer
 *  to that than a placeholder if it is ever shown. */
async function fileFromUrl(url: string, name: string): Promise<File> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  const blob = await response.blob()
  return new File([blob], name, { type: blob.type || 'image/png' })
}
