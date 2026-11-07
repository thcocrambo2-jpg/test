# Runpod GPU sniper

A local page that rents Runpod GPUs by sending create requests back to back
until one wins, then shows the pod and sounds an alarm. It also lists your
pods with start / stop / terminate buttons.

It is a small Python server (stdlib only, the same pattern as
`lora_trainer/dashboard.py`) serving `index.html`. The page can't call Runpod
itself: the browser blocks those requests (CORS), and the page would expose
the key.

## Set up

```
copy runpod_sniper\.env.example runpod_sniper\.env
```

Put your key in `runpod_sniper/.env`:

```
RUNPOD_API_KEY=rpa_...
```

You can also add several named accounts. Each one shows in the account
dropdown under its lowercased suffix:

```
RUNPOD_API_KEY_MAIN=rpa_...
RUNPOD_API_KEY_ALT=rpa_...
```

Put the pod's licence key there too:

```
EMBER_LICENSE_KEY=EMBER-XXXX-XXXX
```

The licence key is never stored in a preset, so `presets.json` can be shared
and one preset can be run with different keys. The page's licence key box
starts with the key from `.env`, or shows the placeholder `LICENSE_KEY` when
`.env` has none. Whatever is in the box goes with the next snipe, as the
pod's `EMBER_LICENSE_KEY`; "Save to .env" keeps it for next time. Snipe
stays off while the box is empty. Each watcher shows the key it runs with.

Until a placeholder from `.env.example` (`rpa_xxxx…`, `EMBER-XXXX-XXXX`) is
replaced, it counts as no key.

`.env` is git-ignored. The server never sends a Runpod API key to the page,
writes it to the log, or puts it in an error message. The server re-reads `.env` when
the page loads, so after editing it, just reload the page.

## Run

Needs Python 3.10 or newer and no packages: everything is in the standard
library (see `requirements.txt`).

**Windows:** double-click `start.bat`. It checks for Python 3.10+, creates
`.env` from `.env.example` the first time (and opens it in Notepad for your
key), then starts the server and shows the link. Arguments are passed on,
e.g. `start.bat --dry-run`.

**Any system:**

```
python runpod_sniper/server.py                # real Runpod, http://127.0.0.1:7895
python runpod_sniper/server.py --dry-run      # fake Runpod, nothing is created
```

| Flag | Meaning |
|---|---|
| `--port 7895` | Port to listen on. 7860/7870/7880/7890/7891/8888 are taken. |
| `--dry-run` | Uses `fake.py` instead of Runpod and ignores `.env`. Gives two fake accounts, `dry-main` and `dry-alt`, and the licence key `EMBER-DRY-RUN`; saving a key from the page keeps it in memory and leaves `.env` alone. |
| `--dry-fails 20` | In a dry run, how many "no capacity" replies come before a win. |
| `--dry-health random` | In a dry run, what won pods look like: `fast`, `slownet`, `throttled`, `x4`, `cached`, `hosterr`, `oldbuild`, `lowram`, `slowdisk`, `restartloop`, or `random` (half fast). |
| `--dry-country FR` | In a dry run, put every fake pod in this country (to try the location guard). |
| `--no-sound` | Don't play `Alarm01.wav` (win) or the pod-check warning sound on the server. |

The server listens on 127.0.0.1 only. It refuses requests whose Host header
isn't `127.0.0.1` or `localhost`, and POSTs without the page's `x-sniper`
header, so other websites can't start a snipe through it.

## Using it

1. **GPUs.** The table comes from `gpus.json`. "Refresh live prices + stock"
   reads `GET /v2/catalog/gpus?include=AVAILABILITY&product=POD` once for
   Community and once for Secure, and shows NONE / LOW / MEDIUM / HIGH. Stock
   is only a hint: see below. "+ preset" adds a GPU to the form.
2. **Preset.** Click a preset to load it, edit the form, then Snipe. "Save as
   preset" writes the form to `presets.json` under the name you give it; an
   existing name is overwritten. "Snipe for (minutes)" (1–600) is how long
   the watcher keeps trying before it gives up. Under the form, the page says
   which create API the snipe will use and why.
