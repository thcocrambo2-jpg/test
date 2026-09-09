// The order state machine — one document per purchase attempt, and the
// only reason a payment webhook can be received twice without selling
// anything twice.
//
//   CREATED ──▶ INVOICED ──▶ PAID ──▶ PROVISIONED ──▶ DELIVERED
//                             │
//                             └──▶ FAILED_PROVISION ──┐ (retry)
//                             ▲                       │
//                             └───────────────────────┘
//               INVOICED ─────────▶ EXPIRED_UNPAID    (swept, cosmetic)
//
// ── Why this is the whole of the idempotency mechanism ─────────────────
//
// Telegram retries a webhook with backoff for up to 24 hours on any
// non-2xx, so receiving the same `successful_payment` twice is ordinary
// traffic, not an edge case. Every transition below is therefore **one
// conditional update** whose filter names the status it must be coming
// from, returning the document on success and `null` when the filter
// matched nothing. `null` is not an error: it means somebody else already
// did this, and the caller logs it and answers 200.
//
// That is the entire design. There are no locks, no leases and no worker
// queue, because Telegram's own retry *is* the queue and Mongo's
// conditional update is the lock.
//
// ── Why there is deliberately no LICENSE_PROVISIONING state ────────────
//
// The obvious extra state — "provisioning, in flight" — cannot be made to
// work. A persisted in-flight marker cannot tell "running right now" from
// "the function was killed mid-write", so recovering from it needs a lease
// timeout, and a lease timeout on a step that takes forty milliseconds is a
// window in which either a paying customer waits for a stuck order or two
// invocations provision the same one. Instead PAID → PROVISIONED is itself
// the conditional update, which answers both cases with no clock involved.
//
// ── Three independent layers stop a charge being honoured twice ────────
//
//   1. The conditional update here — a second `successful_payment` for an
//      order that has left INVOICED matches nothing.
//   2. The unique sparse index on `telegram_payment_charge_id` (db.js) —
//      even if layer 1 were bypassed, two orders cannot record one charge.
//   3. Provisioning is gated on the order still being unprovisioned, so a
//      replay arriving after PROVISIONED cannot write a second licence.
//
// ── The import rule ────────────────────────────────────────────────────
//
// **This module must not import from `telegram/` or from `provision.js`.**
// It moves an order document between states and knows nothing about Star
// invoices, chat ids or licence keys beyond storing the ones it is handed.
// The orchestration that calls both this and provision.js lives above
// them, which is what keeps either usable — and testable — without the
// other.

import { randomUUID } from "node:crypto";

import { collections } from "./db.js";

export const ORDER_STATUS = Object.freeze({
  CREATED: "CREATED",
  INVOICED: "INVOICED",
  PAID: "PAID",
  PROVISIONED: "PROVISIONED",
  DELIVERED: "DELIVERED",
  FAILED_PROVISION: "FAILED_PROVISION",
  EXPIRED_UNPAID: "EXPIRED_UNPAID",
});

/** The statuses an order can still be worked on from. */
export const OPEN_STATUSES = Object.freeze([
  ORDER_STATUS.CREATED,
  ORDER_STATUS.INVOICED,
]);

/** Enough of an order id to read in a log line without carrying the rest. */
function short(orderId) {
  return String(orderId).slice(0, 8);
}

/**
 * One conditional transition: move an order from one of `from` to `to`.
 *
 * Returns the updated document, or null when the order was not in a status
 * this transition applies to — which on this path almost always means a
 * duplicate delivery from Telegram rather than a fault.
 */
async function transition(orderId, from, to, set = {}, extra = {}) {
  const { orders } = await collections();
  const now = new Date();
  return orders.findOneAndUpdate(
    { _id: orderId, status: { $in: from } },
    { $set: { status: to, updated_at: now, ...set }, ...extra },
    { returnDocument: "after" },
  );
}

