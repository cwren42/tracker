import json
from typing import Dict

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .db import (
    auto_link_or_create_asset,
    create_rmm_session,
    end_rmm_session,
    get_eagle_config,
    get_latest_screenshot,
    get_latest_telemetry,
    log_availability,
    log_rmm_event,
    store_eagle_event,
    store_patches,
    store_pending_updates,
    store_screenshot,
    store_session_events,
    store_software,
    store_telemetry,
    _utc_to_mst,
    update_patch_job,
    get_patch_job,
    get_session_reason,
    store_rustdesk_id,
    validate_agent,
    validate_api_key,
    validate_session_token,
)

import ipaddress


def _is_public_ip(ip: str) -> bool:
    """Return True only if ip is a routable public IPv4/IPv6 address."""
    try:
        addr = ipaddress.ip_address(ip)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_unspecified or addr.is_multicast)
    except ValueError:
        return False


app = FastAPI(title="Tracker RMM Gateway")

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


@app.post("/update-agent/{agent_id}")
async def push_update_now(agent_id: str):
    """Tell a connected agent to check for an update and restart if one is available."""
    ws = agents.get(agent_id)
    if not ws:
        return JSONResponse({"ok": False, "error": "Agent not connected"})
    try:
        await ws.send_text(json.dumps({"type": "update_now"}))
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/send-msg/{agent_id}")
async def send_msg(agent_id: str, request: Request):
    """Send an arbitrary JSON message to a connected agent (admin use)."""
    ws = agents.get(agent_id)
    if not ws:
        return JSONResponse({"ok": False, "error": "Agent not connected"})
    try:
        body = await request.json()
        await ws.send_text(json.dumps(body))
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/eagle-eyes/{agent_id}/push")
async def push_eagle_config(agent_id: str, request: Request):
    """Flask calls this to push eagle_eyes_config to a connected agent."""
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


