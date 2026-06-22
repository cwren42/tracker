#Requires -RunAsAdministrator
<#
.SYNOPSIS
    One-click Cirque RMM Agent installer.
    Downloads agent files from the tracker server, installs Python dependencies,
    registers the Windows service, and sets up the tray application.
.PARAMETER Token
    Agent enrollment token from the tracker server (required).
.PARAMETER TrackerUrl
    Base URL of the Cirque Tracker. Default: https://tracker.corp.cirque.com
.PARAMETER GatewayUrl
    RMM gateway WebSocket URL. Default: wss://rmm.corp.cirque.com
.PARAMETER InstallDir
    Installation directory. Default: C:\Program Files\CirqueRMM
.PARAMETER AgentId
    Override agent ID (defaults to computer hostname).
.EXAMPLE
    .\install_agent.ps1 -Token "agent_abc123..."
.EXAMPLE
    Invoke-Expression (Invoke-WebRequest -Uri "https://tracker.corp.cirque.com/download/agent-installer" -UseBasicParsing).Content
#>

param(
    [Parameter(Mandatory = $false)]
    [string]$Token = "",

    # Site-wide enrollment token (baked into the MSI).
    # When supplied the installer calls /api/rmm/enroll to get a per-device token automatically.
    [string]$SiteToken = "",

    [string]$TrackerUrl  = "https://tracker.corp.cirque.com",
    [string]$GatewayUrl  = "wss://rmm.corp.cirque.com",
    [string]$TrackerUrlFallback = "https://tracker.cirquetools.com",
    [string]$GatewayUrlFallback = "wss://rmm.cirquetools.com",
    [string]$InstallDir  = "C:\CirqueRMM",
    [string]$NssmPath    = "C:\Program Files\NSSM\nssm.exe",
    [string]$AgentId     = "",

    # When set (e.g. invoked by the MSI installer), skips downloading agent files
    # because the MSI already installed them.
    [switch]$SkipDownload,

    # Python source. By DEFAULT the installer ships a PRIVATE, self-contained Python
    # under $InstallDir\python\ (embedded-Python bundle from the Tracker mirror) so the
    # endpoint needs NO system-wide Python and no python.org reachability. Pass
    # -UseSystemPython to fall back to the legacy behaviour (find/install a system
    # Python under C:\Program Files\Python312\) — kept for revert/escape-hatch only.
    [switch]$UseSystemPython
)

$ErrorActionPreference = "Stop"
$ServiceName = "CirqueRMM"
$TrayName    = "CirqueRMM Tray"
if (-not $AgentId) { $AgentId = $env:COMPUTERNAME }

# - Normalize TEMP before anything uses it. Some profiles (short-name / uninitialized /
#   redirected) have a $env:TEMP whose folder does not exist, which makes EVERY download
#   below (python bundle, wheelhouse, nssm) fail with "path ... does not exist". Fall back
#   to the system temp (always present + admin-writable). Wrapped so it can never throw. -
try {
    if (-not $env:TEMP -or -not (Test-Path -LiteralPath $env:TEMP)) {
        $env:TEMP = "$env:SystemRoot\Temp"; $env:TMP = "$env:SystemRoot\Temp"
    }
    New-Item -ItemType Directory -Force -Path $env:TEMP -ErrorAction SilentlyContinue | Out-Null
} catch {
    $env:TEMP = "$env:SystemRoot\Temp"; $env:TMP = "$env:SystemRoot\Temp"
    New-Item -ItemType Directory -Force -Path $env:TEMP -ErrorAction SilentlyContinue | Out-Null
}

