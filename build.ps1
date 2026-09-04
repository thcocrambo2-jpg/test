#Requires -Version 5.1
<#
    Build the single-file Windows artifact with Nuitka.

    RUN THIS ON WINDOWS. Nuitka emits native code for the OS it runs on, so
    this is the Windows half of a pair: build.sh produces dist/krea2app for
    Linux pods on a pod, this produces dist/krea2app.exe for Windows
    customers on Windows. Neither can produce the other's artifact, and
    that is a property of Nuitka rather than a decision taken here.

    The build needs no GPU: Nuitka compiles source rather than executing
    it, so comfy.py's import-time GPU check never fires here.

    What ends up inside: this app plus everything it imports (fastapi,
    uvicorn, huggingface_hub, requests, safetensors, websocket-client,
    Pillow), and webui_bundle.py — the React front end, generated and
    committed so that this host never needs Node.
    What does NOT: torch and ComfyUI — the app never imports them, it
    installs them at runtime and launches ComfyUI as a separate process.
    That is why bootstrap.runtime_python() exists, and why the machine
    running the .exe still needs its own Python 3.12 and git.

      .\build.ps1                  -> dist\krea2app.exe, and stop
      .\build.ps1 -Publish         -> ... then offer to publish it
      .\build.ps1 -Publish -Yes    -> ... and publish without asking
      .\build.ps1 -UploadOnly      -> publish the dist\krea2app.exe already
                                      there, compiling nothing

    PUBLISHING IS OFF BY DEFAULT, and that differs deliberately from
    build.sh, where the default is to ask. This script runs on a desktop
    rather than on a pod that exists only to build: it is the one people
    will run to see whether the thing compiles at all, often on a tree with
    uncommitted experiments in it. The default therefore cannot be a path
    that ends at a prompt whose "yes" ships to customers.

    Publishing uploads to the same private Cloudflare R2 bucket build.sh
    uses and then registers the build with the licence API, which is what
    points a channel at it. Both halves need environment variables, and a
    plain build needs none of them:

      R2_ACCOUNT_ID          Cloudflare account id
      R2_ACCESS_KEY_ID       R2 API token, Object Read & Write on the bucket
      R2_SECRET_ACCESS_KEY   ... its secret
      R2_BUILDS_BUCKET       krea2-builds
      KREA2_NODE_TAG         the deployment id — the same tag pods carry.
                             The API is https://<tag>.vercel.app, assembled
                             here exactly as config.py assembles it there
      KREA2_ADMIN_TOKEN      the ADMIN_TOKEN set on that deployment

      KREA2_BUILD_CHANNEL    which channel to point at this build
                             (default "stable"; set it to something else to
                             upload without customers getting it)

    A Windows build is registered with platform="windows", which is what
    keeps it away from Linux pods: /v1/build resolves a build by (channel,
    platform) and defaults to linux for any client that does not say — and
    scripts/runpod_start.sh never says. Publishing to "stable" from here is
    therefore safe for pods, and the channel means the same thing on both
    platforms.

    ── Keeping the Nuitka flags in step with build.sh ────────────────────
    The long --include-* list below is duplicated from build.sh rather than
    generated from a shared file, so that build.sh — the script that
    produces what every customer runs today — did not have to be touched to
    add Windows. The cost of that choice is drift, and it is paid for by
    scripts/check_build_args.py, which parses both scripts and fails when
    the two lists differ. `make check-args` runs it, and so do `make
    compile` and `make release`.

    If you add a flag here, add it there. The checker will tell you if you
    forget, but only if you run it.
#>

