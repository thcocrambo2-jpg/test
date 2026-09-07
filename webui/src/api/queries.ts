import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
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

/** One page of the gallery listing.
 *
 *  `kind` is part of the key rather than a filter applied to the answer: the
 *  server pages a *narrowed* listing, so the stills page and the everything
 *  page are two different lists with two different cursors. Both sit under
 *  `['gallery', …]`, so the `invalidateQueries({ queryKey: ['gallery'] })`
 *  every delete already does still reaches both. */
export function useGallery(cursor: string | null, kind?: 'image') {
  return useQuery({
    queryKey: ['gallery', kind ?? 'all', cursor],
    queryFn: () => api.getGallery(cursor, kind),
    staleTime: 30_000,
  })
}

/** The newest stills, for the strip under every image input.
 *
 *  The first page and only the first page. This is "the thing you made a
 *  minute ago, fed back in", not a browser — the Gallery is one click away
 *  and does that job properly, with paging and a lightbox.
 *
 *  It refetches whenever a run produces a file, which the Gallery itself
 *  deliberately does not always do: there, a grid reflowing under a
 *  half-made selection or an open lightbox is its own small hostility.
 *  Neither exists here, and a strip that does not offer the picture you have
 *  just made is a strip nobody looks at twice. The two are never mounted
 *  together — a tab route and `/library/gallery` are different routes — so
 *  this cannot pull the grid out from under anyone.
 *
 *  An error is not reported. `/gallery` is gated on the Gallery feature, so
 *  a licence without it answers 403, and the honest rendering of that under
 *  an upload field is nothing at all. */
export function useRecentImages() {
  const queryClient = useQueryClient()
  const mediaRevision = useQueue((state) => state.mediaRevision)
  const query = useGallery(null, 'image')

  const counted = useRef(mediaRevision)
  useEffect(() => {
    if (mediaRevision === counted.current) return
    counted.current = mediaRevision
    void queryClient.invalidateQueries({ queryKey: ['gallery'] })
  }, [mediaRevision, queryClient])

  return query
}
