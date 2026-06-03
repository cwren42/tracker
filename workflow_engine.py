"""
Cirque RMM — Workflow Engine
Executes visual workflow definitions stored in workflow_definitions table.
Runs as a background thread inside the Flask app.
"""
import json, logging, re, threading, time, traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from pg_db import pg_connect
import approval

log = logging.getLogger("workflow_engine")


_SENSITIVE_KEY = re.compile(r"(pass|pwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)


def _redact(d):
    """Mask values of obviously-sensitive keys before they're persisted to the ledger /
    shown in the approvals UI. The parked ctx snapshot is plaintext-at-rest otherwise."""
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v not in (None, "", "[redacted]"):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


def _notify_parked(action_type, risk_tier, ledger_id, label):
    """Drop an in-app notification-bell entry when an action parks for approval. Context-free
    (raw pg_db) so it works in the workflow daemon thread; best-effort, never raises."""
    try:
        db = _db()
        try:
            db.execute(
                "INSERT INTO notification_bell (title, body, icon, color, link, read_flag, created_at) "
                "VALUES (?,?,?,?,?,false,?)",
                (f"Approval needed: {action_type.replace('_', ' ')}",
                 f"A {risk_tier}-risk action ({label}) is parked awaiting your approval.",
                 "bi-shield-exclamation", "warning", "/approvals", _now()),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("parked-approval notification failed (non-fatal)")


def _action_object(action_type, config, ctx):
    """Best-effort (object_type, object_id) for the command ledger / approval queue.
    Templated config values are rendered against ctx so the approval shows the real
    target (e.g. 'jdoe'), not the raw '{{submitter_sam}}' placeholder."""
    asset_id = config.get("asset_id") or (ctx or {}).get("asset_id")
    if asset_id:
        return "asset", _render(str(asset_id), ctx or {})
    user = (config.get("username") or (ctx or {}).get("username")
            or (ctx or {}).get("upn") or (ctx or {}).get("email"))
    if user:
        rendered = _render(str(user), ctx or {})
        # If the template didn't resolve (still contains {{…}}), don't show the raw token.
        return "identity", (rendered if "{{" not in rendered else None)
    return "workflow_action", None


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────
def _db():
    return pg_connect()


def _now():
    # Local time (server TZ = America/Denver), matching models.now_mst() and the rest of the
    # app. utcnow() here previously made ledger/event/run timestamps display 6-7h in the future.
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────────────────────────
# Action handlers — each returns (success: bool, output: dict)
# ──────────────────────────────────────────────────────────────────────────────

def _action_create_ticket(config: dict, ctx: dict) -> tuple:
    """Create a support ticket."""
    title   = _render(config.get("title", "Automated Ticket"), ctx)
    body    = _render(config.get("body", "Created by workflow."), ctx)
    priority = config.get("priority", "medium")
    category = config.get("category", "general")
    try:
        db = _db()
        now = _now()
        cur = db.execute(
            """INSERT INTO support_ticket (title, description, priority, category, status, created_at, updated_at)
               VALUES (?,?,?,?,'open',?,?)""",
            (title, body, priority, category, now, now)
        )
        ticket_id = cur.lastrowid
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "title": title}
    except Exception as e:
        return False, {"error": str(e)}


def _action_send_notification(config: dict, ctx: dict) -> tuple:
    """Add an in-app bell notification."""
    message = _render(config.get("message", "Workflow notification"), ctx)
    try:
        db = _db()
        users = db.execute("SELECT id FROM user WHERE role IN ('admin','manager')").fetchall()
        for u in users:
            db.execute(
                "INSERT INTO notification_bell (user_id, message, type, created_at) VALUES (?,?,'workflow',?)",
                (u["id"], message, _now())
            )
        db.commit(); db.close()
        return True, {"notified": len(users), "message": message}
    except Exception as e:
        return False, {"error": str(e)}


def _action_send_email(config: dict, ctx: dict) -> tuple:
    """Send an email via configured SMTP."""
    to      = _render(config.get("to", ""), ctx)
    subject = _render(config.get("subject", "Workflow Alert"), ctx)
    body    = _render(config.get("body", ""), ctx)
    if not to:
        return False, {"error": "No 'to' address configured"}
    try:
        import smtplib
        from email.mime.text import MIMEText
        db = _db()
        cfg = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM setting").fetchall()}
        db.close()
        msg = MIMEText(body, "html")
        msg["Subject"] = subject
        msg["From"]    = cfg.get("mail_sender", "noreply@cirque.com")
        msg["To"]      = to
        host = cfg.get("mail_server", "localhost")
        port = int(cfg.get("mail_port", 25))
        with smtplib.SMTP(host, port, timeout=10) as s:
            if cfg.get("mail_use_tls") == "1":
                s.starttls()
            if cfg.get("mail_username"):
                s.login(cfg["mail_username"], cfg.get("mail_password", ""))
            s.send_message(msg)
        return True, {"to": to, "subject": subject}
    except Exception as e:
        return False, {"error": str(e)}


def _action_disable_ad_user(config: dict, ctx: dict) -> tuple:
    """Disable a user in Active Directory via LDAP."""
    username = _render(config.get("username", ctx.get("username", "")), ctx)
    if not username:
        return False, {"error": "No username provided"}
    try:
        import ldap3, ssl
        db = _db()
        ad = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM setting WHERE key LIKE 'ad_%'").fetchall()}
        db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        server = ldap3.Server(ad["ad_server"], port=int(ad.get("ad_port", 636)),
                              use_ssl=ad.get("ad_use_ssl") == "1",
                              get_info=ldap3.ALL)
        from secret_store import decrypt_secret
        conn = ldap3.Connection(server, ad["ad_bind_username"], decrypt_secret(ad["ad_bind_password"]), auto_bind=True)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName", "userAccountControl"])
        if not conn.entries:
            return False, {"error": f"User {username} not found in AD"}
        dn  = conn.entries[0].distinguishedName.value
        uac = int(conn.entries[0].userAccountControl.value)
        # Set bit 2 (0x0002) to disable
        conn.modify(dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [uac | 2])]})
        conn.unbind()
        return True, {"username": username, "dn": dn, "disabled": True}
    except Exception as e:
        return False, {"error": str(e)}


def _entra_sync_asset():
    """asset_id of the AAD-Connect host — the system tagged as the Entra/AD-Connect sync
    (e.g. NABOO). Lets azure_sync 'just work' without an asset_id in config."""
    try:
        db = _db()
        try:
            row = db.execute(
                "SELECT asset_id FROM it_system WHERE asset_id IS NOT NULL AND ("
                "LOWER(name) LIKE '%entra%' OR LOWER(name) LIKE '%ad connect%' "
                "OR LOWER(name) LIKE '%aad%') ORDER BY id LIMIT 1").fetchone()
            return row["asset_id"] if row else None
        finally:
            db.close()
    except Exception:
        return None


def _action_azure_sync(config: dict, ctx: dict) -> tuple:
    """Trigger Azure AD Connect delta sync on the AD-Connect server via its RMM agent.

    Resolves the host by asset_id from config/ctx, else the Entra-Sync system's host.
    """
    asset_id = config.get("asset_id") or (ctx or {}).get("asset_id") or _entra_sync_asset()
    if not asset_id:
        return False, {"error": "No AD-Connect host found (set a host asset on the Entra Sync system)."}
    script = "Import-Module ADSync; Start-ADSyncSyncCycle -PolicyType Delta"
    return _device_action(
        "azure_sync", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="medium", reason="workflow: Azure AD Connect delta sync",
        wait_result=True, extra_output={"method": "rmm_agent"}, ctx=ctx,
    )


def _action_webhook(config: dict, ctx: dict) -> tuple:
    """POST to an external webhook URL."""
    url     = _render(config.get("url", ""), ctx)
    payload = json.loads(_render(config.get("payload", "{}"), ctx))
    if not url:
        return False, {"error": "No URL configured"}
    try:
        import urllib.request, urllib.error
        data = json.dumps({**payload, "_workflow_ctx": ctx}).encode()
        req  = urllib.request.Request(url, data=data,
                                       headers={"Content-Type": "application/json"},
                                       method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, {"status": r.status, "url": url}
    except Exception as e:
        return False, {"error": str(e)}


def _action_release_quarantine(config: dict, ctx: dict) -> tuple:
    """Release a quarantined/blocked email via Defender, then auto-close the linked
    release-request ticket. Medium-risk: the gate PARKS this at /approvals (and in
    Mission Control) so a human signs off before a quarantined message is delivered.
    Runs in the workflow daemon — no app context / no current_user — so it uses env
    M365 creds + raw SQL, exactly like the other live-change actions. The ticket close
    deliberately does NOT publish ticket.resolved (a single release is not a runbook —
    consistent with the manual release route)."""
    message_id = _render(config.get("message_id", ctx.get("message_id", "")), ctx)
    recipient  = _render(config.get("recipient",  ctx.get("recipient", "")),  ctx)
    if not message_id:
        return False, {"error": "No message_id"}
    db = _db()
    try:
        # Backfill the recipient from the message row if the trigger didn't carry one.
        if not recipient:
            row = db.execute("SELECT recipient_address FROM quarantine_message WHERE message_id=?",
                             (message_id,)).fetchone()
            recipient = (row["recipient_address"] if row else "") or ""
        # Defender release (context-free: env creds + HTTPS).
        from quarantine_service import QuarantineService
        from m365_config import get_m365_credentials
        tid, cid, sec = get_m365_credentials()
        if not (tid and cid and sec):
            return False, {"error": "M365 credentials not configured"}
        result = QuarantineService(tid, cid, sec).release_message(message_id, recipient)
        if not result.get("success"):
            return False, {"error": result.get("error", "release failed")}
        # Mark released + auto-close the [qmsg:] release-request ticket (system actor).
        db.execute("UPDATE quarantine_message SET release_status='Released', "
                   "released_by='agentic-os', released_at=? WHERE message_id=?",
                   (_now(), message_id))
        marker = "[qmsg:%s]" % message_id
        trow = db.execute(
            "SELECT id FROM support_ticket WHERE source='quarantine' "
            "AND status NOT IN ('Closed','Merged') AND description LIKE ? ORDER BY id DESC LIMIT 1",
            ("%" + marker + "%",)).fetchone()
        ticket_id = trow["id"] if trow else None
        if ticket_id:
            db.execute("UPDATE support_ticket SET status='Closed', closed_at=?, updated_at=? WHERE id=?",
                       (_now(), _now(), ticket_id))
            db.execute("INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (ticket_id, None,
                        "Released from quarantine to %s — approved via the Action Center. Auto-closed." % recipient,
                        True, False, _now()))
        db.commit()
        # Feed the Knowledge "Learn" loop: a human-approved release is a grounded
        # email-triage decision. Publish onto the bus; the dispatcher's built-in
        # subscriber distills it into an email runbook (with app context). Best-effort.
        try:
            import event_bus
            event_bus.publish("email.released",
                              {"message_id": message_id, "recipient": recipient, "actor": "agentic-os"},
                              source="workflow")
        except Exception:
            log.exception("release_quarantine: email.released publish failed for %s", message_id)
        return True, {"message_id": message_id, "recipient": recipient,
                      "released": True, "ticket_id": ticket_id}
    except Exception as e:
        db.rollback()
        return False, {"error": str(e)}
    finally:
        db.close()


