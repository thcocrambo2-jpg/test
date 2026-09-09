// Issue a license key for a customer.
//
//   npm run issue-key -- --name "Acme Corp" --plan creator --seats 2
//   npm run issue-key -- --name "Trial" --plan creator --seats 1 --days 30
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --revoke
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --admin --update
//
// ── --admin ────────────────────────────────────────────────────────────
//
// A role, not an entitlement. It grants no tab and never has — what a key
// can run is `features` and nothing else, so marking one admin cannot
// change what it generates. What it changes is the prompt library: an
// ordinary pod captures every new recipe into it silently, an admin pod
// captures nothing and publishes only what its operator ticks the box for.
// Put it on the keys you generate from yourself, or your own testing fills
// the review queue you are the one working through.
//
// The generated key is what the customer puts in KREA2_LICENSE_KEY on
// their pod. Keys are stored in plain text so you can match a key to a
// customer while supporting them; the collection is never exposed to
// clients, which is the point of this service sitting in front of it.
//
// ── How a key gets its tabs ────────────────────────────────────────────
//
// --plan is the normal route. The license stores plan_id and the server
// resolves the feature list at request time, so editing the plan later
// moves every customer on it. --features-extra grants a tab on top of the
// plan, for the one-off deal that does not justify a new tier.
//
// --features still works and still wins over the plan, because keys issued
// before plans existed carry one. Prefer a plan: a license with a literal
// features array is frozen at the day it was issued and will not follow
// any pricing change.

import { collections, ensureIndexes } from "../src/db.js";
import {
  featureOrder,
  invalidateFeatures,
  parseFeatureArg,
} from "../src/features.js";
import { invalidatePlans, resolveEntitlement } from "../src/plans.js";
// Every licence write below goes through provision.js, which is also what
// the Telegram purchase path calls. Two implementations of "issue a key"
// would be two answers to what a key looks like, and the wrong one would be
// the one nobody runs by hand and therefore nobody sees.
import {
  createLicense,
  ProvisionError,
  updateLicense,
} from "../src/provision.js";

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

function die(...lines) {
  for (const line of lines) console.error(line);
  process.exit(1);
}

/**
 * Run a provisioning call, turning a rejected argument into a one-line
 * usage error. Anything else — an unreachable Atlas, most likely — keeps
 * its stack trace, because that is a fault to look at rather than a
 * mistake to correct.
 */
async function orDie(operation) {
  try {
    return await operation();
  } catch (err) {
    if (err instanceof ProvisionError) die(err.message);
    throw err;
  }
}

const opts = args(process.argv.slice(2));
const { plans } = await collections();
await ensureIndexes();
invalidatePlans(); // a long-lived shell should not print a stale plan
invalidateFeatures();

// Validated against the collection, not this build's seed list, so a
// feature added in Atlas can be granted the same day. An un-seeded
// database falls back to the seed registry — see allFeatures.
const knownFeatures = await featureOrder();

if (opts.revoke || opts.enable) {
  if (!opts.key) die("--revoke/--enable needs --key KREA2-...");
  const active = Boolean(opts.enable);
  const result = await orDie(() => updateLicense({ key: opts.key, active }));
  if (!result) die(`no license with key ${opts.key}`);
  // Running instances notice on their next heartbeat, so a revoke takes
  // effect in about a minute rather than only blocking the next start.
  console.log(`${active ? "enabled" : "revoked"}  ${opts.key}`);
  process.exit(0);
}

if (!opts.name && !opts.key) {
  const known = await plans.find({}).sort({ sort_order: 1 }).toArray();
  die(
    'usage: npm run issue-key -- --name "Acme Corp" --plan creator --seats 2',
    '       [--features-extra "wan_i2v"] [--days 30] [--admin|--no-admin]',
    "       [--update] [--key KREA2-...] [--revoke]",
    "",
    `plans:    ${known.map((p) => p._id).join(", ") ||
      "(none — run: npm run seed-catalog)"}`,
    `features: ${knownFeatures.join(", ")}`,
  );
}

const seats = Number.parseInt(opts.seats, 10) || 1;
// The term is provision.js's arithmetic now, not this script's. On a create
// it still runs from today; on an --update it extends what is left instead
// of resetting it, which is the bug this delegation exists to fix.
const days = opts.days ? opts.days : null;

// ── Validate before writing anything ───────────────────────────────────

if (opts.plan !== undefined && opts.features !== undefined) {
  die(
    "--plan and --features together are ambiguous: a literal features " +
      "array overrides the plan, so the plan would have no effect.",
    "Use --plan on its own, or --plan with --features-extra to add to it.",
  );
}

