import { useCallback, useSyncExternalStore } from 'react'

export type ThemeName = 'dark' | 'light'

const KEY = 'ember.theme'
const listeners = new Set<() => void>()

function read(): ThemeName {
  const attr = document.documentElement.dataset.theme
  return attr === 'light' ? 'light' : 'dark'
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function write(next: ThemeName) {
  document.documentElement.dataset.theme = next
  try {
    localStorage.setItem(KEY, next)
  } catch {
    /* private mode, or storage disabled — the theme just does not persist */
  }
  listeners.forEach((fn) => fn())
}

/** The theme, and a toggle. `data-theme` on <html> is the single source of
 *  truth; index.html stamps it before first paint so there is no flash. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, read, () => 'dark' as ThemeName)
  const toggle = useCallback(() => {
    write(read() === 'dark' ? 'light' : 'dark')
  }, [])
  const set = useCallback((next: ThemeName) => write(next), [])
  return { theme, toggle, set }
}
