// The webhook. Authenticate, dispatch, answer 200 — in that order, always.
//
// ── Why this always answers 200 ────────────────────────────────────────
//
// Telegram retries any non-2xx with backoff for up to 24 hours. That is
// the design's queue and its durability, and it is exactly what you want
// *before* a payment — but once a `successful_payment` has been recorded,
// a non-2xx asks Telegram to send the money event again, and the only
// thing standing between that and a second licence is the order state
// machine. So the rule here is that anything past authentication gets a
// 200: an internal failure is recorded in the order status and the log,
// where a human and the sweep can both see it, and never signalled by the
// status code.
//
// The one exception is authentication itself, which answers 404 — see
// below.
//
// ── Two independent checks, one configured secret ──────────────────────
//
// 1. A long random path segment. Anyone who does not have it gets the
//    service's ordinary 404 and learns nothing, including whether a bot
//    exists here at all.
// 2. `X-Telegram-Bot-Api-Secret-Token`, which Telegram sends on every
//    update because setWebhook was given a `secret_token`.
//
// Both are compared with crypto.timingSafeEqual, over SHA-256 digests
// rather than the raw values: timingSafeEqual throws on a length mismatch,
// and hashing first makes both sides a fixed 32 bytes, so a wrong-length
// guess is rejected by the same constant-time comparison as a wrong-value
// one rather than by an exception. It is the same discipline api.py
// already applies to its process token with secrets.compare_digest.
//
// The path is derived from the secret rather than configured separately.
// One value to set and rotate, but two independent checks, because the
// derivation is one-way: the path is written to proxy logs, access logs
// and `getWebhookInfo` output, and none of those reveal the header token
// an attacker would also need. Rotating TELEGRAM_WEBHOOK_SECRET moves the
// path too, which is the correct behaviour — the old URL stops working the
// moment the old header does.
//
// ── Unconfigured is a supported state ──────────────────────────────────
//
// With no bot token or no webhook secret, nothing here is reachable: the
// route answers 404 exactly as /v1/admin/* does without an ADMIN_TOKEN.
// That is what makes deploying this code to the existing service a no-op
// until the environment variables are set — the property the whole project
// is measured against.

import { createHash, timingSafeEqual } from "node:crypto";

import express from "express";

import { TELEGRAM_WEBHOOK_SECRET } from "../config.js";

import { botConfigured } from "./bot.js";
import { handleCallbackQuery, handleMessage } from "./commands.js";
import {
  handlePreCheckoutQuery,
  handleSuccessfulPayment,
} from "./payments.js";

const router = express.Router();

// Its own parser, at a limit of its own. The global express.json in
// app.js is capped at 16kb, and that cap is a deliberate bound on what a
// pod may submit to /v1/prompts — raising it to fit a Telegram update
// would loosen an unrelated endpoint. An update with a long caption and a
// callback payload can approach 16kb, and the failure would be an opaque
// 400 that Telegram then retries for a day.
router.use(express.json({ limit: "64kb" }));

/** Constant-time equality for two secrets of any length. */
function secretsMatch(received, expected) {
  const a = createHash("sha256").update(String(received ?? "")).digest();
  const b = createHash("sha256").update(String(expected ?? "")).digest();
  return timingSafeEqual(a, b);
}

/** Is there a bot token and a webhook secret? */
export function webhookConfigured() {
  return botConfigured() && TELEGRAM_WEBHOOK_SECRET !== "";
}

/**
 * The random-looking path segment the webhook lives at.
 *
 * A one-way function of the secret, domain-separated so that it can never
 * collide with any other value derived from the same secret later. 32 hex
 * characters — long enough that it cannot be found by guessing, short
 * enough to read back from getWebhookInfo.
 */
export function webhookPath() {
  return createHash("sha256")
    .update(`telegram-webhook-path:${TELEGRAM_WEBHOOK_SECRET}`)
    .digest("hex")
    .slice(0, 32);
}

/** The full URL to register, given the deployment's base. */
export function webhookUrl(baseUrl) {
  return `${String(baseUrl).replace(/\/+$/, "")}/tg/webhook/${webhookPath()}`;
}

/**
 * Which update types the router understands.
 *
 * Handed to setWebhook as `allowed_updates`, so Telegram never delivers
 * anything else and the dispatch below cannot be reached by an update type
 * nothing here handles. Kept beside the dispatch it describes, so the two
 * cannot drift.
 */
export const ALLOWED_UPDATES = Object.freeze([
  "message",
  "callback_query",
  "pre_checkout_query",
]);

router.post("/webhook/:path", async (req, res) => {
  if (!webhookConfigured()) {
    return res.status(404).json({ ok: false, error: "not_found" });
  }

  // 404 rather than 401 or 403, for both checks. A wrong path and a wrong
  // token are answered identically to a path that does not exist, so
  // probing tells an attacker nothing — not that the URL was right, and
  // not that a bot is deployed here.
  const pathOk = secretsMatch(req.params.path, webhookPath());
  const headerOk = secretsMatch(
    req.get("x-telegram-bot-api-secret-token"),
    TELEGRAM_WEBHOOK_SECRET,
  );
  if (!pathOk || !headerOk) {
    // Which of the two failed is logged, because in practice this is how
    // a half-finished setWebhook is diagnosed. The values are not.
    console.warn(
      `tg reject path=${pathOk ? "ok" : "bad"} secret=${headerOk ? "ok" : "bad"}`,
    );
    return res.status(404).json({ ok: false, error: "not_found" });
  }

  // Past this line every path answers 200.
  try {
    const update = req.body || {};
    // Order matters. A successful payment arrives as an ordinary `message`
    // with a `successful_payment` on it, so it must be checked before the
    // command handler — which would otherwise see a message with no text
    // and answer it with the help blurb while the money went unrecorded.
    if (update.message?.successful_payment) {
      await handleSuccessfulPayment(update.message);
    } else if (update.message) {
      await handleMessage(update.message);
    } else if (update.callback_query) {
      await handleCallbackQuery(update.callback_query);
    } else if (update.pre_checkout_query) {
      await handlePreCheckoutQuery(update.pre_checkout_query);
    } else {
      // The key names only — never the update itself, which carries the
      // customer's name and whatever they typed.
      console.log(
        `tg skip   ${Object.keys(update).filter((k) => k !== "update_id").join(",") || "(empty)"}`,
      );
    }
  } catch (err) {
    // handleMessage catches its own failures, so reaching here means
    // something outside it threw. Recorded, not signalled: a 500 would ask
    // Telegram to send this update again for the next 24 hours.
    console.error("tg error  update handling failed:", err.message);
  }

  res.json({ ok: true });
});

export default router;
