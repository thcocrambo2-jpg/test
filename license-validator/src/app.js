// Seat-limited license API.
//
// Three endpoints the app calls for its seat (/v1/acquire, /v1/heartbeat,
// /v1/release), one the *start script* calls before the app exists at all
// (/v1/build), three public reads it renders pages from (/v1/plans,
// /v1/prompts, /v1/presets), one public write it makes silently
// (POST /v1/prompts) and one it makes on an admin's say-so
// (POST /v1/presets), plus health and the admin surface.
//
// The status code carries the contract, and the Python client branches on
// exactly this — keep it stable:
//
//   200  ok                                        proceed
//   400  bad_request                               client bug, do not retry
//   403  invalid_key | revoked | expired |         stop the app now
//        seat_limit
//        forbidden                                 POST /v1/presets only:
//                                                  the licence is fine, it
//                                                  is just not an admin
//   429  rate_limited                              /v1/build only
//   503  server_error | no_build                   transient: retry at
//                                                  startup, or ride the
//                                                  grace window if running
//
// /v1/build is the one endpoint whose caller is bash rather than Python,
// and the one that runs before there is an app to stop. It does not
// branch as finely: scripts/runpod_start.sh falls back to the binary
// already on the volume for *any* non-200, and lets the seat check that
// follows deliver the real verdict. A revoked key running a cached build
// gets licensing.py's message, which is the one worth showing, instead of
// a download error that says nothing about why.
//
// The 403/503 split is the important one. A database outage must never
// look like a license violation, or an Atlas blip reads to your customers
// as an accusation — and a real violation must never look transient, or
// the grace window makes it survivable.

import { randomUUID } from "node:crypto";

import express from "express";
import cors from "cors";
import { ObjectId } from "mongodb";

import { collections } from "./db.js";
import { allFeatures, isFeatureEnabled, sortByRegistry } from "./features.js";
import {
  allPlans,
  billingCycles,
  cyclePrice,
  resolveEntitlement,
} from "./plans.js";
import {
  STALE_SECONDS,
  HEARTBEAT_SECONDS,
  ADMIN_TOKEN,
  CONTACT_URL,
  DB_NAME,
  BUILD_URL_TTL_SECONDS,
  BUILD_DOWNLOADS_PER_HOUR,
} from "./config.js";
import {
  DEFAULT_FILENAME,
  buildKey,
  presignGet,
  r2Configured,
} from "./r2.js";
import telegramRouter from "./telegram/router.js";

const app = express();

app.set("trust proxy", true);
app.disable("x-powered-by");   // no need to advertise the framework/version

// ── Telegram bot ────────────────────────────────────────────────────────
//
// The payment plane, on the same app because provisioning is a module
// call rather than a network hop — see TELEGRAM_BOT_PLAN.md §3. With
// TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET unset it answers 404 to
// everything, exactly as the admin routes do without an ADMIN_TOKEN.
//
// Mounted ABOVE the global body parser on purpose. The 16kb limit below
// is a deliberate bound on what a pod may submit to /v1/prompts, and it
// applies to every route registered after it — so a Telegram router
// mounted underneath would be capped at 16kb however large its own
// parser was, and an update that exceeded it would 413 and be retried by
// Telegram for a day. Registering it first lets it read the body with
// its own express.json({ limit: "64kb" }) and leaves the global cap
// exactly where it was for everything else.
app.use("/tg", telegramRouter);

app.use(express.json({ limit: "16kb" }));

// Wide open on purpose. The real traffic is server-side: the pod calls
// this from Python, so no Origin header is sent and CORS never applies to
// it. The permissiveness is for anything browser-side you add later —
// there is no origin worth pinning anyway, since the Gradio share URL is
// freshly generated on every run.
app.use(cors({ origin: true }));
app.options("*", cors({ origin: true }));

/** Wrap an async handler so a thrown error becomes 503, never a crash. */
function wrap(handler) {
  return (req, res, next) => {
    Promise.resolve(handler(req, res, next)).catch((err) => {
      console.error(`${req.method} ${req.path} failed:`, err);
      // 503 and not 500: this is the code the client treats as retryable.
      res.status(503).json({
        ok: false,
        error: "server_error",
        message: "The license service is temporarily unavailable.",
      });
    });
  };
}

function badRequest(res, message) {
  return res.status(400).json({ ok: false, error: "bad_request", message });
}

/** Why this license cannot be used, or null when it is fine. */
function licenseProblem(license) {
  if (!license) {
    return {
      error: "invalid_key",
      message: "This license key is not recognised.",
    };
  }
  if (license.active === false) {
    return {
      error: "revoked",
      message: "This license key has been revoked. Contact your supplier.",
    };
  }
  if (license.expires_at && new Date(license.expires_at) < new Date()) {
    return {
      error: "expired",
      message: `This license expired on ${new Date(license.expires_at)
        .toISOString().slice(0, 10)}.`,
    };
  }
  return null;
}

/** Keep only the few fields worth storing, bounded in size. */
function sanitizeMeta(meta, req) {
  const out = {
    ip: req.ip || null,
    seen_at: new Date(),
  };
  if (meta && typeof meta === "object") {
    for (const field of ["pod_id", "hostname", "version", "gpu"]) {
      const value = meta[field];
      if (typeof value === "string" && value) out[field] = value.slice(0, 200);
    }
  }
  return out;
}

const cutoffDate = () => new Date(Date.now() - STALE_SECONDS * 1000);

const countLive = (sessions, license_key) =>
  sessions.countDocuments({ license_key, last_seen: { $gt: cutoffDate() } });

// `features` is the entitlement the client acts on: it is the only thing
// that decides which tabs get built and which weights get downloaded, so
// it is sent on every 200 — acquire, where it is applied, and heartbeat,
// where a change tells a running instance it needs a restart to pick the
// new set up. null means the document says nothing and the client should
// use its own defaults.
//
// This is the *only* place plans are resolved. The array below is a flat
// list of feature keys either way, so the Python client never learns that
// tiers exist and needs no rebuild for any of it — see plans.js. A license
// whose plan_id names nothing throws, which wrap() turns into a 503.
//
// plan_id / plan_name / expires_at are labels: the app bar shows the tier
// and the date, and the pricing page marks the tier the customer is on.
// Nothing is enforced by them being read — expiry is checked here, in
// licenseProblem(), on every acquire and every heartbeat, so a client that
// ignores the date (an older build does) is no less bounded by it. Sending
// null for a perpetual key is deliberate: the client renders no date, and
// "no expiry" is exactly what it should show.
//
// `feature_info` is what the client titles its tabs from, and is a sibling
// of `features` rather than a richer replacement for it on purpose: the
// entitlement stays a flat array of keys, so a build that predates this
// field ignores it and behaves exactly as it did. Only the granted keys
// are sent — the whole catalogue is /v1/plans' job, and this rides every
// heartbeat, so it is kept to the few short strings the tab strip needs.
//
// `is_admin` is a *role*, and the only one this service has. It is not an
// entitlement and grants no tab — what a licence can run still comes from
// `features` alone, so marking a key admin never changes what it can
// generate. It changes one thing: whether the app captures prompts into
// the library automatically. Ordinary pods do, silently; an admin pod does
// not, and publishes only what its operator ticks the box for. That way
// your own test generations do not fill the review queue you are the one
// working through.
async function seatPayload(license, inUse) {
  const entitlement = await resolveEntitlement(license);
  return {
    ok: true,
    license_name: license.name || null,
    is_admin: license.is_admin === true,
    seats: license.seats,
    seats_in_use: inUse,
    features: entitlement.features,
    feature_info: await grantedInfo(entitlement.features),
    plan_id: entitlement.plan_id,
    plan_name: entitlement.plan_name,
    expires_at: license.expires_at || null,
    heartbeat_seconds: HEARTBEAT_SECONDS,
    stale_seconds: STALE_SECONDS,
  };
}

/**
 * Display strings for the granted keys, by key. Null when `features` is.
 *
 * A key the catalogue does not describe is simply left out rather than
 * given an invented name: the client already turns a bare key into a
 * readable label, and it is the side that knows what its own tab is
 * called. Sending a guess from here would override a correct built-in
 * label with a worse one.
 *
 * Never throws. This is the one field on the payload that is purely
 * cosmetic, and it is the only reason acquire touches the features
 * collection at all — letting a read failure here 503 a seat request that
 * is otherwise perfectly good would trade a working pod for a tab title.
 * The client falls back to its built-in labels on an absent field, which
 * is exactly the older-server case it already handles.
 */
