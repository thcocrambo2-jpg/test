"""Refine Krea renders with Z-Image Turbo image-to-image, and keep score.

    python img2prompt/refine.py image  --image img4.png
    python img2prompt/refine.py judge  --item img4
    python img2prompt/refine.py init   --picks img2prompt/runs/refine-pick
    python img2prompt/refine.py render --item "img1 · iter4_1" [--denoise 0.25]
    python img2prompt/refine.py status

`image` is the step after run.py finishes an image on Krea: every Krea
render of that image is refined once at denoise 0.25 (lessons.md), into
the refine run that belongs to the Krea run (runs/<krea run>-refine/). The
item is the image; iteration n is the refine of Krea iteration n, saved as
iter<n>_1.png beside the Krea render it came from (krea<n>_1.png). The
refines are not judged. runs/latest.txt stays on the Krea run, because a
second image may be mid-loop there; `judge` (optional) still works on an
item if latest.txt is pointed at the refine run by hand.

`init` and `render` are the denoise sweep: each pick file
(refine-pick/<image>.json, written by a content judge) names Krea renders
worth refining, each becomes an item whose iteration 0 is the Krea render
and iterations 1..n its refines at each denoise in DENOISES.

Every refine submits the Krea render to zimage_t2i as the start image with
its Krea prompt, the Default preset with every LoRA off, steps 12, CFG 1,
er_sde, upscale off, the 1328x1328 area (the start image keeps its own
aspect) and the image's seed. Long — run it in the background.
"""

import argparse
import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402 — state, API and polling helpers

TAB = "zimage_t2i"
PRESET = "Default"
DENOISES = (0.25, 0.35, 0.45)
DENOISE = 0.25  # the sweep's winner: kept content 8 of 8, more real 7 of 8
AREA = "1328×1328 (1:1)"
SETTINGS = {"steps": 12, "cfg": 1.0, "sampler": "er_sde", "upscale": False,
            "batch_count": 1, "randomize": False}


def source_run(image: str) -> tuple[Path, dict]:
    """The Krea run and image row a pick file's image came from."""
    for sdir in sorted(run.RUNS.iterdir(), reverse=True):
        sfile = sdir / "state.json"
        if not sfile.exists():
            continue
        state = json.loads(sfile.read_text(encoding="utf-8"))
        if (state.get("tab") or run.TAB) != run.TAB:
            continue
        for row in state["images"]:
            if row["dir"] == image and row["iterations"]:
                return sdir, row
    run.fail("No Krea run has %r" % image)


def cmd_init(args) -> None:
    picks = Path(args.picks)
    rdir = run.RUNS / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-refine")
    rdir.mkdir()
    state = {"folder": "Krea renders, refined with Z-Image i2i",
             "tab": TAB + " i2i", "preset": PRESET,
             "overrides": {"denoise": list(DENOISES), "loras": "off"},
             "started_at": run.now(), "finished_at": None, "current": None,
             "images": []}
    for pick in sorted(picks.glob("*.json")):
        image = pick.stem
        sdir, srow = source_run(image)
        top = json.loads(pick.read_text(encoding="utf-8"))["top2"]
        for render in top:
            n = int(render.split("_")[0].removeprefix("iter"))
            stem = render.removesuffix(".png")
            item_dir = rdir / ("%s_%s" % (image, stem))
            item_dir.mkdir()
            src = sdir / srow["dir"]
            ref = item_dir / Path(srow["reference"]).name
            shutil.copy2(sdir / srow["reference"], ref)
            shutil.copy2(src / render, item_dir / "iter0_1.png")
            shutil.copy2(src / ("iter%d_prompt.txt" % n),
                         item_dir / "iter0_prompt.txt")
            now = run.now()
            state["images"].append({
                "name": "%s · %s" % (image, stem), "dir": item_dir.name,
                "reference": ref.relative_to(rdir).as_posix(),
                "width": srow["width"], "height": srow["height"],
                "seed": srow["seed"], "aspect": AREA,
                "status": "pending", "config": {"denoise": list(DENOISES)},
                "source": str(src / render),
                "started_at": None, "finished_at": None, "note": None,
                "best": None,
                # Iteration 0 is the Krea render, so the page shows it
                # beside its refines.
                "iterations": [{"n": 0, "started_at": now, "finished_at": now,
                                "render_started_at": None,
                                "render_seconds": None,
                                "renders": ["iter0_1.png"], "denoise": None,
                                "negative_changed": False,
                                "ram_free_gb": None}]})
    run.save(rdir, state)
    (run.RUNS / "latest.txt").write_text(rdir.name, encoding="utf-8")
    run.out({"ok": True, "run": str(rdir),
             "items": [r["name"] for r in state["images"]]})


