import { useSession } from '@/api/queries'
import { useQueue } from '@/store/queue'
import { Pill } from '@/components/ui'
import { useCopy } from '@/lib/util'
import s from './shell.module.css'

/*
 * The line under everything: the keyboard hint and the ambient facts.
 *
 * Carried over from `footer_html` (theme.py:3159) including its reasoning —
 * the counts and the path are reference material rather than something read
 * on every glance, and the path in particular gets typed into scp often
 * enough to be worth a click to copy. In Gradio that click needed injected JS
 * and a `data-kx-copy` attribute; here it is an onClick.
 */
export function Footer() {
  const { data: session } = useSession()
  const { copied, copy } = useCopy()
  const transport = useQueue((state) => state.transport)

  const models = session?.modelCount ?? 0
  const gpus = session?.gpuCount ?? 0
  const path = session?.outputDir ?? ''

  return (
    <footer className={s.footer}>
      <div className={s.hint}>
        <kbd className={s.kbd}>Ctrl</kbd>
        <span>+</span>
        <kbd className={s.kbd}>Enter</kbd>
        <span>runs the tab you are on</span>
      </div>

      <div className={s.footerRight}>
        {/* Only when it is not live. A page that is up to date says nothing
         *  about how it got that way; a page that has fallen back to polling
         *  has to say so, because the failure this replaced looked exactly
         *  like a healthy page that had quietly stopped listening. */}
        {transport === 'polling' && (
          <Pill
            tone="warning"
            title="The event stream is not delivering — something between this browser and the app is holding it. Falling back to polling; everything still updates, about a second slower."
          >
            polling
          </Pill>
        )}
        <Pill>
          <b>{models}</b> model{models === 1 ? '' : 's'}
        </Pill>
        <Pill>
          <b>{gpus}</b> GPU{gpus === 1 ? '' : 's'}
        </Pill>
        {path && (
          <Pill
            tone={copied ? 'success' : 'default'}
            onClick={() => copy(path)}
            title="Click to copy the output directory"
            className={s.path}
          >
            {copied ? 'Copied' : path}
          </Pill>
        )}
      </div>
    </footer>
  )
}
