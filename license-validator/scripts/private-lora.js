// Private LoRAs: offered only to the licences that are granted them.
//
//   npm run private-lora -- list
//   npm run private-lora -- add face-sayara --file face_sayara.safetensors \
//       --name "Sayara" --trigger sayarax [--strength 1] \
//       [--features krea_v2_t2i,krea_t2i]
//   npm run private-lora -- grant  face-sayara --keys KEY1,KEY2
//   npm run private-lora -- revoke face-sayara --keys KEY1,KEY2 | --all
//
// The file is uploaded first, to the PRIVATE builds bucket at
// loras/private/<file>, by the app repo's scripts/r2_upload_lora.py —
// which prints the `add` line to run here.
//
// ── How it is private ──────────────────────────────────────────────────
//
// The record is an ordinary `loras` row whose source is
// {kind: "r2", key: "loras/private/<file>"}. POST /v1/catalog leaves such a
// row out — the record and its place in every feature list — for every
// licence whose `private_loras` does not name its id, so those keys cannot
// even see that it exists. A licence that does name it gets the row with a
// signed download link in its source. Granting is writing the id into the
// licence's `private_loras`; nothing else about the licence changes.
//
// `add` puts the id at the end of each feature's LoRA list (the Krea2 V2
// and Krea2 tabs by default), which is where a granted pod's tab shows it.
// The lists are shared by every licence, and that is safe: an id a licence
// is not sent is cut from its lists too.
//
// A pod reads the catalogue once at startup, so a grant reaches a running
// pod on its next start. A pod on a build older than private LoRAs skips
// the row with a warning and is otherwise unaffected.
//
// Talks to Mongo directly, like every other script here. Every value is
// checked by src/assets.js, the same rules the admin routes apply.

import { collections } from "../src/db.js";
import {
  PRIVATE_LORA_PREFIX,
  featureProblem,
  idProblem,
  isPrivateLora,
  loraProblems,
} from "../src/assets.js";

const DEFAULT_FEATURES = ["krea_v2_t2i", "krea_t2i"];

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) {
      out._.push(argv[i]);
      continue;
    }
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

const list = (value) =>
  typeof value === "string" ? value.split(",").map((s) => s.trim()).filter(Boolean) : [];

// Enough of a key to tell two apart in a listing, not enough to use one.
const shortKey = (key) => (key.length > 12 ? `${key.slice(0, 8)}…${key.slice(-4)}` : key);

const opts = args(process.argv.slice(2));
const [command, id] = opts._;
const { loras, licenses, feature_assets } = await collections();

async function privateRecord(rid) {
  const bad = idProblem(rid);
  if (bad) die(`${bad}.`);
  const row = await loras.findOne({ _id: rid });
  if (!row) die(`No LoRA ${JSON.stringify(rid)} — run \`add\` first.`);
  if (!isPrivateLora(row)) {
    die(`${rid} is a public LoRA (source ${row.source?.kind}); only private ` +
        "(r2) LoRAs are granted per licence.");
  }
  return row;
}

async function licencesFor(keys) {
  if (!keys.length) die("--keys KEY1,KEY2 is required.");
  const found = await licenses
    .find({ key: { $in: keys } }, { projection: { key: 1, name: 1 } })
    .toArray();
  const known = new Set(found.map((row) => row.key));
  const missing = keys.filter((key) => !known.has(key));
  if (missing.length) die(`No licence for: ${missing.map(shortKey).join(", ")}`);
  return found;
}

if (command === "list" || command === undefined) {
  const rows = await loras.find({ "source.kind": "r2" }).sort({ _id: 1 }).toArray();
  console.log(`\nprivate loras (${rows.length})`);
  for (const row of rows) {
    const holders = await licenses
      .find({ private_loras: row._id }, { projection: { key: 1, name: 1 } })
      .toArray();
    const lists = await feature_assets
      .find({ loras: row._id }, { projection: { _id: 1 } })
      .toArray();
    const bad = loraProblems(row).length ? "  ⚠️  invalid — not served" : "";
    console.log(
      `  ${row.enabled === false ? "OFF" : "on "} ${row._id}  ${row.file}` +
        `  trigger=${row.trigger || "-"}${bad}`,
    );
    console.log(`      tabs: ${lists.map((f) => f._id).join(", ") || "none"}`);
    console.log(
      `      granted to ${holders.length}: ` +
        (holders.map((h) => `${h.name || "?"} (${shortKey(h.key)})`).join(", ") || "nobody"),
    );
  }
  process.exit(0);
}

