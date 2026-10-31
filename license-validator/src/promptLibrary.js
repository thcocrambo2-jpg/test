// The prompt library, read from R2.
//
// There is no collection behind it. The library is one index file and its
// pictures in the PRIVATE builds bucket, written only by the operator's
// local admin page (scripts/prompt_library/admin.py in the app repo):
//
//   library/prompts/index.json         every entry, in display order
//   library/prompts/<id>/thumb.webp    and full.webp beside it
//
// This module reads the index and turns entries into what a pod renders:
// the switched-on ones only, each with signed picture links. The pictures
// themselves never pass through this function — the customer's browser
// fetches them straight from R2.
//
// Links are signed as if at the top of the current hour and last six
// hours. A link is then the same string for the whole hour, so a browser
// that already has the picture does not fetch it again, and every link a
// pod is handed has at least five hours left. scripts/prompt_library/
// store.py signs the same way for the admin page and the showcase.

import { presignGet, r2Configured } from "./r2.js";

export const INDEX_KEY = "library/prompts/index.json";
export const URL_TTL_SECONDS = 6 * 3600;

// How long one read of the index is reused. The admin page saves a toggle
// straight to R2; a pod sees it within this, plus its own five-minute cache.
const INDEX_TTL_MS = 60_000;
const INDEX_FETCH_TIMEOUT_MS = 8_000;

let cached = null; // { at, entries }

const hourStart = () => {
  const now = new Date();
  now.setUTCMinutes(0, 0, 0);
  return now;
};

/** The picture of one role ("thumb" | "full") an entry names, if any. */
const media = (entry, role) =>
  (Array.isArray(entry.media) ? entry.media : []).find(
    (item) => item && item.role === role && typeof item.key === "string",
  );

/**
 * Every well-formed entry in the index, in order — enabled or not.
 *
 * No index yet is an empty library, not an error: it is what the bucket
 * looks like before the first upload. Anything else that goes wrong throws,
 * and the route answers 503 — a library that failed to load must not look
 * like one that is empty.
 */
export async function loadEntries() {
  if (cached && Date.now() - cached.at < INDEX_TTL_MS) return cached.entries;
  if (!r2Configured()) throw new Error("R2 is not configured");

  const response = await fetch(presignGet(INDEX_KEY, 300), {
    signal: AbortSignal.timeout(INDEX_FETCH_TIMEOUT_MS),
  });
  let entries = [];
  if (response.status !== 404) {
    if (!response.ok) throw new Error(`R2 answered ${response.status} for the index`);
    const document = await response.json();
    entries = (Array.isArray(document?.entries) ? document.entries : []).filter(
      (entry) =>
        entry &&
        typeof entry.id === "string" &&
        typeof entry.tab === "string" &&
        typeof entry.token === "string" &&
        media(entry, "thumb") &&
        media(entry, "full"),
    );
  }
  cached = { at: Date.now(), entries };
  return entries;
}

/**
 * One entry as a pod reads it.
 *
 * `granted` is whether the licence has the entry's tab. The picture is
 * shown either way — what a tab they have not bought can make is exactly
 * what is worth showing — but the token, which is the recipe, only goes to
 * a licence that can use it.
 */
export function entryWire(entry, granted) {
  const signedAt = hourStart();
  return {
    id: entry.id,
    tab: entry.tab,
    title: typeof entry.title === "string" && entry.title ? entry.title : null,
    width: Number.isInteger(entry.width) ? entry.width : null,
    height: Number.isInteger(entry.height) ? entry.height : null,
    thumb_url: presignGet(media(entry, "thumb").key, URL_TTL_SECONDS, signedAt),
    full_url: presignGet(media(entry, "full").key, URL_TTL_SECONDS, signedAt),
    token: granted ? entry.token : null,
  };
}
