// Write data/assets.json into the loras, models and feature_assets
// collections.
//
//   npm run seed-assets
//   npm run seed-assets -- --dry-run
//
// Idempotent — records are keyed by their id, so re-running after editing
// the file updates rather than duplicates, and a second run in a row
// changes nothing.
//
// ── What the file is ───────────────────────────────────────────────────
//
// data/assets.json is the model and LoRA catalogue as the repo knows it,
// in exactly the shape POST /v1/catalog answers (minus `ok`) — which is
// also why the pod can read it directly when KREA2_CATALOG_FILE points at
// it, for dry runs and tests. Seeding it makes the database hold the same
// thing; from then on the database is what pods get, and small edits (a
// mirror location, a LoRA switched off) are `npm run assets` or the admin
// routes rather than a file edit.
//
// ── The split between content and decisions ────────────────────────────
//
// Same as seed-presets. What a record *is* — name, file, source, mirror,
// strengths, steps — is $set, so editing it in the file and re-running
// updates it. `enabled` and `sort_order` are $setOnInsert: they are
// decisions made about a record once it exists, so a LoRA you switched off
// or moved stays that way across a re-seed.
//
// One consequence worth knowing: `mirror` is content, so a mirror recorded
// by the mirror script (POST /v1/admin/loras) is overwritten by the file's
// value on the next seed run unless the file is updated to match — the
// same direction as every other seeded collection here. A `mirror: null`
// in the file does not count as a value, though: it leaves a stored mirror
// alone, because "the file does not know one" is not "there is none".
//
// Feature lists are replaced whole from the file. They are content too:
// the order of a dropdown is exactly what the file says it is.
//
// ── Validate before writing anything ───────────────────────────────────
//
// All of it, and only then write — the rule every seed script here keeps.
// A half-seeded catalogue is worse than an unseeded one: a feature list
// that names LoRAs whose records did not make it is a tab with holes in
// it, and you would have to work out which rows landed before re-running.
//
// It never deletes. A record dropped from the file stays in the database;
// switch it off with `npm run assets -- --disable-lora <id>`.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { collections, ensureIndexes } from "../src/db.js";
import {
  emptyModelsProblem,
  featureProblem,
  idListProblems,
  loraProblems,
  modelProblems,
} from "../src/assets.js";

const dryRun = process.argv.includes("--dry-run");
const FILE = join(dirname(fileURLToPath(import.meta.url)), "..", "data", "assets.json");

function die(...lines) {
  for (const line of lines) console.error(line);
  process.exit(1);
}

let document;
try {
  document = JSON.parse(readFileSync(FILE, "utf8"));
} catch (err) {
  die(`could not read ${FILE}: ${err.message}`);
}

const seedLoras = Array.isArray(document.loras) ? document.loras : null;
const seedModels = Array.isArray(document.models) ? document.models : null;
const seedFeatures =
  document.features && typeof document.features === "object" &&
  !Array.isArray(document.features)
    ? document.features
    : null;

// ── Validate ───────────────────────────────────────────────────────────

const problems = [];
if (!seedLoras) problems.push("loras must be an array");
if (!seedModels) problems.push("models must be an array");
if (!seedFeatures) problems.push("features must be an object");
if (problems.length) die(...problems, `\n${problems.length} problem(s) — nothing written.`);

const loraIds = new Set();
const loraFiles = new Map();
for (const [index, row] of seedLoras.entries()) {
  const where = `loras[${index}] ${JSON.stringify(row?.id)}`;
  for (const problem of loraProblems(row)) problems.push(`${where}: ${problem}`);
  // Two records sharing an id would make the second silently overwrite the
  // first and the run report two successes; two sharing a file would fail
  // the unique index half-way through the write.
  if (loraIds.has(row?.id)) problems.push(`${where}: duplicate id`);
  loraIds.add(row?.id);
  if (typeof row?.file === "string") {
    if (loraFiles.has(row.file)) {
      problems.push(`${where}: file ${row.file} is also ${loraFiles.get(row.file)}`);
    }
    loraFiles.set(row.file, row.id);
  }
}

const modelIds = new Set();
for (const [index, row] of seedModels.entries()) {
  const where = `models[${index}] ${JSON.stringify(row?.id)}`;
  for (const problem of modelProblems(row, { loraIds })) {
    problems.push(`${where}: ${problem}`);
  }
  if (modelIds.has(row?.id)) problems.push(`${where}: duplicate id`);
  modelIds.add(row?.id);
}