def _action_deploy_patch(config: dict, ctx: dict) -> tuple:
    """Trigger CVE patch deployment for a device."""
    cve_id   = _render(config.get("cve_id", ctx.get("cve_id", "")), ctx)
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    if not cve_id:
        return False, {"error": "No CVE ID"}
    try:
        import urllib.request
        url  = f"http://127.0.0.1:8000/api/vulnerabilities/{cve_id}/deploy"
        body = json.dumps({"asset_id": asset_id} if asset_id else {}).encode()
        req  = urllib.request.Request(url, data=body,
                                       headers={"Content-Type": "application/json", "X-Internal": "workflow"},
                                       method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
        return True, result
    except Exception as e:
        return False, {"error": str(e)}


def _action_wait(config: dict, ctx: dict) -> tuple:
    """Sleep for N seconds (up to 1 hour)."""
    seconds = min(int(config.get("seconds", 60)), 3600)
    time.sleep(seconds)
    return True, {"waited_seconds": seconds}


def _action_update_ticket(config: dict, ctx: dict) -> tuple:
    """Update a ticket's status/assigned_to."""
    ticket_id = config.get("ticket_id") or ctx.get("ticket_id")
    if not ticket_id:
        return False, {"error": "No ticket_id"}
    updates, vals = [], []
    for f in ["status", "priority", "assigned_to"]:
        if f in config:
            updates.append(f"{f}=?")
            vals.append(config[f])
    if not updates:
        return False, {"error": "No fields to update"}
    vals += [_now(), ticket_id]
    try:
        db = _db()
        db.execute(f"UPDATE support_ticket SET {','.join(updates)}, updated_at=? WHERE id=?", vals)
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "updated": config}
    except Exception as e:
        return False, {"error": str(e)}


def _action_ai_suggest(config: dict, ctx: dict) -> tuple:
    """Generate a knowledge-grounded AI resolution suggestion for the ticket in context.

    Delegates to ai_engine.suggest_ticket_resolution — the good path that grounds the
    suggestion in resolved-ticket history AND the Knowledge Agent (learned runbooks + policy
    + system docs) and saves it to ai_ticket_suggestions. Both ai_engine and knowledge_agent
    are raw-pg_db / context-free, so this runs fine in the workflow daemon thread."""
    ticket_id = ctx.get("ticket_id")
    if not ticket_id:
        return False, {"error": "No ticket_id in context"}
    try:
        import ai_engine
        out = ai_engine.suggest_ticket_resolution(int(ticket_id))
        return True, {"ticket_id": ticket_id, "suggestion_id": out.get("suggestion_id"),
                      "knowledge_used": len((out.get("informed_by") or [])),
                      "suggestion_created": True}
    except Exception as e:
        return False, {"error": str(e)}



# ── HELPERS ────────────────────────────────────────────────────────────────────
def _get_ad_settings(db):
    rows = db.execute("SELECT key, value FROM setting WHERE key LIKE 'ad_%'").fetchall()
    ad = {r["key"]: r["value"] for r in rows}
    # Normalize boolean-ish settings to "1"/"0". The handlers below check == "1", but the
    # app stores these as 'true'/'false' (settings.py) — without this every AD action fails
    # with "AD integration not enabled" even when AD is on.
    for k in ("ad_enabled", "ad_use_ssl"):
        if k in ad:
            ad[k] = "1" if str(ad[k]).strip().lower() in ("1", "true", "yes", "on") else "0"
    return ad


def _ad_connect(ad):
    import ldap3
    server = ldap3.Server(
        ad["ad_server"],
        port=int(ad.get("ad_port", 636)),
        use_ssl=ad.get("ad_use_ssl") == "1",
        get_info=ldap3.ALL,
    )
    from secret_store import decrypt_secret
    return ldap3.Connection(server, ad["ad_bind_username"], decrypt_secret(ad["ad_bind_password"]), auto_bind=True)


# Agent is considered "online" if it has checked in within this window.
_AGENT_ONLINE_WINDOW = "5 minutes"


