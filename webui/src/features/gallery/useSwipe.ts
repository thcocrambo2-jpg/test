import { useCallback, useRef, type MouseEvent, type TouchEvent } from 'react'

/** How far a finger has to travel sideways, in px, to be a swipe and not a
 *  tap that wandered. */
const DISTANCE = 50
/** How much more sideways than upright it has to be. */
const SLOPE = 1.5
/** A flick, not a slow drag that happened to end somewhere else. */
const DURATION = 700
/** The height of a `<video controls>` bar, from the clip's bottom edge. A
 *  touch that starts there is scrubbing the timeline. */
const CONTROLS = 64

/** Swipe left for the next file and right for the previous one, on a phone.
 *
 *  The ends of the stage do this for a click, but a clip in portrait on a
 *  phone fills the width and leaves no end to tap, and a swipe is the
 *  gesture a thumb tries first anyway.
 *
 *  Touch events rather than pointer events. The picture's frame sets
 *  `touch-action: none` for its pinch and pan, but a `<video>` cannot: the
 *  browser owns its controls. There, once a moving finger looks like a
 *  scroll to the browser, the pointer stream is cancelled mid-gesture — the
 *  touch stream is not, and `touchend` still says where the finger lifted.
 *
 *  Left alone:
 *    * a zoomed picture — a drag there is a pan (`enabled`);
 *    * two fingers — a pinch;
 *    * a touch that starts on a clip's control bar — a scrub;
 *    * a touch that starts on the view toolbar — its buttons.
 *
 *  A swipe that ends over the stage would otherwise go on to be a click on
 *  it, and a click on the stage closes the dialog, so the click that follows
 *  one is swallowed. */
export function useSwipe({
  enabled,
  onPrev,
  onNext,
}: {
  enabled: boolean
  onPrev: () => void
  onNext: () => void
}) {
  const start = useRef<{ x: number; y: number; at: number } | null>(null)
  const swallowUntil = useRef(0)

  const onTouchStart = useCallback(
    (event: TouchEvent<HTMLElement>) => {
      start.current = null
      if (!enabled || event.touches.length !== 1) return
      const touch = event.touches[0]
      const target = event.target as Element
      if (target.closest('[role=toolbar]')) return
      if (target instanceof HTMLVideoElement) {
        const box = target.getBoundingClientRect()
        if (touch.clientY > box.bottom - CONTROLS) return
      }
      start.current = { x: touch.clientX, y: touch.clientY, at: event.timeStamp }
    },
    [enabled],
  )

  const onTouchEnd = useCallback(
    (event: TouchEvent<HTMLElement>) => {
      const from = start.current
      start.current = null
      // A second finger landing makes it a pinch, whatever the first did.
      if (!from || event.touches.length > 0) return
      const touch = event.changedTouches[0]
      const dx = touch.clientX - from.x
      const dy = touch.clientY - from.y
      if (Math.abs(dx) < DISTANCE || Math.abs(dx) < Math.abs(dy) * SLOPE) return
      if (event.timeStamp - from.at > DURATION) return
      swallowUntil.current = event.timeStamp + 500
      if (dx < 0) onNext()
      else onPrev()
    },
    [onPrev, onNext],
  )

  const onTouchCancel = useCallback(() => {
    start.current = null
  }, [])

  const onClickCapture = useCallback((event: MouseEvent<HTMLElement>) => {
    if (event.timeStamp < swallowUntil.current) {
      event.stopPropagation()
      event.preventDefault()
    }
  }, [])

  return { onTouchStart, onTouchEnd, onTouchCancel, onClickCapture }
}
