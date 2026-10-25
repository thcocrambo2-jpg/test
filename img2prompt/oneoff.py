"""Render one prompt outside the loop: no judge, no state.json.

    python img2prompt/oneoff.py <folder> [--tab zimage_t2i] [--preset Default]
        [--like reference.png | --size "3:4 (Portrait Standard)"]
        [--seed N] [--steps N] [--batch N]

<folder> holds prompt.txt and, optionally, negative.txt (the tab default
when absent). The tab and preset default to config.json's; steps default
to config.json's on Krea and to the preset's own elsewhere. --like picks
the tab's size choice nearest to that image's shape. Renders land in the
folder as render_1.png, render_2.png, ... Long-running, so run it in the
background like `run.py render`.
"""

import argparse
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402 — the loop's API helpers and config reader


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--tab")
    parser.add_argument("--preset")
    parser.add_argument("--like", help="pick the size nearest this image")
    parser.add_argument("--size", help="an exact size choice of the tab")
    parser.add_argument("--seed", type=int, default=1234567)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()

    cfg = run.read_config()
    tab = args.tab or cfg.get("tab", run.TAB)
    preset = args.preset or cfg["preset"]
    folder = Path(args.folder).resolve()
    fields = {f["name"]: f for f in run.call("GET", "/schema/" + tab)["fields"]}
    values = run.call("POST", "/schema/%s/apply" % tab,
                      {"preset": preset})["values"]
    if not values:
        run.fail("Preset %r is not on offer on %s." % (preset, tab))

    shape = run.size_field(fields)
    if args.like:
        values[shape] = run.nearest_aspect(*run.image_size(Path(args.like)),
                                           fields[shape]["choices"])
    elif args.size:
        values[shape] = args.size
    steps = args.steps or (int(cfg["steps"]) if tab == run.TAB else None)
    if steps:
        values["steps"] = steps
    negative_file = folder / "negative.txt"
    if negative_file.exists():
        values["negative"] = negative_file.read_text(encoding="utf-8").strip()
    values.update(prompt=(folder / "prompt.txt").read_text("utf-8").strip(),
                  seed=args.seed, randomize=False,
                  batch_count=args.batch or int(cfg.get("batch_count", 1)))

    started = time.time()
    job = run.call("POST", "/tabs/%s/generate" % tab,
                   {"values": values})["jobId"]
    while True:
        time.sleep(run.POLL_SECONDS)
        queue = run.call_retrying("GET", "/queue")
        mine = next((j for j in queue["jobs"] if j["id"] == job), None)
        if mine is None or mine["status"] in ("done", "failed", "cancelled"):
            break
    if mine is None or mine["status"] != "done":
        run.fail("Render %s" % (mine and (mine.get("error") or mine["status"])))

    shown = run.call_retrying("GET", "/tabs/%s/display?job=%s"
                              % (tab, urllib.parse.quote(job)))
    saved = []
    for k, media in enumerate((shown.get("result") or {}).get("images") or [],
                              start=1):
        target = folder / ("render_%d.png" % k)
        with urllib.request.urlopen(run.API.rsplit("/api/v1", 1)[0]
                                    + media["url"], timeout=120) as resp:
            target.write_bytes(resp.read())
        saved.append(str(target))
    run.out({"ok": True, "tab": tab, "preset": preset,
             "size": values.get(shape), "steps": values.get("steps"),
             "renders": saved,
             "render_minutes": round((time.time() - started) / 60, 1)})


if __name__ == "__main__":
    main()
