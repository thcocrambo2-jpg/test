#Requires -Version 5.1
<#
    Windows start script — the desktop equivalent of runpod_start.sh.
    It fetches the published build, checks it, and runs it.

    The customer sets two environment variables and nothing else:

        KREA2_LICENSE_KEY   the key they were issued
        KREA2_NODE_TAG      the deployment id issued with it

    and then runs, from PowerShell:

        powershell -ExecutionPolicy Bypass -File krea2-start.ps1

    There is no credential in this file. The build comes from a private
    bucket, but the thing that opens it is the customer's own licence key,
    which is already on their machine — exactly as on a pod. Nothing here
    has to be a secret, which is what lets it be published to R2 and
    fetched fresh on every start.

    What limits who can *run* the app is still the seat check the binary
    makes on startup, not where it was downloaded from. Gating the download
    stops a lapsed key fetching a new build and shows which machines pull
    on which key; it does not stop a binary someone already has from being
    copied, and nothing here pretends otherwise.

    Optional, for when something is wrong:

        KREA2_BASE_DIR      where models and outputs live (default below)
        CIVITAI_TOKEN       only if a CivitAI download starts refusing
                            anonymous

    Which build a licence gets is decided by the server, so pinning one
    customer to an older build or rolling everybody back is done there —
    see /v1/admin/builds/promote — rather than by talking a customer
    through editing anything here.

    ── What this machine still needs ────────────────────────────────────
    The .exe is NOT self-contained, and that is by design rather than an
    oversight. The app never imports torch or ComfyUI: it pip-installs
    them and runs ComfyUI as a separate process (see
    bootstrap.runtime_python). So the machine needs, and this script
    checks for:

        Python 3.12   a real interpreter for ComfyUI and pip
        git           ComfyUI is cloned, not vendored
        an NVIDIA GPU with a current driver

    ── PowerShell 5.1 ───────────────────────────────────────────────────
    Written for it deliberately: it is what `powershell.exe` is on a stock
    Windows 11, and a customer should not have to install PowerShell 7 to
    start an app. That rules out ??, ?:, ?. and -AsHashtable, all of which
    are parse errors there rather than runtime failures — so a script that
    used one would not run at all, on the machine it was written for.
#>

Set-StrictMode -Version 2.0
# PowerShell's default is to carry on after a failed cmdlet, which for a
# script that downloads and then executes a binary is the wrong default in
# the most expensive way. This is the closest thing to `set -e`.
$ErrorActionPreference = 'Stop'
# Invoke-WebRequest and friends render a progress bar that costs more than
# the transfer on 5.1. Nothing here uses them for bulk data (curl.exe does
# that), but Unblock-File and the rest inherit this too.
$ProgressPreference = 'SilentlyContinue'
# PowerShell 7.4 turns a non-zero exit from a native command into a
# terminating error when $ErrorActionPreference is Stop. curl exiting
# non-zero is an expected, handled case here — every call checks
# $LASTEXITCODE and has a message for it — so that behaviour would replace
# a written-for-the-customer error with a stack trace. Assigning this is
# harmless on 5.1, where it is just an unused variable.
$PSNativeCommandUseErrorActionPreference = $false

$NAME = 'krea2app.exe'

# ── Where everything lives ───────────────────────────────────────────────
# C:\krea2 rather than somewhere under the user profile: this holds ~90 GB
# of weights, profiles are often on a small system drive, and paths under
# AppData are long enough to matter to ComfyUI's deeply nested node packs.
# A standard user can create a directory at the root of C:, so this needs
# no elevation.
#
# config.py defaults KREA2_BASE_DIR to /workspace/krea2, which on Windows
# resolves to C:\workspace\krea2 — a pod path that happens to be a legal
# Windows one. Setting it explicitly here is what stops the app quietly
# using it.
$BASE = $env:KREA2_BASE_DIR
if ([string]::IsNullOrWhiteSpace($BASE)) { $BASE = 'C:\krea2' }
$BIN_DIR = Join-Path $BASE 'bin'
$BIN = Join-Path $BIN_DIR $NAME

