# Building the Windows binary

For whoever ships a build. `build.ps1` compiles the same app to
`dist\ember.exe`, so a customer with an NVIDIA card and no cloud
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

It needs the same credentials `build.sh` does (`R2_*`, `EMBER_NODE_TAG`,
`EMBER_ADMIN_TOKEN`) and reads them from the environment. On Windows
there is no `make`, so set them in the shell:

```powershell
$env:R2_ACCOUNT_ID = "..."; $env:R2_ACCESS_KEY_ID = "..."   # etc.
```

See [publishing](publishing.md) for what each one is and where the real
values live.

Before any of these, point `$env:PYTHON` at the build environment and
run the [checks below](#before-you-build).

## What the build machine needs

`build.ps1` installs `nuitka` and `zstandard` into whichever environment
you point it at if they are missing — the same way `build.sh` does on a
pod. The app's own requirements are a different story: the script only
tries to *import* them, and runs `pip install -r requirements.txt` only
when that import fails. An environment that imports everything but holds
the wrong versions is used exactly as it is, so **the pins in
`requirements.txt` are not enforced by the build** — that is what the
[dry-run check](#before-you-build) is for. What the script cannot install
at all is a C compiler, and the rule there is not the one you would
guess:

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

### The build environment is `krea2build`

The build environment is the conda env **`krea2build`**, at
`%USERPROFILE%\miniconda3\envs\krea2build`: Python 3.12, Nuitka,
`zstandard`, and `requirements.txt` satisfied, and nothing else. Point the
build at it — **PS**:

```powershell
$env:PYTHON = "$env:USERPROFILE\miniconda3\envs\krea2build\python.exe"
.\build.ps1
```

**Not `krea2`.** The `krea2` env from
[running on Windows](../running/windows.md) is where the app *runs*, and
running the app changes it: `install_comfyui()` in
`ember/comfy/setup.py` installs ComfyUI's requirements and ours in one
resolver pass, and ComfyUI's requirements are unpinned and move. So every
start can leave different versions behind, and a build from that env
compiles in whatever the last start happened to resolve rather than what
`requirements.txt` says. A build env is only useful if nothing but you
installs into it.

`$env:PYTHON` is how you point the script at an interpreter other than
whatever `python` resolves to, exactly as `PYTHON=` does for `build.sh`.
Note that it is the **build** interpreter only — it decides what gets
compiled in, and has nothing to do with the Python 3.12 the customer's
machine needs for ComfyUI.

### Creating it from scratch

With conda — **PS**:

```powershell
conda create -n krea2build python=3.12 -y
$py = "$env:USERPROFILE\miniconda3\envs\krea2build\python.exe"
& $py -m pip install -r requirements.txt nuitka zstandard
```

Without conda, from a uv-managed CPython 3.12 — **PS**:

```powershell
uv python install 3.12                  # if uv python find finds none
& (uv python find 3.12) -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt nuitka zstandard
$env:PYTHON = "$PWD\.venv312\Scripts\python.exe"
```

Call the interpreter by path, as both recipes do, rather than
`conda activate` and a bare `pip`: which `pip` a shell finds is exactly
the kind of thing that quietly lands packages in the wrong env. And
don't use `py -3.12`: the `py` launcher only exists if python.org's
installer put it there, and on this machine it did not.

## Before you build

Four checks, all seconds long, all run against the interpreter the build
will use — **PS**:

```powershell
$py = $env:PYTHON                       # krea2build, set as above
& $py --version                          # 1. must say 3.12.x
& $py -m pip install --dry-run -r requirements.txt   # 2. no "Would install" lines
& $py -m nuitka --version                # 3. Nuitka is importable
git status --short                       # 4. clean, or you know why not
```

1. **Python 3.12**, for the compiler reason in the table above.
2. **`--dry-run` wants to install nothing.** Any `Would install …` line
   means the env has drifted from `requirements.txt`. Run the same command
   without `--dry-run` into the build env, then check again.
3. **Nuitka runs.** `build.ps1` would install it anyway, but a failure
   here is a broken env, and it is better found now than mid-build.
4. **The tree is clean.** The build stamps its commit into the binary,
   and a tree with uncommitted work produces a binary nobody can
   reproduce from git.

**Why the env has to match `requirements.txt`.** Nuitka compiles in
whatever version of each package the build interpreter has, so the build
env *is* the dependency resolution for every customer who runs the
binary. On 2026-09-24 a Linux build published to `stable` crashed on pods
with `ModuleNotFoundError: huggingface_hub.utils._headers`. From
huggingface_hub 1.32 the `utils` package loads its submodules lazily
through `importlib`, which Nuitka's import graph cannot follow, and the
build env had resolved 1.32 while `requirements.txt` said nothing to stop
it. The fix (commit `d321bac`) names the whole package with
`--include-package=huggingface_hub` and pins `huggingface_hub<1.32` — but
a pin only helps in an env that honours it, and as described above, the
build scripts do not check.

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

`scripts/windows_start.ps1` checks **most** of these before downloading
anything, and says which is missing: `curl.exe`, `python` (and that the
interpreter it found can actually `import pip` — an MSYS2 python and the
Microsoft Store alias both satisfy `Get-Command` and then install
nothing), `git`, and the symlink privilege, which it probes by calling
`os.symlink` rather than reading the registry.

**It does not check for a GPU.** Nothing in the start script tests for
one; the card and its driver are proved later, when `ensure_torch()`
launches a real kernel. So a machine with no NVIDIA card gets past every
pre-flight message and fails further in.

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
this as **`ember.cmd`** on their desktop; double-clicking it starts the
app:

```bat
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Join-Path $env:TEMP 'ember-start.ps1'; $u = 'https://' + $env:EMBER_NODE_TAG + '.vercel.app/v1/start.ps1'; curl.exe -fsSL $u -o $s; if ($LASTEXITCODE -ne 0) { Write-Host 'Could not fetch the start script. Check EMBER_NODE_TAG and your internet connection.'; exit 1 }; & $s; exit $LASTEXITCODE"
```

Or, from a PowerShell window they already have open — **PS**:

```powershell
$s = "$env:TEMP\ember-start.ps1"
curl.exe -fsSL https://$env:EMBER_NODE_TAG.vercel.app/v1/start.ps1 -o $s
powershell -ExecutionPolicy Bypass -File $s
```

Both need `EMBER_LICENSE_KEY` and `EMBER_NODE_TAG` set as **user**
environment variables (not just for the session), which is the equivalent
of filling them into a pod's environment panel — **PS**:

```powershell
[Environment]::SetEnvironmentVariable("EMBER_LICENSE_KEY", "<key>", "User")
[Environment]::SetEnvironmentVariable("EMBER_NODE_TAG", "<tag>", "User")
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

- Models default to **`C:\ember`**, not `/workspace/ember`. The pod
  default resolves to `C:\workspace\ember` on Windows, which is a real
  path and the wrong one. Override with `EMBER_BASE_DIR`.
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
   artifact is stored at `builds/<sha>/ember.exe`. Content addressing
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
