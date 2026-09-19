// Manage the model and LoRA catalogue the Krea tabs are built from.
//
//   npm run assets                                      # everything
//   npm run assets -- --loras | --models | --features   # one part
//   npm run assets -- --enable-lora   realism-v2
//   npm run assets -- --disable-lora  realism-v2
//   npm run assets -- --enable-model  krea2-raw-fp8
//   npm run assets -- --disable-model krea2-raw-fp8
//   npm run assets -- --feature krea_t2i --add-lora    pawg [--at 3]
//   npm run assets -- --feature krea_t2i --remove-lora pawg
//   npm run assets -- --feature krea_v2_t2i --add-model    krea2-raw-fp8 [--at 1]
//   npm run assets -- --feature krea_v2_t2i --remove-model krea2-raw-fp8
//   npm run assets -- --mirror realism-v2 --repo owner/name --path loras/x.safetensors
//   npm run assets -- --mirror realism-v2 --clear
//
// ── What lives where ───────────────────────────────────────────────────
//
// `loras` and `models` hold one record per id: what the file is called,
// where it downloads from, and its defaults. `feature_assets` holds, per
// tab, which of those ids its dropdowns offer and in what order — the
// first model being the one the tab opens on. A record that is in no
// feature's list is offered nowhere; a record that is switched off is
// offered nowhere either, but keeps its place in every list, so switching
// it back on puts it back exactly where it was.
//
// --at is a 1-based position in the feature's list. Without it an id is
// added at the end; with it an id already in the list moves there. For
// models, position 1 is the tab's default.
//
// New records and whole-record edits are data/assets.json + `npm run
// seed-assets`, or POST /v1/admin/loras and /v1/admin/models. This script
// is for the small edits you make on a running catalogue.
//
// Nothing here reaches a running pod — a pod reads the catalogue once at
// startup — so an edit is picked up on each pod's next start.
//
// Talks to Mongo directly rather than through the admin HTTP routes, like
// every other script here — one less thing to configure, and it works
// against a deployment whose ADMIN_TOKEN you have not set. Every value is
// checked by src/assets.js, the same rules those routes apply.

import { collections } from "../src/db.js";
import {
  featureProblem,
  idListProblems,
  idProblem,
  loraProblems,
  modelProblems,
} from "../src/assets.js";

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

const opts = args(process.argv.slice(2));
const { loras, models, feature_assets } = await collections();

const isOn = (row) => row.enabled !== false;

// ── Listing ────────────────────────────────────────────────────────────

async function listLoras() {
  const rows = await loras.find({}).sort({ sort_order: 1, _id: 1 }).toArray();
  console.log(`\nloras (${rows.length}, ${rows.filter(isOn).length} on)`);
  if (!rows.length) {
    console.log("  none yet — npm run seed-assets writes data/assets.json");
    return;
  }
  console.log("  st   ord  id                     strength  mirror  file");
  for (const row of rows) {
    const bad = loraProblems(row).length ? "  ⚠️  invalid — not served" : "";
    console.log(
      `  ${(isOn(row) ? "on" : "OFF").padEnd(4)} ${String(row.sort_order ?? 0).padStart(4)}  ` +
        `${String(row._id).padEnd(22)} ${String(row.default_strength ?? "-").padEnd(8)}  ` +
        `${(row.mirror ? "yes" : "-").padEnd(6)}  ${row.file}${bad}`,
    );
  }
}

async function listModels() {
  const rows = await models.find({}).sort({ _id: 1 }).toArray();
  console.log(`\nmodels (${rows.length}, ${rows.filter(isOn).length} on)`);
  if (!rows.length) {
    console.log("  none yet — npm run seed-assets writes data/assets.json");
    return;
  }
  console.log("  st   id                     variant steps  cfg   turbo_lora         file");
  for (const row of rows) {
    const bad = modelProblems(row).length ? "  ⚠️  invalid — not served" : "";
    const turbo = row.turbo_lora ? `${row.turbo_lora.lora}@${row.turbo_lora.strength}` : "-";
    console.log(
      `  ${(isOn(row) ? "on" : "OFF").padEnd(4)} ${String(row._id).padEnd(22)} ` +
        `${String(row.variant ?? "-").padEnd(7)} ${String(row.steps ?? "-").padStart(5)}  ` +
        `${String(row.cfg ?? "-").padEnd(5)} ${turbo.padEnd(18)} ${row.file}${bad}`,
    );
  }
}

