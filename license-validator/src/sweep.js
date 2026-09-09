// The recovery pass. The only background processing in the design.
//
// Every action it takes is one the webhook would have taken anyway — it
// exists because the webhook can be interrupted between two of its steps
// (the function is killed, Atlas blips, the Bot API times out) and the
// order is then durably correct but unfinished. Nothing here is a second
// implementation of a purchase: it calls the same fulfillOrder() and
// deliverOrder() the payment handler does, so a retried order and a
// first-time order go through identical code.
//
// ── Why it is safe to run at any time, twice at once ───────────────────
//
// Every write it can cause is a conditional transition in orders.js that
// names the status it must come from. Two sweeps racing, or a sweep racing
// the webhook, means one of them gets the document and the other gets null
// and stops. That is also why there is no lock, no lease and no "claimed"
// flag — see the header of orders.js.
//
// ── Why orders must be a few minutes old to be swept ───────────────────
//
// The webhook is usually still working on an order that is seconds old.
// Sweeping it immediately would mean two invocations provisioning the same
// purchase at once; they would not both succeed — the conditional update
// prevents that — but the loser would log a failure that never happened.
// Five minutes is comfortably longer than the webhook's whole path and
// short enough that a customer whose delivery failed is not left wondering.
//
// ── Why attempts are capped ────────────────────────────────────────────
//
// An order that cannot be provisioned — a plan deleted from under it, an
// amount that does not match — will fail identically every time. Without a
// cap the sweep would retry it every ten minutes forever, burying the
// orders that can still be fixed. After five attempts it stops and waits
// for a person, which is what `npm run orders -- --list` is for.

import {
  ORDER_STATUS,
  markExpiredUnpaid,
} from "./orders.js";
import { collections } from "./db.js";
import { deliverOrder, fulfillOrder } from "./telegram/payments.js";

/** How settled an order must be before the sweep touches it. */
const MIN_AGE_MS = 5 * 60 * 1000;

/** After this many failed provisioning attempts, a person is needed. */
const MAX_PROVISION_ATTEMPTS = 5;

/**
 * How long an unpaid invoice stays open.
 *
 * Cosmetic: it clears the customer's open-order cap and stops an abandoned
 * tap sitting in the list forever. Telegram invoices do not expire on their
 * own, but pre_checkout refuses anything that is not INVOICED, so an order
 * closed here cannot later be paid.
 */
const INVOICE_TTL_MS = 24 * 60 * 60 * 1000;

/**
 * How many orders one run will touch, per pass.
 *
 * A serverless invocation has a wall clock, and a backlog is better worked
 * through over several runs than abandoned halfway through one. The cron
 * fires every fifteen minutes; anything not reached is reached next time.
 */
const BATCH = 25;

/**
 * One recovery pass.
 *
 * Returns a summary rather than logging only, so the cron route can answer
 * with it and `npm run orders -- --sweep` can print it.
 */
export async function sweep({ now = new Date() } = {}) {
  const { orders } = await collections();
  const settled = new Date(now.getTime() - MIN_AGE_MS);
  const summary = { provisioned: 0, delivered: 0, expired: 0, failed: 0 };

  // ── Paid, but no licence yet ─────────────────────────────────────────
  //
  // The important one: these are customers who have been charged. Includes
  // PAID orders the webhook never got to, and FAILED_PROVISION orders whose
  // cause may since have cleared.
  const stuck = await orders
    .find({
      status: { $in: [ORDER_STATUS.PAID, ORDER_STATUS.FAILED_PROVISION] },
      updated_at: { $lt: settled },
      provision_attempts: { $lt: MAX_PROVISION_ATTEMPTS },
    })
    .sort({ updated_at: 1 })
    .limit(BATCH)
    .toArray();

  for (const order of stuck) {
    try {
      const done = await fulfillOrder(order);
      if (done) summary.provisioned += 1;
      else summary.failed += 1;
    } catch (err) {
      // fulfillOrder records its own failures; reaching here means
      // something outside it threw, and one bad order must not stop the
      // pass for the rest.
      summary.failed += 1;
      console.error(`sweep    ${order._id.slice(0, 8)} threw — ${err.message}`);
    }
  }

  // ── Provisioned, but the message never arrived ───────────────────────
  //
  // The licence is valid and the customer does not know. Note that a
  // customer who blocked the bot stays here permanently, which is correct:
  // there is nothing to deliver to, and /mykeys serves them if they return.
  const undelivered = await orders
    .find({
      status: ORDER_STATUS.PROVISIONED,
      updated_at: { $lt: settled },
    })
    .sort({ updated_at: 1 })
    .limit(BATCH)
    .toArray();

  for (const order of undelivered) {
    try {
      if (await deliverOrder(order)) summary.delivered += 1;
    } catch (err) {
      console.error(`sweep    ${order._id.slice(0, 8)} threw — ${err.message}`);
    }
  }

  // ── Invoices nobody paid ─────────────────────────────────────────────
  const abandoned = await orders
    .find({
      status: { $in: [ORDER_STATUS.CREATED, ORDER_STATUS.INVOICED] },
      created_at: { $lt: new Date(now.getTime() - INVOICE_TTL_MS) },
    })
    .limit(BATCH)
    .toArray();

  for (const order of abandoned) {
    if (await markExpiredUnpaid(order._id)) summary.expired += 1;
  }

  // One aligned line, like everything else in the service. Logged even when
  // nothing happened, because "the sweep ran and found nothing" and "the
  // cron is not firing" look identical otherwise.
  console.log(
    `sweep    provisioned=${summary.provisioned} delivered=${summary.delivered} ` +
      `expired=${summary.expired} failed=${summary.failed}`,
  );
  return summary;
}
