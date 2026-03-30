# Cirque RMM Agent Repair Script
# Run this as ADMINISTRATOR on the Windows endpoint if the agent stops working.
# It restores the previous working agent from backup or downloads a fresh copy,
# then restarts the NSSM service.
#
# Usage:  powershell -ExecutionPolicy Bypass -File agent_repair.ps1
#         (copy the file locally first if needed)

$AgentDir  = "C:\CirqueRMM"
$AgentPy   = "$AgentDir\agent_client.py"
$AgentOld  = "$AgentPy.old"
$ServiceName = "CirqueRMM"

# Find NSSM — bundled copy takes priority
$NssmExe = if (Test-Path "$AgentDir\nssm.exe") { "$AgentDir\nssm.exe" }
           elseif (Test-Path "C:\Program Files\NSSM\nssm.exe") { "C:\Program Files\NSSM\nssm.exe" }
           else { "nssm" }

Write-Host "[repair] Cirque RMM Agent Self-Repair" -ForegroundColor Cyan
Write-Host "[repair] Agent dir: $AgentDir"

# ---- Step 1: Check current state ----
$currentOK = $false
try {
    $result = & python -c "import ast, sys; ast.parse(open(sys.argv[1],'rb').read()); print('OK')" $AgentPy 2>&1
    $currentOK = ($result -eq "OK")
} catch {}

if ($currentOK) {
    Write-Host "[repair] agent_client.py is valid — checking if service is running" -ForegroundColor Green
} else {
    Write-Host "[repair] agent_client.py has errors — attempting recovery" -ForegroundColor Yellow

    # ---- Step 2: Try .old backup ----
    $restoredFromOld = $false
    if (Test-Path $AgentOld) {
        try {
            $result = & python -c "import ast, sys; ast.parse(open(sys.argv[1],'rb').read()); print('OK')" $AgentOld 2>&1
            if ($result -eq "OK") {
                Copy-Item -Path $AgentOld -Destination $AgentPy -Force
                Write-Host "[repair] Restored from $AgentOld" -ForegroundColor Green
                $restoredFromOld = $true
            } else {
                Write-Host "[repair] .old backup also invalid — will try download" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "[repair] Could not validate .old backup: $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[repair] No .old backup found" -ForegroundColor Yellow
    }

    # ---- Step 3: Download fresh from server ----
    if (-not $restoredFromOld) {
        # Read environment variables from NSSM service (they contain the credentials)
        $nssmEnv = @{}
        try {
            $envBlock = & $NssmExe get $ServiceName AppEnvironmentExtra 2>&1
            ($envBlock -join "`n") -split "`n" | ForEach-Object {
                $trimmed = $_.Trim()
                if ($trimmed -match "^(RMM_\w+)=(.+)$") { $nssmEnv[$Matches[1]] = $Matches[2].Trim() }
            }
        } catch {}

        $trackerUrl = if ($nssmEnv["RMM_TRACKER_URL"]) { $nssmEnv["RMM_TRACKER_URL"] } else { "https://tracker.corp.cirque.com" }
        $agentId    = $nssmEnv["RMM_AGENT_ID"]
        $token      = $nssmEnv["RMM_AGENT_TOKEN"]

        if ($agentId -and $token) {
            try {
                $webClient = New-Object System.Net.WebClient
                [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }

                $fileUrl = "$trackerUrl/rmm/agent/file?agent_id=$agentId&token=$token"
                Write-Host "[repair] Downloading agent_client.py from $fileUrl" -ForegroundColor Cyan
                $webClient.DownloadFile($fileUrl, "$AgentPy.dl")

                $result = & python -c "import ast, sys; ast.parse(open(sys.argv[1],'rb').read()); print('OK')" "$AgentPy.dl" 2>&1
                if ($result -eq "OK") {
                    Copy-Item -Path "$AgentPy.old" -Destination "$AgentPy.bak" -Force -ErrorAction SilentlyContinue
                    Copy-Item -Path "$AgentPy.dl"  -Destination $AgentPy -Force
                    Write-Host "[repair] agent_client.py updated" -ForegroundColor Green
                } else {
                    Write-Host "[repair] Downloaded file also invalid — cannot repair automatically" -ForegroundColor Red
                    Remove-Item "$AgentPy.dl" -ErrorAction SilentlyContinue
                    exit 1
                }

                # Also update agent_launcher.py
                $LauncherPy  = "$AgentDir\agent_launcher.py"
                $launcherUrl = "$trackerUrl/rmm/agent/launcher?agent_id=$agentId&token=$token"
                Write-Host "[repair] Downloading agent_launcher.py from $launcherUrl" -ForegroundColor Cyan
                try {
                    $webClient.DownloadFile($launcherUrl, "$LauncherPy.dl")
                    Move-Item -Path "$LauncherPy.dl" -Destination $LauncherPy -Force
                    Write-Host "[repair] agent_launcher.py updated" -ForegroundColor Green
                } catch {
                    Write-Host "[repair] launcher update skipped: $_" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "[repair] Download failed: $_" -ForegroundColor Red
                exit 1
            }
        } else {
            Write-Host "[repair] Cannot determine tracker URL/agent ID/token from NSSM env" -ForegroundColor Red
            Write-Host "[repair] Manual fix required:" -ForegroundColor Red
            Write-Host "  1. Copy a working agent_client.py to $AgentPy" -ForegroundColor White
            Write-Host "  2. Run: nssm restart $ServiceName" -ForegroundColor White
            exit 1
        }
    }
}

# ---- Step 4: Restart the NSSM service ----
Write-Host "[repair] Restarting $ServiceName service..." -ForegroundColor Cyan
try {
    sc.exe stop $ServiceName 2>$null
    Start-Sleep -Seconds 3
    & $NssmExe start $ServiceName
    Start-Sleep -Seconds 5
    $status = & $NssmExe status $ServiceName
    Write-Host "[repair] Service status: $status" -ForegroundColor $(if ($status -eq "SERVICE_RUNNING") { "Green" } else { "Yellow" })
} catch {
    Write-Host "[repair] Service restart failed: $_" -ForegroundColor Red
}

Write-Host "[repair] Done." -ForegroundColor Cyan
