/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** '1' selects the mock API. See src/api/client.ts — the one seam. */
  readonly VITE_USE_MOCK: string
  readonly VITE_API_BASE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
