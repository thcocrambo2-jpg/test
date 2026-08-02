// Plans — what a tier is called, what it costs, and which tabs it grants.
//
// A license names a plan (`plan_id`) instead of listing features directly.
// One edit to a plan document then moves every customer on that tier,
// which is the entire point: pricing changes should not be a bulk update
// across the licenses collection.
//
// ── The wire format does not change ────────────────────────────────────
//
// resolveEntitlement() runs behind seatPayload() in app.js, and /v1/acquire
// still answers with a flat `features` array. The Python client knows
// nothing about plans and needs no rebuild for any of this. Keep it that
// way — plans must not leak past seatPayload().
//
// ── Precedence ─────────────────────────────────────────────────────────
//
//   1. license.features is an array   → use it verbatim, ignore the plan
//   2. license.plan_id is set         → plan.features + license.features_extra
//   3. neither                        → null, and the client uses its defaults
//
// Rule 1 is what makes this migration zero-step: every key issued before
// plans existed carries a `features` array and keeps behaving exactly as
// it did. It is also the escape hatch for a one-off deal that does not fit
// any tier — but prefer `features_extra` for that, since it keeps the
// customer on a plan and so keeps them moving when the plan moves.
//
// ── Why there is no features_cache on the license ──────────────────────
//
// Denormalising the resolved list onto each license would turn one plan
// edit into a fan-out write across every license on that tier, and a
// half-failed fan-out leaves licenses silently disagreeing with the plan
// they claim — the exact failure this indirection exists to remove. There
// are a handful of plans, so the whole collection is cached in module
// scope instead: a cold start pays one small query and warm invocations
// pay nothing.
//
// ── Billing cycles live in the collection, prices are derived ───────────
//
// A plan document carries one figure — `price_monthly` — and the billing
// cycles it can be bought on are a single `__billing` document sitting in
// the same collection (see DEFAULT_BILLING). Every other price is computed
// from those two things in cyclePrice(), so a quarterly or yearly price
// cannot drift out of step with the monthly one it is supposed to
// discount. Turning a cycle on is one boolean in Atlas, and the pricing
// page grows a tab for it with nothing rebuilt on either side.

import { collections } from "./db.js";
import { featureOrder, normalizeFeatures, sortByRegistry } from "./features.js";

// How long a cached copy of the plans collection is trusted. A plan edit
// reaches running instances after this plus one heartbeat, where the
// client logs that a restart is needed (see licensing.py). Short enough
// that an edit is not mysterious, long enough that the collection is not
// re-read on every acquire.
const PLAN_TTL_MS = 60_000;

// On globalThis for the same reason db.js caches its client there: warm
// serverless invocations reuse the module scope, cold ones do not.
let cache = globalThis.__krea2Plans;
if (!cache) cache = globalThis.__krea2Plans = { at: 0, byId: null, cycles: null };

/**
 * A license points at a plan that does not exist.
 *
 * Deliberately thrown rather than handled: app.js's wrap() turns it into
 * a 503, which is the only honest answer. Falling back to the client's
 * defaults would hand a Studio customer three tabs and look like their
 * fault; granting everything would be worse. 503 is retryable, rides the
 * client's grace window if the instance is already running, and puts the
 * reason in the deployment log where you will see it.
 */
export class MissingPlanError extends Error {
  constructor(planId, licenseKey) {
    super(
      `license ${licenseKey} names plan "${planId}", which is not in the ` +
        `plans collection — run "npm run seed-catalog" or fix the license`,
    );
    this.name = "MissingPlanError";
    this.planId = planId;
    this.licenseKey = licenseKey;
  }
}

