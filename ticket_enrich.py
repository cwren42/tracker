"""Phase 01 — ticket auto-enrichment (Agentic IT OS roadmap).

On ticket.created, stamp the unified reporter + device context (Phase 0 context
layer) onto the ticket as an INTERNAL system note, so a tech sees the full picture
the moment they open it — live device telemetry, Intune compliance, AD region,
account state, and derived risk flags — instead of just the subject line.

Idempotent: a marker in the note content prevents duplicate stamps under the
event bus's at-least-once delivery. Best-effort; never raises into the dispatcher.
"""
import os
import logging

import psycopg2

import context_service

log = logging.getLogger(__name__)

_MARKER = "[context-layer]"  # idempotency + identifies our system note


def enrich(ticket_id):
    """Add the at-creation context note to a ticket. Returns True if a note was
    written, False if skipped (already stamped, no ticket, or no context)."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT asset_id, reporter_email FROM support_ticket WHERE id=%s",
                    (ticket_id,))
        row = cur.fetchone()
        if not row:
            return False
        asset_id, reporter_email = row

        # idempotent under at-least-once delivery
        cur.execute("SELECT 1 FROM ticket_note WHERE ticket_id=%s AND content LIKE %s LIMIT 1",
                    (ticket_id, _MARKER + "%"))
        if cur.fetchone():
            return False

        block = context_service.context_block_for_ticket(
            asset_id=asset_id, reporter_email=reporter_email)
        if not block:
            return False

        content = f"{_MARKER} IT context at creation\n\n{block}"
        cur.execute(
            "INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
            "VALUES (%s, NULL, %s, TRUE, FALSE, now())",
            (ticket_id, content))
        return True
    except Exception:
        log.exception("ticket_enrich.enrich failed (ticket=%s)", ticket_id)
        return False
    finally:
        conn.close()
