// Write the built-in defaults into MongoDB as the starting presets.
//
//   npm run seed-presets
//   npm run seed-presets -- --dry-run
//
// Idempotent — rows are keyed by (tab, name), which has a unique index, so
// re-running after editing SEED_PRESETS below updates rather than duplicates.
//
// ── What this is for ───────────────────────────────────────────────────
//
// Each tab's "Default" preset is the one marked `is_default`, which is what
// a fresh session loads — so these rows are the tabs' shipped starting
// point, and changing one is an edit here (or in Atlas) rather than a
// client rebuild. On the V2 tabs it is more than a convenience: the tab
// builds one LoRA row per LoRA in its feature list, all switched off, and
// this preset is what turns the usual ones on.
//
// ── The fields that will bite you ──────────────────────────────────────
//
// **Run `npm run seed-assets` first.** Every model and LoRA in a settings
// blob is an id from the catalogue (data/assets.json), and this script —
// like POST /v1/presets — refuses a blob whose `model` is not a stored
// model id or whose LoRA rows name a LoRA that is not stored. Against an
// unseeded catalogue every row here fails that check, and nothing is
// written.
//
// Otherwise `settings` belongs to the app's tabs and is stored as given.
// It carries **no prompt text** — a preset is settings, and the prompt
// library is what carries prompts. The tab shapes are NOT
// interchangeable:
//
//   krea_t2i      flat. `resolution` is a preset label. Eight LoRA slots.
//   krea_v2_t2i   nested `sampler` and `variance` objects. Size is the
//                 aspect/megapixels/multiple the sliders hold, never a
//                 width/height. The tab has one LoRA row per LoRA in its
//                 feature list; a preset's rows are matched to them by id,
//                 and a LoRA the preset does not mention stays off.
//   minimax_i2v   flat, and **no `model`** — shared by both MiniMax tabs.
//                 `seconds` is the clip length; `aspect` only lands on the
//                 text tab. Eight LoRA slots, like krea_t2i.
//   zimage_t2i    flat, like krea_t2i, plus `multiplier` (a number) and
//                 `upscale` (a bool). `resolution` is a Z-Image preset
//                 label. Eight LoRA slots. No preset is seeded for it.
//
// All four store `loras` the same way: [on, lora id, weight] triples, with
// `null` for an empty slot (the form shows it as "None"; that word never
// reaches storage).
//
// Strings that are dropdown values (sampler, resolution, the variance
// presets) must match **character for character**, emoji included. A value
// this build does not offer is not an error; that control is simply left
// where it was when the preset is applied.
//
// `is_default` is at most one row per tab. This script writes it as given
// and then fixes up the collection so that holds, so moving the flag from
// one row to another here is a re-run and nothing else.

import { collections, ensureIndexes } from "../src/db.js";
import { assetIds, settingsProblem } from "../src/assets.js";

const dryRun = process.argv.includes("--dry-run");

const PRESET_TABS = ["krea_t2i", "krea_v2_t2i", "minimax_i2v", "zimage_t2i"];

