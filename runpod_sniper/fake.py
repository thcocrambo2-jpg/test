"""A pretend Runpod for `server.py --dry-run`.

It answers the same URLs with the same status codes and messages the real
API uses, so the watcher's handling of each case gets exercised:

- create: "no capacity" for the first FAILS attempts of each snipe (v1:
  500, v2: 400), with an occasional 429 thrown in, then a pod. The win
  stays open for a moment so a request already in flight also wins — the
  race guard has to terminate that one. Now and then the pod lands on a
  pricier host, which the price guard should throw back.
- v1 rejects EUR-IS-4 / EUR-IS-5 / EUR-NO-2 with a 400 schema error, like
  the real one; v2 rejects an unknown GPU id with a plain 400 — the
  "real error" the watcher must stop on.
- pods go PROVISIONING → RUNNING after a few seconds; starting a stopped
  Community pod fails half the time with "not enough free GPUs".
- every reply carries RateLimit headers counted from a real sliding
  window, and an exhausted window really answers 429.
- every pod has a health *scenario* (--dry-health): its logs stream
  (system + container lines, played 10x fast), its machine (country,
  host disk) and the app's EMBER_PODCHECK lines follow it — a fast pod,
  slow internet, a throttled or x4 GPU, a cached image, a host error, an
  old build that prints no pod checks, low RAM, a slow disk, a restart
  loop. Docker Hub's manifest and the mirror's file list are faked too,
  so a dry run never reaches outside this machine.
"""

import json
import random
import re
import string
import threading
import time
from collections import deque
from urllib.parse import parse_qs, urlparse

V1_REJECTS = {"EUR-IS-4", "EUR-IS-5", "EUR-NO-2"}
V1_NO_CAPACITY = ("create pod: There are no instances currently available")
V2_NO_CAPACITY = ("There are no longer any instances available with the "
                  "requested specifications. Please refresh and try again.")


