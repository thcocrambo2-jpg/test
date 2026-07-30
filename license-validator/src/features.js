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
// ── Keys are permanent, names are not ──────────────────────────────────
//
// `key` is written into license documents and compiled into every binary
// that has shipped. Renaming one silently drops that tab for anyone on an
// older build — the client logs "unknown feature, ignoring" and carries on
// without it. `name`, `description` and `category` are display-only and
// safe to reword at any time; that is where naming should be fixed.
//
// There is deliberately no alias map for renamed keys. The one rename this
// catalogue has had happened before any key was issued, so nothing needed
// translating — and a permanent map that translates nothing is a trap for
// whoever reads it next. If a key ever has to change after launch, the
// honest fix is to reissue the affected licenses.
//
// This registry mirrors the one in the app's features.py. That file is the
// source of truth for what a key *means* (which weights it downloads, which
// tab it builds); this one exists so issue-key and seed-catalog can reject
// a typo at the moment it is made rather than shipping a customer a key
// with a tab silently missing.
//
// Deliberately used at issue time only — never to filter what /v1/acquire
// returns. The two deploy separately, so an app that ships a new tab before
// this list is updated must still be able to grant it.

export const FEATURES = [
  {
    key: "krea_t2i",
    name: "Single / Simple Batch",
    description: "Text-to-image generation with Krea 2",
    category: "generation",
    sort_order: 10,
  },
  {
    key: "krea_v2_t2i",
    name: "Krea 2 V2",
    description: "Advanced Krea 2 V2 pipeline (Turbo / Raw)",
    category: "generation",
    sort_order: 20,
  },
  {
    key: "gallery",
    name: "Gallery",
    description: "View and download everything you have generated",
    category: "tools",
    sort_order: 30,
  },
  {
    key: "krea_edit",
    name: "Krea Edit — Instruction",
    description:
      "Edit an image by describing the change, using Krea 2 " +
      "(no mask painting)",
    category: "editing",
    sort_order: 40,
  },
  {
    key: "krea_inpaint",
    name: "Krea Inpaint — Img2Img",
    description: "Mask-based inpainting and image-to-image with Krea 2",
    category: "editing",
    sort_order: 50,
  },
  {
    key: "faceswap",
    name: "Face Swap (ReActor)",
    description: "Fast face swapping using ReActor",
    category: "editing",
    sort_order: 60,
  },
  {
    key: "flux_t2i",
    name: "Flux 2 — Text to Image",
    description: "Text-to-image generation with Flux 2 Dev (32B)",
    category: "generation",
    sort_order: 70,
  },
  {
    key: "klein_i2i",
    name: "Klein Edit — Image to Image",
    description:
      "Image-to-image editing with FLUX.2 Klein 9B, and combining two " +
      "source images",
    category: "editing",
    sort_order: 80,
  },
  {
    key: "wan_i2v",
    name: "Wan 2.2 Video",
    description: "Image-to-video generation with Wan 2.2",
    category: "video",
    sort_order: 90,
  },
  {
    key: "json_batch",
    name: "JSON Advanced Batch",
    description: "Advanced batch generation driven by a pasted JSON graph",
    category: "tools",
    sort_order: 100,
  },
];

export const FEATURE_KEYS = FEATURES.map((feature) => feature.key);

const RANK = new Map(FEATURE_KEYS.map((key, index) => [key, index]));

/**
 * Registry order, with anything unrecognised kept and pushed to the end.
 *
 * Sorting rather than filtering is the point: an unknown key is either a
 * tab this list has not caught up with or one the app has retired, and
 * neither is this service's call to make. It only decides the order two
 * equivalent entitlements are written in, so they compare equal by eye in
 * the admin listing.
 */
export function sortByRegistry(keys) {
  return [...keys].sort((a, b) => {
    const ra = RANK.has(a) ? RANK.get(a) : RANK.size;
    const rb = RANK.has(b) ? RANK.get(b) : RANK.size;
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
function applyToken(token, out) {
  if (token === "all") {
    for (const key of FEATURE_KEYS) if (!out.includes(key)) out.push(key);
    return null;
  }
  if (token === "none") {
    out.length = 0;
    return null;
  }
  if (!FEATURE_KEYS.includes(token)) {
    return (
      `unknown feature ${JSON.stringify(token)}. ` +
      `Known features: ${FEATURE_KEYS.join(", ")}`
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
 * Returns { features } or { error }.
 */
export function parseFeatureArg(raw) {
  if (raw === true || raw === undefined) {
    return { error: '--features needs a value, e.g. --features "wan,flux"' };
  }
  const tokens = String(raw).split(/[,;\s]+/).map(normalizeKey).filter(Boolean);

  const out = [];
  for (const token of tokens) {
    const error = applyToken(token, out);
    if (error) return { error };
  }
  return { features: sortByRegistry(out) };
}