# - Fallback crash log in TEMP: written before anything else so we always get SOMETHING -
$FallbackLog = "$env:TEMP\CirqueRMM_install.log"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') === Install starting on $env:COMPUTERNAME (PID $PID) SiteToken=$(if($SiteToken){'set'}else{'MISSING'}) ===" | Out-File $FallbackLog -Append -Encoding UTF8
trap {
    $errLine = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') FATAL: $_`n$($_.ScriptStackTrace)"
    # Stop transcript first so setup.log is not locked before we write to it
    try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
    $errLine | Out-File $FallbackLog -Append -Encoding UTF8
    try { $errLine | Out-File -FilePath $LogFile -Append -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
    Write-Host "FATAL ERROR: $_ (see $FallbackLog)" -ForegroundColor Red
    break
}

# - Bootstrap log dir early (before Start-Transcript) so failures are always captured -
$LogDir  = "$InstallDir\logs"
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch {
    # If InstallDir doesn't exist yet, create both levels
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}
$LogFile = "$LogDir\setup.log"
# Kill any previous installer task/process that may be holding setup.log open via an
# active transcript.  This runs BEFORE Start-Transcript so the file is not locked.
try {
    Stop-ScheduledTask  -TaskName 'CirqueRMM_Setup'  -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName 'CirqueRMM_Setup' -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
} catch { }
# Also kill any other powershell processes running install_agent.ps1 (exclude self)
Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and
    ($_.CommandLine -like '*install_agent.ps1*' -or $_.CommandLine -like '*CirqueRMM_Setup*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
# Stop any transcript from a previous run in this session before touching setup.log
try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch { }
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') === Cirque RMM Install starting (PID $PID) ===" | Out-File -FilePath $LogFile -Append -Encoding UTF8 -ErrorAction SilentlyContinue
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') SiteToken=$(if($SiteToken){'present'}else{'MISSING'})  TrackerUrl=$TrackerUrl  AgentId=$AgentId" | Out-File -FilePath $LogFile -Append -Encoding UTF8 -ErrorAction SilentlyContinue
try { Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch { }
Write-Host "Log: $LogFile"

# - Trust self-signed certs (common in internal networks) -
try {
    Add-Type -TypeDefinition @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAll : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint sp, X509Certificate cert,
        WebRequest req, int status) { return true; }
}
"@
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAll
} catch { <# type already loaded on re-run #> }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# - Use system proxy (so corporate proxy users can reach external URLs like browser does) -
try {
    [System.Net.WebRequest]::DefaultWebProxy = [System.Net.WebRequest]::GetSystemWebProxy()
    [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
} catch { }

# - HTTP via curl.exe ---------------------------------------------------------
# TeamViewer 15.76+ injects tv_x64.dll into every process, which breaks .NET's
# System.Net HTTP stack (Invoke-WebRequest/Invoke-RestMethod fail with "underlying
# connection was closed: an unexpected error occurred on a send") while native
# curl.exe (libcurl, built into Win10 1803+/Win11) is unaffected. So prefer curl;
# fall back to .NET only when curl.exe is absent. -k trusts the internal cert.
$script:CurlExe  = "$env:SystemRoot\System32\curl.exe"
$script:HaveCurl = Test-Path $script:CurlExe
function Get-HttpFile([string]$Url, [string]$OutFile, [int]$TimeoutSec = 300) {
    if ($script:HaveCurl) {
        # -g/--globoff so {}/[]/ in URLs (or tokens) aren't treated as curl globs.
        & $script:CurlExe -fsSL -k -g --retry 3 --retry-delay 2 --connect-timeout 30 --max-time $TimeoutSec -o $OutFile $Url
        if ($LASTEXITCODE -ne 0) { throw "curl download failed (exit $LASTEXITCODE): $Url" }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec $TimeoutSec
    }
}
function Invoke-HttpPost([string]$Url, [string]$BodyJson, [int]$TimeoutSec = 20) {
    if ($script:HaveCurl) {
        # Write the JSON to a temp file and let curl read it (--data-binary @file).
        # Passing multi-line JSON as a native-exe arg is mangled by PowerShell 5.1
        # (quotes stripped) and the {} trip curl's URL globbing — a file avoids both.
        $tmp = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tmp, $BodyJson, (New-Object System.Text.UTF8Encoding($false)))
            $out = & $script:CurlExe -fsS -k -g --retry 2 --connect-timeout 30 --max-time $TimeoutSec `
                      -X POST -H "Content-Type: application/json" --data-binary "@$tmp" $Url
            if ($LASTEXITCODE -ne 0) { throw "curl POST failed (exit $LASTEXITCODE): $Url" }
            return ($out | ConvertFrom-Json)
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    } else {
        return Invoke-RestMethod -Uri $Url -Method POST -Body $BodyJson -ContentType "application/json" -UseBasicParsing -TimeoutSec $TimeoutSec
    }
}

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Cirque RMM Agent Installer" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# - 0. Obtain per-device token -
if (-not $Token) {
    if ($SiteToken) {
        # Auto-enroll using the site-wide token -> server returns a per-device token
        # Try primary URL first, then fallback
        $enrollUrls = @($TrackerUrl)
        if ($TrackerUrlFallback -and $TrackerUrlFallback -ne $TrackerUrl) {
            $enrollUrls += $TrackerUrlFallback
        }
        $enrolled = $false
        # Real hardware serial — lets the server match this device to its existing
        # procurement asset even when it was renamed before the agent was installed
        # (repurpose flow), instead of creating a duplicate asset.
        $DeviceSerial = ''
        try { $DeviceSerial = (Get-CimInstance -ClassName Win32_BIOS -ErrorAction SilentlyContinue).SerialNumber } catch {}
        $DeviceSerial = ("$DeviceSerial").Trim()
        foreach ($tryUrl in $enrollUrls) {
            Write-Host "[0/7] Auto-enrolling via $tryUrl ..." -ForegroundColor Yellow
            try {
                $body = @{ site_token = $SiteToken; hostname = $env:COMPUTERNAME; agent_id = $AgentId; serial = $DeviceSerial } | ConvertTo-Json
                $resp = Invoke-HttpPost "$tryUrl/api/rmm/enroll" $body 20
                if (-not $resp.ok) { throw "Server returned error: $($resp.error)" }
                $Token      = $resp.token
                $AgentId    = $resp.agent_id
                # Switch to the URL that actually worked
                if ($tryUrl -ne $TrackerUrl) {
                    Write-Host "    (LAN unreachable — using fallback URL)" -ForegroundColor Yellow
                    $TrackerUrl = $tryUrl
                    $GatewayUrl = $GatewayUrlFallback
                }
                Write-Host "    Enrolled as agent '$AgentId' (asset ID $($resp.asset_id))" -ForegroundColor Green
                $enrolled = $true
                break
            } catch {
                Write-Host "    Failed ($tryUrl): $_" -ForegroundColor DarkYellow
            }
        }
        if (-not $enrolled) {
            $errMsg = "ENROLLMENT FAILED: Could not reach tracker at any URL ($($enrollUrls -join ', '))"
            Write-Host $errMsg -ForegroundColor Red
            "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $errMsg" | Out-File -FilePath $LogFile -Append -Encoding UTF8
            Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
            exit 1
        }
    } elseif ($SkipDownload) {
        # Running via MSI installer without any token -- files are installed but
        # service cannot be configured. Log a note and exit cleanly.
        $logDir = "$InstallDir\logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $msg = "$(Get-Date -f 'yyyy-MM-dd HH:mm:ss') Files installed by MSI but no ENROLLMENT_TOKEN provided. " +
               "To complete setup run: install_agent.ps1 -Token YOUR_TOKEN -SkipDownload"
        $msg | Out-File -FilePath "$logDir\setup.log" -Append -Encoding UTF8
        Write-Warning "No enrollment token -- service not configured. See $logDir\setup.log"
        exit 0
    } else {
        Write-Host "ERROR: No enrollment token provided. Run the one-liner from Tracker > Settings > RMM Agent." -ForegroundColor Red
        Write-Host "  irm 'https://YOUR-TRACKER/download/site-install.ps1?t=YOUR-TOKEN' | iex" -ForegroundColor Yellow
        exit 1
    }
}

# - 1. Provision Python (PRIVATE embedded bundle by default; system Python on -UseSystemPython) -
Write-Host "[1/7] Provisioning Python..." -ForegroundColor Yellow

$PythonExe   = $null
$PrivatePython = $false   # set true once the embedded bundle is in place

if (-not $UseSystemPython) {
    # --- Embedded-Python bundle: a self-contained CPython under $InstallDir\python\ ---
    # No system Python, no python.org dependency, version-pinned. The bundle (built by
    # build_python_bundle.ps1 / CI) carries the agent's deps + tkinter, so nothing is
    # pip-installed at runtime and the tray dialogs work. Fetched from the Tracker mirror
    # (LAN/primary first, public Cloudflare fallback) — China-reachable, never python.org.
    $PyDir       = "$InstallDir\python"
    $PyExePath   = "$PyDir\python.exe"
    $PyBundleDep = "deps/cirque-python-embed-3.12.4-win_amd64.zip"
    $PyBundleZip = "$env:TEMP\cirque-python-embed.zip"

    # Idempotent: if a prior install already deployed the private Python and it runs, reuse it.
    $haveGoodPrivate = $false
    if (Test-Path $PyExePath) {
        try {
            $pv = & $PyExePath --version 2>&1
            if ($pv -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) { $haveGoodPrivate = $true }
        } catch {}
    }

    if (-not $haveGoodPrivate) {
        $bundleMirrors = @("$TrackerUrl/download/$PyBundleDep`?t=$SiteToken")
        if ($TrackerUrlFallback -and $TrackerUrlFallback -ne $TrackerUrl) {
            $bundleMirrors += "$TrackerUrlFallback/download/$PyBundleDep`?t=$SiteToken"
        }
        $bundleOk = $false
        foreach ($m in $bundleMirrors) {
            $shown = $m -replace '\?t=.*$', '?t=***'
            Write-Host "    Downloading embedded-Python bundle from $shown ..."
            try { Get-HttpFile $m $PyBundleZip 600; $bundleOk = $true; break }
            catch { Write-Host "      failed ($shown): $_" -ForegroundColor DarkYellow }
        }
        if (-not $bundleOk) {
            Write-Warning "    Embedded-Python bundle unavailable on the Tracker mirror -- falling back to system Python."
            Write-Warning "    (To make this the default, build the bundle: rmm_agent/build_python_bundle.ps1, then place it in the Tracker deps mirror.)"
            $UseSystemPython = $true
        } else {
            # Fresh-extract: stop the service so python\ files aren't in use, then wipe + unzip.
            try { sc.exe stop CirqueRMM 2>$null | Out-Null } catch {}
            Start-Sleep -Seconds 2
            if (Test-Path $PyDir) { Remove-Item $PyDir -Recurse -Force -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
            Expand-Archive -Path $PyBundleZip -DestinationPath $PyDir -Force
            Remove-Item $PyBundleZip -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path $PyExePath)) {
                Write-Warning "    Bundle extracted but $PyExePath missing -- falling back to system Python."
                $UseSystemPython = $true
            }
        }
    }

    if (-not $UseSystemPython) {
        # Verify the private interpreter runs AND imports a core agent dep before committing.
        try {
            $pv = & $PyExePath --version 2>&1
            & $PyExePath -c "import psutil, websockets, ssl; print('deps ok')" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "private interpreter failed to import agent deps" }
            $PythonExe = $PyExePath
            $PrivatePython = $true
            Write-Host "    Using PRIVATE embedded Python: $PythonExe ($pv)" -ForegroundColor Green
        } catch {
            Write-Warning "    Private Python self-test failed ($_) -- falling back to system Python."
            $UseSystemPython = $true
        }
    }
}