[CmdletBinding()]
param(
    # Off by default — see the note above. -Publish alone still asks.
    [switch]$Publish,
    [switch]$Yes,
    [switch]$UploadOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# See scripts/windows_start.ps1 for why: PowerShell 7.4 would otherwise
# turn every non-zero exit from python/curl into a terminating error, and
# this script checks $LASTEXITCODE itself so it can say something useful.
$PSNativeCommandUseErrorActionPreference = $false

Set-Location -LiteralPath $PSScriptRoot

$PYTHON = $env:PYTHON
if (-not $PYTHON) { $PYTHON = 'python' }
$OUTPUT_DIR = 'dist'
$OUTPUT_NAME = 'krea2app.exe'
$ARTIFACT = Join-Path $OUTPUT_DIR $OUTPUT_NAME
$START_SCRIPT = 'scripts\windows_start.ps1'

function Say([string]$Message) { Write-Host $Message }
function Die([string]$Message) {
    Write-Host ''
    Write-Host $Message -ForegroundColor Red
    Write-Host ''
    exit 1
}

# ── Running a native command that is ALLOWED to fail ─────────────────────
#
# PowerShell 5.1 wraps a native command's stderr in ErrorRecords the moment
# it is redirected, and with $ErrorActionPreference = 'Stop' the first one
# is a terminating error. So the obvious way to write a probe —
#
#     & $PYTHON -c 'import nuitka' 2>$null
#
# does the opposite of what it looks like: the traceback it is trying to
# hide is exactly what kills the script, and the build dies at the step
# that was only asking a question. (It did, before these two helpers
# existed.) Relaxing the preference for the length of the call is the fix;
# every caller checks the exit code itself.

# True when the command exited 0. Output and errors are both swallowed.
function Test-NativeOk([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}

# The command's stdout, trimmed, or '' if it failed. For the ones whose
# answer is a value rather than a yes/no — git's commit id, the hf_xet
# path — and where "it failed" is a legitimate answer rather than a fault.
function Get-NativeOutput([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Command 2>$null
        if ($LASTEXITCODE -ne 0) { return '' }
        return "$output".Trim()
    } finally {
        $ErrorActionPreference = $previous
    }
}

# Run a multi-line Python snippet from a temp file.
#
# NOT `& $PYTHON -c $snippet`. PowerShell 5.1 re-quotes arguments on their
# way to a native process and mangles embedded double quotes doing it: the
# hf_xet lookup below ends in `else ""` and arrived at Python as `else "`,
# which is a SyntaxError blamed on the snippet rather than on the shell.
# A file has no quoting layer to get wrong.
function Invoke-PythonFile([string]$Snippet) {
    $file = Join-Path ([System.IO.Path]::GetTempPath()) ('krea2-' + [guid]::NewGuid().ToString('N') + '.py')
    [System.IO.File]::WriteAllText($file, $Snippet, (New-Object System.Text.UTF8Encoding($false)))
    try {
        return Get-NativeOutput { & $PYTHON $file }
    } finally {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
}

# The .ico for --windows-icon-from-ico, or nothing at all.
#
# Returns an array so the caller can append it unconditionally: an empty
# array adds no argument, which is how "no icon" stays a skip rather than a
# branch at the call site.
function Get-IconArgs {
    $source = 'assets/branding/ember-icon-256.png'
    if (-not (Test-Path -LiteralPath $source)) { return @() }

    # Sizes rather than one 256x256 frame: Windows picks the nearest frame
    # and downscales badly when it has to, so a shortcut at 32x32 off a
    # single large frame looks blurred next to every other icon on the
    # desktop. Pillow writes them all into one .ico.
    $snippet = @'
import pathlib, sys, tempfile
from PIL import Image

source = pathlib.Path("assets/branding/ember-icon-256.png")
target = pathlib.Path(tempfile.gettempdir()) / "krea2app-icon.ico"
image = Image.open(source).convert("RGBA")
image.save(target, format="ICO",
           sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(target)
'@
    $ico = Invoke-PythonFile $snippet
    if (-not $ico -or -not (Test-Path -LiteralPath $ico)) {
        Say '    no icon (could not convert the PNG) - building without one'
        return @()
    }
    Say "    icon: $ico"
    return @("--windows-icon-from-ico=$ico")
}

if ($UploadOnly -and -not $Publish) {
    # -UploadOnly is unambiguous about intent — there is nothing else it
    # could mean — so it implies -Publish rather than refusing.
    $Publish = $true
}

# ── Publish ──────────────────────────────────────────────────────────────
#
# Two objects go to one PRIVATE Cloudflare R2 bucket:
#
#   builds/<sha256>/krea2app.exe   the binary, addressed by its own hash
#   start.ps1                      scripts/windows_start.ps1, fixed key
#
# The Linux side of the bucket is untouched by this: its binaries are at
# builds/<sha256>/krea2app and its script at start.sh. The two never
# collide, because a sha256 prefix is per-artifact and the two start
# scripts have different names.
#
# Neither object is reachable without going through the licence API. A
# machine asks /v1/build with its key and gets a short-lived signed URL
# back, so a lapsed or revoked key cannot pull a new build at all;
# /v1/start.ps1 redirects to a signed URL for the script, which is what
# the customer's two-line shortcut fetches.
#
# start.ps1 is published rather than emailed for the same reason start.sh
# is published rather than pasted into a RunPod template: a copy a
# customer holds is a copy no fix ever reaches. The shortcut on their
# desktop fetches the current one every time.
#
# What stops a stranger *running* this build is still the seat check in
# licensing.py, not where the bytes are kept. Gating the download stops a
# lapsed key getting a new build and shows which machines pull on which
# key. It does not stop a binary someone already has from being copied.
function Invoke-Publish {
    # Normalised and validated exactly as config.py and windows_start.ps1
    # do it, so a tag that works on a customer machine works here.
    $nodeTag = ''
    if ($env:KREA2_NODE_TAG) {
        $nodeTag = ($env:KREA2_NODE_TAG -replace '\s', '').ToLowerInvariant()
    }
    $apiUrl = ''
    if ($nodeTag -match '^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$') {
        $apiUrl = "https://$nodeTag.vercel.app"
    }

    $channel = $env:KREA2_BUILD_CHANNEL
    if (-not $channel) { $channel = 'stable' }

    # The build itself succeeded, so this is only fatal when -Yes said to
    # publish. Without it the operator was going to be asked anyway, and
    # "answer no for them" is the same outcome.
    function Stop-Publish([string]$Message) {
        Write-Host ''
        Write-Host $Message -ForegroundColor Yellow
        Write-Host ''
        if ($Yes) { exit 1 }
        exit 0
    }

    # Missing files are checked before the token, because they are the more
    # fundamental problem and reporting them second is actively misleading:
    # an -UploadOnly run against an empty dist\ would otherwise be told its
    # token was the thing standing in the way.
    if (-not (Test-Path -LiteralPath $ARTIFACT)) {
        Die @"
ERROR: no binary at $ARTIFACT — there is nothing to publish.
       Run .\build.ps1 without -UploadOnly to compile one.
"@
    }

    # Fatal rather than skipped: publishing a binary without the script
    # that launches it leaves customers fetching a start.ps1 from the
    # previous build, which is the one shape of mismatch nothing
    # downstream can detect.
    if (-not (Test-Path -LiteralPath $START_SCRIPT)) {
        Die @"
ERROR: $START_SCRIPT is missing — it is published alongside the
       binary and customers fetch it on every start.
"@
    }

    # The R2 credential here must be an Object Read & Write token. It is
    # the only place one exists — the licence API holds a read-only token
    # and can therefore never overwrite a published build, which is the
    # whole point of keeping the two apart.
    $r2Missing = @()
    foreach ($name in 'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUILDS_BUCKET') {
        if (-not (Get-Item "env:$name" -ErrorAction SilentlyContinue)) { $r2Missing += $name }
    }
    if ($r2Missing.Count -gt 0) {
        Stop-Publish @"
Not publishing: $($r2Missing -join ', ') unset.
The binary goes to a private R2 bucket. Create an R2 API token with
Object Read & Write on that bucket and set:

    R2_ACCOUNT_ID          your Cloudflare account id
    R2_ACCESS_KEY_ID       from the R2 API token
    R2_SECRET_ACCESS_KEY   from the R2 API token
    R2_BUILDS_BUCKET       krea2-builds
"@
    }

    # Separated from the token check below, because "unset" and "set to
    # something that is not a tag" send you to different places and
    # collapsing them would have you checking a value that is right there
    # and correct.
    if (-not $apiUrl) {
        if (-not $env:KREA2_NODE_TAG) {
            Stop-Publish @"
Not publishing: KREA2_NODE_TAG is unset.
It names the deployment to register this build with — the same tag the
customers carry. Set it and re-run with -UploadOnly:

    KREA2_NODE_TAG     the deployment id (a single name, no dots or
                       slashes, as it appears in <name>.vercel.app)
"@
        }
        Stop-Publish @"
Not publishing: KREA2_NODE_TAG is not a valid deployment id.
It has to be one name as it appears in <name>.vercel.app — no dots, no
slashes, no https:// prefix. Got: $env:KREA2_NODE_TAG
"@
    }

    if (-not $env:KREA2_ADMIN_TOKEN) {
        Stop-Publish @"
Not publishing: KREA2_ADMIN_TOKEN is unset.
Uploading the binary without registering it would put bytes in the bucket
that nothing points at — customers would keep running the previous build
and nothing would say why. Set it and re-run with -UploadOnly:

    KREA2_ADMIN_TOKEN  the ADMIN_TOKEN set on $apiUrl
"@
    }

    Write-Host ''
    Say '>>> Preparing to publish ...'
    $sha = (Get-FileHash -LiteralPath $ARTIFACT -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $ARTIFACT).Length
    $commit = ''
    $branch = ''
    $dirty = ''
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $commit = Get-NativeOutput { & git rev-parse --short HEAD }
        $branch = Get-NativeOutput { & git rev-parse --abbrev-ref HEAD }
        # Only meaningful once we know there is a checkout: outside one,
        # git fails for a reason that has nothing to do with the tree being
        # dirty, and reporting "uncommitted changes" then is a lie about
        # the build's origin.
        if ($commit -and -not (Test-NativeOk { & git diff --quiet HEAD })) {
            $dirty = ' (uncommitted changes)'
        }
    }

    $branchNote = ''
    if ($branch) { $branchNote = " on $branch" }
    $commitNote = $commit
    if (-not $commitNote) { $commitNote = 'unknown' }
    $sizeNote = '{0:N1} MB' -f ($size / 1MB)

    Write-Host @"

    bucket    r2://$env:R2_BUILDS_BUCKET   (PRIVATE)
    binary    builds/$sha/$OUTPUT_NAME
              $sizeNote
    start.ps1 start.ps1  (from $START_SCRIPT, overwritten in place)
    register  $apiUrl  ->  channel "$channel", platform windows
    source    $commitNote$branchNote$dirty

  The binary is addressed by its own hash, so this adds a build rather
  than replacing one. What changes is where "$channel" points FOR WINDOWS:
  the Linux build on the same channel is not touched, and Linux pods are
  unaffected by anything this does. Windows customers pick it up on their
  next start, and every previous build stays in the bucket to roll back
  to.

"@

    if ($Yes) {
        Say '>>> Publishing (-Yes) ...'
    } elseif ([System.Console]::IsInputRedirected) {
        Stop-Publish @'
Not publishing: no terminal to confirm on. Re-run with -Yes to publish
non-interactively.
'@
    } else {
        $answer = Read-Host "  Publish to r2://$env:R2_BUILDS_BUCKET? [y/N]"
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host ''
            Say "Not published. $ARTIFACT is built and waiting."
            exit 0
        }
    }

    # Upload with a presigned PUT rather than an SDK, using the same
    # scripts/r2_presign.py the Linux build uses — it is stdlib-only and
    # platform-neutral, so there is exactly one implementation of the
    # signing on the build side.
    function Send-Object([string]$File, [string]$Key) {
        $url = & $PYTHON scripts/r2_presign.py --key $Key --method PUT --expires 3600
        if ($LASTEXITCODE -ne 0) { return $false }
        # --progress-bar draws to stderr while the transfer runs; -w prints
        # the totals when it finishes. Both, because the bar is what
        # reassures you during a slow upload and it is also what renders as
        # nothing on a fast one.
        $stats = & curl.exe -fSL --progress-bar --retry 3 --retry-delay 5 `
            -w '%{size_upload} %{speed_upload} %{time_total}' `
            -T $File "$url"
        if ($LASTEXITCODE -ne 0) { return $false }
        $parts = "$stats".Trim() -split '\s+'
        $sent = [double]$parts[0]
        Write-Host ('    sent {0:N1} MB in {1}s' -f ($sent / 1MB), $parts[2])
        # A PUT that uploaded nothing is not a success, whatever the status
        # code said. Worth its own check: an empty or unreadable artifact
        # would otherwise publish as a perfectly valid zero-byte build, and
        # the first thing to notice would be a customer's machine failing
        # to execute it.
        if ($sent -eq 0) {
            Write-Host "    ERROR: nothing was uploaded — $File is empty or unreadable" -ForegroundColor Red
            return $false
        }
        return $true
    }

    Say ">>> Uploading $OUTPUT_NAME ..."
    if (-not (Send-Object $ARTIFACT "builds/$sha/$OUTPUT_NAME")) {
        Die @"
ERROR: uploading the binary to R2 failed.
       Check R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY are an
       Object Read & Write token for $env:R2_BUILDS_BUCKET.
"@
    }

    Say '>>> Uploading start.ps1 ...'
    if (-not (Send-Object $START_SCRIPT 'start.ps1')) {
        Die @"
ERROR: uploading start.ps1 to R2 failed. The binary is already up
       at builds/$sha/$OUTPUT_NAME — re-run with -UploadOnly, which
       re-transfers it but breaks nothing.
"@
    }

    # Registering is what actually publishes: until this runs, the bytes
    # are in the bucket and no channel points at them, so customers carry
    # on running the previous build.
    Say '>>> Registering the build ...'
    $env:KREA2_REG_SHA = $sha
    $env:KREA2_REG_SIZE = "$size"
    $env:KREA2_REG_COMMIT = $commit
    $env:KREA2_REG_BRANCH = $branch
    $env:KREA2_REG_CHANNEL = $channel
    $env:KREA2_REG_URL = $apiUrl
    $env:KREA2_REG_FILENAME = $OUTPUT_NAME

    # Inline Python rather than PowerShell's Invoke-RestMethod: it is the
    # same request build.sh sends, written the same way, so the two
    # registrations cannot drift in what they put on the wire. It also
    # keeps the error handling — the 401 hint especially — identical.
    $register = @'
import json, os, platform, sys, time
import urllib.error, urllib.request

body = json.dumps({
    "sha256": os.environ["KREA2_REG_SHA"],
    "size": int(os.environ["KREA2_REG_SIZE"]),
    "git_commit": os.environ.get("KREA2_REG_COMMIT") or None,
    "git_branch": os.environ.get("KREA2_REG_BRANCH") or None,
    "arch": f"{platform.system().lower()}-{platform.machine()}",
    # The field that keeps this artifact away from Linux pods. `arch`
    # above is close but not it: platform.machine() is not lowercased, so
    # it reads "windows-AMD64", and the server must not have to parse a
    # field whose case varies to decide who may download a build.
    "platform": "windows",
    # What the object is called inside its content-addressed prefix, so
    # the API signs a URL for the name that was actually uploaded.
    "filename": os.environ["KREA2_REG_FILENAME"],
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "promote": os.environ["KREA2_REG_CHANNEL"],
}).encode()

request = urllib.request.Request(
    f"{os.environ['KREA2_REG_URL'].rstrip('/')}/v1/admin/builds",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ['KREA2_ADMIN_TOKEN']}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        json.load(response)
except urllib.error.HTTPError as err:
    detail = (err.read() or b"").decode(errors="replace")[:400]
    # 401 here is the single most likely way this step fails, and it is
    # worth naming: the bytes are already uploaded, so the fix is one
    # variable and an -UploadOnly re-run, not a rebuild.
    print(f"  HTTP {err.code} from the API: {detail}", file=sys.stderr)
    if err.code == 401:
        print("  KREA2_ADMIN_TOKEN does not match the ADMIN_TOKEN set on "
              "that deployment.", file=sys.stderr)
    if err.code == 400 and "platform" in detail:
        print("  This deployment does not know about platforms yet. Deploy "
              "the licence server changes before publishing a Windows "
              "build — until then a Windows build cannot be registered, "
              "which is the safe failure.", file=sys.stderr)
    raise SystemExit(1)
except Exception as err:
    print(f"  could not reach {os.environ['KREA2_REG_URL']}: {err}",
          file=sys.stderr)
    raise SystemExit(1)
'@
    # A file rather than -c, for the reason in Invoke-PythonFile. This one
    # runs in the foreground rather than through that helper because its
    # stderr is the diagnosis when registration fails.
    $registerFile = Join-Path ([System.IO.Path]::GetTempPath()) ('krea2-register-' + [guid]::NewGuid().ToString('N') + '.py')
    [System.IO.File]::WriteAllText($registerFile, $register, (New-Object System.Text.UTF8Encoding($false)))
    try {
        & $PYTHON $registerFile
        $registered = ($LASTEXITCODE -eq 0)
    } finally {
        Remove-Item -LiteralPath $registerFile -Force -ErrorAction SilentlyContinue
    }
    if (-not $registered) { Die 'ERROR: registering the build failed.' }

    Write-Host @"

>>> Published: builds/$sha/$OUTPUT_NAME  ->  channel "$channel" (windows)

    What a Windows customer runs. Two lines, saved as a .ps1 or a
    shortcut, which fetch the current start script every time — so they
    pick up every future build and every fix to the start script without
    being sent a new file:

      `$s = "`$env:TEMP\krea2-start.ps1"
      curl.exe -fsSL https://`$env:KREA2_NODE_TAG.vercel.app/v1/start.ps1 -o `$s; powershell -ExecutionPolicy Bypass -File `$s

    To roll back, every build stays in the bucket and in the builds
    collection. List them and move the channel — no re-upload, and
    customers take it on their next start. A promote is scoped to the
    build's own platform, so this cannot disturb Linux pods:

      curl.exe -s -H "Authorization: Bearer `$env:KREA2_ADMIN_TOKEN" ``
           $apiUrl/v1/admin/builds
      curl.exe -s -X POST -H "Authorization: Bearer `$env:KREA2_ADMIN_TOKEN" ``
           -H "Content-Type: application/json" ``
           -d '{\"sha256\":\"<older sha>\",\"channel\":\"$channel\"}' ``
           $apiUrl/v1/admin/builds/promote

    To hold one customer on a specific build, set build_sha on their
    licence document instead — it wins over the channel. Note that a pin
    names ONE artifact and an artifact is for one OS: a customer running
    both a pod and a Windows machine cannot be pinned for both at once.

    The .exe needs python3, git, an NVIDIA driver and disk on the target
    machine — it installs ComfyUI and downloads models on first run,
    exactly like 'python app.py' does.
"@
}

if ($UploadOnly) {
    Say ">>> -UploadOnly: not compiling, publishing what is in $OUTPUT_DIR\"
    Invoke-Publish
    exit 0
}

# ── Tooling ──────────────────────────────────────────────────────────────
#
# Nothing here is apt-get. build.sh installs build-essential, patchelf and
# ccache because a fresh pod has none of them and pods are ephemeral; a
# Windows desktop has no equivalent, and the one thing Nuitka genuinely
# needs — a C compiler — it can fetch itself.
#
# patchelf has no meaning here at all: it rewrites RPATHs in ELF binaries,
# and there are none. Neither does --static-libpython, which is a Unix
# linking choice; passing it on Windows is an error rather than a no-op,
# which is why the have_python_headers probe from build.sh is absent
# rather than ported.
Say '>>> Checking the build tooling ...'

$hasMsvc = $false
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $vswhere) {
    $found = Get-NativeOutput { & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath -latest }
    if ($found) { $hasMsvc = $true }
}

# Which interpreter is doing the building decides whether MinGW64 is even an
# option, so it is read here rather than assumed.
#
# Nuitka refuses --mingw64 outright on Python 3.13 and later: "FATAL: Error,
# cannot use '--mingw64' on Python version 3.13 or higher." Finding that out
# from Nuitka is finding it out at the END of this script, after the pip
# installs, the dependency checks and the hf_xet lookup have all passed —
# which is exactly the shape of failure the tooling section exists to
# prevent. So the combination is rejected here, with the fix.
$pyMajorMinor = Invoke-PythonFile 'import sys; print("%d.%d" % sys.version_info[:2])'
if (-not $pyMajorMinor) { Die "ERROR: could not run $PYTHON." }
$pyParts = $pyMajorMinor -split '\.'
$mingwPossible = ([int]$pyParts[0] -eq 3 -and [int]$pyParts[1] -le 12)

Say "    building with Python $pyMajorMinor"
if ($hasMsvc) {
    Say '    MSVC build tools found — Nuitka will use them'
} elseif ($mingwPossible) {
    # Nuitka downloads a MinGW64 toolchain on demand and
    # --assume-yes-for-downloads accepts that without a prompt, which is
    # the difference between "install Visual Studio first" and "the build
    # takes a few more minutes the first time".
    Say '    no MSVC build tools — Nuitka will download MinGW64 instead.'
    Say '    For faster builds, install "Desktop development with C++" from'
    Say '    https://visualstudio.microsoft.com/visual-cpp-build-tools/'
} else {
    Die @"
ERROR: no C compiler Nuitka can use.

       This machine has no MSVC build tools, and Nuitka will not use
       MinGW64 on Python $pyMajorMinor — it supports it only up to 3.12.
       Two ways out, and the second is the better one anyway:

       1) Install the MSVC build tools (a few GB), then re-run:
            https://visualstudio.microsoft.com/visual-cpp-build-tools/
            Choose "Desktop development with C++".

       2) Build with Python 3.12, which needs no MSVC and is the version
          the pod builds on and the app is tested against:
            py -3.12 -m venv .venv312
            .\.venv312\Scripts\python.exe -m pip install -r requirements.txt
            `$env:PYTHON = "`$PWD\.venv312\Scripts\python.exe"
            .\build.ps1
