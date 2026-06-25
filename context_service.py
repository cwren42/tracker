#!/usr/bin/env python3
"""Context Layer (Agentic IT OS roadmap, Phase 0).

A READ-ONLY assembler that resolves an entity (person or device) into one unified
context object the AI / enrichment / playbooks reason over -- the difference
between answering "why" and only seeing "what". Pulls from the systems that
already hold the data (employee, asset, rmm_telemetry, support_ticket,
license_assignment, audit_trail, rmm_eagle_current) and joins them on the real
keys:  asset.employee_id (person<->device), support_ticket.reporter_email /
.asset_id, rmm_telemetry.asset_id, license_assignment.employee_id/.asset_id.

No writes. Safe to import in the Flask app (pass a psycopg2 connection) or run
standalone against DATABASE_URL. Risk flags are derived, not stored.
"""
import os
import json
import psycopg2
import psycopg2.extras


# --- region from AD distinguishedName (same rule used by the VPN sweep) -------
def region_from_dn(dn):
    if not dn:
        return "unknown"
    d = dn.lower()
    if "cirqueasia" in d or "ou=taiwan" in d or "ou=china" in d:
        return "asia"
    if "cirqueus" in d:
        return "us"
    if "dc=corp" in d or "cirquecompany" in d:
        return "us-other"  # domain-joined but not in a region OU (DCs, TEST OUs)
    return "unknown"


def _age_str(seconds):
    if seconds is None:
        return None
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _parse_m365_licenses(raw):
    """m365_licenses_json -> a flat list of human SKU names, best-effort."""
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    out = []
    if isinstance(v, list):
        for item in v:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(item.get("skuPartNumber") or item.get("name")
                           or item.get("sku") or json.dumps(item))
    elif isinstance(v, dict):
        out = list(v.keys())
    return out


