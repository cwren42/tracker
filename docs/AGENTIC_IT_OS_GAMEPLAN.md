# Agentic IT-Ops OS — Build Gameplan (grounded on this server)

North star: turn the Tracker into an **Agentic IT Operations OS** with a central **master brain
that learns and grows** — a swarm of IT agents over a connected world model, so one admin operates
like a team of 50. The brain reasons over the IT graph + event history, orchestrates the swarm, and
improves from execution telemetry + the admin's approval decisions.

> Produced by the `it-os-gameplan` multi-agent workflow (7 recon experts → synthesis → adversarial
> critique → finalize), grounded in the real codebase.

## Stance
The server is **lopsided**: the *execution substrate* (RMM agent + WebSocket gateway + the CVE
auto-remediation loop) is production-grade, the *world model* is rich but **disconnected** (identity
islands), and the *connective tissue the roadmap sells* (event bus, approval engine, graph queries,
agent runtime) is **dead code or absent**. So the work is **not "build agents"** — it's: **connect the
islands → wire the dead event path → gate it with risk → record everything → wrap a thin reasoning
loop around primitives that already work.**

**No new infra.** At ~91 employees / 225 assets / 5 gunicorn workers: Postgres recursive CTEs + a
transactional outbox + the existing fcntl-locked systemd-executor pattern. No Neo4j / Kafka / Redis /
vector DB. The moat is the connected data + codified policy, not a broker.

## Current-state reality (verified)
**Crown jewel — reuse, don't rebuild:** `rmm_agent/agent_client.py` + `rmm_gateway/main.py` expose ~28
message types (`run_script`, `install_cve_patches`, `winget_install`, …) over the live gateway
(`POST 127.0.0.1:8765/send-msg/<agent_id>`). The gateway CVE loop (`_new_vuln_dispatch_loop` →
`cve_patch_result`) is a **real Observe→Plan→Execute→Verify→Learn loop already running in prod** —
generalize it; don't invent a runtime.

**Broken / dead — fix before anything triggers:**
- **`workflow_engine` device actions have NEVER executed.** `_queue_rmm`, `_action_azure_sync:144`, and
  ~6 `rmm_event` INSERT handlers (507-604) insert wrong columns (`rmm_event` is
  `id, session_id, actor_type, event_type, data_json, created_at`). Silent no-ops.
- **`rmm_commands` is NOT the rich lane** — only consumed on the agent's 5-min heartbeat poll, string
  commands only. The rich types are gateway-only. Reuse `api_rmm_send_command`'s dual-path
  (`rmm.py:732-776`): gateway `/send-msg` + `rmm_commands` fallback for offline agents.
- **`fire_trigger`/`start_schedule_runner` have zero callers**; the only live run path is the manual UI
  button (`ai.py:338`) running `execute_workflow` synchronously in a Flask worker.

**Disconnected world model:** `m365_user.employee_id` 0/183; `intune_device.asset_id` 0/83 (FKs exist,
nothing populates them). **No group/membership table.** `workflow_runs`=0, `ai_ticket_suggestions`=0
(nothing has ever run). No `events`/`approvals` tables.

**Good infra to clone:** `alert_service` IS fcntl-locked (flock + timestamp, single-flighted across the
5 workers) — clone *this* real pattern for the dispatcher. `m365_service` is GET-only (all cloud writes
are net-new). Secrets already rotated/handled per the operator (do not re-raise).

## The four moat components — how to build them here

**(a) IT graph — Postgres + recursive CTE (no graph DB).**
1. *Connect the islands* (highest value): add resolution to the M365 sync (`blueprints/employees.py`,
   match m365_user→employee on UPN/email) and Intune sync (`assets_intune.py:79`, match
   intune_device→asset on serial/azure_ad_device_id) + backfill the existing 183/83.
2. New flat tables: `group_node`, `group_membership(group_id, employee_id)`,
   `group_grants(group_id, grant_type, target_ref)`, `application(...)`, `employee.manager_id`. Sync from
   `m365_service.get_all_groups/get_group_members` + LDAP `memberOf`. (No bitemporal until a reader needs it.)
3. `who_loses_access(group_id)` recursive-CTE view — the Phase-4 flagship answer, zero new datastore.

