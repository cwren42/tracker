<#
================================================================================
  warp_validate.ps1  --  read-only WARP + internal-RustDesk-relay validation
  Enqueue this PER BOX via /remediation/<agent>/enqueue (action_type=run_script,
  shell=powershell). It reports WARP enrollment/connection state and proves the
  box can reach the INTERNAL RustDesk relay (10.15.0.63) over the WARP mesh.

  -----------------------------------------------------------------------------
  WHY THIS FILE EXISTS / DEFENDER NOTES  (READ THIS)
  -----------------------------------------------------------------------------
  Do NOT hand-roll a gzip+Base64 self-extracting one-liner to squeeze this
  through the enqueue form. That blob lands verbatim on the powershell.exe
  command line and is byte-for-byte the Defender/MDE "Suspicious PowerShell
  command line" signature. Agent 2.9.41+ stages every run_script body to a
  C:\CirqueRMM\scripts\_run_<uuid>.ps1 and launches it via -File, so just paste
  this script's contents as the run_script code -- no wrapper, no compression.

  This script is also deliberately RDP-FREE. The previous version probed
  10.15.0.63:3389 (RDP), which tripped the MDE "Remote Desktop session"
  (LateralMovement) heuristic -- and 3389 is irrelevant to RustDesk anyway
  (RustDesk relay = 21115/21116/21117). The RustDesk-relay ports below are the
  correct, sufficient reachability proof.
================================================================================
#>
$ErrorActionPreference = 'SilentlyContinue'

# TCP connect probe (connect-then-close; no RDP, no session APIs).
function TC($ip, $port, $ms = 3000) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect($ip, $port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($ms, $false) -and $c.Connected) {
            $c.EndConnect($iar); 'OPEN'
        } else { 'FAIL/timeout' }
    } catch { "FAIL($($_.Exception.Message.Split([char]10)[0]))" }
    finally { $c.Close() }
}

$warp = 'C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe'
Write-Output ("WARP status: " + ((& $warp --accept-tos status 2>&1 | Out-String).Trim()))
Write-Output ("WARP onboarding setting: " + ((& $warp --accept-tos settings 2>&1 | Out-String) -split "`n" | Where-Object { $_ -match 'Onboard' } | Out-String).Trim())

Write-Output "=== onboarding-fix log ==="
$lg = 'C:\ProgramData\Cloudflare\warp_onboard_fix.log'
if (Test-Path $lg) { Get-Content $lg | Select-Object -Last 20 } else { Write-Output "(no log yet)" }

Write-Output "=== RustDesk ID ==="
$rid = 'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config\RustDesk.toml'
if (Test-Path $rid) { (Get-Content $rid | Select-String -Pattern '^id ').Line }
else {
    $alt = "$env:APPDATA\RustDesk\config\RustDesk.toml"
    if (Test-Path $alt) { (Get-Content $alt | Select-String '^id ').Line } else { "(toml not found)" }
}

Write-Output "=== RustDesk connection (server log tail) ==="
$rl = 'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\log'
if (Test-Path $rl) {
    Get-ChildItem $rl -Recurse -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content | Select-Object -Last 6
}

Write-Output "=== connectivity tests ==="
# LAN reachability (proves WARP mesh routing to the internal subnet).
Write-Output ("LAN 10.15.0.1:445    -> " + (TC '10.15.0.1' 445))
Write-Output ("LAN 10.15.0.1:80     -> " + (TC '10.15.0.1' 80))
Write-Output ("LAN 10.15.0.53:443   -> " + (TC '10.15.0.53' 443))
# RustDesk relay reachability -- the ports that actually matter for relay.
# (No 3389/RDP probe: it is irrelevant to RustDesk and trips MDE LateralMovement.)
Write-Output ("RELAY 10.15.0.63:443   -> " + (TC '10.15.0.63' 443))
Write-Output ("RELAY 10.15.0.63:21115 -> " + (TC '10.15.0.63' 21115))
Write-Output ("RELAY 10.15.0.63:21116 -> " + (TC '10.15.0.63' 21116))
Write-Output ("RELAY 10.15.0.63:21117 -> " + (TC '10.15.0.63' 21117))
