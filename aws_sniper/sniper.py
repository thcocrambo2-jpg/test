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

Exactly one instance: a launch is one RunInstances call with MaxCount=1,
the calls are sequential, and the run stops at the first success. A run
also refuses to start while an instance it launched earlier (tag
sniper=ember) is pending or running, so a second snipe can't double the
bill. Stop or terminate that one first.

Credentials are the usual AWS ones (env vars, ~/.aws, or --profile).
The keys that go into the instance (licence, node tag, HF/CivitAI tokens)
come from aws_sniper/.env and are written into the user data from
userdata.sh; nothing else in the template is changed.
"""

import argparse
import random
import sys
import time
import uuid
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
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
PENDING_ERRORS = {"PendingVerification", "Blocked"}
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


def build_user_data(env: dict) -> str:
    missing = [k for k in REQUIRED_ENV if is_placeholder(env.get(k, ""))]
    if missing:
        sys.exit(f"Set {', '.join(missing)} in {ENV_FILE} (copy .env.example first).")
    for key, value in env.items():
        if key in ENV_KEYS and ("\n" in value or "\r" in value):
            sys.exit(f"{key} in {ENV_FILE} spans more than one line.")
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
    def __init__(self, args, session):
        self.a = args
        self.ec2 = session.client("ec2")
        self.ssm = session.client("ssm")

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
        return ami, image["RootDeviceName"], image.get("Name", "")

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
                say(f"already running: {i['InstanceId']} {i['InstanceType']} "
                    f"in {i['Placement']['AvailabilityZone']} ({i['State']['Name']})")
            say("Stop or terminate it first. Not launching a second one.")
            return 1

        user_data = build_user_data(read_env(ENV_FILE))
        ami, root_device, ami_name = self.resolve_ami()
        say(f"AMI {ami}  {ami_name}")

        # zones_of[type] = zones to try for that type, in preference order of types.
        offered = {t: self.zones(t) for t in a.types}
        all_zones = sorted({z for zs in offered.values() for z in zs})
        subnet_of = self.subnets(all_zones) if all_zones else {}
        zones_of = {}
        for t in a.types:
            zs = [z for z in offered[t] if z in subnet_of]
            if zs:
                zones_of[t] = zs
            elif offered[t]:
                say(f"{t}: no subnet in the security group's VPC sits in a zone that "
                    "offers it. Create one, or use a security group in the default VPC.")
            else:
                say(f"{t} is not offered in any zone of {a.region}.")
        if not zones_of:
            return 1
        market = "spot (persistent, stop on interruption)" if a.spot else "on-demand"
        say(f"{market}, {a.disk} GB gp3 @ {a.throughput} MB/s")
        for t, zs in zones_of.items():
            say(f"  {t}: {', '.join(zs)}")

        deadline = time.time() + a.minutes * 60 if a.minutes else None
        rounds = 0
        while True:
            rounds += 1
            order = []
            for t, zs in zones_of.items():      # types in preference order
                zs = zs[:]
                random.shuffle(zs)
                order += [(t, z) for z in zs]
            outcomes = []
            for itype, zone in order:
                where = f"{itype} {zone}" if len(zones_of) > 1 else zone
                params = self.launch_params(itype, ami, root_device, subnet_of[zone], user_data)
                try:
                    resp = self.ec2.run_instances(**params)
                except ClientError as err:
                    code = code_of(err)
                    if code == "DryRunOperation":
                        outcomes.append(f"{where}: would succeed")
                        continue
                    if code in CAPACITY_ERRORS:
                        outcomes.append(f"{where}: no capacity")
                        continue
                    if code in ZONE_ERRORS:
                        say(f"{where}: {message_of(err)} (dropping this zone)")
                        zones_of[itype].remove(zone)
                        if not zones_of[itype]:
                            del zones_of[itype]
                        continue
                    if code in THROTTLE_ERRORS:
                        outcomes.append(f"{where}: throttled")
                        time.sleep(5)
                        continue
                    if code in PENDING_ERRORS:
                        outcomes.append(f"{where}: account still being verified")
                        continue
                    say(f"{where}: {code}: {message_of(err)}")
                    say("Stopping: retrying won't fix this.")
                    return 2
                inst = resp["Instances"][0]
                return self.won(inst["InstanceId"], itype, zone)

            if a.dry_run:
                for line in outcomes:
                    say(line)
                say("Dry run done: nothing was launched.")
                return 0
            say(f"round {rounds}: " + "; ".join(outcomes))
            if not zones_of:
                say("No zone left to try.")
                return 1
            if deadline and time.time() >= deadline:
                say(f"Gave up after {a.minutes} min.")
                return 1
            wait = a.interval
            if any("verified" in o for o in outcomes):
                wait = max(wait, 120)
            time.sleep(wait)

    def won(self, instance_id: str, itype: str, zone: str) -> int:
        say(f"LAUNCHED {instance_id} ({itype}) in {zone}. Waiting for it to run...")
        alarm(not self.a.no_sound)
        try:
            self.ec2.get_waiter("instance_running").wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        except Exception as exc:       # the instance exists either way
            say(f"(waiter: {exc})")
        inst = self.ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        ip = inst.get("PublicIpAddress", "")
        say(f"state: {inst['State']['Name']}  public IP: {ip or 'none'}")
        if ip:
            say(f"ssh -i {self.a.key_name}.pem ubuntu@{ip}")
            say("then:  sudo docker logs -f ember     (the trycloudflare link appears there)")
        say("Billing has started. Stop the instance when you're done.")
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
    ap.add_argument("--disk", type=int, default=200, help="root gp3 size in GB (default 200)")
    ap.add_argument("--throughput", type=int, default=250, help="gp3 MB/s, 125-1000 (default 250)")
    ap.add_argument("--iam-profile", default="", help="instance profile name, e.g. one with SSM access")
    ap.add_argument("--name", default="ember-gpu", help="Name tag")
    ap.add_argument("--interval", type=int, default=20, help="seconds between rounds (default 20)")
    ap.add_argument("--minutes", type=int, default=0, help="give up after this many minutes (0 = never)")
    ap.add_argument("--profile", default=None, help="AWS CLI profile")
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
        session = boto3.Session(profile_name=a.profile, region_name=a.region)
        return Sniper(a, session).run()
    except NoCredentialsError:
        print("No AWS credentials. Run `aws configure` (or `aws configure sso`), or pass --profile.")
        return 1
    except ClientError as err:
        print(f"{code_of(err)}: {message_of(err)}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Nothing was launched in the round that was interrupted "
              "unless it said LAUNCHED above.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
