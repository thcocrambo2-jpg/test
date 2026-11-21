"""AWS GPU sniper: launch one EC2 GPU instance as soon as any zone has room.

AWS answers "InsufficientInstanceCapacity" when a zone has no free host
for the type. Capacity differs per zone and changes minute to minute, so
this tries every zone in the region that offers the type, round after
round, until one launch succeeds. Several types can be given in order of
preference (--type g7e.2xlarge,g6e.2xlarge): each round tries every zone
of the first type, then of the next, and takes whichever launches first. Then it waits for the instance to run,
prints how to reach it, and sounds the alarm.

    python aws_sniper/sniper.py --key-name ember-key --sg sg-0123...
    python aws_sniper/sniper.py ... --spot          # spot instead of on-demand
    python aws_sniper/sniper.py ... --type g7e.2xlarge,g6e.2xlarge   # fallback
    python aws_sniper/sniper.py ... --dry-run       # check permissions only

One instance: a launch is one RunInstances call with MaxCount=1, and no
new round starts after the first success. Rounds overlap (a new one every
--interval seconds), so a try already on its way can still land a second
instance; the log then says EXTRA INSTANCE and you terminate it. A run
also refuses to start while an instance it launched earlier (tag
sniper=ember) is pending or running, so a second snipe can't double the
bill. Stop or terminate that one first.

Credentials: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in aws_sniper/.env,
or --profile, or the usual AWS places (env vars, ~/.aws). More accounts:
AWS_ACCESS_KEY_ID__<name> / AWS_SECRET_ACCESS_KEY__<name>, picked with
--account <name>; any other .env line ending in __<name> (SSH_KEY_FILE,
the licence, the tokens) overrides that line for that account.
The keys that go into the instance (licence, node tag, HF/CivitAI tokens)
come from aws_sniper/.env and are written into the user data from
userdata.sh; nothing else in the template is changed.
"""

import argparse
import queue
import random
import re
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, HTTPClientError, NoCredentialsError
    from botocore.exceptions import ConnectionError as BotoConnectionError
except ImportError:
    sys.exit("boto3 is missing:  pip install -r aws_sniper/requirements.txt")

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
USERDATA_TEMPLATE = HERE / "userdata.sh"

DEFAULT_AMI_PARAM = ("/aws/service/deeplearning/ami/x86_64/"
                     "base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id")
TAG_KEY, TAG_VALUE = "sniper", "ember"
ENV_KEYS = ("EMBER_LICENSE_KEY", "EMBER_NODE_TAG", "HF_TOKEN", "CIVITAI_TOKEN")
REQUIRED_ENV = ("EMBER_LICENSE_KEY", "EMBER_NODE_TAG")
PLACEHOLDER_HINTS = ("your-", "xxxx", "XXXX")

# Errors that mean "no room right now": try the next zone / next round.
CAPACITY_ERRORS = {"InsufficientInstanceCapacity", "InsufficientCapacity",
                   "InsufficientHostCapacity", "InsufficientReservedInstanceCapacity",
                   "SpotMaxPriceTooLow"}
# The zone will never offer the type: drop it for the rest of the run.
ZONE_ERRORS = {"Unsupported", "InvalidAvailabilityZone"}
# Slow down and keep going.
THROTTLE_ERRORS = {"RequestLimitExceeded", "Throttling", "ThrottlingException"}
# The account is still being verified: keep going, but slowly.
PENDING_ERRORS = {"PendingVerification"}
# The account itself is held (billing or verification); retrying never clears it.
BLOCKED_ERRORS = {"Blocked"}
# The vCPU quota is full: after a launch, that just means another round won.
QUOTA_ERRORS = {"VcpuLimitExceeded", "InstanceLimitExceeded", "MaxSpotInstanceCountExceeded"}
# Rounds that may run at once (each one is a few seconds of tries).
MAX_ROUNDS_IN_FLIGHT = 4
# Anything else (quota, permissions, bad parameters) is fatal: retrying
# would only repeat it.


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def alarm(enabled: bool) -> None:
    """Windows: Alarm01.wav twice. Elsewhere: the terminal bell."""
    if not enabled:
        return
    if sys.platform == "win32":
        import winsound
        for _ in range(2):
            winsound.PlaySound(r"C:\Windows\Media\Alarm01.wav", winsound.SND_FILENAME)
        return
    sys.stdout.write("\a\a")
    sys.stdout.flush()


