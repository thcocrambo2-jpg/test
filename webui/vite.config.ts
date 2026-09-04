import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// One JS chunk, no sourcemap — deliberate, and not a performance oversight.
// The bundle is later embedded into the Nuitka binary (see context.md §4.7/§4.8)
// and served out of process memory, so:
//   * code-splitting buys nothing — there is no CDN, no HTTP cache, no second visit;
//   * a runtime import() of a chunk the embedder did not register is a hard 404;
//   * a sourcemap would ship the whole source tree inside a commercial binary.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // The mock layer reads ../scripts/parity_baseline.json — the parity contract
    // itself, not a copy of it, so the forms cannot drift from the baseline.
    fs: { allow: ['..'] },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: undefined,
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
})
