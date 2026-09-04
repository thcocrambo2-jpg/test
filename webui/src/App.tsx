import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useSchemas } from '@/api/queries'
import { Header } from '@/features/shell/Header'
import { Footer } from '@/features/shell/Footer'
import { Nav } from '@/features/shell/Nav'
import { QueuePanel } from '@/features/queue/QueuePanel'
import { GenerateTab } from '@/features/tabs/GenerateTab'
import { StagePending } from '@/features/tabs/StagePending'
import { Gallery } from '@/features/gallery/Gallery'
import { Pricing } from '@/features/pricing/Pricing'
import { EmptyState, Skeleton } from '@/components/ui'
import type { TabSchema } from '@/api/types'
import s from '@/features/shell/shell.module.css'

/*
 * Routing.
 *
 * A route per tab, which the twelve flat `gr.Tab`s never had: they share one
 * URL, so a link to "the inpaint tab with your settings" was not a thing that
 * could exist. Routes are `/{category}/{tab}` so the address bar says which
 * group you are in as well as which tab.
 */
export function App() {
  const { data: schemas, isLoading } = useSchemas()

  return (
    <div className={s.app}>
      <Header />
      <Nav />
      <main className={s.main}>
        {isLoading || !schemas ? (
          <PageSkeleton />
        ) : (
          <Routes>
            <Route path="/" element={<Navigate to={schemas[0].route} replace />} />

            {schemas.map((schema) => (
              <Route
                key={schema.key}
                path={schema.route}
                element={<TabPage schema={schema} />}
              />
            ))}

            <Route
              path="/library/gallery"
              element={
                <Page title="Gallery" icon="🖼️" blurb="Everything you have made, newest first.">
                  <Gallery />
                </Page>
              }
            />
            <Route
              path="/library/prompts"
              element={
                <Page title="Prompt Library" icon="🌟" blurb="Prompts that already work.">
                  <EmptyState icon="🌟" title="Stage B">
                    The prompt library is one of the three genuinely bespoke pages, and it
                    is not part of Stage A.
                  </EmptyState>
                </Page>
              }
            />
            <Route path="/pricing" element={<Pricing />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        )}
      </main>
      <Footer />
      <QueuePanel />
    </div>
  )
}

function TabPage({ schema }: { schema: TabSchema }) {
  return (
    <Page title={schema.label} icon={schema.icon} blurb={schema.blurb}>
      {schema.ready ? <GenerateTab schema={schema} /> : <StagePending schema={schema} />}
    </Page>
  )
}

function Page({
  title,
  icon,
  blurb,
  children,
}: {
  title: string
  icon: string
  blurb: string
  children: React.ReactNode
}) {
  return (
    <>
      <div className={s.pageHead}>
        <div>
          <h1 className={s.pageTitle}>
            <span className={s.pageIcon} aria-hidden>
              {icon}
            </span>
            {title}
          </h1>
          <p className={s.pageBlurb}>{blurb}</p>
        </div>
      </div>
      {children}
    </>
  )
}

function NotFound() {
  const location = useLocation()
  return (
    <EmptyState icon="🧭" title="No such page">
      Nothing is routed at <code>{location.pathname}</code>.
    </EmptyState>
  )
}

function PageSkeleton() {
  return (
    <>
      <div className={s.pageHead}>
        <div>
          <Skeleton width={220} height={28} />
          <Skeleton width={320} height={14} style={{ marginTop: 8 }} />
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 380px) 1fr', gap: 20 }}>
        <Skeleton height={520} radius="var(--r-lg)" />
        <Skeleton height={520} radius="var(--r-lg)" />
      </div>
    </>
  )
}
