import asyncio
import json
import random
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .db import (
    create_rmm_session,
    end_rmm_session,
    get_agent_flags,
    get_conn,
    get_cursor,
    get_eagle_config,
    get_latest_screenshot,
    get_latest_telemetry,
    log_rmm_event,
    mark_agent_offline,
    store_eagle_event,
    store_patches,
    store_pending_updates,
    store_screenshot,
    store_software,
    store_telemetry,
    store_work_hours,
    validate_agent,
    validate_api_key,
    validate_session_token,
)

import time as _time

# --- Eagle Eyes config cache (per-agent, short TTL) ---------------------------
# Event ingest is gated on the live `enabled` flag, but events are frequent, so a
# fresh DB read on every event would hammer the DB. Cache get_eagle_config per
# agent_id for a short window. TTL is short enough that a disable takes effect
# within ~1 min on its own; an explicit config PUSH also invalidates the entry
# (see _invalidate_eagle_cache) so a disable is reflected immediately.
_EAGLE_CFG_TTL_S = 45
_eagle_cfg_cache: Dict[str, tuple] = {}  # agent_id -> (expires_at_epoch, config_dict)


def get_eagle_config_cached(agent_id: str) -> dict:
    """get_eagle_config with a short in-process TTL cache (for hot event ingest)."""
    now = _time.monotonic()
    hit = _eagle_cfg_cache.get(agent_id)
    if hit and hit[0] > now:
        return hit[1]
    cfg = get_eagle_config(agent_id)
    _eagle_cfg_cache[agent_id] = (now + _EAGLE_CFG_TTL_S, cfg)
    return cfg


def _invalidate_eagle_cache(agent_id: str) -> None:
    """Drop the cached config so the next ingest re-reads from the DB."""
    _eagle_cfg_cache.pop(agent_id, None)


async def _dispatch_next_product(websocket, agent_id: str) -> bool:
    """Dispatch the next queued product (fewest CVEs first) to the agent.
    Returns True if a product was dispatched, False if nothing queued."""
    try:
        conn = get_conn()
        cur  = get_cursor(conn)
        # Pick the product+asset group with the fewest pending CVE jobs (quickest to finish)
        cur.execute(
            """SELECT COALESCE(dv.product_name,'') AS product_name, j.asset_id,
                      MIN(j.id) AS rep_id, COUNT(*) AS job_count
               FROM cve_patch_job j
               LEFT JOIN device_vulnerability dv
                      ON dv.cve_id = j.cve_id AND dv.asset_id = j.asset_id
               WHERE j.agent_id=%s AND j.status='queued'
               GROUP BY COALESCE(dv.product_name,''), j.asset_id
               ORDER BY COUNT(*) ASC
               LIMIT 1""",
            (agent_id,)
        )
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return False
        product_name = row["product_name"]
        asset_id_j   = row["asset_id"]
        rep_id       = row["rep_id"]
        # Collect all sibling job IDs + CVE IDs for this product
        if product_name:
            cur.execute(
                """SELECT j.id, j.cve_id FROM cve_patch_job j
                   WHERE j.agent_id=%s AND j.status='queued'
                     AND j.cve_id IN (
                         SELECT cve_id FROM device_vulnerability
                         WHERE product_name=%s AND asset_id=%s
                     )""",
                (agent_id, product_name, asset_id_j)
            )
        else:
            cur.execute(
                """SELECT id, cve_id FROM cve_patch_job
                   WHERE agent_id=%s AND status='queued' AND id=%s""",
                (agent_id, rep_id)
            )
        sibling_rows = cur.fetchall()
        all_cves    = list({r["cve_id"] for r in sibling_rows})
        sibling_ids = [r["id"] for r in sibling_rows]
        job_id_out  = sibling_ids[0] if sibling_ids else rep_id
        payload_out = json.dumps({
            "type":         "install_cve_patches",
            "job_id":       job_id_out,
            "cve_ids":      all_cves,
            "product_name": product_name,
            "asset_id":     asset_id_j,
        })
        # Mark as deploying and commit BEFORE awaiting the send so the connection
        # is returned to the pool before the event loop yields to other coroutines.
        if sibling_ids:
            cur.execute(
                "UPDATE cve_patch_job SET status='deploying', updated_at=NOW() WHERE id = ANY(%s)",
                (sibling_ids,)
            )
        conn.commit()
        cur.close(); conn.close()
        await websocket.send_text(payload_out)
        print(f"[gw] dispatched product='{product_name}' {len(all_cves)} CVEs to {agent_id}", flush=True)
        return True
    except Exception as _e:
        print(f"[gw] dispatch-next error for {agent_id}: {_e}", flush=True)
        return False


# --- Reconnect-triggered remediation delivery -------------------------------
# Roaming laptops are rarely online in any given push window, so a deploy aimed
# at an offline/asleep agent used to be marked 'deploying' optimistically and
# never delivered (stuck forever). Instead we leave such work 'queued' and flush
# it over the live WS the moment the agent reconnects — mirroring the proven
# cve_patch_job queue/flush model (see _dispatch_next_product). We mark a row
# 'deploying' ONLY after a confirmed WS send; terminal status comes from the
# agent's result message (patch_install_result / script_result).

# Cap how many jobs we push per connect so a box waking up isn't hammered with a
# wall of installs. The agent runs OS patches serially anyway (one product at a
# time), so this primarily bounds the run_script remediations.
_REMEDIATION_FLUSH_BATCH = 5

# Debounce: an agent on a flaky link can reconnect repeatedly within seconds.
# Suppress repeat flushes within this window (per agent_id) so we don't spam.
_FLUSH_DEBOUNCE_S = 30
_last_flush_at: Dict[str, float] = {}

# Retry cap: a job picked up but never finished (agent sleeps mid-run) cycles
# deploying -> stale-reset -> queued -> re-flush. Without a cap that loops forever.
# Once a row's dispatch `attempts` exceeds this, abandon it (terminal) instead of
# redelivering. The per-agent debounce + batch cap above is also bumped by this.
_REMEDIATION_MAX_ATTEMPTS = 3

# Reconnect-storm throttle: the per-agent 30s debounce above is per-process and
# in-memory, so a rmm-gateway restart drops it and every reconnecting agent (the
# whole fleet, ~64 boxes) would flush at once — a thundering herd of installs.
# Two cheap global guards bound the dispatch rate without changing per-agent logic:
#   1) a small randomized jitter sleep before each agent's flush, and
#   2) a process-wide semaphore capping how many agents flush concurrently.
_FLUSH_JITTER_MAX_S = 4.0          # 0..N s randomized pre-flush sleep
_FLUSH_MAX_CONCURRENCY = 4         # at most this many agents flushing at once
# Created lazily so it binds to the running event loop, not import time.
_flush_semaphore: "asyncio.Semaphore | None" = None


def _get_flush_semaphore() -> asyncio.Semaphore:
    global _flush_semaphore
    if _flush_semaphore is None:
        _flush_semaphore = asyncio.Semaphore(_FLUSH_MAX_CONCURRENCY)
    return _flush_semaphore


