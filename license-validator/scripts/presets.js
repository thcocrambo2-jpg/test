// Manage the settings presets the app's dropdowns are built from.
//
//   npm run presets                                   # everything
//   npm run presets -- --tab krea_v2_t2i              # one tab
//   npm run presets -- --show 65f1...                 # one preset in full
//   npm run presets -- --enable  65f1...
//   npm run presets -- --disable 65f1...
//   npm run presets -- --default 65f1...              # what a session opens on
//   npm run presets -- --order 65f1... --to 10        # where it sits
//   npm run presets -- --rename 65f1... --to "Portrait, soft"
//   npm run presets -- --delete  65f1...
//   npm run presets -- --add --tab krea_t2i --name "Sharp landscape" \
//                      --settings ./settings.json [--description "..."]
//
// ── What a preset is, and is not ───────────────────────────────────────
//
// A named settings blob for one tab: model, steps, CFG, resolution, the
// LoRA stack — everything the tab's controls hold **except the prompt**.
// Prompts live in the prompt library and carry their settings with them;
// presets are the other way round, a way to move every dial at once and
// then type whatever you like.
//
// Only an admin licence can write one from the app (the tickbox next to
// Generate). Customers only read, and only ever see `enabled` rows.
//
// ── Disabling is the tool, deleting is the exception ───────────────────
//
// --disable takes a preset out of every dropdown and leaves the row where
// it is, which is what you want when one turns out to be wrong: it can come
// back with --enable and nothing had to be retyped. --delete is there for
// the preset that was a mistake to create at all. Neither touches a pod
// that is already running — a dropdown is built at startup, so a pod picks
// the change up when it next starts or when someone presses 🔄.
//
// Talks to Mongo directly rather than through the admin HTTP routes, like
// every other script here — one less thing to configure, and it works
// against a deployment whose ADMIN_TOKEN you have not set.

import { readFileSync } from "node:fs";

import { ObjectId } from "mongodb";

import { collections, ensureIndexes } from "../src/db.js";

const PRESET_TABS = ["krea_t2i", "krea_v2_t2i"];

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

/** One line per preset, short enough that the whole set stays readable. */
function line(row) {
  const state = row.enabled === true ? "on" : "off";
  const flag = row.is_default === true ? "default" : "       ";
  return (
    `  ${String(row._id)}  ${(row.tab || "").padEnd(12)} ${state.padEnd(4)} ` +
    `${flag}  ${String(row.sort_order ?? 0).padStart(3)}  ${row.name}`
  );
}

const opts = args(process.argv.slice(2));
const { presets } = await collections();
await ensureIndexes();

/** The one preset an id-taking flag names, or exit. */
async function target(raw, flag) {
  if (raw === true || !raw) die(`${flag} needs a preset id`);
  if (!ObjectId.isValid(raw)) die(`${flag}: ${raw} is not a preset id`);
  const row = await presets.findOne({ _id: new ObjectId(String(raw)) });
  if (!row) die(`no preset with id ${raw}`);
  return row;
}

async function list() {
  const filter = {};
  if (typeof opts.tab === "string") {
    if (!PRESET_TABS.includes(opts.tab)) {
      die(`--tab must be one of: ${PRESET_TABS.join(", ")}`);
    }
    filter.tab = opts.tab;
  }
  const rows = await presets
    .find(filter)
    .sort({ tab: 1, sort_order: 1, name: 1 })
    .toArray();
  if (!rows.length) {
    console.log("no presets yet — npm run seed-presets writes the defaults");
    return;
  }
  console.log("  id                        tab          st   default  ord  name");
  for (const row of rows) console.log(line(row));
  const live = rows.filter((row) => row.enabled === true).length;
  console.log(`\n${rows.length} preset(s), ${live} offered to pods`);
}

// ── One preset, in full ────────────────────────────────────────────────

if (opts.show) {
  const row = await target(opts.show, "--show");
  console.log(`id          ${row._id}`);
  console.log(`tab         ${row.tab}`);
  console.log(`name        ${row.name}`);
  console.log(`description ${row.description || "-"}`);
  console.log(`enabled     ${row.enabled === true}`);
  console.log(`is_default  ${row.is_default === true}`);
  console.log(`sort_order  ${row.sort_order ?? 0}`);
  console.log(`created_by  ${row.created_by || "-"}`);
  console.log(`updated_at  ${row.updated_at || row.created_at || "-"}`);
  console.log(`\nsettings\n${JSON.stringify(row.settings || {}, null, 2)}`);
  process.exit(0);
}

