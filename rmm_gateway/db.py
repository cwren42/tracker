import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

PG_DSN = "dbname=tracker user=tracker_user password=tracker_secure_2026 host=localhost"


def get_conn() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    return conn


def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_api_key(api_key: str, required_permission: Optional[str] = None) -> Dict[str, Any]:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            SELECT id, user_id, permissions, rate_limit, expires_at, enabled
            FROM api_keys
            WHERE api_key = %s
            """,
            (api_key,),
        )
        row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Invalid API key"}
        if not row["enabled"]:
            return {"valid": False, "error": "API key disabled"}

        if row["expires_at"]:
            expires_at = row["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                return {"valid": False, "error": "API key expired"}

        perms = json.loads(row["permissions"]) if row["permissions"] else []
        if required_permission and required_permission not in perms:
            return {"valid": False, "error": "Insufficient permissions"}

        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        cur.execute(
            """
            SELECT COUNT(*) AS request_count
            FROM api_request_log
            WHERE api_key_id = %s AND created_at > %s
            """,
            (row["id"], one_hour_ago),
        )
        count = cur.fetchone()["count"]
        if row["rate_limit"] is not None and count >= row["rate_limit"]:
            return {"valid": False, "error": "Rate limit exceeded"}

        cur.execute(
            "UPDATE api_keys SET last_used = %s WHERE id = %s",
            (now_iso(), row["id"]),
        )
        conn.commit()

        return {"valid": True, "key_id": row["id"], "user_id": row["user_id"], "permissions": perms}
    finally:
        conn.close()


def validate_agent(agent_id: str, token: str) -> Dict[str, Any]:
    token_hash = sha256_hex(token)
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            SELECT id, asset_id, enabled
            FROM rmm_agent
            WHERE agent_id = %s AND agent_token_sha256 = %s
            """,
            (agent_id, token_hash),
        )
        row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Invalid agent credentials"}
        if not row["enabled"]:
            return {"valid": False, "error": "Agent disabled"}

        now = now_iso()
        cur.execute(
            "UPDATE rmm_agent SET last_seen_at = %s WHERE id = %s",
            (now, row["id"]),
        )
        # Also mark asset online
        if row["asset_id"]:
            cur.execute(
                "UPDATE asset SET online_state = 'Online', last_seen = %s WHERE id = %s",
                (now, row["asset_id"]),
            )
        conn.commit()
        return {"valid": True, "agent_db_id": row["id"], "asset_id": row["asset_id"]}
    finally:
        conn.close()


def mark_agent_offline(agent_id: str) -> None:
    """Called when an agent WebSocket disconnects."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """UPDATE asset SET online_state = 'Offline'
               WHERE id = (SELECT asset_id FROM rmm_agent WHERE agent_id = %s)""",
            (agent_id,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def create_rmm_session(asset_id: int, started_by_user_id: Optional[int], reason: str) -> int:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (asset_id, started_by_user_id, reason, now_iso()),
        )
        session_id = cur.fetchone()["id"]
        conn.commit()
        return int(session_id)
    finally:
        conn.close()


def end_rmm_session(session_id: int) -> None:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "UPDATE rmm_session SET ended_at = %s WHERE id = %s",
            (now_iso(), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def validate_session_token(token: str, agent_id: str) -> Dict[str, Any]:
    """Validate a short-lived connect token issued by Tracker."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            SELECT user_id, agent_id, expires_at, used
            FROM rmm_connect_token
            WHERE token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Invalid session token"}
        if row["used"]:
            return {"valid": False, "error": "Session token already used"}
        now = datetime.now(timezone.utc).isoformat()
        expires = row["expires_at"]
        if hasattr(expires, "isoformat"):
            expires = expires.isoformat()
        if now > str(expires):
            return {"valid": False, "error": "Session token expired"}
        if row["agent_id"] != agent_id:
            return {"valid": False, "error": "Token not valid for this agent"}
        # Mark as used
        cur.execute("UPDATE rmm_connect_token SET used = TRUE WHERE token = %s", (token,))
        conn.commit()
        return {"valid": True, "user_id": row["user_id"]}
    finally:
        conn.close()


