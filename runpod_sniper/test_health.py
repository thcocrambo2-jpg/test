"""Tests for the pod health checks (health.py) and the location guard.

    python runpod_sniper/test_health.py

Standard library only, like the rest of the sniper. Nothing here talks
to Runpod: the logs are a real excerpt (pod 64c143urvnged8's system log,
2026-11-06) plus made-up lines for everything that has not happened to
us yet.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fake  # noqa: E402
import health  # noqa: E402
from health import LogParser, evaluate  # noqa: E402

# The system log of a real Community RTX 5090 (repeated "Downloading",
# "Extracting" and "Pull complete" lines left out). No byte counts: this
# host only logged "Downloading" / "Download complete".
LOG_64C = """\
11:39:55 create container emberai/ember:latest
11:39:56 latest Pulling from emberai/ember
11:39:56 689b91d88a0f Already exists
11:39:56 95e35692d3cc Already exists
11:39:56 5e25946ba40d Already exists
11:39:56 f0af354daeda Already exists
11:39:56 4f4fb700ef54 Already exists
11:39:56 ccf9c70a85fc Already exists
11:39:56 730c8029606b Already exists
11:39:56 a30da298bda6 Already exists
11:39:56 21e0ed254c8b Already exists
11:39:56 cb3db0ad5ae0 Already exists
11:39:56 8bd080c2e4b2 Already exists
11:39:56 42ab5aaffc26 Pulling fs layer
11:39:56 0a02abafac52 Pulling fs layer
11:39:56 4f4fb700ef54 Pulling fs layer
11:39:56 c4d6229c9ef9 Pulling fs layer
11:39:56 051986ec19f4 Pulling fs layer
11:39:56 81dc4154923c Pulling fs layer
11:39:56 ff68216e9bdc Pulling fs layer
11:39:56 cbd3eb980e3e Pulling fs layer
11:39:56 6e1a77657458 Pulling fs layer
11:39:56 0b5e0ab2bace Pulling fs layer
11:39:56 e39a158cbe1c Pulling fs layer
11:39:56 d8e35b5c9c24 Pulling fs layer
11:39:56 cda7d440a849 Pulling fs layer
11:39:56 9d7c8d8b1bf3 Pulling fs layer
11:39:57 4f4fb700ef54 Downloading
11:39:57 4f4fb700ef54 Download complete
11:39:57 0a02abafac52 Downloading
11:39:57 c4d6229c9ef9 Downloading
11:39:57 c4d6229c9ef9 Download complete
11:39:57 051986ec19f4 Downloading
11:39:57 051986ec19f4 Download complete
11:39:57 81dc4154923c Downloading
11:39:57 42ab5aaffc26 Downloading
11:39:58 0a02abafac52 Download complete
11:39:58 ff68216e9bdc Downloading
11:39:58 ff68216e9bdc Download complete
11:39:58 cbd3eb980e3e Downloading
11:39:58 cbd3eb980e3e Download complete
11:39:58 6e1a77657458 Downloading
11:40:00 6e1a77657458 Download complete
11:40:00 0b5e0ab2bace Downloading
11:40:03 create container: still fetching image emberai/ember:latest
11:40:04 0b5e0ab2bace Download complete
11:40:04 e39a158cbe1c Downloading
11:40:05 e39a158cbe1c Download complete
11:40:05 d8e35b5c9c24 Downloading
11:40:05 d8e35b5c9c24 Download complete
11:40:05 cda7d440a849 Downloading
11:40:05 cda7d440a849 Download complete
11:40:05 9d7c8d8b1bf3 Downloading
11:40:05 9d7c8d8b1bf3 Download complete
11:40:06 42ab5aaffc26 Download complete
11:40:11 81dc4154923c Download complete
11:40:18 create container emberai/ember:latest
11:40:18 create container: still fetching image emberai/ember:latest
11:40:34 create container emberai/ember:latest
11:40:34 create container: still fetching image emberai/ember:latest
11:40:49 create container emberai/ember:latest
11:40:49 create container: still fetching image emberai/ember:latest
11:40:50 Digest: sha256:0b6f353660c6ca44c0933a3f34ac04bc5058cfc9e0aad545f32519c6ed33f0c9
11:40:50 Status: Downloaded newer image for emberai/ember:latest
11:40:51 create container emberai/ember:latest
11:40:51 latest Pulling from emberai/ember
11:40:51 Digest: sha256:0b6f353660c6ca44c0933a3f34ac04bc5058cfc9e0aad545f32519c6ed33f0c9
11:40:51 Status: Image is up to date for emberai/ember:latest
11:40:53 start container for emberai/ember:latest: begin
11:49:01 restart container
11:49:36 restart container
11:50:15 stop container 978fb4472b01f9d94598aedfe0802348fa09bd4dde01e9de777fe9819218a9f4
11:50:17 remove container
11:50:17 create container emberai/ember:latest
11:50:18 latest Pulling from emberai/ember
11:50:18 Digest: sha256:0b6f353660c6ca44c0933a3f34ac04bc5058cfc9e0aad545f32519c6ed33f0c9
11:50:18 Status: Image is up to date for emberai/ember:latest
11:50:18 start container for emberai/ember:latest: begin
11:50:44 restart container
12:29:47 restart container
12:54:18 stop container 84620e1d3e6f4d86d59de89ac40d2672b302170ef0496088b98361988a03e313
12:54:23 remove container
WARN: container is unhealthy: dead
"""

DAY = "2026-11-06T"


def ev(clock, line, source="system"):
    return {"source": source, "line": line, "ts": DAY + clock + "Z"}


def feed(p, lines, source="system", clock="12:00:00"):
    for line in lines:
        p.feed(ev(clock, line, source))
    return p


def parsed_64c():
    p = LogParser()
    for row in LOG_64C.splitlines():
        if row.startswith("WARN:"):
            p.feed(ev("12:54:24", row))
            continue
        clock, line = row.split(" ", 1)
        p.feed(ev(clock, line))
    return p


def t(clock):
    return health._ts(DAY + clock + "Z")


def pc(kind, **fields):
    return "12:00:00 [INFO] ember: EMBER_PODCHECK " + json.dumps({"v": 1, "kind": kind, **fields})


def codes(res, level=None):
    return {f["code"] for f in res["findings"] if level is None or f["level"] == level}


class RealLog(unittest.TestCase):
    def test_layers_and_lifecycle(self):
        p = parsed_64c()
        self.assertEqual(len(p.cached), 11)
        self.assertEqual(len(p.fetch), 14)
        self.assertEqual(len(p.starts), 2)
        self.assertEqual(len(p.restarts), 4)
        self.assertEqual(p.digest, "sha256:0b6f353660c6ca44c0933a3f34ac04bc5058cfc9e0aad545f32519c6ed33f0c9")
        self.assertEqual(p.errors, {})               # the "unhealthy: dead" line is ignored

    def test_speed_from_manifest_sizes(self):
        p = parsed_64c()
        self.assertTrue(p.needs_layer_sizes())
        img = p.image(fake.LAYER_SIZES)
        self.assertEqual(img["method"], "manifest")
        self.assertAlmostEqual(img["mbs"], 299.4, delta=1.0)   # 4.49 GB in 15 s
        self.assertEqual(p.image(None)["method"], "unknown")

    def test_restarts_explained(self):
        """The second start follows "remove container" (an env edit): not the host."""
        res = evaluate(parsed_64c(), now=t("13:00:00"))
        self.assertEqual(res["metrics"]["restarts"], 0)
        self.assertNotIn("restarts", codes(res))

    def test_duplicates_dropped(self):
        p = LogParser()
        self.assertTrue(p.feed(ev("11:00:00", "create container x")))
        self.assertFalse(p.feed(ev("11:00:00", "create container x")))
        self.assertEqual(p.lines, 1)


class ImagePull(unittest.TestCase):
    def test_byte_progress(self):
        p = LogParser()
        feed(p, ["81dc4154923c Pulling fs layer"], clock="10:00:00")
        p.feed(ev("10:00:01", "81dc4154923c Downloading [=>   ]  100MB/2.8GB"))
        p.feed(ev("10:00:10", "81dc4154923c Downloading [====>]  1.5GB/2.8GB"))
        p.feed(ev("10:00:20", "81dc4154923c Download complete"))
        img = p.image()
        self.assertEqual(img["method"], "progress")
        self.assertAlmostEqual(img["mbs"], 2800 / 19, delta=1)

    def test_cached(self):
        p = feed(LogParser(), ["aaaaaaaaaaaa Already exists", "bbbbbbbbbbbb Already exists",
                               "Status: Image is up to date for emberai/ember:latest"])
        self.assertEqual(p.image()["method"], "cached")

    def test_slow_image_warns(self):
        p = LogParser()
        p.feed(ev("10:00:00", "81dc4154923c Downloading [=>  ]  10MB/2.8GB"))
        p.feed(ev("10:01:40", "81dc4154923c Downloading [==> ]  2.5GB/2.8GB"))
        self.assertIn("image_slow", codes(evaluate(p, now=t("10:02:00"))))


class Errors(unittest.TestCase):
    CASES = [
        ("system", "toomanyrequests: You have reached your pull rate limit.", "pull_rate_limit"),
        ("system", "error pulling image configuration: unexpected EOF", "pull_failed"),
        ("container", "OSError: [Errno 28] No space left on device", "disk_full"),
        ("system", "nvidia-container-cli: initialization error: nvml error: driver/library version mismatch",
         "nvidia_runtime"),
        ("container", "[ember] ERROR: the app download failed (curl 56) — check this pod's internet",
         "start_failed"),
        ("container", "12:00:00 [ERROR] ember: No NVIDIA GPU visible (nvidia-smi returned nothing). ",
         "no_gpu"),
        ("container", "RuntimeError: CUDA error: an illegal memory access was encountered", "cuda_error"),
        ("container", "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB", "vram_oom"),
        ("container", "/opt/ember/bin/start.sh: line 260:    42 Killed                  \"$BIN\"", "killed"),
        ("container", "12:00:00 [ERROR] ember: Catalogue model x (diffusion_models/x) unavailable (404) — that",
         "model_unavailable"),
        ("container", "12:00:00 [WARNING] ember: Mirror thcocrambo2/krea2-assets could not serve vae/x (404)",
         "mirror_miss"),
        ("container", "12:00:00 [WARNING] ember: loras/x (mirror) failed (attempt 1/3): timeout — retrying in 5s",
         "download_retry"),
    ]

    def test_each_pattern(self):
        for source, line, code in self.CASES:
            with self.subTest(code=code):
                p = LogParser()
                p.feed(ev("12:00:00", line, source))
                self.assertIn(code, p.errors)
                self.assertIn(code, codes(evaluate(p, now=t("12:00:05"))))

    def test_traceback_keeps_lines(self):
        p = LogParser()
        lines = ["Traceback (most recent call last):", '  File "x.py", line 1, in <module>',
                 "ValueError: bad thing"]
        for i, line in enumerate(lines):
            p.feed(ev("12:00:0%d" % i, line, "container"))
        self.assertEqual(p.errors["traceback"]["excerpt"], lines)

    def test_retries_become_a_warning(self):
        p = LogParser()
        for i in range(5):
            p.feed(ev("12:00:%02d" % i, "x (mirror) failed (attempt 1/3): boom — retrying in 5s", "container"))
        found = [f for f in evaluate(p, now=t("12:01:00"))["findings"] if f["code"] == "download_retry"]
        self.assertEqual(found[0]["level"], "warn")


class Secrets(unittest.TestCase):
    def test_redact(self):
        self.assertEqual(health.redact(">>> OPEN THE UI HERE: https://a.trycloudflare.com/#k=SECRET123"),
                         ">>> OPEN THE UI HERE: https://a.trycloudflare.com/#k=…")
        self.assertNotIn("abcdefghijklmnopqrstu", health.redact("token hf_abcdefghijklmnopqrstu"))
        self.assertNotIn("EMBER-TRAIN-TEST", health.redact("[ember] license key EMBER-TRAIN-TEST · node"))

    def test_banner_token_never_kept(self):
        p = LogParser()
        p.feed(ev("12:00:00", ">>> OPEN THE UI HERE: https://x.trycloudflare.com/#k=TOPSECRET", "container"))
        p.feed(ev("12:00:01", "[ember] ERROR: crash at https://x.trycloudflare.com/#k=TOPSECRET", "container"))
        res = evaluate(p, now=t("12:00:05"))
        self.assertIn("ui_ready", res["milestones"])
        self.assertNotIn("TOPSECRET", json.dumps(res))


class PodCheckLines(unittest.TestCase):
    def test_versions(self):
        p = LogParser()
        p.feed(ev("12:00:00", pc("net", mbs=148.2), "container"))
        self.assertEqual(p.pc["net"]["mbs"], 148.2)
        p.feed(ev("12:00:01", '12:00:01 [INFO] ember: EMBER_PODCHECK {"v":2,"kind":"net","mbs":1}', "container"))
        self.assertEqual(p.pc_unknown, 2)
        self.assertEqual(p.pc["net"]["mbs"], 148.2)
        p.feed(ev("12:00:02", "12:00:02 [INFO] ember: EMBER_PODCHECK {broken", "container"))
        self.assertEqual(p.pc_bad, 1)
        self.assertIn("podcheck_version", codes(evaluate(p, now=t("12:00:05"))))

    def test_error_record_still_counts(self):
        p = LogParser()
        p.feed(ev("12:00:00", pc("gpu", error="timeout"), "container"))
        res = evaluate(p, now=t("12:00:05"))
        self.assertIn("gpu_error", codes(res, "info"))


class Downloads(unittest.TestCase):
    def test_gap_speed_from_old_build(self):
        p = LogParser()
        p.feed(ev("12:00:00", "12:00:00 [INFO] ember: Downloading asset groups: catalog", "container"))
        p.feed(ev("12:01:00", "12:01:00 [INFO] ember: ✓ text_encoders/qwen3vl_4b_abliterated.safetensors "
                              "(mirror: thcocrambo2/krea2-encoders)", "container"))
        p.feed(ev("12:01:01", "12:01:01 [INFO] ember: ✓ loras/tiny.safetensors (mirror: thcocrambo2/krea2-loras)",
                  "container"))
        self.assertTrue(p.mirror_files_without_size())
        dl = p.download({"qwen3vl_4b_abliterated.safetensors": 8.88e9, "tiny.safetensors": 1e6})
        self.assertEqual(dl["source"], "log gaps")
        self.assertAlmostEqual(dl["mbs"], 148.0, delta=0.5)      # tiny file ignored
        res = evaluate(p, now=t("12:02:00"))
        self.assertTrue(res["metrics"]["old_build"])

    def test_suffix_from_new_build(self):
        p = LogParser()
        p.feed(ev("12:00:00", pc("net", mbs=150), "container"))
        p.feed(ev("12:00:01", "12:00:01 [INFO] ember: Downloading asset groups: catalog", "container"))
        p.feed(ev("12:01:00", "12:01:00 [INFO] ember: ✓ diffusion_models/a.safetensors (mirror: x/y) — "
                              "23.81 GB in 158 s, 150.7 MB/s", "container"))
        dl = p.download()
        self.assertEqual(dl["source"], "ember")
        self.assertAlmostEqual(dl["mbs"], 150.7, delta=0.2)
        self.assertFalse(evaluate(p, now=t("12:02:00"))["metrics"]["old_build"])

    def test_slow_live_rate(self):
        p = LogParser()
        p.feed(ev("12:00:00", "12:00:00 [INFO] ember: Downloading asset groups: catalog", "container"))
        p.feed(ev("12:00:30", pc("rx", mbs=25), "container"))
        p.feed(ev("12:01:00", pc("rx", mbs=28), "container"))
        self.assertIn("rx_slow", codes(evaluate(p, now=t("12:01:05"))))


class Thresholds(unittest.TestCase):
    def judge(self, line, **kw):
        p = LogParser()
        p.feed(ev("12:00:00", line, "container"))
        return evaluate(p, now=t("12:00:05"), **kw)

    def test_net(self):
        self.assertEqual(codes(self.judge(pc("net", mbs=49)), "warn"), {"net_slow"})
        self.assertNotIn("net_slow", codes(self.judge(pc("net", mbs=51))))
        self.assertEqual(codes(self.judge(pc("net", mbs=24)), "bad"), {"net_slow"})

    def test_ram(self):
        self.assertIn("ram_low", codes(self.judge(pc("host", ram_gb=59))))
        self.assertNotIn("ram_low", codes(self.judge(pc("host", ram_gb=60))))

    def test_gpu_score_needs_a_baseline(self):
        base = {"tflops": 200.0, "h2d_gbs": 25, "n": 3, "from": "history"}
        self.assertIn("gpu_slow", codes(self.judge(pc("gpu", tflops=169), baseline=base)))
        self.assertNotIn("gpu_slow", codes(self.judge(pc("gpu", tflops=171), baseline=base)))
        learning = self.judge(pc("gpu", tflops=100), baseline={**base, "n": 2})
        self.assertNotIn("gpu_slow", codes(learning))
        self.assertEqual(learning["metrics"]["gpu"]["learning"], "2/3")
        seeded = self.judge(pc("gpu", tflops=100), baseline={**base, "n": 0, "from": "seed"})
        self.assertIn("gpu_slow", codes(seeded))

    def test_absolute_gpu_flags(self):
        self.assertIn("gpu_throttle", codes(self.judge(pc("gpu", tflops=150, throttle=["hw_slowdown"]))))
        self.assertNotIn("gpu_throttle", codes(self.judge(pc("gpu", tflops=150, throttle=["sw_power_cap"]))))
        self.assertIn("gpu_drop", codes(self.judge(pc("gpu", tflops=150, tflops_first=200, tflops_last=167))))
        self.assertNotIn("gpu_drop", codes(self.judge(pc("gpu", tflops=150, tflops_first=200, tflops_last=171))))
        self.assertIn("power_capped", codes(self.judge(pc("gpu", tflops=1, power_limit_w=510, power_default_w=575))))
        self.assertNotIn("power_capped", codes(self.judge(pc("gpu", tflops=1, power_limit_w=520, power_default_w=575))))
        self.assertIn("gpu_hot", codes(self.judge(pc("gpu", tflops=1, temp_c=87))))

    def test_pcie_by_bandwidth(self):
        slow = self.judge(pc("gpu", tflops=150, h2d_gbs=9.9, pcie_gen=4, pcie_width=4))
        self.assertIn("pcie_slow", codes(slow))
        self.assertIn("Gen4 x4", [f for f in slow["findings"] if f["code"] == "pcie_slow"][0]["text"])
        self.assertNotIn("pcie_slow", codes(self.judge(pc("gpu", tflops=150, h2d_gbs=10.1))))

    def test_host_disk(self):
        p = LogParser()
        self.assertIn("host_disk_slow", codes(evaluate(p, now=1, machine={"host_disk_mbs": 1123})))
        self.assertNotIn("host_disk_slow", codes(evaluate(p, now=1, machine={"host_disk_mbs": 7096})))

    def test_render_slow(self):
        p = LogParser()
        for i in range(3):
            p.feed(ev("12:00:0%d" % i, pc("render", key="k", s_per_step=0.6), "container"))
        self.assertIn("render_slow", codes(evaluate(p, now=1, render_base={"k": 0.45})))
        self.assertNotIn("render_slow", codes(evaluate(p, now=1, render_base={"k": 0.55})))


class Stuck(unittest.TestCase):
    def test_image_stuck(self):
        p = LogParser()
        p.feed(ev("12:00:00", "create container: still fetching image emberai/ember:latest"))
        self.assertNotIn("image_stuck", codes(evaluate(p, now=t("12:09:00"))))
        self.assertEqual(codes(evaluate(p, now=t("12:11:00")), "warn"), {"image_stuck"})
        self.assertEqual(codes(evaluate(p, now=t("12:21:00")), "bad"), {"image_stuck"})

    def test_host_restarts(self):
        p = LogParser()
        p.feed(ev("12:00:00", "start container for emberai/ember:latest: begin"))
        p.feed(ev("12:05:00", "start container for emberai/ember:latest: begin"))
        self.assertIn("restarts", codes(evaluate(p, now=t("12:06:00"))))
        ours = evaluate(p, now=t("12:06:00"), actions=[t("12:04:30")])
        self.assertNotIn("restarts", codes(ours))

    def test_pod_exit(self):
        p = LogParser()
        host = evaluate(p, now=1, pod={"status": "EXITED"},
                        machine={"last_status": "Exited: container exited with code 1"})
        self.assertIn("pod_exited", codes(host, "bad"))
        user = evaluate(p, now=1, pod={"status": "EXITED"},
                        machine={"last_status": "Exited by user: Fri Nov 06 2026 12:54:17 GMT"})
        self.assertNotIn("pod_exited", codes(user))
        self.assertEqual(user["ended"], "stopped by you")

    def test_done(self):
        p = LogParser()
        p.feed(ev("12:00:00", pc("net", mbs=150), "container"))
        p.feed(ev("12:00:01", pc("gpu", tflops=200), "container"))
        p.feed(ev("12:10:00", ">>> OPEN THE UI HERE: https://x/#k=y", "container"))
        self.assertFalse(evaluate(p, now=1, app={"up": False})["done"])
        self.assertTrue(evaluate(p, now=1, app={"up": True})["done"])


class Baselines(unittest.TestCase):
    def test_median_of_clean_pods(self):
        recs = [{"pod": "a%d" % i, "gpu": "G", "gpu_tflops": v, "h2d_gbs": 24, "won_at": "2026-11-0%d" % i}
                for i, v in enumerate([200, 204, 196, 150], 1)]
        recs[3]["flags"] = ["gpu_throttle"]                       # not clean: left out
        b = health.baselines(recs)["G"]
        self.assertEqual((b["tflops"], b["n"]), (200, 3))
        self.assertEqual(health.baselines(recs, exclude="a1")["G"]["n"], 2)
        seeded = health.baselines(recs[:1], {"G": {"tflops": 210, "h2d_gbs": 25}})["G"]
        self.assertEqual((seeded["tflops"], seeded["from"]), (210, "seed"))

    def test_render_baselines(self):
        recs = [{"pod": "a", "gpu": "G", "render": {"k": {"n": 3, "s_per_step": 0.4}}},
                {"pod": "b", "gpu": "G", "render": {"k": {"n": 5, "s_per_step": 0.5}}},
                {"pod": "c", "gpu": "G", "render": {"k": {"n": 1, "s_per_step": 9}}}]
        self.assertEqual(health.render_baselines(recs), {("G", "k"): 0.45})


class Guard(unittest.TestCase):
    def test_rules(self):
        fr = {"country": "FR", "machine": "qaof1yb11sza"}
        self.assertEqual(health.guard_check(fr, {"countries": ["FR"], "country_mode": "block"}, {})[0], "block")
        self.assertEqual(health.guard_check(fr, {"countries": ["SE", "US"], "country_mode": "allow"}, {})[0], "block")
        self.assertEqual(health.guard_check(fr, {"countries": ["SE"], "country_mode": "block"}, {})[0], "ok")
        self.assertEqual(health.guard_check({}, {"countries": ["FR"]}, {})[0], "unknown")
        self.assertEqual(health.guard_check(fr, {}, {"qaof1yb11sza": {}})[0], "block")
        self.assertEqual(health.guard_check(fr, {}, {})[0], "ok")

    def test_watcher_throws_back_a_blocked_country(self):
        import server
        preset = server._clean_preset({"gpus": ["G"], "cloud": "COMMUNITY", "countries": "fr, de",
                                       "country_mode": "block", "country_action": "terminate"})
        self.assertEqual(preset["countries"], ["DE", "FR"])
        with self.assertRaises(ValueError):
            server._clean_preset({"gpus": ["G"], "cloud": "COMMUNITY", "countries": ["France"]})
        w = server.Watcher(type("A", (), {"name": "t"})(), preset, {"notes": [], "api": "v2"})
        killed = []
        w._terminate = lambda pid, why: killed.append((pid, why))
        old_machine, old_blocked = server._get_machine, health.blocked_hosts
        try:
            health.blocked_hosts = lambda: {}
            server._get_machine = lambda acct, pid: {"country": "FR", "machine": "m1"}
            self.assertIs(w._location_guard("pod1", {}), False)
            self.assertEqual(killed[0][0], "pod1")
            self.assertFalse(w.checking)
            server._get_machine = lambda acct, pid: {"country": "SE", "machine": "m2"}
            self.assertEqual(w._location_guard("pod2", {})["country"], "SE")
            self.assertEqual(len(killed), 1)
        finally:
            server._get_machine, health.blocked_hosts = old_machine, old_blocked


class Watch(unittest.TestCase):
    """The watcher itself, with Runpod stubbed: Keep, and a re-check that
    must not forget the machine or what was kept."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.saved = (dict(health.HOOKS), health.HISTORY_FILE, health.HOSTS_FILE)
        health.HISTORY_FILE = Path(self.dir.name) / "h.jsonl"
        health.HOSTS_FILE = Path(self.dir.name) / "hosts.json"
        self.sounds = []
        health.HOOKS.update(
            pod=lambda acct, pid: {"status": "RUNNING", "cuda": "13.0", "ram": 41, "gpu": "G"},
            machine=lambda acct, pid: {"country": "SE", "machine": "m1", "host_disk_mbs": 7000},
            stream=lambda acct, url, headers, idle: iter(()), ext_get=None,
            app_up=lambda pid: False, sound=lambda: self.sounds.append(1))

    def tearDown(self):
        health.forget("pod1")
        hooks, health.HISTORY_FILE, health.HOSTS_FILE = self.saved
        health.HOOKS.clear()
        health.HOOKS.update(hooks)
        self.dir.cleanup()

    def wait_for(self, pred, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.1)
        return False

    def test_keep_survives_a_recheck(self):
        acct = type("A", (), {"name": "t"})()
        h = health.watch(acct, "pod1", source="snipe")
        self.assertTrue(self.wait_for(lambda: h.view()["status"] == "warning"))
        self.assertEqual([f["code"] for f in h.view()["findings"]], ["ram_low"])
        self.assertTrue(self.wait_for(lambda: self.sounds))
        h.keep()
        self.assertEqual(h.view()["decision"], "kept")
        self.assertNotEqual(h.view()["status"], "warning")
        again = health.watch(acct, "pod1", source="check", restart=True)
        self.assertIsNot(again, h)
        self.assertEqual(again.machine["machine"], "m1")
        self.assertEqual(again.source, "snipe")
        self.assertTrue(self.wait_for(lambda: again.view()["findings"]))
        self.assertNotEqual(again.view()["status"], "warning")
        self.assertEqual(len(self.sounds), 1)               # no second beep for the same warning
        rec = health.history_load()[0]
        self.assertEqual((rec["pod"], rec["decision"], rec["country"]), ("pod1", "kept", "SE"))


