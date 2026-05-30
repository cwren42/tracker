#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Downloads and silently installs the Cirque RMM Agent from Cloudflare.
    Safe to run on-LAN or off-LAN. Re-running upgrades an existing install.
.EXAMPLE
    .\deploy_agent.ps1
#>

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$installer = "$env:TEMP\CirqueRMM.exe"

Write-Host "Downloading CirqueRMM agent..."
(New-Object Net.WebClient).DownloadFile(
    "https://tracker.cirquetools.com/get/agent-exe",
    $installer
)

Write-Host "Running installer..."
$p = Start-Process -FilePath $installer -ArgumentList "/silent" -Wait -PassThru

if ($p.ExitCode -eq 0) {
    Write-Host "Install complete." -ForegroundColor Green
} else {
    Write-Host "Installer exited with code $($p.ExitCode) — check $env:TEMP\CirqueRMM_install.log" -ForegroundColor Yellow
}
