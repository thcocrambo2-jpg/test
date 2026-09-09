// Licence provisioning — the only thing in this service that writes a
// licence document.
//
// Until now `scripts/issue-key.js` was the only writer, because licences
// were only ever issued by hand. A Telegram purchase has to do the same
// four things from a webhook, and the one outcome worth designing against
// is the CLI and the bot drifting apart: two implementations of "issue a
// key" means two answers to what a key looks like, and the one that is
// wrong is the one nobody runs by hand and therefore nobody sees.
//
// So the CLI now delegates here too, and this module owns the whole of it —
// key generation, term arithmetic, plan validation, and the document shape.
//
// ── What this deliberately is not ───────────────────────────────────────
//
// Not an HTTP route. There is no POST /internal/licenses/create. The only
// caller that would ever reach it lives in this same process, so a route
// would buy a publicly-reachable maximally-privileged endpoint, a secret to
// rotate, a replay window and a signature scheme, in exchange for a
// function call. It stays a module: plain arguments in, licence document
// out.
//
// Not a Telegram module either. **It must not import from `telegram/` or
// from `orders.js`.** That rule is what keeps it usable from the CLI,
// testable with no bot in existence, and movable behind an HTTP route later
// if the bot ever leaves this deployment. Break it and that becomes a
// rewrite rather than a move.
//
// ── The line between here and the licensing path ────────────────────────
//
// Everything above this module is orders, charges, Telegram identity and
// retries. Everything below it is a key, a plan, a date, a seat count and a
// feature array. A licence provisioned from a Star purchase is
// byte-indistinguishable from one typed out by hand, which is exactly why
// nothing on the pod side has to learn that any of this exists.
//
// ── Term arithmetic: months and days ───────────────────────────────────
//
// The bot sells months, because a Stars invoice is a whole billing cycle
// (`cyclePrice()` is derived from a month count). The CLI has always taken
// `--days`, because a hand-issued trial is "give them a fortnight". Both
// are supported here rather than converting one into the other: 30 days is
// not a month, and quietly turning `--days 30` into a calendar month would
// change what every existing invocation of the CLI does.

import { randomBytes } from "node:crypto";

import { collections } from "./db.js";

/**
 * Anything this module rejects on the way in.
 *
 * A distinct base class so a caller can tell "you asked for something that
 * cannot be done" apart from "Atlas is unreachable". The CLI turns one into
 * a one-line usage error and lets the other keep its stack trace; the
 * webhook will need the same split to decide between failing an order and
 * retrying it.
 */
export class ProvisionError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProvisionError";
  }
}

/**
 * A caller named a plan that is not in the collection.
 *
 * Not `MissingPlanError` from plans.js — that one means a licence already
 * in the database points at a plan that has since vanished, which is a
 * server fault and answers 503. This one means the caller asked for a tier
 * that does not exist, which is a bad request and must never reach a write.
 */
export class UnknownPlanError extends ProvisionError {
  constructor(planId) {
    super(
      `no plan "${planId}" in the plans collection — run ` +
        `"npm run seed-catalog", or name a tier that exists`,
    );
    this.name = "UnknownPlanError";
    this.planId = planId;
  }
}

/** An operation named a licence key that matches nothing. */
export class UnknownLicenseError extends ProvisionError {
  constructor(key) {
    super(`no license with key ${key}`);
    this.name = "UnknownLicenseError";
    this.key = key;
  }
}

/** KREA2-XXXX-XXXX-XXXX from a rejection-free alphabet (no O/0/I/1). */
export function generateKey() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = randomBytes(12);
  const chars = [...bytes].map((b) => alphabet[b % alphabet.length]);
  return [
    "KREA2",
    chars.slice(0, 4).join(""),
    chars.slice(4, 8).join(""),
    chars.slice(8, 12).join(""),
  ].join("-");
}

// ── Dates ───────────────────────────────────────────────────────────────

