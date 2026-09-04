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