for (const [key, lists] of Object.entries(seedFeatures)) {
  const where = `features.${key}`;
  const bad = featureProblem(key);
  if (bad) problems.push(`${where}: ${bad}`);
  if (!lists || typeof lists !== "object") {
    problems.push(`${where}: must be {models, loras}`);
    continue;
  }
  for (const problem of idListProblems(lists.models, modelIds, "models")) {
    problems.push(`${where}: ${problem}`);
  }
  const empty = emptyModelsProblem(key, lists.models);
  if (empty) problems.push(`${where}: ${empty}`);
  for (const problem of idListProblems(lists.loras, loraIds, "loras")) {
    problems.push(`${where}: ${problem}`);
  }
}

if (problems.length) {
  die(...problems, `\n${problems.length} problem(s) — nothing written.`);
}

// ── Write ──────────────────────────────────────────────────────────────

const { loras, models, feature_assets } = await collections();
if (!dryRun) await ensureIndexes();

// Before anything is written: a file already held by a *different* stored
// LoRA would fail the unique index part-way through the loop below, which
// is exactly the half-seeded state validation exists to prevent.
for (const row of seedLoras) {
  const holder = await loras.findOne({ file: row.file, _id: { $ne: row.id } });
  if (holder) {
    problems.push(
      `loras ${JSON.stringify(row.id)}: file ${row.file} is already stored as ` +
        `LoRA ${JSON.stringify(holder._id)}`,
    );
  }
}
if (problems.length) {
  die(...problems, `\n${problems.length} problem(s) — nothing written.`);
}

/** The content half of a seed record: everything but the decisions. */
function content(row) {
  const { id, enabled, sort_order, ...rest } = row;
  if (rest.mirror === null) delete rest.mirror;   // see the header
  return rest;
}

async function upsertAll(collection, rows, withOrder) {
  let created = 0;
  let updated = 0;
  for (const row of rows) {
    if (dryRun) {
      if (await collection.findOne({ _id: row.id })) updated += 1;
      else created += 1;
      continue;
    }
    const now = new Date();
    const onInsert = { enabled: row.enabled !== false, created_at: now };
    if (withOrder) onInsert.sort_order = row.sort_order ?? 0;
    if (row.mirror === null) onInsert.mirror = null;
    const result = await collection.updateOne(
      { _id: row.id },
      { $set: { ...content(row), updated_at: now }, $setOnInsert: onInsert },
      { upsert: true },
    );
    if (result.upsertedCount) created += 1;
    else updated += 1;
  }
  return { created, updated };
}

const loraResult = await upsertAll(loras, seedLoras, true);
const modelResult = await upsertAll(models, seedModels, false);

let featuresCreated = 0;
let featuresUpdated = 0;
for (const [key, lists] of Object.entries(seedFeatures)) {
  if (dryRun) {
    if (await feature_assets.findOne({ _id: key })) featuresUpdated += 1;
    else featuresCreated += 1;
    continue;
  }
  const now = new Date();
  const result = await feature_assets.updateOne(
    { _id: key },
    {
      $set: { models: lists.models, loras: lists.loras, updated_at: now },
      $setOnInsert: { created_at: now },
    },
    { upsert: true },
  );
  if (result.upsertedCount) featuresCreated += 1;
  else featuresUpdated += 1;
}

const suffix = dryRun ? "  (dry run — nothing written)" : "";
console.log(`loras            ${loraResult.created} created, ${loraResult.updated} updated${suffix}`);
console.log(`models           ${modelResult.created} created, ${modelResult.updated} updated${suffix}`);
console.log(`feature_assets   ${featuresCreated} created, ${featuresUpdated} updated${suffix}`);

// What pods would be sent now — the counts matter more than the write did.
const liveLoras = await loras.countDocuments({ enabled: { $ne: false } });
const liveModels = await models.countDocuments({ enabled: { $ne: false } });
console.log(
  `\ncatalogue now: ${await loras.countDocuments({})} LoRA(s) (${liveLoras} on), ` +
    `${await models.countDocuments({})} model(s) (${liveModels} on), ` +
    `${await feature_assets.countDocuments({})} feature list(s)`,
);

console.log("\nNext: npm run assets             (list, enable, disable, edit lists)");
process.exit(0);
