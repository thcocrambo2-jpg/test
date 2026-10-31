/**
 * A stored (uncompressed) zip, built in the browser.
 *
 * Why it exists: saving a Gallery selection file by file is one browser
 * download per file, and a browser set to ask where to save each download
 * then asks fifty times. One archive is one question.
 *
 * Stored and not deflated because everything the Gallery holds — PNG, WebP,
 * MP4 — is compressed already; deflating it again costs time and saves next
 * to nothing. That also keeps this to a CRC and some headers rather than a
 * dependency. No zip64, so the archive must stay under 4 GiB, which is
 * checked rather than assumed.
 */

export interface ZipEntry {
  name: string
  data: Blob
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff
  for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

/** MS-DOS time and date, the only clock a plain zip header has. */
function dosDateTime(when: Date): [time: number, date: number] {
  const time = (when.getHours() << 11) | (when.getMinutes() << 5) | (when.getSeconds() >> 1)
  const date = ((when.getFullYear() - 1980) << 9) | ((when.getMonth() + 1) << 5) | when.getDate()
  return [time, date]
}

/** Two entries with one name would unpack as one file; the later one gets
 *  ` (2)` before its extension, the way a browser names a repeat download. */
export function uniqueNames(names: string[]): string[] {
  const seen = new Set<string>()
  return names.map((name) => {
    let candidate = name
    const dot = name.lastIndexOf('.')
    const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, '']
    for (let n = 2; seen.has(candidate.toLowerCase()); n++) candidate = `${stem} (${n})${ext}`
    seen.add(candidate.toLowerCase())
    return candidate
  })
}

export async function buildZip(entries: ZipEntry[], when = new Date()): Promise<Blob> {
  const encoder = new TextEncoder()
  const [time, date] = dosDateTime(when)
  const names = uniqueNames(entries.map((entry) => entry.name))
  const parts: BlobPart[] = []
  const central: Uint8Array<ArrayBuffer>[] = []
  let offset = 0

  for (let i = 0; i < entries.length; i++) {
    const name = new Uint8Array(encoder.encode(names[i]))
    const data = entries[i].data
    const crc = crc32(new Uint8Array(await data.arrayBuffer()))
    const size = data.size
    if (offset + 30 + name.length + size > 0xffffffff) {
      throw new Error('The selection is over 4 GB; save fewer files at a time.')
    }

    const local = new DataView(new ArrayBuffer(30))
    local.setUint32(0, 0x04034b50, true)
    local.setUint16(4, 20, true) // version needed
    local.setUint16(6, 0x0800, true) // names are UTF-8
    local.setUint16(8, 0, true) // stored
    local.setUint16(10, time, true)
    local.setUint16(12, date, true)
    local.setUint32(14, crc, true)
    local.setUint32(18, size, true)
    local.setUint32(22, size, true)
    local.setUint16(26, name.length, true)
    local.setUint16(28, 0, true)
    parts.push(local.buffer, name, data)

    const entry = new DataView(new ArrayBuffer(46))
    entry.setUint32(0, 0x02014b50, true)
    entry.setUint16(4, 20, true) // version made by
    entry.setUint16(6, 20, true) // version needed
    entry.setUint16(8, 0x0800, true)
    entry.setUint16(10, 0, true)
    entry.setUint16(12, time, true)
    entry.setUint16(14, date, true)
    entry.setUint32(16, crc, true)
    entry.setUint32(20, size, true)
    entry.setUint32(24, size, true)
    entry.setUint16(28, name.length, true)
    // extra, comment, disk, internal and external attributes: all zero
    entry.setUint32(42, offset, true)
    const record = new Uint8Array(46 + name.length)
    record.set(new Uint8Array(entry.buffer), 0)
    record.set(name, 46)
    central.push(record)

    offset += 30 + name.length + size
  }

  const centralSize = central.reduce((sum, record) => sum + record.length, 0)
  const end = new DataView(new ArrayBuffer(22))
  end.setUint32(0, 0x06054b50, true)
  end.setUint16(8, entries.length, true)
  end.setUint16(10, entries.length, true)
  end.setUint32(12, centralSize, true)
  end.setUint32(16, offset, true)

  return new Blob([...parts, ...central, end.buffer], { type: 'application/zip' })
}