class FakeRunpod:
    def __init__(self, gpus: list, fails: int = 20, rate429: float = 0.03,
                 expensive: float = 0.15, health: str = "random", country: str = ""):
        self.gpus = {g["id"]: g for g in gpus}
        self.fails = fails
        self.rate429 = rate429
        self.expensive = expensive
        self.health = health
        self.country = (country or "").upper()
        self.lock = threading.Lock()
        self.keys = {}          # key -> per-account state

    def _acct(self, key: str) -> dict:
        if key not in self.keys:
            self.keys[key] = {"misses": 0, "open_until": 0.0,
                              "pods": {}, "calls": deque()}
        return self.keys[key]

    # ── rate limit: 90/min, 1800/h, like the real thing ─────────────

    def _limit(self, acct: dict) -> tuple:
        now = time.time()
        calls = acct["calls"]
        while calls and calls[0] < now - 3600:
            calls.popleft()
        minute = sum(1 for t in calls if t > now - 60)
        hour = len(calls)
        headers = {
            "RateLimit-Policy": '"minute";q=90;w=60, "hour";q=1800;w=3600',
            "RateLimit": '"minute";r=%d;t=%d, "hour";r=%d;t=%d' % (
                max(0, 90 - minute - 1), 60, max(0, 1800 - hour - 1), 3600),
        }
        over = minute >= 90 or hour >= 1800
        if not over:
            calls.append(now)
        return over, headers

    # ── the one entry point the server calls ────────────────────────

    def request(self, key: str, method: str, url: str, body) -> tuple:
        """(status, text, headers), as urllib would have produced them."""
        time.sleep(random.uniform(0.15, 0.9))          # network + scheduler
        if not key.startswith("dry-"):
            return 401, json.dumps({"error": "invalid api key"}), {}
        with self.lock:
            acct = self._acct(key)
            over, headers = self._limit(acct)
            if over:
                return 429, json.dumps({"error": "Too many requests: "
                                        "90 per minute"}), headers
            code, obj = self._route(acct, method, urlparse(url), body)
        return code, json.dumps(obj), headers

    def _route(self, acct, method, url, body) -> tuple:
        path = url.path
        if method == "POST" and path == "/v1/pods":
            return self._create(acct, "v1", body or {})
        if method == "POST" and path == "/v2/pods":
            return self._create(acct, "v2", body or {})
        if method == "GET" and path == "/v2/pods":
            return 200, {"pods": [self._v2(p) for p in acct["pods"].values()],
                         "pagination": {"nextCursor": None,
                                        "hasNextPage": False}}
        m = re.fullmatch(r"/v[12]/pods/([a-z0-9]+)(/action)?", path)
        if m:
            pod = acct["pods"].get(m.group(1))
            if not pod:
                return 404, {"title": "Not Found", "detail": "pod not found"}
            if method == "GET" and path.startswith("/v1/"):
                return 200, self._v1(pod, "includeMachine=true" in url.query)
            if method == "GET":
                return 200, self._v2(pod)
            if method == "DELETE":
                del acct["pods"][pod["id"]]
                return 204, {}
            if method == "POST" and m.group(2):
                return self._action(acct, pod, (body or {}).get("action"))
        if method == "GET" and path == "/v2/catalog/gpus":
            return 200, self._catalog(parse_qs(url.query))
        return 404, {"title": "Not Found", "detail": "no route %s %s"
                     % (method, path)}

    # ── create ──────────────────────────────────────────────────────

    def _create(self, acct, api, body) -> tuple:
        if api == "v1":
            bad = [d for d in body.get("dataCenterIds") or [] if d in V1_REJECTS]
            if bad:
                return 400, {"error": "request body has an error: doesn't match "
                             "schema: Error at \"/dataCenterIds/0\": value is "
                             "not one of the allowed values"}
            gpu = (body.get("gpuTypeIds") or [None])[0]
            cloud = body.get("cloudType", "SECURE")
            count = body.get("gpuCount", 1)
        else:
            gpu = (body.get("gpu") or {}).get("id")
            cloud = body.get("cloud", "SECURE")
            count = (body.get("gpu") or {}).get("count", 1)
        if gpu not in self.gpus:
            return 400, {"title": "Bad Request",
                         "detail": "gpu type %r does not exist" % gpu}
        if random.random() < self.rate429:
            return 429, {"error": "Too many requests"}
        now = time.time()
        if now > acct["open_until"]:
            if acct["misses"] < self.fails:
                acct["misses"] += 1
                if api == "v1":
                    return 500, {"error": V1_NO_CAPACITY, "status": 500}
                return 400, {"title": "Bad Request", "detail": V2_NO_CAPACITY}
            acct["open_until"] = now + 2.5      # a GPU just freed up
            acct["misses"] = 0
        return 201, self._new_pod(acct, api, body, gpu, cloud, count)

    def _new_pod(self, acct, api, body, gpu, cloud, count) -> dict:
        info = self.gpus[gpu]
        price = info.get(cloud.lower()) or info.get("secure") or 1.0
        if random.random() < self.expensive:
            price *= 1.5                        # a pricier host
        pid = "".join(random.choices(string.ascii_lowercase + string.digits, k=14))
        dcs = body.get("dataCenterIds") or ["EU-RO-1", "US-TX-3", "EUR-IS-2"]
        pod = {
            "id": pid,
            "name": body.get("name") or "dry-run-pod",
            "gpu": gpu, "count": count, "cloud": cloud,
            "cost": round(price * count, 3),
            "memory": random.choice([62, 93, 107, 125]) * count,
            "vcpu": random.choice([12, 16, 21, 32, 48, 54]) * count,
            "dc": random.choice(dcs),
            "cuda": random.choice(["13.0", "13.1", "13.2"]),
            "disk": body.get("containerDiskInGb") or body.get("disk") or 50,
            "status": "PROVISIONING", "since": time.time(),
            "ssh_key": api == "v1" or bool(body.get("startSsh")),
        }
        self._health_setup(pod)
        acct["pods"][pid] = pod
        if api == "v1":
            return {"id": pid, "name": pod["name"], "costPerHr": pod["cost"],
                    "adjustedCostPerHr": pod["cost"], "desiredStatus": "RUNNING",
                    "machineId": "m" + pid[:6], "gpuCount": count,
                    "memoryInGb": pod["memory"], "vcpuCount": pod["vcpu"]}
        return self._v2(pod)

    def _v2(self, pod) -> dict:
        if pod["status"] == "PROVISIONING" and time.time() - pod["since"] > 8:
            pod["status"] = "RUNNING"
        if (pod["status"] == "RUNNING" and pod.get("exit_at") is not None
                and _virtual(pod) > pod["exit_at"]):
            pod["status"] = "EXITED"                    # the host error took it down
            pod["last_change"] = "Exited: container exited with code 1"
        running = pod["status"] == "RUNNING"
        ssh = None
        if running and pod["ssh_key"]:
            ssh = {"host": "ssh.runpod.io", "port": 22,
                   "username": pod["id"] + "-64410a1f",
                   "command": "ssh %s-64410a1f@ssh.runpod.io -i ~/.ssh/id_ed25519"
                              % pod["id"]}
        actions = {"PROVISIONING": ["stop", "terminate"],
                   "RUNNING": ["stop", "restart", "terminate"],
                   "EXITED": ["start", "terminate"]}[pod["status"]]
        return {
            "id": pod["id"], "name": pod["name"], "status": pod["status"],
            "actions": actions, "cloud": pod["cloud"],
            "gpu": {"id": pod["gpu"], "count": pod["count"],
                    "vcpuCount": pod["vcpu"], "memory": pod["memory"]},
            "cost": pod["cost"], "dataCenterId": pod["dc"],
            "cudaVersion": pod["cuda"] if running else None,
            "disk": pod["disk"], "ports": ["7860/http", "8888/http", "22/tcp"],
            "ssh": {"proxy": ssh, "direct": None},
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime(pod["since"])),
        }

    def _action(self, acct, pod, action) -> tuple:
        if action == "terminate":
            del acct["pods"][pod["id"]]
            return 200, {}
        if action == "stop":
            pod["status"] = "EXITED"
            pod["last_change"] = "Exited by user: " + time.strftime("%a %b %d %Y %H:%M:%S GMT")
        elif action == "start":
            if pod["cloud"] == "COMMUNITY" and random.random() < 0.5:
                return 400, {"title": "Bad Request",
                             "detail": "There are not enough free GPUs on the "
                                       "host machine to start this pod."}
            pod.update(status="PROVISIONING", since=time.time())
        elif action == "restart":
            pod.update(status="PROVISIONING", since=time.time())
        else:
            return 422, {"title": "Unprocessable", "detail": "bad action"}
        return 200, self._v2(pod)

    def _catalog(self, query) -> dict:
        cloud = (query.get("cloud") or ["SECURE"])[0]
        out = []
        for g in self.gpus.values():
            price = g.get(cloud.lower())
            if price is None:
                continue
            out.append({"id": g["id"], "name": g["name"], "memory": g["vram"],
                        "price": {"secure": g.get("secure") or 0,
                                  "community": g.get("community") or 0},
                        "availability": random.choice(
                            ["NONE", "NONE", "LOW", "MEDIUM", "HIGH"])})
        return {"gpus": out}

    # ── health: machine, logs, outside lookups ──────────────────────

    def _health_setup(self, pod) -> None:
        sc = self.health
        if sc not in SCENARIOS:
            sc = "fast" if random.random() < 0.5 else random.choice(SCENARIOS[1:])
        pod["scenario"] = sc
        pod["country"] = self.country or random.choice(COUNTRIES)
        pod["machine"] = "m" + "".join(random.choices(string.ascii_lowercase + string.digits, k=11))
        pod["host_disk"] = 1123 if sc == "slowdisk" else random.choice([6725, 7096, 7178, 7244])
        if sc == "lowram":
            pod["memory"] = 41 * pod["count"]
        if sc == "hosterr":
            pod["err"] = random.choice(["pull", "start"])
        pod["timeline"] = _timeline(pod)

    def _v1(self, pod, include_machine: bool) -> dict:
        self._v2(pod)                                   # moves the status on
        last = pod.get("last_change") or "Rented by User: " + time.strftime(
            "%a %b %d %Y %H:%M:%S GMT", time.gmtime(pod["since"]))
        machine = {}
        # Right after the create the machine is not attached yet, as far as
        # anyone knows; the guard has to retry.
        if include_machine and time.time() - pod["since"] > 1.0:
            machine = {"location": pod["country"], "gpuTypeId": pod["gpu"],
                       "diskThroughputMBps": pod["host_disk"],
                       "maxDownloadSpeedMbps": 220 if pod["scenario"] == "slownet" else 3441,
                       "maxUploadSpeedMbps": 3912, "secureCloud": pod["cloud"] == "SECURE",
                       "dataCenterId": pod["dc"] if pod["cloud"] == "SECURE" else None,
                       "supportPublicIp": True}
        return {"id": pod["id"], "name": pod["name"], "machineId": pod["machine"],
                "desiredStatus": pod["status"], "lastStatusChange": last,
                "costPerHr": pod["cost"], "gpuCount": pod["count"],
                "memoryInGb": pod["memory"], "vcpuCount": pod["vcpu"], "machine": machine}

    def stream(self, key: str, url: str, headers: dict):
        """GET /v2/pods/{id}/logs as SSE lines, like the real stream: the
        last `tail` events so far, then new ones as they happen. Lines
        carry virtual timestamps (since + virtual seconds), so the speeds
        worked out from them are the scenario's, while the whole startup
        plays in about a minute. Resumes at Last-Event-ID (inclusive, so
        the reader has to drop the repeats)."""
        m = re.search(r"/v2/pods/([a-z0-9]+)/logs", url)
        if not key.startswith("dry-") or not m:
            raise ValueError("logs answered HTTP 401")
        with self.lock:
            pod = self._acct(key)["pods"].get(m.group(1))
        if not pod:
            raise ValueError("logs answered HTTP 404")
        q = parse_qs(urlparse(url).query)
        tail = int((q.get("tail") or ["100"])[0])
        last = headers.get("Last-Event-ID")
        timeline = pod["timeline"]
        since = pod["since"]
        i = 0
        if not last:
            ready = sum(1 for vt, _s, _l in timeline if vt <= _virtual(pod))
            i = max(0, ready - tail)
        idle = time.time()
        while True:
            now_v = _virtual(pod)
            while i < len(timeline) and timeline[i][0] <= now_v:
                vt, source, line = timeline[i]
                i += 1
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since + vt))
                if last and ts < last:
                    continue
                idle = time.time()
                yield "id: %s\n" % ts
                yield "data: %s\n" % json.dumps({"source": source, "line": line, "ts": ts})
                yield "\n"
            with self.lock:
                gone = pod["id"] not in self._acct(key)["pods"]
            if gone or pod["status"] == "EXITED":
                return
            if time.time() - idle > 30:                 # a quiet stream gets closed
                return
            time.sleep(0.3)

    def external(self, url: str, headers: dict) -> tuple:
        """Docker Hub and the HF tree API, for a dry run."""
        u = urlparse(url)
        if u.netloc == "auth.docker.io":
            return 200, {}, json.dumps({"token": "dry-run-token"})
        if u.netloc == "registry-1.docker.io":
            ref = u.path.rsplit("/", 1)[-1]
            if ref == IMAGE_DIGEST:
                return 200, {}, json.dumps({"manifests": [
                    {"digest": AMD64_DIGEST, "platform": {"architecture": "amd64", "os": "linux"}},
                    {"digest": "sha256:" + "f" * 64, "platform": {"architecture": "unknown"}}]})
            if ref == AMD64_DIGEST:
                return 200, {}, json.dumps({"layers": [
                    {"digest": "sha256:" + lid + "0" * 52, "size": size}
                    for lid, size in LAYER_SIZES.items()]})
            return 404, {}, json.dumps({"errors": [{"code": "MANIFEST_UNKNOWN"}]})
        m = re.fullmatch(r"/api/models/thcocrambo2/([\w-]+)/tree/main", u.path)
        if u.netloc == "huggingface.co" and m:
            return 200, {}, json.dumps([{"type": "file", "path": path, "size": int(size)}
                                        for path, size, repo in MODEL_FILES if repo == m.group(1)])
        return 404, {}, "not faked: %s" % url