"@
}

if (-not (Test-NativeOk { & $PYTHON -c 'import nuitka' })) {
    Say '    pip install nuitka ...'
    & $PYTHON -m pip install -q "nuitka[onefile]"
    if ($LASTEXITCODE -ne 0) { Die 'ERROR: could not install nuitka.' }
}

# zstandard is what compresses the onefile payload. Plain `pip install
# nuitka` does not pull it, and its absence is only a warning — so the
# build succeeds and quietly produces a much larger binary. That size is
# paid on every start, by every customer, forever.
if (-not (Test-NativeOk { & $PYTHON -c 'import zstandard' })) {
    Say '    pip install zstandard (onefile compression) ...'
    & $PYTHON -m pip install -q zstandard
}

# Nuitka can only compile in what it can import, so these must be present
# in the build interpreter.
Say ">>> Checking the app's own dependencies are present ..."
$appImports = 'import fastapi, uvicorn, huggingface_hub, requests, safetensors, websocket, PIL, hf_xet'
if (Test-NativeOk { & $PYTHON -c $appImports }) {
    Say '    all present'
} else {
    Say '    missing — installing from requirements.txt ...'
    & $PYTHON -m pip install -q -r requirements.txt
    & $PYTHON -c $appImports
    if ($LASTEXITCODE -ne 0) {
        Die 'ERROR: dependencies still not importable after install.'
    }
    Say '    installed'
}