async def _flush_os_patch_jobs(websocket, agent_id: str) -> int:
    """Deliver queued OS Windows-Update jobs (rmm_patch_job, status='queued') to a
    just-connected agent. Marks each 'deploying' ONLY after a confirmed send.
    Idempotent: never touches terminal rows (completed/no_op/failed).

    Dedup: re-queued rows can pile up with the SAME update_ids set (e.g. SARA-LENOVO
    had 5 identical jobs), which would run the identical WUA install N times on one
    reconnect. Before dispatch we collapse the batch by (agent_id, update_ids): send
    ONE job per distinct update set and mark the rest 'superseded' (terminal).

    Retry cap: each dispatch bumps `attempts`; a job that has already been dispatched
    too many times (cap exceeded) is moved to terminal 'abandoned' rather than
    redelivered forever. Returns the number of jobs actually dispatched."""
    dispatched = 0
    try:
        conn = get_conn()
        cur = get_cursor(conn)
        cur.execute(
            """SELECT id, update_ids, kb_ids, titles, COALESCE(attempts, 0) AS attempts
                 FROM rmm_patch_job
                WHERE agent_id=%s AND status='queued'
                ORDER BY id ASC
                LIMIT %s""",
            (agent_id, _REMEDIATION_FLUSH_BATCH),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Collapse duplicates by the raw update_ids text (the persisted JSON string is
        # stable for identical sets — see SARA's 5 rows). Keep the first (lowest id),
        # mark the rest 'superseded' so they are never re-flushed.
        seen_update_ids = set()
        for row in rows:
            key = row["update_ids"] or "[]"
            if key in seen_update_ids:
                _c = get_conn(); _cur = get_cursor(_c)
                _cur.execute(
                    "UPDATE rmm_patch_job SET status='superseded', updated_at=NOW() "
                    "WHERE id=%s AND status='queued'",
                    (row["id"],),
                )
                _c.commit(); _cur.close(); _c.close()
                print(f"[gw] os-patch flush: superseded duplicate job={row['id']} {agent_id} (same update_ids)", flush=True)
                continue
            seen_update_ids.add(key)

            # Retry cap: abandon a job that has been dispatched too many times.
            if row["attempts"] >= _REMEDIATION_MAX_ATTEMPTS:
                _c = get_conn(); _cur = get_cursor(_c)
                _cur.execute(
                    "UPDATE rmm_patch_job SET status='abandoned', updated_at=NOW() "
                    "WHERE id=%s AND status='queued'",
                    (row["id"],),
                )
                _c.commit(); _cur.close(); _c.close()
                print(f"[gw] os-patch flush: abandoned job={row['id']} {agent_id} "
                      f"after {row['attempts']} attempts (cap {_REMEDIATION_MAX_ATTEMPTS})", flush=True)
                continue

            payload = json.dumps({
                "type":       "install_patches",
                "job_id":     row["id"],
                "update_ids": json.loads(row["update_ids"] or "[]"),
                "kb_ids":     json.loads(row["kb_ids"] or "[]"),
                "titles":     json.loads(row["titles"] or "[]"),
            })
            try:
                await websocket.send_text(payload)
            except Exception as _se:
                # Send failed — leave the job 'queued' for the next reconnect.
                print(f"[gw] os-patch flush send failed job={row['id']} {agent_id}: {_se}", flush=True)
                break
            # Confirmed send → mark deploying + bump attempts (guarded so a concurrent
            # result can't be clobbered).
            _c = get_conn(); _cur = get_cursor(_c)
            _cur.execute(
                "UPDATE rmm_patch_job "
                "SET status='deploying', attempts=COALESCE(attempts,0)+1, "
                "    deployed_at=NOW(), updated_at=NOW() "
                "WHERE id=%s AND status='queued'",
                (row["id"],),
            )
            _c.commit(); _cur.close(); _c.close()
            dispatched += 1
        if dispatched:
            print(f"[gw] flushed {dispatched} queued OS patch job(s) to {agent_id}", flush=True)
    except Exception as _e:
        print(f"[gw] os-patch flush error for {agent_id}: {_e}", flush=True)
    return dispatched


async def _flush_remediation_queue(websocket, agent_id: str) -> int:
    """Deliver queued general remediation actions (rmm_remediation_queue,
    status='queued') to a just-connected agent. Stamps a unique correlation
    session_id into the payload (the agent echoes it back in script_result, which
    the result handler uses to mark the row terminal). Marks 'deploying' ONLY after
    a confirmed send. Idempotent.

    Retry cap: each dispatch bumps `attempts`; a row that has already been dispatched
    too many times (cap exceeded) is moved to terminal 'abandoned' rather than
    redelivered forever (the deploying->stale-reset->queued->re-flush loop).
    Returns the number of actions dispatched."""
    dispatched = 0
    try:
        conn = get_conn()
        cur = get_cursor(conn)
        cur.execute(
            """SELECT id, action_type, payload, COALESCE(attempts, 0) AS attempts
                 FROM rmm_remediation_queue
                WHERE agent_id=%s AND status='queued'
                ORDER BY id ASC
                LIMIT %s""",
            (agent_id, _REMEDIATION_FLUSH_BATCH),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for row in rows:
            # Retry cap: abandon a row that has been dispatched too many times.
            if row["attempts"] >= _REMEDIATION_MAX_ATTEMPTS:
                _c = get_conn(); _cur = get_cursor(_c)
                _cur.execute(
                    "UPDATE rmm_remediation_queue SET status='abandoned', updated_at=NOW() "
                    "WHERE id=%s AND status='queued'",
                    (row["id"],),
                )
                _c.commit(); _cur.close(); _c.close()
                print(f"[gw] remediation flush: abandoned id={row['id']} {agent_id} "
                      f"after {row['attempts']} attempts (cap {_REMEDIATION_MAX_ATTEMPTS})", flush=True)
                continue
            try:
                body = json.loads(row["payload"] or "{}")
            except Exception:
                body = {}
            # Negative correlation id keyed off the queue row id so it can't collide
            # with real rmm_session ids (which are positive) used by live tech sessions.
            corr_session_id = -int(row["id"])
            body["session_id"] = corr_session_id
            body.setdefault("type", row["action_type"] or "run_script")
            try:
                await websocket.send_text(json.dumps(body))
            except Exception as _se:
                print(f"[gw] remediation flush send failed id={row['id']} {agent_id}: {_se}", flush=True)
                break
            _c = get_conn(); _cur = get_cursor(_c)
            _cur.execute(
                "UPDATE rmm_remediation_queue "
                "SET status='deploying', session_id=%s, attempts=COALESCE(attempts,0)+1, "
                "    deployed_at=NOW(), updated_at=NOW() "
                "WHERE id=%s AND status='queued'",
                (corr_session_id, row["id"]),
            )
            _c.commit(); _cur.close(); _c.close()
            dispatched += 1
        if dispatched:
            print(f"[gw] flushed {dispatched} queued remediation action(s) to {agent_id}", flush=True)
    except Exception as _e:
        print(f"[gw] remediation flush error for {agent_id}: {_e}", flush=True)
    return dispatched


async def _flush_queued_work(websocket, agent_id: str) -> None:
    """Single entry point for the reconnect flush. Debounced per agent so a flaky
    WS that reconnects rapidly doesn't trigger repeated bursts. Flushes OS patch
    jobs and general remediation actions, each capped to a small batch per connect."""
    now = _time.monotonic()
    last = _last_flush_at.get(agent_id)
    if last is not None and (now - last) < _FLUSH_DEBOUNCE_S:
        print(f"[gw] flush debounced for {agent_id} ({now - last:.0f}s since last)", flush=True)
        return
    _last_flush_at[agent_id] = now

    # Reconnect-storm throttle. The per-agent debounce above is in-memory, so a
    # gateway restart drops it and the whole fleet reconnects ~simultaneously. A
    # randomized jitter + a process-wide concurrency cap spread the dispatch out so
    # we don't fan a wall of installs to all agents at once (thundering herd).
    await asyncio.sleep(random.uniform(0, _FLUSH_JITTER_MAX_S))
    async with _get_flush_semaphore():
        await _flush_os_patch_jobs(websocket, agent_id)
        await _flush_remediation_queue(websocket, agent_id)


async def _stale_job_reset_loop():
    """Background task: reset deploying jobs older than 40 min back to queued."""
    while True:
        await asyncio.sleep(600)  # run every 10 minutes
        try:
            conn = get_conn()
            cur = get_cursor(conn)
            # Only reset deploying jobs for agents that are no longer online.
            # Skip agents seen in the last 10 min so active long-running WUA installs
            # are not interrupted.
            cur.execute(
                "UPDATE cve_patch_job SET status='queued', updated_at=NOW() "
                "WHERE status='deploying' AND updated_at < NOW() - INTERVAL '40 minutes' "
                "AND agent_id NOT IN ("
                "    SELECT agent_id FROM rmm_agent WHERE last_seen_at > NOW() - INTERVAL '10 minutes'"
                ")"
            )
            n = cur.rowcount
            # Same treatment for stuck remediation actions: a 'deploying' row that
            # never returned a script_result (agent slept mid-run / gateway restart)
            # goes BACK TO 'queued' so the next reconnect re-delivers it. NEVER touch
            # 'queued' rows here — they are waiting on the reconnect flush by design.
            cur.execute(
                "UPDATE rmm_remediation_queue SET status='queued', session_id=NULL, "
                "deployed_at=NULL, updated_at=NOW() "
                "WHERE status='deploying' AND updated_at < NOW() - INTERVAL '40 minutes' "
                "AND agent_id NOT IN ("
                "    SELECT agent_id FROM rmm_agent WHERE last_seen_at > NOW() - INTERVAL '10 minutes'"
                ")"
            )
            rn = cur.rowcount
            conn.commit()
            cur.close()
            conn.close()
            if n:
                print(f"[gw] stale-reset: {n} deploying jobs reset to queued", flush=True)
            if rn:
                print(f"[gw] stale-reset: {rn} deploying remediation actions reset to queued", flush=True)
        except Exception as _e:
            print(f"[gw] stale-reset error: {_e}", flush=True)


async def _failed_job_retry_loop():
    """Background task: every 2 hours, reset transient-failed CVE patch jobs back
    to queued for currently-online agents so they are retried automatically.
    Only resets jobs that have been failed for at least 2 hours to allow cool-down.

    NOTE: this cve_patch_job retry loop has NO attempt cap — a perpetually-failing
    job is reset every 2h indefinitely. Left as-is per review; add a per-job attempt
    cap (mirroring rmm_patch_job/rmm_remediation_queue.attempts) in a future pass."""
    await asyncio.sleep(120)  # initial delay
    while True:
        await asyncio.sleep(7200)  # every 2 hours
        connected = list(agents.keys())
        if not connected:
            continue
        try:
            conn = get_conn()
            cur = get_cursor(conn)
            cur.execute(
                """UPDATE cve_patch_job SET status='queued', updated_at=NOW()
                   WHERE status='failed'
                     AND agent_id = ANY(%s)
                     AND updated_at < NOW() - INTERVAL '2 hours'""",
                (connected,)
            )
            n = cur.rowcount
            conn.commit()
            cur.close()
            conn.close()
            if n:
                print(f"[gw] failed-retry: reset {n} failed jobs to queued for {len(connected)} online agents", flush=True)
                for aid in connected:
                    ws = agents.get(aid)
                    if ws:
                        try:
                            await _dispatch_next_product(ws, aid)
                        except Exception as _de:
                            print(f"[gw] failed-retry dispatch error for {aid}: {_de}", flush=True)
        except Exception as _e:
            print(f"[gw] failed-retry error: {_e}", flush=True)


async def _new_vuln_dispatch_loop():
    """Background task: every 5 minutes, create and dispatch CVE patch jobs for
    currently-connected agents that have open vulns with no existing job.
    This handles the case where Defender sync adds new vulnerabilities after
    an agent is already connected (on-connect auto-create doesn't catch these)."""
    await asyncio.sleep(30)  # short initial delay to let agents connect at startup
    while True:
        connected = list(agents.keys())
        if connected:
            try:
                conn = get_conn()
                cur = get_cursor(conn)
                # Bulk-insert missing jobs for all connected agents in one shot
                cur.execute(
                    """INSERT INTO cve_patch_job
                              (asset_id, agent_id, cve_id, status, deployed_by,
                               deployed_at, updated_at, created_at)
                       SELECT dv.asset_id, ra.agent_id, dv.cve_id,
                              'queued', 'auto', NOW(), NOW(), NOW()
                       FROM device_vulnerability dv
                       JOIN rmm_agent ra ON ra.asset_id = dv.asset_id
                       WHERE ra.agent_id = ANY(%s)
                         AND dv.status = 'Open'
                         AND NOT EXISTS (
                             SELECT 1 FROM cve_patch_job cpj
                             WHERE cpj.asset_id = dv.asset_id
                               AND cpj.cve_id = dv.cve_id
                         )""",
                    (connected,)
                )
                new_jobs = cur.rowcount
                conn.commit()
                cur.close(); conn.close()
                if new_jobs:
                    print(f"[gw] new-vuln-loop: created {new_jobs} patch jobs across {len(connected)} agents", flush=True)
                    # Dispatch to each agent that now has queued jobs
                    for aid in connected:
                        ws = agents.get(aid)
                        if ws:
                            try:
                                await _dispatch_next_product(ws, aid)
                            except Exception as _de:
                                print(f"[gw] new-vuln-loop dispatch error for {aid}: {_de}", flush=True)
            except Exception as _e:
                print(f"[gw] new-vuln-loop error: {_e}", flush=True)
        await asyncio.sleep(300)  # run every 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    task1 = asyncio.create_task(_stale_job_reset_loop())
    task2 = asyncio.create_task(_new_vuln_dispatch_loop())
    task3 = asyncio.create_task(_failed_job_retry_loop())
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()


app = FastAPI(title="Tracker RMM Gateway", lifespan=lifespan)

# In-memory connection maps
agents: Dict[str, WebSocket] = {}
agent_asset_ids: Dict[str, int] = {}
tech_sessions: Dict[int, WebSocket] = {}
# pending screenshot requests: session_id -> tech WebSocket
screenshot_pending: Dict[int, WebSocket] = {}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status/{agent_id}")
def agent_status(agent_id: str):
    online = agent_id in agents
    return JSONResponse({"agent_id": agent_id, "online": online})


@app.get("/agents")
def list_agents():
    return JSONResponse({"agents": list(agents.keys())})


@app.post("/eagle-eyes/{agent_id}/push")
async def push_eagle_config(agent_id: str, request: Request):
    """Flask calls this to push eagle_eyes_config to a connected agent."""
    # Invalidate the cached config FIRST (even if the agent isn't connected) so the
    # per-event/screenshot ingest gate re-reads the new enabled/screenshots_enabled
    # state immediately rather than waiting out the TTL.
    _invalidate_eagle_cache(agent_id)
    agent_ws = agents.get(agent_id)
    if not agent_ws:
        return JSONResponse({"ok": False, "error": "Agent not connected"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    try:
        await agent_ws.send_text(json.dumps({
            "type": "eagle_eyes_config",
            "enabled": bool(body.get("enabled", False)),
            "screenshot_interval_min": int(body.get("screenshot_interval_min", 30)),
            "screenshots_enabled": bool(body.get("screenshots_enabled", False)),
        }))
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/screenshot-request/{agent_id}")
async def screenshot_request(agent_id: str, request: Request):
    """HTTP endpoint for Flask to trigger a screenshot from a connected agent."""
    agent_ws = agents.get(agent_id)
    if not agent_ws:
        return JSONResponse({"ok": False, "error": "Agent not connected"}, status_code=404)
    try:
        await agent_ws.send_text(json.dumps({"type": "screenshot_request", "session_id": 0}))
        return JSONResponse({"ok": True, "message": "Screenshot requested"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/send-msg/{agent_id}")
async def send_msg(agent_id: str, request: Request):
    """Flask calls this to forward a JSON command to a connected agent."""
    agent_ws = agents.get(agent_id)
    if not agent_ws:
        return JSONResponse({"ok": False, "error": "Agent not connected"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    try:
        await agent_ws.send_text(json.dumps(body))
        session_id = body.get("session_id")
        if session_id:
            try:
                log_rmm_event(int(session_id), "tech", body.get("type") or "cmd", body)
            except Exception:
                pass
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/remediation/{agent_id}/enqueue")
async def enqueue_remediation(agent_id: str, request: Request):
    """Flask calls this to queue a general remediation action (e.g. a winget
    run_script) for an agent. ALWAYS persists the action to rmm_remediation_queue
    first. If the agent is live on the gateway right now, dispatch immediately and
    mark 'deploying' (confirmed send); otherwise leave it 'queued' for the reconnect
    flush to deliver. Never marks 'deploying' into the void.

    Body JSON: { "action_type": "run_script", "payload": {...message body...},
                 "asset_id": <int|null>, "created_by": <int|null> }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    action_type = body.get("action_type") or "run_script"
    msg_body = body.get("payload") or {}
    if not isinstance(msg_body, dict):
        return JSONResponse({"ok": False, "error": "payload must be an object"}, status_code=400)
    asset_id = body.get("asset_id")
    created_by = body.get("created_by")
    # Optional: originating ticket. When set, the script_result posts a ticket
    # note (e.g. the disk-space diagnostic attaches its output to the disk ticket).
    ticket_id = body.get("ticket_id")

    # 0) Canonicalize agent_id. The {agent_id} URL segment is whatever the caller
    #    passed — often the asset NAME or hostname, or wrong case. Renamed/repurposed
    #    boxes keep their original enrollment agent_id while the asset name changed
    #    (e.g. asset "Ken-Lenovo" -> agent_id "KEN-DELL"; "ChrisHome" -> "CHRISHOME").
    #    Both the INSERT and the live-push (agents.get) match agent_id EXACTLY, so a
    #    mismatched identifier silently strands the command as an undeliverable
    #    'queued' orphan that looks like a dead command channel. Resolve the TRUE
    #    rmm_agent.agent_id — prefer the asset_id in the body, else a case-insensitive
    #    match on the passed value — so mis-addressing can't happen again.
    try:
        _c = get_conn(); _cu = get_cursor(_c)
        _canon = None
        if asset_id is not None:
            _cu.execute(
                "SELECT agent_id FROM rmm_agent WHERE asset_id=%s "
                "ORDER BY last_seen_at DESC NULLS LAST LIMIT 1",
                (asset_id,),
            )
            _r = _cu.fetchone()
            if _r:
                _canon = _r["agent_id"]
        if _canon is None and agent_id:
            _cu.execute(
                "SELECT agent_id FROM rmm_agent WHERE lower(agent_id)=lower(%s) LIMIT 1",
                (agent_id,),
            )
            _r = _cu.fetchone()
            if _r:
                _canon = _r["agent_id"]
        _cu.close(); _c.close()
        if _canon and _canon != agent_id:
            print(f"[gw] enqueue: canonicalized agent_id {agent_id!r} -> {_canon!r} "
                  f"(asset_id={asset_id})", flush=True)
            agent_id = _canon
    except Exception as _e:
        print(f"[gw] enqueue: agent_id canonicalize failed for {agent_id!r} "
              f"(asset_id={asset_id}): {_e}", flush=True)

    # 1) Persist as queued (durable — survives if the agent is offline).
    try:
        conn = get_conn(); cur = get_cursor(conn)
        cur.execute(
            """INSERT INTO rmm_remediation_queue
                   (agent_id, asset_id, action_type, payload, status, created_by, ticket_id)
               VALUES (%s, %s, %s, %s, 'queued', %s, %s)
               RETURNING id""",
            (agent_id, asset_id, action_type, json.dumps(msg_body), created_by, ticket_id),
        )
        rq_id = cur.fetchone()["id"]
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"enqueue failed: {e}"}, status_code=500)

    # 2) Live-ness is determined by the in-memory connection map (a real open WS),
    #    NOT rmm_agent.last_seen_at. If not live, leave it queued for reconnect flush.
    agent_ws = agents.get(agent_id)
    if not agent_ws:
        return JSONResponse({"ok": True, "id": rq_id, "status": "queued", "delivered": False})

    # 3) Live → dispatch now with a correlation session_id; mark deploying on confirmed send.
    corr_session_id = -int(rq_id)
    out = dict(msg_body)
    out["session_id"] = corr_session_id
    out.setdefault("type", action_type)
    try:
        await agent_ws.send_text(json.dumps(out))
    except Exception as e:
        # Send failed — leave it queued; the reconnect flush will retry.
        return JSONResponse({"ok": True, "id": rq_id, "status": "queued", "delivered": False,
                             "error": str(e)})
    try:
        conn = get_conn(); cur = get_cursor(conn)
        cur.execute(
            "UPDATE rmm_remediation_queue "
            "SET status='deploying', session_id=%s, deployed_at=NOW(), updated_at=NOW() "
            "WHERE id=%s AND status='queued'",
            (corr_session_id, rq_id),
        )
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[gw] enqueue mark-deploying error id={rq_id}: {e}", flush=True)
    return JSONResponse({"ok": True, "id": rq_id, "status": "deploying", "delivered": True})


@app.get("/telemetry/{agent_id}")
def get_telemetry(agent_id: str):
    from .db import get_latest_telemetry as _gt
    t = _gt(agent_id)
    if not t:
        return JSONResponse({"ok": False, "error": "No telemetry"}, status_code=404)
    return JSONResponse({"ok": True, "telemetry": t})


@app.get("/screenshot/{agent_id}/latest")
def get_screenshot(agent_id: str):
    from .db import get_latest_screenshot as _gs
    s = _gs(agent_id)
    if not s:
        return JSONResponse({"ok": False, "error": "No screenshot"}, status_code=404)
    return JSONResponse({"ok": True, "screenshot": s})


@app.websocket("/ws/agent/{agent_id}")
async def ws_agent(websocket: WebSocket, agent_id: str, token: str):
    await websocket.accept()

    validation = validate_agent(agent_id=agent_id, token=token)
    if not validation["valid"]:
        await websocket.send_text(json.dumps({"type": "error", "error": validation["error"]}))
        await websocket.close(code=4401)
        return

    agents[agent_id] = websocket
    asset_id = int(validation["asset_id"]) if validation.get("asset_id") is not None else 0
    agent_asset_ids[agent_id] = asset_id

    try:
        await websocket.send_text(json.dumps({"type": "hello", "agent_id": agent_id}))

        # Push per-agent behaviour flags (disable_rustdesk, disable_tray, etc.)
        try:
            flags = get_agent_flags(agent_id)
            await websocket.send_text(json.dumps({"type": "agent_config", **flags}))
            print(f"[gw] pushed agent_config to {agent_id}: {flags}", flush=True)
        except Exception as _e:
            print(f"[gw] agent_config push error: {_e}", flush=True)

        # Auto-push eagle config on (re)connect so monitoring survives gateway restarts.
        # Push the CURRENT stored config REGARDLESS of enabled: an agent that was enabled,
        # then disabled while offline, must receive enabled=false on reconnect so it
        # cancels its in-memory monitor loop (agent handler stops the loop on enabled=false).
        # Refresh the cache so the per-event gate reflects current state immediately.
        try:
            _invalidate_eagle_cache(agent_id)
            ecfg = get_eagle_config(agent_id)
            await websocket.send_text(json.dumps({
                "type": "eagle_eyes_config",
                "enabled": bool(ecfg.get("enabled", False)),
                "screenshot_interval_min": ecfg.get("screenshot_interval_min", 30),
                "screenshots_enabled": bool(ecfg.get("screenshots_enabled", False)),
            }))
            print(f"[gw] pushed eagle_eyes_config to {agent_id} on connect (enabled={ecfg.get('enabled')})", flush=True)
        except Exception as _e:
            print(f"[gw] eagle auto-push error: {_e}", flush=True)

        # Dispatch any queued CVE patch jobs for this agent on (re)connect.
        # First reset this agent's own stale deploying jobs so they're picked up below.
        try:
            _conn = get_conn()
            _cur = get_cursor(_conn)
            # On reconnect, reset ALL deploying jobs for this agent regardless of age.
            # This ensures re-dispatch of any jobs that were in-flight when the agent
            # disconnected or the gateway restarted.
            _cur.execute(
                "UPDATE cve_patch_job SET status='queued', updated_at=NOW() "
                "WHERE agent_id=%s AND status='deploying'",
                (agent_id,)
            )
            _stale_n = _cur.rowcount
            if _stale_n:
                print(f"[gw] reset {_stale_n} stale deploying jobs for {agent_id}", flush=True)
            # Same for OS Windows-Update jobs (rmm_patch_job): re-queue this agent's
            # 'deploying' rows that never returned a result so the flush below
            # re-delivers them. Guard with a 5-min grace + completed_at IS NULL so a
            # job actively installing on an agent that briefly flapped is NOT yanked
            # out from under an in-progress WUA run. This is what was leaving roaming
            # laptops (e.g. SARA-LENOVO) stuck 'deploying' forever.
            #
            # Retry cap: a job that has already been dispatched too many times and
            # still never finished is perpetually stuck — abandon it (terminal)
            # instead of re-queueing it forever. Do this FIRST so the re-queue below
            # never picks up an over-cap row.
            _cur.execute(
                "UPDATE rmm_patch_job SET status='abandoned', updated_at=NOW() "
                "WHERE agent_id=%s AND status='deploying' AND completed_at IS NULL "
                "AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL '5 minutes') "
                "AND COALESCE(attempts,0) >= %s",
                (agent_id, _REMEDIATION_MAX_ATTEMPTS)
            )
            _abandoned_os = _cur.rowcount
            if _abandoned_os:
                print(f"[gw] abandoned {_abandoned_os} perpetually-stuck OS patch job(s) for "
                      f"{agent_id} (>= {_REMEDIATION_MAX_ATTEMPTS} attempts)", flush=True)
            _cur.execute(
                "UPDATE rmm_patch_job SET status='queued', updated_at=NOW() "
                "WHERE agent_id=%s AND status='deploying' AND completed_at IS NULL "
                "AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL '5 minutes') "
                "AND COALESCE(attempts,0) < %s",
                (agent_id, _REMEDIATION_MAX_ATTEMPTS)
            )
            _stale_os = _cur.rowcount
            if _stale_os:
                print(f"[gw] re-queued {_stale_os} stale deploying OS patch job(s) for {agent_id}", flush=True)
            _conn.commit()
            _cur.close(); _conn.close()  # return to pool before next operation
        except Exception as _e:
            print(f"[gw] stale-reset on-connect error for {agent_id}: {_e}", flush=True)

        # Auto-create queued patch jobs for any open exposure that has no job yet.
        try:
            _conn = get_conn()  # fresh connection — previous one was closed above
            _cur = get_cursor(_conn)
            _cur.execute(
                """INSERT INTO cve_patch_job
                          (asset_id, agent_id, cve_id, status, deployed_by,
                           deployed_at, updated_at, created_at)
                   SELECT dv.asset_id, ra.agent_id, dv.cve_id,
                          'queued', 'auto', NOW(), NOW(), NOW()
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id
                   WHERE ra.agent_id = %s
                     AND dv.status = 'Open'
                     AND NOT EXISTS (
                         SELECT 1 FROM cve_patch_job cpj
                         WHERE cpj.asset_id = dv.asset_id
                           AND cpj.cve_id = dv.cve_id
                     )""",
                (agent_id,)
            )
            _new_jobs = _cur.rowcount
            _conn.commit()
            if _new_jobs:
                print(f"[gw] auto-created {_new_jobs} queued patch jobs for {agent_id}", flush=True)
            _cur.close(); _conn.close()  # return to pool before dispatch
        except Exception as _e:
            print(f"[gw] auto-create job error for {agent_id}: {_e}", flush=True)
            try:
                _conn.rollback()
                _conn.close()
            except Exception:
                pass

        try:
            # Dispatch ONE product at a time (fewest CVEs first) to prevent
            # the agent from being overwhelmed with concurrent WUA/winget runs.
            await _dispatch_next_product(websocket, agent_id)
        except Exception as _e:
            print(f"[gw] queued-job dispatch error for {agent_id}: {_e}", flush=True)

        # Reconnect-triggered remediation delivery: flush queued OS patch jobs and
        # general remediation actions for this agent now that its WS is live.
        # Debounced + small-batch capped inside _flush_queued_work. Each item is
        # marked 'deploying' only on a confirmed send; terminal status comes back
        # via patch_install_result / script_result.
        try:
            await _flush_queued_work(websocket, agent_id)
        except Exception as _e:
            print(f"[gw] reconnect flush error for {agent_id}: {_e}", flush=True)

        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
            except Exception:
                continue

            msg_type = payload.get("type")
            if msg_type == "pong":
                continue

            # --- Store telemetry ---
            if msg_type in ("agent_info", "telemetry_update"):
                # Always-on HR work-hours meter (independent of Eagle Eyes). The agent
                # stamps wh_* running daily totals onto every telemetry_update; a
                # local-day rollover also sends a work-hours-ONLY packet (no core
                # telemetry) to record the closed prior day's final total. Detect that
                # so we don't clobber the rich rmm_telemetry row with an empty payload.
                if msg_type == "telemetry_update" and payload.get("wh_local_date"):
                    try:
                        store_work_hours(agent_id, asset_id, payload)
                    except Exception as e:
                        print(f"[gw] store_work_hours error: {e}", flush=True)
                # A work-hours-only rollover packet carries no core telemetry (no
                # hostname/cpu) — skip the telemetry upsert for it.
                wh_only = (
                    msg_type == "telemetry_update"
                    and payload.get("wh_local_date")
                    and payload.get("cpu_percent") is None
                    and not payload.get("hostname")
                )
                if not wh_only:
                    try:
                        store_telemetry(agent_id, asset_id, payload)
                    except Exception as e:
                        print(f"[gw] store_telemetry error: {e}", flush=True)
                continue

            # --- Eagle Eyes: window focus event ---
            if msg_type == "eagle_event":
                # Server-side enforcement: a DISABLED device must never store events,
                # regardless of what a stale/old agent still streams. Gated on `enabled`
                # only (NOT screenshots_enabled — CHRIS-DESKTOP has screenshots off but
                # events must keep flowing). Uses the short TTL cache to avoid hammering
                # the DB on every event. Fails CLOSED on lookup error.
                try:
                    if not get_eagle_config_cached(agent_id).get("enabled", False):
                        print(f"[gw] dropped eagle_event for {agent_id}: enabled=False", flush=True)
                        continue
                except Exception as e:
                    print(f"[gw] eagle_event gate error for {agent_id} (dropping): {e}", flush=True)
                    continue
                print(f"[gw] eagle_event from {agent_id}: {payload.get('process')} | {payload.get('title')}", flush=True)
                try:
                    store_eagle_event(
                        agent_id,
                        payload.get("captured_at") or "",
                        payload.get("process") or "",
                        payload.get("title") or "",
                        int(payload.get("duration_s") or 0),
                        idle_s=int(payload.get("idle_s") or 0),
                        is_idle=bool(payload.get("is_idle", False)),
                    )
                except Exception as e:
                    print(f"[gw] store_eagle_event error: {e}", flush=True)
                continue

            # --- Eagle Eyes: periodic screenshot ---
            if msg_type == "eagle_screenshot":
                # Defense in depth: drop the shot if monitoring is OFF (enabled=false)
                # OR screenshots are disabled for this agent server-side, regardless of
                # what a stale agent still uploads. Fails CLOSED — a missing/None flag is
                # treated as false (don't store). Uses the short TTL cache.
                try:
                    _ecfg = get_eagle_config_cached(agent_id)
                    if not _ecfg.get("enabled", False):
                        print(f"[gw] dropped eagle_screenshot for {agent_id}: enabled=False", flush=True)
                        continue
                    if not _ecfg.get("screenshots_enabled", False):
                        print(f"[gw] dropped eagle_screenshot for {agent_id}: screenshots_enabled=False", flush=True)
                        continue
                except Exception as e:
                    print(f"[gw] eagle_screenshot gate error for {agent_id} (dropping): {e}", flush=True)
                    continue
                if payload.get("data"):
                    try:
                        store_screenshot(
                            agent_id, None,
                            payload["data"],
                            payload.get("width", 0),
                            payload.get("height", 0),
                            payload.get("format", "jpeg"),
                            source="eagle",
                        )
                    except Exception as e:
                        print(f"[gw] eagle_screenshot store error: {e}", flush=True)
                continue

            # --- Store installed patches ---
            if msg_type == "patch_report":
                patches = payload.get("patches") or []
                try:
                    store_patches(agent_id, patches)
                    print(f"[gw] stored {len(patches)} patches for {agent_id}", flush=True)
                except Exception as e:
                    print(f"[gw] store_patches error: {e}", flush=True)
                continue

            # --- CVE patch job result ---
            if msg_type == "cve_patch_result":
                job_id = payload.get("job_id")
                result = payload.get("result") or {}
                if job_id:
                    try:
                        conn = get_conn()
                        cur  = get_cursor(conn)
                        error     = result.get("error") or ""
                        installed = int(result.get("installed") or 0)
                        found     = int(result.get("updates_found") or 0)
                        # Transient failures (timeout, download error, network) must be
                        # 'failed' — NOT 'no_patch' — so CVEs are not falsely auto-closed.
                        _transient_fail = any(kw in error for kw in (
                            "timed out", "download failed", "timed_out",
                            "connection", "network", "exit code",
                        ))
                        if installed > 0 or error == "Already up to date":
                            new_status = "installed"
                        elif _transient_fail:
                            new_status = "failed"
                        elif found == 0 or "No pending patches" in error or "not found in installed" in error:
                            new_status = "no_patch"
                        elif error:
                            new_status = "failed"
                        else:
                            new_status = "installed"
                        # Fetch asset_id + cve_id before updating
                        cur.execute("SELECT asset_id, cve_id FROM cve_patch_job WHERE id=%s", (job_id,))
                        job_row = cur.fetchone()
                        cur.execute(
                            """UPDATE cve_patch_job
                               SET status=%s, result_json=%s, reboot_required=%s,
                                   updates_found=%s, completed_at=NOW(),
                                   updated_at=NOW()
                               WHERE id=%s""",
                            (new_status, json.dumps(result),
                             bool(result.get("reboot_required")),
                             result.get("installed", 0), job_id)
                        )
                        # Auto-close CVEs on success or no applicable patch.
                        # Close ALL CVEs for the same product on this asset so WUA cumulative
                        # patches (which cover many CVEs at once) are fully reflected.
                        product_name_j = None  # ensure always defined before bulk-close block
                        if new_status in ("installed", "no_patch") and job_row:
                            j_asset = job_row["asset_id"]
                            j_cve   = job_row["cve_id"]
                            if new_status == 'installed':
                                close_status = 'Remediated'
                                close_note   = 'RMM installed updates — pending Defender re-scan confirmation (specific KB not individually verified)'
                            else:
                                close_status = 'Exception'
                                close_note   = 'No automated patch found (no Windows Update KB or package manager update matched this CVE). Device remains exposed — manual update required.'
                            # Look up product_name for this CVE to close all related CVEs
                            cur.execute(
                                "SELECT product_name FROM device_vulnerability "
                                "WHERE cve_id=%s AND asset_id=%s LIMIT 1",
                                (j_cve, j_asset)
                            )
                            prod_row = cur.fetchone()
                            product_name_j = prod_row["product_name"] if prod_row else None
                            if product_name_j:
                                cur.execute(
                                    """UPDATE device_vulnerability
                                       SET status=%s, remediation_note=%s,
                                           updated_at=NOW()
                                       WHERE asset_id=%s AND product_name=%s AND status='Open'""",
                                    (close_status, close_note, j_asset, product_name_j)
                                )
                                affected = cur.rowcount
                                print(f"[gw] {close_status} {affected} CVE(s) for product='{product_name_j}' "
                                      f"asset={j_asset} reason={new_status}", flush=True)
                            else:
                                cur.execute(
                                    """UPDATE device_vulnerability
                                       SET status=%s, remediation_note=%s,
                                           updated_at=NOW()
                                       WHERE cve_id=%s AND asset_id=%s AND status='Open'""",
                                    (close_status, close_note, j_cve, j_asset)
                                )
                                print(f"[gw] {close_status} device_vulnerability cve={j_cve} asset={j_asset} reason={new_status}", flush=True)
                        # Bulk-close all sibling deploying jobs for the same agent+asset+product.
                        # Dispatch batches multiple CVE jobs into one product-level message;
                        # the agent returns one result covering all of them.
                        if job_row:
                            j_asset_b = job_row["asset_id"]
                            if product_name_j:
                                cur.execute(
                                    """UPDATE cve_patch_job
                                       SET status=%s, result_json=%s, reboot_required=%s,
                                           updates_found=%s, completed_at=NOW(), updated_at=NOW()
                                       WHERE agent_id=%s AND asset_id=%s
                                         AND status IN ('deploying','queued')
                                         AND id != %s
                                         AND cve_id IN (
                                             SELECT cve_id FROM device_vulnerability
                                             WHERE product_name=%s AND asset_id=%s
                                         )""",
                                    (new_status, json.dumps(result),
                                     bool(result.get("reboot_required")),
                                     result.get("installed", 0),
                                     agent_id, j_asset_b, job_id,
                                     product_name_j, j_asset_b)
                                )
                                bulk_n = cur.rowcount
                                if bulk_n:
                                    print(f"[gw] bulk-closed {bulk_n} sibling jobs for product='{product_name_j}' agent={agent_id}", flush=True)
                            else:
                                # No product — close sibling jobs matching same cve_id
                                cur.execute(
                                    """UPDATE cve_patch_job
                                       SET status=%s, result_json=%s, reboot_required=%s,
                                           updates_found=%s, completed_at=NOW(), updated_at=NOW()
                                       WHERE agent_id=%s AND asset_id=%s AND cve_id=%s
                                         AND status IN ('deploying','queued') AND id != %s""",
                                    (new_status, json.dumps(result),
                                     bool(result.get("reboot_required")),
                                     result.get("installed", 0),
                                     agent_id, j_asset_b, job_row["cve_id"], job_id)
                                )
                        conn.commit()
                        cur.close()
                        conn.close()
                        print(f"[gw] cve_patch_result job={job_id} status={new_status}", flush=True)
                        # Serial dispatch: send next queued product now that this one finished
                        await _dispatch_next_product(websocket, agent_id)
                    except Exception as e:
                        print(f"[gw] cve_patch_result DB error: {e}", flush=True)
                continue

            # --- Windows Update (rmm_patch_job) result ---
            if msg_type == "patch_install_result":
                job_id = payload.get("job_id")
                result = payload.get("result") or {}
                if job_id:
                    try:
                        conn = get_conn()
                        cur  = get_cursor(conn)
                        error     = result.get("error") or ""
                        installed = int(result.get("installed") or 0)
                        # No-op deploys (the agent found nothing to install) are NOT
                        # failures — they were polluting the 'failed' bucket (~2.9k rows).
                        # Mirror the cve_patch_result pattern: guard genuine transient
                        # failures FIRST so we never swallow a real error, then classify
                        # the no-op cases to a non-failed 'no_op' status.
                        _noop_errors = ("No matching pending updates", "No update IDs specified")
                        _transient_fail = any(kw in error for kw in (
                            "timed out", "download failed", "timed_out",
                            "connection", "network", "exit code", "No output from installer",
                        ))
                        if installed > 0:
                            new_status = "completed"
                        elif _transient_fail:
                            new_status = "failed"
                        elif error in _noop_errors:
                            new_status = "no_op"
                        elif error:
                            new_status = "failed"
                        else:
                            new_status = "completed"
                        cur.execute(
                            """UPDATE rmm_patch_job
                               SET status=%s, result_json=%s, reboot_required=%s,
                                   completed_at=NOW(), updated_at=NOW()
                               WHERE id=%s""",
                            (new_status, json.dumps(result),
                             bool(result.get("reboot_required")), job_id)
                        )
                        conn.commit()
                        cur.close()
                        conn.close()
                        print(f"[gw] patch_install_result job={job_id} status={new_status} installed={installed}", flush=True)
                    except Exception as e:
                        print(f"[gw] patch_install_result DB error: {e}", flush=True)
                continue

            # --- Remediation-queue result (run_script correlated by negative session_id) ---
            # The reconnect flush stamps a NEGATIVE session_id (= -queue_row_id) into the
            # run_script payload; the agent echoes it back here. A negative session_id can
            # never be a real rmm_session, so it unambiguously identifies a queued
            # remediation. Positive session_ids fall through to the live tech relay below.
            if msg_type == "script_result" and isinstance(payload.get("session_id"), int) \
                    and payload["session_id"] < 0:
                rq_id = -int(payload["session_id"])
                try:
                    exit_code = payload.get("exit_code")
                    stderr = (payload.get("stderr") or "")
                    if exit_code == 0:
                        rq_status = "completed"
                    elif exit_code is None:
                        # Missing/None exit code: we cannot confirm the remediation
                        # actually succeeded (e.g. a winget upgrade). Treat as failed
                        # rather than optimistically calling it completed.
                        rq_status = "failed"
                    elif exit_code == -1 and "Timed out" in stderr:
                        rq_status = "failed"
                    elif isinstance(exit_code, int) and exit_code != 0:
                        rq_status = "failed"
                    else:
                        rq_status = "completed"
                    conn = get_conn(); cur = get_cursor(conn)
                    # Only close a row that's actually deploying (idempotent — a duplicate
                    # result or a row already swept won't be reopened/reclassified).
                    cur.execute(
                        "UPDATE rmm_remediation_queue "
                        "SET status=%s, result_json=%s, completed_at=NOW(), updated_at=NOW() "
                        "WHERE id=%s AND status='deploying' "
                        "RETURNING ticket_id, action_type",
                        (rq_status, json.dumps(payload), rq_id),
                    )
                    _rrow = cur.fetchone()
                    n = cur.rowcount
                    conn.commit(); cur.close(); conn.close()
                    if n:
                        print(f"[gw] remediation result id={rq_id} status={rq_status} exit={exit_code}", flush=True)
                    # If this remediation was tied to a ticket (e.g. the disk
                    # diagnostic), attach the script output as a ticket note. Only
                    # the row we actually transitioned (n==1) posts — idempotent.
                    # INVARIANT (verified): a completed/failed row is TERMINAL and is
                    # never reset back to 'deploying'. Both stale-reset paths (the
                    # _stale_job_reset_loop and the per-agent reconnect flush) scope
                    # their UPDATE to WHERE status='deploying', so a re-delivered or
                    # replayed script_result for an already-terminal row matches 0
                    # rows here (n==0) and posts no duplicate note. Safe.
                    if n and _rrow and _rrow.get("ticket_id"):
                        try:
                            _stdout = (payload.get("stdout") or "").strip()
                            _stderr = (payload.get("stderr") or "").strip()
                            _body = _stdout or "(no output)"
                            if _stderr:
                                _body += f"\n\n[stderr]\n{_stderr}"
                            # Bound the note so a runaway script can't bloat the ticket.
                            if len(_body) > 12000:
                                _body = _body[:12000] + "\n…(truncated)"
                            _label = "Disk diagnostic" if _rrow.get("action_type") == "run_script" else "Remediation"
                            _note = (
                                f"[Auto] {_label} result (exit {exit_code}, "
                                f"remediation #{rq_id}):\n\n{_body}"
                            )
                            conn = get_conn(); cur = get_cursor(conn)
                            cur.execute(
                                "INSERT INTO ticket_note (ticket_id, user_id, content, created_at) "
                                "VALUES (%s, NULL, %s, NOW())",
                                (_rrow["ticket_id"], _note),
                            )
                            cur.execute(
                                "UPDATE support_ticket SET updated_at=NOW() WHERE id=%s",
                                (_rrow["ticket_id"],),
                            )
                            conn.commit(); cur.close(); conn.close()
                            print(f"[gw] attached diagnostic note to ticket #{_rrow['ticket_id']} (rq {rq_id})", flush=True)
                        except Exception as _ne:
                            print(f"[gw] ticket-note attach error rq={rq_id}: {_ne}", flush=True)
                except Exception as e:
                    print(f"[gw] remediation result DB error id={rq_id}: {e}", flush=True)
                continue

            # --- Store pending Windows Updates ---
            if msg_type == "pending_updates":
                updates = payload.get("updates") or []
                try:
                    store_pending_updates(agent_id, updates)
                    print(f"[gw] stored {len(updates)} pending updates for {agent_id}", flush=True)
                except Exception as e:
                    print(f"[gw] store_pending_updates error: {e}", flush=True)
                continue

            # --- Store installed-software inventory ---
            # The agent also POSTs this over direct HTTPS, but that fails on some
            # boxes (TeamViewer tv_x64.dll HTTP breakage / payload-timeout) leaving
            # them with 0 rows. Handling it here, over the WS channel that already
            # works, closes that gap (and keeps /licenses install counts accurate).
            if msg_type == "software_inventory":
                software = payload.get("software") or []
                try:
                    n = store_software(agent_id, software)
                    print(f"[gw] stored {n} software entries for {agent_id}", flush=True)
                except Exception as e:
                    print(f"[gw] store_software error: {e}", flush=True)
                continue

            # --- Store screenshot and relay ---
            if msg_type == "screenshot_response":
                ss_session_id = payload.get("session_id")
                if not payload.get("error") and payload.get("data"):
                    try:
                        store_screenshot(
                            agent_id,
                            None,
                            payload["data"],
                            payload.get("width", 0),
                            payload.get("height", 0),
                            payload.get("format", "jpeg"),
                        )
                    except Exception as e:
                        print(f"[gw] store_screenshot error: {e}", flush=True)
                # relay to waiting tech
                if ss_session_id is not None:
                    tech_ws = screenshot_pending.pop(int(ss_session_id), None) or tech_sessions.get(int(ss_session_id))
                    if tech_ws:
                        try:
                            await tech_ws.send_text(json.dumps(payload))
                        except Exception:
                            pass
                continue

            # If session_id is present, log as agent output/event
            session_id = payload.get("session_id")
            if session_id is not None:
                try:
                    log_rmm_event(int(session_id), "agent", msg_type or "message", payload)
                except Exception:
                    pass

            # Relay agent responses to the corresponding technician session.
            if session_id is not None:
                tech_ws = tech_sessions.get(int(session_id))
                if tech_ws:
                    try:
                        await tech_ws.send_text(json.dumps(payload))
                    except Exception:
                        pass

    except WebSocketDisconnect:
        pass
    finally:
        # best effort cleanup
        if agents.get(agent_id) is websocket:
            agents.pop(agent_id, None)
        agent_asset_ids.pop(agent_id, None)
        try:
            mark_agent_offline(agent_id)
        except Exception:
            pass


@app.websocket("/ws/tech/{agent_id}")
async def ws_tech(websocket: WebSocket, agent_id: str, api_key: str = "", session_token: str = "", reason: str = ""):
    await websocket.accept()

    if session_token:
        validation = validate_session_token(token=session_token, agent_id=agent_id)
    elif api_key:
        validation = validate_api_key(api_key=api_key, required_permission="rmm_connect")
    else:
        await websocket.send_text(json.dumps({"type": "error", "error": "No credentials provided"}))
        await websocket.close(code=4403)
        return

    if not validation["valid"]:
        await websocket.send_text(json.dumps({"type": "error", "error": validation["error"]}))
        await websocket.close(code=4403)
        return

    agent_ws = agents.get(agent_id)
    if not agent_ws:
        await websocket.send_text(json.dumps({"type": "error", "error": "Agent not connected"}))
        await websocket.close(code=4404)
        return

    asset_id = agent_asset_ids.get(agent_id)
    if not asset_id:
        asset_id = None
    session_id = create_rmm_session(asset_id=asset_id, started_by_user_id=validation.get("user_id"), reason=reason or "")
    tech_sessions[session_id] = websocket
    log_rmm_event(session_id, "tech", "session_start", {"agent_id": agent_id, "reason": reason or ""})

    try:
        await websocket.send_text(json.dumps({"type": "session", "session_id": session_id}))
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
            except Exception:
                continue

            payload["session_id"] = session_id
            log_rmm_event(session_id, "tech", payload.get("type") or "message", payload)

            await agent_ws.send_text(json.dumps(payload))

    except WebSocketDisconnect:
        pass
    finally:
        try:
            end_rmm_session(session_id)
            log_rmm_event(session_id, "tech", "session_end", {})
        except Exception:
            pass
        tech_sessions.pop(session_id, None)
