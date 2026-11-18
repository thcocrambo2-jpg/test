"""AWS GPU sniper page.

    python aws_sniper/server.py [--port 7896] [--profile ember] [--no-sound]

AWS credentials: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in aws_sniper/.env,
or --profile for one set up with `aws configure`. More accounts:
AWS_ACCESS_KEY_ID__<name> / AWS_SECRET_ACCESS_KEY__<name> (or the page's Add
account); the page picks one for credit, setup and sniping, and lists every
account's instances.

A page on http://127.0.0.1:7896 for sniper.py: pick region, market,
instance types and zones (with live spot price and placement score),
snipe, and manage what it launched (start, stop, delete, copy SSH, open
the app). One snipe at a time, and never while an instance it launched
is running anywhere, or was stopped by AWS and may come back by itself.

The app's link comes from the instance's serial console (userdata.sh
copies it there), so the page never needs SSH. Instances launched before
that was added show no link.

Money: what each instance costs per hour running (instance + its disks +
public IP) and stopped (disks only), from the Pricing API and the spot
price history. Spending is not read from AWS: history.json keeps every
running/stopped stretch of every instance the page has seen, with its
rate, and the credit left is the credit total you set (settings.json,
default $100) minus that. A stretch that ended while this server was off
counts until the moment AWS says it ended (StateTransitionReason), or
until the server saw it again, so the estimate errs high, never low.

Auto download: runpod_sniper/downloader.py, unchanged, pointed at the
instance's tunnel link with the link's #k= key; a new link after a
restart is picked up by itself.
"""

import argparse
import base64
import json
import re
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sniper as core                         # noqa: E402  (exits if boto3 is missing)
import boto3                                  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError  # noqa: E402

sys.path.insert(1, str(HERE.parent / "runpod_sniper"))
import downloader                             # noqa: E402  (the Runpod page's, as is)

TOKENS_CSS = HERE / "tokens.css"
HISTORY_FILE = HERE / "history.json"
SETTINGS_FILE = HERE / "settings.json"
DOWNLOADS_FILE = HERE / "downloads.json"
DEFAULT_CREDIT = 100.0
HOURS_PER_MONTH = 730          # AWS bills per-month storage over 730 hours
IPV4_HOURLY = 0.005            # a public IPv4 address while the instance runs
FREE_IOPS, FREE_MIBPS = 3000, 125   # gp3 includes these
APP_PORT = 7860

# Regions that offer g7e. The page lists these; quotas decide what works.
REGIONS = {
    "ap-south-1": "Mumbai", "us-east-1": "N. Virginia", "us-east-2": "Ohio",
    "us-west-2": "Oregon", "ap-northeast-1": "Tokyo", "ap-northeast-2": "Seoul",
    "eu-west-2": "London", "eu-central-1": "Frankfurt", "eu-north-1": "Stockholm",
    "eu-south-2": "Spain",
}
# What the page offers, fastest first.
TYPES = {
    "g7e.2xlarge": {"gpu": "RTX PRO 6000 96 GB", "vcpu": 8},
    "g7e.4xlarge": {"gpu": "RTX PRO 6000 96 GB", "vcpu": 16},
    "g6e.2xlarge": {"gpu": "L40S 48 GB", "vcpu": 8},
    "g6e.xlarge": {"gpu": "L40S 48 GB", "vcpu": 4},
}
# G and VT vCPU quotas, per market.
QUOTAS = {"spot": "L-3819A6DF", "on-demand": "L-DB2E81BA"}
QUIET_REGION_ERRORS = {"AuthFailure", "OptInRequired", "UnauthorizedOperation",
                       "InvalidClientTokenId"}
URL_RE = re.compile(r"EMBER_URL (https://[a-z0-9-]+\.trycloudflare\.com/\S*)")

PROFILE = None
SOUND = True
_lock = threading.Lock()
_sessions = {}
_cache = {}


def _client(region: str, service: str = "ec2", account: str = core.MAIN_ACCOUNT):
    with _lock:
        if (account, region) not in _sessions:
            _sessions[(account, region)] = core.make_session(region, PROFILE, account)
        key = (account, region, service)
        if key not in _sessions:
            _sessions[key] = _sessions[(account, region)].client(service)
        return _sessions[key]


def _forget_account(account: str) -> None:
    """Its keys changed: build its sessions again."""
    with _lock:
        for k in [k for k in _sessions if k[0] == account]:
            del _sessions[k]
        for k in [k for k in _cache if isinstance(k, tuple) and account in k]:
            del _cache[k]


def _accounts() -> list:
    return core.accounts()


def _account(raw) -> str:
    name = str(raw or core.MAIN_ACCOUNT)
    if name not in _accounts():
        raise ValueError("Unknown account %s." % name)
    return name


def _account_id(account: str):
    """(12-digit id, error): who the keys belong to, cached for a day."""
    def fetch():
        try:
            return _client("us-east-1", "sts", account).get_caller_identity()["Account"], ""
        except (ClientError, BotoCoreError, NoCredentialsError, ValueError) as exc:
            return None, _err(exc)
    hit = _cached(("sts", account), 86400, fetch)
    if hit[0] is None:
        with _lock:
            _cache.pop(("sts", account), None)       # try again next time
    return hit


