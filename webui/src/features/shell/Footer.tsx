import { useSession } from '@/api/queries'
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
