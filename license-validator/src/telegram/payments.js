// Payments: the pre-checkout gate, and what happens after the money.
//
// This is the only file in the project that both moves an order and writes
// a licence, and it does the two in a fixed order that the rest of the
// design depends on:
//
//   1. record the charge          markPaid()      — durable, atomic, first
//   2. compare what was charged   against amount_stars
//   3. write the licence          provision.js
//   4. record that we did         markProvisioned()
//   5. tell the customer          sendMessage()
//   6. record that too            markDelivered()
//
// Money is committed to the database before any licence work begins, so a
// purchase cannot be lost by anything that happens in steps 3-6: the order
// carries the charge id and the intent, and the sweep or the CLI can
// finish it later. Every step after 1 is idempotent, because each is a
// conditional transition that matches nothing the second time.
//
// ── Fail closed before the money, fail open after it ───────────────────
//
// Before the charge, refusing is free and being wrong is expensive: an
// unreachable database at pre-checkout means answering ok:false, so the
// customer is never charged for something we cannot deliver.
//
// After the charge, refusing is the expensive answer: Telegram retries a
// non-2xx for 24 hours, and the money already moved. So everything past
// markPaid records the failure and answers 200 — the order status and the
// log carry the real outcome, and the sweep retries.
//
// ── Why the amount is compared here and not in orders.js ───────────────
//
// orders.js stores `amount_stars` (what was quoted, captured when the
// invoice was sent) and `paid_amount` (what Telegram says was charged) and
// deliberately compares neither: it is a state machine over documents, and
// what counts as an acceptable payment is a policy question. The policy is
// here, and it is exact equality. A mismatch is FAILED_PROVISION with no
// licence written — pre_checkout should have caught it, so reaching this
// means something is wrong that a retry will not fix.
//
// ── Why a customer is never messaged twice about one failure ───────────
//
// The sweep re-runs fulfillOrder() on a failed order up to five times.
// Without care that is five identical "something went wrong" messages for
// one problem, which reads as five problems. Only the first attempt
// notifies; the retries are silent and land, when they succeed, as the key
// itself.

import { collections } from "../db.js";
import { KREA2_NODE_TAG } from "../config.js";
import {
  ORDER_STATUS,
  findOrder,
  markDelivered,
  markFailedProvision,
  markPaid,
  markProvisioned,
} from "../orders.js";
import { getPlan } from "../plans.js";
import {
  changePlan,
  createLicense,
  findLicense,
  renewLicense,
} from "../provision.js";

import { answerPreCheckoutQuery, sendMessage } from "./bot.js";
import * as copy from "./copy.js";

/** The three things an order can be for. */
export const INTENT = Object.freeze({
  NEW: "new",
  RENEW: "renew",
  UPGRADE: "upgrade",
});

/** Enough of an order id to read in a log line. */
function short(orderId) {
  return String(orderId).slice(0, 8);
}

/**
 * The last moment a charge can be refused, and it has a ten-second
 * deadline of Telegram's own.
 *
 * One read, four comparisons, one answer — nothing else belongs in here.
 * Miss the deadline and the customer sees the payment fail with nothing
 * explaining why.
 *
 * Anything thrown becomes ok:false rather than a 500, because a database
 * we cannot reach is a licence we cannot issue, and charging for that is
 * strictly worse than a customer retrying in a minute.
 */
