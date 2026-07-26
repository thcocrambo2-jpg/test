// MongoDB connection, cached across serverless invocations.
//
// Each cold start would otherwise open its own connection and a busy
// deployment can exhaust an Atlas cluster's connection cap. Holding the
// client on globalThis lets warm invocations reuse it, which is the
// standard fix on Vercel and costs one module-scope variable.

import { MongoClient } from "mongodb";
import { MONGODB_URI, DB_NAME, SESSION_TTL_SECONDS } from "./config.js";

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
  };
}

/**
 * Create the indexes the service depends on. Idempotent — safe to re-run.
 *
 * The compound unique index is what makes the acquire upsert safe: a pod
 * that restarts reclaims its own row instead of racing itself into two.
 */
export async function ensureIndexes() {
  const { licenses, sessions } = await collections();
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
}
