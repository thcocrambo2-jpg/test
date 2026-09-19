// The model and LoRA catalogue — what each Krea tab offers, and the one
// set of rules every writer of it goes through.
//
// Three collections (see db.js):
//
//   loras            one record per LoRA file, `_id` = a readable id
//   models           one record per model *setting*, `_id` = a readable id.
//                    Two records may point at the same file (same weights,
//                    different steps/CFG), so `file` is not unique there.
//   feature_assets   per feature key, the ordered model ids and LoRA ids
//                    that tab offers. The first model is the tab's default.
//
// Ids are the contract. Presets, prompts and every form value on a pod
// carry ids, and the only place an id turns back into a filename is the
// pod's catalog.py, when a download or a ComfyUI graph needs the name on
// disk. So an id is never renamed and never reused: a preset that names
// "realism-engine-v3-1" means that record for as long as the preset lives.
//
// ── Why the rules live here and not in each route ──────────────────────
//
// Four things write these collections — the admin routes, seed-assets,
// the assets CLI, and (for settings blobs that *reference* them) the
// preset and prompt routes — and a rule that is only enforced by three of
// them is not a rule. They all import this module. The pod checks the
// same things again on read (catalog.py), because a record edited by hand
// in Atlas never passed through here, and a file name becomes a path on
// the pod's disk.
//
// `file` and the two `path`s are the fields that matter most. A file name
// is joined onto a models directory on the pod, and a path is appended to
// a Hugging Face URL; a value with a slash or a `..` segment in the wrong
// place is a write outside that directory or a download of something
// nobody chose. Everything else here is about not storing a record that
// cannot be offered.

import { collections } from "./db.js";
import { FEATURE_KEYS } from "./features.js";

// The same patterns catalog.py compiles. Change them together.
export const ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
export const FILE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.safetensors$/;
export const REPO_RE = /^[A-Za-z0-9][\w.-]*\/[\w.-]+$/;

export const MODEL_VARIANTS = ["turbo", "raw"];

// Display strings only, but still bounded: they ride every /v1/catalog
// answer and end up as dropdown labels.
const MAX_NAME_CHARS = 120;
const MAX_TRIGGER_CHARS = 500;

const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
const isObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const show = (value) => JSON.stringify(value) ?? String(value);

// ── Field rules ────────────────────────────────────────────────────────
//
// Each returns a sentence saying what is wrong, or null. Sentences rather
// than booleans because they go straight into a 400 body and a seed
// script's error list, and "file is not valid" makes you go and find out
// which rule it broke.

export function idProblem(value, what = "id") {
  if (typeof value !== "string" || !ID_RE.test(value)) {
    return (
      `${what} ${show(value)} is not a valid id (lowercase letters, digits ` +
      "and -, starting with a letter or digit, at most 63 characters)"
    );
  }
  return null;
}

/** A Hugging Face path or a mirror path: relative, and going nowhere odd. */
function pathProblem(value, what) {
  if (typeof value !== "string" || !value) return `${what} must be a non-empty string`;
  if (value.startsWith("/")) return `${what} ${show(value)} must not start with /`;
  if (value.split("/").includes("..")) {
    return `${what} ${show(value)} must not contain a .. segment`;
  }
  return null;
}

function repoProblem(value, what) {
  if (typeof value !== "string" || !REPO_RE.test(value)) {
    return `${what} ${show(value)} is not an owner/name Hugging Face repo`;
  }
  return null;
}

export function fileProblem(value) {
  if (typeof value !== "string" || !FILE_RE.test(value)) {
    return (
      `file ${show(value)} is not a plain .safetensors file name ` +
      "(letters, digits, . _ -, no directories)"
    );
  }
  return null;
}

/** `{kind:"civitai", version}` or `{kind:"hf", repo, path}`, nothing else. */
export function sourceProblem(source) {
  if (!isObject(source)) return "source must be an object";
  if (source.kind === "civitai") {
    const { version } = source;
    if (!Number.isInteger(version) || version <= 0) {
      return `source.version ${show(version)} must be a positive integer`;
    }
    return null;
  }
  if (source.kind === "hf") {
    return (
      repoProblem(source.repo, "source.repo") ||
      pathProblem(source.path, "source.path")
    );
  }
  return `source.kind ${show(source.kind)} must be "civitai" or "hf"`;
}