/**
 * Open an order. Nothing has been shown to the customer yet.
 *
 * `_id` is a uuid and *is* the invoice payload: Telegram echoes
 * `invoice_payload` back verbatim on both `pre_checkout_query` and
 * `successful_payment`, so making it the primary key means every lookup on
 * the payment path is an `_id` hit with no second identifier to keep in
 * step. A uuid rather than a sequence because it round-trips through a
 * third party and must not be enumerable.
 *
 * `amount_stars` is captured here, at the moment the customer was shown a
 * price, and never recomputed — a plan repriced between the invoice being
 * sent and paid must not change what this order is worth.
 */
export async function createOrder({
  telegram_user_id,
  telegram_chat_id,
  intent,
  plan_id,
  cycle,
  months,
  amount_stars,
  seats = 1,
  license_key = null,
}) {
  const now = new Date();
  const doc = {
    _id: randomUUID(),
    telegram_user_id,
    telegram_chat_id,
    intent,
    plan_id,
    cycle,
    months,
    amount_stars,
    currency: "XTR",
    seats,
    status: ORDER_STATUS.CREATED,
    provider_payment_charge_id: null,
    paid_amount: null,
    license_action: null,
    expires_at_after: null,
    provision_attempts: 0,
    last_error: null,
    created_at: now,
    updated_at: now,
    invoiced_at: null,
    paid_at: null,
    provisioned_at: null,
    delivered_at: null,
  };
  // Two fields are left *absent* rather than written as null, because both
  // are covered by a sparse index and a null is a value a sparse index
  // still stores:
  //
  //   telegram_payment_charge_id  unique+sparse. Written as null on every
  //                               unpaid order, the second such order in
  //                               existence would fail to insert on a
  //                               duplicate key — an outage far worse than
  //                               the double-charge the index prevents.
  //   license_key                 only ever present once there is a key,
  //                               so "which order paid for this key" stays
  //                               a lookup over paid orders alone. A
  //                               renewal or upgrade names one on the way
  //                               in, so it can be there from the start.
  if (license_key != null) doc.license_key = license_key;

  const { orders } = await collections();
  await orders.insertOne(doc);
  console.log(
    `order    ${short(doc._id)}  CREATED    user=${telegram_user_id} ` +
      `${intent} ${plan_id} ${cycle} ${amount_stars} XTR`,
  );
  return doc;
}

export async function findOrder(orderId) {
  const { orders } = await collections();
  return orders.findOne({ _id: orderId });
}

/** The invoice reached the customer. */
export async function markInvoiced(orderId) {
  const order = await transition(
    orderId,
    [ORDER_STATUS.CREATED],
    ORDER_STATUS.INVOICED,
    { invoiced_at: new Date() },
  );
  console.log(
    order
      ? `order    ${short(orderId)}  INVOICED`
      : `order    ${short(orderId)}  invoice ignored (not CREATED)`,
  );
  return order;
}

/**
 * The money arrived. **This is the fence.**
 *
 * Committed before any licence work begins, so a purchase can never be
 * lost: whatever happens next, the charge and the intent are durably on
 * record and the order can be retried by hand or by the sweep.
 *
 * Returns null on a replay — the second delivery of the same
 * `successful_payment` matches nothing, and the caller answers 200.
 *
 * The duplicate-key case is the same answer for a different reason: the
 * unique sparse index refused to let this charge attach to a second order.
 * It is logged as a warning rather than a debug line because reaching it
 * means the status fence above was somehow passed, which should not happen
 * and is worth seeing.
 *
 * `amount` is what Telegram says was *actually* charged, kept separate
 * from `amount_stars` (what was quoted) so the two can be compared. This
 * function does not compare them — the caller does, before it decides
 * whether to provision.
 */
