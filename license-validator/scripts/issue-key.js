// Issue a license key for a customer.
//
//   npm run issue-key -- --name "Acme Corp" --seats 2
//   npm run issue-key -- --name "Trial" --seats 1 --days 30
//   npm run issue-key -- --name "Acme Corp" --seats 3 --update
//   npm run issue-key -- --key KREA2-XXXX-XXXX-XXXX --revoke
//
// The generated key is what the customer puts in KREA2_LICENSE_KEY on
// their pod. Keys are stored in plain text so you can match a key to a
// customer while supporting them; the collection is never exposed to
// clients, which is the point of this service sitting in front of it.

import { randomBytes } from "node:crypto";
import { collections, ensureIndexes } from "../src/db.js";

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

/** KREA2-XXXX-XXXX-XXXX from a rejection-free alphabet (no O/0/I/1). */
function generateKey() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = randomBytes(12);
  const chars = [...bytes].map((b) => alphabet[b % alphabet.length]);
  return [
    "KREA2",
    chars.slice(0, 4).join(""),
    chars.slice(4, 8).join(""),
    chars.slice(8, 12).join(""),
  ].join("-");
}

const opts = args(process.argv.slice(2));
const { licenses } = await collections();
await ensureIndexes();

if (opts.revoke || opts.enable) {
  if (!opts.key) {
    console.error("--revoke/--enable needs --key KREA2-...");
    process.exit(1);
  }
  const active = Boolean(opts.enable);
  const result = await licenses.updateOne(
    { key: opts.key },
    { $set: { active, updated_at: new Date() } },
  );
  if (!result.matchedCount) {
    console.error(`no license with key ${opts.key}`);
    process.exit(1);
  }
  // Running instances notice on their next heartbeat, so a revoke takes
  // effect in about a minute rather than only blocking the next start.
  console.log(`${active ? "enabled" : "revoked"}  ${opts.key}`);
  process.exit(0);
}

if (!opts.name && !opts.key) {
  console.error("usage: npm run issue-key -- --name \"Acme Corp\" --seats 2");
  console.error("       [--days 30] [--update] [--key KREA2-...] [--revoke]");
  process.exit(1);
}

const seats = Number.parseInt(opts.seats, 10) || 1;
const expires_at = opts.days
  ? new Date(Date.now() + Number.parseInt(opts.days, 10) * 86400_000)
  : null;

if (opts.update) {
  const filter = opts.key ? { key: opts.key } : { name: opts.name };
  const update = { seats, active: true, updated_at: new Date() };
  if (opts.days) update.expires_at = expires_at;
  const result = await licenses.findOneAndUpdate(
    filter,
    { $set: update },
    { returnDocument: "after" },
  );
  if (!result) {
    console.error("no matching license to update");
    process.exit(1);
  }
  console.log(`updated  ${result.key}  seats=${result.seats}`);
  process.exit(0);
}

const key = opts.key && opts.key !== true ? opts.key : generateKey();
await licenses.insertOne({
  key,
  name: opts.name === true ? null : opts.name,
  seats,
  active: true,
  expires_at,
  created_at: new Date(),
});

console.log(`\n  customer   ${opts.name}`);
console.log(`  key        ${key}`);
console.log(`  seats      ${seats}`);
console.log(`  expires    ${expires_at ? expires_at.toISOString() : "never"}`);
console.log("\nGive the customer this, to set on their RunPod pod:");
console.log(`\n  KREA2_LICENSE_KEY=${key}\n`);
process.exit(0);
