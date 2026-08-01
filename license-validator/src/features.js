// The feature catalogue — what a tab is called and what it costs to grant.
//
// A license names the tabs it grants either directly (`features`) or, more
// usually, through a plan (`plan_id` → `plans.features`). Either way the
// wire format is unchanged: /v1/acquire returns a flat `features` array and
// the Python client knows nothing about plans. See plans.js.
//
//   features: ["single", "v2", "gallery", "wan"]   exactly those tabs
//   features: []                                   nothing (a dead key)
//   field absent / null                            fall through to plan_id
//
// ── The database is the source of truth ────────────────────────────────
//
// The `features` collection is what the API serves — names, descriptions,
// categories and tab labels all come from allFeatures() below, the same
// way plans do. Renaming a tab is therefore an edit in Atlas, not a
// redeploy, which is the whole point: the two things a customer reads
// (the pricing page and the tab strip) are now editable in the place the
// prices already are.
//
// FEATURES below is seed data and a fallback, in that order. seed-catalog
// writes it into the collection, and allFeatures() serves it verbatim if
// the collection is empty — a deployment that has never been seeded then
// still answers with sane names instead of blanking every tab title.
//
// ── Keys are permanent, names are not ──────────────────────────────────
//
// `key` is written into license documents and compiled into every binary
// that has shipped. Renaming one silently drops that tab for anyone on an
// older build — the client logs "unknown feature, ignoring" and carries on
// without it. `name`, `description`, `category` and `tab_label` are
// display-only and safe to reword at any time, in Atlas or here.
//
// There is deliberately no alias map for renamed keys. The one rename this
// catalogue has had happened before any key was issued, so nothing needed
// translating — and a permanent map that translates nothing is a trap for
// whoever reads it next. If a key ever has to change after launch, the
// honest fix is to reissue the affected licenses.
//
// `name` vs `tab_label`: the pricing page wants prose that says what a tab
// is ("Krea Inpaint — Img2Img"), the tab strip wants something short enough
// to sit in a row of ten ("Inpaint / Img2Img"). They were separate strings
// in separate repos before this collection existed; keeping both fields
// means unifying the source without flattening the two registers into one
// awkward compromise. tab_label is optional — the client falls back to
// `name`, then to its own built-in label.
//
// Still never used to filter what /v1/acquire returns. The app and this
// service deploy separately, so a license granting a tab that shipped
// before this catalogue knew about it must still be able to grant it.

import { collections } from "./db.js";

// Same TTL and the same globalThis trick as plans.js, for the same reason:
// warm serverless invocations reuse module scope, cold ones do not, so a
// cold start pays one small query and everything after it pays nothing.
// An edit in Atlas reaches running instances within this window.
const FEATURE_TTL_MS = 60_000;

let cache = globalThis.__krea2Features;
if (!cache) cache = globalThis.__krea2Features = { at: 0, byKey: null };

export const FEATURES = [
  {
    key: "krea_t2i",
    name: "Krea2",
    tab_label: "🎨 Krea2",
    description: "Create images from text prompts",
    category: "generation",
    sort_order: 10,
  },
  {
    key: "krea_v2_t2i",
    name: "Krea2 V2",
    tab_label: "🔶 Krea2 V2",
    description: "Better quality and more control over your generations",
    category: "generation",
    sort_order: 20,
  },
  {
    key: "gallery",
    name: "Gallery",
    tab_label: "🖼️ Gallery",
    description: "View and download everything you have generated",
    category: "tools",
    sort_order: 30,
  },
  {
    key: "krea_edit",
    name: "Krea2 Edit",
    tab_label: "✨ Krea2 Edit",
    description: "Change any image just by describing what you want",
    category: "editing",
    sort_order: 40,
  },
  {
    key: "krea_inpaint",
    name: "Krea2 Inpaint",
    tab_label: "🖌️ Krea2 Inpaint",
    description: "Edit only specific parts of an image",
    category: "editing",
    sort_order: 50,
  },
  {
    key: "faceswap",
    name: "Face Swap",
    tab_label: "🎭 Face Swap",
    description: "Easily replace faces in any image",
    category: "editing",
    sort_order: 60,
  },
  {
    key: "flux_t2i",
    name: "Flux2D",
    tab_label: "🌊 Flux2D",
    description: "Create high-quality images with Flux 2 Dev",
    category: "generation",
    sort_order: 70,
  },
  {
    key: "klein_i2i",
    name: "Klein Edit",
    tab_label: "🧩 Klein Edit",
    description: "Edit images or combine two images together",
    category: "editing",
    sort_order: 80,
  },
  {
    key: "wan_i2v",
    name: "Wan Video",
    tab_label: "🎬 Wan Video",
    description: "Turn your images into short videos",
    category: "video",
    sort_order: 90,
  },
  {
    key: "json_batch",
    name: "Krea2 Batch",
    tab_label: "📦 Krea2 Batch",
    description: "Generate many images at once using advanced controls",
    category: "tools",
    sort_order: 100,
  },
  {
    key: "community_prompts",
    name: "Prompt Library",
    tab_label: "🌟 Prompt Library",
    description:
      "Browse ready-made prompts and load one into Krea2 or Krea2 V2 " +
      "with every setting already dialled in",
    category: "tools",
    // 65, between faceswap (60) and flux_t2i (70), so it reads with the
    // rest of the everyday set rather than trailing the video and batch
    // tabs. This number is the *only* thing that decides where it appears
    // in a plan's feature list — /v1/plans sorts each plan's keys by it —
    // so keep it in step with the collection, or the next seed-catalog run
    // reverts an edit made in Atlas.
    sort_order: 65,
  },
];

