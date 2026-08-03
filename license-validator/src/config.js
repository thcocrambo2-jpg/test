// Configuration, all from the environment.
//
// Nothing here is secret to the *client* — the client never sees any of it.
// The whole point of putting this service in front of Atlas is that the
// connection string lives here and not in a binary handed to customers.

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";

// Load .env for local runs (npm start, npm run init-db, npm run issue-key).
// Everything imports this module, so loading it here covers the scripts
// too. On Vercel there is no .env and the platform supplies the variables
// directly — dotenv is a silent no-op then, which is why this is not
// guarded by NODE_ENV. Existing process.env always wins, so a real
// deployment can never be overridden by a stray file.
//
// Resolved against this file rather than process.cwd(), so it still works
// when the scripts are run from the repository root instead of from here.
const packageRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
dotenv.config({ path: join(packageRoot, ".env"), quiet: true });

function int(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export const MONGODB_URI = process.env.MONGODB_URI || "";
export const DB_NAME = process.env.MONGODB_DB || "krea2_license";

// A session counts against the seat limit only while its last_seen is
// newer than this. It is the single number that makes an ungraceful death
// (SIGKILL, pod terminate, host failure) self-healing: nothing has to run
// on the client for the seat to come back.
export const STALE_SECONDS = int("STALE_SECONDS", 180);

// How often the client should call /v1/heartbeat. Sent in the acquire
// response so the cadence is server-controlled — you can slow every
// deployed binary down from here without reshipping anything.
export const HEARTBEAT_SECONDS = int("HEARTBEAT_SECONDS", 60);

// TTL index retention. Deliberately well above STALE_SECONDS: expiry is
// housekeeping to keep the collection small, not the correctness
// mechanism. Mongo's TTL monitor only runs about once a minute, so the
// acquire query filters on last_seen rather than trusting the sweep.
export const SESSION_TTL_SECONDS = int("SESSION_TTL_SECONDS", 900);

// Optional. When set, guards /v1/admin/*.
export const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

// ── Cloudflare R2: where the app binary is distributed from ──────────────
//
// The bucket is PRIVATE. Pods never address it directly — they ask
// /v1/build with their license key and get a presigned URL back, which is
// what makes an expired or revoked key unable to pull a new build at all.
//
// R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY must be an R2 API token with
// **Object Read only**, scoped to this one bucket. This service is
// public-facing and only ever signs GETs; a write-capable token here would
// turn any leak into the ability to replace the binary every customer
// downloads. Uploads use a separate read-write token that exists only on
// the build machine.
//
// Keep this bucket distinct from the public showcase-images bucket. Public
// access on R2 is a per-bucket setting, so a bucket reachable over r2.dev
// cannot also hold something that is meant to be gated.
export const R2_ACCOUNT_ID = process.env.R2_ACCOUNT_ID || "";
export const R2_ACCESS_KEY_ID = process.env.R2_ACCESS_KEY_ID || "";
export const R2_SECRET_ACCESS_KEY = process.env.R2_SECRET_ACCESS_KEY || "";
export const R2_BUILDS_BUCKET = process.env.R2_BUILDS_BUCKET || "krea2-builds";

// How long a download URL stays usable. Generous on purpose: the artifact
// is a few hundred megabytes and a pod on a slow link needs room for
// curl's retries, which restart the request and so are re-checked against
// this deadline. A transfer already in flight is not cut off when it
// passes. Short enough that a URL pasted somewhere is not a lasting
// bypass — and it is not the security boundary anyway, the seat check is.
export const BUILD_URL_TTL_SECONDS = int("BUILD_URL_TTL_SECONDS", 1800);

// Cap on presigned URLs per license per hour. This is the abuse signal the
// whole gate exists to give you: one key pulling builds from thirty
// machines is visible here and nowhere else. Set to 0 to disable the cap
// (the download log is still written either way).
export const BUILD_DOWNLOADS_PER_HOUR = int("BUILD_DOWNLOADS_PER_HOUR", 20);

// Retention for that log. Long enough to answer "who has been pulling
// this?" across a support conversation, short enough to stay small.
export const DOWNLOAD_TTL_SECONDS = int("DOWNLOAD_TTL_SECONDS", 30 * 24 * 3600);

// Where a customer is sent to change plan — a Telegram link to whoever
// handles sales. Served on /v1/plans and shown in the pricing page's
// "contact us" dialog; with it unset the dialog still explains what to do,
// it just has no link to offer. One value for the deployment rather than a
// field per plan: the same person handles every tier, and repeating the
// URL five times in Mongo is five places to forget when it changes.
export const CONTACT_URL = process.env.CONTACT_URL || "";

export const PORT = int("PORT", 3000);
