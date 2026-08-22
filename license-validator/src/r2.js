// Presigned Cloudflare R2 URLs.
//
// R2's only API for handing out a time-limited download link is its
// S3-compatible one, so what is implemented below is AWS SigV4. The name
// is the protocol's — nothing here talks to AWS, there is no AWS account,
// and the endpoint is the r2.cloudflarestorage.com host for one Cloudflare
// account. Region is the literal string "auto"; R2 has exactly one and it
// is never anything else.
//
// Hand-rolled against node:crypto rather than depending on
// @aws-sdk/client-s3 + @aws-sdk/s3-request-presigner. That pair is tens of
// megabytes to sign a single GET, on a serverless function whose cold
// start sits on the critical path of every pod boot. What follows is the
// whole of what it would do for this one case.
//
// The credential this reads must be READ-ONLY (Object Read for the builds
// bucket, nothing else). This service is public-facing; a write-capable
// key here would mean any path to leaking it is a path to replacing the
// binary customers download. Uploads use a separate write token that lives
// only on the build machine — see scripts/r2_presign.py.

import { createHash, createHmac } from "node:crypto";

import {
  R2_ACCOUNT_ID,
  R2_ACCESS_KEY_ID,
  R2_SECRET_ACCESS_KEY,
  R2_BUILDS_BUCKET,
} from "./config.js";

const ALGORITHM = "AWS4-HMAC-SHA256";
const REGION = "auto";
const SERVICE = "s3";

const sha256hex = (value) => createHash("sha256").update(value).digest("hex");
const hmac = (key, value) => createHmac("sha256", key).update(value).digest();

/** True when every R2 variable needed to sign is present. */
export function r2Configured() {
  return Boolean(
    R2_ACCOUNT_ID &&
      R2_ACCESS_KEY_ID &&
      R2_SECRET_ACCESS_KEY &&
      R2_BUILDS_BUCKET,
  );
}

export const r2Host = () => `${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`;

// RFC 3986, which is stricter than encodeURIComponent: that leaves
// ! ' ( ) * unescaped, and SigV4 requires them escaped. A signature
// computed over a differently-encoded string is simply wrong, and R2
// rejects it as SignatureDoesNotMatch with nothing to say about why.
function uriEncode(value) {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

/** Canonical URI form of an object key: every segment escaped, "/" kept. */
const encodePath = (key) => key.split("/").map(uriEncode).join("/");

/**
 * A presigned GET for one object, valid for `expiresIn` seconds.
 *
 * Expiry is checked when the request *starts*, not while it runs, so a
 * download already in flight is not cut off at the deadline — but a retry
 * after it is will fail. That is why the caller's window is generous
 * relative to how long the transfer itself takes.
 */
export function presignGet(key, expiresIn) {
  if (!r2Configured()) throw new Error("R2 is not configured");

  const host = r2Host();
  // YYYYMMDDTHHMMSSZ, the only format SigV4 accepts for X-Amz-Date.
  const amzDate = new Date().toISOString().replace(/[-:]|\.\d{3}/g, "");
  const dateStamp = amzDate.slice(0, 8);
  const scope = `${dateStamp}/${REGION}/${SERVICE}/aws4_request`;

  // Sorted by encoded name, which SigV4 requires. These happen to already
  // be in order; sorting anyway means adding a parameter later cannot
  // silently produce a signature that does not verify.
  const canonicalQuery = [
    ["X-Amz-Algorithm", ALGORITHM],
    ["X-Amz-Credential", `${R2_ACCESS_KEY_ID}/${scope}`],
    ["X-Amz-Date", amzDate],
    ["X-Amz-Expires", String(expiresIn)],
    ["X-Amz-SignedHeaders", "host"],
  ]
    .map(([name, value]) => [uriEncode(name), uriEncode(value)])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([name, value]) => `${name}=${value}`)
    .join("&");

  // Path style: R2 puts the bucket in the path, not in the hostname.
  const canonicalPath = `/${uriEncode(R2_BUILDS_BUCKET)}/${encodePath(key)}`;

  // The trailing "" on the headers line is the blank line SigV4 puts
  // between the canonical headers block and the signed-header list.
  const canonicalRequest = [
    "GET",
    canonicalPath,
    canonicalQuery,
    `host:${host}`,
    "",
    "host",
    "UNSIGNED-PAYLOAD",
  ].join("\n");

  const stringToSign = [
    ALGORITHM,
    amzDate,
    scope,
    sha256hex(canonicalRequest),
  ].join("\n");

  let signingKey = hmac(`AWS4${R2_SECRET_ACCESS_KEY}`, dateStamp);
  for (const part of [REGION, SERVICE, "aws4_request"]) {
    signingKey = hmac(signingKey, part);
  }
  const signature = createHmac("sha256", signingKey)
    .update(stringToSign)
    .digest("hex");

  return (
    `https://${host}${canonicalPath}?${canonicalQuery}` +
    `&X-Amz-Signature=${signature}`
  );
}

/**
 * What an artifact is called inside its prefix, when nothing says.
 *
 * Every build published before Windows existed is at this name, so it is
 * the default rather than a preference — a build document with no
 * `filename` is one of those, and its object is still there under it.
 */
export const DEFAULT_FILENAME = "krea2app";

/**
 * Where a build's bytes live.
 *
 * Content-addressed, so an object is immutable once written: publishing
 * never overwrites, rollback is a pointer change rather than a re-upload,
 * and a stale cache anywhere in the path cannot serve the wrong bytes
 * under the right name.
 *
 * The name inside the prefix is stored on the build document rather than
 * derived from its platform. The sha already keeps the two platforms from
 * colliding, so this is for the human reading a bucket listing — and
 * recording what the publisher actually uploaded cannot drift from it,
 * where a rule reimplemented here and in the build script can.
 *
 * The caller is the API's own build document. `filename` is validated at
 * registration (see FILENAME_RE in app.js) precisely because it lands
 * here, in a key this service signs.
 */
export const buildKey = (sha256, filename = DEFAULT_FILENAME) =>
  `builds/${sha256}/${filename || DEFAULT_FILENAME}`;
