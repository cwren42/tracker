"""Ticket auto-scoop — the agentic-OS "Mission Control" reflex.

On `ticket.created`, the brain reads the ticket, matches it to a vetted library
fix (rmm_script_library where is_fix=true), and — when it's confident, the fix is
TESTED, and the device is reachable — PARKS a 1-click apply_fix at /approvals,
pre-diagnosed. A human still approves every apply; nothing auto-executes. This is
the Apply half of the Learn -> Apply loop (ticket.resolved -> Learn is its mirror).

Wired as a built-in subscriber in event_bus._dispatch_once, alongside the
ticket.resolved -> Learn step. Fail-safe: any error is logged and swallowed so a
bad scoop can never block event dispatch. Idempotent against at-least-once
delivery (won't double-park the same ticket).

Gates (ALL must pass to PARK a fix):
  * kill-switch setting `ticket_autoscoop_enabled` = '1'   (OFF by default)
  * ticket still open and not already scooped
  * match confidence >= AUTOSCOOP_HIGH and the matched fix is_tested
  * a device resolves for the ticket AND it has an enabled RMM agent
Medium-confidence matches (or untested / device-less matches) add an advisory
note to the ticket instead of parking — visibility without action.
"""
import logging
from datetime import datetime

from pg_db import pg_connect

log = logging.getLogger("ticket_autoscoop")

AUTOSCOOP_HIGH = 0.80   # >= this AND the fix is tested -> park a 1-click apply_fix
AUTOSCOOP_MED  = 0.55   # >= this -> advisory note only (no park)

# Closed/terminal ticket states we never scoop (re-delivery after close, etc.)
_CLOSED_STATES = {"closed", "resolved", "cancelled", "canceled"}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # TZ=America/Denver


def _setting(db, key, default=""):
    row = db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def _already_scooped(db, ticket_id):
    """True if an apply_fix for this ticket already exists in the ledger — guards
    against at-least-once double delivery of ticket.created."""
    row = db.execute(
        "SELECT 1 FROM command_ledger WHERE action_type='apply_fix' "
        "AND before_state->'replay'->'ctx'->>'ticket_id' = ? LIMIT 1",
        (str(ticket_id),)
    ).fetchone()
    return row is not None


def _resolve_device(db, ticket):
    """Resolve (asset_id, hostname) for a ticket: explicit asset > hostname match >
    reporter's employee asset. Mirrors blueprints/tickets.apply_ticket_fix."""
    asset_id, hostname = ticket["asset_id"], ticket["hostname"]
    if asset_id:
        return asset_id, hostname
    if hostname:
        a = db.execute("SELECT id, name FROM asset WHERE name=? LIMIT 1", (hostname,)).fetchone()
        if a:
            return a["id"], hostname
    if ticket["created_by_user_id"]:
        a = db.execute(
            'SELECT a.id, a.name FROM asset a '
            'JOIN "user" u ON u.employee_id = a.employee_id '
            'WHERE u.id=? AND a.employee_id IS NOT NULL LIMIT 1',
            (ticket["created_by_user_id"],)
        ).fetchone()
        if a:
            return a["id"], (hostname or a["name"])
    return None, hostname


def _has_live_agent(db, asset_id):
    return db.execute(
        "SELECT 1 FROM rmm_agent WHERE asset_id=? AND enabled=true LIMIT 1", (asset_id,)
    ).fetchone() is not None


def _add_note(db, ticket_id, text):
    # ticket_note has NO created_by column (see ai-engine-ticket-note-bug). user_id
    # is nullable -> NULL marks a system/brain note.
    db.execute(
        "INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (ticket_id, None, text, True, False, _now())
    )