// ── Billing cycles ──────────────────────────────────────────────────────
//
// One lookup document in the plans collection, rather than a collection of
// its own: there is exactly one of it, it is read on the same request as
// the plans, and keeping it here means the whole pricing story is one
// screen in Atlas. Mongo does not care that it has a different shape from
// its neighbours; the readers do, so it is filtered out of allPlans() by
// its `__` prefix and never reaches getPlan() or the catalogue listing.
//
// Each cycle says how long it is and what it takes off the monthly rate.
// Nothing stores a quarterly or yearly *price* — cyclePrice() derives it,
// which is why editing `price_monthly` alone can never leave a stale
// discounted figure behind it.
//
//   enabled           whether the pricing page offers this cycle at all.
//                     Off is invisible, not greyed out: no tab, no price,
//                     and with every extra cycle off the page shows no tab
//                     bar at all and reads exactly as it did before cycles
//                     existed. Flipping one to true in Atlas is the whole
//                     launch — the client renders whatever it is sent —
//                     but seed-catalog upserts this document like any
//                     other, so set it here too or the next run reverts it.
//   discount_percent  off the monthly rate, per month, for committing to
//                     the longer term. 0 is a legitimate value: a cycle
//                     that is only a convenience gets no "save" badge.
//
export const BILLING_ID = "__billing";

// The cycle every plan is priced in. Always offered, never discounted:
// `price_monthly` *is* the list price, so a discount here would mean the
// figure in the plan document is not the one anybody pays.
const BASE_CYCLE = "monthly";

export const DEFAULT_BILLING = {
  _id: BILLING_ID,
  kind: "billing",
  cycles: [
    { id: BASE_CYCLE, label: "Monthly", months: 1, enabled: true,
      discount_percent: 0 },
    { id: "quarterly", label: "Quarterly", months: 3, enabled: false,
      discount_percent: 10 },
    { id: "yearly", label: "Yearly", months: 12, enabled: false,
      discount_percent: 20 },
  ],
};

/** A percentage from the wire, or null for anything unusable. */
function percent(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0 || number >= 100) return null;
  return Math.round(number * 100) / 100;
}

/**
 * The cycles as the rest of the service should see them, from a `__billing`
 * document that may have been hand-edited.
 *
 * Every field is re-derived rather than trusted, and a row too broken to
 * price (no id, no sane month count) is dropped: this document is edited in
 * Atlas by hand, and a typo in it must cost one cycle rather than the whole
 * pricing page. The monthly cycle is added back if it is missing and forced
 * on if it was switched off, because a catalogue with no base cycle has no
 * price to show for anything.
 */
function normalizeCycles(doc) {
  const rows = Array.isArray(doc?.cycles) ? doc.cycles : DEFAULT_BILLING.cycles;
  const seen = new Set();
  const out = [];

  for (const row of rows) {
    const id = typeof row?.id === "string" ? row.id.trim() : "";
    const months = Number(row?.months);
    if (!id || seen.has(id) || !Number.isInteger(months) || months < 1) continue;
    seen.add(id);

    const fallback = DEFAULT_BILLING.cycles.find((cycle) => cycle.id === id);
    const label =
      (typeof row.label === "string" && row.label.trim()) ||
      fallback?.label ||
      id.charAt(0).toUpperCase() + id.slice(1);
    out.push({
      id,
      label,
      months,
      enabled: id === BASE_CYCLE ? true : row.enabled === true,
      discount_percent: id === BASE_CYCLE ? 0 : percent(row.discount_percent) ?? 0,
    });
  }

  // A copy, not the seed object itself: this array is cached and handed to
  // every caller, and one of them mutating it would edit the default.
  if (!seen.has(BASE_CYCLE)) {
    out.push({ ...DEFAULT_BILLING.cycles.find((c) => c.id === BASE_CYCLE) });
  }
  // Shortest first, so the tab bar reads left to right in term length
  // whatever order the document happens to list them in.
  return out.sort((a, b) => a.months - b.months);
}

