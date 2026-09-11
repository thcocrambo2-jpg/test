/*
 * The terms themselves, kept apart from the thing that shows them.
 *
 * Wording is the one part of this feature that is certain to change, and it
 * should never take a component with it. Edit this file, bump TERMS_VERSION,
 * and every visitor is asked again — see `storage.ts`, which compares the
 * accepted version against this one rather than storing a bare boolean.
 *
 * NOT drafted by a lawyer. It says plainly what this app will not be used
 * for and who answers for what comes out of it, which is what was asked for;
 * if these have to hold up somewhere, have someone qualified read them first.
 */

/** Bump on any change of substance. A visitor who accepted an older version
 *  is asked again; a typo fix is not worth interrupting anyone over. */
export const TERMS_VERSION = '2026-09-11'

export interface Clause {
  title: string
  body: string
  /** Rendered with the accent rail. The three that are the point of having
   *  terms at all — not "important", but the ones that end access. */
  critical?: boolean
}

export const TERMS: Clause[] = [
  {
    title: 'Nothing sexual involving a minor. Ever.',
    critical: true,
    body:
      'You will not generate, edit, upscale or attempt to produce sexual or ' +
      'suggestive imagery of anyone under 18 — real, drawn, described, ' +
      'stylised or "synthetic". You will not upload a photograph of a child ' +
      'to any edit, inpaint, variance or face-swap tool here, for any reason, ' +
      'including ones you consider harmless. There is no artistic exception, ' +
      'no research exception, and no "it is not a real person" exception. ' +
      'This is a crime in most of the world and it is the one line where ' +
      'intent, framing and explanation do not matter. Use this app this way ' +
      'and your access ends without notice.',
  },
  {
    title: 'No real person without that person’s consent.',
    critical: true,
    body:
      'Do not upload, edit, inpaint, face-swap or otherwise alter an image of ' +
      'a real human being unless they have agreed to what you are about to ' +
      'do. That covers putting someone into a scene they were never in, ' +
      'changing or removing their clothing, and producing sexual, intimate or ' +
      'degrading imagery of them. A celebrity, a politician, an ex, a ' +
      'colleague and a stranger from the internet are all real people. If you ' +
      'cannot point to their consent, do not press Generate.',
  },
  {
    title: 'What you generate is yours — including the consequences.',
    critical: true,
    body:
      'This app runs the model you picked, on the prompt and images you gave ' +
      'it. It does not review, approve or vouch for anything that comes out. ' +
      'Whatever you generate, keep, publish, sell or send to another person ' +
      'is your act: your responsibility under the law where you live, and ' +
      'yours in any claim brought by someone depicted or harmed. "The model ' +
      'produced it" is not a defence — you chose the prompt, the source ' +
      'image and the moment you pressed the button.',
  },
  {
    title: 'Stay inside the law.',
    body:
      'Beyond the above: no forged identity or financial documents, no ' +
      'content built to harass, threaten, defame or impersonate a specific ' +
      'person, no material designed to mislead a viewer about a real event, ' +
      'and nothing illegal where you are or where you send it. If a use would ' +
      'embarrass you to explain to the person it depicts, treat that as the ' +
      'answer.',
  },
  {
    title: 'Your files are not backed up.',
    body:
      'Uploads land in a temporary directory and are swept a few hours later. ' +
      'Finished images are written to the output directory on the machine ' +
      'running this app, and stopping or terminating that machine can destroy ' +
      'them. Download anything you want to keep, when you make it.',
  },
  {
    title: 'Provided as-is.',
    body:
      'Generations fail, queues drop, models return nonsense and GPUs ' +
      'disappear mid-run. Nothing here is a promise of availability, of ' +
      'fitness for any particular purpose, or that a given prompt will ever ' +
      'produce a given picture.',
  },
]

/** The checkbox label. Its own export because it is the sentence that is
 *  actually being agreed to, and it should be as easy to find as the rest. */
export const AGREEMENT =
  'I am 18 or over, I have read these terms, and I accept responsibility for ' +
  'everything I generate with this app.'
