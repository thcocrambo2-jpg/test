// The Telegram Bot API, over global fetch. Transport only.
//
// This module knows how to *call* Telegram and how to classify what comes
// back. It knows nothing about orders, licences, plans or copy — those sit
// above it, so every one of them can be tested without a network and this
// can be exercised against a real bot with no database at all.
//
// ── Why there is no library here ────────────────────────────────────────
//
// The whole surface this project needs is six methods of one HTTP API that
// takes JSON and returns JSON. Node 20 has `fetch`, so a dependency would
// buy polling loops, middleware, scene managers and a session store that
// this design deliberately does not use — Telegram's own retry is the
// queue and Mongo is the state — in exchange for another package on the
// serverless cold path and another thing to audit. The service has four
// dependencies today. This adds none.
//
// ── The three outcomes a caller must tell apart ─────────────────────────
//
// Every failure here is one of three things, and the difference decides
// whether a paid customer gets their key:
//
//   blocked    Telegram answers 403 — the customer blocked the bot, or
//              their account is gone. Retrying achieves nothing, ever.
//              The licence is still valid and still theirs; the order
//              stops at PROVISIONED and /mykeys serves it when they come
//              back. NOT a provisioning failure.
//   retryable  a timeout, a 429, a 5xx, or fetch itself throwing. The
//              message may well arrive on the next attempt, so the sweep
//              should pick it up.
//   permanent  a 400. The request was wrong — bad chat id, malformed
//              markup, a payload Telegram refuses. Retrying re-sends the
//              same wrong request, so this needs a human, and the log line
//              is how they find out.
//
// ── The token ──────────────────────────────────────────────────────────
//
// The bot token is the bot's entire identity: whoever holds it can read
// everything sent to the bot and speak as it. It is in the URL of every
// call below, which is why nothing here ever puts a URL in an error, a log
// line or a thrown message — and why redact() scrubs it from any string
// that came from somewhere else before it is allowed out.

import { TELEGRAM_BOT_TOKEN } from "../config.js";

const API_ROOT = "https://api.telegram.org";

// Every call is inside a serverless invocation that a customer is waiting
// on, and one of them (answerPreCheckoutQuery) has a hard 10-second
// deadline of Telegram's own. A hung socket must fail fast enough to leave
// room to record what happened, rather than being killed mid-write when the
// function times out.
const TIMEOUT_MS = 8000;

/** Is the bot configured at all? Everything else no-ops without this. */
export function botConfigured() {
  return TELEGRAM_BOT_TOKEN !== "";
}

/** Remove the bot token from a string that is about to be logged. */
function redact(text) {
  const value = String(text ?? "");
  if (!TELEGRAM_BOT_TOKEN) return value;
  return value.split(TELEGRAM_BOT_TOKEN).join("<token>");
}

export class BotApiError extends Error {
  constructor(method, { status = 0, description = "", retryable = false,
                        blocked = false, retryAfter = null } = {}) {
    // The method, never the URL: the URL contains the token.
    super(`${method} failed: ${redact(description) || `http ${status}`}`);
    this.name = "BotApiError";
    this.method = method;
    this.status = status;
    this.description = redact(description);
    this.retryable = retryable;
    this.blocked = blocked;
    this.retryAfter = retryAfter;
  }
}

/**
 * One Bot API call.
 *
 * Returns the `result` field on success and throws BotApiError on anything
 * else, with the three flags above set. Telegram answers 200 with
 * `ok: false` for some errors and a 4xx for others, so both shapes are
 * folded into the same classification rather than trusting the status code
 * alone.
 */