function Say([string]$Message) { Write-Host "[krea2] $Message" }

function Die([string]$Message) {
    Write-Host ''
    Write-Host "[krea2] ERROR: $Message" -ForegroundColor Red
    Write-Host ''
    exit 1
}

Write-Host ''
Say 'starting'

# ── What the customer has to have set ────────────────────────────────────
# Checked here rather than left to the binary so a missing variable costs a
# few seconds instead of a 300 MB download. The wording matches what
# licensing.py says for the same two problems, so a customer who hits one
# of them later reads the same sentence twice rather than two different
# ones about the same mistake.
if ([string]::IsNullOrWhiteSpace($env:KREA2_LICENSE_KEY)) {
    Die @'
No license key found.
       Set KREA2_LICENSE_KEY to the key you were given, then run this
       script again:

           $env:KREA2_LICENSE_KEY = "<your key>"

       To keep it across reboots:

           [Environment]::SetEnvironmentVariable(
               "KREA2_LICENSE_KEY", "<your key>", "User")
'@
}

if ([string]::IsNullOrWhiteSpace($env:KREA2_NODE_TAG)) {
    Die @'
This machine is missing its node tag.
       Set KREA2_NODE_TAG to the value issued with your license key, then
       run this script again:

           $env:KREA2_NODE_TAG = "<your node tag>"
'@
}

# Normalised and then checked exactly as config.py does it, so a tag the
# binary would accept is never rejected here — the two must agree, or this
# fails with a message about the tag and then works fine the moment
# someone lowercases it by hand. config.py strips and lowercases;
# whitespace anywhere fails the pattern either way, so removing all of it
# reaches the same verdict more legibly.
$NODE_TAG = ($env:KREA2_NODE_TAG -replace '\s', '').ToLowerInvariant()

# One DNS label and nothing else. The tag is what the endpoint is assembled
# from, so a value carrying a dot, a slash, a colon or a port is a value
# pointing this script at a host of someone else's choosing — which at the
# download step below means running a binary from wherever they nominated.
# Rejected outright rather than sanitised: whatever is left after removing
# the offending characters is not a tag anybody issued.
if ($NODE_TAG -notmatch '^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$') {
    Die @'
this machine's node tag is not valid.
       Set KREA2_NODE_TAG to the value issued with your license key -
       it is a single name, with no dots, slashes or colons in it.
'@
}

$API = "https://$NODE_TAG.vercel.app"

# The machine id, so the server can tell one machine's downloads from
# another's on the same key. This is the value licensing.py will report
# once the app starts (_resolve_instance_id falls through to
# "host-<hostname>" when RunPod's variables are absent, which they always
# are here), so the download row and the seat row name the same machine
# rather than two.
$INSTANCE = "host-$([System.Net.Dns]::GetHostName())"

$keyPrefix = $env:KREA2_LICENSE_KEY.Substring(
    0, [Math]::Min(10, $env:KREA2_LICENSE_KEY.Length))
Say "license key $keyPrefix... | node tag $NODE_TAG"

