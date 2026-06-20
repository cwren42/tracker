"""Proactive AI Remediation — in-app incident feed (Phase 1 / MVP).

Admin-gated. The feed lists open agent_incident rows with the AI diagnosis,
confidence, severity, age, and the templated fix-option BUTTONS. Acting on an
incident reuses the EXISTING remediation path (rmm_remediation_queue via the
gateway) for 'run' actions, the normal support_ticket columns for 'ticket', and
flips status to 'dismissed' for 'dismiss'.

Routes:
    GET  /incidents            -> the feed page
    POST /incidents/<id>/act   -> act on an incident {action_key}
    POST /incidents/scan       -> manual "Scan now"
    GET  /incidents/badge      -> open-incident count (nav bell, JSON)
"""
import json
import logging
import urllib.request as _ur

from flask import (Blueprint, render_template, request, jsonify, redirect,
                   url_for, flash)
from flask_login import login_required, current_user

from utils import admin_required
import incident_service as _inc

logger = logging.getLogger(__name__)

bp = Blueprint('incidents', __name__)

# Statuses considered OPEN for the feed + badge (mirror incident_service).
_OPEN = ('new', 'diagnosed', 'awaiting_approval', 'remediating')


def _db():
    from pg_db import pg_connect
    return pg_connect()


def _gw():
    from app import app
    return app.config.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')


# ─────────────────────────────────────────────────────────────
#  Feed page
# ─────────────────────────────────────────────────────────────
@bp.route('/incidents')
@login_required
@admin_required
def feed():
    con = _db()
    try:
        rows = con.execute(
            """SELECT i.*, a.name AS asset_name
               FROM agent_incident i
               LEFT JOIN asset a ON a.id = i.asset_id
               WHERE i.signal_type <> 'ai_assist'
               ORDER BY
                 CASE WHEN i.status IN ('new','diagnosed','awaiting_approval','remediating')
                      THEN 0 ELSE 1 END,
                 CASE i.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                 i.created_at DESC
               LIMIT 200"""
        ).fetchall()
    finally:
        con.close()

    incidents = []
    open_count = 0
    for r in rows:
        d = dict(r)
        # proposed_actions is JSONB — psycopg2 returns it already-parsed (list),
        # but be defensive if a driver hands back text.
        pa = d.get('proposed_actions')
        if isinstance(pa, str):
            try:
                pa = json.loads(pa)
            except Exception:
                pa = []
        d['proposed_actions'] = pa or []
        d['is_open'] = d['status'] in _OPEN
        if d['is_open']:
            open_count += 1
        incidents.append(d)

    signal_toggles = _signal_toggle_state()
    con = _db()
    try:
        teams_configured = bool(_inc._get_setting(con, 'teams_webhook_url', ''))
    finally:
        con.close()
    return render_template('incidents.html',
                           incidents=incidents, open_count=open_count,
                           signal_toggles=signal_toggles,
                           teams_configured=teams_configured)


# Human labels for the per-signal toggle switches (Feature 1).
_SIGNAL_LABELS = {
    'disk_low': 'Low disk space',
    'service_down': 'Service down',
    'agent_offline_but_up': 'Agent not checking in',
    'patch_failed': 'Patch install failed',
    'defender_critical': 'Critical vulnerability',
}


def _signal_toggle_state():
    """Return [{signal, label, enabled}] for the per-signal switch row."""
    con = _db()
    try:
        out = []
        for sig in _inc.SIGNAL_TYPES:
            out.append({'signal': sig,
                        'label': _SIGNAL_LABELS.get(sig, sig),
                        'enabled': _inc._signal_enabled(con, sig)})
        return out
    finally:
        con.close()


