import type { MediaItem } from '@/api/types'

/*
 * Stand-in pictures.
 *
 * Generated as SVG data URIs rather than shipped as files: the mock directory
 * gets deleted in Section 2, and a folder of binary placeholder art would be
 * the one part of it that leaves a trace in the repository. They are also
 * deliberately *not* pretty — a placeholder that looks like a finished
 * photograph makes it easy to ship a layout that only works for one aspect
 * ratio, which is the defect the fixed height=600 galleries already had.
 */

function svg(width: number, height: number, hue: number, caption: string): string {
  const body = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="hsl(${hue} 42% 26%)"/>
      <stop offset="55%" stop-color="hsl(${(hue + 34) % 360} 38% 17%)"/>
      <stop offset="100%" stop-color="hsl(${(hue + 68) % 360} 32% 12%)"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#g)"/>
  <circle cx="${width * 0.72}" cy="${height * 0.3}" r="${Math.min(width, height) * 0.22}"
          fill="hsl(${(hue + 18) % 360} 60% 60%)" opacity="0.22"/>
  <text x="50%" y="50%" fill="rgba(255,255,255,0.62)" font-family="system-ui, sans-serif"
        font-size="${Math.round(Math.min(width, height) * 0.075)}" text-anchor="middle">${caption}</text>
  <text x="50%" y="${height / 2 + Math.min(width, height) * 0.1}" fill="rgba(255,255,255,0.34)"
        font-family="system-ui, sans-serif" font-size="${Math.round(Math.min(width, height) * 0.05)}"
        text-anchor="middle">${width} × ${height}</text>
</svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(body)}`
}

const SIZES: [number, number][] = [
  [1024, 1536],
  [1024, 1024],
  [1216, 832],
  [832, 1216],
  [1536, 1024],
  [1344, 768],
]

let counter = 0

/** "1024×1536 (Portrait XL)" → [1024, 1536]. The tab's own resolution choice,
 *  so the tiles a run produces have the aspect ratio that run asked for —
 *  a grid that only ever sees one ratio is a grid whose reflow is untested. */
function parseSize(label: unknown): [number, number] | null {
  if (typeof label !== 'string') return null
  const match = label.match(/(\d{3,5})\s*[x×]\s*(\d{3,5})/)
  return match ? [Number(match[1]), Number(match[2])] : null
}

export function makeMedia(options: {
  tab: string
  prompt?: string
  seed?: number
  kind?: 'image' | 'video'
  index?: number
  createdAt?: string
  resolution?: unknown
}): MediaItem {
  const index = options.index ?? counter
  counter += 1
  const [width, height] = parseSize(options.resolution) ?? SIZES[index % SIZES.length]
  const hue = (index * 47 + 200) % 360
  const id = `m${index}_${Math.random().toString(36).slice(2, 8)}`
  const stamp = options.createdAt ?? new Date().toISOString()
  const name = `${options.tab}_${String(index).padStart(5, '0')}.png`
  return {
    id,
    url: svg(width, height, hue, options.prompt ? clip(options.prompt) : options.tab),
    path: `C:\\workspace\\krea2\\output\\${name}`,
    width,
    height,
    kind: options.kind ?? 'image',
    createdAt: stamp,
    seed: options.seed,
    prompt: options.prompt,
    tab: options.tab,
  }
}

function clip(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  const cut = flat.length > 34 ? `${flat.slice(0, 33)}…` : flat
  return cut.replace(/[<>&]/g, '')
}
