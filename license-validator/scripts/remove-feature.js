// Remove a feature key from the database: every plan, every licence, the
// catalogue row itself, and the tab's model and LoRA lists.
//
//   npm run remove-feature -- --feature some_key            # dry run
//   npm run remove-feature -- --feature some_key --apply    # write
//   npm run remove-feature -- --feature some_key --apply --backup-dir D:\backups
//
// A dry run by default: it connects, finds every document that names the
// key and prints exactly what --apply would do, without writing anything.
//
// What --apply does, in this order:
//
//   1. backs up every document it is about to change — the plans, the
//      licences, the features row and the feature_assets row, whole — to
//      one timestamped EJSON file, and reads that file back before
//      touching anything. The file goes OUTSIDE the repository (default
//      ~/krea2-db-backups), because it holds licence keys in plain text;
//   2. $pull the key from plans.features;
//   3. $pull it from licenses.features and licenses.features_extra;
//   4. deletes the features document whose _id is the key;
//   5. deletes the feature_assets document whose _id is the key — the
//      tab's model and LoRA lists. The loras and models records themselves
//      are left alone: they are shared between tabs, and one that no
//      remaining feature lists is simply offered nowhere.
//
// Stored values are matched the way the server reads them (normalizeKey):
// case-insensitive, surrounding whitespace ignored, and "-" or a space
// standing in for "_". So a hand-typed "Wan-I2V" is found as well as
// "wan_i2v".
//
// One consequence worth knowing before running it. A licence with a
// literal `features` array that named *only* this key is left with
// `features: []`, which the server reads as "grants nothing" — correct,
// since that was all it granted — and not as "fall through to the plan".
// The dry run lists such licences separately.
//
// It refuses to run while src/features.js still lists the key: the next
// seed-catalog would put the row straight back. Remove it from FEATURES
// (and from every plan in DEFAULT_PLANS) first. The same goes for the
// `features` object in data/assets.json, which seed-assets writes into
// feature_assets.
//
// Restoring is a matter of replacing each document from the backup file
// by _id; the file is EJSON, so dates and ids survive the round trip.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, relative, resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

import { BSON } from "mongodb";

import { DB_NAME } from "../src/config.js";
import { collections } from "../src/db.js";
import { FEATURE_KEYS, normalizeKey } from "../src/features.js";
import { DEFAULT_PLANS } from "../src/plans.js";

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
 * `<prefix>-XXXX-…-last4`: enough to tell two keys apart, not enough to use
 * one. A key carries either an `EMBER-` or a `KREA2-` prefix and both stay
 * valid, so this reads the prefix off the key rather than assuming one.
 */
function mask(key) {
  if (typeof key !== "string" || key.length < 8) return "(no key)";
  return `${key.split("-")[0]}-XXXX-…-${key.slice(-4)}`;
}

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const opts = args(process.argv.slice(2));
const apply = opts.apply === true;

if (!opts.feature || opts.feature === true) {
  die(
    "usage: npm run remove-feature -- --feature <key> [--apply] [--backup-dir <dir>]",
    "",
    "Dry run unless --apply is given.",
  );
}
const feature = normalizeKey(opts.feature);
if (!feature) die(`--feature ${JSON.stringify(opts.feature)} is not a usable key`);

// ── Guards ─────────────────────────────────────────────────────────────

const stillSeeded = [];
if (FEATURE_KEYS.includes(feature)) stillSeeded.push("FEATURES in src/features.js");
for (const plan of DEFAULT_PLANS) {
  if ((plan.features || []).includes(feature)) {
    stillSeeded.push(`DEFAULT_PLANS "${plan._id}" in src/plans.js`);
  }
}
const assetsFile = resolve(
  dirname(fileURLToPath(import.meta.url)), "..", "data", "assets.json",
);
try {
  const seededAssets = JSON.parse(readFileSync(assetsFile, "utf8"));
  if (Object.hasOwn(seededAssets.features || {}, feature)) {
    stillSeeded.push("features in data/assets.json");
  }
} catch (err) {
  die(`could not read ${assetsFile}: ${err.message}`);
}
if (stillSeeded.length) {
  die(
    `"${feature}" is still in the seed data, so the next seed-catalog run ` +
      "(or seed-assets) run would restore it:",
    ...stillSeeded.map((where) => `  ${where}`),
    "Remove it there first.",
  );
}

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const backupDir = resolve(
  typeof opts["backup-dir"] === "string"
    ? opts["backup-dir"]
    : join(homedir(), "krea2-db-backups"),
);
const inRepo = relative(repoRoot, backupDir);
if (!inRepo.startsWith("..") && !isAbsolute(inRepo)) {
  die(
    `--backup-dir ${backupDir} is inside the repository.`,
    "The backup holds licence keys in plain text; put it somewhere git " +
      "cannot pick it up.",
  );
}

// ── Survey ─────────────────────────────────────────────────────────────

// normalizeKey lowercases, trims and turns runs of "-" or whitespace into
// "_". The inverse, as a pattern for the stored (un-normalised) value.
const pattern = new RegExp(
  `^\\s*${feature.split("_").map(escapeRegex).join("[-_\\s]+")}\\s*$`,
  "i",
);

const { plans, licenses, features, feature_assets } = await collections();