def _cached(key, seconds: float, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def _err(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return "%s: %s" % (core.code_of(exc), core.message_of(exc))
    if isinstance(exc, NoCredentialsError):
        return "No AWS credentials: put AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in %s." % core.ENV_FILE
    return str(exc)


def _alarm() -> None:
    threading.Thread(target=core.alarm, args=(SOUND,), daemon=True).start()


# ── region facts: keys, security groups, quotas, zones ──────────────


def _quota(region: str, market: str, account: str):
    try:
        q = _client(region, "service-quotas", account).get_service_quota(
            ServiceCode="ec2", QuotaCode=QUOTAS[market])
        return int(q["Quota"]["Value"])
    except (ClientError, BotoCoreError):
        return None


def _scores(region: str, itype: str, account: str) -> dict:
    """Zone name -> spot placement score (1-10). AWS limits how often these
    can be asked, so they're cached for 30 minutes. Zone names differ per
    account (1a in one is not 1a in another), so this is per account."""
    def fetch():
        ec2 = _client(region, account=account)
        names = {z["ZoneId"]: z["ZoneName"]
                 for z in ec2.describe_availability_zones()["AvailabilityZones"]}
        try:
            resp = ec2.get_spot_placement_scores(
                InstanceTypes=[itype], TargetCapacity=1, RegionNames=[region],
                SingleAvailabilityZone=True)
        except ClientError:
            return {}
        return {names.get(s["AvailabilityZoneId"], s["AvailabilityZoneId"]): s["Score"]
                for s in resp.get("SpotPlacementScores", [])}
    return _cached(("scores", region, itype, account), 1800, fetch)


def _spot_prices(region: str, account: str) -> dict:
    """(type, zone) -> current Linux spot price, in the account's zone names."""
    def fetch():
        resp = _client(region, account=account).describe_spot_price_history(
            InstanceTypes=list(TYPES), ProductDescriptions=["Linux/UNIX"],
            StartTime=datetime.now(timezone.utc))
        out = {}
        for p in resp["SpotPriceHistory"]:
            out.setdefault((p["InstanceType"], p["AvailabilityZone"]), float(p["SpotPrice"]))
        return out
    return _cached(("prices", region, account), 300, fetch)


def _price_list(filters: dict) -> list:
    """(attributes, unit, USD) for every on-demand price matching filters."""
    pricing = _client("us-east-1", "pricing", _accounts()[0])
    out = []
    pages = pricing.get_paginator("get_products").paginate(
        ServiceCode="AmazonEC2",
        Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in filters.items()])
    for page in pages:
        for raw in page["PriceList"]:
            item = json.loads(raw)
            for term in item.get("terms", {}).get("OnDemand", {}).values():
                for dim in term["priceDimensions"].values():
                    out.append((item["product"]["attributes"], dim["unit"],
                                float(dim["pricePerUnit"].get("USD", 0))))
    return out


def _on_demand_price(region: str, itype: str):
    def fetch():
        try:
            rows = _price_list({"instanceType": itype, "regionCode": region,
                                "operatingSystem": "Linux", "tenancy": "Shared",
                                "preInstalledSw": "NA", "capacitystatus": "Used"})
        except (ClientError, BotoCoreError):
            return None
        return next((usd for _a, unit, usd in rows if unit == "Hrs" and usd > 0), None)
    return _cached(("od", region, itype), 86400, fetch)


def _disk_rates(region: str):
    """gp3 per month: per GB, per MiB/s above 125, per IOPS above 3000."""
    def fetch():
        try:
            rows = _price_list({"volumeApiName": "gp3", "regionCode": region})
        except (ClientError, BotoCoreError):
            return None
        rates = {}
        for _a, unit, usd in rows:
            if unit == "GB-Mo":
                rates["gb"] = usd
            elif unit == "GiBps-mo":
                rates["mibps"] = usd / 1024
            elif unit == "IOPS-Mo":
                rates["iops"] = usd
        return rates if "gb" in rates else None
    return _cached(("disk", region), 86400, fetch)


def _disk_monthly(rates, size_gb: int, mibps: int, iops: int):
    if not rates:
        return None
    return (size_gb * rates["gb"] + max(0, mibps - FREE_MIBPS) * rates.get("mibps", 0)
            + max(0, iops - FREE_IOPS) * rates.get("iops", 0))


def _admits(group: dict, port: int) -> bool:
    """The group lets `port` in from an address range. A rule that only
    admits the group itself (the default SG's) doesn't count."""
    return any((p.get("IpProtocol") == "-1" or
                p.get("FromPort", 0) <= port <= p.get("ToPort", -1))
               and bool(p.get("IpRanges") or p.get("Ipv6Ranges"))
               for p in group.get("IpPermissions", []))


def _region_view(region: str, account: str) -> dict:
    ec2 = _client(region, account=account)
    keys = sorted(k["KeyName"] for k in ec2.describe_key_pairs()["KeyPairs"])
    sgs = []
    for g in ec2.describe_security_groups()["SecurityGroups"]:
        sgs.append({"id": g["GroupId"], "name": g["GroupName"], "ssh": _admits(g, 22),
                    "app": _admits(g, APP_PORT)})
    offers = ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "instance-type", "Values": list(TYPES)}])["InstanceTypeOfferings"]
    prices = _spot_prices(region, account)
    zones = {}
    for o in sorted(offers, key=lambda o: o["Location"]):
        t, z = o["InstanceType"], o["Location"]
        zones.setdefault(t, []).append({"zone": z, "price": prices.get((t, z))})
    for t, rows in zones.items():
        scores = _scores(region, t, account)
        for r in rows:
            r["score"] = scores.get(r["zone"])
    quotas = _cached(("quotas", region, account), 600,
                     lambda: {m: _quota(region, m, account) for m in QUOTAS})
    on_demand = {t: _on_demand_price(region, t) for t in zones}
    setup = _settings(account)["regions"].get(region)
    if setup and not (setup.get("key") in keys and any(g["id"] == setup.get("sg") for g in sgs)):
        setup = None                 # deleted since: set up again
    return {"region": region, "account": account, "keys": keys, "sgs": sgs, "quotas": quotas, "setup": setup,
            "zones": zones, "on_demand": on_demand, "disk_rates": _disk_rates(region),
            "free_iops": FREE_IOPS, "free_mibps": FREE_MIBPS,
            "hours_per_month": HOURS_PER_MONTH, "ipv4_hourly": IPV4_HOURLY}


# ── instances this tool launched, in every region ──────────────────