def _resolve_agent(asset_id=None):
    """Return (agent_id, asset_id, online: bool) for an asset, or (None, None, False).

    The rmm_agent table has no `online` or `hostname` column — liveness is derived
    from `last_seen_at` and `enabled` (mirrors settings_scripts' online check).
    """
    if asset_id is None:
        return None, None, False
    db = _db()
    try:
        row = db.execute(
            "SELECT agent_id, asset_id, "
            "(last_seen_at > NOW() - INTERVAL '5 minutes') AS online "
            "FROM rmm_agent WHERE asset_id=? AND enabled=1 "
            "ORDER BY last_seen_at DESC NULLS LAST LIMIT 1",
            (asset_id,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        return None, None, False
    return row["agent_id"], row["asset_id"], bool(row["online"])


def _find_agent(asset_id=None, hostname=None):
    """Backwards-compatible shim: return online-or-not agent_id for an asset, or None."""
    agent_id, _asset, _online = _resolve_agent(asset_id=asset_id)
    return agent_id


# ── Command ledger (raw-SQL, best-effort) ───────────────────────────────────────
# The SQLAlchemy command_ledger.log_action/mark_result helpers require a Flask app
# context; the workflow engine runs in background daemon threads on raw pg_db, so we
# write the same command_ledger columns directly here. Ledger failures must NEVER
# crash a workflow — every call is wrapped and swallowed.
def _ledger_log(tool, action_type, *, object_type=None, object_id=None,
                requested_by="workflow_engine", planned_by="workflow_engine",
                risk_tier="low", approval_status="auto", correlation_id=None,
                status="dispatched", before_state=None):
    """Insert a command_ledger row at dispatch time. Returns row id or None.

    before_state holds the planned command (agent/shell/code) when the action is held
    for approval, so the Approvals queue can dispatch the exact same payload on approve.
    """
    try:
        db = _db()
        try:
            cur = db.execute(
                "INSERT INTO command_ledger "
                "(tool, action_type, object_type, object_id, requested_by, planned_by, "
                " risk_tier, approval_status, correlation_id, status, rollback_available, "
                " verification_status, before_state, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tool, action_type, object_type,
                 (str(object_id) if object_id is not None else None),
                 requested_by, planned_by, risk_tier, approval_status,
                 correlation_id, status, False, "pending",
                 (json.dumps(before_state, default=str) if before_state is not None else None), _now()),
            )
            row_id = cur.lastrowid
            db.commit()
            return row_id
        finally:
            db.close()
    except Exception:
        log.exception("command_ledger log failed (non-fatal)")
        return None


def _ledger_result(row_id, status, *, after_state=None,
                   verification_status=None, verification_detail=None):
    """Update a command_ledger row with its outcome. Best-effort."""
    if not row_id:
        return
    try:
        db = _db()
        try:
            db.execute(
                "UPDATE command_ledger SET status=?, after_state=?, "
                "verification_status=?, verification_detail=?, completed_at=? WHERE id=?",
                (status,
                 json.dumps(after_state) if after_state is not None else None,
                 verification_status, verification_detail, _now(), row_id),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("command_ledger result update failed (non-fatal)")


def _verify_status(success, out):
    """Map an action result to a verification status. A synchronous success (e.g. an LDAP
    modify like unlock/disable) IS verified — the change committed. Only an agent dispatch
    whose result we never captured (queued offline, or fire-and-forget websocket) is
    'unverifiable'. Device scripts with a captured exit_code are verified on success."""
    if not success:
        return "failed"
    if isinstance(out, dict) and "exit_code" in out:
        return "verified"
    if isinstance(out, dict) and out.get("delivered") in ("queue", "websocket"):
        return "unverifiable"
    return "verified"


# ── Agent dispatch (gateway-first, rmm_commands fallback) ────────────────────────
def _dispatch_to_agent(agent_id: str, online: bool, shell: str, code: str,
                       *, asset_id=None, reason="workflow", timeout_s=120,
                       wait_result=False) -> tuple:
    """Send a script to an agent via the WS gateway, falling back to the rmm_commands
    queue when the agent is not connected. Mirrors api_rmm_send_command's dual-path.

    Returns (success: bool, output: dict). `success` reflects DISPATCH (and, when
    wait_result=True and the agent is live, the script's exit_code==0). For the
    offline queue path success means "queued" — the agent runs it on next heartbeat.
    """
    import urllib.request as _ur, urllib.error as _err

    # Open an rmm_session so results can be correlated via rmm_event.session_id.
    session_id = 0
    try:
        db = _db()
        try:
            cur = db.execute(
                "INSERT INTO rmm_session (asset_id, reason, started_at) VALUES (?,?,?)",
                (asset_id, reason, _now()),
            )
            session_id = cur.lastrowid or 0
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("failed to open rmm_session (continuing with session_id=0)")

    # --- Try WebSocket gateway first (immediate delivery) ---
    gateway_ok = False
    gw_error = None
    try:
        from utils import RMM_GATEWAY_INTERNAL
        gw_url = f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}"
        body = json.dumps({
            "type": "run_script",
            "shell": shell,
            "code": code,
            "timeout": int(timeout_s),
            "session_id": session_id,
        }).encode()
        req = _ur.Request(gw_url, data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        with _ur.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read())
        gateway_ok = bool(resp.get("ok"))
        if not gateway_ok:
            gw_error = resp.get("error")
    except _err.HTTPError as e:
        # 404 == "Agent not connected" -> fall through to the queue.
        gw_error = f"gateway {e.code}"
    except Exception as e:
        gw_error = str(e)

    if gateway_ok:
        if wait_result:
            result = _wait_for_script_result(session_id, timeout_s)
            if result is None:
                return False, {"delivered": "websocket", "session_id": session_id,
                               "error": "Timed out waiting for script_result"}
            exit_code = int(result.get("exit_code", 1) or 1)
            out = {
                "delivered": "websocket", "session_id": session_id,
                "exit_code": exit_code,
                "stdout": (result.get("stdout") or "")[:200000],   # generous — collector inventories (GPOs, DCs) can be large
                "stderr": (result.get("stderr") or "")[:8000],
            }
            return (exit_code == 0), out
        return True, {"delivered": "websocket", "session_id": session_id}

    # --- Fallback: queue in rmm_commands (picked up on next 5-min heartbeat) ---
    try:
        db = _db()
        try:
            db.execute(
                "INSERT INTO rmm_commands (agent_id, command, command_type, status, created_at) "
                "VALUES (?,?,?,'pending',?)",
                (agent_id, code, "powershell" if shell == "powershell" else "shell", _now()),
            )
            db.commit()
        finally:
            db.close()
        return True, {"delivered": "queue", "session_id": session_id,
                      "note": "agent offline — queued for next heartbeat",
                      "gateway_error": gw_error}
    except Exception as e:
        return False, {"error": f"dispatch failed: {e}", "gateway_error": gw_error}


def _wait_for_script_result(session_id: int, timeout_s: int):
    """Poll rmm_event for the agent's script_result. Mirrors settings_scripts._wait_for_script_result."""
    if not session_id:
        return None
    deadline = time.time() + max(5, int(timeout_s) + 15)
    while time.time() < deadline:
        db = _db()
        try:
            row = db.execute(
                "SELECT data_json FROM rmm_event "
                "WHERE session_id=? AND actor_type='agent' AND event_type='script_result' "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            db.close()
        if row:
            try:
                return json.loads(row["data_json"] or "{}")
            except Exception:
                return {"stdout": "", "stderr": "Invalid result payload", "exit_code": 1}
        time.sleep(1.0)
    return None


def _device_action(action_type: str, tool: str, asset_id, shell: str, code: str,
                   *, risk_tier="medium", reason="workflow", timeout_s=120,
                   wait_result=False, extra_output=None, ctx=None) -> tuple:
    """Shared body for every device (agent) action: resolve agent, ledger, dispatch,
    record result. Returns (success, output)."""
    agent_id, resolved_asset, online = _resolve_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No RMM agent found for device (asset_id=%s)" % asset_id}

    correlation_id = (ctx or {}).get("_correlation_id")
    requested_by = (ctx or {}).get("_requested_by", "workflow_engine")
    # NOTE: the risk-scored approval gate lives one level up, at the single action
    # dispatch point in _run_workflow (so it covers identity actions too, not just
    # device scripts). By the time we reach here the action is either auto-tier or
    # an approved replay (ctx['_approved'] is set). We record the dispatch in the
    # ledger as 'auto' here; the parked/approved row was written by the gate.
    led = _ledger_log(
        tool, action_type, object_type="asset", object_id=resolved_asset,
        requested_by=requested_by, planned_by="workflow_engine",
        risk_tier=risk_tier, approval_status="auto",
        correlation_id=correlation_id, status="dispatched",
    )

    success, output = _dispatch_to_agent(
        agent_id, online, shell, code,
        asset_id=resolved_asset, reason=reason, timeout_s=timeout_s,
        wait_result=wait_result,
    )
    output = {"agent_id": agent_id, **(extra_output or {}), **output}

    # Ledger outcome + verification status.
    _ledger_result(led, "succeeded" if success else "failed",
                   after_state=output, verification_status=_verify_status(success, output))
    return success, output


# ── Approval resolution (called from the Approvals queue, request context) ───────
def _claim_pending(row_id, *, to_approval_status, to_status):
    """Atomically claim a pending approval (compare-and-set). Returns the parsed
    before_state dict if THIS caller won the claim, else None (already resolved/raced).

    The conditional UPDATE is the single source of truth for who wins — two admins
    (or two workers) clicking Approve cannot both pass, so the action can't dispatch
    twice. pg_db only rewrites INSERTs, so our explicit RETURNING survives."""
    try:
        db = _db()
        try:
            row = db.execute(
                "UPDATE command_ledger SET approval_status=?, status=? "
                "WHERE id=? AND approval_status='pending' AND status='awaiting_approval' "
                "RETURNING before_state",
                (to_approval_status, to_status, row_id),
            ).fetchone()
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("approval claim failed")
        return None
    if not row:
        return None
    bs = row["before_state"]
    # command_ledger.before_state is a Postgres json column — psycopg2 returns it already
    # parsed as a dict. Only json.loads when it actually came back as a string/bytes.
    if isinstance(bs, (str, bytes, bytearray)):
        try:
            bs = json.loads(bs or "{}")
        except Exception:
            return {}
    return bs or {}


def approve_action(row_id, approver):
    """Approve a parked action: run JUST that action via its handler, then RESUME the rest
    of the parked workflow run (full DAG resume). Runs in a background thread so a long
    dispatch never blocks the request worker. Returns (claimed, info).

    The approved action runs by calling its handler directly — the gate lives in _drive,
    not the handler, so the direct call executes it. The resumed remainder runs through
    _drive normally, so any FURTHER medium/high action parks again for its own approval."""
    before = _claim_pending(row_id, to_approval_status="approved", to_status="running")
    if before is None:
        return False, {"error": "Not a pending approval (already resolved, denied, or not found)."}
    replay = (before or {}).get("replay") or {}
    action_type = replay.get("action_type")
    if not action_type or action_type not in ACTION_MAP:
        _ledger_set_status(row_id, status="failed", verification_status="failed",
                           verification_detail=f"approved by {approver}; no replayable action ({action_type})",
                           complete=True)
        return True, {"error": f"Approved, but no replayable action ({action_type})."}

    def _run():
        config = replay.get("config") or {}
        ctx = dict(replay.get("ctx") or {})
        step_id = replay.get("step_id")
        run_id = replay.get("run_id")
        try:
            success, output = ACTION_MAP[action_type](config, ctx)
        except Exception as e:
            success, output = False, {"error": str(e)}
        out = output if isinstance(output, dict) else {"result": str(output)}
        _ledger_result(row_id, "succeeded" if success else "failed",
                       after_state=out, verification_status=_verify_status(success, out),
                       verification_detail=f"approved by {approver}")
        if step_id:
            _update_step(step_id, "completed" if success else "failed", out)
        # Resume the rest of the run (or fail it if the approved action failed).
        if run_id:
            if success:
                merged = {**ctx, **{k: v for k, v in out.items() if not k.startswith("_")}}
                try:
                    resume_run(run_id, replay.get("node_id"), merged,
                               replay.get("visited") or [], replay.get("queue") or [])
                except Exception:
                    log.exception("resume_run failed for run %s", run_id)
                    _finish_run(run_id, "failed", "resume failed after approval")
            else:
                _finish_run(run_id, "failed", out.get("error") or "approved action failed")

    threading.Thread(target=_run, daemon=True, name=f"approve-{row_id}").start()
    return True, {"approved": True, "action_type": action_type, "by": approver}


def deny_action(row_id, approver, reason=""):
    """Deny a parked action — atomic claim, no dispatch."""
    before = _claim_pending(row_id, to_approval_status="denied", to_status="denied")
    if before is None:
        return False, {"error": "Not a pending approval (already resolved or not found)."}
    detail = f"denied by {approver}" + (f": {reason}" if reason else "")
    _ledger_set_status(row_id, verification_status="n/a", verification_detail=detail, complete=True)
    return True, {"denied": True, "by": approver}


def _parse_block_rule(reason: str):
    """'Transport rule: SECURITY - Block Known Bad IPs B' -> 'SECURITY - Block Known Bad IPs B'."""
    r = (reason or "").strip()
    if ":" in r and r.lower().startswith("transport rule"):
        return r.split(":", 1)[1].strip() or None
    return None


def _close_qmsg_ticket(db, message_id, note):
    """Close the open [qmsg:<id>] release-request ticket + add an internal note. Uses the
    caller's db handle (no commit here)."""
    marker = "[qmsg:%s]" % message_id
    trow = db.execute(
        "SELECT id FROM support_ticket WHERE source='quarantine' "
        "AND status NOT IN ('Closed','Merged') AND description LIKE ? ORDER BY id DESC LIMIT 1",
        ("%" + marker + "%",)).fetchone()
    if not trow:
        return None
    tid = trow["id"]
    db.execute("UPDATE support_ticket SET status='Closed', closed_at=?, updated_at=? WHERE id=?",
               (_now(), _now(), tid))
    db.execute("INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
               "VALUES (?,?,?,?,?,?)", (tid, None, note, True, False, _now()))
    return tid


# Email-remediation modes the Approvals queue can pick for a parked release_quarantine row.
EMAIL_REMEDIATION_MODES = ("release", "whitelist_domain", "remove_blocklist")


def resolve_email_remediation(row_id, approver, mode):
    """Resolve a parked release_quarantine approval with a CHOSEN remediation (the operator
    picked an option on the card): release the message, whitelist the sender domain (add a
    transport-rule exception + central allow), or remove it from the blacklist rule. Claims
    the row atomically, runs the chosen action in a background thread (EXO calls are slow),
    finalizes the ledger + closes the ticket. Returns (claimed, info); the UI polls status."""
    if mode not in EMAIL_REMEDIATION_MODES:
        return False, {"error": f"unknown remediation mode: {mode}"}
    before = _claim_pending(row_id, to_approval_status="approved", to_status="running")
    if before is None:
        return False, {"error": "Not a pending approval (already resolved, denied, or not found)."}
    replay = (before or {}).get("replay") or {}
    ctx = dict(replay.get("ctx") or {})
    message_id = ctx.get("message_id")
    if not message_id:
        _ledger_result(row_id, "failed", after_state={"error": "no message_id in parked ctx"},
                       verification_status="failed", verification_detail=f"{mode} by {approver}")
        return True, {"error": "Parked row has no message_id."}

    def _run():
        try:
            # If they chose plain release, reuse the release action verbatim (handles the
            # Defender call + ticket close + email.released learn event).
            if mode == "release":
                cfg = {"message_id": message_id, "recipient": ctx.get("recipient", "")}
                success, out = _action_release_quarantine(cfg, ctx)
            else:
                success, out = _run_unblock(message_id, mode, approver)
        except Exception as e:
            success, out = False, {"error": str(e)}
        out = out if isinstance(out, dict) else {"result": str(out)}
        _ledger_result(row_id, "succeeded" if success else "failed",
                       after_state=out, verification_status=_verify_status(success, out),
                       verification_detail=f"{mode} by {approver}")
        if replay.get("step_id"):
            _update_step(replay["step_id"], "completed" if success else "failed", out)
        if replay.get("run_id"):
            _finish_run(replay["run_id"], "completed" if success else "failed",
                        f"email remediation: {mode} ({'ok' if success else 'failed'})")

    threading.Thread(target=_run, daemon=True, name=f"resolve-{row_id}").start()
    return True, {"resolving": True, "mode": mode, "by": approver}


def _run_unblock(message_id, mode, approver):
    """Whitelist the sender domain (transport-rule exception + central allow) or remove it
    from the blacklist rule, then close the ticket. Returns (success, output)."""
    import exo_service
    db = _db()
    try:
        row = db.execute("SELECT sender_address, sender_domain, quarantine_reason FROM quarantine_message "
                         "WHERE message_id=?", (message_id,)).fetchone()
        if not row:
            return False, {"error": "message not found"}
        rule = _parse_block_rule(row["quarantine_reason"])
        if not rule:
            return False, {"error": f"could not identify a transport rule from reason: "
                                    f"{row['quarantine_reason']!r}. Manual unblock needed."}
        # Which domain to whitelist matters: transport rules default to SenderAddressLocation
        # 'Header', so they match the VISIBLE From-header domain (from sender_address) — NOT
        # the envelope domain that our sender_domain column captures for relayed mail (e.g.
        # Constant Contact: From ieeesscs.ccsend.com but envelope in.constantcontact.com).
        # Except BOTH so the rule matches whether it's Header- or Envelope-scoped.
        from_addr = (row["sender_address"] or "")
        header_domain = from_addr.split("@")[-1].strip().lower() if "@" in from_addr else ""
        envelope_domain = (row["sender_domain"] or "").strip().lower()
        domains = []
        for d in (header_domain, envelope_domain):
            if d and d not in domains:
                try:
                    domains.append(exo_service.safe_domain(d))
                except Exception:
                    pass
        if not domains:
            return False, {"error": "no usable sender domain on message"}

        results = {}
        if mode == "whitelist_domain":
            # Add each candidate domain as an exception on the blocking rule (the definitive
            # fix — overrides the match even for content/FromAddressContains rules) and to the
            # central allow list (best-effort). The From-header domain (domains[0]) is primary.
            results["exceptions"] = {}
            primary = None
            for d in domains:
                res = exo_service.add_domain_exception(rule, d)
                results["exceptions"][d] = res
                if primary is None:
                    primary = res
                if res.get("ok"):
                    try:
                        exo_service.add_to_allowlist(d)
                    except Exception as e:
                        log.warning("allow-list add failed for %s: %s", d, e)
            note = (f"Whitelisted {', '.join(domains)}: added as exception(s) on '{rule}' "
                    f"+ '{exo_service.ALLOW_RULE}' via EXO app-only. Future mail will flow; "
                    f"the sender can resend this message. (approved: {approver})")
            verb = "whitelisted"
            log_domain = domains[0]
        else:  # remove_blocklist
            primary = exo_service.remove_from_blacklist(rule, domains[0])
            results["removed"] = primary
            note = (f"Removed {domains[0]} from blacklist rule '{rule}' via EXO app-only. "
                    f"The sender can resend. (approved: {approver})")
            verb = "removed from blocklist"
            log_domain = domains[0]

        if not primary or not primary.get("ok"):
            return False, {"error": (primary or {}).get("error", "EXO change failed"), "detail": results}

        # Persist an audit row + close the ticket.
        try:
            db.execute(
                "INSERT INTO domain_unblock_request (domain, message_id, requested_by, reason, "
                "status, created_at, decided_by, decided_at, decision_note) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (log_domain, message_id, approver, rule, "applied", _now(), approver, _now(), note))
        except Exception:
            log.exception("domain_unblock_request insert failed (non-fatal)")
        tid = _close_qmsg_ticket(db, message_id, note)
        db.commit()
        return True, {"domains": domains, "rule": rule, "action": verb, "ticket_id": tid, **results}
    except Exception as e:
        db.rollback()
        return False, {"error": str(e)}
    finally:
        db.close()


def _ledger_set_status(row_id, *, approval_status=None, status=None,
                       verification_status=None, verification_detail=None,
                       complete=False):
    """Patch approval/status fields on a command_ledger row. Best-effort."""
    sets, vals = [], []
    if approval_status is not None:
        sets.append("approval_status=?"); vals.append(approval_status)
    if status is not None:
        sets.append("status=?"); vals.append(status)
    if verification_status is not None:
        sets.append("verification_status=?"); vals.append(verification_status)
    if verification_detail is not None:
        sets.append("verification_detail=?"); vals.append(verification_detail)
    if complete:
        sets.append("completed_at=?"); vals.append(_now())
    if not sets:
        return
    try:
        db = _db()
        try:
            db.execute(f"UPDATE command_ledger SET {', '.join(sets)} WHERE id=?",
                       (*vals, row_id))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("command_ledger status update failed (non-fatal)")


# ── Rollback (reverse a completed action by parking its inverse for approval) ────
_INVERSE_ACTIONS = {
    "disable_ad_user":   "enable_ad_user",
    "enable_ad_user":    "disable_ad_user",
    "add_to_group":      "remove_from_group",
    "remove_from_group": "add_to_group",
}


def is_reversible(action_type):
    """True if we can automatically reverse this action type."""
    return action_type in _INVERSE_ACTIONS


def create_rollback(ledger_id, requested_by="operator"):
    """Park the INVERSE of a completed ledger action for approval — reusing the approval
    machinery, so the rollback itself passes through /approvals (it's a real change too).
    Returns the new pending ledger id, or None if not reversible / not found."""
    db = _db()
    try:
        row = db.execute(
            "SELECT tool, action_type, object_type, object_id, before_state, status "
            "FROM command_ledger WHERE id=?", (ledger_id,)).fetchone()
    finally:
        db.close()
    if not row:
        return None
    inv = _INVERSE_ACTIONS.get(row["action_type"])
    if not inv:
        return None
    # Recover the original action's config from its parked replay snapshot (username,
    # group_name, etc. carry over to the inverse).
    before = row["before_state"]
    if isinstance(before, (str, bytes, bytearray)):
        try:
            before = json.loads(before or "{}")
        except Exception:
            before = {}
    config = dict(((before or {}).get("replay") or {}).get("config") or {})
    eff_tier = approval.risk_tier_for(inv)
    led = _ledger_log(
        row["tool"] or inv, inv, object_type=row["object_type"], object_id=row["object_id"],
        requested_by=requested_by, planned_by="rollback", risk_tier=eff_tier,
        approval_status="pending", correlation_id=f"rollback-of-{ledger_id}",
        status="awaiting_approval",
        before_state={"replay": {"run_id": None, "node_id": None, "step_id": None,
                                 "action_type": inv, "config": config, "ctx": {},
                                 "visited": [], "queue": []},
                      "policy": f"rollback of ledger #{ledger_id} ({row['action_type']})",
                      "node_label": f"Rollback: {inv.replace('_', ' ')}"},
    )
    if led:
        _notify_parked(inv, eff_tier, led, f"Rollback of #{ledger_id}")
    return led


# ── NEW ACTION HANDLERS ────────────────────────────────────────────────────────

def _action_create_user(config: dict, ctx: dict) -> tuple:
    """Create a new user in Active Directory."""
    username   = _render(config.get("username", ""), ctx)
    first_name = _render(config.get("first_name", ""), ctx)
    last_name  = _render(config.get("last_name", ""), ctx)
    password   = _render(config.get("password", ""), ctx)
    ou         = _render(config.get("ou", ""), ctx)
    if not username:
        return False, {"error": "username is required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        base_ou = ou or ad.get("ad_base_dn", "")
        dn = f"CN={first_name} {last_name},{base_ou}".strip(",")
        attrs = {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "sAMAccountName": username,
            "userPrincipalName": f"{username}@{ad.get('ad_domain', '')}",
            "givenName": first_name,
            "sn": last_name,
            "displayName": f"{first_name} {last_name}".strip(),
        }
        conn.add(dn, attributes=attrs)
        if password:
            # Set password and enable account
            conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [f'"{password}"'.encode("utf-16-le")])]})
            conn.modify(dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [512])]})
        conn.unbind()
        # New AD account → kick the Entra (AAD Connect) delta sync so it propagates to 365
        # without a manual run on the sync host. Best-effort; never fails the user creation.
        entra = "skipped"
        try:
            ok, out = _action_azure_sync({}, ctx)
            entra = "triggered" if ok else f"sync error: {out.get('error')}"
        except Exception as e:
            entra = f"sync error: {e}"
        return True, {"username": username, "dn": dn, "created": True, "entra_sync": entra}
    except Exception as e:
        return False, {"error": str(e)}


