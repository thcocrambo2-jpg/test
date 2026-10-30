/*
 * Which of the Files in a form came off the user's own disk or clipboard.
 *
 * An image field holds a File whatever it came from — dropped, pasted,
 * chosen, picked off the Recent generations strip, or fetched from the pose
 * library — and that invariant is worth keeping (see `fileFromUrl` in
 * RecentStrip). The Recent uploads strip needs the one fact it erases: was
 * this the user's own picture? So the ways a user's file comes in mark it
 * here, and `upload` in api/http.ts tells the server, which keeps the
 * marked ones on the list the strip reads (ember/generation/sources.py).
 *
 * A WeakSet, so a File no field holds any more is not kept alive by it.
 */

const own = new WeakSet<File>()

/** Mark files as the user's own, and hand them back for chaining. */
export function markOwn<T extends File>(files: T[]): T[] {
  for (const file of files) own.add(file)
  return files
}

export function isOwn(blob: Blob): boolean {
  return blob instanceof File && own.has(blob)
}
