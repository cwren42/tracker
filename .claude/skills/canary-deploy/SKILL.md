---
name: canary-deploy
description: Stage and roll out a new RMM agent version to the canary roster — bump version, sign, validate on one box, widen, nudge online agents, confirm landed. Use when deploying an agent_client.py / tray.py change to the Windows fleet.
allowed-tools: Bash, Read, Edit
---
# Deploy an RMM agent version (canary → fleet)

The canary build is served to agents listed in `setting['rmm_agent_canary']` **straight from the on-disk files** in `/var/www/tracker/rmm_agent/canary/`. So the checked-out branch = what the roster pulls. Treat this as production.

## 0. Preflight
- `git branch --show-current` — work on `main` (or merge to main before serving). A stray branch checked out IS being served to the roster.
- Confirm `rmm-gateway.service` is up (`systemctl is-active rmm-gateway`) — it's how `update_now` reaches agents.

## 1. Edit + bump (version.txt MUST match AGENT_VERSION)
- Make the code change in `rmm_agent/canary/agent_client.py` (and/or `tray.py`).
- Bump **both** `AGENT_VERSION` (in agent_client.py) **and** `rmm_agent/canary/version.txt` to the new version — a CI guard enforces they match. version.txt has no trailing newline.
- Regenerate the checksum sidecar: `sha256sum rmm_agent/canary/agent_client.py | cut -d' ' -f1 > rmm_agent/canary/agent_client.py.sha256`
- `venv/bin/python -m py_compile rmm_agent/canary/agent_client.py`

## 2. Verify the SIGNED-update chain (2.9.18+ fail-closed)
Agents on 2.9.18+ refuse an unsigned/badly-signed update. Confirm the server can sign the new bytes and the embedded pubkey accepts it:
```
venv/bin/python - <<'PY'
import agent_update_signing, hashlib, re
f="rmm_agent/canary/agent_client.py"; sig=agent_update_signing.sign_file(f); src=open(f).read()
N=int(re.search(r'_UPDATE_PUB_N = int\("([0-9a-f]+)"',src).group(1),16); E=65537
DER=bytes.fromhex("3031300d060960864801650304020105000420")
s=bytes.fromhex(sig); k=(N.bit_length()+7)//8; em=pow(int.from_bytes(s,'big'),E,N).to_bytes(k,'big')
sep=em.index(b"\x00",2); print("signed-update verifies:", em[sep+1:]==DER+hashlib.sha256(open(f,'rb').read()).digest())
PY
```
Must print `True`. The signing key lives at `.agent_signing/agent_update_key.pem` (server-only, gitignored — must stay backed up; lose it and you can't update the 2.9.18+ fleet).

## 3. Validate on ONE box first (a Windows-specific change can't be lab-tested)
- **Back up the roster**: save `setting['rmm_agent_canary']` to a file first.
- **Narrow** the roster to a single safe box (CHRIS-DESKTOP = admin test box; the rest stay on their current version — agents refuse downgrades, so removing them is harmless): `UPDATE setting SET value='CHRIS-DESKTOP' WHERE key='rmm_agent_canary'`.
- The on-disk new version is now served only to that box. **Nudge it**: `curl -s -X POST http://127.0.0.1:8765/send-msg/CHRIS-DESKTOP -H 'Content-Type: application/json' -d '{"type":"update_now","session_id":0}'`
- Poll `rmm_telemetry.agent_version` for that box until it shows the new version (~1-3 min: pull → swap → NSSM restart → reconnect).
- **Validate the actual change** (e.g. read a file, check a process) via `run_script` over the gateway — confirm it behaves. For UI/login-time behavior (tray VBS, startup), it only shows on login/reboot — confirm accordingly.

## 4. Widen + nudge
- Restore the full roster from the backup file.
- **Nudge online agents** so they pull now instead of waiting ~4h: for each roster agent live on `GET http://127.0.0.1:8765/agents`, POST `update_now`. Offline ones update on their next reconnect.
- Confirm version distribution: `SELECT t.agent_version, COUNT(*) ... GROUP BY 1`.

## Gotchas
- **Servers are NOT on canary** (stay on the fleet default `rmm_agent/version.txt`) — never add the Windows Server estate to the roster.
- `update_now` is the command 2.9.x agents honor (re-runs check_for_update, swaps only if newer). It only reaches agents with a **live WS** on the gateway — offline ones wait for reconnect.
- Commit on `main` and **leave the tree on `main`** when done (serves-from-disk). Reference: [[ship]].
