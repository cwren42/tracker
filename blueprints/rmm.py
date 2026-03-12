import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
import requests
from werkzeug.utils import secure_filename

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, RMM_GATEWAY_PUBLIC, RMM_TRACKER_URL,
    _valid_agent_key, _dt_iso, _get_or_create_site_enrollment_token,
    _ensure_rmm_script_library_table,
)
logger = logging.getLogger(__name__)


bp = Blueprint('rmm', __name__)


# ==================== SSH TERMINAL ====================

from ssh_terminal_manager import get_ssh_manager


# ==================== LINUX AGENT API ====================


# ==================== RMM AGENT DOWNLOAD ====================


RMM_GATEWAY_INTERNAL = os.environ.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
RMM_GATEWAY_PUBLIC   = os.environ.get('RMM_GATEWAY_URL', 'wss://rmm.corp.cirque.com')
RMM_TRACKER_URL      = os.environ.get('RMM_TRACKER_URL', 'https://tracker.corp.cirque.com')


# ── Eagle Eyes ────────────────────────────────────────────────────────────────


import re as _re_tz


# System / lock-screen processes that should never appear in app usage stats
EAGLE_SYSTEM_PROCESSES = (
    'LockApp', 'LockScreenHost', 'ShellHost', 'ShellExperienceHost',
    'StartMenuExperienceHost', 'SearchHost', 'SearchApp', 'TextInputHost',
    'ApplicationFrameHost', 'RuntimeBroker', 'taskhostw', 'sihost',
    'ctfmon', 'fontdrvhost', 'dwm', 'winlogon', 'LogonUI',
    'conhost', 'condrv',
)
_EAGLE_SYSTEM_EXCL = " AND LOWER(process_name) NOT IN ({}) ".format(
    ','.join(f"'{p.lower()}'" for p in EAGLE_SYSTEM_PROCESSES)
)


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Live current state
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Focus Sessions (consecutive uninterrupted blocks per app)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — App Classifications
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Alert Rules
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Report Schedules
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Multi-agent comparison page + data
# ─────────────────────────────────────────────────────────────────────────────


# ==================== RMM AGENT DATA SYNC ====================


# ── Restored: /api/rmm/<agent_id>/software ──


# ── Agent installer download ─────────────────────────────────────────────────



# ── Restored: /download/agent-installer ──


# ── Restored: /download/agent-ps1 ──


# ── Restored: /download/agent-file/<path:filename> ──


# ── Restored: /rmm/agent/launcher ──


# ── Restored: /rmm/agent/repair ──


# ── Restored: /rmm/agent/tray ──


# ── Restored: /api/rmm/last-scan/<agent_id> ──


# ── Eagle Eyes ────────────────────────────────────────────────────────────────



# ── Restored: /api/rmm/metrics-history/<agent_id> ──


# ── Restored: /api/rmm/availability/<agent_id> ──


# ── Restored: /api/rmm/patches/<agent_id> ──


# ── Restored: /api/rmm/pending-updates/<agent_id> ──


# ── Restored: /api/rmm/session-events/<agent_id> ──


# ── Restored: /api/rmm/software/<agent_id> ──


# ── Restored: /api/rmm/patch-jobs/<agent_id> ──


# ── Restored: /api/rmm/cmd/<agent_id> ──


# ── Restored: /api/rmm/cmd-result/<agent_id>/<int:session_id> ──


# ── Restored: /api/rmm/deploy-rustdesk/<agent_id> ──


# ── Restored: /api/rmm/patch-jobs/<agent_id>/<int:job_id>/deploy ──


# ── Restored: /api/rmm/rustdesk-sync/<agent_id> ──


# ── Restored: /api/rmm/agent-info/<agent_id> ──



# ─── Eagle Eyes Report Schedule Emailer ──────────────────────────────────────



@bp.route('/api/rmm/online-agents')
@login_required
def api_rmm_online_agents():
    """Return online agents based on last_seen_at <= 5 min."""
    rows = db.session.execute(text("""
        SELECT ra.agent_id,
               COALESCE(t.hostname, a.name, ra.agent_id) AS hostname,
               ra.last_seen_at
        FROM rmm_agent ra
        LEFT JOIN rmm_telemetry t ON t.agent_id = ra.agent_id
        LEFT JOIN asset a ON a.id = ra.asset_id
        WHERE ra.enabled = true
          AND ra.last_seen_at > NOW() - INTERVAL '5 minutes'
        ORDER BY ra.last_seen_at DESC
    """)).mappings().fetchall()
    return jsonify(ok=True, agents=[{
        'agent_id': r['agent_id'],
        'hostname': r['hostname'],
        'last_seen_at': _dt_iso(r['last_seen_at']),
    } for r in rows])


@bp.route('/api/rmm/scripts/tested', methods=['GET'])
@login_required
def api_rmm_scripts_tested():
    _ensure_rmm_script_library_table()
    rows = db.session.execute(text("""
        SELECT id, name, description, file_type, shell, script_content,
               last_tested_at, last_tested_agent_id
        FROM rmm_script_library
        WHERE is_active = true
          AND is_tested = true
        ORDER BY name ASC
    """)).mappings().fetchall()
    scripts = []
    for r in rows:
        d = dict(r)
        d['last_tested_at'] = _dt_iso(d.get('last_tested_at'))
        scripts.append(d)
    return jsonify(ok=True, scripts=scripts)


@bp.route('/api/rmm/site-token/regenerate', methods=['POST'])
@login_required
@admin_required
def rmm_regenerate_site_token():
    """Regenerate the site-wide RMM enrollment token (invalidates the old MSI)."""
    import secrets as _sec
    setting = Setting.query.filter_by(key='rmm_site_enrollment_token').first()
    if not setting:
        setting = Setting(key='rmm_site_enrollment_token')
        db.session.add(setting)
    setting.value = 'site_' + _sec.token_hex(32)
    db.session.commit()
    return jsonify({'ok': True, 'token': setting.value})


@bp.route('/terminal/<int:asset_id>')
@login_required
def terminal(asset_id):
    """Web-based SSH terminal for an asset"""
    asset = Asset.query.get_or_404(asset_id)
    return render_template('terminal.html', asset=asset)


@bp.route('/api/terminal/connect', methods=['POST'])
@login_required
def api_terminal_connect():
    """Connect to an asset via SSH"""
    data = request.get_json()
    
    asset_id = data.get('asset_id')
    username = data.get('username', 'root')
    password = data.get('password')
    
    asset = Asset.query.get_or_404(asset_id)
    
    # Generate session ID
    import uuid
    session_id = f"{current_user.id}:{asset_id}:{str(uuid.uuid4())[:8]}"
    
    # Create SSH session
    ssh_manager = get_ssh_manager()
    session = ssh_manager.create_session(
        session_id=session_id,
        hostname=asset.ip_address_1,
        username=username,
        password=password,
        port=22
    )
    
    if session:
        return jsonify({
            'success': True,
            'session_id': session_id
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to connect'
        }), 500


@bp.route('/api/terminal/input', methods=['POST'])
@login_required
def api_terminal_input():
    """Send input to SSH session"""
    data = request.get_json()
    
    session_id = data.get('session_id')
    input_data = data.get('data', '')
    
    ssh_manager = get_ssh_manager()
    session = ssh_manager.get_session(session_id)
    
    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    if session.send_input(input_data):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to send input'}), 500


@bp.route('/api/terminal/output', methods=['POST'])
@login_required
def api_terminal_output():
    """Get output from SSH session"""
    data = request.get_json()
    
    session_id = data.get('session_id')
    since_index = data.get('since_index', 0)
    
    ssh_manager = get_ssh_manager()
    session = ssh_manager.get_session(session_id)
    
    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    output = session.get_output(since_index)
    
    return jsonify({
        'success': True,
        'output': output,
        'index': since_index + len(output)
    })