async function grantedInfo(features) {
  if (!features) return null;
  let catalogue;
  try {
    catalogue = await allFeatures();
  } catch (err) {
    console.error("feature catalogue unavailable, sending no labels:", err);
    return null;
  }
  const out = {};
  for (const key of features) {
    const row = catalogue.get(key);
    if (!row) continue;
    out[key] = {
      name: row.name || null,
      tab_label: row.tab_label || null,
    };
  }
  return out;
}

// ── Acquire ─────────────────────────────────────────────────────────────
//
// Takes a seat for one running instance.
//
// A session only counts while last_seen is inside the stale window, so a
// client that died without releasing frees its seat by falling out of that
// window — nothing has to run on the client, and no cleanup job exists.
// Re-acquiring your own instance_id is always free, which is what lets a
// pod restart the app without spending a second seat.
app.post(
  "/v1/acquire",
  wrap(async (req, res) => {
    const { license_key, instance_id, meta } = req.body || {};
    if (!license_key || !instance_id) {
      return badRequest(res, "license_key and instance_id are required.");
    }

    const { licenses, sessions } = await collections();
    const license = await licenses.findOne({ key: license_key });
    const problem = licenseProblem(license);
    if (problem) return res.status(403).json({ ok: false, ...problem });

    const now = new Date();
    const own = await sessions.findOne({ license_key, instance_id });
    const ownIsLive = own && own.last_seen > cutoffDate();

    if (!ownIsLive) {
      // A stale row of our own is excluded by the cutoff, so it does not
      // inflate this count and reclaiming it stays correct.
      //
      // Note this count-then-insert is not atomic: two instances starting
      // in the same millisecond can both pass. At these seat counts the
      // window is negligible and the worst case is one extra seat, which
      // is not worth a locking scheme.
      const inUse = await countLive(sessions, license_key);
      if (inUse >= license.seats) {
        return res.status(403).json({
          ok: false,
          error: "seat_limit",
          message:
            `All ${license.seats} seat(s) on this license are in use. ` +
            `Stop another running instance and try again — a seat that ` +
            `was not released cleanly frees itself within ` +
            `${Math.ceil(STALE_SECONDS / 60)} minute(s).`,
          seats: license.seats,
          seats_in_use: inUse,
        });
      }
    }

    await sessions.updateOne(
      { license_key, instance_id },
      {
        $set: { last_seen: now, meta: sanitizeMeta(meta, req) },
        $setOnInsert: { created_at: now },
      },
      { upsert: true },
    );

    const inUse = await countLive(sessions, license_key);
    console.log(
      `acquire  key=${license_key} instance=${instance_id} ` +
        `${inUse}/${license.seats}${ownIsLive ? " (renewal)" : ""}`,
    );
    res.json(await seatPayload(license, inUse));
  }),
);

// ── Heartbeat ───────────────────────────────────────────────────────────
//
// Re-checks the license every time, so revoking a key or letting it expire
// stops running instances within about one heartbeat instead of only
// blocking the next start.
app.post(
  "/v1/heartbeat",
  wrap(async (req, res) => {
    const { license_key, instance_id } = req.body || {};
    if (!license_key || !instance_id) {
      return badRequest(res, "license_key and instance_id are required.");
    }

    const { licenses, sessions } = await collections();
    const license = await licenses.findOne({ key: license_key });
    const problem = licenseProblem(license);
    if (problem) return res.status(403).json({ ok: false, ...problem });

    const now = new Date();
    const result = await sessions.updateOne(
      { license_key, instance_id },
      { $set: { last_seen: now }, $setOnInsert: { created_at: now } },
    );

    // Row gone: the client was unreachable long enough for the TTL sweep,
    // or it was deleted by hand. Re-take a seat if one is free rather than
    // killing a job that is otherwise healthy.
    if (result.matchedCount === 0) {
      const inUse = await countLive(sessions, license_key);
      if (inUse >= license.seats) {
        return res.status(403).json({
          ok: false,
          error: "seat_limit",
          message:
            "This session expired and its seat has been taken by another " +
            "instance.",
          seats: license.seats,
          seats_in_use: inUse,
        });
      }
      await sessions.insertOne({
        license_key,
        instance_id,
        last_seen: now,
        created_at: now,
        meta: sanitizeMeta(null, req),
      });
      return res.json({
        ...(await seatPayload(license, inUse + 1)),
        reacquired: true,
      });
    }

    res.json(await seatPayload(license, await countLive(sessions, license_key)));
  }),
);

// ── Release ─────────────────────────────────────────────────────────────
//
// Frees a seat immediately on a clean shutdown. Purely an optimisation:
// the stale window would free it anyway, so this is always idempotent and
// always answers 200 — a client that is exiting has nothing useful to do
// with a failure.
app.post(
  "/v1/release",
  wrap(async (req, res) => {
    const { license_key, instance_id } = req.body || {};
    if (!license_key || !instance_id) {
      return badRequest(res, "license_key and instance_id are required.");
    }
    const { sessions } = await collections();
    const result = await sessions.deleteOne({ license_key, instance_id });
    console.log(
      `release  key=${license_key} instance=${instance_id} ` +
        `deleted=${result.deletedCount}`,
    );
    res.json({ ok: true, released: result.deletedCount > 0 });
  }),
);

// ── Platforms ───────────────────────────────────────────────────────────
//
// A build is for one operating system, and handing the wrong one to a
// client is the failure mode with no downstream check: the bytes transfer,
// the sha256 matches, and the pod dies with "Exec format error" — or on
// Windows, with nothing at all. So the OS is a field on the build document
// and a filter on every lookup, rather than a convention in the channel
// name.
//
// Platform is deliberately NOT folded into the channel ("windows-stable").
// The channel is resolved from the *licence*, and a licence is per
// customer, not per machine: a customer with a RunPod pod and a Windows
// desktop on one key would have both machines resolve to one channel, so
// one of them gets the wrong OS. Keeping the two axes apart means the
// channel keeps meaning "which release train" and one `stable` can exist
// per platform.
//
// The cost of that, and it is a real one: `channels` is no longer unique
// across the collection. Two documents can both hold "stable", so anything
// reading that collection has to think in (channel, platform) pairs — see
// promote() and the admin listing below.
const PLATFORMS = ["linux", "windows"];
const DEFAULT_PLATFORM = "linux";

/**
 * The platform a build document is for.
 *
 * Documents published before platforms existed have no such field, and
 * they are all Linux — every build in the collection when this was written
 * carried `arch: "linux-x86_64"`. Answering "linux" for them means no
 * migration and no backfill: the builds that are live keep resolving for
 * the pods that are running.
 *
 * `arch` is not parsed for this. It is assembled client-side as
 * `platform.system().lower() + "-" + platform.machine()`, and machine() is
 * NOT lowercased — a Windows build registers "windows-AMD64". A field that
 * is only sometimes lowercase is a bad thing to branch on, so the platform
 * is sent explicitly and this fallback covers only the pre-platform past.
 */
const buildPlatform = (build) => build?.platform || DEFAULT_PLATFORM;

/**
 * Match builds for one platform, including the pre-platform documents.
 *
 * `{platform: "linux"}` does not match a document with no platform field
 * at all, which is exactly the set of builds already published — so asking
 * for Linux has to mean "linux, or old enough not to say".
 */
const platformFilter = (platform) =>
  platform === DEFAULT_PLATFORM
    ? { $or: [{ platform: DEFAULT_PLATFORM }, { platform: { $exists: false } }] }
    : { platform };

// The artifact's name inside its content-addressed prefix. Constrained
// because it goes straight into an R2 key that this service then signs: a
// value with a slash or a .. in it is a signed URL for an object the
// caller chose. A basename, and a conservative one.
const FILENAME_RE = /^[A-Za-z0-9._-]{1,64}$/;

