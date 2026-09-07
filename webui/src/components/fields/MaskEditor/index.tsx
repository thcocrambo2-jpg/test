import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Pill } from '@/components/ui'
import { cx } from '@/lib/util'
import { RecentStrip } from '../RecentStrip'
import { prepareMask, targetSize } from './prepare'
import s from './MaskEditor.module.css'

export { prepareMask, targetSize } from './prepare'

/*
 * The inpaint canvas.
 *
 * `gr.ImageEditor` hands the Python side `{ background, layers }`, and
 * `_prepare_inpaint_inputs` takes the union of the painted layers' alpha
 * channels. There is no React drop-in for that, so this is the brush tool:
 * layers with real alpha, undo/redo, sizing, and a preview of the dilated and
 * blurred mask that the sliders actually produce.
 *
 * The value it emits is that same pair — the background file and the layer
 * canvases — because the Python transform stays exactly as it is. `prepare.ts`
 * says more about which parts of the pipeline this file reproduces and why.
 */

export interface InpaintEditorValue {
  background: File | null
  /** Full-resolution RGBA layers, oldest first. Alpha is the paint. */
  layers: HTMLCanvasElement[]
  /** Bumped on every change, so a mutation-in-place still reads as new. */
  revision: number
}

export const EMPTY_EDITOR: InpaintEditorValue = {
  background: null,
  layers: [],
  revision: 0,
}

type Tool = 'brush' | 'eraser'

const UNDO_LIMIT = 24

/** Fully opaque, so a stroke reads as alpha 255 in the layer. */
const PAINT = 'rgba(255,255,255,1)'

