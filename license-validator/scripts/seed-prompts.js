// Write the starter prompts into MongoDB.
//
//   npm run seed-prompts
//   npm run seed-prompts -- --dry-run
//
// Idempotent — re-run it after editing SEED_PROMPTS below. Rows are keyed
// by `fingerprint`, which has a unique index, so this is an upsert and
// running it twice changes nothing the second time.
//
// It never deletes, and it never un-approves. Editing a prompt's text or
// settings here and re-running updates the row; its **moderation state is
// left alone**, because that is yours and not this file's — see the
// $set/$setOnInsert split in upsertAll. A prompt you rejected stays
// rejected on the next seed run rather than climbing back into the queue.
//
// ── The two kinds ──────────────────────────────────────────────────────
//
//   ⭐ admin      source:"admin", is_public:true, reviewed_at set.
//                 Live in the app the moment it lands — this is the
//                 curated half of the library and needs no approval.
//   👥 community  source:"community", is_public:false, reviewed_at:null.
//                 Exactly what a pod writes on its own, and **invisible**
//                 until approved:
//                     npm run prompts                 (the queue)
//                     npm run prompts -- --approve <id>
//                 One is seeded here so the queue has something in it the
//                 first time you look at it.
//
// ── The fields that will bite you ──────────────────────────────────────
//
// `fingerprint` must be unique and 64 hex characters. For a hand-written
// row any 64 will do — it only has to not collide. Reusing one turns an
// insert into an update of that other row.
//
// **Run `npm run seed-assets` first.** Every model and LoRA in `settings`
// is an id from the catalogue (data/assets.json), and this script — like
// POST /v1/prompts — refuses a blob whose `model` is not a stored model id
// or whose LoRA rows name a LoRA that is not stored. Against an unseeded
// catalogue every row here fails that check, and nothing is written.
//
// The rest of `settings` belongs to the app's tabs and is stored as given.
// Getting it wrong breaks nothing — the client checks every value against
// what its own build offers and leaves anything it does not recognise
// alone — but the card then loads less of itself than you meant.
//
// The two shapes are NOT interchangeable:
//
//   krea_t2i      flat. `resolution` is a preset label. Eight LoRA slots.
//   krea_v2_t2i   nested `sampler` and `variance` objects. Size is the
//                 aspect/megapixels/multiple the sliders hold, never a
//                 width/height. The tab has one LoRA row per LoRA in its
//                 feature list; the card's rows are matched to them by id,
//                 and a LoRA it does not mention stays off.
//
// Both store `loras` the same way: [on, lora id, weight] triples, with
// `null` for an empty slot (the form shows it as "None"; that word never
// reaches storage).
//
// Strings that are dropdown values must match **character for character**,
// emoji included — "📸 Krea2 (SingleStream)" is one of them. A value this
// build does not offer is not an error; that control is simply left where
// it was when the card is loaded.

import { collections, ensureIndexes } from "../src/db.js";
import { assetIds, settingsProblem } from "../src/assets.js";

const dryRun = process.argv.includes("--dry-run");

// The tabs a prompt can be replayed into, same list the API validates
// against. A row naming anything else would be a card whose Use button has
// nowhere to send it.
const PROMPT_TABS = ["krea_t2i", "krea_v2_t2i"];