/**
 * `base` plus a whole number of calendar months, clamping the day.
 *
 * 31 January plus one month is 28 February (29 in a leap year), not 3
 * March. `Date.setMonth` gives you 3 March, because it lets the day
 * overflow into the next month — which on a yearly plan bought on the 31st
 * hands the customer a few free days every cycle, and on a monthly one
 * walks the renewal date forward through the calendar.
 *
 * Done in UTC throughout. `expires_at` is compared against `new Date()` on
 * the server, and Vercel runs in UTC while the machine running the CLI does
 * not — reading the day in local time would move an expiry by one day for
 * anything issued near midnight.
 */
export function addMonths(base, months) {
  const start = new Date(base);
  const day = start.getUTCDate();

  // Step to the 1st before changing the month, so the intermediate value
  // can never be a date that does not exist and overflow on its own.
  const out = new Date(start);
  out.setUTCDate(1);
  out.setUTCMonth(out.getUTCMonth() + months);

  // Day 0 of the following month is the last day of this one.
  const daysInTarget = new Date(
    Date.UTC(out.getUTCFullYear(), out.getUTCMonth() + 1, 0),
  ).getUTCDate();
  out.setUTCDate(Math.min(day, daysInTarget));
  return out;
}

/** `base` plus a whole number of days. The unit the CLI has always used. */
function addDays(base, days) {
  return new Date(new Date(base).getTime() + days * 86_400_000);
}

function wholeNumber(value, label) {
  // A bare `--days` reaches the argument parser as `true`, and Number(true)
  // is 1 — so anything that is not already a number or a non-empty string
  // is rejected outright rather than coerced into a term nobody asked for.
  const numeric =
    typeof value === "number" ||
    (typeof value === "string" && value.trim() !== "");
  const parsed = numeric ? Number(value) : Number.NaN;
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new ProvisionError(
      `${label} must be a whole number of at least 1, got ${JSON.stringify(value)}`,
    );
  }
  return parsed;
}

/**
 * The end of a term that starts at `base`, or null for "no term".
 *
 * Months or days, never both — a caller passing both has confused two
 * different notions of a term and the right answer is not to guess which.
 * Null (neither) means a licence that does not expire, which is what an
 * issue-key run with no `--days` has always produced.
 */
export function termEnd(base, { months = null, days = null } = {}) {
  if (months != null && days != null) {
    throw new ProvisionError("pass months or days, not both");
  }
  if (months != null) return addMonths(base, wholeNumber(months, "months"));
  if (days != null) return addDays(base, wholeNumber(days, "days"));
  return null;
}

/**
 * Where a renewal should land: the term added to whatever the licence has
 * left, not to today.
 *
 * This is the fix. `issue-key.js --update --days 30` used to write
 * `now + 30` unconditionally, so renewing a licence with twelve days left
 * silently destroyed those twelve days — worst when the customer renews
 * early, which is exactly the customer you least want to short-change.
 *
 * An expiry already in the past is not credit: it is added from today, so a
 * licence that lapsed a year ago does not renew into a term that is already
 * over.
 *
 * `expires_at` is re-read through `new Date()` rather than trusted to be
 * one, because this collection is hand-edited in Atlas and a date typed in
 * as a string is a plausible thing to find. Anything unparseable is treated
 * as no expiry rather than propagated as an Invalid Date.
 */
export function extendedExpiry(license, term, now = new Date()) {
  const raw = license?.expires_at ? new Date(license.expires_at) : null;
  const current = raw && !Number.isNaN(raw.getTime()) ? raw : null;
  const base = current && current > now ? current : now;
  return termEnd(base, term);
}

// ── Validation ──────────────────────────────────────────────────────────

