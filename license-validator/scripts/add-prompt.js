// Moderate the prompt library, and author the curated half of it.
//
//   npm run prompts                                    # the review queue
//   npm run prompts -- --list --all                    # everything
//   npm run prompts -- --show 65f1...                  # one prompt in full
//   npm run prompts -- --approve 65f1...
//   npm run prompts -- --reject  65f1...
//   npm run prompts -- --add --tab krea_v2_t2i --title "Golden hour portrait" \
//                      --prompt "..." --settings ./settings.json
//
// ── What the two halves of the collection are ──────────────────────────
//
// Pods write `source: "community"` rows on every generation whose recipe
// they have not sent before. Those arrive **private** — `is_public: false`,
// `reviewed_at: null` — and no customer can see one until --approve flips
// it. That queue is what this script exists for.
//
// `source: "admin"` rows are the ones you write with --add. They are public
// immediately, because the thing approval guards against is content nobody
// chose, and these are content you chose.
//
// ── Rejecting is a decision, not a delete ──────────────────────────────
//
// --reject sets `reviewed_at` and leaves `is_public` false, so the row
// leaves the queue and never comes back. It is deliberately not a deletion:
// the fingerprint stays unique, so the same recipe submitted again by
// another pod tomorrow collapses onto the row you already ruled on instead
// of reappearing as new work.
//
// Talks to Mongo directly rather than through the admin HTTP routes, like
// every other script here — one less thing to configure, and it works
// against a deployment whose ADMIN_TOKEN you have not set.

import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

import { ObjectId } from "mongodb";

import { collections, ensureIndexes } from "../src/db.js";

const PROMPT_TABS = ["krea_t2i", "krea_v2_t2i"];

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

/** One line per prompt, short enough that a queue of 50 is still readable. */
function line(row) {
  const badge = row.source === "admin" ? "admin" : "community";
  const state = row.is_public
    ? "public"
    : row.reviewed_at
      ? "rejected"
      : "pending";
  const oneLine = (row.title || row.prompt || "")
    .replace(/\s+/g, " ")
    .slice(0, 68);
  return (
    `  ${String(row._id)}  ${badge.padEnd(9)} ${state.padEnd(8)} ` +
    `${(row.tab || "").padEnd(12)} ${oneLine}`
  );
}

const opts = args(process.argv.slice(2));
const { prompts } = await collections();
await ensureIndexes();

/** The one prompt an --approve/--reject/--show names, or exit. */
async function target(raw, flag) {
  if (raw === true || !raw) die(`${flag} needs a prompt id`);
  if (!ObjectId.isValid(raw)) die(`${flag}: ${raw} is not a prompt id`);
  const row = await prompts.findOne({ _id: new ObjectId(String(raw)) });
  if (!row) die(`no prompt with id ${raw}`);
  return row;
}

// ── Review ─────────────────────────────────────────────────────────────

if (opts.approve || opts.reject) {
  const approve = Boolean(opts.approve);
  const row = await target(opts.approve || opts.reject,
                           approve ? "--approve" : "--reject");
  await prompts.updateOne(
    { _id: row._id },
    { $set: { is_public: approve, reviewed_at: new Date() } },
  );
  console.log(`\n${approve ? "approved" : "rejected"}  ${row._id}`);
  console.log(`  tab      ${row.tab}`);
  console.log(`  prompt   ${(row.prompt || "").replace(/\s+/g, " ").slice(0, 200)}`);
  if (approve) {
    // Worth saying: the client caches the library for five minutes, so an
    // approval is not instantly visible even though it is instantly live.
    console.log(
      "\nLive now. A pod that already has the library open sees it within " +
        "5 minutes, or immediately on 🔄 Refresh.",
    );
  }
  process.exit(0);
}

// ── Show one ───────────────────────────────────────────────────────────

if (opts.show) {
  const row = await target(opts.show, "--show");
  console.log(`\n  id         ${row._id}`);
  console.log(`  tab        ${row.tab}`);
  console.log(`  source     ${row.source}`);
  console.log(
    `  state      ${row.is_public ? "public" : row.reviewed_at ? "rejected" : "pending"}`,
  );
  console.log(`  licence    ${row.license_key || "(admin-authored)"}`);
  console.log(`  seen       ${row.seen_count ?? 0}×`);
  console.log(`  created    ${row.created_at?.toISOString?.() || "?"}`);
  console.log(`\nprompt:\n${row.prompt}`);
  if (row.negative) console.log(`\nnegative:\n${row.negative}`);
  console.log(`\nsettings:\n${JSON.stringify(row.settings, null, 2)}`);
  process.exit(0);
}

// ── Author an admin prompt ─────────────────────────────────────────────

if (opts.add) {
  if (!PROMPT_TABS.includes(opts.tab)) {
    die(`--tab must be one of: ${PROMPT_TABS.join(", ")}`);
  }
  if (!opts.prompt || opts.prompt === true) die("--add needs --prompt");
  if (!opts.settings || opts.settings === true) {
    die(
      "--add needs --settings ./file.json",
      "",
      "The easiest way to produce one: run the prompt in the app, then copy " +
        "the `settings` object of the row it created (npm run prompts -- --show <id>).",
    );
  }

  let settings;
  try {
    settings = JSON.parse(readFileSync(String(opts.settings), "utf8"));
  } catch (err) {
    die(`could not read --settings ${opts.settings}: ${err.message}`);
  }
  if (settings === null || typeof settings !== "object" ||
      Array.isArray(settings)) {
    die("--settings must contain a JSON object");
  }

  const now = new Date();
  const doc = {
    // Generated, never derived from the content: this only has to satisfy
    // the unique index, and a content hash would let an admin prompt
    // collide with a community submission of the same recipe and silently
    // do nothing at all.
    fingerprint: randomUUID().replace(/-/g, "").padEnd(64, "0"),
    tab: opts.tab,
    source: "admin",
    is_public: true,
    reviewed_at: now,
    title: opts.title === true ? null : opts.title || null,
    prompt: String(opts.prompt).slice(0, 4000),
    negative:
      opts.negative && opts.negative !== true
        ? String(opts.negative).slice(0, 4000)
        : "",
    settings,
    license_key: null,
    seen_count: 0,
    created_at: now,
    updated_at: now,
  };
  await prompts.insertOne(doc);
  console.log(`\nadded  ${doc._id}  (public immediately)`);
  console.log(line(doc));
  process.exit(0);
}

// ── List (the default) ─────────────────────────────────────────────────

const filter = opts.all ? {} : { reviewed_at: null };
const rows = await prompts
  .find(filter)
  .sort({ reviewed_at: 1, created_at: 1 })
  .limit(200)
  .toArray();

const pending = await prompts.countDocuments({ reviewed_at: null });
const live = await prompts.countDocuments({ is_public: true });

if (!rows.length) {
  console.log(
    opts.all
      ? "\nThe prompt library is empty."
      : "\nNothing waiting for review.",
  );
} else {
  console.log(
    `\n${opts.all ? "every prompt" : "waiting for review"} ` +
      `(${rows.length}${rows.length === 200 ? ", capped" : ""}):\n`,
  );
  for (const row of rows) console.log(line(row));
}

console.log(`\n  ${pending} pending · ${live} live`);
console.log("\nnpm run prompts -- --show <id> | --approve <id> | --reject <id>");
process.exit(0);
