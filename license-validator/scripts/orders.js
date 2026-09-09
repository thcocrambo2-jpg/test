// Look at orders, and finish the ones that did not finish themselves.
//
//   npm run orders                              # the 20 most recent
//   npm run orders -- --status FAILED_PROVISION
//   npm run orders -- --user 987654321
//   npm run orders -- --limit 50
//   npm run orders -- --show    <order-id>
//   npm run orders -- --retry   <order-id>      # provision + deliver
//   npm run orders -- --deliver <order-id>      # re-send the message only
//   npm run orders -- --sweep                   # one recovery pass, now
//
// ── Direct to Mongo, like every other script here ──────────────────────
//
// No admin token, no HTTP, no dependency on the deployment being up. The
// credential is MONGODB_URI from license-validator/.env, exactly as
// issue-key.js and presets.js work, which means this still functions when
// the thing you are trying to diagnose is the service itself.
//
// ── --retry is not a second implementation of a purchase ───────────────
//
// It calls the same fulfillOrder() the payment webhook calls, so a manual
// retry and an automatic one do identical work. That is also why it is
// safe to run twice: every write inside is a conditional transition that
// matches nothing the second time, so a retry on an order somebody else
// just fixed does nothing at all.
//
// It cannot double-charge and it cannot double-issue. What it cannot do
// either is fix an order that is failing for a real reason — an amount
// mismatch stays a mismatch — so read --show before reaching for it.

import { collections } from "../src/db.js";
import { ORDER_STATUS } from "../src/orders.js";
import { sweep } from "../src/sweep.js";
import { deliverOrder, fulfillOrder } from "../src/telegram/payments.js";

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) continue;
    const name = argv[i].slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[name] = true;
    else {
      out[name] = next;
      i += 1;
    }
  }
  return out;
}

const opts = args(process.argv.slice(2));

function when(value) {
  return value ? new Date(value).toISOString().slice(0, 16).replace("T", " ") : "—";
}

/** One line per order, aligned so a column of statuses reads as a column. */
function line(order) {
  return (
    `${String(order._id).slice(0, 8)}  ` +
    `${String(order.status).padEnd(16)} ` +
    `${String(order.intent).padEnd(7)} ` +
    `${String(order.plan_id).padEnd(8)} ` +
    `${String(order.amount_stars).padStart(5)} XTR  ` +
    `paid=${String(order.paid_amount ?? "—").padStart(5)}  ` +
    `user=${String(order.telegram_user_id).padEnd(12)} ` +
    `${when(order.created_at)}  ` +
    `${order.license_key || ""}`
  );
}

async function list() {
  const { orders } = await collections();
  const filter = {};
  if (typeof opts.status === "string") filter.status = opts.status.toUpperCase();
  if (typeof opts.user === "string") {
    filter.telegram_user_id = Number.parseInt(opts.user, 10);
  }
  const limit = Number.parseInt(opts.limit, 10) || 20;

  const rows = await orders
    .find(filter)
    .sort({ created_at: -1 })
    .limit(limit)
    .toArray();

  if (!rows.length) {
    console.log("no orders match.");
    return;
  }
  for (const row of rows) console.log(line(row));

  // A count per status under the listing: the one question you always ask
  // next is "how many are stuck?", and this answers it without a filter.
  const counts = await orders
    .aggregate([{ $group: { _id: "$status", n: { $sum: 1 } } }, { $sort: { _id: 1 } }])
    .toArray();
  console.log(
    "\n" + counts.map((row) => `${row._id}=${row.n}`).join("  "),
  );
}

async function show(orderId) {
  const { orders } = await collections();
  const order = await orders.findOne({ _id: orderId });
  if (!order) {
    console.error(`no order ${orderId}`);
    return 1;
  }
  console.log(JSON.stringify(order, null, 2));
  return 0;
}

async function retry(orderId) {
  const { orders } = await collections();
  const order = await orders.findOne({ _id: orderId });
  if (!order) {
    console.error(`no order ${orderId}`);
    return 1;
  }
  // Refused rather than attempted: fulfillOrder would treat an unpaid
  // order as one to provision, and nothing in this system should issue a
  // licence for money that never arrived.
  if (order.status !== ORDER_STATUS.PAID &&
      order.status !== ORDER_STATUS.FAILED_PROVISION) {
    console.error(
      `order is ${order.status}; --retry only applies to PAID or ` +
        `FAILED_PROVISION. Use --deliver to re-send a message.`,
    );
    return 1;
  }
  const done = await fulfillOrder(order);
  console.log(done ? "retried — see the lines above" : "not completed — see above");
  return done ? 0 : 1;
}

async function deliver(orderId) {
  const { orders } = await collections();
  const order = await orders.findOne({ _id: orderId });
  if (!order) {
    console.error(`no order ${orderId}`);
    return 1;
  }
  if (!order.license_key) {
    console.error("this order has no licence yet — use --retry first.");
    return 1;
  }
  const done = await deliverOrder(order);
  console.log(done ? "delivered" : "not delivered — see above");
  return done ? 0 : 1;
}

async function main() {
  if (typeof opts.show === "string") return show(opts.show);
  if (typeof opts.retry === "string") return retry(opts.retry);
  if (typeof opts.deliver === "string") return deliver(opts.deliver);
  if (opts.sweep) {
    await sweep();
    return 0;
  }
  await list();
  return 0;
}

process.exitCode = await main();

// Close the pooled client rather than calling process.exit(0).
//
// The other scripts here exit explicitly because a live MongoClient holds
// the event loop open. This one also talks to the Bot API, and calling
// process.exit() while fetch's sockets are still closing aborts the
// process with a libuv assertion and a non-zero code — a retry that
// worked, reported as a failure. Closing the one connection db.js caches
// lets the process end on its own instead.
await globalThis.__krea2License?.client?.close();
