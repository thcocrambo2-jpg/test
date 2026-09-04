/*
 * A port of `_prepare_inpaint_inputs` (ui.py:926).
 *
 * WHAT THE SERVER RECEIVES
 * ------------------------
 * The editor's output contract is `{ background, layers }` — byte for byte the
 * shape `gr.ImageEditor` handed over — and `_prepare_inpaint_inputs` on the
 * Python side stays exactly as it is. That is deliberate: the transform below
 * snaps images for Krea 2's VAE and shapes the mask that decides which pixels
 * survive, and a second implementation of it in a second language, running on
 * every submit, is a second chance to change what comes out of the model.
 *
 * WHAT THIS FILE IS FOR
 * ---------------------
 * Two things the Python side cannot do from where it sits:
 *
 *   1. The live preview. Grow and Blur are two sliders whose effect the
 *      current UI never shows — you set them, generate, and find out. Here
 *      the painted region can be drawn with the real dilation and the real
 *      falloff before anything is submitted.
 *   2. The output size. The snap-to-16 arithmetic decides the actual pixel
 *      dimensions of the result, and nothing in the Gradio app ever told
 *      anyone what they were going to be.
 *
 * FIDELITY
 * --------
 * `unionAlpha`, `dilate` and `targetSize` are exact: the same max-of-alpha,
 * the same square MaxFilter kernel of 2r+1, the same integer floor-to-16 with
 * a floor of 64.
 *
 * `gaussianBlur` implements the same algorithm Pillow's `GaussianBlur` uses —
 * three box-blur passes with the box radius derived from sigma by the
 * Gwosdek et al. formula — in floating point rather than Pillow's 24-bit
 * fixed point. Expect agreement to within a level or so, not bit equality.
 * The resample on the final downscale is the browser's, not LANCZOS.
 *
 * None of that reaches a generated picture, because none of it is what gets
 * uploaded. If a later section ever moves the transform client-side, this
 * comment is the list of things that must be made exact first.
 */

export interface Prepared {
  /** Grey mask, one byte per pixel, at the snapped output size. Null when
   *  nothing is painted — which the Python side treats as full-image img2img
   *  rather than as an error. */
  alpha: Uint8ClampedArray | null
  width: number
  height: number
}

/** Max of the layers' alpha channels.
 *
 *  `ImageChops.lighter` over every layer that has an alpha band, in order.
 *  Layers are already at background resolution here, so the resize branch in
 *  the Python (`alpha.size != background.size`) has nothing to do. */
export function unionAlpha(
  layers: Uint8ClampedArray[],
  pixelCount: number,
): Uint8ClampedArray | null {
  if (layers.length === 0) return null
  const out = new Uint8ClampedArray(pixelCount)
  for (const layer of layers) {
    for (let i = 0; i < pixelCount; i += 1) {
      const value = layer[i * 4 + 3]
      if (value > out[i]) out[i] = value
    }
  }
  // `mask.getbbox() is None` — nothing painted at all, so there is no mask.
  for (let i = 0; i < pixelCount; i += 1) {
    if (out[i] !== 0) return out
  }
  return null
}

/** `ImageFilter.MaxFilter(grow * 2 + 1)` — a square dilation.
 *
 *  Separable: a square max kernel is a horizontal max followed by a vertical
 *  one. Each pass uses a monotonic deque so the cost is O(pixels) rather than
 *  O(pixels × kernel), which is what makes a live preview affordable at
 *  2048px with grow at its maximum of 32. */
export function dilate(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  grow: number,
): Uint8ClampedArray {
  if (grow <= 0) return source
  const radius = grow
  const horizontal = maxPass(source, width, height, radius, false)
  return maxPass(horizontal, width, height, radius, true)
}

function maxPass(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  radius: number,
  vertical: boolean,
): Uint8ClampedArray {
  const out = new Uint8ClampedArray(source.length)
  const outer = vertical ? width : height
  const inner = vertical ? height : width
  const stride = vertical ? width : 1
  const step = vertical ? 1 : width

  // Indices into the current line, kept in decreasing order of value.
  const deque = new Int32Array(inner)

  for (let o = 0; o < outer; o += 1) {
    const base = o * step
    let head = 0
    let tail = 0

    // Prime with the first `radius` samples; PIL clamps at the edge, and a
    // window that runs off the end simply has fewer members.
    for (let i = 0; i < Math.min(radius, inner); i += 1) {
      const value = source[base + i * stride]
      while (tail > head && source[base + deque[tail - 1] * stride] <= value) tail -= 1
      deque[tail] = i
      tail += 1
    }

    for (let i = 0; i < inner; i += 1) {
      const add = i + radius
      if (add < inner) {
        const value = source[base + add * stride]
        while (tail > head && source[base + deque[tail - 1] * stride] <= value) tail -= 1
        deque[tail] = add
        tail += 1
      }
      const drop = i - radius - 1
      while (tail > head && deque[head] <= drop) head += 1
      out[base + i * stride] = source[base + deque[head] * stride]
    }
  }
  return out
}