def read_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_placeholder(value: str) -> bool:
    return not value or any(h in value for h in PLACEHOLDER_HINTS)


MAIN_ACCOUNT = "main"
ACCOUNT_SEP = "__"
ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
AWS_KEY_NAMES = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def accounts() -> list:
    """"main" (the plain AWS_* lines) and every <name> with an
    AWS_ACCESS_KEY_ID__<name> line in .env, in file order."""
    names = [MAIN_ACCOUNT]
    prefix = "AWS_ACCESS_KEY_ID" + ACCOUNT_SEP
    for key in read_env(ENV_FILE):
        name = key[len(prefix):] if key.startswith(prefix) else ""
        if ACCOUNT_RE.match(name) and name not in names:
            names.append(name)
    return names


def account_env(account: str = None) -> dict:
    """.env as one account sees it: the plain lines, with any
    <KEY>__<account> line in place of <KEY>. Another account never borrows
    main's AWS keys (that would launch in main's account)."""
    env = read_env(ENV_FILE)
    out = {k: v for k, v in env.items() if ACCOUNT_SEP not in k}
    if account and account != MAIN_ACCOUNT:
        for k in AWS_KEY_NAMES:
            out.pop(k, None)
        suffix = ACCOUNT_SEP + account
        for k, v in env.items():
            if k.endswith(suffix):
                out[k[:-len(suffix)]] = v
    return out


def aws_keys(account: str = None) -> dict:
    """AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN, if
    any) from .env, or {} when they aren't filled in. These stay on this PC:
    only ENV_KEYS go into the instance's user data."""
    env = account_env(account)
    key, secret = env.get("AWS_ACCESS_KEY_ID", ""), env.get("AWS_SECRET_ACCESS_KEY", "")
    if is_placeholder(key) or is_placeholder(secret):
        return {}
    out = {"aws_access_key_id": key, "aws_secret_access_key": secret}
    if not is_placeholder(env.get("AWS_SESSION_TOKEN", "")):
        out["aws_session_token"] = env["AWS_SESSION_TOKEN"]
    return out


def make_session(region: str, profile=None, account: str = None):
    """--profile when given, else the account's keys in .env, else (main
    only) AWS's usual places (environment variables, ~/.aws, aws configure)."""
    if profile and (not account or account == MAIN_ACCOUNT):
        return boto3.Session(profile_name=profile, region_name=region)
    keys = aws_keys(account)
    if not keys and account and account != MAIN_ACCOUNT:
        raise ValueError("Account %s has no AWS_ACCESS_KEY_ID__%s / AWS_SECRET_ACCESS_KEY__%s in %s."
                         % (account, account, account, ENV_FILE))
    return boto3.Session(region_name=region, **keys)


def credentials_source(profile=None, account: str = None) -> str:
    if profile and (not account or account == MAIN_ACCOUNT):
        return "AWS profile " + profile
    keys = aws_keys(account)
    if keys:
        return "access key %s...%s from .env" % (keys["aws_access_key_id"][:4],
                                               keys["aws_access_key_id"][-4:])
    return "AWS default credentials"


def check_env(env: dict) -> None:
    missing = [k for k in REQUIRED_ENV if is_placeholder(env.get(k, ""))]
    if missing:
        sys.exit(f"Set {', '.join(missing)} in {ENV_FILE} (copy .env.example first).")
    for key, value in env.items():
        if key in ENV_KEYS and ("\n" in value or "\r" in value):
            sys.exit(f"{key} in {ENV_FILE} spans more than one line.")


def ember_env_text(env: dict) -> str:
    """/etc/ember.env as userdata.sh writes it: the app's four values only."""
    check_env(env)
    return "".join("%s=%s\n" % (k, "" if is_placeholder(env.get(k, "")) else env[k])
                   for k in ENV_KEYS)


