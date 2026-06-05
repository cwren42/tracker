---
name: vuln-status
description: Get the trustworthy vulnerability posture — run the Defender sync and read the Tracker↔Defender reconciliation with locked unit definitions. Use to answer "where do we stand on CVEs" without conflating units.
allowed-tools: Bash, Read
---
# Vulnerability status (reconciled, unit-explicit)

Microsoft Defender (TVM) is the source of truth and is externally verifiable in the Defender portal. The Tracker mirrors it into `device_vulnerability` (the per-device open/remediated ledger). Always quote numbers **with their unit** and trace them to Defender.

## Locked unit definitions — never conflate these
- **Affected machines** = distinct devices with ≥1 open finding.
- **Distinct CVEs** = unique CVE ids (report by severity Critical/High/Medium/Low).
- **Device-CVE pairs** = machine×CVE rows (the big number — `device_vulnerability` row count). 1 box a few Chrome versions behind ≈ hundreds of CVEs, so **pairs ≫ distinct CVEs ≫ machines**. Don't call pairs "problems."

## 1. Refresh + read
- Run the sync to pull live Defender state and recompute the snapshot: `venv/bin/python run_defender_sync.py` (logs `Reconciliation snapshot stored: defender_raw=… computed_open=… coverage_gap=…` and `Total exposed (all products): …`).
- Or just read the stored snapshot without a live pull: `setting['vuln_reconciliation_snapshot']` (JSON: waterfall, defender_metrics, reconciled_metrics, coverage, fetched_at).

## 2. The truth queries (correct units)
```
-- open by severity (PAIRS) + distinct CVEs + machines
SELECT severity, COUNT(*) pairs, COUNT(DISTINCT cve_id) cves
FROM device_vulnerability WHERE status='Open' GROUP BY severity ORDER BY 2 DESC;
SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status='Open';
```

## 3. The reconciliation waterfall (this is the trust artifact)
The page is at `/vulnerabilities/reconciliation` (Security → Vulnerabilities → Reconciliation), served from the snapshot (loads ~0.4s). It shows:
`Defender raw (distinct machine,CVE pairs) − unmapped machines − software-not-present − multi-machine-per-asset = Tracker Open`. A small delta is the documented filters; a LARGE unexplained delta is a bug — investigate. The top-line distinct-Critical-CVEs + affected-machines are the portal-comparable figures.

## Critical history / gotchas
- **The masking bug (fixed 2026-06-05, commit 7bbaf27):** close-by-absence used naive `utcnow()` vs `NOW()` (local TZ) and auto-closed every Open finding the same sync — the fleet falsely read "0 open / 100% Remediated." Now it uses a per-run seen-set anti-join (`alert_service.build_machine_asset_map` / `build_software_present_fn`). If you EVER see device_vulnerability at ~100% Remediated again, suspect a regression here.
- **`device_vulnerability.agent_id` is a HASH, not a hostname.** To map a finding to an agent for dispatch, join `device_vulnerability.asset_id → asset.id → rmm_agent.asset_id → rmm_agent.agent_id` (hostname). Joining agent_id↔agent_id null-joins and makes everything look offline.
- **`vulnerability_cache.exposed_machines` is a raw vendor-feed match counter** — NOT reconciled to open status. Do not quote it as "open exposure" (that was the morning "85 critical" error). Use `device_vulnerability` status='Open'.
- Numbers legitimately drift between syncs (Defender re-assesses continuously). Quote `fetched_at`.
- Coverage gap: ~38 Defender machines unmapped (printers/NAS/ex-employee laptops) — `compliance-expert` owns reconciling those.