if ($UseSystemPython -and -not $PythonExe) {
    Write-Host "    (Legacy mode) Locating or installing a SYSTEM Python..." -ForegroundColor Yellow
}

# --- Legacy system-Python discovery/install (revert path / escape hatch) ---
if (-not $PythonExe) {
# Also search common install paths directly (avoids Windows Store stub issues)
$extraPaths = @(
    "C:\Python312\python.exe", "C:\Python311\python.exe", "C:\Python310\python.exe",
    "C:\Program Files\Python312\python.exe", "C:\Program Files\Python311\python.exe",
    "C:\Program Files\Python310\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
)
$candidateSources = @()
foreach ($cmd in @("python", "python3", "py")) {
    $r = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($r) { $candidateSources += $r.Source }
}
$candidateSources += $extraPaths

foreach ($src in ($candidateSources | Select-Object -Unique)) {
    if (-not (Test-Path $src -PathType Leaf)) { continue }
    # Skip MSYS2/Cygwin/Git-bash Pythons -- their MinGW compiler breaks psutil/Pillow wheel builds
    if ($src -match '(?i)msys|cygwin|git.bash|mingw|usr.bin') {
        Write-Host "    Skipping non-CPython: $src" -ForegroundColor DarkGray
        continue
    }
    try {
        $ver = & $src --version 2>&1
        if ($ver -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) {
            $PythonExe = $src
            Write-Host "    Found: $PythonExe ($ver)" -ForegroundColor Green
            break
        }
    } catch {
        Write-Host "    Skipping $src (Access Denied or store stub)" -ForegroundColor DarkGray
    }
}

if (-not $PythonExe) {
    Write-Host "    Python 3.10+ not found. Downloading Python 3.12..." -ForegroundColor Yellow
    $PythonInstaller = "$env:TEMP\python_installer.exe"
    # China-mirror: pull Python from the Tracker FIRST (China-reachable via the
    # public tracker.cirquetools.com Cloudflare tunnel); python.org is throttled
    # from China (KEVIN-LENOVO timed out at 8/26 MB). Try the LAN/primary Tracker,
    # then the public fallback Tracker, then python.org as a last resort.
    $PythonDep = "deps/python-3.12.4-amd64.exe"
    $PythonUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
    $pyMirrors = @("$TrackerUrl/download/$PythonDep`?t=$SiteToken")
    if ($TrackerUrlFallback -and $TrackerUrlFallback -ne $TrackerUrl) {
        $pyMirrors += "$TrackerUrlFallback/download/$PythonDep`?t=$SiteToken"
    }
    $pyMirrors += $PythonUrl
    $pyOk = $false
    foreach ($m in $pyMirrors) {
        $shown = $m -replace '\?t=.*$', '?t=***'
        Write-Host "    Downloading from $shown ..."
        try { Get-HttpFile $m $PythonInstaller 600; $pyOk = $true; break }
        catch { Write-Host "      failed ($shown): $_" -ForegroundColor DarkYellow }
    }
    if (-not $pyOk) { Write-Error "Could not download Python from Tracker mirror or python.org."; exit 1 }

    # Kill any leftover installer processes from a previous failed attempt.
    # The Python stub spawns msiexec.exe as a child; if that child is still running it
    # holds the Windows Installer mutex and will silently block any new install forever.
    Write-Host "    Clearing installer mutex from any previous attempt..." -ForegroundColor DarkGray
    Get-Process -Name "python_installer" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process msiexec -ErrorAction SilentlyContinue | Where-Object { -not $_.Responding } | Stop-Process -Force -ErrorAction SilentlyContinue
    try { Restart-Service -Name msiserver -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 } catch { }

    # Snapshot existing msiexec PIDs so we can identify our child process later.
    $msiexecBefore = @( (Get-Process msiexec -ErrorAction SilentlyContinue).Id )

    Write-Host "    Installing Python (silent, timeout 10 min)..."
    $pyProc = Start-Process -FilePath $PythonInstaller `
        -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" `
        -PassThru -NoNewWindow
    # WaitForExit only covers the stub; the real work is done by a msiexec child.
    # Poll until BOTH the stub and any spawned msiexec are gone (up to 10 minutes).
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $pyProc.Refresh()
        $ourMsiAlive = Get-Process msiexec -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -notin $msiexecBefore }
        if ($pyProc.HasExited -and -not $ourMsiAlive) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $pyProc.HasExited) {
        # Timed out -- kill stub and any msiexec children we spawned
        $pyProc | Stop-Process -Force -ErrorAction SilentlyContinue
        Get-Process msiexec -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -notin $msiexecBefore } |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Write-Error "Python installer timed out after 10 minutes."; exit 1
    }
    Remove-Item $PythonInstaller -Force -ErrorAction SilentlyContinue

    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")

    foreach ($cmd in @("python", "py")) {
        $r = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($r) { $PythonExe = $r.Source; break }
    }
    if (-not $PythonExe) { Write-Error "Python installation failed. Install manually from python.org"; exit 1 }
    Write-Host "    Python installed: $PythonExe" -ForegroundColor Green
}
}  # end legacy system-Python block

