import { useSession } from '@/api/queries'
import { useQueue } from '@/store/queue'
import { Pill } from '@/components/ui'
import s from './shell.module.css'

/*
 * The line under everything: the keyboard hint and the ambient facts.
 *
 * Carried over from `footer_html` (theme.py:3159): the counts are reference
 * material rather than something read on every glance.
 *
 * The output directory used to sit here too, as a click-to-copy pill. It is
 * gone, and the server no longer sends it (api.session) — it is an absolute
 * path, so on a machine someone runs locally and shares with others it spells
 * out the host account's name to every visitor. The path ids the gallery and
 * the output tiles copy are OUTPUT_DIR-*relative*, which is the part anyone
 * needed anyway.
 */
export function Footer() {
  const { data: session } = useSession()
  const transport = useQueue((state) => state.transport)

  const models = session?.modelCount ?? 0
  const gpus = session?.gpuCount ?? 0

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
      </div>
    </footer>
  )
}
