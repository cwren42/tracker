#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the Cirque RMM agent as a Windows service via NSSM.
.PARAMETER Token
    Agent enrollment token from the tracker server.
.PARAMETER GatewayUrl
    RMM gateway WebSocket URL. Default: wss://rmm.corp.cirque.com
.PARAMETER InstallDir
    Where agent files live. Default: C:\Program Files\CirqueRMM
.PARAMETER NssmPath
    Path to nssm.exe. Default: C:\Program Files\NSSM\nssm.exe
.PARAMETER AgentId
    Override the agent ID (defaults to computer hostname).
.EXAMPLE
    .\install_service.ps1 -Token "agent_xxxx..."
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Token,

    [string]$GatewayUrl = "wss://rmm.corp.cirque.com",

    [string]$InstallDir = "C:\Program Files\CirqueRMM",

    [string]$NssmPath = "C:\Program Files\NSSM\nssm.exe",

    [string]$AgentId = ""
)

$ServiceName = "CirqueRMM"
if (-not $AgentId) { $AgentId = $env:COMPUTERNAME }

# Find Python (PS 5.1 compatible)
$_pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($_pyCmd) { $PythonExe = $_pyCmd.Source } else { $PythonExe = $null }
if (-not $PythonExe) {
    $_pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($_pyCmd) { $PythonExe = $_pyCmd.Source } else { $PythonExe = $null }
}
if (-not $PythonExe) {
    Write-Error "Python not found. Install Python 3.10+ and add it to PATH."
    exit 1
}

Write-Host "[1/5] Agent ID: $AgentId"
Write-Host "[2/5] Installing Python dependencies..."
& $PythonExe -m pip install -q -r "$InstallDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed."; exit 1 }

if (-not (Test-Path $NssmPath)) {
    Write-Host "[NSSM] Downloading NSSM..."
    $NssmZip = "$env:TEMP\nssm.zip"
    $NssmExtract = "$env:TEMP\nssm_extract"
    $NssmDir = Split-Path $NssmPath
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    Invoke-WebRequest -Uri "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip" -OutFile $NssmZip -UseBasicParsing
    Expand-Archive -Path $NssmZip -DestinationPath $NssmExtract -Force
    if ([Environment]::Is64BitOperatingSystem) { $arch = "win64" } else { $arch = "win32" }
    $extracted = Get-ChildItem $NssmExtract -Recurse -Filter "nssm.exe" | Where-Object { $_.FullName -match $arch } | Select-Object -First 1
    if (-not $extracted) { Write-Error "Could not find nssm.exe in zip."; exit 1 }
    Copy-Item $extracted.FullName $NssmPath -Force
    Remove-Item $NssmZip -Force
    Remove-Item $NssmExtract -Recurse -Force
    Write-Host "[NSSM] Installed to $NssmPath"
}

Write-Host "[3/5] Removing old service if present..."
& $NssmPath stop $ServiceName 2>$null
& $NssmPath remove $ServiceName confirm 2>$null

Write-Host "[4/5] Installing service..."
& $NssmPath install $ServiceName $PythonExe
& $NssmPath set $ServiceName AppParameters "`"$InstallDir\agent_client.py`""
& $NssmPath set $ServiceName AppDirectory $InstallDir
& $NssmPath set $ServiceName DisplayName "Cirque RMM Agent"
& $NssmPath set $ServiceName Description "Connects to the Cirque RMM gateway for remote management."
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppRestartDelay 3000

$env1 = "RMM_GATEWAY_URL=$GatewayUrl"
$env1b = "RMM_TRACKER_URL=$TrackerUrl"
$env1c = "RMM_GATEWAY_URL_PUBLIC=wss://rmm.cirquetools.com"
$env1d = "RMM_TRACKER_URL_PUBLIC=https://tracker.cirquetools.com"
$env2 = "RMM_AGENT_ID=$AgentId"
$env3 = "RMM_AGENT_TOKEN=$Token"
& $NssmPath set $ServiceName AppEnvironmentExtra $env1 $env1b $env1c $env1d $env2 $env3

$LogDir = "$InstallDir\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
& $NssmPath set $ServiceName AppStdout "$LogDir\agent.log"
& $NssmPath set $ServiceName AppStderr "$LogDir\agent.log"
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateBytes 5242880

Write-Host "[5/5] Starting service..."
& $NssmPath start $ServiceName
Start-Sleep -Seconds 2
$status = & $NssmPath status $ServiceName
Write-Host "Service status: $status"
if ($status -eq "SERVICE_RUNNING") {
    Write-Host "[OK] CirqueRMM agent installed and running as: $AgentId"
} else {
    $logpath = "$LogDir\agent.log"
    Write-Warning "Service may not have started. Check: $logpath"
}
