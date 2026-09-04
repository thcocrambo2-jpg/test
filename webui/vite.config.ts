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
    // Vite owns the HTML and the hot reload; the Python app owns everything
    // else. Start it with:
    //   conda run -n krea2 python scripts/dryrun.py --features all --api-only
    //
    // /media and /thumbs are proxied as well as /api because a generated
    // image is served by the Python side too — they replaced Gradio's
    // allowed_paths, so there is no static directory for Vite to serve them
    // from.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:7860',
        changeOrigin: true,
        // Without this the SSE stream at /api/v1/stream is buffered by the
        // proxy and arrives in one lump when the connection closes, which
        // presents a week later as "the queue only updates when I reload".
        // Verified before the queue UI was built on top of it.
        ws: false,
        configure: (proxy) => {
          proxy.on('proxyRes', (res) => {
            if (String(res.headers['content-type']).includes('text/event-stream')) {
              res.headers['cache-control'] = 'no-cache, no-transform'
            }
          })
        },
      },
      '/media': { target: 'http://127.0.0.1:7860', changeOrigin: true },
      '/thumbs': { target: 'http://127.0.0.1:7860', changeOrigin: true },
    },
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
