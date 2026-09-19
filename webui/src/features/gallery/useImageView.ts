import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'

/*
 * Zoom, rotate and flip for the lightbox's picture — a way of *looking*, not
 * an edit. Nothing here touches the file, and Download still hands over the
 * original.
 *
 * It is all one CSS transform on the stage frame, so the thumbnail layer and
 * the original under it move as one and neither needs to know. The frame
 * keeps its box; only what is painted in it changes, which is why turning
 * the toolbar on or zooming in never reflows the dialog around it.
 */

interface View {
  /** Relative to "fitted to the stage", so 1 is always Fit whatever the
   *  picture's size or turn. */
  zoom: number
  /** Quarter turns clockwise. Unbounded; only its parity and sign matter. */
  turns: number
  fx: 1 | -1
  fy: 1 | -1
  /** Pan, in screen pixels from the centre. */
  x: number
  y: number
}

const HOME: View = { zoom: 1, turns: 0, fx: 1, fy: 1, x: 0, y: 0 }
export const MAX_ZOOM = 8
const STEP = 1.5

type Point = [number, number]

/**
 * `key` names the picture: when it changes the view is back to Fit, in the
 * same render, so arrowing to the next image never paints it one frame late
 * in the last one's zoom. Held beside the view rather than reset by an
 * effect for the reason `loaded` is in Lightbox.
 */
