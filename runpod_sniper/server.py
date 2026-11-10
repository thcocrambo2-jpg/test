"""The Runpod GPU sniper page, on http://127.0.0.1:7895.

    python runpod_sniper/server.py [--port 7895] [--dry-run] [--no-sound]

Pick a preset, press Snipe, and a watcher on this server sends pod-create
requests back to back until one wins a GPU (README.md says why only the
create call can win). The page polls /api/status, so a watcher keeps going
when the page is refreshed or closed; one watcher per account at a time.

The API keys come from runpod_sniper/.env (RUNPOD_API_KEY, or several
RUNPOD_API_KEY_<NAME>). They never leave this process: not to the page,
not to the log, not into an error message.

--dry-run swaps Runpod for fake.py and ignores .env, so nothing real is
ever created or billed.
"""

import argparse
import json
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import downloader  # noqa: E402  (this folder's own modules)
import health  # noqa: E402

HERE = Path(__file__).resolve().parent
# A copy of webui/src/theme/tokens.css, kept in this folder so the sniper
# works on its own (e.g. unzipped on another machine).
TOKENS_CSS = HERE / "tokens.css"
ENV_FILE = HERE / ".env"
GPUS_FILE = HERE / "gpus.json"
PRESETS_FILE = HERE / "presets.json"

V1_PODS = "https://rest.runpod.io/v1/pods"
V2 = "https://api.runpod.io/v2"
USER_AGENT = "curl/8.5.0"      # urllib's own User-Agent gets a Cloudflare 403

GAP = 0.7                      # seconds between create attempts, per key
WORKERS = 3
PAUSE_429 = 15
LIMIT_MINUTE, LIMIT_HOUR = 90, 1800

# From GET /v2/catalog/datacenters, 2026-11-04.
DATA_CENTERS = {
    "Europe": ["EU-CZ-1", "EU-FR-1", "EU-NL-1", "EU-RO-1", "EU-SE-1",
               "EUR-IS-1", "EUR-IS-2", "EUR-IS-3", "EUR-IS-4", "EUR-IS-5",
               "EUR-NO-1", "EUR-NO-2"],
    "North America": ["CA-MTL-3", "CA-MTL-4", "US-CA-2", "US-CO-1", "US-GA-2",
                      "US-IL-1", "US-KS-2", "US-MD-1", "US-MO-1", "US-MO-2",
                      "US-NC-1", "US-NC-2", "US-NE-1", "US-PA-1", "US-TX-3",
                      "US-TX-4", "US-WA-1", "US-WA-2"],
    "Asia / Oceania": ["AP-IN-1", "AP-JP-1", "OC-AU-1"],
}
ALL_DCS = {d for dcs in DATA_CENTERS.values() for d in dcs}
# CUDA versions Runpod hosts reported in GET /v2/catalog/gpus, 2026-11-05.
# A stock refresh adds any new one the catalog reports.
CUDA_VERSIONS = ["12.2", "12.4", "12.5", "12.6", "12.7", "12.8", "12.9",
                 "13.0", "13.1", "13.2", "13.3", "13.4"]
# v1's dataCenterIds enum lags behind: these answer a 400 schema error.
V1_REJECTS = {"EUR-IS-4", "EUR-IS-5", "EUR-NO-2"}

V1_NO_CAPACITY = "no instances currently available"
V2_NO_CAPACITY = "no longer any instances available"

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/()+,·-]{0,99}$")
POD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
CUDA_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")
LICENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
LICENCE_ENV = "EMBER_LICENSE_KEY"
POD_ID_RE = re.compile(r"^[a-z0-9]{6,32}$")
TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
MACHINE_RE = re.compile(r"^[a-z0-9]{6,40}$")

DRY_RUN = False
SOUND = True
FAKE = None                    # fake.FakeRunpod in a dry run
PORT = 7895

_lock = threading.Lock()
_accounts: dict = {}           # name -> Account
_watchers: dict = {}           # account name -> Watcher (latest one)
_downloads: dict = {}          # pod id -> downloader.Download
DOWNLOADS_FILE = HERE / "downloads.json"   # running downloads, resumed on restart
_pick_lock = threading.Lock()


# ── auto download ───────────────────────────────────────────────────


def _save_downloads() -> None:
    """Keep the running downloads, so a restarted server resumes them.
    Not in a dry run: its pods are gone when it stops."""
    if DRY_RUN:
        return
    with _lock:
        active = [d.config() for d in _downloads.values() if d.status != "stopped"]
    tmp = DOWNLOADS_FILE.with_suffix(".json.part")
    tmp.write_text(json.dumps(active, indent=1), encoding="utf-8")
    tmp.replace(DOWNLOADS_FILE)