def store_telemetry(agent_id: str, asset_id: int, data: Dict[str, Any]) -> None:
    """Upsert telemetry row from agent_info / telemetry_update messages."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        captured_at = data.get("captured_at") or now_iso()
        cur.execute(
            """
            INSERT INTO rmm_telemetry (
                agent_id, asset_id, hostname, os_name, os_version, os_build, os_arch,
                cpu_name, cpu_cores, cpu_percent,
                ram_total_gb, ram_available_gb, ram_percent,
                battery_present, battery_percent, battery_charging, battery_minutes_left,
                disk_json, network_json, logged_in_user, uptime_seconds,
                screen_resolution, domain, agent_version, captured_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (agent_id) DO UPDATE SET
                asset_id=EXCLUDED.asset_id,
                hostname=EXCLUDED.hostname,
                os_name=EXCLUDED.os_name, os_version=EXCLUDED.os_version,
                os_build=EXCLUDED.os_build, os_arch=EXCLUDED.os_arch,
                cpu_name=EXCLUDED.cpu_name, cpu_cores=EXCLUDED.cpu_cores, cpu_percent=EXCLUDED.cpu_percent,
                ram_total_gb=EXCLUDED.ram_total_gb, ram_available_gb=EXCLUDED.ram_available_gb,
                ram_percent=EXCLUDED.ram_percent,
                battery_present=EXCLUDED.battery_present, battery_percent=EXCLUDED.battery_percent,
                battery_charging=EXCLUDED.battery_charging, battery_minutes_left=EXCLUDED.battery_minutes_left,
                disk_json=EXCLUDED.disk_json, network_json=EXCLUDED.network_json,
                logged_in_user=EXCLUDED.logged_in_user, uptime_seconds=EXCLUDED.uptime_seconds,
                screen_resolution=EXCLUDED.screen_resolution, domain=EXCLUDED.domain,
                agent_version=EXCLUDED.agent_version, captured_at=EXCLUDED.captured_at
            """,
            (
                agent_id,
                asset_id,
                data.get("hostname", ""),
                data.get("os_name", ""),
                data.get("os_version", ""),
                data.get("os_build", ""),
                data.get("os_arch", ""),
                data.get("cpu_name", ""),
                data.get("cpu_cores", 0),
                data.get("cpu_percent", 0.0),
                data.get("ram_total_gb", 0.0),
                data.get("ram_available_gb", 0.0),
                data.get("ram_percent", 0.0),
                bool(data.get("battery_present")),
                data.get("battery_percent"),
                bool(data.get("battery_charging")),
                data.get("battery_minutes_left"),
                json.dumps(data.get("disks") or data.get("disk_json") or []),
                json.dumps(data.get("network") or data.get("network_json") or []),
                data.get("logged_in_user", ""),
                data.get("uptime_seconds", 0),
                data.get("screen_resolution", ""),
                data.get("domain", ""),
                data.get("agent_version", ""),
                captured_at,
            ),
        )
        # Also refresh last_seen_at and asset online_state on each telemetry update
        now = now_iso()
        cur.execute(
            "UPDATE rmm_agent SET last_seen_at = %s WHERE agent_id = %s",
            (now, agent_id),
        )
        if asset_id:
            cur.execute(
                "UPDATE asset SET online_state = 'Online', last_seen = %s WHERE id = %s",
                (now, asset_id),
            )
        conn.commit()
    finally:
        conn.close()


def store_screenshot(agent_id: str, user_id: Optional[int], b64: str, width: int, height: int, fmt: str, source: str = 'manual') -> int:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO rmm_screenshot (agent_id, requested_by_user_id, image_b64, image_format, width, height, captured_at, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (agent_id, user_id, b64, fmt, width, height, now_iso(), source),
        )
        row_id = cur.fetchone()["id"]
        conn.commit()
        return int(row_id)
    finally:
        conn.close()


def get_eagle_config(agent_id: str) -> dict:
    """Return eagle eyes config for an agent (defaults to disabled)."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT enabled, screenshot_interval_min FROM rmm_eagle_config WHERE agent_id = %s",
            (agent_id,)
        )
        row = cur.fetchone()
        if row:
            return {"enabled": bool(row["enabled"]), "screenshot_interval_min": int(row["screenshot_interval_min"])}
        return {"enabled": False, "screenshot_interval_min": 30}
    finally:
        conn.close()


def store_eagle_event(agent_id: str, captured_at_str: str, process_name: str, window_title: str,
                      duration_s: int, idle_s: int = 0, is_idle: bool = False) -> None:
    """Store a single Eagle Eyes window focus event and update rmm_eagle_current."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        # Tell Postgres to interpret incoming naive timestamps as America/Denver.
        # Agent sends naive local time ("2026-03-08T19:30:17") with no offset.
        cur.execute("SET LOCAL timezone = 'America/Denver'")
        ts_str = captured_at_str if captured_at_str else now_iso()

        cur.execute(
            """
            INSERT INTO rmm_eagle_event (agent_id, captured_at, process_name, window_title, duration_s, idle_s)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (agent_id, ts_str, process_name, window_title, duration_s, idle_s),
        )
        cur.execute(
            """
            INSERT INTO rmm_eagle_current (agent_id, process_name, window_title, idle_s, is_idle, captured_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_id) DO UPDATE SET
                process_name = excluded.process_name,
                window_title = excluded.window_title,
                idle_s       = excluded.idle_s,
                is_idle      = excluded.is_idle,
                captured_at  = excluded.captured_at
            """,
            (agent_id, process_name, window_title, idle_s, is_idle, ts_str),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_telemetry(agent_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT * FROM rmm_telemetry WHERE agent_id = %s", (agent_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["disk_json"] = json.loads(d.get("disk_json") or "[]")
        except Exception:
            d["disk_json"] = []
        try:
            d["network_json"] = json.loads(d.get("network_json") or "[]")
        except Exception:
            d["network_json"] = []
        return d
    finally:
        conn.close()


def get_latest_screenshot(agent_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT * FROM rmm_screenshot WHERE agent_id = %s ORDER BY id DESC LIMIT 1",
            (agent_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def log_rmm_event(session_id: int, actor_type: str, event_type: str, data: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO rmm_event (session_id, actor_type, event_type, data_json, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, actor_type, event_type, json.dumps(data), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
