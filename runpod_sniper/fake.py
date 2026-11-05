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
                 expensive: float = 0.15):
        self.gpus = {g["id"]: g for g in gpus}
        self.fails = fails
        self.rate429 = rate429
        self.expensive = expensive
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
            "memory": random.choice([41, 62, 93, 107, 125]) * count,
            "vcpu": random.choice([12, 16, 21, 32, 48, 54]) * count,
            "dc": random.choice(dcs),
            "cuda": random.choice(["13.0", "13.1", "13.2"]),
            "disk": body.get("containerDiskInGb") or body.get("disk") or 50,
            "status": "PROVISIONING", "since": time.time(),
            "ssh_key": api == "v1" or bool(body.get("startSsh")),
        }
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
