"""Build aws_sniper.zip for friends: the page, ready to run with start.bat.

    python aws_sniper/make_zip.py        ->  aws_sniper.zip next to this folder

Holds only what the page needs. Never the .env (keys), the keys/ folder
(.pem files), or the per-user state (history, settings, downloads,
known_hosts, and the Image and Text to prompt tabs' history.json and
images/: only their base prompts and negative.json go in). The Runpod page's downloader.py and
ember_token.py go in beside server.py, which looks in its own folder first.
"""

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNPOD = HERE.parent / "runpod_sniper"
OUT = HERE.parent / "aws_sniper.zip"

FILES = ["server.py", "sniper.py", "imgprompt.py", "txtprompt.py", "index.html", "tokens.css", "userdata.sh",
         "requirements.txt", ".env.example", "README.md", "start.bat",
         "img2prompt/negative.json"] + sorted(
            "img2prompt/" + p.name for p in (HERE / "img2prompt").glob("base_prompt_gpt_*.md")) + sorted(
            "txt2prompt/" + p.name for p in (HERE / "txt2prompt").glob("base_prompt_*.md"))
FROM_RUNPOD = ["downloader.py", "ember_token.py"]
NEVER = {".env", "history.json", "settings.json", "downloads.json", "known_hosts", "img2prompt/history.json",
         "txt2prompt/history.json"}


def main() -> None:
    entries = [(HERE / f, f) for f in FILES] + [(RUNPOD / f, f) for f in FROM_RUNPOD]
    for src, name in entries:
        if not src.is_file():
            raise SystemExit("Missing %s" % src)
        assert name not in NEVER
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for src, name in entries:
            z.write(src, "aws_sniper/" + name)
    print("Wrote %s (%d files): %s" % (OUT, len(entries), ", ".join(n for _s, n in entries)))


if __name__ == "__main__":
    main()