let plan_id;
if (opts.plan !== undefined) {
  if (opts.plan === true) die("--plan needs a value, e.g. --plan creator");
  const plan = await plans.findOne({ _id: String(opts.plan).trim() });
  if (!plan) {
    const known = await plans.find({}).sort({ sort_order: 1 }).toArray();
    die(
      `no plan "${opts.plan}"`,
      known.length
        ? `known plans: ${known.map((p) => p._id).join(", ")}`
        : "the plans collection is empty — run: npm run seed-catalog",
    );
  }
  plan_id = plan._id;
}

function parseOrDie(raw, flag) {
  const parsed = parseFeatureArg(raw, knownFeatures);
  if (parsed.error) die(`${flag}: ${parsed.error}`);
  return parsed.features;
}

const features =
  opts.features === undefined ? null : parseOrDie(opts.features, "--features");
const features_extra =
  opts["features-extra"] === undefined
    ? null
    : parseOrDie(opts["features-extra"], "--features-extra");

/** Print what the customer will actually get, resolved the way the server does. */
async function report(license) {
  invalidatePlans();
  let resolved;
  try {
    resolved = await resolveEntitlement(license);
  } catch (err) {
    console.error(`\n  WARNING  ${err.message}`);
    return;
  }
  const label = resolved.features
    ? resolved.features.join(", ") || "(none)"
    : "(unset — the app's built-in defaults apply)";
  console.log(`  plan       ${resolved.plan_name || plan_id || "(none)"}`);
  console.log(`  features   ${label}`);
  console.log(`  from       ${resolved.source}`);
}

// ── Update ─────────────────────────────────────────────────────────────

if (opts.update) {
  // Said out loud, because it is the one destructive thing here.
  // provision.js clears the literal features array whenever a plan is set;
  // without that a leftover array would keep winning and the new plan would
  // do nothing — a silent no-op is the worst possible outcome of "put them
  // on Pro".
  if (plan_id !== undefined) {
    console.log(
      `clearing this license's literal features array so plan ` +
        `"${plan_id}" takes effect`,
    );
  }
  // Only when one of the two flags is actually passed, for the same reason
  // as the entitlements below: an --update about seats must not silently
  // demote an admin key. Left undefined otherwise, which provision.js reads
  // as "leave this field alone".
  let is_admin;
  if (opts.admin) is_admin = true;
  if (opts["no-admin"]) is_admin = false;

  const result = await orDie(() =>
    updateLicense({
      key: opts.key ?? null,
      match_name: opts.name ?? null,
      seats,
      active: true,
      is_admin,
      // Each only when asked: an --update that is really about seats must
      // not silently wipe an entitlement someone set earlier.
      features: features === null ? undefined : features,
      features_extra: features_extra === null ? undefined : features_extra,
      plan_id,
      // Extends what is left rather than resetting it — the whole point of
      // the delegation. Twelve days remaining plus a 30-day renewal is 42
      // days, not 30.
      extend: days === null ? undefined : { days },
    }),
  );
  if (!result) die("no matching license to update");

  console.log(
    `\nupdated  ${result.key}  seats=${result.seats}` +
      (result.is_admin === true ? "  (admin)" : ""),
  );
  await report(result);
  console.log(
    "\nA running instance keeps the features it started with — the " +
      "customer must restart the app to pick this up.",
  );
  process.exit(0);
}

// ── Create ─────────────────────────────────────────────────────────────

// The key, the term and the document shape — including which fields are
// written even when null — all live in provision.js now, so a licence sold
// through the bot and one issued here are the same document.
const doc = await orDie(() =>
  createLicense({
    key: opts.key && opts.key !== true ? opts.key : null,
    name: opts.name === true ? null : opts.name,
    seats,
    days,
    plan_id: plan_id ?? null,
    features,
    features_extra,
    is_admin: Boolean(opts.admin),
  }),
);
const key = doc.key;

console.log(`\n  customer   ${opts.name}`);
console.log(`  key        ${key}`);
console.log(`  seats      ${seats}`);
console.log(`  admin      ${doc.is_admin ? "yes — prompts are not captured "
  + "automatically; publish with the checkbox" : "no"}`);
await report(doc);
console.log(
  `  expires    ${doc.expires_at ? doc.expires_at.toISOString() : "never"}`,
);
if (plan_id === undefined && features === null) {
  console.log(
    "\n  WARNING  no --plan and no --features: this key falls back to the " +
      "\n           app's built-in defaults, which change between releases.",
  );
}
console.log("\nGive the customer this, to set on their RunPod pod:");
console.log(`\n  KREA2_LICENSE_KEY=${key}\n`);
process.exit(0);
