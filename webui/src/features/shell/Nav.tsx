import { NavLink } from 'react-router-dom'
import { useSchemas, useSession } from '@/api/queries'
import { BESPOKE_TABS, CATEGORY_LABEL, CATEGORY_ORDER } from '@/lib/nav'
import { Skeleton } from '@/components/ui'
import { cx } from '@/lib/util'
import type { TabCategory } from '@/api/types'
import s from './shell.module.css'

interface Item {
  key: string
  label: string
  icon: string
  route: string
  category: TabCategory
  ready: boolean
}

/*
 * Grouped navigation, and a route per tab.
 *
 * Two things the Gradio app could not have: a URL you can bookmark or send to
 * someone (twelve `gr.Tab`s share one URL), and a grouping that says which of
 * them make a picture and which of them change one.
 *
 * A tab absent from the licence is absent from here. That is the same render
 * gate as `ui.py:5139` — and, as context.md §4.5 records, in FastAPI the
 * render gate stops being the whole story, because a route registered
 * unconditionally is reachable whatever the nav shows. Closing that is
 * Section 2's job; this is only the visible half.
 */
export function Nav() {
  const { data: schemas, isLoading } = useSchemas()
  const { data: session } = useSession()

  if (isLoading || !schemas) {
    return (
      <nav className={s.nav}>
        {[0, 1, 2].map((group) => (
          <div key={group} className={s.navGroup}>
            <Skeleton width={68} height={11} style={{ margin: '0 12px 10px' }} />
            {[0, 1].map((row) => (
              <Skeleton key={row} height={32} radius="var(--r-md)" style={{ margin: '1px 0' }} />
            ))}
          </div>
        ))}
      </nav>
    )
  }

  const granted = session?.features
  const items: Item[] = [
    ...schemas.map((schema) => ({
      key: schema.key,
      label: schema.label,
      icon: schema.icon,
      route: schema.route,
      category: schema.category,
      ready: schema.ready,
    })),
    ...BESPOKE_TABS,
  ].filter((item) => !granted || granted.includes(item.key))

  return (
    <nav className={s.nav}>
      {CATEGORY_ORDER.map((category) => {
        const group = items.filter((item) => item.category === category)
        if (group.length === 0) return null
        return (
          <div key={category} className={s.navGroup}>
            <div className={s.navTitle}>{CATEGORY_LABEL[category]}</div>
            {group.map((item) => (
              <NavLink
                key={item.key}
                to={item.route}
                className={({ isActive }) => cx(s.navLink, isActive && s.navActive)}
              >
                <span className={s.navIcon} aria-hidden>
                  {item.icon}
                </span>
                <span className={s.navLabel}>{item.label}</span>
                {!item.ready && <span className={s.navBadge}>soon</span>}
              </NavLink>
            ))}
          </div>
        )
      })}

      <div className={s.navFoot}>
        <NavLink
          to="/pricing"
          className={({ isActive }) => cx(s.navLink, isActive && s.navActive)}
        >
          <span className={s.navIcon} aria-hidden>
            ✦
          </span>
          <span className={s.navLabel}>Plans &amp; features</span>
        </NavLink>
      </div>
    </nav>
  )
}
