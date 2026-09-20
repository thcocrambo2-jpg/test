# Publishing, channels and rollback

For whoever ships a build. Compiling produces `dist/ember` or
`dist\ember.exe`; this page is what happens after that — uploading the
artifact to R2, pointing a channel at it, and moving that channel back
when something is wrong.

A publish is two halves: the binary goes to a **private** Cloudflare R2
bucket, and then it is registered with the licence API, which is what
points a channel at it. The `Makefile` at the repo root wraps both halves
and reads every credential from `license-validator/.env`, so there is one
file to fill in rather than six variables to retype after every reboot.

**`make` is WSL/POD only.** The Makefile declares `SHELL := /bin/bash`
and uses `source`, arrays and `${!indirect}`, so it will not run under
PowerShell or dash. The Windows build has no make target; it is
[`.\build.ps1`](build-windows.md).

## The targets

| Command | Runs | Needs |
| --- | --- | --- |
| `make` | lists these | — |
| `make check` | credentials, artifact, and whether the admin token actually opens the deployment | admin |
| `make health` | the deployment's `/health` — `db`, `r2`, `stable_build` | admin |
| `make builds` | every build ever published, newest first | admin |
| `make check-args` | every check a build must pass — see [the checks](../development/checks.md) | nothing |
| `make compile` | `check-args`, then `build.sh --no-publish` — compiles `dist/ember`, uploads nothing | nothing |
| `make publish` | `build.sh --upload-only` — uploads the binary already in `dist/` and points `stable` at it | write token + admin |
| `make release` | `check-args`, then `build.sh -y` — compile **and** publish in one step | write token + admin |
| `make promote SHA=<sha256>` | point a channel at a build | admin |
| `make start-sh` | PUT `scripts/runpod_start.sh` to `r2://<bucket>/start.sh` | write token + admin |
| `make start-ps1` | PUT `scripts/windows_start.ps1` to `r2://<bucket>/start.ps1` | write token + admin |
| `make webui` | `npm ci && npm run build` in `webui/`, then regenerates `ember/web/webui_bundle.py` — **the only target that needs Node** | Node >= 20 |
| `make webui-dev` | Vite's dev server, proxying the API to `:7860` | Node >= 20 |
| `make image` / `image-dev` / `image-push` | the environment image — see [running under Docker](../running/docker.md) | Docker |

Two variables tune a publish — **WSL or POD**:

```bash
EMBER_BUILD_CHANNEL=beta make publish    # upload without customers getting it
make promote SHA=<older sha> CHANNEL=beta
```

## Credentials

`license-validator/.env` holds two R2 tokens under different names, and
the split is load-bearing: the licence service is public-facing and gets
**Object Read only** (`R2_ACCESS_KEY_ID`), while publishing gets **Object
Read & Write** (`R2_WRITE_ACCESS_KEY_ID`). A leak of the deployed
credential therefore cannot replace the binary customers download.

The Makefile resolves the names so neither side has to know the other's:
`build.sh` wants `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`, and the
publish targets export the **write** pair under those names for the
length of the recipe. The admin token is the same story — the file holds
`ADMIN_TOKEN`, and the Makefile exports it as `EMBER_ADMIN_TOKEN`, which
is the name `build.sh` and the raw `curl` calls below read.

`make check` refuses to publish if the read and write keys are identical,
because R2 does not reject a bad-signature PUT until the bytes have
arrived — a few hundred megabytes to reach a 403.

Listing builds and moving a channel deliberately need **only** the admin
token, not the R2 write pair. The moment you most need them — something
shipped broken and you are rolling it back — is exactly when you might be
on a machine that has never built anything.

`make check` is the cheap pre-flight; run it before spending an upload:

```
  account     8487b896…
  bucket      krea2-builds
  api         https://<node-tag>.vercel.app
  write key   038e9e…  (differs from read key: ok)
  artifact    dist/ember  (100600024 bytes)
  admin api   ok
```

It reports rather than enforces: a diagnostic that stopped at the first
missing value would tell you one thing per run, which is the opposite of
what you want when you are trying to find out what is wrong.