def upload(path: Path) -> str:
    """POST one file to /uploads as multipart form data; return its id."""
    boundary = uuid.uuid4().hex
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
             "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n")
            % (boundary, path.name)).encode() + path.read_bytes() \
        + ("\r\n--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        run.API + "/uploads", data=body, method="POST",
        headers={"content-type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["id"]


def refine_values(preset: dict, prompt: str, seed: int, image_id: str,
                  denoise: float) -> dict:
    values = dict(preset)
    for key in list(values):  # every LoRA slot off
        if key.startswith("lora.") and key.endswith(".enabled"):
            values[key] = False
    values.update(SETTINGS, prompt=prompt, seed=seed, resolution=AREA,
                  image=image_id, denoise=denoise)
    return values


def refine_one(rdir: Path, item: str, n: int, values: dict,
               target: Path) -> dict:
    """Submit one refine as iteration n of item, poll it, save target."""
    job = submit(rdir, item, n, values)
    return wait(rdir, item, n, job, values["denoise"], target)


def submit(rdir: Path, item: str, n: int, values: dict) -> str:
    rdir, state = run.load(rdir)
    row = run.find(state, item)
    it = run.iteration(row, n, create=True)
    it.update(denoise=values["denoise"], render_started_at=run.now(),
              renders=[])
    run.set_current(state, item, n, "rendering")
    run.save(rdir, state)
    return run.call("POST", "/tabs/%s/generate" % TAB,
                    {"values": values})["jobId"]


def wait(rdir: Path, item: str, n: int, job: str, denoise: float,
         target: Path) -> dict:
    """Poll a submitted refine until it finishes, then save target."""
    values = {"denoise": denoise}
    while True:
        time.sleep(run.POLL_SECONDS)
        queue = run.call_retrying("GET", "/queue")
        mine = next((j for j in queue["jobs"] if j["id"] == job), None)
        if mine is None:
            run.fail("Job %s left the queue." % job)
        rdir, state = run.load(rdir)
        # Queued behind its batch-mates: time it from when it runs.
        if mine["status"] in ("queued", "waiting"):
            run.iteration(run.find(state, item), n).update(
                render_started_at=run.now())
        run.active_entry(state, item, n, "rendering").update(
            progress=mine["progress"], step=mine["step"],
            backend_eta=mine["eta"], ram_free_gb=run.free_ram_gb())
        run.save(rdir, state)
        if mine["status"] in ("done", "failed", "cancelled"):
            break
    if mine["status"] != "done":
        run.fail("Refine at %.2f: %s" % (values["denoise"], mine.get("error")
                                          or mine["progress"]))
    shown = run.call_retrying("GET", "/tabs/%s/display?job=%s"
                              % (TAB, urllib.parse.quote(job)))
    media = ((shown.get("result") or {}).get("images") or [None])[0]
    if not media:
        run.fail("Refine at %.2f produced no image." % values["denoise"])
    with urllib.request.urlopen(run.API.rsplit("/api/v1", 1)[0]
                                + media["url"], timeout=120) as resp:
        target.write_bytes(resp.read())

    rdir, state = run.load(rdir)
    row = run.find(state, item)
    it = run.iteration(row, n)
    it.update(renders=[target.name], finished_at=run.now(),
              render_seconds=round(run.now() - it["render_started_at"], 1))
    run.save(rdir, state)
    return {"denoise": values["denoise"], "render": str(target),
            "minutes": round(it["render_seconds"] / 60, 1)}


def cmd_image(args) -> None:
    """Refine every Krea render of one finished image at DENOISE."""
    kdir, kstate = run.load(locked=False)
    if (kstate.get("tab") or run.TAB) != run.TAB:
        run.fail("runs/latest.txt is not a Krea run: %s" % kdir.name)
    krow = run.find(kstate, args.image)
    krenders = [(it["n"], it["renders"][0]) for it in krow["iterations"]
                if it["renders"]]
    if not krenders:
        run.fail("%s has no Krea renders." % args.image)
    ksrc = kdir / krow["dir"]

    rdir = run.RUNS / (kdir.name + "-refine")
    if (rdir / "state.json").exists():
        rdir, state = run.load(rdir)
    else:
        rdir.mkdir(exist_ok=True)
        state = {"folder": "%s, refined with Z-Image i2i" % kstate["folder"],
                 "tab": TAB + " i2i", "preset": PRESET,
                 "overrides": {"denoise": DENOISE, "loras": "off"},
                 "krea_run": kdir.name, "started_at": run.now(),
                 "finished_at": None, "current": None, "images": []}
    item = krow["dir"]
    idir = rdir / item
    idir.mkdir(exist_ok=True)
    ref = idir / Path(krow["reference"]).name
    shutil.copy2(kdir / krow["reference"], ref)
    if not any(r["name"] == item for r in state["images"]):
        state["images"].append({
            "name": item, "dir": item,
            "reference": ref.relative_to(rdir).as_posix(),
            "width": krow["width"], "height": krow["height"],
            "seed": krow["seed"], "aspect": AREA, "status": "pending",
            "config": {"denoise": DENOISE}, "source": str(ksrc),
            "started_at": None, "finished_at": None, "note": None,
            "best": None, "iterations": []})
    row = run.find(state, item)
    # A refine cut off mid-render is forgotten; finished ones are kept.
    row["iterations"] = [it for it in row["iterations"] if it["renders"]]
    done = {it["n"] for it in row["iterations"]}
    row.update(status="running", started_at=row["started_at"] or run.now())
    run.save(rdir, state)

    preset = run.call("POST", "/schema/%s/apply" % TAB,
                      {"preset": PRESET})["values"]
    # Submit every refine of the image before waiting on any, so they sit
    # together in the backend queue: one Krea → Z-Image model swap per
    # image, not one per refine with Krea renders slipping in between.
    jobs = []
    for n, krender in krenders:
        if n in done:
            continue
        shutil.copy2(ksrc / krender, idir / ("krea%d_1.png" % n))
        shutil.copy2(ksrc / ("iter%d_prompt.txt" % n),
                     idir / ("iter%d_prompt.txt" % n))
        prompt = (idir / ("iter%d_prompt.txt" % n)).read_text(
            encoding="utf-8").strip()
        image_id = upload(idir / ("krea%d_1.png" % n))
        values = refine_values(preset, prompt, krow["seed"], image_id, DENOISE)
        jobs.append((n, submit(rdir, item, n, values)))
    results = [wait(rdir, item, n, job, DENOISE, idir / ("iter%d_1.png" % n))
               for n, job in jobs]

    rdir, state = run.load(rdir)
    row = run.find(state, item)
    run.clear_current(state, item)  # refines are not judged
    run.save(rdir, state)
    run.out({"ok": True, "item": item, "run": str(rdir),
             "reference": str(ref),
             "refines": [str(idir / r) for it in sorted(
                 row["iterations"], key=lambda it: it["n"])
                 for r in it["renders"]],
             "verdict_file": str(idir / "verdict.json"), "new": results})


def cmd_render(args) -> None:
    rdir, state = run.load()
    row = run.find(state, args.item)
    idir = rdir / row["dir"]
    row.update(status="running", started_at=row["started_at"] or run.now())
    run.save(rdir, state)

    preset = run.call("POST", "/schema/%s/apply" % TAB,
                      {"preset": PRESET})["values"]
    prompt = (idir / "iter0_prompt.txt").read_text(encoding="utf-8").strip()
    image_id = upload(idir / "iter0_1.png")
    # A refine cut off mid-render is forgotten; one already rendered at a
    # requested denoise is not redone.
    row["iterations"] = [it for it in row["iterations"] if it["renders"]]
    done = {it.get("denoise") for it in row["iterations"]}
    run.save(rdir, state)
    results = []
    for denoise in args.denoise:
        if denoise in done:
            continue
        n = max(it["n"] for it in row["iterations"]) + 1
        shutil.copy2(idir / "iter0_prompt.txt",
                     idir / ("iter%d_prompt.txt" % n))
        results.append(refine_one(
            rdir, args.item, n,
            refine_values(preset, prompt, row["seed"], image_id, denoise),
            idir / ("iter%d_1.png" % n)))
        rdir, state = run.load(rdir)
        row = run.find(state, args.item)

    rdir, state = run.load(rdir)
    row = run.find(state, args.item)
    run.set_current(state, args.item,
                    max(it["n"] for it in row["iterations"]), "judging")
    run.save(rdir, state)
    run.out({"ok": True, "item": args.item, "krea": str(idir / "iter0_1.png"),
             "reference": str(rdir / row["reference"]), "refines": results})


def cmd_judge(args) -> None:
    """Record the judge's verdict.json for one item and close it."""
    rdir, state = run.load()
    row = run.find(state, args.item)
    idir = rdir / row["dir"]
    verdict = json.loads((idir / "verdict.json").read_text(encoding="utf-8"))
    scores = {r["render"]: float(r["score"]) for r in verdict["ranking"]}
    for it in row["iterations"]:
        it["scores"] = scores
        it["differences"] = verdict.get("differences", [])
        it["categories"] = verdict.get("categories", {})
    best = max(scores, key=scores.get)
    n = int(best.split("_")[0].removeprefix("iter"))
    row.update(status="done", finished_at=run.now(),
               best={"render": best, "score": scores[best], "iteration": n},
               note=verdict.get("summary"))
    krea_run = state.get("krea_run")
    if krea_run:  # an image refine keeps its best the way run.py does
        shutil.copy2(idir / best, idir / "best.png")
        shutil.copy2(idir / ("iter%d_prompt.txt" % n),
                     idir / "best_prompt.txt")
    run.clear_current(state, args.item)
    if not krea_run and all(r["status"] != "pending"
                            for r in state["images"]):
        state["finished_at"] = run.now()
    run.save(rdir, state)
    if krea_run:  # hand the dashboard back to the Krea run
        (run.RUNS / "latest.txt").write_text(krea_run, encoding="utf-8")
    run.out({"ok": True, "item": args.item, "best": row["best"],
             "best_prompt": str(idir / ("iter%d_prompt.txt" % n)),
             "per_refine": verdict.get("refines")})


def cmd_status(_args) -> None:
    rdir, state = run.load(locked=False)
    run.out({"run": str(rdir),
             "items": [{"name": r["name"], "status": r["status"],
                        "best": r["best"]} for r in state["images"]]})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("image").add_argument("--image", required=True)
    sub.add_parser("init").add_argument("--picks", required=True)
    for name in ("render", "judge"):
        p = sub.add_parser(name)
        p.add_argument("--item", required=True)
        if name == "render":
            p.add_argument("--denoise", type=float, nargs="+",
                           default=list(DENOISES),
                           help="denoise values to refine at (default: all)")
    sub.add_parser("status")
    args = parser.parse_args()
    {"image": cmd_image, "init": cmd_init, "render": cmd_render,
     "judge": cmd_judge, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