export async function markPaid({
  order_id,
  charge_id,
  provider_charge_id = null,
  amount = null,
}) {
  try {
    const order = await transition(
      order_id,
      [ORDER_STATUS.INVOICED],
      ORDER_STATUS.PAID,
      {
        telegram_payment_charge_id: charge_id,
        provider_payment_charge_id: provider_charge_id,
        paid_amount: amount,
        paid_at: new Date(),
      },
    );
    if (!order) {
      console.log(
        `order    ${short(order_id)}  payment ignored (not INVOICED) — replay`,
      );
      return null;
    }
    console.log(
      `order    ${short(order_id)}  PAID       ${amount} XTR ` +
        `quoted=${order.amount_stars}`,
    );
    return order;
  } catch (err) {
    if (err?.code === 11000) {
      console.warn(
        `order    ${short(order_id)}  charge already recorded elsewhere — ` +
          `refusing to honour it twice`,
      );
      return null;
    }
    throw err;
  }
}

/**
 * A licence now exists (or has been extended) for this order.
 *
 * Reachable from FAILED_PROVISION as well as PAID, because that is what
 * makes a retry able to finish; what it is *not* reachable from is
 * PROVISIONED or DELIVERED, which is the layer that stops a replay writing
 * a second licence.
 *
 * `last_error` is cleared, so a retried order does not keep displaying the
 * failure it recovered from.
 */
export async function markProvisioned({
  order_id,
  license_key,
  license_action,
  expires_at_after = null,
}) {
  const order = await transition(
    order_id,
    [ORDER_STATUS.PAID, ORDER_STATUS.FAILED_PROVISION],
    ORDER_STATUS.PROVISIONED,
    {
      license_key,
      license_action,
      expires_at_after,
      last_error: null,
      provisioned_at: new Date(),
    },
  );
  console.log(
    order
      ? `order    ${short(order_id)}  PROVISIONED ${license_action} ${license_key}`
      : `order    ${short(order_id)}  provision ignored (already past PAID)`,
  );
  return order;
}

/** The customer has been sent their key and node tag. */
export async function markDelivered(orderId) {
  const order = await transition(
    orderId,
    [ORDER_STATUS.PROVISIONED],
    ORDER_STATUS.DELIVERED,
    { delivered_at: new Date() },
  );
  console.log(
    order
      ? `order    ${short(orderId)}  DELIVERED`
      : `order    ${short(orderId)}  delivery ignored (not PROVISIONED)`,
  );
  return order;
}

/**
 * Provisioning failed on a paid order. Retryable, and counted.
 *
 * The customer keeps their money's worth: the order stays paid, carries
 * the reason, and the sweep or `npm run orders -- --retry` picks it up.
 * `provision_attempts` is what stops a permanently broken order being
 * retried forever.
 */
export async function markFailedProvision({ order_id, error }) {
  const reason = String(error?.message ?? error ?? "unknown").slice(0, 500);
  const order = await transition(
    order_id,
    [ORDER_STATUS.PAID, ORDER_STATUS.FAILED_PROVISION],
    ORDER_STATUS.FAILED_PROVISION,
    { last_error: reason },
    { $inc: { provision_attempts: 1 } },
  );
  console.error(
    order
      ? `order    ${short(order_id)}  FAILED_PROVISION attempt=` +
          `${order.provision_attempts}  ${reason}`
      : `order    ${short(order_id)}  failure ignored (already past PAID)`,
  );
  return order;
}

/**
 * An invoice nobody paid. Cosmetic only — it exists so the open-order cap
 * and the customer's own history are not clogged by abandoned taps.
 *
 * Never reachable from PAID: money never expires.
 */
export async function markExpiredUnpaid(orderId) {
  return transition(orderId, OPEN_STATUSES, ORDER_STATUS.EXPIRED_UNPAID);
}

/**
 * How many invoices this customer has open.
 *
 * The cap the caller applies with this is the same shape as the existing
 * MAX_PENDING_PER_LICENSE cap on prompt submissions: check before writing,
 * and answer benignly rather than with an error. It exists so one person
 * tapping "buy" repeatedly cannot fill the collection with CREATED rows.
 */
export async function countOpenOrders(telegramUserId) {
  const { orders } = await collections();
  return orders.countDocuments({
    telegram_user_id: telegramUserId,
    status: { $in: [...OPEN_STATUSES] },
  });
}
