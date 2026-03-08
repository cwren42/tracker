"""
Cirque RMM — AI Engine
Wraps OpenAI API for ticket assistant and security monitoring.
API key stored in setting table (key='openai_api_key').
"""
import json, logging, urllib.request, urllib.error
from datetime import datetime

from pg_db import pg_connect

log = logging.getLogger("ai_engine")


def _db():
    return pg_connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _get_api_key() -> str | None:
    db = _db()
    row = db.execute("SELECT value FROM setting WHERE key='openai_api_key'").fetchone()
    db.close()
    return row["value"] if row and row["value"] else None


def _get_setting(key: str, default: str = "") -> str:
    db = _db()
    row = db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    db.close()
    return row["value"] if row else default


def _openai_chat(messages: list, model: str = None, max_tokens: int = 800) -> str:
    """Call OpenAI chat completions. Raises on failure."""
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("OpenAI API key not configured — add it in Settings → AI tab")
    model = model or _get_setting("openai_model", "gpt-4o")
    payload = json.dumps({
        "model":      model,
        "messages":   messages,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"].strip()


# ──────────────────────────────────────────────────────────────────────────────
# Ticket assistant
# ──────────────────────────────────────────────────────────────────────────────
def suggest_ticket_resolution(ticket_id: int) -> dict:
    """
    Generate an AI resolution suggestion for a ticket.
    Returns the saved suggestion dict or raises on error.
    """
    db = _db()
    ticket = db.execute("SELECT * FROM support_ticket WHERE id=?", (ticket_id,)).fetchone()
    db.close()
    if not ticket:
        raise ValueError(f"Ticket {ticket_id} not found")

    # Check if we already have a recent pending suggestion
    db2 = _db()
    existing = db2.execute(
        "SELECT * FROM ai_ticket_suggestions WHERE ticket_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    db2.close()
    if existing:
        return dict(existing)

    system_prompt = (
        "You are an expert IT support engineer. When given a support ticket, respond with a JSON object:\n"
        '{"diagnosis": "...", "resolution_steps": ["step1","step2",...], '
        '"can_auto_resolve": true/false, "category": "hardware|software|network|account|security|other", '
        '"confidence": 0.0-1.0, "estimated_minutes": N}'
    )
    user_prompt = (
        f"Ticket #{ticket_id}\n"
        f"Title: {ticket['title']}\n"
        f"Description: {ticket['description'] or 'N/A'}\n"
        f"Priority: {ticket['priority']}\n"
        f"Category: {ticket.get('category', 'N/A')}"
    )

    model = _get_setting("openai_model", "gpt-4o")
    raw = _openai_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user",   "content": user_prompt}],
        model=model,
        max_tokens=600
    )

    # Parse JSON (model should return JSON)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: wrap raw text
        parsed = {"diagnosis": raw, "resolution_steps": [], "can_auto_resolve": False,
                  "category": "other", "confidence": 0.5, "estimated_minutes": None}

    suggestion_text = json.dumps(parsed)
    auto_mode = 1 if _get_setting("ai_ticket_auto_mode") == "1" else 0

    db3 = _db()
    cur = db3.execute(
        """INSERT INTO ai_ticket_suggestions
           (ticket_id, model, suggestion, confidence, category, auto_mode, status, created_at)
           VALUES (?,?,?,?,?,?,'pending',?)""",
        (ticket_id, model, suggestion_text,
         parsed.get("confidence", 0.5),
         parsed.get("category", "other"),
         auto_mode, _now())
    )
    sug_id = cur.lastrowid
    db3.commit()
    row = db3.execute("SELECT * FROM ai_ticket_suggestions WHERE id=?", (sug_id,)).fetchone()
    db3.close()

    # Auto-apply if mode is on and confidence is high
    if auto_mode and parsed.get("confidence", 0) >= 0.85 and parsed.get("can_auto_resolve"):
        try:
            apply_ticket_suggestion(sug_id, auto=True)
        except Exception:
            log.exception("Auto-apply failed for suggestion %s", sug_id)

    return dict(row)


def apply_ticket_suggestion(suggestion_id: int, auto: bool = False) -> bool:
    """Mark suggestion as applied and add a note to the ticket."""
    db = _db()
    sug = db.execute("SELECT * FROM ai_ticket_suggestions WHERE id=?", (suggestion_id,)).fetchone()
    if not sug:
        db.close()
        raise ValueError("Suggestion not found")
    parsed = json.loads(sug["suggestion"])
    ticket_id = sug["ticket_id"]

    note_text = f"**AI Resolution Suggestion** {'(auto-applied)' if auto else '(tech-approved)'}\n\n"
    note_text += f"**Diagnosis:** {parsed.get('diagnosis','')}\n\n"
    if parsed.get("resolution_steps"):
        note_text += "**Steps:**\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(parsed["resolution_steps"]))

    db.execute(
        "INSERT INTO ticket_note (ticket_id, content, created_by, created_at) VALUES (?,?,?,?)",
        (ticket_id, note_text, "AI Assistant", _now())
    )
    reviewer = "system" if auto else "technician"
    db.execute(
        "UPDATE ai_ticket_suggestions SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
        (reviewer, _now(), suggestion_id)
    )
    db.commit(); db.close()
    return True


def dismiss_ticket_suggestion(suggestion_id: int) -> bool:
    db = _db()
    db.execute("UPDATE ai_ticket_suggestions SET status='rejected', reviewed_at=? WHERE id=?",
               (_now(), suggestion_id))
    db.commit(); db.close()
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Security monitor
# ──────────────────────────────────────────────────────────────────────────────
def generate_security_summary() -> dict:
    """
    Analyse current vulnerability + patch posture and return an AI summary.
    """
    db = _db()

    # Collect raw data
    vuln_rows = db.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM device_vulnerability GROUP BY severity, status"""
    ).fetchall()
    patch_rows = db.execute(
        """SELECT COUNT(*) as total,
           SUM(CASE WHEN status='installed' THEN 1 ELSE 0 END) as installed
           FROM rmm_patch"""
    ).fetchone()
    offline_agents = db.execute(
        "SELECT COUNT(*) as cnt FROM rmm_agent WHERE online=0"
    ).fetchone()
    open_tickets = db.execute(
        "SELECT COUNT(*) as cnt FROM support_ticket WHERE status='open'"
    ).fetchone()
    recent_alerts = db.execute(
        "SELECT rule_name, COUNT(*) as cnt FROM alert_log GROUP BY rule_name ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    db.close()

    # Summarise numbers
    vuln_by_sev = {}
    total_open  = 0
    for row in vuln_rows:
        sev = (row["severity"] or "unknown").lower()
        if row["status"] == "open":
            vuln_by_sev[sev] = vuln_by_sev.get(sev, 0) + row["cnt"]
            total_open += row["cnt"]

    total_patches  = patch_rows["total"]    if patch_rows else 0
    installed_p    = patch_rows["installed"] if patch_rows else 0
    compliance_pct = int(installed_p / total_patches * 100) if total_patches else 100
    critical_count = vuln_by_sev.get("critical", 0)

    raw_data = {
        "vuln_by_severity": vuln_by_sev,
        "total_open_vulns": total_open,
        "critical_vulns": critical_count,
        "patch_compliance_pct": compliance_pct,
        "offline_agents": offline_agents["cnt"] if offline_agents else 0,
        "open_tickets": open_tickets["cnt"] if open_tickets else 0,
        "top_alerts": [dict(r) for r in recent_alerts],
    }

    prompt = (
        "You are a cybersecurity analyst reviewing an IT environment. "
        "Analyse this data and return a JSON object:\n"
        '{"summary": "2-3 sentence executive summary", '
        '"risk_level": "low|medium|high|critical", '
        '"action_items": ["item1","item2",...], '
        '"positive_notes": ["thing going well",...], '
        '"priority_cves_note": "any commentary on critical CVEs"}\n\n'
        f"Data: {json.dumps(raw_data)}"
    )

    model = _get_setting("openai_model", "gpt-4o")
    raw = _openai_chat(
        [{"role": "user", "content": prompt}],
        model=model,
        max_tokens=500
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"summary": raw, "risk_level": "unknown", "action_items": [], "positive_notes": []}

    db2 = _db()
    db2.execute(
        """INSERT INTO ai_security_summaries
           (summary, vuln_count, critical_count, patch_compliance_pct, action_items, raw_data)
           VALUES (?,?,?,?,?,?)""",
        (json.dumps(parsed), total_open, critical_count, compliance_pct,
         json.dumps(parsed.get("action_items", [])), json.dumps(raw_data))
    )
    db2.commit(); db2.close()
    return {**parsed, **raw_data}


def get_latest_security_summary() -> dict | None:
    db = _db()
    row = db.execute(
        "SELECT * FROM ai_security_summaries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()
    if not row:
        return None
    result = dict(row)
    result["parsed"] = json.loads(result["summary"])
    result["action_items"] = json.loads(result["action_items"] or "[]")
    result["raw_data"] = json.loads(result["raw_data"] or "{}")
    return result


# ── Workflow generation ────────────────────────────────────────────────────────

_WF_SYSTEM = """You are an IT automation workflow designer for a corporate IT helpdesk and RMM system.
Given a plain-English description, output a JSON object with "nodes" and "edges" arrays
that represent an automation workflow.

Available node types and actions:

TRIGGERS (always use as first node):
- type="trigger" — config: {trigger_type: manual|schedule|ticket_created|ticket_updated|vulnerability_detected|patch_failed|user_offboarded|alert_triggered}

TICKET MANAGEMENT:
- type="action", action="create_ticket"    config: {title, description, priority (Low|Medium|High|Critical)}
- type="action", action="update_ticket"    config: {status, note, assigned_to}
- type="action", action="close_ticket"     config: {resolution}
- type="action", action="assign_ticket"    config: {assigned_to}

NOTIFICATIONS:
- type="action", action="send_notification" config: {message, user}
- type="action", action="send_email"        config: {to, subject, body}
- type="action", action="send_teams"        config: {webhook_url, title, message}
- type="action", action="send_slack"        config: {webhook_url, channel, message}

ACTIVE DIRECTORY / IDENTITY:
- type="action", action="create_user"       config: {username, first_name, last_name, password, ou}
- type="action", action="disable_ad_user"   config: {username}
- type="action", action="enable_ad_user"    config: {username}
- type="action", action="reset_password"    config: {username, new_password}
- type="action", action="unlock_account"    config: {username}
- type="action", action="add_to_group"      config: {username, group_name}
- type="action", action="remove_from_group" config: {username, group_name}
- type="action", action="azure_sync"        config: {}

DEVICE MANAGEMENT (requires RMM agent online):
- type="action", action="deploy_software"    config: {asset_id, method (chocolatey|winget|msi|exe), package, args}
- type="action", action="uninstall_software" config: {asset_id, method, package}
- type="action", action="run_script"         config: {asset_id, language (powershell|bash), script}
- type="action", action="deploy_patch"       config: {cve_id, asset_id}
- type="action", action="reboot_device"      config: {asset_id, delay_seconds}
- type="action", action="shutdown_device"    config: {asset_id}
- type="action", action="lock_device"        config: {asset_id, mode (lock|bitlocker)}
- type="action", action="apply_gpo"          config: {asset_id, force (true|false)}

INTEGRATION / FLOW:
- type="action", action="webhook"            config: {url, method (POST|GET), body}
- type="action", action="http_request"       config: {url, method (GET|POST|PUT|PATCH|DELETE), headers, body}
- type="action", action="ai_suggest"         config: {}
- type="action", action="wait"               config: {seconds}
- type="condition"                           config: {field, operator (==|!=|>|<|contains|not_contains), value}
    condition nodes have output ports "true" and "false"

Layout rules:
- First node (trigger) at x=80, y=200
- Space nodes ~250px apart horizontally, ~150px vertically for branches
- Each node needs a unique id like "n1","n2",...
- Each edge needs id "e1","e2",... fromNode, fromPort (out|true|false), toNode
- Use {{username}}, {{asset_id}}, {{ticket_id}}, {{cve_id}} for dynamic context values

Return ONLY valid JSON (no markdown fences). Structure:
{
  "name": "Descriptive workflow name",
  "trigger_type": "alert_triggered",
  "nodes": [
    {"id":"n1","type":"trigger","action":"","label":"Trigger","x":80,"y":200,"config":{"trigger_type":"alert_triggered"}},
    {"id":"n2","type":"action","action":"create_ticket","label":"Create Ticket","x":330,"y":200,"config":{"title":"Alert: {{cve_id}}","priority":"High"}}
  ],
  "edges": [
    {"id":"e1","fromNode":"n1","fromPort":"out","toNode":"n2","label":""}
  ]
}"""


def generate_workflow(prompt: str) -> dict:
    """Ask the AI to generate a workflow from a plain-English prompt."""
    try:
        raw = _openai_chat([
            {"role": "system",  "content": _WF_SYSTEM},
            {"role": "user",    "content": prompt},
        ], max_tokens=2000)
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        wf = json.loads(raw.strip())
        return {"ok": True, "workflow": wf}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"AI generation failed: {e}"}


# ── Cross-module AI Ask ────────────────────────────────────────────────────────

def ask_ai(question: str) -> dict:
    """
    Answer a natural language question by pulling live context from all modules:
    assets, employees, tickets, licenses, monitoring alerts, and backup health.
    Returns {"answer": str, "sources": [str]} or raises on error.
    """
    db = _db()

    # --- Assets ---
    asset_total = db.execute("SELECT COUNT(*) FROM asset").fetchone()[0]
    asset_in_use = db.execute("SELECT COUNT(*) FROM asset WHERE status='In Use'").fetchone()[0]
    asset_offline = db.execute(
        "SELECT COUNT(*) FROM asset WHERE intune_last_seen IS NOT NULL AND "
        "intune_last_seen < datetime('now', '-7 days')"
    ).fetchone()[0]
    asset_low_storage = db.execute(
        "SELECT COUNT(*) FROM asset WHERE hardware_storage_total_gb > 0 AND "
        "CAST(hardware_storage_free_gb AS REAL) / CAST(hardware_storage_total_gb AS REAL) < 0.20"
    ).fetchone()[0]
    low_storage_hosts = [r[0] for r in db.execute(
        "SELECT hostname FROM asset WHERE hardware_storage_total_gb > 0 AND "
        "CAST(hardware_storage_free_gb AS REAL) / CAST(hardware_storage_total_gb AS REAL) < 0.20 "
        "AND hostname IS NOT NULL LIMIT 10"
    ).fetchall()]

    # --- Employees ---
    emp_total = db.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
    departments = [r[0] for r in db.execute(
        "SELECT DISTINCT department FROM employee WHERE department IS NOT NULL ORDER BY department"
    ).fetchall()]

    # --- Tickets ---
    tickets_open = db.execute(
        "SELECT COUNT(*) FROM support_ticket WHERE status='Open'"
    ).fetchone()[0]
    tickets_inprog = db.execute(
        "SELECT COUNT(*) FROM support_ticket WHERE status='In Progress'"
    ).fetchone()[0]
    tickets_unassigned = db.execute(
        "SELECT COUNT(*) FROM support_ticket WHERE status IN ('Open','In Progress') "
        "AND assigned_to_user_id IS NULL"
    ).fetchone()[0]
    urgent_tickets = [dict(r) for r in db.execute(
        "SELECT id, subject, priority, created_at FROM support_ticket "
        "WHERE status IN ('Open','In Progress') AND priority='Urgent' LIMIT 5"
    ).fetchall()]

    # --- Licenses ---
    licenses_active = db.execute("SELECT COUNT(*) FROM license WHERE status='Active'").fetchone()[0]
    licenses_expired = db.execute("SELECT COUNT(*) FROM license WHERE status='Expired'").fetchone()[0]
    licenses_expiring = db.execute(
        "SELECT COUNT(*) FROM license WHERE status='Active' AND expiry_date IS NOT NULL "
        "AND expiry_date <= date('now', '+30 days')"
    ).fetchone()[0]
    annual_cost = db.execute(
        "SELECT COALESCE(SUM(annual_cost), 0) FROM license WHERE annual_cost IS NOT NULL"
    ).fetchone()[0]

    # --- Monitoring alerts ---
    alerts_critical = db.execute(
        "SELECT COUNT(*) FROM monitoring_alert WHERE status='open' AND severity='critical'"
    ).fetchone()[0]
    alerts_warning = db.execute(
        "SELECT COUNT(*) FROM monitoring_alert WHERE status='open' AND severity='warning'"
    ).fetchone()[0]
    top_alerts = [dict(r) for r in db.execute(
        "SELECT message, severity, triggered_at FROM monitoring_alert "
        "WHERE status='open' ORDER BY triggered_at DESC LIMIT 5"
    ).fetchall()]

    # --- Backup health ---
    pools_total = db.execute("SELECT COUNT(*) FROM proxmox_zfs_pool").fetchone()[0]
    pools_degraded = db.execute(
        "SELECT COUNT(*) FROM proxmox_zfs_pool WHERE health != 'ONLINE'"
    ).fetchone()[0]
    vms_stale = db.execute(
        "SELECT COUNT(*) FROM proxmox_backup_job WHERE is_stale=1"
    ).fetchone()[0]

    db.close()

    context = {
        "assets": {
            "total": asset_total, "in_use": asset_in_use,
            "offline_7d": asset_offline, "low_storage_count": asset_low_storage,
            "low_storage_hosts": low_storage_hosts,
        },
        "employees": {"total": emp_total, "departments": departments},
        "tickets": {
            "open": tickets_open, "in_progress": tickets_inprog,
            "unassigned": tickets_unassigned, "urgent": urgent_tickets,
        },
        "licenses": {
            "active": licenses_active, "expired": licenses_expired,
            "expiring_in_30d": licenses_expiring, "annual_cost_usd": annual_cost,
        },
        "monitoring_alerts": {
            "critical_open": alerts_critical, "warning_open": alerts_warning,
            "recent": top_alerts,
        },
        "backups": {
            "zfs_pools_total": pools_total, "zfs_pools_degraded": pools_degraded,
            "vm_backups_stale": vms_stale,
        },
    }

    system_prompt = (
        "You are an expert IT administrator assistant for Cirque Corporation's internal IT tracker. "
        "You have access to real-time data from all IT modules: assets, employees, tickets, licenses, "
        "monitoring alerts, and backup health. Answer questions clearly and concisely. "
        "If specific device names or ticket IDs are relevant, include them. "
        "Format your answer in plain text suitable for display in a web UI — "
        "use numbered lists or bullet points where helpful but avoid markdown headers."
    )
    user_prompt = (
        f"Current IT environment data:\n{json.dumps(context, default=str, indent=2)}\n\n"
        f"Question: {question}"
    )

    answer = _openai_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        max_tokens=600
    )

    sources = []
    ctx_lower = question.lower()
    if any(w in ctx_lower for w in ["asset", "device", "computer", "laptop", "storage", "offline"]):
        sources.append("Assets")
    if any(w in ctx_lower for w in ["ticket", "issue", "request", "help", "support"]):
        sources.append("Tickets")
    if any(w in ctx_lower for w in ["employee", "staff", "user", "department", "person"]):
        sources.append("Employees")
    if any(w in ctx_lower for w in ["license", "software", "cost", "expir"]):
        sources.append("Licenses")
    if any(w in ctx_lower for w in ["alert", "monitor", "warning", "critical"]):
        sources.append("Monitoring")
    if any(w in ctx_lower for w in ["backup", "zfs", "proxmox", "pool", "vm"]):
        sources.append("Backups")
    if not sources:
        sources = ["Assets", "Tickets", "Licenses", "Monitoring", "Backups"]

    return {"answer": answer, "sources": sources}


# ──────────────────────────────────────────────────────────────────────────────
# Predictive failure analysis + ticket auto-triage
# ──────────────────────────────────────────────────────────────────────────────
def predict_asset_failures() -> dict:
    """
    Analyse metrics, CVE counts, age, storage and monitoring alerts to surface
    at-risk assets. Returns rule-based risk scores enriched with AI narrative if
    an OpenAI key is available.
    """
    db = _db()

    # Gather signals
    assets = db.execute("""
        SELECT a.id, a.asset_tag, a.name, a.purchase_date, a.status,
               a.hardware_storage_total_gb, a.hardware_storage_free_gb,
               a.hardware_ram_gb, a.warranty_expiry,
               (SELECT COUNT(*) FROM device_vulnerability v
                WHERE v.asset_id = a.id AND v.severity IN ('Critical','High')
                  AND v.status != 'resolved') AS crit_cves,
               (SELECT COUNT(*) FROM monitoring_alert m
                WHERE m.asset_id = a.id AND m.status = 'open') AS open_alerts
        FROM asset a
        WHERE a.status != 'Retired'
    """).fetchall()

    at_risk = []
    today = datetime.utcnow().date()

    for row in assets:
        risk_score = 0
        reasons = []

        # Age signal
        if row["purchase_date"]:
            import datetime as _dt
            pd = _dt.date.fromisoformat(str(row["purchase_date"]))
            age_years = (today - pd).days / 365.25
            if age_years >= 5:
                risk_score += 30
                reasons.append(f"Age {age_years:.1f}yr ≥ 5yr EOL threshold")
            elif age_years >= 4:
                risk_score += 15
                reasons.append(f"Age {age_years:.1f}yr approaching EOL")

        # Warranty signal
        if row["warranty_expiry"]:
            import datetime as _dt
            we = _dt.date.fromisoformat(str(row["warranty_expiry"]))
            days_left = (we - today).days
            if days_left < 0:
                risk_score += 25
                reasons.append("Warranty expired")
            elif days_left <= 30:
                risk_score += 15
                reasons.append(f"Warranty expiring in {days_left}d")

        # Storage signal
        if row["hardware_storage_total_gb"] and row["hardware_storage_total_gb"] > 0:
            free_pct = (row["hardware_storage_free_gb"] or 0) / row["hardware_storage_total_gb"]
            if free_pct < 0.10:
                risk_score += 25
                reasons.append(f"Storage critically low ({free_pct*100:.0f}% free)")
            elif free_pct < 0.20:
                risk_score += 10
                reasons.append(f"Storage low ({free_pct*100:.0f}% free)")

        # CVE signal
        if row["crit_cves"] and row["crit_cves"] > 0:
            cve_score = min(30, row["crit_cves"] * 2)
            risk_score += cve_score
            reasons.append(f"{row['crit_cves']} unresolved Critical/High CVEs")

        # Monitoring alerts
        if row["open_alerts"] and row["open_alerts"] > 0:
            alert_score = min(20, row["open_alerts"] * 5)
            risk_score += alert_score
            reasons.append(f"{row['open_alerts']} open monitoring alert(s)")

        if risk_score > 0:
            at_risk.append({
                "asset_id":  row["id"],
                "asset_tag": row["asset_tag"],
                "name":      row["name"],
                "risk_score": min(100, risk_score),
                "reasons":   reasons,
            })

    db.close()

    # Sort by descending risk
    at_risk.sort(key=lambda x: x["risk_score"], reverse=True)
    top = at_risk[:20]

    # AI narrative (optional, skipped if no API key)
    narrative = None
    try:
        if top and _get_api_key():
            summary_lines = [
                f"  - {a['name']} (score {a['risk_score']}): {'; '.join(a['reasons'])}"
                for a in top[:10]
            ]
            narrative = _openai_chat([
                {"role": "system", "content":
                    "You are a senior IT infrastructure analyst. Provide a concise executive summary "
                    "(3-5 sentences) of hardware risk across the fleet and prioritised action items."},
                {"role": "user", "content":
                    f"Top at-risk assets:\n" + "\n".join(summary_lines)}
            ], max_tokens=300)
    except Exception as _ai_err:
        log.debug(f"AI narrative skipped: {_ai_err}")

    return {
        "at_risk": top,
        "total_flagged": len(at_risk),
        "narrative": narrative,
        "generated_at": _now(),
    }


def auto_triage_ticket(ticket_id: int) -> dict:
    """
    Rule-based ticket triage: suggest priority, category and assignee based on
    keywords. Falls back to AI if a key is available.
    Returns {"priority", "category", "suggested_assignee", "reason"}.
    """
    db = _db()
    row = db.execute(
        "SELECT subject, description, priority, source FROM support_ticket WHERE id=?",
        (ticket_id,)
    ).fetchone()
    db.close()

    if not row:
        return {"error": "Ticket not found"}

    text = f"{row['subject']} {row['description'] or ''}".lower()

    # Keyword-based rules
    priority = row["priority"] or "Normal"
    category = "General"
    reason = "Keyword match"

    if any(w in text for w in ["ransomware", "breach", "hack", "phish", "malware", "virus"]):
        priority, category = "Urgent", "Security Incident"
        reason = "Security threat keywords detected"
    elif any(w in text for w in ["down", "outage", "offline", "unreachable", "not working", "crash"]):
        priority = "High"
        category = "Outage / Downtime"
        reason = "Outage/downtime keywords"
    elif any(w in text for w in ["slow", "performance", "lag", "freeze"]):
        priority = "Normal"
        category = "Performance"
        reason = "Performance keywords"
    elif any(w in text for w in ["password", "locked out", "login", "access denied"]):
        priority = "High"
        category = "Access / Authentication"
        reason = "Access problem keywords"
    elif any(w in text for w in ["printer", "print", "scanner"]):
        priority = "Low"
        category = "Peripheral"
        reason = "Printer/peripheral keywords"

    result = {"priority": priority, "category": category, "reason": reason, "source": "rules"}

    # Enhance with AI if available
    try:
        if _get_api_key():
            ai_resp = _openai_chat([
                {"role": "system", "content":
                    "You are an IT help-desk triage specialist. Given a support ticket, respond with "
                    "JSON only: {\"priority\": \"Low|Normal|High|Urgent\", \"category\": \"string\", "
                    "\"reason\": \"one sentence\"}"},
                {"role": "user", "content":
                    f"Subject: {row['subject']}\nDescription: {row['description'] or '(none)'}"}
            ], max_tokens=120)
            parsed = json.loads(ai_resp)
            result.update(parsed)
            result["source"] = "ai"
    except Exception:
        pass  # Use rule-based result

    return result
