#!/usr/bin/env python3
# deploy_watchdog.py  --  Queue CirqueRMM-Watchdog scheduled task to all online Windows agents.
#
# Run once:  python3 /var/www/tracker/deploy_watchdog.py [--dry-run]
#
# Each online agent will receive a 'powershell' command that:
#   1. Writes watchdog.ps1 to C:\CirqueRMM\
#   2. Registers the CirqueRMM-Watchdog scheduled task (SYSTEM, every 15 min)
# The agent_launcher.py heartbeat thread picks up the command and executes it.

import os
import sys
import argparse
import datetime
import psycopg2
import psycopg2.extras

# Source the connection string from the environment. Before running:
#   set -a; . /var/www/tracker/.secrets.env; set +a
DB_DSN = os.environ.get('DATABASE_URL')
if not DB_DSN:
    sys.exit('DATABASE_URL not set. Run: set -a; . /var/www/tracker/.secrets.env; set +a')

INSTALL_DIR   = r"C:\CirqueRMM"
SERVICE_NAME  = "CirqueRMM"

# The watchdog PS1 content (literal backslashes – will be embedded in the outer PS1)
WATCHDOG_BODY = r"""
$svc = Get-Service -Name 'CirqueRMM' -ErrorAction SilentlyContinue
if (-not $svc) { exit 0 }
if ($svc.Status -ne 'Running') {
    Get-Process python, python3 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    $nssmExe = if (Test-Path 'C:\CirqueRMM\nssm.exe') { 'C:\CirqueRMM\nssm.exe' } else { 'C:\Program Files\NSSM\nssm.exe' }
    if (Test-Path $nssmExe) { & $nssmExe reset 'CirqueRMM' AppThrottle 2>$null }
    sc.exe start 'CirqueRMM' | Out-Null
    Start-Sleep 5
    $svc.Refresh()
    $status = (Get-Service 'CirqueRMM' -ErrorAction SilentlyContinue).Status
    "Watchdog: restarted CirqueRMM at $(Get-Date -Format 's') -> $status" | Out-File -Append -Encoding UTF8 'C:\CirqueRMM\logs\watchdog.log'
}
""".strip()

# Outer command: write watchdog.ps1 then register the task
DEPLOY_COMMAND = r"""
$watchdogPath = 'C:\CirqueRMM\watchdog.ps1'
$watchdogBody = @'
{watchdog_body}
'@
$null = New-Item -Path 'C:\CirqueRMM\logs' -ItemType Directory -Force
$watchdogBody | Out-File -FilePath $watchdogPath -Encoding UTF8 -Force

$existing = Get-ScheduledTask -TaskName 'CirqueRMM-Watchdog' -ErrorAction SilentlyContinue
if ($existing) {{ Unregister-ScheduledTask -TaskName 'CirqueRMM-Watchdog' -Confirm:$false }}

$wdAction    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdogPath`""
$wdTrigger   = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At (Get-Date).Date
$wdPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$wdSettings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'CirqueRMM-Watchdog' -Action $wdAction -Trigger $wdTrigger -Principal $wdPrincipal -Settings $wdSettings -Description 'Restarts CirqueRMM agent service if stopped. Cirque IT self-healing watchdog.' -Force | Out-Null
"Watchdog task registered at $(Get-Date -Format 's')" | Out-File -Append -Encoding UTF8 'C:\CirqueRMM\logs\watchdog.log'
Write-Output "CirqueRMM-Watchdog task installed OK"
""".strip().format(watchdog_body=WATCHDOG_BODY)


def main():
    parser = argparse.ArgumentParser(description="Deploy watchdog task to all online Windows agents")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done, do not write to DB")
    parser.add_argument("--minutes", type=int, default=30, help="Consider agents online if seen within N minutes (default 30)")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Find online Windows agents (exclude Linux tracker server by filtering on agent_id pattern)
    cur.execute("""
        SELECT a.agent_id, ast.name
        FROM rmm_agent a
        JOIN asset ast ON a.asset_id = ast.id
        WHERE a.last_seen_at > NOW() - INTERVAL '%s minutes'
          AND a.enabled = TRUE
          AND a.agent_id NOT LIKE 'linux-%%'
        ORDER BY ast.name
    """, (args.minutes,))
    agents = cur.fetchall()

    print(f"Targeting {len(agents)} online Windows agents (seen within {args.minutes} min):")
    for ag in agents:
        print(f"  {ag['name']} ({ag['agent_id']})")

    if args.dry_run:
        print("\n[DRY RUN] No commands queued.")
        conn.close()
        return

    print(f"\nQueuing watchdog deployment command...")
    now = datetime.datetime.utcnow()
    queued = 0
    for ag in agents:
        cur.execute("""
            INSERT INTO rmm_commands (agent_id, command, command_type, status, created_at)
            VALUES (%s, %s, 'powershell', 'pending', %s)
        """, (ag['agent_id'], DEPLOY_COMMAND, now))
        queued += 1

    conn.commit()
    conn.close()
    print(f"Done. {queued} commands queued — agents will pick them up on next heartbeat (within 5 min).")
    print("Monitor progress:")
    print("  sudo -u postgres psql tracker -P pager=off -c \"SELECT agent_id, status, exit_code, LEFT(result,80) FROM rmm_commands WHERE command_type='powershell' ORDER BY created_at DESC LIMIT 20;\"")


if __name__ == "__main__":
    main()