/**
 * Confirm a plan exists, and return its canonical id.
 *
 * Against the collection, not `DEFAULT_PLANS`: a tier added in Atlas is
 * sellable the same day, and `getPlan()`'s 60-second cache is deliberately
 * not used here. This runs once per purchase, not once per acquire, and a
 * cached "no such plan" a minute after the tier was created would be a
 * confusing failure to buy something the pricing page is already showing.
 */
async function requirePlan(planId) {
  const id = typeof planId === "string" ? planId.trim() : "";
  if (!id) {
    throw new ProvisionError(
      `plan_id must be a non-empty string, got ${JSON.stringify(planId)}`,
    );
  }
  const { plans } = await collections();
  const plan = await plans.findOne({ _id: id });
  if (!plan) throw new UnknownPlanError(id);
  return plan._id;
}

/** Seats, coerced the way the CLI has always coerced them: garbage is 1. */
function seatCount(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

// ── Reads ───────────────────────────────────────────────────────────────

export async function findLicense(key) {
  const { licenses } = await collections();
  return licenses.findOne({ key });
}

// ── Writes ──────────────────────────────────────────────────────────────

/**
 * Create a licence and return the stored document.
 *
 * The shape is written in full every time, including the nulls: every
 * document then looks the same, and an entitlement that is unset is
 * visibly a choice rather than a field somebody forgot. `features` and
 * `features_extra` default to null because a plan is the route — a literal
 * features array wins over the plan forever after (see resolveEntitlement)
 * and freezes the licence at the day it was issued. The CLI can still pass
 * one, for keys that predate plans and for the one-off deal.
 *
 * `telegram_user_id` is the exception: it is written only when there is
 * one, so a CLI-issued key carries no trace of a bot it was not sold by.
 * It is a label for `/mykeys` and renewal reminders, never an input to
 * anything on the licensing path, and `seatPayload()` must never send it.
 */
export async function createLicense({
  key = null,
  name = null,
  plan_id = null,
  seats = 1,
  months = null,
  days = null,
  features = null,
  features_extra = null,
  is_admin = false,
  telegram_user_id = null,
}) {
  if (plan_id != null && features != null) {
    throw new ProvisionError(
      "plan_id and a literal features array together are ambiguous: the " +
        "array wins, so the plan would have no effect",
    );
  }
  const resolvedPlan = plan_id == null ? null : await requirePlan(plan_id);

  const now = new Date();
  const doc = {
    key: key || generateKey(),
    name: name ?? null,
    seats: seatCount(seats),
    active: true,
    expires_at: termEnd(now, { months, days }),
    plan_id: resolvedPlan,
    features,
    features_extra,
    is_admin: Boolean(is_admin),
    created_at: now,
  };
  if (telegram_user_id != null) doc.telegram_user_id = telegram_user_id;

  const { licenses } = await collections();
  await licenses.insertOne(doc);
  return doc;
}

/**
 * Change an existing licence. Returns the updated document, or null if
 * nothing matched.
 *
 * `undefined` means "leave this field alone" for every option, which is the
 * whole reason this is not one `$set` built at the call site: an update
 * about seats must not silently wipe an entitlement someone set last month,
 * and every past bug of that shape came from a caller assembling the `$set`
 * itself.
 *
 * Two rules are enforced here rather than trusted to callers:
 *
 *   plan_id ⇒ features: null    Precedence rule 1 — a leftover literal
 *                               array keeps winning and the plan change
 *                               silently does nothing. It is applied after
 *                               any `features` passed in, so it cannot be
 *                               overridden by argument order.
 *   extend                      Adds the term to what is left, never
 *                               resets. See extendedExpiry.
 *
 * `expires_at` (an absolute date, or null for never) and `extend` are
 * mutually exclusive: one sets the term, the other moves it.
 */
export async function updateLicense({
  key = null,
  match_name = null,
  seats,
  active,
  is_admin,
  features,
  features_extra,
  plan_id,
  expires_at,
  extend,
  telegram_user_id,
}) {
  // Matching on `name` exists because `issue-key.js --update --name "Acme"`
  // has always been allowed. It is not unique and it is not how anything
  // else addresses a licence — the key is — so it stays a separate,
  // explicitly-named argument that nothing but the CLI passes.
  const filter = key ? { key } : match_name ? { name: match_name } : null;
  if (!filter) {
    throw new ProvisionError("updateLicense needs a key, or a name to match");
  }
  if (expires_at !== undefined && extend !== undefined) {
    throw new ProvisionError("pass expires_at or extend, not both");
  }
  if (plan_id !== undefined && features !== undefined && features !== null) {
    throw new ProvisionError(
      "plan_id and a literal features array together are ambiguous: the " +
        "array wins, so the plan would have no effect",
    );
  }

  const { licenses } = await collections();
  const set = { updated_at: new Date() };

  if (seats !== undefined) set.seats = seatCount(seats);
  if (active !== undefined) set.active = Boolean(active);
  if (is_admin !== undefined) set.is_admin = Boolean(is_admin);
  if (features !== undefined) set.features = features;
  if (features_extra !== undefined) set.features_extra = features_extra;
  if (telegram_user_id !== undefined) set.telegram_user_id = telegram_user_id;
  if (expires_at !== undefined) set.expires_at = expires_at;

  // Last, so `features: null` cannot be undone by the assignment above.
  if (plan_id !== undefined) {
    set.plan_id = await requirePlan(plan_id);
    set.features = null;
  }

  if (extend !== undefined) {
    // Read then compute then write. Two concurrent renewals of the same key
    // would both extend from the same base and one term would be lost —
    // which cannot happen from the bot, because an order transitions to
    // PAID exactly once and provisions from that single transition, and
    // cannot happen from the CLI, which is one person at a shell.
    const current = await licenses.findOne(filter);
    if (!current) return null;
    set.expires_at = extendedExpiry(current, extend);
  }

  return licenses.findOneAndUpdate(
    filter,
    { $set: set },
    { returnDocument: "after" },
  );
}

/**
 * Extend a licence by a term. Same key, always.
 *
 * A renewal must never mint a new key. The key is baked into the pod's
 * environment, into the Docker `.env`, and on Windows into a persisted user
 * environment variable; it is what `/v1/build` resolves a release channel
 * against, and what the `sessions` and `prompts` rows reference. Rotating
 * it every cycle would break a working machine and orphan its history, in
 * exchange for nothing.
 *
 * Reactivates, because a licence that lapsed and has now been paid for is
 * active again — and because a customer who renews after a revoke would
 * otherwise pay and stay locked out.
 */
export async function renewLicense({ key, months = null, days = null }) {
  if (months == null && days == null) {
    throw new ProvisionError("renewLicense needs a term: months or days");
  }
  const updated = await updateLicense({
    key,
    extend: { months, days },
    active: true,
  });
  if (!updated) throw new UnknownLicenseError(key);
  return updated;
}

/**
 * Move a licence to another plan, and optionally restart its term.
 *
 * `features: null` comes with it unconditionally (updateLicense enforces
 * that) — without it the change is a silent no-op on any licence carrying a
 * literal array, which is the worst possible outcome of "put them on
 * Studio".
 *
 * A term given here *resets* from today rather than extending: an upgrade
 * is a new deal at a new price, and Stars are integers so there is no
 * proration to charge anyway. Renewal is the operation that extends.
 *
 * The customer must restart the app to see the change — tabs are built once
 * at launch, and a newly granted one has no weights on disk. The running
 * pod notices on its next heartbeat and logs exactly that; whoever calls
 * this is responsible for saying so too.
 */
export async function changePlan({ key, plan_id, months = null, days = null }) {
  const term =
    months == null && days == null
      ? undefined
      : termEnd(new Date(), { months, days });
  const updated = await updateLicense({ key, plan_id, expires_at: term });
  if (!updated) throw new UnknownLicenseError(key);
  return updated;
}