@bp.route('/api/terminal/disconnect', methods=['POST'])
@login_required
def api_terminal_disconnect():
    """Disconnect SSH session"""
    data = request.get_json()
    
    session_id = data.get('session_id')
    
    ssh_manager = get_ssh_manager()
    if ssh_manager.close_session(session_id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Session not found'}), 404


@bp.route('/api/linux-agent/heartbeat', methods=['POST'])
@limiter.limit("120 per minute")
def api_linux_agent_heartbeat():
    """Receive heartbeat from Linux monitoring agent"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not _valid_agent_key(api_key):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    data = request.get_json()
    
    try:
        agent_id = data.get('agent_id')
        asset_id = data.get('asset_id')
        system_info = data.get('system_info', {})
        metrics = data.get('metrics', {})
        disks = data.get('disks', [])
        services_running = data.get('services_running', 0)
        updates = data.get('updates', {})
        
        # Save heartbeat to database
        cursor = db.session.execute(text("""
            INSERT INTO linux_agent_heartbeat 
            (agent_id, asset_id, system_info, metrics, disks, services_running, 
             updates_available, security_updates, timestamp)
            VALUES (:agent_id, :asset_id, :system_info, :metrics, :disks, 
                    :services_running, :updates_available, :security_updates, :timestamp)
        """), {
            'agent_id': agent_id,
            'asset_id': asset_id,
            'system_info': json.dumps(system_info),
            'metrics': json.dumps(metrics),
            'disks': json.dumps(disks),
            'services_running': services_running,
            'updates_available': updates.get('available', 0),
            'security_updates': updates.get('security', 0),
            'timestamp': datetime.utcnow()
        })
        db.session.commit()
        
        # If asset_id provided, update last seen
        if asset_id:
            cursor = db.session.execute(text("""
                UPDATE assets 
                SET last_seen = :last_seen 
                WHERE id = :asset_id
            """), {
                'last_seen': datetime.utcnow(),
                'asset_id': asset_id
            })
            db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Heartbeat received',
            'timestamp': datetime.utcnow().isoformat()
        })
    
    except Exception as e:
        print(f"Error processing agent heartbeat: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/linux-agent/checks', methods=['GET'])
@limiter.limit("120 per minute")
def api_linux_agent_checks():
    """Get checks that Linux agent should execute"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not _valid_agent_key(api_key):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    asset_id = request.args.get('asset_id')
    if not asset_id:
        return jsonify({'success': False, 'error': 'asset_id required'}), 400
    
    try:
        # Get checks for this asset's profile
        result = db.session.execute(text("""
            SELECT 
                mc.id, mc.name, mc.check_type, mc.check_command,
                mc.warning_threshold, mc.critical_threshold, 
                mc.check_interval_minutes
            FROM monitoring_check mc
            JOIN profile_check pc ON mc.id = pc.check_id
            JOIN asset_monitoring_profile amp ON pc.profile_id = amp.profile_id
            WHERE amp.asset_id = :asset_id AND mc.enabled = true
        """), {'asset_id': asset_id})
        
        checks = []
        for row in result:
            checks.append({
                'id': row[0],
                'name': row[1],
                'type': row[2],
                'command': row[3],
                'warning_threshold': row[4],
                'critical_threshold': row[5],
                'interval_minutes': row[6]
            })
        
        return jsonify({
            'success': True,
            'checks': checks
        })
    
    except Exception as e:
        print(f"Error getting agent checks: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/linux-agent/check-result', methods=['POST'])
@limiter.limit("120 per minute")
def api_linux_agent_check_result():
    """Receive check result from Linux agent"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not _valid_agent_key(api_key):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    data = request.get_json()
    
    try:
        asset_id = data.get('asset_id')
        check_id = data.get('check_id')
        status = data.get('status')
        value = data.get('value')
        message = data.get('message')
        
        # Save check result
        cursor = db.session.execute(text("""
            INSERT INTO monitoring_check_result 
            (asset_id, check_id, status, value, message, checked_at)
            VALUES (:asset_id, :check_id, :status, :value, :message, :checked_at)
        """), {
            'asset_id': asset_id,
            'check_id': check_id,
            'status': status,
            'value': str(value) if value is not None else None,
            'message': message,
            'checked_at': datetime.utcnow()
        })
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Check result saved'
        })
    
    except Exception as e:
        print(f"Error saving check result: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/rmm/agent-download')
@login_required
@admin_required
@license_required
def rmm_agent_download():
    """Serve the RMM agent folder as a zip for admins to push to endpoints."""
    import zipfile

    agent_dir = os.path.join(os.path.dirname(__file__), 'rmm_agent')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in ['agent_client.py', 'requirements.txt', 'install_service.ps1', 'README.md']:
            fpath = os.path.join(agent_dir, fname)
            if os.path.exists(fpath):
                zf.write(fpath, arcname=fname)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name='CirqueRMM-agent.zip',
    )


def _verify_agent_token(agent_id: str, token: str) -> bool:
    """Check agent_id + token against the rmm_agent table (SHA-256 comparison).
    On first contact, also checks rmm_enrollment_tokens for a one-time enrollment
    token and bootstraps the rmm_agent row if found."""
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = db.session.execute(
        text("SELECT id FROM rmm_agent WHERE agent_id = :aid AND agent_token_sha256 = :h AND enabled = true"),
        {'aid': agent_id, 'h': token_hash}
    ).fetchone()
    if row:
        return True

    # Check enrollment token (one-time, bootstraps rmm_agent on first use)
    try:
        enroll_row = db.session.execute(
            text("""SELECT id, asset_id FROM rmm_enrollment_tokens
                    WHERE token_sha256 = :h AND used = FALSE
                      AND (expires_at IS NULL OR expires_at > NOW())"""),
            {'h': token_hash}
        ).fetchone()
        if enroll_row:
            now = datetime.utcnow().isoformat()
            # Create rmm_agent row for this agent using the enrollment token as its credential
            existing = db.session.execute(
                text("SELECT id FROM rmm_agent WHERE agent_id = :aid"),
                {'aid': agent_id}
            ).fetchone()
            if not existing:
                db.session.execute(
                    text("""INSERT INTO rmm_agent
                            (agent_id, asset_id, agent_token_sha256, enabled, created_at, last_seen_at)
                            VALUES (:aid, :asid, :hash, TRUE, :now, :now)"""),
                    {'aid': agent_id, 'asid': enroll_row.asset_id, 'hash': token_hash, 'now': now}
                )
            else:
                db.session.execute(
                    text("""UPDATE rmm_agent SET agent_token_sha256 = :hash,
                            asset_id = :asid, last_seen_at = :now WHERE agent_id = :aid"""),
                    {'hash': token_hash, 'asid': enroll_row.asset_id, 'aid': agent_id, 'now': now}
                )
            # Mark enrollment token as used
            db.session.execute(
                text("UPDATE rmm_enrollment_tokens SET used = TRUE, used_at = NOW() WHERE id = :id"),
                {'id': enroll_row.id}
            )
            db.session.commit()
            return True
    except Exception as e:
        logger.warning(f"Enrollment token check failed: {e}")

    return False


@bp.route('/rmm/agent/version')
def rmm_agent_version():
    """Return current agent version + SHA-256 of agent_client.py.
    Authenticated by agent_id + token query params (agent calls this on startup).
    """
    agent_id = request.args.get('agent_id', '')
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401

    import hashlib
    agent_dir = os.path.join(os.path.dirname(__file__), 'rmm_agent')
    version_path = os.path.join(agent_dir, 'version.txt')
    agent_path = os.path.join(agent_dir, 'agent_client.py')

    version = '0.0.0'
    if os.path.exists(version_path):
        version = open(version_path).read().strip()

    checksum = ''
    if os.path.exists(agent_path):
        checksum = hashlib.sha256(open(agent_path, 'rb').read()).hexdigest()

    return jsonify({'version': version, 'checksum': checksum})


@bp.route('/rmm/agent/file')
def rmm_agent_file():
    """Serve agent_client.py for self-update. Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401

    agent_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'agent_client.py')
    return send_file(agent_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/api/rmm/agent-status/<agent_id>')
@login_required
def api_rmm_agent_status(agent_id):
    """Proxy the gateway /status/<agent_id>, falling back to DB last_seen_at recency."""
    try:
        resp = requests.get(f'{RMM_GATEWAY_INTERNAL}/status/{agent_id}', timeout=3)
        data = resp.json()
        if data.get('online'):
            return jsonify(data)
    except Exception:
        pass
    # Gateway doesn't have a live WS for HTTP-based agents — check DB freshness instead
    row = db.session.execute(
        text("SELECT last_seen_at FROM rmm_agent WHERE agent_id = :aid AND enabled = true"),
        {'aid': agent_id}
    ).fetchone()
    if row and row[0]:
        last = datetime.fromisoformat(str(row[0]))
        online = (datetime.utcnow() - last).total_seconds() < 300  # 5-minute window
        return jsonify({'agent_id': agent_id, 'online': online, 'source': 'db'})
    return jsonify({'agent_id': agent_id, 'online': False})


@bp.route('/api/rmm/issue-token', methods=['POST'])
@login_required
def api_rmm_issue_token():
    """Issue a short-lived connect token so the browser terminal can auth with the gateway."""
    data = request.get_json(force=True) or {}
    agent_id = data.get('agent_id', '').strip()
    if not agent_id:
        return jsonify({'error': 'agent_id required'}), 400

    token = 'rct_' + secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=5)).isoformat(timespec='seconds')

    db.session.execute(text(
        "INSERT INTO rmm_connect_token (token, agent_id, user_id, expires_at) VALUES (:t, :a, :u, :e)"
    ), {'t': token, 'a': agent_id, 'u': current_user.id, 'e': expires_at})
    db.session.commit()

    return jsonify({
        'token': token,
        'agent_id': agent_id,
        'gateway_url': RMM_GATEWAY_PUBLIC,
        'expires_at': expires_at,
    })


@bp.route('/rmm/terminal/<agent_id>')
@login_required
def rmm_terminal(agent_id):
    """Full-page xterm.js terminal for a connected RMM agent."""
    # Look up asset name from agent record
    row = db.session.execute(text(
        "SELECT a.name, a.id FROM rmm_agent ra LEFT JOIN asset a ON a.id = ra.asset_id WHERE ra.agent_id = :aid"
    ), {'aid': agent_id}).fetchone()
    asset_name = row[0] if row else agent_id
    asset_id   = row[1] if row else None
    return render_template('rmm_terminal.html',
        agent_id=agent_id,
        asset_name=asset_name,
        asset_id=asset_id,
        gateway_url=RMM_GATEWAY_PUBLIC,
    )