class History:
    """history.json: {instance id: {account, region, type, market, name,
    segments: [{state: running|stopped, start, end|null, rate}]}}. rate is
    $/hr: the whole running cost while running, the disks alone while
    stopped. Runs recorded before accounts existed are main's."""

    def __init__(self):
        self.lock = threading.Lock()
        try:
            self.data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.saved = json.dumps(self.data, sort_keys=True)

    def save(self) -> None:
        with self.lock:
            text = json.dumps(self.data, sort_keys=True)
            if text == self.saved:
                return
            tmp = HISTORY_FILE.with_suffix(".json.part")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            tmp.replace(HISTORY_FILE)
            self.saved = text

    def observe(self, row: dict, now: float) -> None:
        state = row["state"]
        want = ("running" if state in ("pending", "running") else
                "stopped" if state in ("stopping", "stopped") else None)
        with self.lock:
            if want is None and row["id"] not in self.data:
                return                            # terminated before we ever saw it
            h = self.data.setdefault(row["id"], {"segments": []})
            h.update(account=row["account"], region=row["region"], type=row["type"],
                     market=row["market"], name=row["name"])
            segs = h["segments"]
            cur = segs[-1] if segs and segs[-1]["end"] is None else None
            if want is None:                      # terminated
                if cur:
                    cur["end"] = max(cur["start"], min(row["changed"] or now, now))
                h.setdefault("gone", row["changed"] or now)
                return
            if want == "running":
                when, rate = row["launched"], row["rate_running"]
            else:
                when, rate = row["changed"] or now, row["rate_stopped"]
            when = min(when, now)
            if cur and cur["state"] == "running" and want == "running" and when > cur["start"] + 1:
                # Stopped and started again while the server was off. The
                # stop time is unknown, so the gap counts as running (high).
                cur["end"] = when
                cur = None
            if cur and cur["state"] != want:
                cur["end"] = max(cur["start"], when)
                cur = None
            if cur is None:
                start = max(when, segs[-1]["end"]) if segs else when
                segs.append({"state": want, "start": start, "end": None, "rate": rate})
            elif rate is not None:
                cur["rate"] = rate

    def lost(self, seen: set, regions_ok: set, now: float) -> None:
        """Open stretches of instances no longer listed at all (terminated
        over an hour ago) end now. regions_ok: (account, region) pairs read."""
        with self.lock:
            for iid, h in self.data.items():
                segs = h["segments"]
                where = (h.get("account") or core.MAIN_ACCOUNT, h.get("region"))
                if (iid not in seen and where in regions_ok and segs
                        and segs[-1]["end"] is None):
                    segs[-1]["end"] = now
                    h.setdefault("gone", now)

    def spent_since(self, since: float, now: float, account: str) -> float:
        """What the account's stretches cost from `since` to now (the part
        of each stretch after `since`)."""
        with self.lock:
            cost = 0.0
            for h in self.data.values():
                if (h.get("account") or core.MAIN_ACCOUNT) != account:
                    continue
                for s in h["segments"]:
                    start, end = max(s["start"], since), (s["end"] or now)
                    if end > start and s["rate"] is not None:
                        cost += (end - start) / 3600 * s["rate"]
            return cost

    def spent(self, now: float, account: str) -> dict:
        """The account's total, and per instance for every account."""
        with self.lock:
            per, total, unknown, since = {}, 0.0, False, None
            for iid, h in self.data.items():
                mine = (h.get("account") or core.MAIN_ACCOUNT) == account
                cost = 0.0
                for s in h["segments"]:
                    hours = ((s["end"] or now) - s["start"]) / 3600
                    if s["rate"] is None:
                        unknown = unknown or (mine and hours > 0)
                    else:
                        cost += hours * s["rate"]
                    if mine:
                        since = s["start"] if since is None else min(since, s["start"])
                per[iid] = cost
                total += cost if mine else 0
            return {"total": total, "per": per, "unknown_rate": unknown, "since": since}

    def runs(self, now: float) -> list:
        with self.lock:
            out = []
            for iid, h in self.data.items():
                for s in h["segments"]:
                    hours = ((s["end"] or now) - s["start"]) / 3600
                    out.append({"id": iid, "name": h.get("name"), "region": h.get("region"),
                                "account": h.get("account") or core.MAIN_ACCOUNT,
                                "type": h.get("type"), "market": h.get("market"),
                                "hours": hours,
                                "cost": None if s["rate"] is None else hours * s["rate"], **s})
            return sorted(out, key=lambda r: r["start"], reverse=True)[:200]


HISTORY = History()
REASON_TIME = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT\)")


def _reason_time(reason: str):
    """'User initiated (2026-11-17 18:00:00 GMT)' -> epoch seconds."""
    m = REASON_TIME.search(reason or "")
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc).timestamp()


def _link_parts(url: str):
    """https://x.trycloudflare.com/#k=KEY -> (origin, KEY)."""
    origin = "/".join(url.split("/")[:3])
    key = ""
    if "#" in url:
        key = (parse_qs(url.split("#", 1)[1]).get("k") or [""])[0]
    return origin, key


