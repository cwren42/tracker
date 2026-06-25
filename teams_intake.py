"""Teams intake brain (Phase 02 — the front door).

Turns an inbound Teams message into a tracked, context-enriched support ticket and
a helpful reply. A person messages the bot in plain language; we resolve who they
are (Teams aadObjectId -> employee.m365_id), open a ticket exactly the way the app
does (status Open, source 'teams') so the ticket.created event fires P01 enrichment
+ auto-scoop, then reply with their live context and a deep link.

AI self-resolve (answer without a ticket) is the next increment; this first cut
guarantees nothing falls on the floor — every ask becomes a tracked, enriched item.
"""
import os
import re
import logging

import psycopg2

import context_service

log = logging.getLogger(__name__)


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _deeplink_base():
    return (os.environ.get("PUBLIC_BASE_URL")
            or os.environ.get("RMM_TRACKER_URL_PUBLIC")
            or "https://tracker.cirquetools.com").rstrip("/")


def _strip_mention(text):
    # Teams prefixes @bot mentions; drop a leading <at>..</at> and bare @name.
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
    # fall back to display-name match (best-effort)
    if name:
        cur.execute("SELECT id,name,email FROM employee WHERE lower(name)=lower(%s) LIMIT 1", (name,))
        r = cur.fetchone()
        if r:
            return {"id": r[0], "name": r[1], "email": r[2]}
    return {"id": None, "name": name, "email": None}


def handle_message(activity):
    """Create an enriched ticket from a Teams message. Returns (text, card)."""
    text = _strip_mention(activity.get("text"))
    if not text:
        return ("Hi — tell me what you need (e.g. \"my laptop is slow\" or "
                "\"I need access to the Finance SharePoint\") and I'll open a ticket "
                "with everything our system already knows about your setup.", None)

    conn = _connect()
    conn.autocommit = True
    try:
        cur = conn.cursor()
        emp = _resolve_employee(cur, activity)

        # primary device = the employee's single assigned asset, if exactly one
        asset_id = asset_name = None
        if emp["id"]:
            cur.execute("SELECT id,name FROM asset WHERE employee_id=%s", (emp["id"],))
            rows = cur.fetchall()
            if len(rows) == 1:
                asset_id, asset_name = rows[0]

        subject = (text[:80] + "…") if len(text) > 80 else text
        description = f"{text}\n\n(opened via Teams by {emp.get('name') or 'unknown'})"
        cur.execute(
            """INSERT INTO support_ticket
               (status, priority, source, subject, description,
                reporter_name, reporter_email, asset_id, hostname, created_at, updated_at)
               VALUES ('Open','Normal','teams',%s,%s,%s,%s,%s,%s, now(), now())
               RETURNING id""",
            (subject, description, emp.get("name"), emp.get("email"), asset_id, asset_name))
        ticket_id = cur.fetchone()[0]
    finally:
        conn.close()

    # fire the same event the web/email paths fire -> P01 enrich + auto-scoop
    try:
        import event_bus
        event_bus.publish("ticket.created", {
            "ticket_id": ticket_id, "subject": subject, "priority": "Normal",
            "asset_id": asset_id, "submitter_email": emp.get("email"),
            "source": "teams",
        }, source="teams")
    except Exception:
        log.exception("teams intake: ticket.created publish failed (ticket=%s)", ticket_id)

    # reply card: confirmation + what we already know + deep link
    ctx_block = ""
    try:
        ctx_block = context_service.context_block_for_ticket(
            asset_id=asset_id, reporter_email=emp.get("email"))
    except Exception:
        pass

    base = _deeplink_base()
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "color": "good",
         "wrap": True, "text": f"Ticket #{ticket_id} opened"},
        {"type": "TextBlock", "isSubtle": True, "wrap": True, "text": subject},
    ]
    if ctx_block:
        body.append({"type": "TextBlock", "wrap": True, "separator": True,
                     "text": "**What I already see:**\n" + ctx_block})
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [{"type": "Action.OpenUrl", "title": "View in Tracker",
                     "url": f"{base}/tickets/{ticket_id}"}],
    }
    return (f"Opened ticket #{ticket_id} — I've attached everything our system "
            f"already knows about your setup. A tech will follow up.", card)