/** `{repo, path}` — where our own copy of the file lives — or null. */
export function mirrorProblem(mirror) {
  if (mirror === null) return null;
  if (!isObject(mirror)) return "mirror must be {repo, path} or null";
  return repoProblem(mirror.repo, "mirror.repo") || pathProblem(mirror.path, "mirror.path");
}

function stringProblem(value, what, max) {
  if (typeof value !== "string") return `${what} must be a string`;
  if (value.length > max) return `${what} is longer than ${max} characters`;
  return null;
}

// Per kind, the rule for every field a record may carry. A field not in
// its table is refused rather than stored: these documents are read by a
// pod that knows exactly these fields, and a typo ("defualt_strength")
// stored silently is a setting that silently does nothing.
const COMMON_RULES = {
  name: (value) =>
    stringProblem(value, "name", MAX_NAME_CHARS) ||
    (value.trim() ? null : "name must not be empty"),
  file: fileProblem,
  source: sourceProblem,
  mirror: mirrorProblem,
  trigger: (value) => stringProblem(value, "trigger", MAX_TRIGGER_CHARS),
  enabled: (value) =>
    typeof value === "boolean" ? null : "enabled must be true or false",
};

const LORA_RULES = {
  ...COMMON_RULES,
  default_strength: (value) =>
    isNumber(value) ? null : `default_strength ${show(value)} must be a number`,
  sort_order: (value) =>
    isNumber(value) ? null : `sort_order ${show(value)} must be a number`,
};

const MODEL_RULES = {
  ...COMMON_RULES,
  variant: (value) =>
    MODEL_VARIANTS.includes(value)
      ? null
      : `variant ${show(value)} must be one of: ${MODEL_VARIANTS.join(", ")}`,
  steps: (value) =>
    Number.isInteger(value) && value > 0
      ? null
      : `steps ${show(value)} must be a positive integer`,
  cfg: (value) =>
    isNumber(value) && value >= 0 ? null : `cfg ${show(value)} must be a number ≥ 0`,
  // The LoRA a model's own recipe switches on (V2's raw model needs the
  // turbo LoRA to run at turbo step counts). Only the shape is checked
  // here; that the id names a real LoRA is a cross-record check, done by
  // modelProblems when it is handed the LoRA ids.
  turbo_lora: (value) => {
    if (value === null) return null;
    if (!isObject(value)) return "turbo_lora must be {lora, strength} or null";
    const extra = Object.keys(value).filter((k) => k !== "lora" && k !== "strength");
    if (extra.length) return `turbo_lora has unknown field(s): ${extra.join(", ")}`;
    return (
      idProblem(value.lora, "turbo_lora.lora") ||
      (isNumber(value.strength)
        ? null
        : `turbo_lora.strength ${show(value.strength)} must be a number`)
    );
  },
};

export const LORA_FIELDS = Object.keys(LORA_RULES);
export const MODEL_FIELDS = Object.keys(MODEL_RULES);

/**
 * Every problem with one record, as a list of sentences (empty = fine).
 *
 * `record` carries its id as `id` or `_id` — the seed file and the admin
 * routes say `id`, a stored document says `_id`.
 *
 * `partial: true` is an update: only the fields present are checked, and
 * nothing is required. That is what lets the mirror script send
 * `{id, mirror}` alone. A full record (a create, or a stored document
 * being served) must have `file` and `source` — without those there is
 * nothing to download.
 *
 * `loraIds`, when given, makes a model's turbo_lora check that it names a
 * LoRA that exists. Left out, only its shape is checked.
 */