class Instances:
    """Polled in the background so /api/status answers at once. Also finds
    each running instance's app link, sounds the alarm when it opens, keeps
    the history, and points auto downloads at the current link."""

    def __init__(self):
        self.rows = []
        self.error = ""
        self.errors = []       # per account, e.g. one whose keys don't work
        self.links = {}        # instance id -> {"url", "checked", "ready", "probed"}
        self.announced = set()
        self.first = True
        self.wake = threading.Event()
        self.poll_lock = threading.Lock()

    def loop(self) -> None:
        while True:
            try:
                self.poll()
                self.error = ""
            except (ClientError, BotoCoreError) as exc:
                self.error = _err(exc)
            self.first = False
            self.wake.wait(10)
            self.wake.clear()

    def poll(self) -> None:
        with self.poll_lock:
            where = [(acc, reg) for acc in _accounts() for reg in REGIONS]
            with ThreadPoolExecutor(min(32, len(where))) as pool:
                found = dict(zip(where, pool.map(lambda w: self.region_rows(*w), where)))
            now = time.time()
            errors = sorted({rows for rows in found.values() if isinstance(rows, str)})
            found = {w: (None if isinstance(rows, str) else rows) for w, rows in found.items()}
            ok = {w for w, rows in found.items() if rows is not None}
            rows = [r for rs in found.values() if rs for r in rs]
            for r in rows:
                HISTORY.observe(r, now)
            HISTORY.lost({r["id"] for r in rows}, ok, now)
            HISTORY.save()
            rows = [r for r in rows if r["state"] != "terminated"]
            for r in rows:
                self.link(r)
                self.interruption(r)
            _follow_links(rows)
            rows.sort(key=lambda r: (r["state"] == "stopped", r["account"], r["region"], r["id"]))
            self.rows = rows
            self.errors = errors

    def interruption(self, row: dict) -> None:
        """The alarm when AWS takes a spot instance back: once at the ~2
        minute warning (the spot request says marked-for-stop), once when
        it has stopped. Not for what was already so when the page started."""
        code = row.get("spot_code") or ""
        row["interrupting"] = code.startswith("marked-for-") and row["state"] == "running"
        events = []
        if row["interrupting"]:
            events.append((row["id"], "warned", row["launched"]))
        if row["stopped_by_aws"]:
            events.append((row["id"], "stopped", row["launched"]))
        for ev in events:
            if ev not in self.announced:
                self.announced.add(ev)
                if not self.first:
                    _alarm()

    def region_rows(self, account: str, region: str):
        """This account's instances in this region, None when the region
        can't be read, or a message when the account's keys don't work."""
        try:
            ec2 = _client(region, account=account)
            resp = ec2.describe_instances(Filters=[
                {"Name": "tag:" + core.TAG_KEY, "Values": [core.TAG_VALUE]},
                {"Name": "instance-state-name",
                 "Values": ["pending", "running", "stopping", "stopped", "shutting-down",
                            "terminated"]}])
        except ValueError as exc:                    # no keys for this account
            return str(exc)
        except NoCredentialsError as exc:
            return "%s: %s" % (account, _err(exc))
        except ClientError as exc:
            bad_keys = core.code_of(exc) in ("AuthFailure", "InvalidClientTokenId",
                                             "SignatureDoesNotMatch")
            if bad_keys and region == "us-east-1":     # always enabled: say it once
                return "Account %s: %s" % (account, _err(exc))
            if bad_keys or core.code_of(exc) in QUIET_REGION_ERRORS:
                return None
            raise
        insts = [i for r in resp["Reservations"] for i in r["Instances"]]
        if not insts:
            return []
        live = [i["InstanceId"] for i in insts if i["State"]["Name"] != "terminated"]
        sirs = [i["SpotInstanceRequestId"] for i in insts if i.get("SpotInstanceRequestId")]
        status = {}
        if sirs:
            for s in ec2.describe_spot_instance_requests(
                    SpotInstanceRequestIds=sirs)["SpotInstanceRequests"]:
                status[s["SpotInstanceRequestId"]] = {
                    "state": s["State"], "code": s.get("Status", {}).get("Code", "")}
        disks = {}
        if live:
            for v in ec2.describe_volumes(Filters=[
                    {"Name": "attachment.instance-id", "Values": live}])["Volumes"]:
                for att in v.get("Attachments", []):
                    disks.setdefault(att["InstanceId"], []).append(v)
        prices = _spot_prices(region, account) if sirs else {}
        rates = _disk_rates(region)
        group_ids = sorted({g["GroupId"] for i in insts for g in i.get("SecurityGroups", [])})
        open_groups = set()
        if group_ids:
            for g in ec2.describe_security_groups(GroupIds=group_ids)["SecurityGroups"]:
                if _admits(g, APP_PORT):
                    open_groups.add(g["GroupId"])
        rows = []
        for i in insts:
            iid, sir = i["InstanceId"], i.get("SpotInstanceRequestId")
            zone, itype = i["Placement"]["AvailabilityZone"], i["InstanceType"]
            st = status.get(sir, {})
            name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "")
            code = st.get("code", "")
            vols = disks.get(iid, [])
            disk_month = None
            if rates is not None:
                disk_month = sum(_disk_monthly(rates, v["Size"], v.get("Throughput") or FREE_MIBPS,
                                               v.get("Iops") or FREE_IOPS) for v in vols)
            compute = prices.get((itype, zone)) if sir else _on_demand_price(region, itype)
            disk_hr = None if disk_month is None else disk_month / HOURS_PER_MONTH
            rows.append({
                "id": iid, "name": name, "account": account, "region": region, "zone": zone,
                "type": itype, "state": i["State"]["Name"],
                "market": "spot" if sir else "on-demand", "spot_request": sir,
                "spot_state": st.get("state"), "spot_code": code,
                "stopped_by_aws": (i["State"]["Name"] in ("stopping", "stopped")
                                   and code.startswith("instance-stopped-")
                                   and code != "instance-stopped-by-user"),
                "ip": i.get("PublicIpAddress"), "key": i.get("KeyName"),
                # ec2-1-2-3-4.<region>.compute.amazonaws.com: the IP as a name,
                # so it changes with it; the IP when the VPC gives no names.
                "host": i.get("PublicDnsName") or i.get("PublicIpAddress"),
                "port_open": any(g["GroupId"] in open_groups for g in i.get("SecurityGroups", [])),
                "launched": i["LaunchTime"].timestamp(),
                "changed": _reason_time(i.get("StateTransitionReason", "")),
                "price": compute,
                "disk_gb": sum(v["Size"] for v in vols), "disk_monthly": disk_month,
                "rate_running": (None if compute is None or disk_hr is None
                                 else compute + disk_hr + IPV4_HOURLY),
                "rate_stopped": disk_hr,
            })
        return rows

    def link(self, row: dict) -> None:
        iid = row["id"]
        if row["state"] != "running":
            self.links.pop(iid, None)
            row.update(url=None, ready=False)
            return
        info = self.links.setdefault(iid, {"url": None, "checked": 0, "ready": False, "probed": 0})
        now = time.time()
        direct = "http://%s:%d/" % (row["host"], APP_PORT) if row["port_open"] and row["host"] else None
        if direct:
            # The security group lets the app's port in: use it straight,
            # it follows the IP after a restart and needs no key.
            if info["url"] != direct:
                info.update(url=direct, ready=False, probed=0)
        elif now - info["checked"] >= (60 if info["ready"] else 20):
            info["checked"] = now
            try:
                out = _client(row["region"], account=row["account"]).get_console_output(
                    InstanceId=iid, Latest=True)
                text = base64.b64decode(out.get("Output") or "").decode("utf-8", "replace")
                urls = URL_RE.findall(text)
                if urls and urls[-1] != info["url"]:
                    info.update(url=urls[-1], ready=False, probed=0)
            except (ClientError, ValueError):
                pass
        if info["url"] and now - info["probed"] >= 15:    # the page's green/red dot
            info["probed"] = now
            info["ready"] = _reachable(info["url"])
        if info["ready"] and (iid, info["url"]) not in self.announced:
            self.announced.add((iid, info["url"]))
            if not self.first:
                _alarm()
        row.update(url=info["url"], ready=info["ready"])

    def busy(self, account: str) -> list:
        """The account's instances that block a new snipe there: running in
        any region, or stopped by AWS (a persistent spot request brings
        those back by itself)."""
        return [r for r in self.rows if r["account"] == account and
                (r["state"] in ("pending", "running", "stopping", "shutting-down")
                 or r["stopped_by_aws"])]


def _reachable(url: str) -> bool:
    origin = "/".join(url.split("/")[:3]) + "/"
    try:
        with urllib.request.urlopen(urllib.request.Request(
                origin, headers={"user-agent": "ember-aws-sniper"}), timeout=8) as r:
            return r.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


INSTANCES = Instances()


# ── the snipe ──────────────────────────────────────────────────────


class Snipe:
    def __init__(self, req: dict):
        self.req = req
        self.log = deque(maxlen=400)
        self.state = "sniping"
        self.stop = threading.Event()
        self.started = time.time()
        self.instance_id = None

    def say(self, msg: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + msg)

    def run(self) -> None:
        r = self.req
        args = types.SimpleNamespace(
            key_name=r["key"], sg=r["sg"], region=r["region"], types=r["types"],
            type=",".join(r["types"]), zones=r["zones"], spot=r["market"] == "spot",
            ami="", disk=r["disk"], throughput=r["throughput"], iam_profile="",
            name="ember-gpu", interval=20, minutes=0, dry_run=r["dry_run"],
            no_sound=not SOUND, account=r["account"])
        try:
            sn = core.Sniper(args, core.make_session(r["region"], PROFILE, r["account"]),
                             log=self.say, stop=self.stop)
            rc = sn.run()
            self.instance_id = sn.instance_id
            self.state = {0: "won" if sn.instance_id else "done", 3: "stopped"}.get(rc, "error")
        except SystemExit as exc:          # build_user_data: .env not filled in
            self.say(str(exc))
            self.state = "error"
        except Exception as exc:           # noqa: BLE001 — shown on the page
            self.say(_err(exc))
            self.state = "error"
        INSTANCES.wake.set()

    def view(self) -> dict:
        return {"state": self.state, "log": list(self.log), "region": self.req["region"],
                "account": self.req["account"],
                "market": self.req["market"], "types": self.req["types"],
                "started": self.started, "instance_id": self.instance_id,
                "dry_run": self.req["dry_run"]}


_snipes = {}                   # account -> its latest Snipe (one at a time per account)