// ── Build download ──────────────────────────────────────────────────────
//
// Hands a pod a time-limited URL for the app binary, which lives in a
// PRIVATE R2 bucket. This replaced fetching it from a public Hugging Face
// repo, and it is worth being exact about what that did and did not buy:
//
//   it does      stop a lapsed or revoked key pulling a NEW build, let a
//                build be pinned or rolled back per licence from the
//                server, and record which machines pull on which key
//   it does NOT  stop the binary being copied once someone has it
//
// The second line is unchanged from the Hugging Face days. What limits who
// can *run* the app is the seat check above, not where the bytes came
// from — see the note at the top of licensing.py. Nothing here is
// load-bearing for that.
//
// This deliberately does not take a seat. A pod that is downloading has
// not started yet, and charging it one would make a slow download look
// like a seat leak on a single-seat licence.
//
// `current_sha` is what the pod already has on its volume. Answering
// up_to_date with no URL is what keeps a restart free: no signature is
// minted, no row is written, and pods that are merely rebooting never
// touch the rate limit.
//
// `platform` is what keeps a Windows .exe away from a Linux pod. It is
// optional and defaults to "linux", which is not a preference — it is the
// compatibility guarantee. scripts/runpod_start.sh sends
// {license_key, instance_id, current_sha} and nothing else, and every pod
// running today is that script. A default of anything but "linux", or a
// required field, would break all of them at once.
app.post(
  "/v1/build",
  wrap(async (req, res) => {
    const { license_key, instance_id, current_sha } = req.body || {};
    if (!license_key) return badRequest(res, "license_key is required.");

    const platform = req.body?.platform ?? DEFAULT_PLATFORM;
    if (!PLATFORMS.includes(platform)) {
      return badRequest(
        res,
        `platform must be one of: ${PLATFORMS.join(", ")}.`,
      );
    }

    const { licenses, builds, downloads } = await collections();
    const license = await licenses.findOne({ key: license_key });
    const problem = licenseProblem(license);
    if (problem) return res.status(403).json({ ok: false, ...problem });

    // Most specific first. Both fields are absent on an ordinary licence,
    // so the default is "whatever stable points at" and nothing has to be
    // edited to get it. `build_sha` pins one customer to one build —
    // which is how you hold a customer back, or put a single pod on a
    // build you are still checking, without touching anyone else.
    //
    // The platform filter is on BOTH lookups, and the pin is the one that
    // is easy to miss: a licence pinned to a Linux sha would otherwise
    // hand that sha to a Windows client, which is the exact failure the
    // channel filter exists to prevent — reintroduced through the door
    // marked "hold this customer back one build".
    const channel = license.build_channel || "stable";
    const build = license.build_sha
      ? await builds.findOne({ _id: license.build_sha, ...platformFilter(platform) })
      : await builds.findOne({ channels: channel, ...platformFilter(platform) });

    if (!build) {
      // 503 rather than 404. From the pod's side this is indistinguishable
      // from the service being transiently wrong, and it is a problem at
      // the supplier's end either way. A 404 reads as "this pod asked for
      // something that makes no sense", which is never what happened.
      //
      // A pin that exists but is for the other platform is called out
      // separately. It is the one case here that looks like a server fault
      // and is actually a one-field licence edit: build_sha names a single
      // artifact, and an artifact is for one OS.
      if (license.build_sha) {
        const pinned = await builds.findOne({ _id: license.build_sha });
        if (pinned) {
          console.error(
            `build    key=${license_key} PIN IS ${buildPlatform(pinned)} ` +
              `but client asked for ${platform} — sha=${license.build_sha.slice(0, 12)}`,
          );
        }
      }
      console.error(
        `build    key=${license_key} NO BUILD pin=${license.build_sha || "-"} ` +
          `channel=${channel} platform=${platform}`,
      );
      return res.status(503).json({
        ok: false,
        error: "no_build",
        message:
          "No app build is published for this license yet. Nothing is " +
          "wrong with this pod — contact your supplier.",
      });
    }

    // `platform` is in the manifest so the client can refuse a build for
    // the wrong OS rather than download it, checksum it happily and fail
    // to execute it. scripts/windows_start.ps1 treats a missing platform
    // as a refusal too, which is what makes an un-upgraded server (one
    // that ignores the field it was sent and answers with the Linux
    // build) a clean error instead of a mysterious one.
    const manifest = {
      sha256: build._id,
      size: build.size ?? null,
      version: build.version || null,
      built_at: build.built_at || null,
      platform: buildPlatform(build),
    };

    if (current_sha && current_sha === build._id) {
      return res.json({ ok: true, up_to_date: true, build: manifest });
    }

    if (!r2Configured()) {
      console.error("build    R2 is not configured — cannot sign a URL");
      return res.status(503).json({
        ok: false,
        error: "server_error",
        message:
          "The download service is not available. Nothing is wrong with " +
          "this pod — contact your supplier.",
      });
    }

    // The cap is on *URLs issued*, not bytes: a pod that legitimately
    // re-downloads sends current_sha and never reaches here, so anything
    // that does reach here is a machine without the build. Twenty of those
    // an hour on one key is already well past normal.
    if (BUILD_DOWNLOADS_PER_HOUR > 0) {
      const since = new Date(Date.now() - 3600 * 1000);
      const recent = await downloads.countDocuments({
        license_key,
        created_at: { $gt: since },
      });
      if (recent >= BUILD_DOWNLOADS_PER_HOUR) {
        console.warn(
          `build    key=${license_key} RATE LIMITED ${recent}/h ` +
            `instance=${instance_id || "-"}`,
        );
        return res.status(429).json({
          ok: false,
          error: "rate_limited",
          message:
            "This license has requested the app download too many times " +
            "in the last hour. Wait an hour and start the pod again, or " +
            "contact your supplier if this is unexpected.",
        });
      }
    }

    const url = presignGet(
      buildKey(build._id, build.filename),
      BUILD_URL_TTL_SECONDS,
    );
    await downloads.insertOne({
      license_key,
      instance_id: typeof instance_id === "string" ? instance_id.slice(0, 200) : null,
      sha256: build._id,
      platform: buildPlatform(build),
      ip: req.ip || null,
      created_at: new Date(),
    });
    console.log(
      `build    key=${license_key} instance=${instance_id || "-"} ` +
        `sha=${build._id.slice(0, 12)} platform=${buildPlatform(build)} ` +
        `channel=${license.build_sha ? "pinned" : channel}`,
    );

    res.json({
      ok: true,
      up_to_date: false,
      build: { ...manifest, url, url_expires_in: BUILD_URL_TTL_SECONDS },
    });
  }),
);

// The RunPod template's bootstrapper fetches this, and it is the one
// endpoint that must answer before a pod has anything at all — no licence
// key is sent and none is required. That is deliberate: the template is
// public, so a credential in the start command would be a credential given
// to everyone who clones it. The script it returns carries no secret
// either. Everything that actually needs authorising happens in the script,
// which sends the key it finds on the pod to /v1/build.
//
// A redirect rather than a proxy: the object lives in the private bucket
// with everything else, and handing back a short-lived signed URL means
// the bytes never pass through this function. curl -L in the template
// follows it. The window is small because the fetch happens immediately
// and a start script URL has no reason to outlive the boot that asked
// for it.
app.get(
  "/v1/start.sh",
  wrap(async (_req, res) => {
    if (!r2Configured()) {
      return res
        .status(503)
        .type("text/plain")
        .send("# The download service is not configured. Contact support.\n");
    }
    res.redirect(302, presignGet("start.sh", 300));
  }),
);

// The Windows half of the same idea, and for the same reason: a customer
// keeps a two-line shortcut that fetches this every time, so a fix to the
// start script reaches them without anyone being talked through editing a
// file. There is no template to paste into here — the shortcut is on their
// desktop — which makes it *more* important that the script itself is not
// the thing they hold a copy of.
//
// A separate object rather than content negotiation on /v1/start.sh: the
// two scripts are fetched by different tools (curl in a RunPod template,
// PowerShell on a desktop), and a redirect that depends on a User-Agent is
// a thing that breaks silently when either side changes.
//
// This will 404 from R2 until a Windows publish has uploaded start.ps1.
// That is the same behaviour /v1/start.sh has always had before a first
// publish, and the presign cannot tell the difference — the object's
// existence is not checked here, R2 answers it.
app.get(
  "/v1/start.ps1",
  wrap(async (_req, res) => {
    if (!r2Configured()) {
      return res
        .status(503)
        .type("text/plain")
        .send("# The download service is not configured. Contact support.\n");
    }
    res.redirect(302, presignGet("start.ps1", 300));
  }),
);