// Model and LoRA values are catalogue ids (data/assets.json). Edit here and
// re-run to change what a fresh session opens on.
export const SEED_PRESETS = [
  // ────────────────────────────────────────────────────── 🎨 Krea 2
  {
    tab: "krea_t2i",
    name: "Default",
    description: "The values this tab ships with.",
    is_default: true,
    sort_order: 0,
    settings: {
      // The V2 tabs' turbo model, at its own recipe — Krea2 and Krea2 V2
      // run the same weights.
      model: "krea2-turbo-mxfp8",
      steps: 10,
      cfg: 1.0,
      // A preset label, not "1024x1536" — see RESOLUTION_PRESETS in
      // ember/pipelines/krea2/constants.py. Note the × is U+00D7,
      // not a letter x.
      resolution: "1024×1536 (Portrait XL)",
      sampler: "er_sde",
      seed: 42,
      randomize: true,
      batch_count: 1,
      // Three LoRAs on, then empty slots for the rest of the eight. A LoRA
      // this pod's catalogue does not offer resets its slot to empty rather
      // than being left as whatever was loaded before.
      loras: [
        [true, "hmbody-d-e10", 0.8],
        [true, "realism-engine-v2-0", 0.4],
        [true, "galaxyace", 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
        [false, null, 0.8],
      ],
    },
  },

  // ─────────────────────────────────────────────────── 🔶 Krea 2 V2
  {
    tab: "krea_v2_t2i",
    name: "Default",
    description: "The source workflow's own settings, as shipped.",
    is_default: true,
    sort_order: 0,
    settings: {
      model: "krea2-turbo-mxfp8",
      aspect: "3:4 (Portrait Standard)",
      megapixels: 1.5,
      multiple: 8,
      seed: 370102505887178,
      randomize: true,
      batch_count: 1,
      sampler: {
        eta: 0.5,
        sampler_name: "linear/euler",
        scheduler: "bong_tangent",
        // The turbo model's own recipe.
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
      // Both post-processing nodes are bypassed in the source workflow.
      sharpen: false,
      film_grain: false,
      // The source workflow's eleven LoRAs. The turbo LoRA is off: this is
      // a turbo model, and only the raw model's recipe (its `turbo_lora`)
      // switches it on. The tab's other rows stay off.
      loras: [
        [false, "krea2-turbo", 0.6],
        [true, "filter-bypass-3", 0.93],
        [true, "enhancer", 0.4],
        [true, "realism-v2", 0.3],
        [true, "realism-engine-v3-1", 0.6],
        [true, "realistic-snapshot", 0.8],
        [true, "purelens", 0.6],
        [true, "lenovo", 0.5],
        [false, "mysticxxx-v3", 1.0],
        [false, "knp-v4-1", 1.0],
        [false, "snofs-krea-v1", 1.0],
      ],
    },
  },

  // ─────────────────────────────────── 🎥 MiniMax I2V + 🎞️ MiniMax T2V
  {
    // One list for both MiniMax tabs, filed under the image tab's key.
    tab: "minimax_i2v",
    name: "Default",
    description: "The tabs' shipped settings, with the usual LoRAs on.",
    is_default: true,
    sort_order: 0,
    settings: {
      // No `model`: these tabs have no Model control (MODELLESS_TABS).
      steps: 8,
      // Note the em dash — character for character, like every label.
      resolution: "Standard (0.7 MP — the template default)",
      seconds: 5,
      sampler: "euler",
      // The text tab's alone; the image tab skips it.
      aspect: "9:16 (Portrait Widescreen)",
      seed: 42,
      randomize: true,
      batch_count: 1,
      loras: [
        [true, "hmnsfw-aio-v2-5", 0.5],
        [true, "vgna", 0.5],
        [true, "hmbrst", 0.5],
        [true, "hmpenis-v2-0", 0.5],
        [true, "humanmotion-v1-0", 0.5],
        [false, null, 1.0],
        [false, null, 1.0],
        [false, null, 1.0],
      ],
    },
  },
];

function die(...lines) {
  for (const line of lines) console.error(line);
  process.exit(1);
}

// ── Validate before writing anything ───────────────────────────────────
//
// All of it, and only then write — same rule as seed-prompts: a half-seeded
// set is worse than an unseeded one, because you would have to work out
// which rows made it before you could safely re-run.

const problems = [];
const seenNames = new Set();
const defaults = new Set();

for (const [index, row] of SEED_PRESETS.entries()) {
  const where = `SEED_PRESETS[${index}]`;
  if (!PRESET_TABS.includes(row.tab)) {
    problems.push(
      `${where}: tab "${row.tab}" is not one of ${PRESET_TABS.join(", ")}`,
    );
  }
  if (typeof row.name !== "string" || !row.name.trim()) {
    problems.push(`${where}: name is required`);
  } else {
    const key = `${row.tab} ${row.name}`;
    // Two rows sharing (tab, name) would make the second silently overwrite
    // the first, and the run would report two successes.
    if (seenNames.has(key)) problems.push(`${where}: duplicate ${row.tab}/${row.name}`);
    seenNames.add(key);
  }
  if (row.settings === null || typeof row.settings !== "object" ||
      Array.isArray(row.settings)) {
    problems.push(`${where}: settings must be an object`);
  }
  if (row.is_default) {
    // At most one per tab, because that is what the dropdown reads.
    if (defaults.has(row.tab)) {
      problems.push(`${where}: ${row.tab} already has an is_default preset`);
    }
    defaults.add(row.tab);
  }
}

if (problems.length) {
  die(...problems, `\n${problems.length} problem(s) — nothing written.`);
}

// The model and LoRA ids, against the catalogue as stored — the check POST
// /v1/presets makes, and a second pass only because it needs the database.
const ids = await assetIds();
for (const [index, row] of SEED_PRESETS.entries()) {
  const bad = settingsProblem(row.settings, ids, row.tab);
  if (bad) problems.push(`SEED_PRESETS[${index}] ${row.tab}/${row.name}: ${bad}`);
}
if (problems.length) {
  die(
    ...problems,
    `\n${problems.length} problem(s) — nothing written.`,
    ...(ids.models.size ? [] : ["The catalogue is empty: run npm run seed-assets first."]),
  );
}

const { presets } = await collections();
// Not on a dry run: creating an index is a write.
if (!dryRun) await ensureIndexes();

async function upsertAll(docs) {
  let created = 0;
  let updated = 0;
  for (const doc of docs) {
    const { tab, name, is_default, enabled, sort_order, ...content } = doc;
    if (dryRun) {
      if (await presets.findOne({ tab, name })) updated += 1;
      else created += 1;
      continue;
    }
    const now = new Date();
    const result = await presets.updateOne(
      { tab, name },
      {
        // Content is $set, so editing a preset here and re-running updates
        // it. Both flags are $setOnInsert, so a preset you switched off in
        // Atlas stays off across a re-seed — the same split seed-prompts
        // makes between content and the decisions you made about it.
        $set: { ...content, updated_at: now },
        $setOnInsert: {
          tab,
          name,
          enabled: enabled !== false,
          is_default: false,
          sort_order: sort_order ?? 0,
          created_by: null,
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

const { created, updated } = await upsertAll(SEED_PRESETS);

// The default flag is applied after every row exists, and as a pair of
// updates per tab rather than a field on the upsert: it is a property of
// the tab, so setting it means clearing it everywhere else.
if (!dryRun) {
  for (const row of SEED_PRESETS.filter((preset) => preset.is_default)) {
    const stored = await presets.findOne({ tab: row.tab, name: row.name });
    await presets.updateMany(
      { tab: row.tab, _id: { $ne: stored._id } },
      { $set: { is_default: false } },
    );
    await presets.updateOne({ _id: stored._id }, { $set: { is_default: true } });
  }
}

console.log(
  `presets   ${created} created, ${updated} updated` +
    (dryRun ? "  (dry run — nothing written)" : ""),
);

const live = await presets.countDocuments({ enabled: true });
const total = await presets.countDocuments({});
console.log(`\npresets now: ${total} total — ${live} offered to pods`);
for (const row of await presets.find({}).sort({ tab: 1, sort_order: 1, name: 1 })
  .limit(40).toArray()) {
  const state = row.enabled === true ? "on" : "off";
  console.log(
    `  ${String(row._id)}  ${(row.tab || "").padEnd(12)} ${state.padEnd(4)} ` +
      `${row.is_default === true ? "default" : "       "}  ${row.name}`,
  );
}

console.log("\nNext: npm run presets            (list, enable, disable)");
process.exit(0);