@bp.route('/incidents/signal-toggle', methods=['POST'])
@login_required
@admin_required
def signal_toggle():
    """Flip a per-signal detection switch (Feature 1). Writes the Setting
    'incident_signal_<signal>_enabled' = '1'/'0'. Detection of that signal is
    skipped on the next scan when off; re-enabling reuses the same scan."""
    data = request.get_json(silent=True) or request.form
    signal = (data.get('signal') or '').strip()
    enabled = str(data.get('enabled')).strip().lower() in ('1', 'true', 'yes', 'on')
    if signal not in _inc.SIGNAL_TYPES:
        return jsonify({'ok': False, 'error': 'unknown signal'}), 400
    con = _db()
    try:
        key = f'incident_signal_{signal}_enabled'
        val = '1' if enabled else '0'
        con.execute(
            """INSERT INTO setting (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (key, val))
        con.commit()
    finally:
        con.close()
    return jsonify({'ok': True, 'signal': signal, 'enabled': enabled})


# ─────────────────────────────────────────────────────────────
#  Badge (nav bell) — open incident count
# ─────────────────────────────────────────────────────────────
@bp.route('/incidents/badge')
@login_required
@admin_required
def badge():
    con = _db()
    try:
        r = con.execute(
            f"SELECT COUNT(*) AS c FROM agent_incident "
            f"WHERE status IN {str(_OPEN)} AND signal_type <> 'ai_assist'"
        ).fetchone()
        return jsonify({'open': r['c'] if r else 0})
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  Manual scan
# ─────────────────────────────────────────────────────────────
@bp.route('/incidents/scan', methods=['POST'])
@login_required
@admin_required
def scan_now():
    from app import app
    summary = _inc.scan(app)
    if request.is_json or request.headers.get('X-Requested-With'):
        return jsonify({'ok': True, 'summary': summary})
    flash(f"Scan complete: {summary.get('created', {})}, "
          f"verified {summary.get('verified', 0)}.", 'success')
    return redirect(url_for('incidents.feed'))


# ─────────────────────────────────────────────────────────────
#  Act on an incident
# ─────────────────────────────────────────────────────────────
@bp.route('/incidents/<int:incident_id>/act', methods=['POST'])
@login_required
@admin_required
def act(incident_id):
    action_key = request.form.get('action_key')
    if not action_key and request.is_json:
        action_key = (request.json or {}).get('action_key')
    if not action_key:
        return jsonify({'ok': False, 'error': 'missing action_key'}), 400

    con = _db()
    try:
        inc = con.execute(
            "SELECT * FROM agent_incident WHERE id=%s", (incident_id,)
        ).fetchone()
        if not inc:
            return jsonify({'ok': False, 'error': 'not found'}), 404
        if inc['status'] not in _OPEN:
            return jsonify({'ok': False,
                            'error': f"incident is {inc['status']} (not actionable)"}), 409

        actions = inc['proposed_actions']
        if isinstance(actions, str):
            actions = json.loads(actions)
        action = next((a for a in (actions or []) if a.get('key') == action_key), None)
        if not action:
            return jsonify({'ok': False, 'error': 'unknown action_key'}), 400

        uid = current_user.id if hasattr(current_user, 'id') else None
        kind = action.get('kind')

        if kind == 'dismiss':
            con.execute(
                """UPDATE agent_incident
                   SET status='dismissed', chosen_action=%s, approved_by=%s,
                       approved_at=NOW(), updated_at=NOW(),
                       verify_result='dismissed by user'
                   WHERE id=%s""", (action_key, uid, incident_id))
            con.commit()
            result = {'ok': True, 'status': 'dismissed'}

        elif kind == 'ticket':
            subject = f"[{inc['signal_type']}] {_asset_name(con, inc['asset_id'])}"
            tid = _inc._open_ticket(
                con, inc['asset_id'], subject,
                inc['signal_type'], body=inc['diagnosis_text'])
            con.execute(
                """UPDATE agent_incident
                   SET status='resolved', chosen_action=%s, approved_by=%s,
                       approved_at=NOW(), resolved_at=NOW(), updated_at=NOW(),
                       verify_result=%s
                   WHERE id=%s""",
                (action_key, uid,
                 f'ticket #{tid} opened' if tid else 'ticket create failed',
                 incident_id))
            con.commit()
            result = {'ok': True, 'status': 'resolved', 'ticket_id': tid}

        elif kind == 'run':
            # Bug-3 guard (defense-in-depth): a server/critical asset must NEVER
            # run an automated change, even via a crafted POST. The feed strips
            # run buttons for servers, but enforce it here too — the only allowed
            # actions on a server are ticket/dismiss.
            if _inc._is_server_or_critical(con, inc['asset_id']):
                return jsonify({'ok': False,
                                'error': 'server/critical asset is notify-only '
                                         '(no automated remediation)'}), 403
            payload = action.get('run_payload') or {}
            # Special case: patch retry re-enqueues the failed Windows Update job.
            if payload.get('type') == 'retry_patch_job':
                rq_id = _retry_patch_job(con, inc, payload.get('patch_job_id'), uid)
            else:
                rq_id = _inc._enqueue_action(con, inc['asset_id'],
                                             inc['agent_id'], action)
            con.execute(
                """UPDATE agent_incident
                   SET status='remediating', chosen_action=%s,
                       remediation_queue_id=COALESCE(%s, remediation_queue_id),
                       approved_by=%s, approved_at=NOW(),
                       attempt_count=attempt_count+1, updated_at=NOW()
                   WHERE id=%s""",
                (action_key, rq_id, uid, incident_id))
            con.commit()
            result = {'ok': True, 'status': 'remediating', 'queue_id': rq_id}
        else:
            return jsonify({'ok': False, 'error': 'unsupported kind'}), 400
    finally:
        con.close()

    if request.is_json or request.headers.get('X-Requested-With'):
        return jsonify(result)
    flash(f"Incident #{incident_id}: {result.get('status')}.", 'success')
    return redirect(url_for('incidents.feed'))


def _asset_name(con, asset_id):
    if not asset_id:
        return 'unknown asset'
    a = con.execute("SELECT name FROM asset WHERE id=%s", (asset_id,)).fetchone()
    return (a['name'] if a else None) or f'asset {asset_id}'


# ─────────────────────────────────────────────────────────────
#  CHAT — per-incident conversation thread (the AI Triage Chat)
# ─────────────────────────────────────────────────────────────
def _uid():
    return current_user.id if hasattr(current_user, 'id') else None


def _run_bg(fn, *args, **kwargs):
    """Run a (potentially slow) triage call on a background daemon thread so it
    never blocks the gunicorn worker. The UI polls /thread for results. The
    thread pushes its own app context; the triage fns manage their own DB conn.
    Fully fail-safe — a thread error is logged, never surfaced."""
    import threading
    from app import app

    def _target():
        with app.app_context():
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception('background triage thread failed: %s', getattr(fn, '__name__', fn))

    threading.Thread(target=_target, daemon=True).start()


@bp.route('/incidents/<int:incident_id>/chat')
@login_required
@admin_required
def chat(incident_id):
    """The conversation view for one incident. Lazily triages Tier-2 NOTIFY
    signals (defender_critical / agent_offline_but_up) on FIRST open so we don't
    auto-burn gpt-4o loops for the whole Defender list."""
    import triage_agent as _tri
    con = _db()
    try:
        inc = con.execute(
            """SELECT i.*, a.name AS asset_name
               FROM agent_incident i LEFT JOIN asset a ON a.id=i.asset_id
               WHERE i.id=%s""", (incident_id,)).fetchone()
        if not inc:
            return ("Incident not found", 404)
        inc = dict(inc)
        # Lazy triage on first open for the notify-tier signals.
        lazy = inc['signal_type'] in ('defender_critical', 'agent_offline_but_up')
        needs = (inc.get('triage_state') in (None, '') )
    finally:
        con.close()

    if lazy and needs:
        # Kick lazy triage in the BACKGROUND so the page renders instantly; the
        # client polls /thread and watches the AI work appear. Mark running now
        # so a double-open (or the poll racing) doesn't start a second loop.
        con2 = _db()
        won = False
        try:
            cur = con2.execute(
                "UPDATE agent_incident SET triage_state='running', updated_at=NOW() "
                "WHERE id=%s AND (triage_state IS NULL OR triage_state='')",
                (incident_id,))
            con2.commit()
            won = (getattr(cur, 'rowcount', 0) or 0) > 0
        finally:
            con2.close()
        if won:
            _run_bg(_tri.triage_incident, incident_id, force=True)

    con = _db()
    try:
        inc = con.execute(
            """SELECT i.*, a.name AS asset_name
               FROM agent_incident i LEFT JOIN asset a ON a.id=i.asset_id
               WHERE i.id=%s""", (incident_id,)).fetchone()
        inc = dict(inc)
        thread = _tri.get_thread(con, incident_id)
        pf = inc.get('proposed_fix')
        if isinstance(pf, str):
            try:
                pf = json.loads(pf)
            except Exception:
                pf = None
        inc['proposed_fix'] = pf
        pa = inc.get('proposed_actions')
        if isinstance(pa, str):
            try:
                pa = json.loads(pa)
            except Exception:
                pa = []
        inc['proposed_actions'] = pa or []
        inc['is_open'] = inc['status'] in _OPEN
    finally:
        con.close()
    return render_template('incident_chat.html', inc=inc, thread=thread)


@bp.route('/incidents/<int:incident_id>/thread')
@login_required
@admin_required
def thread_json(incident_id):
    """Poll endpoint: the chat thread + current proposal + status as JSON."""
    import triage_agent as _tri
    con = _db()
    try:
        inc = con.execute(
            "SELECT status, triage_state, proposed_fix FROM agent_incident WHERE id=%s",
            (incident_id,)).fetchone()
        if not inc:
            return jsonify({'ok': False, 'error': 'not found'}), 404
        thread = _tri.get_thread(con, incident_id)
        pf = inc['proposed_fix']
        if isinstance(pf, str):
            try:
                pf = json.loads(pf)
            except Exception:
                pf = None
    finally:
        con.close()
    return jsonify({'ok': True, 'thread': thread, 'status': inc['status'],
                    'triage_state': inc['triage_state'], 'proposed_fix': pf})


@bp.route('/incidents/<int:incident_id>/reply', methods=['POST'])
@login_required
@admin_required
def reply(incident_id):
    """Technician replies in the thread → continue the agentic loop."""
    import triage_agent as _tri
    text = (request.form.get('text') or
            (request.json or {}).get('text') if request.is_json else
            request.form.get('text'))
    if request.is_json and not text:
        text = (request.json or {}).get('text')
    text = (text or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'empty message'}), 400
    # Persist the tech's turn NOW (instant in the UI); run the AI loop in the bg.
    _tri.post_user_message(incident_id, text, created_by=_uid())
    _run_bg(_tri.continue_incident_chat, incident_id, text,
            created_by=_uid(), post_user=False)
    return jsonify({'ok': True, 'queued': True})


@bp.route('/incidents/<int:incident_id>/retriage', methods=['POST'])
@login_required
@admin_required
def retriage(incident_id):
    """Force a fresh triage pass (e.g. after telemetry refresh) — backgrounded."""
    import triage_agent as _tri
    _run_bg(_tri.triage_incident, incident_id, force=True)
    return jsonify({'ok': True, 'queued': True})


@bp.route('/incidents/<int:incident_id>/approve_fix', methods=['POST'])
@login_required
@admin_required
def approve_fix(incident_id):
    """Approve the AI's GATED change proposal → execute via the EXISTING
    remediation path → post the result back into the thread.

    This is the only place a triage-proposed CHANGE can run, and only on an
    explicit human click. Read-only diagnostics never come through here."""
    import triage_agent as _tri
    con = _db()
    try:
        inc = con.execute("SELECT * FROM agent_incident WHERE id=%s",
                          (incident_id,)).fetchone()
        if not inc:
            return jsonify({'ok': False, 'error': 'not found'}), 404
        inc = dict(inc)
        pf = inc.get('proposed_fix')
        if isinstance(pf, str):
            pf = json.loads(pf)
        if not pf:
            return jsonify({'ok': False, 'error': 'no proposal to approve'}), 400

        uid = _uid()
        execute = pf.get('execute')
        if execute == 'ticket':
            subject = f"[{inc['signal_type']}] {_asset_name(con, inc['asset_id'])}"
            body = (pf.get('diagnosis') or '') + "\n\nWhy this fix: " + (pf.get('why_it_works') or '')
            tid = _inc._open_ticket(con, inc['asset_id'], subject, inc['signal_type'], body=body)
            con.execute(
                """UPDATE agent_incident
                   SET status='resolved', chosen_action='ai_ticket', approved_by=%s,
                       approved_at=NOW(), resolved_at=NOW(), updated_at=NOW(),
                       verify_result=%s WHERE id=%s""",
                (uid, f'ticket #{tid} opened' if tid else 'ticket create failed', incident_id))
            con.commit()
            _tri.post_message(con, incident_id, 'system',
                              f"Approved → opened ticket #{tid}." if tid else
                              "Approved → ticket creation failed.",
                              meta={'approved_by': uid})
            return jsonify({'ok': True, 'status': 'resolved', 'ticket_id': tid})

        # Bug-3 guard (defense-in-depth): never execute a CHANGE on a
        # server/critical asset, even an AI-proposed one approved by a click.
        if _inc._is_server_or_critical(con, inc['asset_id']):
            return jsonify({'ok': False,
                            'error': 'server/critical asset is notify-only '
                                     '(no automated remediation)'}), 403

        # A CHANGE: enqueue via the existing remediation path.
        action = {'key': 'ai_proposed_fix', 'kind': 'run',
                  'risk_tier': pf.get('risk_tier', 1),
                  'run_payload': pf.get('run_payload') or {}}
        payload = action['run_payload']
        if payload.get('type') == 'retry_patch_job':
            rq_id = _retry_patch_job(con, inc, payload.get('patch_job_id'), uid)
        else:
            rq_id = _inc._enqueue_action(con, inc['asset_id'], inc['agent_id'], action)
        con.execute(
            """UPDATE agent_incident
               SET status='remediating', chosen_action='ai_proposed_fix',
                   remediation_queue_id=COALESCE(%s, remediation_queue_id),
                   approved_by=%s, approved_at=NOW(),
                   attempt_count=attempt_count+1, updated_at=NOW() WHERE id=%s""",
            (rq_id, uid, incident_id))
        con.commit()
        _tri.post_message(con, incident_id, 'system',
                          f"Approved → executing '{pf.get('fix_label')}'. "
                          f"The result will post here when the agent reports back.",
                          meta={'approved_by': uid, 'queue_id': rq_id})
        return jsonify({'ok': True, 'status': 'remediating', 'queue_id': rq_id})
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────
#  GLOBAL AI ASSIST — unscoped chat (not tied to one incident)
# ─────────────────────────────────────────────────────────────
@bp.route('/ai-assist')
@login_required
@admin_required
def assist_page():
    import triage_agent as _tri
    con = _db()
    try:
        inc = _tri.get_or_create_assist_thread(con, _uid())
        thread = _tri.get_thread(con, inc['id'])
    finally:
        con.close()
    return render_template('ai_assist.html', inc=inc, thread=thread)


@bp.route('/ai-assist/send', methods=['POST'])
@login_required
@admin_required
def assist_send():
    import triage_agent as _tri
    text = request.form.get('text') or (request.json or {}).get('text') if request.is_json else request.form.get('text')
    if request.is_json and not text:
        text = (request.json or {}).get('text')
    target = request.form.get('target_asset') or (
        (request.json or {}).get('target_asset') if request.is_json else None)
    text = (text or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'empty message'}), 400
    uid = _uid()
    # Post the user turn + bind target synchronously (instant in UI); run the loop
    # in the background so the request returns immediately.
    inc_id = _tri.assist_prepare(uid, text, target_asset=target)
    _run_bg(_tri.assist_chat, uid, text, target_asset=target, post_user=False)
    return jsonify({'ok': True, 'queued': True, 'incident_id': inc_id})


@bp.route('/ai-assist/device-search')
@login_required
@admin_required
def assist_device_search():
    """Typeahead for the chat-widget device picker. Returns up to 10 asset
    names matching ``q`` by name (prefix matches first), so the operator can
    pick a box instead of needing the exact hostname. Targeting stays optional —
    the name string is what triage_agent._resolve_asset_by_name expects."""
    q = (request.args.get('q') or '').strip()
    if len(q) < 1:
        return jsonify({'ok': True, 'results': []})
    con = _db()
    try:
        rows = con.execute(
            """SELECT a.name,
                      (ra.agent_id IS NOT NULL) AS has_agent
               FROM asset a
               LEFT JOIN rmm_agent ra ON ra.asset_id=a.id AND ra.enabled=TRUE
               WHERE a.name ILIKE %s AND a.name IS NOT NULL AND a.name <> ''
               ORDER BY (LOWER(a.name) LIKE LOWER(%s)) DESC,
                        ra.last_seen_at DESC NULLS LAST,
                        a.name ASC
               LIMIT 10""",
            (f'%{q}%', f'{q}%')).fetchall()
        seen, out = set(), []
        for r in rows:
            nm = r['name']
            if nm in seen:
                continue
            seen.add(nm)
            out.append({'name': nm, 'has_agent': bool(r['has_agent'])})
    finally:
        con.close()
    return jsonify({'ok': True, 'results': out})


@bp.route('/ai-assist/thread')
@login_required
@admin_required
def assist_thread_json():
    import triage_agent as _tri
    con = _db()
    try:
        inc = _tri.get_or_create_assist_thread(con, _uid())
        thread = _tri.get_thread(con, inc['id'])
        pf = inc.get('proposed_fix')
        if isinstance(pf, str):
            try:
                pf = json.loads(pf)
            except Exception:
                pf = None
    finally:
        con.close()
    return jsonify({'ok': True, 'incident_id': inc['id'], 'thread': thread,
                    'proposed_fix': pf})


def _retry_patch_job(con, inc, src_job_id, uid):
    """Re-enqueue a failed Windows Update job: copy its update_ids into a fresh
    rmm_patch_job and fire install_patches to the gateway (reconnect flush if
    offline). Returns a remediation marker id (the new patch job id) or None.

    Note: patch jobs live in their own queue (rmm_patch_job), not
    rmm_remediation_queue, so the FK on agent_incident.remediation_queue_id is
    left NULL for retries — the verify pass keys off rmm_patch_job status instead
    for this signal."""
    try:
        src = con.execute(
            "SELECT agent_id, update_ids, kb_ids, titles FROM rmm_patch_job WHERE id=%s",
            (src_job_id,)).fetchone()
        if not src:
            return None
        new = con.execute(
            """INSERT INTO rmm_patch_job
                 (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
               VALUES (%s,%s,%s,%s,'queued',%s,NOW())
               RETURNING id""",
            (src['agent_id'], src['update_ids'], src['kb_ids'], src['titles'], uid)
        ).fetchone()
        new_id = new['id']
        con.commit()
        # Fire to the gateway (best-effort; reconnect flush covers offline).
        try:
            uids = json.loads(src['update_ids']) if src['update_ids'] else []
            kbids = json.loads(src['kb_ids']) if src['kb_ids'] else []
            titles = json.loads(src['titles']) if src['titles'] else []
        except Exception:
            uids, kbids, titles = [], [], []
        body = json.dumps({'type': 'install_patches', 'job_id': new_id,
                           'update_ids': uids, 'kb_ids': kbids,
                           'titles': titles}).encode()
        try:
            req = _ur.Request(f"{_gw()}/send-msg/{src['agent_id']}", data=body,
                              headers={'Content-Type': 'application/json'},
                              method='POST')
            with _ur.urlopen(req, timeout=8) as resp:
                res = json.loads(resp.read())
            if res.get('ok'):
                con.execute(
                    "UPDATE rmm_patch_job SET status='deploying', deployed_at=NOW(), "
                    "updated_at=NOW() WHERE id=%s", (new_id,))
                con.commit()
        except Exception as e:
            logger.info('patch retry: gateway offline for %s, queued: %s',
                        src['agent_id'], e)
        return None  # not an rmm_remediation_queue id
    except Exception as e:
        logger.warning('retry_patch_job failed: %s', e)
        try:
            con.rollback()
        except Exception:
            pass
        return None