def docker_run_command() -> str:
    """The `docker run ... emberai/ember:latest` line from userdata.sh, on one
    line, so the page recreates the container exactly as first boot made it."""
    import re
    text = USERDATA_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"^docker run .*?emberai/ember:\S+", text, re.S | re.M)
    if not m:
        raise RuntimeError("No `docker run` line in %s." % USERDATA_TEMPLATE)
    return " ".join(m.group(0).replace("\\\n", " ").split())


def build_user_data(env: dict) -> str:
    check_env(env)
    text = USERDATA_TEMPLATE.read_text(encoding="utf-8")
    for key in ENV_KEYS:
        value = env.get(key, "")
        if is_placeholder(value):
            value = ""
        text = text.replace("{{" + key + "}}", value)
    return text


def code_of(err: ClientError) -> str:
    return err.response.get("Error", {}).get("Code", "")


def message_of(err: ClientError) -> str:
    return err.response.get("Error", {}).get("Message", str(err))


class Sniper:
    """args: the CLI's namespace (server.py builds the same fields). log
    prints a line; stop, when set, ends the run between attempts."""

    def __init__(self, args, session, log=None, stop: threading.Event = None):
        self.a = args
        self.ec2 = session.client("ec2")
        self.ssm = session.client("ssm")
        self.say = log or say
        self.stop = stop or threading.Event()
        self.instance_id = None
        self.wins = []                  # (instance id, type, zone), in launch order
        self.lock = threading.Lock()

    # ── setup ───────────────────────────────────────────────────────

    def running_already(self) -> list:
        resp = self.ec2.describe_instances(Filters=[
            {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
        ])
        return [i for r in resp["Reservations"] for i in r["Instances"]]

    def resolve_ami(self) -> tuple:
        ami = self.a.ami
        if not ami:
            ami = self.ssm.get_parameter(Name=DEFAULT_AMI_PARAM)["Parameter"]["Value"]
        image = self.ec2.describe_images(ImageIds=[ami])["Images"][0]
        root = image["RootDeviceName"]
        need = max((b.get("Ebs", {}).get("VolumeSize") or 0 for b in image.get("BlockDeviceMappings", [])
                    if b.get("DeviceName") == root), default=0)
        if need > self.a.disk:
            self.say(f"The AMI needs a {need} GB root disk; using {need} GB instead of {self.a.disk}.")
            self.a.disk = need
        return ami, root, image.get("Name", "")

    def zones(self, itype: str) -> list:
        resp = self.ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [itype]}])
        return sorted(o["Location"] for o in resp["InstanceTypeOfferings"])

    def subnets(self, zones: list) -> dict:
        """One subnet per zone, in the security group's VPC. Subnets that
        hand out public IPs (the default VPC's) win."""
        sg = self.ec2.describe_security_groups(GroupIds=[self.a.sg])["SecurityGroups"][0]
        resp = self.ec2.describe_subnets(Filters=[
            {"Name": "vpc-id", "Values": [sg["VpcId"]]},
            {"Name": "availability-zone", "Values": zones},
        ])
        best = {}
        for s in resp["Subnets"]:
            rank = (s.get("DefaultForAz", False), s.get("MapPublicIpOnLaunch", False),
                    s.get("AvailableIpAddressCount", 0))
            zone = s["AvailabilityZone"]
            if zone not in best or rank > best[zone][0]:
                best[zone] = (rank, s["SubnetId"])
        return {z: v[1] for z, v in best.items()}

    # ── one launch attempt ──────────────────────────────────────────

    def launch_params(self, itype: str, ami: str, root_device: str, subnet: str,
                      user_data: str) -> dict:
        p = {
            "ImageId": ami,
            "InstanceType": itype,
            "MinCount": 1,
            "MaxCount": 1,
            "KeyName": self.a.key_name,
            "UserData": user_data,
            "ClientToken": uuid.uuid4().hex,
            "NetworkInterfaces": [{
                "DeviceIndex": 0,
                "SubnetId": subnet,
                "Groups": [self.a.sg],
                "AssociatePublicIpAddress": True,
                "DeleteOnTermination": True,
            }],
            "BlockDeviceMappings": [{
                "DeviceName": root_device,
                "Ebs": {
                    "VolumeSize": self.a.disk,
                    "VolumeType": "gp3",
                    "Iops": 3000,
                    "Throughput": self.a.throughput,
                    "DeleteOnTermination": True,
                },
            }],
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "InstanceInitiatedShutdownBehavior": "stop",
            "TagSpecifications": [
                {"ResourceType": rt, "Tags": [
                    {"Key": "Name", "Value": self.a.name},
                    {"Key": TAG_KEY, "Value": TAG_VALUE},
                ]} for rt in ("instance", "volume")
            ],
        }
        if self.a.spot:
            p["InstanceMarketOptions"] = {
                "MarketType": "spot",
                "SpotOptions": {
                    "SpotInstanceType": "persistent",
                    "InstanceInterruptionBehavior": "stop",
                },
            }
        if self.a.iam_profile:
            p["IamInstanceProfile"] = {"Name": self.a.iam_profile}
        if self.a.dry_run:
            p["DryRun"] = True
        return p

    # ── the run ─────────────────────────────────────────────────────

    def run(self) -> int:
        a = self.a
        live = self.running_already()
        if live:
            for i in live:
                self.say(f"already running: {i['InstanceId']} {i['InstanceType']} "
                    f"in {i['Placement']['AvailabilityZone']} ({i['State']['Name']})")
            self.say("Stop or terminate it first. Not launching a second one.")
            return 1

        user_data = build_user_data(account_env(getattr(a, "account", None)))
        ami, root_device, ami_name = self.resolve_ami()
        self.say(f"AMI {ami}  {ami_name}")

        # zones_of[type] = zones to try for that type, in preference order of types.
        offered = {t: self.zones(t) for t in a.types}
        all_zones = sorted({z for zs in offered.values() for z in zs})
        subnet_of = self.subnets(all_zones) if all_zones else {}
        zones_of = {}
        only = set(getattr(a, "zones", None) or [])
        for t in a.types:
            zs = [z for z in offered[t] if z in subnet_of and (not only or z in only)]
            if zs:
                zones_of[t] = zs
            elif only and offered[t]:
                self.say(f"{t}: none of the chosen zones offer it.")
            elif offered[t]:
                self.say(f"{t}: no subnet in the security group's VPC sits in a zone that "
                    "offers it. Create one, or use a security group in the default VPC.")
            else:
                self.say(f"{t} is not offered in any zone of {a.region}.")
        if not zones_of:
            return 1
        market = "spot (persistent, stop on interruption)" if a.spot else "on-demand"
        self.say(f"{market}, {a.disk} GB gp3 root @ {a.throughput} MB/s; models and Docker on the local NVMe SSD")
        for t, zs in zones_of.items():
            self.say(f"  {t}: {', '.join(zs)}")

        deadline = time.time() + a.minutes * 60 if a.minutes else None
        if a.dry_run:                   # one pass, then say what would happen
            res = self.one_round(1, zones_of, ami, root_device, subnet_of, user_data)
            if res[0] == "fatal":
                return res[1]
            for line in res[1]:
                self.say(line)
            self.say("Dry run done: nothing was launched.")
            return 0

        # Overlapping rounds: a new one starts every --interval seconds even
        # while earlier ones are still going (each try takes ~2 s, so a full
        # round takes longer than the interval). The first launch wins and
        # no new round starts after it; a try already on its way can still
        # land a second instance, which is reported loudly below.
        results = queue.Queue()
        active, rounds, next_start = 0, 0, time.time()
        fatal = None
        while True:
            now = time.time()
            starting = (not self.wins and fatal is None and not self.stop.is_set()
                        and zones_of and active < MAX_ROUNDS_IN_FLIGHT and now >= next_start
                        and not (deadline and now >= deadline))
            if starting:
                rounds += 1
                active += 1
                next_start = now + a.interval
                threading.Thread(target=lambda n=rounds: results.put(self.one_round(
                    n, zones_of, ami, root_device, subnet_of, user_data)), daemon=True).start()
            elif active == 0 and (self.wins or fatal is not None or self.stop.is_set()
                                  or not zones_of or (deadline and now >= deadline)):
                break                   # nothing running and nothing more to start
            try:
                res = results.get(timeout=0.2)
            except queue.Empty:
                continue
            active -= 1
            if res[0] == "fatal":
                fatal = res[1] if fatal is None else fatal
            elif res[0] == "slow":      # account still being verified: back off
                next_start = max(next_start, time.time() + 120)

        if self.wins:
            first = self.wins[0]
            for extra in self.wins[1:]:
                self.say(f"!!! EXTRA INSTANCE {extra[0]} ({extra[1]}) in {extra[2]} also launched. "
                         "TERMINATE IT: it is billing too.")
            rc = self.won(*first)
            if len(self.wins) > 1:
                self.say("!!! Reminder: terminate the extra instance(s): "
                         + ", ".join(w[0] for w in self.wins[1:]))
            return rc
        if fatal is not None:
            return fatal
        if self.stop.is_set():
            self.say("Stopped. Nothing was launched.")
            return 3
        if not zones_of:
            self.say("No zone left to try.")
            return 1
        self.say(f"Gave up after {a.minutes} min.")
        return 1

    def one_round(self, n: int, zones_of: dict, ami, root_device, subnet_of, user_data) -> tuple:
        """Try every (type, zone) once, types in preference order. Returns
        ("fatal", rc), ("slow", outcomes) when the account is still being
        verified, or ("done", outcomes). A launch goes into self.wins."""
        with self.lock:
            order = []
            for t, zs in zones_of.items():      # types in preference order
                zs = zs[:]
                random.shuffle(zs)
                order += [(t, z) for z in zs]
            several = len(zones_of) > 1
        outcomes = []
        for itype, zone in order:
            if self.stop.is_set() or self.wins:
                return ("done", outcomes)       # quietly: the run is ending
            where = f"{itype} {zone}" if several else zone
            params = self.launch_params(itype, ami, root_device, subnet_of[zone], user_data)
            try:
                resp = self.run_instances_retrying(params)
                if resp is None:
                    return ("done", outcomes)
            except ClientError as err:
                code = code_of(err)
                if code == "DryRunOperation":
                    outcomes.append(f"{where}: would succeed")
                    continue
                if code in CAPACITY_ERRORS:
                    outcomes.append(f"{where}: no capacity")
                    continue
                if self.wins and code in QUOTA_ERRORS:
                    continue        # another round already launched: quota full
                if code in ZONE_ERRORS:
                    self.say(f"{where}: {message_of(err)} (dropping this zone)")
                    with self.lock:
                        if zone in zones_of.get(itype, []):
                            zones_of[itype].remove(zone)
                            if not zones_of[itype]:
                                del zones_of[itype]
                    continue
                if code in THROTTLE_ERRORS:
                    outcomes.append(f"{where}: throttled")
                    time.sleep(5)
                    continue
                if code in BLOCKED_ERRORS:
                    self.say(f"{where}: {code}: {message_of(err)}")
                    self.say("Stopping: AWS has blocked this account. Check its email and "
                             "Billing > Payments, and open the account-verification case "
                             "linked above; sniping can't get past this.")
                    return ("fatal", 2)
                if code in PENDING_ERRORS:
                    outcomes.append(f"{where}: account still being verified "
                                    f"({code}: {message_of(err)})")
                    continue
                self.say(f"{where}: {code}: {message_of(err)}")
                self.say("Stopping: retrying won't fix this.")
                return ("fatal", 2)
            inst = resp["Instances"][0]
            with self.lock:
                self.wins.append((inst["InstanceId"], itype, zone))
            return ("done", outcomes)
        if not self.a.dry_run and not self.wins and not self.stop.is_set():
            self.say(f"round {n}: " + "; ".join(outcomes))
        kind = "slow" if any("verified" in o for o in outcomes) else "done"
        return (kind, outcomes)

    def run_instances_retrying(self, params: dict):
        """run_instances, retried through network trouble (timeouts, dropped
        connections) with the same request. Its ClientToken makes the retry
        safe: if the lost attempt did launch, AWS hands back that instance
        instead of starting a second one. None when stopped meanwhile."""
        while True:
            try:
                return self.ec2.run_instances(**params)
            except (BotoConnectionError, HTTPClientError) as err:   # network only
                self.say(f"network trouble ({err}); retrying the same request in 10 s")
                if self.stop.wait(10):
                    return None

    def won(self, instance_id: str, itype: str, zone: str) -> int:
        self.instance_id = instance_id
        self.say(f"LAUNCHED {instance_id} ({itype}) in {zone}. Waiting for it to run...")
        alarm(not self.a.no_sound)
        try:
            self.ec2.get_waiter("instance_running").wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        except Exception as exc:       # the instance exists either way
            self.say(f"(waiter: {exc})")
        inst = self.ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        ip = inst.get("PublicIpAddress", "")
        host = inst.get("PublicDnsName") or ip
        self.say(f"state: {inst['State']['Name']}  public IP: {ip or 'none'}")
        if ip:
            self.say(f"ssh -i {self.a.key_name}.pem ubuntu@{host}")
            self.say(f"app (once port 7860 is open and it has started): http://{host}:7860/")
            self.say("then:  sudo docker logs -f ember     (the trycloudflare link appears there)")
        self.say("Billing has started. Stop the instance when you're done.")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Launch one EC2 GPU instance as soon as a zone has room.")
    ap.add_argument("--key-name", required=True, help="EC2 key pair name")
    ap.add_argument("--sg", required=True, help="security group id (sg-...)")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--type", default="g7e.2xlarge",
                    help="instance type, or several in order of preference, comma-separated "
                         "(default g7e.2xlarge)")
    ap.add_argument("--spot", action="store_true", help="spot instead of on-demand")
    ap.add_argument("--ami", default="", help="AMI id (default: latest DLAMI Base OSS Nvidia, Ubuntu 24.04)")
    ap.add_argument("--disk", type=int, default=75,
                    help="root gp3 size in GB, OS only: models and Docker go on the local NVMe SSD "
                         "(default 75, the AMI's minimum)")
    ap.add_argument("--throughput", type=int, default=125, help="root gp3 MB/s, 125-1000 (default 125, free)")
    ap.add_argument("--iam-profile", default="", help="instance profile name, e.g. one with SSM access")
    ap.add_argument("--name", default="ember-gpu", help="Name tag")
    ap.add_argument("--interval", type=int, default=20, help="seconds between round starts; rounds overlap (default 20)")
    ap.add_argument("--minutes", type=int, default=0, help="give up after this many minutes (0 = never)")
    ap.add_argument("--account", default=None,
                    help="account name from .env (AWS_ACCESS_KEY_ID__<name>); default: the plain keys")
    ap.add_argument("--profile", default=None,
                    help="AWS CLI profile (default: the keys in aws_sniper/.env)")
    ap.add_argument("--dry-run", action="store_true", help="check permissions in every zone, launch nothing")
    ap.add_argument("--no-sound", action="store_true")
    a = ap.parse_args()

    if not 125 <= a.throughput <= 1000:
        ap.error("--throughput must be between 125 and 1000")
    if a.interval < 5:
        ap.error("--interval must be at least 5 seconds")
    a.types = list(dict.fromkeys(t.strip() for t in a.type.split(",") if t.strip()))
    if not a.types:
        ap.error("--type is empty")

    try:
        session = make_session(a.region, a.profile, a.account)
        return Sniper(a, session).run()
    except NoCredentialsError:
        print("No AWS credentials. Run `aws configure` (or `aws configure sso`), or pass --profile.")
        return 1
    except ClientError as err:
        print(f"{code_of(err)}: {message_of(err)}")
        return 1
    except ValueError as err:          # make_session: the account has no keys
        print(err)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Nothing was launched in the round that was interrupted "
              "unless it said LAUNCHED above.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
