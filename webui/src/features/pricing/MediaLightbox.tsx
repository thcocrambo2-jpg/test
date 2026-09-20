import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ShowcaseMedia } from '@/api/types'
import { Button } from '@/components/ui'
import g from '@/features/gallery/gallery.module.css'

/** A single showcase picture, full size. Escape closes it, which is the
 *  first thing anybody tries on a full-screen image. */
export function MediaLightbox({
  media,
  onClose,
}: {
  media: ShowcaseMedia
  onClose: () => void
}) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [onClose])

  if (!media.url) return null

  return createPortal(
    <div className={g.lightbox} role="dialog" aria-modal="true">
      <header className={g.lightboxHead}>
        <span className={g.lightboxTitle}>{media.caption ?? media.label ?? media.path}</span>
        <Button variant="ghost" size="sm" iconOnly onClick={onClose} aria-label="Close">
          ✕
        </Button>
      </header>
      <div className={g.lightboxStage} onClick={onClose}>
        {media.is_video ? (
          <video className={g.lightboxImage} src={media.url} controls autoPlay loop />
        ) : (
          <img className={g.lightboxImage} src={media.url} alt={media.caption ?? ''} />
        )}
      </div>
      <div />
    </div>,
    document.body,
  )
}