/** `ImageFilter.GaussianBlur(radius)`.
 *
 *  Pillow treats `radius` as sigma and approximates the Gaussian with three
 *  box blurs, taking the box radius from sigma per Gwosdek et al. (2011),
 *  §[7] and §[11]/[14] — the same derivation reproduced here so the falloff
 *  has the same shape and extent, not merely a similar softness. */
export function gaussianBlur(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  sigma: number,
): Uint8ClampedArray {
  if (sigma <= 0) return source
  const passes = 3
  const sigma2 = (sigma * sigma) / passes
  const boxLength = Math.sqrt(12 * sigma2 + 1)
  const l = Math.floor((boxLength - 1) / 2)
  const denominator = 6 * (sigma2 - (l + 1) * (l + 1))
  const a =
    denominator === 0 ? 0 : ((2 * l + 1) * (l * (l + 1) - 3 * sigma2)) / denominator
  const radius = l + a

  let buffer: Float32Array = new Float32Array(source)
  for (let pass = 0; pass < passes; pass += 1) {
    buffer = boxPass(buffer, width, height, radius, false)
    buffer = boxPass(buffer, width, height, radius, true)
  }

  const out = new Uint8ClampedArray(source.length)
  for (let i = 0; i < out.length; i += 1) out[i] = Math.round(buffer[i])
  return out
}

/** One box-blur pass with a fractional radius: whole pixels at full weight,
 *  the two pixels straddling the edge at the fractional weight. Edges clamp,
 *  as Pillow's do. */
function boxPass(
  source: Float32Array,
  width: number,
  height: number,
  radius: number,
  vertical: boolean,
): Float32Array {
  const out = new Float32Array(source.length)
  const whole = Math.floor(radius)
  const fraction = radius - whole
  const weight = 1 / (radius * 2 + 1)

  const outer = vertical ? width : height
  const inner = vertical ? height : width
  const stride = vertical ? width : 1
  const step = vertical ? 1 : width
  const last = inner - 1

  const at = (base: number, index: number) =>
    source[base + Math.min(last, Math.max(0, index)) * stride]

  for (let o = 0; o < outer; o += 1) {
    const base = o * step

    // Running sum of the whole-pixel part of the window, seeded at i = 0.
    let acc = 0
    for (let k = -whole; k <= whole; k += 1) acc += at(base, k)

    for (let i = 0; i < inner; i += 1) {
      const edge = at(base, i - whole - 1) + at(base, i + whole + 1)
      out[base + i * stride] = (acc + edge * fraction) * weight
      acc += at(base, i + whole + 1) - at(base, i - whole)
    }
  }
  return out
}

export const MAX_LONG_SIDE = 2048
export const VAE_MULTIPLE = 16
export const MIN_SIDE = 64

/** The size the VAE will actually be handed.
 *
 *  Long side capped at 2048 and never upscaled, then both sides floored to a
 *  multiple of 16 with a floor of 64 — Krea 2's VAE requires the multiple.
 *  Same integer arithmetic as the Python, including the truncation order:
 *  `int(w * scale) // 16 * 16`. */
export function targetSize(width: number, height: number): { width: number; height: number } {
  const scale = Math.min(1, MAX_LONG_SIDE / Math.max(width, height))
  return {
    width: Math.max(MIN_SIDE, Math.floor(Math.trunc(width * scale) / VAE_MULTIPLE) * VAE_MULTIPLE),
    height: Math.max(MIN_SIDE, Math.floor(Math.trunc(height * scale) / VAE_MULTIPLE) * VAE_MULTIPLE),
  }
}

/** The whole pipeline, at the background's own resolution.
 *
 *  Returns the mask *before* the final resize, plus the size that resize will
 *  produce — the preview wants the former and the size readout wants the
 *  latter, and resampling a preview the user is about to see scaled anyway
 *  would only lose detail. */
export function prepareMask(
  layers: Uint8ClampedArray[],
  width: number,
  height: number,
  grow: number,
  blur: number,
): Prepared {
  const size = targetSize(width, height)
  let alpha = unionAlpha(layers, width * height)
  if (alpha) {
    if (grow > 0) alpha = dilate(alpha, width, height, grow)
    if (blur > 0) alpha = gaussianBlur(alpha, width, height, blur)
  }
  return { alpha, width: size.width, height: size.height }
}
