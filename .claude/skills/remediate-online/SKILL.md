---
name: remediate-online
description: Remediate critical CVEs on the devices that are online right now — enqueue SAFE winget upgrades via the reconnect-remediation engine. Use to actively close browser/app exposure on reachable machines.
allowed-tools: Bash, Read
---
# Remediate critical CVEs on online devices

Uses the **reconnect-triggered remediation engine**: enqueue a winget upgrade per (machine, product); online agents dispatch immediately over the live WS, offline ones deliver on reconnect. The CVE flips to Remediated on the next Defender re-scan once the box reports the new version.

## 1. Find the online attack surface (CORRECT join)
`device_vulnerability.agent_id` is a HASH — map via asset: `device_vulnerability.asset_id → asset.id → rmm_agent.asset_id → rmm_agent.agent_id` (hostname). "Online now" = the gateway's live-WS list, NOT `last_seen_at`:
```
curl -s http://127.0.0.1:8765/agents   # live set; intersect against the affected hosts
```
Pull open/critical rows joined to live agents, grouped by product.

## 2. SAFE products only (these stage, no force-close/reboot)
Enqueue winget upgrades ONLY for: **chrome→Google.Chrome, edge_chromium-based→Microsoft.Edge, firefox→Mozilla.Firefox, 7-zip→7zip.7zip, reader→Adobe.Acrobat.Reader.64-bit**.
**DO NOT** auto-remediate: **python** (winget can't upgrade in-place + it's dev tooling — surface as a decision, never auto-push), **openssl / log4j / commons_text / qt / sqlite** (embedded libs — not winget-fixable, mostly benign, mitigation/app-update), **windows_10/11** (OS cumulative — needs reboots, separate flow), **jre / visual_studio / silverlight / meetings / tigervnc / codemeter** (app-specific / EOL-remove). Mac (`chrome_for_mac`) — winget is Windows-only.

## 3. Enqueue (idempotent)
Use the existing action in `blueprints/vulnerabilities.py` — `POST /api/vulnerabilities/browser-remediate` (or `_enqueue_browser_remediation` directly), which builds the run_script winget payload and POSTs the gateway `/remediation/<agent>/enqueue`. The payload **resolves winget.exe explicitly** (not on SYSTEM PATH) and treats "no applicable upgrade" exit as success. Engine guardrails apply: dedup by (machine,package), confirm-on-send, attempts cap → abandoned. Skip a (machine,package) that already has a pending/deploying/recently-completed `rmm_remediation_queue` row.

## 4. Validate + report
- Validate a NEW package type on one box first (read version → upgrade → re-read). Browsers/7-zip already proven.
- Report per-machine: enqueued / dispatched-now (online) / queued (offline) / completed / failed. Failures auto-retry via the engine's stale-reset + retry loop.
- Closure proof: re-run `vuln-status` after Defender re-scans — the criticals flip Open→Remediated.

## Gotchas
- winget over the agent runs as SYSTEM with no winget on PATH — the payload globs `C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_*\winget.exe`. If a box errors with "'winget' not recognized," that resolution failed.
- run_script is capped at 300s on the agent — a big Chrome download can hit it and land in `deploying`; the engine resets it to `queued` for retry. Don't treat that as a hard failure.
- Surface the judgment-call leftovers (python, openssl, Windows cumulative, untracked machines) to the operator — never auto-act on them.