// ── Catalogue ───────────────────────────────────────────────────────────
//
// Public, unauthenticated, and safe to be: it is the pricing page's data
// and nothing here is a secret. Only `is_public` plans are listed, so
// `admin` and any tier being trialled stay out of it.
//
// The feature metadata comes from the features collection, the same place
// the plans come from — so renaming a tab or rewording what it does is an
// edit in Atlas, not a redeploy. seed-catalog puts the shipped catalogue
// there; after that the collection wins, and src/features.js is seed data
// and the un-seeded fallback (see allFeatures).
//
// The cost of that is real and worth naming: a feature row added here by
// hand will be described on the pricing page whether or not any deployed
// build has the tab. Nothing breaks — a plan granting a key the app does
// not know is already ignored with a warning on the client — but the page
// is only as accurate as the collection, which is the trade taken when the
// database became the source of truth.
//
// Each plan's feature list is sorted **here**, by the catalogue's
// sort_order, and not left in whatever order the plan document happens to
// list its keys in. Two reasons it belongs on this side:
//
//   * The client renders the list in the order it arrives (see
//     theme._plan_card, "the plan's order is kept as the server sorted
//     it"), so this is the only place that decides it.
//   * It makes `sort_order` on a feature row mean one thing everywhere —
//     move a feature in Atlas and every plan card follows on the next
//     read, with no plan document edited and nothing redeployed. The
//     alternative is reordering the `features` array on all five plans by
//     hand and keeping them consistent, which is five chances to get it
//     wrong for one decision.
//
// A key the catalogue does not describe sorts to the end rather than
// being dropped — the deploy-skew case the rest of this file tolerates. A
// key the catalogue describes and marks `enabled: false` *is* dropped,
// here and only here: that flag is about what this page advertises, and
// nothing about it reaches an entitlement (see features.js).
//
// ── Billing cycles ─────────────────────────────────────────────────────
//
// `cycles` carries only the ones that are switched on, monthly always
// among them, and each plan carries a `prices` map keyed by those same
// cycle ids. The page renders a tab per cycle it is sent and nothing when
// it is sent one — so turning quarterly or yearly on is a boolean in
// Atlas, with no rebuild on either side. Prices are computed rather than
// stored; the plan document holds `price_monthly` and nothing else.
//
// `price_yearly` is still sent when a yearly cycle is enabled, because
// builds that shipped before `prices` existed read that field and nothing
// else. It is a compatibility alias for `prices.yearly.total`, not a
// second source of truth, and it is null whenever yearly is off — which is
// exactly what an older build should see while the cycle is unlaunched.
app.get(
  "/v1/plans",
  wrap(async (_req, res) => {
    const [plans, catalogue, cycles] = await Promise.all([
      allPlans(),
      allFeatures(),
      billingCycles(),
    ]);
    // The whole catalogue is kept alongside the filtered one: a key missing
    // from `features` is either disabled (drop it from the plan) or unknown
    // to this catalogue entirely (keep it — deploy skew), and only the full
    // map can tell those apart.
    const features = new Map(
      [...catalogue].filter(([, row]) => isFeatureEnabled(row)),
    );
    // allFeatures() returns the catalogue already ordered by sort_order,
    // so its key order *is* the display order sortByRegistry ranks against.
    const order = [...features.keys()];
    const live = cycles.filter((cycle) => cycle.enabled);

    res.json({
      ok: true,
      cycles: live,
      plans: [...plans.values()]
        .filter((plan) => plan.is_public !== false)
        .map((plan) => {
          const prices = {};
          for (const cycle of live) {
            const price = cyclePrice(plan, cycle);
            if (price) prices[cycle.id] = price;
          }
          return {
            id: plan._id,
            name: plan.name,
            description: plan.description || null,
            price_monthly: plan.price_monthly ?? null,
            price_yearly: prices.yearly?.total ?? null,
            currency: plan.currency || "INR",
            prices,
            // A disabled feature is filtered out of the plan's list even
            // though the plan still grants it — the tier keeps working for
            // everyone on it, the page just stops selling the tab.
            features: sortByRegistry(
              (plan.features || []).filter((key) =>
                isFeatureEnabled(catalogue.get(key)),
              ),
              order,
            ),
            is_popular: plan.is_popular === true,
            sort_order: plan.sort_order ?? 0,
          };
        }),
      features: [...features.values()].map(featureWire),
      contact_url: CONTACT_URL || null,
    });
  }),
);

// ── Prompt library ──────────────────────────────────────────────────────
//
// Two halves that never meet: pods write here on every generation whose
// recipe they have not sent before, and the library tab reads back only
// what an admin has approved. A submission is private until then — that
// gap is the whole moderation story, and it is enforced by the `is_public`
// filter on the read, not by anything the writer sends.
//
// The customer is not told any of this happens, which sets the error
// contract for the write: it answers 200 for everything the pod could not
// have known was wrong (a duplicate, a cap it has hit), because the pod has
// nothing useful to do with a failure it cannot show anyone. Only a bad
// licence or a malformed body gets a real error code, and even those the
// client only logs.

// Which tabs a prompt can be replayed into. A hard list rather than the
// feature catalogue: replaying means writing values back into a specific
// set of form controls, so a tab is only valid here once the app has code
// that knows how to do that for it. A new tab joins this list in the same
// commit that teaches the client to load it.
const PROMPT_TABS = ["krea_t2i", "krea_v2_t2i"];

// Bounds on one submitted document. The body limit above already caps the
// request at 16kb; these cap what is *stored*, so one pod with a runaway
// prompt box cannot bloat a collection everyone else reads from.
const MAX_PROMPT_CHARS = 4000;
const MAX_SETTINGS_BYTES = 8192;

// How many un-reviewed prompts one licence may have waiting. Past this the
// write is dropped silently: the point is to keep the review queue workable
// when a pod misbehaves, and a customer who is not told their prompts are
// saved cannot be told they have been throttled either.
const MAX_PENDING_PER_LICENSE = 200;

/** A trimmed string of at most `max` chars, or "" for anything else. */
function text(value, max) {
  return typeof value === "string" ? value.trim().slice(0, max) : "";
}

/** One prompt as clients read it. `license_key` can never reach this. */
function promptWire(row) {
  return {
    id: String(row._id),
    tab: row.tab,
    source: row.source || "community",
    title: row.title || null,
    prompt: row.prompt || "",
    negative: row.negative || "",
    settings: row.settings || {},
    created_at: row.created_at || null,
  };
}

