# Build distribution

How a pod gets the app binary, and how you pin, promote or roll back what
it gets. For whoever ships a build.

The app binary lives in a **private** Cloudflare R2 bucket. Pods never
address it: they send their key to `/v1/build` and get back a signed URL
that expires in `BUILD_URL_TTL_SECONDS` (30 minutes by default).

Be clear about what that does and does not buy. It stops a lapsed or
revoked key pulling a **new** build, it lets a build be pinned or rolled
back per licence from the server, and it records which machines pull on
which key. It does **not** stop a binary someone already has from being
copied — what limits who can *run* the app is still the seat check.

Objects are content-addressed at `builds/<sha256>/<filename>`, so
publishing never overwrites and every build stays available. A channel is
just a name in the `channels` array of a build document, which makes
rolling forward and rolling back the identical operation:

```bash
curl -s -H "Authorization: Bearer $EMBER_ADMIN_TOKEN" \
     https://<deployment>.vercel.app/v1/admin/builds

curl -s -X POST -H "Authorization: Bearer $EMBER_ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"sha256":"<older sha>","channel":"stable"}' \
     https://<deployment>.vercel.app/v1/admin/builds/promote
```

## One build per platform

Every build document carries a `platform`, one of `linux` or `windows`,
and it is what keeps a Windows `.exe` away from a Linux pod. `/v1/build`
takes the client's `platform` (defaulting to `linux`) and filters **both**
lookups by it — the channel lookup and the per-licence pin. A pin that
exists but names a build for the other platform is called out in the log
rather than silently served.

So a channel name is unique per platform, not across the collection: one
build holds `stable` for Linux and another holds it for Windows.
`promote` takes the platform from the stored build document rather than
from the request, because the artifact's own record is the only thing that
knows what the artifact is.

Build documents published before platforms existed carry no `platform`
field and are treated as `linux`, so the Linux filter matches both a
document that says `linux` and one that says nothing.

`GET /health` reports `stable_builds` per platform, and keeps
`stable_build` as the Linux one — that is what every pod in production
downloads, and what anything already reading the endpoint means.

## Which build a licence resolves to

Most specific first:

| On the licence | Result |
| --- | --- |
| `build_sha` is set | exactly that build, channel ignored |
| `build_channel` is set | whichever build holds that channel |
| neither | whichever build holds `stable` |

Both fields are absent on an ordinary licence, so the default needs no
edit. Set `build_sha` to hold one customer on a known-good build, or
`build_channel: "beta"` to put a willing customer on new builds first.

## The download cap

`BUILD_DOWNLOADS_PER_HOUR` (20 by default) caps presigned URLs per licence
per hour, answering `429 rate_limited` past it. This is the abuse signal
the whole gate exists to give you: one key pulling builds from thirty
machines is visible here and nowhere else. Set it to 0 to disable the cap
— the `downloads` log is written either way, and expires after
`DOWNLOAD_TTL_SECONDS` (30 days).

## Two R2 tokens, deliberately

| Where | Scope | Why |
| --- | --- | --- |
| this service | Object **Read** | public-facing; only ever signs GETs |
| the build scripts | Object **Read & Write** | the only thing that uploads |

Giving the API a write token would mean any path to leaking it is a path
to replacing the binary every customer downloads. Keeping them apart is
what makes the read-only half actually read-only.

The builds bucket must also be **separate from the public showcase-images
bucket**. Public access on R2 is a per-bucket setting, so one bucket
cannot be both gated and world-readable.

## If the API is down

[`../../scripts/runpod_start.sh`](../../scripts/runpod_start.sh) falls
back to the binary already on the pod's volume for *any* non-200 —
unreachable, expired, revoked, rate limited, nothing published. A pod that
has everything it needs to run is not bricked by this service having a bad
afternoon, and the seat check that follows delivers the real verdict with
the message worth reading. A pod with no cached binary and a failed call
stops, and prints the server's message when there is one.
[`../../scripts/windows_start.ps1`](../../scripts/windows_start.ps1) does
the same on a desktop. `platform` rides on the build manifest so a client
can refuse a build for the wrong OS rather than download it, checksum it
happily and fail to execute it — and the Windows script treats a manifest
with **no** `platform` as a refusal too, which turns a server that ignores
the field it was sent into a clean error rather than a mysterious one.
