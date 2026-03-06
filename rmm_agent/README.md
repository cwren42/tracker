# Tracker RMM Agent (MVP)

This is a minimal Python client that connects to the RMM gateway.

## Install

On the endpoint:

```bash
python -m pip install -r requirements.txt
```

## Deploy (Windows, via NSSM — temporary until MSI)

### 1. Enroll the machine on the Tracker server

Run this once per endpoint, using the computer's hostname as the agent ID:

```bash
/var/www/tracker/venv/bin/python /var/www/tracker/scripts/enroll_rmm_agent.py \
  --agent-id PC-01 --asset-id 123
```

Copy the printed `token`.

### 2. Copy the agent folder to the endpoint

Place files at: `C:\Program Files\CirqueRMM\`

### 3. Install as a Windows service (PowerShell, run as admin)

```powershell
cd "C:\Program Files\CirqueRMM"
.\install_service.ps1 -Token "agent_xxxx..."
```

- `RMM_AGENT_ID` is set **automatically to `%COMPUTERNAME%`** — no manual config needed.
- Download NSSM from https://nssm.cc/download and place `nssm.exe` at `C:\Program Files\NSSM\nssm.exe`.

### Optional env overrides

If you need to override the auto-detected hostname, set `RMM_AGENT_ID` as a system env var
before running the installer (or pass it via the `-NssmPath`/NSSM AppEnvironmentExtra manually).

## Notes

- MVP supports `ping` and a limited `exec` command set.
- Next steps are adding:
  - PowerShell/CMD interactive streams
  - Run-as-SYSTEM vs run-as-logged-in-user
  - File browse/upload/download
  - Services/process control
  - Full audit trail in `rmm_event`