if (-not $PythonExe) { Write-Error "No usable Python (neither embedded bundle nor system Python)."; exit 1 }

# - 2. Create install directory -
Write-Host "[2/7] Creating install directory: $InstallDir" -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null

# - 3. Download agent files -
if ($SkipDownload) {
    Write-Host "[3/7] Skipping download -- files already installed." -ForegroundColor DarkGray
} else {
    Write-Host "[3/7] Downloading agent files from $TrackerUrl ..." -ForegroundColor Yellow

    $AgentFiles = @(
        "agent_client.py",
        "agent_launcher.py",
        "tray.py",
        "requirements.txt",
        "version.txt"
    )

    foreach ($file in $AgentFiles) {
        $url  = "$TrackerUrl/download/agent-file/${file}?t=$SiteToken"
        $dest = "$InstallDir\$file"
        Write-Host "    GET $file ..."
        try {
            Get-HttpFile $url $dest 60
        } catch {
            Write-Warning "    Failed to download $file`: $_"
        }
    }

    # Download icon files (base64 encoded)
    foreach ($iconFile in @("cirque_icon_ico.b64", "cirque_icon_png.b64")) {
        $url  = "$TrackerUrl/download/agent-file/${iconFile}?t=$SiteToken"
        $dest = "$InstallDir\$iconFile"
        try {
            Get-HttpFile $url $dest 60
        } catch { }
    }
}

