"""Script-library settings API for the settings blueprint (list/save/upload/
delete/test/generate). Split from blueprints/settings.py."""
import base64
import json
import os
import re
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from secret_store import encrypt_secret, encrypt_if_secret, decrypt_secret
try:
    import ai_engine as _ai_engine
except ImportError:
    _ai_engine = None

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
from license_service import license_service
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, _get_or_create_site_enrollment_token, _dt_iso,
)
logger = logging.getLogger(__name__)


from blueprints.settings import bp


def _normalize_script_file_type(raw: str) -> str:
    ft = (raw or '').strip().lower()
    if not ft:
        raise ValueError('file_type is required')
    if not ft.startswith('.'):
        ft = f'.{ft}'
    if ft not in _SCRIPT_FILE_TYPES:
        raise ValueError('file_type must be one of: .ps1, .bat')
    return ft


def _ensure_rmm_script_library_table():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS rmm_script_library (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            file_type TEXT NOT NULL,
            shell TEXT NOT NULL,
            script_content TEXT NOT NULL,
            is_tested BOOLEAN NOT NULL DEFAULT false,
            last_tested_at TIMESTAMPTZ,
            last_tested_agent_id TEXT,
            last_test_result TEXT,
            created_by_user_id INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active BOOLEAN NOT NULL DEFAULT true
        )
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_rmm_script_library_active
        ON rmm_script_library (is_active, is_tested, name)
    """))
    db.session.commit()


def _send_script_to_agent(agent_id: str, shell: str, code: str, timeout_s: int, reason: str):
    import json as _json, urllib.request as _req, urllib.error as _err
    agent_row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = true LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    asset_id = agent_row[0] if agent_row else None

    res = db.session.execute(
        text("""INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at)
                VALUES (:aid, :uid, :reason, NOW()) RETURNING id"""),
        {'aid': asset_id, 'uid': current_user.id if hasattr(current_user, 'id') else None, 'reason': reason}
    )
    db.session.commit()
    session_id = int(res.scalar() or 0)

    payload = _json.dumps({
        'type': 'run_script',
        'shell': shell,
        'code': code,
        'timeout': int(timeout_s),
        'session_id': session_id,
    }).encode()
    try:
        req = _req.Request(
            f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return False, session_id, result.get('error', 'Gateway error')
    except _err.HTTPError as e:
        try:
            body = _json.loads(e.read())
            msg = body.get('error') or body.get('detail') or str(e)
        except Exception:
            msg = str(e)
        return False, session_id, msg
    except Exception as e:
        return False, session_id, str(e)
    return True, session_id, None


def _wait_for_script_result(session_id: int, timeout_s: int):
    import json as _json
    deadline = _time.time() + max(5, int(timeout_s) + 15)
    while _time.time() < deadline:
        row = db.session.execute(
            text("""SELECT data_json
                    FROM rmm_event
                    WHERE session_id = :sid
                      AND actor_type = 'agent'
                      AND event_type = 'script_result'
                    ORDER BY id DESC
                    LIMIT 1"""),
            {'sid': session_id}
        ).fetchone()
        if row:
            try:
                return _json.loads(row[0] or '{}')
            except Exception:
                return {'stdout': '', 'stderr': 'Invalid result payload', 'exit_code': 1}
        _time.sleep(1.0)
    return None


@bp.route('/api/settings/scripts', methods=['GET'])
@login_required
@admin_required
def api_settings_scripts_list():
    _ensure_rmm_script_library_table()
    rows = db.session.execute(text("""
        SELECT id, name, description, file_type, shell, script_content,
               is_tested, last_tested_at, last_tested_agent_id,
               created_at, updated_at
        FROM rmm_script_library
        WHERE is_active = true
        ORDER BY name ASC, id DESC
    """)).mappings().fetchall()
    scripts = []
    for r in rows:
        item = dict(r)
        item['last_tested_at'] = _dt_iso(item.get('last_tested_at'))
        item['created_at'] = _dt_iso(item.get('created_at'))
        item['updated_at'] = _dt_iso(item.get('updated_at'))
        scripts.append(item)
    return jsonify(ok=True, scripts=scripts)


@bp.route('/api/settings/scripts', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_save():
    _ensure_rmm_script_library_table()
    data = request.get_json(force=True) or {}
    script_id = data.get('id')
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    script_content = (data.get('script_content') or '').strip()
    try:
        file_type = _normalize_script_file_type(data.get('file_type'))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    if not name:
        return jsonify(ok=False, error='name is required'), 400
    if not script_content:
        return jsonify(ok=False, error='script_content is required'), 400

    shell = _SCRIPT_FILE_TYPES[file_type]
    if script_id:
        row = db.session.execute(
            text("SELECT script_content, file_type FROM rmm_script_library WHERE id = :id AND is_active = true"),
            {'id': int(script_id)}
        ).fetchone()
        if not row:
            return jsonify(ok=False, error='script not found'), 404
        content_changed = (row[0] or '') != script_content or (row[1] or '') != file_type
        db.session.execute(text("""
            UPDATE rmm_script_library
            SET name = :name,
                description = :description,
                file_type = :file_type,
                shell = :shell,
                script_content = :script_content,
                is_tested = CASE WHEN :changed THEN false ELSE is_tested END,
                updated_at = NOW()
            WHERE id = :id
        """), {
            'id': int(script_id),
            'name': name,
            'description': description,
            'file_type': file_type,
            'shell': shell,
            'script_content': script_content,
            'changed': bool(content_changed),
        })
        db.session.commit()
        return jsonify(ok=True, id=int(script_id), message='Script updated')

    row = db.session.execute(text("""
        INSERT INTO rmm_script_library
            (name, description, file_type, shell, script_content,
             created_by_user_id, created_at, updated_at, is_active)
        VALUES
            (:name, :description, :file_type, :shell, :script_content,
             :uid, NOW(), NOW(), true)
        RETURNING id
    """), {
        'name': name,
        'description': description,
        'file_type': file_type,
        'shell': shell,
        'script_content': script_content,
        'uid': current_user.id if hasattr(current_user, 'id') else None,
    }).fetchone()
    db.session.commit()
    return jsonify(ok=True, id=int(row[0]), message='Script saved')


@bp.route('/api/settings/scripts/upload', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_upload():
    _ensure_rmm_script_library_table()
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(ok=False, error='file is required'), 400

    filename = secure_filename(f.filename)
    if '.' not in filename:
        return jsonify(ok=False, error='uploaded file must have an extension'), 400
    ext = f".{filename.rsplit('.', 1)[1].lower()}"
    try:
        file_type = _normalize_script_file_type(ext)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    raw = f.read()
    if len(raw) > 1024 * 1024:
        return jsonify(ok=False, error='script file too large (max 1 MB)'), 400
    try:
        script_content = raw.decode('utf-8')
    except UnicodeDecodeError:
        script_content = raw.decode('latin-1')

    script_content = script_content.replace('\r\n', '\n').strip()
    if not script_content:
        return jsonify(ok=False, error='script file is empty'), 400

    name = (request.form.get('name') or os.path.splitext(filename)[0]).strip()
    description = (request.form.get('description') or '').strip()
    shell = _SCRIPT_FILE_TYPES[file_type]

    row = db.session.execute(text("""
        INSERT INTO rmm_script_library
            (name, description, file_type, shell, script_content,
             created_by_user_id, created_at, updated_at, is_active)
        VALUES
            (:name, :description, :file_type, :shell, :script_content,
             :uid, NOW(), NOW(), true)
        RETURNING id
    """), {
        'name': name,
        'description': description,
        'file_type': file_type,
        'shell': shell,
        'script_content': script_content,
        'uid': current_user.id if hasattr(current_user, 'id') else None,
    }).fetchone()
    db.session.commit()
    return jsonify(ok=True, id=int(row[0]), message='Script uploaded')


@bp.route('/api/settings/scripts/<int:script_id>', methods=['DELETE'])
@login_required
@admin_required
def api_settings_scripts_delete(script_id):
    _ensure_rmm_script_library_table()
    db.session.execute(text("""
        UPDATE rmm_script_library
        SET is_active = false, updated_at = NOW()
        WHERE id = :id
    """), {'id': script_id})
    db.session.commit()
    return jsonify(ok=True)


@bp.route('/api/settings/scripts/<int:script_id>/test', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_test(script_id):
    _ensure_rmm_script_library_table()
    data = request.get_json(force=True) or {}
    agent_id = (data.get('agent_id') or '').strip()
    timeout_s = int(data.get('timeout', 90) or 90)
    if not agent_id:
        return jsonify(ok=False, error='agent_id is required'), 400

    script = db.session.execute(text("""
        SELECT id, name, file_type, shell, script_content
        FROM rmm_script_library
        WHERE id = :id AND is_active = true
    """), {'id': script_id}).mappings().fetchone()
    if not script:
        return jsonify(ok=False, error='script not found'), 404

    online_row = db.session.execute(text("""
        SELECT 1
        FROM rmm_agent
        WHERE agent_id = :aid
          AND enabled = true
          AND last_seen_at > NOW() - INTERVAL '5 minutes'
    """), {'aid': agent_id}).fetchone()
    if not online_row:
        return jsonify(ok=False, error='selected agent is offline'), 400

    ok, session_id, err = _send_script_to_agent(
        agent_id=agent_id,
        shell=script['shell'],
        code=script['script_content'],
        timeout_s=timeout_s,
        reason=f"Test script: {script['name']}",
    )
    if not ok:
        return jsonify(ok=False, error=err or 'failed to dispatch script'), 502

    result = _wait_for_script_result(session_id, timeout_s)
    if result is None:
        db.session.execute(text("""
            UPDATE rmm_script_library
            SET is_tested = false,
                last_tested_at = NOW(),
                last_tested_agent_id = :aid,
                last_test_result = :res,
                updated_at = NOW()
            WHERE id = :id
        """), {
            'id': script_id,
            'aid': agent_id,
            'res': 'Timed out waiting for script_result',
        })
        db.session.commit()
        return jsonify(ok=False, error='Timed out waiting for script result'), 504

    exit_code = int(result.get('exit_code', 1) or 1)
    stdout = (result.get('stdout') or '').strip()
    stderr = (result.get('stderr') or '').strip()
    # PowerShell serializes its warning/verbose streams to stderr as CLIXML even when a
    # script succeeds (exit 0). That is NOT a failure — pass/fail is decided by exit_code.
    # Label such output as warnings so a clean run doesn't look broken.
    if stderr.startswith('#< CLIXML') or '<Objs ' in stderr[:200]:
        stderr = '(PowerShell warning/verbose stream — not an error)'
    summary = (stdout[:1000] + ('\n...' if len(stdout) > 1000 else '')).strip()
    if stderr:
        label = 'STDERR' if exit_code != 0 else 'Warnings'
        summary = (summary + f'\n{label}:\n' + stderr[:1000]).strip()

    db.session.execute(text("""
        UPDATE rmm_script_library
        SET is_tested = :tested,
            last_tested_at = NOW(),
            last_tested_agent_id = :aid,
            last_test_result = :res,
            updated_at = NOW()
        WHERE id = :id
    """), {
        'id': script_id,
        'tested': exit_code == 0,
        'aid': agent_id,
        'res': summary,
    })
    db.session.commit()

    return jsonify(
        ok=(exit_code == 0),
        session_id=session_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        tested=(exit_code == 0),
    )


@bp.route('/api/settings/scripts/generate', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_generate():
    data = request.get_json(force=True) or {}
    prompt = (data.get('prompt') or '').strip()
    try:
        file_type = _normalize_script_file_type(data.get('file_type'))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    if not prompt:
        return jsonify(ok=False, error='prompt is required'), 400

    script_language = {
        '.ps1': 'PowerShell',
        '.bat': 'Windows Batch (.bat)',
        '.sh': 'POSIX shell (.sh)',
    }[file_type]

    try:
        raw = _ai_engine._openai_chat(
            [
                {
                    'role': 'system',
                    'content': (
                        f'Generate only {script_language} code. '
                        'Return code only with no markdown fences and no extra explanation.'
                    ),
                },
                {
                    'role': 'user',
                    'content': prompt,
                },
            ],
            max_tokens=1200,
        )
        code = (raw or '').strip()
        if code.startswith('```'):
            code = code.strip('`')
            nl = code.find('\n')
            if nl != -1:
                code = code[nl + 1:]
            if code.endswith('```'):
                code = code[:-3]
            code = code.strip()
        return jsonify(ok=True, script_content=code)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


