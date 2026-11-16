// Face LoRAs a licence publishes itself — what the app's 🎭 Face LoRA page
// calls, authorised by the pod's own licence key (which must grant
// `face_lora`), never by an admin token.
//
// The file goes from the pod straight to R2 — far too big for a request
// body here — through a PUT link this service signs for exactly one
// object in the face bucket (r2.js presignFace). No R2 key leaves this
// service, and the face key can reach nothing but that bucket.
//
//   upload    checks the name, size and consent, signs the link
//   register  checks what landed (size and MD5) and saves the record,
//             granted to the licence that published it and no other
//   mine      the Face LoRAs that licence has now, for the page
//
// Nobody owns a Face LoRA. The record keeps `published_by`, the key that
// published it, for the record only: it grants no rights. Publishing
// grants it to that key once; after that its grants are the operator's
// (npm run private-lora), and a customer can neither unpublish it nor get
// a grant back by publishing again.
//
// Every name carries the publishing key's tag —
// loras/private/<tag>/face_<tag>_<name> — so two customers can both call a
// run "anna". The record is an ordinary
// private LoRA (source {kind:"r2", bucket:"face"}); it names its own tabs
// (`tabs`) instead of joining the shared feature lists, which nothing a
// customer does may edit — catalogFrom adds it to them for the licences
// granted it. Hand-published private LoRAs (src/privateLoras.js) are
// untouched by all of this.

import { createHash } from "node:crypto";

import { collections } from "./db.js";
import { FACE_BUCKET, PRIVATE_LORA_PREFIX, loraProblems } from "./assets.js";
import { faceR2Configured, presignFace } from "./r2.js";
import { DEFAULT_FEATURES, PrivateLoraError } from "./privateLoras.js";

// A rank-16 Krea 2 LoRA is ~235 MB; rank 32 about twice that.
export const MAX_FACE_LORA_BYTES = 600 * 1024 * 1024;
// Long enough for a slow pod to send ~500 MB once, short enough to be useless later.
const UPLOAD_TTL_SECONDS = 30 * 60;
// The run names the pod accepts (ember/trainer/run.py NAME_RE).
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,40}$/;
const MD5_RE = /^[a-f0-9]{32}$/;

/** What a customer ticks before publishing, stored with the record. */
export const CONSENT_TEXT =
  "I have this person's permission to train and use a LoRA of their face.";

const fail = (message, status = 400) => {
  const err = new PrivateLoraError(message);
  err.status = status;
  throw err;
};

/** A licence's tag: the same 8 hex digits the pod puts in its file names. */
export const ownerTag = (licenceKey) =>
  createHash("sha256").update(String(licenceKey)).digest("hex").slice(0, 8);

/** Where one licence's run `name` lives: its id, file and object key. */
export function faceNames(licenceKey, name) {
  if (typeof name !== "string" || !NAME_RE.test(name)) {
    fail("Name: lowercase letters, digits, - and _, at most 41 characters.");
  }
  const tag = ownerTag(licenceKey);
  const file = `face_${tag}_${name}.safetensors`;
  return {
    id: `face-${tag}-${name.replace(/_/g, "-")}`,
    file,
    key: `${PRIVATE_LORA_PREFIX}${tag}/${file}`,
  };
}

function checkUpload({ size, md5, consent }) {
  if (!faceR2Configured()) fail("Face LoRA storage is not set up on the server yet.", 503);
  if (consent !== true) fail("Tick the box confirming you have this person's permission.");
  if (!Number.isInteger(size) || size <= 0) fail("size must be the file's size in bytes.");
  if (size > MAX_FACE_LORA_BYTES) {
    fail(`The file is ${(size / 1e6).toFixed(0)} MB; the limit is ` +
      `${(MAX_FACE_LORA_BYTES / 1e6).toFixed(0)} MB.`);
  }
  if (typeof md5 !== "string" || !MD5_RE.test(md5)) fail("md5 must be 32 hex digits.");
}

/**
 * The record this licence already published under `name`, or none yet.
 * Refuses a name another key published, and a different file under a
 * name already published: a pod that has the old file keeps it by name.
 */
async function publishedRecord(licence, names, md5) {
  const { loras } = await collections();
  const row = await loras.findOne({ $or: [{ _id: names.id }, { file: names.file }] });
  if (!row) return null;
  if (row.published_by !== licence.key || row._id !== names.id) {
    fail("That name is taken — pick another.", 409);
  }
  if (row.md5 !== md5) {
    fail("You already published a different file under this name. Pods that " +
      "have the old one never fetch it again, so train this one under a new " +
      "name (e.g. adding _v2).", 409);
  }
  return row;
}