// ── Submit ──────────────────────────────────────────────────────────────
//
// The pod has already decided this recipe is new to it — see prompts.py,
// which fingerprints everything except the seed, the 🎲 toggle and the
// batch count, so re-rolling the same prompt is not a submission. This end
// does the same job for the cases that memory cannot cover: a restarted
// pod, a second pod on one licence, two customers who typed the same
// thing. The unique index on `fingerprint` makes the upsert below collapse
// all of them into one document with a `seen_count`.
app.post(
  "/v1/prompts",
  wrap(async (req, res) => {
    const { license_key, instance_id, tab, fingerprint } = req.body || {};
    if (!license_key || !instance_id) {
      return badRequest(res, "license_key and instance_id are required.");
    }
    if (!PROMPT_TABS.includes(tab)) {
      return badRequest(res, `tab must be one of: ${PROMPT_TABS.join(", ")}.`);
    }
    // 64 hex chars — a sha256 digest. Checked because it is a unique index
    // key chosen by the client: anything else stored here would be a row
    // that can never be deduplicated against.
    if (typeof fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(fingerprint)) {
      return badRequest(res, "fingerprint must be a 64-character sha256 hex.");
    }

    const prompt = text(req.body.prompt, MAX_PROMPT_CHARS);
    if (!prompt) return badRequest(res, "prompt is required.");
    const negative = text(req.body.negative, MAX_PROMPT_CHARS);

    const settings = req.body.settings;
    if (settings === null || typeof settings !== "object" ||
        Array.isArray(settings)) {
      return badRequest(res, "settings must be an object.");
    }
    if (JSON.stringify(settings).length > MAX_SETTINGS_BYTES) {
      return badRequest(res, "settings is too large.");
    }

    const { licenses, prompts } = await collections();
    const license = await licenses.findOne({ key: license_key });
    const problem = licenseProblem(license);
    if (problem) return res.status(403).json({ ok: false, ...problem });

    const now = new Date();

    // An admin publishing from the app's own checkbox. Checked against the
    // licence document, never against what the body claims — `publish` is
    // a request, and `is_admin` on the record is the only thing that grants
    // it. A non-admin sending publish:true is not an error, it is simply
    // ignored and stored the ordinary way.
    if (req.body.publish === true && license.is_admin === true) {
      const doc = {
        // Generated here, not the fingerprint the pod sent. An official
        // prompt must always be created: deduplicating it against an
        // existing community row would silently turn "publish this" into
        // a no-op that bumps a counter.
        fingerprint: randomUUID().replace(/-/g, "").padEnd(64, "0"),
        tab,
        source: "admin",
        is_public: true,
        reviewed_at: now,
        title: text(req.body.title, 120) || null,
        prompt,
        negative,
        settings,
        license_key: null,          // an official prompt has no submitter
        seen_count: 0,
        created_at: now,
        updated_at: now,
      };
      await prompts.insertOne(doc);
      console.log(`prompt   published ${doc._id} tab=${tab} by=${license_key}`);
      return res.json({ ok: true, stored: true, published: true });
    }

    // Counted before the write, and only against rows nobody has looked at
    // yet — an approved or rejected prompt has left the queue and should
    // not hold a slot against the licence that submitted it.
    const pending = await prompts.countDocuments({
      license_key,
      reviewed_at: null,
    });
    if (pending >= MAX_PENDING_PER_LICENSE) {
      return res.json({ ok: true, stored: false });
    }

    const result = await prompts.updateOne(
      { fingerprint },
      {
        // Everything about the recipe is $setOnInsert: a second pod sending
        // the same fingerprint has by definition the same prompt, and
        // letting it rewrite the fields would let a later submission edit a
        // document an admin has already read and approved.
        $setOnInsert: {
          fingerprint,
          tab,
          source: "community",
          is_public: false,
          reviewed_at: null,
          title: null,
          prompt,
          negative,
          settings,
          license_key,
          created_at: now,
        },
        $inc: { seen_count: 1 },
        $set: { updated_at: now },
      },
      { upsert: true },
    );

    res.json({ ok: true, stored: result.upsertedCount > 0 });
  }),
);

// ── Library ─────────────────────────────────────────────────────────────
//
// Public and unauthenticated like /v1/plans, and for a stronger reason: the
// only rows it can reach are ones an admin has deliberately made public,
// and `license_key` is projected away so who wrote one never leaves this
// service. Filtering, searching and paging all happen here rather than in
// the client, so a pod holds one page at a time however large the library
// grows.
app.get(
  "/v1/prompts",
  wrap(async (req, res) => {
    const filter = { is_public: true };
    if (PROMPT_TABS.includes(req.query.tab)) filter.tab = req.query.tab;
    if (req.query.source === "admin" || req.query.source === "community") {
      filter.source = req.query.source;
    }

    const search = text(req.query.q, 200);
    if (search) {
      // Escaped before it becomes a RegExp. An unescaped query string
      // compiled into a pattern is a denial of service someone else pays
      // for — a handful of nested quantifiers is all it takes to hang a
      // serverless invocation until it times out.
      const safe = search.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const pattern = new RegExp(safe, "i");
      filter.$or = [{ prompt: pattern }, { title: pattern }];
    }

    const skip = Math.max(0, Number.parseInt(req.query.skip, 10) || 0);
    const limit = Math.min(
      48,
      Math.max(1, Number.parseInt(req.query.limit, 10) || 12),
    );

    const { prompts } = await collections();
    const [rows, total] = await Promise.all([
      prompts
        .find(filter, { projection: { license_key: 0 } })
        // Admin prompts first — "admin" sorts before "community" — then
        // newest first within each group. Curated content leads the page
        // without needing a rank field nobody would maintain.
        .sort({ source: 1, created_at: -1 })
        .skip(skip)
        .limit(limit)
        .toArray(),
      prompts.countDocuments(filter),
    ]);

    res.json({
      ok: true,
      prompts: rows.map(promptWire),
      total,
      skip,
      limit,
    });
  }),
);

// ── Settings presets ────────────────────────────────────────────────────
//
// A preset is a named settings blob for one tab: the same shape the prompt
// library stores, minus the prompt text. Pods read them into a dropdown and
// applying one writes every control below it. Two halves, like the prompt
// library, but the moderation story is the opposite way round — there is no
// queue, because only an admin licence can write one at all:
//
//   write   POST /v1/presets from the app's own tickbox. Refused outright
//           unless the licence document says `is_admin`.
//   read    GET /v1/presets, public, `enabled: true` only.
//
// `enabled` is the whole reason a preset that turns out to be wrong is a
// one-field edit rather than a delete: it stops being offered, and the pods
// already holding it in a dropdown lose it on their next read.

// The same list the prompt library uses, and necessarily so: applying a
// preset means writing values into a specific set of form controls, which
// is exactly what replaying a prompt does. A tab joins both lists in the
// commit that teaches the client to load it.
const PRESET_TABS = PROMPT_TABS;

const MAX_PRESET_NAME_CHARS = 60;
const MAX_PRESET_DESCRIPTION_CHARS = 200;

// How many presets may share a name on one tab before the write is refused.
// The suffix walk below is O(attempts) round trips, so this is both a naming
// cap and the loop's bound.
const MAX_NAME_ATTEMPTS = 20;

/**
 * Insert a preset under `doc.name`, stepping past names already taken.
 *
 * Saving from the app **never overwrites** — see POST /v1/presets — so a
 * name that exists becomes "Portrait (2)", then "Portrait (3)". Returns the
 * name it actually used, or null when even the last attempt collided.
 *
 * The duplicate-key error is the mechanism rather than a pre-flight
 * findOne, and that is deliberate: the unique index is the only thing that
 * can answer "is this name free" without a race, so two admins saving the
 * same name in the same second get two presets rather than one of them
 * silently landing on the other's row.
 */
async function insertUnique(presets, doc) {
  for (let attempt = 1; attempt <= MAX_NAME_ATTEMPTS; attempt += 1) {
    const suffix = ` (${attempt})`;
    // The base is trimmed to make room rather than the suffix being
    // dropped, so a 60-character name still ends up unique instead of
    // colliding forever at the length limit.
    const name =
      attempt === 1
        ? doc.name
        : doc.name.slice(0, MAX_PRESET_NAME_CHARS - suffix.length) + suffix;
    try {
      await presets.insertOne({ ...doc, name });
      return name;
    } catch (err) {
      if (err?.code !== 11000) throw err;      // not a name clash — real error
    }
  }
  return null;
}

/** One preset as clients read it. */
function presetWire(row) {
  return {
    id: String(row._id),
    tab: row.tab,
    name: row.name,
    description: row.description || null,
    settings: row.settings || {},
    is_default: row.is_default === true,
    sort_order: row.sort_order ?? 0,
  };
}

/**
 * Make one preset the default for its tab, taking the flag off the others.
 *
 * The same shape as promote() for builds, and for the same reason: "which
 * one is the default" is a property of the tab, not of the document, so it
 * is only ever true in one place and setting it is one operation.
 */
async function setDefault(presets, tab, id) {
  await presets.updateMany(
    { tab, _id: { $ne: id } },
    { $set: { is_default: false } },
  );
  await presets.updateOne({ _id: id }, { $set: { is_default: true } });
}

/** The shared body checks for a preset write. Returns {error} or {value}. */
function presetBody(body) {
  if (!PRESET_TABS.includes(body.tab)) {
    return { error: `tab must be one of: ${PRESET_TABS.join(", ")}.` };
  }
  const name = text(body.name, MAX_PRESET_NAME_CHARS);
  if (!name) return { error: "name is required." };
  const settings = body.settings;
  if (settings === null || typeof settings !== "object" ||
      Array.isArray(settings)) {
    return { error: "settings must be an object." };
  }
  if (JSON.stringify(settings).length > MAX_SETTINGS_BYTES) {
    return { error: "settings is too large." };
  }
  return {
    value: {
      tab: body.tab,
      name,
      settings,
      description: text(body.description, MAX_PRESET_DESCRIPTION_CHARS) || null,
    },
  };
}