async function listFeatures() {
  const [rows, loraRows, modelRows] = await Promise.all([
    feature_assets.find({}).sort({ _id: 1 }).toArray(),
    loras.find({}, { projection: { enabled: 1 } }).toArray(),
    models.find({}, { projection: { enabled: 1 } }).toArray(),
  ]);
  const state = (list) => new Map(list.map((row) => [row._id, isOn(row)]));
  const loraState = state(loraRows);
  const modelState = state(modelRows);
  // An id is shown with why a pod will not get it: switched off, or not a
  // record at all (only a hand edit in Atlas can do the second).
  const mark = (id, known) =>
    !known.has(id) ? `${id} (missing)` : known.get(id) ? id : `${id} (off)`;
  console.log(`\nfeature lists (${rows.length})`);
  if (!rows.length) {
    console.log("  none yet — npm run seed-assets writes data/assets.json");
    return;
  }
  for (const row of rows) {
    console.log(`  ${row._id}`);
    const modelIds = row.models || [];
    const loraIds = row.loras || [];
    console.log(`    models (${modelIds.length}): ` +
      (modelIds.map((id) => mark(id, modelState)).join(", ") || "-"));
    console.log(`    loras  (${loraIds.length}):`);
    for (const [index, id] of loraIds.entries()) {
      console.log(`      ${String(index + 1).padStart(3)}  ${mark(id, loraState)}`);
    }
  }
}

async function list() {
  const only = opts.loras || opts.models || opts.features;
  if (!only || opts.loras) await listLoras();
  if (!only || opts.models) await listModels();
  if (!only || opts.features) await listFeatures();
}

// ── Enable / disable ───────────────────────────────────────────────────

const toggles = [
  ["enable-lora", loras, "LoRA", true, loraProblems],
  ["disable-lora", loras, "LoRA", false, loraProblems],
  ["enable-model", models, "model", true, modelProblems],
  ["disable-model", models, "model", false, modelProblems],
];
for (const [flag, collection, what, enable, check] of toggles) {
  if (!opts[flag]) continue;
  const id = opts[flag];
  if (id === true) die(`--${flag} needs a ${what} id`);
  const row = await collection.findOne({ _id: id });
  if (!row) die(`no ${what} with id ${JSON.stringify(id)}`);
  await collection.updateOne(
    { _id: id },
    { $set: { enabled: enable, updated_at: new Date() } },
  );
  console.log(`${enable ? "enabled" : "disabled"} ${what} ${id}`);
  const problems = check(row);
  if (enable && problems.length) {
    // Said out loud: switched on is not the same as served. POST
    // /v1/catalog skips a record that fails validation, however it is set.
    console.log(`  ⚠️  but it is invalid and will not be served: ${problems.join("; ")}`);
  }
  if (!enable && what === "model") {
    const first = await feature_assets
      .find({ "models.0": id }, { projection: { _id: 1 } })
      .toArray();
    for (const feature of first) {
      console.log(
        `  ⚠️  it was ${feature._id}'s default — that tab now opens on the ` +
          "next enabled model in its list",
      );
    }
  }
  await list();
  process.exit(0);
}

// ── Mirror ─────────────────────────────────────────────────────────────
//
// Where our own copy of a LoRA's file lives. Pods try it before the
// record's source, so a CivitAI version that is pulled or rate-limited
// does not stop a pod starting. The mirror script records this itself
// through POST /v1/admin/loras; this is the by-hand version of that call.

