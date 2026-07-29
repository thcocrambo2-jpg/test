// Which tabs a license grants.
//
// A license document may carry a `features` array naming the feature keys
// that key is entitled to. It is the *only* switch — the app has no
// environment variable that can turn a tab on, so what is written here is
// what the customer gets.
//
//   features: ["single", "v2", "gallery", "wan"]   exactly those tabs
//   features: []                                   nothing (a dead key)
//   field absent / null                            the app's own defaults
//
// The absent case is what keeps every key issued before this existed
// working: the client falls back to its registry defaults (single, v2,
// gallery) and says so in its log. Writing an explicit array is always
// better — absent means "whatever that build happens to default to",
// which is a moving target across releases.

// Mirrors the registry in the app's features.py. That file is the source
// of truth for what a key *means*; this list exists so issue-key can
// reject a typo at the moment it is made, rather than shipping a customer
// a key with a tab silently missing.
//
// Deliberately used at issue time only — never to filter what /v1/acquire
// returns. The two files deploy separately, so an app that ships a new
// tab before this list is updated must still be able to grant it.
export const FEATURE_KEYS = [
  "single",
  "v2",
  "gallery",
  "edit",
  "inpaint",
  "faceswap",
  "flux",
  "klein",
  "wan",
  "json_batch",
];

/**
 * The features array to hand a client: a clean list, or null for "no
 * opinion" (field absent, null, or not an array at all).
 *
 * Unknown keys are passed through, not dropped — see above. The client
 * warns about anything it does not recognise.
 */
export function normalizeFeatures(value) {
  if (!Array.isArray(value)) return null;
  const out = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    const key = item.trim().toLowerCase().replace(/[-\s]+/g, "_");
    if (key && !out.includes(key)) out.push(key);
  }
  return out;
}

/**
 * Parse a --features value from the CLI into a stored array.
 *
 * "all" and "none" are expanded here rather than stored as sentinels, so
 * a license document is always an explicit list and reading one never
 * requires knowing what "all" meant on the day it was issued.
 *
 * Returns { features } or { error }.
 */
export function parseFeatureArg(raw) {
  if (raw === true || raw === undefined) {
    return { error: '--features needs a value, e.g. --features "wan,flux"' };
  }
  const tokens = String(raw)
    .split(/[,;\s]+/)
    .map((t) => t.trim().toLowerCase().replace(/[-\s]+/g, "_"))
    .filter(Boolean);

  const out = [];
  for (const token of tokens) {
    if (token === "all") {
      for (const key of FEATURE_KEYS) if (!out.includes(key)) out.push(key);
      continue;
    }
    if (token === "none") {
      out.length = 0;
      continue;
    }
    if (!FEATURE_KEYS.includes(token)) {
      return {
        error:
          `unknown feature ${JSON.stringify(token)}. ` +
          `Known features: ${FEATURE_KEYS.join(", ")}`,
      };
    }
    if (!out.includes(token)) out.push(token);
  }
  // Keep registry order rather than the order they were typed, so two
  // equivalent keys compare equal by eye in the admin listing.
  return { features: FEATURE_KEYS.filter((key) => out.includes(key)) };
}
