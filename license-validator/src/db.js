// MongoDB connection, cached across serverless invocations.
//
// Each cold start would otherwise open its own connection and a busy
// deployment can exhaust an Atlas cluster's connection cap. Holding the
// client on globalThis lets warm invocations reuse it, which is the
// standard fix on Vercel and costs one module-scope variable.

import { MongoClient } from "mongodb";
import {
  MONGODB_URI,
  DB_NAME,
  SESSION_TTL_SECONDS,
  DOWNLOAD_TTL_SECONDS,
} from "./config.js";

let cached = globalThis.__krea2License;
if (!cached) cached = globalThis.__krea2License = { client: null, promise: null };

export async function getDb() {
  if (cached.client) return cached.client.db(DB_NAME);
  if (!cached.promise) {
    if (!MONGODB_URI) throw new Error("MONGODB_URI is not set");
    cached.promise = new MongoClient(MONGODB_URI, {
      maxPoolSize: 10,
      serverSelectionTimeoutMS: 8000,
    }).connect();
  }
  try {
    cached.client = await cached.promise;
  } catch (err) {
    // Clear the cache on failure. A rejected promise left in place would
    // be re-awaited by every later request for the life of this warm
    // instance, so one bad connect would keep failing instantly and never
    // retry — a transient Atlas blip would look permanent until the
    // instance recycled.
    cached.promise = null;
    cached.client = null;
    throw err;
  }
  return cached.client.db(DB_NAME);
}

export async function collections() {
  const db = await getDb();
  return {
    licenses: db.collection("licenses"),
    sessions: db.collection("sessions"),
    // Catalogue. Both use a string _id (the plan / feature key), so they
    // need no unique index of their own and a seed run is a plain upsert.
    plans: db.collection("plans"),
    features: db.collection("features"),
    // The prompt library. Unlike the two above this is written by pods, so
    // it carries a generated _id and is deduplicated on `fingerprint` — see
    // ensureIndexes.
    prompts: db.collection("prompts"),
    // Settings presets: a named settings blob per tab, offered to every pod
    // in a dropdown. The sibling of `prompts` and deliberately not the same
    // collection — a prompt is a whole recipe *including its text* and
    // arrives from customers for review, a preset carries no prompt text,
    // is only ever written by an admin, and is keyed by a name people read
    // rather than by a fingerprint nobody does.
    //
    // `enabled` is what the public read filters on: a preset that is off
    // stays in the collection and simply stops being offered, which is the
    // one thing you want when a preset turns out to be wrong.
    presets: db.collection("presets"),
    // Published builds, `_id` being the artifact's sha256 — the same value
    // that addresses it in R2. One document per build ever published, so
    // the collection is also the history a rollback picks from.
    //
    // `channels` is an array of the channel names currently pointing at
    // this build ("stable", usually). Promotion pulls the name off every
    // other document and pushes it onto one, which makes rolling back and
    // rolling forward the identical operation.
    //
    // A channel name is unique per `platform`, not across the collection:
    // "stable" sits on one Linux build and one Windows build at the same
    // time, because they are two artifacts of the same release. Documents
    // published before Windows existed carry no `platform` and are Linux
    // — see buildPlatform() in app.js.
    builds: db.collection("builds"),
    // One row per download URL issued. This is the visibility the gate
    // buys: seat counts say how many pods run at once, this says how many
    // distinct machines have ever pulled the binary on a given key.
    downloads: db.collection("downloads"),
  };
}

/**
 * Create the indexes the service depends on. Idempotent — safe to re-run.
 *
 * The compound unique index is what makes the acquire upsert safe: a pod
 * that restarts reclaims its own row instead of racing itself into two.
 */