// Written from the app, by an admin, with the tickbox next to Generate.
//
// **Always an insert.** Ticking the box means "keep these settings", and
// the settings on screen are a new starting point — usually a preset that
// was loaded and then changed. Treating a repeated name as an edit would
// make the ordinary gesture (load Default, adjust, save) destroy the row it
// started from, with no undo and nothing on screen having said so. So a
// name that is taken becomes "Portrait (2)" and the response says which
// name it got. Editing a preset in place is the admin route's job, where
// naming an existing preset is the whole point of the call.
//
// Unlike POST /v1/prompts this answers a real error when it refuses. That
// endpoint is silent because the customer was never told it exists; this
// one is a button someone deliberately pressed, and "did my preset save?"
// deserves an answer.
app.post(
  "/v1/presets",
  wrap(async (req, res) => {
    const { license_key, instance_id } = req.body || {};
    if (!license_key || !instance_id) {
      return badRequest(res, "license_key and instance_id are required.");
    }
    const parsed = presetBody(req.body || {});
    if (parsed.error) return badRequest(res, parsed.error);

    const { licenses, presets } = await collections();
    const license = await licenses.findOne({ key: license_key });
    const problem = licenseProblem(license);
    if (problem) return res.status(403).json({ ok: false, ...problem });

    // Checked against the licence document, never against what the body
    // claims — same rule as publishing a prompt. A customer build hides
    // the tickbox entirely, so anything arriving here without the role is
    // not a mistake worth being gentle about.
    if (license.is_admin !== true) {
      return res.status(403).json({
        ok: false,
        error: "forbidden",
        message: "Only an admin license can save presets.",
      });
    }

    const now = new Date();
    const name = await insertUnique(presets, {
      ...parsed.value,
      enabled: true,
      is_default: false,
      sort_order: 0,
      created_by: license_key,
      created_at: now,
      updated_at: now,
    });
    if (name === null) {
      return badRequest(
        res,
        `There are already ${MAX_NAME_ATTEMPTS} presets called ` +
          `"${parsed.value.name}" on this tab. Use a different name.`,
      );
    }

    console.log(
      `preset   created tab=${parsed.value.tab} name="${name}" ` +
        `by=${license_key}`,
    );
    // `name` is the name it actually got, which is not always the one that
    // was asked for — the client shows this rather than what was typed.
    res.json({ ok: true, stored: true, created: true, name });
  }),
);

// Public and unauthenticated like /v1/plans: a preset holds dropdown labels
// and slider values and nothing else, and the only rows reachable here are
// ones an admin switched on. `tab` is optional — the app asks for all of
// them in one request at startup and splits them itself, which is one round
// trip on the path between a pod booting and its tabs being usable.
app.get(
  "/v1/presets",
  wrap(async (req, res) => {
    const filter = { enabled: true };
    if (PRESET_TABS.includes(req.query.tab)) filter.tab = req.query.tab;

    const { presets } = await collections();
    const rows = await presets
      .find(filter)
      .sort({ tab: 1, sort_order: 1, name: 1 })
      .limit(200)
      .toArray();
    res.json({ ok: true, presets: rows.map(presetWire) });
  }),
);

// The admin listing — every preset, including the ones switched off, which
// is the whole difference from the public read.
app.get(
  "/v1/admin/presets",
  requireAdmin,
  wrap(async (req, res) => {
    const { presets } = await collections();
    const filter = {};
    if (PRESET_TABS.includes(req.query.tab)) filter.tab = req.query.tab;
    const rows = await presets
      .find(filter)
      .sort({ tab: 1, sort_order: 1, name: 1 })
      .toArray();
    res.json({
      ok: true,
      presets: rows.map((row) => ({
        ...presetWire(row),
        enabled: row.enabled === true,
        created_by: row.created_by || null,
        updated_at: row.updated_at || null,
      })),
    });
  }),
);

// Author or edit a preset from outside the app. Same upsert as the pod
// route, plus the three fields the pod has no business setting: whether it
// is offered at all, whether it is the one a fresh session starts on, and
// where it sits in the dropdown.
app.post(
  "/v1/admin/presets",
  requireAdmin,
  wrap(async (req, res) => {
    const parsed = presetBody(req.body || {});
    if (parsed.error) return badRequest(res, parsed.error);

    const { presets } = await collections();
    const now = new Date();
    const update = {
      $set: { ...parsed.value, updated_at: now },
      $setOnInsert: {
        enabled: true,
        is_default: false,
        sort_order: 0,
        created_by: null,
        created_at: now,
      },
    };
    // Only the fields actually sent are moved from $setOnInsert to $set, so
    // editing a preset's settings without mentioning `enabled` leaves it
    // exactly as switched on or off as it was.
    for (const field of ["enabled", "sort_order"]) {
      if (req.body[field] === undefined) continue;
      delete update.$setOnInsert[field];
      update.$set[field] =
        field === "enabled" ? req.body[field] === true : Number(req.body[field]) || 0;
    }

    await presets.updateOne(
      { tab: parsed.value.tab, name: parsed.value.name },
      update,
      { upsert: true },
    );
    const row = await presets.findOne({
      tab: parsed.value.tab,
      name: parsed.value.name,
    });
    // Last, and against the stored row: making this one the default is a
    // change to every other preset on the tab, so it cannot ride along in
    // the upsert above.
    if (req.body.is_default === true) {
      await setDefault(presets, row.tab, row._id);
      row.is_default = true;
    }
    console.log(`preset   admin ${row._id} tab=${row.tab} name="${row.name}"`);
    res.json({ ok: true, preset: presetWire(row) });
  }),
);

// Switch one preset on or off, or make it the tab's default. Separate from
// the upsert above because it names a preset by id rather than by content:
// this is the route you reach for when a preset is already wrong and you do
// not want to restate it to change one flag.
app.post(
  "/v1/admin/presets/state",
  requireAdmin,
  wrap(async (req, res) => {
    const { id } = req.body || {};
    if (!id || !ObjectId.isValid(id)) {
      return badRequest(res, "id must be a preset's _id.");
    }
    if (req.body.enabled === undefined && req.body.is_default === undefined) {
      return badRequest(res, "send enabled and/or is_default.");
    }
    const { presets } = await collections();
    const _id = new ObjectId(String(id));
    const row = await presets.findOne({ _id });
    if (!row) return badRequest(res, "no preset with that id.");

    if (req.body.enabled !== undefined) {
      await presets.updateOne(
        { _id },
        { $set: { enabled: req.body.enabled === true, updated_at: new Date() } },
      );
    }
    // Deliberately allowed on a disabled preset, and deliberately not
    // enabling it: the two flags answer different questions, and quietly
    // switching one on because the other was set is the kind of help that
    // makes a state impossible to reason about.
    if (req.body.is_default === true) await setDefault(presets, row.tab, _id);
    else if (req.body.is_default === false) {
      await presets.updateOne({ _id }, { $set: { is_default: false } });
    }

    const after = await presets.findOne({ _id });
    console.log(
      `preset   state ${id} enabled=${after.enabled === true} ` +
        `default=${after.is_default === true}`,
    );
    res.json({
      ok: true,
      preset: { ...presetWire(after), enabled: after.enabled === true },
    });
  }),
);

/** One catalogue row as the clients read it — `key`, never Mongo's `_id`. */
function featureWire(row) {
  return {
    key: row.key || row._id,
    name: row.name || null,
    tab_label: row.tab_label || null,
    description: row.description || "",
    category: row.category || "",
    sort_order: row.sort_order ?? 0,
    // Always true on /v1/plans, which never sends a disabled row. It is on
    // the wire for the admin listing, which sends every row and needs to
    // say which of them the pricing page is currently hiding.
    enabled: isFeatureEnabled(row),
  };
}

// ── Ops ─────────────────────────────────────────────────────────────────

