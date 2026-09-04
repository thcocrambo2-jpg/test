import { useQuery } from '@tanstack/react-query'
import { api } from './client'
import type { TabSchema } from './types'

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
