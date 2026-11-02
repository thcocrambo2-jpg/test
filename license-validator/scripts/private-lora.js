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
// loras/private/<file> — by the trainer page's Publish (which also does
// what `add` and `grant` do, through /v1/admin/private-loras), or by the
// app repo's scripts/r2_upload_lora.py, which prints the `add` line.
//
// ── How it is private ──────────────────────────────────────────────────
//
// The record is an ordinary `loras` row whose source is
// {kind: "r2", key: "loras/private/<file>"}. POST /v1/catalog leaves such a
// row out — the record and its place in every feature list — for every
// licence whose `private_loras` does not name its id, so those keys cannot
// even see that it exists. A licence that does name it gets the row with a
// signed download link in its source.
//
// `add` puts the id at the end of each feature's LoRA list (the Krea2 V2
// and Krea2 tabs by default). The lists are shared by every licence, and
// that is safe: an id a licence is not sent is cut from its lists too.
//
// A pod reads the catalogue once at startup, so a grant reaches a running
// pod on its next start. A pod on a build older than private LoRAs skips
// the row with a warning and is otherwise unaffected.
//
// Talks to Mongo directly, like every other script here; the writes are
// src/privateLoras.js, the same code the admin routes run.

import {
  DEFAULT_FEATURES,
  PrivateLoraError,
  grantPrivateLora,
  listPrivateLoras,
  revokePrivateLora,
  upsertPrivateLora,
} from "../src/privateLoras.js";

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

const list = (value) =>
  typeof value === "string" ? value.split(",").map((s) => s.trim()).filter(Boolean) : [];

const opts = args(process.argv.slice(2));
const [command, id] = opts._;

try {
  if (command === "list" || command === undefined) {
    const rows = await listPrivateLoras();
    console.log(`\nprivate loras (${rows.length})`);
    for (const row of rows) {
      console.log(
        `  ${row.enabled ? "on " : "OFF"} ${row.id}  ${row.file}  trigger=${row.trigger || "-"}` +
          (row.valid ? "" : "  ⚠️  invalid — not served"),
      );
      console.log(`      tabs: ${row.tabs.join(", ") || "none"}`);
      console.log(
        `      granted to ${row.licences.length}: ` +
          (row.licences.map((l) => `${l.name || "?"} (${l.key})`).join(", ") || "nobody"),
      );
    }
  } else if (command === "add") {
    if (typeof opts.file !== "string") throw new PrivateLoraError("--file <name>.safetensors is required.");
    const result = await upsertPrivateLora({
      id,
      file: opts.file,
      name: typeof opts.name === "string" ? opts.name : undefined,
      trigger: typeof opts.trigger === "string" ? opts.trigger : "",
      default_strength: opts.strength === undefined ? 1.0 : Number(opts.strength),
      features: opts.features ? list(opts.features) : DEFAULT_FEATURES,
    });
    console.log(`${result.created ? "created" : "updated"} private lora ${result.id} → ${result.key}`);
    for (const [feature, what] of Object.entries(result.lists)) console.log(`  ${feature}: ${what}`);
    console.log(`\nNext, if nobody has it yet:\n  npm run private-lora -- grant ${result.id} --keys KEY1,KEY2`);
  } else if (command === "grant") {
    const result = await grantPrivateLora(id, list(opts.keys));
    console.log(`granted ${result.id} to ${result.licences.length} licence(s) (${result.newly} newly):`);
    for (const l of result.licences) console.log(`  ${l.name || "?"} (${l.key})`);
    console.log("Each pod picks it up on its next start.");
  } else if (command === "revoke") {
    const result = await revokePrivateLora(id, list(opts.keys), { all: opts.all === true });
    console.log(`revoked ${result.id} from ${result.revoked} licence(s).`);
    console.log(
      "A pod that already downloaded the file keeps it on disk, but its tabs " +
        "stop offering it from its next start.",
    );
  } else {
    throw new PrivateLoraError(`Unknown command ${JSON.stringify(command)} — list, add, grant or revoke.`);
  }
  process.exit(0);
} catch (err) {
  if (!(err instanceof PrivateLoraError)) throw err;
  console.error(err.message);
  process.exit(1);
}