if (opts.mirror) {
  const id = opts.mirror;
  if (id === true) die("--mirror needs a LoRA id");
  const row = await loras.findOne({ _id: id });
  if (!row) die(`no LoRA with id ${JSON.stringify(id)}`);
  let mirror;
  if (opts.clear) mirror = null;
  else {
    if (typeof opts.repo !== "string" || typeof opts.path !== "string") {
      die("--mirror needs --repo <owner/name> and --path <path in the repo>, or --clear");
    }
    mirror = { repo: opts.repo, path: opts.path };
  }
  const problems = loraProblems({ id, mirror }, { partial: true });
  if (problems.length) die(...problems);
  await loras.updateOne({ _id: id }, { $set: { mirror, updated_at: new Date() } });
  console.log(
    mirror
      ? `LoRA ${id} mirror → ${mirror.repo} ${mirror.path}`
      : `LoRA ${id} mirror cleared`,
  );
  await listLoras();
  process.exit(0);
}

// ── Feature lists ──────────────────────────────────────────────────────

const listEdits = [
  ["add-lora", "loras", loras, "LoRA", true],
  ["remove-lora", "loras", loras, "LoRA", false],
  ["add-model", "models", models, "model", true],
  ["remove-model", "models", models, "model", false],
];
for (const [flag, field, collection, what, adding] of listEdits) {
  if (!opts[flag]) continue;
  const id = opts[flag];
  if (id === true) die(`--${flag} needs a ${what} id`);
  const badId = idProblem(id);
  if (badId) die(badId);
  const feature = opts.feature;
  if (typeof feature !== "string") die(`--${flag} needs --feature <key>`);
  const badKey = featureProblem(feature);
  if (badKey) die(badKey);

  const doc = await feature_assets.findOne({ _id: feature });
  if (!doc && field === "loras") {
    // Creating it with LoRAs alone would leave it with no model, which is
    // a tab with nothing to run. A feature starts with its default model.
    die(`${feature} has no lists yet — --add-model its default model first`);
  }
  const current = [...(doc?.[field] || [])];
  const next = current.filter((entry) => entry !== id);
  if (adding) {
    // 1-based, and clamped: --at 99 on a list of 20 means "at the end".
    let at = next.length;
    if (opts.at !== undefined) {
      const position = Number.parseInt(opts.at, 10);
      if (!Number.isInteger(position) || position < 1) {
        die("--at must be a position, 1 or more");
      }
      at = Math.min(position - 1, next.length);
    } else if (current.includes(id)) {
      console.log(
        `${feature} already lists ${what} ${id} at position ` +
          `${current.indexOf(id) + 1} — pass --at <n> to move it`,
      );
      process.exit(0);
    }
    next.splice(at, 0, id);
  } else if (next.length === current.length) {
    die(`${feature} does not list ${what} ${id}`);
  }

  // Checked as a whole list, exactly as POST /v1/admin/feature-assets
  // checks one: every id must be a record. Only the list being edited is
  // checked, so an id already broken in the *other* list does not block
  // an unrelated edit.
  const known = new Set(await collection.distinct("_id"));
  const problems = idListProblems(next, known, field);
  if (field === "models" && !next.length) {
    problems.push(
      "models must list at least one model (the first is the default) — " +
        "a feature with nothing to run is npm run remove-feature's job",
    );
  }
  if (problems.length) die(...problems, "nothing written.");

  const now = new Date();
  const onInsert = { created_at: now };
  onInsert[field === "loras" ? "models" : "loras"] = [];
  await feature_assets.updateOne(
    { _id: feature },
    { $set: { [field]: next, updated_at: now }, $setOnInsert: onInsert },
    { upsert: true },
  );
  console.log(
    adding
      ? `${feature}: ${what} ${id} at position ${next.indexOf(id) + 1} of ${next.length}`
      : `${feature}: removed ${what} ${id} (${next.length} left)`,
  );
  if (field === "models" && next[0] !== current[0]) {
    console.log(`  ${feature} now opens on ${next[0]}`);
  }
  await listFeatures();
  process.exit(0);
}

await list();
process.exit(0);
