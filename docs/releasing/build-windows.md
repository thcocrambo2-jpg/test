# Building the Windows binary

For whoever ships a build. `build.ps1` compiles the same app to
`dist\krea2app.exe`, so a customer with an NVIDIA card and no cloud
account runs one script and gets the RunPod experience on their own
machine. Everything about licences, seats, channels and rollback is
unchanged: the same licence server, the same private bucket, the same
`stable` channel.

**It has to be built on Windows.** Nuitka emits native code for the OS it
runs on, so `build.sh` and `build.ps1` are two scripts producing two
artifacts, and there is no cross-compiling either way.

**PS** (PowerShell, repo root):

```powershell
.\build.ps1                  # compile only. Publishes nothing.
.\build.ps1 -Publish         # ... then ask before publishing
.\build.ps1 -Publish -Yes    # ... publish without asking
.\build.ps1 -UploadOnly      # publish what is already in dist\
```

Publishing is **off by default**, unlike `build.sh`, where the default is
to ask. This one runs on a desktop rather than on a pod that exists only
to build, so the common case is "does it still compile" on a tree with
uncommitted work in it, and that must not end at a prompt whose yes
reaches customers.

It needs the same credentials `build.sh` does (`R2_*`, `KREA2_NODE_TAG`,
`KREA2_ADMIN_TOKEN`) and reads them from the environment. On Windows
there is no `make`, so set them in the shell:

```powershell
$env:R2_ACCOUNT_ID = "..."; $env:R2_ACCESS_KEY_ID = "..."   # etc.
```

See [publishing](publishing.md) for what each one is and where the real
values live.

## What the build machine needs

`build.ps1` installs `nuitka`, `zstandard` and the app's own requirements
itself, into whichever environment you point it at — the same way
`build.sh` does on a pod. What it cannot install is a C compiler, and the
rule there is not the one you would guess:

| Build interpreter | Compiler |
| --- | --- |
| **Python 3.12 or older** | MSVC if present, otherwise Nuitka downloads MinGW64 — nothing to install |
| **Python 3.13 or newer** | **MSVC build tools required.** Nuitka refuses MinGW64 above 3.12 |

So on a machine with 3.13+ and no Visual Studio there is no build, and
Nuitka's own message for it (`FATAL: Error, cannot use '--mingw64' on
Python version 3.13 or higher`) arrives *after* the pip installs and the
dependency checks have all passed. `build.ps1` therefore checks the pair
up front and refuses immediately, naming both ways out.

**Prefer a Python 3.12 build environment.** It needs no Visual Studio,
and 3.12 is what the pod builds on and what the app is tested against —
so the two artifacts differ in as few ways as possible.

The `krea2` conda environment described in
[running on Windows](../running/windows.md) is already that: Python 3.12
with the app's dependencies installed, which is exactly what Nuitka needs
to compile them in. Point the build at it — **PS**:

```powershell
$env:PYTHON = "$env:USERPROFILE\miniconda3\envs\krea2\python.exe"
.\build.ps1
```

Or from scratch, without conda — **PS**:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHON = "$PWD\.venv312\Scripts\python.exe"
.\build.ps1
```

`$env:PYTHON` is how you point the script at an interpreter other than
whatever `python` resolves to, exactly as `PYTHON=` does for `build.sh`.
Note that it is the **build** interpreter only — it decides what gets
compiled in, and has nothing to do with the Python 3.12 the customer's
machine needs for ComfyUI.

## What the machine running it still needs

The `.exe` is **not** self-contained, and that is the same design as on
Linux rather than a Windows shortcoming: the app never imports torch or
ComfyUI, it installs them and runs ComfyUI as a **separate process**. So
the target machine needs

- **Python 3.12** on `PATH` — from python.org, with "Add python.exe to
  PATH" ticked. It is what ComfyUI runs on. The Microsoft Store build
  causes enough path trouble to be worth avoiding.
- **git** — ComfyUI is cloned, not vendored.
- **an NVIDIA GPU** with a current driver. `ensure_torch()` installs the
  cu128 wheels, which is what Blackwell cards (RTX 50xx, `sm_120`) need.
- **Developer Mode**, or an elevated shell. `link_model_dirs()` symlinks
  ComfyUI's model folders; without the privilege ComfyUI silently sees no
  models and every generation fails validation with a message that never
  mentions symlinks.
- **~45 GB free**, more with Wan.

`scripts/windows_start.ps1` checks all of these and says which is missing
before downloading anything.

**SmartScreen and antivirus will complain.** The binary is unsigned, so
the first run gets "Windows protected your PC" → *More info* → *Run
anyway*. Code signing is not attempted here — it needs a certificate and
is a separate decision. Defender also rescans the onefile extraction on
every launch, so excluding the base directory is worth suggesting to
anyone who finds startup slow.

## What a customer runs

The exact counterpart of the RunPod template's container start command —
fetch the current start script, then run it. Windows has no template
field to paste it into, so it goes in a file the customer keeps. Save
this as **`krea2.cmd`** on their desktop; double-clicking it starts the
app:

```bat
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Join-Path $env:TEMP 'krea2-start.ps1'; $u = 'https://' + $env:KREA2_NODE_TAG + '.vercel.app/v1/start.ps1'; curl.exe -fsSL $u -o $s; if ($LASTEXITCODE -ne 0) { Write-Host 'Could not fetch the start script. Check KREA2_NODE_TAG and your internet connection.'; exit 1 }; & $s; exit $LASTEXITCODE"
```

