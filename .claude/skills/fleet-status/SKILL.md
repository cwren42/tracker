---
name: fleet-status
description: One-shot fleet standup — agent versions/online, patch posture, and CVE posture, with the correct joins and online signal so counts are right. Use for "where do we stand with agents/patches/CVEs".
allowed-tools: Bash, Read
---
# Fleet status (agents · patches · CVEs)

A consistent standup view. The recurring mistake to avoid: **two different "online" signals + a hash-vs-hostname join** — get those right or the numbers are wrong.

## Online: two signals, know which you're using
- **`rmm_agent.last_seen_at`** — DB heartbeat (5-min pull). Can lag.
- **Gateway live-WS** (`curl -s http://127.0.0.1:8765/agents`) — the authoritative "reachable right now" set, and what dispatch uses. The Assets page treats online = gateway-live OR last_seen<5min. For "can I push to it now?", use the gateway list.

## 1. AGENTS
```
-- version distribution (enabled) + online
SELECT t.agent_version, COUNT(*) FROM rmm_agent a
  JOIN LATERAL (SELECT agent_version FROM rmm_telemetry WHERE agent_id=a.agent_id ORDER BY captured_at DESC LIMIT 1) t ON true
  WHERE a.enabled=true GROUP BY 1 ORDER BY 2 DESC;
SELECT COUNT(*) FROM rmm_agent WHERE enabled=true AND now()-last_seen_at < interval '10 min';  -- DB-online
```
Cross-check the DB-online count against `len(/agents)` — if they diverge a lot, agents are connected-but-not-writing last_seen (or vice-versa). Flag agents on old versions or enabled-but-offline >30d (cleanup candidates). **Servers (Windows Server estate) are intentionally on the fleet default (~2.9.7), NOT canary** — don't flag that as drift.

## 2. PATCHES
```
SELECT status, COUNT(*) FROM rmm_patch_job GROUP BY 1 ORDER BY 2 DESC;   -- 'failed' should be small/real (no_op is benign)
SELECT COUNT(DISTINCT agent_id), COUNT(*) FROM rmm_pending_update;        -- devices with pending + total
```
- `no_op` = nothing-to-install (not a failure). `queued` = waiting for the agent to come online (reconnect-remediation engine delivers it). `deploying` >6h with no result = genuinely stuck (the stale-sweep fails it).
- Watch a box stuck `deploying` (was the SARA symptom); the engine + sweep handle it, but call it out.

## 3. CVEs — use vuln-status definitions (don't conflate units)
```
SELECT severity, COUNT(*) pairs, COUNT(DISTINCT cve_id) cves
FROM device_vulnerability WHERE status='Open' GROUP BY 1 ORDER BY 2 DESC;
SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status='Open';
```
Report **pairs vs distinct CVEs vs affected machines** separately, each labeled. The trustworthy/portal-comparable view is the reconciliation snapshot (`setting['vuln_reconciliation_snapshot']`) / `/vulnerabilities/reconciliation`. NEVER quote `vulnerability_cache.exposed_machines` as open exposure (it's a raw feed counter). See [[vuln-status]].

## Output
Three labeled sections (Agents / Patches / CVEs), headline numbers first, then notables + a short "needs attention" list. Scannable, not an essay. If anything looks off vs a second source, say so rather than asserting a number.
