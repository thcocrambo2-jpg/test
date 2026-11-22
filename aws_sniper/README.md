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
   written to `.env`) and `ember-sg` with ports 22 and 7860 open. Models
   go on the instance's local SSD; the root disk is 75 GB.

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
It never writes over a `.pem`, and stops instead of guessing when
`SSH_KEY_FILE` (or the account's `SSH_KEY_FILE__<name>`) points to a file
that doesn't exist, or when the region already has a key pair of that name
holding a different key.

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

## Image to prompt (second tab)

Upload one picture (drop, paste or choose); it stays the current picture
for every Generate until you upload another or click one of the earlier
pictures. Tick one or more base prompts (`v1` copies the photo's body
exactly, `v2` writes flowing photographer-style prose) and one or both
models: **Qwen3-VL 235B** ($0.53 / $2.66 per million tokens in / out) or
**Kimi K2.5** ($0.60 / $3.00). Generate writes one prompt per model and
base prompt, all at once (both and both = 4 prompts), on Bedrock in
us-east-1 with the picked account's keys. The optional changes box
overrides the photo.

**Several at once:** Generate never waits. Each click saves that picture,
models, base prompts, changes and the auto-send tick as a run; up to 5 runs
write at the same time, and any more wait in line (listed under the button
with **✕ Remove**) until one finishes. The line lives in the open page only,
so a reload drops runs not yet started. Auto-sent jobs go to the instance
one at a time, in the order their runs finish. A picture with a run going
or waiting can't be deleted.

**Your own base prompts:** every `img2prompt/base_prompt_gpt_<name>.md`
is one (`<name>` is letters, digits, `_` or `-`, e.g. `v3`). Add or edit
a file and open the tab again or press **↻ Reload**: no code change, no
restart. An optional first line `<!-- what it does -->` becomes the
chip's description and isn't sent to the model. Each result keeps the
name and `prompt_sha` (first 8 hex of the text sent), so results from an
edited v3 can be told apart in `history.json`.

**Delete picture…** under the current picture deletes it and every prompt
written for it. Their cost stays in the totals and the Credit card
(`history.json` keeps only time, account and cost), and jobs already sent
to an instance's queue keep running.

**Send to the app:** pick one of the account's running instances (those
with an app link). **⤒ Queue** on any prompt, or the tick "Send new
prompts to the top of the queue" before Generate, makes a Krea 2 V2 job
with the **Natural Raw 2** preset (its size, LoRAs and so on), batch 1,
and moves it to the top of that app's queue. **Steps** next to the
instance shows the preset's own count as a placeholder; type a number
(1-150) and every job, auto-sent ones included, uses it instead; empty or
**Preset** goes back to the preset's. **Aspect** works the same way: pick
one of the instance's aspects and every job uses it, or "Preset's own". The
negative is
`"negative"` in `img2prompt/negative.json` (read on every send); while it's
empty, Natural Raw 2's own negative is used. A prompt the model skipped
(`SKIP: ...`) can't be queued.

The tab shows what the last Generate cost, the total this session (since
the server started) and the total ever. The Credit card counts it too,
since Bedrock is paid from the same credit. The IAM user needs Bedrock
access (`bedrock-mantle` in us-east-1).

Everything stays in `aws_sniper/img2prompt/`, so copying the aws_sniper
folder to another PC brings it along: the base prompts, `images/` (every
uploaded picture) and `history.json` (every prompt with its tokens and
cost). The last two are git-ignored and left out of the friends' zip.

## Text to prompt (third tab)