export const FEATURE_KEYS = FEATURES.map((feature) => feature.key);

/**
 * Every feature, by key, in sort_order — from Mongo, cached like plans.
 *
 * Falls back to the seed registry when the collection is empty, which is
 * the un-seeded deployment: serving nothing there would blank every tab
 * title and every line of the pricing page, and "we forgot to run
 * seed-catalog" should not look to a customer like a broken product.
 *
 * A collection with rows in it is trusted completely, including rows this
 * build has never heard of. That is what "the database is the source of
 * truth" costs and buys: a tab can be renamed, or a new one described,
 * without shipping anything.
 */
export async function allFeatures() {
  if (cache.byKey && Date.now() - cache.at < FEATURE_TTL_MS) return cache.byKey;
  const { features } = await collections();
  const rows = await features.find({}).sort({ sort_order: 1 }).toArray();
  cache.byKey = rows.length
    ? new Map(rows.map((row) => [row._id, { ...row, key: row._id }]))
    : new Map(FEATURES.map((feature) => [feature.key, feature]));
  cache.at = Date.now();
  return cache.byKey;
}

/** Drop the cache, so the next read hits Mongo. For scripts and tests. */
export function invalidateFeatures() {
  cache.byKey = null;
  cache.at = 0;
}

/** Feature keys in catalogue order — what sortByRegistry ranks against. */
export async function featureOrder() {
  return [...(await allFeatures()).keys()];
}

/**
 * Catalogue order, with anything unrecognised kept and pushed to the end.
 *
 * Sorting rather than filtering is the point: an unknown key is either a
 * tab the catalogue has not caught up with or one the app has retired, and
 * neither is this service's call to make. It only decides the order two
 * equivalent entitlements are written in, so they compare equal by eye in
 * the admin listing.
 *
 * `order` defaults to the seed registry so the synchronous callers (the
 * issue-key CLI) keep working without a database round trip; the request
 * path passes featureOrder() so the ordering follows Atlas too.
 */
export function sortByRegistry(keys, order = FEATURE_KEYS) {
  const rank = new Map(order.map((key, index) => [key, index]));
  return [...keys].sort((a, b) => {
    const ra = rank.has(a) ? rank.get(a) : rank.size;
    const rb = rank.has(b) ? rank.get(b) : rank.size;
    return ra === rb ? a.localeCompare(b) : ra - rb;
  });
}

/**
 * One feature key, normalised. Returns "" for anything unusable.
 *
 * An unrecognised key is returned as-is rather than dropped: this service
 * and the app deploy separately, so a key naming a tab that shipped before
 * this file was updated has to survive the trip.
 */
export function normalizeKey(value) {
  if (typeof value !== "string") return "";
  return value.trim().toLowerCase().replace(/[-\s]+/g, "_");
}

/**
 * A features array as a clean list, or null for "no opinion" (field
 * absent, null, or not an array at all).
 *
 * Unknown keys are passed through, not dropped — see above. The client
 * warns about anything it does not recognise.
 */
export function normalizeFeatures(value) {
  if (!Array.isArray(value)) return null;
  const out = [];
  for (const item of value) {
    const key = normalizeKey(item);
    if (key && !out.includes(key)) out.push(key);
  }
  return out;
}

/** Apply one CLI token to the accumulating list. Returns an error, or null. */
function applyToken(token, out, known) {
  if (token === "all") {
    for (const key of known) if (!out.includes(key)) out.push(key);
    return null;
  }
  if (token === "none") {
    out.length = 0;
    return null;
  }
  if (!known.includes(token)) {
    return (
      `unknown feature ${JSON.stringify(token)}. ` +
      `Known features: ${known.join(", ")}`
    );
  }
  if (!out.includes(token)) out.push(token);
  return null;
}

/**
 * Parse a --features / --features-extra value from the CLI.
 *
 * "all" and "none" are expanded here rather than stored as sentinels, so a
 * license document is always an explicit list and reading one never
 * requires knowing what "all" meant on the day it was issued.
 *
 * `known` is the catalogue to validate against — issue-key passes the keys
 * from Mongo, so a feature added in Atlas can be granted the same day
 * rather than waiting for this file to catch up. It defaults to the seed
 * registry for any caller that has no database handle.
 *
 * Returns { features } or { error }.
 */
export function parseFeatureArg(raw, known = FEATURE_KEYS) {
  if (raw === true || raw === undefined) {
    return { error: '--features needs a value, e.g. --features "wan,flux"' };
  }
  const tokens = String(raw).split(/[,;\s]+/).map(normalizeKey).filter(Boolean);

  const out = [];
  for (const token of tokens) {
    const error = applyToken(token, out, known);
    if (error) return { error };
  }
  return { features: sortByRegistry(out, known) };
}
