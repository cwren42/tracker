"""Teams intake brain (Phase 02 — the front door).

A person messages the bot in plain language. We resolve who they are (Teams
aadObjectId -> employee.m365_id), pull their live context (P0), and let the AI
triage (gpt-4.1-mini): answer simple things in-chat, or open a context-enriched
ticket (source='teams' -> ticket.created -> P01 enrich + auto-scoop). The reply
card carries 1-click actions (Mark resolved / Escalate) handled by handle_invoke.
"""
import os
import re
import logging

import psycopg2

import context_service

log = logging.getLogger(__name__)


def _connect():
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    c.autocommit = True
    return c


def _deeplink_base():
    return (os.environ.get("PUBLIC_BASE_URL")
            or os.environ.get("RMM_TRACKER_URL_PUBLIC")
            or "https://tracker.cirquetools.com").rstrip("/")


def _strip_mention(text):
    text = re.sub(r"<at>.*?</at>", "", text or "", flags=re.I)
    return text.strip()


def _resolve_employee(cur, activity):
    frm = activity.get("from") or {}
    aad = frm.get("aadObjectId")
    name = frm.get("name")
    if aad:
        cur.execute("SELECT id,name,email FROM employee WHERE m365_id=%s LIMIT 1", (aad,))
        r = cur.fetchone()
        if r:
            return {"id": r[0], "name": r[1], "email": r[2]}
    if name:
        cur.execute("SELECT id,name,email FROM employee WHERE lower(name)=lower(%s) LIMIT 1", (name,))
        r = cur.fetchone()
        if r:
            return {"id": r[0], "name": r[1], "email": r[2]}
    return {"id": None, "name": name, "email": None}


def _add_note(cur, ticket_id, text):
    cur.execute(
        "INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
        "VALUES (%s, NULL, %s, TRUE, FALSE, now())", (ticket_id, text))


def _ticket_card(ticket_id, subject, ctx_block):
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "color": "good",
         "wrap": True, "text": f"Ticket #{ticket_id} opened"},
        {"type": "TextBlock", "isSubtle": True, "wrap": True, "text": subject},
    ]
    if ctx_block:
        body.append({"type": "TextBlock", "wrap": True, "separator": True,
                     "text": "**What I already see:**\n" + ctx_block})
    base = _deeplink_base()
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [
            {"type": "Action.Execute", "title": "✅ Mark resolved",
             "verb": "ticket_resolved", "data": {"ticket_id": ticket_id}},
            {"type": "Action.Execute", "title": "⏫ Escalate",
             "verb": "ticket_escalate", "data": {"ticket_id": ticket_id}},
            {"type": "Action.OpenUrl", "title": "View in Tracker",
             "url": f"{base}/tickets/{ticket_id}"},
        ],
    }


def handle_message(activity):
    """Triage a Teams message: answer in-chat, or open an enriched ticket.
    Returns (text, card)."""
    text = _strip_mention(activity.get("text"))
    if not text:
        return ("Hi — tell me what you need (e.g. \"my laptop is slow\" or "
                "\"I need access to the Finance SharePoint\") and I'll help or open a ticket.", None)

    # who + their primary device
    conn = _connect()
    try:
        cur = conn.cursor()
        emp = _resolve_employee(cur, activity)
        asset_id = asset_name = None
        if emp["id"]:
            cur.execute("SELECT id,name FROM asset WHERE employee_id=%s", (emp["id"],))
            rows = cur.fetchall()
            if len(rows) == 1:
                asset_id, asset_name = rows[0]
    finally:
        conn.close()

    # live context for triage + the card
    ctx_block = ""
    try:
        ctx_block = context_service.context_block_for_ticket(
            asset_id=asset_id, reporter_email=emp.get("email"))
    except Exception:
        log.exception("teams intake: context fetch failed")

    # AI triage (cheap model) — answer vs ticket
    triage = {"action": "ticket", "reply": None}
    try:
        import ai_engine
        triage = ai_engine.triage_teams_message(text, ctx_block)
    except Exception:
        log.exception("teams intake: triage failed (fail-safe -> ticket)")

    if triage.get("action") == "answer":
        reply = triage.get("reply") or "Here's what I found."
        return (reply + "\n\n_If that doesn't sort it, just reply and I'll open a ticket._", None)

    # ── open a context-enriched ticket ──────────────────────────────────────
    subject = triage.get("subject") or ((text[:80] + "…") if len(text) > 80 else text)
    priority = triage.get("priority") or "Normal"
    category = triage.get("category")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO support_ticket
               (status, priority, source, subject, description, category,
                reporter_name, reporter_email, asset_id, hostname, created_at, updated_at)
               VALUES ('Open',%s,'teams',%s,%s,%s,%s,%s,%s,%s, now(), now())
               RETURNING id""",
            (priority, subject, f"{text}\n\n(opened via Teams by {emp.get('name') or 'unknown'})",
             category, emp.get("name"), emp.get("email"), asset_id, asset_name))
        ticket_id = cur.fetchone()[0]
    finally:
        conn.close()

    try:
        import event_bus
        event_bus.publish("ticket.created", {
            "ticket_id": ticket_id, "subject": subject, "priority": priority,
            "category": category, "asset_id": asset_id,
            "submitter_email": emp.get("email"), "source": "teams",
        }, source="teams")
    except Exception:
        log.exception("teams intake: ticket.created publish failed (ticket=%s)", ticket_id)

    reply = triage.get("reply") or (f"Opened ticket #{ticket_id} — a tech will follow up.")
    return (reply, _ticket_card(ticket_id, subject, ctx_block))


def handle_invoke(activity):
    """Handle an Action.Execute (1-click) from a card. Returns the Bot Framework
    adaptiveCard/action invoke response (a message shown to the user)."""
    val = activity.get("value") or {}
    action = val.get("action") or {}
    verb = action.get("verb")
    data = action.get("data") or {}
    tid = data.get("ticket_id")
    msg = "Sorry, I didn't recognize that action."
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            if verb == "ticket_escalate" and tid:
                cur.execute("UPDATE support_ticket SET priority='High', updated_at=now() WHERE id=%s", (tid,))
                _add_note(cur, tid, "[teams] user escalated — priority raised to High")
                msg = f"⏫ Escalated ticket #{tid} to High — a tech will jump on it."
            elif verb == "ticket_resolved" and tid:
                cur.execute("UPDATE support_ticket SET status='Closed', closed_at=now(), updated_at=now() WHERE id=%s", (tid,))
                _add_note(cur, tid, "[teams] user marked resolved from chat")
                msg = f"✅ Closed ticket #{tid} — glad it's sorted. Reply anytime to reopen."
        finally:
            conn.close()
    except Exception:
        log.exception("teams invoke failed (verb=%s ticket=%s)", verb, tid)
        msg = "Something went wrong applying that — a tech will take a look."
    return {"statusCode": 200,
            "type": "application/vnd.microsoft.activity.message",
            "value": msg}