# ── the health scenarios ────────────────────────────────────────────

SCENARIOS = ["fast", "slownet", "throttled", "x4", "cached", "hosterr", "oldbuild",
             "lowram", "slowdisk", "restartloop"]
COUNTRIES = ["SE", "US", "CA", "CZ", "NO", "RO", "NL", "FR"]
SPEED = 10.0              # virtual seconds per real second
IMAGE_DIGEST = "sha256:0b6f353660c6ca44c0933a3f34ac04bc5058cfc9e0aad545f32519c6ed33f0c9"
AMD64_DIGEST = "sha256:d06c3a364e02" + "0" * 52
# The real emberai/ember layers (manifest of IMAGE_DIGEST, 2026-11-06) and
# which of them a Community host already had cached (pod 64c143urvnged8).
LAYER_SIZES = {
    "689b91d88a0f": 29733459, "95e35692d3cc": 6961924, "5e25946ba40d": 362520,
    "f0af354daeda": 104893539, "4f4fb700ef54": 32, "ccf9c70a85fc": 186, "730c8029606b": 6886,
    "a30da298bda6": 1508350026, "21e0ed254c8b": 59988, "cb3db0ad5ae0": 1685,
    "8bd080c2e4b2": 1524, "42ab5aaffc26": 351507479, "0a02abafac52": 214803055,
    "c4d6229c9ef9": 405, "051986ec19f4": 221, "81dc4154923c": 2802732019, "ff68216e9bdc": 244,
    "cbd3eb980e3e": 41353124, "6e1a77657458": 235255882, "0b5e0ab2bace": 741274363,
    "e39a158cbe1c": 104372807, "d8e35b5c9c24": 6811, "cda7d440a849": 3047, "9d7c8d8b1bf3": 509,
}
CACHED_LAYERS = ["689b91d88a0f", "95e35692d3cc", "5e25946ba40d", "f0af354daeda", "4f4fb700ef54",
                 "ccf9c70a85fc", "730c8029606b", "a30da298bda6", "21e0ed254c8b", "cb3db0ad5ae0",
                 "8bd080c2e4b2"]