# ── Tooling ──────────────────────────────────────────────────────────────
# A pod's base image ships all of this and runpod_start.sh apt-gets
# anything missing. A Windows desktop ships almost none of it and there is
# no equivalent of apt-get to fall back on, so this reports rather than
# installs — with the exact source for each, because "install Python"
# leads to the Microsoft Store build often enough to be worth pre-empting.
#
# curl.exe is in System32 on Windows 10 1803 and later, so it is present on
# anything that can run this app. It is used instead of Invoke-WebRequest
# for the same reasons runpod_start.sh uses it: real retry semantics, a
# distinguishable exit code for an expired signed URL, streaming rather
# than buffering a few hundred megabytes in memory, and an HTTP error body
# that is still readable instead of thrown away.
$missing = @()
foreach ($tool in 'curl.exe', 'python', 'git') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        $missing += $tool
    }
}
if ($missing.Count -gt 0) {
    Die @"
this machine is missing: $($missing -join ', ')

       python   Python 3.12 from https://www.python.org/downloads/
                Tick "Add python.exe to PATH" in the installer. The app
                does not run on this interpreter - it runs ComfyUI on it,
                which is why it is needed even though the app is a .exe.
       git      https://git-scm.com/download/win - ComfyUI is cloned.
       curl.exe ships with Windows 10 1803 and later. If it is genuinely
                absent, this machine is too old for the app.

       Close and reopen PowerShell after installing, so PATH is re-read.
"@
}

# The interpreter the app will find, reported now rather than discovered
# halfway through a model download. 3.12 is what the pod runs and what
# everything is tested on; anything else is allowed through with a note,
# because ComfyUI mostly does not care and refusing would be worse than
# the risk.
#
# Not redirected with 2>&1: in PowerShell 5.1 that wraps a native
# command's stderr in ErrorRecords, which with $ErrorActionPreference =
# 'Stop' turns a version banner into a terminating error.
$pyVersion = "$(& python --version)" -replace '^Python\s+', ''
if ($pyVersion -and $pyVersion -notmatch '^3\.12\.') {
    Say "note: python $pyVersion - the app is tested on 3.12"
}

# Having an interpreter is not the same as having one that can install
# ComfyUI. bootstrap.runtime_python() validates this too and refuses an
# interpreter without pip, but finding out here is the difference between a
# message before anything is downloaded and the same message after the
# licence seat is taken and a few hundred megabytes have moved.
#
# The two Windows impostors that satisfy Get-Command and then cannot
# install anything are an MSYS2 python, which ships no pip, and the
# zero-byte Microsoft Store alias stub. Probed with find_spec rather than a
# bare `import pip` so a missing pip prints nothing: a traceback on stderr
# here would be noise, and redirecting it away is the 2>&1 trap above.
& python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -ne 0) {
    Die @"
python is on PATH, but that interpreter cannot import pip.

       ComfyUI is installed with pip and run as a separate process, so
       the app cannot continue without it. This is usually an MSYS2
       python (no pip) or the Microsoft Store alias sitting ahead of a
       real one on PATH.

       Install Python 3.12 from https://www.python.org/downloads/, tick
       "Add python.exe to PATH", and make sure it comes before any
       MSYS2 or WindowsApps entry. Check with:

           where.exe python

       Close and reopen PowerShell afterwards, so PATH is re-read.
"@
}

try {
    New-Item -ItemType Directory -Path $BIN_DIR -Force -ErrorAction Stop | Out-Null
} catch {
    Die "could not create $BIN_DIR - $($_.Exception.Message)"
}