# Checked rather than left to Nuitka, and fatal rather than a warning. A
# binary built without these still runs — it just quietly stops using the
# mirror and the pins, which is the one failure here that looks like
# success. It surfaces much later as a customer being asked for a CivitAI
# token, or as weights that changed under a release nobody rebuilt.
Say '>>> Checking the mirror data files are present ...'
foreach ($datafile in 'scripts/mirror_manifest.json', 'scripts/PINS.json') {
    if (-not (Test-Path -LiteralPath $datafile)) {
        Die @"
ERROR: $datafile is missing.
       It is compiled into the binary; without it the app runs
       un-mirrored and un-pinned, downloading every weight from
       its original upstream. Restore it from git, or run
       scripts/mirror_to_hf.py --pins-only to regenerate PINS.
"@
    }
}
Say '    mirror_manifest.json + PINS.json will be bundled'

# hf_xet's metadata, located here because the path carries its version.
#
# Why this is not just --include-distribution-metadata: Nuitka's own
# metadata finder matches distribution names EXACTLY, while
# importlib.metadata treats hf_xet and hf-xet as the same distribution.
# huggingface_hub asks for the underscore spelling (is_package_available
# -> importlib.metadata.version("hf_xet")) and Nuitka can only register
# the hyphen one, so neither spelling of that flag switches Xet on.
#
# Shipping the real .dist-info as a data directory puts it on sys.path,
# where Python's ordinary path-based finder — which does normalise —
# resolves it.
#
# Fatal, not skipped: without it every Xet-backed download silently drops
# to single-stream HTTP, turning a model fetch of seconds into minutes.
# The React bundle is a COMMITTED generated module (webui_bundle.py), not
# something this script builds — neither build host has Node, which is the
# whole reason it is committed. So the only thing to check here is that it
# was regenerated after the last edit to webui/src.
#
# Before the compile on purpose: a stale front end is not a build error,
# it is a binary that compiles, runs, and serves whatever the UI looked
# like the last time somebody remembered to run `make webui`. Finding that
# out after twenty minutes of Nuitka is finding it out too late.
Say '>>> Checking the React bundle is current ...'
& $PYTHON scripts/check_webui.py
if ($LASTEXITCODE -ne 0) {
    Die 'ERROR: the React bundle is stale. Run `make webui` on a machine with Node and commit webui_bundle.py.'
}

