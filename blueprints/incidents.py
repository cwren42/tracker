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

    return render_template('incidents.html',
                           incidents=incidents, open_count=open_count)


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
            f"SELECT COUNT(*) AS c FROM agent_incident WHERE status IN {str(_OPEN)}"
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