Write a short brief (who, what, where, the light, the camera) and, if you
like, add up to 6 reference pictures (drop, paste or **Add pictures…**),
each with a note on what to take from it: "background like this", "this
outfit", "this pose". The model keeps everything the brief says, takes
only the noted part from each reference (never the people in it: the
subject is always the brief's), and fills in the rest. Tick base prompts
and models as on Image to prompt; Generate writes one prompt per pair, all
at once, and you can click it again while they write.

The base prompts are `txt2prompt/base_prompt_<name>.md`: **v1** is a
detailed scene (built on Image to prompt's v3), **v2** the same with
explicit adult content allowed (built on v4). Add your own the same way
as on Image to prompt (new file, then **↻ Reload**). Both refuse, with a
`SKIP: ...` line that can't be queued, a brief for anyone under 18, a
real public figure or someone made to look like a person in a reference,
and v2 also non-consensual acts.

Sending to the app uses the same instance, Steps and Aspect as Image to
prompt (the controls move to whichever tab is open), with this tab's own
negative: `txt2prompt/negative.json`. **Use this
brief** on any past prompt puts its brief and references back. The brief,
references and notes stay in the page between visits; the reference
pictures and every prompt (tokens, cost, the brief) are kept in
`txt2prompt/images/` and `txt2prompt/history.json`, git-ignored and left
out of the friends' zip. The cost counts in the tab's totals and the
Credit card.

## Chat (its own page)

**Chat ↗** next to the tabs opens `http://127.0.0.1:7896/chat` in its own
browser tab. The account that pays is picked in the chat's
header, separately from the sniper's. A chat with **Qwen3-VL** that writes and edits prompts, laid out like Grok
in Ember's look. Write a brief, attach pictures (📎, paste, drop, or 🖼
**Library**) and mention them by `@handle` ("outfit from @kurta, background
like @taj"); typing `@` lists the library. Replies stream in; a prompt shows
as a card with **Copy**, **Save .txt**, **⤒ Queue** and **↻ Regenerate**.

**Only the message you write is sent**, never the chat history. To change a
prompt, click **✎ Edit** on its card: the next message is sent with only
that prompt as context, and the reply is the next version (v2, v3...; the
trail on each card jumps between them). Editing carries on from the newest
version until you press ✕ on the "Editing" strip. **✎ Edit & resend** on
one of your messages puts it back in the box; sending it replaces it and
everything after it. ■ stops a reply and keeps what was written.

**Modes** (the pill in the box, per chat) are the files
`chat2prompt/base_prompt_<name>.md`, read on every send: **scene** (Text to
prompt v1), **scene-explicit** (Text to prompt v2) and **recreate** (Image
to prompt v4), each a word-for-word copy of the original with an "IN THIS
CHAT" section added at the end; the originals are not touched. **Free
chat** has no base prompt. An edit adds `chat2prompt/edit.md` to the mode
(or `edit_<mode>.md`, if you make one). Add a mode by adding a file.

The **Library** shows every picture on this PC: chat uploads plus the
Image to prompt and Text to prompt ones (read only; only chat uploads can be
deleted here). Every picture gets a renamable `@handle`. Handles stay on the
page: Qwen gets each mention as "Picture N", the picture's place in the
message, so a handle like `@image-3` can't be mistaken for the third picture.

The **⤒ pill** at the top right shows where prompts go: `Auto · <instance>`
(accent), `Auto · no instance` (warning: nothing to send to) or `Manual`, and
pulses once with "✓ sent" on each auto-send. Clicking it opens the chat's own
Send to the app settings, separate from the prompt tabs':
- **Instance**: every running instance in every account (the account picker
  only says who pays for Qwen), plus your own app links.
- **Add an app link**: for a pod the sniper didn't start, as `host:7860`, or
  the app's full link with `#k=KEY` if it asks for a key. Links are kept in
  this browser.
- **Steps**, and **Auto-send** (on by default).

The aspect is the **▭** pill under the message box, 3:4 unless you pick
another; the list is the app's own once it has been read. Every mode's
queued prompts get `chat2prompt/negative.json`. Chats, handles and spend are
kept in `chat2prompt/chats.json` and chat uploads in `chat2prompt/images/`,
git-ignored and left out of the friends' zip. A message costs about the same
as a Text to prompt generation (~$0.006 for a full prompt), however long the
chat is; deleting a chat keeps its cost in the totals and the Credit card.

## Accounts

The plain `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` lines are the
account **main**. Add more with **+ Add account** on the page (it checks the
keys with AWS, then writes them to `.env`) or by hand:

```
AWS_ACCESS_KEY_ID__friend=AKIA...
AWS_SECRET_ACCESS_KEY__friend=...
```

Any other `.env` line ending in `__friend` (`SSH_KEY_FILE__friend`,
`EMBER_LICENSE_KEY__friend`, `HF_TOKEN__friend`...) overrides that line for
that account; without one it uses the plain line, so one `.pem` and one
licence can serve every account (the first setup in an account's region
imports the `.pem`'s public key there). An account never falls back to
main's AWS keys.

The account picker at the top chooses which account the Credit and Snipe
cards work on: its credit, Sync with AWS, region setup, quotas, zones and
prices (zone names differ between accounts). The Instances, App and Auto
download cards show every account's instances, each tagged with its
account. One snipe and one running instance at a time per account, so two
accounts can each run one. `settings.json` keeps each account's credit and
setup under `accounts`, `history.json` tags each run with its account.

## Disks

g7e instances come with a local NVMe SSD (1.7 TB usable on g7e.2xlarge,
included in the price) that the DLAMI mounts at `/opt/dlami/nvme` on every
boot. `userdata.sh` bind-mounts it over `/workspace` (models),
`/var/lib/docker` and `/var/lib/containerd` (the app image) before Docker
starts. Measured on g7e.2xlarge: 6.3 GB/s read on the SSD against 279 MB/s on
the network root disk, which the instance caps at 250 MB/s (625 burst)
whatever volume type you pay for.

The SSD is wiped when the instance stops, including a spot interruption, so
a start pulls the image and downloads the models again (~20 min); the
`ember-start` service starts the container on every boot when it's missing.
The root disk (EBS) holds only the OS: 75 GB, the AMI's minimum, at the free
125 MB/s.

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
| `--account` | main | Account from `.env` (`AWS_ACCESS_KEY_ID__<name>`); its overrides apply too |
| `--spot` | off | Persistent spot that stops (not terminates) when interrupted |
| `--disk` / `--throughput` | 75 GB / 125 MB/s | Root gp3 volume, OS only (~41 GB used); raised to the AMI's minimum if it needs more. See *Disks* below. |
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