// Unlike /v1/*, this one reports *why* it is unhealthy. That is the whole
// job of the endpoint: without it a broken deployment is just an opaque
// 503 at the client and the only clue is in the platform logs. The Atlas
// error text ("bad auth", "connection timed out") is what distinguishes a
// wrong password from a blocked IP.
app.get("/health", async (_req, res) => {
  try {
    const { licenses, builds } = await collections();
    const licenseCount = await licenses.estimatedDocumentCount();
    // Both halves of "can a pod actually start right now": credentials to
    // sign a URL, and something for that URL to point at. Either being
    // absent leaves every /v1/build answering 503, and this is the only
    // place that says which one it is.
    //
    // Once "stable" can sit on one build per platform, a single answer
    // here is not merely incomplete — it is arbitrary, because findOne
    // returns whichever of the two the server reaches first. So each
    // platform is looked up on its own, and `stable_build` keeps naming
    // the Linux one: it is what every pod in production downloads, and it
    // is the field anything reading this endpoint already means.
    const stable = Object.fromEntries(
      await Promise.all(
        PLATFORMS.map(async (name) => [
          name,
          (await builds.findOne({
            channels: "stable",
            ...platformFilter(name),
          }))?._id ?? null,
        ]),
      ),
    );
    res.json({
      ok: true,
      db: "connected",
      db_name: DB_NAME,
      licenses: licenseCount,
      stale_seconds: STALE_SECONDS,
      r2: r2Configured() ? "configured" : "unset",
      stable_build: stable[DEFAULT_PLATFORM],
      stable_builds: stable,
    });
  } catch (err) {
    console.error("health check failed:", err);
    res.status(503).json({
      ok: false,
      error: "server_error",
      db: "unreachable",
      db_name: DB_NAME,
      reason: err.message,
      hint:
        "Check MONGODB_URI is set in the deployment's environment " +
        "variables, and that Atlas Network Access allows 0.0.0.0/0 " +
        "(serverless function IPs are not fixed).",
    });
  }
});

function requireAdmin(req, res, next) {
  if (!ADMIN_TOKEN) {
    return res.status(404).json({ ok: false, error: "not_found" });
  }
  const header = req.get("authorization") || "";
  if (header !== `Bearer ${ADMIN_TOKEN}`) {
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }
  next();
}

// Every license with its live seat usage. This is the detection half of
// the scheme: a 2-seat key that has produced 40 distinct instance ids this
// week is a conversation you can have with evidence behind it.
app.get(
  "/v1/admin/licenses",
  requireAdmin,
  wrap(async (_req, res) => {
    const { licenses, sessions } = await collections();
    const cutoff = cutoffDate();
    const all = await licenses.find({}).sort({ created_at: -1 }).toArray();
    const rows = await Promise.all(
      all.map(async (license) => {
        // Resolved rather than raw: what you want to see here is what the
        // customer actually gets, which for a license on a plan is not
        // written on the license at all. Caught per row, because one
        // license pointing at a deleted plan should cost you that row's
        // features and not the whole listing.
        let entitlement = { features: null, plan_id: null, source: "error" };
        let planError = null;
        try {
          entitlement = await resolveEntitlement(license);
        } catch (err) {
          planError = err.message;
        }
        return {
          key: license.key,
          name: license.name || null,
          seats: license.seats,
          active: license.active !== false,
          is_admin: license.is_admin === true,
          expires_at: license.expires_at || null,
          plan_id: entitlement.plan_id ?? license.plan_id ?? null,
          build_sha: license.build_sha || null,
          build_channel: license.build_channel || null,
          features: entitlement.features,
          features_source: entitlement.source,
          plan_error: planError,
          seats_in_use: await sessions.countDocuments({
            license_key: license.key,
            last_seen: { $gt: cutoff },
          }),
          instances_seen: await sessions.countDocuments({
            license_key: license.key,
          }),
        };
      }),
    );
    res.json({ ok: true, licenses: rows });
  }),
);

// Every plan with the number of licenses on it. The count is the guard
// rail for editing: it tells you how many customers a change to this
// document is about to move, before you make it.
//
// Unlike /v1/plans this shows everything as it really is: cycles that are
// switched off, features that are disabled, and the price each cycle
// *would* charge if it were on — which is the number you want in front of
// you when deciding whether to switch it on.
app.get(
  "/v1/admin/plans",
  requireAdmin,
  wrap(async (_req, res) => {
    const { licenses } = await collections();
    const [plans, features, cycles] = await Promise.all([
      allPlans(),
      allFeatures(),
      billingCycles(),
    ]);
    const rows = await Promise.all(
      [...plans.values()].map(async (plan) => ({
        ...plan,
        prices: Object.fromEntries(
          cycles
            .map((cycle) => [cycle.id, cyclePrice(plan, cycle)])
            .filter(([, price]) => price !== null),
        ),
        licenses: await licenses.countDocuments({ plan_id: plan._id }),
      })),
    );
    res.json({
      ok: true,
      cycles,
      plans: rows,
      features: [...features.values()].map(featureWire),
    });
  }),
);

app.get(
  "/v1/admin/sessions",
  requireAdmin,
  wrap(async (req, res) => {
    const { sessions } = await collections();
    const filter = req.query.license_key
      ? { license_key: String(req.query.license_key) }
      : {};
    const rows = await sessions
      .find(filter)
      .sort({ last_seen: -1 })
      .limit(200)
      .toArray();
    const cutoff = cutoffDate();
    res.json({
      ok: true,
      sessions: rows.map((row) => ({
        ...row,
        _id: undefined,
        live: row.last_seen > cutoff,
      })),
    });
  }),
);

// The moderation surface, and the first admin routes here that write.
//
// `?pending=1` is the queue: everything nobody has ruled on yet, oldest
// first. Unlike the public listing this one carries `license_key`, which is
// the reason it is behind a token — a prompt that is abusive is only
// actionable if you can see which licence sent it.
app.get(
  "/v1/admin/prompts",
  requireAdmin,
  wrap(async (req, res) => {
    const { prompts } = await collections();
    const filter = {};
    if (req.query.pending === "1") filter.reviewed_at = null;
    if (PROMPT_TABS.includes(req.query.tab)) filter.tab = req.query.tab;
    const rows = await prompts
      .find(filter)
      .sort({ reviewed_at: 1, created_at: 1 })
      .limit(200)
      .toArray();
    res.json({
      ok: true,
      prompts: rows.map((row) => ({
        ...promptWire(row),
        license_key: row.license_key || null,
        is_public: row.is_public === true,
        reviewed_at: row.reviewed_at || null,
        seen_count: row.seen_count ?? 0,
      })),
      pending: await prompts.countDocuments({ reviewed_at: null }),
    });
  }),
);

// Approve or reject. Both set `reviewed_at`, and that is what takes a
// prompt out of the queue — a rejection is a decision, so it must not come
// back tomorrow looking like it was never read. Only approval sets
// `is_public`, which is the single field the public listing filters on.
app.post(
  "/v1/admin/prompts/review",
  requireAdmin,
  wrap(async (req, res) => {
    const { id, approve } = req.body || {};
    if (!id || !ObjectId.isValid(id)) {
      return badRequest(res, "id must be a prompt's _id.");
    }
    if (typeof approve !== "boolean") {
      return badRequest(res, "approve must be true or false.");
    }
    const { prompts } = await collections();
    const row = await prompts.findOneAndUpdate(
      { _id: new ObjectId(String(id)) },
      { $set: { is_public: approve, reviewed_at: new Date() } },
      { returnDocument: "after" },
    );
    if (!row) return badRequest(res, "no prompt with that id.");
    console.log(`review   ${id} ${approve ? "approved" : "rejected"}`);
    res.json({ ok: true, prompt: promptWire(row), is_public: row.is_public });
  }),
);