`admin api 404` means `ADMIN_TOKEN` is unset on the deployment *or* the
deployment predates these routes — the two return an identical body, and
both are fixed by redeploying with the environment variables set.
`/health` gaining `"r2":"configured"` is the confirmation the new code
landed. See [`license-validator/README.md`](../../license-validator/README.md)
for the service side: how `/v1/build` gates a download on the licence,
and why that stops a lapsed key fetching a *new* build without pretending
to stop a binary someone already has from being copied.

Where the real values live is in
[the command reference](../reference/commands.md#14-where-the-real-values-live).

## Rollback and roll-forward are the same call

Builds are content-addressed at `builds/<sha256>/ember`, so publishing
never overwrites and every build stays in the bucket. A channel is just a
name sitting on one build document — `make builds` lists them,
`make promote` moves the name. Nothing is re-uploaded and pods take it on
their next start.

**WSL or POD**:

```bash
make builds                                  # find the sha
make promote SHA=<sha256>
make promote SHA=<sha256> CHANNEL=beta
```

To hold a single customer on a specific build, set `build_sha` on their
licence document instead; it wins over the channel.

A Windows build registers with `platform="windows"`, so publishing to
`stable` from a Windows machine is safe for Linux pods — `/v1/build`
resolves by *(channel, platform)* and defaults to linux for any client
that does not say. The detail is in
[Building the Windows binary](build-windows.md#how-a-windows-build-stays-away-from-linux-pods).

### Raw equivalents

Any shell with `curl`, needs `EMBER_ADMIN_TOKEN`:

```bash
curl -s -H "Authorization: Bearer $EMBER_ADMIN_TOKEN" \
     https://<tag>.vercel.app/v1/admin/builds

curl -s -X POST -H "Authorization: Bearer $EMBER_ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"sha256":"<older sha>","channel":"stable"}' \
     https://<tag>.vercel.app/v1/admin/builds/promote

curl -s https://<tag>.vercel.app/health
```

Presign an R2 URL by hand — any shell, needs the four `R2_*`:

```bash
python scripts/r2_presign.py --key builds/<sha256>/ember
python scripts/r2_presign.py --key start.ps1 --method PUT --expires 900
```

## Publishing a start-script fix

`start.sh`, `start.ps1` and the binaries are independent objects in the
bucket at fixed keys. `build.sh` uploads the pod start script alongside
the binary and `.\build.ps1 -Publish` uploads the Windows one, but a fix
to either should not cost a Nuitka compile, a new build document, or —
for the Windows script — finding a Windows machine. Each is one upload.

**WSL or POD**:

```bash
make start-sh       # scripts/runpod_start.sh  -> r2://<bucket>/start.sh
make start-ps1      # scripts/windows_start.ps1 -> r2://<bucket>/start.ps1
```

The binary is untouched; each target writes exactly the one object that
`/v1/start.sh` or `/v1/start.ps1` redirects to, and customers get it on
their next start.

Both targets refuse a file whose encoding would break on the machine that
runs it, because the two scripts are read by tools with opposite tastes:

- **`start.ps1` must have a UTF-8 BOM.** `powershell.exe` decodes a
  BOM-less script as Windows-1252, and a single em-dash in a comment then
  ends a string early — the file fails to *parse* on the customer's
  machine, before any of its own error handling can say why.
- **`start.sh` must have no BOM and LF endings.** bash reads a BOM as the
  first three bytes of the shebang line, and a CR at the end of it as
  part of the interpreter's name, so either one fails on the pod with
  `bad interpreter` — a message that names neither the cause nor, usually,
  the right file. `.gitattributes` pins `*.sh` to LF; this check catches
  a file that got past it.

## The React bundle

`make webui` is the only target that needs Node, and the bundle it
generates, `ember/web/webui_bundle.py`, is **committed**. Both build
scripts then do nothing but verify it — `check_webui.py` recomputes a
hash over the front-end sources and compares it to the `SOURCE_HASH`
baked into the module, so "someone edited a `.tsx` and forgot to rebuild"
is a build failure rather than a silent ship.

Why a generated file is committed at all, and why a merge conflict in it
is always resolved by regenerating rather than by editing, is in
[the web UI architecture](../architecture/web-ui.md).