async function call(method, payload = {}) {
  if (!botConfigured()) {
    throw new BotApiError(method, {
      description: "TELEGRAM_BOT_TOKEN is not set",
    });
  }

  let response;
  let body;
  try {
    response = await fetch(`${API_ROOT}/bot${TELEGRAM_BOT_TOKEN}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    body = await response.json();
  } catch (err) {
    // A transport failure, a timeout, or a response that was not JSON.
    // Always retryable: none of them says the request was wrong.
    throw new BotApiError(method, {
      description: redact(err?.message || String(err)),
      retryable: true,
    });
  }

  if (response.ok && body?.ok === true) return body.result;

  const status = body?.error_code || response.status;
  const description = body?.description || `http ${status}`;
  throw new BotApiError(method, {
    status,
    description,
    // 403 is "the customer blocked the bot" or "the account is gone".
    blocked: status === 403,
    retryable: status === 429 || status >= 500,
    retryAfter: body?.parameters?.retry_after ?? null,
  });
}

/**
 * Send a message.
 *
 * HTML by default, because the delivery message wraps the licence key in
 * <code> — which is what makes it one tap to copy on a phone, and this is
 * a product whose customers are on phones. Everything interpolated into a
 * message must go through copy.js's escape(); a plan description is edited
 * in Atlas, and an unescaped "&" there would make Telegram reject the
 * whole message rather than render it oddly.
 *
 * Previews are off: the only links the bot sends are a support handle and
 * an invoice, and an unfurled card under either is noise.
 */
export async function sendMessage(chatId, text, extra = {}) {
  return call("sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    ...extra,
  });
}

/**
 * Send a Telegram Stars invoice.
 *
 * Stars invoices are the one case with **no provider token** — the field
 * is omitted entirely rather than sent empty, and the currency is `XTR`.
 * `amount` is a whole number of Stars: there are no minor units here, so
 * unlike every other Telegram currency it is not multiplied by 100.
 *
 * `payload` is the order id and comes back verbatim on both
 * `pre_checkout_query` and `successful_payment`. It is the only thing tying
 * a payment to what was bought, which is why the order document is written
 * before the invoice is sent and never derived from anything the customer
 * can type.
 */
export async function sendInvoice({ chatId, title, description, payload,
                                    amount, extra = {} }) {
  return call("sendInvoice", {
    chat_id: chatId,
    title,
    description,
    payload,
    currency: "XTR",
    prices: [{ label: title, amount }],
    ...extra,
  });
}

/**
 * Answer a pre-checkout query — the last moment the charge can be refused.
 *
 * Telegram gives this **ten seconds**. Miss the deadline and the customer
 * sees the payment fail with nothing explaining why, so the caller does one
 * Mongo read and one comparison before calling this and nothing else.
 *
 * `ok: false` requires a message, and the customer reads it verbatim.
 */
export async function answerPreCheckoutQuery(queryId, ok, errorMessage) {
  return call("answerPreCheckoutQuery", {
    pre_checkout_query_id: queryId,
    ok,
    ...(ok ? {} : { error_message: errorMessage }),
  });
}

/** Acknowledge a tapped inline button, so its spinner stops. */
export async function answerCallbackQuery(queryId, text) {
  return call("answerCallbackQuery", {
    callback_query_id: queryId,
    ...(text ? { text } : {}),
  });
}

/** Who this token belongs to. Used by scripts/set-webhook.js to confirm. */
export async function getMe() {
  return call("getMe");
}

/**
 * Point Telegram at a URL.
 *
 * `secret_token` is what Telegram then sends back in
 * X-Telegram-Bot-Api-Secret-Token on every update — half of the webhook's
 * authentication, the other half being the random path in `url`.
 *
 * `allowed_updates` is an allowlist, and a deliberately short one: an
 * update type that is not on it is never delivered, so the router cannot be
 * reached by anything the bot has no handler for.
 */
export async function setWebhook({ url, secretToken, allowedUpdates,
                                   dropPendingUpdates = false }) {
  return call("setWebhook", {
    url,
    secret_token: secretToken,
    allowed_updates: allowedUpdates,
    drop_pending_updates: dropPendingUpdates,
    max_connections: 40,
  });
}

/** Stop delivery. Telegram keeps queuing updates unless told to drop them. */
export async function deleteWebhook({ dropPendingUpdates = false } = {}) {
  return call("deleteWebhook", { drop_pending_updates: dropPendingUpdates });
}

/** What Telegram thinks the webhook is, including its last error. */
export async function getWebhookInfo() {
  return call("getWebhookInfo");
}