export async function handlePreCheckoutQuery(query) {
  const payload = query?.invoice_payload;
  const userId = query?.from?.id;
  let ok = false;
  let reason = copy.CHECKOUT_UNAVAILABLE;
  let why = "unknown";

  try {
    const order = await findOrder(payload);
    if (!order) {
      why = "no such order";
      reason = copy.CHECKOUT_EXPIRED;
    } else if (order.status !== ORDER_STATUS.INVOICED) {
      // Already paid, already provisioned, or swept as abandoned. A second
      // payment for one invoice is exactly what this refuses.
      why = `status ${order.status}`;
      reason = copy.CHECKOUT_EXPIRED;
    } else if (order.telegram_user_id !== userId) {
      // The payload round-trips through the customer's client, so the
      // order it names is checked against who is paying.
      why = "wrong user";
      reason = copy.CHECKOUT_EXPIRED;
    } else if (query.currency !== "XTR") {
      why = `currency ${query.currency}`;
      reason = copy.CHECKOUT_MISMATCH;
    } else if (query.total_amount !== order.amount_stars) {
      why = `amount ${query.total_amount} != ${order.amount_stars}`;
      reason = copy.CHECKOUT_MISMATCH;
    } else {
      ok = true;
      why = "ok";
    }
  } catch (err) {
    // Fail closed: no charge is better than a charge we cannot honour.
    console.error(`checkout ${short(payload)}  refused — ${err.message}`);
    ok = false;
    reason = copy.CHECKOUT_UNAVAILABLE;
    why = "threw";
  }

  console.log(`checkout ${short(payload)}  ${ok ? "ALLOW" : "REFUSE"}  ${why}`);

  // The answer is the last thing, and its failure does not change the
  // decision that was made. If Telegram cannot be reached the charge does
  // not happen — the ten-second deadline passes and the payment sheet
  // fails — which is the same outcome as refusing, so there is nothing to
  // undo here.
  try {
    await answerPreCheckoutQuery(query.id, ok, reason);
  } catch (err) {
    console.error(`checkout ${short(payload)}  could not answer — ${err.message}`);
  }

  // Returned so the decision can be asserted on without a live Bot API.
  return { ok, why };
}

/**
 * A payment landed. Record it, then fulfil it.
 *
 * markPaid is the fence: it returns null for a replay — the same
 * successful_payment delivered twice — and for a charge id the unique
 * index refuses to attach to a second order. Either way the answer is to
 * log it and stop, because whatever this update is asking for has already
 * happened.
 */
export async function handleSuccessfulPayment(message) {
  const payment = message.successful_payment;
  const orderId = payment.invoice_payload;

  const order = await markPaid({
    order_id: orderId,
    charge_id: payment.telegram_payment_charge_id,
    provider_charge_id: payment.provider_payment_charge_id ?? null,
    amount: payment.total_amount,
  });
  if (!order) return;

  await fulfillOrder(order);
}

/**
 * Turn a paid order into a licence and a message.
 *
 * Shared by the payment webhook, the sweep and `npm run orders -- --retry`,
 * so all three do exactly the same thing and a retry is not a second
 * implementation of a purchase. Safe to call on an order that is already
 * provisioned or already delivered: every write inside is a conditional
 * transition, and the ones that no longer apply return null.
 */
export async function fulfillOrder(order) {
  // Retries must not re-send the apology; the customer has had it once.
  const firstAttempt = (order.provision_attempts ?? 0) === 0;

  // The comparison the whole fence exists for. Exact equality: Stars are
  // integers, so there is no rounding to be tolerant of.
  if (order.paid_amount !== order.amount_stars) {
    const detail =
      `paid ${order.paid_amount} but quoted ${order.amount_stars}`;
    console.error(`order    ${short(order._id)}  AMOUNT MISMATCH  ${detail}`);
    await markFailedProvision({ order_id: order._id, error: detail });
    if (firstAttempt) await tell(order, copy.AMOUNT_MISMATCH);
    return null;
  }

  let result;
  try {
    result = await provisionFor(order);
  } catch (err) {
    console.error(`order    ${short(order._id)}  provisioning threw — ${err.message}`);
    await markFailedProvision({ order_id: order._id, error: err });
    if (firstAttempt) await tell(order, copy.PROVISION_FAILED);
    return null;
  }

  const moved = await markProvisioned({
    order_id: order._id,
    license_key: result.license.key,
    license_action: result.action,
    expires_at_after: result.license.expires_at ?? null,
  });
  // null means somebody else got there first — the sweep and the webhook
  // racing, most likely. The licence exists either way; leave delivery to
  // whoever won.
  if (!moved) return null;

  await deliverOrder(moved, result);
  return moved;
}

