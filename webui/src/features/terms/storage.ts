/*
 * Where "yes, I agreed" is kept.
 *
 * localStorage, deliberately — not the server. The auth gate is open by
 * default now (api.py, ALLOW_ANON), so anyone holding the link reaches this
 * app, and a record on the server would be one flag for all of them: the
 * first visitor to tick the box would silently agree on behalf of every
 * person who opened the link afterwards. Consent that one stranger can give
 * for another is not consent. Per-browser is the honest scope.
 *
 * The cost of that choice, stated plainly: clearing site data, a private
 * window, or a second device asks again. That is the correct behaviour for a
 * record of who agreed, and the wrong behaviour for a preference — which is
 * why this does not live next to the theme setting.
 */

import { TERMS_VERSION } from './content'

const KEY = 'ember.terms.accepted'

/* Storage can be absent entirely — Safari in private mode used to throw on
 * write, an embedded webview can have it disabled, and a browser told to
 * block site data throws on read as well. None of that should leave a
 * visitor unable to get past a checkbox, so a failed write degrades to "this
 * tab, until reload" rather than to an error. */
let inMemory: string | null = null

function read(): string | null {
  try {
    return window.localStorage.getItem(KEY) ?? inMemory
  } catch {
    return inMemory
  }
}

/** Has this browser accepted *these* terms?
 *
 *  Version-compared rather than a boolean, so that editing content.ts and
 *  bumping TERMS_VERSION asks everyone again instead of quietly holding
 *  people to a document they never saw. */
export function hasAcceptedTerms(): boolean {
  return read() === TERMS_VERSION
}

/** Record agreement to the current version. Stores the timestamp alongside
 *  it under a second key — not read by anything here, and kept because "when
 *  did this browser agree, and to what" is the only question anyone ever asks
 *  afterwards, and it cannot be answered retroactively. */
export function recordAcceptance(): void {
  inMemory = TERMS_VERSION
  try {
    window.localStorage.setItem(KEY, TERMS_VERSION)
    window.localStorage.setItem(`${KEY}.at`, new Date().toISOString())
  } catch {
    /* In-memory is already set; this tab proceeds and the next one asks. */
  }
}
