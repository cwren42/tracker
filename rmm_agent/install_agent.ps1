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

    [string]$TrackerUrl  = "https://tracker.corp.cirque.com",
    [string]$GatewayUrl  = "wss://rmm.corp.cirque.com",
    [string]$InstallDir  = "C:\Program Files\CirqueRMM",
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

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "  Cirque RMM Agent Installer" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# ── 0. Prompt for token if not supplied ─────────────────────────────────────
if (-not $Token) {
    if ($SkipDownload) {
        # Running via MSI installer without a token — files are installed but
        # service cannot be configured. Log a note and exit cleanly.
        $logDir = "$InstallDir\logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $msg = "$(Get-Date -f 'yyyy-MM-dd HH:mm:ss') Files installed by MSI but no ENROLLMENT_TOKEN provided. " +
               "To complete setup run: install_agent.ps1 -Token YOUR_TOKEN -SkipDownload"
        $msg | Out-File -FilePath "$logDir\setup.log" -Append -Encoding UTF8
        Write-Warning "No enrollment token — service not configured. See $logDir\setup.log"
        exit 0
    }
    $Token = Read-Host "Enter your enrollment token (from Tracker > Assets)"
    if (-not $Token) { Write-Error "Token is required."; exit 1 }
}

# ── 1. Find or install Python ────────────────────────────────────────────────
Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow

$PythonExe = $null
foreach ($cmd in @("python", "python3", "py")) {
    $r = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($r) {
        # Verify version >= 3.10
        $ver = & $r.Source --version 2>&1
        if ($ver -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) {
            $PythonExe = $r.Source
            Write-Host "    Found: $PythonExe ($ver)" -ForegroundColor Green
            break
        }
    }
}

if (-not $PythonExe) {
    Write-Host "    Python 3.10+ not found. Downloading Python 3.12..." -ForegroundColor Yellow
    $PythonInstaller = "$env:TEMP\python_installer.exe"
    $PythonUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
    Write-Host "    Downloading from $PythonUrl ..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonInstaller -UseBasicParsing
    Write-Host "    Installing Python (silent)..."
    Start-Process -FilePath $PythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -Wait -NoNewWindow
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

# ── 2. Create install directory ──────────────────────────────────────────────
Write-Host "[2/7] Creating install directory: $InstallDir" -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null

# ── 3. Download agent files ──────────────────────────────────────────────────
if ($SkipDownload) {
    Write-Host "[3/7] Skipping download — files already installed." -ForegroundColor DarkGray
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
        $url  = "$TrackerUrl/download/agent-file/$file"
        $dest = "$InstallDir\$file"
        Write-Host "    GET $file ..."
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        } catch {
            Write-Warning "    Failed to download $file`: $_"
        }
    }

    # Download icon files (base64 encoded)
    foreach ($iconFile in @("cirque_icon_ico.b64", "cirque_icon_png.b64")) {
        $url  = "$TrackerUrl/download/agent-file/$iconFile"
        $dest = "$InstallDir\$iconFile"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        } catch { }
    }
}

# ── 4. Write config file ─────────────────────────────────────────────────────
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

# ── 5. Install Python dependencies ───────────────────────────────────────────
Write-Host "[5/7] Installing Python dependencies..." -ForegroundColor Yellow
& $PythonExe -m pip install -q --upgrade pip
& $PythonExe -m pip install -q -r "$InstallDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed."; exit 1 }
Write-Host "    Dependencies installed." -ForegroundColor Green

# ── 6. Install NSSM & register service ──────────────────────────────────────
Write-Host "[6/7] Configuring Windows service..." -ForegroundColor Yellow

if (-not (Test-Path $NssmPath)) {
    Write-Host "    Downloading NSSM..."
    $NssmZip     = "$env:TEMP\nssm.zip"
    $NssmExtract = "$env:TEMP\nssm_extract"
    $NssmDir     = Split-Path $NssmPath
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    Invoke-WebRequest -Uri "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip" -OutFile $NssmZip -UseBasicParsing
    Expand-Archive -Path $NssmZip -DestinationPath $NssmExtract -Force
    if ([Environment]::Is64BitOperatingSystem) { $arch = "win64" } else { $arch = "win32" }
    $extracted = Get-ChildItem $NssmExtract -Recurse -Filter "nssm.exe" | Where-Object { $_.FullName -match $arch } | Select-Object -First 1
    if (-not $extracted) { Write-Error "Could not find nssm.exe in downloaded archive."; exit 1 }
    Copy-Item $extracted.FullName $NssmPath -Force
    Remove-Item $NssmZip, $NssmExtract -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    NSSM installed." -ForegroundColor Green
}

# Remove old service if present
& $NssmPath stop $ServiceName 2>$null | Out-Null
& $NssmPath remove $ServiceName confirm 2>$null | Out-Null
Start-Sleep -Seconds 1

# Register new service
& $NssmPath install $ServiceName $PythonExe | Out-Null
& $NssmPath set $ServiceName AppParameters    "`"$InstallDir\agent_launcher.py`""
& $NssmPath set $ServiceName AppDirectory     $InstallDir
& $NssmPath set $ServiceName DisplayName      "Cirque RMM Agent"
& $NssmPath set $ServiceName Description      "Connects to the Cirque IT tracker for remote management and monitoring."
& $NssmPath set $ServiceName Start            SERVICE_AUTO_START
& $NssmPath set $ServiceName AppRestartDelay  5000
& $NssmPath set $ServiceName AppEnvironmentExtra `
    "RMM_GATEWAY_URL=$GatewayUrl" `
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
    Write-Warning "    Service status: $svcStatus — check $InstallDir\logs\agent.log"
}

# ── 7. Create tray startup shortcut ─────────────────────────────────────────
Write-Host "[7/7] Creating tray startup shortcut..." -ForegroundColor Yellow
$StartupFolder = [System.Environment]::GetFolderPath("CommonStartup")
$ShortcutPath  = "$StartupFolder\$TrayName.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath       = $PythonExe
$Shortcut.Arguments        = "`"$InstallDir\tray.py`""
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.WindowStyle      = 7  # minimized
$Shortcut.Description      = "Cirque RMM Tray"
$Shortcut.Save()
Write-Host "    Tray shortcut created: $ShortcutPath" -ForegroundColor Green

# ── Launch tray immediately for current user ─────────────────────────────────
try {
    Start-Process $PythonExe -ArgumentList "`"$InstallDir\tray.py`"" -WorkingDirectory $InstallDir -WindowStyle Hidden
    Write-Host "    Tray application launched." -ForegroundColor Green
} catch {
    Write-Warning "    Could not launch tray: $_"
}

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