3. **Snipe.** Starts a watcher on the server: 3 worker threads share one
   0.7 s slot per key. It shows tries, rate, elapsed and remaining time, the
   last reply, a count of replies by status code, and an event log. The
   header shows the requests left this minute and this hour. These numbers
   come from Runpod's `RateLimit` headers when it sends them; otherwise the
   server counts its own requests. The watcher keeps running if you refresh
   or close the page. Each account runs one watcher at a time, and different
   accounts can run at once.
4. **Won.** The page shows the pod card: id, GPU, $/hr, RAM/vCPU, CUDA, data
   center, `https://<id>-7860.proxy.runpod.net`, and the SSH command. Click
   the SSH command to copy it. The server re-reads the pod every 10 s until
   it is RUNNING and its CUDA version is known. The page plays a siren and
   flashes its title, and the server plays `C:\Windows\Media\Alarm01.wav`
   twice.
5. **My pods.** Shows the selected account's pods with start / stop /
   restart / terminate. Terminate and stop ask before acting. If a start
   fails with "not enough free GPUs", the page offers "Snipe a new one like
   it", which loads that pod's GPU, cloud and name into the form.
   **🩺 Check** runs the pod health check (below) on any running pod.

## Pod health checks

Every won pod is checked from the moment it is created, and the pod card
shows what was found: country and machine, internet speed, GPU speed,
PCIe link, RAM, disk, the image pull. When something is wrong, a banner
appears at the top of the page with **Terminate**, **Keep** and **Block
this host**, the server plays `Windows Notify System Generic.wav` once
and the page two soft notes (the win alarm stays the siren). **Nothing is
ever terminated without you** — except by the location guard, if you set
one (below).

Where the numbers come from:

| Signal | From | Warns when |
|---|---|---|
| Country, machine id, host disk | v1 `GET /v1/pods/{id}?includeMachine=true` | blocked country or host; Runpod's disk figure < 2000 MB/s |
| RAM / vCPU | the pod | RAM < 60 GB |
| Image pull speed | the pod's system log (byte counts, or the image's layer sizes from Docker Hub) | < 30 MB/s — a rough hint only; a cached image has nothing to measure |
| Stuck / restarting / exited | system log and the pod's status | image still fetching after 10 min, container restarts nobody asked for, the pod stopping on its own |
| Errors | the logs | Docker Hub rate limit, disk full, NVIDIA runtime, CUDA, crashes, models that could not be downloaded … |
| The app | its page on port 7860, once it says it is ready | not answering 3 min after "OPEN THE UI HERE" |
| Internet speed | Ember's `EMBER_PODCHECK net` line (4 streams from the model mirror's CDN), or — on older builds — the gaps between its "✓ file" lines | < 50 MB/s (< 25 bad) |
| GPU | Ember's `gpu` line: bf16 TFLOPS against the normal for that card, throttle reasons, power cap, temperature | < 85 % of normal; throttling, power capped, ≥ 87 °C |
| PCIe | Ember's `gpu` line: host→GPU copy speed, link read under load | < 10 GB/s |
| Render speed | Ember's `render` line per picture | s/step 1.2× slower than other pods with the same card and model |

The Ember numbers need a build with `ember/podcheck.py` (the pod
downloads the current build at every start; no new Docker image needed).
On an older build the card says so and judges from the logs only.

"Normal" for a GPU is learned: the median of the last 10 clean pods of
that card (no GPU warning). Until 3 are recorded the card says
"learning"; a `"baseline": {"tflops": …, "h2d_gbs": …}` added by hand to a
GPU in `gpus.json` is used meanwhile.

Everything goes into `pods-history.jsonl` (git-ignored, one line per
update, newest wins), which the **📊 Pods** page summarises: every pod,
by country, by host, the learned normals, and the blocked hosts.