export const SEED_PROMPTS = [
  // ─────────────────────────────────────────────── ⭐ admin, Krea 2 V2
  {
    fingerprint: "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
    tab: "krea_v2_t2i",
    source: "admin",
    is_public: true,
    title: "Golden hour portrait",
    prompt:
      "A photorealistic golden-hour portrait of a woman by a window, warm " +
      "rim light through sheer curtains, natural skin texture with visible " +
      "pores and fine hair, shallow depth of field, 85mm lens, soft falloff " +
      "into a dim interior",
    negative:
      "This low quality greyscale unfinished sketch is inaccurate and " +
      "flawed. The image is very blurred and lacks detail, with plastic " +
      "waxy skin, deformed hands and extra fingers.",
    settings: {
      model: "krea2-turbo-mxfp8",
      aspect: "3:4 (Portrait Standard)",
      megapixels: 1.5,
      multiple: 8,
      // Stored for replay, but NOT part of the fingerprint — a re-roll is
      // not a different prompt. See prompts.py.
      seed: 370102505887178,
      randomize: true,
      batch_count: 1,
      sampler: {
        eta: 0.5,
        sampler_name: "linear/euler",
        scheduler: "bong_tangent",
        steps: 10,
        denoise: 1.0,
        cfg: 1.0,
        sampler_mode: "standard",
        bongmath: true,
      },
      variance: {
        variance_preset: "🌱 Subtle",
        fine_tune_variance: 50,
        model_type: "📸 Krea2 (SingleStream)",
        variance_schedule: "constant",
        cutoff_step: 8,
        total_steps: 20,
        cutoff_strength: 0.0,
        shift_strength: 100,
      },
      sharpen: false,
      film_grain: false,
      // Matched to the tab's rows by id, so order does not matter and a
      // LoRA left out simply stays off; the off rows are listed only to
      // keep their weights with the recipe.
      loras: [
        [false, "krea2-turbo", 0.6],
        [true, "filter-bypass-3", 0.93],
        [true, "enhancer", 0.4],
        [true, "realism-v2", 0.3],
        [false, "realism-engine-v3-1", 0.6],
        [true, "realistic-snapshot", 0.8],
        [false, "purelens", 0.6],
        [false, "lenovo", 0.5],
        [false, "mysticxxx-v3", 1.0],
        [false, "knp-v4-1", 1.0],
        [false, "snofs-krea-v1", 1.0],
      ],
    },
  },

  // ─────────────────────────────────────── 👥 community, Krea 2 (pending)
  {
    fingerprint: "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0",
    tab: "krea_t2i",
    source: "community",
    is_public: false,
    title: null,         // community prompts are not titled
    prompt:
      "A rain-slicked Tokyo alley at night, neon signage reflected in the " +
      "puddles, steam rising from a vent, one figure walking away from " +
      "camera under an umbrella, cinematic colour grade",
    negative: "blurry, low quality, watermark, text",
    settings: {
      model: "krea2-turbo-mxfp8",
      steps: 10,
      cfg: 1.0,
      // A preset label, not "1024x1024" — see RESOLUTION_PRESETS in
      // config.py. Note the × is U+00D7, not a letter x.
      resolution: "832×1216 (Portrait)",
      sampler: "er_sde",
      seed: 42,
      randomize: true,
      batch_count: 1,
      // Eight slots, positional. null is an empty slot.
      loras: [
        [true, "filter-bypass-3", 0.93],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
      ],
    },
    // Which licence submitted it. Kept for moderation and never sent to
    // any client — GET /v1/prompts projects it away. A seeded community
    // row has no real submitter, so this is a placeholder.
    license_key: "KREA2-SEED-SEED-SEED",
  },
];

function die(...lines) {
  for (const line of lines) console.error(line);
  process.exit(1);
}

// ── Validate before writing anything ───────────────────────────────────
//
// All of it, and only then write. A half-seeded library is worse than an
// unseeded one: you would have to work out which rows made it before you
// could safely re-run.

const problems = [];
const seenFingerprints = new Set();

