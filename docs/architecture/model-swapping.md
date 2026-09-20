# Model swapping and crash recovery

Why the app unloads weights between jobs, what it deliberately does not
count as a swap, and how it recovers when ComfyUI dies anyway. For someone
changing the code, and for anyone reading an OOM in `comfyui.log`.

## The problem

With Krea 2 V1 (turbo / raw), Krea 2 V2 (turbo mxfp8 / raw), Wan and
MiniMax all selectable, several multi-gigabyte UNets are in rotation.
ComfyUI keeps what it has loaded until memory pressure evicts it, so a
swap has a window where **two full model sets are resident** — and that
window is where the server gets OOM-killed. When it dies mid-job the
websocket drops (`Connection to remote host was lost`) and every later job
fails with `Connection refused`, whichever tab it came from.

Three mechanisms handle it.

## Unload on swap

Before submitting, the runner compares the graph's heavy weights against
what that ComfyUI instance last loaded. If they differ it calls ComfyUI's
`POST /free` and **waits for free VRAM to stop rising** before queueing.

The waiting is the point: `/free` only sets a flag the prompt worker
consumes between jobs, so submitting immediately can win the race and
execute with the old models still loaded — exactly the peak this avoids.

`client.model_signature()` in
[`ember/comfy/client.py`](../../ember/comfy/client.py) derives the
signature from the workflow dict itself rather than tracking it per tab,
so a new tab gets swap handling for free and cannot forget to declare what
it loads. It reads two loader inputs, `unet_name` and `clip_name` — the
diffusion models at 13–35 GB and the text encoders at 5–18 GB.

Two things are deliberately excluded, both because `/free` is
all-or-nothing: it unloads *everything*, so anything in the signature can
cost a full UNet reload.

- **LoRAs.** They are patches on top of the base weights, so including
  them would make every slider tweak look like a model swap. Changing
  prompt, seed, steps or LoRA slots costs nothing.
- **VAEs**, at 0.25–1.4 GB. Krea 2 V1 and V2 use different ones
  (`qwen_image` vs `wan21`), so counting them would dump a 13 GB UNet the
  two tabs otherwise share just to swap 254 MB. Leaving both resident is
  far cheaper.

A consequence worth knowing: **point V1 and V2 at the same UNet and
switching between the tabs needs no reload at all.**

Set `KREA2_KEEP_MODELS_LOADED=1` to turn the unload off on a machine with
room to spare, where keeping models warm is faster. It is read once, in
[`ember/settings.py`](../../ember/settings.py), as `FREE_ON_SWAP`.

## One job at a time

Every generation runs on a *lane*, and a lane is one worker thread — so a
second tab's Generate queues rather than running alongside. They all feed
one single-threaded ComfyUI prompt worker anyway, so nothing real is lost.

But without it, a second handler would run far enough to call `/free`
while the first job still holds the models, stalling on the VRAM-settle
wait and corrupting the what-is-loaded bookkeeping. Video keeps its own
lane when `KREA2_WAN_PARALLEL` gives it a separate ComfyUI instance, which
is also a separate signature to track. See [job-queue.md](job-queue.md).

## Restart if it died anyway

`server.ensure_alive()` in
[`ember/comfy/server.py`](../../ember/comfy/server.py) runs before every
batch. ComfyUI is otherwise only waited for at startup, so a mid-session
death — an OOM kill during a swap, a segfault in a custom node — used to
leave every later job failing with a bare `ECONNREFUSED` and the app
needing a manual restart.

If the API does not answer, it restarts ComfyUI, preserving that
instance's `--reserve-vram` flags, and returns **the last 20 lines of
`comfyui.log`** for the tab's status box. The message is empty while
nothing is wrong, so a healthy path stays silent.

Transport failures are converted to `ComfyUIError` in the client, so a
dead server reads as a status message rather than a bare traceback.

## Reading a crash

If one persists, `comfyui.log` names the cause:

```bash
# bash, on the pod
dmesg -T | grep -i 'killed process'     # the OOM killer
grep -iE 'segmentation fault|CUDA' comfyui.log
```

`Killed process` in `dmesg -T` means the OOM killer. A `Segmentation
fault` or a CUDA error in the log points at a custom node instead. More in
[Troubleshooting](../troubleshooting.md).
