// Publish a picture library: the row /v1/library reads to sign a pack URL.
//
//   npm run library -- list
//   npm run library -- set poses --version 1 --key libraries/poses/v1/pack.tar \
//       --sha256 <hex> --size <bytes> --features qwen_edit_2511
//
// The pack itself is uploaded first, to the PRIVATE builds bucket, by the
// app repo's scripts/r2_upload_poses.py — which prints the `set` line to
// run here. Writing the row is what publishes it: the next pod to open the
// picker without that version downloads it, and a pod that has an older
// one picks it up on its next start.
//
// Rolling back is `set` with the previous version's values; the older
// archive is still in the bucket, because every version has its own key.

import { collections } from "../src/db.js";
import { FEATURE_KEYS } from "../src/features.js";

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

const opts = args(process.argv.slice(2));
const [command, name] = opts._;
const { libraries } = await collections();

if (command === "list") {
  for (const row of await libraries.find({}).sort({ _id: 1 }).toArray()) {
    console.log(
      `${row._id}  v${row.version}  ${row.key}  ` +
        `${row.size ?? "?"} bytes  features=${(row.features || []).join(",")}  ` +
        `updated ${row.updated_at?.toISOString?.() ?? "-"}`,
    );
  }
  process.exit(0);
}

if (command !== "set" || !name) {
  die("usage: npm run library -- list",
      "       npm run library -- set <name> --version N --key K --sha256 H " +
        "--size BYTES --features a,b");
}
if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name)) die(`Bad library name: ${name}`);

const version = Number(opts.version);
if (!Number.isInteger(version) || version < 1) die("--version must be a positive integer");
if (typeof opts.key !== "string" || !/^[A-Za-z0-9._/-]{1,200}$/.test(opts.key) ||
    opts.key.includes("..")) {
  die("--key must be the object key the pack was uploaded to");
}
if (typeof opts.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(opts.sha256)) {
  die("--sha256 must be the pack's 64-character hex digest");
}
const size = Number(opts.size);
if (!Number.isInteger(size) || size <= 0) die("--size must be the pack's size in bytes");
const features = String(opts.features || "").split(",").map((s) => s.trim()).filter(Boolean);
if (!features.length) die("--features must name at least one tab");
const unknown = features.filter((key) => !FEATURE_KEYS.includes(key));
if (unknown.length) die(`Unknown feature key(s): ${unknown.join(", ")}`);

const before = await libraries.findOne({ _id: name });
await libraries.updateOne(
  { _id: name },
  {
    $set: { version, key: opts.key, sha256: opts.sha256, size, features, updated_at: new Date() },
  },
  { upsert: true },
);
console.log(
  before
    ? `${name}: v${before.version} -> v${version} (${opts.key})`
    : `${name}: published v${version} (${opts.key})`,
);
process.exit(0);