def _start_snipe(body: dict) -> None:
    account = _account(body.get("account"))
    region = str(body.get("region") or "")
    if region not in REGIONS:
        raise ValueError("Unknown region.")
    market = body.get("market")
    if market not in QUOTAS:
        raise ValueError("Market must be spot or on-demand.")
    chosen = [t for t in body.get("types") or [] if t in TYPES]
    if not chosen:
        raise ValueError("Pick at least one instance type.")
    key, sg = str(body.get("key") or ""), str(body.get("sg") or "")
    if not key or not re.match(r"^sg-[0-9a-f]+$", sg):
        raise ValueError("Pick a key pair and a security group.")
    disk, tput = int(body.get("disk") or 75), int(body.get("throughput") or 125)
    if not 75 <= disk <= 2000 or not 125 <= tput <= 1000:
        raise ValueError("Disk 75-2000 GB, throughput 125-1000 MB/s.")
    dry = bool(body.get("dry_run"))
    with _lock:
        old = _snipes.get(account)
        if old and old.state == "sniping":
            raise ValueError("A snipe is already running in account %s." % account)
    if not dry:
        INSTANCES.poll()                   # fresh, not up to 10 s old
        busy = INSTANCES.busy(account)
        if busy:
            b = busy[0]
            why = "was stopped by AWS and will come back by itself" if b["stopped_by_aws"] \
                else "is " + b["state"]
            raise ValueError("%s (%s, %s) %s. Stop or delete it first: one instance at a time "
                             "per account." % (b["id"], b["type"], b["region"], why))
    run = Snipe({"account": account, "region": region, "market": market, "types": chosen,
                 "zones": [str(z) for z in body.get("zones") or []], "key": key,
                 "sg": sg, "disk": disk, "throughput": tput, "dry_run": dry})
    with _lock:
        _snipes[account] = run
    threading.Thread(target=run.run, daemon=True).start()


def _action(body: dict) -> str:
    region, iid, act = body.get("region"), str(body.get("id") or ""), body.get("action")
    if region not in REGIONS or not re.match(r"^i-[0-9a-f]+$", iid):
        raise ValueError("Bad instance.")
    ec2 = _client(region, account=_account(body.get("account")))
    inst = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
    if tags.get(core.TAG_KEY) != core.TAG_VALUE:
        raise ValueError("That instance wasn't launched by the sniper.")
    if act == "start":
        ec2.start_instances(InstanceIds=[iid])
        msg = "Starting %s." % iid
    elif act == "stop":
        ec2.stop_instances(InstanceIds=[iid])
        msg = "Stopping %s. The root disk stays; the models on the local SSD are wiped." % iid
    elif act == "delete":
        sir = inst.get("SpotInstanceRequestId")
        if sir:     # first, or the persistent request launches a replacement
            ec2.cancel_spot_instance_requests(SpotInstanceRequestIds=[sir])
        ec2.terminate_instances(InstanceIds=[iid])
        msg = "Deleting %s%s and its disk." % (iid, " (spot request %s cancelled)" % sir if sir else "")
    else:
        raise ValueError("Unknown action.")
    INSTANCES.wake.set()
    return msg


def _licence(account: str) -> dict:
    env = core.account_env(account)
    key = env.get("EMBER_LICENSE_KEY", "")
    ok = all(not core.is_placeholder(env.get(k, "")) for k in core.REQUIRED_ENV)
    return {"file": str(core.ENV_FILE), "ok": ok,
            "key": key if not core.is_placeholder(key) else "",
            "hf": not core.is_placeholder(env.get("HF_TOKEN", "")),
            "civitai": not core.is_placeholder(env.get("CIVITAI_TOKEN", ""))}


# ── on the instance, over SSH ──────────────────────────────────────

KNOWN_HOSTS = HERE / "known_hosts"     # instance IPs are reused: never trusted for long
GIT_SSH = Path(r"C:\Program Files\Git\usr\bin\ssh.exe")
_ops = {}                              # instance id -> the last restart/apply and its output


def _ssh_key(account: str = core.MAIN_ACCOUNT):
    raw = core.account_env(account).get("SSH_KEY_FILE", "").strip().strip('"')
    return Path(raw).expanduser() if raw else None


def _ssh_binary() -> str:
    # Git's ssh doesn't refuse a .pem that Windows file permissions leave
    # readable by others; Windows' own OpenSSH does.
    if sys.platform == "win32" and GIT_SSH.is_file():
        return str(GIT_SSH)
    import shutil
    found = shutil.which("ssh")
    if not found:
        raise ValueError("No ssh program on this PC.")
    return found


def _ssh(row: dict, command: str, stdin: str = None, timeout: int = 180) -> str:
    """Run command on the instance as ubuntu. Returns its output; raises
    ValueError with the output when it fails."""
    import subprocess
    key = _ssh_key(row["account"])
    if not key:
        raise ValueError("Set SSH_KEY_FILE in %s to your .pem file." % core.ENV_FILE)
    if not key.is_file():
        raise ValueError("SSH_KEY_FILE %s doesn't exist." % key)
    if row["state"] != "running" or not row.get("ip"):
        raise ValueError("%s isn't running." % row["id"])
    args = [_ssh_binary(), "-i", str(key), "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=" + str(KNOWN_HOSTS),
            "-o", "ConnectTimeout=15", "-o", "LogLevel=ERROR", "ubuntu@" + row["host"], command]
    try:
        res = subprocess.run(args, input=stdin, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError("No answer from the instance within %d s." % timeout)
    out = (res.stdout + res.stderr).strip()
    if res.returncode != 0:
        if "Permission denied (publickey)" in out:
            out += ("\n\nThe instance was launched with key pair '%s': SSH_KEY_FILE must be "
                    "that key's .pem." % row.get("key"))
        raise ValueError(out or "ssh failed (exit %d)." % res.returncode)
    return out


def _instance_row(iid: str) -> dict:
    row = next((r for r in INSTANCES.rows if r["id"] == iid), None)
    if not row:
        raise ValueError("Unknown instance (or not loaded yet).")
    return row


def _docker_logs(iid: str, lines: int = 300) -> str:
    row = _instance_row(iid)
    return _ssh(row, "sudo docker ps -a --filter name=^ember$ --format "
                     "'container: {{.Status}} ({{.Image}})'; echo; "
                     "sudo docker logs --tail %d --timestamps ember 2>&1" % lines, timeout=60)


def _app_op(iid: str, kind: str) -> None:
    """kind: restart (docker restart: the app reloads its licence and
    features) or apply (/etc/ember.env from aws_sniper/.env, then the
    container is recreated). Runs in the background; _ops has the result."""
    row = _instance_row(iid)
    if kind == "restart":
        command, stdin = ("sudo docker restart -t 60 ember && sudo docker ps --filter "
                          "name=^ember$ --format 'container: {{.Status}}'"), None
    elif kind == "apply":
        try:
            stdin = core.ember_env_text(core.account_env(row["account"]))
        except SystemExit as exc:
            raise ValueError(str(exc))
        command = ("sudo tee /etc/ember.env >/dev/null && sudo chmod 600 /etc/ember.env && "
                   "(sudo docker stop -t 60 ember; sudo docker rm -f ember) >/dev/null 2>&1; sudo "
                   + core.docker_run_command()
                   + " && sudo docker ps --filter name=^ember$ --format 'container: {{.Status}}'")
    else:
        raise ValueError("Unknown action.")
    with _lock:
        op = _ops.get(iid)
        if op and op["state"] == "running":
            raise ValueError("Already %s on this instance." % op["label"])
        op = _ops[iid] = {"kind": kind, "state": "running", "started": time.time(),
                          "label": "restarting the app" if kind == "restart"
                          else "applying .env and recreating the app",
                          "output": "", "finished": None}

    def work():
        try:
            out = _ssh(row, command, stdin=stdin, timeout=300)
            op.update(state="done", output=out)
        except ValueError as exc:
            op.update(state="error", output=str(exc))
        op["finished"] = time.time()
        info = INSTANCES.links.get(iid)
        if info:                       # its app is starting over: probe again
            info.update(ready=False, probed=0)
        INSTANCES.wake.set()
    threading.Thread(target=work, daemon=True).start()


# ── settings and money ─────────────────────────────────────────────


def _settings_file() -> dict:
    """settings.json: {"accounts": {name: {credit_total, regions, aws_credit}}}.
    A file from before accounts (those keys at the top) is main's."""
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get("accounts"), dict):
        old = {k: data[k] for k in ("credit_total", "regions", "aws_credit") if k in data}
        data = {"accounts": {core.MAIN_ACCOUNT: old} if old else {}}
    return data