def scoop(ticket_id: int, dry_run: bool = False) -> dict:
    """Evaluate a newly-created ticket for auto-scoop. Returns a result dict
    describing the decision (for logging/tests). dry_run=True does everything
    EXCEPT publish fix.requested / write notes — used to validate matching live
    without touching the queue. Never raises."""
    result = {"ticket_id": ticket_id, "action": "none", "fix_id": None,
              "confidence": 0.0, "reason": ""}
    try:
        db = pg_connect()
        try:
            enabled = _setting(db, "ticket_autoscoop_enabled", "0") == "1"
            if not enabled and not dry_run:
                result["action"] = "disabled"
                return result

            t = db.execute(
                "SELECT id, subject, status, asset_id, hostname, created_by_user_id "
                "FROM support_ticket WHERE id=?", (ticket_id,)
            ).fetchone()
            if not t:
                result["action"] = "no_ticket"
                return result
            if (t["status"] or "").strip().lower() in _CLOSED_STATES:
                result["action"] = "ticket_closed"
                return result
            if _already_scooped(db, ticket_id):
                result["action"] = "already_scooped"
                return result

            # Match the ticket to a library fix
            import ai_engine
            m = ai_engine.match_ticket_to_fix(ticket_id)
            result.update({"fix_id": m.get("fix_id"), "confidence": m.get("confidence", 0.0),
                           "reason": m.get("reason", ""), "fix_name": m.get("fix_name", ""),
                           "source": m.get("source")})
            if not m.get("fix_id"):
                result["action"] = "no_match"
                return result

            conf = m.get("confidence", 0.0)
            asset_id, hostname = _resolve_device(db, t)
            device_ok = bool(asset_id) and _has_live_agent(db, asset_id)
            result["asset_id"] = asset_id

            # PARK gate: confident + tested fix + a reachable device
            if conf >= AUTOSCOOP_HIGH and m.get("is_tested") and device_ok:
                note = (f"**Auto-scoop** — matched library fix #{m['fix_id']} "
                        f"({m['fix_name']}), confidence {conf:.0%}. {m.get('reason','')}\n\n"
                        f"Parked a 1-click *Apply Fix* at /approvals for {hostname or 'this device'}. "
                        f"Approve it to run the fix and auto-resolve this ticket.")
                if dry_run:
                    result["action"] = "would_park"
                    return result
                import event_bus
                event_bus.publish("fix.requested", {
                    "fix_id": m["fix_id"], "fix_name": m["fix_name"],
                    "asset_id": asset_id, "hostname": hostname or "",
                    "ticket_id": ticket_id, "requested_by": "agentic-os (auto-scoop)",
                    "justification": f"Auto-scoop: confidence {conf:.0%} — {m.get('reason','')}",
                }, source="autoscoop")
                _add_note(db, ticket_id, note)
                db.commit()
                result["action"] = "parked"
                log.info("auto-scoop PARKED fix #%s for ticket %s (conf=%.2f, asset=%s)",
                         m["fix_id"], ticket_id, conf, asset_id)
                return result

            # NOTE-only band: a plausible match we won't auto-apply. Explain why.
            if conf >= AUTOSCOOP_MED:
                why = []
                if not m.get("is_tested"):
                    why.append("the fix is not yet tested")
                if not device_ok:
                    why.append("no reachable agent on the device")
                if conf < AUTOSCOOP_HIGH:
                    why.append(f"confidence {conf:.0%} below auto-apply threshold")
                note = (f"**Auto-scoop (suggestion)** — this looks like library fix "
                        f"#{m['fix_id']} ({m['fix_name']}), confidence {conf:.0%}. "
                        f"{m.get('reason','')}\n\nNot auto-parked because "
                        f"{'; '.join(why) or 'gating not met'}. "
                        f"Review and apply manually from the ticket if appropriate.")
                if dry_run:
                    result["action"] = "would_note"
                    return result
                _add_note(db, ticket_id, note)
                db.commit()
                result["action"] = "noted"
                log.info("auto-scoop NOTED fix #%s for ticket %s (conf=%.2f)",
                         m["fix_id"], ticket_id, conf)
                return result

            result["action"] = "below_threshold"
            return result
        finally:
            db.close()
    except Exception:
        log.exception("auto-scoop failed for ticket %s (non-fatal)", ticket_id)
        result["action"] = "error"
        return result