function recordProblems(rules, record, { partial = false, loraIds } = {}) {
  if (!isObject(record)) return ["the record must be an object"];
  const problems = [];
  const id = record.id ?? record._id;
  const bad = idProblem(id);
  if (bad) problems.push(bad);
  for (const [field, value] of Object.entries(record)) {
    if (field === "id" || field === "_id") continue;
    // Timestamps belong to the writer, not to the record's content. A
    // stored document carries them; nothing sent is allowed to set them.
    if (field === "created_at" || field === "updated_at") continue;
    const rule = rules[field];
    if (!rule) {
      problems.push(`unknown field ${show(field)}`);
      continue;
    }
    const problem = rule(value);
    if (problem) problems.push(problem);
  }
  if (!partial) {
    for (const field of ["file", "source"]) {
      if (record[field] === undefined) problems.push(`${field} is required`);
    }
  }
  if (loraIds && isObject(record.turbo_lora) &&
      typeof record.turbo_lora.lora === "string" &&
      !idProblem(record.turbo_lora.lora) &&
      !loraIds.has(record.turbo_lora.lora)) {
    problems.push(`turbo_lora.lora ${show(record.turbo_lora.lora)} is not a LoRA id`);
  }
  return problems;
}

export const loraProblems = (record, options) =>
  recordProblems(LORA_RULES, record, options);
export const modelProblems = (record, options) =>
  recordProblems(MODEL_RULES, record, options);

/** A feature key that can hold asset lists: one the registry knows. */
export function featureProblem(key) {
  if (!FEATURE_KEYS.includes(key)) {
    return `feature ${show(key)} is not one of: ${FEATURE_KEYS.join(", ")}`;
  }
  return null;
}

/**
 * Problems with one feature's `models` or `loras` list, given the ids
 * that exist. Duplicates are refused rather than collapsed: a list is a
 * dropdown in order, and an id twice in it is a mistake about that order.
 */
export function idListProblems(list, known, what) {
  if (!Array.isArray(list)) return [`${what} must be an array of ids`];
  const problems = [];
  const seen = new Set();
  for (const id of list) {
    const bad = idProblem(id, `${what} entry`);
    if (bad) problems.push(bad);
    else if (!known.has(id)) problems.push(`${what}: ${show(id)} does not exist`);
    else if (seen.has(id)) problems.push(`${what}: ${show(id)} is listed twice`);
    seen.add(id);
  }
  return problems;
}

// ── Settings blobs ─────────────────────────────────────────────────────
//
// Presets and prompts store a tab's settings, and every model and LoRA in
// them is referenced by id. This is the one part of a settings blob the
// server does interpret: the rest belongs to the tabs and is only bounded
// in size, but an id that names nothing is a preset that loads a model
// the pod has never heard of — and unlike a stale dropdown label, the pod
// cannot guess what was meant.
//
// Existence is checked against every record, disabled ones included. A
// disabled LoRA still exists and may come back; a preset naming it is not
// wrong, it just has one slot the tab will leave empty for now.

/**
 * What is wrong with a settings blob's model and LoRA rows, or null.
 *
 * `ids` is `{models: Set, loras: Set}` — see assetIds().
 *
 *   settings.model   required, an existing model id
 *   settings.loras   optional; when present an array of rows, each exactly
 *                    [on: boolean, lora id | null, weight: number]
 *
 * An empty slot is null. The form calls it "None", but that is a label and
 * never reaches storage — a "None" here means a client sent the form
 * value through untranslated, and is refused like any other bad id.
 */
export function settingsProblem(settings, ids) {
  if (!isObject(settings)) return "settings must be an object.";
  const { model, loras } = settings;
  if (typeof model !== "string" || !ids.models.has(model)) {
    return `settings.model ${show(model)} is not a model id.`;
  }
  if (loras === undefined) return null;
  if (!Array.isArray(loras)) return "settings.loras must be an array of rows.";
  for (const [index, row] of loras.entries()) {
    const where = `settings.loras[${index}]`;
    if (!Array.isArray(row) || row.length !== 3) {
      return `${where} ${show(row)} must be [on, lora id or null, weight].`;
    }
    const [on, id, weight] = row;
    if (typeof on !== "boolean") {
      return `${where}: on ${show(on)} must be true or false.`;
    }
    if (id !== null && (typeof id !== "string" || !ids.loras.has(id))) {
      return `${where}: ${show(id)} is not a LoRA id (an empty slot is null).`;
    }
    if (!isNumber(weight)) {
      return `${where}: weight ${show(weight)} must be a number.`;
    }
  }
  return null;
}