def _action_enable_ad_user(config: dict, ctx: dict) -> tuple:
    """Enable a disabled Active Directory user account."""
    username = _render(config.get("username", ctx.get("username", "")), ctx)
    if not username:
        return False, {"error": "No username provided"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName", "userAccountControl"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        dn  = conn.entries[0].distinguishedName.value
        uac = int(conn.entries[0].userAccountControl.value)
        conn.modify(dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [uac & ~2])]})
        conn.unbind()
        return True, {"username": username, "dn": dn, "enabled": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_reset_password(config: dict, ctx: dict) -> tuple:
    """Reset an AD user's password."""
    username    = _render(config.get("username", ctx.get("username", "")), ctx)
    new_password = _render(config.get("new_password", ""), ctx)
    if not username or not new_password:
        return False, {"error": "username and new_password are required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        dn = conn.entries[0].distinguishedName.value
        pw_encoded = f'"{new_password}"'.encode("utf-16-le")
        conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [pw_encoded])]})
        conn.unbind()
        return True, {"username": username, "password_reset": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_unlock_account(config: dict, ctx: dict) -> tuple:
    """Unlock a locked-out Active Directory account."""
    username = _render(config.get("username", ctx.get("username", "")), ctx)
    if not username:
        return False, {"error": "No username provided"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        dn = conn.entries[0].distinguishedName.value
        conn.modify(dn, {"lockoutTime": [(ldap3.MODIFY_REPLACE, [0])]})
        conn.unbind()
        return True, {"username": username, "unlocked": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_add_to_group(config: dict, ctx: dict) -> tuple:
    """Add an AD user to a security/distribution group."""
    username   = _render(config.get("username", ctx.get("username", "")), ctx)
    group_name = _render(config.get("group_name", ""), ctx)
    if not username or not group_name:
        return False, {"error": "username and group_name are required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        # Find user DN
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        user_dn = conn.entries[0].distinguishedName.value
        # Find group DN
        conn.search(ad["ad_base_dn"], f"(cn={group_name})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"Group {group_name} not found"}
        group_dn = conn.entries[0].distinguishedName.value
        conn.modify(group_dn, {"member": [(ldap3.MODIFY_ADD, [user_dn])]})
        conn.unbind()
        return True, {"username": username, "group": group_name, "added": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_remove_from_group(config: dict, ctx: dict) -> tuple:
    """Remove an AD user from a security/distribution group."""
    username   = _render(config.get("username", ctx.get("username", "")), ctx)
    group_name = _render(config.get("group_name", ""), ctx)
    if not username or not group_name:
        return False, {"error": "username and group_name are required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        user_dn = conn.entries[0].distinguishedName.value
        conn.search(ad["ad_base_dn"], f"(cn={group_name})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"Group {group_name} not found"}
        group_dn = conn.entries[0].distinguishedName.value
        conn.modify(group_dn, {"member": [(ldap3.MODIFY_DELETE, [user_dn])]})
        conn.unbind()
        return True, {"username": username, "group": group_name, "removed": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_reboot_device(config: dict, ctx: dict) -> tuple:
    """Reboot a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    delay_s  = int(config.get("delay_seconds", 0))
    script = f"Start-Sleep -Seconds {delay_s}; Restart-Computer -Force" if delay_s else "Restart-Computer -Force"
    # Reboot kills the agent connection, so the script_result rarely returns — don't wait.
    return _device_action(
        "reboot_device", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="high", reason="workflow: reboot device",
        wait_result=False, extra_output={"action": "reboot"}, ctx=ctx,
    )


def _action_shutdown_device(config: dict, ctx: dict) -> tuple:
    """Shut down a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    return _device_action(
        "shutdown_device", "rmm.run_script", asset_id, "powershell", "Stop-Computer -Force",
        risk_tier="high", reason="workflow: shutdown device",
        wait_result=False, extra_output={"action": "shutdown"}, ctx=ctx,
    )


def _action_lock_device(config: dict, ctx: dict) -> tuple:
    """Lock workstation or enable BitLocker on a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    mode     = config.get("mode", "lock")   # lock | bitlocker
    if mode == "bitlocker":
        script = "Enable-BitLocker -MountPoint 'C:' -EncryptionMethod XtsAes256 -UsedSpaceOnlyEncryption -TpmProtector"
    else:
        script = "rundll32.exe user32.dll,LockWorkStation"
    return _device_action(
        "lock_device", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="high" if mode == "bitlocker" else "medium",
        reason=f"workflow: lock device ({mode})",
        wait_result=(mode == "bitlocker"), extra_output={"action": mode}, ctx=ctx,
    )


def _action_deploy_software(config: dict, ctx: dict) -> tuple:
    """Deploy software to a device via RMM agent (Chocolatey, MSI, or EXE)."""
    asset_id  = config.get("asset_id") or ctx.get("asset_id")
    method    = config.get("method", "chocolatey")   # chocolatey | msi | exe | winget
    package   = _render(config.get("package", ""), ctx)
    args      = _render(config.get("args", ""), ctx)
    if not package:
        return False, {"error": "Package name/path is required"}
    if method == "chocolatey":
        script = f"choco install {package} -y --no-progress {args}".strip()
    elif method == "winget":
        script = f"winget install --id {package} --silent --accept-source-agreements --accept-package-agreements {args}".strip()
    elif method == "msi":
        script = f"msiexec /i \"{package}\" /qn /norestart {args}".strip()
    else:  # exe
        script = f"Start-Process -FilePath \"{package}\" -ArgumentList \"{args}\" -Wait -NoNewWindow"
    return _device_action(
        "deploy_software", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="medium", reason=f"workflow: deploy software ({package})",
        timeout_s=600, wait_result=True,
        extra_output={"package": package, "method": method}, ctx=ctx,
    )


def _action_uninstall_software(config: dict, ctx: dict) -> tuple:
    """Uninstall software from a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    method   = config.get("method", "chocolatey")
    package  = _render(config.get("package", ""), ctx)
    if not package:
        return False, {"error": "Package name is required"}
    if method == "chocolatey":
        script = f"choco uninstall {package} -y --no-progress"
    elif method == "winget":
        script = f"winget uninstall --id {package} --silent"
    else:
        script = f"Get-WmiObject -Class Win32_Product | Where-Object {{ $_.Name -like '*{package}*' }} | ForEach-Object {{ $_.Uninstall() }}"
    return _device_action(
        "uninstall_software", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="medium", reason=f"workflow: uninstall software ({package})",
        timeout_s=600, wait_result=True,
        extra_output={"package": package}, ctx=ctx,
    )


# Only a bare filename inside C:\ITTOOLS is allowed — no path separators, no traversal.
_ITTOOLS_FILE_RE = re.compile(r"^[A-Za-z0-9 ._()\-]{1,160}\.(exe|msi|bat|cmd|ps1|msix|appx)$", re.I)


def _action_install_local_tool(config: dict, ctx: dict) -> tuple:
    """Install a user-staged installer from C:\\ITTOOLS on a device, as SYSTEM (no UAC).

    The user drops the installer in the sanctioned C:\\ITTOOLS folder and requests it; this
    runs ONLY from that folder (never an arbitrary path), verifies the SHA-256 matches what
    was approved (defeats swap-after-approval), Unblock-File's it (clears Mark-of-the-Web /
    SmartScreen), then runs it silently. Medium-risk → parks at /approvals."""
    asset_id  = config.get("asset_id") or ctx.get("asset_id")
    file_name = _render(config.get("file_name", ctx.get("file_name", "")), ctx).strip()
    args      = _render(config.get("args", ctx.get("args", "")), ctx).strip()
    sha256    = (_render(config.get("sha256", ctx.get("sha256", "")), ctx).strip() or "").upper()
    if not file_name or not _ITTOOLS_FILE_RE.match(file_name):
        return False, {"error": f"Invalid installer filename (must be a bare name in C:\\ITTOOLS): {file_name!r}"}
    if not sha256 or not re.match(r"^[A-F0-9]{64}$", sha256):
        return False, {"error": "A valid SHA-256 is required (computed at request time)."}

    # Build the install script. Values are validated above; arg tokens are emitted as a
    # quoted PS array so multi-flag silent args (e.g. "/qn /norestart") pass correctly.
    arg_tokens = args.split()
    ps_args = ", ".join("'" + t.replace("'", "''") + "'" for t in arg_tokens)
    fn_lit = file_name.replace("'", "''")
    run = ("$p = Start-Process msiexec.exe -ArgumentList '/i', ('\"'+$file+'\"'), '/qn', '/norestart' -Wait -PassThru"
           if file_name.lower().endswith(".msi")
           else ("$p = Start-Process -LiteralPath $file" +
                 (f" -ArgumentList @({ps_args})" if ps_args else "") +
                 " -Wait -PassThru -WindowStyle Hidden"))
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$dir='C:\\ITTOOLS'\n"
        f"$file=Join-Path $dir '{fn_lit}'\n"
        "if (-not $file.StartsWith($dir + '\\')) { Write-Output 'PATH_OUTSIDE_ITTOOLS'; exit 9 }\n"
        "if (-not (Test-Path -LiteralPath $file)) { Write-Output 'FILE_NOT_FOUND'; exit 2 }\n"
        "$h=(Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToUpper()\n"
        f"if ($h -ne '{sha256}') {{ Write-Output \"HASH_MISMATCH actual=$h\"; exit 3 }}\n"
        "Unblock-File -LiteralPath $file\n"
        f"{run}\n"
        "Write-Output ('INSTALL_EXIT=' + $p.ExitCode)\n"
        "exit $p.ExitCode\n"
    )
    success, output = _device_action(
        "install_local_tool", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="medium", reason=f"workflow: install C:\\ITTOOLS\\{file_name}",
        timeout_s=1800, wait_result=True,
        extra_output={"file_name": file_name, "sha256": sha256, "args": args}, ctx=ctx,
    )
    # On success close the originating software-request ticket with a result note.
    ticket_id = config.get("ticket_id") or ctx.get("ticket_id")
    if success and ticket_id:
        try:
            db = _db()
            db.execute("UPDATE support_ticket SET status='Closed', closed_at=?, updated_at=? WHERE id=?",
                       (_now(), _now(), ticket_id))
            db.execute("INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (ticket_id, None,
                        "Installed %s on the device (approved via the Action Center). %s Auto-closed."
                        % (file_name, output.get("result") or output.get("stdout") or ""),
                        True, False, _now()))
            db.commit(); db.close()
        except Exception:
            log.exception("install_local_tool: ticket close failed for %s", ticket_id)
    return success, output


def _action_apply_fix(config: dict, ctx: dict) -> tuple:
    """Run a vetted one-click fix from the script library (rmm_script_library, is_fix=true)
    on a device, as SYSTEM. Used for admin-approved remediation of needs-admin issues (e.g.
    re-enable touchpad) raised in a ticket. Runs the library script BY ID — not arbitrary
    code — so the approval is auditable to a known, curated fix. Medium-risk → parks."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    fix_id   = config.get("fix_id") or ctx.get("fix_id")
    try:
        fix_id = int(fix_id)
    except (TypeError, ValueError):
        return False, {"error": "fix_id is required"}
    db = _db()
    try:
        row = db.execute("SELECT name, shell, script_content FROM rmm_script_library "
                         "WHERE id=? AND is_fix=? AND is_active=?", (fix_id, True, True)).fetchone()
    finally:
        db.close()
    if not row:
        return False, {"error": f"fix #{fix_id} not found or not an active fix"}
    script = row["script_content"] or ""
    if not script.strip():
        return False, {"error": f"fix #{fix_id} has no script"}
    success, output = _device_action(
        "apply_fix", "rmm.run_script", asset_id, (row["shell"] or "powershell"), script,
        risk_tier="medium", reason=f"workflow: apply fix '{row['name']}' (#{fix_id})",
        timeout_s=600, wait_result=True,
        extra_output={"fix_id": fix_id, "fix_name": row["name"]}, ctx=ctx,
    )
    ticket_id = config.get("ticket_id") or ctx.get("ticket_id")
    if success and ticket_id:
        try:
            db = _db()
            db.execute("UPDATE support_ticket SET status='Closed', closed_at=?, updated_at=? WHERE id=?",
                       (_now(), _now(), ticket_id))
            db.execute("INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (ticket_id, None,
                        "Applied fix '%s' on the device (approved via the Action Center). %s Auto-closed."
                        % (row["name"], output.get("result") or output.get("stdout") or ""),
                        True, False, _now()))
            db.commit(); db.close()
        except Exception:
            log.exception("apply_fix: ticket close failed for %s", ticket_id)
    return success, output


def _action_run_script(config: dict, ctx: dict) -> tuple:
    """Run a PowerShell or Bash script on a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    script   = _render(config.get("script", ""), ctx)
    lang     = config.get("language", "powershell")
    if not script:
        return False, {"error": "Script content is required"}
    shell = "powershell" if lang == "powershell" else "bash"
    return _device_action(
        "run_script", "rmm.run_script", asset_id, shell, script,
        risk_tier="medium", reason="workflow: run script",
        wait_result=True, extra_output={"language": lang}, ctx=ctx,
    )


def _action_apply_gpo(config: dict, ctx: dict) -> tuple:
    """Force a Group Policy update on a device or DC via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    target   = config.get("target", "device")   # device | dc
    force    = config.get("force", True)
    flag = "/force" if force else ""
    script = f"gpupdate {flag}" if target == "device" else f"Invoke-GPUpdate -Computer $env:COMPUTERNAME {'/Force' if force else ''}"
    return _device_action(
        "apply_gpo", "rmm.run_script", asset_id, "powershell", script,
        risk_tier="medium", reason=f"workflow: gpupdate ({target})",
        wait_result=True, extra_output={"action": "gpupdate", "target": target}, ctx=ctx,
    )


def _action_send_teams(config: dict, ctx: dict) -> tuple:
    """Send a message to a Microsoft Teams channel via webhook."""
    webhook_url = _render(config.get("webhook_url", ""), ctx)
    title       = _render(config.get("title", "Workflow Alert"), ctx)
    message     = _render(config.get("message", ""), ctx)
    color       = config.get("color", "0078D4")
    if not webhook_url:
        return False, {"error": "Teams webhook URL is required"}
    try:
        import urllib.request
        payload = json.dumps({
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": title,
            "sections": [{"activityTitle": title, "activityText": message}],
        }).encode()
        req = urllib.request.Request(webhook_url, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, {"status": r.status, "title": title}
    except Exception as e:
        return False, {"error": str(e)}


def _action_send_slack(config: dict, ctx: dict) -> tuple:
    """Send a message to a Slack channel via webhook."""
    webhook_url = _render(config.get("webhook_url", ""), ctx)
    message     = _render(config.get("message", ""), ctx)
    channel     = config.get("channel", "")
    if not webhook_url:
        return False, {"error": "Slack webhook URL is required"}
    try:
        import urllib.request
        payload = {"text": message}
        if channel:
            payload["channel"] = channel
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(webhook_url, data=data,
                                      headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, {"status": r.status, "message": message}
    except Exception as e:
        return False, {"error": str(e)}


def _action_close_ticket(config: dict, ctx: dict) -> tuple:
    """Close a support ticket."""
    ticket_id = config.get("ticket_id") or ctx.get("ticket_id")
    resolution = _render(config.get("resolution", "Resolved by workflow"), ctx)
    if not ticket_id:
        return False, {"error": "No ticket_id"}
    try:
        db = _db()
        db.execute(
            "UPDATE support_ticket SET status='closed', updated_at=? WHERE id=?",
            (_now(), ticket_id),
        )
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "closed": True, "resolution": resolution}
    except Exception as e:
        return False, {"error": str(e)}


def _action_assign_ticket(config: dict, ctx: dict) -> tuple:
    """Assign a ticket to a user."""
    ticket_id  = config.get("ticket_id") or ctx.get("ticket_id")
    assigned_to = _render(config.get("assigned_to", ""), ctx)
    if not ticket_id:
        return False, {"error": "No ticket_id"}
    if not assigned_to:
        return False, {"error": "assigned_to is required"}
    try:
        db = _db()
        db.execute(
            "UPDATE support_ticket SET assigned_to=?, status='in_progress', updated_at=? WHERE id=?",
            (assigned_to, _now(), ticket_id),
        )
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "assigned_to": assigned_to}
    except Exception as e:
        return False, {"error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Auto-resolve: when a remediation action succeeds for a ticket-driven run, close
# the originating ticket + notify the reporter — mirroring the human resolve path
# (status='Closed', closed_at, a ticket_note, a status_changed activity, and a
# ticket.resolved bus event so the Knowledge Agent's Learn step fires). Raw-pg only,
# so it runs in the workflow daemon thread without a Flask app context.
# ──────────────────────────────────────────────────────────────────────────────

# Actions that, on success, mean the user's problem is actually fixed and the ticket
# can be auto-closed. Read/notify/ticket-bookkeeping actions are deliberately excluded.
_REMEDIATION_ACTIONS = {
    "unlock_account", "reset_password", "enable_ad_user",
    "add_to_group", "remove_from_group",
    "deploy_patch", "deploy_software", "uninstall_software",
    "reboot_device",
}


def _resolution_summary(action_type: str, output: dict, ctx: dict) -> str:
    """Human-readable one-liner describing what the brain did, for the ticket note + email."""
    username = output.get("username") or ctx.get("submitter_sam") or ctx.get("username") or ""
    if action_type == "unlock_account":
        who = f" for {username}" if username else ""
        return f"unlocked the Active Directory account{who}"
    if action_type == "reset_password":
        who = f" for {username}" if username else ""
        return f"reset the Active Directory password{who}"
    if action_type == "enable_ad_user":
        who = f" for {username}" if username else ""
        return f"re-enabled the Active Directory account{who}"
    if action_type in ("add_to_group", "remove_from_group"):
        grp = output.get("group") or ""
        verb = "added to" if action_type == "add_to_group" else "removed from"
        return f"{verb} the group {grp}".strip()
    if action_type in ("deploy_patch", "deploy_software"):
        return "deployed the required update"
    if action_type == "uninstall_software":
        return "removed the offending software"
    if action_type == "reboot_device":
        return "rebooted the affected device"
    return f"completed the {action_type.replace('_', ' ')} remediation"


def _auto_resolve_ticket_after_remediation(action_type: str, output: dict, ctx: dict):
    """Close the ticket in ctx and notify its reporter — only on a successful remediation.
    Best-effort and idempotent: a ticket already Closed/Merged is left untouched and no
    second email is sent. Never raises into the run (logged + swallowed)."""
    if action_type not in _REMEDIATION_ACTIONS:
        return
    ticket_id = ctx.get("ticket_id")
    if not ticket_id:
        return
    try:
        ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        return

    summary = _resolution_summary(action_type, output, ctx)
    note_text = (f"🤖 Brain auto-remediated: {summary}. "
                 f"Resolved automatically — no action needed on your part.")
    try:
        db = _db()
        try:
            row = db.execute(
                "SELECT id, subject, status, reporter_email, reporter_name, category "
                "FROM support_ticket WHERE id=?", (ticket_id,)).fetchone()
            if not row:
                log.warning("auto-resolve: ticket %s not found", ticket_id)
                return
            # Idempotency: never double-close or double-notify a finished ticket.
            if row["status"] in ("Closed", "Merged"):
                log.info("auto-resolve: ticket %s already %s — skipping", ticket_id, row["status"])
                return

            now = _now()
            # 1) conversation note (real Python bools for the NOT NULL boolean columns)
            db.execute(
                "INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (ticket_id, None, note_text, False, False, now))
            # 2) close exactly like the human handler (status='Closed' + closed_at)
            db.execute(
                "UPDATE support_ticket SET status='Closed', closed_at=?, updated_at=? WHERE id=?",
                (now, now, ticket_id))
            # 3) audit activity, attributed to the brain
            db.execute(
                "INSERT INTO ticket_activity (ticket_id, user_id, action, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (ticket_id, None, "status_changed",
                 f"{row['status']} → Closed (auto-remediated by brain)", now))
            db.commit()
        finally:
            db.close()
        log.info("auto-resolve: ticket %s closed after %s", ticket_id, action_type)
    except Exception:
        log.exception("auto-resolve: failed to close ticket %s", ctx.get("ticket_id"))
        return

    # 4) notify the reporting user via the EXISTING mailer (reuse, don't reinvent).
    reporter_email = row["reporter_email"] or ctx.get("submitter_email")
    if reporter_email:
        try:
            from email import send_email  # standalone SMTP helper, app-context-free
            base = "https://tracker.cirque.com"
            html_body = f"""
            <div style="font-family:sans-serif;max-width:600px;">
              <p>Hi {row['reporter_name'] or 'there'},</p>
              <p>Good news — your support ticket
                 <strong>#{ticket_id}: {row['subject']}</strong> has been resolved
                 automatically.</p>
              <blockquote style="border-left:4px solid #0d6efd;padding:10px 16px;
                                 background:#f0f4ff;margin:16px 0;border-radius:4px;">
                <p style="margin:0;">The IT brain {summary}. No further action is needed
                   on your part. If the problem persists, just reply to reopen the ticket.</p>
              </blockquote>
              <p style="margin-top:20px;">
                <a href="{base}/tickets/{ticket_id}"
                   style="background:#0d6efd;color:#fff;padding:8px 16px;
                          text-decoration:none;border-radius:4px;">
                  View Ticket #{ticket_id}
                </a>
              </p>
              <p style="color:#6c757d;font-size:12px;margin-top:24px;">
                Ticket #{ticket_id} · IT Support (automated)
              </p>
            </div>"""
            send_email(
                subject=f"Resolved: [Ticket #{ticket_id}] {row['subject']}",
                recipients=[reporter_email],
                text_body=(f"Hi {row['reporter_name'] or 'there'},\n\n"
                           f"Your support ticket #{ticket_id}: {row['subject']} has been "
                           f"resolved automatically. The IT brain {summary}. "
                           f"No further action is needed. If the problem persists, "
                           f"reply to reopen the ticket.\n\n{base}/tickets/{ticket_id}"),
                html_body=html_body)
            log.info("auto-resolve: resolution email sent to %s for ticket %s",
                     reporter_email, ticket_id)
        except Exception:
            log.exception("auto-resolve: resolution email failed for ticket %s", ticket_id)
    else:
        log.info("auto-resolve: ticket %s has no reporter email — closed without notify", ticket_id)

    # 5) publish ticket.resolved so the Knowledge Agent's Learn step fires — same event
    #    the human resolve path emits. Best-effort; subscribers are idempotent.
    try:
        import event_bus
        event_bus.publish("ticket.resolved", {
            "ticket_id": ticket_id, "subject": row["subject"],
            "category": row["category"], "closed_by": "IT Brain (auto-remediation)",
        }, source="workflow_engine")
    except Exception:
        log.exception("auto-resolve: ticket.resolved publish failed for ticket %s", ticket_id)


def _action_http_request(config: dict, ctx: dict) -> tuple:
    """Generic HTTP request to any URL."""
    url     = _render(config.get("url", ""), ctx)
    method  = config.get("method", "GET").upper()
    body    = _render(config.get("body", ""), ctx)
    headers_raw = config.get("headers", {})
    headers = {k: _render(v, ctx) for k, v in (headers_raw.items() if isinstance(headers_raw, dict) else {})}
    headers.setdefault("Content-Type", "application/json")
    if not url:
        return False, {"error": "URL is required"}
    try:
        import urllib.request
        data = body.encode() if body and method not in ("GET", "HEAD") else None
        req  = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as r:
            resp_body = r.read().decode(errors="replace")[:2000]
            return True, {"status": r.status, "url": url, "response": resp_body}
    except Exception as e:
        return False, {"error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Condition evaluator
# ──────────────────────────────────────────────────────────────────────────────
def _evaluate_condition(config: dict, ctx: dict) -> bool:
    field    = config.get("field", "")
    operator = config.get("operator", "==")
    value    = str(config.get("value", ""))
    actual   = str(ctx.get(field, ""))
    ops = {
        "==": actual == value,
        "!=": actual != value,
        ">":  float(actual or 0) > float(value or 0),
        "<":  float(actual or 0) < float(value or 0),
        "contains": value.lower() in actual.lower(),
        "not_contains": value.lower() not in actual.lower(),
    }
    return ops.get(operator, False)


# ──────────────────────────────────────────────────────────────────────────────
# Template renderer (simple {{key}} substitution)
# ──────────────────────────────────────────────────────────────────────────────
def _render(template: str, ctx: dict) -> str:
    import re
    def replacer(m):
        key = m.group(1).strip()
        return str(ctx.get(key, m.group(0)))
    return re.sub(r"\{\{(.+?)\}\}", replacer, str(template))


# ──────────────────────────────────────────────────────────────────────────────
# Action dispatcher
# ──────────────────────────────────────────────────────────────────────────────
ACTION_MAP = {
    "create_ticket":      _action_create_ticket,
    "update_ticket":      _action_update_ticket,
    "close_ticket":       _action_close_ticket,
    "assign_ticket":      _action_assign_ticket,
    "send_notification":  _action_send_notification,
    "send_email":         _action_send_email,
    "send_teams":         _action_send_teams,
    "send_slack":         _action_send_slack,
    "disable_ad_user":    _action_disable_ad_user,
    "enable_ad_user":     _action_enable_ad_user,
    "create_user":        _action_create_user,
    "reset_password":     _action_reset_password,
    "unlock_account":     _action_unlock_account,
    "add_to_group":       _action_add_to_group,
    "remove_from_group":  _action_remove_from_group,
    "azure_sync":         _action_azure_sync,
    "release_quarantine": _action_release_quarantine,
    "webhook":            _action_webhook,
    "http_request":       _action_http_request,
    "deploy_patch":       _action_deploy_patch,
    "deploy_software":    _action_deploy_software,
    "uninstall_software": _action_uninstall_software,
    "install_local_tool": _action_install_local_tool,
    "apply_fix":          _action_apply_fix,
    "run_script":         _action_run_script,
    "reboot_device":      _action_reboot_device,
    "shutdown_device":    _action_shutdown_device,
    "lock_device":        _action_lock_device,
    "apply_gpo":          _action_apply_gpo,
    "wait":               _action_wait,
    "ai_suggest":         _action_ai_suggest,
}


# ──────────────────────────────────────────────────────────────────────────────
# Core executor
# ──────────────────────────────────────────────────────────────────────────────
def execute_workflow(workflow_id: int, trigger_data: dict = None) -> int:
    """
    Start executing a workflow. Returns run_id.
    Executed in a background thread.
    """
    trigger_data = trigger_data or {}
    db = _db()
    wf = db.execute("SELECT * FROM workflow_definitions WHERE id=? AND enabled=1", (workflow_id,)).fetchone()
    if not wf:
        db.close()
        raise ValueError(f"Workflow {workflow_id} not found or disabled")
    nodes = json.loads(wf["nodes"])
    edges = json.loads(wf["edges"])
    cur = db.execute(
        "INSERT INTO workflow_runs (workflow_id, trigger_data, status, started_at) VALUES (?,?,?,?)",
        (workflow_id, json.dumps(trigger_data), "running", _now())
    )
    run_id = cur.lastrowid
    db.commit(); db.close()

    # ── APPROVAL GATE ───────────────────────────────────────────────────────────
    # The risk-scored gate runs per-action inside _run_workflow (see approval.decide):
    # the first medium/high action parks the whole run as 'awaiting_approval' and writes
    # a pending command_ledger row, rather than executing. A human approves/denies it
    # from the Approvals queue (approve_action/deny_action), which replays the single
    # approved action. (v1 replays the gated action; full multi-step DAG resume is next.)

    # Run in background
    t = threading.Thread(target=_run_workflow, args=(run_id, nodes, edges, trigger_data), daemon=True)
    t.start()
    return run_id


def _build_graph(nodes, edges):
    """(node_map, adj, edge_conditions) from a workflow definition."""
    adj, edge_conditions = {}, {}
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src and tgt:
            adj.setdefault(src, []).append(tgt)
            if e.get("label"):
                edge_conditions[(src, tgt)] = e["label"].lower()
    node_map = {n["id"]: n for n in nodes}
    return node_map, adj, edge_conditions


def _load_run_definition(run_id):
    """Reload the (nodes, edges) of the workflow behind a run — used to resume a parked run."""
    db = _db()
    try:
        row = db.execute(
            "SELECT wd.nodes AS nodes, wd.edges AS edges FROM workflow_runs wr "
            "JOIN workflow_definitions wd ON wd.id = wr.workflow_id WHERE wr.id=?",
            (run_id,)).fetchone()
    finally:
        db.close()
    if not row:
        return None, None
    parse = lambda x: x if isinstance(x, list) else json.loads(x or "[]")
    return parse(row["nodes"]), parse(row["edges"])


def _set_run_status(run_id, status, error=None):
    db = _db()
    try:
        db.execute("UPDATE workflow_runs SET status=?, error=? WHERE id=?", (status, error, run_id))
        db.commit()
    finally:
        db.close()


def _run_workflow(run_id: int, nodes: list, edges: list, ctx: dict):
    try:
        # Seed correlation + actor for the command ledger so every action in this run is
        # tied together and attributed. _-prefixed keys are not merged into downstream
        # node output (see the action merge in _drive).
        ctx = dict(ctx or {})
        # Never let caller-supplied trigger_data pre-satisfy the approval gate.
        ctx.pop("_approved", None)
        ctx.setdefault("_correlation_id", f"wf-run-{run_id}")
        ctx.setdefault("_requested_by", ctx.get("requested_by", "workflow_engine"))
        node_map, adj, edge_conditions = _build_graph(nodes, edges)
        start_nodes = [n for n in nodes if n.get("type") == "trigger"] or nodes[:1]
        _drive(run_id, node_map, adj, edge_conditions, ctx,
               visited=set(), queue=[n["id"] for n in start_nodes])
    except Exception:
        log.exception("Workflow run %s crashed", run_id)
        _finish_run(run_id, "failed", traceback.format_exc())


def resume_run(run_id, parked_node_id, ctx, visited, queue):
    """Continue a parked run after its gated action was approved + executed. Re-enters the
    DAG at the parked node's successors; any FURTHER medium/high action parks again."""
    nodes, edges = _load_run_definition(run_id)
    if nodes is None:
        log.warning("resume_run: no definition for run %s", run_id)
        return
    node_map, adj, edge_conditions = _build_graph(nodes, edges)
    visited = set(visited or [])
    # The parked node already executed via approve_action; enqueue its successors + whatever
    # was still queued when the run parked.
    new_queue = [t for t in adj.get(parked_node_id, []) if t not in visited] + list(queue or [])
    _set_run_status(run_id, "running")
    _drive(run_id, node_map, adj, edge_conditions, dict(ctx or {}), visited, new_queue)


def _drive(run_id, node_map, adj, edge_conditions, ctx, visited, queue):
    """Core DAG traversal — shared by the initial run and resume-after-approval. Runs until
    the queue drains (completed), an action parks (awaiting_approval), or one fails. The run
    status is finalized here in every exit path."""
    try:
        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            node = node_map.get(node_id)
            if not node:
                continue

            ntype  = node.get("type", "action")
            config = node.get("config", {})
            label  = node.get("label", ntype)

            db = _db()
            cur = db.execute(
                "INSERT INTO workflow_run_steps (run_id, node_id, node_type, node_label, status, started_at) VALUES (?,?,?,?,?,?)",
                (run_id, node_id, ntype, label, "running", _now())
            )
            db.commit()
            step_id = cur.lastrowid
            db.close()

            success, output = True, {}

            if ntype == "trigger":
                success, output = True, ctx

            elif ntype == "condition":
                result = _evaluate_condition(config, ctx)
                output = {"result": result}
                next_nodes = []
                for tgt in adj.get(node_id, []):
                    label_req = edge_conditions.get((node_id, tgt), "")
                    if not label_req:
                        next_nodes.append(tgt)
                    elif label_req == "true" and result:
                        next_nodes.append(tgt)
                    elif label_req == "false" and not result:
                        next_nodes.append(tgt)
                queue = next_nodes + queue
                _update_step(step_id, "completed", output)
                continue

            elif ntype == "action":
                action_type = node.get("action") or config.get("action_type", "")

                # ── Approval gate — single choke point for EVERY action type ──────
                # medium/high park the run for human approval. Persist enough to resume:
                # the parked node, the visited set, and the remaining queue.
                decision, eff_tier, policy_note = approval.decide(action_type, config.get("risk_tier"))
                if decision == "require" and not ctx.get("_approved"):
                    obj_type, obj_id = _action_object(action_type, config, ctx)
                    led = _ledger_log(
                        action_type, action_type, object_type=obj_type, object_id=obj_id,
                        requested_by=ctx.get("_requested_by", "workflow_engine"),
                        planned_by="workflow_engine", risk_tier=eff_tier,
                        approval_status="pending", correlation_id=ctx.get("_correlation_id"),
                        status="awaiting_approval",
                        before_state={"replay": {"run_id": run_id, "node_id": node_id,
                                                 "step_id": step_id, "action_type": action_type,
                                                 "config": config, "ctx": _redact(dict(ctx)),
                                                 "visited": list(visited), "queue": list(queue)},
                                      "policy": policy_note, "node_label": label},
                    )
                    parked = {"awaiting_approval": True, "ledger_id": led, "risk_tier": eff_tier,
                              "policy": policy_note,
                              "note": f"{eff_tier}-risk {action_type} parked for human approval"}
                    _update_step(step_id, "awaiting_approval", parked)
                    _notify_parked(action_type, eff_tier, led, label)
                    _finish_run(run_id, "awaiting_approval",
                                f"Parked on '{label}' ({action_type}) — needs approval (ledger #{led})")
                    return

                handler = ACTION_MAP.get(action_type)
                if handler:
                    try:
                        success, output = handler(config, ctx)
                    except Exception as e:
                        success, output = False, {"error": str(e)}
                else:
                    success, output = False, {"error": f"Unknown action: {action_type}"}
                ctx = {**ctx, **{k: v for k, v in output.items() if not k.startswith("_")}}

                # A successful remediation for a ticket-driven run closes the originating
                # ticket + notifies the reporter (idempotent, best-effort). Failures leave
                # the ticket open — see _auto_resolve_ticket_after_remediation.
                if success:
                    _auto_resolve_ticket_after_remediation(action_type, output, ctx)

            status = "completed" if success else "failed"
            _update_step(step_id, status, output)

            if not success:
                _finish_run(run_id, "failed", output.get("error"))
                return

            for tgt in adj.get(node_id, []):
                if tgt not in visited:
                    queue.append(tgt)

        _finish_run(run_id, "completed")
    except Exception:
        log.exception("Workflow run %s crashed (drive)", run_id)
        _finish_run(run_id, "failed", traceback.format_exc())


def _update_step(step_id: int, status: str, output: dict):
    # Surface failures in the dedicated `error` column so a failed step is visible
    # without parsing output_data. No-op handlers now record their real status here.
    error = None
    if status == "failed" and isinstance(output, dict):
        error = output.get("error") or "action failed"
    db = _db()
    try:
        db.execute(
            "UPDATE workflow_run_steps SET status=?, output_data=?, error=?, completed_at=? WHERE id=?",
            (status, json.dumps(output, default=str), error, _now(), step_id)
        )
        db.commit()
    finally:
        db.close()


def _finish_run(run_id: int, status: str, error: str = None):
    db = _db()
    db.execute(
        "UPDATE workflow_runs SET status=?, completed_at=?, error=? WHERE id=?",
        (status, _now(), error, run_id)
    )
    db.commit(); db.close()
    log.info("Workflow run %s → %s", run_id, status)


# ──────────────────────────────────────────────────────────────────────────────
# Trigger listeners (called from app event hooks)
# ──────────────────────────────────────────────────────────────────────────────
def fire_trigger(trigger_type: str, context: dict):
    """
    Called by the Flask app when an event happens.
    Finds all enabled workflows with a matching trigger and runs them.
    """
    try:
        db = _db()
        workflows = db.execute(
            "SELECT id, trigger_config FROM workflow_definitions WHERE trigger_type=? AND enabled=1",
            (trigger_type,)
        ).fetchall()
        db.close()
        for wf in workflows:
            try:
                tcfg = json.loads(wf["trigger_config"] or "{}")
                # Apply any trigger-level filters
                if _trigger_matches(tcfg, context):
                    execute_workflow(wf["id"], context)
            except Exception:
                log.exception("Failed to fire workflow %s for trigger %s", wf["id"], trigger_type)
    except Exception:
        log.exception("fire_trigger crashed for %s", trigger_type)


def _trigger_matches(tcfg: dict, ctx: dict) -> bool:
    """Check if trigger config filters match the event context (case-insensitive
    substring per key). An empty tcfg matches everything — callers should scope it."""
    for key, expected in tcfg.items():
        if key.startswith("_"):
            continue
        actual = str(ctx.get(key, "")).lower()
        if str(expected).lower() not in actual:
            return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Schedule runner — polls every 60s for schedule-triggered workflows
# ──────────────────────────────────────────────────────────────────────────────
_schedule_thread = None
_stop_event      = threading.Event()


def start_schedule_runner():
    global _schedule_thread
    if _schedule_thread and _schedule_thread.is_alive():
        return
    _stop_event.clear()
    _schedule_thread = threading.Thread(target=_schedule_loop, daemon=True, name="wf-scheduler")
    _schedule_thread.start()
    log.info("Workflow schedule runner started")


def _schedule_loop():
    while not _stop_event.wait(60):
        try:
            db = _db()
            workflows = db.execute(
                "SELECT id, trigger_config FROM workflow_definitions WHERE trigger_type='schedule' AND enabled=1"
            ).fetchall()
            db.close()
            now = datetime.utcnow()
            for wf in workflows:
                tcfg = json.loads(wf["trigger_config"] or "{}")
                if _should_run_now(tcfg, now):
                    execute_workflow(wf["id"], {"scheduled_at": now.isoformat()})
        except Exception:
            log.exception("Schedule loop error")


def _should_run_now(tcfg: dict, now: datetime) -> bool:
    """Simple schedule check: hourly, daily at HH:MM, weekly on day."""
    freq = tcfg.get("frequency", "daily")
    if freq == "hourly":
        return now.minute == int(tcfg.get("minute", 0))
    if freq == "daily":
        h, m = map(int, tcfg.get("time", "08:00").split(":"))
        return now.hour == h and now.minute == m
    if freq == "weekly":
        day = tcfg.get("day", "monday").lower()
        days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        h, m = map(int, tcfg.get("time", "08:00").split(":"))
        return days[now.weekday()] == day and now.hour == h and now.minute == m
    return False
