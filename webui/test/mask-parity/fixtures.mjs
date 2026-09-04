// Runs the TS mask pipeline in Node and writes raw bytes for Pillow to compare.
import { dilate, gaussianBlur, targetSize, unionAlpha } from './prepare.mjs'
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const path = (name) => join(here, name)

const W = 256, H = 192

// Two overlapping painted layers with hard and soft edges, so the union, the
// dilation and the blur all have something to disagree about.
function layer(shape) {
  const data = new Uint8ClampedArray(W * H * 4)
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4
      let a = 0
      if (shape === 'rect') a = x > 40 && x < 120 && y > 30 && y < 110 ? 255 : 0
      if (shape === 'disc') {
        const d = Math.hypot(x - 160, y - 120)
        a = d < 45 ? Math.round(255 * Math.min(1, (45 - d) / 12)) : 0
      }
      data[i + 3] = a
    }
  }
  return data
}

const layers = [layer('rect'), layer('disc')]
const union = unionAlpha(layers, W * H)
writeFileSync(path('union.raw'), Buffer.from(union))

for (const grow of [0, 3, 8, 32]) {
  const out = dilate(union, W, H, grow)
  writeFileSync(path(`dilate_${grow}.raw`), Buffer.from(out))
}

for (const blur of [1, 4, 8, 32]) {
  const out = gaussianBlur(union, W, H, blur)
  writeFileSync(path(`blur_${blur}.raw`), Buffer.from(out))
}

const sizes = [[3000, 2000], [1024, 1536], [1000, 700], [50, 40], [4096, 4096], [1919, 1081]]
writeFileSync(path('sizes.json'), JSON.stringify(sizes.map(([w, h]) => targetSize(w, h))))
console.log('wrote fixtures')
