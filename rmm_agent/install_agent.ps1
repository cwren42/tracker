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
    [string]$InstallDir  = "C:\CirqueRMM",
    [string]$NssmPath    = "C:\Program Files\NSSM\nssm.exe",
    [string]$AgentId     = "",

    # When set (e.g. invoked by the MSI installer), skips downloading agent files
    # because the MSI already installed them.
    [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"
$ServiceName = "CirqueRMM"
$TrayName    = "CirqueRMM Tray"
if (-not $AgentId) { $AgentId = $env:COMPUTERNAME }

# - Fallback crash log in TEMP: written before anything else so we always get SOMETHING -
$FallbackLog = "$env:TEMP\CirqueRMM_install.log"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') === Install starting on $env:COMPUTERNAME (PID $PID) SiteToken=$(if($SiteToken){'set'}else{'MISSING'}) ===" | Out-File $FallbackLog -Append -Encoding UTF8
trap {
    $errLine = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') FATAL: $_`n$($_.ScriptStackTrace)"
    $errLine | Out-File $FallbackLog -Append -Encoding UTF8
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

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Cirque RMM Agent Installer" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# - 0. Obtain per-device token -
if (-not $Token) {
    if ($SiteToken) {
        # Auto-enroll using the site-wide token -> server returns a per-device token
        Write-Host "[0/7] Auto-enrolling device with site token..." -ForegroundColor Yellow
        try {
            $body = @{ site_token = $SiteToken; hostname = $env:COMPUTERNAME; agent_id = $AgentId } | ConvertTo-Json
            $resp = Invoke-RestMethod -Uri "$TrackerUrl/api/rmm/enroll" `
                       -Method POST -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 30
            if (-not $resp.ok) { throw "Server returned error: $($resp.error)" }
            $Token   = $resp.token
            $AgentId = $resp.agent_id
            Write-Host "    Enrolled as agent '$AgentId' (asset ID $($resp.asset_id))" -ForegroundColor Green
        } catch {
            $errMsg = "ENROLLMENT FAILED: $_"
            Write-Host $errMsg -ForegroundColor Red
            Write-Host "Tracker URL attempted: $TrackerUrl/api/rmm/enroll" -ForegroundColor Yellow
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

# - 1. Find or install Python -
Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow

$PythonExe = $null
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
    $PythonUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
    Write-Host "    Downloading from $PythonUrl ..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonInstaller -UseBasicParsing -TimeoutSec 300
    Write-Host "    Installing Python (silent, timeout 5 min)..."
    $pyProc = Start-Process -FilePath $PythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -PassThru -NoNewWindow
    if (-not $pyProc.WaitForExit(300000)) {
        $pyProc.Kill()
        Write-Error "Python installer timed out after 5 minutes."; exit 1
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
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 60
        } catch {
            Write-Warning "    Failed to download $file`: $_"
        }
    }

    # Download icon files (base64 encoded)
    foreach ($iconFile in @("cirque_icon_ico.b64", "cirque_icon_png.b64")) {
        $url  = "$TrackerUrl/download/agent-file/${iconFile}?t=$SiteToken"
        $dest = "$InstallDir\$iconFile"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 60
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
& $PythonExe -m pip install -q --upgrade pip --timeout 120
& $PythonExe -m pip install -q -r "$InstallDir\requirements.txt" --timeout 120
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed."; exit 1 }
Write-Host "    Dependencies installed." -ForegroundColor Green

# - 6. Install NSSM & register service -
Write-Host "[6/7] Configuring Windows service..." -ForegroundColor Yellow

# Use bundled nssm.exe from InstallDir if present, else fall back to NssmPath or download
$bundledNssm = "$InstallDir\nssm.exe"
if (Test-Path $bundledNssm) {
    $NssmPath = $bundledNssm
    Write-Host "    Using bundled NSSM: $NssmPath" -ForegroundColor Green
}

if (-not (Test-Path $NssmPath)) {
    Write-Host "    nssm.exe not bundled and not at $NssmPath -- attempting download..."
    $NssmZip     = "$env:TEMP\nssm.zip"
    $NssmExtract = "$env:TEMP\nssm_extract"
    $NssmDir     = Split-Path $NssmPath
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    Invoke-WebRequest -Uri "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip" -OutFile $NssmZip -UseBasicParsing -TimeoutSec 120
    Expand-Archive -Path $NssmZip -DestinationPath $NssmExtract -Force
    if ([Environment]::Is64BitOperatingSystem) { $arch = "win64" } else { $arch = "win32" }
    $extracted = Get-ChildItem $NssmExtract -Recurse -Filter "nssm.exe" | Where-Object { $_.FullName -match $arch } | Select-Object -First 1
    if (-not $extracted) { Write-Error "Could not find nssm.exe in downloaded archive."; exit 1 }
    Copy-Item $extracted.FullName $NssmPath -Force
    Remove-Item $NssmZip, $NssmExtract -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    NSSM installed." -ForegroundColor Green
}

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

# Install pystray + pillow
try { & $PythonExe -m pip install --quiet pystray pillow | Out-Null } catch {}

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
    $line = ($qw | Select-String 'Active' | Select-Object -First 1).ToString()
    $wmiUser = (($line -replace '>','').Trim() -split '\s+')[1]
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
