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

import { collections } from "./db.js";
import { normalizeFeatures, sortByRegistry } from "./features.js";

// How long a cached copy of the plans collection is trusted. A plan edit
// reaches running instances after this plus one heartbeat, where the
// client logs that a restart is needed (see licensing.py). Short enough
// that an edit is not mysterious, long enough that the collection is not
// re-read on every acquire.
const PLAN_TTL_MS = 60_000;

// On globalThis for the same reason db.js caches its client there: warm
// serverless invocations reuse the module scope, cold ones do not.
let cache = globalThis.__krea2Plans;
if (!cache) cache = globalThis.__krea2Plans = { at: 0, byId: null };

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

/** Every plan, by _id. Cached for PLAN_TTL_MS. */
export async function allPlans() {
  if (cache.byId && Date.now() - cache.at < PLAN_TTL_MS) return cache.byId;
  const { plans } = await collections();
  const rows = await plans.find({}).sort({ sort_order: 1 }).toArray();
  cache.byId = new Map(rows.map((plan) => [plan._id, plan]));
  cache.at = Date.now();
  return cache.byId;
}

/** Drop the cache, so the next read hits Mongo. For scripts and tests. */
export function invalidatePlans() {
  cache.byId = null;
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
  // An explicit array wins, including an empty one: `features: []` is a
  // deliberate "this key starts but does nothing", and reading it as
  // "no opinion" would silently re-grant the plan's tabs.
  const explicit = normalizeFeatures(license.features);
  if (explicit !== null) {
    return {
      features: sortByRegistry(explicit),
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
    features: sortByRegistry([...new Set([...granted, ...extra])]),
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
export const DEFAULT_PLANS = [
  {
    _id: "starter",
    name: "Starter",
    description: "Fast Krea 2 generation for everyday use",
    price_monthly: 19,
    price_yearly: 190,
    currency: "USD",
    features: ["krea_t2i", "krea_v2_t2i", "gallery"],
    is_public: true,
    sort_order: 10,
  },
  {
    _id: "creator",
    name: "Creator",
    description: "Generation plus the full editing set",
    price_monthly: 39,
    price_yearly: 390,
    currency: "USD",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "faceswap",
    ],
    is_public: true,
    sort_order: 20,
  },
  {
    _id: "pro",
    name: "Pro",
    description: "Everything in Creator, plus Flux 2 and Klein Edit",
    price_monthly: 59,
    price_yearly: 590,
    currency: "USD",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "faceswap",
      "flux_t2i",
      "klein_i2i",
    ],
    is_public: true,
    sort_order: 30,
  },
  {
    _id: "studio",
    name: "Studio",
    description: "Full access — every model, video, and the batch tools",
    price_monthly: 89,
    price_yearly: 890,
    currency: "USD",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "faceswap",
      "flux_t2i",
      "klein_i2i",
      "wan_i2v",
      "json_batch",
    ],
    is_public: true,
    sort_order: 40,
  },
  // Same feature set as studio today, and still its own document: it is
  // not public, it is the name that shows up in the server logs for your
  // own pods, and the moment there is a feature #11 you will want it here
  // before it is on anything a customer is paying for.
  {
    _id: "admin",
    name: "Admin (Internal)",
    description: "Full unrestricted access for development",
    price_monthly: 0,
    price_yearly: 0,
    currency: "USD",
    features: [
      "krea_t2i",
      "krea_v2_t2i",
      "gallery",
      "krea_edit",
      "krea_inpaint",
      "faceswap",
      "flux_t2i",
      "klein_i2i",
      "wan_i2v",
      "json_batch",
    ],
    is_public: false,
    sort_order: 999,
  },
];
