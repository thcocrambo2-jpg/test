# Running on RunPod

For someone running Ember on a RunPod GPU pod: the customer path, where the
binary comes from, and how to run a checkout on a pod instead. RunPod is the
proven deployment — it is what production runs on and what the pricing and
support flow assume.

## The four ways to run it

One app, one licence server, four ways of getting the app onto a machine.
They differ in who compiles it and what the machine has to have already;
they do **not** differ in what the app does, and all four take a seat
through the same licence check.

| | For | What runs | Where the app comes from |
| --- | --- | --- | --- |
| **1. Linux binary** | RunPod customers | `scripts/runpod_start.sh` → `dist/krea2app` | built by `./build.sh` on a pod, fetched from the licence server on every start |
| **2. Windows binary** | Windows customers | `scripts/windows_start.ps1` → `dist\krea2app.exe` | built by `.\build.ps1` on Windows, fetched the same way |
| **3. Docker image** | anyone with a GPU and Docker | `docker compose up` | the image carries the *environment*; the binary is still fetched by `runpod_start.sh` inside it |
| **4. From source** | you, while developing | `python app.py` | your working tree |

**Which one to use.**

- **A customer on RunPod** gets 1 — this page.
- **A customer on their own Windows PC with an NVIDIA card** gets 2. They
  need Python 3.12 and git installed; the `.exe` is not self-contained.
  See [Windows](windows.md).
- **A customer who wants a reproducible environment**, or who is on Linux
  but not RunPod, gets 3. It removes the first-boot ComfyUI install, not
  the model download. See [Docker](docker.md).
- **You, changing code**, use 4. Nothing else lets you test what you just
  edited: 1, 2 and 3 all run the last *published* build. For UI work with
  no GPU at all, see [Dry run](dry-run.md).

**Which are proven.** 1 and 4 are what production runs on. 3 is written but
has never been built or run — treat it as unverified. 2 is newer; what has
and has not been tested is in
[Building the Windows binary](../releasing/build-windows.md).

The two build scripts are a matched pair and neither can produce the
other's artifact — Nuitka compiles for the OS it runs on and cannot
cross-compile. They bundle the same set of data files and packages, and
`make check-args` fails the build if that ever stops being true.

## How a customer launches

`scripts/runpod_start.sh` goes in the template's container start command. It
fetches the published build, verifies it, and runs it. On the pod (bash):

```bash
bash -c 'curl -fsSL https://<tag>.vercel.app/v1/start.sh -o /tmp/krea2-start.sh && exec bash /tmp/krea2-start.sh'
```

The customer sets two environment variables on the pod and nothing else:

| Variable | What it is |
| --- | --- |
| `KREA2_LICENSE_KEY` | the key they were issued |
| `KREA2_NODE_TAG` | the deployment id issued with it |

Leave both **empty in the template**. A template is public and every field
in it is readable by whoever clones it, so a key typed in there is a key
given to everyone. That is also why there is no credential anywhere in the
start script: the build comes from a private bucket, but the thing that
opens it is the customer's own licence key, which is already on the pod.

What limits who can *run* the app is the seat check the binary makes on
startup, not where it was downloaded from. Gating the download stops a
lapsed key fetching a new build and shows which machines pull on which
key; it does not stop a binary someone already has from being copied, and
nothing here pretends otherwise.

Optional on the pod: `KREA2_BASE_DIR` and `CIVITAI_TOKEN` — see
[Configuration](../configuration.md).

There is no variable that pins a build. Which build a licence gets is
decided by the server, so pinning one customer to an older build or
rolling everybody back is done through `/v1/admin/builds/promote` rather
than by talking a customer through editing their pod. See
[Publishing](../releasing/publishing.md).

The start script **replaces** the image's own `/start.sh`, so SSH and
JupyterLab do not come up. That is deliberate for a customer pod: the app's
output goes to the container log, which is where the UI link is read from.

## Running a checkout on a pod

On a RunPod GPU pod (PyTorch base image, `git` available), from the repo
root (bash):

```bash
python app.py
```

That single command clones ComfyUI if missing, installs requirements,
downloads any missing models (tens of GB on the first run), starts the
ComfyUI server, waits for it, and serves the web app. A public URL is
printed when it is up (`>>> OPEN THE UI HERE: ...`). Press Ctrl-C to stop.

Every step is idempotent, so an interrupted run resumes rather than
restarting. How much it downloads depends entirely on the licence — see
[Licensing and features](../architecture/licensing-and-features.md).

## The public URL

The printed URL is a **Cloudflare quick tunnel** —
`https://<words>.trycloudflare.com`, with an access token in the fragment.
It needs no account and no signup. cloudflared is fetched once and cached
under the base directory: the start script pre-seeds it from the public
Hugging Face mirror, pinned to a release and checksummed, and
`serve.cloudflared_binary()` falls back to the GitHub release if that is
unreachable. If the tunnel cannot be established the app says so and keeps
running on the local port, which on a pod is still reachable through
RunPod's own proxy.

The token rides in the URL **fragment** (`/#k=...`) rather than the query
string, so it is never sent to a server: not to Cloudflare's edge, not to
any proxy in between, and not in a `Referer` header. The page trades it for
an HttpOnly cookie and removes it from the address bar. See
`serve.announce()` in [`../../ember/web/serve.py`](../../ember/web/serve.py).

To serve without a tunnel, run the web layer on its own and pass
`--no-tunnel`. The flag belongs to `ember.web.serve`, not to `app.py`
(bash):

```bash
python -m ember.web.serve --no-tunnel
```

That skips the licence check, the bootstrap and the downloads, so it is
only useful against a pod that has already started once.

## The filesystem

The pod filesystem is treated as ephemeral: the ComfyUI install, model
weights, generated images and logs all live under `settings.BASE_DIR` and
are lost when the pod is destroyed. The default, `/workspace/krea2`, is
under the RunPod network volume, so the weights survive a Stop and the
second start takes minutes instead of an hour. Terminate destroys the
volume and pays for the whole download again.

Mount the network volume at `/workspace` and expose HTTP port `7860`.

## Next

- [Configuration](../configuration.md) — every environment variable
- [Troubleshooting](../troubleshooting.md) — what a failed start looks like
- [Building the Linux binary](../releasing/build-linux.md)
- [Architecture overview](../architecture/overview.md)
