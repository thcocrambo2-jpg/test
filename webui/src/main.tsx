import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { exchangeToken } from '@/api/client'
import { ConfirmHost, ToastHost } from '@/components/ui'
import './theme/base.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Everything the app reads is local to one machine and one process —
      // there is no other client to race with, so refetching on focus is pure
      // noise. The gallery invalidates itself when something is added.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

/* Trade the URL fragment for a cookie before the first request goes out.
 *
 * The token arrives as `https://….trycloudflare.com/#k=<token>`, and the
 * fragment is the whole point: fragments are never sent to servers, so it
 * stays out of Cloudflare's logs, out of every proxy in between and out of
 * `Referer` headers. `exchangeToken` also calls history.replaceState, so it
 * is gone from the address bar before anyone can screenshot it.
 *
 * Awaited rather than fired and forgotten: React Query's first fetch happens
 * on mount, and a session request that races the cookie is a 401 the shell
 * would have to recover from for no reason. */
void exchangeToken().finally(() => boot())

function boot() {
  createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastHost>
          <ConfirmHost>
            <App />
          </ConfirmHost>
        </ToastHost>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
  )
}
