// Private LoRAs — the writes behind `npm run private-lora` and the
// /v1/admin/private-loras routes (which the trainer page's Publish calls).
//
// A private LoRA is an ordinary `loras` row whose source is
// {kind: "r2", key: "loras/private/<file>"}; a licence is granted it by
// naming its id in `private_loras`. POST /v1/catalog does the hiding —
// see catalogFrom in assets.js. Everything here is idempotent: publishing
// the same LoRA twice, or granting a key twice, changes nothing.

import { collections } from "./db.js";
import {
  PRIVATE_LORA_PREFIX,
  featureProblem,
  idProblem,
  isPrivateLora,
  loraProblems,
} from "./assets.js";

export const DEFAULT_FEATURES = ["krea_v2_t2i", "krea_t2i"];

/** A refusal whose message is meant for the person who asked. */
export class PrivateLoraError extends Error {}

const fail = (message) => {
  throw new PrivateLoraError(message);
};

// Enough of a key to tell two apart in a listing, not enough to use one.
export const shortKey = (key) =>
  typeof key === "string" && key.length > 12 ? `${key.slice(0, 8)}…${key.slice(-4)}` : String(key);

/**
 * Create or update a private LoRA and put it on `features`' LoRA lists.
 *
 * Duplicates: an id that is already a *public* LoRA is refused (it would
 * turn a LoRA every customer has into one only a few do); a file another
 * record already uses is refused (the unique index would anyway); an id
 * that is already this private LoRA is updated in place — name, trigger,
 * strength — and a list that already has it is left as it is.
 */
export async function upsertPrivateLora({
  id, file, name, trigger = "", default_strength = 1.0, features = DEFAULT_FEATURES,
}) {
  const bad = idProblem(id);
  if (bad) fail(`${bad}.`);
  for (const feature of features) {
    const problem = featureProblem(feature);
    if (problem) fail(`${problem}.`);
  }
  const record = {
    _id: id,
    name: typeof name === "string" && name.trim() ? name : id,
    file,
    source: { kind: "r2", key: PRIVATE_LORA_PREFIX + file },
    mirror: null,
    trigger: typeof trigger === "string" ? trigger : "",
    default_strength: Number(default_strength),
    enabled: true,
  };
  const problems = loraProblems(record);
  if (problems.length) fail(`lora: ${problems.join("; ")}.`);

  const { loras, feature_assets } = await collections();
  const existing = await loras.findOne({ _id: id });
  if (existing && !isPrivateLora(existing)) {
    fail(`${id} already exists as a public LoRA — pick another id.`);
  }
  const holder = await loras.findOne({ file, _id: { $ne: id } });
  if (holder) fail(`file ${file} is already LoRA ${holder._id}.`);

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

  const lists = {};
  for (const feature of features) {
    // Only a feature that already has lists: creating one here would make
    // a tab with no model, which the admin route refuses for good reason.
    // Matched only while the id is absent, so "added" means added — the
    // updated_at stamp alone would count as a modification otherwise.
    const result = await feature_assets.updateOne(
      { _id: feature, loras: { $ne: id } },
      { $push: { loras: id }, $set: { updated_at: now } },
    );
    lists[feature] = result.modifiedCount ? "added"
      : (await feature_assets.findOne({ _id: feature }, { projection: { _id: 1 } }))
        ? "already listed" : "no lists — skipped";
  }
  console.log(`private  ${existing ? "updated" : "created"} ${id} → ${record.source.key}`);
  return { created: !existing, id, key: record.source.key, lists };
}

async function privateRecord(id) {
  const bad = idProblem(id);
  if (bad) fail(`${bad}.`);
  const { loras } = await collections();
  const row = await loras.findOne({ _id: id });
  if (!row) fail(`No LoRA ${JSON.stringify(id)} — add it first.`);
  if (!isPrivateLora(row)) {
    fail(`${id} is a public LoRA; only private (r2) LoRAs are granted per licence.`);
  }
  return row;
}

async function licencesFor(keys) {
  const wanted = [...new Set((keys || []).map((k) => String(k).trim()).filter(Boolean))];
  if (!wanted.length) fail("Name at least one licence key.");
  const { licenses } = await collections();
  const found = await licenses
    .find({ key: { $in: wanted } }, { projection: { key: 1, name: 1 } })
    .toArray();
  const known = new Set(found.map((row) => row.key));
  const missing = wanted.filter((key) => !known.has(key));
  if (missing.length) fail(`No licence for: ${missing.map(shortKey).join(", ")}.`);
  return found;
}

/** Add `id` to each licence's private_loras. A key that has it already stays as it is. */
export async function grantPrivateLora(id, keys) {
  const row = await privateRecord(id);
  const found = await licencesFor(keys);
  const { licenses } = await collections();
  const result = await licenses.updateMany(
    { key: { $in: found.map((l) => l.key) } },
    { $addToSet: { private_loras: row._id } },
  );
  console.log(`private  granted ${row._id} to ${found.length} (${result.modifiedCount} new)`);
  return {
    id: row._id,
    newly: result.modifiedCount,
    licences: found.map((l) => ({ name: l.name || null, key: shortKey(l.key) })),
  };
}

/** Take `id` from the named licences, or from every licence with `all`. */
export async function revokePrivateLora(id, keys, { all = false } = {}) {
  const row = await privateRecord(id);
  const { licenses } = await collections();
  const filter = all
    ? { private_loras: row._id }
    : { key: { $in: (await licencesFor(keys)).map((l) => l.key) } };
  const result = await licenses.updateMany(filter, { $pull: { private_loras: row._id } });
  console.log(`private  revoked ${row._id} from ${result.modifiedCount}`);
  return { id: row._id, revoked: result.modifiedCount };
}

/** Every private LoRA, the tabs it is on and who holds it. */
export async function listPrivateLoras() {
  const { loras, licenses, feature_assets } = await collections();
  const rows = await loras.find({ "source.kind": "r2" }).sort({ _id: 1 }).toArray();
  // A Face LoRA names its tabs itself (`tabs`); a hand-published one is on
  // the shared lists.
  const out = [];
  for (const row of rows) {
    const holders = await licenses
      .find({ private_loras: row._id }, { projection: { key: 1, name: 1 } })
      .toArray();
    const lists = await feature_assets
      .find({ loras: row._id }, { projection: { _id: 1 } })
      .toArray();
    out.push({
      id: row._id,
      name: row.name || row._id,
      file: row.file,
      trigger: row.trigger || "",
      published_by: row.published_by ? shortKey(row.published_by) : null,
      enabled: row.enabled !== false,
      valid: loraProblems(row).length === 0,
      tabs: Array.isArray(row.tabs) ? row.tabs : lists.map((f) => f._id),
      licences: holders.map((h) => ({ name: h.name || null, key: shortKey(h.key) })),
    });
  }
  return out;
}
