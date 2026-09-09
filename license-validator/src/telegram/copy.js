// Every string a customer reads. Nothing else in `telegram/` builds a
// sentence.
//
// ── Why one file ───────────────────────────────────────────────────────
//
// The wording of one of these decides whether a paying customer can run
// what they bought, and it is the single likeliest cause of "I paid and it
// doesn't work": the delivery message must carry the node tag as well as
// the licence key, because a pod with a key and no tag exits at startup
// with code 2 before it prints anything a customer could act on
// (config.py:955-964 builds the licence API URL from the tag, and there is
// no key-entry screen anywhere in the app — both are environment
// variables). Keeping the strings together is what makes that reviewable
// in one place instead of spread across three handlers.
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
// Only three characters need it (<, > and &), and escaping them is safe
// for text that contains none.

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

/**
 * A date as the customer sees it.
 *
 * ISO, not a localised long form: this is read by people in several
 * countries and 09/10/2026 means two different days depending on where
 * they are. The rest of the service formats expiry dates the same way,
 * including the message licensing.py shows when a key has run out.
 */
export function day(value) {
  return value ? new Date(value).toISOString().slice(0, 10) : "—";
}

export const START = [
  "<b>Krea 2</b>",
  "",
  "This bot sells and renews licence keys for the Krea 2 image and video app,",
  "paid in Telegram Stars.",
  "",
  "/plans — what is available and what it costs",
  "/buy — buy or renew a licence",
  "/mykeys — the keys you already have",
  "/help — everything I can do",
].join("\n");

export const HELP = [
  "<b>What I can do</b>",
  "",
  "/plans — the tiers, what each one includes, and the price in Stars",
  "/buy — buy a licence, renew it, or move to another tier",
  "/mykeys — your keys, their plan and when they run out",
  "/renew — renew the licence you already have",
  "/help — this message",
  "",
  "Payment is in Telegram Stars. Renewing adds to the time you have left;",
  "it never resets your key.",
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
  "",
  "/buy when you are ready.",
].join("\n");

/** No plan carries a Stars price. Should never be seen by a customer. */
export const PLANS_EMPTY =
  "No plans are on sale right now. Please check back shortly.";

// ── Buying ──────────────────────────────────────────────────────────────

export const BUY_PROMPT = "Which tier would you like?";

/** The same prompt for somebody who already holds a licence. */
export function buyPromptExisting(planName, expiresAt) {
  return [
    `You are on <b>${escape(planName)}</b> until ${day(expiresAt)}.`,
    "",
    "Tap the same tier to add another month to what is left, or a different",
    "one to move up. Moving up starts a fresh month from today.",
  ].join("\n");
}

export function invoiceTitle(planName) {
  return `Krea 2 — ${planName}`;
}

/**
 * The invoice body.
 *
 * Telegram shows this under the title in the payment sheet, and it is the
 * last thing read before money moves — so it states the term and the fact
 * that a key arrives here, in this chat, rather than by email.
 */
export function invoiceDescription({ planName, months, renewal }) {
  const term = months === 1 ? "one month" : `${months} months`;
  return renewal
    ? `Adds ${term} to your ${planName} licence. Your key does not change.`
    : `${planName} for ${term}. Your licence key is sent here as soon as ` +
        "the payment goes through.";
}

/**
 * One person has too many unpaid invoices open.
 *
 * Benign wording on purpose: the cap exists to stop the collection filling
 * with abandoned taps, and the person who hits it is usually somebody who
 * tapped buy four times, not an attacker.
 */
export const TOO_MANY_OPEN = [
  "You have a few invoices still open. Pay or dismiss one of those first,",
  "then try again.",
].join("\n");

/**
 * Selling is switched off because the deployment has no node tag.
 *
 * Checked before an invoice is sent, never after the money arrives: half
 * of what the customer needs is unavailable, so the only correct thing to
 * do is refuse the sale.
 */
export const CANNOT_SELL = [
  "Purchases are temporarily unavailable. Nothing has been charged.",
  "Please try again a little later.",
].join("\n");

// ── Delivery ────────────────────────────────────────────────────────────

/**
 * The message the whole system exists to send.
 *
 * Both variables, both labelled, each in its own <code> block so a phone
 * copies one without the other's whitespace. The sentence about needing
 * both is not decoration: a customer who sets only the key gets a pod that
 * exits with code 2 and no explanation, and that failure looks exactly
 * like "the licence I paid for does not work".
 */