Say ">>> Locating hf_xet's distribution metadata ..."
$locate = @'
import glob, pathlib
import hf_xet
site = pathlib.Path(hf_xet.__file__).resolve().parent.parent
found = sorted(glob.glob(str(site / "hf_xet-*.dist-info")))
print(found[0] if found else "")
'@
$xetDistinfo = Invoke-PythonFile $locate
$xetMetadata = @()
if ($xetDistinfo -and (Test-Path -LiteralPath $xetDistinfo)) {
    $xetMetadata = @("--include-data-dir=$xetDistinfo=$(Split-Path -Leaf $xetDistinfo)")
    Say "    $(Split-Path -Leaf $xetDistinfo) will be bundled"
} else {
    Die @"
ERROR: could not find hf_xet's .dist-info next to the installed
       package. Without it the binary compiles fine and then runs
       every model download over plain HTTP instead of Xet.
       Try: $PYTHON -m pip install --force-reinstall hf_xet
"@
}

# ── The Nuitka argument list ─────────────────────────────────────────────
# Duplicated from build.sh; scripts/check_build_args.py is what keeps the
# two honest. Every comment below is there because the flag it explains is
# not obvious, and each one is a bug that already happened.
$sharedArgs = @(
    # Bundled next to __file__, which is where config.PROJECT_DIR points.
    # deps/ carries the vendored ReActor pack that install_reactor() copies
    # into custom_nodes instead of cloning from GitHub.
    '--include-data-dir=deps=deps'
    '--include-data-files=requirements.txt=requirements.txt'

    # The pricing page's feature showcase — the copy only. A few KB of
    # JSON, read at runtime from config.ASSETS_DIR (= PROJECT_DIR/assets,
    # i.e. inside the extraction dir). Leaving it out is not fatal: the
    # page falls back to the plan cards alone and logs why.
    #
    # The screenshots it names are deliberately NOT bundled. They are
    # served from the public R2 bucket in KREA2_SHOWCASE_URL and fetched by
    # the customer's browser, which keeps a page of forty pictures out of a
    # onefile binary that is re-extracted on every launch — and means a new
    # screenshot is an upload rather than a release.
    '--include-data-files=assets/showcase/showcase.json=assets/showcase/showcase.json'

    # mirror.py looks for these at PROJECT_DIR first, then
    # PROJECT_DIR/scripts — they live under scripts/ in a checkout so
    # Nuitka never sweeps the operator tooling in, and are flattened to the
    # root here. Without them the binary runs un-mirrored and un-pinned:
    # mirror.location() answers None for everything, so every weight goes
    # to its original upstream and the CivitAI LoRAs start needing a
    # customer CIVITAI_TOKEN that the mirror exists precisely to make
    # unnecessary. It degrades silently — two warnings at startup and then
    # apparently normal behaviour.
    '--include-data-files=scripts/mirror_manifest.json=mirror_manifest.json'
    '--include-data-files=scripts/PINS.json=PINS.json'

    # huggingface_hub imports hf_xet inside a try/except and only when a
    # repo is Xet-backed, so the import graph does not reach it and the
    # binary silently ships without it. The cost is not subtle: every
    # Xet-backed download drops to single-stream HTTP, which is the whole
    # difference between a model fetch taking seconds and taking minutes.
    # It is a compiled extension, so it must be bundled — a pip install on
    # the target machine is invisible to the huggingface_hub inside this
    # bundle.
    #
    # The metadata flag is the one that actually switches Xet on, for the
    # same reason it is needed for gradio below: huggingface_hub does not
    # probe with an import, it calls importlib.metadata.version("hf_xet")
    # in utils/_runtime.py. Without the .dist-info that raises
    # PackageNotFoundError, so the package is compiled in and reported
    # missing — the exact warning the pod logs on every download.
    #
    # Note the two spellings: the importable module is hf_xet, the
    # distribution on PyPI is hf-xet. Nuitka wants each flag given the name
    # that belongs to it and warns rather than fails on the wrong one, so
    # getting this backwards produces a working build that is still missing
    # the metadata Xet detection depends on.
    '--include-package=hf_xet'
    '--include-distribution-metadata=hf-xet'

    # The React front end, as a generated Python module rather than as
    # data files: Nuitka follows imports, so naming the module is enough,
    # while data files would need a correct destination for every asset
    # and a runtime path that resolves inside a onefile extraction.
    # webui_bundle.py is committed — see scripts/gen_webui_bundle.py — so
    # this host never needs Node. check_webui.py above is what stops a
    # stale one shipping.
    '--include-module=webui_bundle'

    # uvicorn is NOT optional, and its absence is the failure this whole
    # file exists to prevent: it resolves its protocol and loop backends
    # by STRING import — "uvicorn.protocols.http.h11_impl",
    # "uvicorn.loops.asyncio" — so the import graph never reaches them.
    # Without this flag the binary compiles, starts, prints its banner and
    # dies inside uvicorn.run() on a module that was never bundled.
    #
    # fastapi/starlette/h11/anyio follow the same shape less severely
    # (starlette picks middleware and response classes dynamically), and
    # naming them costs nothing next to a mistake that only shows up at
    # run time.
    '--include-package=uvicorn'
    '--include-package=fastapi'
    '--include-package=starlette'
    '--include-package=h11'
    '--include-package=anyio'

    # pydantic builds its validators at import time from type annotations,
    # and pydantic_core is the Rust extension that runs them — the same
    # reasoning as hf_xet above: a compiled extension has to be bundled,
    # because a pip install next to the binary is invisible to the
    # pydantic inside it. --include-package-data=pydantic carries the
    # version file its own import reads.
    '--include-package=pydantic'
    '--include-package=pydantic_core'
    '--include-package-data=pydantic'
)