// Author an admin prompt — the curated half of the library. Public the
// moment it is written, because the thing approval protects against is
// content nobody chose, and this is content someone chose.
//
// The fingerprint is generated rather than derived from the content: it
// exists only to satisfy the unique index, and deriving it would let an
// admin prompt collide with a community submission of the same recipe and
// silently do nothing.
app.post(
  "/v1/admin/prompts",
  requireAdmin,
  wrap(async (req, res) => {
    const { tab } = req.body || {};
    if (!PROMPT_TABS.includes(tab)) {
      return badRequest(res, `tab must be one of: ${PROMPT_TABS.join(", ")}.`);
    }
    const prompt = text(req.body.prompt, MAX_PROMPT_CHARS);
    if (!prompt) return badRequest(res, "prompt is required.");
    const settings = req.body.settings;
    if (settings === null || typeof settings !== "object" ||
        Array.isArray(settings)) {
      return badRequest(res, "settings must be an object.");
    }
    if (JSON.stringify(settings).length > MAX_SETTINGS_BYTES) {
      return badRequest(res, "settings is too large.");
    }

    const now = new Date();
    const { prompts } = await collections();
    const doc = {
      fingerprint: randomUUID().replace(/-/g, "").padEnd(64, "0"),
      tab,
      source: "admin",
      is_public: true,
      reviewed_at: now,
      title: text(req.body.title, 120) || null,
      prompt,
      negative: text(req.body.negative, MAX_PROMPT_CHARS),
      settings,
      license_key: null,
      seen_count: 0,
      created_at: now,
      updated_at: now,
    };
    await prompts.insertOne(doc);
    console.log(`prompt   admin ${doc._id} tab=${tab}`);
    res.json({ ok: true, prompt: promptWire(doc) });
  }),
);

// ── Builds ──────────────────────────────────────────────────────────────
//
// build.sh calls the first of these once it has uploaded the artifact to
// R2. Registering is separate from uploading on purpose: the bytes are
// content-addressed and therefore harmless to have sitting in the bucket
// unreferenced, so an upload that succeeds and a register that fails
// leaves nothing broken and the retry costs no transfer.
//
// Registering is idempotent — the sha256 is the _id, so re-running
// --upload-only against an artifact that is already published updates the
// metadata and re-promotes rather than failing.
const CHANNEL_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

/**
 * Point `channel` at one build, taking it off whichever build holds it
 * FOR THE SAME PLATFORM.
 *
 * Promotion and rollback are the same operation: there is no "newer" here,
 * only which document the name currently sits on. That is what makes
 * going back a one-line admin call rather than a re-upload.
 *
 * The platform scope is what makes two platforms able to share a channel
 * name, and leaving it out is not a cosmetic bug: an unscoped $pull run
 * for a Windows build would take "stable" off the Linux build, and every
 * Linux pod would get no_build on its next start. The outage would happen
 * at publish time, before any pod asked for anything.
 *
 * The platform comes from the stored document rather than from the
 * caller, so the two can never disagree — a promote is always for the
 * platform the artifact actually is.
 */
async function promote(builds, sha256, channel) {
  const target = await builds.findOne({ _id: sha256 });
  const platform = buildPlatform(target);
  await builds.updateMany(
    { channels: channel, _id: { $ne: sha256 }, ...platformFilter(platform) },
    { $pull: { channels: channel } },
  );
  await builds.updateOne({ _id: sha256 }, { $addToSet: { channels: channel } });
  return platform;
}

app.post(
  "/v1/admin/builds",
  requireAdmin,
  wrap(async (req, res) => {
    const { sha256, size, version, git_commit, git_branch, arch, built_at } =
      req.body || {};
    if (typeof sha256 !== "string" || !/^[0-9a-f]{64}$/.test(sha256)) {
      return badRequest(res, "sha256 must be a 64-character hex digest.");
    }
    if (!Number.isInteger(size) || size <= 0) {
      return badRequest(res, "size must be the artifact's size in bytes.");
    }
    // null/absent means "publish but do not point anything at it yet",
    // which is how a build gets uploaded for checking before customers see
    // it. Anything else has to be a plausible channel name rather than
    // whatever was typed — a typo here silently strands every pod on the
    // channel it was meant to be.
    const channel = req.body.promote ?? null;
    if (channel !== null && !CHANNEL_RE.test(String(channel))) {
      return badRequest(res, "promote must be a short lowercase channel name.");
    }

    // Both default to the Linux values, so build.sh registers exactly what
    // it always did without sending either field. Validated rather than
    // trusted: `platform` decides which clients are handed this artifact,
    // and `filename` becomes part of a key this service signs.
    const platform = req.body.platform ?? DEFAULT_PLATFORM;
    if (!PLATFORMS.includes(platform)) {
      return badRequest(res, `platform must be one of: ${PLATFORMS.join(", ")}.`);
    }
    const filename = req.body.filename ?? DEFAULT_FILENAME;
    if (!FILENAME_RE.test(String(filename))) {
      return badRequest(
        res,
        "filename must be a plain basename (letters, digits, . _ -).",
      );
    }

    const { builds } = await collections();
    const now = new Date();
    await builds.updateOne(
      { _id: sha256 },
      {
        $set: {
          size,
          version: version || git_commit || null,
          git_commit: git_commit || null,
          git_branch: git_branch || null,
          arch: arch || null,
          platform,
          filename,
          built_at: built_at ? new Date(built_at) : now,
          updated_at: now,
        },
        $setOnInsert: { channels: [], published_at: now },
      },
      { upsert: true },
    );
    if (channel) await promote(builds, sha256, channel);

    console.log(
      `publish  sha=${sha256.slice(0, 12)} size=${size} platform=${platform} ` +
        `commit=${git_commit || "-"} promote=${channel || "-"}`,
    );
    const row = await builds.findOne({ _id: sha256 });
    res.json({ ok: true, build: { ...row, sha256: row._id, _id: undefined } });
  }),
);

// The rollback surface, and the reason every build stays in this
// collection rather than the current one overwriting the last.
//
// `platform` is filled in for the older rows rather than left absent, so
// the listing answers "which build does a Linux pod get" without the
// reader having to know that a missing field means Linux. Two rows can
// hold the same channel now — one per platform — and that is only legible
// if every row says which platform it is.
app.get(
  "/v1/admin/builds",
  requireAdmin,
  wrap(async (_req, res) => {
    const { builds } = await collections();
    const rows = await builds
      .find({})
      .sort({ published_at: -1 })
      .limit(100)
      .toArray();
    res.json({
      ok: true,
      builds: rows.map((row) => ({
        ...row,
        sha256: row._id,
        _id: undefined,
        channels: row.channels || [],
        platform: buildPlatform(row),
        filename: row.filename || DEFAULT_FILENAME,
      })),
    });
  }),
);

app.post(
  "/v1/admin/builds/promote",
  requireAdmin,
  wrap(async (req, res) => {
    const { sha256 } = req.body || {};
    const channel = req.body.channel || "stable";
    if (typeof sha256 !== "string" || !/^[0-9a-f]{64}$/.test(sha256)) {
      return badRequest(res, "sha256 must be a 64-character hex digest.");
    }
    if (!CHANNEL_RE.test(String(channel))) {
      return badRequest(res, "channel must be a short lowercase name.");
    }
    const { builds } = await collections();
    // Checked rather than upserted: promoting a sha that was never
    // registered would point every pod on the channel at an object that
    // may not be in the bucket, and they would all fail the same way at
    // once with nothing saying why.
    if (!(await builds.findOne({ _id: sha256 }))) {
      return badRequest(res, "no build with that sha256 has been published.");
    }
    // The platform is reported back rather than accepted as input: it is
    // the artifact's own, and a rollback is one place you want to be told
    // which set of machines you just moved. `make promote SHA=...` prints
    // this, so a Windows sha typed in by mistake says "windows" instead of
    // looking like it did what was meant.
    const platform = await promote(builds, sha256, channel);
    console.log(
      `promote  ${channel}/${platform} -> ${sha256.slice(0, 12)}`,
    );
    res.json({ ok: true, channel, platform, sha256 });
  }),
);

app.use((_req, res) => {
  res.status(404).json({ ok: false, error: "not_found" });
});

// Malformed JSON reaches here as a SyntaxError from express.json(). Answer
// in JSON like everything else rather than express's default HTML page.
app.use((err, _req, res, _next) => {
  if (err instanceof SyntaxError && "body" in err) {
    return res
      .status(400)
      .json({ ok: false, error: "bad_request", message: "Malformed JSON." });
  }
  console.error("unhandled:", err);
  res.status(503).json({ ok: false, error: "server_error" });
});

export default app;