export function delivered({ key, nodeTag, planName, seats, expiresAt }) {
  return [
    "✅ Payment received. Your licence is ready.",
    "",
    "Set <b>both</b> of these on the machine you run the app on — it will",
    "not start with only the key:",
    "",
    "<b>KREA2_LICENSE_KEY</b>",
    `<code>${escape(key)}</code>`,
    "",
    "<b>KREA2_NODE_TAG</b>",
    `<code>${escape(nodeTag)}</code>`,
    "",
    `<b>${escape(planName)}</b> · ${seats} ${seats === 1 ? "machine" : "machines"} ` +
      `at a time · runs until ${day(expiresAt)}`,
    "",
    "Tap a value to copy it. /mykeys shows this again whenever you need it.",
  ].join("\n");
}

/** A renewal: same key, more time. */
export function renewed({ key, planName, expiresAt, previousExpiry }) {
  return [
    "✅ Payment received. Your licence is renewed.",
    "",
    `<b>${escape(planName)}</b> · runs until ${day(expiresAt)}` +
      (previousExpiry ? ` (was ${day(previousExpiry)})` : ""),
    "",
    "<b>KREA2_LICENSE_KEY</b>",
    `<code>${escape(key)}</code>`,
    "",
    "Your key has not changed, so there is nothing to update on your machine.",
  ].join("\n");
}

/**
 * An upgrade: same key, new tier, and the restart warning.
 *
 * The warning is mandatory. licensing.py notices the plan change on the
 * next heartbeat and logs that a restart is needed, but deliberately does
 * not apply it — tabs are built once at launch and a newly granted tab has
 * no model files on disk. Without this paragraph the customer pays, sees
 * nothing change, and opens a support conversation.
 */
export function upgraded({ key, planName, expiresAt }) {
  return [
    `✅ Payment received. You are now on <b>${escape(planName)}</b>.`,
    "",
    `Runs until ${day(expiresAt)}. Your key has not changed:`,
    `<code>${escape(key)}</code>`,
    "",
    "⚠️ <b>Restart the app to get the new tabs.</b> A machine that is already",
    "running keeps the old ones until it restarts — tabs are built when the",
    "app starts, and a newly granted one has no model files downloaded yet.",
  ].join("\n");
}

// ── When something goes wrong after the money ───────────────────────────

/**
 * Paid, but provisioning failed.
 *
 * Says the two things that are true and useful: the money is not lost, and
 * a human will not have to be chased for it. It does not apologise at
 * length or invite a support ticket, because the sweep will usually have
 * fixed this before the customer finishes reading.
 */
export const PROVISION_FAILED = [
  "✅ Your payment went through — thank you.",
  "",
  "Issuing the key itself hit a problem, which we can see and are retrying.",
  "Your key will arrive in this chat shortly. Nothing has been lost, and you",
  "do not need to pay again.",
].join("\n");

/**
 * The amount charged is not the amount quoted.
 *
 * Deliberately different wording from PROVISION_FAILED: this one is not
 * self-healing, no licence is written, and it needs a person. It should be
 * unreachable — pre_checkout refuses a mismatch before the charge — so
 * seeing it means something is wrong that a retry would not fix.
 */
export const AMOUNT_MISMATCH = [
  "✅ Your payment went through, but it does not match the invoice we sent,",
  "so we have held it for review rather than issuing a key automatically.",
  "",
  "Nothing has been lost. Please contact support and quote this chat.",
].join("\n");

/** pre_checkout refusals. Telegram shows these verbatim in the sheet. */
export const CHECKOUT_EXPIRED =
  "This invoice is no longer valid. Send /buy for a fresh one.";
export const CHECKOUT_MISMATCH =
  "The price has changed since this invoice was created. Send /buy for a " +
  "fresh one.";
export const CHECKOUT_UNAVAILABLE =
  "We cannot complete this purchase right now. You have not been charged.";

// ── Keys you already have ───────────────────────────────────────────────

export const NO_KEYS = [
  "You do not have a licence yet.",
  "",
  "/plans — what is available",
  "/buy — buy one",
].join("\n");

/** One licence in the /mykeys list. */
export function keyBlock({ key, planName, seats, expiresAt, active, nodeTag }) {
  const lines = [
    `<b>${escape(planName)}</b>` + (active ? "" : "  ·  revoked"),
    "<b>KREA2_LICENSE_KEY</b>",
    `<code>${escape(key)}</code>`,
    "<b>KREA2_NODE_TAG</b>",
    `<code>${escape(nodeTag)}</code>`,
    `${seats} ${seats === 1 ? "machine" : "machines"} at a time · ` +
      (expiresAt ? `runs until ${day(expiresAt)}` : "no expiry"),
  ];
  return lines.join("\n");
}

export const RENEW_NO_KEY = [
  "There is nothing to renew — you do not have a licence yet.",
  "",
  "/buy to get one.",
].join("\n");

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
  "/buy — buy or renew a licence",
  "/mykeys — the keys you already have",
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