# The platform-specific half. Each of these is a place build.sh cannot be
# copied from:
#
#   --jobs          nproc does not exist; Windows publishes the count as
#                   an environment variable.
#   --output-filename  a Windows file without .exe cannot be executed at
#                   all, which is also why the R2 object and the build
#                   document carry the extension.
#   --mingw64       only when MSVC is absent AND the build interpreter is
#                   3.12 or older. Nuitka prefers MSVC when it can find it,
#                   and passing --mingw64 anyway would force the slower
#                   toolchain onto a machine that has the fast one — while
#                   passing it on 3.13+ is a hard error (see the tooling
#                   section, which refuses that combination before any of
#                   the slow steps run).
#
# No --static-libpython: it is Unix-only. No --windows-console-mode
# either — console is the default, and the app prints the UI link to the
# console, so the default is what is wanted. Naming the flag would only
# pin behaviour to a Nuitka version that spells it that way.
$jobs = $env:NUMBER_OF_PROCESSORS
if (-not $jobs) { $jobs = '4' }
$platformArgs = @(
    '--standalone'
    '--onefile'
    '--assume-yes-for-downloads'
    "--jobs=$jobs"
    "--output-dir=$OUTPUT_DIR"
    "--output-filename=$OUTPUT_NAME"
    '--remove-output'
)
if (-not $hasMsvc -and $mingwPossible) { $platformArgs += '--mingw64' }