**Location guard.** Community pods have no data center — the data-center
picker only works for Secure — so the preset has **Countries** instead
(block list or allow list). Right after a win the server reads the pod's
machine (creates pause meanwhile); a pod in the wrong country, or on a
host you blocked, is terminated and sniping goes on, like the price
guard — or, with "keep it, warn me", it is kept and flagged.

Cost: about 40 requests in a pod's first 20 minutes (the logs stream is
one request however long it stays open), all counted in the budget.

## Auto download

The **⬇ Auto download** page (navbar at the top) lists every pod on the
selected account, whether it was sniped here or started elsewhere. For a
running pod, choose a folder (type it, or **Browse…** for the normal folder
dialog), then press **Start**. Each pod can have its own folder, or several
can share one.

- Every picture and video on the pod is saved, including what was generated
  before you pressed Start, each with its settings token beside it as
  `<name>.txt`. The token is built in Python (`ember_token.py`), with no
  Node; it decodes to the same settings as the Gallery's own token.
- The pod is checked every 8 s; a new file is saved as soon as it appears.
  A video still being encoded is left until it is finished.
- Files are written under a temporary name and renamed when complete.
- Nothing is downloaded twice: each pod keeps a record in its folder,
  `.ember-downloads-<pod id>.json`. A file already in the folder with the
  same name and size counts as saved; a different file with the same name
  (two pods, one folder) gets the pod id added to its name.
- The card shows files on the pod, in the folder, saved now, still being
  written, tokens, MB downloaded, the current file with its speed, and the
  last file saved.
- Downloads keep running if the page is closed, and resume when the server
  restarts (`downloads.json`, git-ignored, keeps the list). A pod that stops
  answering is retried every 30 s.
- The app is reached at `https://<pod id>-7860.proxy.runpod.net`. Under
  **Advanced** you can give another app URL, or the app's access key for a
  pod started with `EMBER_UI_REQUIRE_TOKEN=1`.
- In a dry run every fake pod runs a fake Ember app that adds a file every
  6 s; every fifth is a video that stays "still encoding" for 14 s.

## Runpod API facts this relies on

- **Only the create call wins a GPU.** Community stock disappears within
  milliseconds, so if you check stock and then create, you lose the race. The
  watcher sends create requests back to back.
- **Rate limit: 90 requests/min, 1800/hour, 21600/day per key.** The watcher
  waits about 0.7 s between attempts, using a slot shared by its 3 workers.
  On a 429 it pauses that key for 15 s. Listing pods and refreshing stock
  count against the same budget.
- **Send a `User-Agent` header.** Python's default urllib User-Agent gets a
  403 from Cloudflare, so the server sends `curl/8.5.0`.
- **There are two create APIs, and the server uses both:**
  - **v1**: `POST https://rest.runpod.io/v1/pods`
    - The only API with the bandwidth (`minDownloadMbps`), vCPU, RAM and
      public-IP filters.
    - `gpuTypeIds` takes the whole GPU list in one request.
    - No capacity is **HTTP 500** "There are no instances currently
      available".
    - Its `dataCenterIds` field rejects EUR-IS-4, EUR-IS-5 and EUR-NO-2 with
      a 400 schema error.
    - It adds the account's SSH key (`PUBLIC_KEY`) to the pod automatically.
    - The reply's cost field is `costPerHr`.
  - **v2**: `POST https://api.runpod.io/v2/pods`
    - Accepts every data center, but has **no bandwidth filter**. It does
      filter on RAM and vCPU through `gpu.minRamPerGpu` and
      `gpu.minVcpuCountPerGpu`.
    - Takes one GPU id per request, so the watcher cycles through the
      preset's GPU list.
    - No capacity is **HTTP 400** "no longer any instances available".
    - Without `startSsh: true` the pod gets no SSH key, so the server always
      sends it.
    - The reply's cost field is `cost`.
  - **How "auto" picks:** v1 if a bandwidth or public-IP filter is set, or
    if every chosen data center is one v1 accepts. Otherwise v2: when no data
    center is chosen, or when one of them is a data center v1 rejects. If v1
    is chosen anyway, the server drops the data centers v1 rejects and says
    so in the event log. The form's API field can force v1 or v2.