**(b) Event bus — transactional outbox + ONE systemd dispatcher** (in-process won't span 5 workers).
- `events(id, event_type, aggregate_type, aggregate_id, payload jsonb, actor, occurred_at, correlation_id, dispatched_at)`.
- Publishers emit on **`after_commit`** (NOT the per-row `_log_audit` hooks at `models.py:1207` — they fire
  inside the txn and would log rolled-back events). Emit from ticket-create, `alert_service` fire,
  `/employees/offboard`, `cve_patch_result`.
- Dispatcher = one process cloning `alert_service` flock+timestamp + `check_execution_engine.py` systemd
  shape; poll `events WHERE dispatched_at IS NULL ... FOR UPDATE SKIP LOCKED` → `fire_trigger()`. Budget
  full app context (`EnvironmentFile`, secret decrypt, `pg_db` liveness). **Monitor it** (`Restart=on-failure`
  + staleness alert) — it's a SPOF for all automation.

**(c) Agent runtime — generalize the CVE loop (O→A→P→E→V→L).** Thin orchestrator: Observe (graph context
via `ai_engine.ask_ai`) → Analyze/Plan (`_openai_chat` → structured JSON of *typed catalog actions*, like
`suggest_ticket_resolution`) → Execute (typed catalog: RMM via gateway dual-path; identity via `ldap_service`)
→ Verify (read `rmm_commands.result/exit_code`; per-action assertions for cloud writes) → Learn (record
outcome, feed suggestion grounding). Each agent = an event subscriber on the single-instance pattern.

**(d) Approval engine + telemetry.**
- `approvals(id, action_type, risk_tier, payload, status, confidence, requested_by, reviewed_by, event_id, correlation_id, prior_state jsonb)`.
- Risk tiers: **Low (auto)** = password reset, unlock, catalog install, CVE patch on connected agent;
  **Medium (approve)** = license/group change, reboot, arbitrary run_script; **High** = disable_ad_user,
  role/domain-admin, firewall; **Critical** = prod infra, account delete.
- **Defender isolate (`machineActions`) is permanently manual** — highest blast radius, no SOC to undo it.
- **Gate goes BELOW `execute_workflow`** (so the manual `ai.py:338` button can't bypass it).
- **Pause/resume is a real state machine** (the most under-estimated build): `_run_workflow` runs the DAG
  synchronously today; Medium+ must persist partial state, insert an `approvals` row, resume later.
- Telemetry: **`workflow_run_steps` is the v1 system of record** (per-step status/input/output/error).
  Defer a separate ledger; add a union *view* when Mission Control is built.

## Corrected sequence
- **Sprint 0:** secrets — already handled per operator (DB pw + M365 + PAT rotated). Don't block on it.
- **Months 1-3 (connect, wire, gate):** connect identity islands → `events` + after_commit outbox + monitored
  dispatcher (activate `fire_trigger`) → pause/resume + `approvals` + risk gate below `execute_workflow` →
  fix ALL broken `rmm_event` INSERTs → **kill switch (`AUTOMATION_ENABLED`) + shadow mode** → ship the
  warm-up workflow and let it bake.
- **Months 4-6 (first full agent):** group graph + `who_loses_access` (must precede offboarding's group step)
  → M365/Graph WRITE methods (license assign/remove, revokeSignInSessions) → **Offboarding as a guarded saga**
  (only after gate + pause/resume + kill switch + shadow + rollback snapshots exist).
- **Months 7-12 (consolidate, NOT a swarm):** run ONE workflow live for a month before the second. Then
  Knowledge Agent (pgvector over PolicySection/ISMS), Security Agent (persist Defender/AAD risk events,
  correlate via graph; isolate stays manual), Procurement (backfill license cost/renewal + subscribedSkus),
  Mission Control UI over workflow_runs/events/approvals.

## The TRUE first killer workflow
**NOT offboarding first.** Start with **ticket-driven AD unlock / password reset** — lowest-risk action
class (recoverable), produces the first-ever `ai_ticket_suggestions`/`workflow_runs` rows, exercises the
full O→A→P→E→V loop. **Let it bake a month.** Offboarding is bet #2, built as a saga: idempotent re-runnable
steps, prior-state snapshot before destructive writes (one-click reversible), group removal off
`group_membership`.

## 90-day build list (dependency order, real files)
1. Connect islands: `blueprints/employees.py` + `assets_intune.py:79` populate `m365_user.employee_id` /
   `intune_device.asset_id` + backfill migration. Add `employee.manager_id`.
2. Fix **every** broken `rmm_event` INSERT in `workflow_engine.py` (`_queue_rmm:295`, `_action_azure_sync:144`,
   handlers ~507-604) → gateway `/send-msg` + `rmm_commands` fallback (reuse `rmm.py:732-776`); read result back.
3. `events` table + `after_commit` publishers (ticket-create, alert fire, offboard, cve_patch_result).
4. `event_dispatcher.py` systemd service (clone `alert_service` flock + `check_execution_engine` shape;
   FOR UPDATE SKIP LOCKED → `fire_trigger`); `Restart=on-failure` + staleness alert.
5. Pause/resume state machine in `_run_workflow`; `approvals` table (+ `prior_state`); `risk.py` scorer;
   gate below `execute_workflow`; approvals queue UI (reuse ai.py suggestion-review routes).
6. Kill switch `AUTOMATION_ENABLED=false` + shadow mode (propose-and-log for weeks before live writes).
7. Warm-up workflow: extend `suggest_ticket_resolution` context with reporter account state (now reachable),
   wire apply → `_action_unlock_account`/`_action_reset_password` + Verify. **No offboarding yet.**
8. Group graph (`group_node`/`group_membership`/`group_grants`) + sync + `who_loses_access` view.
   *(Offboarding saga follows in Months 4-6.)*

## Hard parts honored
PII/retention on the append-only `events` log (you hold SOC2/ISMS — define retention + read-access first);
saga safety (idempotency, partial-failure resume, rollback snapshots); dispatcher operational surface
(app context, connection liveness, health monitoring — budgeted, not free).

## Moat thesis
For a one-admin shop on a single Flask+Postgres monolith with on-prem AD + M365/Intune/Defender + a custom
RMM fleet, the moat is **not** the gpt-4o calls. It's the **connected, append-only record of THIS
environment**: the **graph** (Employee↔M365User↔Asset↔IntuneDevice + groups/grants/apps), the **event
history** (after_commit `events` — queryable, replayable, the brain's training signal), the **approval
policy** (codified risk tiers + the admin's accept/reject decisions = organizational knowledge the LLM
doesn't own), and the **telemetry** (what actually worked on these endpoints). Connect the graph, wire the
bus on after_commit, gate with risk below execute_workflow, record everything — the agents become a thin,
swappable layer over a moat no competitor can copy, because it's *your* environment's state, history,
policy, and proven execution. Discipline that keeps a solo operator safe: **one workflow live at a time,
kill switch always present, isolate always manual.**