FETCH_LAYERS = [k for k in LAYER_SIZES if k not in CACHED_LAYERS]
# Made-up files with plausible sizes: what the mirror would serve.
MODEL_FILES = [
    ("diffusion_models/dry_krea2_v2_bf16.safetensors", 23.8e9, "krea2-assets"),
    ("diffusion_models/dry_zimage_turbo_bf16.safetensors", 12.3e9, "krea2-assets"),
    ("text_encoders/qwen3vl_4b_abliterated.safetensors", 8.88e9, "krea2-encoders"),
    ("text_encoders/dry_qwen3_4b.safetensors", 8.04e9, "krea2-assets"),
    ("vae/dry_krea2_v2_vae.safetensors", 0.33e9, "krea2-assets"),
    ("vae/dry_ae.safetensors", 0.335e9, "krea2-assets"),
    ("loras/dry_realism_v3.safetensors", 0.86e9, "krea2-loras"),
    ("loras/dry_detail_slider.safetensors", 0.44e9, "krea2-loras"),
    ("loras/dry_film_grain.safetensors", 0.23e9, "krea2-loras"),
    ("loras/dry_small_style.safetensors", 0.0123e9, "krea2-loras"),
]
# Fake "normal" numbers per GPU, for the dry run only: real baselines are
# learned from real pods.
GPU_PERF = {"NVIDIA GeForce RTX 5090": (205.0, 5, 0.40), "NVIDIA GeForce RTX 4090": (158.0, 4, 0.55),
            "NVIDIA L40S": (330.0, 4, 0.48), "NVIDIA RTX 6000 Ada Generation": (320.0, 4, 0.50)}