/** Step 1: a PUT link for the file, or `already` when it is published. */
export async function faceUpload(licence, { name, size, md5, consent }) {
  checkUpload({ size, md5, consent });
  const names = faceNames(licence.key, name);
  const existing = await publishedRecord(licence, names, md5);
  if (existing) return { ...names, already: true };
  return {
    ...names,
    already: false,
    url: presignFace("PUT", names.key, UPLOAD_TTL_SECONDS),
    expires_in: UPLOAD_TTL_SECONDS,
  };
}

/** (size, md5) of an object in the face bucket, or null when it is not there. */
async function head(key) {
  const res = await fetch(presignFace("HEAD", key, 300), { method: "HEAD" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`R2 answered HTTP ${res.status} to HEAD`);
  return {
    size: Number(res.headers.get("content-length")),
    etag: (res.headers.get("etag") || "").replace(/"/g, "").toLowerCase(),
  };
}

async function deleteObject(key) {
  const res = await fetch(presignFace("DELETE", key, 300), { method: "DELETE" });
  // 204 deleted, 404 already gone — both mean it is not there now.
  if (!res.ok && res.status !== 404) throw new Error(`R2 answered HTTP ${res.status} to DELETE`);
}

/**
 * Step 2: check what landed and save the record, granted to this licence.
 * Safe to repeat — the same file again changes nothing, its grants included.
 */
export async function faceRegister(licence, { name, trigger = "", size, md5, consent }) {
  checkUpload({ size, md5, consent });
  const names = faceNames(licence.key, name);
  const existing = await publishedRecord(licence, names, md5);
  if (existing) return { id: existing._id, file: existing.file, created: false };

  const found = await head(names.key);
  if (!found) fail("The file is not in storage — upload it first.", 409);
  if (found.size !== size || found.etag !== md5) {
    // Not what was promised: leave nothing behind for a pod to fetch.
    await deleteObject(names.key);
    fail("What reached storage is not the file described (size or MD5 differ) — " +
      "publish again.", 409);
  }

  const { loras, licenses } = await collections();
  const now = new Date();
  const record = {
    _id: names.id,
    name: name,
    file: names.file,
    source: { kind: "r2", bucket: FACE_BUCKET, key: names.key },
    mirror: null,
    trigger: typeof trigger === "string" ? trigger.trim().slice(0, 200) : "",
    default_strength: 1.0,
    // Private LoRAs start with auto_trigger on whenever they have a trigger.
    auto_trigger: typeof trigger === "string" && trigger.trim() !== "",
    enabled: true,
    published_by: licence.key,
    tabs: DEFAULT_FEATURES,
    size,
    md5,
    consent: { at: now, text: CONSENT_TEXT },
  };
  const problems = loraProblems(record);
  if (problems.length) fail(`lora: ${problems.join("; ")}.`);

  const [last] = await loras
    .find({}, { projection: { sort_order: 1 } })
    .sort({ sort_order: -1 })
    .limit(1)
    .toArray();
  // A record only ever inserted: a repeat of this call lost the race above
  // and changes nothing.
  const { _id, ...fields } = record;
  const saved = await loras.updateOne(
    { _id },
    {
      $setOnInsert: {
        ...fields,
        created_at: now,
        updated_at: now,
        sort_order: (Number(last?.sort_order) || 0) + 10,
      },
    },
    { upsert: true },
  );
  const created = saved.upsertedCount === 1;
  if (created) {
    await licenses.updateOne({ key: licence.key }, { $addToSet: { private_loras: _id } });
  }
  return { id: _id, file: names.file, created };
}

/** The Face LoRAs this licence has now — its own and any shared with it — newest first. */
export async function faceMine(licence) {
  const { loras, licenses } = await collections();
  const fresh = await licenses.findOne({ key: licence.key }, { projection: { private_loras: 1 } });
  const ids = Array.isArray(fresh?.private_loras) ? fresh.private_loras : [];
  const rows = await loras
    .find({ _id: { $in: ids }, "source.bucket": FACE_BUCKET })
    .sort({ created_at: -1 })
    .toArray();
  return {
    loras: rows.map((row) => ({
      id: row._id,
      name: row.name,
      file: row.file,
      trigger: row.trigger || "",
      size: row.size ?? null,
      created_at: row.created_at || null,
    })),
  };
}

// ── Checkpoint backups ─────────────────────────────────────────────────
//
// While a run trains, the pod sends each checkpoint musubi saves
// (<name>-step00000200.safetensors, …) here, so a pod that dies mid-run
// loses nothing it had saved. A backup is NOT a LoRA: it is never in
// `loras`, never in a catalogue, never offered on a tab. It sits in the
// face bucket at loras/private/<tag>/checkpoints/<name>/<file>, with one
// row in `face_checkpoints`, and only the licence that sent it can list it
// or fetch it back (the 🎭 page's Recover, which then publishes it as an
// ordinary Face LoRA through faceUpload/faceRegister above).
//
// Same two steps as a LoRA: checkpointUpload signs the PUT, and
// checkpointRegister checks what landed and saves the row. Unlike a LoRA,
// a backup may be replaced — a new run under an old name trains new
// checkpoints, and no pod fetches a backup by name.

const STEP_RE = /^-step(\d{1,10})\.safetensors$/;

/** Where checkpoint `file` of this licence's run `name` lives, and its step. */
export function checkpointNames(licenceKey, name, file) {
  const { id } = faceNames(licenceKey, name);
  const match = typeof file === "string" && file.startsWith(name)
    ? STEP_RE.exec(file.slice(name.length))
    : null;
  if (!match) fail(`${file} is not a checkpoint of ${name} (${name}-step<n>.safetensors).`);
  const tag = ownerTag(licenceKey);
  return {
    lora_id: id,
    file,
    step: Number(match[1]),
    key: `${PRIVATE_LORA_PREFIX}${tag}/checkpoints/${name}/${file}`,
  };
}

/** Step 1 for a checkpoint: a PUT link, or `already` when this file is backed up. */
export async function checkpointUpload(licence, { name, file, size, md5, consent }) {
  checkUpload({ size, md5, consent });
  const names = checkpointNames(licence.key, name, file);
  const { face_checkpoints } = await collections();
  const row = await face_checkpoints.findOne({ _id: names.key });
  if (row && row.md5 === md5 && row.size === size) return { ...names, already: true };
  return {
    ...names,
    already: false,
    url: presignFace("PUT", names.key, UPLOAD_TTL_SECONDS),
    expires_in: UPLOAD_TTL_SECONDS,
  };
}

/** Step 2 for a checkpoint: check what landed and save its row. Safe to repeat. */
export async function checkpointRegister(licence, { name, file, trigger = "", size, md5, consent }) {
  checkUpload({ size, md5, consent });
  const names = checkpointNames(licence.key, name, file);
  const found = await head(names.key);
  if (!found) fail("The checkpoint is not in storage — upload it first.", 409);
  if (found.size !== size || found.etag !== md5) {
    await deleteObject(names.key);
    fail("What reached storage is not the checkpoint described (size or MD5 differ) — " +
      "it will be sent again.", 409);
  }
  const { face_checkpoints } = await collections();
  const now = new Date();
  await face_checkpoints.updateOne(
    { _id: names.key },
    {
      $set: {
        licence_key: licence.key,
        name,
        file,
        step: names.step,
        trigger: typeof trigger === "string" ? trigger.trim().slice(0, 200) : "",
        size,
        md5,
        consent: { at: now, text: CONSENT_TEXT },
        uploaded_at: now,
      },
    },
    { upsert: true },
  );
  return { id: names.key, file, step: names.step };
}

/** This licence's backed-up runs, newest first, each with its checkpoints by step. */
export async function checkpointList(licence) {
  const { face_checkpoints, loras } = await collections();
  const rows = await face_checkpoints
    .find({ licence_key: licence.key })
    .sort({ uploaded_at: -1 })
    .toArray();
  const runs = new Map();
  for (const row of rows) {
    if (!runs.has(row.name)) {
      runs.set(row.name, { name: row.name, trigger: row.trigger || "", checkpoints: [] });
    }
    runs.get(row.name).checkpoints.push({
      file: row.file,
      step: row.step,
      size: row.size,
      uploaded_at: row.uploaded_at,
    });
  }
  const list = [...runs.values()];
  const ids = list.map((run) => faceNames(licence.key, run.name).id);
  const published = new Set(
    (await loras.find({ _id: { $in: ids } }, { projection: { _id: 1 } }).toArray())
      .map((row) => row._id),
  );
  for (const [index, run] of list.entries()) {
    run.checkpoints.sort((a, b) => a.step - b.step);
    run.published = published.has(ids[index]);
  }
  return { runs: list };
}

/** A GET link for one of this licence's own checkpoints. */
export async function checkpointDownload(licence, { name, file }) {
  const names = checkpointNames(licence.key, name, file);
  const { face_checkpoints } = await collections();
  const row = await face_checkpoints.findOne({ _id: names.key, licence_key: licence.key });
  if (!row) fail("That checkpoint is not backed up for this licence.", 404);
  return {
    file,
    step: row.step,
    size: row.size,
    md5: row.md5,
    trigger: row.trigger || "",
    url: presignFace("GET", names.key, 60 * 60),
  };
}