export async function ensureIndexes() {
  const { licenses, sessions, prompts, presets, builds, downloads } =
    await collections();
  await licenses.createIndex({ key: 1 }, { unique: true, name: "key_unique" });
  await sessions.createIndex(
    { license_key: 1, instance_id: 1 },
    { unique: true, name: "license_instance_unique" },
  );
  await sessions.createIndex(
    { license_key: 1, last_seen: -1 },
    { name: "license_last_seen" },
  );
  await sessions.createIndex(
    { last_seen: 1 },
    { expireAfterSeconds: SESSION_TTL_SECONDS, name: "session_ttl" },
  );
  // Only ever queried by the admin listing and the "is this plan still in
  // use?" check that guards a plan deletion — both rare, but both scan the
  // whole collection without it.
  await licenses.createIndex({ plan_id: 1 }, { name: "plan_id" });

  // The prompt library. `fingerprint` is the deduplication key and the one
  // index that is load-bearing rather than an optimisation: the pod already
  // skips a recipe it has submitted before, but that memory is per-process,
  // so a restart, a second pod on the same licence, or two customers who
  // happen to type the same thing all arrive here as a repeat. Unique means
  // the upsert collapses every one of them into a single document instead
  // of quietly filling the collection with copies.
  await prompts.createIndex(
    { fingerprint: 1 },
    { unique: true, name: "fingerprint_unique" },
  );
  // The public listing: is_public first because it filters out almost
  // everything, then tab, then newest-first within that.
  await prompts.createIndex(
    { is_public: 1, tab: 1, created_at: -1 },
    { name: "public_tab_recent" },
  );
  // The review queue — oldest first, so the backlog is worked from the end
  // that has been waiting longest.
  await prompts.createIndex(
    { reviewed_at: 1, created_at: 1 },
    { name: "review_queue" },
  );
  // Only for moderation: "what else has this licence submitted?", and the
  // pending-count cap that POST /v1/prompts checks on every write.
  await prompts.createIndex(
    { license_key: 1, reviewed_at: 1 },
    { name: "license_pending" },
  );

  // Settings presets. A name identifies a preset within its tab — it is
  // what the dropdown shows and what people say to each other — so this
  // index is what keeps two of them apart. Saving from the app relies on it
  // twice over: it is the only race-free answer to "is this name free", and
  // its duplicate-key error is what makes a repeated name step to
  // "Portrait (2)" instead of overwriting the row (see insertUnique).
  //
  // Per tab, because the same name meaning one thing in Krea2 and another
  // in Krea2 V2 is normal — the two dropdowns are separate lists.
  await presets.createIndex(
    { tab: 1, name: 1 },
    { unique: true, name: "tab_name_unique" },
  );
  // The public listing: enabled first because it is what filters, then the
  // tab, then the display order the dropdown is built in.
  await presets.createIndex(
    { enabled: 1, tab: 1, sort_order: 1, name: 1 },
    { name: "enabled_tab_order" },
  );

  // Build distribution. The channel lookup runs on every pod boot that is
  // not already up to date, and it is the one query on the path between a
  // customer starting a pod and the app existing on it.
  //
  // Compound with `platform` because that lookup is now (channel,
  // platform): one build holds "stable" per operating system, so the
  // channel alone no longer identifies a single document. Nothing depends
  // on this index existing — the collection holds one row per build ever
  // published and a scan of it is nothing — so a deployment that never
  // runs `npm run init-db` is slower here and not broken.
  await builds.createIndex(
    { channels: 1, platform: 1 },
    { name: "channels_platform" },
  );
  await builds.createIndex({ published_at: -1 }, { name: "published" });

  // The rate-limit read: this licence's downloads, newest first. Unlike
  // the sessions TTL this one *is* only housekeeping — the hourly cap
  // filters on created_at in the query, exactly as acquire does.
  await downloads.createIndex(
    { license_key: 1, created_at: -1 },
    { name: "license_recent" },
  );
  await downloads.createIndex(
    { created_at: 1 },
    { expireAfterSeconds: DOWNLOAD_TTL_SECONDS, name: "download_ttl" },
  );
}