- **Telling "no capacity" from a real error.** With v2, a bad request is
  also a 400. Any reply that isn't the no-capacity message stops the watcher
  and shows the error, so it doesn't retry a bad request for 15 minutes.
  5xx replies and network errors other than v1's no-capacity message are
  treated as temporary and retried.
- **Env with a template merges per key, and your values win.** The server
  sets only `EMBER_LICENSE_KEY`, from the licence key box. The template's
  `HF_TOKEN` and `CIVITAI_TOKEN` stay.
  Template `yjkhkejrf7` (image `emberai/ember:latest`) only allows hosts with
  CUDA 13.0–13.4. Leave the "Allowed CUDA" dropdown on Any to keep that
  range; ticking any version replaces the template's range completely.
- **Price guard.** If a created pod costs more than the max $/hr (for the
  whole pod), the watcher terminates it and keeps trying.
- **Race guard.** If two workers win at once, the watcher terminates the
  extra pod. Every termination appears in the event log. If a termination
  fails, the log says "TERMINATE FAILED"; delete that pod by hand.
- **Pod actions** use v2 `POST /v2/pods/{id}/action` with
  `{"action": "start|stop|restart|terminate"}`. Starting a stopped Community
  pod often fails with "not enough free GPUs on the host machine"; snipe a
  new one instead.
- **Stopped pods lose their container disk.** Only a volume survives.

## Dry run

`--dry-run` replaces Runpod with `fake.py`, which returns the same status
codes and messages as the real API:

- Each snipe gets `--dry-fails` no-capacity replies first (500 on v1, 400 on
  v2), with an occasional 429, and then a pod.
- The win stays open for 2.5 s, so a request already in flight also wins
  and the race guard has to terminate it.
- About 1 win in 7 lands on a host at 1.5× the price, which the price guard
  throws back.
- v1 rejects EUR-IS-4, EUR-IS-5 and EUR-NO-2. v2 rejects an unknown GPU id
  with a plain 400, which the watcher should stop on.
- Pods go from PROVISIONING to RUNNING after 8 s. Starting a stopped
  Community pod fails half the time.
- Replies carry `RateLimit` headers counted over a real 60 s / 1 h window.
- Each pod plays a health scenario (`--dry-health`): its logs stream (10×
  fast: a startup takes about a minute), its machine and the app's pod
  check lines follow it, and Docker Hub and the mirror's file list are
  faked, so nothing leaves the PC. A dry run keeps its own
  `pods-history.dry.jsonl`.
- `python runpod_sniper/test_health.py` runs the health checks' tests,
  including a real pod's system log.

## Files

| File | What it is |
|---|---|
| `server.py` | HTTP server, watcher, Runpod calls |
| `health.py` | Pod health checks: log parser, rules, per-pod watch, history, blocked hosts |
| `test_health.py` | Tests for `health.py` and the location guard (standard library) |
| `pods-history.jsonl`, `hosts.json`, `image-layers.json` | Made by the health checks, git-ignored |
| `index.html` | The page (vanilla JS, no build step). It uses the app's palette from `tokens.css`, which the server serves as `/tokens.css`. |
| `tokens.css` | The theme: a copy of the app's `webui/src/theme/tokens.css`, kept here so the folder works on its own. Copy it again if the app's palette changes. |
| `gpus.json` | The GPU table, with list prices read 2026-11-03 |
| `presets.json` | Presets; "Save as preset" writes here |
| `downloader.py` | The auto downloader: one thread per pod |
| `ember_token.py` | Ember settings tokens in Python (a port of the web UI's) |
| `fake.py` | The fake Runpod, and a fake Ember app on each fake pod, for `--dry-run` |
| `start.bat` | Windows launcher: checks Python, creates `.env` the first time, starts the server |
| `requirements.txt` | Lists no packages: the sniper uses only the standard library |
| `.env.example` | Template for `.env` |
