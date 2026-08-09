// Write the feature and plan catalogue into MongoDB.
//
//   npm run seed-catalog
//   npm run seed-catalog -- --dry-run
//
// Idempotent — re-run it after editing FEATURES in src/features.js or
// DEFAULT_PLANS in src/plans.js. Both collections use the key as `_id`, so
// this is a plain upsert and running it twice changes nothing the second
// time.
//
// It never deletes. A plan removed from DEFAULT_PLANS stays in Mongo and
// is reported instead, because licenses may still point at it and dropping
// the document would 503 every one of those customers at their next
// heartbeat. Retiring a plan means moving its licenses off it first.
//
// One direction worth being clear about: after seeding, the *collections*
// are what the API reads — plans for prices and entitlements, features for
// the names on the pricing page and the labels on the app's tabs — so all
// of it can be changed in Atlas without a redeploy. That also means a hand
// edit in Atlas is reverted by the next seed run unless DEFAULT_PLANS and
// FEATURES are updated to match.

import { collections, ensureIndexes } from "../src/db.js";
import { FEATURES, FEATURE_KEYS } from "../src/features.js";
import {
  DEFAULT_BILLING,
  DEFAULT_PLANS,
  billingCycles,
  cyclePrice,
  invalidatePlans,
} from "../src/plans.js";

const dryRun = process.argv.includes("--dry-run");

const { plans, features, licenses } = await collections();
await ensureIndexes();

// Validated before anything is written: a plan naming a feature this build
// does not have is a typo every time, and it would reach a customer as a
// tab that never appears.
let invalid = 0;
for (const plan of DEFAULT_PLANS) {
  for (const key of plan.features || []) {
    if (!FEATURE_KEYS.includes(key)) {
      console.error(
        `plan "${plan._id}" names unknown feature "${key}"\n` +
          `  known: ${FEATURE_KEYS.join(", ")}`,
      );
      invalid += 1;
    }
  }
}
if (invalid) {
  console.error(`\n${invalid} problem(s) — nothing written.`);
  process.exit(1);
}

async function upsertAll(collection, docs, label) {
  let created = 0;
  let updated = 0;
  for (const doc of docs) {
    const { _id, ...rest } = doc;
    if (dryRun) {
      const existing = await collection.findOne({ _id });
      if (existing) updated += 1;
      else created += 1;
      continue;
    }
    const result = await collection.updateOne(
      { _id },
      { $set: { ...rest, updated_at: new Date() },
        $setOnInsert: { created_at: new Date() } },
      { upsert: true },
    );
    if (result.upsertedCount) created += 1;
    else updated += 1;
  }
  console.log(
    `${label.padEnd(9)} ${created} created, ${updated} updated` +
      (dryRun ? "  (dry run — nothing written)" : ""),
  );
}

// Features carry their key as _id so the collection reads the same way the
// plans do, and so a plan's features array is a list of foreign keys you
// can follow by eye in Atlas. This collection is served, not just stored:
// `name` is what the pricing page shows and `tab_label` is what the app
// titles the tab with, so a typo here is a typo a customer reads.
await upsertAll(
  features,
  FEATURES.map(({ key, ...rest }) => ({ _id: key, ...rest })),
  "features",
);
await upsertAll(plans, DEFAULT_PLANS, "plans");

// The billing cycles, upserted exactly like everything else above: the
// code is the truth and a re-run reverts a hand edit, which is the rule
// this script already follows for prices and feature lists. Nothing bills
// anyone from this document — what a customer is charged is arranged by
// hand, and how long their key lasts is `expires_at` on the license — so
// it is display config for the pricing page and gets no special handling.
await upsertAll(plans, [DEFAULT_BILLING], "billing");

// Anything in Mongo this build no longer defines. Not an error — you may
// have added a plan by hand — but a plan with licenses on it and no
// definition here is one seed run away from being forgotten about.
const seededPlans = new Set(DEFAULT_PLANS.map((plan) => plan._id));
const orphans = (await plans.find({}).toArray()).filter(
  // `__`-prefixed ids are lookup documents (the billing config), not tiers.
  // allPlans() filters them out of the API for the same reason.
  (plan) => !seededPlans.has(plan._id) && !String(plan._id).startsWith("__"),
);
if (orphans.length) {
  console.log("\nplans in the database that this build does not define:");
  for (const plan of orphans) {
    const count = await licenses.countDocuments({ plan_id: plan._id });
    console.log(`  ${plan._id.padEnd(10)} ${count} license(s) on it`);
  }
  console.log(
    "  (left alone — deleting one would 503 every license pointing at it)",
  );
}

// Read back through billingCycles() rather than from DEFAULT_BILLING, so
// this prints what the API would actually serve: a cycle hand-edited in
// Atlas between seed runs, or one missing a field, is normalized or dropped
// there, and seeing that happen here is the point. The prices below follow
// the enabled ones, so a run of this script reports what a customer would
// see on the pricing page.
invalidatePlans();
const allCycles = await billingCycles();
const cycles = allCycles.filter((cycle) => cycle.enabled);

console.log("\nbilling cycles:");
for (const cycle of allCycles) {
  console.log(
    `  ${String(cycle.id).padEnd(10)} ${String(cycle.months).padStart(2)} month(s)  ` +
      `${String(cycle.discount_percent ?? 0).padStart(2)}% off  ` +
      (cycle.enabled ? "shown" : "hidden"),
  );
}

console.log("\nplans now live:");
for (const plan of await plans.find({}).sort({ sort_order: 1 }).toArray()) {
  if (String(plan._id).startsWith("__")) continue;
  const count = await licenses.countDocuments({ plan_id: plan._id });
  const currency = plan.currency || "INR";
  // One column per enabled cycle: "599/mo  1617/3mo  5750/12mo".
  const prices = cycles
    .map((cycle) => cyclePrice(plan, cycle))
    .filter(Boolean)
    .map((price) => `${price.total}/${price.months}mo`)
    .join("  ");
  console.log(
    `  ${plan._id.padEnd(13)} ${currency} ${prices.padEnd(34)}` +
      `${String(plan.features?.length ?? 0).padStart(2)} features  ` +
      `${count} license(s)` +
      (plan.is_public === false ? "  (not public)" : ""),
  );
}

console.log(
  '\nNext: npm run issue-key -- --name "Acme Corp" --plan pro --seats 2',
);
process.exit(0);
