import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool


def _resolve_dsn() -> str:
    """Resolve the Postgres connection string without hardcoding the password.

    Order: DATABASE_URL env var (set by systemd EnvironmentFile) → parse
    /var/www/tracker/.secrets.env directly (this service's unit may not load it).
    """
    dsn = os.environ.get('DATABASE_URL')
    if dsn:
        return dsn
    secrets_path = '/var/www/tracker/.secrets.env'
    try:
        with open(secrets_path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith('DATABASE_URL='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError('DATABASE_URL not set and not found in %s' % secrets_path)


PG_DSN = _resolve_dsn()

# Shared connection pool — keeps 5 ready, allows up to 40 concurrent.
# This prevents thundering-herd exhaustion when all agents reconnect at once.
_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None

def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(2, 30, PG_DSN)
    return _pool


class _PooledConn:
    """Thin wrapper so callers can call conn.close() to return to the pool."""
    __slots__ = ("_conn", "_pool")

    def __init__(self, conn, pool):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)

    # Delegate everything except close() to the real connection
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        if name in ("_conn", "_pool"):
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_conn"), name, value)

    def close(self):
        conn = object.__getattribute__(self, "_conn")
        pool = object.__getattribute__(self, "_pool")
        try:
            conn.rollback()   # ensure clean state before returning
        except Exception:
            pass
        try:
            pool.putconn(conn)
        except Exception:
            pass

    def commit(self):
        object.__getattribute__(self, "_conn").commit()

    def rollback(self):
        object.__getattribute__(self, "_conn").rollback()

    def cursor(self, *args, **kwargs):
        return object.__getattribute__(self, "_conn").cursor(*args, **kwargs)


def get_conn() -> "_PooledConn":
    """Get a connection from the pool.  Callers must call conn.close() when done."""
    pool = _get_pool()
    conn = pool.getconn()
    conn.autocommit = False
    return _PooledConn(conn, pool)


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
    """Called when an agent WebSocket disconnects.

    Guard against WS flaps false-Offlining a live box: only mark Offline when
    the agent's last_seen_at is stale (>5 min) or unknown. A box can keep its
    pull-based heartbeat (/api/rmm/agent/heartbeat) fresh even while its
    WebSocket drops (common for Taiwan/remote boxes) — in that case it is still
    Online and a disconnect must NOT down it. Genuinely-dead boxes (last_seen
    aged out) still flip to Offline here, and the periodic reconcile in
    sync_scheduler.reconcile_agent_online_state catches any that slip through.
    """
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """UPDATE asset SET online_state = 'Offline'
               WHERE id = (
                   SELECT asset_id FROM rmm_agent
                   WHERE agent_id = %s
                     AND enabled IS NOT FALSE
                     AND (last_seen_at IS NULL
                          OR last_seen_at < NOW() - interval '5 minutes')
               )""",
            (agent_id,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def create_rmm_session(asset_id: Optional[int], started_by_user_id: Optional[int], reason: str) -> int:
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
        expires = row["expires_at"]
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
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
        clock_utc = data.get("clock_utc")  # aware UTC iso from agent (>=2.9.38); NULL for older
        # Clock skew measured AT INGEST: (server now) - (box's UTC belief). ~transit if the
        # clock is fine; large if w32time is off / clock wrong. Computed here (not later) so it
        # never conflates telemetry sample-age. NULL for pre-2.9.38 agents.
        clock_skew_seconds = None
        if clock_utc:
            try:
                _cu = datetime.fromisoformat(clock_utc)
                if _cu.tzinfo is None:
                    _cu = _cu.replace(tzinfo=timezone.utc)
                clock_skew_seconds = (datetime.now(timezone.utc) - _cu).total_seconds()
            except Exception:
                clock_skew_seconds = None

        # Extended fields — only update when the agent sends them (non-None).
        # This preserves previously stored values if a periodic update omits them.
        def _js(key, alias=None):
            val = data.get(alias or key) or data.get(key)
            return json.dumps(val) if val is not None else None

        def _s(key):
            v = data.get(key)
            return str(v).strip() if v else None

        vendor       = _s("vendor")
        model_name   = _s("model_name")
        serial_num   = _s("serial_number")
        motherboard  = _s("motherboard")
        bios_mfr     = _s("bios_manufacturer")
        bios_ver     = _s("bios_version")
        bios_date    = _s("bios_date")
        gpu_json     = _js("gpu")
        sound_card   = _s("sound_card")
        os_edition   = _s("os_edition")
        security_j   = _js("security")
        sysinfo_j    = _js("sysinfo")
        # services_down (2.9.40): JSON list of stopped auto-start / watch-listed
        # services. Sent as `services_down`; persisted so the server-side
        # service_down incident detector has a source. MUST be in the INSERT
        # below (col + placeholder + value + COALESCE) or it's silently dropped.
        services_j   = _js("services_down")

        # Hardware Info refresh (2.9.36): wifi adapter, battery model/health,
        # timezone every cycle. Strings via _s (None when blank so COALESCE
        # below preserves a prior value); numerics passed through directly.
        wifi_adapter = _s("wifi_adapter")
        batt_model   = _s("battery_model")
        batt_serial  = _s("battery_serial")
        batt_chem    = _s("battery_chemistry")
        batt_health  = data.get("battery_health_pct")
        batt_cycles  = data.get("battery_cycles")
        tz_str       = _s("timezone")
        # warp_dns_hijacked (2.9.48): agent-computed bool — physical NIC DNS on a
        # 127.0.2.x WARP stub OR corp.cirque.com -> public/non-10.x IP. NULL for
        # pre-2.9.48 agents (unknown). Persisted here + COALESCE'd below or it is
        # SILENTLY DROPPED (telemetry-field gateway gotcha). Feeds the
        # warp_dns_hijack alert. Preserve False (an explicit "not hijacked") — so
        # gate on "is None", not truthiness.
        _wdh = data.get("warp_dns_hijacked")
        warp_dns_hijacked = None if _wdh is None else bool(_wdh)

        cur.execute(
            """
            INSERT INTO rmm_telemetry (
                agent_id, asset_id, hostname, os_name, os_version, os_build, os_arch,
                cpu_name, cpu_cores, cpu_percent,
                ram_total_gb, ram_available_gb, ram_percent,
                battery_present, battery_percent, battery_charging, battery_minutes_left,
                disk_json, network_json, logged_in_user, uptime_seconds,
                screen_resolution, domain, agent_version, captured_at,
                vendor, model_name, serial_number, motherboard,
                bios_manufacturer, bios_version, bios_date,
                gpu_json, sound_card, os_edition, security_json, sysinfo_json,
                wifi_adapter, battery_model, battery_serial, battery_health_pct,
                battery_cycles, battery_chemistry, timezone, public_ip,
                services_down, clock_utc, clock_skew_seconds, warp_dns_hijacked
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s)
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
                agent_version=EXCLUDED.agent_version, captured_at=EXCLUDED.captured_at,
                vendor=COALESCE(EXCLUDED.vendor, rmm_telemetry.vendor),
                model_name=COALESCE(EXCLUDED.model_name, rmm_telemetry.model_name),
                serial_number=COALESCE(EXCLUDED.serial_number, rmm_telemetry.serial_number),
                motherboard=COALESCE(EXCLUDED.motherboard, rmm_telemetry.motherboard),
                bios_manufacturer=COALESCE(EXCLUDED.bios_manufacturer, rmm_telemetry.bios_manufacturer),
                bios_version=COALESCE(EXCLUDED.bios_version, rmm_telemetry.bios_version),
                bios_date=COALESCE(EXCLUDED.bios_date, rmm_telemetry.bios_date),
                gpu_json=COALESCE(EXCLUDED.gpu_json, rmm_telemetry.gpu_json),
                sound_card=COALESCE(EXCLUDED.sound_card, rmm_telemetry.sound_card),
                os_edition=COALESCE(EXCLUDED.os_edition, rmm_telemetry.os_edition),
                security_json=COALESCE(EXCLUDED.security_json, rmm_telemetry.security_json),
                sysinfo_json=COALESCE(EXCLUDED.sysinfo_json, rmm_telemetry.sysinfo_json),
                wifi_adapter=COALESCE(EXCLUDED.wifi_adapter, rmm_telemetry.wifi_adapter),
                battery_model=COALESCE(EXCLUDED.battery_model, rmm_telemetry.battery_model),
                battery_serial=COALESCE(EXCLUDED.battery_serial, rmm_telemetry.battery_serial),
                battery_health_pct=COALESCE(EXCLUDED.battery_health_pct, rmm_telemetry.battery_health_pct),
                battery_cycles=COALESCE(EXCLUDED.battery_cycles, rmm_telemetry.battery_cycles),
                battery_chemistry=COALESCE(EXCLUDED.battery_chemistry, rmm_telemetry.battery_chemistry),
                timezone=COALESCE(EXCLUDED.timezone, rmm_telemetry.timezone),
                public_ip=COALESCE(EXCLUDED.public_ip, rmm_telemetry.public_ip),
                services_down=COALESCE(EXCLUDED.services_down, rmm_telemetry.services_down),
                clock_utc=COALESCE(EXCLUDED.clock_utc, rmm_telemetry.clock_utc),
                clock_skew_seconds=COALESCE(EXCLUDED.clock_skew_seconds, rmm_telemetry.clock_skew_seconds),
                warp_dns_hijacked=COALESCE(EXCLUDED.warp_dns_hijacked, rmm_telemetry.warp_dns_hijacked),
                -- last_seen was DEFAULT now() but never refreshed on upsert, so it froze at each
                -- row's first insert (misreported live boxes as offline/dead). Refresh it every
                -- telemetry so it actually tracks last check-in.
                last_seen=now()
            """,
            (
                agent_id, asset_id,
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
                vendor, model_name, serial_num, motherboard,
                bios_mfr, bios_ver, bios_date,
                gpu_json, sound_card, os_edition, security_j, sysinfo_j,
                wifi_adapter, batt_model, batt_serial, batt_health,
                batt_cycles, batt_chem, tz_str,
                data.get("public_ip", "") or None,
                services_j,
                clock_utc,
                clock_skew_seconds,
                warp_dns_hijacked,
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
            # Windows licensing (OEM key / edition / activation) -> asset. COALESCE so a
            # report that omits them (or a VL box with a blank key) doesn't wipe a good value.
            wkey = (data.get("windows_product_key") or "").strip() or None
            wed  = (data.get("windows_edition") or "").strip() or None
            wact = (data.get("windows_activation") or "").strip() or None
            if wkey or wed or wact:
                cur.execute(
                    """UPDATE asset SET
                         windows_product_key = COALESCE(%s, windows_product_key),
                         windows_edition     = COALESCE(%s, windows_edition),
                         windows_activation  = COALESCE(%s, windows_activation)
                       WHERE id = %s""",
                    (wkey, wed, wact, asset_id),
                )
            # Sync IP and MAC from network_json back to the asset record so the
            # Overview tab shows them without needing an Intune sync.
            networks = data.get("network") or data.get("network_json") or []
            primary_ip  = None
            eth_mac     = None
            wifi_mac    = None
            for iface in networks:
                name = (iface.get("interface") or "").lower()
                ips  = iface.get("ips") or []
                mac  = iface.get("mac") or ""
                # Skip loopback and virtual adapters
                if any(x in name for x in ("loopback", "lo", "vmware", "virtualbox", "vethernet", "docker", "vbox")):
                    continue
                if not primary_ip and ips:
                    candidate = ips[0]
                    if not candidate.startswith("169.254"):  # skip APIPA
                        primary_ip = candidate
                if mac and len(mac) >= 17:
                    if any(x in name for x in ("wi-fi", "wifi", "wlan", "wireless", "wl")):
                        if not wifi_mac:
                            wifi_mac = mac
                    else:
                        if not eth_mac:
                            eth_mac = mac
            # ip_address ALWAYS tracks the agent's current primary IP (so a repurposed/
            # renamed box doesn't keep the prior occupant's stale IP). MACs only fill when
            # empty — hardware is stable, so preserve any manual edits there.
            if primary_ip or eth_mac or wifi_mac:
                parts = ["UPDATE asset SET"]
                sets  = []
                vals  = []
                if primary_ip:
                    sets.append("ip_address = %s")
                    vals.append(primary_ip)
                if eth_mac:
                    sets.append("hardware_mac_ethernet = COALESCE(NULLIF(hardware_mac_ethernet,''), %s)")
                    vals.append(eth_mac)
                if wifi_mac:
                    sets.append("hardware_mac_wifi = COALESCE(NULLIF(hardware_mac_wifi,''), %s)")
                    vals.append(wifi_mac)
                if sets:
                    vals.append(asset_id)
                    cur.execute(f"UPDATE asset SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()
    finally:
        conn.close()


def store_work_hours(agent_id: str, asset_id: Optional[int], data: Dict[str, Any]) -> None:
    """Upsert the always-on HR work-hours daily meter from a telemetry_update.

    Reads the agent's wh_* running totals and UPSERTs into rmm_work_hours_daily
    keyed on (agent_id, local_date). LATEST running total wins — the agent sends
    monotonic cumulative daily totals, so the newest value is authoritative (never
    additive). This path is ALWAYS ON: it never consults rmm_eagle_config.

    employee_id is resolved from the asset's assigned person when available; left
    NULL otherwise (the Phase-2 report resolves it via join). No app/window data.
    """
    local_date = (data.get("wh_local_date") or "").strip()
    if not local_date:
        return  # pre-work-hours agent (no wh_* fields) — nothing to record

    try:
        on_seconds = int(data.get("wh_on_seconds") or 0)
    except (TypeError, ValueError):
        on_seconds = 0
    try:
        active_seconds = int(data.get("wh_active_seconds") or 0)
    except (TypeError, ValueError):
        active_seconds = 0
    first_utc = data.get("wh_first_activity_utc") or None
    last_utc = data.get("wh_last_activity_utc") or None

    conn = get_conn()
    cur = get_cursor(conn)
    try:
        # Resolve employee_id from the asset's assigned person (best-effort).
        employee_id = None
        if asset_id:
            try:
                cur.execute("SELECT employee_id FROM asset WHERE id = %s", (asset_id,))
                r = cur.fetchone()
                if r and r.get("employee_id"):
                    employee_id = int(r["employee_id"])
            except Exception:
                employee_id = None
                # If the SELECT aborted the transaction, clear it so the UPSERT below
                # still runs (otherwise this packet's work-hours would be dropped —
                # cumulative totals self-heal, but this avoids the needless miss).
                try:
                    conn.rollback()
                except Exception:
                    pass

        cur.execute(
            """
            INSERT INTO rmm_work_hours_daily (
                agent_id, asset_id, employee_id, local_date,
                on_seconds, active_seconds, first_activity_utc, last_activity_utc,
                updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (agent_id, local_date) DO UPDATE SET
                -- Latest running total wins (agent totals are cumulative + monotonic
                -- within a day). GREATEST guards against an out-of-order/older packet
                -- ever regressing a day's count.
                on_seconds     = GREATEST(rmm_work_hours_daily.on_seconds, EXCLUDED.on_seconds),
                active_seconds = GREATEST(rmm_work_hours_daily.active_seconds, EXCLUDED.active_seconds),
                asset_id       = COALESCE(EXCLUDED.asset_id, rmm_work_hours_daily.asset_id),
                employee_id    = COALESCE(EXCLUDED.employee_id, rmm_work_hours_daily.employee_id),
                first_activity_utc = LEAST(
                    COALESCE(rmm_work_hours_daily.first_activity_utc, EXCLUDED.first_activity_utc),
                    COALESCE(EXCLUDED.first_activity_utc, rmm_work_hours_daily.first_activity_utc)),
                last_activity_utc = GREATEST(
                    COALESCE(rmm_work_hours_daily.last_activity_utc, EXCLUDED.last_activity_utc),
                    COALESCE(EXCLUDED.last_activity_utc, rmm_work_hours_daily.last_activity_utc)),
                updated_at = now()
            """,
            (
                agent_id,
                asset_id if asset_id else None,
                employee_id,
                local_date,
                on_seconds,
                active_seconds,
                first_utc,
                last_utc,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# Eagle (periodic) screenshots kept for 3 days; manually triggered kept for 30 days
SCREENSHOT_RETENTION_EAGLE_DAYS = 3
SCREENSHOT_RETENTION_MANUAL_DAYS = 30
SCREENSHOT_MAX_PER_AGENT = 200

def store_screenshot(agent_id: str, user_id: Optional[int], b64: str, width: int, height: int, fmt: str, source: str = 'manual') -> int:
    retention_days = SCREENSHOT_RETENTION_EAGLE_DAYS if source == 'eagle' else SCREENSHOT_RETENTION_MANUAL_DAYS
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
        # Enforce per-source retention age and per-agent cap
        cur.execute(
            "DELETE FROM rmm_screenshot WHERE agent_id = %s AND source = %s AND captured_at < NOW() - (%s * INTERVAL '1 day')",
            (agent_id, source, retention_days),
        )
        cur.execute(
            """
            DELETE FROM rmm_screenshot
            WHERE agent_id = %s AND id NOT IN (
                SELECT id FROM rmm_screenshot WHERE agent_id = %s
                ORDER BY captured_at DESC LIMIT %s
            )
            """,
            (agent_id, agent_id, SCREENSHOT_MAX_PER_AGENT),
        )
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
            "SELECT enabled, screenshot_interval_min, screenshots_enabled FROM rmm_eagle_config WHERE agent_id = %s",
            (agent_id,)
        )
        row = cur.fetchone()
        if row:
            return {
                "enabled": bool(row["enabled"]),
                "screenshot_interval_min": int(row["screenshot_interval_min"]),
                "screenshots_enabled": bool(row["screenshots_enabled"]),
            }
        # Fail-closed: a missing config row must never mean "capture". Both flags off.
        return {"enabled": False, "screenshot_interval_min": 30, "screenshots_enabled": False}
    finally:
        conn.close()


def get_agent_flags(agent_id: str) -> dict:
    """Return per-agent behaviour flags (e.g. disable_rustdesk, disable_tray)."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT disable_rustdesk, disable_tray FROM rmm_agent_flags WHERE agent_id = %s",
            (agent_id,)
        )
        row = cur.fetchone()
        if row:
            return {"disable_rustdesk": bool(row["disable_rustdesk"]), "disable_tray": bool(row["disable_tray"])}
        return {"disable_rustdesk": False, "disable_tray": False}
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


def store_patches(agent_id: str, patches: list) -> None:
    """Replace installed hotfixes for an agent."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute("DELETE FROM rmm_patch WHERE agent_id = %s", (agent_id,))
        now = now_iso()
        for p in patches:
            cur.execute(
                """
                INSERT INTO rmm_patch (agent_id, hotfix_id, description, installed_on, captured_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    agent_id,
                    p.get("hotfix_id") or "",
                    p.get("description") or "",
                    p.get("installed_on") or "",
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def store_pending_updates(agent_id: str, updates: list) -> None:
    """Replace pending Windows Update entries for an agent."""
    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute("DELETE FROM rmm_pending_update WHERE agent_id = %s", (agent_id,))
        now = now_iso()
        for u in updates:
            cur.execute(
                """
                INSERT INTO rmm_pending_update
                    (agent_id, update_id, title, kb_ids, severity, size_mb, reboot_required, category, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    agent_id,
                    u.get("update_id") or "",
                    u.get("title") or "",
                    u.get("kb_ids") or "",
                    u.get("severity") or "",
                    float(u.get("size_mb") or 0) or None,
                    bool(u.get("reboot_required", False)),
                    u.get("category") or "",
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def store_software(agent_id: str, apps: list) -> int:
    """Replace the installed-software inventory for an agent. Mirrors the Flask
    /api/rmm/<agent_id>/software endpoint, but reached over the gateway WebSocket
    so boxes whose direct HTTPS POST fails (e.g. the TeamViewer tv_x64.dll HTTP
    breakage, or a payload/timeout) still report inventory — their WS channel
    already works. Returns rows inserted.

    GUARD: an empty/missing list is ignored (never wipes a good inventory with a
    transient empty collection)."""
    if not apps:
        return 0

    def _clean(v):
        # Some registry DisplayName/Publisher values contain NUL (0x00) or other
        # C0 control bytes (garbage from certain MSI/Uninstall entries). Postgres
        # rejects NUL in text ("A string literal cannot contain NUL (0x00)
        # characters."), which aborted the whole insert and left the box with 0
        # rows on BOTH the WS and HTTP paths. Strip NUL + control chars (keep
        # tab/newline-free single-line values).
        s = (v or "").strip()
        if not s:
            return None
        s = s.replace("\x00", "")
        s = "".join(ch for ch in s if ch >= " " or ch in "\t")
        s = s.strip()
        return s or None

    conn = get_conn()
    cur = get_cursor(conn)
    try:
        cur.execute("DELETE FROM rmm_software WHERE agent_id = %s", (agent_id,))
        now = now_iso()
        inserted = 0
        seen = set()
        for a in apps:
            name = _clean(a.get("name"))
            if not name or name in seen:
                continue
            seen.add(name)
            cur.execute(
                """INSERT INTO rmm_software
                       (agent_id, name, version, publisher, install_date, captured_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    agent_id,
                    name,
                    _clean(a.get("version")),
                    _clean(a.get("publisher")),
                    _clean(a.get("install_date")),
                    now,
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
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
