import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

DB_PATH = "/var/www/tracker/assets.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


import os as _os, time as _time
_os.environ.setdefault('TZ', 'America/Denver')
_time.tzset()
def now_iso() -> str:
    """Return current MST time as ISO string."""
    return datetime.now().isoformat(timespec='seconds')
def _utc_to_mst(ts: str) -> str:
    """Shift a UTC ISO timestamp string to MST (UTC-7)."""
    if not ts: return ts
    try:
        return (datetime.fromisoformat(ts.replace('Z','')) - timedelta(hours=7)).isoformat(timespec='seconds')
    except Exception:
        return ts


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_api_key(api_key: str, required_permission: Optional[str] = None) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, user_id, permissions, rate_limit, expires_at, enabled
            FROM api_keys
            WHERE api_key = ?
            """,
            (api_key,),
        )
        row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Invalid API key"}
        if not row["enabled"]:
            return {"valid": False, "error": "API key disabled"}

        if row["expires_at"]:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.now() > expires_at:
                return {"valid": False, "error": "API key expired"}

        perms = json.loads(row["permissions"]) if row["permissions"] else []
        if required_permission and required_permission not in perms:
            return {"valid": False, "error": "Insufficient permissions"}

        one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        cur.execute(
            """
            SELECT COUNT(*) as request_count
            FROM api_request_log
            WHERE api_key_id = ? AND created_at > ?
            """,
            (row["id"], one_hour_ago),
        )
        count = cur.fetchone()["request_count"]
        if row["rate_limit"] is not None and count >= row["rate_limit"]:
            return {"valid": False, "error": "Rate limit exceeded"}

        cur.execute(
            "UPDATE api_keys SET last_used = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), row["id"]),
        )
        conn.commit()

        return {"valid": True, "key_id": row["id"], "user_id": row["user_id"], "permissions": perms}
    finally:
        conn.close()


def validate_agent(agent_id: str, token: str) -> Dict[str, Any]:
    token_hash = sha256_hex(token)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, asset_id, enabled
            FROM rmm_agent
            WHERE agent_id = ? AND agent_token_sha256 = ?
            """,
            (agent_id, token_hash),
        )
        row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Invalid agent credentials"}
        if not row["enabled"]:
            return {"valid": False, "error": "Agent disabled"}

        cur.execute(
            "UPDATE rmm_agent SET last_seen_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        conn.commit()
        return {"valid": True, "agent_db_id": row["id"], "asset_id": row["asset_id"]}
    finally:
        conn.close()


def create_rmm_session(asset_id: int, started_by_user_id: Optional[int], reason: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (asset_id, started_by_user_id, reason, now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def end_rmm_session(session_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE rmm_session SET ended_at = ? WHERE id = ?",
            (now_iso(), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def validate_session_token(token: str, agent_id: str) -> Dict[str, Any]:
    """Validate a short-lived connect token issued by Tracker."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT user_id, agent_id, expires_at, used
            FROM rmm_connect_token
            WHERE token = ?
            """,
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Invalid session token"}
        if row["used"]:
            return {"valid": False, "error": "Session token already used"}
        if datetime.now().isoformat() > row["expires_at"]:
            return {"valid": False, "error": "Session token expired"}
        if row["agent_id"] != agent_id:
            return {"valid": False, "error": "Token not valid for this agent"}
        # Mark as used
        cur.execute("UPDATE rmm_connect_token SET used = 1 WHERE token = ?", (token,))
        conn.commit()
        return {"valid": True, "user_id": row["user_id"]}
    finally:
        conn.close()


def store_telemetry(agent_id: str, asset_id: int, data: Dict[str, Any]) -> None:
    """Upsert telemetry row from agent_info / telemetry_update messages."""
    conn = get_conn()
    cur = conn.cursor()
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
                screen_resolution, domain, agent_version, captured_at,
                timezone, last_login_user, last_login_time,
                vendor, model_name, serial_number, motherboard,
                bios_manufacturer, bios_version, bios_date,
                gpu_json, sound_card, os_edition, security_json, public_ip, sysinfo_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                asset_id=excluded.asset_id,
                hostname=excluded.hostname,
                os_name=excluded.os_name, os_version=excluded.os_version,
                os_build=excluded.os_build, os_arch=excluded.os_arch,
                cpu_name=excluded.cpu_name, cpu_cores=excluded.cpu_cores, cpu_percent=excluded.cpu_percent,
                ram_total_gb=excluded.ram_total_gb, ram_available_gb=excluded.ram_available_gb,
                ram_percent=excluded.ram_percent,
                battery_present=excluded.battery_present, battery_percent=excluded.battery_percent,
                battery_charging=excluded.battery_charging, battery_minutes_left=excluded.battery_minutes_left,
                disk_json=excluded.disk_json, network_json=excluded.network_json,
                logged_in_user=excluded.logged_in_user, uptime_seconds=excluded.uptime_seconds,
                screen_resolution=excluded.screen_resolution, domain=excluded.domain,
                agent_version=excluded.agent_version, captured_at=excluded.captured_at,
                timezone=COALESCE(excluded.timezone, timezone),
                last_login_user=COALESCE(excluded.last_login_user, last_login_user),
                last_login_time=COALESCE(excluded.last_login_time, last_login_time),
                vendor=COALESCE(excluded.vendor, vendor),
                model_name=COALESCE(excluded.model_name, model_name),
                serial_number=COALESCE(excluded.serial_number, serial_number),
                motherboard=COALESCE(excluded.motherboard, motherboard),
                bios_manufacturer=COALESCE(excluded.bios_manufacturer, bios_manufacturer),
                bios_version=COALESCE(excluded.bios_version, bios_version),
                bios_date=COALESCE(excluded.bios_date, bios_date),
                gpu_json=COALESCE(excluded.gpu_json, gpu_json),
                sound_card=COALESCE(excluded.sound_card, sound_card),
                os_edition=COALESCE(excluded.os_edition, os_edition),
                security_json=COALESCE(excluded.security_json, security_json),
                public_ip=COALESCE(excluded.public_ip, public_ip),
                sysinfo_json=COALESCE(excluded.sysinfo_json, sysinfo_json)
            """,
            (
                agent_id, asset_id,
                data.get("hostname", ""),
                data.get("os_name", ""), data.get("os_version", ""),
                data.get("os_build", ""), data.get("os_arch", ""),
                data.get("cpu_name", ""), data.get("cpu_cores", 0), data.get("cpu_percent", 0.0),
                data.get("ram_total_gb", 0.0), data.get("ram_available_gb", 0.0), data.get("ram_percent", 0.0),
                1 if data.get("battery_present") else 0,
                data.get("battery_percent"),
                1 if data.get("battery_charging") else 0,
                data.get("battery_minutes_left"),
                json.dumps(data.get("disks") or data.get("disk_json") or []),
                json.dumps(data.get("network") or data.get("network_json") or []),
                data.get("logged_in_user", ""),
                data.get("uptime_seconds", 0),
                data.get("screen_resolution", ""),
                data.get("domain", ""),
                data.get("agent_version", ""),
                captured_at,
                data.get("timezone") or None,
                data["last_login_user"] if "last_login_user" in data else None,
                data["last_login_time"] if "last_login_time" in data else None,
                data.get("vendor") or None,
                data.get("model_name") or None,
                data.get("serial_number") or None,
                data.get("motherboard") or None,
                data.get("bios_manufacturer") or None,
                data.get("bios_version") or None,
                data.get("bios_date") or None,
                json.dumps(data.get("gpu") or []) if data.get("gpu") is not None else None,
                data.get("sound_card") or None,
                data.get("os_edition") or None,
                json.dumps(data.get("security") or {}) if data.get("security") is not None else None,
                data.get("public_ip") or None,
                json.dumps(data.get("sysinfo") or {}) if data.get("sysinfo") is not None else None,
            ),
        )
        # Also append a metrics history point
        cpu = data.get("cpu_percent")
        ram = data.get("ram_percent")
        if cpu is not None or ram is not None:
            cur.execute(
                "INSERT INTO rmm_metrics_history (agent_id, cpu_percent, ram_percent, captured_at) VALUES (?,?,?,?)",
                (agent_id, cpu, ram, captured_at),
            )
            # Prune history older than 30 days
            cur.execute(
                "DELETE FROM rmm_metrics_history WHERE agent_id=? AND captured_at < datetime('now','-30 days')",
                (agent_id,),
            )
        conn.commit()
    finally:
        conn.close()


def auto_link_or_create_asset(agent_id: str, data: Dict[str, Any]) -> int:
    """
    Ensure rmm_agent.asset_id is set. On each call:
      1. If rmm_agent already has asset_id → return it (already linked).
      2. Match asset table on serial_number → link & return.
      3. Match asset table on hostname (name) → link & return.
      4. Auto-create a new asset → link & return.
    Returns the resolved asset_id (> 0), or 0 on failure.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        # ── 1. Already linked? ────────────────────────────────────────────
        row = cur.execute(
            "SELECT asset_id FROM rmm_agent WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row and row["asset_id"]:
            return int(row["asset_id"])

        hostname   = (data.get("hostname") or "").strip()
        serial     = (data.get("serial_number") or "").strip()
        vendor     = (data.get("vendor") or "").strip()
        model      = (data.get("model_name") or "").strip()
        os_ver     = (data.get("os_name") or "").strip()

        asset_id = None

        # ── 2. Match on serial_number ─────────────────────────────────────
        if serial:
            r = cur.execute(
                "SELECT id FROM asset WHERE serial_number = ? LIMIT 1", (serial,)
            ).fetchone()
            if r:
                asset_id = r["id"]
                print(f"[gw] auto_link: matched {agent_id} → asset {asset_id} via serial '{serial}'", flush=True)

        # ── 3. Match on hostname → asset.name ─────────────────────────────
        if not asset_id and hostname:
            r = cur.execute(
                "SELECT id FROM asset WHERE LOWER(name) = LOWER(?) LIMIT 1", (hostname,)
            ).fetchone()
            if r:
                asset_id = r["id"]
                print(f"[gw] auto_link: matched {agent_id} → asset {asset_id} via hostname '{hostname}'", flush=True)

        # ── 4. Auto-create ────────────────────────────────────────────────
        if not asset_id:
            # Pick a unique asset_tag (prefer hostname, add suffix if taken)
            tag_base = hostname or agent_id
            tag = tag_base
            suffix = 1
            while cur.execute("SELECT id FROM asset WHERE asset_tag = ?", (tag,)).fetchone():
                tag = f"{tag_base}-{suffix}"
                suffix += 1

            cur.execute(
                """INSERT INTO asset
                   (asset_tag, name, category, manufacturer, model, serial_number,
                    os_version, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)""",
                (tag, hostname or agent_id, "Computer",
                 vendor or None, model or None, serial or None,
                 os_ver or None, now_iso(), now_iso()),
            )
            asset_id = cur.lastrowid
            print(f"[gw] auto_link: created asset {asset_id} (tag='{tag}') for agent '{agent_id}'", flush=True)

        # ── Update rmm_agent ──────────────────────────────────────────────
        cur.execute(
            "UPDATE rmm_agent SET asset_id = ? WHERE agent_id = ?",
            (asset_id, agent_id),
        )
        # Also keep rmm_telemetry.asset_id in sync
        cur.execute(
            "UPDATE rmm_telemetry SET asset_id = ? WHERE agent_id = ?",
            (asset_id, agent_id),
        )
        conn.commit()
        return asset_id

    except Exception as e:
        print(f"[gw] auto_link_or_create_asset error: {e}", flush=True)
        return 0
    finally:
        conn.close()


def log_availability(agent_id: str, event: str) -> None:
    """Record an online/offline event for the agent."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO rmm_availability (agent_id, event, occurred_at) VALUES (?,?,?)",
            (agent_id, event, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def store_patches(agent_id: str, patches: list) -> None:
    """Replace all patch records for this agent."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM rmm_patch WHERE agent_id=?", (agent_id,))
        captured_at = now_iso()
        for p in patches:
            cur.execute(
                "INSERT INTO rmm_patch (agent_id, hotfix_id, description, installed_on, captured_at) VALUES (?,?,?,?,?)",
                (agent_id, p.get("hotfix_id"), p.get("description"), p.get("installed_on"), captured_at),
            )
        conn.commit()
    finally:
        conn.close()


def get_metrics_history(agent_id: str, hours: int = 24) -> list:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT cpu_percent, ram_percent, captured_at
            FROM rmm_metrics_history
            WHERE agent_id=? AND captured_at >= datetime('now', ?)
            ORDER BY captured_at ASC
            """,
            (agent_id, f"-{hours} hours"),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_availability_log(agent_id: str, limit: int = 100) -> list:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT event, occurred_at FROM rmm_availability WHERE agent_id=? ORDER BY occurred_at DESC LIMIT ?",
            (agent_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_patches(agent_id: str) -> list:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT hotfix_id, description, installed_on, captured_at FROM rmm_patch WHERE agent_id=? ORDER BY installed_on DESC",
            (agent_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


SCREENSHOTS_DIR = "/var/www/tracker/screenshots"


def store_screenshot(agent_id: str, user_id: Optional[int], b64: str, width: int, height: int, fmt: str, source: str = 'manual') -> int:
    import os as _os, base64 as _b64
    conn = get_conn()
    cur = conn.cursor()
    try:
        # Insert row first to get the auto-increment ID; image_b64 stays NULL if we can save to disk
        cur.execute(
            """
            INSERT INTO rmm_screenshot (agent_id, requested_by_user_id, image_format, width, height, captured_at, source)
            VALUES (?,NULL,?,?,?,?,?)
            """,
            (agent_id, fmt, width, height, now_iso(), source),
        )
        row_id = int(cur.lastrowid)

        # Save image to disk
        agent_dir = _os.path.join(SCREENSHOTS_DIR, agent_id)
        _os.makedirs(agent_dir, exist_ok=True)
        file_path = _os.path.join(agent_dir, f"{row_id}.{fmt}")
        try:
            with open(file_path, 'wb') as fh:
                fh.write(_b64.b64decode(b64))
            cur.execute("UPDATE rmm_screenshot SET file_path=? WHERE id=?", (file_path, row_id))
        except Exception:
            # Fallback: store base64 in DB if disk write fails
            cur.execute("UPDATE rmm_screenshot SET image_b64=? WHERE id=?", (b64, row_id))

        conn.commit()
        return row_id
    finally:
        conn.close()


def store_eagle_event(agent_id: str, captured_at: str, process_name: str, window_title: str, duration_s: int, idle_s: int = 0) -> None:
    """Store a single Eagle Eyes window focus event."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO rmm_eagle_event (agent_id, captured_at, process_name, window_title, duration_s, idle_s) VALUES (?,?,?,?,?,?)",
            (agent_id, captured_at, process_name, window_title, duration_s, idle_s),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_eagle_current(agent_id: str, process_name: str, window_title: str, idle_s: int, is_idle: bool, captured_at: str) -> None:
    """Update the live 'right now' state for an agent (no history, just latest)."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO rmm_eagle_current (agent_id, process_name, window_title, idle_s, is_idle, captured_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 process_name=excluded.process_name,
                 window_title=excluded.window_title,
                 idle_s=excluded.idle_s,
                 is_idle=excluded.is_idle,
                 captured_at=excluded.captured_at""",
            (agent_id, process_name, window_title, idle_s, 1 if is_idle else 0, captured_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_eagle_config(agent_id: str) -> dict:
    """Return eagle eyes config for an agent (defaults to disabled)."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        row = cur.execute(
            "SELECT enabled, screenshot_interval_min FROM rmm_eagle_config WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()
        if row:
            return {"enabled": bool(row[0]), "screenshot_interval_min": row[1]}
        return {"enabled": False, "screenshot_interval_min": 30}
    finally:
        conn.close()


def get_latest_telemetry(agent_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM rmm_telemetry WHERE agent_id = ?", (agent_id,))
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
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM rmm_screenshot WHERE agent_id = ? ORDER BY id DESC LIMIT 1",
            (agent_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def log_rmm_event(session_id: int, actor_type: str, event_type: str, data: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO rmm_event (session_id, actor_type, event_type, data_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, actor_type, event_type, json.dumps(data), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_session_reason(session_id: int) -> Optional[str]:
    """Return the reason string for an rmm_session row."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        row = cur.execute(
            "SELECT reason FROM rmm_session WHERE id = ? LIMIT 1", (session_id,)
        ).fetchone()
        return row["reason"] if row else None
    finally:
        conn.close()


def store_rustdesk_id(agent_id: str, rustdesk_id: str) -> None:
    """Write the RustDesk peer ID into the asset linked to the given agent."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        row = cur.execute(
            "SELECT asset_id FROM rmm_agent WHERE agent_id = ? AND enabled = 1 LIMIT 1",
            (agent_id,)
        ).fetchone()
        if not row or not row["asset_id"]:
            return
        cur.execute(
            "UPDATE asset SET rustdesk_id = ? WHERE id = ? AND (rustdesk_id IS NULL OR rustdesk_id != ?)",
            (rustdesk_id, row["asset_id"], rustdesk_id)
        )
        conn.commit()
        if cur.rowcount:
            print(f"[gw] stored rustdesk_id={rustdesk_id} for asset {row['asset_id']}", flush=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Patch management
# ---------------------------------------------------------------------------

def store_pending_updates(agent_id: str, updates: list) -> None:
    """Replace the pending-update inventory for an agent."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM rmm_pending_update WHERE agent_id = ?", (agent_id,))
        for u in updates:
            c.execute(
                """
                INSERT OR REPLACE INTO rmm_pending_update
                    (agent_id, update_id, title, kb_ids, severity,
                     size_mb, reboot_required, category, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    u.get("update_id") or u.get("UpdateID") or "",
                    u.get("title") or u.get("Title") or "",
                    json.dumps(u.get("kb_ids") or u.get("KBs") or []),
                    u.get("severity") or u.get("Severity") or "",
                    u.get("size_mb") or u.get("SizeMB") or 0.0,
                    1 if (u.get("reboot_required") or u.get("RebootRequired")) else 0,
                    u.get("category") or u.get("Category") or "",
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_pending_updates(agent_id: str) -> list:
    conn = get_conn()
    c = conn.cursor()
    try:
        rows = c.execute(
            """
            SELECT update_id, title, kb_ids, severity, size_mb,
                   reboot_required, category, recorded_at
            FROM rmm_pending_update
            WHERE agent_id = ?
            ORDER BY
                CASE severity
                    WHEN 'Critical'  THEN 1
                    WHEN 'Important' THEN 2
                    WHEN 'Moderate'  THEN 3
                    WHEN 'Low'       THEN 4
                    ELSE 5
                END, title
            """,
            (agent_id,),
        ).fetchall()
        return [
            {
                "update_id":       r[0],
                "title":           r[1],
                "kb_ids":          json.loads(r[2] or "[]"),
                "severity":        r[3],
                "size_mb":         r[4],
                "reboot_required": bool(r[5]),
                "category":        r[6],
                "recorded_at":     r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def create_patch_job(
    agent_id: str,
    update_ids: list,
    kb_ids: list,
    titles: list,
    approved_by: Optional[int] = None,
) -> int:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO rmm_patch_job
                (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                agent_id,
                json.dumps(update_ids),
                json.dumps(kb_ids),
                json.dumps(titles),
                approved_by,
                now_iso(),
            ),
        )
        job_id = c.lastrowid
        conn.commit()
        return job_id
    finally:
        conn.close()


def update_patch_job(
    job_id: int,
    status: str,
    result: Optional[dict] = None,
    reboot_required: bool = False,
) -> None:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE rmm_patch_job
            SET status          = ?,
                result_json     = ?,
                reboot_required = ?,
                updated_at      = ?,
                deployed_at     = CASE WHEN ? = 'deploying' AND deployed_at IS NULL
                                       THEN ? ELSE deployed_at END,
                completed_at    = CASE WHEN ? IN ('installed','failed','deferred')
                                       THEN ? ELSE completed_at END
            WHERE id = ?
            """,
            (
                status,
                json.dumps(result) if result is not None else None,
                1 if reboot_required else 0,
                now_iso(),
                status, now_iso(),
                status, now_iso(),
                job_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_patch_jobs(agent_id: str, limit: int = 30) -> list:
    conn = get_conn()
    c = conn.cursor()
    try:
        rows = c.execute(
            """
            SELECT id, update_ids, kb_ids, titles, status,
                   approved_by, approved_at, deployed_at, completed_at,
                   result_json, reboot_required, created_at, updated_at
            FROM rmm_patch_job
            WHERE agent_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
        return [
            {
                "id":              r[0],
                "update_ids":      json.loads(r[1] or "[]"),
                "kb_ids":          json.loads(r[2] or "[]"),
                "titles":          json.loads(r[3] or "[]"),
                "status":          r[4],
                "approved_by":     r[5],
                "approved_at":     r[6],
                "deployed_at":     r[7],
                "completed_at":    r[8],
                "result":          json.loads(r[9]) if r[9] else None,
                "reboot_required": bool(r[10]),
                "created_at":      r[11],
                "updated_at":      r[12],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_patch_job(job_id: int) -> Optional[dict]:
    conn = get_conn()
    c = conn.cursor()
    try:
        row = c.execute(
            """
            SELECT id, agent_id, update_ids, kb_ids, titles, status
            FROM rmm_patch_job WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id":         row[0],
            "agent_id":   row[1],
            "update_ids": json.loads(row[2] or "[]"),
            "kb_ids":     json.loads(row[3] or "[]"),
            "titles":     json.loads(row[4] or "[]"),
            "status":     row[5],
        }
    finally:
        conn.close()


def update_cve_patch_job(
    job_id: int,
    status: str,
    result: Optional[dict] = None,
    reboot_required: bool = False,
) -> None:
    """Update a cve_patch_job row with the result from the agent."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            """
            UPDATE cve_patch_job
            SET status          = ?,
                result_json     = ?,
                reboot_required = ?,
                updated_at      = ?,
                completed_at    = CASE WHEN ? IN ('installed','failed','no_patch')
                                       THEN ? ELSE completed_at END
            WHERE id = ?
            """,
            (
                status,
                json.dumps(result) if result is not None else None,
                1 if reboot_required else 0,
                now_iso(),
                status, now_iso(),
                job_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def store_session_events(agent_id: str, asset_id: int, events: list) -> int:
    """Insert session events, ignoring duplicates (unique on agent+type+time)."""
    if not events:
        return 0
    conn = get_conn()
    cur = conn.cursor()
    try:
        inserted = 0
        for ev in events:
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO rmm_session_events
                         (agent_id, asset_id, event_type, username, event_time)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        agent_id, asset_id,
                        ev.get("type", ""),
                        ev.get("user", ""),
                        ev.get("time", ""),
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except Exception:
                pass
        conn.commit()
        return inserted
    finally:
        conn.close()


def store_software(agent_id: str, software: list) -> int:
    """Replace software inventory for an agent. Returns count stored."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM rmm_software WHERE agent_id = ?", (agent_id,))
        for sw in software:
            conn.execute(
                """INSERT INTO rmm_software (agent_id, name, version, publisher, install_date)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    sw.get("name", ""),
                    sw.get("version", ""),
                    sw.get("publisher", ""),
                    sw.get("install_date", ""),
                ),
            )
        conn.commit()
        return len(software)
    finally:
        conn.close()


def get_software(agent_id: str) -> list:
    """Return software inventory for an agent, sorted by name."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        rows = cur.execute(
            """SELECT name, version, publisher, install_date, captured_at
               FROM rmm_software WHERE agent_id = ? ORDER BY name""",
            (agent_id,),
        ).fetchall()
        return [
            {
                "name": r[0],
                "version": r[1],
                "publisher": r[2],
                "install_date": r[3],
                "captured_at": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def _parse_event_time(ts: str) -> str:
    """Normalise a Windows ISO 8601 timestamp to a plain UTC string (YYYY-MM-DD HH:MM:SS)."""
    import re as _re
    from datetime import datetime, timezone, timedelta
    if not ts:
        return ts
    # Truncate fractional seconds to 6 digits (microseconds) so Python can parse it
    ts = _re.sub(r'(\.\d{6})\d+', r'\1', ts)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return ts  # return as-is if all formats fail


def get_session_events(agent_id: str, days: int = 7) -> list:
    """Return session events for the agent in the last `days` days, newest first."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        rows = cur.execute(
            """SELECT event_type, username, event_time
               FROM rmm_session_events
               WHERE agent_id = ?
                 AND event_time >= datetime('now', ?)
               ORDER BY event_time DESC
               LIMIT 1000""",
            (agent_id, f"-{days} days"),
        ).fetchall()
        return [{"type": r[0], "user": r[1], "time": _parse_event_time(r[2])} for r in rows]
    finally:
        conn.close()
