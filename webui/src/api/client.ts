import type { ApiClient } from './types'
import { httpClient } from './http'
import { mockClient } from '@/mock/server'

/*
 * The seam.
 *
 * This is the only place in the application that knows whether it is talking
 * to a real server. There is no `if (mock)` anywhere else — not in a
 * component, not in a hook, not in the store — which is what makes Section 2
 * a deletion rather than an excavation:
 *
 *   1. delete `src/mock/`
 *   2. delete the import and the ternary below
 *   3. done
 *
 * `VITE_USE_MOCK` is checked as a string because Vite inlines env values as
 * strings; `.env.production` already sets it to 0, so a production build gets
 * the real client whatever a developer left in their shell.
 *
 * The mock is still *linked* into a production build even when the flag is
 * off — the ternary references it, so Rollup cannot prove it dead. That is
 * accepted rather than worked around with a build-time alias, because the
 * directory is deleted outright in Section 2 and a temporary ~40 KB of JSON
 * is cheaper than config that outlives the thing it was hiding.
 */

const USE_MOCK = import.meta.env.VITE_USE_MOCK === '1'

export const api: ApiClient = USE_MOCK ? mockClient : httpClient

export const usingMock = USE_MOCK
