"""
Alert Evaluation Service
Runs as a background thread; evaluates all alert_rule rows and fires
alert_log entries, creates tickets, sends email/Teams notifications.

State-based alerting (enterprise RMM pattern):
  Each continuous-condition alert (CPU high, offline, disk critical, etc.)
  is tracked as an active "alert state" row.  When the condition clears the
  associated ticket is automatically closed with a resolution note — exactly
  how NinjaRMM / ConnectWise / Datto handle alert lifecycle.
"""
import fcntl
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, date, timezone

import requests

logger = logging.getLogger(__name__)

# How often to run the evaluator (seconds)
EVAL_INTERVAL_S = 300   # 5 minutes

# Internal address of the RMM WebSocket gateway. Same default the Flask app uses
# (RMM_GATEWAY_INTERNAL); the disk loop POSTs run_script remediations here.
RMM_GATEWAY_INTERNAL = os.environ.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')

# Only one Gunicorn worker should run the evaluator per cycle
ALERT_EVAL_LOCK_PATH = '/tmp/tracker_alert_eval.lock'

# Flap protection: an active alert must have been UNSEEN for at least this many
# minutes before its ticket is auto-closed. One missed sample (transient telemetry
# gap) must not flap-close a ticket — so this is comfortably > one eval interval.
AUTO_RESOLVE_GRACE_MINUTES = 20   # ~4 missed 5-min cycles

# Re-open (instead of create-new) a closed alert ticket only if it was closed
# within this window. Older closed tickets are treated as a fresh incident.
REOPEN_WINDOW_DAYS = 7

# Alert types that represent a CONTINUOUS condition (on/off state).
# When the condition clears (no telemetry sample re-fires it for the grace
# window), the open ticket is auto-closed by _resolve_cleared_alerts().
# Event-based types (new_local_admin) are intentionally excluded — they fire
# once and the ticket needs human review.
_AUTO_RESOLVE_TYPES = frozenset({
    'offline', 'cpu_high', 'ram_high',
    'disk_critical', 'disk_low',
    'battery_low', 'battery_not_chg',
    'av_disabled', 'firewall_off', 'pending_reboot',
    'failed_logins', 'not_seen', 'cve_unpatched',
})

# Alert types that must DEDUP (track an alert_state row + reuse the open ticket
# instead of spamming a new one each cooldown cycle) but whose lifecycle is NOT
# driven by telemetry-gap grace windows. CVE alerts live here: a fresh detection
# of the SAME CVE must collapse onto the existing open ticket, and the ticket is
# only auto-closed when Defender confirms the CVE is remediated across all
# devices (see _resolve_remediated_cve_alerts()), never by the grace window.
# These are deliberately NOT in _AUTO_RESOLVE_TYPES so _resolve_cleared_alerts
# never time-closes a still-exposed CVE just because the daily feed went quiet.
_DEDUP_TYPES = frozenset({
    'cve_critical', 'cve_high',
})

# Types whose alert_state identity is keyed by a per-event token (e.g. the CVE
# id) rather than by target host/asset, so each distinct CVE is its own evolving
# ticket. The dedup token is passed into _fire_alert(dedup_token=...).
_TOKEN_KEYED_TYPES = _DEDUP_TYPES

# Terminal ticket statuses for alert-lifecycle purposes. 'Resolved' is used by
# the CVE remediation loop (auto-close on remediation); it must be treated as
# terminal everywhere in this file so dedup/reopen never reuse a resolved ticket
# and the open-checks don't double-close it. SQL fragment kept in one place.
_TERMINAL_TICKET_SQL = "('Closed','Merged','Resolved')"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_str():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def _now():
    return datetime.utcnow()


def _get_db():
    """Return a PostgreSQL connection (thread-safe)."""
    from pg_db import pg_connect
    return pg_connect()


def _send_email(subject, body_html):
    """Send alert email.

    If the 'alert_notify_email' setting contains one or more comma-separated
    addresses, only those addresses receive alert emails.  This keeps system
    alert noise out of the main admin inbox so ticket notifications are not
    buried.  When the setting is absent or empty the function falls back to
    send_admin_notification (all admin-role users).
    """
    try:
        from app import app
        with app.app_context():
            con = _get_db()
            alert_addrs = _get_setting(con, 'alert_notify_email', '').strip()
            con.close()
            if alert_addrs:
                from utils import send_email as _util_email
                recipients = [a.strip() for a in alert_addrs.split(',') if a.strip()]
                if recipients:
                    result = _util_email(subject, recipients, subject, body_html)
                    if result:
                        logger.info(f'Alert email sent to {recipients}: {subject}')
                    else:
                        logger.warning(f'Alert email failed (SMTP): {subject}')
                    return
            # Fallback: send to all admin users
            from utils import send_admin_notification
            result = send_admin_notification(subject, body_html)
            if result:
                logger.info(f'Alert email sent: {subject}')
            else:
                logger.warning(f'Alert email returned False: {subject}')
    except Exception as e:
        logger.warning(f'Alert email failed: {e}', exc_info=True)


def _send_teams(webhook_url, title, body):
    """Post an Adaptive Card to a Teams webhook."""
    if not webhook_url:
        return
    try:
        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Medium", "weight": "Bolder",
                         "text": f"🚨 {title}", "wrap": True},
                        {"type": "TextBlock", "text": body, "wrap": True, "isSubtle": True}
                    ]
                }
            }]
        }
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f'Teams notification failed: {e}')


def _get_setting(con, key, default=''):
    row = con.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default


def _cooldown_ok(con, rule_id, agent_id, asset_id, cooldown_minutes):
    """Return True if enough time has passed since the last fire for this rule+target."""
    since = (_now() - timedelta(minutes=cooldown_minutes)).strftime('%Y-%m-%d %H:%M:%S')
    row = con.execute(
        "SELECT id FROM alert_log WHERE rule_id=? AND (agent_id=? OR asset_id=?) AND fired_at > ?",
        (rule_id, agent_id or '', asset_id or 0, since)
    ).fetchone()
    return row is None


