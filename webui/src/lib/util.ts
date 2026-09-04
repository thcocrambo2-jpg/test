import { useEffect, useRef, useState } from 'react'

/** Join class names, skipping the falsy ones. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  INR: '₹',
}

/** Money, the way theme.py:_SYMBOLS does it: a symbol where we know one, and
 *  the plain code otherwise ("42 CHF") — correct if less pretty, and better
 *  than guessing a symbol for a currency we do not know. */
export function money(amount: number, currency: string): string {
  const rounded = Number.isInteger(amount) ? amount : Math.round(amount)
  const grouped = rounded.toLocaleString('en-IN')
  const symbol = CURRENCY_SYMBOLS[currency.toUpperCase()]
  return symbol ? `${symbol}${grouped}` : `${grouped} ${currency}`
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  return new Date(then).toLocaleDateString()
}

/** Days until an expiry, or null when there is no expiry — which is ordinary:
 *  a licence key can name no plan and can have no end date. */
export function daysUntil(iso: string | null): number | null {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  return Math.ceil((then - Date.now()) / 864e5)
}

export function formatDate(iso: string | null): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** Copy to the clipboard, reporting whether it landed so the caller can show
 *  a "Copied" state instead of hoping. */
export function useCopy(resetMs = 1600) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), resetMs)
    } catch {
      setCopied(false)
    }
  }

  return { copied, copy }
}

/** The name a generated file should land on disk under.
 *
 *  A MediaItem's `id` is the OUTPUT_DIR-relative posix key (api._media), so
 *  the last segment is the name ComfyUI already gave it — which carries the
 *  tab and the counter and is therefore worth keeping. */
export function fileName(id: string): string {
  return id.split('/').pop() || id
}

/** Save one file to disk, and resolve once the browser has taken it.
 *
 *  Fetched to a blob rather than pointed at with `<a download href={url}>`,
 *  for two reasons that both end in the picture replacing the gallery:
 *  `/media` answers with no `Content-Disposition` (api.py `_send`, which
 *  serves the same bytes to the <img> on screen), and `download` is honoured
 *  only same-origin — which is true today and is a `VITE_API_BASE` away from
 *  not being. A blob URL is same-origin by construction.
 *
 *  Awaitable so a caller saving a selection can hand the browser one file at
 *  a time; forty anchors clicked in one tick is forty files it drops most
 *  of. */
export async function saveFile(url: string, name: string): Promise<void> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  const href = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = name
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  // Not revoked synchronously: the click is queued, not done, and a URL
  // revoked before the browser reads it saves a zero-byte file.
  setTimeout(() => URL.revokeObjectURL(href), 60_000)
}

/** Ctrl/Cmd+Enter runs the tab you are on — the shortcut the footer advertises
 *  and the one thing from the old header worth keeping literally. */
export function useSubmitHotkey(onSubmit: () => void, enabled = true) {
  const handler = useRef(onSubmit)
  handler.current = onSubmit

  useEffect(() => {
    if (!enabled) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault()
        handler.current()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enabled])
}

/** True once the viewport is at least `query` wide. Used to decide layout
 *  facts JS has to know (the queue drawer), never to decide styling — that
 *  stays in CSS. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  )
  useEffect(() => {
    const list = window.matchMedia(query)
    const onChange = () => setMatches(list.matches)
    onChange()
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** A tab label with its leading emoji removed.
 *
 *  `features.label_for()` returns "🎨 Krea2" — the emoji is part of the
 *  string because a Gradio tab had nowhere else to put one. The schema
 *  carries `icon` separately and every surface here renders the two apart,
 *  so without this the icon appears twice: "🎨 🎨 Krea2".
 *
 *  Only a *leading* pictographic run is taken, and only when something is
 *  left after it. The label is the one field an admin edits in Atlas rather
 *  than in this repo, so a label with no emoji, or one that is nothing but
 *  an emoji, has to come back unchanged rather than empty. */
export function labelText(label: string): string {
  const stripped = label.replace(/^[\p{Extended_Pictographic}\p{Emoji_Component}\uFE0F\u200D]+\s*/u, '')
  return stripped.trim() || label
}
