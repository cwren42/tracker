import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
import workflow_engine as _wf_engine
try:
    import ai_engine as _ai_engine
except ImportError:
    _ai_engine = None
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
)
logger = logging.getLogger(__name__)

def get_db():
    from pg_db import pg_connect
    return pg_connect()


bp = Blueprint('ai', __name__)


# ── Asset check-out / check-in ──────────────────────────────────────────────



# ── Restored: /api/ai/test ──


# ════════════════════════════════════════════════════════════════════════════════
# REPORT ROUTES
# ════════════════════════════════════════════════════════════════════════════════



# ── Error handlers ────────────────────────────────────────────────────────────

@bp.app_errorhandler(404)
def page_not_found(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'error': 'Not found'}), 404
    return render_template('errors/404.html'), 404


@bp.app_errorhandler(500)
def internal_server_error(e):
    db.session.rollback()
    current_app.logger.error(f'500 error on {request.path}: {e}')
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('errors/500.html', error=str(e)), 500


@bp.app_errorhandler(403)
def forbidden(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'error': 'Forbidden'}), 403
    return render_template('errors/403.html'), 403


# ── AI Cross-Module Ask ────────────────────────────────────────────────────────


# ════════════════════════════════════════════════════════════════════════════════
# AI ROUTES
# ════════════════════════════════════════════════════════════════════════════════



@bp.route('/api/ai/test', methods=['POST'])
@login_required
def api_ai_test():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    try:
        import openai as _oai, ai_config
        if not ai_config.ready():
            return jsonify({'ok': False, 'error': 'No provider configured. Set an OpenAI key, or point AI base URL at Ollama, and Save first.'}), 400
        base, key, model = ai_config.base_url(), ai_config.api_key(), ai_config.chat_model()
        client = _oai.OpenAI(api_key=key, base_url=base)
        resp   = client.chat.completions.create(
            model=model,
            messages=[{'role':'user', 'content':'Reply with just the word OK.'}],
            max_tokens=5
        )
        reply = resp.choices[0].message.content.strip()
        return jsonify({'ok': True, 'model': model, 'provider': ('Ollama' if ai_config.is_ollama() else 'OpenAI'), 'reply': reply})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/ai/ask', methods=['POST'])
@login_required
@admin_required
def api_ai_ask():
    """Cross-module AI question answering."""
    data = request.get_json(force=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question required'}), 400
    try:
        result = _ai_engine.ask_ai(question)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(f'AI ask error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ai/daily-briefing', methods=['GET'])
@login_required
@admin_required
def api_ai_daily_briefing_get():
    """Return the cached 'what needs attention today' briefing, if any."""
    from models import Setting
    row = Setting.query.filter_by(key='ai_daily_briefing').first()
    if not row or not row.value:
        return jsonify({'answer': None, 'generated_at': None})
    try:
        return jsonify(json.loads(row.value))
    except Exception:
        return jsonify({'answer': None, 'generated_at': None})


@bp.route('/api/ai/daily-briefing/generate', methods=['POST'])
@login_required
@admin_required
def api_ai_daily_briefing_generate():
    """Generate a fresh daily briefing from live ops data and cache it."""
    from models import Setting
    try:
        result = _ai_engine.generate_daily_briefing()
        payload = {
            'answer': result.get('answer'),
            'sources': result.get('sources', []),
            'generated_at': datetime.utcnow().isoformat() + 'Z',
        }
        row = Setting.query.filter_by(key='ai_daily_briefing').first()
        if not row:
            row = Setting(key='ai_daily_briefing')
            db.session.add(row)
        row.value = json.dumps(payload)
        db.session.commit()
        return jsonify(payload)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(f'AI daily-briefing error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ai/predict-failures', methods=['GET'])
@login_required
@admin_required
def api_ai_predict_failures():
    """Return risk-scored at-risk assets using AI predictive analysis."""
    try:
        result = _ai_engine.predict_asset_failures()
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f'AI predict-failures error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ai/triage-ticket/<int:ticket_id>', methods=['POST'])
@login_required
@admin_required
def api_ai_triage_ticket(ticket_id):
    """Auto-triage a ticket: suggest priority and category."""
    try:
        result = _ai_engine.auto_triage_ticket(ticket_id)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f'AI triage error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ai/asset-health/<int:asset_id>', methods=['POST'])