# - 4. Write config file -
Write-Host "[4/7] Writing configuration..." -ForegroundColor Yellow
$ConfigContent = @"
[agent]
agent_id = $AgentId
tracker_url = $TrackerUrl
gateway_url = $GatewayUrl
token = $Token
"@
$ConfigContent | Set-Content -Path "$InstallDir\agent.conf" -Encoding UTF8
Write-Host "    Config written to $InstallDir\agent.conf" -ForegroundColor Green

# - 5. Install Python dependencies -
Write-Host "[5/7] Installing Python dependencies..." -ForegroundColor Yellow
# Stop any running instance first so pip can overwrite in-use files
try { sc.exe stop CirqueRMM 2>$null | Out-Null } catch {}
Start-Sleep -Seconds 2

if ($PrivatePython) {
    # The embedded bundle already carries every agent dependency (+ pystray/pillow +
    # tkinter) in its own site-packages — verified at build time. Nothing to pip-install;
    # skip the wheelhouse round-trip entirely. $wheelReady stays false so the tray step
    # below uses the bundled packages (already present) rather than re-installing.
    $wheelReady = $false
    Write-Host "    Private embedded Python carries all deps -- skipping pip install." -ForegroundColor Green
} else {

# China-mirror: install the agent's pip deps from a Tracker-hosted wheelhouse FIRST
# (offline, --no-index), so PyPI (throttled from China) is only a fallback. The
# wheelhouse is a flat zip of win_amd64+cp312 wheels (websockets/psutil/mss/Pillow/
# pystray/pywinpty + transitive) served token-gated from the Tracker. Download it
# from the Tracker (primary then public fallback), unzip, and pip --find-links it.
$WheelDep   = "deps/wheelhouse-cp312-win_amd64.zip"
$WheelZip   = "$env:TEMP\cirque_wheelhouse.zip"
$WheelDir   = "$env:TEMP\cirque_wheelhouse"
$wheelReady = $false
$wheelMirrors = @("$TrackerUrl/download/$WheelDep`?t=$SiteToken")
if ($TrackerUrlFallback -and $TrackerUrlFallback -ne $TrackerUrl) {
    $wheelMirrors += "$TrackerUrlFallback/download/$WheelDep`?t=$SiteToken"
}
foreach ($m in $wheelMirrors) {
    $shown = $m -replace '\?t=.*$', '?t=***'
    try {
        Write-Host "    Fetching offline wheelhouse from $shown ..."
        Get-HttpFile $m $WheelZip 300
        if (Test-Path $WheelDir) { Remove-Item $WheelDir -Recurse -Force -ErrorAction SilentlyContinue }
        Expand-Archive -Path $WheelZip -DestinationPath $WheelDir -Force
        $wheelReady = $true
        break
    } catch { Write-Host "      wheelhouse fetch failed ($shown): $_" -ForegroundColor DarkYellow }
}

# pip upgrade is best-effort (non-fatal) and only matters online; skip the PyPI
# round-trip entirely when we have the offline wheelhouse.
if (-not $wheelReady) {
    & $PythonExe -m pip install -q --upgrade pip --timeout 120
}

$depsOk = $false
if ($wheelReady) {
    Write-Host "    Installing dependencies from Tracker wheelhouse (offline)..." -ForegroundColor Green
    & $PythonExe -m pip install -q --no-index --find-links "$WheelDir" -r "$InstallDir\requirements.txt"
    if ($LASTEXITCODE -eq 0) { $depsOk = $true }
    else { Write-Warning "    Offline wheelhouse install failed -- falling back to PyPI." }
}
if (-not $depsOk) {
    & $PythonExe -m pip install -q -r "$InstallDir\requirements.txt" --timeout 120
    if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed."; exit 1 }
}
Remove-Item $WheelZip -Force -ErrorAction SilentlyContinue
Write-Host "    Dependencies installed." -ForegroundColor Green
}  # end system-Python dependency install

