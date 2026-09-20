"""Start the app: uvicorn, plus a Cloudflare quick tunnel for the URL.

Replaces `ui.launch_ui()`. What that did was call `gr.Blocks.launch(
share=True)` and let Gradio run both the web server and the public
tunnel, falling back to cloudflared only when the *.gradio.live link did
not answer. Deleting Gradio deletes both halves, so both are here.

The tunnel is now the **default**, not the fallback, and that is the
change with the widest blast radius in the whole rewrite.

Why cloudflared, and why default
--------------------------------
`share=True` is a *Gradio service*. It is the only thing that has ever
given a Windows customer a public URL — scripts/windows_start.ps1 has no
tunnel logic at all, and never needed any. So when Gradio goes, so does
the link, on both platforms at once.

A Cloudflare quick tunnel is free, needs no account and no signup, and
`ui._start_cloudflared` already implemented it here as Gradio's fallback.
Promoting it costs one binary download on first launch. The alternatives
were rejected on the same ground each time: pyngrok needs an account,
localtunnel needs Node on the target machine, and constraint 5 says
neither build host has Node.

The Windows branch
------------------
The code this came from hardcoded `cloudflared-linux-amd64` — correct,
because on Linux it was a fallback and on Windows it was never reached at
all: Gradio's share link always worked, so the fallback never ran. After
Section 3 it runs on *every* launch, on both platforms, so the download
picks its asset by `sys.platform`, keeps the `.exe` suffix Windows needs
to execute it, and skips the chmod that only means something on POSIX.

That single missing branch would have been a Windows launch that prints
no URL, on the first build after Gradio was removed, for every Windows
customer at once.
"""

import argparse
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from config import TEMP_DIR, log

# Where the quick tunnel URL appears in cloudflared's own output. It
# writes it to stderr in a box of ASCII art, so this is a search rather
# than a parse.
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# cloudflared has one release asset per platform and the names are stable.
# Keyed on sys.platform, which is "win32" on every Windows build and
# "linux" on the pod.
RELEASES = {
    "win32": ("cloudflared-windows-amd64.exe", "cloudflared.exe"),
    "linux": ("cloudflared-linux-amd64", "cloudflared"),
    "darwin": ("cloudflared-darwin-amd64.tgz", "cloudflared"),
}

DOWNLOAD = ("https://github.com/cloudflare/cloudflared/releases/latest/"
            "download/%s")

# How long to wait for the tunnel to name itself. Ninety seconds is what
# the Gradio fallback allowed and it has been enough; the failure it
# guards against is a network that blocks the Cloudflare edge, where
# waiting longer changes nothing.
TUNNEL_TIMEOUT = 90


def cloudflared_binary() -> Path:
    """The tunnel binary, downloaded on first use. Cached in TEMP_DIR.

    macOS ships as a .tgz rather than a bare binary, so it is listed for
    completeness and refused rather than half-supported: nothing in this
    product runs on macOS, and a download that lands a tarball where an
    executable is expected fails later and less clearly than this does.
    """
    asset, filename = RELEASES.get(sys.platform, (None, None))
    if asset is None or asset.endswith(".tgz"):
        raise RuntimeError(
            "No cloudflared build is wired up for %s — start the app with "
            "--no-tunnel and reach it on the local port." % sys.platform
        )
    binary = TEMP_DIR / filename
    if binary.exists():
        return binary
    log.info("Downloading cloudflared (%s) ...", asset)
    # Written aside and renamed: a half-downloaded binary that exists is
    # worse than one that does not, because the next launch would find it
    # and try to run it.
    partial = binary.with_name(binary.name + ".part")
    urllib.request.urlretrieve(DOWNLOAD % asset, partial)
    if sys.platform != "win32":
        partial.chmod(0o755)
    os.replace(partial, binary)
    return binary


def start_tunnel(port: int):
    """Start a quick tunnel to `port`; return (process, url).

    Raises if cloudflared does not name a URL within TUNNEL_TIMEOUT. The
    caller decides what that means — serve() logs it and carries on with
    the local URL, because a pod nobody can reach from outside is still a
    pod somebody can reach through RunPod's own proxy.
    """
    binary = cloudflared_binary()
    proc = subprocess.Popen(
        [str(binary), "tunnel", "--url", "http://127.0.0.1:%d" % port,
         "--no-autoupdate"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + TUNNEL_TIMEOUT
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        if not line:
            break
        found = TUNNEL_URL.search(line)
        if found:
            # cloudflared keeps writing to this pipe for the life of the
            # tunnel. Nothing reads it after this point, so a full pipe
            # buffer would block the tunnel process itself — drained on a
            # daemon thread rather than closed, because closing it makes
            # cloudflared exit.
            threading.Thread(target=_drain, args=(proc,), daemon=True).start()
            return proc, found.group(0)
    proc.terminate()
    raise RuntimeError(
        "cloudflared did not produce a tunnel URL within %ds — restart the "
        "app to retry" % TUNNEL_TIMEOUT
    )


def _drain(proc) -> None:
    """Read and discard cloudflared's output so its pipe cannot fill."""
    try:
        for _line in proc.stdout:
            pass
    except Exception:                        # noqa: BLE001
        pass


def announce(url: str, token: str) -> None:
    """Print the one line a customer needs, the way launch_ui did.

    The token rides in the **fragment**. Fragments are never sent to
    servers, so it stays out of Cloudflare's logs, out of every proxy in
    between and out of `Referer` headers — a query parameter would be in
    all three. The SPA reads it from `location.hash`, trades it for an
    HttpOnly cookie and calls `history.replaceState` to take it out of
    the address bar.
    """
    line = "%s/#k=%s" % (url.rstrip("/"), token)
    print("\n" + "=" * 60, flush=True)
    print(">>> OPEN THE UI HERE: %s" % line, flush=True)
    print("=" * 60 + "\n", flush=True)


def serve(port: int = 7860, host: str = "0.0.0.0", tunnel: bool = True,
          reload: bool = False) -> None:
    """Run the app until Ctrl-C."""
    import uvicorn

    import api

    app = api.create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="info",
                            access_log=False, reload=reload)
    server = uvicorn.Server(config)

    # Started before the tunnel so cloudflared connects to something that
    # already answers; the tunnel's own health check fails otherwise and
    # the first visitor gets a 502 the app never saw.
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    _wait_for(server)

    proc, url = None, "http://127.0.0.1:%d" % port
    if tunnel:
        try:
            proc, url = start_tunnel(port)
        except Exception as exc:             # noqa: BLE001
            log.warning("Could not start the Cloudflare tunnel (%s) — the "
                        "app is still running on %s", exc, url)
    announce(url, api.TOKEN)

    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        server.should_exit = True
        if proc is not None:
            proc.terminate()


def _wait_for(server, timeout: float = 30.0) -> None:
    """Block until uvicorn is accepting connections, or give up quietly."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if getattr(server, "started", False):
            return
        time.sleep(0.1)
    log.warning("uvicorn did not report itself started within %.0fs — "
                "carrying on anyway", timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Ember web app.")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--no-tunnel", action="store_true",
        help="skip the Cloudflare quick tunnel and serve locally only",
    )
    args = parser.parse_args()
    serve(port=args.port, host=args.host, tunnel=not args.no_tunnel)


if __name__ == "__main__":
    main()