@app.post("/deploy-patches/{agent_id}")
async def deploy_patches(agent_id: str, request: Request):
    """Flask calls this to push an approved patch job to a connected agent."""
    agent_ws = agents.get(agent_id)
    if not agent_ws:
        return JSONResponse({"ok": False, "error": "Agent not connected"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    job_id     = body.get("job_id")
    update_ids = body.get("update_ids") or []
    kb_ids     = body.get("kb_ids") or []
    titles     = body.get("titles") or []

    if not job_id or not update_ids:
        return JSONResponse({"ok": False, "error": "job_id and update_ids required"}, status_code=400)

    try:
        await agent_ws.send_text(json.dumps({
            "type":       "install_patches",
            "job_id":     job_id,
            "update_ids": update_ids,
            "kb_ids":     kb_ids,
            "titles":     titles,
        }))
        update_patch_job(int(job_id), status="deploying")
        return JSONResponse({"ok": True, "message": f"Deploy command sent for job {job_id}"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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

    # Capture public IP from WebSocket connection (X-Real-IP from nginx, or direct client host)
    client_ip = (
        websocket.headers.get("x-real-ip")
        or websocket.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (websocket.client.host if websocket.client else None)
    )

    try:
        log_availability(agent_id, "online")
        await websocket.send_text(json.dumps({"type": "hello", "agent_id": agent_id}))

        # Auto-push eagle config on (re)connect so monitoring survives gateway restarts
        try:
            ecfg = get_eagle_config(agent_id)
            if ecfg.get("enabled"):
                await websocket.send_text(json.dumps({
                    "type": "eagle_eyes_config",
                    "enabled": True,
                    "screenshot_interval_min": ecfg.get("screenshot_interval_min", 30),
                }))
                print(f"[gw] pushed eagle_eyes_config to {agent_id} on connect", flush=True)
        except Exception as _e:
            print(f"[gw] eagle auto-push error: {_e}", flush=True)

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
                try:
                    if client_ip and _is_public_ip(client_ip):
                        payload["public_ip"] = client_ip
                    # Auto-link or create asset if not yet linked
                    if asset_id == 0:
                        resolved = auto_link_or_create_asset(agent_id, payload)
                        if resolved:
                            asset_id = resolved
                            agent_asset_ids[agent_id] = asset_id
                    store_telemetry(agent_id, asset_id, payload)
                except Exception as e:
                    print(f"[gw] store_telemetry error: {e}", flush=True)
                continue

            # --- Patch report (installed KBs inventory) ---
            if msg_type == "patch_report":
                try:
                    store_patches(agent_id, payload.get("patches") or [])
                except Exception as e:
                    print(f"[gw] store_patches error: {e}", flush=True)
                continue

            # --- Pending / available Windows Updates ---
            if msg_type == "pending_updates":
                try:
                    store_pending_updates(agent_id, payload.get("updates") or [])
                except Exception as e:
                    print(f"[gw] store_pending_updates error: {e}", flush=True)
                continue

            # --- Software inventory ---
            if msg_type == "software_inventory":
                try:
                    n = store_software(agent_id, payload.get("software") or [])
                    print(f"[gw] stored {n} software entries", flush=True)
                except Exception as e:
                    print(f"[gw] store_software error: {e}", flush=True)
                continue

            # --- Software inventory ---
            if msg_type == "software_inventory":
                try:
                    n = store_software(agent_id, payload.get("software") or [])
                    print(f"[gw] stored {n} software entries", flush=True)
                except Exception as e:
                    print(f"[gw] store_software error: {e}", flush=True)
                continue

            # --- Session events (logon/logoff/lock/unlock/sleep/wake) ---
            if msg_type == "session_events":
                try:
                    n = store_session_events(agent_id, asset_id, payload.get("events") or [])
                    print(f"[gw] stored {n} new session event(s)", flush=True)
                except Exception as e:
                    print(f"[gw] store_session_events error: {e}", flush=True)
                continue

            # --- Result from an install_patches job ---
            if msg_type == "patch_install_result":
                job_id = payload.get("job_id")
                if job_id:
                    result = payload.get("result") or {}
                    success = result.get("installed", 0) > 0 and not result.get("error")
                    try:
                        update_patch_job(
                            int(job_id),
                            status="installed" if success else "failed",
                            result=result,
                            reboot_required=bool(result.get("reboot_required")),
                        )
                    except Exception as e:
                        print(f"[gw] update_patch_job error: {e}", flush=True)
                continue

            # --- Eagle Eyes: window focus event ---
            if msg_type == "eagle_event":
                print(f"[gw] eagle_event from {agent_id}: {payload.get('process')} | {payload.get('title')}", flush=True)
                try:
                    store_eagle_event(
                        agent_id,
                        _utc_to_mst(payload.get("captured_at") or ""),
                        payload.get("process") or "",
                        payload.get("title") or "",
                        int(payload.get("duration_s") or 0),
                    )
                except Exception as e:
                    print(f"[gw] store_eagle_event error: {e}", flush=True)
                continue

            # --- Eagle Eyes: periodic screenshot ---
            if msg_type == "eagle_screenshot":
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
                # Temporarily print script results so they appear in journalctl
                if msg_type == "script_result":
                    print(f"[gw] script_result sid={session_id} exit={payload.get('exit_code')} "
                          f"stdout={repr(payload.get('stdout','')[:500])} "
                          f"stderr={repr(payload.get('stderr','')[:200])}", flush=True)
                    # If this was a RustDesk deploy job, extract and store the peer ID
                    if payload.get('exit_code') == 0:
                        try:
                            import re as _re
                            reason = get_session_reason(int(session_id))
                            if reason == 'Deploy RustDesk':
                                m = _re.search(r'RUSTDESK_ID=(\S+)', payload.get('stdout', ''))
                                if m:
                                    store_rustdesk_id(agent_id, m.group(1))
                        except Exception as _e:
                            print(f"[gw] rustdesk_id store error: {_e}", flush=True)
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
            log_availability(agent_id, "offline")
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

    asset_id = agent_asset_ids.get(agent_id, 0)
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
