"""Run Ember headless for img2prompt: every startup step, then the API on
127.0.0.1 with no Cloudflare tunnel.

    python img2prompt/serve_backend.py [--port 7870]

`python app.py` always serves on 0.0.0.0:7860 and always opens a tunnel.
EMBER_SKIP_LAUNCH runs every step of ember.main.main() except the serve,
so this calls serve() itself with the address the loop wants. Port 7870,
not 7860: a 127.0.0.1:7860 listener takes the browser away from an
ember.exe already serving 0.0.0.0:7860.

Everything else is read from the environment exactly as app.py reads it:
EMBER_BASE_DIR, EMBER_LICENSE_KEY, EMBER_NODE_TAG, HF_TOKEN and the rest.
img2prompt/.env (KEY=value lines, git-ignored) fills in whatever the
environment does not set, so a Claude Code chat — whose shell never saw
the user's terminal — can start this too.
"""

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


load_env_file(HERE / ".env")
# ember.settings reads the environment into module constants on import,
# so the flag has to be set before anything imports it.
os.environ["EMBER_SKIP_LAUNCH"] = "1"
sys.path.insert(0, str(HERE.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7870)
    args = parser.parse_args()
    if args.port == 7860:
        parser.error("7860 is the user's own Ember; pick another port")

    from ember import main as ember_main
    ember_main.main()

    from ember.web import serve
    serve.serve(port=args.port, host="127.0.0.1", tunnel=False)


if __name__ == "__main__":
    main()