def _virtual(pod) -> float:
    return (time.time() - pod["since"]) * SPEED


def _hb(n: float) -> str:
    return "%.1fMB" % (n / 1e6) if n < 1e9 else "%.2fGB" % (n / 1e9)


def _pc(kind: str, **fields) -> str:
    return "EMBER_PODCHECK " + json.dumps({"v": 1, "kind": kind, **fields}, separators=(",", ":"))


def _timeline(pod) -> list:
    """Everything this pod will print: [(virtual seconds, source, line)],
    sorted. Built once, at create."""
    sc, out = pod["scenario"], []

    def add(vt, source, line):
        out.append((round(vt, 2), source, line))

    def log(vt, level, msg):
        clock = time.strftime("%H:%M:%S", time.gmtime(pod["since"] + vt))
        add(vt, "container", "%s [%s] ember: %s" % (clock, level, msg))

    add(0, "system", "create container emberai/ember:latest")
    add(1, "system", "latest Pulling from emberai/ember")
    if sc == "cached":
        for lid in CACHED_LAYERS + FETCH_LAYERS:
            add(1, "system", "%s Already exists" % lid)
        add(2, "system", "Digest: " + IMAGE_DIGEST)
        add(2, "system", "Status: Image is up to date for emberai/ember:latest")
        vt = 3.0
    else:
        for lid in CACHED_LAYERS:
            add(1, "system", "%s Already exists" % lid)
        for lid in FETCH_LAYERS:
            add(1, "system", "%s Pulling fs layer" % lid)
        speed = 22e6 if sc == "slownet" else 300e6
        progress = sc in ("fast", "throttled", "x4")     # some hosts log byte counts
        cum = 0.0
        for lid in FETCH_LAYERS:
            size = LAYER_SIZES[lid]
            start = 2 + cum / speed
            cum += size
            if progress and size > 50e6:
                for frac in (0.25, 0.5, 0.75):
                    add(start + size * frac / speed, "system", "%s Downloading [=====>    ]  %s/%s"
                        % (lid, _hb(size * frac), _hb(size)))
            else:
                add(2, "system", "%s Downloading" % lid)
            add(2 + cum / speed, "system", "%s Download complete" % lid)
        end = 2 + cum / speed
        add(min(end, 7), "system", "create container: still fetching image emberai/ember:latest")
        if sc == "hosterr" and pod["err"] == "pull":
            add(end / 2, "system", "toomanyrequests: You have reached your pull rate limit. You may "
                                   "increase the limit by authenticating and upgrading")
            pod["exit_at"] = end / 2 + 20
            return sorted(out, key=lambda e: e[0])
        for lid in FETCH_LAYERS:
            add(end + 1, "system", "%s Pull complete" % lid)
        add(end + 3, "system", "Digest: " + IMAGE_DIGEST)
        add(end + 3, "system", "Status: Downloaded newer image for emberai/ember:latest")
        vt = end + 4
    add(vt, "system", "start container for emberai/ember:latest: begin")
    c = vt + 1
    add(c, "container", "[ember-image] built 2026-11-04T10:00:00+00:00")
    add(c, "container", "[ember] starting")
    add(c + 1, "container", "[ember] license key EMBER-DRY-… · node tag dryrun-node")
    if sc == "hosterr":
        add(c + 3, "container", "[ember] ERROR: the app download failed (curl 56) — check this "
                                "pod's internet access and start it again.")
        pod["exit_at"] = c + 25
        return sorted(out, key=lambda e: e[0])
    add(c + 3, "container", "[ember] downloaded and verified")
    app = c + 5
    new = sc != "oldbuild"
    net = 22.0 if sc == "slownet" else random.uniform(130, 170)
    perf, gen, s_step = GPU_PERF.get(pod["gpu"], (150.0, 4, 0.6))
    log(app, "INFO", "Features — Krea2 V2, Ember Lite")
    log(app + 1, "INFO", "Assets — mirror thcocrambo2 (public)")
    if new:
        log(app + 2, "INFO", _pc("host", ram_gb=float(pod["memory"]), ram_host_gb=503.6, swap_gb=0,
                                 cpus=float(pod["vcpu"]), disk_free_gb=172.4,
                                 disk_write_mbs=2410 if sc != "slowdisk" else 380,
                                 disk_read_mbs=3120 if sc != "slowdisk" else 640, secs=2.1))
        log(app + 10, "INFO", _pc("net", mbs=round(net, 1), bytes=402653184,
                                  secs=round(402653184 / 1e6 / net, 1), streams=4, ttfb_ms=180,
                                  cdn="us.aws.cdn.hf.co", error=None))
    dls = app + (11 if new else 2)
    log(dls, "INFO", "Downloading asset groups: catalog, zimage")
    log(dls, "INFO", "Catalogue files for krea_v2_t2i, zimage_t2i: %d" % len(MODEL_FILES))
    if new:
        width = 4 if sc == "x4" else 16
        h2d = 6.1 if sc == "x4" else (48.0 if gen == 5 else 24.6)
        k = 0.7 if sc == "throttled" else random.uniform(0.97, 1.02)
        first, last = (perf * 0.95, perf * 0.62) if sc == "throttled" else (perf * k * 1.01, perf * k)
        log(dls + 12, "INFO", _pc(
            "gpu", name=pod["gpu"], driver="580.95.05", cuda=pod["cuda"],
            tflops=round(perf * k, 1), tflops_first=round(first, 1), tflops_last=round(last, 1),
            h2d_gbs=h2d, d2h_gbs=round(h2d * 0.93, 1), pcie_gen=gen, pcie_gen_max=gen,
            pcie_width=width, pcie_width_max=16, sm_mhz=1650 if sc == "throttled" else 2730,
            sm_mhz_max=3090, power_w=430 if sc == "throttled" else 540,
            power_limit_w=450 if sc == "throttled" else 575, power_default_w=575,
            power_max_w=600, temp_c=88 if sc == "throttled" else 64,
            throttle=["hw_slowdown"] if sc == "throttled" else [], ecc="N/A", vram_gb=31.8,
            secs=9.4, error=None))
    vt, total, next_rx = dls, 0.0, dls + 30
    for path, size, repo in MODEL_FILES:
        secs = size / (net * 1e6) + 0.4
        vt += secs
        total += size
        while new and next_rx < vt:
            log(next_rx, "INFO", _pc("rx", mbs=round(net * random.uniform(0.93, 1.03), 1), secs=30))
            next_rx += 30
        suffix = (" — %.2f GB in %.0f s, %.1f MB/s" % (size / 1e9, secs, size / 1e6 / secs)
                  if new else "")
        log(vt, "INFO", "✓ %s (mirror: thcocrambo2/%s)%s" % (path, repo, suffix))
    log(vt + 1, "INFO", "Downloads complete — 120.3 GB free on /workspace/ember")
    if new:
        log(vt + 1, "INFO", _pc("dl", files=len(MODEL_FILES), cached=0, failed=0, bytes=int(total),
                                secs=round(vt - dls), mbs=round(total / 1e6 / (vt - dls), 1)))
    log(vt + 2, "INFO", "Starting ComfyUI on port 8188 (logs → /workspace/ember/comfyui.log)")
    if sc == "restartloop":
        add(dls + 40, "system", "start container for emberai/ember:latest: begin")
        add(dls + 90, "system", "start container for emberai/ember:latest: begin")
    ui = vt + 40
    pod["ui_at"] = ui
    add(ui, "container", "=" * 60)
    add(ui, "container", ">>> OPEN THE UI HERE: https://dry-%s.trycloudflare.com/#k=DRYRUNSECRET"
                         "TOKEN0123456789abcdef" % pod["id"])
    add(ui, "container", "=" * 60)
    if new:
        slow = 1.45 if sc == "throttled" else random.uniform(0.97, 1.03)
        for n in range(120):
            log(ui + 60 + n * 45, "INFO", _pc(
                "render", key="krea_v2_t2i|dry_krea2_v2_bf16", steps=14, mp=1.05,
                s_per_step=round(s_step * slow * random.uniform(0.98, 1.02), 3), pre_s=6.2,
                cold=n == 0))
    return sorted(out, key=lambda e: e[0])