export function useImageView(key: string, width: number, height: number) {
  const [frame, setFrame] = useState<HTMLDivElement | null>(null)
  const [box, setBox] = useState<Point>([0, 0])
  const [state, setState] = useState({ key, view: HOME })
  const [moving, setMoving] = useState(false)
  const view = state.key === key ? state.view : HOME

  useEffect(() => {
    if (!frame) return
    const measure = () => setBox([frame.clientWidth, frame.clientHeight])
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(frame)
    return () => observer.disconnect()
  }, [frame])

  /* How big the picture is on screen at Fit, upright and on its side.
   *
   * `.lightboxImage` fits by max-width/max-height at 100% and never scales a
   * picture *up*, so upright Fit is `min(1, W/w, H/h)`. A quarter turn swaps
   * which side meets which edge; `k` is the extra scale that makes a turned
   * picture fit the stage too, instead of a landscape standing on end and
   * running off the top and bottom. */
  const [W, H] = box
  const known = width > 0 && height > 0 && W > 0 && H > 0
  const upright = known ? Math.min(1, W / width, H / height) : 1
  const sideways = known ? Math.min(1, W / height, H / width) : 1
  const odd = Math.abs(view.turns) % 2 === 1
  const fit = odd ? sideways : upright
  const k = fit / upright

  /** Keep the picture over the stage: pan is free only as far as the
   *  zoomed picture overhangs it. */
  const clamp = useCallback(
    (next: View): View => {
      if (!known) return next
      const turned = Math.abs(next.turns) % 2 === 1
      const scale = (turned ? sideways : upright) * next.zoom
      const w = (turned ? height : width) * scale
      const h = (turned ? width : height) * scale
      const mx = Math.max(0, (w - W) / 2)
      const my = Math.max(0, (h - H) / 2)
      return {
        ...next,
        x: Math.min(mx, Math.max(-mx, next.x)),
        y: Math.min(my, Math.max(-my, next.y)),
      }
    },
    [known, sideways, upright, width, height, W, H],
  )

  const update = useCallback(
    (change: (current: View) => View) =>
      setState((previous) => ({
        key,
        view: clamp(change(previous.key === key ? previous.view : HOME)),
      })),
    [key, clamp],
  )

  /** Zoom keeping the point under `at` (stage-centre pixels) where it is. */
  const zoomTo = useCallback(
    (target: number | ((zoom: number) => number), at: Point = [0, 0]) =>
      update((current) => {
        const raw = typeof target === 'function' ? target(current.zoom) : target
        const zoom = Math.min(MAX_ZOOM, Math.max(1, raw))
        if (zoom === 1) return { ...current, zoom, x: 0, y: 0 }
        const ratio = zoom / current.zoom
        return {
          ...current,
          zoom,
          x: at[0] - (at[0] - current.x) * ratio,
          y: at[1] - (at[1] - current.y) * ratio,
        }
      }),
    [update],
  )

  const zoomIn = useCallback(() => zoomTo((zoom) => zoom * STEP), [zoomTo])
  const zoomOut = useCallback(() => zoomTo((zoom) => zoom / STEP), [zoomTo])
  const fitView = useCallback(() => zoomTo(1), [zoomTo])
  const reset = useCallback(() => update(() => HOME), [update])
  const rotate = useCallback(
    (by: 1 | -1) => update((current) => ({ ...current, turns: current.turns + by })),
    [update],
  )
  /* Flips are about the picture as it is *seen*. After a quarter turn the
   * picture's own x axis is the screen's y, so "mirror left to right" is a
   * flip of the other axis — otherwise the button labelled ⇆ would turn the
   * picture upside down. */
  const flipH = useCallback(
    () =>
      update((current) =>
        Math.abs(current.turns) % 2 === 1
          ? { ...current, fy: current.fy === 1 ? -1 : 1 }
          : { ...current, fx: current.fx === 1 ? -1 : 1 },
      ),
    [update],
  )
  const flipV = useCallback(
    () =>
      update((current) =>
        Math.abs(current.turns) % 2 === 1
          ? { ...current, fx: current.fx === 1 ? -1 : 1 }
          : { ...current, fy: current.fy === 1 ? -1 : 1 },
      ),
    [update],
  )

  /** Where a pointer is, from the centre of the stage. The stage and not the
   *  frame, because the frame's own rectangle is the transformed one. */
  const local = useCallback(
    (clientX: number, clientY: number): Point => {
      const stage = frame?.parentElement
      if (!stage) return [0, 0]
      const rect = stage.getBoundingClientRect()
      return [clientX - rect.left - rect.width / 2, clientY - rect.top - rect.height / 2]
    },
    [frame],
  )

  /* The wheel, attached by hand: React's `onWheel` is passive, and a wheel
   * that zooms the picture must not also scroll whatever is behind it. */
  const settle = useRef<number | undefined>(undefined)
  useEffect(() => {
    if (!frame) return
    function onWheel(event: WheelEvent) {
      event.preventDefault()
      setMoving(true)
      window.clearTimeout(settle.current)
      settle.current = window.setTimeout(() => setMoving(false), 150)
      zoomTo((zoom) => zoom * Math.exp(-event.deltaY * 0.0015), local(event.clientX, event.clientY))
    }
    frame.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      frame.removeEventListener('wheel', onWheel)
      window.clearTimeout(settle.current)
    }
  }, [frame, zoomTo, local])

  /* Drag to pan, two fingers to pinch. Pointer events cover mouse, pen and
   * touch alike. A pointer is only captured once it is doing something —
   * a plain click on an unzoomed picture stays a plain click. */
  const pointers = useRef(new Map<number, Point>())
  const pinch = useRef<{ distance: number; zoom: number } | null>(null)
  const zoomRef = useRef(view.zoom)
  zoomRef.current = view.zoom

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return
      pointers.current.set(event.pointerId, local(event.clientX, event.clientY))
      if (pointers.current.size === 2) {
        const [a, b] = [...pointers.current.values()]
        pinch.current = { distance: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, zoom: zoomRef.current }
      }
      if (zoomRef.current > 1 || pointers.current.size === 2) {
        event.currentTarget.setPointerCapture(event.pointerId)
      }
    },
    [local],
  )

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const before = pointers.current.get(event.pointerId)
      if (!before) return
      const now = local(event.clientX, event.clientY)
      pointers.current.set(event.pointerId, now)
      if (pointers.current.size === 2 && pinch.current) {
        const [a, b] = [...pointers.current.values()]
        const distance = Math.hypot(a[0] - b[0], a[1] - b[1])
        setMoving(true)
        zoomTo((pinch.current.zoom * distance) / pinch.current.distance, [
          (a[0] + b[0]) / 2,
          (a[1] + b[1]) / 2,
        ])
      } else if (zoomRef.current > 1) {
        setMoving(true)
        update((current) => ({
          ...current,
          x: current.x + now[0] - before[0],
          y: current.y + now[1] - before[1],
        }))
      }
    },
    [local, zoomTo, update],
  )

  const onPointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId)
    if (pointers.current.size < 2) pinch.current = null
    if (pointers.current.size === 0) setMoving(false)
  }, [])

  const onDoubleClick = useCallback(
    (event: { clientX: number; clientY: number }) =>
      zoomTo(zoomRef.current > 1 ? 1 : 2, local(event.clientX, event.clientY)),
    [zoomTo, local],
  )

  /** The shortcuts, for the dialog's own key handler. True when the key
   *  was one of these. Ctrl/Cmd/Alt are left alone: Ctrl+0 and Ctrl+R are
   *  the browser's. */
  const onKey = useCallback(
    (event: KeyboardEvent): boolean => {
      if (event.ctrlKey || event.metaKey || event.altKey) return false
      switch (event.key) {
        case '+':
        case '=':
          zoomIn()
          return true
        case '-':
        case '_':
          zoomOut()
          return true
        case '0':
          reset()
          return true
        case 'r':
        case 'R':
          rotate(event.shiftKey ? -1 : 1)
          return true
        case 'h':
        case 'H':
          flipH()
          return true
        case 'v':
        case 'V':
          flipV()
          return true
        default:
          return false
      }
    },
    [zoomIn, zoomOut, reset, rotate, flipH, flipV],
  )

  /* Clamped again here and not only when the view changes: the stage can
   * shrink under a zoomed picture (a window resized, a phone turned), and
   * the pan it was given may now reach past the edge. */
  const shown = clamp(view)
  const style: CSSProperties = {
    transform:
      `translate(${shown.x}px, ${shown.y}px) scale(${view.zoom * k}) ` +
      `rotate(${view.turns * 90}deg) scale(${view.fx}, ${view.fy})`,
  }

  return {
    ref: setFrame,
    style,
    moving,
    zoomed: view.zoom > 1,
    atMin: view.zoom <= 1,
    atMax: view.zoom >= MAX_ZOOM,
    changed: view.zoom !== 1 || view.turns % 4 !== 0 || view.fx < 0 || view.fy < 0,
    flippedH: odd ? view.fy < 0 : view.fx < 0,
    flippedV: odd ? view.fx < 0 : view.fy < 0,
    label: view.zoom === 1 ? 'Fit' : `${Math.round(fit * view.zoom * 100)}%`,
    zoomIn,
    zoomOut,
    fitView,
    reset,
    rotate,
    flipH,
    flipV,
    onKey,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onDoubleClick,
    },
  }
}