# ── Two things Windows needs that Linux does not ─────────────────────────
# Both are warnings rather than errors: each breaks something the app does
# later, and neither is worth refusing to start over when the customer may
# only be trying the tabs that do not touch it.
#
# The symlink privilege is probed rather than read out of the registry, in
# the same spirit as build.sh's chmod probe: what matters is whether the
# operation works for this account, not what a policy key claims.
#
# The probe runs PYTHON, and that is the whole point of it. PowerShell
# 5.1's New-Item -ItemType SymbolicLink always demands elevation — it never
# passes SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE, which is what makes
# Developer Mode work — while Python's os.symlink does pass it. Probing
# with New-Item therefore fails on a perfectly good machine and sends the
# customer off to enable something already enabled. link_model_dirs() calls
# os.symlink, so os.symlink is what has to be asked. Verified both ways on
# a Developer Mode machine: New-Item raised "Administrator privilege
# required", python succeeded.
#
# Probed on a FILE in the temp directory rather than a directory inside
# $BASE: the privilege is the same either way, and PowerShell 5.1's
# Remove-Item has a long history of following a directory symlink and
# deleting what it points at, which here would be the model tree.
$probeTarget = Join-Path $env:TEMP ('krea2-probe-' + [guid]::NewGuid().ToString('N'))
$probeLink = "$probeTarget.link"
try {
    [System.IO.File]::WriteAllText($probeTarget, '')
    & python -c 'import os,sys; os.symlink(sys.argv[1], sys.argv[2])' $probeTarget $probeLink
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Say 'WARNING: this account cannot create symbolic links.'
        Say "  bootstrap.link_model_dirs() points ComfyUI's model folders at"
        Say "  $BASE\models with symlinks. Without them ComfyUI sees no models"
        Say '  and every generation fails validation with a message that says'
        Say '  nothing about symlinks. Fix it with either:'
        Say '    Settings -> System -> For developers -> Developer Mode = On'
        Say '    or run this script from an elevated PowerShell.'
        Write-Host ''
    }
} finally {
    # File.Delete removes the link itself and never follows it.
    [System.IO.File]::Delete($probeLink)
    [System.IO.File]::Delete($probeTarget)
}

# ComfyUI's custom node packs nest deeply, and a base directory that is
# itself short does not save a path five node packs down. The failure is a
# pip install or a git clone dying on a path nobody printed.
$longPaths = 0
try {
    $longPaths = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
        -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
} catch {
    $longPaths = 0
}
if ($longPaths -ne 1) {
    Write-Host ''
    Say 'WARNING: long paths are disabled on this machine. ComfyUI''s node'
    Say '  packs nest deeply enough to hit the 260-character path limit.'
    Say '  Turn it on from an elevated PowerShell, then reboot:'
    Say '    Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1'
    Write-Host ''
}

# ── Which build to run ───────────────────────────────────────────────────
# The build lives in a private bucket, so this asks the licence server for
# it: send the key and the sha256 of whatever is already on disk, get back
# either "that is the current one" or a short-lived download URL for the
# one this licence should have.
#
# Sending the hash we already have is what makes a restart free. The server
# mints no signature and writes no download record for a machine that is
# merely restarting, which keeps the rate limit measuring the thing it is
# meant to measure: distinct machines pulling the binary.
$have = ''
if (Test-Path -LiteralPath $BIN) {
    $have = (Get-FileHash -LiteralPath $BIN -Algorithm SHA256).Hash.ToLowerInvariant()
}

Say 'checking for the current build ...'

# platform is what keeps this machine from being handed the Linux build.
# The server defaults to linux when the field is absent, because that is
# what every pod running runpod_start.sh sends — so this side has to say
# what it is, every time.
$currentSha = $null
if ($have) { $currentSha = $have }
$payload = @{
    license_key = $env:KREA2_LICENSE_KEY
    instance_id = $INSTANCE
    current_sha = $currentSha
    platform    = 'windows'
} | ConvertTo-Json -Compress

# Written to a file and handed to curl with --data-binary @file rather than
# interpolated into the command line: the key would otherwise be visible in
# this machine's process list for the life of the request, and PowerShell
# 5.1's native-argument quoting mangles embedded quotes in ways that are
# entertaining to debug. UTF8 without a BOM, because a BOM would ride along
# into the JSON body and the server would reject it as malformed.
$payloadFile = Join-Path $env:TEMP ('krea2-build-req-' + [guid]::NewGuid().ToString('N') + '.json')
[System.IO.File]::WriteAllText($payloadFile, $payload, (New-Object System.Text.UTF8Encoding($false)))

# --fail is deliberately NOT used: a refusal here carries a message written
# for the customer ("this license expired on ..."), and -f would throw the
# body away and leave nothing to print but a number.
$raw = & curl.exe -sS --retry 5 --retry-delay 3 --retry-connrefused `
    -X POST -H 'Content-Type: application/json' `
    --data-binary "@$payloadFile" -w "`n%{http_code}" "$API/v1/build"
