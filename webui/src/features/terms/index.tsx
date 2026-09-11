import { useId, useState } from 'react'
import { Button, Modal } from '@/components/ui'
import { AGREEMENT, TERMS, TERMS_VERSION, type Clause } from './content'
import { recordAcceptance } from './storage'
import t from './terms.module.css'

export { hasAcceptedTerms } from './storage'

/*
 * The terms, in the two shapes they are needed in.
 *
 * `TermsGate` is the wall: shown before the app mounts at all, and there is
 * no way past it but the checkbox. `TermsDialog` is the same text read back
 * afterwards, from the footer — because a document someone is held to should
 * not become unreachable the moment they agree to it.
 */

/* A stable identity, because `Modal`'s key handler is keyed on [open,
 * onClose] and an inline arrow would tear the listener down and rebuild it
 * on every keystroke. Its job is to do nothing: Escape and a click on the
 * backdrop both land here, and both must not dismiss the gate. */
const noop = () => {}

function TermsBody() {
  return (
    <div className={t.clauses}>
      {TERMS.map((clause) => (
        <ClauseBlock key={clause.title} clause={clause} />
      ))}
    </div>
  )
}

function ClauseBlock({ clause }: { clause: Clause }) {
  return (
    <section className={clause.critical ? `${t.clause} ${t.critical}` : t.clause}>
      <h3 className={t.clauseTitle}>{clause.title}</h3>
      <p className={t.clauseBody}>{clause.body}</p>
    </section>
  )
}

/** The first-run wall. Rendered *instead of* the app, not over it — see
 *  main.tsx: nothing should be fetching, queueing or connecting on behalf of
 *  someone who has not yet agreed to anything. */
export function TermsGate({ onAccept }: { onAccept: () => void }) {
  const [checked, setChecked] = useState(false)
  const id = useId()

  function accept() {
    recordAcceptance()
    onAccept()
  }

  return (
    /* No `title` prop on purpose. The header it renders carries a ✕, and a
     * close button on a gate that cannot be closed is a dead control on the
     * one screen where every control has to work. The heading below is the
     * dialog's own. */
    <Modal open onClose={noop} width={640}>
      <h1 className={t.title}>Before you generate anything</h1>
      <p className={t.lede}>
        This app puts a GPU and an image model in your hands. Three of the rules
        below are the ones people actually break, so they are first. Read them.
      </p>

      <TermsBody />

      <label className={t.agree} htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          className={t.box}
          checked={checked}
          onChange={(event) => setChecked(event.target.checked)}
        />
        <span className={t.agreeText}>{AGREEMENT}</span>
      </label>

      <div className={t.foot}>
        <span className={t.version}>Version {TERMS_VERSION}</span>
        <Button variant="primary" size="lg" disabled={!checked} onClick={accept}>
          Agree and continue
        </Button>
      </div>
    </Modal>
  )
}

/** The same text, read back from the footer. Dismissible, and it records
 *  nothing — agreeing happened once, at the gate. */
export function TermsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Terms & Conditions" width={640}>
      <TermsBody />
      <div className={t.foot}>
        <span className={t.version}>Version {TERMS_VERSION}</span>
      </div>
    </Modal>
  )
}