def _settings(account: str) -> dict:
    """One account's settings: credit_total, regions: {region: {sg, key}}
    that "Set up region" made (the page picks them by default), aws_credit."""
    data = _settings_file()["accounts"].get(account) or {}
    credit = data.get("credit_total")
    regions = data.get("regions") if isinstance(data.get("regions"), dict) else {}
    aws = data.get("aws_credit") if isinstance(data.get("aws_credit"), dict) else None
    return {"credit_total": float(credit) if isinstance(credit, (int, float)) else DEFAULT_CREDIT,
            "regions": regions, "aws_credit": aws}


def _sync_credits(account: str) -> dict:
    """AWS's credit balance (Billing API GetCredits; AWS lists no price for
    it, unlike Cost Explorer). Sums the estimated amount, which includes
    this month's usage so far, of every enabled credit that hasn't
    expired. Saved in settings.json as aws_credit."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    account_id = _client("us-east-1", "sts", account).get_caller_identity()["Account"]
    resp = _client("us-east-1", "billing", account).get_credits(
        accountId=account_id, startDate=now - timedelta(days=364))
    credits = []
    for c in resp.get("credits", []):
        end = c.get("endDate")
        if c.get("creditStatus", "ENABLED") != "ENABLED" or (end and end <= now):
            continue
        amount = lambda k: float((c.get(k) or {}).get("currencyAmount") or 0)
        credits.append({"description": c.get("description") or c.get("creditType") or "credit",
                        "initial": amount("initialAmount"), "remaining": amount("remainingAmount"),
                        "estimated": amount("estimatedAmount"),
                        "expires": end.timestamp() if end else None})
    data = {"synced": time.time(), "estimated": sum(c["estimated"] for c in credits),
            "remaining": sum(c["remaining"] for c in credits),
            "initial": sum(c["initial"] for c in credits),
            "expires": min((c["expires"] for c in credits if c["expires"]), default=None),
            "credits": credits}
    s = _settings(account)
    s["aws_credit"] = data
    _write_settings(account, s)
    return data


def _write_settings(account: str, s: dict) -> None:
    with _lock:
        data = _settings_file()
        data["accounts"][account] = s
        SETTINGS_FILE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _save_settings(body: dict) -> dict:
    account = _account(body.get("account"))
    s = _settings(account)
    if "credit_total" in body:
        credit = float(body["credit_total"])
        if not 0 <= credit <= 1_000_000:
            raise ValueError("Credit must be between 0 and 1,000,000.")
        s["credit_total"] = round(credit, 2)
    _write_settings(account, s)
    return s


# ── first-time setup of a region: key pair + security group ───────

SG_NAME = "ember-sg"
KEYS_DIR = HERE / "keys"
NEW_KEY_NAME = "ember-key"


def _ssh_keygen() -> str:
    git = Path(r"C:\Program Files\Git\usr\bin\ssh-keygen.exe")
    if sys.platform == "win32" and git.is_file():
        return str(git)
    import shutil
    found = shutil.which("ssh-keygen")
    if not found:
        raise ValueError("No ssh-keygen on this PC (it comes with Git for Windows).")
    return found


def _public_key(pem: Path) -> str:
    import subprocess
    res = subprocess.run([_ssh_keygen(), "-y", "-f", str(pem)], capture_output=True,
                         text=True, timeout=30)
    if res.returncode != 0 or not res.stdout.startswith(("ssh-", "ecdsa-")):
        raise ValueError("Can't read %s: %s" % (pem, (res.stderr or res.stdout).strip()))
    return res.stdout.strip()


def _owner_only(path: Path) -> None:
    """ssh refuses a private key others can read. Windows' OpenSSH checks
    the file's ACL, so drop the inherited entries and keep only the user."""
    import os
    import subprocess
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        if user:
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", user + ":F"],
                           capture_output=True, timeout=30)
    else:
        path.chmod(0o600)