$rc = $LASTEXITCODE
Remove-Item -LiteralPath $payloadFile -Force -ErrorAction SilentlyContinue

# curl writes the status code on its own last line, so the body is
# everything before it, rejoined — a JSON body may itself contain
# newlines, and an error page from something in front of the API
# (Vercel's own 404, a captive portal) certainly does.
#
# PowerShell hands back multi-line output from a native command as an
# ARRAY of lines, already split. Casting it to a string first would join
# those lines with a space, leaving one line whose last "field" is the
# whole response — which is how an HTTP 404 first reported itself as
# "HTTP The deployment could not be found on Vercel ... 404".
$lines = @($raw)
$code = ''
$body = ''
if ($lines.Count -ge 2) {
    $code = "$($lines[-1])".Trim()
    $body = ($lines[0..($lines.Count - 2)] -join "`n")
} elseif ($lines.Count -eq 1) {
    # No body at all — curl printed only the status line, or nothing.
    $code = "$($lines[0])".Trim()
}

$upToDate = $false
$want = ''
$url = ''
$buildPlatform = ''
$message = ''
try {
    $parsed = $body | ConvertFrom-Json
    $fields = $parsed.PSObject.Properties.Name
    if ($fields -contains 'up_to_date') { $upToDate = [bool]$parsed.up_to_date }
    if ($fields -contains 'message' -and $parsed.message) {
        # Collapsed to one line so a multi-line server message cannot break
        # up the output this script writes around it.
        $message = (("$($parsed.message)" -split '\s+') -join ' ').Trim()
    }
    if ($fields -contains 'build' -and $parsed.build) {
        $b = $parsed.build.PSObject.Properties.Name
        if ($b -contains 'sha256') { $want = "$($parsed.build.sha256)" }
        if ($b -contains 'url') { $url = "$($parsed.build.url)" }
        if ($b -contains 'platform') { $buildPlatform = "$($parsed.build.platform)" }
    }
} catch {
    # A body that is not JSON is not worth distinguishing from an empty
    # one: both take the branch below, which falls back to the binary on
    # disk or reports the status code.
}