# The icon is a nicety, not a requirement, and every failure below is a
# skip rather than an error: a build that stopped because a PNG moved would
# be a worse trade than an .exe wearing the default Nuitka icon. It is
# worth some effort though — this is the icon on a customer's desktop
# shortcut and in their taskbar, which is the whole of the app's presence
# on the machine before it opens a browser tab.
#
# Converted here with Pillow rather than handed to Nuitka as a PNG.
# --windows-icon-from-ico does accept a PNG, but only by delegating the
# conversion to imageio, and without it the build dies at the very end:
#
#   FATAL: Need to install 'imageio' to automatically convert the non
#   native icon image (PNG) ...
#
# Pillow is already a hard dependency of the app (config/ui import it, and
# the dependency check above refuses to build without it), so doing it here
# costs no new build-time package. imageio would be one — installed on
# every build machine forever, to convert one file that never changes.
$platformArgs += Get-IconArgs

Say '>>> Compiling (the first build is slow — every package above is compiled) ...'

$nuitkaArgs = @('-m', 'nuitka') + $platformArgs + $sharedArgs + $xetMetadata + @('app.py')
& $PYTHON $nuitkaArgs
if ($LASTEXITCODE -ne 0) { Die "ERROR: the Nuitka build failed (exit $LASTEXITCODE)." }

