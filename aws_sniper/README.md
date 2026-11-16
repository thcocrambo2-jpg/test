# AWS GPU sniper

Launches one EC2 GPU instance (default `g7e.2xlarge` in `us-east-1`) as soon
as any zone has room. AWS answers *Insufficient capacity* per zone, so the
script tries every zone that offers the type, round after round, until one
launch succeeds. Then it prints the IP and the SSH command, and plays the
alarm.

## Set up

```
pip install -r aws_sniper/requirements.txt
copy aws_sniper\.env.example aws_sniper\.env     # fill in the licence key and node tag
aws configure                                    # or aws configure sso, or --profile
```

`.env` is git-ignored. Its values go into the instance's user data
(`userdata.sh`), which writes `/etc/ember.env` and starts `emberai/ember:latest`.

You need a key pair and a security group (SSH from your IP) in the region.
The AWS identity needs `ec2:RunInstances`, `ec2:Describe*`,
`ec2:CreateTags` and `ssm:GetParameter`, plus `iam:PassRole` if you use
`--iam-profile`.

## Run

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
| `--disk` / `--throughput` | 200 GB / 250 MB/s | Root gp3 volume |
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