# ── the Ember app on a fake pod, for the auto downloader ────────────
#
# Answers the four app routes the downloader uses, through the same
# response shape urllib gives it: a Gallery that gains a file every few
# seconds while the pod runs (every fifth a video that stays "still
# encoding" — Cache-Control: no-store — for a while), the media, the tab
# schemas and the recipe apply. A stopped or terminated pod does not answer.

import io
import urllib.error

APP_EVERY = 6            # seconds between new files
APP_SEED_FILES = 3       # already there when the pod comes up
APP_ENCODE = 14          # a video is "still encoding" this long
APP_SPEED = 6e6          # bytes/s the fake proxy serves at (slow enough to watch)
APP_SCHEMA = {
    "key": "krea_v2_t2i", "tabId": "k2v2", "label": "Krea2 V2",
    "fields": [
        {"name": "prompt", "type": "text", "default": ""},
        {"name": "seed", "type": "number", "default": 0},
        {"name": "randomize", "type": "checkbox", "default": True},
        {"name": "steps", "type": "number", "default": 20},
        {"name": "init_image", "type": "image", "default": None},
    ],
    "lora": {"key": "lora", "count": 2, "parts": ["name", "enabled", "weight"],
             "slots": [{"name": "None", "enabled": False, "weight": 1.0}] * 2,
             "weightDefault": 1.0},
}


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers: dict, throttle=False):
        super().__init__(data)
        self.status, self.headers, self.throttle = 200, headers, throttle

    def read(self, n=-1):
        chunk = super().read(n)
        if self.throttle and chunk:
            time.sleep(len(chunk) / APP_SPEED)
        return chunk


