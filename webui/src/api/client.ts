import type { ApiClient } from './types'
import { httpClient } from './http'

/*
 * The seam, now that there is nothing on the other side of it.
 *
 * Section 1 built the whole UI against a behavioural mock and put the only
 * `if (mock)` in the application here, so that Section 2 would be three
 * deletions rather than an excavation. It was:
 *
 *   1. delete `src/mock/`
 *   2. delete the import and the ternary
 *   3. done
 *
 * Kept as a module rather than collapsed into `http.ts` because every
 * component imports `api` from here, and the indirection is what made the
 * swap invisible to all of them. It costs one re-export.
 */

export const api: ApiClient = httpClient

export { exchangeToken } from './http'
