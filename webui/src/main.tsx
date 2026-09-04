import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { ToastHost } from '@/components/ui'
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastHost>
          <App />
        </ToastHost>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
