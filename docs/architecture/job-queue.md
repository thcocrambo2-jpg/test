# The job queue

What happens between a click on **Generate** and a picture coming back:
the queue, its lanes, how a job is stopped, how progress and results reach
the browser, and where the ETA comes from. For someone changing the code.

## A click records work, it does not do it

Clicking **Generate** does not generate. It writes the click down — the
handler, the values every control held at that moment, the tab it came
from — hands it to [`ember/generation/queue.py`](../../ember/generation/queue.py)
and returns in a few milliseconds. A worker thread per lane then runs the
recorded jobs one at a time, in the order they arrived.

That indirection buys three things:

- **The button comes straight back.** Queue a second idea while the first
  is still rendering, instead of the pod idling between jobs whenever
  nobody is sitting there to click again.
- **The queue is visible.** The panel lists every job, live, with its tab,
  its prompt and its progress.
- **And it can be edited.** Each row carries a button, and what it does
  depends on the job: **✕ Remove** forgets one that is still waiting,
  **🛑 Stop** interrupts one that is already running, **✕ Clear** tidies a
  finished one off the list. **🧹 Clear finished** does the last of those
  in bulk.

The panel is outside any one tab, because the queue belongs to the pod and
not to a tab: a Krea job and a video are waiting on the same GPU.
Everything in the module is process-wide, like the ComfyUI server it
feeds, so two browser tabs open on the same pod see one queue — which is
the truthful picture, since there is one GPU behind them. The panel
carries the counts, so a collapsed panel still says how deep the queue is.

`HISTORY` finished jobs (20) stay on the list. They are kept at all
because a tab reads its gallery back out of its last job; drop them on
completion and the images vanish the moment they arrive.

## Lanes

A lane is one worker, which makes it exactly the "only one of these at a
time" rule. `register_lane()` declares one and starts its thread, and
registers the ComfyUI client that serves it so a running job can be
interrupted.

| Lane | Jobs | ComfyUI instance |
| --- | --- | --- |
| `comfy` | every image tab, and video when it shares an instance | the main one |
| `wan` | video, only when `EMBER_WAN_PARALLEL=1` grants it a second instance | the second one |

One worker per lane is also what keeps the model-swap bookkeeping honest —
see [model-swapping.md](model-swapping.md).

A running job is stopped through `ComfyClient.interrupt()` — ComfyUI's
`POST /interrupt` — on its lane's instance. **Stopping is asynchronous by
nature:** the interrupt aborts the prompt ComfyUI is executing *now*, and
the worker sees the cancel flag at the job's next yield and stops feeding
it the rest of its batch. So a batch of eight can finish the picture it is
on before the queue lets go of it, which is why the row says *stopping*
rather than *stopped* until it is.

A handler that raises is a bug, but it must not take the lane down with
it: the worker logs and recovers rather than dying.

## How results get back to a tab

The submit is long over by the time there are any, so it cannot return
them. They arrive over **server-sent events**: the browser holds one
`GET /api/v1/stream` open for the life of the page, and every job state
change, progress step and finished batch is pushed down it. That is one
connection for the whole app rather than a poll per tab.

Progress is **determinate**. The ComfyUI client already yields
`{"type": "progress", "step", "total"}` as structured data, and the event
stream carries the numbers, so the bar is a real bar rather than a status
string.

A keep-alive comment every 15 seconds stops an idle proxy closing the
stream, and the response carries `X-Accel-Buffering: no` so nothing in
between holds events back to fill a buffer. The Vite dev proxy needs the
same treatment; see [web-ui.md](web-ui.md).

A tab shows the newest job *that has begun* — so it switches to a new run
when that run starts, not when it was queued, and goes on showing the last
finished run while three more wait behind it.

## What a queued job is frozen against

The arguments are read out of the controls at click time and copied into
the job, so moving a slider afterwards cannot reach work already in the
line. That extends to the two per-run tickboxes: **publish** and **save as
preset** are read on the click, then disarmed immediately, because they
are per-run decisions rather than modes.

The preset save itself happens wherever the job runs, which is after the
click that asked for it has returned. So the save tells the queue
(`queue.note_preset_saved`), and the same event stream that carries the
images back refills the preset dropdown — every dropdown showing that
tab's list, which since the Edit tabs joined in is two of them.

## The ETA

[`ember/generation/eta.py`](../../ember/generation/eta.py) works out how
long the running job has left, because ComfyUI never says. The "ETA"
ComfyUI shows is tqdm printing to its own console; nothing of it crosses
the websocket, where a `progress` frame carries `{value, max, node}` and
that is all. So the estimate is made on this side, out of two things only
this side can know.

**What the sampler is doing right now.** The gaps between progress frames
are a seconds-per-step rate, and a rate times the steps left is a good
answer — but only while sampling. Everything around it sends nothing:
waking ComfyUI, loading or swapping the weights (often the biggest part of
an image run), encoding the prompt, decoding and saving. A step counter
alone is blind for exactly the part that takes longest.

**What the same kind of run took last time.** Every finished picture is
recorded in phases — the silent stretch before the first step, each
sampler segment, the silent stretch after the last — under a key that says
which models it loaded and how big a thing it made. That is what gives the
customer a number the moment a job starts, before any step has arrived.

The two are blended: history until the sampler has been timed over a few
steps, the live rate for the rest of the segment it is in, history again
for whatever comes after it and for the pictures of the batch still to
come. A fresh estimate is pulled toward the previous one (`SMOOTHING`),
because rates wobble step to step and a countdown that lurches by twenty
seconds on every frame reads as a broken one.

Two things about the history key are worth knowing before changing it:

- **Step counts are deliberately not part of it.** Sampling time is
  proportional to them and nothing else is, so steps are recorded with
  each sample and the history is rescaled to the run at hand. Keying on
  them would mean moving the steps slider threw away every past timing —
  including the model load, which does not care how many steps follow it.
- **Strings are left out**, except the weights, which the model signature
  already covers. Prompts, filenames and seeds change on every click and
  cost nothing.

The history lives in `eta_history.json` beside the images and the logs, so
it survives restarts and is per-pod — which is right, since an A40 and an
RTX 5050 take very different times over the same graph. It keeps the last
`SAMPLES` runs of each of at most `MAX_KEYS` kinds, least recently used
dropped.

One tracker is made per Generate click, by the executors in
[`ember/generation/runner.py`](../../ember/generation/runner.py), and
fed the same client events they already consume; it reports through
`queue.report_eta`. Off the queue's worker threads — the golden snapshots
and the dry run drive the handlers directly — it is a tracker that does
nothing, so those neither see an estimate nor write a history file.

**Nothing in the ETA may break a render.** Every entry point swallows its
own failures, and the worst outcome of a bug there is a missing or wrong
estimate.