const planDocs = await plans.find({ features: pattern }).toArray();
const licenseDocs = await licenses
  .find({ $or: [{ features: pattern }, { features_extra: pattern }] })
  .toArray();
const featureDoc = await features.findOne({ _id: feature });
const assetsDoc = await feature_assets.findOne({ _id: feature });

const inFeatures = licenseDocs.filter(
  (doc) => Array.isArray(doc.features) && doc.features.some((v) => pattern.test(v)),
);
const inExtra = licenseDocs.filter(
  (doc) =>
    Array.isArray(doc.features_extra) &&
    doc.features_extra.some((v) => pattern.test(v)),
);
const emptied = inFeatures.filter((doc) =>
  doc.features.every((v) => pattern.test(v)),
);

console.log(`database   ${DB_NAME}`);
console.log(`feature    ${feature}   (matching ${pattern})`);
console.log(`mode       ${apply ? "APPLY" : "dry run — nothing will be written"}`);
console.log("");
console.log(
  `plans.updateMany({ features: ${pattern} }, { $pull: { features: ${pattern} } })`,
);
console.log(`  matches ${planDocs.length}`);
for (const plan of planDocs) console.log(`    ${plan._id}`);
console.log(
  `licenses.updateMany({ features: ${pattern} }, { $pull: { features: ${pattern} } })`,
);
console.log(`  matches ${inFeatures.length}`);
for (const doc of inFeatures) {
  const note = emptied.includes(doc) ? "  → features: [] (grants nothing)" : "";
  console.log(`    ${mask(doc.key)}  plan=${doc.plan_id ?? "-"}${note}`);
}
console.log(
  `licenses.updateMany({ features_extra: ${pattern} }, { $pull: { features_extra: ${pattern} } })`,
);
console.log(`  matches ${inExtra.length}`);
for (const doc of inExtra) console.log(`    ${mask(doc.key)}  plan=${doc.plan_id ?? "-"}`);
console.log(`features.deleteOne({ _id: "${feature}" })`);
console.log(
  `  matches ${featureDoc ? 1 : 0}` +
    (featureDoc
      ? `    (${featureDoc.name ?? "?"}, enabled=${featureDoc.enabled})`
      : ""),
);
console.log(`feature_assets.deleteOne({ _id: "${feature}" })`);
console.log(
  `  matches ${assetsDoc ? 1 : 0}` +
    (assetsDoc
      ? `    (${(assetsDoc.models || []).length} model(s), ` +
        `${(assetsDoc.loras || []).length} LoRA(s))`
      : ""),
);

const total =
  planDocs.length + licenseDocs.length + (featureDoc ? 1 : 0) +
  (assetsDoc ? 1 : 0);
console.log("");
if (!total) {
  console.log(`Nothing names "${feature}". Nothing to do.`);
  process.exit(0);
}
if (!apply) {
  console.log(
    `${total} document(s) would change. Re-run with --apply to write; ` +
      `the backup would go to ${backupDir}.`,
  );
  process.exit(0);
}

// ── Backup, then write ─────────────────────────────────────────────────

mkdirSync(backupDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const backupPath = join(backupDir, `remove-feature-${feature}-${stamp}.json`);
const backup = {
  database: DB_NAME,
  feature,
  taken_at: new Date(),
  plans: planDocs,
  licenses: licenseDocs,
  features: featureDoc ? [featureDoc] : [],
  feature_assets: assetsDoc ? [assetsDoc] : [],
};
writeFileSync(
  backupPath,
  BSON.EJSON.stringify(backup, null, 2, { relaxed: false }),
  { encoding: "utf8", mode: 0o600 },
);
// Read it back before writing anything: a backup that cannot be parsed
// is not a backup.
const check = BSON.EJSON.parse(readFileSync(backupPath, "utf8"), { relaxed: false });
if (
  check.plans.length !== planDocs.length ||
  check.licenses.length !== licenseDocs.length ||
  check.features.length !== (featureDoc ? 1 : 0) ||
  check.feature_assets.length !== (assetsDoc ? 1 : 0)
) {
  die(`backup ${backupPath} did not read back intact — nothing written.`);
}
console.log(`backup     ${backupPath}`);

const now = new Date();
const planResult = await plans.updateMany(
  { features: pattern },
  { $pull: { features: pattern }, $set: { updated_at: now } },
);
const featuresResult = await licenses.updateMany(
  { features: pattern },
  { $pull: { features: pattern } },
);
const extraResult = await licenses.updateMany(
  { features_extra: pattern },
  { $pull: { features_extra: pattern } },
);
const deleteResult = await features.deleteOne({ _id: feature });
const assetsResult = await feature_assets.deleteOne({ _id: feature });

console.log(
  `plans              matched ${planResult.matchedCount}, modified ${planResult.modifiedCount}`,
);
console.log(
  `licenses.features  matched ${featuresResult.matchedCount}, modified ${featuresResult.modifiedCount}`,
);
console.log(
  `licenses.extra     matched ${extraResult.matchedCount}, modified ${extraResult.modifiedCount}`,
);
console.log(`features           deleted ${deleteResult.deletedCount}`);
console.log(`feature_assets     deleted ${assetsResult.deletedCount}`);
console.log(
  "\nThe running service caches plans and features for a minute; it " +
    "picks this up on its own.",
);
process.exit(0);