@bp.route('/api/rmm/telemetry/<agent_id>')
@login_required
def api_rmm_telemetry(agent_id):
    """Return latest saved telemetry for an agent."""
    import json as _json
    row = db.session.execute(
        text("SELECT * FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'No telemetry yet'}), 404
    d = dict(row._mapping)
    for _json_field, _alias in [
        ('disk_json', 'disk_json'),
        ('network_json', 'network_json'),
        ('gpu_json', 'gpu'),
        ('security_json', 'security'),
        ('sysinfo_json', 'sysinfo'),
    ]:
        raw = d.get(_json_field)
        parsed = None
        try:
            parsed = _json.loads(raw) if raw else ([] if _json_field in ('disk_json', 'network_json', 'gpu_json') else {})
        except Exception:
            parsed = [] if _json_field in ('disk_json', 'network_json', 'gpu_json') else {}
        d[_alias] = parsed
    # Normalize datetime fields so jsonify emits ISO 8601 with +00:00 offset
    for _k in ('captured_at', 'created_at', 'updated_at'):
        if _k in d:
            d[_k] = _dt_iso(d[_k])
    return jsonify({'ok': True, 'telemetry': d})


@bp.route('/api/rmm/eagle-eyes/<agent_id>', methods=['GET', 'POST'])
@login_required
def api_rmm_eagle_eyes(agent_id):
    """GET: return current Eagle Eyes config.  POST: enable/disable and push to agent."""
    import json as _json
    if request.method == 'GET':
        row = db.session.execute(
            text("SELECT enabled, screenshot_interval_min FROM rmm_eagle_config WHERE agent_id = :aid"),
            {'aid': agent_id}
        ).fetchone()
        if row:
            return jsonify({'ok': True, 'enabled': bool(row[0]), 'screenshot_interval_min': row[1]})
        return jsonify({'ok': True, 'enabled': False, 'screenshot_interval_min': 30})

    # POST — update config and push to gateway
    data = request.get_json(force=True) or {}
    enabled  = bool(data.get('enabled', False))
    interval = int(data.get('screenshot_interval_min', 30))
    db.session.execute(
        text("""INSERT INTO rmm_eagle_config (agent_id, enabled, screenshot_interval_min, updated_at)
                VALUES (:aid, :en, :iv, NOW() - INTERVAL '7 hours')
                ON CONFLICT(agent_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    screenshot_interval_min = excluded.screenshot_interval_min,
                    updated_at = excluded.updated_at"""),
        {'aid': agent_id, 'en': enabled, 'iv': interval}
    )
    db.session.commit()
    # Push config to connected agent via gateway
    try:
        import urllib.request as _ur
        payload = _json.dumps({'enabled': enabled, 'screenshot_interval_min': interval}).encode()
        req = _ur.Request(
            f"{RMM_GATEWAY_INTERNAL}/eagle-eyes/{agent_id}/push",
            data=payload, headers={'Content-Type': 'application/json'}, method='POST',
        )
        with _ur.urlopen(req, timeout=4) as r:
            _json.loads(r.read())
    except Exception:
        pass  # agent may not be connected; config is persisted so it applies on next connect
    return jsonify({'ok': True, 'enabled': enabled, 'screenshot_interval_min': interval})


def _dt_iso(dt) -> str | None:
    """Return an ISO 8601 string with UTC offset for JSON responses.
    psycopg2 returns TIMESTAMPTZ as naive datetimes when session timezone=UTC.
    We force UTC tzinfo so isoformat() emits '+00:00', letting the browser
    convert to the viewer's local timezone via new Date()."""
    if dt is None:
        return None
    if hasattr(dt, 'isoformat'):
        if getattr(dt, 'tzinfo', None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec='seconds')
    return str(dt)


def _agent_tz_offset_minutes(agent_id: str) -> int:
    """Return the agent's UTC offset in minutes by reading stored timezone string."""
    row = db.session.execute(
        text("SELECT timezone FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    tz_str = (row[0] or '') if row else ''
    m = _re_tz.search(r'\(UTC([+-])(\d+):(\d+)\)', tz_str)
    if not m:
        return 0
    sign = 1 if m.group(1) == '+' else -1
    return sign * (int(m.group(2)) * 60 + int(m.group(3)))


def _eagle_date_params(default_days: int = 7) -> tuple:
    """Return (where_clause, params_dict) for date filtering Eagle Eyes queries.
    All timestamps stored in MST. UI dates are MST. No conversion needed."""
    from_date = request.args.get('from_date', '').strip()
    to_date   = request.args.get('to_date', '').strip()
    if from_date and to_date:
        return (
            "captured_at >= :from_date AND captured_at < (CAST(:to_date AS DATE) + INTERVAL '1 day')",
            {'from_date': from_date, 'to_date': to_date}
        )
    days = int(request.args.get('days', default_days))
    return ("captured_at >= NOW() - CAST(:since AS INTERVAL)", {'since': f'{days} days'})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/events')
@login_required
def api_rmm_eagle_events(agent_id):
    """Return Eagle Eyes window events. Query params: days/from_date/to_date, limit."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    limit = int(request.args.get('limit', 500))
    rows = db.session.execute(
        text(f"""SELECT captured_at, process_name, window_title, duration_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                ORDER BY captured_at DESC
                LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit, **date_params}
    ).fetchall()
    events = [{'captured_at': _dt_iso(r[0]), 'process_name': r[1], 'window_title': r[2], 'duration_s': r[3]} for r in rows]
    return jsonify({'ok': True, 'events': events})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/app-summary')
@login_required
def api_rmm_eagle_app_summary(agent_id):
    """Return total time per process for the requested day range."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 17" if request.args.get('work_hours') == '1' else ''
    rows = db.session.execute(
        text(f"""SELECT process_name,
                       COUNT(*) as events,
                       SUM(duration_s) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                {wh_clause}
                GROUP BY process_name
                ORDER BY total_s DESC
                LIMIT 30"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    summary = [{'process_name': r[0], 'events': r[1], 'total_s': int(r[2] or 0)} for r in rows]
    return jsonify({'ok': True, 'summary': summary})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/hourly')
@login_required
def api_rmm_eagle_hourly(agent_id):
    """Return total active seconds per hour-of-day (0-23) grouped in server local time."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 17" if request.args.get('work_hours') == '1' else ''
    rows = db.session.execute(
        text(f"""SELECT CAST(EXTRACT(HOUR FROM (captured_at AT TIME ZONE 'America/Denver')) AS INTEGER) as hr,
                       SUM(COALESCE(duration_s, 0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                {wh_clause}
                GROUP BY hr ORDER BY hr"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    by_hour = {r[0]: int(r[1] or 0) for r in rows}
    result = [{'hour': h, 'total_s': by_hour.get(h, 0)} for h in range(24)]
    return jsonify({'ok': True, 'hourly': result})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/daily')
@login_required
def api_rmm_eagle_daily(agent_id):
    """Return total active seconds per calendar day grouped in server local time."""
    date_clause, date_params = _eagle_date_params(default_days=30)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 17" if request.args.get('work_hours') == '1' else ''
    rows = db.session.execute(
        text(f"""SELECT CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE) as day,
                       SUM(COALESCE(duration_s, 0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                {wh_clause}
                GROUP BY day ORDER BY day"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    result = [{'day': str(r[0]), 'total_s': int(r[1] or 0)} for r in rows]
    return jsonify({'ok': True, 'daily': result})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/top-sites')
@login_required
def api_rmm_eagle_top_sites(agent_id):
    """Return top browser sites derived from window titles."""
    import re as _re_site
    date_clause, date_params = _eagle_date_params(default_days=7)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 17" if request.args.get('work_hours') == '1' else ''
    rows = db.session.execute(
        text(f"""SELECT window_title, SUM(COALESCE(duration_s,0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                  {wh_clause}
                  AND LOWER(process_name) IN ('msedge','chrome','firefox','brave','opera','iexplore','safari')
                  AND window_title IS NOT NULL AND window_title != ''
                GROUP BY window_title ORDER BY total_s DESC LIMIT 200"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    # Strip browser name suffixes then take last " - " segment as site name
    strip_re = _re_site.compile(
        r'\s*[-\u2013|]\s*(Google Chrome|Microsoft\u200b?\s*Edge|Mozilla Firefox'
        r'|Brave|Opera|Work\s*[-\u2013]\s*Microsoft\u200b?\s*Edge).*$'
        r'|( and \d+ more pages.*$)', _re_site.IGNORECASE
    )
    agg: dict = {}
    for title, total_s in rows:
        t = strip_re.sub('', title or '').strip()
        parts = _re_site.split(r'\s+[-\u2013|]\s+', t)
        site = (parts[-1] if len(parts) >= 2 else parts[0]).strip()
        if not site or site.lower() in ('new tab', 'about:blank', ''):
            continue
        agg[site] = agg.get(site, 0) + int(total_s or 0)
    result = sorted([{'site': k, 'total_s': v} for k, v in agg.items()], key=lambda x: -x['total_s'])[:15]
    return jsonify({'ok': True, 'sites': result})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/screenshots')
@login_required
def api_rmm_eagle_screenshots(agent_id):
    """Return Eagle Eyes screenshots metadata (no image data) for the gallery."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    limit = int(request.args.get('limit', 200))
    rows = db.session.execute(
        text(f"""SELECT id, captured_at, width, height, image_format
                FROM rmm_screenshot
                WHERE agent_id = :aid AND source = 'eagle' AND {date_clause}
                ORDER BY id DESC LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit, **date_params}
    ).fetchall()
    shots = [{'id': r[0], 'time': _dt_iso(r[1]), 'width': r[2], 'height': r[3], 'format': r[4]} for r in rows]
    return jsonify({'ok': True, 'screenshots': shots})


@bp.route('/api/rmm/eagle-eyes/screenshot/<int:shot_id>')
@login_required
def api_rmm_eagle_screenshot_image(shot_id):
    """Return a single Eagle Eyes screenshot including the base64 image."""
    import base64 as _b64, os as _os
    row = db.session.execute(
        text("SELECT agent_id, image_b64, image_format, width, height, captured_at, file_path FROM rmm_screenshot WHERE id = :id"),
        {'id': shot_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    b64 = row[1]
    if not b64 and row[6] and _os.path.exists(row[6]):
        with open(row[6], 'rb') as fh:
            b64 = _b64.b64encode(fh.read()).decode()
    return jsonify({'ok': True, 'screenshot': {
        'id': shot_id, 'agent_id': row[0], 'data': b64,
        'format': row[2], 'width': row[3], 'height': row[4], 'time': _dt_iso(row[5]),
    }})


@bp.route('/api/rmm/eagle-eyes/screenshot/<int:shot_id>/download')
@login_required
def api_rmm_eagle_screenshot_download(shot_id):
    """Download a screenshot as an image file attachment."""
    import base64 as _b64, io, os as _os
    from flask import send_file
    row = db.session.execute(
        text("SELECT agent_id, image_b64, image_format, captured_at, file_path FROM rmm_screenshot WHERE id = :id"),
        {'id': shot_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    agent_id, b64_data, fmt, captured_at, file_path = row
    ts = (captured_at or 'unknown').replace(':', '').replace(' ', '_').replace('T', '_')
    fname = f"{agent_id}_{ts}.{fmt or 'jpeg'}"
    if file_path and _os.path.exists(file_path):
        return send_file(file_path, mimetype=f'image/{fmt or "jpeg"}',
                         as_attachment=True, download_name=fname)
    if b64_data:
        buf = io.BytesIO(_b64.b64decode(b64_data))
        return send_file(buf, mimetype=f'image/{fmt or "jpeg"}',
                         as_attachment=True, download_name=fname)
    return jsonify({'ok': False, 'error': 'Image data not available'}), 404


@bp.route('/rmm/eagle-eyes/<agent_id>')
@login_required
def rmm_eagle_eyes_dashboard(agent_id):
    """Eagle Eyes dashboard page for a specific agent."""
    row = db.session.execute(
        text("""SELECT ra.asset_id, COALESCE(a.name, ra.agent_id)
                FROM rmm_agent ra
                LEFT JOIN asset a ON ra.asset_id = a.id
                WHERE ra.agent_id ILIKE :aid"""),
        {'aid': agent_id}
    ).fetchone()
    hostname     = row[1] if row else agent_id
    asset_id_num = row[0] if row else None
    # Get timezone offset from the most recent event's stored UTC offset.
    # This is the only reliable source — no telemetry string parsing needed.
    tz_offset_h = -6.0  # MDT default (server timezone)
    recent_ev = db.session.execute(
        text("SELECT captured_at FROM rmm_eagle_event WHERE agent_id = :aid ORDER BY captured_at DESC LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if recent_ev and recent_ev[0] and recent_ev[0].utcoffset() is not None:
        tz_offset_h = recent_ev[0].utcoffset().total_seconds() / 3600
    return render_template('eagle_eyes.html', agent_id=agent_id, hostname=hostname,
                           asset_id_num=asset_id_num,
                           tz_offset_h=tz_offset_h)


@bp.route('/api/rmm/eagle-eyes/<agent_id>/current')
@login_required
def api_eagle_current(agent_id):
    try:
        row = db.session.execute(
            text("SELECT process_name, window_title, idle_s, is_idle, captured_at FROM rmm_eagle_current WHERE agent_id = :aid"),
            {"aid": agent_id}
        ).mappings().fetchone()
        if row:
            c = dict(row)
            dt = c.get('captured_at')
            c['captured_at'] = _dt_iso(dt)
            # Pass DST-aware Mountain Time offset so JS keeps agentTzOffsetH correct
            try:
                tz_h = db.session.execute(
                    text("SELECT EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'America/Denver' - NOW() AT TIME ZONE 'UTC'))/3600")
                ).scalar()
                c['tz_offset_h'] = float(tz_h) if tz_h is not None else -6.0
            except Exception:
                c['tz_offset_h'] = -6.0
            return jsonify(ok=True, current=c)
        return jsonify(ok=True, current=None)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/<agent_id>/focus-sessions')
@login_required
def api_eagle_focus_sessions(agent_id):
    date_clause, date_params = _eagle_date_params(default_days=7)
    try:
        rows = db.session.execute(
            text(f"""
                SELECT process_name, window_title, duration_s, captured_at
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                ORDER BY captured_at
            """),
            {"aid": agent_id, **date_params}
        ).mappings().fetchall()
        # Group consecutive events on the same process into focus sessions
        sessions = []
        FOCUS_MIN_S = 600   # only surface sessions ≥ 10 min
        BREAK_S     = 120   # gap ≥ 2 min breaks the session
        if rows:
            cur_proc  = rows[0]['process_name']
            cur_title = rows[0]['window_title']
            cur_start = rows[0]['captured_at']
            cur_dur   = rows[0]['duration_s'] or 0
            for r in rows[1:]:
                if r['process_name'] == cur_proc:
                    cur_dur += r['duration_s'] or 0
                else:
                    if cur_dur >= FOCUS_MIN_S:
                        sessions.append({'process_name': cur_proc, 'window_title': cur_title,
                                         'started_at': _dt_iso(cur_start), 'duration_s': cur_dur})
                    cur_proc  = r['process_name']
                    cur_title = r['window_title']
                    cur_start = r['captured_at']
                    cur_dur   = r['duration_s'] or 0
            if cur_dur >= FOCUS_MIN_S:
                sessions.append({'process_name': cur_proc, 'window_title': cur_title,
                                 'started_at': _dt_iso(cur_start), 'duration_s': cur_dur})
        sessions.sort(key=lambda s: s['duration_s'], reverse=True)
        return jsonify(ok=True, sessions=sessions[:50])
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/app-classifications', methods=['GET', 'POST'])
@login_required
def api_eagle_app_classifications():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, process_pattern, label, productivity, created_at FROM rmm_eagle_app_class ORDER BY process_pattern")
            ).mappings().fetchall()
            return jsonify(ok=True, classifications=[dict(r) for r in rows])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    # POST — add or update
    data = request.get_json() or {}
    pattern = (data.get('process_pattern') or '').strip().lower()
    label   = (data.get('label') or '').strip()
    prod    = (data.get('productivity') or 'neutral').strip()
    if not pattern:
        return jsonify(ok=False, error='process_pattern required')
    if prod not in ('productive','unproductive','neutral'):
        return jsonify(ok=False, error='Invalid productivity value')
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_app_class (process_pattern, label, productivity, created_at)
            VALUES (:p, :l, :pr, :ca)
            ON CONFLICT(process_pattern) DO UPDATE SET label=excluded.label, productivity=excluded.productivity
        """), {'p': pattern, 'l': label, 'pr': prod, 'ca': datetime.utcnow().isoformat()})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/app-classifications/<int:cid>', methods=['DELETE'])
@login_required
def api_eagle_app_class_delete(cid):
    try:
        db.session.execute(text("DELETE FROM rmm_eagle_app_class WHERE id = :id"), {'id': cid})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/alerts', methods=['GET', 'POST'])
@login_required
def api_eagle_alerts():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, agent_id, alert_type, threshold, process_pattern, email_notify, enabled, last_fired_at, created_at FROM rmm_eagle_alert_rule ORDER BY id DESC")
            ).mappings().fetchall()
            logs = db.session.execute(
                text("SELECT rule_id, agent_id, message, fired_at FROM rmm_eagle_alert_log ORDER BY fired_at DESC LIMIT 50")
            ).mappings().fetchall()
            return jsonify(ok=True, rules=[dict(r) for r in rows], log=[dict(l) for l in logs])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    alert_type = (data.get('alert_type') or '').strip()
    if alert_type not in ('productivity_below','app_used','idle_over','unproductive_app'):
        return jsonify(ok=False, error='Invalid alert_type')
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_alert_rule (agent_id, alert_type, threshold, process_pattern, email_notify, enabled, created_at)
            VALUES (:aid, :at, :th, :pp, :en, 1, :ca)
        """), {
            'aid': data.get('agent_id') or None,
            'at':  alert_type,
            'th':  data.get('threshold') or None,
            'pp':  (data.get('process_pattern') or '').strip().lower() or None,
            'en':  1 if data.get('email_notify', True) else 0,
            'ca':  datetime.utcnow().isoformat()
        })
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/alerts/<int:rid>', methods=['PUT', 'DELETE'])
@login_required
def api_eagle_alert_rule(rid):
    if request.method == 'DELETE':
        try:
            db.session.execute(text("DELETE FROM rmm_eagle_alert_rule WHERE id = :id"), {'id': rid})
            db.session.commit()
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    try:
        db.session.execute(text("""
            UPDATE rmm_eagle_alert_rule SET
              enabled=:en, threshold=:th, email_notify=:email
            WHERE id=:id
        """), {'en': 1 if data.get('enabled',True) else 0, 'th': data.get('threshold'), 'email': 1 if data.get('email_notify',True) else 0, 'id': rid})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/report-schedules', methods=['GET', 'POST', 'DELETE'])
@login_required
def api_eagle_report_schedules():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, agent_id, frequency, day_of_week, send_time, email_to, last_sent_at, enabled, created_at FROM rmm_eagle_report_schedule ORDER BY id DESC")
            ).mappings().fetchall()
            return jsonify(ok=True, schedules=[dict(r) for r in rows])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    if request.method == 'DELETE':
        sid = request.args.get('id')
        try:
            db.session.execute(text("DELETE FROM rmm_eagle_report_schedule WHERE id = :id"), {'id': sid})
            db.session.commit()
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_report_schedule (agent_id, frequency, day_of_week, send_time, email_to, enabled, created_at)
            VALUES (:aid, :freq, :dow, :st, :email, 1, :ca)
        """), {
            'aid':   data.get('agent_id') or None,
            'freq':  data.get('frequency','weekly'),
            'dow':   data.get('day_of_week', 1),
            'st':    data.get('send_time','08:00'),
            'email': data.get('email_to',''),
            'ca':    datetime.utcnow().isoformat()
        })
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/rmm/eagle-eyes')
@login_required
@eagle_eyes_required
def rmm_eagle_eyes_fleet():
    """Fleet-wide Eagle Eyes dashboard — all monitored devices."""
    return render_template('eagle_eyes_fleet.html')


@bp.route('/api/rmm/eagle-eyes/fleet')
@login_required
@eagle_eyes_required
def api_eagle_fleet():
    """Return all eagle-eyes-enabled agents with live + daily stats."""
    try:
        # All enabled agents with telemetry + current app
        agents_q = db.session.execute(text("""
            SELECT
                ec.agent_id,
                COALESCE(t.hostname, ec.agent_id)       AS hostname,
                COALESCE(t.logged_in_user, '')           AS logged_in_user,
                cur.process_name                         AS current_app,
                cur.captured_at                          AS last_event,
                ra.last_seen_at
            FROM rmm_eagle_config ec
            LEFT JOIN rmm_telemetry t   ON t.agent_id  = ec.agent_id
            LEFT JOIN rmm_eagle_current cur ON cur.agent_id = ec.agent_id
            LEFT JOIN rmm_agent ra      ON ra.agent_id = ec.agent_id
            WHERE ec.enabled = true
            ORDER BY COALESCE(t.logged_in_user, ec.agent_id)
        """)).mappings().fetchall()

        # Filter out excluded agents for non-admins
        if current_user.role != 'admin':
            excluded_ids = {r['agent_id'] for r in db.session.execute(text(
                "SELECT agent_id FROM eagle_eyes_exclusions"
            )).mappings().fetchall()}
            agents_q = [r for r in agents_q if r['agent_id'] not in excluded_ids]

        # Today's active seconds per agent (Mountain Time day)
        today_q = db.session.execute(text(f"""
            SELECT agent_id, SUM(duration_s) AS today_s
            FROM rmm_eagle_event
            WHERE CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE)
                  = CAST(NOW() AT TIME ZONE 'America/Denver' AS DATE)
            {_EAGLE_SYSTEM_EXCL}
            GROUP BY agent_id
        """)).mappings().fetchall()
        today_map = {r['agent_id']: int(r['today_s'] or 0) for r in today_q}

        # Top app today per agent
        top_q = db.session.execute(text(f"""
            SELECT DISTINCT ON (agent_id)
                agent_id, process_name, SUM(duration_s) AS total_s
            FROM rmm_eagle_event
            WHERE CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE)
                  = CAST(NOW() AT TIME ZONE 'America/Denver' AS DATE)
            {_EAGLE_SYSTEM_EXCL}
            GROUP BY agent_id, process_name
            ORDER BY agent_id, total_s DESC
        """)).mappings().fetchall()
        top_map = {r['agent_id']: r['process_name'] for r in top_q}

        now_utc = datetime.utcnow()
        result = []
        for a in agents_q:
            aid = a['agent_id']
            last_seen = a['last_seen_at']
            last_event = a['last_event']
            online = False
            if last_seen:
                if hasattr(last_seen, 'tzinfo') and last_seen.tzinfo:
                    from datetime import timezone as _tz
                    diff = (datetime.now(_tz.utc) - last_seen).total_seconds()
                else:
                    diff = (now_utc - last_seen).total_seconds()
                online = diff < 300
            result.append({
                'agent_id':     aid,
                'hostname':     a['hostname'],
                'user':         a['logged_in_user'],
                'current_app':  a['current_app'] or '',
                'last_event':   _dt_iso(last_event),
                'last_seen':    _dt_iso(last_seen),
                'online':       online,
                'today_s':      today_map.get(aid, 0),
                'top_app':      top_map.get(aid, ''),
            })
        total_today_s = sum(r['today_s'] for r in result)
        online_count  = sum(1 for r in result if r['online'])
        return jsonify(ok=True, agents=result,
                       total=len(result), online=online_count,
                       total_today_s=total_today_s)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/rmm/eagle-eyes/compare')
@login_required
def rmm_eagle_compare():
    agents = db.session.execute(
        text("""
            SELECT a.agent_id, COALESCE(t.hostname, a.agent_id) as hostname
            FROM rmm_agent a
            LEFT JOIN rmm_telemetry t ON t.agent_id = a.agent_id
            WHERE a.enabled = true
            ORDER BY hostname
        """)
    ).mappings().fetchall()
    return render_template('compare_agents.html', agents=[dict(a) for a in agents])


@bp.route('/api/rmm/eagle-eyes/compare-data')
@login_required
def api_eagle_compare_data():
    agent_ids = request.args.get('agents','').split(',')
    agent_ids = [a.strip() for a in agent_ids if a.strip()]
    days      = int(request.args.get('days', 7))
    if not agent_ids:
        return jsonify(ok=False, error='No agents specified')
    results = {}
    for aid in agent_ids:
        try:
            summary = db.session.execute(text(f"""
                SELECT process_name, SUM(duration_s) as total_s, COUNT(*) as events
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND captured_at >= NOW() - INTERVAL '{days} days'
                {_EAGLE_SYSTEM_EXCL}
                GROUP BY process_name ORDER BY total_s DESC LIMIT 10
            """), {'aid': aid}).mappings().fetchall()
            daily = db.session.execute(text(f"""
                SELECT CAST(captured_at AS DATE) as day, SUM(duration_s) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND captured_at >= NOW() - INTERVAL '{days} days'
                {_EAGLE_SYSTEM_EXCL}
                GROUP BY day ORDER BY day
            """), {'aid': aid}).mappings().fetchall()
            hostname = db.session.execute(
                text("SELECT hostname FROM rmm_telemetry WHERE agent_id = :aid LIMIT 1"), {'aid': aid}
            ).scalar() or aid
            results[aid] = {
                'hostname': hostname,
                'summary':  [{'process_name': r['process_name'], 'total_s': int(r['total_s'] or 0), 'events': r['events']} for r in summary],
                'daily':    [{'day': str(r['day']), 'total_s': int(r['total_s'] or 0)} for r in daily],
                'total_s':  sum(int(r['total_s'] or 0) for r in summary),
            }
        except Exception as e:
            results[aid] = {'hostname': aid, 'error': str(e)}
    return jsonify(ok=True, results=results, days=days)


@bp.route('/api/rmm/eagle-eyes/<agent_id>/gantt')
@login_required
def api_eagle_gantt(agent_id):
    """Return events for a specific day as a gantt-ready list."""
    day = request.args.get('day')  # YYYY-MM-DD
    if not day:
        from datetime import date
        day = date.today().isoformat()
    try:
        # Compute DST-aware offset for Mountain Time so returned timestamps carry correct offset
        tz_h_row = db.session.execute(
            text("SELECT EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'America/Denver' - NOW() AT TIME ZONE 'UTC'))/3600")
        ).scalar()
        tz_offset_h = float(tz_h_row) if tz_h_row is not None else -6.0
        tz_suffix = f'-{abs(int(tz_offset_h)):02d}:00' if tz_offset_h < 0 else f'+{int(tz_offset_h):02d}:00'
        rows = db.session.execute(text("""
            SELECT process_name, window_title, duration_s, idle_s,
                   to_char(captured_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD"T"HH24:MI:SS') AS local_ts
            FROM rmm_eagle_event
            WHERE agent_id = :aid
              AND CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE) = CAST(:day AS DATE)
            ORDER BY captured_at
        """), {'aid': agent_id, 'day': day}).mappings().fetchall()
        events = [{'process_name': r['process_name'], 'window_title': r['window_title'],
                   'duration_s': r['duration_s'], 'idle_s': r['idle_s'],
                   'captured_at': r['local_ts'] + tz_suffix} for r in rows]
        return jsonify(ok=True, day=day, events=events, tz_offset_h=tz_offset_h)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/screenshot/<agent_id>', methods=['POST'])
@login_required
def api_rmm_screenshot_request(agent_id):
    """Ask the gateway to request a screenshot from the agent.
    The gateway must have an agent session open.
    We POST a JSON command to the gateway's internal HTTP endpoint."""
    gw_internal = RMM_GATEWAY_INTERNAL
    try:
        import urllib.request as _ur, json as _json
        payload = _json.dumps({
            'type': 'screenshot_request',
            'target_agent': agent_id,
        }).encode()
        req = _ur.Request(
            f"{gw_internal}/screenshot-request/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _ur.urlopen(req, timeout=5) as r:
            resp = _json.loads(r.read())
        return jsonify({'ok': True, **resp})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502


@bp.route('/api/rmm/screenshot/<agent_id>/latest')
@login_required
def api_rmm_screenshot_latest(agent_id):
    """Return the most recent stored screenshot for an agent."""
    row = db.session.execute(
        text("SELECT id, image_b64, image_format, width, height, captured_at FROM rmm_screenshot WHERE agent_id = :aid ORDER BY id DESC LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'No screenshot yet'}), 404
    return jsonify({
        'ok': True,
        'id': row[0],
        'image_b64': row[1],
        'format': row[2],
        'width': row[3],
        'height': row[4],
        'captured_at': row[5],
    })


@bp.route('/api/rmm/system-info', methods=['POST'])
def receive_system_info():
    """Receive system info from Linux agent and update asset record"""
    try:
        data = request.get_json()
        
        if not data or 'agent_id' not in data:
            return jsonify({'ok': False, 'error': 'Missing agent_id'}), 400
        
        agent_id = data['agent_id']
        hostname = data.get('hostname', 'Unknown')
        
        # Find asset by agent_id (stored in serial_number) or by hostname
        asset = Asset.query.filter(
            (Asset.serial_number == agent_id) | 
            (Asset.name.ilike(f'%{hostname}%'))
        ).first()
        
        if not asset:
            # Create new asset from agent data
            asset = Asset(
                asset_tag=f'LINUX-{agent_id[:8].upper()}',
                name=hostname,
                category='Server' if 'server' in hostname.lower() else 'Workstation',
                device_type='Virtual Machine' if data.get('virtualization', {}).get('is_virtual') else 'Linux Workstation',
                status='In Use'
            )
            db.session.add(asset)
        
        # Update asset with system info
        os_info = data.get('os', {})
        cpu_info = data.get('cpu', {})
        mem_info = data.get('memory', {})
        virt_info = data.get('virtualization', {})
        network_info = data.get('network', [])
        
        asset.operating_system = os_info.get('pretty_name', 'Linux')
        asset.os_version = os_info.get('version', 'Unknown')
        asset.hardware_cpu = cpu_info.get('model', 'Unknown').strip()
        asset.hardware_ram_gb = mem_info.get('total_gb')
        asset.last_seen = datetime.utcnow()
        asset.online_state = 'Online'
        
        # Extract primary IP and MAC from network interfaces
        if network_info:
            for iface in network_info:
                if iface.get('is_up') and iface.get('addresses'):
                    # Get first IPv4 address
                    for addr in iface.get('addresses', []):
                        if addr.get('type') == 'ipv4' and not asset.ip_address:
                            asset.ip_address = addr.get('address')
                    
                    # Get MAC address
                    mac = iface.get('mac')
                    if mac:
                        # Store in appropriate field based on interface name
                        if 'wl' in iface.get('name', '') or 'wifi' in iface.get('name', '').lower():
                            asset.hardware_mac_wifi = mac
                        else:
                            asset.hardware_mac_ethernet = mac
        
        # Set manufacturer/model from virtualization or detect
        if virt_info.get('is_virtual'):
            asset.manufacturer = virt_info.get('type', 'Unknown').upper()
            asset.model = 'Virtual Machine'
            if not asset.device_type or asset.device_type == '-':
                asset.device_type = 'Virtual Machine'
        
        # Store agent_id in serial_number field
        if not asset.serial_number:
            asset.serial_number = agent_id
        
        db.session.commit()
        
        # Ensure rmm_agent entry exists for this agent (auto-register if missing)
        try:
            rmm_row = db.session.execute(
                text("SELECT id FROM rmm_agent WHERE agent_id = :aid"),
                {'aid': agent_id}
            ).fetchone()
            if not rmm_row:
                import hashlib, secrets
                tok = secrets.token_hex(32)
                tok_hash = hashlib.sha256(tok.encode()).hexdigest()
                db.session.execute(
                    text("INSERT INTO rmm_agent (agent_id, asset_id, agent_token_sha256, enabled, created_at, last_seen_at) VALUES (:aid, :asid, :hash, 1, :now, :now)"),
                    {'aid': agent_id, 'asid': asset.id, 'hash': tok_hash, 'now': datetime.utcnow().isoformat()}
                )
            else:
                db.session.execute(
                    text("UPDATE rmm_agent SET last_seen_at = :now, asset_id = :asid WHERE agent_id = :aid"),
                    {'now': datetime.utcnow().isoformat(), 'asid': asset.id, 'aid': agent_id}
                )
            db.session.commit()
        except Exception as e:
            logger.warning(f"rmm_agent upsert failed: {e}")
        
        logger.info(f"Updated asset {asset.name} (ID {asset.id}) from agent {agent_id}")
        
        return jsonify({
            'ok': True,
            'asset_id': asset.id,
            'message': f'Updated asset {asset.name}'
        })
        
    except Exception as e:
        logger.error(f"Error receiving system info: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/rmm/telemetry', methods=['POST'])
def receive_telemetry():
    """Receive telemetry from Linux agent, update asset last_seen, and persist metrics."""
    import json as _json
    try:
        data = request.get_json()

        if not data or 'agent_id' not in data:
            return jsonify({'ok': False, 'error': 'Missing agent_id'}), 400

        agent_id = data['agent_id']
        now = datetime.utcnow()

        # Update asset last_seen / online_state
        asset = Asset.query.filter_by(serial_number=agent_id).first()
        asset_id = asset.id if asset else None
        if asset:
            asset.last_seen = now
            asset.online_state = 'Online'

        # Refresh rmm_agent.last_seen_at
        db.session.execute(
            text("UPDATE rmm_agent SET last_seen_at = :ts WHERE agent_id = :aid"),
            {'ts': now.isoformat(), 'aid': agent_id}
        )

        # Persist CPU / RAM / Disk metrics into rmm_telemetry
        cpu_info  = data.get('cpu', {})
        mem_info  = data.get('memory', {})
        disk_info = data.get('disk', {})

        cpu_pct  = cpu_info.get('usage_percent')
        ram_pct  = mem_info.get('usage_percent')
        ram_total_gb = (mem_info.get('total_mb') or 0) / 1024
        ram_avail_gb = (mem_info.get('available_mb') or 0) / 1024

        # Normalise disk into the same JSON array the UI expects
        disk_json = '[]'
        if disk_info:
            disk_json = _json.dumps([{
                'mountpoint': '/',
                'device': '/',
                'total_gb': disk_info.get('total_gb', 0),
                'free_gb':  disk_info.get('free_gb', 0),
                'percent':  disk_info.get('usage_percent', 0),
            }])

        db.session.execute(text("""
            INSERT INTO rmm_telemetry
                (agent_id, asset_id, cpu_percent, ram_percent, ram_total_gb,
                 ram_available_gb, disk_json, captured_at)
            VALUES
                (:aid, :asid, :cpu, :ram, :ramt, :rava, :dj, :ts)
            ON CONFLICT(agent_id) DO UPDATE SET
                cpu_percent=excluded.cpu_percent,
                ram_percent=excluded.ram_percent,
                ram_total_gb=excluded.ram_total_gb,
                ram_available_gb=excluded.ram_available_gb,
                disk_json=excluded.disk_json,
                captured_at=excluded.captured_at
        """), {
            'aid': agent_id, 'asid': asset_id,
            'cpu': cpu_pct, 'ram': ram_pct,
            'ramt': ram_total_gb, 'rava': ram_avail_gb,
            'dj': disk_json, 'ts': now.isoformat()
        })

        db.session.commit()
        return jsonify({'ok': True})

    except Exception as e:
        logger.error(f"Error receiving telemetry: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/rmm/<agent_id>/software', methods=['POST'])
def rmm_update_software(agent_id):
    """RMM agent POSTs software inventory via its agent_id + token (no user login needed)."""
    token = request.args.get('token', '') or request.headers.get('X-Agent-Token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    # Look up the asset linked to this agent
    row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = true LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row or not row[0]:
        return jsonify({'error': 'No asset linked to this agent'}), 404
    asset_id = row[0]
    apps = request.get_json(silent=True) or []
    # Delete existing software inventory for this agent and re-insert
    db.session.execute(text("DELETE FROM rmm_software WHERE agent_id = :aid"), {'aid': agent_id})
    now = now_mst()
    inserted = 0
    for a in apps:
        name = (a.get('name') or '').strip()
        if not name:
            continue
        db.session.execute(
            text("""INSERT INTO rmm_software (agent_id, name, version, publisher, install_date, captured_at)
                     VALUES (:aid, :name, :ver, :pub, :idate, :now)"""),
            {
                'aid': agent_id,
                'name': name,
                'ver': (a.get('version') or '').strip() or None,
                'pub': (a.get('publisher') or '').strip() or None,
                'idate': (a.get('install_date') or '').strip() or None,
                'now': now,
            }
        )
        inserted += 1
    db.session.commit()
    return jsonify({'ok': True, 'count': inserted})


@bp.route('/download/agent-installer')
@login_required
@license_required
def download_agent_installer():
    """Serve the EXE installer (preferred), then MSI, then PS1 fallback."""
    import os
    # NSIS-built EXE: no msiexec Custom Action engine, most reliable
    exe_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.exe')
    if os.path.exists(exe_path):
        return send_file(exe_path, as_attachment=True, download_name='CirqueRMM.exe',
                         mimetype='application/octet-stream')
    # Legacy MSI fallback
    msi_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.msi')
    if os.path.exists(msi_path):
        return send_file(msi_path, as_attachment=True, download_name='CirqueRMM.msi',
                         mimetype='application/x-msi')
    # Last resort: bare PS1
    ps1_path = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(ps1_path):
        return "Installer not found on server.", 404
    return send_file(ps1_path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')


@bp.route('/download/agent-ps1')
@login_required
@license_required
def download_agent_ps1():
    """Serve the raw PowerShell installer script directly."""
    import os
    path = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')


@bp.route('/download/agent-bat')
@login_required
@license_required
def download_agent_bat():
    """Serve a .bat launcher that runs the PS1 bypassing execution policy.

    The user saves this .bat alongside CirqueRMM-Install.ps1 (or it downloads
    the PS1 automatically) and just double-clicks it as Administrator.
    """
    import hmac as _hmac
    site_token  = _get_or_create_site_enrollment_token()
    tracker_url = RMM_TRACKER_URL.rstrip('/')
    ps1_url     = f"{tracker_url}/download/site-install.ps1?t={site_token}"

    bat = (
        "@echo off\r\n"
        ":: Cirque RMM Agent Installer Launcher\r\n"
        ":: Right-click this file and choose 'Run as administrator'\r\n"
        "setlocal\r\n"
        "\r\n"
        ":: Check for admin rights\r\n"
        "net session >nul 2>&1\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo ERROR: Please right-click and choose 'Run as administrator'\r\n"
        "    pause\r\n"
        "    exit /b 1\r\n"
        ")\r\n"
        "\r\n"
        "echo Installing Cirque RMM Agent...\r\n"
        "echo Log: C:\\CirqueRMM\\logs\\setup.log\r\n"
        "echo.\r\n"
        "\r\n"
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -NonInteractive -Command \""
        f"[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        f"irm '{ps1_url}' | iex\"\r\n"
        "\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo.\r\n"
        "    echo Installation failed. Check C:\\CirqueRMM\\logs\\setup.log\r\n"
        "    echo Also check %%TEMP%%\\CirqueRMM_install.log\r\n"
        ") else (\r\n"
        "    echo.\r\n"
        "    echo Installation complete!\r\n"
        ")\r\n"
        "pause\r\n"
    )

    from flask import Response
    return Response(bat.encode('ascii'),
                    mimetype='application/octet-stream',
                    headers={'Content-Disposition': 'attachment; filename="CirqueRMM-Install.bat"'})


@bp.route('/download/agent-msi')
@login_required
@license_required
def download_agent_msi():
    """Serve the MSI installer directly."""
    import os
    msi_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.msi')
    if not os.path.exists(msi_path):
        return 'MSI installer not found on server.', 404
    return send_file(msi_path, as_attachment=True, download_name='CirqueRMM.msi',
                     mimetype='application/x-msi')


@bp.route('/download/site-install.ps1')
def download_site_install_ps1():
    """Serve a pre-configured PS1 authenticated by the site token in ?t=.

    Designed for the one-liner:  irm 'https://.../download/site-install.ps1?t=TOKEN' | iex
    No browser session required — the site token itself is the credential.

    Strips the param() block so iex can run the script without errors.
    iex cannot handle param() blocks — only script files/-File mode can.
    """
    import os, re as _re
    t = request.args.get('t', '').strip()
    expected = _get_or_create_site_enrollment_token()
    import hmac as _hmac
    if not t or not _hmac.compare_digest(t, expected):
        return 'Invalid or missing site token.', 403

    ps1_src = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(ps1_src):
        return 'install_agent.ps1 not found on server.', 404

    tracker_url = RMM_TRACKER_URL.rstrip('/')
    gateway_url = RMM_GATEWAY_PUBLIC.rstrip('/')
    site_token  = expected

    ps1 = open(ps1_src, encoding='utf-8-sig').read()

    # ── Strip #Requires and <# ... #> comment block ───────────────────────
    ps1 = _re.sub(r'#Requires[^\n]*\n', '', ps1)
    ps1 = _re.sub(r'<#.*?#>', '', ps1, flags=_re.DOTALL)

    # ── Strip the param( ... ) block ──────────────────────────────────────
    # iex evaluates expressions, not script definitions — param() is invalid.
    # Match 'param' then everything up to the matching closing paren on its own line.
    ps1 = _re.sub(r'\bparam\s*\(.*?\n\)', '', ps1, flags=_re.DOTALL)

    # ── Prepend direct variable assignments ───────────────────────────────
    header = (
        "# Cirque RMM Agent - auto-configured by tracker server\n"
        "# Run as Administrator. Logs: C:\\CirqueRMM\\logs\\setup.log\n"
        f"$SiteToken   = '{site_token}'\n"
        f"$TrackerUrl  = '{tracker_url}'\n"
        f"$GatewayUrl  = '{gateway_url}'\n"
        "$Token       = ''\n"
        "$InstallDir  = 'C:\\CirqueRMM'\n"
        "$NssmPath    = 'C:\\Program Files\\NSSM\\nssm.exe'\n"
        "$AgentId     = ''\n"
        "$SkipDownload = $false\n\n"
    )
    ps1 = header + ps1.lstrip()

    # ── Scrub non-ASCII so PowerShell 5.1 never chokes on encoding ────────
    ps1 = ps1.replace('\u2192', '->').replace('\u2014', '--').replace('\u2013', '-')
    ps1 = _re.sub(r'[^\x00-\x7f]', '-', ps1)

    from flask import Response
    # No BOM — irm returns a plain string; BOM confuses the scriptblock parser
    return Response(ps1.encode('utf-8'),
                    mimetype='text/plain; charset=utf-8',
                    headers={'Content-Disposition': 'inline; filename="CirqueRMM-Install.ps1"'})


@bp.route('/download/deploy-silent.ps1')
def download_deploy_silent_ps1():
    """Serve a silent deployment wrapper script (no site-token auth needed — designed
    to be uploaded to GPO / Intune / PSRemoting as a SYSTEM-run startup script).

    Checks if the service already exists before doing anything.
    Creates a hidden scheduled task that runs the installer as SYSTEM so
    no console window is ever visible to the logged-in user.
    The task self-deletes after the installer completes.
    Auth: site token embedded in the URL (?t=TOKEN) same as site-install.ps1.
    """
    import hmac as _hmac
    t = request.args.get('t', '').strip()
    expected = _get_or_create_site_enrollment_token()
    if not t or not _hmac.compare_digest(t, expected):
        return 'Invalid or missing site token.', 403

    tracker_url  = RMM_TRACKER_URL.rstrip('/')
    installer_url = f"{tracker_url}/download/site-install.ps1?t={expected}"

    script = f"""# Cirque RMM Agent - Silent Mass Deployment Script
# Safe for GPO Startup Scripts, Intune Script Policies, or PSRemoting.
# Runs as SYSTEM - no console window is shown to users.
# If the agent is already installed this script exits immediately.

$ServiceName  = 'CirqueRMM'
$TaskName     = 'CirqueRMM-SilentInstall'
$InstallerUrl = '{installer_url}'

# Already installed? Nothing to do.
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {{
    Write-Host "CirqueRMM already installed - exiting."
    exit 0
}}

# Build an Argument string that downloads and runs the installer completely hidden
$psArgs = "-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -Command " +
    "`"& {{ [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " +
    "irm '$InstallerUrl' | iex }}`""

# Create a one-time scheduled task that runs as SYSTEM (guaranteed no window)
$action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs
$trigger   = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(10)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# Start immediately rather than waiting for the trigger
Start-ScheduledTask -TaskName $TaskName

Write-Host "Cirque RMM silent install task created and started on $env:COMPUTERNAME."
Write-Host "The agent will enroll and start within 1-2 minutes."
Write-Host "Check C:\\CirqueRMM\\logs\\setup.log for progress."
"""

    from flask import Response
    return Response(script.encode('utf-8'),
                    mimetype='text/plain; charset=utf-8',
                    headers={'Content-Disposition': 'inline; filename="CirqueRMM-Deploy-Silent.ps1"'})


def _get_or_create_site_enrollment_token() -> str:
    """Return the site-wide RMM enrollment token, creating it if it doesn't exist."""
    import secrets as _sec
    setting = Setting.query.filter_by(key='rmm_site_enrollment_token').first()
    if not setting or not setting.value:
        if not setting:
            setting = Setting(key='rmm_site_enrollment_token')
            db.session.add(setting)
        setting.value = 'site_' + _sec.token_hex(32)
        db.session.commit()
    return setting.value


@bp.route('/api/rmm/enroll', methods=['POST'])
def rmm_enroll():
    """Self-registration endpoint for new agents.

    The installer PS1 (embedded in the MSI) sends the site-wide enrollment token
    here on first install.  The server creates (or finds) an asset and an rmm_agent
    row, then returns a fresh per-device token that the PS1 writes to agent.conf.

    Request JSON:
        site_token  – site-wide enrollment token (from Settings → RMM)
        hostname    – the device's computer name (becomes asset name)
        agent_id    – optional override; defaults to hostname

    Response JSON:
        token       – per-device credential for ongoing agent auth
        agent_id    – the agent ID the token is bound to
        asset_id    – tracker asset ID
    """
    import hashlib as _hl, secrets as _sec
    data = request.get_json(silent=True) or {}
    site_token = data.get('site_token', '').strip()
    hostname   = data.get('hostname', '').strip()
    agent_id   = (data.get('agent_id') or hostname).strip()

    if not site_token or not hostname:
        return jsonify({'ok': False, 'error': 'site_token and hostname required'}), 400

    # Validate site token
    expected = _get_or_create_site_enrollment_token()
    if not hmac.compare_digest(site_token, expected):
        return jsonify({'ok': False, 'error': 'Invalid enrollment token'}), 403

    if not agent_id:
        return jsonify({'ok': False, 'error': 'hostname required'}), 400

    try:
        now = datetime.utcnow().isoformat()

        # Find or create an asset for this device.
        # Match by hostname FIRST — serials like "TobefilledbyO.E.M." are identical
        # across many machines and cannot be used as a reliable unique key.
        _GARBAGE_SERIALS = {
            'tobefilled', 'tobefilledbyoem', 'tobefillbyoem',
            'default string', 'system serial number',
            'not specified', 'none', 'n/a', 'na', 'o.e.m.', '',
        }
        def _is_garbage_serial(s: str) -> bool:
            return s.lower().replace(' ', '').replace('.', '').replace('-', '') in _GARBAGE_SERIALS

        asset = Asset.query.filter(Asset.name.ilike(hostname)).first()
        if not asset and not _is_garbage_serial(agent_id):
            # Only fall back to serial match when the serial is a real unique value
            asset = Asset.query.filter(Asset.serial_number == agent_id).first()
        if not asset:
            asset = Asset(
                asset_tag=f'RMM-{agent_id[:8].upper()}',
                name=hostname,
                category='Workstation',
                device_type='Windows Workstation',
                status='In Use',
            )
            db.session.add(asset)
            db.session.flush()  # get asset.id

        # Generate a fresh per-device token
        raw_token = 'agent_' + _sec.token_hex(32)
        token_hash = _hl.sha256(raw_token.encode()).hexdigest()

        existing = db.session.execute(
            text("SELECT id FROM rmm_agent WHERE agent_id = :aid"),
            {'aid': agent_id}
        ).fetchone()
        if existing:
            db.session.execute(
                text("""UPDATE rmm_agent
                        SET agent_token_sha256 = :hash, asset_id = :asid,
                            last_seen_at = :now
                        WHERE agent_id = :aid"""),
                {'hash': token_hash, 'asid': asset.id, 'now': now, 'aid': agent_id}
            )
        else:
            db.session.execute(
                text("""INSERT INTO rmm_agent
                        (agent_id, asset_id, agent_token_sha256, enabled, created_at, last_seen_at)
                        VALUES (:aid, :asid, :hash, TRUE, :now, :now)"""),
                {'aid': agent_id, 'asid': asset.id, 'hash': token_hash, 'now': now}
            )
        db.session.commit()

        logger.info(f"RMM self-enrollment: agent_id={agent_id} hostname={hostname} asset_id={asset.id}")
        return jsonify({'ok': True, 'token': raw_token, 'agent_id': agent_id, 'asset_id': asset.id})

    except Exception as e:
        logger.error(f"RMM enroll error: {e}")
        return jsonify({'ok': False, 'error': 'Server error during enrollment'}), 500


@bp.route('/download/agent-file/<path:filename>')
def download_agent_file(filename):
    """Serve individual agent files for the installer.

    Auth: either a logged-in browser session OR ?t=<site_enrollment_token>.
    This allows the PS1 one-liner (irm | iex) to download files without a session.
    """
    import os, posixpath, hmac as _hmac
    # Whitelist allowed files (prevent path traversal)
    allowed = {
        'agent_client.py', 'agent_launcher.py', 'tray.py',
        'requirements.txt', 'version.txt',
        'cirque_icon_ico.b64', 'cirque_icon_png.b64',
    }
    clean = posixpath.basename(filename)
    if clean not in allowed:
        return "File not available.", 404

    # Accept site token in ?t= (for PS1 one-liner) or a valid login session
    t = request.args.get('t', '').strip()
    if t:
        expected = _get_or_create_site_enrollment_token()
        if not _hmac.compare_digest(t, expected):
            return "Invalid token.", 403
    elif not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.url))

    path = os.path.join(current_app.root_path, 'rmm_agent', clean)
    if not os.path.exists(path):
        return f"{clean} not found on server.", 404
    return send_file(path, as_attachment=False, mimetype='text/plain')


@bp.route('/rmm/agent/launcher')
def rmm_agent_launcher():
    """Serve agent_launcher.py (self-healing wrapper). Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    launcher_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'agent_launcher.py')
    return send_file(launcher_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/rmm/agent/repair')
def rmm_agent_repair():
    """Serve agent_repair.ps1. Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    repair_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'agent_repair.ps1')
    return send_file(repair_path, mimetype='text/plain', as_attachment=False)


@bp.route('/rmm/agent/tray')
def rmm_agent_tray():
    """Serve tray.py to authenticated agents."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    tray_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'tray.py')
    if not os.path.isfile(tray_path):
        return jsonify({'error': 'tray.py not found on server'}), 404
    return send_file(tray_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/rmm/agent/tray-install')
def rmm_agent_tray_install():
    """Serve tray_install.py — authenticated by agent token or browser session."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    # Accept either a valid agent token OR an active browser session
    if not (current_user.is_authenticated or
            (agent_id and token and _verify_agent_token(agent_id, token))):
        return jsonify({'error': 'Unauthorized'}), 401
    path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'tray_install.py')
    if not os.path.isfile(path):
        return jsonify({'error': 'tray_install.py not found'}), 404
    return send_file(path, mimetype='text/x-python',
                     as_attachment=True, download_name='tray_install.py')


@bp.route('/api/rmm/last-scan/<agent_id>', methods=['POST'])
@login_required
def api_rmm_last_scan(agent_id):
    """Persist the last AV scan time into security_json."""
    import json as _json
    data = request.get_json(force=True) or {}
    scan_type = data.get('scan_type', 'quick')
    scan_time = data.get('scan_time', '')
    row = db.session.execute(
        text("SELECT security_json FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'No telemetry row'}), 404
    try:
        sec = _json.loads(row[0] or '{}')
    except Exception:
        sec = {}
    sec['last_scan'] = {'type': scan_type, 'time': scan_time}
    db.session.execute(
        text("UPDATE rmm_telemetry SET security_json = :sj WHERE agent_id = :aid"),
        {'sj': _json.dumps(sec), 'aid': agent_id}
    )
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/api/rmm/metrics-history/<agent_id>')
@login_required
def api_rmm_metrics_history(agent_id):
    """Return CPU/RAM history for the last N hours (default 24)."""
    hours = int(request.args.get('hours', 24))
    rows = db.session.execute(
        text("""SELECT captured_at, cpu_percent, ram_percent
                FROM rmm_metrics_history
                WHERE agent_id = :aid
                  AND captured_at >= NOW() + CAST(:delta AS INTERVAL)
                ORDER BY captured_at ASC"""),
        {'aid': agent_id, 'delta': f'-{hours} hours'}
    ).fetchall()
    return jsonify({
        'ok': True,
        'hours': hours,
        'data': [{'ts': _dt_iso(r[0]), 'cpu': r[1], 'ram': r[2]} for r in rows],
    })


@bp.route('/api/rmm/availability/<agent_id>')
@login_required
def api_rmm_availability(agent_id):
    """Return recent online/offline events for an agent."""
    limit = int(request.args.get('limit', 100))
    rows = db.session.execute(
        text("""SELECT event, occurred_at
                FROM rmm_availability
                WHERE agent_id = :aid
                ORDER BY occurred_at DESC
                LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit}
    ).fetchall()
    return jsonify({
        'ok': True,
        'events': [{'event': r[0], 'ts': _dt_iso(r[1])} for r in rows],
    })


@bp.route('/api/rmm/patches/<agent_id>')
@login_required
def api_rmm_patches(agent_id):
    """Return installed Windows hotfixes for an agent."""
    rows = db.session.execute(
        text("""SELECT hotfix_id, description, installed_on
                FROM rmm_patch
                WHERE agent_id = :aid
                ORDER BY installed_on DESC"""),
        {'aid': agent_id}
    ).fetchall()
    return jsonify({
        'ok': True,
        'count': len(rows),
        'patches': [{'id': r[0], 'description': r[1], 'installed_on': r[2]} for r in rows],
    })


@bp.route('/api/rmm/pending-updates/<agent_id>')
@login_required
def api_rmm_pending_updates(agent_id):
    """List available (not yet installed) Windows Updates reported by the agent."""
    rows = db.session.execute(
        text("""SELECT update_id, title, kb_ids, severity, size_mb,
                       reboot_required, category, recorded_at
                FROM rmm_pending_update
                WHERE agent_id = :aid
                ORDER BY
                    CASE severity
                        WHEN 'Critical'  THEN 1 WHEN 'Important' THEN 2
                        WHEN 'Moderate'  THEN 3 WHEN 'Low'       THEN 4 ELSE 5
                    END, title"""),
        {'aid': agent_id}
    ).fetchall()
    import json as _json

    def _parse_kb_ids(raw):
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        s = str(raw).strip()
        # PostgreSQL array format: {KB1234,KB5678}
        if s.startswith('{') and s.endswith('}'):
            return [x.strip() for x in s[1:-1].split(',') if x.strip()]
        try:
            return _json.loads(s)
        except (_json.JSONDecodeError, ValueError):
            return [s]

    return jsonify({
        'ok': True,
        'count': len(rows),
        'updates': [{
            'update_id':       r[0], 'title':    r[1],
            'kb_ids':          _parse_kb_ids(r[2]),
            'severity':        r[3], 'size_mb':  r[4],
            'reboot_required': bool(r[5]),
            'category':        r[6], 'recorded_at': r[7],
        } for r in rows],
    })


@bp.route('/api/rmm/session-events/<agent_id>')
@login_required
def api_rmm_session_events(agent_id):
    """Return session activity events (logon/logoff/lock/unlock/sleep/wake) for an agent."""
    days = request.args.get('days', 7, type=int)
    rows = db.session.execute(
        text("""SELECT event_type, username, event_time
                FROM rmm_session_events
                WHERE agent_id = :aid
                  AND captured_at >= NOW() - (:days * INTERVAL '1 day')
                ORDER BY event_time DESC NULLS LAST"""),
        {'aid': agent_id, 'days': min(days, 90)}
    ).fetchall()
    events = [{'type': r[0], 'username': r[1], 'time': r[2]} for r in rows]
    return jsonify({'ok': True, 'events': events})


@bp.route('/api/rmm/software/<agent_id>')
@login_required
def api_rmm_software(agent_id):
    """Return installed software inventory for an agent."""
    rows = db.session.execute(
        text("""SELECT name, version, publisher, install_date
                FROM rmm_software
                WHERE agent_id = :aid
                ORDER BY lower(name)"""),
        {'aid': agent_id}
    ).fetchall()
    software = [{'name': r[0], 'version': r[1], 'publisher': r[2], 'install_date': r[3]} for r in rows]
    return jsonify({'ok': True, 'software': software, 'count': len(software)})


@bp.route('/api/rmm/patch-jobs/<agent_id>', methods=['GET'])
@login_required
def api_rmm_patch_jobs_get(agent_id):
    """List patch deployment jobs for an agent."""
    import json as _json
    rows = db.session.execute(
        text("""SELECT id, update_ids, kb_ids, titles, status,
                       approved_by, approved_at, deployed_at, completed_at,
                       result_json, reboot_required, created_at, updated_at
                FROM rmm_patch_job
                WHERE agent_id = :aid
                ORDER BY id DESC LIMIT 30"""),
        {'aid': agent_id}
    ).fetchall()
    return jsonify({
        'ok': True,
        'jobs': [{
            'id':              r[0],
            'update_ids':      _json.loads(r[1] or '[]'),
            'kb_ids':          _json.loads(r[2] or '[]'),
            'titles':          _json.loads(r[3] or '[]'),
            'status':          r[4],
            'approved_by':     r[5],
            'approved_at':     r[6],
            'deployed_at':     r[7],
            'completed_at':    r[8],
            'result':          _json.loads(r[9]) if r[9] else None,
            'reboot_required': bool(r[10]),
            'created_at':      r[11],
            'updated_at':      r[12],
        } for r in rows],
    })


@bp.route('/api/rmm/patch-jobs/<agent_id>', methods=['POST'])
@login_required
def api_rmm_patch_jobs_create(agent_id):
    """Approve a set of pending updates, creating a queued patch job."""
    import json as _json
    data       = request.get_json() or {}
    update_ids = data.get('update_ids') or []
    kb_ids     = data.get('kb_ids') or []
    titles     = data.get('titles') or []
    if not update_ids:
        return jsonify({'ok': False, 'error': 'update_ids required'}), 400
    db.session.execute(
        text("""INSERT INTO rmm_patch_job
                    (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
                VALUES (:aid, :uids, :kbids, :titles, 'queued', :uid, NOW() - INTERVAL '7 hours')
                RETURNING id"""),
        {
            'aid':    agent_id,
            'uids':   _json.dumps(update_ids),
            'kbids':  _json.dumps(kb_ids),
            'titles': _json.dumps(titles),
            'uid':    current_user.id if hasattr(current_user, 'id') else None,
        }
    )
    db.session.commit()
    job_id = db.session.execute(text("SELECT lastval()")).scalar()
    return jsonify({'ok': True, 'job_id': job_id})


@bp.route('/api/rmm/cmd/<agent_id>', methods=['POST'])
@login_required
def api_rmm_cmd(agent_id):
    """Proxy any JSON message to the connected agent via gateway send-msg."""
    import json as _json, urllib.request as _req, urllib.error as _err
    data = request.get_json(force=True) or {}
    session_id = data.get('session_id') or 0
    if not session_id:
        try:
            agent_row = db.session.execute(
                text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = true LIMIT 1"),
                {'aid': agent_id}
            ).fetchone()
            asset_id = agent_row[0] if agent_row else None
            res = db.session.execute(
                text("INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at) VALUES (:aid, :uid, :reason, NOW()) RETURNING id"),
                {'aid': asset_id, 'uid': current_user.id, 'reason': data.get('type', 'cmd')}
            )
            db.session.commit()
            session_id = res.scalar()
        except Exception:
            session_id = 0
    data['session_id'] = session_id
    payload = _json.dumps(data).encode()
    try:
        req = _req.Request(
            f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
            data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return jsonify({'ok': False, 'session_id': session_id, 'error': result.get('error', 'Gateway error')}), 502
    except _err.HTTPError as e:
        try:
            body = _json.loads(e.read())
            msg = body.get('error') or body.get('detail') or str(e)
        except Exception:
            msg = str(e)
        return jsonify({'ok': False, 'session_id': session_id, 'error': msg}), 502
    except Exception as e:
        return jsonify({'ok': False, 'session_id': session_id, 'error': str(e)}), 502
    return jsonify({'ok': True, 'session_id': session_id})


@bp.route('/api/rmm/cmd-result/<agent_id>/<int:session_id>')
@login_required
def api_rmm_cmd_result(agent_id, session_id):
    """Poll for latest agent response in rmm_event for a given session."""
    import json as _json
    row = db.session.execute(
        text("""SELECT event_type, data_json, created_at
                FROM rmm_event WHERE session_id = :sid AND actor_type = 'agent'
                ORDER BY id DESC LIMIT 1"""),
        {'sid': session_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'ready': False})
    try:
        data = _json.loads(row[1] or '{}')
    except Exception:
        data = {}
    return jsonify({'ok': True, 'ready': True, 'event_type': row[0], 'data': data, 'created_at': row[2]})


@bp.route('/api/rmm/deploy-rustdesk/<agent_id>', methods=['POST'])
@login_required
@manager_required
def api_rmm_deploy_rustdesk(agent_id):
    """Send a PowerShell script to the agent to install RustDesk via winget
    and configure it to use the internal relay server."""
    import json as _json, urllib.request as _req

    # Find the linked asset so we can update rustdesk_id after install
    row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = true LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'agent not found'}), 404

    server   = 'rust.corp.cirque.com'
    key      = 's18RB+OV0ctX3SuOIcXy6EeYqA+Elx25RODTGYBnyV8='

    # PowerShell: install RustDesk silently, then write server config
    ps = r"""
$ErrorActionPreference = 'Stop'
Write-Host 'Installing RustDesk via winget...'
try {
    winget install --id RustDesk.RustDesk --silent --accept-source-agreements --accept-package-agreements 2>&1
} catch { Write-Host "winget error: $_" }

Start-Sleep -Seconds 5

$cfg2 = @"
rendezvous_server = '{server}'
nat_type = 1
serial = 0

[options]
custom-rendezvous-server = '{server}'
key = '{key}'
relay-server = ''
api-server = ''
"@

# Write config for current user and for SYSTEM (service)
$paths = @(
    "$env:APPDATA\RustDesk\config",
    "$env:ProgramData\RustDesk\config",
    "C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config",
    "C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config"
)
foreach ($dir in $paths) {
    try {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $cfg2 | Set-Content -Path "$dir\RustDesk2.toml" -Encoding UTF8 -Force
        Write-Host "Wrote config to $dir"
    } catch { Write-Host "Skipped $dir: $_" }
}

# Restart RustDesk service if running
try {
    $svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    if ($svc) { Restart-Service -Name 'RustDesk' -Force; Write-Host 'RustDesk service restarted' }
} catch { Write-Host "Service restart: $_" }

# Start RustDesk briefly to trigger peer ID generation, then close it
try {
    $rd = "$env:ProgramFiles\RustDesk\RustDesk.exe"
    if (-not (Test-Path $rd)) { $rd = "${env:ProgramFiles(x86)}\RustDesk\RustDesk.exe" }
    if (Test-Path $rd) {
        Start-Process $rd --minimized -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 8
        Stop-Process -Name 'RustDesk' -ErrorAction SilentlyContinue
    }
} catch { Write-Host "RustDesk launch: $_" }

# Read and report the peer ID so the tracker can store it
try {
    $tomlPaths = @(
        "$env:APPDATA\RustDesk\config\RustDesk.toml",
        "$env:ProgramData\RustDesk\config\RustDesk.toml",
        "C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config\RustDesk.toml"
    )
    foreach ($tp in $tomlPaths) {
        if (Test-Path $tp) {
            $raw = Get-Content $tp -Raw -ErrorAction SilentlyContinue
            if ($raw -match '(?m)^id\s*=\s*(\S+)') {
                Write-Host "RUSTDESK_ID=$($Matches[1].Trim())"
                break
            }
        }
    }
} catch { Write-Host "ID read: $_" }

Write-Host 'Done.'
""".replace('{server}', server).replace('{key}', key)

    try:
        session_row = db.session.execute(
            text("INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at) VALUES (:aid, :uid, 'Deploy RustDesk', NOW() - INTERVAL '7 hours') RETURNING id"),
            {'aid': row[0], 'uid': current_user.id}
        )
        db.session.commit()
        session_id = session_row.scalar()
    except Exception:
        session_id = 0

    payload = _json.dumps({'type': 'run_script', 'shell': 'powershell', 'code': ps, 'timeout': 180, 'session_id': session_id}).encode()
    try:
        req = _req.Request(f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
                           data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        with _req.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return jsonify({'ok': False, 'error': result.get('error', 'Gateway error')}), 502
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

    return jsonify({'ok': True, 'session_id': session_id})


@bp.route('/api/rmm/patch-jobs/<agent_id>/<int:job_id>/deploy', methods=['POST'])
@login_required
def api_rmm_patch_jobs_deploy(agent_id, job_id):
    """Push a queued patch job to the connected agent via the gateway."""
    import json as _json, urllib.request as _req, urllib.error as _err
    row = db.session.execute(
        text("SELECT update_ids, kb_ids, titles, status FROM rmm_patch_job WHERE id = :jid AND agent_id = :aid"),
        {'jid': job_id, 'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    if row[3] not in ('queued', 'failed'):
        return jsonify({'ok': False, 'error': f'Job is already {row[3]}'}), 400

    payload = _json.dumps({
        'job_id':     job_id,
        'update_ids': _json.loads(row[0] or '[]'),
        'kb_ids':     _json.loads(row[1] or '[]'),
        'titles':     _json.loads(row[2] or '[]'),
    }).encode()

    gw = RMM_GATEWAY_INTERNAL
    try:
        req = _req.Request(
            f"{gw}/deploy-patches/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return jsonify({'ok': False, 'error': result.get('error', 'Gateway error')}), 502
    except _err.HTTPError as e:
        body = e.read().decode()
        return jsonify({'ok': False, 'error': f'Gateway {e.code}: {body}'}), 502
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

    # Mark as deploying
    db.session.execute(
        text("UPDATE rmm_patch_job SET status='deploying', deployed_at=NOW() - INTERVAL '7 hours', updated_at=NOW() - INTERVAL '7 hours' WHERE id=:jid"),
        {'jid': job_id}
    )
    db.session.commit()
    return jsonify({'ok': True, 'message': 'Deploy command sent to agent'})


@bp.route('/api/rmm/rustdesk-sync/<agent_id>', methods=['POST'])
def api_rmm_rustdesk_sync(agent_id):
    """RMM agent reports its RustDesk peer ID so the tracker can auto-populate
    asset.rustdesk_id without manual entry.

    Auth: agent_id + token as query params (same as other agent endpoints).
    Body JSON: { "rustdesk_id": "<10-digit peer id>", "rustdesk_password": "<optional>" }
    """
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    peer_id = (data.get('rustdesk_id') or '').strip()
    if not peer_id:
        return jsonify({'ok': False, 'error': 'rustdesk_id is required'}), 400

    # Find the asset linked to this agent
    row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = true LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row or not row[0]:
        return jsonify({'ok': False, 'error': 'agent not linked to an asset'}), 404

    asset = Asset.query.get(row[0])
    if not asset:
        return jsonify({'ok': False, 'error': 'asset not found'}), 404

    changed = asset.rustdesk_id != peer_id
    asset.rustdesk_id = peer_id

    optional_pw = (data.get('rustdesk_password') or '').strip()
    if optional_pw:
        asset.rustdesk_password = optional_pw

    if changed:
        db.session.add(AssetHistory(
            asset_id=asset.id,
            action='RustDesk ID Updated',
            description=f'RMM agent auto-synced RustDesk peer ID: {peer_id}',
            user_id=None
        ))

    db.session.commit()
    return jsonify({'ok': True, 'asset_id': asset.id, 'changed': changed})


@bp.route('/api/rmm/agent-info/<agent_id>')
def api_rmm_agent_info(agent_id):
    """Return asset info for a given agent (authenticated by token).
    Used by the tray app setup to populate tray_config.json."""
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    row = db.session.execute(
        text("SELECT a.id, a.asset_tag, a.name FROM rmm_agent ra LEFT JOIN asset a ON a.id = ra.asset_id WHERE ra.agent_id = :aid AND ra.enabled = true LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'agent not found'}), 404
    return jsonify({'ok': True, 'asset_id': row[0], 'asset_tag': row[1] or '', 'hostname': row[2] or ''})


def _eagle_report_scheduler():
    """Check report schedules every 15 minutes and send due emails."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    def _is_due(freq, dow, send_time, last_sent):
        """Return True if this schedule should fire now."""
        now = datetime.utcnow()
        h, m = (int(x) for x in (send_time or '08:00').split(':'))
        if now.hour != h or now.minute > m + 15:
            return False
        if last_sent and (now - last_sent).total_seconds() < 3600:
            return False  # already sent within the last hour
        if freq == 'daily':
            return True
        if freq == 'weekly' and now.isoweekday() == int(dow or 1):
            return True
        if freq == 'monthly' and now.day == 1:
            return True
        return False

    def _build_eagle_report(agent_id):
        """Build a plain-text Eagle Eyes summary for a given agent (or all)."""
        with app.app_context():
            where = "WHERE agent_id = :aid" if agent_id else ""
            params = {'aid': agent_id} if agent_id else {}
            rows = db.session.execute(text(f"""
                SELECT agent_id, event_type, description, captured_at
                FROM rmm_eagle_event
                {where}
                ORDER BY captured_at DESC LIMIT 50
            """), params).mappings().fetchall()
            if not rows:
                return None, "No Eagle Eyes events in the last period."
            lines = [f"Eagle Eyes Report — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n"]
            for r in rows:
                lines.append(f"  [{r['captured_at']}] {r['agent_id']} – {r['event_type']}: {r['description']}")
            return rows[0]['agent_id'], "\n".join(lines)

    while True:
        try:
            _time.sleep(900)  # check every 15 minutes
            with app.app_context():
                schedules = db.session.execute(text("""
                    SELECT id, agent_id, frequency, day_of_week, send_time, email_to, last_sent_at
                    FROM rmm_eagle_report_schedule WHERE enabled = true
                """)).mappings().fetchall()

                for sch in schedules:
                    last = datetime.fromisoformat(sch['last_sent_at']) if sch['last_sent_at'] else None
                    if not _is_due(sch['frequency'], sch['day_of_week'], sch['send_time'], last):
                        continue
                    subject_agent, body = _build_eagle_report(sch['agent_id'])
                    if not body:
                        continue
                    try:
                        msg = MIMEMultipart('alternative')
                        msg['Subject'] = f"Eagle Eyes Report — {subject_agent or 'All Agents'}"
                        msg['From'] = current_app.config.get('MAIL_DEFAULT_SENDER', 'assettracker@cirque.com')
                        msg['To'] = sch['email_to']
                        msg.attach(MIMEText(body, 'plain'))
                        with smtplib.SMTP(current_app.config.get('MAIL_SERVER', '10.15.0.4'),
                                          current_app.config.get('MAIL_PORT', 25)) as smtp:
                            smtp.sendmail(msg['From'], [sch['email_to']], msg.as_string())
                        db.session.execute(text(
                            "UPDATE rmm_eagle_report_schedule SET last_sent_at = :ts WHERE id = :id"
                        ), {'ts': datetime.utcnow().isoformat(), 'id': sch['id']})
                        db.session.commit()
                        logger.info(f"Eagle Eyes report sent to {sch['email_to']} (schedule {sch['id']})")
                    except Exception as _mail_err:
                        logger.warning(f"Eagle Eyes email failed for schedule {sch['id']}: {_mail_err}")
        except Exception as _sched_err:
            logger.warning(f'Eagle report scheduler error: {_sched_err}')