class ContextService:
    def __init__(self, conn):
        self.conn = conn

    def _cur(self):
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ---- live telemetry for one asset (latest fresh-or-not row) -------------
    def _telemetry(self, asset_id):
        cur = self._cur()
        cur.execute("""
            SELECT agent_id, agent_version, cpu_percent, ram_percent, disk_json,
                   logged_in_user, uptime_seconds, public_ip,
                   EXTRACT(EPOCH FROM (now() - last_seen)) AS seen_secs
            FROM rmm_telemetry WHERE asset_id = %s
            ORDER BY last_seen DESC NULLS LAST LIMIT 1
        """, (asset_id,))
        r = cur.fetchone()
        if not r:
            return None
        disk_busiest = None
        try:
            disks = json.loads(r["disk_json"]) if r["disk_json"] else []
            pcts = [d.get("percent") for d in disks
                    if d.get("drive_type") == 3 and d.get("percent") is not None]
            disk_busiest = round(max(pcts), 1) if pcts else None
        except (ValueError, TypeError):
            pass
        return {
            "agent_id": r["agent_id"],
            "agent_version": r["agent_version"],
            "cpu_percent": r["cpu_percent"],
            "ram_percent": r["ram_percent"],
            "disk_busiest_pct": disk_busiest,
            "logged_in_user": r["logged_in_user"],
            "uptime": _age_str(r["uptime_seconds"]),
            "public_ip": r["public_ip"],
            "last_seen_age": _age_str(r["seen_secs"]),
            "online": (r["seen_secs"] is not None and r["seen_secs"] < 1800),
        }

    def _tickets_for(self, where_sql, params):
        cur = self._cur()
        cur.execute(f"""
            SELECT id, subject, status, priority, category, created_at
            FROM support_ticket
            WHERE {where_sql}
            ORDER BY created_at DESC LIMIT 8
        """, params)
        rows = cur.fetchall()
        open_states = ("open", "new", "in_progress", "pending", "on_hold")
        return {
            "open_count": sum(1 for t in rows if (t["status"] or "").lower() in open_states),
            "recent": [dict(id=t["id"], subject=t["subject"], status=t["status"],
                            priority=t["priority"], created_at=str(t["created_at"]))
                       for t in rows],
        }

    def _recent_changes(self, entity_type, entity_id):
        cur = self._cur()
        cur.execute("""
            SELECT action, changes, created_at
            FROM audit_trail
            WHERE lower(entity_type) = lower(%s) AND entity_id = %s
            ORDER BY created_at DESC LIMIT 6
        """, (entity_type, str(entity_id)))
        return [dict(action=r["action"], at=str(r["created_at"]),
                     changes=(r["changes"] if isinstance(r["changes"], (dict, list))
                              else str(r["changes"])[:200] if r["changes"] else None))
                for r in cur.fetchall()]

    # ---- device summary used both standalone and nested under a person ------
    def _device_row(self, a):
        tel = self._telemetry(a["id"])
        return {
            "asset_id": a["id"], "name": a["name"], "asset_tag": a["asset_tag"],
            "serial": a["serial_number"], "model": a["model"], "category": a["category"],
            "device_type": a["device_type"], "location": a["location"], "status": a["status"],
            "online_state": a["online_state"],
            "os_version": a["os_version"],
            "intune_compliance": a["intune_compliance_state"],
            "ad_dn": a["ad_dn"], "region": region_from_dn(a["ad_dn"]),
            "rustdesk_id": a["rustdesk_id"],
            "telemetry": tel,
        }

    # ========================= PERSON =======================================
    def person(self, ident):
        cur = self._cur()
        cur.execute("""
            SELECT * FROM employee
            WHERE id = %s::text::int OR lower(email) = lower(%s)
               OR lower(sam_account_name) = lower(%s)
            ORDER BY (id = %s::text::int) DESC NULLS LAST LIMIT 1
        """, (_intish(ident), ident, ident, _intish(ident)))
        e = cur.fetchone()
        if not e:
            return None

        cur.execute("SELECT * FROM asset WHERE employee_id = %s ORDER BY name", (e["id"],))
        devices = [self._device_row(a) for a in cur.fetchall()]

        cur.execute("""
            SELECT l.software_name, l.vendor, l.license_type, la.status, la.assigned_date
            FROM license_assignment la JOIN license l ON l.id = la.license_id
            WHERE la.employee_id = %s ORDER BY l.software_name
        """, (e["id"],))
        licenses = [dict(software=r["software_name"], vendor=r["vendor"],
                         type=r["license_type"], status=r["status"],
                         assigned=str(r["assigned_date"]) if r["assigned_date"] else None)
                    for r in cur.fetchall()]

        tickets = self._tickets_for("lower(reporter_email) = lower(%s)", (e["email"],))
        m365_lic = _parse_m365_licenses(e.get("m365_licenses_json"))
        offboarded = e.get("offboarded_at") is not None

        ctx = {
            "entity_type": "person",
            "id": e["id"], "name": e["name"], "email": e["email"],
            "department": e["department"], "job_title": e.get("job_title") or e.get("position"),
            "manager": e["manager"], "location": e["location"], "work_type": e["work_type"],
            "status": {"onboard_status": e["onboard_status"], "offboarded": offboarded,
                       "offboarded_at": str(e["offboarded_at"]) if offboarded else None},
            "accounts": {
                "ad": {"sam": e["sam_account_name"], "guid": e["ad_guid"], "dn": e["ad_dn"],
                       "enabled": e["ad_enabled"], "region": region_from_dn(e["ad_dn"]),
                       "last_sync": str(e["ad_last_sync"]) if e["ad_last_sync"] else None},
                "m365": {"id": e["m365_id"], "enabled": e["m365_account_enabled"],
                         "licenses": m365_lic, "license_count": len(m365_lic)},
            },
            "devices": devices,
            "licenses": licenses,
            "tickets": tickets,
            "recent_changes": self._recent_changes("employee", e["id"]),
        }
        ctx["risk_flags"] = self._person_risks(ctx)
        return ctx

    def _person_risks(self, c):
        f = []
        off = c["status"]["offboarded"]
        if off and c["accounts"]["ad"]["enabled"]:
            f.append("offboarded but AD account still enabled")
        if off and c["accounts"]["m365"]["enabled"]:
            f.append("offboarded but M365 account still enabled")
        if off and c["accounts"]["m365"]["license_count"]:
            f.append(f"offboarded but holding {c['accounts']['m365']['license_count']} M365 license(s)")
        if off and c["devices"]:
            f.append(f"offboarded but still assigned {len(c['devices'])} device(s)")
        if not off and not c["devices"]:
            f.append("active employee with no assigned device")
        for d in c["devices"]:
            t = d.get("telemetry")
            if d["intune_compliance"] and str(d["intune_compliance"]).lower() not in ("compliant", "", "none"):
                f.append(f"{d['name']}: Intune {d['intune_compliance']}")
            if t and t.get("disk_busiest_pct") and t["disk_busiest_pct"] >= 90:
                f.append(f"{d['name']}: disk {t['disk_busiest_pct']}% full")
        if c["tickets"]["open_count"]:
            f.append(f"{c['tickets']['open_count']} open ticket(s)")
        return f

    # ========================= DEVICE =======================================
    def device(self, ident):
        cur = self._cur()
        cur.execute("""
            SELECT * FROM asset
            WHERE id = %s::text::int OR lower(name) = lower(%s)
               OR lower(serial_number) = lower(%s) OR lower(asset_tag) = lower(%s)
            ORDER BY (id = %s::text::int) DESC NULLS LAST LIMIT 1
        """, (_intish(ident), ident, ident, ident, _intish(ident)))
        a = cur.fetchone()
        if not a:
            return None

        owner = None
        if a["employee_id"]:
            cur.execute("SELECT id,name,email,department,job_title FROM employee WHERE id=%s",
                        (a["employee_id"],))
            o = cur.fetchone()
            if o:
                owner = dict(id=o["id"], name=o["name"], email=o["email"],
                             department=o["department"], job_title=o["job_title"])

        base = self._device_row(a)
        base.update({
            "entity_type": "device",
            "owner": owner,
            "hardware": {"cpu": a["hardware_cpu"], "ram_gb": a["hardware_ram_gb"],
                         "storage_total_gb": a["hardware_storage_total_gb"],
                         "storage_free_gb": a["hardware_storage_free_gb"],
                         "bios": a["hardware_bios_version"], "tpm": a["hardware_tpm_version"],
                         "windows_edition": a["windows_edition"],
                         "windows_activation": a["windows_activation"]},
            "ad": {"dn": a["ad_dn"], "region": region_from_dn(a["ad_dn"]),
                   "enabled": a["ad_enabled"], "last_logon": str(a["ad_last_logon"]) if a["ad_last_logon"] else None},
            "remote": {"rustdesk_id": a["rustdesk_id"], "teamviewer_id": a["teamviewer_id"]},
            "tickets": self._tickets_for("asset_id = %s", (a["id"],)),
            "recent_changes": self._recent_changes("asset", a["id"]),
        })

        # current foreground activity (Eagle Eyes) via the device's agent
        if base["telemetry"] and base["telemetry"].get("agent_id"):
            cur.execute("""SELECT process_name, window_title, idle_s, is_idle, captured_at
                           FROM rmm_eagle_current WHERE agent_id=%s
                           ORDER BY captured_at DESC LIMIT 1""", (base["telemetry"]["agent_id"],))
            ec = cur.fetchone()
            if ec:
                base["activity"] = dict(process=ec["process_name"], window=ec["window_title"],
                                        idle_s=ec["idle_s"], is_idle=ec["is_idle"],
                                        at=str(ec["captured_at"]))
        base["risk_flags"] = self._device_risks(base)
        return base

    def _device_risks(self, c):
        f = []
        t = c.get("telemetry")
        if t and t.get("disk_busiest_pct") and t["disk_busiest_pct"] >= 90:
            f.append(f"disk {t['disk_busiest_pct']}% full")
        if c.get("intune_compliance") and str(c["intune_compliance"]).lower() not in ("compliant", "", "none"):
            f.append(f"Intune {c['intune_compliance']}")
        if c["ad"]["region"] == "us-other":
            f.append("AD object not in a region OU (DC/TEST OU)")
        if c.get("owner") is None and (c.get("status") or "").lower() not in ("retired", "disposed", "spare"):
            f.append("no assigned owner")
        if c["tickets"]["open_count"]:
            f.append(f"{c['tickets']['open_count']} open ticket(s)")
        return f


def _intish(v):
    try:
        return int(str(v))
    except (ValueError, TypeError):
        return -1


# --- standalone convenience --------------------------------------------------
def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def get_person_context(ident):
    with _connect() as conn:
        return ContextService(conn).person(ident)


def get_device_context(ident):
    with _connect() as conn:
        return ContextService(conn).device(ident)


if __name__ == "__main__":
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else "person"
    ident = sys.argv[2] if len(sys.argv) > 2 else "1"
    fn = get_device_context if kind == "device" else get_person_context
    print(json.dumps(fn(ident), indent=2, default=str))
