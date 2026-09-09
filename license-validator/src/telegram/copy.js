// Every string a customer reads. Nothing else in `telegram/` builds a
// sentence.
//
// ── Why one file ───────────────────────────────────────────────────────
//
// The wording of two of these decides whether a paying customer can run
// what they bought, and one of them is the single likeliest cause of "I
// paid and it doesn't work": the delivery message must carry the node tag
// as well as the licence key, because a pod with a key and no tag exits at
// startup with code 2 before it prints anything a customer could act on
// (config.py builds the licence API URL from the tag, and there is no
// key-entry screen anywhere in the app — both are environment variables).
// Keeping the strings together is what makes that reviewable in one place
// instead of spread across three handlers.
//
// It also means the tone is consistent, and that changing "licence" to
// "license" — or translating the lot — is one file.
//
// ── HTML, and why every value is escaped ───────────────────────────────
//
// Messages are sent with parse_mode HTML so the key can be wrapped in
// <code>, which on a phone is one tap to copy. That makes escaping
// mandatory rather than tidy: plan names and descriptions are edited in
// Atlas by hand, and a single unescaped "&" in one of them makes Telegram
// reject the entire message with can't parse entities — so the customer
// would get nothing at all, not a message with a stray ampersand in it.
//
// Only four characters need it (<, >, & and, inside attributes, "), and
// escaping them is safe for text that contains none.

/** Escape text for parse_mode: "HTML". */
export function escape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** "850 ⭐" — the Stars amount as it is written everywhere. */
export function stars(amount) {
  return `${amount} ⭐`;
}

export const START = [
  "<b>Krea 2</b>",
  "",
  "This bot sells and renews licence keys for the Krea 2 image and video app,",
  "paid in Telegram Stars.",
  "",
  "/plans — what is available and what it costs",
  "/help — everything I can do",
].join("\n");

export const HELP = [
  "<b>What I can do</b>",
  "",
  "/plans — the tiers, what each one includes, and the price in Stars",
  "/help — this message",
  "",
  "Payments are not switched on yet. Nothing here can charge you.",
].join("\n");

/** The header of the /plans reply. */
export const PLANS_HEADER = "<b>Plans</b>";

/**
 * One tier.
 *
 * Price first and on the same line as the name, because that is the line
 * people read. The feature list is the same one the pricing page shows —
 * the enabled features of the plan, in catalogue order — so a tab
 * withdrawn from sale disappears from both at once.
 */
export function planBlock({ name, price, description, features, popular }) {
  const lines = [
    `<b>${escape(name)}</b> — ${stars(price)} / month` +
      (popular ? "  ·  most popular" : ""),
  ];
  if (description) lines.push(escape(description));
  if (features.length) {
    lines.push(`Includes: ${features.map(escape).join(", ")}`);
  }
  return lines.join("\n");
}

/**
 * The footer under the tiers.
 *
 * It states the two things a customer cannot find out by reading the list
 * and would otherwise ask: what a month means, and that a key is per
 * machine at a time rather than per person.
 */
export const PLANS_FOOTER = [
  "A licence runs for one month from the day it is issued, on one machine",
  "at a time. Renewing adds another month to whatever is left — it never",
  "resets your key or your remaining days.",
].join("\n");

/** No plan carries a Stars price. Should never be seen by a customer. */
export const PLANS_EMPTY =
  "No plans are on sale right now. Please check back shortly.";

/**
 * Anything that is not a command we know.
 *
 * Deliberately not "unknown command": most of what lands here is a person
 * typing "hi", and telling them they made a mistake is a worse answer than
 * telling them what is available.
 */
export const FALLBACK = [
  "I only understand a few commands.",
  "",
  "/plans — what is available and what it costs",
  "/help — everything I can do",
].join("\n");

/**
 * Something threw.
 *
 * No detail, no error code, no apology for a specific failure: the customer
 * cannot act on any of it, and the real cause is in the server log with the
 * order id beside it. What they can act on is trying again.
 */
export const ERROR =
  "Something went wrong on our side. Please try that again in a moment.";