class History(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = health.HISTORY_FILE
        health.HISTORY_FILE = Path(self.dir.name) / "pods-history.jsonl"

    def tearDown(self):
        health.HISTORY_FILE = self.old
        self.dir.cleanup()

    def test_latest_wins_and_compacts(self):
        for i in range(250):
            health.history_append({"pod": "p%d" % (i % 2), "won_at": "2026-11-0%d" % (i % 2 + 1), "n": i})
            if i % 50 == 0:
                time.sleep(0.01)                     # a new mtime, so the cache re-reads
        recs = health.history_load()
        self.assertEqual([(r["pod"], r["n"]) for r in recs], [("p1", 249), ("p0", 248)])
        health.history_compact()
        self.assertEqual(len(health.HISTORY_FILE.read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual(len(health.history_load()), 2)

    def test_summary(self):
        recs = [{"pod": "a", "country": "FR", "machine": "m", "net_mbs": 100, "gpu_score": 0.9, "flags": ["x"]},
                {"pod": "b", "country": "FR", "machine": "m", "net_mbs": 200, "verdict": "bad"}]
        s = health.history_summary(recs)
        self.assertEqual(s["by_country"]["FR"]["pods"], 2)
        self.assertEqual(s["by_country"]["FR"]["net_mbs"], 150)
        self.assertEqual(s["by_machine"]["m"]["bad"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