def _ensure_alert_state_table(con):
    """Create alert_state table if it doesn't exist yet."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS alert_state (
            id           SERIAL PRIMARY KEY,
            rule_id      INTEGER NOT NULL,
            category     VARCHAR(50),
            alert_type   VARCHAR(50) NOT NULL,
            alert_key    VARCHAR(255) NOT NULL UNIQUE,
            agent_id     VARCHAR(100),
            asset_id     INTEGER,
            hostname     VARCHAR(200),
            ticket_id    INTEGER,
            fired_at     TIMESTAMP NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
            resolved_at  TIMESTAMP,
            occurrence_count INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Migration for pre-existing tables that lack occurrence_count.
    con.execute(
        "ALTER TABLE alert_state ADD COLUMN IF NOT EXISTS occurrence_count INTEGER NOT NULL DEFAULT 1"
    )
    con.commit()


def _alert_key(rule, agent_id, asset_id, dedup_token=None):
    """Stable identity for one alert condition.

    Normally keyed by (rule, target host/asset). For token-keyed types (CVE
    alerts) the identity is (rule, dedup_token) — e.g. ``21:cve:CVE-2026-9998``
    — so a repeat detection of the SAME CVE matches the existing open
    alert_state row and collapses onto its ticket instead of creating a new one.
    Without the token every CVE under a rule shared one key (or, since CVE types
    never persisted state at all, never matched) — that was the dup-storm bug.
    """
    if dedup_token:
        return f"{rule['id']}:cve:{dedup_token}"
    return f"{rule['id']}:{agent_id or ''}:{asset_id or 0}"


def _get_alert_state(con, alert_key):
    """Return the alert_state row for this key, or None."""
    return con.execute(
        "SELECT * FROM alert_state WHERE alert_key = ?", (alert_key,)
    ).fetchone()


def _upsert_alert_state(con, rule, agent_id, asset_id, hostname, ticket_id,
                        bump=False, dedup_token=None):
    """
    Track that this alert condition is currently active.
    Upserts: insert on first fire, update last_seen_at on subsequent fires.
    The alert_key uniquely identifies one alert condition on one target.

    - When `ticket_id` is provided it OVERWRITES the stored ticket_id so a
      re-opened or freshly-created ticket re-links the state (the previous
      COALESCE froze a stale id forever and broke auto-resolution).
    - `bump=True` increments occurrence_count (a real fire/re-fire, not a
      cooldown-only last_seen refresh).
    - Always clears resolved_at so a re-firing condition reactivates its state.
    """
    alert_type = rule['alert_type']
    if alert_type not in _AUTO_RESOLVE_TYPES and alert_type not in _DEDUP_TYPES:
        return
    alert_key = _alert_key(rule, agent_id, asset_id, dedup_token)
    con.execute("""
        INSERT INTO alert_state
            (rule_id, category, alert_type, alert_key, agent_id, asset_id, hostname,
             ticket_id, fired_at, last_seen_at, occurrence_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW(), 1)
        ON CONFLICT (alert_key) DO UPDATE
            SET last_seen_at = NOW(),
                hostname     = EXCLUDED.hostname,
                ticket_id    = COALESCE(EXCLUDED.ticket_id, alert_state.ticket_id),
                resolved_at  = NULL,
                occurrence_count = alert_state.occurrence_count + (CASE WHEN ? THEN 1 ELSE 0 END)
    """, (rule['id'], rule['category'], alert_type, alert_key,
          agent_id or '', asset_id or 0, hostname, ticket_id, bool(bump)))


def _open_or_reopen_alert_ticket(con, rule, label, cat, priority, assigned_uid,
                                 message, agent_id, asset_id, hostname, rule_id,
                                 dedup_token=None):
    """
    Return the ticket_id for this alert condition, deduplicated across close.

    Resolution order (one evolving ticket per rule+host):
      1. If the alert_state row already points at an OPEN ticket → append an
         occurrence note + bump updated_at; reuse it (don't spam new tickets).
      2. Else if a matching alert ticket was CLOSED within REOPEN_WINDOW_DAYS →
         RE-OPEN the most recent one (status->Open, clear closed_at, note the
         re-fire) instead of creating a brand-new ticket.
      3. Else INSERT a new ticket.

    Only ever touches source='alert' tickets. Returns the live ticket id (or
    None if ticket creation failed).
    """
    subject   = f'[ALERT] {label}'
    alert_key = _alert_key(rule, agent_id, asset_id, dedup_token)
    state     = _get_alert_state(con, alert_key)

    # occurrence_count in alert_state is the source of truth; it is incremented
    # by the _upsert_alert_state(bump=True) call that follows this helper, so
    # the "this fire" occurrence number is current + 1.
    occ = ((state['occurrence_count'] if state else 0) or 0) + 1

    # ── Build the target-match predicate (asset_id preferred, else hostname) ──
    if asset_id:
        match_sql = "asset_id = ?"
        match_val = asset_id
    elif hostname:
        match_sql = "hostname = ?"
        match_val = hostname
    else:
        match_sql = None
        match_val = None

    # 1. Reuse the OPEN ticket the state already tracks, if it's still open.
    if state and state['ticket_id']:
        open_t = con.execute(
            f"""SELECT id FROM support_ticket
               WHERE id = ? AND source = 'alert'
                 AND status NOT IN {_TERMINAL_TICKET_SQL}""",
            (state['ticket_id'],)
        ).fetchone()
        if open_t:
            con.execute(
                """INSERT INTO ticket_note (ticket_id, user_id, content, created_at)
                   VALUES (?, NULL, ?, NOW())""",
                (open_t['id'],
                 f'[Alert] Condition still active (occurrence {occ}): {message}')
            )
            con.execute(
                "UPDATE support_ticket SET updated_at = NOW() WHERE id = ?",
                (open_t['id'],)
            )
            con.execute(
                """INSERT INTO ticket_activity (ticket_id, user_id, action, detail, created_at)
                   VALUES (?, NULL, 'alert_refired', ?, NOW())""",
                (open_t['id'], f'Occurrence {occ}: {message}')
            )
            logger.debug(f'Dedup: alert ticket #{open_t["id"]} still open — '
                         f'appended occurrence {occ}.')
            return open_t['id']

    # 2. Re-open the most recent CLOSED matching alert ticket within the window.
    #
    # For token-keyed (CVE) types the generic subject ("[ALERT] New Critical CVE
    # Detected") and shared hostname ("Defender Vulnerability Feed") are NOT a
    # safe match key — they would collapse DIFFERENT CVEs onto one ticket. So we
    # reopen strictly via the per-CVE alert_state row's tracked ticket_id (the
    # state persists across close and is keyed on the CVE id). Non-token types
    # keep the original subject+target match.
    recent_closed = None
    if dedup_token:
        if state and state['ticket_id']:
            recent_closed = con.execute(
                f"""SELECT id FROM support_ticket
                    WHERE id = ? AND source = 'alert'
                      AND status IN {_TERMINAL_TICKET_SQL}
                      AND closed_at IS NOT NULL
                      AND closed_at > NOW() - INTERVAL '{int(REOPEN_WINDOW_DAYS)} days'
                    LIMIT 1""",
                (state['ticket_id'],)
            ).fetchone()
    elif match_sql:
        recent_closed = con.execute(
            f"""SELECT id FROM support_ticket
                WHERE source = 'alert'
                  AND status IN {_TERMINAL_TICKET_SQL}
                  AND subject = ?
                  AND {match_sql}
                  AND closed_at IS NOT NULL
                  AND closed_at > NOW() - INTERVAL '{int(REOPEN_WINDOW_DAYS)} days'
                ORDER BY closed_at DESC
                LIMIT 1""",
            (subject, match_val)
        ).fetchone()
    if recent_closed:
        tid = recent_closed['id']
        con.execute(
            """UPDATE support_ticket
               SET status='Open', closed_at=NULL, closed_by_user_id=NULL,
                   priority=?, updated_at=NOW()
               WHERE id=?""",
            (priority, tid)
        )
        con.execute(
            """INSERT INTO ticket_note (ticket_id, user_id, content, created_at)
               VALUES (?, NULL, ?, NOW())""",
            (tid, f'[Alert] Re-fired (occurrence {occ}): {message}\n\n'
                  f'Condition recurred; re-opening this ticket instead of '
                  f'creating a new one.')
        )
        con.execute(
            """INSERT INTO ticket_activity (ticket_id, user_id, action, detail, created_at)
               VALUES (?, NULL, 'alert_reopened', ?, NOW())""",
            (tid, f'Re-opened by alert engine (occurrence {occ}): {message}')
        )
        logger.info(f'Re-opened alert ticket #{tid} for "{subject}" on '
                    f'{hostname or asset_id} (occurrence {occ}).')
        return tid

    # 3. No reusable ticket — create a fresh one.
    csat_token = str(uuid.uuid4()).replace('-', '')
    cur = con.execute(
        """INSERT INTO support_ticket
           (status, priority, category, source, subject, description,
            hostname, asset_id, assigned_to_user_id, csat_token, created_at, updated_at)
           VALUES ('Open', ?, ?, 'alert', ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (priority, cat, subject,
         f'{message}\n\nAuto-created by alert rule #{rule_id}.',
         hostname or '', asset_id, assigned_uid, csat_token)
    )
    return cur.lastrowid


def _resolve_cleared_alerts(con, eval_started_at):
    """
    After each evaluation cycle, find alert_state rows whose condition has NOT
    been seen for at least AUTO_RESOLVE_GRACE_MINUTES (flap protection — one
    missing telemetry sample must not flap-close a ticket) and auto-close their
    tickets. This is the core of state-based alerting: ticket lifecycle follows
    the alert condition lifecycle.

    Closes are done in raw SQL only and intentionally do NOT publish
    ticket.resolved / hit the ticket Learn loop — auto-resolved alert noise must
    never become runbooks. Only ever touches source='alert' tickets.
    """
    # eval_started_at is only used as a sanity anchor; the real gate is the
    # grace window measured against last_seen_at, so a single skipped cycle
    # (last_seen_at just under eval_started_at) does NOT close anything.
    stale = con.execute("""
        SELECT id, rule_id, category, alert_type, alert_key,
               agent_id, hostname, ticket_id, occurrence_count
        FROM alert_state
        WHERE resolved_at IS NULL
          AND last_seen_at < NOW() - INTERVAL '%d minutes'
    """ % int(AUTO_RESOLVE_GRACE_MINUTES)).fetchall()

    closed_any = False
    for s in stale:
        # Mark state as resolved (idempotent — guarded by resolved_at IS NULL).
        con.execute(
            "UPDATE alert_state SET resolved_at = NOW() WHERE id = ?",
            (s['id'],)
        )
        closed_any = True
        if not s['ticket_id']:
            continue
        # Auto-close the linked ticket only if it's still open AND alert-sourced.
        open_ticket = con.execute(
            f"""SELECT id FROM support_ticket
               WHERE id = ? AND source = 'alert'
                 AND status NOT IN {_TERMINAL_TICKET_SQL}""",
            (s['ticket_id'],)
        ).fetchone()
        if not open_ticket:
            continue
        con.execute(
            """UPDATE support_ticket
               SET status='Closed', closed_at=NOW(), updated_at=NOW()
               WHERE id=?""",
            (s['ticket_id'],)
        )
        label = s['alert_type'].replace('_', ' ').title()
        host  = s['hostname'] or s['agent_id'] or 'unknown'
        con.execute(
            """INSERT INTO ticket_note (ticket_id, user_id, content, created_at)
               VALUES (?, NULL, ?, NOW())""",
            (s['ticket_id'],
             f'[Auto-resolved] Alert condition "{label}" back within threshold '
             f'on {host}. Ticket closed automatically by the alert engine '
             f'(no further occurrences for {int(AUTO_RESOLVE_GRACE_MINUTES)}+ minutes).')
        )
        con.execute(
            """INSERT INTO ticket_activity (ticket_id, user_id, action, detail, created_at)
               VALUES (?, NULL, 'auto_resolved', ?, NOW())""",
            (s['ticket_id'], f'{label} cleared on {host}; closed by alert engine.')
        )
        logger.info(f'Auto-resolved ticket #{s["ticket_id"]} — '
                    f'{s["alert_type"]} cleared on {host}')
    if closed_any:
        con.commit()


def _resolve_remediated_cve_alerts(con, today_str=None):
    """Closed-loop auto-close for CVE alert tickets (the remediation half).

    For every ACTIVE CVE alert_state row (alert_type in _DEDUP_TYPES,
    resolved_at IS NULL) whose CVE no longer has ANY open exposure in
    device_vulnerability — i.e. ``COUNT(*) FILTER (status='Open') = 0`` for that
    cve_id — auto-close the linked open ticket and mark the state resolved.

    HARD GATE: the close is driven SOLELY by device_vulnerability having zero
    Open rows for the CVE. We never trust vulnerability_cache.exposed_machines
    (it can lag). A CVE with even one Open device row is skipped. MUST run AFTER
    the Defender close-by-absence step so device_vulnerability reflects current
    truth before we read it.

    Idempotent: guarded by resolved_at IS NULL + status NOT IN(Closed,Merged),
    and only ever touches source='alert' tickets. Does not commit — the caller
    owns the transaction. Returns the number of tickets closed.
    """
    from models import now_mst
    if today_str is None:
        today_str = now_mst().strftime('%Y-%m-%d')

    # The CVE id is stored on the alert_state key as "<rule>:cve:<CVE-ID>".
    states = con.execute(
        """SELECT id, alert_key, ticket_id, alert_type
           FROM alert_state
           WHERE alert_type = ANY(%s)
             AND resolved_at IS NULL""",
        (list(_DEDUP_TYPES),)
    ).fetchall()

    closed = 0
    for s in states:
        key = s['alert_key'] or ''
        marker = ':cve:'
        if marker not in key:
            continue
        cve_id = key.split(marker, 1)[1]
        if not cve_id:
            continue

        # HARD GATE: count Open exposure rows for this exact CVE. Anything > 0
        # means the CVE is still live — do NOT close.
        row = con.execute(
            """SELECT COUNT(*) AS open_rows
               FROM device_vulnerability
               WHERE cve_id = ? AND status = 'Open'""",
            (cve_id,)
        ).fetchone()
        open_rows = (row['open_rows'] if row else 0) or 0
        if open_rows > 0:
            continue  # still exposed — never fabricate a remediated close

        # Remediated everywhere. Mark the state resolved (idempotent).
        con.execute(
            "UPDATE alert_state SET resolved_at = NOW() WHERE id = ? AND resolved_at IS NULL",
            (s['id'],)
        )

        if not s['ticket_id']:
            continue
        open_ticket = con.execute(
            f"""SELECT id FROM support_ticket
               WHERE id = ? AND source = 'alert'
                 AND status NOT IN {_TERMINAL_TICKET_SQL}""",
            (s['ticket_id'],)
        ).fetchone()
        if not open_ticket:
            continue

        con.execute(
            """UPDATE support_ticket
               SET status='Resolved', closed_at=NOW(), updated_at=NOW()
               WHERE id=?""",
            (s['ticket_id'],)
        )
        con.execute(
            """INSERT INTO ticket_note (ticket_id, user_id, content, created_at)
               VALUES (?, NULL, ?, NOW())""",
            (s['ticket_id'],
             f'Auto-resolved: Defender confirms {cve_id} remediated across all '
             f'devices on {today_str} (0 open exposures in device_vulnerability). '
             f'Ticket closed by the CVE remediation loop.')
        )
        con.execute(
            """INSERT INTO ticket_activity (ticket_id, user_id, action, detail, created_at)
               VALUES (?, NULL, 'auto_resolved', ?, NOW())""",
            (s['ticket_id'],
             f'{cve_id} remediated across all devices; closed by CVE remediation loop.')
        )
        closed += 1
        logger.info(f'CVE remediation loop: auto-resolved ticket #{s["ticket_id"]} '
                    f'— {cve_id} no longer exposed on any device.')
    return closed


# ── Disk-space loop: diagnostic + safe auto-cleanup ──────────────────────────
# The agent caps run_script at ~300s; both scripts are bounded well under that.
_DISK_SCRIPT_TIMEOUT = 240


def _enqueue_remediation(agent_id, asset_id, action_type, payload,
                         ticket_id=None, dedup_substr=None, once_per_ticket=False):
    """POST a run_script remediation to the gateway's reconnect-remediation
    engine. Online → dispatched now; offline → queued for the reconnect flush
    (best-effort, never raises).

    Idempotent guards (both keyed on dedup_substr appearing in payload):
      * default: skip if a queued/deploying row for this AGENT already contains
        it (don't double-dispatch the same in-flight action).
      * once_per_ticket=True: skip if ANY row for this TICKET already carries the
        action in ANY status (queued/deploying/completed/failed/abandoned). Used
        for the destructive disk cleanup and the diagnostic so a disk that stays
        full doesn't re-fire either of them every cooldown cycle — once we've
        attempted it for a ticket, leave the ticket for a human.

    Returns a small dict describing the outcome (status: skipped/queued/
    deploying/error)."""
    if once_per_ticket and ticket_id and dedup_substr:
        # Once-per-ticket: any prior attempt (terminal or not) for THIS ticket
        # short-circuits. The disk loop re-opens the same evolving ticket each
        # cooldown cycle; without this, a still-full disk re-queues the cleanup
        # hourly, indefinitely. Match on ticket_id so a completed/failed/abandoned
        # row still blocks the re-run.
        con = _get_db()
        try:
            prior = con.execute(
                """SELECT id, status FROM rmm_remediation_queue
                   WHERE ticket_id = %s
                     AND action_type = 'run_script'
                     AND payload LIKE %s
                   ORDER BY id DESC LIMIT 1""",
                (ticket_id, f'%{dedup_substr}%')
            ).fetchone()
        except Exception:
            prior = None
        finally:
            con.close()
        if prior:
            return {'agent_id': agent_id, 'status': 'skipped',
                    'reason': 'already attempted for ticket',
                    'existing_id': prior['id']}
    elif dedup_substr:
        con = _get_db()
        try:
            existing = con.execute(
                """SELECT id, status FROM rmm_remediation_queue
                   WHERE agent_id = %s
                     AND action_type = 'run_script'
                     AND status IN ('queued', 'deploying')
                     AND payload LIKE %s
                   ORDER BY id DESC LIMIT 1""",
                (agent_id, f'%{dedup_substr}%')
            ).fetchone()
        except Exception:
            existing = None
        finally:
            con.close()
        if existing:
            return {'agent_id': agent_id, 'status': 'skipped',
                    'reason': 'already pending', 'existing_id': existing['id']}

    body = {
        'action_type': action_type,
        'payload':     payload,
        'asset_id':    asset_id,
        'created_by':  None,
        'ticket_id':   ticket_id,
    }
    try:
        resp = requests.post(
            f"{RMM_GATEWAY_INTERNAL}/remediation/{agent_id}/enqueue",
            json=body, timeout=10,
        )
        gw = resp.json()
    except Exception as e:
        logger.warning(f'disk-loop enqueue failed for {agent_id}: {e}')
        return {'agent_id': agent_id, 'status': 'error', 'error': str(e)}
    return {'agent_id': agent_id, 'status': gw.get('status', 'queued'),
            'delivered': gw.get('delivered', False), 'row_id': gw.get('id')}


def _disk_diagnostic_code(letter: str) -> str:
    """READ-ONLY 'what's filling it' script for one Windows drive.

    Get-*/Measure-Object only — never deletes or modifies anything. Reports the
    drive type, top-level folders by size on the affected drive, the known hog
    paths, and the largest >1GB files. `letter` is e.g. 'C:'."""
    drv = letter.rstrip('\\').rstrip('/')  # 'C:'
    root = drv + '\\'
    # NB: all paths below are inspected with Get-ChildItem/Measure-Object only.
    return f'''$ErrorActionPreference='SilentlyContinue'
$drv='{drv}'; $root='{root}'
function SizeGB($p){{ if(Test-Path $p){{ $b=(Get-ChildItem -LiteralPath $p -Recurse -Force -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum; if($b){{ [math]::Round($b/1GB,2) }} else {{ 0 }} }} else {{ 'n/a' }} }}
$ld = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drv'"
"=== Disk diagnostic for $drv ==="
if($ld){{ "DriveType={{0}} (2=removable 3=fixed 4=network 5=optical/mounted)  FileSystem={{1}}  FreeGB={{2}}  SizeGB={{3}}" -f $ld.DriveType,$ld.FileSystem,[math]::Round($ld.FreeSpace/1GB,1),[math]::Round($ld.Size/1GB,1) }}
""
"=== Top folders by size (depth 1) ==="
Get-ChildItem -LiteralPath $root -Directory -Force -EA SilentlyContinue | ForEach-Object {{
  $sz=(Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum
  [PSCustomObject]@{{ Folder=$_.FullName; GB=[math]::Round(($sz)/1GB,2) }}
}} | Sort-Object GB -Descending | Select-Object -First 12 | Format-Table -Auto | Out-String
""
"=== Known hogs ==="
$tmp=$env:TEMP
"Windows\\Temp           : $(SizeGB (Join-Path $root 'Windows\\Temp')) GB"
"%TEMP% ($tmp): $(SizeGB $tmp) GB"
"SoftwareDistribution\\Download: $(SizeGB (Join-Path $root 'Windows\\SoftwareDistribution\\Download')) GB"
"Windows\\Installer      : $(SizeGB (Join-Path $root 'Windows\\Installer')) GB  (orphaned-MSI — needs validation, NOT auto-cleaned)"
"Windows.old            : $(SizeGB (Join-Path $root 'Windows.old')) GB"
$rb = Join-Path $root '$Recycle.Bin'
"Recycle Bin            : $(SizeGB $rb) GB"
$pf = Join-Path $root 'pagefile.sys'; if(Test-Path $pf){{ "pagefile.sys           : $([math]::Round((Get-Item $pf -Force).Length/1GB,2)) GB" }}
$hb = Join-Path $root 'hiberfil.sys'; if(Test-Path $hb){{ "hiberfil.sys           : $([math]::Round((Get-Item $hb -Force).Length/1GB,2)) GB" }}
$wim = Get-ChildItem -LiteralPath $root -Recurse -Force -Include *.iso,*.wim,*.vhd,*.vhdx -EA SilentlyContinue | Where-Object {{ $_.Length -gt 1GB }} | Select-Object -First 5
if($wim){{ ""; "=== Large disk-image files (mounted ISO/WIM/VHD?) ==="; $wim | ForEach-Object {{ "{{0}}  {{1}} GB" -f $_.FullName,[math]::Round($_.Length/1GB,2) }} }}
""
"=== Largest files >1GB (top 15) ==="
Get-ChildItem -LiteralPath $root -Recurse -Force -File -EA SilentlyContinue | Where-Object {{ $_.Length -gt 1GB }} | Sort-Object Length -Descending | Select-Object -First 15 | ForEach-Object {{ "{{0}}  {{1}} GB" -f $_.FullName,[math]::Round($_.Length/1GB,2) }}
'''


def _disk_cleanup_code(letter: str) -> str:
    """SAFE auto-cleanup for one Windows OS drive (C: only — caller-gated).

    Deletes ONLY well-known safe caches and logs exactly what was freed per path.
    Strict allowlist — NEVER user data (Downloads), NEVER Windows\\Installer
    (orphaned-MSI needs validation), NEVER data/backup volumes. Idempotent
    (re-runnable; already-empty paths free 0) and bounded (per-path try/catch,
    -Force -Recurse with -EA SilentlyContinue so locked files are skipped, not
    fatal)."""
    drv = letter.rstrip('\\').rstrip('/')
    root = drv + '\\'
    return f'''$ErrorActionPreference='SilentlyContinue'
$root='{root}'
$freed=0.0
function Clean($label,$path){{
  if(-not (Test-Path $path)){{ "{{0}}: n/a" -f $label; return }}
  $before=(Get-ChildItem -LiteralPath $path -Recurse -Force -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum
  Get-ChildItem -LiteralPath $path -Force -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue
  $after=(Get-ChildItem -LiteralPath $path -Recurse -Force -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum
  $gb=[math]::Round((($before-$after))/1GB,3); if($gb -lt 0){{ $gb=0 }}
  $script:freed += $gb
  "{{0}}: freed {{1}} GB ({{2}})" -f $label,$gb,$path
}}
"=== Safe cache cleanup on $root (OS drive) ==="
# ── SAFE-CACHE ALLOWLIST (the ONLY paths this script touches) ──
Clean 'Windows\\Temp'              (Join-Path $root 'Windows\\Temp')
Clean 'WU cache (SoftwareDistribution\\Download)' (Join-Path $root 'Windows\\SoftwareDistribution\\Download')
# Per-user %TEMP% across all profiles
Get-ChildItem -LiteralPath (Join-Path $root 'Users') -Directory -Force -EA SilentlyContinue | ForEach-Object {{
  Clean ("User TEMP: " + $_.Name) (Join-Path $_.FullName 'AppData\\Local\\Temp')
}}
# Browser caches (Chrome/Edge/Firefox) per profile — caches only, never profile data
Get-ChildItem -LiteralPath (Join-Path $root 'Users') -Directory -Force -EA SilentlyContinue | ForEach-Object {{
  $u=$_.FullName
  Clean ("Chrome cache: " + $_.Name) (Join-Path $u 'AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cache')
  Clean ("Edge cache: "   + $_.Name) (Join-Path $u 'AppData\\Local\\Microsoft\\Edge\\User Data\\Default\\Cache')
  # Firefox: delete ONLY the per-profile cache2 dirs — never the profile itself.
  Get-ChildItem -LiteralPath (Join-Path $u 'AppData\\Local\\Mozilla\\Firefox\\Profiles') -Directory -Force -EA SilentlyContinue | ForEach-Object {{
    Clean ("Firefox cache: " + $_.Name) (Join-Path $_.FullName 'cache2')
  }}
}}
# Recycle Bin — C-anchored ONLY. Clear-RecycleBin -DriveLetter C is unreliable
# (on many builds it empties EVERY volume's recycle bin, which would violate the
# C:-only contract and could purge a data/backup volume). So we delete only the
# C-rooted $Recycle.Bin contents the same allowlisted way as every other path.
try {{ $rb=(Join-Path $root '$Recycle.Bin')
  $rbBefore=(Get-ChildItem -LiteralPath $rb -Recurse -Force -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum
  Get-ChildItem -LiteralPath $rb -Force -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue
  $rbAfter=(Get-ChildItem -LiteralPath $rb -Recurse -Force -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum
  $rbGb=[math]::Round((($rbBefore-$rbAfter))/1GB,3); if($rbGb -lt 0){{ $rbGb=0 }}; $freed+=$rbGb
  "Recycle Bin: freed $rbGb GB" }} catch {{ "Recycle Bin: skipped" }}
""
"=== TOTAL FREED: {{0}} GB ===" -f [math]::Round($freed,2)
"NOTE: Downloads, Windows\\Installer, and data/backup volumes are intentionally NOT touched."
'''


def _dispatch_disk_diagnostic(agent_id, asset_id, host, letter, ticket_id):
    """Best-effort: enqueue the READ-ONLY disk diagnostic for `host`, tied to the
    disk ticket so the result lands as a ticket note. Never blocks ticket
    creation. Windows-only (PowerShell)."""
    if not letter or not letter.endswith(':'):
        return  # Linux/macOS: skip the PowerShell diagnostic
    payload = {
        'type':    'run_script',
        'shell':   'powershell',
        'code':    _disk_diagnostic_code(letter),
        'timeout': _DISK_SCRIPT_TIMEOUT,
    }
    # dedup_substr must be a literal that survives into the stored payload. The
    # script header is "=== Disk diagnostic for $drv ===" — $drv is a runtime var,
    # so match on the literal prefix "Disk diagnostic for" (not the letter, which
    # is never substituted server-side). once_per_ticket: one diagnostic note per
    # ticket is enough — don't pile a fresh one on every hourly cooldown cycle.
    res = _enqueue_remediation(
        agent_id, asset_id, 'run_script', payload, ticket_id=ticket_id,
        dedup_substr='Disk diagnostic for', once_per_ticket=True)
    if res.get('status') == 'error':
        # Host likely offline / gateway unreachable — note it on the ticket so the
        # tech knows the diagnostic didn't run. Best-effort, never raises.
        try:
            con = _get_db()
            con.execute(
                "INSERT INTO ticket_note (ticket_id, user_id, content, created_at) "
                "VALUES (%s, NULL, %s, NOW())",
                (ticket_id, f'[Auto] Disk diagnostic could not be dispatched to '
                            f'{host} (offline/unreachable): {res.get("error")}'))
            con.commit(); con.close()
        except Exception:
            pass
    else:
        logger.info(f'disk-loop: diagnostic {res.get("status")} for {host} {letter} '
                    f'(ticket #{ticket_id})')


def _dispatch_disk_cleanup(agent_id, asset_id, host, letter, ticket_id):
    """Best-effort: enqueue the SAFE auto-cleanup for a FIXED OS drive (C:) on a
    disk_critical. Frees only allowlisted caches; logs what it freed. The existing
    disk_critical auto-resolve closes the ticket if free space recovers; if not
    (e.g. Installer bloat), the ticket stays open with the diagnostic note."""
    # Defense-in-depth: only the Windows OS drive. The caller already gates on
    # is_os_drive, but never let cleanup run against anything but C:.
    if (letter or '').upper() != 'C:':
        return
    payload = {
        'type':    'run_script',
        'shell':   'powershell',
        'code':    _disk_cleanup_code(letter),
        'timeout': _DISK_SCRIPT_TIMEOUT,
    }
    # once_per_ticket: the cleanup DELETES files — attempt it at most once per
    # ticket. The disk loop re-opens the same evolving ticket every cooldown cycle
    # for a disk that stays full; without this guard the destructive cleanup would
    # re-queue hourly, indefinitely. If a single safe-cleanup didn't recover the
    # drive (e.g. Installer/user-data bloat we intentionally never touch), leave it
    # for a human. Matches ANY prior row (queued/deploying/completed/failed/
    # abandoned) for this ticket carrying 'Safe cache cleanup on'.
    res = _enqueue_remediation(
        agent_id, asset_id, 'run_script', payload, ticket_id=ticket_id,
        dedup_substr='Safe cache cleanup on', once_per_ticket=True)
    logger.info(f'disk-loop: cleanup {res.get("status")} for {host} {letter} '
                f'(ticket #{ticket_id})')


def _fire_alert(con, rule, message, agent_id=None, asset_id=None,
                hostname=None, extra_html='', dedup_token=None):
    """
    Create alert_log row, optional ticket, email, Teams notification, bell.
    `rule` is a sqlite3.Row from alert_rule.

    `dedup_token` (e.g. a CVE id) gives token-keyed alert types a stable
    per-event identity so repeated detections of the SAME event collapse onto
    one evolving ticket instead of spawning a new one each cooldown cycle.
    """
    rule_id       = rule['id']
    category      = rule['category']
    alert_type    = rule['alert_type']
    cooldown      = rule['cooldown_minutes'] or 60
    email_notify  = rule['email_notify']
    teams_notify  = rule['teams_notify']
    teams_wh      = rule['teams_webhook_url'] or _get_setting(con, 'teams_webhook_url')
    auto_ticket   = rule['auto_ticket']
    priority      = rule['ticket_priority'] or 'Normal'
    assigned_uid  = rule['assigned_to_user_id']
    label         = rule['label'] or alert_type

    # Only admins may HOLD a ticket. Drop any non-admin (or stale) rule assignee
    # so an auto-created alert ticket is never assigned to a non-admin user.
    if assigned_uid is not None:
        try:
            _arow = con.execute(
                'SELECT role FROM "user" WHERE id = ?', (assigned_uid,)
            ).fetchone()
            if not _arow or _arow['role'] != 'admin':
                assigned_uid = None
        except Exception:
            assigned_uid = None

    if not _cooldown_ok(con, rule_id, agent_id, asset_id, cooldown):
        # Condition still active — refresh last_seen_at so state doesn't get
        # auto-resolved while we're in the cooldown window. NOT a new occurrence.
        try:
            _upsert_alert_state(con, rule, agent_id, asset_id, hostname,
                                ticket_id=None, bump=False, dedup_token=dedup_token)
        except Exception:
            pass
        return None  # already fired recently

    # Create / re-open ticket (deduplicated across close — one evolving ticket
    # per (rule, host) instead of a fresh ticket every cooldown cycle).
    ticket_id = None
    if auto_ticket:
        try:
            cat_map = {
                'agent': 'Hardware', 'asset': 'Hardware',
                'vulnerability': 'Security', 'eagle_eyes': 'General'
            }
            cat = cat_map.get(category, 'General')
            ticket_id = _open_or_reopen_alert_ticket(
                con, rule, label, cat, priority, assigned_uid,
                message, agent_id, asset_id, hostname, rule_id,
                dedup_token=dedup_token)
        except Exception as e:
            logger.error(f'Auto-ticket open/reopen failed: {e}')

    # Track alert state for continuous-condition auto-resolution.
    # bump=True: this is a real fire (cooldown elapsed), so count the occurrence.
    try:
        _upsert_alert_state(con, rule, agent_id, asset_id, hostname,
                            ticket_id, bump=True, dedup_token=dedup_token)
    except Exception as e:
        logger.debug(f'alert_state upsert failed (table may not exist yet): {e}')

    # Insert alert log
    con.execute(
        """INSERT INTO alert_log (rule_id, category, alert_type, agent_id, asset_id, message, ticket_id, fired_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (rule_id, category, alert_type, agent_id or '', asset_id or 0, message, ticket_id)
    )

    # Insert notification bell
    icon_map = {
        'agent': 'bi-pc-display', 'asset': 'bi-hdd',
        'vulnerability': 'bi-shield-exclamation', 'eagle_eyes': 'bi-eye'
    }
    color_map = {
        'Urgent': 'danger', 'High': 'danger', 'Normal': 'warning', 'Low': 'info'
    }
    link = f'/alerts/center#{category}'
    if ticket_id:
        link = f'/tickets/{ticket_id}'
    # Only surface in the notification bell if the rule is notify-worthy (same flag that
    # gates email). Operational noise (RAM/CPU/disk/battery — email_notify=False) used to
    # write a bell entry every cycle and buried the bell; mirror the email policy here.
    if email_notify:
        con.execute(
            """INSERT INTO notification_bell (title, body, icon, color, link, read_flag, created_at)
               VALUES (?, ?, ?, ?, ?, false, NOW())""",
            (label, message, icon_map.get(category, 'bi-bell'),
             color_map.get(priority, 'warning'), link)
        )

    con.commit()

    # Email
    if email_notify:
        body_html = f"""
        <div style="font-family:sans-serif;max-width:600px;">
          <h3 style="color:#dc3545;">🚨 Alert: {label}</h3>
          <p><strong>Category:</strong> {category.replace('_',' ').title()}</p>
          <p><strong>Details:</strong> {message}</p>
          {f'<p><strong>Hostname:</strong> {hostname}</p>' if hostname else ''}
          {f'<p><strong>Ticket created:</strong> <a href="/tickets/{ticket_id}">#{ticket_id}</a></p>' if ticket_id else ''}
          {extra_html}
          <hr><p style="color:#888;font-size:.85em;">Alert rule #{rule_id} · {_now().strftime('%Y-%m-%d %H:%M')} UTC</p>
        </div>"""
        _send_email(f'[ALERT] {label}', body_html)

    # Teams
    if teams_notify and teams_wh:
        _send_teams(teams_wh, label,
                    f'{message}{" | Hostname: " + hostname if hostname else ""}')

    logger.info(f'Alert fired: [{category}] {alert_type} — {message}')
    return ticket_id


# ─────────────────────────────────────────────────────────────────────────────
# Evaluators
# ─────────────────────────────────────────────────────────────────────────────

def _eval_agent_alerts(con, rules_by_type):
    """Check RMM telemetry for every active agent."""
    agents = con.execute(
        """SELECT a.agent_id, a.asset_id, t.hostname,
                  t.cpu_percent, t.ram_percent, t.battery_percent,
                  t.battery_present, t.battery_charging,
                  t.disk_json, t.security_json, a.last_seen_at
           FROM rmm_agent a
           LEFT JOIN rmm_telemetry t ON t.agent_id = a.agent_id
           WHERE a.enabled = true"""
    ).fetchall()

    for ag in agents:
        aid      = ag['agent_id']
        asset_id = ag['asset_id']
        host     = ag['hostname'] or aid

        # ── Offline ──────────────────────────────────────────────────────────
        rule = rules_by_type.get('offline')
        if rule and rule['enabled']:
            last_seen = ag['last_seen_at']
            if last_seen:
                try:
                    ls = datetime.strptime(last_seen[:19], '%Y-%m-%d %H:%M:%S')
                    thresh_m = rule['threshold_value'] or 60
                    if (_now() - ls).total_seconds() / 60 > thresh_m:
                        _fire_alert(con, rule,
                                    f'{host} has not checked in for over {int(thresh_m)} minutes.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)
                except Exception:
                    pass

        # Skip telemetry checks if no data
        if ag['cpu_percent'] is None:
            continue

        # ── CPU ──────────────────────────────────────────────────────────────
        rule = rules_by_type.get('cpu_high')
        if rule and rule['enabled']:
            if (ag['cpu_percent'] or 0) >= rule['threshold_value']:
                _fire_alert(con, rule,
                            f'{host} CPU at {ag["cpu_percent"]:.0f}% (threshold {rule["threshold_value"]:.0f}%).',
                            agent_id=aid, asset_id=asset_id, hostname=host)

        # ── RAM ──────────────────────────────────────────────────────────────
        rule = rules_by_type.get('ram_high')
        if rule and rule['enabled']:
            if (ag['ram_percent'] or 0) >= rule['threshold_value']:
                _fire_alert(con, rule,
                            f'{host} RAM at {ag["ram_percent"]:.0f}% (threshold {rule["threshold_value"]:.0f}%).',
                            agent_id=aid, asset_id=asset_id, hostname=host)

        # ── Disk ─────────────────────────────────────────────────────────────
        disk_json = ag['disk_json']
        if disk_json:
            try:
                disks = json.loads(disk_json)
                for d in (disks if isinstance(disks, list) else []):
                    mp = (d.get('mountpoint') or '').strip()
                    letter = mp.upper().rstrip('\\').rstrip('/')  # 'C:' / 'D:' / ''
                    is_windows_os = letter == 'C:'
                    is_linux_os   = mp == '/'

                    # ── DriveType gate (2.9.22+ agents) ───────────────────────
                    # drive_type from Win32_LogicalDisk: 2=removable, 3=fixed,
                    # 4=network, 5=optical/mounted-ISO. Alert on FIXED drives only
                    # (drive_type==3) regardless of letter, so a real D:/E: data
                    # volume filling up DOES alert; SKIP removable/network/optical-
                    # or-mounted-image — that suppresses the ISO/optical false-
                    # positive class on ANY letter.
                    #
                    # GRACEFUL FALLBACK: older agents don't report drive_type. When
                    # it's absent, keep the legacy OS-drive-only behavior (C:\ or /)
                    # so nothing breaks during the canary rollout.
                    dt = d.get('drive_type')
                    if dt is not None:
                        try:
                            dt = int(dt)
                        except (TypeError, ValueError):
                            dt = None
                    if dt is not None:
                        if dt != 3:
                            continue  # not a fixed drive — skip (USB/ISO/optical/net)
                    else:
                        # No drive_type → legacy gate: OS drive only.
                        if not is_windows_os and not is_linux_os:
                            continue

                    pct_free = 100 - (d.get('percent', 100))
                    drive    = d.get('device', '?')
                    # Auto-cleanup eligibility (Part 4): only the Windows OS volume
                    # (C:) gets the safe-cache cleanup — NEVER a data/backup volume
                    # (a fixed D:/E:) and NEVER the Linux root (cleanup is C:-only
                    # PowerShell). Gated below on is_windows_os at the call site.
                    for rtype in ('disk_critical', 'disk_low'):
                        rule = rules_by_type.get(rtype)
                        if rule and rule['enabled'] and pct_free <= rule['threshold_value']:
                            tid = _fire_alert(con, rule,
                                        f'{host} drive {drive} free space at '
                                        f'{pct_free:.1f}% (threshold {rule["threshold_value"]:.0f}%).',
                                        agent_id=aid, asset_id=asset_id, hostname=host)
                            # New ticket just created (cooldown elapsed) → attach a
                            # read-only diagnostic, and for the Windows OS drive (C:)
                            # on a disk_critical, kick off the safe auto-cleanup.
                            if tid:
                                _dispatch_disk_diagnostic(
                                    aid, asset_id, host, letter, tid)
                                # Auto-cleanup is Windows C:-only (PowerShell, C-
                                # anchored allowlist). Skip it outright for the Linux
                                # root rather than calling into the C:-only no-op.
                                if rtype == 'disk_critical' and is_windows_os:
                                    _dispatch_disk_cleanup(
                                        aid, asset_id, host, letter, tid)
                            break  # only fire the most severe
            except Exception:
                pass

        # ── Battery ──────────────────────────────────────────────────────────
        if ag['battery_present']:
            rule = rules_by_type.get('battery_low')
            if rule and rule['enabled']:
                bat = ag['battery_percent'] or 100
                if bat <= rule['threshold_value']:
                    _fire_alert(con, rule,
                                f'{host} battery health at {bat:.0f}% '
                                f'(threshold {rule["threshold_value"]:.0f}%).',
                                agent_id=aid, asset_id=asset_id, hostname=host)

            rule = rules_by_type.get('battery_not_chg')
            if rule and rule['enabled']:
                if ag['battery_present'] and not ag['battery_charging'] \
                        and (ag['battery_percent'] or 100) < 20:
                    _fire_alert(con, rule,
                                f'{host} battery at {ag["battery_percent"]:.0f}% and not charging.',
                                agent_id=aid, asset_id=asset_id, hostname=host)

        # ── Security JSON (AV, Firewall, pending reboot) ─────────────────────
        sec_json = ag['security_json']
        if sec_json:
            try:
                sec = json.loads(sec_json)

                rule = rules_by_type.get('av_disabled')
                if rule and rule['enabled']:
                    av_ok = sec.get('antivirus_enabled', True)
                    av_upd = sec.get('antivirus_updated', True)
                    if not av_ok or not av_upd:
                        reason = 'disabled' if not av_ok else 'definitions out of date'
                        _fire_alert(con, rule,
                                    f'{host} antivirus is {reason}.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

                rule = rules_by_type.get('firewall_off')
                if rule and rule['enabled']:
                    if not sec.get('firewall_enabled', True):
                        _fire_alert(con, rule,
                                    f'{host} Windows Firewall is disabled.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

                rule = rules_by_type.get('pending_reboot')
                if rule and rule['enabled']:
                    reboot_since = sec.get('pending_reboot_since')
                    if reboot_since:
                        try:
                            rb = datetime.strptime(reboot_since[:10], '%Y-%m-%d')
                            days = (date.today() - rb.date()).days
                            if days >= rule['threshold_value']:
                                _fire_alert(con, rule,
                                            f'{host} has had a pending reboot for {days} days.',
                                            agent_id=aid, asset_id=asset_id, hostname=host)
                        except Exception:
                            pass

                rule = rules_by_type.get('failed_logins')
                if rule and rule['enabled']:
                    fails = sec.get('failed_logins_24h', 0)
                    if fails >= rule['threshold_value']:
                        _fire_alert(con, rule,
                                    f'{host} had {fails} failed login attempts in the last 24 hours.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

                rule = rules_by_type.get('new_local_admin')
                if rule and rule['enabled']:
                    new_admins = sec.get('new_local_admins', [])
                    for acct in new_admins:
                        _fire_alert(con, rule,
                                    f'{host}: new local admin account created — {acct}.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

            except Exception:
                pass


def _eval_asset_alerts(con, rules_by_type):
    """Check asset table for lifecycle/warranty conditions."""
    assets = con.execute(
        """SELECT a.id, a.name, a.warranty_expiry, a.eol_date,
                  a.purchase_date, a.expected_life_years, a.replacement_date, a.last_seen,
                  CASE WHEN ra.asset_id IS NOT NULL THEN 1 ELSE 0 END AS has_rmm
           FROM asset a
           LEFT JOIN rmm_agent ra ON ra.asset_id = a.id AND ra.enabled = true
           WHERE a.status != 'Disposed'"""
    ).fetchall()

    today = date.today()

    for a in assets:
        aid  = a['id']
        name = a['name'] or f'Asset #{aid}'

        # ── Warranty ─────────────────────────────────────────────────────────
        if a['warranty_expiry']:
            try:
                exp = date.fromisoformat(str(a['warranty_expiry']))
                days_left = (exp - today).days

                # Expired
                rule = rules_by_type.get('warranty_expired')
                if rule and rule['enabled'] and days_left < 0:
                    _fire_alert(con, rule,
                                f'{name} warranty expired {abs(days_left)} days ago ({exp}).',
                                asset_id=aid, hostname=name)

                # 30-day warning
                rule = rules_by_type.get('warranty_30')
                if rule and rule['enabled'] and 0 <= days_left <= 30:
                    _fire_alert(con, rule,
                                f'{name} warranty expires in {days_left} days ({exp}).',
                                asset_id=aid, hostname=name)

                # 90-day warning
                rule = rules_by_type.get('warranty_90')
                if rule and rule['enabled'] and 30 < days_left <= 90:
                    _fire_alert(con, rule,
                                f'{name} warranty expires in {days_left} days ({exp}).',
                                asset_id=aid, hostname=name)
            except Exception:
                pass

        # ── OS End of Life ────────────────────────────────────────────────────
        if a['eol_date']:
            try:
                eol = date.fromisoformat(str(a['eol_date']))
                days_left = (eol - today).days

                rule = rules_by_type.get('eol_os_passed')
                if rule and rule['enabled'] and days_left < 0:
                    _fire_alert(con, rule,
                                f'{name} OS reached End of Life {abs(days_left)} days ago ({eol}).',
                                asset_id=aid, hostname=name)

                rule = rules_by_type.get('eol_os')
                if rule and rule['enabled'] and 0 <= days_left <= 90:
                    _fire_alert(con, rule,
                                f'{name} OS End of Life in {days_left} days ({eol}). Planning required.',
                                asset_id=aid, hostname=name)
            except Exception:
                pass

        # ── Device age vs expected life ───────────────────────────────────────
        rule = rules_by_type.get('device_age')
        if rule and rule['enabled'] and a['purchase_date']:
            try:
                pd   = date.fromisoformat(str(a['purchase_date']))
                life = a['expected_life_years'] or 3
                age_years = (today - pd).days / 365.25
                if age_years > life:
                    _fire_alert(con, rule,
                                f'{name} is {age_years:.1f} years old (expected life: {life} years).',
                                asset_id=aid, hostname=name)
            except Exception:
                pass

        # ── Not seen (only for non-RMM assets; 'offline' rule covers agents) ──
        rule = rules_by_type.get('not_seen')
        if rule and rule['enabled'] and a['last_seen'] and not a['has_rmm']:
            try:
                ls = datetime.fromisoformat(str(a['last_seen'])[:19])
                days_absent = (_now() - ls).days
                if days_absent >= rule['threshold_value']:
                    _fire_alert(con, rule,
                                f'{name} has not been seen for {days_absent} days.',
                                asset_id=aid, hostname=name)
            except Exception:
                pass


def _eval_vulnerability_alerts(con, rules_by_type):
    """
    Check vulnerability_cache for new Critical/High CVEs and unpatched CVEs.
    Defender sync must have been run first to populate the cache.
    """
    rule_crit = rules_by_type.get('cve_critical')
    rule_high  = rules_by_type.get('cve_high')
    rule_old   = rules_by_type.get('cve_unpatched')

    if rule_crit and rule_crit['enabled']:
        new_crits = con.execute(
            """SELECT cve_id, name, exposed_machines FROM vulnerability_cache
               WHERE severity='Critical'
               AND synced_at::timestamp > NOW() - INTERVAL '1 day'"""
        ).fetchall()
        for v in new_crits:
            if not (v["exposed_machines"] and int(v["exposed_machines"]) > 0):
                continue
            _fire_alert(con, rule_crit,
                        f'Critical CVE detected: {v["cve_id"]} — {v["name"]} '
                        f'({v["exposed_machines"]} device(s) exposed).',
                        hostname='Defender Vulnerability Feed',
                        dedup_token=v["cve_id"])

    if rule_high and rule_high['enabled']:
        new_highs = con.execute(
            """SELECT cve_id, name, exposed_machines FROM vulnerability_cache
               WHERE severity='High'
               AND synced_at::timestamp > NOW() - INTERVAL '1 day'"""
        ).fetchall()
        for v in new_highs:
            if not (v["exposed_machines"] and int(v["exposed_machines"]) > 0):
                continue
            _fire_alert(con, rule_high,
                        f'High CVE detected: {v["cve_id"]} — {v["name"]} '
                        f'({v["exposed_machines"]} device(s) exposed).',
                        hostname='Defender Vulnerability Feed',
                        dedup_token=v["cve_id"])

    if rule_old and rule_old['enabled']:
        thresh_days = int(rule_old['threshold_value'] or 30)
        old_open = con.execute(
            f"""SELECT dv.cve_id, dv.severity, dv.asset_id, a.name as aname
               FROM device_vulnerability dv
               LEFT JOIN asset a ON a.id = dv.asset_id
               WHERE dv.status='Open'
               AND dv.synced_at::timestamp < NOW() - INTERVAL '{thresh_days} days'"""
        ).fetchall()
        for v in old_open:
            _fire_alert(con, rule_old,
                        f'CVE {v["cve_id"]} ({v["severity"]}) on {v["aname"] or v["asset_id"]} '
                        f'has been open for over {thresh_days} days.',
                        asset_id=v['asset_id'])


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def run_evaluator():
    """Blocking loop – run in a daemon thread.
    Uses a timestamp file + exclusive lock so only ONE of the Gunicorn workers
    actually fires alerts per cycle. The lock is held only briefly to read/write
    the timestamp; the timestamp prevents subsequent workers from re-running
    within the same cycle even after the lock is released.
    """
    logger.info('Alert evaluator started.')
    while True:
        try:
            _try_run_once()
        except Exception as e:
            logger.error(f'Alert evaluator error: {e}', exc_info=True)
        time.sleep(EVAL_INTERVAL_S)


def _try_run_once():
    """Acquire lock, check timestamp, run only if this cycle hasn't been handled yet."""
    try:
        with open(ALERT_EVAL_LOCK_PATH, 'a+') as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return  # Another worker is actively writing the timestamp right now

            # Read last-run timestamp
            lf.seek(0)
            content = lf.read().strip()
            if content:
                try:
                    if time.time() - float(content) < (EVAL_INTERVAL_S - 30):
                        fcntl.flock(lf, fcntl.LOCK_UN)
                        return  # Another worker already ran this cycle
                except ValueError:
                    pass

            # Stamp now and release lock before the slow DB work
            lf.seek(0)
            lf.truncate()
            lf.write(str(time.time()))
            lf.flush()
            fcntl.flock(lf, fcntl.LOCK_UN)

    except Exception as e:
        logger.warning(f'Alert eval lock error: {e}')

    _run_once()


def _run_once():
    eval_started_at = _now()
    con = _get_db()
    try:
        # Ensure alert_state table exists (idempotent)
        _ensure_alert_state_table(con)

        # Load enabled rules, keyed by alert_type
        rows = con.execute("SELECT * FROM alert_rule").fetchall()
        rules_by_type = {r['alert_type']: r for r in rows}

        _eval_agent_alerts(con, rules_by_type)
        _eval_asset_alerts(con, rules_by_type)
        _eval_vulnerability_alerts(con, rules_by_type)

        # State-based auto-resolution: close tickets for conditions that cleared
        _resolve_cleared_alerts(con, eval_started_at)
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()


def start_background_thread():
    """Call once from app startup to launch the evaluator daemon thread."""
    t = threading.Thread(target=run_evaluator, daemon=True, name='AlertEvaluator')
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Defender sync helper (called from UI route)
# ─────────────────────────────────────────────────────────────────────────────

# Products that are OS/system/library level — always accept because they cannot
# be detected as standalone entries in the installed-apps list. Shared by the
# sync AND the reconciliation page so both apply the IDENTICAL filter.
_ALWAYS_ACCEPT = {
    'windows_11', 'windows_10',
    '.net', '.net_core', '.net_framework', 'asp.net_core',
    'chipset_device_software', 'computing_improvement_program',
    'dynamic_platform_and_thermal_framework', 'dynamic_tuning_technology',
    'proset_wireless', 'rapid_storage_technology',
    'hardware_accelerated_execution_manager',
    # Bundled libraries — cannot validate as standalone apps
    'openssl', 'log4j', 'libwebp', 'commons_text', 'sqlite', 'qt',
    # Ambiguous / meta product names
    'agent', 'update', 'next', 'desktop', 'software_updater',
    'odbc', 'command_update',
}
# Defender product_name → keywords to find in rmm_software.name (lowercase)
_PRODUCT_KEYWORDS = {
    'chrome': ['chrome'], 'chrome_for_mac': ['chrome'],
    'firefox': ['firefox'],
    'python': ['python'],
    'jre': ['java', 'jre', 'jdk', 'temurin', 'liberica', 'corretto', 'zulu'],
    'jdk': ['java', 'jre', 'jdk', 'temurin', 'liberica', 'corretto', 'zulu'],
    'meetings': ['zoom', 'webex', 'teams', 'goto'],
    'reader': ['reader', 'acrobat', 'foxit'],
    'visual_studio_2017': ['visual studio'], 'visual_studio_2022': ['visual studio'],
    'visual_studio_2013': ['visual studio'],
    'edge_chromium-based': ['microsoft edge', 'edge'],
    'edge_webview2_runtime': ['webview2', 'edge'],
    'mariadb': ['mariadb', 'mysql'],
    'visual_studio_code': ['visual studio code'], 'visual_studio_code_for_mac': ['visual studio code'],
    'geforce_experience': ['geforce', 'nvidia'],
    'vim': ['vim'], 'teams': ['teams'],
    'vlc_media_player': ['vlc'], 'vlc_media_player_for_mac': ['vlc'],
    '7-zip': ['7-zip', '7zip'],
    'office': ['microsoft 365', 'office'],
    'notepad++': ['notepad++'], 'wireshark': ['wireshark'],
    'netextender': ['netextender', 'sonicwall'],
    'git': ['git'], 'gimp': ['gimp'], 'silverlight': ['silverlight'],
    'illustrator': ['illustrator'],
    'openoffice': ['openoffice', 'libreoffice'],
    'sourcetree': ['sourcetree'],
    'pdf_reader': ['reader', 'acrobat', 'foxit'],
    'global_vpn_client': ['global vpn', 'sonicwall'],
    'supportassist': ['supportassist'],
    'vm_virtualbox': ['virtualbox'],
    'dragon_center': ['dragon center', 'msi center', 'msi app'],
    'workstation': ['vmware workstation'],
    'itunes': ['itunes'], 'webex': ['webex'], 'webex_meetings': ['webex'],
    'digital_delivery': ['digital delivery', 'autodesk'],
    'filezilla': ['filezilla'],
    'synapse': ['razer synapse'], 'winrar': ['winrar'],
    'tera_term': ['tera term'],
    'nodejs': ['node.js', 'nodejs', 'node '],
    'acrobat_reader_dc': ['acrobat', 'reader'],
    'pycharm': ['pycharm', 'jetbrains'],
    'skype': ['skype'],
    'tortoisesvn': ['tortoisesvn'],
    'creative_cloud': ['creative cloud'],
    'codemeter_runtime': ['codemeter'],
    'keepass': ['keepass'],
    'photoshop_elements': ['photoshop'],
    'tightvnc': ['tightvnc'], 'ultravnc': ['ultravnc'],
    'snagit': ['snagit'], 'mobaxterm': ['mobaxterm'],
    'xmind': ['xmind'], 'beyond_compare': ['beyond compare'],
    'expressvpn': ['expressvpn'],
    'viscosity_for_mac': ['viscosity'],
    'wibukey': ['wibu', 'codemeter'],
    'everything': ['everything'],
}


def build_machine_asset_map(con, machines):
    """Map each Defender machine GUID → tracked asset_id using the SAME match
    ladder the sync uses (rmm_telemetry exact/short, asset.name exact/short, then
    single-match normalized punctuation-insensitive keys). Returns
    (machine_asset_map, unmapped) where unmapped is a list of dicts describing
    machines we could NOT map (for the coverage panel). Read-only.

    Kept in lockstep with the inline logic in sync_defender_vulnerabilities()
    (~line 950) so the reconciliation waterfall reproduces it exactly."""
    machine_asset_map = {}
    unmapped = []

    def _norm(s):
        return ''.join(c for c in (s or '').lower() if c.isalnum())

    for m in machines:
        machine_id = m.get('id')
        fqdn = m.get('computerDnsName', '')
        excluded = bool(m.get('isExcluded'))
        merged   = bool(m.get('mergedIntoMachineId'))
        if not machine_id or not fqdn:
            # Defender row without an id/name can't be mapped or even named. These
            # are typically Defender-side excluded/merged/duplicate entries that
            # the sync also skips — flag as a Defender-side state, not a Tracker
            # coverage gap.
            if machine_id:
                if merged:
                    reason = 'Defender-side: merged into another machine record'
                elif excluded:
                    reason = 'Defender-side: excluded from monitoring' + (f' ({m.get("exclusionReason")})' if m.get('exclusionReason') else '')
                else:
                    reason = 'Defender-side: no DNS name reported (cannot be mapped)'
                unmapped.append({'machine_id': machine_id, 'name': fqdn or '(no DNS name)',
                                 'reason': reason, 'defender_side': True})
            continue
        short_name = fqdn.split('.')[0]
        short_norm = _norm(short_name)

        asset_row = con.execute(
            "SELECT t.asset_id FROM rmm_telemetry t WHERE LOWER(t.hostname)=LOWER(?) AND t.asset_id > 0 LIMIT 1",
            (fqdn,)
        ).fetchone()
        if not asset_row and short_name:
            asset_row = con.execute(
                "SELECT t.asset_id FROM rmm_telemetry t WHERE LOWER(t.hostname)=LOWER(?) AND t.asset_id > 0 LIMIT 1",
                (short_name,)
            ).fetchone()
        if not asset_row:
            asset_row = con.execute(
                "SELECT id AS asset_id FROM asset WHERE LOWER(name)=LOWER(?) AND status!='Disposed' LIMIT 1",
                (fqdn,)
            ).fetchone()
        if not asset_row and short_name:
            asset_row = con.execute(
                "SELECT id AS asset_id FROM asset WHERE LOWER(name)=LOWER(?) AND status!='Disposed' LIMIT 1",
                (short_name,)
            ).fetchone()
        if not asset_row and short_norm:
            norm_rows = con.execute(
                "SELECT id AS asset_id FROM asset "
                "WHERE regexp_replace(LOWER(name), '[^a-z0-9]', '', 'g') = ? "
                "AND status!='Disposed' LIMIT 2",
                (short_norm,)
            ).fetchall()
            if len(norm_rows) == 1:
                asset_row = norm_rows[0]
        if not asset_row and short_norm:
            norm_rows = con.execute(
                "SELECT DISTINCT t.asset_id FROM rmm_telemetry t "
                "WHERE regexp_replace(LOWER(t.hostname), '[^a-z0-9]', '', 'g') = ? "
                "AND t.asset_id > 0 LIMIT 2",
                (short_norm,)
            ).fetchall()
            if len(norm_rows) == 1:
                asset_row = norm_rows[0]

        if asset_row and asset_row['asset_id']:
            machine_asset_map[machine_id] = asset_row['asset_id']
        else:
            # Classify WHY it is unmapped, so the coverage gap is visible.
            nm = (short_name or '').lower()
            if merged:
                reason = 'Defender-side: merged into another machine record'
                ds = True
            elif excluded:
                reason = 'Defender-side: excluded from monitoring' + (f' ({m.get("exclusionReason")})' if m.get('exclusionReason') else '')
                ds = True
            elif any(k in nm for k in ('printer', 'print', 'ricoh', 'npi', 'nas', 'iosafe', 'scan', 'hp24', 'hp48')):
                reason = 'No asset match (printer / NAS / appliance — likely ex-inventory)'
                ds = False
            else:
                reason = 'No asset match (untracked device — short name not in asset/RMM inventory)'
                ds = False
            unmapped.append({'machine_id': machine_id, 'name': fqdn, 'reason': reason, 'defender_side': ds})

    return machine_asset_map, unmapped


def build_software_present_fn(con):
    """Return a predicate software_present(asset_id, product_name) -> bool that
    mirrors the sync's _software_present false-positive filter. Read-only.

    Kept in lockstep with the inline logic in sync_defender_vulnerabilities()
    (~line 1124)."""
    asset_software: dict = {}
    try:
        for _row in con.execute(
            """SELECT ra.asset_id, lower(rs.name) AS sw_name
               FROM rmm_software rs
               JOIN rmm_agent ra ON ra.agent_id = rs.agent_id
               WHERE ra.asset_id IS NOT NULL"""
        ).fetchall():
            _aid = _row['asset_id']
            asset_software.setdefault(_aid, set()).add(_row['sw_name'])
    except Exception as _sw_err:
        logger.warning(f'reconciliation: could not load software inventory for validation: {_sw_err}')

    def software_present(asset_id, product_name: str) -> bool:
        pn = (product_name or '').lower().strip()
        if not pn or pn in _ALWAYS_ACCEPT:
            return True
        installed = asset_software.get(asset_id)
        if installed is None:
            return True  # no RMM inventory — accept conservatively
        keywords = _PRODUCT_KEYWORDS.get(pn)
        if keywords is None:
            return True  # unknown product — accept conservatively
        return any(any(kw in sw for sw in installed) for kw in keywords)

    return software_present


# ─────────────────────────────────────────────────────────────────────────────
# RECONCILIATION SNAPSHOT
# Compute the Defender-side reconciliation waterfall / metrics / coverage from an
# already-fetched (machines, machine_vulns) pull and persist it as a JSON blob in
# the `setting` table so the reconciliation PAGE can render INSTANTLY off the DB
# (no live Defender API on GET). This is the EXACT same math the old synchronous
# blueprint helper did — only the Defender-derived half is frozen here; the page
# overlays live device_vulnerability counts (fast SQL) at render time so the
# Tracker bottom line / drift always reflect the current DB.
#
# Snapshot is written:
#   1. during every sync_defender_vulnerabilities() run (machines + vulns in hand
#      already — nearly free), and
#   2. by the page's async "Refresh from Defender" background thread.
# ─────────────────────────────────────────────────────────────────────────────

RECON_SNAPSHOT_SETTING_KEY = 'vuln_reconciliation_snapshot'


def _recon_severity_norm(s):
    s = (s or '').strip().lower()
    if s == 'critical':       return 'Critical'
    if s == 'high':           return 'High'
    if s in ('medium', 'med'): return 'Medium'
    if s == 'low':            return 'Low'
    return 'Other'


def _is_synthetic_eval_cve(cve_id, description=''):
    """True if this CVE is orphaned Microsoft Defender Evaluation-Lab synthetic data.

    The retired Eval Lab left an AI-generated catalogue in this tenant: every
    catalogue record's description carries '[Generated by AI]', 7999/8000 ids are
    CVE-2026-* (a sparse range, not a contiguous block), plus one 'TVM-' demo id.
    Zero false positives on this tenant — no genuine CVE carries the marker and
    there is no real CVE-2026 in the feed. The catalogue feed has a description
    (use the marker, the tightest signal); the machine-pairs feed has none, so
    fall back to the id prefix there. Phase 1 drops the CVE-2026-* / TVM- block;
    residual synthetic CVE-2025/2024 clusters are a separate later pass.

    Gated by Setting 'defender_drop_synthetic_eval_lab' — flip that off (or delete
    this filter) if Microsoft ever purges the orphaned data at the source."""
    if '[Generated by AI]' in (description or ''):
        return True
    cid = cve_id or ''
    return cid.startswith('CVE-2026-') or cid.startswith('TVM-')


def _drop_synthetic_enabled(con):
    """Read the reversible kill-switch once per sync (avoid a per-CVE Setting read)."""
    return (_get_setting(con, 'defender_drop_synthetic_eval_lab', '0') or '').strip().lower() in ('1', 'true', 'yes', 'on')


def compute_reconciliation_snapshot(con, machines, machine_vulns, fetched_at):
    """Reproduce the sync's mapping + filters against an already-fetched Defender
    pull and return the Defender-derived reconciliation snapshot as a plain dict
    (JSON-serializable). Read-only against the DB.

    GRAIN — device_vulnerability has UNIQUE(asset_id, cve_id) and the sync upserts
    ON CONFLICT, so the Tracker bottom line is DISTINCT (asset, CVE) pairs, NOT raw
    Defender feed rows. Defender's bulk feed returns one row per
    (machine, CVE, *product*), so the same (machine, CVE) can appear several times.
    A pair counts as kept if ANY of its product rows passes the software filter
    (mirrors the DB: one surviving row inserts the pair).

    Waterfall (DISTINCT machine-CVE pairs grain):
        Defender raw distinct (machine,CVE)   = N
          − pairs on unmapped machines        = −A   (coverage gap)
          − software-not-present false-pos    = −B   (_software_present, all fail)
          = kept (machine,CVE)                → collapse to DISTINCT (asset,CVE) = Y
    """
    machine_asset_map, unmapped = build_machine_asset_map(con, machines)
    software_present = build_software_present_fn(con)
    _drop_synth = _drop_synthetic_enabled(con)
    synthetic_dropped = 0

    raw_rows = 0
    pair_info = {}
    raw_machine_ids = set()
    raw_cves_by_sev = {'Critical': set(), 'High': set(), 'Medium': set(), 'Low': set(), 'Other': set()}

    for mv in machine_vulns:
        machine_id = mv.get('machineId', '')
        cve_id     = mv.get('cveId', '')
        if not cve_id:
            continue
        if _drop_synth and _is_synthetic_eval_cve(cve_id):
            synthetic_dropped += 1
            continue
        raw_rows += 1
        sev = _recon_severity_norm(mv.get('severity'))
        if machine_id:
            raw_machine_ids.add(machine_id)
        raw_cves_by_sev[sev].add(cve_id)

        key = (machine_id, cve_id)
        asset_id = machine_asset_map.get(machine_id)
        info = pair_info.get(key)
        if info is None:
            info = {'sev': sev, 'asset_id': asset_id, 'kept': False}
            pair_info[key] = info
        if asset_id and not info['kept']:
            if software_present(asset_id, mv.get('productName', '')):
                info['kept'] = True

    raw_pairs        = len(pair_info)
    unmapped_pairs   = 0
    filtered_pairs   = 0
    reconciled_pairs = 0
    rec_asset_ids    = set()
    rec_cves_by_sev  = {'Critical': set(), 'High': set(), 'Medium': set(), 'Low': set(), 'Other': set()}
    rec_pairs_seen   = set()

    for (machine_id, cve_id), info in pair_info.items():
        if not info['asset_id']:
            unmapped_pairs += 1
            continue
        if not info['kept']:
            filtered_pairs += 1
            continue
        ak = (info['asset_id'], cve_id)
        if ak in rec_pairs_seen:
            continue
        rec_pairs_seen.add(ak)
        reconciled_pairs += 1
        rec_asset_ids.add(info['asset_id'])
        rec_cves_by_sev[info['sev']].add(cve_id)

    # ── Coverage panel sourcing ──
    gap      = sorted([u for u in unmapped if not u.get('defender_side')], key=lambda u: u['name'].lower())
    defsider = sorted([u for u in unmapped if u.get('defender_side')],     key=lambda u: u['name'].lower())
    coverage = {
        'defender_machines':   len(machines),
        'mapped_machines':     len(machine_asset_map),
        'unmapped_machines':   len(unmapped),
        'coverage_gap_count':  len(gap),
        'defender_side_count': len(defsider),
        'gap':                 [{'name': u['name'], 'reason': u['reason']} for u in gap],
        'defender_side':       [{'name': u['name'], 'reason': u['reason']} for u in defsider],
    }

    kept_machine_pairs = raw_pairs - unmapped_pairs - filtered_pairs

    def _sev_counts(d):
        return {k: len(v) for k, v in d.items()}

    snapshot = {
        'fetched_at':  fetched_at.astimezone(timezone.utc).isoformat(),
        # Defender-side waterfall numbers (the live-DB tracker_open/drift/ties_out
        # are overlaid by the page at render time, not frozen here).
        'waterfall': {
            'synthetic_eval_dropped':     synthetic_dropped,
            'raw_feed_rows':              raw_rows,
            'defender_raw':               raw_pairs,
            'minus_unmapped':             unmapped_pairs,
            'minus_filtered':             filtered_pairs,
            'kept_machine_pairs':         kept_machine_pairs,
            'computed_open':              reconciled_pairs,
            'machine_to_asset_collapse':  kept_machine_pairs - reconciled_pairs,
        },
        'defender_metrics': {
            'machines':      len(raw_machine_ids),
            'pairs':         raw_pairs,
            'cves_by_sev':   _sev_counts(raw_cves_by_sev),
            'distinct_cves': len(set().union(*raw_cves_by_sev.values())) if raw_cves_by_sev else 0,
        },
        'reconciled_metrics': {
            'machines':      len(rec_asset_ids),
            'pairs':         reconciled_pairs,
            'cves_by_sev':   _sev_counts(rec_cves_by_sev),
            'distinct_cves': len(set().union(*rec_cves_by_sev.values())) if rec_cves_by_sev else 0,
        },
        'coverage': coverage,
    }
    return snapshot


def write_reconciliation_snapshot(con, snapshot):
    """Persist the reconciliation snapshot JSON to the shared `setting` row.
    Cross-worker (DB-backed) and idempotent — overwrites the single row."""
    import json as _json
    payload = _json.dumps(snapshot)
    con.execute(
        """INSERT INTO setting (key, value) VALUES (?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
        (RECON_SNAPSHOT_SETTING_KEY, payload)
    )


def compute_and_store_reconciliation_snapshot(con, machines, machine_vulns, fetched_at):
    """Compute + persist the reconciliation snapshot in one call. Best-effort:
    never raises into the sync's main path (a snapshot failure must not fail the
    sync)."""
    try:
        snap = compute_reconciliation_snapshot(con, machines, machine_vulns, fetched_at)
        write_reconciliation_snapshot(con, snap)
        logger.info(
            'Reconciliation snapshot stored: defender_raw=%s computed_open=%s '
            'coverage_gap=%s', snap['waterfall']['defender_raw'],
            snap['waterfall']['computed_open'], snap['coverage']['coverage_gap_count']
        )
        return snap
    except Exception as _snap_err:
        logger.warning('Reconciliation snapshot computation failed: %s', _snap_err, exc_info=True)
        return None


def _resolve_installed_version(con_software_index, asset_id, product_key, software_name, vendor):
    """Best-effort installed version for (asset, software) from rmm_software.

    Defender's per-machine software feed does NOT carry the installed version,
    but our own RMM software inventory does. con_software_index is the dict
    built by _build_asset_software_versions(): asset_id -> [(name_lc, version)].
    Matches on a normalized substring of the Defender product/display name.
    Returns the version string or None (None renders as "—" in the UI; we never
    fabricate a version).
    """
    rows = con_software_index.get(asset_id)
    if not rows:
        return None

    def _norm(s):
        return ''.join(c for c in (s or '').lower() if c.isalnum())

    # Candidate keys to match against installed-software names, most specific first.
    cands = [_norm(software_name), _norm(product_key)]
    # product_key is often a vendor-stripped token (e.g. "chrome"); also try the
    # bare last token of the display name.
    cands = [c for c in cands if len(c) >= 3]
    if not cands:
        return None
    best = None
    for name_lc, version in rows:
        nm = _norm(name_lc)
        for c in cands:
            if c in nm or nm in c:
                # Prefer the longest version string (full installed build).
                if version and (best is None or len(version) > len(best)):
                    best = version
                break
    return best


def _build_asset_software_versions(con):
    """asset_id -> list of (software_name_lc, version) from rmm_software.

    Built once per sync so version resolution is in-memory (no per-row query)."""
    idx: dict = {}
    try:
        for row in con.execute(
            """SELECT ra.asset_id, lower(rs.name) AS sw_name, rs.version AS ver
               FROM rmm_software rs
               JOIN rmm_agent ra ON ra.agent_id = rs.agent_id
               WHERE ra.asset_id IS NOT NULL AND rs.version IS NOT NULL"""
        ).fetchall():
            idx.setdefault(row['asset_id'], []).append((row['sw_name'], row['ver']))
    except Exception as e:
        logger.warning(f'software-update sync: could not load rmm_software versions: {e}')
    return idx


def sync_defender_software_updates(con, svc, machine_asset_map):
    """Populate device_software_update from Defender 3rd-party software-update
    recommendations. Reuses the caller's connection + Defender service + the
    SAME machine->asset map the CVE sync built (do NOT null-join on device hash).

    Mirrors the CVE sync's close-by-absence handling EXACTLY: a per-run seen-set
    anti-join keyed on the EXACT (asset_id, product_key) pairs confirmed THIS
    run — NOT a synced_at/NOW() timestamp window (the timestamp approach once
    auto-closed every freshly-inserted row when a naive-UTC start was read in the
    session TZ, commit 7bbaf27). Only rows genuinely absent this run are closed.

    Caller commits. Returns (rows_written, error_or_None).
    """
    try:
        recs = svc.get_software_update_recommendations()
        version_index = _build_asset_software_versions(con)

        # (asset_id, product_key) pairs confirmed THIS run. Only entries absent
        # from this set may be closed by close-by-absence.
        seen_pairs: set = set()
        written = 0
        for rec in recs:
            product_key = rec['product_key']
            if not product_key:
                continue
            for m in rec['machines']:
                asset_id = machine_asset_map.get(m['machineId'])
                if not asset_id:
                    continue  # untracked / Defender-side machine — skip (no null-join)
                installed = _resolve_installed_version(
                    version_index, asset_id, product_key,
                    rec['software_name'], rec['vendor'])
                con.execute(
                    """INSERT INTO device_software_update
                       (asset_id, agent_id, software_name, product_key, vendor,
                        current_version, recommended_version, severity, weaknesses,
                        public_exploit, source, status, synced_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?, 'defender','Open', NOW())
                       ON CONFLICT(asset_id, product_key) DO UPDATE SET
                         software_name=excluded.software_name,
                         vendor=excluded.vendor,
                         current_version=excluded.current_version,
                         recommended_version=excluded.recommended_version,
                         severity=excluded.severity,
                         weaknesses=excluded.weaknesses,
                         public_exploit=excluded.public_exploit,
                         synced_at=excluded.synced_at,
                         -- Defender still flags this update → re-open it unless a
                         -- human deliberately Accepted it (sticky).
                         status=CASE WHEN device_software_update.status='Accepted'
                                     THEN 'Accepted' ELSE 'Open' END""",
                    (asset_id, m['machineId'], rec['software_name'], product_key,
                     rec['vendor'], installed, rec['recommended_version'],
                     rec['severity'], rec['weaknesses'], rec['public_exploit'])
                )
                seen_pairs.add((asset_id, product_key))
                written += 1

        # ── Close-by-absence (seen-set anti-join, NOT a timestamp window) ──
        # If Defender no longer reports an update for a (asset, product) it
        # monitors, the app has been updated → mark 'Updated'. Scope the close to
        # Defender-monitored assets only; never touch a human 'Accepted' row.
        defender_asset_ids = list(set(machine_asset_map.values()))
        if defender_asset_ids:
            import psycopg2.extras as _pg_extras
            raw = con.cursor()._c  # raw psycopg2 cursor (RealDictCursor)
            raw.execute(
                "CREATE TEMP TABLE _dsu_seen "
                "(asset_id BIGINT, product_key TEXT) ON COMMIT DROP"
            )
            if seen_pairs:
                _pg_extras.execute_values(
                    raw,
                    "INSERT INTO _dsu_seen (asset_id, product_key) VALUES %s",
                    list(seen_pairs),
                    page_size=5000,
                )
                raw.execute("CREATE INDEX ON _dsu_seen (asset_id, product_key)")
            raw.execute(
                """
                UPDATE device_software_update dsu
                SET status = 'Updated',
                    remediation_note = 'Cleared by Defender re-assessment: no update flagged',
                    updated_at = NOW()
                WHERE dsu.asset_id = ANY(%s)
                  AND dsu.status = 'Open'
                  AND NOT EXISTS (
                      SELECT 1 FROM _dsu_seen s
                      WHERE s.asset_id = dsu.asset_id
                        AND s.product_key = dsu.product_key
                  )
                """,
                (defender_asset_ids,)
            )
            cleared = raw.rowcount
            if cleared:
                logger.info(f'Software-update sync: auto-closed {cleared} updates no longer flagged by Defender')

        logger.info(f'Software-update sync: wrote {written} device-software-update rows '
                    f'across {len(set(p[0] for p in seen_pairs))} assets')
        return written, None
    except Exception as e:
        logger.error(f'Software-update sync failed (non-fatal): {e}', exc_info=True)
        return 0, str(e)


def sync_defender_vulnerabilities():
    """
    Pull vulnerabilities from Defender API into vulnerability_cache
    and device_vulnerability tables. Returns (vuln_count, device_count, error).
    """
    try:
        from defender_service import DefenderService
        svc = DefenderService()

        con = _get_db()

        # Reversible kill-switch for the orphaned Eval-Lab synthetic CVEs.
        _drop_synth = _drop_synthetic_enabled(con)
        synthetic_cat_dropped = 0
        synthetic_dev_dropped = 0

        # Sync CVE catalogue
        vulns = svc.get_vulnerabilities()
        vuln_count = 0
        for v in vulns:
            cve_id = v.get('id') or v.get('cveId', '')
            if not cve_id:
                continue
            if _drop_synth and _is_synthetic_eval_cve(cve_id, v.get('description', '')):
                synthetic_cat_dropped += 1
                continue
            con.execute(
                """INSERT INTO vulnerability_cache
                   (cve_id, name, severity, cvss, description, exposed_machines, published_on, synced_at)
                   VALUES (?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(cve_id) DO UPDATE SET
                     name=excluded.name, severity=excluded.severity, cvss=excluded.cvss,
                     exposed_machines=excluded.exposed_machines, synced_at=excluded.synced_at""",
                (cve_id, v.get('name',''), v.get('severity','Unknown'),
                 v.get('cvssV3', 0) or 0, v.get('description',''),
                 v.get('exposedMachines', 0) or 0, v.get('publishedOn',''))
            )
            vuln_count += 1

        # Build machine_id → asset_id map from Defender machines list, using the
        # shared match-ladder helper (kept in lockstep with the reconciliation
        # page so its waterfall reproduces this exactly).
        machines  = svc.get_machines()
        machine_asset_map, _unmapped = build_machine_asset_map(con, machines)
        logger.info(f'Defender sync: matched {len(machine_asset_map)}/{len(machines)} Defender machines to tracked assets')

        # ── Software-presence validation (shared helper) ──────────────────────
        _software_present = build_software_present_fn(con)
        # ─────────────────────────────────────────────────────────────────────

        # Bulk-fetch ALL machine-CVE pairs in one API call (much faster than per-machine calls)
        all_machine_vulns = svc.get_all_machine_vulnerabilities()
        import datetime as _dt
        # tz-aware UTC (belt-and-suspenders). The seen-set below is the real
        # guard against the close-by-absence race; sync_start is no longer used
        # to decide what to close.
        sync_start = _dt.datetime.now(_dt.timezone.utc)
        dev_count = 0
        filtered_count = 0
        unmapped_count = 0          # Defender machine-CVE pairs with no mapped asset
        # (asset_id, cve_id) pairs we actually inserted/confirmed THIS run. Only
        # findings absent from this set may be closed by close-by-absence.
        seen_pairs: set = set()
        for mv in all_machine_vulns:
            machine_id = mv.get('machineId', '')
            asset_id   = machine_asset_map.get(machine_id)
            if not asset_id:
                unmapped_count += 1
                continue  # skip machines we can't map to a tracked asset
            cve_id = mv.get('cveId', '')
            if not cve_id:
                continue
            # Machine-pairs feed has no description → match on id prefix.
            if _drop_synth and _is_synthetic_eval_cve(cve_id):
                synthetic_dev_dropped += 1
                continue
            product_name = mv.get('productName', '')
            if not _software_present(asset_id, product_name):
                filtered_count += 1
                continue  # software not found on device — skip false positive
            con.execute(
                """INSERT INTO device_vulnerability
                   (asset_id, agent_id, cve_id, severity, cvss, product_name, status, synced_at)
                   VALUES (?,?,?,?,?,?,'Open',datetime('now'))
                   ON CONFLICT(asset_id, cve_id) DO UPDATE SET
                     severity=excluded.severity, cvss=excluded.cvss,
                     product_name=excluded.product_name, synced_at=excluded.synced_at,
                     -- Defender is authoritative: if it STILL reports this CVE the device is
                     -- still vulnerable, so re-open it even if RMM/auto-close marked it
                     -- 'Remediated' (that closure was never confirmed). Only a deliberate
                     -- human 'Accepted' risk-acceptance stays sticky.
                     status=CASE WHEN device_vulnerability.status = 'Accepted'
                                 THEN 'Accepted' ELSE 'Open' END,
                     remediation_note=CASE WHEN device_vulnerability.status = 'Remediated'
                                 THEN 'Re-opened: Defender still flags this CVE — prior closure was not confirmed'
                                 ELSE device_vulnerability.remediation_note END""",
                (asset_id, machine_id, cve_id,
                 mv.get('severity', 'Unknown'), mv.get('cvssV3', 0) or 0,
                 product_name)
            )
            seen_pairs.add((asset_id, cve_id))
            dev_count += 1

        if _drop_synth and (synthetic_cat_dropped or synthetic_dev_dropped):
            logger.info(
                f'Defender sync: dropped synthetic Evaluation-Lab CVEs — '
                f'{synthetic_cat_dropped} catalogue / {synthetic_dev_dropped} device-pairs '
                f'(reversible via Setting defender_drop_synthetic_eval_lab)')
        if filtered_count:
            logger.info(f'Defender sync: filtered {filtered_count} CVEs where software not found in device inventory')
        if unmapped_count:
            logger.warning(
                f'Defender sync: skipped {unmapped_count} Defender machine-CVE pairs '
                f'with no mapped tracked asset (silent coverage gap — reconciliation needed)'
            )

        # Close-by-absence: if Defender no longer reports a (asset, CVE) pair for
        # an asset it monitors, the vulnerability has been resolved — mark it
        # Remediated. We compare against the EXACT set of pairs Defender confirmed
        # THIS run (seen_pairs), NOT a synced_at/sync_start timestamp window — the
        # timestamp approach falsely closed every freshly-inserted row because a
        # naive-UTC sync_start was interpreted in the session TZ and shifted into
        # the future. A row is only closed when it is genuinely no longer reported.
        # A human 'Accepted' risk-acceptance is never touched (sticky).
        defender_asset_ids = list(set(machine_asset_map.values()))
        if defender_asset_ids:
            import psycopg2.extras as _pg_extras
            raw = con.cursor()._c  # raw psycopg2 cursor (RealDictCursor)
            # Stage the confirmed pairs in a TEMP TABLE (dropped on commit). This
            # keeps the anti-join index-friendly and avoids a ~94k-element IN-list.
            raw.execute(
                "CREATE TEMP TABLE _defender_seen "
                "(asset_id BIGINT, cve_id TEXT) ON COMMIT DROP"
            )
            if seen_pairs:
                _pg_extras.execute_values(
                    raw,
                    "INSERT INTO _defender_seen (asset_id, cve_id) VALUES %s",
                    list(seen_pairs),
                    page_size=5000,
                )
                raw.execute(
                    "CREATE INDEX ON _defender_seen (asset_id, cve_id)"
                )
            # Close only Open/Exception findings on Defender-monitored assets that
            # are NOT in the confirmed set this run.
            raw.execute(
                """
                UPDATE device_vulnerability dv
                SET status = 'Remediated',
                    remediation_note = 'Cleared by Defender re-assessment: no longer flagged as vulnerable',
                    updated_at = NOW()
                WHERE dv.asset_id = ANY(%s)
                  AND dv.status IN ('Open', 'Exception')
                  AND NOT EXISTS (
                      SELECT 1 FROM _defender_seen s
                      WHERE s.asset_id = dv.asset_id
                        AND s.cve_id   = dv.cve_id
                  )
                """,
                (defender_asset_ids,)
            )
            cleared = raw.rowcount
            if cleared:
                logger.info(f'Defender sync: auto-closed {cleared} CVEs no longer reported by Defender')

        # ── Closed-loop: auto-resolve CVE alert tickets whose CVE now has ZERO
        # open exposure. MUST run here — AFTER close-by-absence above — so the
        # device_vulnerability table reflects Defender's current truth before we
        # gate on it. Strictly gated (0 Open rows for the CVE), idempotent, and
        # only touches source='alert' tickets. Best-effort: never fail the sync.
        try:
            cve_closed = _resolve_remediated_cve_alerts(con)
            if cve_closed:
                logger.info(f'Defender sync: CVE remediation loop auto-resolved '
                            f'{cve_closed} alert ticket(s) (CVE remediated everywhere)')
        except Exception as e:
            logger.error(f'CVE remediation loop failed (non-fatal): {e}', exc_info=True)

        # ── 3rd-party software-update recommendations (Chrome/Zoom/Acrobat →
        # newer version). Reuses the SAME Defender service + machine->asset map
        # so the headline "N apps have updates available" maps to the right
        # asset. Best-effort: a failure here never fails the CVE sync. ──
        try:
            sw_written, sw_err = sync_defender_software_updates(con, svc, machine_asset_map)
            if sw_err:
                logger.warning(f'Defender sync: software-update step error (non-fatal): {sw_err}')
        except Exception as e:
            logger.error(f'Defender sync: software-update step crashed (non-fatal): {e}', exc_info=True)

        # ── Persist the reconciliation snapshot from THIS pull (machines +
        # all_machine_vulns are already in hand, so the waterfall math is nearly
        # free here). The page reads this DB-backed snapshot instead of pulling
        # Defender live on every load. Best-effort: never fails the sync. ──
        compute_and_store_reconciliation_snapshot(
            con, machines, all_machine_vulns, sync_start
        )

        con.commit()
        con.close()
        return vuln_count, dev_count, None

    except Exception as e:
        logger.error(f'Defender sync failed: {e}', exc_info=True)
        return 0, 0, str(e)