/** Every model id and LoRA id that exists, enabled or not. */
export async function assetIds() {
  const { loras, models } = await collections();
  const [loraIds, modelIds] = await Promise.all([
    loras.distinct("_id"),
    models.distinct("_id"),
  ]);
  return { loras: new Set(loraIds), models: new Set(modelIds) };
}

/** settingsProblem() against the collections as they are now. */
export async function checkSettings(settings) {
  return settingsProblem(settings, await assetIds());
}

// ── The /v1/catalog answer ─────────────────────────────────────────────

/** One LoRA as pods read it. */
export function loraWire(row) {
  return {
    id: row._id,
    name: row.name || row._id,
    file: row.file,
    source: row.source,
    mirror: row.mirror ?? null,
    default_strength: row.default_strength ?? 1.0,
    trigger: row.trigger || "",
  };
}

/** One model as pods read it. */
export function modelWire(row) {
  return {
    id: row._id,
    name: row.name || row._id,
    file: row.file,
    source: row.source,
    mirror: row.mirror ?? null,
    variant: row.variant || "turbo",
    steps: row.steps ?? 10,
    cfg: row.cfg ?? 1.0,
    turbo_lora: row.turbo_lora ?? null,
    trigger: row.trigger || "",
  };
}

/**
 * The POST /v1/catalog body, minus `ok`, from the three collections.
 *
 * Enabled records only — absent `enabled` counts as on, the same reading
 * catalog.py gives it. LoRAs in `sort_order`; models by id, since their
 * order on screen is the feature's list and nothing else.
 *
 * A stored record that fails validation is **skipped and logged, never
 * sent.** It can only have got there by hand, and the pod would drop it
 * anyway; sending it would just move the error somewhere harder to see.
 * Each feature's lists are then cut down to the ids actually being sent,
 * in their stored order, so a list never names something the same answer
 * does not describe.
 */
export async function buildCatalog() {
  const { loras, models, feature_assets } = await collections();
  const live = { enabled: { $ne: false } };
  const [loraRows, modelRows, featureRows] = await Promise.all([
    loras.find(live).sort({ sort_order: 1, _id: 1 }).toArray(),
    models.find(live).sort({ _id: 1 }).toArray(),
    feature_assets.find({}).sort({ _id: 1 }).toArray(),
  ]);
  return catalogFrom(loraRows, modelRows, featureRows);
}

/**
 * The answer from rows already read: enabled records, in the order they
 * should be sent. Separate from buildCatalog so the filtering can be
 * checked without a database.
 */
export function catalogFrom(loraRows, modelRows, featureRows) {
  const outLoras = [];
  for (const row of loraRows) {
    if (row.enabled === false) continue;
    const problems = loraProblems(row);
    if (problems.length) {
      console.warn(`catalog  skipping lora ${show(row._id)}: ${problems.join("; ")}`);
      continue;
    }
    outLoras.push(loraWire(row));
  }
  const outModels = [];
  for (const row of modelRows) {
    if (row.enabled === false) continue;
    const problems = modelProblems(row);
    if (problems.length) {
      console.warn(`catalog  skipping model ${show(row._id)}: ${problems.join("; ")}`);
      continue;
    }
    outModels.push(modelWire(row));
  }

  const loraIds = new Set(outLoras.map((row) => row.id));
  const modelIds = new Set(outModels.map((row) => row.id));
  const keep = (list, known) => {
    const out = [];
    for (const id of Array.isArray(list) ? list : []) {
      if (known.has(id) && !out.includes(id)) out.push(id);
    }
    return out;
  };
  const features = {};
  for (const row of featureRows) {
    features[row._id] = {
      models: keep(row.models, modelIds),
      loras: keep(row.loras, loraIds),
    };
  }
  return { loras: outLoras, models: outModels, features };
}