if ($rc -ne 0 -or $code -ne '200') {
    # Anything that is not a clean 200 — unreachable, expired, revoked,
    # rate limited, nothing published — takes the same branch, because the
    # right thing to do about all of them is identical: if there is a
    # verified build already on this machine, run it and let the seat check
    # in the binary deliver the real verdict. That check is the enforcement
    # anyway, its message is the one worth reading, and it means a licence
    # server having a bad afternoon does not brick a machine that has
    # everything it needs to run.
    if ($have) {
        $suffix = ''
        if ($message) { $suffix = " - $message" }
        Say "could not check for a newer build$suffix"
        Say 'running the build already on this machine'
    } elseif ($message) {
        Die $message
    } elseif ($rc -ne 0) {
        Die @"
could not reach the license server to fetch the app (curl $rc).
       This machine needs outbound internet access. If it has some, the
       server may be blocked - check with your supplier, and check
       whether a VPN or a corporate proxy is in the way.
"@
    } else {
        Die @"
the license server refused to hand out the app (HTTP $code).
       Nothing is wrong with this machine; contact your supplier.
"@
    }
} elseif ($upToDate) {
    # Checked even here. up_to_date is the server comparing the sha we sent
    # against the one it would serve, so a machine somehow holding a Linux
    # build would be told it is current — and this is the branch that would
    # then run it.
    if ($buildPlatform -ne 'windows') {
        Die @"
the license server says this machine is up to date with a build for
       "$buildPlatform", not for Windows. Delete this file and run the
       script again:

           $BIN

       If it happens twice, contact your supplier - it means a build was
       published to the wrong platform.
"@
    }
    Say 'already have this build - skipping the download'
} else {
    if (-not $want -or -not $url) {
        Die @'
the license server did not say which build to run.
       This is a problem at the supplier''s end, not on this machine.
'@
    }

    # The guard that makes a wrong-platform build a refusal instead of a
    # download. A missing field is refused too, and that is the important
    # half: a licence server that predates Windows support ignores the
    # platform we sent and answers with the Linux build, and this is the
    # only place that can tell.
    if ($buildPlatform -ne 'windows') {
        $described = $buildPlatform
        if (-not $described) { $described = 'no platform at all' }
        Die @"
the license server offered a build for $described, not for Windows.
       Nothing has been downloaded. This usually means the licence server
       has not been updated for Windows builds yet - contact your
       supplier and quote this message.
"@
    }

    if ($have) { Say 'a newer build is available - updating' }
    Say 'downloading the app (this takes a moment) ...'
    $tmp = "$BIN.part"
    # Removed first: a .part left by a machine killed mid-download belongs
    # to whatever build was current then, and resuming onto it would append
    # new bytes to old ones. The checksum below would catch it, but only
    # after another full download, and on every start from then on.
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    # Straight to .part and renamed only once the hash matches: a download
    # cut off by a dropped connection would otherwise leave a truncated
    # binary that looks installed and fails to start on every future run.
    & curl.exe -fL -sS --retry 5 --retry-delay 3 --retry-connrefused `
        --progress-bar -o "$tmp" "$url"
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        # 22 is the signed URL being refused. The realistic cause is a
        # connection slow enough that it expired mid-attempt, and starting
        # again gets a fresh one — so say that rather than sending them to
        # support.
        if ($rc -eq 22) {
            Die @'
the app download link was refused, which usually means it expired
       before the download finished. Run this script again to get a new
       one.
'@
        }
        Die @"
the app download failed (curl $rc) - check this machine's internet
       access and run this script again.
"@
    }

    $got = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $want.ToLowerInvariant()) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        Die @'
the downloaded app is damaged (checksum does not match).
       Run this script again - this is almost always an interrupted
       download and a second attempt fixes it.
'@
    }

    # Windows marks anything that arrived from the internet with a
    # Zone.Identifier stream, and SmartScreen refuses to run a marked
    # executable without a prompt the customer has to click through. curl
    # does not set the mark, but a customer who fetched the file another
    # way would have, and this costs nothing.
    Unblock-File -LiteralPath $tmp -ErrorAction SilentlyContinue

    # No chmod +x equivalent: on Windows the extension is what makes a file
    # executable, which is why the artifact is named .exe rather than
    # matching the Linux build's bare "krea2app".
    Move-Item -LiteralPath $tmp -Destination $BIN -Force
    Say 'downloaded and verified'
}

# ── The tunnel helper ────────────────────────────────────────────────────
# serve.py needs cloudflared to hand the customer a public URL, and since
# the Gradio UI was removed it is the ONLY thing that produces one. Left to
# itself the app fetches it from the GitHub "latest" release on first
# launch, which makes one github.com endpoint a hard dependency of every
# first start - and the failure mode is a customer with no link at all.
#
# So it is fetched here instead, from the same public Hugging Face mirror
# the weights come from, pinned to a release and checked against a known
# sha256. serve.cloudflared_binary() returns early when the file is already
# at KREA2_BASE_DIR, so putting it there is the entire change: no recompile,
# no new build published, just this script.
#
# EVERY failure below is a note rather than a hard stop. If the mirror is
# unreachable, or the bytes are wrong, the app still starts and still falls
# back to the GitHub release exactly as it does today - so this can only
# ever add a source, never take one away.
#
# Refreshing to a newer cloudflared is scripts/mirror_cloudflared.py, then
# the three constants below, then `make start-ps1` / `make start-sh`.
$CF_BIN = Join-Path $BASE 'cloudflared.exe'
$CF_URL = 'https://huggingface.co/thcocrambo2/krea2-tools/resolve/ba22c6ba0afc0c9f9b644c133310903831bbc17b/cloudflared/2026.8.3/cloudflared-windows-amd64.exe'
$CF_SHA = '83e726ed18ea78c5ad5213c4c3a3a27051393950d2bc8ed4de69bec12d14eaae'

# Not re-verified when it is already there: serve.py checks only that the
# file exists, and hashing 55 MB on every start to reach the same verdict
# would be the most expensive line in this script.
if (-not (Test-Path -LiteralPath $CF_BIN)) {
    $cfTmp = "$CF_BIN.part"
    # try/catch around the whole thing rather than just the download:
    # $ErrorActionPreference is 'Stop', so a locked file or a full disk at
    # the Move-Item below would otherwise end this script with a stack
    # trace - for an optional step whose failure the app already handles.
    try {
        Say 'fetching the tunnel helper (once) ...'
        Remove-Item -LiteralPath $cfTmp -Force -ErrorAction SilentlyContinue
        & curl.exe -fL -sS --retry 3 --retry-delay 2 --retry-connrefused --progress-bar -o "$cfTmp" "$CF_URL"
        $cfRc = $LASTEXITCODE
        if ($cfRc -ne 0) {
            Say "note: could not fetch the tunnel helper (curl $cfRc)."
            Say '      The app will download it from its original source.'
        } else {
            $cfGot = (Get-FileHash -LiteralPath $cfTmp -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($cfGot -ne $CF_SHA) {
                Say 'note: the tunnel helper arrived damaged (checksum does not match).'
                Say '      The app will download it from its original source.'
            } else {
                # Windows marks anything fetched from the internet with a
                # Zone.Identifier stream, and the app runs this file as a
                # child process. Same treatment the .exe above gets.
                Unblock-File -LiteralPath $cfTmp -ErrorAction SilentlyContinue
                # Renamed only once the hash matches. serve.py trusts the
                # file's mere existence, so a truncated one left at the real
                # name is a machine whose tunnel never works again - which is
                # the WinError 193 row in the README's table.
                Move-Item -LiteralPath $cfTmp -Destination $CF_BIN -Force
                Say 'tunnel helper ready'
            }
        }
    } catch {
        Say "note: could not install the tunnel helper - $($_.Exception.Message)"
        Say '      The app will download it from its original source.'
    } finally {
        # Whatever happened above, no .part survives it. The next start
        # would otherwise resume onto bytes from a different attempt.
        Remove-Item -LiteralPath $cfTmp -Force -ErrorAction SilentlyContinue
    }
}

# ── Run ──────────────────────────────────────────────────────────────────
$env:KREA2_BASE_DIR = $BASE

Write-Host @"

  Models and outputs live in $BASE. Deleting that directory means
  downloading everything again, so keep it if you reinstall.

  The first start installs ComfyUI and downloads the models your license
  covers, which takes a while. Watch this window: the app prints the link
  to open the UI when it is ready, between two lines of '='.

  Windows will probably warn about this app the first time. It is an
  unsigned binary, which is what SmartScreen and most antivirus react to -
  choose "More info" and then "Run anyway". If Defender makes every start
  slow, excluding $BASE from real-time scanning helps: a onefile build
  unpacks itself on each launch and gets rescanned every time.

"@

# There is no exec on Windows, so the app runs as a child process and this
# script waits for it. That is not merely a syntactic difference:
# runpod_start.sh uses exec so the binary becomes PID 1 and a container
# stop delivers SIGTERM straight to licensing.py's handler, which gives the
# seat back. Nothing here can reproduce that. Ctrl-C does reach the child
# (same console, same process group) and exits cleanly through atexit;
# closing the window does not, and that seat is freed by the server's
# stale-lease sweep a few minutes later instead.
& $BIN
exit $LASTEXITCODE