def _resume_downloads() -> None:
    if DRY_RUN:
        return
    try:
        saved = json.loads(DOWNLOADS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for cfg in saved:
        try:
            d = downloader.Download(**cfg)
            d.start()
            d.event("Resumed after a server restart.")
        except (OSError, TypeError) as exc:
            print("Could not resume the download for %s: %s" % (cfg.get("pod_id"), exc))
            continue
        _downloads[d.pod_id] = d
    if _downloads:
        print("Resumed %d download(s)." % len(_downloads))


def _clean_folder(raw) -> Path:
    folder = Path(str(raw or "").strip().strip('"')).expanduser()
    if not str(raw or "").strip():
        raise ValueError("Choose a folder for this pod first.")
    if not folder.is_absolute():
        raise ValueError("Give the full path of the folder, e.g. C:\\Users\\you\\Downloads\\pod.")
    if folder.exists() and not folder.is_dir():
        raise ValueError("%s is a file, not a folder." % folder)
    return folder


def _pick_folder(initial: str):
    """The system's folder dialog, on this PC (the server runs here).
    None if cancelled; raises if there is no tkinter."""
    import tkinter as tk
    from tkinter import filedialog
    with _pick_lock:                          # one dialog at a time
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            start = initial if initial and Path(initial).is_dir() else str(Path.home())
            chosen = filedialog.askdirectory(parent=root, initialdir=start,
                                             title="Folder for this pod's downloads",
                                             mustexist=False)
        finally:
            root.destroy()
    return str(Path(chosen)) if chosen else None


def _open_folder(folder: Path) -> None:
    import os
    import subprocess
    if sys.platform == "win32":
        os.startfile(str(folder))             # noqa: S606 (local, user's own folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _gpus() -> list:
    return _read_json(GPUS_FILE, {}).get("gpus", [])


def _presets() -> list:
    return _read_json(PRESETS_FILE, [])


def _write_presets(presets: list) -> None:
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8", newline="\n")


# ── accounts and the shared per-key budget ──────────────────────────


class Account:
    """One API key: its pacing slot, its 429 pause and its request count.

    Runpod's limits are per key, so everything that spends requests —
    the watcher's creates, the pods list, the stock refresh — goes
    through here.
    """

    def __init__(self, name: str, key: str):
        self.name = name
        self.key = key
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.paused_until = 0.0
        self.calls = deque()   # timestamps of our own requests, last hour
        self.header = {}       # last RateLimit headers, parsed
        self.header_at = None

    def scrub(self, text: str) -> str:
        """Belt and braces: a reply should never echo the API key or the
        licence key, but if one ever did, it must not reach the page."""
        for secret in (self.key, _licence_key()):
            if secret:
                text = text.replace(secret, "***")
        return text

    def take_slot(self, stop: threading.Event) -> bool:
        """Wait for this key's next create slot. False if stopped first."""
        with self.lock:
            at = max(self.next_slot, self.paused_until, time.time())
            self.next_slot = at + GAP
        while not stop.is_set():
            left = at - time.time()
            if left <= 0:
                return True
            stop.wait(min(left, 0.25))
        return False

    def pause(self, seconds: float) -> None:
        with self.lock:
            self.paused_until = max(self.paused_until, time.time() + seconds)

    def budget(self) -> dict:
        now = time.time()
        with self.lock:
            while self.calls and self.calls[0] < now - 3600:
                self.calls.popleft()
            minute = sum(1 for t in self.calls if t > now - 60)
            hour = len(self.calls)
            header = dict(self.header)
            paused = max(0.0, self.paused_until - now)
        return {"minute_used": minute, "hour_used": hour,
                "minute_limit": LIMIT_MINUTE, "hour_limit": LIMIT_HOUR,
                "header": header, "paused_for": round(paused, 1)}

    def _note_headers(self, headers) -> None:
        """Keep what the RateLimit headers say is left.

        v2 sends the IETF draft form, e.g.
            RateLimit: "minute";r=71;t=38, "hour";r=1622;t=2410
        v1 has used x-ratelimit-* in the past; take whichever comes.
        """
        found = {}
        for name, value in headers.items():
            low = name.lower()
            if low == "ratelimit":
                for part in value.split(","):
                    m = re.match(r'\s*"?([A-Za-z0-9_-]+)"?\s*;(.*)', part)
                    if not m:
                        continue
                    params = dict(re.findall(r"(\w+)=([0-9]+)", m.group(2)))
                    if "r" in params:
                        found[m.group(1)] = {"remaining": int(params["r"]),
                                             "reset": int(params.get("t", 0))}
            elif low in ("x-ratelimit-remaining", "ratelimit-remaining"):
                if value.strip().isdigit():
                    found.setdefault("window", {})["remaining"] = int(value)
            elif low in ("x-ratelimit-reset", "ratelimit-reset"):
                if value.strip().isdigit():
                    found.setdefault("window", {})["reset"] = int(value)
        if found:
            with self.lock:
                self.header = found
                self.header_at = time.time()

    def request(self, method: str, url: str, body=None, timeout=30) -> tuple:
        """(status, text). Never raises for an HTTP status."""
        with self.lock:
            self.calls.append(time.time())
        if FAKE is not None:
            code, text, headers = FAKE.request(self.key, method, url, body)
        else:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(url, method=method, data=data, headers={
                "Authorization": "Bearer " + self.key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    code, headers = res.status, dict(res.headers)
                    text = res.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                code, headers = exc.code, dict(exc.headers or {})
                text = exc.read().decode("utf-8", "replace")
            except (urllib.error.URLError, OSError) as exc:
                return 0, "network error: %s" % getattr(exc, "reason", exc)
        self._note_headers(headers)
        if code == 429:
            self.pause(PAUSE_429)
        return code, self.scrub(text)

    def stream(self, url: str, headers: dict = None, idle: float = 300):
        """A long-lived GET (the logs SSE stream), line by line. One request
        against the budget however long it stays open. Raises on an HTTP
        error or when the stream goes quiet for `idle` seconds."""
        with self.lock:
            self.calls.append(time.time())
        secrets = [s for s in (self.key, _licence_key()) if s]
        if FAKE is not None:
            lines = FAKE.stream(self.key, url, headers or {})
        else:
            lines = self._real_stream(url, headers or {}, idle)
        for line in lines:
            for s in secrets:
                line = line.replace(s, "***")
            yield line

    def _real_stream(self, url, headers, idle):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.key, "User-Agent": USER_AGENT, **headers})
        try:
            res = urllib.request.urlopen(req, timeout=20)   # a short connect timeout
        except urllib.error.HTTPError as exc:
            self._note_headers(dict(exc.headers or {}))
            if exc.code == 429:
                self.pause(PAUSE_429)
            raise ValueError("logs answered HTTP %s" % exc.code)
        with res:
            self._note_headers(dict(res.headers))
            try:                                  # then a long one between lines
                res.fp.raw._sock.settimeout(idle)
            except AttributeError:
                pass
            for raw in res:
                yield raw.decode("utf-8", "replace")


# The placeholders .env.example ships with. A .env copied from it and not
# filled in must read as "no key", not as a key Runpod will answer 401 to.
PLACEHOLDER_RE = re.compile(r"^(rpa_x+|EMBER-X{4}-X{4})$", re.I)


def _env_values() -> dict:
    """Every NAME=value in .env, placeholders left out."""
    values = {}
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name.startswith("export "):
            name = name[7:].strip()
        if value and not PLACEHOLDER_RE.match(value):
            values[name] = value
    return values


def _parse_env() -> dict:
    keys = {}
    for name, value in _env_values().items():
        if name == "RUNPOD_API_KEY":
            keys["default"] = value
        elif name.startswith("RUNPOD_API_KEY_") and len(name) > 15:
            keys[name[15:].lower()] = value
    return keys


# ── the licence key ─────────────────────────────────────────────────
# The pod's EMBER_LICENSE_KEY lives in .env — never in presets.json, which
# is meant to be shared, so one preset can run with different keys. The
# page shows it, can replace it (POST /api/licence), and sends the box's
# value with each snipe. A dry run keeps it in memory and leaves .env alone.

_dry_licence = "EMBER-DRY-RUN"


def _licence_key() -> str:
    if DRY_RUN:
        return _dry_licence
    return _env_values().get(LICENCE_ENV, "")


def _set_licence_key(key: str) -> None:
    """Write EMBER_LICENSE_KEY into .env, keeping every other line."""
    global _dry_licence
    if DRY_RUN:
        _dry_licence = key
        return
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    pattern = re.compile(r"^\s*(export\s+)?%s\s*=" % LICENCE_ENV)
    out, done = [], False
    for line in lines:
        if pattern.match(line):
            if key and not done:
                out.append("%s=%s" % (LICENCE_ENV, key))
                done = True
            continue
        out.append(line)
    if key and not done:
        out.append("%s=%s" % (LICENCE_ENV, key))
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def _load_accounts() -> None:
    """(Re)read .env, keeping each account's request history."""
    keys = ({"dry-main": "dry-main", "dry-alt": "dry-alt"} if DRY_RUN
            else _parse_env())
    with _lock:
        for name, key in keys.items():
            if name in _accounts:
                _accounts[name].key = key
            else:
                _accounts[name] = Account(name, key)
        for name in list(_accounts):
            if name not in keys and name not in _watchers:
                del _accounts[name]


def _account(name) -> Account:
    with _lock:
        acct = _accounts.get(str(name or ""))
    if not acct:
        raise ValueError("No account %r in runpod_sniper/.env." % name)
    return acct


def _detail(text: str) -> str:
    """The human part of a Runpod error reply."""
    try:
        obj = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(obj, dict):
        for k in ("detail", "error", "message", "title"):
            if obj.get(k):
                v = obj[k]
                return (v if isinstance(v, str) else json.dumps(v))[:300]
    return text[:300]


# ── presets ─────────────────────────────────────────────────────────


def _whole(v: float):
    """15.0 → 15, so a saved preset reads like a hand-written one."""
    return int(v) if float(v).is_integer() else v


def _clean_preset(p: dict) -> dict:
    """A preset from the page, checked field by field. Raises ValueError."""
    if not isinstance(p, dict):
        raise ValueError("Preset must be an object.")

    def num(key, lo, hi, cast=int, default=0):
        v = p.get(key, default)
        if v in (None, ""):
            v = default
        v = cast(v)
        if not lo <= v <= hi:
            raise ValueError("%s must be between %s and %s." % (key, lo, hi))
        return v

    gpus = [str(g).strip() for g in p.get("gpus") or [] if str(g).strip()]
    if not gpus:
        raise ValueError("Pick at least one GPU.")
    if len(gpus) > 10 or any(len(g) > 80 for g in gpus):
        raise ValueError("Too many GPUs, or a GPU id that is too long.")
    cloud = str(p.get("cloud") or "").upper()
    if cloud not in ("COMMUNITY", "SECURE"):
        raise ValueError("Cloud must be COMMUNITY or SECURE.")
    template = str(p.get("template_id") or "").strip()
    if template and not TEMPLATE_RE.match(template):
        raise ValueError("Template id looks wrong.")
    dcs = [str(d) for d in p.get("data_centers") or []]
    unknown = [d for d in dcs if d not in ALL_DCS]
    if unknown:
        raise ValueError("Unknown data center(s): %s" % ", ".join(unknown))
    cudas = [str(c).strip() for c in p.get("cuda_versions") or [] if str(c).strip()]
    if any(not CUDA_RE.match(c) for c in cudas):
        raise ValueError("CUDA versions look like 13.0, 12.8.")
    pod_name = str(p.get("pod_name") or "sniped").strip()
    if not POD_NAME_RE.match(pod_name):
        raise ValueError("Pod name: letters, digits, . _ - (max 60).")
    api = str(p.get("api") or "auto")
    if api not in ("auto", "v1", "v2"):
        raise ValueError("API must be auto, v1 or v2.")
    # The location guard: Community hosts have no data center, so the
    # host's country (from its machine) is what can be filtered on.
    countries = p.get("countries") or []
    if isinstance(countries, str):
        countries = re.split(r"[\s,;]+", countries)
    countries = sorted({str(c).strip().upper() for c in countries if str(c).strip()})
    if any(not COUNTRY_RE.match(c) for c in countries) or len(countries) > 60:
        raise ValueError("Countries are two-letter codes, e.g. FR, US, SE.")
    country_mode = str(p.get("country_mode") or "block")
    if country_mode not in ("block", "allow"):
        raise ValueError("Country mode must be block or allow.")
    country_action = str(p.get("country_action") or "terminate")
    if country_action not in ("terminate", "warn"):
        raise ValueError("Country action must be terminate or warn.")
    return {
        "name": str(p.get("name") or "").strip(),
        "gpus": gpus,
        "gpu_count": num("gpu_count", 1, 8, default=1),
        "cloud": cloud,
        "template_id": template,
        "container_disk_gb": num("container_disk_gb", 5, 2000, default=50),
        "volume_gb": num("volume_gb", 0, 4000),
        "min_download_mbps": num("min_download_mbps", 0, 100000),
        "min_vcpu_per_gpu": num("min_vcpu_per_gpu", 0, 256),
        "min_ram_per_gpu": num("min_ram_per_gpu", 0, 2048),
        "data_centers": sorted(set(dcs), key=dcs.index),
        "cuda_versions": cudas,
        "public_ip": bool(p.get("public_ip")),
        "max_price": num("max_price", 0.01, 100, cast=float, default=1.0),
        "minutes": _whole(num("minutes", 1, 600, cast=float, default=15)),
        "pod_name": pod_name,
        "api": api,
        "countries": countries,
        "country_mode": country_mode,
        "country_action": country_action,
    }


def _plan(p: dict, licence: str = "") -> dict:
    """Which create API to use for a preset, and the request body/bodies.

    v1 is the only one with a bandwidth filter (and a public-IP filter),
    so those force it. Otherwise v1 is used when every chosen data
    center is one v1 accepts, and v2 — which takes every data center
    but filters on bandwidth not at all — for the rest.
    """
    notes = []
    # The only env override: the licence key, never from the preset. The
    # template's own env (HF_TOKEN, CIVITAI_TOKEN) is merged in by Runpod.
    env = {LICENCE_ENV: licence} if licence else {}
    dcs = p["data_centers"]
    api = p["api"]
    if api == "auto":
        if p["min_download_mbps"] or p["public_ip"]:
            api = "v1"
        elif dcs and not (set(dcs) & V1_REJECTS):
            api = "v1"
        else:
            api = "v2"
    if api == "v1":
        dropped = [d for d in dcs if d in V1_REJECTS]
        if dropped:
            dcs = [d for d in dcs if d not in V1_REJECTS]
            notes.append("v1 does not take %s — left out." % ", ".join(dropped))
            if not dcs:
                raise ValueError("Every chosen data center is one v1 rejects, "
                                 "and the bandwidth / public-IP filter needs v1. "
                                 "Drop the filter (v2) or pick other data centers.")
        body = {
            "name": p["pod_name"],
            "cloudType": p["cloud"],
            "computeType": "GPU",
            "gpuTypeIds": p["gpus"],
            "gpuCount": p["gpu_count"],
            "containerDiskInGb": p["container_disk_gb"],
            "volumeInGb": p["volume_gb"],
        }
        if p["template_id"]:
            body["templateId"] = p["template_id"]
        if env:
            body["env"] = env
        if p["min_download_mbps"]:
            body["minDownloadMbps"] = p["min_download_mbps"]
        if p["min_vcpu_per_gpu"]:
            body["minVCPUPerGPU"] = p["min_vcpu_per_gpu"]
        if p["min_ram_per_gpu"]:
            body["minRAMPerGPU"] = p["min_ram_per_gpu"]
        if dcs:
            body["dataCenterIds"] = dcs
        if p["cuda_versions"]:
            body["allowedCudaVersions"] = p["cuda_versions"]
        if p["public_ip"]:
            body["supportPublicIp"] = True
        if len(p["gpus"]) > 1:
            notes.append("v1 takes the whole GPU list in one request.")
        return {"api": "v1", "url": V1_PODS, "bodies": [body], "notes": notes}

    if p["public_ip"]:
        notes.append("v2 has no public-IP filter — ignored.")
    bodies = []
    for gpu in p["gpus"]:        # v2 takes one GPU id: alternate through them
        g = {"id": gpu, "count": p["gpu_count"]}
        if p["min_ram_per_gpu"]:
            g["minRamPerGpu"] = p["min_ram_per_gpu"]
        if p["min_vcpu_per_gpu"]:
            g["minVcpuCountPerGpu"] = p["min_vcpu_per_gpu"]
        if p["cuda_versions"]:   # replaces the template's CUDA range entirely
            g["allowedCudaVersions"] = p["cuda_versions"]
        body = {"name": p["pod_name"], "cloud": p["cloud"], "gpu": g,
                "disk": p["container_disk_gb"], "startSsh": True}
        if p["template_id"]:
            body["templateId"] = p["template_id"]
        if env:
            body["env"] = env
        if dcs:
            body["dataCenterIds"] = dcs
        if p["volume_gb"]:
            body["mounts"] = {"persistent": {"size": max(10, p["volume_gb"]),
                                             "path": "/workspace"}}
        bodies.append(body)
    if len(bodies) > 1:
        notes.append("v2 takes one GPU per request — alternating %d GPUs."
                     % len(bodies))
    return {"api": "v2", "url": V2 + "/pods", "bodies": bodies, "notes": notes}


# ── pods ────────────────────────────────────────────────────────────


def _pod_view(pod: dict) -> dict:
    """A v2 pod, cut down to what the page shows."""
    gpu = pod.get("gpu") or {}
    ssh = pod.get("ssh") or {}
    cmd = None
    for kind in ("proxy", "direct"):
        if isinstance(ssh.get(kind), dict) and ssh[kind].get("command"):
            cmd = ssh[kind]["command"]
            break
    pid = pod.get("id")
    return {
        "id": pid, "name": pod.get("name"), "status": pod.get("status"),
        "actions": pod.get("actions") or [], "cloud": pod.get("cloud"),
        "gpu": gpu.get("id"), "gpu_count": gpu.get("count"),
        "ram": gpu.get("memory"), "vcpu": gpu.get("vcpuCount"),
        "cost": pod.get("cost"), "dc": pod.get("dataCenterId"),
        "cuda": pod.get("cudaVersion"), "disk": pod.get("disk"),
        "volume": ((pod.get("mounts") or {}).get("persistent") or {}).get("size"),
        "created": pod.get("createdAt"),
        "app_url": "https://%s-7860.proxy.runpod.net" % pid if pid else None,
        "ssh": cmd,
    }


def _health_pod(acct: Account, pid: str):
    """The pod for the health watcher: a view, {"gone": True} once it is
    terminated (404), or None when Runpod did not answer."""
    code, text = acct.request("GET", "%s/pods/%s" % (V2, pid))
    if code == 404:
        return {"gone": True}
    if code == 200:
        try:
            return _pod_view(json.loads(text))
        except ValueError:
            pass
    return None


def _get_machine(acct: Account, pid: str):
    """The pod's host, from v1 with includeMachine (v2 has none of it):
    country, machine id, Runpod's own disk/network figures, and the last
    status change ("Exited by user: ..."). None when unavailable."""
    code, text = acct.request("GET", "%s/%s?includeMachine=true" % (V1_PODS, pid))
    if code != 200:
        return None
    try:
        pod = json.loads(text)
    except ValueError:
        return None
    m = pod.get("machine") or {}
    return {"country": m.get("location") or None, "machine": pod.get("machineId") or None,
            "host_dl_mbps": m.get("maxDownloadSpeedMbps"),
            "host_disk_mbs": m.get("diskThroughputMBps"),
            "dc": m.get("dataCenterId"), "secure": m.get("secureCloud"),
            "last_status": pod.get("lastStatusChange")}


def _app_up(pid: str) -> bool:
    """Does the pod's app answer on port 7860 (through Runpod's proxy)? A
    401 counts: the app is up, it only wants its access key."""
    path = downloader.API + "/gallery?limit=1"
    try:
        if downloader.FAKE_APP is not None:
            downloader.FAKE_APP(pid, "GET", path, None).read()
            return True
        req = urllib.request.Request(downloader.proxy_url(pid) + path,
                                     headers={"User-Agent": downloader.USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 401
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _ext_get(url: str, headers: dict) -> tuple:
    """GET outside Runpod (Docker Hub manifest, HF tree): (code, headers, text)."""
    if FAKE is not None:
        return FAKE.external(url, headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, dict(res.headers), res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, {}, str(getattr(exc, "reason", exc))


def _pod_action(acct: Account, pid: str, action: str) -> tuple:
    code, text = acct.request("POST", "%s/pods/%s/action" % (V2, pid),
                              {"action": action})
    if action == "terminate" and code in (404, 405):
        code, text = acct.request("DELETE", "%s/pods/%s" % (V2, pid))
    return code, text


MAC_SOUNDS = ["/System/Library/Sounds/Sosumi.aiff", "/System/Library/Sounds/Funk.aiff",
              "/System/Library/Sounds/Glass.aiff"]
ALARM_SECONDS = 10       # how long the Mac alarm repeats (Windows: Alarm01 twice, ~11 s)


def _alarm() -> None:
    """The long alarm: a won pod, and a pod check warning (errors, restarts).
    Windows: Alarm01.wav twice. Mac: a system sound repeated for ~10 s."""
    if not SOUND:
        return
    if sys.platform == "win32":
        import winsound
        wav = r"C:\Windows\Media\Alarm01.wav"
        for _ in range(2):
            winsound.PlaySound(wav, winsound.SND_FILENAME)
        return
    if sys.platform == "darwin":
        import subprocess
        sound = next((f for f in MAC_SOUNDS if Path(f).exists()), None)
        if sound:
            end = time.time() + ALARM_SECONDS
            try:
                while time.time() < end:
                    subprocess.run(["afplay", sound], timeout=10,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except (OSError, subprocess.SubprocessError):
                pass                               # no afplay: the terminal bell below
    sys.stdout.write("\a\a")
    sys.stdout.flush()


# ── the watcher ─────────────────────────────────────────────────────


class Watcher:
    def __init__(self, acct: Account, preset: dict, plan: dict, licence: str = ""):
        self.acct = acct
        self.licence = licence
        self.preset = preset
        self.plan = plan
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.started = time.time()
        self.deadline = self.started + preset["minutes"] * 60
        self.finished = None
        self.status = "running"          # running/won/stopped/timeout/error
        self.error = None
        self.tries = 0
        self.codes = {}
        self.last = None
        self.pod = None
        self.events = deque(maxlen=40)
        self.threads = []
        # The location guard reads the won pod's machine before keeping it.
        # Meanwhile no worker creates (checking), and a second win waits for
        # the first one's verdict (guard_lock) instead of racing it.
        self.checking = False
        self.guard_lock = threading.Lock()
        for n in plan["notes"]:
            self.event(n)

    def event(self, text: str) -> None:
        with self.lock:
            self.events.append([time.time(), text])

    def start(self) -> None:
        self.event("Sniping %s %s on %s with %s, up to %g min, max $%.2f/hr."
                   % (self.preset["cloud"].title(),
                      " / ".join(self.preset["gpus"]), self.acct.name,
                      self.plan["api"], self.preset["minutes"],
                      self.preset["max_price"]))
        for i in range(WORKERS):
            t = threading.Thread(target=self._work, args=(i,), daemon=True)
            self.threads.append(t)
            t.start()
        threading.Thread(target=self._reaper, daemon=True).start()

    def stop(self, why="stopped", error=None) -> None:
        with self.lock:
            if self.status == "running":
                self.status, self.error = why, error
                self.finished = time.time()
        self.stop_event.set()

    def _reaper(self) -> None:
        while not self.stop_event.wait(1):
            if time.time() > self.deadline:
                self.event("Time is up — no GPU in %g min." % self.preset["minutes"])
                self.stop("timeout")
        for t in self.threads:
            t.join(timeout=40)

    def _terminate(self, pid: str, why: str) -> None:
        code, text = _pod_action(self.acct, pid, "terminate")
        ok = code in (200, 202, 204)
        self.event("%s pod %s — %s" % (why, pid, "terminated" if ok else
                   "TERMINATE FAILED (%s %s): delete it by hand!"
                   % (code, _detail(text))))

    def _work(self, i: int) -> None:
        bodies = self.plan["bodies"]
        while not self.stop_event.is_set():
            if not self.acct.take_slot(self.stop_event):
                return
            while self.checking and not self.stop_event.is_set():
                self.stop_event.wait(0.2)
            if self.stop_event.is_set() or time.time() > self.deadline:
                return
            with self.lock:
                n = self.tries
                self.tries += 1
            body = bodies[n % len(bodies)]
            code, text = self.acct.request("POST", self.plan["url"], body)
            with self.lock:
                self.codes[str(code)] = self.codes.get(str(code), 0) + 1
                self.last = {"at": time.time(), "code": code,
                             "text": _detail(text)[:200],
                             "gpu": (body.get("gpu") or {}).get("id")
                                    or ", ".join(body.get("gpuTypeIds", []))}
            if code in (200, 201):
                self._won(text)
                continue
            if code == 429:
                self.event("429 rate limited — pausing %d s." % PAUSE_429)
                continue
            if self._no_capacity(code, text):
                continue
            if code == 0 or code >= 500 or code == 403 and "cloudflare" in text.lower():
                self.event("Transient %s: %s" % (code or "network", _detail(text)))
                continue
            self.event("Stopped on a real error %s: %s" % (code, _detail(text)))
            self.stop("error", "%s %s" % (code, _detail(text)))
            return

    def _no_capacity(self, code: int, text: str) -> bool:
        low = text.lower()
        if self.plan["api"] == "v1":
            return code == 500 and V1_NO_CAPACITY in low
        return code == 400 and V2_NO_CAPACITY in low

    def _won(self, text: str) -> None:
        try:
            pod = json.loads(text)
        except ValueError:
            pod = {}
        pid = str(pod.get("id") or "")
        if not POD_ID_RE.match(pid):
            self.event("Create answered without a pod id: %s" % text[:200])
            return
        cost = pod.get("costPerHr") if self.plan["api"] == "v1" else pod.get("cost")
        if cost is None:
            cost = pod.get("adjustedCostPerHr") or pod.get("cost")
        with self.lock:
            extra = self.status != "running" or self.pod is not None
        if extra:
            self._terminate(pid, "Race guard: extra")
            return
        if cost is not None and float(cost) > self.preset["max_price"]:
            self._terminate(pid, "Price guard: $%.3f/hr > $%.2f —"
                            % (float(cost), self.preset["max_price"]))
            return
        with self.guard_lock:
            with self.lock:
                extra = self.status != "running" or self.pod is not None
            if extra:
                self._terminate(pid, "Race guard: extra")
                return
            machine = self._location_guard(pid, pod)
            if machine is False:
                return                       # thrown back; keep sniping
            with self.lock:
                self.pod = {"id": pid, "name": pod.get("name"), "cost": cost,
                            "app_url": "https://%s-7860.proxy.runpod.net" % pid}
                self.status = "won"
                self.finished = time.time()
        self.stop_event.set()
        where = (" in %s" % machine["country"]) if machine and machine.get("country") else ""
        self.event("WON pod %s%s at $%s/hr after %d tries." % (pid, where, cost, self.tries))
        threading.Thread(target=_alarm, daemon=True).start()
        # The health watch reads the pod back from here on (status, CUDA,
        # host), and judges it: machine, logs, the app.
        health.watch(self.acct, pid, source="snipe", won_at=self.finished,
                     preset=self.preset, machine=machine or None)

    def _location_guard(self, pid: str, created: dict):
        """Read the won pod's machine and apply the location guard (the
        preset's countries, and the blocked-hosts list) when it is set to
        terminate. Returns the machine dict, None when there was nothing to
        check, or False when the pod was thrown back."""
        rules = {k: self.preset.get(k) for k in ("countries", "country_mode")}
        blocked = health.blocked_hosts()
        if self.preset.get("country_action") != "terminate" or not (rules["countries"] or blocked):
            return None
        self.checking = True
        try:
            machine = None
            for i in range(3):              # the machine may not be attached yet
                machine = _get_machine(self.acct, pid)
                if machine and machine.get("machine") and (machine.get("country")
                                                           or not rules["countries"]):
                    break
                time.sleep(1.0)
            verdict, why = health.guard_check(machine, rules, blocked)
        finally:
            self.checking = False
        if verdict == "block":
            self._terminate(pid, "Location guard: %s —" % why)
            return False
        if verdict == "unknown":
            self.event("Location guard: %s for %s — kept it." % (why, pid))
        return machine or {}

    def view(self) -> dict:
        now = time.time()
        pod = None
        with self.lock:
            if self.pod:
                pod = dict(self.pod)
        if pod:
            h = health.get(pod["id"])
            if h:
                hv = h.view()
                pod.update({k: v for k, v in hv["pod_view"].items() if v is not None})
                pod["health"] = hv
        with self.lock:
            return {
                "account": self.acct.name, "preset": self.preset,
                "licence": self.licence,
                "api": self.plan["api"], "status": self.status,
                "error": self.error, "started": self.started,
                "deadline": self.deadline, "finished": self.finished,
                "elapsed": (self.finished or now) - self.started,
                "tries": self.tries, "codes": dict(self.codes),
                "last": self.last, "pod": pod,
                "events": list(self.events),
                "rate": self.tries / max(1.0, (self.finished or now) - self.started)
                        * 60,
            }


# ── stock ───────────────────────────────────────────────────────────


def _stock(acct: Account) -> dict:
    out, cuda = {}, set(CUDA_VERSIONS)
    for cloud in ("COMMUNITY", "SECURE"):
        q = urlencode({"include": "AVAILABILITY", "product": "POD",
                       "cloud": cloud})
        code, text = acct.request("GET", "%s/catalog/gpus?%s" % (V2, q))
        if code != 200:
            raise ValueError("Catalog %s: %s %s" % (cloud, code, _detail(text)))
        rows = {}
        for g in json.loads(text).get("gpus", []):
            offered = g.get(cloud.lower(), True)
            price = (g.get("price") or {}).get(cloud.lower()) if offered else None
            rows[g.get("id")] = {"price": price or None,
                                 "availability": g.get("availability")}
            for c in g.get("cudaVersions") or []:
                v = str(c.get("version") or "")
                if CUDA_RE.match(v):
                    cuda.add(v)
        out[cloud] = rows
    out["cuda"] = sorted(cuda, key=lambda v: tuple(map(int, v.split("."))))
    return out


# ── HTTP ────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:    # the page polls every second
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code: int = 200) -> None:
        self.send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def error(self, message: str, code: int = 400) -> None:
        self.json({"error": message}, code)

    def local(self) -> bool:
        """Only this machine's own page: refuse other Host names (DNS
        rebinding) — a web page elsewhere must not be able to rent GPUs."""
        host = (self.headers.get("host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:
        if not self.local():
            return self.error("Forbidden.", 403)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path
        try:
            if path == "/":
                return self.send(200, (HERE / "index.html").read_bytes(),
                                 "text/html; charset=utf-8")
            if path == "/tokens.css":
                if not TOKENS_CSS.is_file():
                    return self.error("tokens.css is missing from %s — the page "
                                      "has no theme without it." % HERE, 404)
                css = TOKENS_CSS.read_bytes()
                return self.send(200, css, "text/css; charset=utf-8")
            if path == "/api/config":
                _load_accounts()
                with _lock:
                    names = sorted(_accounts)
                return self.json({
                    "accounts": names, "gpus": _gpus(),
                    "prices_read": _read_json(GPUS_FILE, {}).get("prices_read"),
                    "presets": _presets(), "data_centers": DATA_CENTERS,
                    "v1_rejects": sorted(V1_REJECTS), "dry_run": DRY_RUN,
                    "env_found": DRY_RUN or ENV_FILE.is_file(),
                    "licence": _licence_key() or None,
                    "cuda_versions": CUDA_VERSIONS})
            if path == "/api/status":
                with _lock:
                    watchers = list(_watchers.values())
                    accounts = list(_accounts.values())
                    downloads = list(_downloads.values())
                return self.json({
                    "watchers": [w.view() for w in watchers],
                    "budget": {a.name: a.budget() for a in accounts},
                    "downloads": [d.view() for d in downloads],
                    "health": health.views(),
                    "now": time.time(), "dry_run": DRY_RUN})
            if path == "/api/history":
                recs = health.history_load()
                return self.json({
                    "pods": recs[:300], "summary": health.history_summary(recs),
                    "baselines": health.baselines(recs, health.seed_baselines()),
                    "blocked": health.blocked_hosts(), "thresholds": health.T})
            if path == "/api/stock":
                acct = _account(query.get("account", [""])[0])
                return self.json(_stock(acct))
            if path == "/api/pods":
                acct = _account(query.get("account", [""])[0])
                code, text = acct.request("GET", V2 + "/pods")
                if code != 200:
                    return self.error("Runpod %s: %s" % (code, _detail(text)), 502)
                pods = [_pod_view(p) for p in json.loads(text).get("pods", [])]
                return self.json({"pods": pods})
        except ValueError as exc:
            return self.error(str(exc))
        self.error("Not found.", 404)

    def do_POST(self) -> None:
        # The custom header forces a CORS preflight, which is never
        # answered, so only this page's own fetch() can POST.
        if not self.local() or self.headers.get("x-sniper") != "1":
            return self.error("Forbidden.", 403)
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("content-length") or 0)
            if length > 200_000:
                return self.error("Too large.", 413)
            body = json.loads(self.rfile.read(length) or b"{}")

            if url.path == "/api/snipe":
                acct = _account(body.get("account"))
                preset = _clean_preset(body.get("preset"))
                licence = str(body.get("licence") or "").strip()
                if not licence or licence.upper() == "LICENSE_KEY":
                    return self.error("No licence key — fill in the licence key "
                                      "box (Save to .env keeps it).")
                if not LICENCE_RE.match(licence):
                    return self.error("A licence key is letters, digits, - _ . "
                                      "(3–128 characters).")
                plan = _plan(preset, licence)
                with _lock:
                    old = _watchers.get(acct.name)
                    if old and old.status == "running":
                        return self.error("%s already has a watcher running — "
                                          "stop it first." % acct.name)
                    w = Watcher(acct, preset, plan, licence)
                    _watchers[acct.name] = w
                w.start()
                return self.json({"ok": True, "api": plan["api"],
                                  "notes": plan["notes"]})

            if url.path == "/api/licence":
                key = str(body.get("key") or "").strip()
                if key and not LICENCE_RE.match(key):
                    return self.error("A licence key is letters, digits, - _ . "
                                      "(3–128 characters).")
                _set_licence_key(key)
                return self.json({"ok": True, "licence": _licence_key() or None})

            if url.path == "/api/stop":
                with _lock:
                    w = _watchers.get(str(body.get("account") or ""))
                if w:
                    w.event("Stopped from the page.")
                    w.stop("stopped")
                return self.json({"ok": True})

            if url.path == "/api/dismiss":
                with _lock:
                    w = _watchers.get(str(body.get("account") or ""))
                    if w and w.status != "running":
                        del _watchers[w.acct.name]
                return self.json({"ok": True})

            if url.path == "/api/presets":
                preset = _clean_preset(body.get("preset"))
                name = str(body.get("name") or "").strip()
                if not NAME_RE.match(name):
                    return self.error("Give the preset a name (max 100 chars).")
                preset["name"] = name
                presets = [p for p in _presets() if p.get("name") != name]
                old = [p.get("name") for p in _presets()]
                if name in old:
                    presets.insert(old.index(name), preset)
                else:
                    presets.append(preset)
                _write_presets(presets)
                return self.json({"ok": True, "presets": presets})

            if url.path == "/api/presets/delete":
                name = str(body.get("name") or "")
                presets = [p for p in _presets() if p.get("name") != name]
                _write_presets(presets)
                return self.json({"ok": True, "presets": presets})

            if url.path == "/api/downloads/start":
                pid = str(body.get("pod_id") or "")
                if not POD_ID_RE.match(pid):
                    return self.error("Bad pod id.")
                folder = _clean_folder(body.get("folder"))
                app_url = str(body.get("app_url") or "").strip()
                if app_url and not re.match(r"^https?://[^\s]+$", app_url):
                    return self.error("The app URL must start with http:// or https://.")
                with _lock:
                    old = _downloads.get(pid)
                    if old and old.status not in ("stopped", "error"):
                        return self.error("This pod is already downloading — stop it first.")
                d = downloader.Download(
                    pid, str(body.get("pod_name") or pid)[:80], str(folder), app_url=app_url,
                    access_key=str(body.get("access_key") or "").strip(),
                    account=str(body.get("account") or ""))
                try:
                    d.start()
                except OSError as exc:
                    return self.error("Cannot use %s: %s" % (folder, exc))
                with _lock:
                    _downloads[pid] = d
                _save_downloads()
                return self.json({"ok": True})

            if url.path == "/api/downloads/stop":
                with _lock:
                    d = _downloads.get(str(body.get("pod_id") or ""))
                if d:
                    d.stop()
                    _save_downloads()
                return self.json({"ok": True})

            if url.path == "/api/downloads/dismiss":
                with _lock:
                    d = _downloads.get(str(body.get("pod_id") or ""))
                    if d and d.status == "stopped":
                        del _downloads[d.pod_id]
                return self.json({"ok": True})

            if url.path == "/api/pick-folder":
                try:
                    chosen = _pick_folder(str(body.get("initial") or ""))
                except ImportError:
                    return self.error("No folder dialog here (Python without tkinter) — "
                                      "type the folder path instead.")
                except Exception as exc:
                    return self.error("The folder dialog failed (%s) — type the path." % exc)
                return self.json({"folder": chosen})

            if url.path == "/api/open-folder":
                folder = _clean_folder(body.get("folder"))
                if not folder.is_dir():
                    return self.error("%s does not exist yet — it is made when the "
                                      "download starts." % folder)
                _open_folder(folder)
                return self.json({"ok": True})

            if url.path == "/api/pods/action":
                acct = _account(body.get("account"))
                pid, action = str(body.get("id") or ""), str(body.get("action") or "")
                if not POD_ID_RE.match(pid):
                    return self.error("Bad pod id.")
                if action not in ("start", "stop", "restart", "terminate"):
                    return self.error("Bad action.")
                health.note_action(pid, action)
                code, text = _pod_action(acct, pid, action)
                if code not in (200, 202, 204):
                    detail = _detail(text)
                    return self.json({"error": "%s failed (%s): %s"
                                      % (action, code, detail),
                                      "no_gpu": "free gpu" in detail.lower()
                                                or "not enough" in detail.lower()},
                                     502)
                h = health.get(pid)
                if h and action == "terminate":
                    h.set_decision("terminated")
                return self.json({"ok": True})

            if url.path == "/api/health/check":
                # The page's Check button: watch any running pod, sniped here
                # or not. Starts over if it was already being watched.
                acct = _account(body.get("account"))
                pid = str(body.get("id") or "")
                if not POD_ID_RE.match(pid):
                    return self.error("Bad pod id.")
                health.watch(acct, pid, source="check", restart=True)
                return self.json({"ok": True})

            if url.path == "/api/health/keep":
                h = health.get(str(body.get("id") or ""))
                if not h:
                    return self.error("That pod is not being watched.")
                h.keep()
                return self.json({"ok": True})

            if url.path == "/api/health/forget":
                health.forget(str(body.get("id") or ""))
                return self.json({"ok": True})

            if url.path == "/api/hosts/block":
                mid = str(body.get("machine") or "")
                if not MACHINE_RE.match(mid):
                    return self.error("Bad machine id.")
                info = {k: str(body.get(k) or "")[:80] for k in ("pod", "gpu", "country", "why")}
                return self.json({"ok": True, "blocked": health.block_host(mid, info)})

            if url.path == "/api/hosts/unblock":
                return self.json({"ok": True, "blocked":
                                  health.unblock_host(str(body.get("machine") or ""))})
        except (ValueError, TypeError, KeyError) as exc:
            return self.error(str(exc))
        self.error("Not found.", 404)


def _setup_health() -> None:
    health.HOOKS.update(pod=_health_pod, machine=_get_machine,
                        stream=lambda acct, url, headers, idle: acct.stream(url, headers, idle),
                        ext_get=_ext_get, app_up=_app_up, sound=_alarm)
    if DRY_RUN:
        # A dry run's pods are fake: keep their history, layer cache and
        # blocked hosts apart from the real ones.
        health.HISTORY_FILE = HERE / "pods-history.dry.jsonl"
        health.LAYERS_FILE = HERE / "image-layers.dry.json"
        health.HOSTS_FILE = HERE / "hosts.dry.json"
    try:
        health.history_compact()
    except OSError as exc:
        print("Could not compact %s: %s" % (health.HISTORY_FILE.name, exc))


def main() -> None:
    global DRY_RUN, SOUND, FAKE, PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7895)
    parser.add_argument("--dry-run", action="store_true",
                        help="talk to fake.py instead of Runpod")
    parser.add_argument("--dry-fails", type=int, default=20,
                        help="dry run: no-capacity answers before a win")
    parser.add_argument("--dry-health", default="random",
                        help="dry run: what the won pods look like — " + "|".join(
                            ["random", "fast", "slownet", "throttled", "x4", "cached",
                             "hosterr", "oldbuild", "lowram", "slowdisk", "restartloop"]))
    parser.add_argument("--dry-country", default="",
                        help="dry run: put every fake pod in this country (e.g. FR)")
    parser.add_argument("--no-sound", action="store_true")
    args = parser.parse_args()
    DRY_RUN, SOUND, PORT = args.dry_run, not args.no_sound, args.port
    if DRY_RUN:
        sys.path.insert(0, str(HERE))
        import fake
        FAKE = fake.FakeRunpod(_gpus(), fails=args.dry_fails, health=args.dry_health,
                               country=args.dry_country)
        downloader.FAKE_APP = fake.FakeApp(FAKE)
    _setup_health()
    _load_accounts()
    _resume_downloads()
    with _lock:
        names = sorted(_accounts)
    if not names:
        print("No API key: copy runpod_sniper/.env.example to .env and fill it in.")
    print("Runpod sniper on http://127.0.0.1:%d  accounts: %s%s"
          % (args.port, ", ".join(names) or "none",
             "  [dry run: fake Runpod]" if DRY_RUN else ""), flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