Write-Host ''
Say ">>> Built: $ARTIFACT"

# The equivalent of build.sh's `strings | grep`. There is no strings on
# Windows, so the bytes are searched directly for the ASCII of strings
# that only exist in this app's source. Same purpose: catch a build that
# shipped readable source, which is the one defect a successful compile
# can still hide.
#
# Three needles, and they are not all the same question.
#
#   def generate_single   licensed Python logic shipping as readable
#                         source. It lives in handlers.py now, not ui.py.
#   sourceMappingURL      a Vite build with sourcemaps on, which would put
#   webui/src/            the whole TSX tree inside the binary.
#
# The front-end pair is not about secrecy — a browser is handed that
# JavaScript in cleartext by definition, and gzipping it means it is not
# greppable here anyway. It is about `vite build` having silently run in a
# mode nobody asked for: a sourcemap is megabytes of dead weight in a
# onefile binary that re-extracts on every launch, and it names every file
# in webui/src.
#
# scripts/check_build_args.py cannot see any of this — it diffs FLAGS
# between the two scripts, and this is shared logic — so it checks that
# both scripts still carry the needles, the way it already does for the
# hf_xet discovery block.
Say '>>> Sanity check — no source should ship:'
$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ARTIFACT))
$sanityOk = $true
foreach ($text in 'def generate_single', 'sourceMappingURL', 'webui/src/') {
    $needle = [System.Text.Encoding]::ASCII.GetBytes($text)
    $hit = -1
    for ($i = 0; $i -le $bytes.Length - $needle.Length; $i++) {
        if ($bytes[$i] -eq $needle[0]) {
            $match = $true
            for ($j = 1; $j -lt $needle.Length; $j++) {
                if ($bytes[$i + $j] -ne $needle[$j]) { $match = $false; break }
            }
            if ($match) { $hit = $i; break }
        }
    }
    if ($hit -ge 0) {
        Write-Host "    WARNING: found '$text' in the binary (offset $hit)" -ForegroundColor Yellow
        $sanityOk = $false
    }
}
if ($sanityOk) { Say '    OK: no app source found' }

if ($Publish) {
    Invoke-Publish
} else {
    Write-Host @"

Not publishing (the default). Test it, then ship it with:

    .\build.ps1 -UploadOnly

"@
}