/**
 * Write the licence the order was bought for.
 *
 * Every branch goes through provision.js and nothing here builds a $set of
 * its own — which is what guarantees a Telegram-sold key is
 * byte-indistinguishable from a CLI-issued one, and that `plan_id` never
 * arrives without `features: null` beside it.
 */
async function provisionFor(order) {
  if (order.intent === INTENT.NEW) {
    const license = await createLicense({
      name: await customerName(order),
      plan_id: order.plan_id,
      seats: order.seats,
      months: order.months,
      telegram_user_id: order.telegram_user_id,
    });
    return { license, action: "created", previousExpiry: null };
  }

  if (order.intent === INTENT.RENEW) {
    // Read first, only so the message can say "was <date>". The renewal
    // itself extends from whatever is on the document at write time.
    const before = await findLicense(order.license_key);
    const license = await renewLicense({
      key: order.license_key,
      months: order.months,
    });
    return {
      license,
      action: "renewed",
      previousExpiry: before?.expires_at ?? null,
    };
  }

  if (order.intent === INTENT.UPGRADE) {
    const license = await changePlan({
      key: order.license_key,
      plan_id: order.plan_id,
      months: order.months,
    });
    return { license, action: "upgraded", previousExpiry: null };
  }

  throw new Error(`unknown order intent: ${order.intent}`);
}

/**
 * Send the customer what they paid for, and record that we did.
 *
 * A blocked customer is not a failure of the purchase: the licence is
 * valid and theirs, the order stays at PROVISIONED, and /mykeys serves it
 * whenever they come back. Anything else is left un-delivered for the
 * sweep to try again.
 */
export async function deliverOrder(order, result = null) {
  const license = result?.license ?? (await findLicense(order.license_key));
  if (!license) {
    console.error(
      `order    ${short(order._id)}  delivery has no licence to describe`,
    );
    return null;
  }

  const plan = await getPlan(license.plan_id);
  const planName = plan?.name || license.plan_id || "Krea 2";
  const action = order.license_action || result?.action || "created";

  let text;
  if (action === "renewed") {
    text = copy.renewed({
      key: license.key,
      planName,
      expiresAt: license.expires_at,
      previousExpiry: result?.previousExpiry ?? null,
    });
  } else if (action === "upgraded") {
    text = copy.upgraded({
      key: license.key,
      planName,
      expiresAt: license.expires_at,
    });
  } else {
    text = copy.delivered({
      key: license.key,
      nodeTag: KREA2_NODE_TAG,
      planName,
      seats: license.seats ?? 1,
      expiresAt: license.expires_at,
    });
  }

  try {
    await sendMessage(order.telegram_chat_id, text);
  } catch (err) {
    if (err?.blocked) {
      console.warn(
        `order    ${short(order._id)}  customer blocked the bot — licence ` +
          `is valid and waiting on /mykeys`,
      );
      await noteBlocked(order.telegram_user_id);
      return null;
    }
    console.error(`order    ${short(order._id)}  delivery failed — ${err.message}`);
    return null;
  }

  return markDelivered(order._id);
}

/** A short apology, best effort — never the reason a handler fails. */
async function tell(order, text) {
  try {
    await sendMessage(order.telegram_chat_id, text);
  } catch (err) {
    if (err?.blocked) await noteBlocked(order.telegram_user_id);
    console.error(`order    ${short(order._id)}  could not notify — ${err.message}`);
  }
}

/** Remember that the Bot API said 403 for this person. */
async function noteBlocked(telegramUserId) {
  const { telegram_users } = await collections();
  await telegram_users.updateOne(
    { _id: telegramUserId },
    { $set: { is_blocked: true, blocked_at: new Date() } },
  );
}

/**
 * A human-readable name for the licence, for the admin listing.
 *
 * The Telegram username when there is one, because "@acme_ops" is what you
 * would search for when they write in; the numeric id otherwise, which is
 * the only identifier that never changes.
 */
async function customerName(order) {
  const { telegram_users } = await collections();
  const user = await telegram_users.findOne({ _id: order.telegram_user_id });
  return user?.username
    ? `Telegram @${user.username}`
    : `Telegram ${order.telegram_user_id}`;
}