def _set_env_line(key: str, value: str) -> None:
    """Set KEY=value in aws_sniper/.env, keeping every other line."""
    path = core.ENV_FILE
    lines = path.read_text(encoding="utf-8").split("\n") if path.exists() else []
    for n, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[n] = "%s=%s" % (key, value)
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("%s=%s" % (key, value))
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def _setup_region(region: str, account: str) -> dict:
    """Make sure the account's region has the key pair for SSH_KEY_FILE (or
    a new one, saved to keys/ and written to .env) and ember-sg with 22 and
    7860 open. Creates only what's missing, so running it twice changes
    nothing. Another account uses main's SSH_KEY_FILE unless it has its
    own SSH_KEY_FILE__<name>, so one .pem opens every account's instances."""
    if region not in REGIONS:
        raise ValueError("Unknown region.")
    ec2 = _client(region, account=account)
    done = []
    # The key pair.
    pem = _ssh_key(account)
    existing = {k["KeyName"] for k in ec2.describe_key_pairs()["KeyPairs"]}
    if pem and pem.is_file():
        name = pem.stem
        if name not in existing:
            ec2.import_key_pair(KeyName=name, PublicKeyMaterial=_public_key(pem).encode())
            done.append("imported key pair %s from %s" % (name, pem))
    else:
        name = NEW_KEY_NAME
        KEYS_DIR.mkdir(exist_ok=True)
        pem = KEYS_DIR / (name + ".pem")
        if name in existing:
            if not pem.is_file():
                raise ValueError("%s already has a key pair %s, but its .pem isn't in %s. "
                                 "Set SSH_KEY_FILE in .env to it." % (region, name, KEYS_DIR))
        elif pem.is_file():          # made in another region: same key here
            ec2.import_key_pair(KeyName=name, PublicKeyMaterial=_public_key(pem).encode())
            done.append("imported key pair %s from %s" % (name, pem))
        else:
            material = ec2.create_key_pair(KeyName=name, KeyType="ed25519",
                                           KeyFormat="pem")["KeyMaterial"]
            pem.write_text(material, encoding="utf-8", newline="\n")
            _owner_only(pem)
            done.append("created key pair %s, saved to %s" % (name, pem))
        _set_env_line("SSH_KEY_FILE", str(pem))
    # The security group, in the default VPC.
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise ValueError("%s has no default VPC to put the security group in." % region)
    vpc = vpcs[0]["VpcId"]
    groups = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc]},
        {"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"]
    if groups:
        group = groups[0]
    else:
        gid = ec2.create_security_group(
            GroupName=SG_NAME, VpcId=vpc,
            Description="Ember GPU instances: SSH and the app port")["GroupId"]
        group = {"GroupId": gid, "IpPermissions": []}
        done.append("created security group %s (%s)" % (SG_NAME, gid))
    for port in (22, APP_PORT):
        if not _admits(group, port):
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=group["GroupId"], IpPermissions=[{
                        "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "ember sniper"}]}])
                done.append("opened port %d on %s" % (port, SG_NAME))
            except ClientError as exc:
                if core.code_of(exc) != "InvalidPermission.Duplicate":
                    raise
    s = _settings(account)
    s["regions"][region] = {"sg": group["GroupId"], "key": name}
    _write_settings(account, s)
    return {"region": region, "account": account, "sg": group["GroupId"], "key": name,
            "ssh_key": str(pem), "done": done or ["already set up: nothing to change"]}


def _add_account(body: dict) -> dict:
    """Check the keys with AWS (STS: who they belong to), then write them to
    .env as AWS_ACCESS_KEY_ID__<name> / AWS_SECRET_ACCESS_KEY__<name>."""
    name = str(body.get("name") or "").strip().lower()
    key, secret = str(body.get("key_id") or "").strip(), str(body.get("secret") or "").strip()
    if not core.ACCOUNT_RE.match(name) or name == core.MAIN_ACCOUNT:
        raise ValueError("Name: lowercase letters, digits and -, up to 31, not 'main'.")
    if name in _accounts():
        raise ValueError("There's already an account %s." % name)
    if not re.match(r"^A[SK]IA[A-Z0-9]{12,}$", key) or len(secret) < 30 or any(c.isspace() for c in secret):
        raise ValueError("That doesn't look like an access key id (AKIA...) and its secret.")
    try:
        ident = boto3.Session(aws_access_key_id=key, aws_secret_access_key=secret,
                              region_name="us-east-1").client("sts").get_caller_identity()
    except (ClientError, BotoCoreError) as exc:
        raise ValueError("AWS refused those keys: %s" % _err(exc))
    for k, v in (("AWS_ACCESS_KEY_ID", key), ("AWS_SECRET_ACCESS_KEY", secret)):
        _set_env_line(k + core.ACCOUNT_SEP + name, v)
    _forget_account(name)
    INSTANCES.wake.set()
    return {"name": name, "id": ident["Account"], "arn": ident["Arn"]}


def _accounts_view() -> list:
    names = _accounts()
    with ThreadPoolExecutor(len(names)) as pool:
        ids = list(pool.map(_account_id, names))
    out = []
    for name, (aid, error) in zip(names, ids):
        key = _ssh_key(name)
        out.append({"name": name, "id": aid, "error": error,
                    "source": core.credentials_source(PROFILE, name),
                    "ssh_key": str(key) if key else "", "licence": _licence(name)})
    return out


def _money(rows: list, account: str) -> dict:
    """Credit, what's been spent (from history.json), the burn rate now,
    and how long the rest lasts."""
    now = time.time()
    settings = _settings(account)
    credit = settings["credit_total"]
    spent = HISTORY.spent(now, account)
    synced = settings.get("aws_credit")
    rows = [r for r in rows if r["account"] == account]
    if synced:
        # AWS's own balance at the last sync (it already counts whatever
        # was used outside this page), minus what the page saw since.
        since_sync = HISTORY.spent_since(synced["synced"], now, account)
        left = synced["estimated"] - since_sync
    else:
        since_sync = None
        left = credit - spent["total"]
    running = [r for r in rows if r["state"] in ("pending", "running")]
    stopped = [r for r in rows if r["state"] in ("stopping", "stopped")]
    unknown = any(r["rate_running" if r in running else "rate_stopped"] is None
                  for r in running + stopped)
    burn = (sum(r["rate_running"] or 0 for r in running)
            + sum(r["rate_stopped"] or 0 for r in stopped))
    storage = sum(r["rate_stopped"] or 0 for r in rows if r["state"] != "shutting-down")
    return {"account": account, "credit_total": credit, "spent": spent["total"], "left": left,
            "aws": synced, "spent_since_sync": since_sync,
            "since": spent["since"], "per_instance": spent["per"],
            "unknown_rate": spent["unknown_rate"] or unknown,
            "burn_hourly": burn, "running": len(running),
            "hours_left": left / burn if burn > 0 and left > 0 else None,
            # if everything were stopped: the disks alone
            "storage_hourly": storage,
            "days_left_stopped": left / storage / 24 if storage > 0 and left > 0 else None}


# ── auto download ──────────────────────────────────────────────────

_downloads = {}                # instance id -> downloader.Download
_pick_lock = threading.Lock()


def _save_downloads() -> None:
    with _lock:
        active = [d.config() for d in _downloads.values() if d.status != "stopped"]
    tmp = DOWNLOADS_FILE.with_suffix(".json.part")
    tmp.write_text(json.dumps(active, indent=1), encoding="utf-8")
    tmp.replace(DOWNLOADS_FILE)


def _resume_downloads() -> None:
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


def _follow_links(rows: list) -> None:
    """A restarted instance has a new tunnel link and key: move its
    download over, so it carries on by itself."""
    changed = False
    for r in rows:
        with _lock:
            d = _downloads.get(r["id"])
        if not d or d.status == "stopped" or not r.get("url"):
            continue
        origin, key = _link_parts(r["url"])
        if (origin, key) != (d.app_url, d.access_key):
            d.app_url, d.access_key = origin, key
            d.event("The app has a new link: %s" % origin)
            changed = True
    if changed:
        _save_downloads()


def _start_download(body: dict) -> None:
    iid = str(body.get("instance_id") or "")
    row = next((r for r in INSTANCES.rows if r["id"] == iid), None)
    if not row:
        raise ValueError("Unknown instance.")
    if not row.get("url"):
        raise ValueError("No app link for this instance yet: wait until the page shows "
                         "'Open app'.")
    folder = _clean_folder(body.get("folder"))
    with _lock:
        old = _downloads.get(iid)
        if old and old.status not in ("stopped", "error"):
            raise ValueError("Already downloading from this instance. Stop it first.")
    origin, key = _link_parts(row["url"])
    # Off unless asked for: with a hold, each new file waits HOLD seconds so
    # one deleted in the Gallery meanwhile is never saved.
    hold = downloader.HOLD if body.get("hold") else 0
    d = downloader.Download(iid, (row["name"] or iid) + " · " + row["account"] + " · " + row["region"],
                            str(folder),
                            app_url=origin, access_key=key, account="aws", hold=hold)
    try:
        d.start()
    except OSError as exc:
        raise ValueError("Cannot use %s: %s" % (folder, exc))
    with _lock:
        _downloads[iid] = d
    _save_downloads()


def _clean_folder(raw) -> Path:
    text = str(raw or "").strip().strip('"')
    if not text:
        raise ValueError("Choose a folder first.")
    folder = Path(text).expanduser()
    if not folder.is_absolute():
        raise ValueError("Give the full path of the folder, e.g. C:\\Users\\you\\Downloads\\aws.")
    if folder.exists() and not folder.is_dir():
        raise ValueError("%s is a file, not a folder." % folder)
    return folder


def _pick_folder(initial: str):
    """The system's folder dialog on this PC. None if cancelled."""
    import tkinter as tk
    from tkinter import filedialog
    with _pick_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            start = initial if initial and Path(initial).is_dir() else str(Path.home())
            chosen = filedialog.askdirectory(parent=root, initialdir=start, mustexist=False,
                                             title="Folder for this instance's downloads")
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


# ── HTTP ────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:    # the page polls
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
        """Only this machine's page (no DNS rebinding): a web page elsewhere
        must not be able to launch instances."""
        host = (self.headers.get("host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:
        if not self.local():
            return self.error("Forbidden.", 403)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/tokens.css":
                return self.send(200, TOKENS_CSS.read_bytes(), "text/css; charset=utf-8")
            if url.path == "/api/config":
                return self.json({"regions": REGIONS, "types": TYPES, "env_file": str(core.ENV_FILE),
                                  "accounts": _accounts_view()})
            if url.path == "/api/region":
                region = query.get("region", [""])[0]
                if region not in REGIONS:
                    return self.error("Unknown region.")
                try:
                    account = _account(query.get("account", [""])[0])
                    return self.json(_region_view(region, account))
                except ValueError as exc:
                    return self.error(str(exc))
            if url.path == "/api/status":
                try:
                    account = _account(query.get("account", [""])[0])
                except ValueError:
                    account = core.MAIN_ACCOUNT          # gone from .env: show main's
                with _lock:
                    run = _snipes.get(account)
                    others = [v.view() for a, v in _snipes.items()
                              if a != account and v.state == "sniping"]
                    downloads = [d.view() for d in _downloads.values()]
                    ops = {k: dict(v) for k, v in _ops.items()}
                rows = INSTANCES.rows
                return self.json({"snipe": run.view() if run else None, "other_snipes": others,
                                  "instances": rows,
                                  "error": "\n".join(filter(None, [INSTANCES.error] + INSTANCES.errors)),
                                  "money": _money(rows, account), "downloads": downloads,
                                  "ops": ops, "now": time.time()})
            if url.path == "/api/history":
                return self.json({"runs": HISTORY.runs(time.time())})
            if url.path == "/api/logs":
                try:
                    return self.json({"logs": _docker_logs(query.get("id", [""])[0])})
                except ValueError as exc:
                    return self.error(str(exc))
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            return self.error(_err(exc), 502)
        self.error("Not found.", 404)

    def do_POST(self) -> None:
        # The custom header forces a CORS preflight, which is never
        # answered, so only this page's own fetch() can POST.
        if not self.local() or self.headers.get("x-sniper") != "1":
            return self.error("Forbidden.", 403)
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(min(length, 100_000)) or b"{}")
            if url.path == "/api/snipe":
                _start_snipe(body)
                return self.json({"ok": True})
            if url.path == "/api/snipe/stop":
                with _lock:
                    run = _snipes.get(str(body.get("account") or core.MAIN_ACCOUNT))
                if run:
                    run.stop.set()
                return self.json({"ok": True})
            if url.path == "/api/instance":
                return self.json({"ok": True, "message": _action(body)})
            if url.path == "/api/settings":
                return self.json(_save_settings(body))
            if url.path == "/api/credits/sync":
                return self.json(_sync_credits(_account(body.get("account"))))
            if url.path == "/api/setup":
                return self.json(_setup_region(str(body.get("region") or ""),
                                               _account(body.get("account"))))
            if url.path == "/api/accounts":
                return self.json(_add_account(body))
            if url.path == "/api/app":
                _app_op(str(body.get("id") or ""), str(body.get("action") or ""))
                return self.json({"ok": True})
            if url.path == "/api/downloads/start":
                _start_download(body)
                return self.json({"ok": True})
            if url.path in ("/api/downloads/stop", "/api/downloads/dismiss"):
                iid = str(body.get("instance_id") or "")
                with _lock:
                    d = _downloads.get(iid)
                    if d and url.path.endswith("dismiss") and d.status in ("stopped", "error"):
                        del _downloads[iid]
                if d and url.path.endswith("stop"):
                    d.stop()
                _save_downloads()
                return self.json({"ok": True})
            if url.path == "/api/pick-folder":
                try:
                    return self.json({"folder": _pick_folder(str(body.get("initial") or ""))})
                except Exception as exc:     # noqa: BLE001 — no tkinter, no display
                    return self.error("No folder dialog (%s): type the path instead." % exc)
            if url.path == "/api/open-folder":
                folder = _clean_folder(body.get("folder"))
                if not folder.is_dir():
                    return self.error("%s doesn't exist yet." % folder)
                _open_folder(folder)
                return self.json({"ok": True})
        except (ValueError, TypeError) as exc:
            return self.error(str(exc))
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            return self.error(_err(exc), 502)
        self.error("Not found.", 404)


def main() -> None:
    global PROFILE, SOUND
    ap = argparse.ArgumentParser(description="AWS GPU sniper page")
    ap.add_argument("--port", type=int, default=7896)
    ap.add_argument("--profile", default=None,
                    help="AWS CLI profile (default: the keys in aws_sniper/.env)")
    ap.add_argument("--no-sound", action="store_true")
    a = ap.parse_args()
    PROFILE, SOUND = a.profile, not a.no_sound
    _resume_downloads()
    threading.Thread(target=INSTANCES.loop, daemon=True).start()
    print("AWS sniper on http://127.0.0.1:%d  (accounts: %s)" % (a.port, ", ".join(
        "%s = %s" % (n, core.credentials_source(PROFILE, n)) for n in _accounts())), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