Or, from a PowerShell window they already have open — **PS**:

```powershell
$s = "$env:TEMP\krea2-start.ps1"
curl.exe -fsSL https://$env:KREA2_NODE_TAG.vercel.app/v1/start.ps1 -o $s
powershell -ExecutionPolicy Bypass -File $s
```

Both need `KREA2_LICENSE_KEY` and `KREA2_NODE_TAG` set as **user**
environment variables (not just for the session), which is the equivalent
of filling them into a pod's environment panel — **PS**:

```powershell
[Environment]::SetEnvironmentVariable("KREA2_LICENSE_KEY", "<key>", "User")
[Environment]::SetEnvironmentVariable("KREA2_NODE_TAG", "<tag>", "User")
```

Three details differ from the bash one-liner, all forced by Windows:

- **`-ExecutionPolicy Bypass`** replaces nothing in the bash version — it
  is simply required, because the default policy refuses to run a
  downloaded `.ps1` at all.
- **`exit $LASTEXITCODE`** replaces `exec`. There is no exec, so the
  script runs as a child and its exit code has to be passed back by hand;
  `-Command` otherwise returns its own status and the app's is lost. (The
  `-File` form above does this on its own.)
- **`curl.exe`, not `curl`** — in PowerShell, bare `curl` is an alias for
  `Invoke-WebRequest`, which takes none of these flags.

A copy of a start script a customer holds is a copy no fix ever reaches,
which is why the file above holds only the bootstrapper. Everything that
might need changing lives in `start.ps1`, on the server — see
[publishing a start-script fix](publishing.md#publishing-a-start-script-fix).

`scripts/windows_start.ps1` mirrors `scripts/runpod_start.sh` step for
step — same variable checks, same node-tag validation, sends the sha256
of the binary it already has so a restart downloads nothing, falls back
to the on-disk build when the server is unreachable, verifies the
download before running it. Two things differ, both because Windows
differs:

- Models default to **`C:\krea2`**, not `/workspace/krea2`. The pod
  default resolves to `C:\workspace\krea2` on Windows, which is a real
  path and the wrong one. Override with `KREA2_BASE_DIR`.
- There is no `exec`, so the app runs as a child process. Ctrl-C reaches
  it and releases the seat cleanly; closing the window does not, and that
  seat is freed by the server's stale-lease sweep a few minutes later.

## How a Windows build stays away from Linux pods

Worth understanding before publishing one, because the failure mode is
silent on the publishing side and total on the receiving side: a Linux
pod handed a `.exe` downloads it, matches the checksum, and dies with
`Exec format error`.

A build document carries a **`platform`**, and `/v1/build` resolves a
build by *(channel, platform)* rather than by channel alone:

```
POST /v1/build {license_key, instance_id, current_sha}              -> linux
POST /v1/build {license_key, instance_id, current_sha, platform}    -> as asked
```

**A client that sends no `platform` gets Linux.** That is not a default
chosen for tidiness — it is the compatibility guarantee. Every pod
running today sends exactly the first body, and `scripts/runpod_start.sh`
is unchanged, so they all keep resolving the build they already had.

Three things hold it together, all in
[`license-validator/`](../../license-validator/README.md):

1. `/v1/build` filters by platform, on **both** the channel lookup and
   the `build_sha` pin. A pin names one artifact and an artifact is for
   one OS, so a customer pinned to a Linux sha and running Windows gets a
   clean "no build" rather than the wrong one.
2. `promote()` scopes its `$pull` by platform. Without that, promoting a
   Windows build to `stable` would take `stable` off the **Linux** build
   and every Linux pod would get `no_build` on its next start — an outage
   caused at publish time, before any pod asked for anything.
3. `buildKey()` takes the filename from the build document, so a Windows
   artifact is stored at `builds/<sha>/krea2app.exe`. Content addressing
   already keeps the two apart; this is so a bucket listing is readable.

Builds published with no `platform` field are treated as Linux, so
nothing needs migrating.

**Deploy order matters.** The licence server change must be live *before*
the first Windows build is published and before the start script reaches
a customer. An old server ignores a `platform` field it does not know
about and answers with the Linux build; `windows_start.ps1` refuses to
run anything that is not marked `windows` — including a response with no
platform at all — so the failure is a clear message rather than a
mystery, but it is still a failure.

## Keeping the two build scripts in step

`build.ps1` carries its own copy of the long `--include-*` argument list.
That duplication is deliberate — `build.sh` produces what every customer
runs today and is left alone — and the price of it is drift:

```bash
make check-args          # WSL or POD
```

```powershell
python scripts\check_build_args.py    # PS, the same check on its own
```

`make compile` and `make release` both depend on it, so the Linux build
refuses to run while the two disagree. Flags that genuinely belong to one
platform (`--jobs`, `--mingw64`, `--static-libpython`, the output
filename) are listed in `PLATFORM_SPECIFIC` in
[`scripts/check_build_args.py`](../../scripts/check_build_args.py), with
the reason. What that check does and does not prove is in
[the checks](../development/checks.md#check_build_argspy).