for (const [index, row] of SEED_PROMPTS.entries()) {
  const where = `SEED_PROMPTS[${index}]`;
  if (!/^[0-9a-f]{64}$/.test(row.fingerprint || "")) {
    problems.push(`${where}: fingerprint must be 64 lowercase hex characters`);
  } else if (seenFingerprints.has(row.fingerprint)) {
    // Two rows sharing one would make the second silently overwrite the
    // first, and the run would report two successes.
    problems.push(`${where}: duplicate fingerprint ${row.fingerprint}`);
  } else {
    seenFingerprints.add(row.fingerprint);
  }
  if (!PROMPT_TABS.includes(row.tab)) {
    problems.push(
      `${where}: tab "${row.tab}" is not one of ${PROMPT_TABS.join(", ")}`,
    );
  }
  if (row.source !== "admin" && row.source !== "community") {
    problems.push(`${where}: source must be "admin" or "community"`);
  }
  if (typeof row.prompt !== "string" || !row.prompt.trim()) {
    problems.push(`${where}: prompt is required`);
  }
  if (row.settings === null || typeof row.settings !== "object" ||
      Array.isArray(row.settings)) {
    problems.push(`${where}: settings must be an object`);
  }
}

if (problems.length) {
  die(...problems, `\n${problems.length} problem(s) — nothing written.`);
}

// The model and LoRA ids, against the catalogue as stored — the check POST
// /v1/prompts makes, and a second pass only because it needs the database.
const ids = await assetIds();
for (const [index, row] of SEED_PROMPTS.entries()) {
  const bad = settingsProblem(row.settings, ids);
  if (bad) problems.push(`SEED_PROMPTS[${index}]: ${bad}`);
}
if (problems.length) {
  die(
    ...problems,
    `\n${problems.length} problem(s) — nothing written.`,
    ...(ids.models.size ? [] : ["The catalogue is empty: run npm run seed-assets first."]),
  );
}

const { prompts } = await collections();
// Not on a dry run: creating an index is a write.
if (!dryRun) await ensureIndexes();

async function upsertAll(docs) {
  let created = 0;
  let updated = 0;
  for (const doc of docs) {
    const { fingerprint, is_public, source, license_key, ...content } = doc;
    if (dryRun) {
      if (await prompts.findOne({ fingerprint })) updated += 1;
      else created += 1;
      continue;
    }
    const now = new Date();
    const result = await prompts.updateOne(
      { fingerprint },
      {
        // Content is $set, so editing a prompt here and re-running
        // actually updates it.
        $set: { ...content, source, updated_at: now },
        // Moderation state is $setOnInsert, so re-running never touches a
        // decision you made. A community row you approved stays approved;
        // one you rejected stays rejected instead of climbing back into
        // the queue every time this script runs.
        $setOnInsert: {
          fingerprint,
          is_public,
          reviewed_at: source === "admin" ? now : null,
          license_key: license_key ?? null,
          seen_count: 0,
          created_at: now,
        },
      },
      { upsert: true },
    );
    if (result.upsertedCount) created += 1;
    else updated += 1;
  }
  return { created, updated };
}

const { created, updated } = await upsertAll(SEED_PROMPTS);
console.log(
  `prompts   ${created} created, ${updated} updated` +
    (dryRun ? "  (dry run — nothing written)" : ""),
);

// What the two readers can actually see now. The counts matter more than
// the write did: a seeded community row that is not in the queue, or an
// admin row that is not live, means the moderation fields did not land.
const live = await prompts.countDocuments({ is_public: true });
const pending = await prompts.countDocuments({ reviewed_at: null });
const total = await prompts.countDocuments({});

console.log(`\nlibrary now: ${total} prompt(s) — ${live} live, ${pending} pending`);
for (const row of await prompts.find({}).sort({ source: 1, created_at: -1 })
  .limit(20).toArray()) {
  const state = row.is_public
    ? "live"
    : row.reviewed_at
      ? "rejected"
      : "pending";
  const text = (row.title || row.prompt || "").replace(/\s+/g, " ").slice(0, 54);
  console.log(
    `  ${String(row._id)}  ${(row.source || "").padEnd(9)} ${state.padEnd(8)} ` +
      `${(row.tab || "").padEnd(12)} ${text}`,
  );
}

console.log("\nNext: npm run prompts            (review the queue)");
process.exit(0);
