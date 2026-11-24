"""Download pictures of a public figure from Pinterest search results.

Run:  python pinterest_collect.py
It asks for the search text and a folder name, then saves:
  * 50 regular pictures, preferring pins uploaded before 2023
    (fewer AI edits back then); if there aren't enough, it tops up
    with the oldest later pins.
  * 25 close-ups from any year, preferring pins whose caption or
    Pinterest's image description mentions face / close / portrait etc.

A plain folder name goes under Downloads\\del; a full path is used as-is.
Needs gallery-dl:  pip install gallery-dl --index-url https://pypi.org/simple
"""
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

REGULAR = 50
CLOSEUPS = 25
PER_SEARCH = 300

# captions that suggest the picture isn't a real photo
NOT_REAL = re.compile(
    r"\b(ai|a\.i|midjourney|stable diffusion|generated|aiart|edit|edited|morph|"
    r"fake|art|artwork|painting|drawing|sketch|cartoon|lookalike)\b", re.I)
CLOSE = re.compile(r"close|face|portrait|headshot|selfie|smile|eyes", re.I)


def search(query):
    """Return (image_url, pin_metadata) for a Pinterest search via gallery-dl -j."""
    url = "https://www.pinterest.com/search/pins/?q=" + urllib.parse.quote(query)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "gallery_dl", "-j", "--range", f"1-{PER_SEARCH}",
         "--sleep-request", "1", url],
        capture_output=True, env=env)
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        print(f"  search '{query}': no results (gallery-dl exit {proc.returncode})"
              + (f": {err[-1]}" if err else ""))
        return []
    return [(e[1], e[2]) for e in data
            if isinstance(e, list) and len(e) >= 3 and e[0] == 3 and isinstance(e[2], dict)]


def candidates(queries, name_word, seen):
    out = []
    for q in queries:
        for img_url, p in search(q):
            sig = p.get("image_signature") or img_url
            if sig in seen:
                continue
            seen.add(sig)
            text = " ".join(str(p.get(k) or "") for k in ("title", "description", "grid_title"))
            board = str((p.get("board") or {}).get("name", ""))
            if NOT_REAL.search(text) or name_word not in (text + " " + board).lower():
                continue
            alt = " ".join(str(p.get(k) or "") for k in ("auto_alt_text", "seo_alt_text"))
            try:
                created = parsedate_to_datetime(p["created_at"])
            except Exception:
                continue
            out.append((created, img_url, bool(CLOSE.search(text + " " + alt))))
    return out


def download(items, n, folder, prefix, have):
    saved = 0
    for created, img_url, _ in items:
        if saved == n:
            break
        try:
            req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            continue
        digest = hashlib.md5(data).hexdigest()
        if len(data) < 15000 or digest in have:   # skip thumbnails and repeats
            continue
        have.add(digest)
        saved += 1
        ext = os.path.splitext(img_url.split("?")[0])[1] or ".jpg"
        num = saved
        while list(folder.glob(f"{prefix}_{num:02d}_*")):   # never overwrite an earlier run
            num += 1
        (folder / f"{prefix}_{num:02d}_{created:%Y%m%d}{ext}").write_bytes(data)
        time.sleep(1)
    return saved


def main():
    try:
        import gallery_dl  # noqa: F401
    except ImportError:
        sys.exit("gallery-dl isn't installed for this Python:\n  " + sys.executable +
                 "\nInstall it with:\n  python -m pip install gallery-dl "
                 "--index-url https://pypi.org/simple")
    query = input("Search text: ").strip()
    folder_name = input("Folder name: ").strip()
    if not query or not folder_name:
        sys.exit("Both the search text and the folder name are needed.")

    folder = Path(folder_name)
    if not folder.is_absolute():
        folder = Path.home() / "Downloads" / "del" / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    prefix = re.sub(r"\W+", "_", query.split()[0].lower())
    name_word = query.split()[0].lower()
    # repeats are checked against pictures already in the folder too
    have = {hashlib.md5(Path(f).read_bytes()).hexdigest()
            for f in glob.glob(str(folder / "*")) if os.path.isfile(f)}
    seen = set()

    print("Searching for regular pictures...")
    regular = candidates([query, f"{query} photos", f"{query} photoshoot"], name_word, seen)
    old = [c for c in regular if c[0].year <= 2022]
    newer = sorted(c for c in regular if c[0].year > 2022)
    got = download(old + newer, REGULAR, folder, prefix, have)
    old_saved = sum(1 for f in folder.glob(f"{prefix}_[0-9]*")
                    if int(f.name.split("_")[-1][:4]) <= 2022)
    print(f"  regular: {got} saved ({old_saved} from before 2023)")

    print("Searching for close-ups...")
    close = candidates([f"{query} close up", f"{query} face", f"{query} closeup face"],
                       name_word, seen)
    worded = [c for c in close if c[2]]
    other = [c for c in close if not c[2]]
    got_c = download(worded + other, CLOSEUPS, folder, prefix + "_closeup", have)
    print(f"  close-ups: {got_c} saved ({min(got_c, len(worded))} with close-up words)")
    print(f"Done: {folder}")


if __name__ == "__main__":
    main()