@login_required
def api_ai_asset_health(asset_id):
    """Run an AI health analysis for a single asset."""
    if not _ai_engine:
        return jsonify({'error': 'AI engine unavailable'}), 503
    try:
        result = _ai_engine.analyze_asset_health(asset_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        current_app.logger.error(f'AI asset-health error for {asset_id}: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/workflows')
@login_required
def workflows():
    if current_user.role != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard.index'))
    return render_template('workflows.html')


@bp.route('/api/workflows', methods=['GET'])
@login_required
def api_workflows_list():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, description, trigger_type, enabled, created_by, created_at FROM workflow_definitions ORDER BY id DESC"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/workflows', methods=['POST'])
@login_required
def api_workflow_create():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    data = request.get_json()
    if not data.get('name') or not data.get('trigger_type'):
        return jsonify({'error': 'name and trigger_type required'}), 400
    db_conn = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cur = db_conn.execute(
        """INSERT INTO workflow_definitions
           (name, description, trigger_type, trigger_config, nodes, edges, enabled, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (data['name'], data.get('description', ''), data['trigger_type'],
         json.dumps(data.get('trigger_config', {})),
         json.dumps(data.get('nodes', [])),
         json.dumps(data.get('edges', [])),
         bool(data.get('enabled', True)),
         current_user.username, now, now)
    )
    wf_id = cur.lastrowid
    db_conn.commit(); db_conn.close()
    return jsonify({'id': wf_id, 'ok': True})


@bp.route('/api/workflows/<int:wf_id>', methods=['GET'])
@login_required
def api_workflow_get(wf_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM workflow_definitions WHERE id=?", (wf_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    r = dict(row)
    r['nodes']          = json.loads(r['nodes'] or '[]')
    r['edges']          = json.loads(r['edges'] or '[]')
    r['trigger_config'] = json.loads(r['trigger_config'] or '{}')
    return jsonify(r)


@bp.route('/api/workflows/<int:wf_id>', methods=['PUT'])
@login_required
def api_workflow_update(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    data    = request.get_json()
    now     = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db_conn = get_db()
    db_conn.execute(
        """UPDATE workflow_definitions
           SET name=?, description=?, trigger_type=?, trigger_config=?,
               nodes=?, edges=?, enabled=?, updated_at=?
           WHERE id=?""",
        (data.get('name'), data.get('description', ''), data.get('trigger_type'),
         json.dumps(data.get('trigger_config', {})),
         json.dumps(data.get('nodes', [])),
         json.dumps(data.get('edges', [])),
         bool(data.get('enabled', True)),
         now, wf_id)
    )
    db_conn.commit(); db_conn.close()
    return jsonify({'ok': True})


@bp.route('/api/workflows/<int:wf_id>', methods=['DELETE'])
@login_required
def api_workflow_delete(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    db_conn = get_db()
    db_conn.execute("DELETE FROM workflow_definitions WHERE id=?", (wf_id,))
    db_conn.commit(); db_conn.close()
    return jsonify({'ok': True})


@bp.route('/api/workflows/<int:wf_id>/toggle', methods=['POST'])
@login_required
def api_workflow_toggle(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    db_conn = get_db()
    row = db_conn.execute("SELECT enabled FROM workflow_definitions WHERE id=?", (wf_id,)).fetchone()
    if not row:
        db_conn.close()
        return jsonify({'error': 'Not found'}), 404
    new_val = not bool(row['enabled'])
    db_conn.execute("UPDATE workflow_definitions SET enabled=? WHERE id=?", (new_val, wf_id))
    db_conn.commit(); db_conn.close()
    return jsonify({'enabled': new_val})


@bp.route('/api/workflows/<int:wf_id>/run', methods=['POST'])
@login_required
def api_workflow_run(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    ctx = request.get_json() or {}
    ctx['triggered_by'] = current_user.username
    try:
        run_id = _wf_engine.execute_workflow(wf_id, ctx)
        return jsonify({'ok': True, 'run_id': run_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/workflows/<int:wf_id>/runs', methods=['GET'])
@login_required
@admin_required
def api_workflow_runs(wf_id):
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, status, started_at, completed_at, error FROM workflow_runs WHERE workflow_id=? ORDER BY id DESC LIMIT 50",
        (wf_id,)
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/workflows/ai-generate', methods=['POST'])
@login_required
@admin_required
def api_workflow_ai_generate():
    data   = request.get_json(force=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'ok': False, 'error': 'prompt required'}), 400
    result = _ai_engine.generate_workflow(prompt)
    return jsonify(result)


@bp.route('/api/workflows/runs/<int:run_id>/steps', methods=['GET'])
@login_required
@admin_required
def api_workflow_run_steps(run_id):
    db_conn = get_db()
    steps = db_conn.execute(
        "SELECT * FROM workflow_run_steps WHERE run_id=? ORDER BY id", (run_id,)
    ).fetchall()
    db_conn.close()
    result = []
    for s in steps:
        r = dict(s)
        r['output_data'] = json.loads(r.get('output_data') or '{}')
        result.append(r)
    return jsonify(result)


@bp.route('/api/ai/ticket/<int:ticket_id>/suggest', methods=['POST'])
@login_required
@admin_required
def api_ai_ticket_suggest(ticket_id):
    try:
        result = _ai_engine.suggest_ticket_resolution(ticket_id)
        if result.get('suggestion'):
            try:
                result['parsed'] = json.loads(result['suggestion'])
            except Exception:
                result['parsed'] = {'diagnosis': result['suggestion']}
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ai/suggestions/<int:sug_id>/apply', methods=['POST'])
@login_required
@admin_required
def api_ai_suggestion_apply(sug_id):
    try:
        _ai_engine.apply_ticket_suggestion(sug_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/ai/suggestions/<int:sug_id>/dismiss', methods=['POST'])
@login_required
@admin_required
def api_ai_suggestion_dismiss(sug_id):
    _ai_engine.dismiss_ticket_suggestion(sug_id)
    return jsonify({'ok': True})


@bp.route('/api/ai/ticket/<int:ticket_id>/suggestions', methods=['GET'])
@login_required
@admin_required
def api_ai_ticket_suggestions(ticket_id):
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT * FROM ai_ticket_suggestions WHERE ticket_id=? ORDER BY id DESC LIMIT 10", (ticket_id,)
    ).fetchall()
    db_conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d['parsed'] = json.loads(d['suggestion'])
        except Exception:
            d['parsed'] = {'diagnosis': d['suggestion']}
        result.append(d)
    return jsonify(result)


@bp.route('/api/ai/security-summary', methods=['GET'])
@login_required
def api_ai_security_summary_get():
    summary = _ai_engine.get_latest_security_summary()
    return jsonify(summary or {})


@bp.route('/api/ai/security-summary/generate', methods=['POST'])
@login_required
def api_ai_security_summary_generate():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    try:
        result = _ai_engine.generate_security_summary()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/ai/settings', methods=['GET'])
@login_required
def api_ai_settings_get():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    db_conn = get_db()
    keys = ['openai_api_key', 'openai_model', 'ai_base_url', 'openai_embed_model',
            'ai_ticket_enabled', 'ai_ticket_auto_mode', 'ai_security_monitor_enabled']
    result = {}
    for key in keys:
        row = db_conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        val = row['value'] if row else ''
        if key == 'openai_api_key' and val:
            from secret_store import decrypt_secret
            val = decrypt_secret(val)
            val = val[:8] + '…' + val[-4:]
        result[key] = val
    db_conn.close()
    return jsonify(result)


@bp.route('/api/ai/settings', methods=['POST'])
@login_required
def api_ai_settings_save():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    data    = request.get_json()
    db_conn = get_db()
    allowed = ['openai_api_key', 'openai_model', 'ai_base_url', 'openai_embed_model',
               'ai_ticket_enabled', 'ai_ticket_auto_mode', 'ai_security_monitor_enabled']
    for key in allowed:
        if key in data:
            if key == 'openai_api_key' and '…' in str(data[key]):
                continue
            from secret_store import encrypt_if_secret
            _val = encrypt_if_secret(key, data[key])
            existing = db_conn.execute("SELECT id FROM setting WHERE key=?", (key,)).fetchone()
            if existing:
                db_conn.execute("UPDATE setting SET value=? WHERE key=?", (_val, key))
            else:
                db_conn.execute("INSERT INTO setting (key, value) VALUES (?,?)", (key, _val))
    db_conn.commit(); db_conn.close()
    return jsonify({'ok': True})