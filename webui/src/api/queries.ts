import { useQuery } from '@tanstack/react-query'
import { api } from './client'
import type { ModelRow, TabSchema } from './types'
import { useQueue } from '@/store/queue'

/** Server state. The schema, session, catalogue and showcase are all
 *  effectively static for a session, so they are cached hard — refetching
 *  them on a window focus would be noise. */

const FOREVER = { staleTime: Infinity, gcTime: Infinity } as const

export function useSession() {
  return useQuery({ queryKey: ['session'], queryFn: () => api.getSession(), ...FOREVER })
}

export function useSchemas() {
  return useQuery({ queryKey: ['schemas'], queryFn: () => api.getSchemas(), ...FOREVER })
}

export function useSchema(key: string | undefined): TabSchema | undefined {
  const { data } = useSchemas()
  if (!key) return undefined
  return data?.find((tab) => tab.key === key)
}

/** The model registries and per-variant defaults. Static for a session:
 *  they come from config.py, which is 909 lines with zero `def` and zero
 *  `class` — pure data compiled into the binary. */
export function useAppCatalog() {
  return useQuery({ queryKey: ['catalog'], queryFn: () => api.getAppCatalog(), ...FOREVER })
}

/** One tab's model registry, or an empty list for a tab with no models. */
export function useModels(schema: TabSchema | undefined): ModelRow[] {
  const { data } = useAppCatalog()
  if (!schema?.modelRegistry) return EMPTY_MODELS
  return data?.models[schema.modelRegistry] ?? EMPTY_MODELS
}

const EMPTY_MODELS: ModelRow[] = []

/** One tab's presets, refetched when a background job saves one.
 *
 *  The save happens on the worker thread, wherever the job eventually ran,
 *  so nothing else is in a position to notice that this list went stale —
 *  which is what jobqueue.note_preset_saved and the `presets` stream event
 *  exist for. */
export function usePresets(tab: string | null) {
  const revision = useQueue((state) => (tab ? (state.presetRevision[tab] ?? 0) : 0))
  return useQuery({
    queryKey: ['presets', tab, revision],
    queryFn: () => api.getPresets(tab as string),
    enabled: Boolean(tab),
    staleTime: 5 * 60_000,
  })
}

export function useCatalogue() {
  return useQuery({
    queryKey: ['catalogue'],
    queryFn: () => api.getCatalogue(),
    staleTime: 5 * 60_000,
  })
}

export function useShowcase() {
  return useQuery({ queryKey: ['showcase'], queryFn: () => api.getShowcase(), ...FOREVER })
}

export function useGallery(cursor: string | null) {
  return useQuery({
    queryKey: ['gallery', cursor],
    queryFn: () => api.getGallery(cursor),
    staleTime: 30_000,
  })
}
