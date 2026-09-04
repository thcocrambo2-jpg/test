import { useMemo, useState } from 'react'
import { useCatalogue, useShowcase } from '@/api/queries'
import type { Catalogue, Plan } from '@/api/types'
import { Alert, Button, Modal, Pill, Segmented, Skeleton } from '@/components/ui'
import { cx, money } from '@/lib/util'
import { useTabState } from '@/store/tabState'
import { ShowcaseSections } from './Showcase'
import s from './pricing.module.css'

/*
 * The pricing page.
 *
 * Roughly 780 lines of hand-written HTML generation in theme.py today —
 * `pricing_html` (:3493), `showcase_html` (:3827), `_lightbox` (:3616),
 * `_cycle_style` (:3250), `_cycle_tabs` (:3291), `_cycle_price` (:3334) —
 * and it is not a decorative panel: it is how a customer upgrades.
 *
 * Three of those functions exist only to work around one Gradio limitation.
 * `gr.HTML` strips `<script>` (theme.py:1724, :1801), so the monthly/annual
 * toggle is a CSS-only radio hack whose every price has to be emitted for
 * every cycle up front, the modals are `:target` links, and the lightbox is
 * the same trick again. In React the toggle is `useState` and the markup for
 * one cycle is the markup for all of them.
 *
 * What does *not* change: every figure is rendered, never derived. The server
 * computes each cycle's total, per-month and saving from the plan's monthly
 * rate (plans.js `cyclePrice`), and plans.py:69 spells out why a second
 * implementation of that arithmetic is a second chance to quote a price the
 * invoice does not match.
 */
export function Pricing() {
  const { data: catalogue, isLoading, error } = useCatalogue()
  const { data: showcase } = useShowcase()
  // Kept per tab: losing it falls back to the derived default below, which
  // reads as the prices having changed on their own while you were away.
  const [cycle, setCycle] = useTabState<string | null>('pricing.cycle', null)
  const [contact, setContact] = useState(false)

  const cycles = catalogue?.cycles ?? []
  // Default to the longest cycle offering a discount, else monthly. A server
  // sending one cycle gets no tab bar at all, which is what plans.py:52
  // designed the tuple for.
  const active = useMemo(() => {
    if (cycle) return cycle
    const discounted = [...cycles].sort((a, b) => b.discount_percent - a.discount_percent)[0]
    return discounted?.id ?? cycles[0]?.id ?? 'monthly'
  }, [cycle, cycles])

  if (isLoading) return <PricingSkeleton />

  if (error || !catalogue) {
    return (
      <Alert tone="error" title="The plan catalogue could not be loaded">
        {(error as Error)?.message ?? 'The licence server did not answer.'}
      </Alert>
    )
  }

  return (
    <div className={s.page}>
      <header className={s.hero}>
        {showcase?.eyebrow && <span className={s.eyebrow}>{showcase.eyebrow}</span>}
        <h1 className={s.title}>
          {showcase?.title ?? 'Everything this app can do'}
          {showcase?.title_accent && (
            <span className={s.titleAccent}>{showcase.title_accent}</span>
          )}
        </h1>
        {showcase?.body[0] && <p className={s.lede}>{showcase.body[0]}</p>}
        {showcase && showcase.stats.length > 0 && (
          <div className={s.stats}>
            {showcase.stats.map((stat) => (
              <div key={stat.label} className={s.stat}>
                <div className={s.statValue}>{stat.value}</div>
                <div className={s.statLabel}>{stat.label}</div>
              </div>
            ))}
          </div>
        )}
      </header>

      <section className={s.section}>
        <div className={s.sectionHead}>
          <h2 className={s.sectionTitle}>Plans</h2>
          {catalogue.error && <Alert tone="warning">{catalogue.error}</Alert>}
          {cycles.length > 1 && (
            <Segmented
              value={active}
              ariaLabel="Billing cycle"
              onChange={setCycle}
              options={cycles.map((entry) => ({
                value: entry.id,
                label:
                  entry.discount_percent > 0
                    ? `${entry.label} · −${Math.round(entry.discount_percent)}%`
                    : entry.label,
              }))}
            />
          )}
        </div>

        <div className={s.plans}>
          {[...catalogue.plans]
            .sort((a, b) => a.sort_order - b.sort_order)
            .map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                cycle={active}
                catalogue={catalogue}
                onContact={() => setContact(true)}
              />
            ))}
        </div>
      </section>

      {showcase && showcase.sections.length > 0 && (
        <section className={s.section}>
          <ShowcaseSections sections={showcase.sections} />
        </section>
      )}

      {showcase?.cta_title && (
        <section className={s.cta}>
          <h2 className={s.ctaTitle}>{showcase.cta_title}</h2>
          {showcase.cta_body && <p className={s.ctaBody}>{showcase.cta_body}</p>}
          <Button variant="primary" size="lg" onClick={() => setContact(true)}>
            Get in touch
          </Button>
        </section>
      )}

      <Modal open={contact} onClose={() => setContact(false)} title="Change your plan">
        <p style={{ color: 'var(--c-text-muted)' }}>
          Upgrades are applied to your key by hand and take a few minutes. You keep
          working while it happens — the new tabs are there the next time the app
          starts.
        </p>
        {catalogue.contact_url ? (
          <p style={{ marginTop: 'var(--s-4)' }}>
            <a href={catalogue.contact_url} target="_blank" rel="noreferrer noopener">
              {catalogue.contact_url}
            </a>
          </p>
        ) : (
          <p style={{ marginTop: 'var(--s-4)', color: 'var(--c-text-faint)' }}>
            Get in touch through your usual channel and quote your licence key.
          </p>
        )}
      </Modal>
    </div>
  )
}