# - 6. Install NSSM & register service -
Write-Host "[6/7] Configuring Windows service..." -ForegroundColor Yellow

# NSSM must run from an SRP/AppLocker-ALLOWED path. Hardened boxes (domain
# controllers, STIG/CIS baselines) only permit .exe execution from C:\Windows and
# C:\Program Files -- running nssm.exe from the user-writable install dir
# (C:\CirqueRMM) triggers "This program is blocked by group policy". So always
# place + run NSSM from $NssmPath (default C:\Program Files\NSSM\nssm.exe).
$NssmDir = Split-Path $NssmPath
New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
if (-not (Test-Path $NssmPath)) {
    $srcNssm = "$InstallDir\nssm.exe"
    if (Test-Path $srcNssm) {
        Write-Host "    Relocating nssm.exe to allowed path: $NssmPath" -ForegroundColor Green
        Copy-Item $srcNssm $NssmPath -Force
    } else {
        try {
            Write-Host "    Downloading nssm.exe from $TrackerUrl -> $NssmPath ..."
            Get-HttpFile "$TrackerUrl/download/agent-file/nssm.exe?t=$SiteToken" $NssmPath 120
        } catch {
            Write-Warning "    Tracker nssm.exe download failed ($_); trying NSSM zip ..."
            # China-mirror: pull the NSSM zip from the Tracker FIRST (Tracker primary,
            # then public fallback), and only hit nssm.cc (frequently 503) as a last
            # resort. Whichever source returns the zip, extract the arch-correct exe.
            $NssmZip     = "$env:TEMP\nssm.zip"
            $NssmExtract = "$env:TEMP\nssm_extract"
            $NssmDep     = "deps/nssm-2.24-101-g897c7ad.zip"
            $nssmMirrors = @("$TrackerUrl/download/$NssmDep`?t=$SiteToken")
            if ($TrackerUrlFallback -and $TrackerUrlFallback -ne $TrackerUrl) {
                $nssmMirrors += "$TrackerUrlFallback/download/$NssmDep`?t=$SiteToken"
            }
            $nssmMirrors += "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip"
            $nssmZipOk = $false
            foreach ($m in $nssmMirrors) {
                $shown = $m -replace '\?t=.*$', '?t=***'
                try { Write-Host "    Downloading NSSM zip from $shown ..."; Get-HttpFile $m $NssmZip 120; $nssmZipOk = $true; break }
                catch { Write-Host "      failed ($shown): $_" -ForegroundColor DarkYellow }
            }
            if (-not $nssmZipOk) { Write-Error "Could not download NSSM from Tracker mirror or nssm.cc."; exit 1 }
            if (Test-Path $NssmExtract) { Remove-Item $NssmExtract -Recurse -Force -ErrorAction SilentlyContinue }
            Expand-Archive -Path $NssmZip -DestinationPath $NssmExtract -Force
            if ([Environment]::Is64BitOperatingSystem) { $arch = "win64" } else { $arch = "win32" }
            $extracted = Get-ChildItem $NssmExtract -Recurse -Filter "nssm.exe" | Where-Object { $_.FullName -match $arch } | Select-Object -First 1
            if (-not $extracted) { Write-Error "Could not find nssm.exe in downloaded archive."; exit 1 }
            Copy-Item $extracted.FullName $NssmPath -Force
            Remove-Item $NssmZip, $NssmExtract -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "    NSSM ready: $NssmPath" -ForegroundColor Green
} else {
    Write-Host "    Using existing NSSM: $NssmPath" -ForegroundColor Green
}
# Remove any nssm.exe a prior run dropped in the blocked install dir.
if (Test-Path "$InstallDir\nssm.exe") { Remove-Item "$InstallDir\nssm.exe" -Force -ErrorAction SilentlyContinue }

# Remove old service if present (ignore errors -- service may not exist on first install)
try { & $NssmPath stop $ServiceName 2>$null | Out-Null } catch {}
try { & $NssmPath remove $ServiceName confirm 2>$null | Out-Null } catch {}
Start-Sleep -Seconds 1

# Register new service
& $NssmPath install $ServiceName $PythonExe | Out-Null
& $NssmPath set $ServiceName AppParameters    "agent_launcher.py"
& $NssmPath set $ServiceName AppDirectory     $InstallDir
& $NssmPath set $ServiceName DisplayName      "Cirque RMM Agent"
& $NssmPath set $ServiceName Description      "Connects to the Cirque IT tracker for remote management and monitoring."
& $NssmPath set $ServiceName Start            SERVICE_AUTO_START
& $NssmPath set $ServiceName AppRestartDelay  5000
& $NssmPath set $ServiceName AppEnvironmentExtra `
    "RMM_GATEWAY_URL=$GatewayUrl" `
    "RMM_TRACKER_URL=$TrackerUrl" `
    "RMM_GATEWAY_URL_PUBLIC=wss://rmm.cirquetools.com" `
    "RMM_TRACKER_URL_PUBLIC=https://tracker.cirquetools.com" `
    "RMM_AGENT_ID=$AgentId" `
    "RMM_AGENT_TOKEN=$Token" `
    "TRACKER_URL=$TrackerUrl"
& $NssmPath set $ServiceName AppStdout "$InstallDir\logs\agent.log"
& $NssmPath set $ServiceName AppStderr "$InstallDir\logs\agent.log"
& $NssmPath set $ServiceName AppRotateFiles  1
& $NssmPath set $ServiceName AppRotateBytes  5242880 | Out-Null

Write-Host "    Starting service..."
& $NssmPath start $ServiceName | Out-Null
Start-Sleep -Seconds 3
$svcStatus = & $NssmPath status $ServiceName 2>&1
if ($svcStatus -eq "SERVICE_RUNNING") {
    Write-Host "    Service running." -ForegroundColor Green
} else {
    Write-Warning "    Service status: $svcStatus -- check $InstallDir\logs\agent.log"
}

# - 7. Set up tray (startup VBS + immediate launch via scheduled task) -
Write-Host "[7/7] Setting up tray application..." -ForegroundColor Yellow

# Prefer pythonw.exe (no console window)
$PythonwExe = Join-Path (Split-Path $PythonExe -Parent) "pythonw.exe"
if (-not (Test-Path $PythonwExe)) { $PythonwExe = $PythonExe }

# Install pystray + pillow (best-effort). The private embedded bundle already ships
# both (+ tkinter) so we install NOTHING in that mode. Otherwise use the offline
# wheelhouse if we fetched it above (China-reachable), else PyPI.
if ($PrivatePython) {
    Write-Host "    Tray deps (pystray/pillow/tkinter) bundled in private Python." -ForegroundColor Green
} else {
    try {
        if ($wheelReady -and (Test-Path $WheelDir)) {
            & $PythonExe -m pip install --quiet --no-index --find-links "$WheelDir" pystray pillow | Out-Null
        } else {
            & $PythonExe -m pip install --quiet pystray pillow | Out-Null
        }
    } catch {}
}

# Write a VBS launcher to All-Users Startup so tray runs on every future login
$StartupFolder = [System.Environment]::GetFolderPath('CommonStartup')
$VbsPath = "$StartupFolder\CirqueTray.vbs"
$VbsContent = "Set oShell = CreateObject(`"WScript.Shell`")`r`noShell.Run Chr(34) & `"$PythonwExe`" & Chr(34) & `" $InstallDir\tray.py`", 0, False`r`n"
[System.IO.File]::WriteAllText($VbsPath, $VbsContent, [System.Text.Encoding]::UTF8)
Write-Host "    Startup VBS written: $VbsPath" -ForegroundColor Green

# Launch tray immediately for the current logged-in user via scheduled task
# (scheduled task bypasses session-0 isolation; runs in user's interactive desktop)
$wmiUser = (Get-WmiObject Win32_ComputerSystem -ErrorAction SilentlyContinue).UserName
if (-not $wmiUser) {
    $qw = & qwinsta 2>$null
    $activeMatch = $qw | Select-String '\s+Active\s+' | Select-Object -First 1
    if ($activeMatch) {
        $line = $activeMatch.ToString()
        $parts = (($line -replace '>','').Trim() -split '\s+')
        if ($parts.Count -ge 2) {
            $wmiUser = $parts[1]
        }
    }
}
$targetUser = ($wmiUser -replace '.*\\', '')
if ($targetUser) {
    Write-Host "    Launching tray for user: $targetUser" -ForegroundColor Cyan
    $ta = New-ScheduledTaskAction -Execute $PythonwExe -Argument "$InstallDir\tray.py" -WorkingDirectory $InstallDir
    $tp = New-ScheduledTaskPrincipal -UserId $targetUser -LogonType Interactive -RunLevel Limited
    $regErr = $null
    Register-ScheduledTask -TaskName 'CirqueTrayLaunch' -Action $ta -Principal $tp -Force -ErrorVariable regErr | Out-Null
    if ($regErr) {
        Write-Warning "    Could not register tray task: $regErr"
    } else {
        Start-ScheduledTask -TaskName 'CirqueTrayLaunch' -ErrorAction SilentlyContinue
        Write-Host "    Tray launched — should appear in taskbar within seconds." -ForegroundColor Green
    }
} else {
    Write-Warning "    No interactive user detected — tray will start on next login via Startup folder."
}

# - Watchdog scheduled task: restarts CirqueRMM if it stops/crashes, every 15 min -
Write-Host "[+] Installing self-healing watchdog task..." -ForegroundColor Yellow
try {
    $watchdogScript = @"
`$svc = Get-Service -Name '$ServiceName' -ErrorAction SilentlyContinue
if (-not `$svc) { exit 0 }
if (`$svc.Status -ne 'Running') {
    # Kill any frozen python process first (handles NSSM Paused state)
    Get-Process python, python3 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    # Reset NSSM throttle counter so it won't stay Paused
    `$nssmExe = if (Test-Path '$InstallDir\nssm.exe') { '$InstallDir\nssm.exe' } else { 'C:\Program Files\NSSM\nssm.exe' }
    if (Test-Path `$nssmExe) { & `$nssmExe reset '$ServiceName' AppThrottle 2>`$null }
    sc.exe start '$ServiceName' | Out-Null
    Start-Sleep 5
    `$svc.Refresh()
    `$status = (Get-Service '$ServiceName' -ErrorAction SilentlyContinue).Status
    "Watchdog: restarted $ServiceName at `$(Get-Date -Format 's') -> `$status" | Out-File -Append -Encoding UTF8 '$InstallDir\logs\watchdog.log'
}
"@
    $watchdogPath = "$InstallDir\watchdog.ps1"
    $watchdogScript | Out-File -FilePath $watchdogPath -Encoding UTF8 -Force

    $wdAction = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdogPath`""
    $wdTrigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At (Get-Date).Date
    $wdPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $wdSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName 'CirqueRMM-Watchdog' `
        -Action $wdAction -Trigger $wdTrigger -Principal $wdPrincipal -Settings $wdSettings `
        -Description 'Restarts CirqueRMM agent service if stopped. Cirque IT self-healing watchdog.' `
        -Force | Out-Null
    Write-Host "    Watchdog task registered (runs as SYSTEM every 15 min)" -ForegroundColor Green
} catch {
    Write-Warning "    Watchdog task setup failed: $_  (non-fatal)"
}

Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Installation Complete!" -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Agent ID   : $AgentId"
Write-Host "  Tracker    : $TrackerUrl"
Write-Host "  Install dir: $InstallDir"
Write-Host "  Service    : $ServiceName"
Write-Host ""
Write-Host "  To check status: Get-Service $ServiceName"
Write-Host "  Log file: $InstallDir\logs\agent.log"
Write-Host ""