if (command === "add") {
  const rid = id;
  const bad = idProblem(rid);
  if (bad) die(`${bad}.`, "usage: add <id> --file <file> [--name --trigger --strength --features]");
  if (typeof opts.file !== "string") die("--file <name>.safetensors is required.");
  const features = opts.features ? list(opts.features) : DEFAULT_FEATURES;
  for (const feature of features) {
    const problem = featureProblem(feature);
    if (problem) die(`${problem}.`);
  }
  const record = {
    _id: rid,
    name: typeof opts.name === "string" ? opts.name : rid,
    file: opts.file,
    source: { kind: "r2", key: PRIVATE_LORA_PREFIX + opts.file },
    mirror: null,
    trigger: typeof opts.trigger === "string" ? opts.trigger : "",
    default_strength: opts.strength === undefined ? 1.0 : Number(opts.strength),
    enabled: true,
  };
  const problems = loraProblems(record);
  if (problems.length) die(`lora: ${problems.join("; ")}.`);

  const existing = await loras.findOne({ _id: rid });
  if (existing && !isPrivateLora(existing)) {
    die(`${rid} already exists as a public LoRA — pick another id.`);
  }
  const holder = await loras.findOne({ file: record.file, _id: { $ne: rid } });
  if (holder) die(`file ${record.file} is already LoRA ${holder._id}.`);

  const now = new Date();
  const { _id, ...content } = record;
  const [last] = await loras
    .find({}, { projection: { sort_order: 1 } })
    .sort({ sort_order: -1 })
    .limit(1)
    .toArray();
  await loras.updateOne(
    { _id },
    {
      $set: { ...content, updated_at: now },
      $setOnInsert: { created_at: now, sort_order: (Number(last?.sort_order) || 0) + 10 },
    },
    { upsert: true },
  );
  console.log(`${existing ? "updated" : "created"} private lora ${rid} → ${record.source.key}`);

  for (const feature of features) {
    // Only a feature that already has lists: creating one here would make
    // a tab with no model, which the admin route refuses for good reason.
    const result = await feature_assets.updateOne(
      { _id: feature },
      { $addToSet: { loras: rid }, $set: { updated_at: now } },
    );
    console.log(
      result.matchedCount
        ? `  ${feature}: ${result.modifiedCount ? "added" : "already listed"}`
        : `  ${feature}: has no lists — skipped`,
    );
  }
  console.log(`\nNobody is offered it yet. Next:\n  npm run private-lora -- grant ${rid} --keys KEY1,KEY2`);
  process.exit(0);
}

if (command === "grant") {
  const row = await privateRecord(id);
  const found = await licencesFor(list(opts.keys));
  const result = await licenses.updateMany(
    { key: { $in: found.map((l) => l.key) } },
    { $addToSet: { private_loras: row._id } },
  );
  console.log(`granted ${row._id} to ${found.length} licence(s) (${result.modifiedCount} newly):`);
  for (const l of found) console.log(`  ${l.name || "?"} (${shortKey(l.key)})`);
  console.log("Each pod picks it up on its next start.");
  process.exit(0);
}

if (command === "revoke") {
  const row = await privateRecord(id);
  const filter = opts.all === true
    ? { private_loras: row._id }
    : { key: { $in: (await licencesFor(list(opts.keys))).map((l) => l.key) } };
  const result = await licenses.updateMany(filter, { $pull: { private_loras: row._id } });
  console.log(`revoked ${row._id} from ${result.modifiedCount} licence(s).`);
  console.log(
    "A pod that already downloaded the file keeps it on disk, but its tabs " +
      "stop offering it from its next start.",
  );
  process.exit(0);
}

die(`Unknown command ${JSON.stringify(command)} — list, add, grant or revoke.`);