// ── Author one from a settings file ────────────────────────────────────
//
// The file is the settings object as the tab stores it — the quickest way
// to get one that is certainly right is to copy it out of a prompt:
// `npm run prompts -- --show <id>` prints exactly this shape.

if (opts.add) {
  if (!PRESET_TABS.includes(opts.tab)) {
    die(`--add needs --tab, one of: ${PRESET_TABS.join(", ")}`);
  }
  const name = typeof opts.name === "string" ? opts.name.trim() : "";
  if (!name) die("--add needs --name");
  if (typeof opts.settings !== "string") {
    die("--add needs --settings <path to a .json file>");
  }
  let settings;
  try {
    settings = JSON.parse(readFileSync(opts.settings, "utf8"));
  } catch (err) {
    die(`could not read --settings: ${err.message}`);
  }
  if (settings === null || typeof settings !== "object" ||
      Array.isArray(settings)) {
    die("--settings must hold a JSON object");
  }

  const now = new Date();
  const result = await presets.updateOne(
    { tab: opts.tab, name },
    {
      $set: {
        settings,
        description:
          typeof opts.description === "string" ? opts.description : null,
        updated_at: now,
      },
      // Re-adding under a name that exists edits that preset and leaves the
      // decisions you made about it — on/off, default, position — alone.
      $setOnInsert: {
        tab: opts.tab,
        name,
        enabled: true,
        is_default: false,
        sort_order: 0,
        created_by: null,
        created_at: now,
      },
    },
    { upsert: true },
  );
  console.log(
    `${result.upsertedCount ? "created" : "updated"} ${opts.tab}/${name}`,
  );
  await list();
  process.exit(0);
}

// ── The one-flag edits ─────────────────────────────────────────────────

if (opts.enable || opts.disable) {
  const enable = Boolean(opts.enable);
  const row = await target(enable ? opts.enable : opts.disable,
                           enable ? "--enable" : "--disable");
  await presets.updateOne(
    { _id: row._id },
    { $set: { enabled: enable, updated_at: new Date() } },
  );
  console.log(`${enable ? "enabled" : "disabled"} ${row.tab}/${row.name}`);
  if (!enable && row.is_default === true) {
    // Left as the default on purpose, and said out loud: the flag answers
    // "which one does a fresh session open on", and a disabled preset
    // answering it means the tab opens on nothing until you move it.
    console.log(
      "  ⚠️  this was the tab's default — set another with --default <id>",
    );
  }
  await list();
  process.exit(0);
}

if (opts.default) {
  const row = await target(opts.default, "--default");
  // A property of the tab, so setting it here clears it everywhere else.
  await presets.updateMany(
    { tab: row.tab, _id: { $ne: row._id } },
    { $set: { is_default: false } },
  );
  await presets.updateOne({ _id: row._id }, { $set: { is_default: true } });
  if (row.enabled !== true) {
    console.log("  ⚠️  this preset is disabled — --enable it to have any effect");
  }
  console.log(`${row.tab} now opens on "${row.name}"`);
  await list();
  process.exit(0);
}

if (opts.order) {
  const row = await target(opts.order, "--order");
  const to = Number.parseInt(opts.to, 10);
  if (!Number.isFinite(to)) die("--order needs --to <number>");
  await presets.updateOne({ _id: row._id }, { $set: { sort_order: to } });
  console.log(`${row.tab}/${row.name} → position ${to}`);
  await list();
  process.exit(0);
}

if (opts.rename) {
  const row = await target(opts.rename, "--rename");
  const to = typeof opts.to === "string" ? opts.to.trim() : "";
  if (!to) die('--rename needs --to "the new name"');
  if (await presets.findOne({ tab: row.tab, name: to })) {
    die(`${row.tab} already has a preset called "${to}"`);
  }
  await presets.updateOne(
    { _id: row._id },
    { $set: { name: to, updated_at: new Date() } },
  );
  console.log(`${row.tab}/${row.name} → "${to}"`);
  await list();
  process.exit(0);
}

if (opts.delete) {
  const row = await target(opts.delete, "--delete");
  await presets.deleteOne({ _id: row._id });
  console.log(`deleted ${row.tab}/${row.name}`);
  console.log("  (--disable keeps a preset out of the dropdowns without this)");
  await list();
  process.exit(0);
}

await list();
process.exit(0);
