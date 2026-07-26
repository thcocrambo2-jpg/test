// Seat-limited license API.
//
// Three endpoints the app calls (/v1/acquire, /v1/heartbeat, /v1/release)
// plus health and admin read-outs.
//
// The status code carries the contract, and the Python client branches on
// exactly this — keep it stable:
//
//   200  ok                                        proceed
//   400  bad_request                               client bug, do not retry
//   403  invalid_key | revoked | expired |         stop the app now
//        seat_limit
//   503  server_error                              transient: retry at
//                                                  startup, or ride the
//                                                  grace window if running
//
// The 403/503 split is the important one. A database outage must never
// look like a license violation, or an Atlas blip reads to your customers
// as an accusation — and a real violation must never look transient, or
// the grace window makes it survivable.

import express from "express";
import cors from "cors";

import { collections } from "./db.js";
import {
  STALE_SECONDS,
  HEARTBEAT_SECONDS,
  ADMIN_TOKEN,
} from "./config.js";

const app = express();

app.set("trust proxy", true);
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

function seatPayload(license, inUse) {
  return {
    ok: true,
    license_name: license.name || null,
    seats: license.seats,
    seats_in_use: inUse,
    heartbeat_seconds: HEARTBEAT_SECONDS,
    stale_seconds: STALE_SECONDS,
  };
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
    res.json(seatPayload(license, inUse));
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
      return res.json({ ...seatPayload(license, inUse + 1), reacquired: true });
    }

    res.json(seatPayload(license, await countLive(sessions, license_key)));
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

// ── Ops ─────────────────────────────────────────────────────────────────

app.get(
  "/health",
  wrap(async (_req, res) => {
    const { licenses } = await collections();
    await licenses.estimatedDocumentCount();
    res.json({ ok: true, stale_seconds: STALE_SECONDS });
  }),
);

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
      all.map(async (license) => ({
        key: license.key,
        name: license.name || null,
        seats: license.seats,
        active: license.active !== false,
        expires_at: license.expires_at || null,
        seats_in_use: await sessions.countDocuments({
          license_key: license.key,
          last_seen: { $gt: cutoff },
        }),
        instances_seen: await sessions.countDocuments({
          license_key: license.key,
        }),
      })),
    );
    res.json({ ok: true, licenses: rows });
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
