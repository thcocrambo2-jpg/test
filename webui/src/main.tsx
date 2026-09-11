import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { exchangeToken } from '@/api/client'
import { ConfirmHost, ToastHost } from '@/components/ui'
import { TermsGate, hasAcceptedTerms } from '@/features/terms'
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

/* The terms gate stands in front of the whole application.
 *
 * `App` is not rendered behind it — it is not rendered *at all* until the box
 * is ticked. That is the difference between a dialog and a gate: App's first
 * mount opens the SSE connection and fires the session and schema queries, and
 * none of that should happen on behalf of someone who has not yet agreed to
 * anything. It also means there is no half-usable page to tab into behind the
 * backdrop.
 *
 * Read once, synchronously, from localStorage — so a browser that has already
 * agreed goes straight to the app with nothing flashing on the way past. */
function Gated() {
  const [agreed, setAgreed] = useState(() => hasAcceptedTerms())
  if (!agreed) return <TermsGate onAccept={() => setAgreed(true)} />
  return <App />
}

function boot() {
  createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastHost>
          <ConfirmHost>
            <Gated />
          </ConfirmHost>
        </ToastHost>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
  )
}