/** Read the collection once, splitting the lookup document off the plans. */
async function load() {
  if (cache.byId && cache.cycles && Date.now() - cache.at < PLAN_TTL_MS) {
    return cache;
  }
  const { plans } = await collections();
  const rows = await plans.find({}).sort({ sort_order: 1 }).toArray();

  // `__`-prefixed ids are lookup documents, not tiers. Filtering on the
  // prefix rather than on `kind` means a future lookup document is excluded
  // the day it is added, without this line having to learn about it.
  const tiers = rows.filter((row) => !String(row._id).startsWith("__"));
  const billing = rows.find((row) => row._id === BILLING_ID);

  cache.byId = new Map(tiers.map((plan) => [plan._id, plan]));
  cache.cycles = normalizeCycles(billing);
  cache.at = Date.now();
  return cache;
}

/** Every plan, by _id. Cached for PLAN_TTL_MS. */
export async function allPlans() {
  return (await load()).byId;
}

/**
 * The billing cycles, normalized, shortest term first — including the ones
 * that are switched off, so the caller decides what to do about them.
 * /v1/plans sends only the enabled ones; the admin listing shows all.
 */
export async function billingCycles() {
  return (await load()).cycles;
}

/**
 * What one plan costs on one cycle, or null if the plan carries no price.
 *
 * `total` is what is actually charged for the term and is rounded to a
 * whole unit of currency: the fraction a percentage discount leaves behind
 * (₹14,390.40) is an artifact of the arithmetic, not a price anybody
 * intends to quote. `per_month` is the comparison figure the pricing page
 * puts under it, and is derived from the rounded total so the two cannot
 * disagree by a rupee.
 *
 * A plan may override a cycle's discount with `discounts: { yearly: 25 }`,
 * for the tier where the standard commitment discount is not the deal you
 * want to offer. Unset — the normal case — means the cycle's own rate.
 */
export function cyclePrice(plan, cycle) {
  const monthly = Number(plan?.price_monthly);
  if (!Number.isFinite(monthly) || monthly < 0) return null;

  const override = cycle.id === BASE_CYCLE ? null : percent(plan?.discounts?.[cycle.id]);
  const discount = override ?? cycle.discount_percent;
  const undiscounted = Math.round(monthly * cycle.months);
  const total = Math.round(monthly * cycle.months * (1 - discount / 100));

  return {
    cycle: cycle.id,
    months: cycle.months,
    total,
    per_month: Math.round((total / cycle.months) * 100) / 100,
    discount_percent: discount,
    saving: undiscounted - total,
  };
}

/** Drop the cache, so the next read hits Mongo. For scripts and tests. */
export function invalidatePlans() {
  cache.byId = null;
  cache.cycles = null;
  cache.at = 0;
}

export async function getPlan(planId) {
  return (await allPlans()).get(planId) || null;
}

/**
 * What this license grants.
 *
 * Returns { features, plan_id, plan_name, source } where `features` is the
 * array to send the client — null meaning "the document says nothing, use
 * your own defaults". Throws MissingPlanError if plan_id names nothing.
 */
export async function resolveEntitlement(license) {
  const order = await featureOrder();

  // An explicit array wins, including an empty one: `features: []` is a
  // deliberate "this key starts but does nothing", and reading it as
  // "no opinion" would silently re-grant the plan's tabs.
  const explicit = normalizeFeatures(license.features);
  if (explicit !== null) {
    return {
      features: sortByRegistry(explicit, order),
      plan_id: license.plan_id || null,
      plan_name: null,
      source: "license",
    };
  }

  const planId =
    typeof license.plan_id === "string" ? license.plan_id.trim() : "";
  if (!planId) {
    return { features: null, plan_id: null, plan_name: null, source: "none" };
  }

  const plan = await getPlan(planId);
  if (!plan) throw new MissingPlanError(planId, license.key);

  const granted = normalizeFeatures(plan.features) || [];
  const extra = normalizeFeatures(license.features_extra) || [];
  return {
    features: sortByRegistry([...new Set([...granted, ...extra])], order),
    plan_id: plan._id,
    plan_name: plan.name || plan._id,
    source: extra.length ? "plan+extra" : "plan",
  };
}