class FakeApp:
    def __init__(self, runpod: "FakeRunpod"):
        self.runpod = runpod

    def _pod(self, pod_id):
        with self.runpod.lock:
            for acct in self.runpod.keys.values():
                pod = acct["pods"].get(pod_id)
                if pod:
                    self.runpod._v2(pod)                 # moves it to RUNNING
                    return pod
        return None

    def _items(self, pod):
        up = pod["since"] + 8
        now = time.time()
        n = min(300, APP_SEED_FILES + max(0, int((now - up) / APP_EVERY)))
        items = []
        for i in range(n):
            video = i % 5 == 4
            created = up + (i - APP_SEED_FILES + 1) * APP_EVERY
            items.append({
                "id": "%s_%05d_.%s" % ("Video" if video else "Krea2V2_dry", i + 1,
                                       "mp4" if video else "png"),
                "kind": "video" if video else "image", "i": i,
                "settled": not video or now - created > APP_ENCODE,
                "size": (6_000_000 + i * 1000) if video else (300_000 + (i * 7919) % 1_200_000),
                "tab": APP_SCHEMA["key"], "seed": 1000 + i,
            })
        return items[::-1]                              # newest first

    def __call__(self, pod_id, method, path, body):
        time.sleep(random.uniform(0.05, 0.25))
        url = "https://%s-7860.proxy.runpod.net%s" % (pod_id, path)
        pod = self._pod(pod_id)
        if not pod or pod["status"] != "RUNNING":
            raise urllib.error.URLError("pod %s is not running" % pod_id)
        if pod.get("ui_at") is None or _virtual(pod) < pod["ui_at"]:
            # Still downloading models: the proxy answers 502 until the app is up.
            raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)
        u = urlparse(path)
        route = u.path[len("/api/v1"):]
        if route == "/gallery":
            q = parse_qs(u.query)
            start = int((q.get("cursor") or ["0"])[0] or 0)
            limit = int((q.get("limit") or ["50"])[0])
            items = self._items(pod)
            page = [{k: v for k, v in it.items() if k not in ("i", "settled", "size")}
                    for it in items[start:start + limit]]
            nxt = start + limit
            return self._json({"items": page, "total": len(items),
                               "nextCursor": str(nxt) if nxt < len(items) else None})
        if route == "/schemas":
            return self._json([APP_SCHEMA])
        if route.startswith("/schema/") and route.endswith("/apply"):
            key = (body or {}).get("recipe", "")
            m = re.search(r"_(\d{5})_", key)
            i = int(m.group(1)) if m else 0
            return self._json({"values": {"prompt": "dry run picture %d" % i, "seed": 1000 + i,
                                          "randomize": False, "steps": 20 + i % 3,
                                          "lora.0.name": "dry-lora", "lora.0.enabled": True,
                                          "lora.0.weight": 0.8},
                               "preset": "Default", "presetValues": {"steps": 20}})
        if route.startswith("/media/"):
            from urllib.parse import unquote
            key = unquote(route[len("/media/"):])
            item = next((it for it in self._items(pod) if it["id"] == key), None)
            if not item:
                raise urllib.error.HTTPError(url, 404, "No such file.", {}, None)
            data = random.Random(pod_id + item["id"]).randbytes(item["size"])
            return _Resp(data, {"Content-Length": str(len(data)),
                                "Cache-Control": "private, max-age=86400" if item["settled"]
                                else "no-store"}, throttle=True)
        raise urllib.error.HTTPError(url, 404, "Not found", {}, None)

    @staticmethod
    def _json(obj):
        data = json.dumps(obj).encode()
        return _Resp(data, {"Content-Length": str(len(data)), "Content-Type": "application/json"})
