// Issue a license key for a customer.
//
//   npm run issue-key -- --name "Acme Corp" --plan pro --seats 2
//   npm run issue-key -- --name "Trial" --plan creator --seats 1 --days 30
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --plan studio --update
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --features-extra "wan_i2v" --update
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --revoke
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

import { randomBytes } from "node:crypto";
import { collections, ensureIndexes } from "../src/db.js";
import { FEATURE_KEYS, parseFeatureArg } from "../src/features.js";
import { invalidatePlans, resolveEntitlement } from "../src/plans.js";

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

/** KREA2-XXXX-XXXX-XXXX from a rejection-free alphabet (no O/0/I/1). */
function generateKey() {
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

function die(...lines) {
  for (const line of lines) console.error(line);
  process.exit(1);
}

const opts = args(process.argv.slice(2));
const { licenses, plans } = await collections();
await ensureIndexes();
invalidatePlans(); // a long-lived shell should not print a stale plan

if (opts.revoke || opts.enable) {
  if (!opts.key) die("--revoke/--enable needs --key KREA2-...");
  const active = Boolean(opts.enable);
  const result = await licenses.updateOne(
    { key: opts.key },
    { $set: { active, updated_at: new Date() } },
  );
  if (!result.matchedCount) die(`no license with key ${opts.key}`);
  // Running instances notice on their next heartbeat, so a revoke takes
  // effect in about a minute rather than only blocking the next start.
  console.log(`${active ? "enabled" : "revoked"}  ${opts.key}`);
  process.exit(0);
}

if (!opts.name && !opts.key) {
  const known = await plans.find({}).sort({ sort_order: 1 }).toArray();
  die(
    'usage: npm run issue-key -- --name "Acme Corp" --plan pro --seats 2',
    '       [--features-extra "wan_i2v"] [--days 30]',
    "       [--update] [--key KREA2-...] [--revoke]",
    "",
    `plans:    ${known.map((p) => p._id).join(", ") ||
      "(none — run: npm run seed-catalog)"}`,
    `features: ${FEATURE_KEYS.join(", ")}`,
  );
}

const seats = Number.parseInt(opts.seats, 10) || 1;
const expires_at = opts.days
  ? new Date(Date.now() + Number.parseInt(opts.days, 10) * 86_400_000)
  : null;

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
  if (opts.plan === true) die("--plan needs a value, e.g. --plan pro");
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
  const parsed = parseFeatureArg(raw);
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
  const filter = opts.key ? { key: opts.key } : { name: opts.name };
  const update = { seats, active: true, updated_at: new Date() };
  if (opts.days) update.expires_at = expires_at;
  // Each only when asked: an --update that is really about seats must not
  // silently wipe an entitlement someone set earlier.
  if (features !== null) update.features = features;
  if (features_extra !== null) update.features_extra = features_extra;
  if (plan_id !== undefined) {
    update.plan_id = plan_id;
    // Loudly, because it is the one destructive thing here. A leftover
    // literal array would keep winning and the new plan would do nothing —
    // a silent no-op is the worst possible outcome of "put them on Pro".
    update.features = null;
    console.log(
      `clearing this license's literal features array so plan ` +
        `"${plan_id}" takes effect`,
    );
  }
  const result = await licenses.findOneAndUpdate(
    filter,
    { $set: update },
    { returnDocument: "after" },
  );
  if (!result) die("no matching license to update");

  console.log(`\nupdated  ${result.key}  seats=${result.seats}`);
  await report(result);
  console.log(
    "\nA running instance keeps the features it started with — the " +
      "customer must restart the app to pick this up.",
  );
  process.exit(0);
}

// ── Create ─────────────────────────────────────────────────────────────

const key = opts.key && opts.key !== true ? opts.key : generateKey();
const doc = {
  key,
  name: opts.name === true ? null : opts.name,
  seats,
  active: true,
  expires_at,
  // All three written even when null, so the document shape is the same
  // for every key and an unset entitlement is visibly a choice.
  plan_id: plan_id ?? null,
  features,
  features_extra,
  created_at: new Date(),
};
await licenses.insertOne(doc);

console.log(`\n  customer   ${opts.name}`);
console.log(`  key        ${key}`);
console.log(`  seats      ${seats}`);
await report(doc);
console.log(`  expires    ${expires_at ? expires_at.toISOString() : "never"}`);
if (plan_id === undefined && features === null) {
  console.log(
    "\n  WARNING  no --plan and no --features: this key falls back to the " +
      "\n           app's built-in defaults, which change between releases.",
  );
}
console.log("\nGive the customer this, to set on their RunPod pod:");
console.log(`\n  KREA2_LICENSE_KEY=${key}\n`);
process.exit(0);