export function MaskEditor({
  value,
  onChange,
  grow,
  blur,
}: {
  value: InpaintEditorValue
  onChange: (next: InpaintEditorValue) => void
  grow: number
  blur: number
}) {
  const [tool, setTool] = useState<Tool>('brush')
  const [brush, setBrush] = useState(48)
  const [showMask, setShowMask] = useState(true)
  const [dragging, setDragging] = useState(false)
  const [activeLayer, setActiveLayer] = useState(0)
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null)
  const [, forceRender] = useState(0)

  const bitmapRef = useRef<HTMLImageElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const undoRef = useRef<{ layer: number; data: ImageData }[]>([])
  const redoRef = useRef<{ layer: number; data: ImageData }[]>([])
  const strokeRef = useRef<{ x: number; y: number } | null>(null)
  const maskOverlayRef = useRef<HTMLCanvasElement | null>(null)

  const size = useMemo(() => {
    const image = bitmapRef.current
    if (!image) return null
    return { width: image.naturalWidth, height: image.naturalHeight }
  }, [value.revision, value.background])

  // ----------------------------------------------------------- load image

  useEffect(() => {
    const file = value.background
    if (!file) {
      bitmapRef.current = null
      forceRender((n) => n + 1)
      return
    }
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => {
      bitmapRef.current = image
      // One empty layer, at the background's own resolution — the Python
      // resizes a mismatched layer, but matching here means it never has to.
      const layer = document.createElement('canvas')
      layer.width = image.naturalWidth
      layer.height = image.naturalHeight
      undoRef.current = []
      redoRef.current = []
      setActiveLayer(0)
      onChange({ background: file, layers: [layer], revision: value.revision + 1 })
    }
    image.src = url
    return () => URL.revokeObjectURL(url)
    // Only when the file itself changes; re-running on every stroke would
    // wipe the layers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.background])

  // ------------------------------------------------------------ mask prep

  const [maskInfo, setMaskInfo] = useState<{ painted: boolean; out: string } | null>(null)

  useEffect(() => {
    const image = bitmapRef.current
    if (!image) {
      setMaskInfo(null)
      maskOverlayRef.current = null
      return
    }
    const width = image.naturalWidth
    const height = image.naturalHeight
    const out = targetSize(width, height)

    // Debounced: dilate + three box passes over a 2048px image is tens of
    // milliseconds, which is fine once a stroke ends and awful per pointermove.
    const timer = setTimeout(() => {
      const alphas = value.layers.map((layer) => {
        const context = layer.getContext('2d', { willReadFrequently: true })
        return context!.getImageData(0, 0, width, height).data
      })
      const prepared = prepareMask(alphas, width, height, Math.round(grow), Math.round(blur))
      if (!prepared.alpha) {
        maskOverlayRef.current = null
        setMaskInfo({ painted: false, out: `${out.width} × ${out.height}` })
        draw()
        return
      }
      const overlay = document.createElement('canvas')
      overlay.width = width
      overlay.height = height
      const context = overlay.getContext('2d')!
      const rgba = context.createImageData(width, height)
      for (let i = 0; i < prepared.alpha.length; i += 1) {
        // The accent, so the overlay reads as "this is the app's mask" and
        // not as part of the picture.
        rgba.data[i * 4] = 255
        rgba.data[i * 4 + 1] = 161
        rgba.data[i * 4 + 2] = 22
        rgba.data[i * 4 + 3] = Math.round(prepared.alpha[i] * 0.62)
      }
      context.putImageData(rgba, 0, 0)
      maskOverlayRef.current = overlay
      setMaskInfo({ painted: true, out: `${out.width} × ${out.height}` })
      draw()
    }, 180)

    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.revision, grow, blur])

  // -------------------------------------------------------------- drawing

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const image = bitmapRef.current
    if (!canvas || !image) return
    const context = canvas.getContext('2d')
    if (!context) return

    context.clearRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)

    if (showMask && maskOverlayRef.current) {
      context.drawImage(maskOverlayRef.current, 0, 0, canvas.width, canvas.height)
    } else {
      // Raw paint, so the brush still gives feedback with the preview off.
      context.globalAlpha = 0.55
      for (const layer of value.layers) {
        context.drawImage(layer, 0, 0, canvas.width, canvas.height)
      }
      context.globalAlpha = 1
    }
  }, [showMask, value.layers])

  // Size the backing store to the displayed box × DPR, so the picture is
  // sharp on a HiDPI screen without ever resampling the source layers.
  useEffect(() => {
    const canvas = canvasRef.current
    const image = bitmapRef.current
    const stage = stageRef.current
    if (!canvas || !image || !stage) return

    function fit() {
      const box = stage!.parentElement!.getBoundingClientRect()
      const scale = Math.min(
        (box.width - 2) / image!.naturalWidth,
        (box.height - 2) / image!.naturalHeight,
        1,
      )
      const cssWidth = Math.max(1, Math.floor(image!.naturalWidth * scale))
      const cssHeight = Math.max(1, Math.floor(image!.naturalHeight * scale))
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      canvas!.style.width = `${cssWidth}px`
      canvas!.style.height = `${cssHeight}px`
      canvas!.width = Math.round(cssWidth * dpr)
      canvas!.height = Math.round(cssHeight * dpr)
      draw()
    }

    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(stage.parentElement!)
    return () => observer.disconnect()
  }, [value.background, value.revision, draw])

  useEffect(() => {
    draw()
  }, [draw, showMask])

  // ------------------------------------------------------------ painting

  function toImageSpace(event: React.PointerEvent): { x: number; y: number } | null {
    const canvas = canvasRef.current
    const image = bitmapRef.current
    if (!canvas || !image) return null
    const box = canvas.getBoundingClientRect()
    return {
      x: ((event.clientX - box.left) / box.width) * image.naturalWidth,
      y: ((event.clientY - box.top) / box.height) * image.naturalHeight,
    }
  }

  function pushUndo() {
    const layer = value.layers[activeLayer]
    if (!layer) return
    const context = layer.getContext('2d', { willReadFrequently: true })!
    undoRef.current.push({
      layer: activeLayer,
      data: context.getImageData(0, 0, layer.width, layer.height),
    })
    if (undoRef.current.length > UNDO_LIMIT) undoRef.current.shift()
    redoRef.current = []
  }

  function paintTo(point: { x: number; y: number }) {
    const layer = value.layers[activeLayer]
    if (!layer) return
    const context = layer.getContext('2d')!
    context.save()
    context.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over'
    // Not a UI colour: this is the layer's alpha channel, and alpha is what
    // `_prepare_inpaint_inputs` reads. The RGB is never looked at by anything
    // — only `getchannel("A")` is — so opaque white is simply "painted".
    context.strokeStyle = PAINT
    context.fillStyle = PAINT
    context.lineCap = 'round'
    context.lineJoin = 'round'
    context.lineWidth = brush
    const from = strokeRef.current
    if (from) {
      context.beginPath()
      context.moveTo(from.x, from.y)
      context.lineTo(point.x, point.y)
      context.stroke()
    } else {
      context.beginPath()
      context.arc(point.x, point.y, brush / 2, 0, Math.PI * 2)
      context.fill()
    }
    context.restore()
    strokeRef.current = point
    draw()
  }

  function onPointerDown(event: React.PointerEvent) {
    if (!bitmapRef.current || event.button !== 0) return
    const point = toImageSpace(event)
    if (!point) return
    // Capture so a stroke that leaves the canvas keeps painting. Guarded
    // because a pointer id the element never saw throws NotFoundError, and
    // losing the whole stroke over the capture is the wrong trade.
    try {
      ;(event.target as Element).setPointerCapture(event.pointerId)
    } catch {
      /* keep painting without capture */
    }
    pushUndo()
    strokeRef.current = null
    paintTo(point)
  }

  function onPointerMove(event: React.PointerEvent) {
    const canvas = canvasRef.current
    if (canvas) {
      const box = canvas.getBoundingClientRect()
      setCursor({ x: event.clientX - box.left, y: event.clientY - box.top })
    }
    if (!strokeRef.current && event.buttons !== 1) return
    if (event.buttons !== 1) return
    const point = toImageSpace(event)
    if (point) paintTo(point)
  }

  function endStroke() {
    if (strokeRef.current === null) return
    strokeRef.current = null
    onChange({ ...value, revision: value.revision + 1 })
  }

  // --------------------------------------------------------- undo / redo

  const undo = useCallback(() => {
    const entry = undoRef.current.pop()
    if (!entry) return
    const layer = value.layers[entry.layer]
    if (!layer) return
    const context = layer.getContext('2d', { willReadFrequently: true })!
    redoRef.current.push({
      layer: entry.layer,
      data: context.getImageData(0, 0, layer.width, layer.height),
    })
    context.putImageData(entry.data, 0, 0)
    onChange({ ...value, revision: value.revision + 1 })
  }, [onChange, value])

  const redo = useCallback(() => {
    const entry = redoRef.current.pop()
    if (!entry) return
    const layer = value.layers[entry.layer]
    if (!layer) return
    const context = layer.getContext('2d', { willReadFrequently: true })!
    undoRef.current.push({
      layer: entry.layer,
      data: context.getImageData(0, 0, layer.width, layer.height),
    })
    context.putImageData(entry.data, 0, 0)
    onChange({ ...value, revision: value.revision + 1 })
  }, [onChange, value])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey)) return
      const key = event.key.toLowerCase()
      if (key === 'z' && !event.shiftKey) {
        // Only when the canvas is the thing being worked on — Ctrl+Z inside
        // the prompt box must still undo typing.
        if (document.activeElement?.tagName.match(/INPUT|TEXTAREA/)) return
        event.preventDefault()
        undo()
      } else if (key === 'y' || (key === 'z' && event.shiftKey)) {
        if (document.activeElement?.tagName.match(/INPUT|TEXTAREA/)) return
        event.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [undo, redo])

  // ------------------------------------------------------------- loading

  /** A new source image. The layers go with it — paint is in the source's
   *  own pixel coordinates, so keeping it across a different picture would
   *  put the mask somewhere nobody drew it. */
  const loadFile = useCallback(
    (file: File) => {
      onChange({ background: file, layers: [], revision: value.revision + 1 })
    },
    [onChange, value.revision],
  )

  const load = useCallback(
    (files: FileList | null) => {
      const file = files?.[0]
      if (file && file.type.startsWith('image/')) loadFile(file)
    },
    [loadFile],
  )

  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      const stage = stageRef.current?.parentElement
      if (!stage || !stage.matches(':hover, :focus-within')) return
      const item = Array.from(event.clipboardData?.items ?? []).find((entry) =>
        entry.type.startsWith('image/'),
      )
      const file = item?.getAsFile()
      if (file) {
        event.preventDefault()
        onChange({ background: file, layers: [], revision: value.revision + 1 })
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [onChange, value.revision])

  // -------------------------------------------------------------- layers

  function addLayer() {
    const image = bitmapRef.current
    if (!image) return
    const layer = document.createElement('canvas')
    layer.width = image.naturalWidth
    layer.height = image.naturalHeight
    setActiveLayer(value.layers.length)
    onChange({ ...value, layers: [...value.layers, layer], revision: value.revision + 1 })
  }

  function removeLayer(index: number) {
    if (value.layers.length <= 1) return
    const layers = value.layers.filter((_, i) => i !== index)
    undoRef.current = []
    redoRef.current = []
    setActiveLayer(Math.max(0, Math.min(index, layers.length - 1)))
    onChange({ ...value, layers, revision: value.revision + 1 })
  }

  function clearLayer() {
    const layer = value.layers[activeLayer]
    if (!layer) return
    pushUndo()
    layer.getContext('2d')!.clearRect(0, 0, layer.width, layer.height)
    onChange({ ...value, revision: value.revision + 1 })
  }

  // -------------------------------------------------------------- render

  const hasImage = Boolean(value.background && bitmapRef.current)
  const cursorScale = (() => {
    const canvas = canvasRef.current
    const image = bitmapRef.current
    if (!canvas || !image) return 1
    return canvas.getBoundingClientRect().width / image.naturalWidth
  })()

  return (
    <div className={s.wrap}>
      <div className={s.toolbar}>
        <Button
          size="sm"
          variant={tool === 'brush' ? 'primary' : 'default'}
          onClick={() => setTool('brush')}
          disabled={!hasImage}
          title="Paint the region to replace"
        >
          Brush
        </Button>
        <Button
          size="sm"
          variant={tool === 'eraser' ? 'primary' : 'default'}
          onClick={() => setTool('eraser')}
          disabled={!hasImage}
          title="Rub paint back off"
        >
          Eraser
        </Button>

        <div className={s.brushSize}>
          <span className={s.brushLabel}>Size</span>
          <input
            type="range"
            className={s.brushSlider}
            min={4}
            max={400}
            step={1}
            value={brush}
            disabled={!hasImage}
            onChange={(event) => setBrush(Number(event.target.value))}
            aria-label="Brush size"
            style={{ ['--fill' as string]: `${((brush - 4) / 396) * 100}%` }}
          />
          <span className={s.brushValue}>{brush}</span>
        </div>

        <span className={s.divider} />

        <Button size="sm" onClick={undo} disabled={!hasImage} title="Ctrl+Z">
          Undo
        </Button>
        <Button size="sm" onClick={redo} disabled={!hasImage} title="Ctrl+Shift+Z">
          Redo
        </Button>
        <Button size="sm" onClick={clearLayer} disabled={!hasImage}>
          Clear
        </Button>

        <span className={s.divider} />

        <div className={s.layers}>
          {value.layers.map((_, index) => (
            <button
              key={index}
              type="button"
              className={cx(s.layerChip, index === activeLayer && s.layerActive)}
              onClick={() => setActiveLayer(index)}
              onDoubleClick={() => removeLayer(index)}
              title={
                value.layers.length > 1
                  ? `Layer ${index + 1} — double-click to delete`
                  : `Layer ${index + 1}`
              }
            >
              {index + 1}
            </button>
          ))}
          <button
            type="button"
            className={s.layerChip}
            onClick={addLayer}
            disabled={!hasImage}
            title="Add a layer — the mask is the union of them all"
          >
            +
          </button>
        </div>

        <span className={s.spacer} />

        <Button
          size="sm"
          variant={showMask ? 'primary' : 'default'}
          onClick={() => setShowMask((on) => !on)}
          disabled={!hasImage}
          title="Show the mask as Grow and Blur will actually shape it"
        >
          Mask preview
        </Button>
        {hasImage && (
          <Button size="sm" variant="ghost" onClick={() => fileInputRef.current?.click()}>
            Replace image
          </Button>
        )}
      </div>

      <div className={s.stageOuter}>
        {hasImage ? (
          <div
            className={s.stage}
            ref={stageRef}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endStroke}
            onPointerLeave={() => {
              endStroke()
              setCursor(null)
            }}
          >
            <canvas ref={canvasRef} className={s.canvas} />
            {cursor && (
              <span
                className={s.cursor}
                style={{
                  left: cursor.x,
                  top: cursor.y,
                  width: brush * cursorScale,
                  height: brush * cursorScale,
                }}
              />
            )}
          </div>
        ) : (
          <div
            ref={stageRef as never}
            className={cx(s.empty, dragging && s.emptyDrag)}
            role="button"
            tabIndex={0}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') fileInputRef.current?.click()
            }}
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              load(event.dataTransfer.files)
            }}
          >
            <div>
              <span className={s.emptyIcon} aria-hidden>
                🖌
              </span>
              Drop an image here, or click to choose one
              <div className={s.emptyHint}>Ctrl+V pastes from the clipboard</div>
            </div>
          </div>
        )}
      </div>

      <div className={s.status}>
        {size && (
          <span className={s.statusStrong}>
            source {size.width} × {size.height}
          </span>
        )}
        {maskInfo && <span>output {maskInfo.out}</span>}
        {hasImage &&
          (maskInfo?.painted ? (
            <Pill tone="success">masked · grow {Math.round(grow)} · blur {Math.round(blur)}</Pill>
          ) : (
            <Pill tone="warning">nothing painted — runs as full-image img2img</Pill>
          ))}
      </div>

      {/* The same reel every other image input carries. Inpaint is where it
          earns the most: painting over a picture this app made a minute ago
          is the ordinary case, and it used to mean a round trip through the
          gallery and the downloads folder. */}
      <RecentStrip value={value.background} onPick={loadFile} />

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(event) => load(event.target.files)}
      />
    </div>
  )
}

/** The editor's value as blobs, ready to POST.
 *
 *  Exactly `{ background, layers }` — the same pair `gr.ImageEditor` produced,
 *  as PNGs so the alpha survives. `_prepare_inpaint_inputs` does the rest,
 *  unchanged. */
export async function serializeEditor(
  value: InpaintEditorValue,
): Promise<{ background: Blob; layers: Blob[] } | null> {
  if (!value.background) return null
  const layers = await Promise.all(
    value.layers.map(
      (layer) =>
        new Promise<Blob | null>((resolve) => layer.toBlob(resolve, 'image/png')),
    ),
  )
  return {
    background: value.background,
    layers: layers.filter((blob): blob is Blob => blob !== null),
  }
}
