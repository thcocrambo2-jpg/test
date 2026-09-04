import { Link } from 'react-router-dom'
import { useSession } from '@/api/queries'
import { Button, Pill, Skeleton } from '@/components/ui'
import { useTheme } from '@/theme/useTheme'
import { daysUntil, formatDate } from '@/lib/util'
import s from './shell.module.css'

/*
 * The application bar.
 *
 * Same editorial decision `header_html` (theme.py:3122) already made and
 * documented: the brand, plus the two things a customer cannot read off the
 * page itself — which plan they are on and when it runs out. The engine chips
 * are gone for good, and the model/GPU counts and the output path live in the
 * footer. Both licence facts are routinely null (a key can name no plan and
 * have no expiry), so neither renders a placeholder.
 *
 * The theme toggle is new. The old app had both modes and no way to pick one.
 */
export function Header() {
  const { data: session, isLoading } = useSession()
  const { theme, toggle } = useTheme()

  const days = daysUntil(session?.expiresAt ?? null)
  const expiryTone = days === null ? 'default' : days <= 7 ? 'danger' : days <= 30 ? 'warning' : 'default'

  return (
    <header className={s.header}>
      <Link to="/" className={s.brand}>
        <span className={s.logo} aria-hidden>
          E
        </span>
        <span>
          <span className={s.name}>{session?.brand ?? 'Ember'}</span>
          <span className={s.tagline}>{session?.tagline ?? ' '}</span>
        </span>
      </Link>

      <div className={s.headerRight}>
        {isLoading ? (
          <>
            <Skeleton width={96} height={24} radius="var(--r-full)" />
            <Skeleton width={128} height={24} radius="var(--r-full)" />
          </>
        ) : (
          <>
            {session?.planName && (
              <Pill tone="accent">
                <b>{session.planName}</b>
              </Pill>
            )}
            {session?.expiresAt && (
              <Pill
                tone={expiryTone}
                title={`Expires ${formatDate(session.expiresAt) ?? ''}`}
              >
                {days !== null && days >= 0 ? `${days} days left` : 'Expired'}
              </Pill>
            )}
          </>
        )}

        <Button
          variant="ghost"
          size="sm"
          iconOnly
          onClick={toggle}
          title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
          aria-label="Toggle theme"
        >
          {theme === 'dark' ? '☀' : '☾'}
        </Button>
      </div>
    </header>
  )
}
