import json
from typing import Dict

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .db import (
    create_rmm_session,
    end_rmm_session,
    get_eagle_config,
    get_latest_screenshot,
    get_latest_telemetry,
    log_rmm_event,
    mark_agent_offline,
    store_eagle_event,
    store_screenshot,
    store_telemetry,
    validate_agent,
    validate_api_key,
    validate_session_token,
)

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
                    store_telemetry(agent_id, asset_id, payload)
                except Exception as e:
                    print(f"[gw] store_telemetry error: {e}", flush=True)
                continue

            # --- Eagle Eyes: window focus event ---
            if msg_type == "eagle_event":
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
