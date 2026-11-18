# AWS GPU sniper

Launches one EC2 GPU instance (default `g7e.2xlarge` in `us-east-1`) as soon
as any zone has room. AWS answers *Insufficient capacity* per zone, so the
script tries every zone that offers the type, round after round, until one
launch succeeds. Then it prints the IP and the SSH command, and plays the
alarm.

## For a friend (Windows)

`python aws_sniper/make_zip.py` builds `aws_sniper.zip`: the page plus
`start.bat`, without `.env`, keys or any local state. Your friend needs
Python 3.10+, a G and VT vCPU quota in their region (spot 8 for one
g7e.2xlarge) and an IAM access key.

1. Unzip, double-click `start.bat`. It installs boto3 the first time,
   makes `.env` from `.env.example` and opens it in Notepad.
2. Fill in `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `EMBER_LICENSE_KEY`, `EMBER_NODE_TAG` (tokens optional), save, run
   `start.bat` again. The page opens at http://127.0.0.1:7896.
3. Pick the region and Snipe. The first snipe in a region sets it up:
   creates the key pair `ember-key` (its `.pem` saved in `keys/`, its path
   written to `.env`) and `ember-sg` with ports 22 and 7860 open. Disk
   defaults to 250 GB.

## Set up

```
pip install -r aws_sniper/requirements.txt
copy aws_sniper\.env.example aws_sniper\.env     # AWS keys, licence key, node tag
```

AWS credentials: put `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in
`.env` (IAM → Users → Security credentials → Create access key). They stay
on this PC; only the licence/token lines go into the instance. Or leave
them empty and pass `--profile` for one set up with `aws configure`.

`.env` is git-ignored. The licence and token lines go into the instance's user data
(`userdata.sh`), which writes `/etc/ember.env` and starts `emberai/ember:latest`.

You need a key pair and a security group (SSH from your IP) in the region.
The AWS identity needs `ec2:RunInstances`, `ec2:Describe*`,
`ec2:CreateTags` and `ssm:GetParameter`, plus `iam:PassRole` if you use
`--iam-profile`.

## Page

```
python aws_sniper/server.py
```

Open http://127.0.0.1:7896. Pick region, market (spot now; on-demand
lights up once that quota is non-zero), instance types in order of
preference and zones. Each zone shows the spot price and placement score
(10 = easiest to get). The instance table covers every region: start,
stop, delete (cancels the spot request first), copy the SSH command, and
open the app. When the instance's security group lets port 7860 in, the
app link is `http://<public IP>:7860/` (it follows the IP after a restart,
and the app asks for no key by default, so anyone with the IP can use
it). Otherwise the page looks for the trycloudflare link that
`userdata.sh` copies to the serial console; AWS doesn't always refresh
console output, so the open port is the reliable way. The alarm sounds
when an instance launches and again when its app answers.

A snipe is refused while any instance it launched is running, or was
stopped by AWS (a persistent spot request brings that one back by itself).

**Sync with AWS** reads the real balance with the Billing API's
`GetCredits` (AWS lists no price for it, unlike Cost Explorer's $0.01 a
request): the estimated amount of every enabled, unexpired credit, which
includes this month's use so far, also anything used outside this page.
Credit left is then that balance minus what the page has seen run since
the sync. Sync now and then; AWS updates its estimate a few times a day.

**Credit (before the first sync).** Each instance shows its full hourly cost while running
(instance + disk + public IP) and its disk cost while stopped, from the
Pricing API and the spot price history. Spending isn't read from AWS
billing: `history.json` records every running and stopped stretch the
page sees, and the credit left is the total you set on the page
(`settings.json`, default $100) minus that. Gaps while the server was off
are counted as running when the exact stop time is unknown, so the
estimate errs high. Both files stay on this PC (git-ignored).

**Set up this region** (shown until a region is set up, once per region,
since key pairs and security groups are regional): imports the public key
of `SSH_KEY_FILE` as a key pair named after the file, or, with no
`SSH_KEY_FILE`, creates `ember-key`, saves its `.pem` to `aws_sniper/keys/`
(git-ignored) and writes `SSH_KEY_FILE` into `.env`; then creates
`ember-sg` in the default VPC with ports 22 and 7860 open. It only adds
what's missing, and the page picks that key and group by default after.

**App** (each running instance, over SSH with `SSH_KEY_FILE` from `.env`):
the app link with a green/red dot, **Restart app** (`docker restart`: the
app reloads its licence, for features added to the key), **Apply .env &
recreate** (writes `/etc/ember.env` from `aws_sniper/.env` and recreates
the container with the `docker run` line from `userdata.sh`; models stay),
and the last 300 lines of `docker logs`. Both restarts lose the app's queue.

**Auto download** uses `runpod_sniper/downloader.py` unchanged: it saves
each new Gallery file, with its settings token, into a folder you choose,
from the instance's tunnel link and the link's `#k=` key. A restart's new
link is picked up by itself, and downloads resume when the server
restarts (`downloads.json`).

## Run from the command line

```
python aws_sniper/sniper.py --key-name ember-key --sg sg-0123456789abcdef0 --dry-run
python aws_sniper/sniper.py --key-name ember-key --sg sg-0123456789abcdef0
python aws_sniper/sniper.py --key-name ember-key --sg sg-0123456789abcdef0 --spot
```

| Flag | Default | Meaning |
|---|---|---|
| `--type` | `g7e.2xlarge` | Instance type, or several in order of preference: `g7e.2xlarge,g6e.2xlarge` tries every g7e zone first each round, then g6e |
| `--region` | `us-east-1` | Region. The quota is per region. |
| `--spot` | off | Persistent spot that stops (not terminates) when interrupted |
| `--disk` / `--throughput` | 250 GB / 250 MB/s | Root gp3 volume. The full model set (Krea + MiniMax) needs ~205 GB with the OS and image; 200 GB ran out. |
| `--ami` | latest DLAMI Base OSS Nvidia (Ubuntu 24.04) | AMI id |
| `--iam-profile` | none | Instance profile, e.g. one with SSM access |
| `--interval` | 20 s | Pause between rounds |
| `--minutes` | 0 (never) | Give up after this long |
| `--dry-run` | off | Check permissions in every zone; launches nothing |
| `--no-sound` | off | No alarm |

## Safety

- Exactly one instance: one `RunInstances` call at a time with
  `MaxCount=1`, and the run stops at the first success.
- It refuses to start while an instance it launched earlier (tag
  `sniper=ember`) is pending or running.
- Quota, permission and parameter errors stop the run, since retrying
  won't fix them. Capacity and throttling errors are retried.

**Spot:** a persistent spot request relaunches the instance if you only
terminate the instance. To get rid of it, cancel the spot request first
(EC2 → Spot Requests → Cancel), then terminate the instance.
