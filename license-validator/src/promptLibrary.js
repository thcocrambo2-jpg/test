// The prompt library, read from R2.
//
// There is no collection behind it. The library is one index file and its
// pictures in the PRIVATE builds bucket, written only by the operator's
// local admin page (scripts/prompt_library/admin.py in the app repo):
//
//   library/prompts/index.json         every entry, in display order
//   library/prompts/<id>/thumb.webp    and full.webp beside it
//   library/prompts/<id>/token.txt     its settings token
//
// The token has a file of its own because it is several kilobytes and the
// index is read whole. A page of the library carries no tokens at all —
// only whether each card can be used — and a token is fetched when someone
// presses Use or Copy on that card (POST /v1/prompt-library/token). A token
// never changes once its entry exists, so each one is fetched once per warm
// instance and kept.
//
// The index also carries the category and tag lists (key and label, in
// display order) beside the entries; each entry names one category key and
// a list of tag keys.
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
const TOKEN_FETCH_TIMEOUT_MS = 6_000;
const TOKEN_CACHE_MAX = 2_000;

const tokens = new Map(); // entry id -> token

let cached = null; // { at, library: { entries, categories, tags } }

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

/** A list of {key, label} from the index, well-formed rows only. */
const keyed = (rows) =>
  (Array.isArray(rows) ? rows : [])
    .filter((row) => row && typeof row.key === "string" && typeof row.label === "string")
    .map((row) => ({ key: row.key, label: row.label }));

/**
 * Every well-formed entry in the index, in order — enabled or not — and
 * the category and tag lists.
 *
 * No index yet is an empty library, not an error: it is what the bucket
 * looks like before the first upload. Anything else that goes wrong throws,
 * and the route answers 503 — a library that failed to load must not look
 * like one that is empty.
 */
export async function loadLibrary() {
  if (cached && Date.now() - cached.at < INDEX_TTL_MS) return cached.library;
  if (!r2Configured()) throw new Error("R2 is not configured");

  const response = await fetch(presignGet(INDEX_KEY, 300), {
    signal: AbortSignal.timeout(INDEX_FETCH_TIMEOUT_MS),
  });
  const library = { entries: [], categories: [], tags: [] };
  if (response.status !== 404) {
    if (!response.ok) throw new Error(`R2 answered ${response.status} for the index`);
    const document = await response.json();
    library.categories = keyed(document?.categories);
    library.tags = keyed(document?.tags);
    library.entries = (Array.isArray(document?.entries) ? document.entries : []).filter(
      (entry) =>
        entry &&
        typeof entry.id === "string" &&
        typeof entry.tab === "string" &&
        media(entry, "thumb") &&
        media(entry, "full"),
    );
  }
  cached = { at: Date.now(), library };
  return library;
}

/** One entry's token, from the cache or its token.txt. Throws on failure. */
export async function tokenFor(id) {
  const known = tokens.get(id);
  if (known !== undefined) return known;
  const response = await fetch(presignGet(`library/prompts/${id}/token.txt`, 300), {
    signal: AbortSignal.timeout(TOKEN_FETCH_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`R2 answered ${response.status} for ${id}'s token`);
  const token = (await response.text()).trim();
  if (tokens.size >= TOKEN_CACHE_MAX) tokens.clear();
  tokens.set(id, token);
  return token;
}

/**
 * One entry as a pod reads it.
 *
 * `granted` is whether the licence has the entry's tab. The picture is
 * shown either way — what a tab they have not bought can make is exactly
 * what is worth showing — but only a granted card is `usable`, and only a
 * usable card's token can be fetched.
 */
export function entryWire(entry, granted) {
  const signedAt = hourStart();
  return {
    id: entry.id,
    tab: entry.tab,
    title: typeof entry.title === "string" && entry.title ? entry.title : null,
    category: typeof entry.category === "string" ? entry.category : null,
    tags: Array.isArray(entry.tags) ? entry.tags.filter((tag) => typeof tag === "string") : [],
    width: Number.isInteger(entry.width) ? entry.width : null,
    height: Number.isInteger(entry.height) ? entry.height : null,
    thumb_url: presignGet(media(entry, "thumb").key, URL_TTL_SECONDS, signedAt),
    full_url: presignGet(media(entry, "full").key, URL_TTL_SECONDS, signedAt),
    usable: granted,
  };
}
