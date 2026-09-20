import type { ApiClient } from './types'
import { httpClient } from './http'

/*
 * The seam between the app and its server.
 *
 * Kept as a module rather than collapsed into `http.ts` because every
 * component imports `api` from here. One re-export buys the freedom to
 * change what backs it without touching a single component.
 */

export const api: ApiClient = httpClient

export { exchangeToken } from './http'