// ── Seed data ───────────────────────────────────────────────────────────
//
// The plans as shipped. scripts/seed-catalog.js upserts these into Mongo;
// after that the collection is what /v1/acquire reads, so a price or a
// feature list can be changed in Atlas without a redeploy. Keep this in
// step with what is live, or the next seed run will quietly revert an
// edit made by hand.
//
// `seats` is deliberately absent. Seat count lives on the license and is
// per-deal negotiable — resolving it through the plan would mean an ops
// edit to `studio` retroactively changed how many pods every studio
// customer may run.
//
// `price_monthly` is the only price stored. Quarterly and yearly are
// derived from it and the discounts on the `__billing` document, so there
// is no second figure here to forget to update — see cyclePrice(). A plan
// that should not follow the standard discount can carry its own with
// `discounts: { yearly: 25 }`; none does today.
//
// Two optional presentation fields, read only by the pricing page:
//
//   is_popular    marks the recommended tier. The page gives it a "Most
//                 Popular" flag and a stronger card. At most one plan
//                 should carry it; if several do, every one of them is
//                 flagged and the recommendation stops meaning anything.
//
//   discounts     per-cycle override of the billing document's rate, as a
//                 percentage off the monthly figure above.
//
// They live here rather than in the client so the recommended tier can be
// moved from Atlas without a redeploy — the same reason the feature
// names moved to the features collection.
export const DEFAULT_PLANS = [
  {
    _id: "starter",
    name: "Starter",
    description: "Fast Krea 2 generation for everyday use",
    price_monthly: 599,
    currency: "INR",
    features: ["krea_t2i", "krea_v2_t2i", "gallery"],
    is_public: true,
    sort_order: 10,
  },
  {
    _id: "creator",
    name: "Creator",
    description: "Generation plus the full editing set",
    price_monthly: 999,
    currency: "INR",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "faceswap",
      "community_prompts",
    ],
    is_public: true,
    is_popular: true,
    sort_order: 20,
  },
  {
    _id: "pro",
    name: "Pro",
    description: "Everything in Creator, plus Flux 2 and Klein Edit",
    price_monthly: 1499,
    currency: "INR",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "faceswap",
      "krea_v2_edit",
      "flux_t2i",
      "klein_i2i",
      "community_prompts",
    ],
    is_public: true,
    sort_order: 30,
  },
  {
    _id: "studio",
    name: "Studio",
    description: "Full access — every model, video, and the batch tools",
    price_monthly: 1799,
    currency: "INR",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "krea_v2_edit",
      "faceswap",
      "flux_t2i",
      "klein_i2i",
      "wan_i2v",
      "json_batch",
      "community_prompts",
    ],
    is_public: true,
    sort_order: 40,
  },
  // Not public, it is the name that shows up in the server logs for your
  // own pods, and it is where a new tab lands first — which is exactly
  // what `krea_v2_edit` is doing here. It is on no paid plan yet; add it
  // to creator/pro/studio above (next to `krea_edit`) when it should be
  // something a customer can buy.
  {
    _id: "admin",
    name: "Admin (Internal)",
    description: "Full unrestricted access for development",
    price_monthly: 0,
    currency: "INR",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_v2_edit",
      "krea_inpaint",
      "faceswap",
      "flux_t2i",
      "klein_i2i",
      "wan_i2v",
      "json_batch",
      "community_prompts",
    ],
    is_public: false,
    sort_order: 999,
  },
  {
    _id: "admin-minimal",
    name: "Admin (Minimal)",
    description: "Minimal access for development",
    price_monthly: 0,
    currency: "INR",
    features: ["krea_t2i", "krea_v2_t2i", "krea_edit", "krea_v2_edit", "gallery", "community_prompts"],
    is_public: false,
    sort_order: 999,
  },
];