function PlanCard({
  plan,
  cycle,
  catalogue,
  onContact,
}: {
  plan: Plan
  cycle: string
  catalogue: Catalogue
  onContact: () => void
}) {
  const price = plan.prices[cycle]
  const owned = plan.features.every((key) => catalogue.owned.includes(key))

  return (
    <article className={cx(s.plan, plan.is_popular && s.planPopular)}>
      {plan.is_popular && <span className={s.flag}>Most popular</span>}

      <div>
        <h3 className={s.planName}>{plan.name}</h3>
        <p className={s.planDesc}>{plan.description}</p>
      </div>

      {price ? (
        <div>
          <div className={s.price}>
            <span className={s.priceAmount}>{money(price.per_month, plan.currency)}</span>
            <span className={s.pricePer}>/ month</span>
          </div>
          <div className={s.priceNote}>
            {price.months > 1 ? (
              <>
                {money(price.total, plan.currency)} billed every {price.months} months
                {price.saving > 0 && (
                  <>
                    {' · '}
                    <span className={s.saving}>
                      save {money(price.saving, plan.currency)}
                    </span>
                  </>
                )}
              </>
            ) : (
              'Billed monthly'
            )}
          </div>
        </div>
      ) : (
        <div>
          <div className={s.priceAmount} style={{ fontSize: 'var(--t-xl)' }}>
            Price on application
          </div>
          <div className={s.priceNote} />
        </div>
      )}

      <ul className={s.features}>
        {plan.features.map((key) => {
          const info = catalogue.features[key]
          const has = catalogue.owned.includes(key)
          return (
            <li key={key} className={s.feature}>
              {/* Green means your licence already grants it; grey means this
                  tier is where you would get it. One glyph, two colours —
                  a second symbol only makes the list look like two lists. */}
              <span
                className={cx(s.tick, has && s.tickOwned)}
                title={has ? 'Already in your plan' : 'Included in this plan'}
                aria-hidden
              >
                ✓
              </span>
              <span>
                <span className={s.featureName}>
                  {info?.name ?? key.replace(/_/g, ' ')}
                </span>
                {info?.description && (
                  <span className={s.featureDesc}>{info.description}</span>
                )}
              </span>
            </li>
          )
        })}
      </ul>

      {owned ? (
        <Pill tone="success">Your current plan covers this</Pill>
      ) : (
        <Button variant={plan.is_popular ? 'primary' : 'default'} block onClick={onContact}>
          Upgrade to {plan.name}
        </Button>
      )}
    </article>
  )
}

function PricingSkeleton() {
  return (
    <div className={s.page}>
      <div className={s.hero}>
        <Skeleton width={180} height={12} />
        <Skeleton width="min(560px, 90%)" height={44} />
        <Skeleton width="min(680px, 95%)" height={54} />
      </div>
      <div className={s.plans}>
        {[0, 1, 2].map((index) => (
          <Skeleton key={index} height={420} radius="var(--r-xl)" />
        ))}
      </div>
    </div>
  )
}
