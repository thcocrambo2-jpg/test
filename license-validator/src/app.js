// Seat-limited license API.
//
// Three endpoints the app calls for its seat (/v1/acquire, /v1/heartbeat,
// /v1/release), two public reads it renders pages from (/v1/plans,
// /v1/prompts), one public write it makes silently (POST /v1/prompts),
// plus health and the admin surface.
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
} from "./config.js";

const app = express();

app.set("trust proxy", true);
app.disable("x-powered-by");   // no need to advertise the framework/version
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
    const { licenses } = await collections();
    const licenseCount = await licenses.estimatedDocumentCount();
    res.json({
      ok: true,
      db: "connected",
      db_name: DB_NAME,
      licenses: licenseCount,
      stale_seconds: STALE_SECONDS,
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
