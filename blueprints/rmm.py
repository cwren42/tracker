import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
import requests
from werkzeug.utils import secure_filename

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   make_response, redirect, render_template, request,
                   send_file, session, url_for)
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
# LAN URL uses the internal Cirque Corp CA cert (trusted by domain-joined machines).
# Public URL uses Cloudflare (valid for all browsers, including off-network).
RMM_GATEWAY_PUBLIC   = os.environ.get('RMM_GATEWAY_URL', 'wss://rmm.cirquetools.com')
RMM_GATEWAY_LAN      = os.environ.get('RMM_GATEWAY_URL_LAN', 'wss://rmm.corp.cirque.com')
RMM_TRACKER_URL      = os.environ.get('RMM_TRACKER_URL', 'https://tracker.corp.cirque.com')

# RFC-1918 prefixes that indicate a LAN client
_LAN_PREFIXES = ('10.', '172.16.', '172.17.', '172.18.', '172.19.',
                 '172.20.', '172.21.', '172.22.', '172.23.', '172.24.',
                 '172.25.', '172.26.', '172.27.', '172.28.', '172.29.',
                 '172.30.', '172.31.', '192.168.', '127.')


def _gateway_url_for_request():
    """Return the WebSocket gateway URL for browser clients.
    LAN clients (RFC-1918 source IPs) use rmm.corp.cirque.com — the cert is issued
    by the Cirque Corp CA which domain-joined machines trust.
    External / off-network clients fall back to the Cloudflare public URL.
    """
    client_ip = (
        request.headers.get('X-Real-IP') or
        request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
        request.remote_addr or ''
    )
    if any(client_ip.startswith(p) for p in _LAN_PREFIXES):
        return RMM_GATEWAY_LAN
    return RMM_GATEWAY_PUBLIC


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


# SSH web-terminal routes moved to blueprints/rmm_terminal.py (registered on bp
# via the import at the bottom of this file).


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

    agent_dir = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent')
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
    agent_dir = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent')
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

    agent_path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'agent_client.py')
    return send_file(agent_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/api/rmm/agent/heartbeat')
def rmm_agent_heartbeat():
    """Lightweight pull-based heartbeat — called by the agent every 5 minutes.

    Works independently of the WebSocket gateway, so it still works when the
    gateway is broken or the connection is dropping.  The response carries:
      - action: 'none' | 'force_update' | 'restart' | 'reinstall'
      - pending_commands: list of {id, command, command_type} rows to execute
    The agent is expected to POST /api/rmm/agent/command_result for each one.
    Also updates last_seen_at so the dashboard reflects the agent as online.
    Also checks whether > 30% of the fleet went offline in the last 15 minutes
    and fires an admin alert email if so (fleet crash detection).
    """
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401

    now_iso = datetime.utcnow().isoformat(timespec='seconds')

    # Touch last_seen_at on rmm_agent and last_seen on rmm_telemetry
    try:
        db.session.execute(
            text("UPDATE rmm_agent SET last_seen_at = NOW() WHERE agent_id = :aid"),
            {'aid': agent_id}
        )
        db.session.execute(
            text("UPDATE rmm_telemetry SET last_seen = NOW() WHERE agent_id = :aid"),
            {'aid': agent_id}
        )
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Determine action from rmm_commands table (highest-priority pending control command)
    action = 'none'
    ctrl_row = None
    try:
        ctrl_row = db.session.execute(
            text("""SELECT id, command FROM rmm_commands
                    WHERE agent_id = :aid AND command_type = 'control'
                      AND status = 'pending'
                    ORDER BY id ASC LIMIT 1"""),
            {'aid': agent_id}
        ).fetchone()
        if ctrl_row:
            action = ctrl_row.command  # e.g. 'force_update', 'restart', 'reinstall'
            db.session.execute(
                text("UPDATE rmm_commands SET status = 'dispatched', executed_at = NOW() WHERE id = :id"),
                {'id': ctrl_row.id}
            )
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Fetch pending shell/script commands (non-control)
    pending = []
    try:
        rows = db.session.execute(
            text("""SELECT id, command, command_type FROM rmm_commands
                    WHERE agent_id = :aid AND command_type != 'control'
                      AND status = 'pending'
                    ORDER BY id ASC LIMIT 5"""),
            {'aid': agent_id}
        ).fetchall()
        pending = [{'id': r.id, 'command': r.command, 'command_type': r.command_type} for r in rows]
        if pending:
            ids = [r['id'] for r in pending]
            db.session.execute(
                text("UPDATE rmm_commands SET status = 'dispatched', executed_at = NOW() WHERE id = ANY(:ids)"),
                {'ids': ids}
            )
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Fleet crash detection: if a deploy happened recently and > 30% are now offline, alert
    try:
        last_alert_key = 'rmm_fleet_crash_last_alert'
        last_alert_row = db.session.execute(
            text("SELECT value FROM setting WHERE key = :k"), {'k': last_alert_key}
        ).fetchone()
        last_alert_ts = 0
        if last_alert_row:
            try:
                last_alert_ts = float(last_alert_row.value)
            except Exception:
                pass

        import time as _time
        if _time.time() - last_alert_ts > 900:  # at most once per 15 minutes
            total, offline = db.session.execute(
                text("""SELECT COUNT(*), COUNT(*) FILTER (
                          WHERE last_seen_at < NOW() - INTERVAL '10 minutes'
                            OR last_seen_at IS NULL)
                        FROM rmm_agent WHERE enabled = true""")
            ).fetchone()
            if total and total > 0 and offline / total >= 0.30:
                try:
                    from utils import send_admin_notification
                    send_admin_notification(
                        'ALERT: RMM Fleet Offline',
                        f'<p><strong>Fleet crash detected:</strong> {offline}/{total} agents '
                        f'({int(offline/total*100)}%) have been offline for &gt;10 minutes.</p>'
                        f'<p>Check the <a href="/rmm">RMM dashboard</a> and consider rolling back '
                        f'the agent version if a recent deploy caused this.</p>'
                    )
                    # Record alert time so we don't spam
                    db.session.execute(
                        text("""INSERT INTO setting (key, value, updated_at)
                                VALUES (:k, :v, NOW())
                                ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()"""),
                        {'k': last_alert_key, 'v': str(_time.time())}
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
    except Exception:
        pass

    return jsonify({'action': action, 'pending_commands': pending, 'ts': now_iso})


@bp.route('/api/rmm/agent/command_result', methods=['POST'])
def rmm_agent_command_result():
    """Agent posts the result of a dispatched command back to the server."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(force=True) or {}
    cmd_id    = data.get('id')
    result    = data.get('result', '')
    exit_code = data.get('exit_code', 0)

    if not cmd_id:
        return jsonify({'error': 'id required'}), 400

    try:
        db.session.execute(
            text("""UPDATE rmm_commands SET status = 'completed', result = :res,
                    exit_code = :ec, completed_at = NOW()
                    WHERE id = :id AND agent_id = :aid"""),
            {'res': str(result)[:4000], 'ec': exit_code, 'id': cmd_id, 'aid': agent_id}
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'ok': False}), 500

    return jsonify({'ok': True})


@bp.route('/api/rmm/admin/send-command', methods=['POST'])
@login_required
def api_rmm_send_command():
    """Send a control or shell command to an agent.

    Body: {agent_id, command, command_type}
    command_type = 'control'  -> action word: force_update | restart | reinstall
    command_type = 'shell'    -> arbitrary PowerShell/cmd string (agent executes + returns result)

    Strategy: try the WebSocket gateway first (immediate delivery). If the agent
    is not connected to the gateway, fall back to the rmm_commands queue (picked
    up on the agent's next 5-min heartbeat).
    """
    if current_user.role != 'admin':
        return jsonify(ok=False, error='Admin required'), 403

    data         = request.get_json(force=True) or {}
    agent_id     = data.get('agent_id', '').strip()
    command      = data.get('command', '').strip()
    command_type = data.get('command_type', 'control').strip()

    if not agent_id or not command:
        return jsonify(ok=False, error='agent_id and command required'), 400
    if command_type not in ('control', 'shell', 'powershell'):
        return jsonify(ok=False, error='Invalid command_type'), 400
    # Prevent obviously dangerous shell injections from UI
    if command_type in ('shell', 'powershell') and len(command) > 2000:
        return jsonify(ok=False, error='Command too long'), 400

    # --- Try WebSocket gateway first (immediate) ---
    # Map shell commands and control actions to the appropriate gateway message type.
    gateway_ok = False
    try:
        import urllib.request as _ur, json as _json, ssl as _ssl
        gw_url = 'http://127.0.0.1:8765/send-msg/' + agent_id

        if command_type in ('shell', 'powershell'):
            gw_payload = {'type': 'run_script', 'shell': 'powershell', 'code': command, 'session_id': 0}
        else:
            # control: force_update / restart / reinstall -> power_action restart,
            # or a dedicated control message understood by the launcher.
            # Use power_action for restart; queue the rest (launcher handles on heartbeat).
            if command == 'restart':
                gw_payload = {'type': 'power_action', 'action': 'restart', 'session_id': 0}
            else:
                gw_payload = None  # force_update / reinstall still go via queue

        if gw_payload:
            body = _json.dumps(gw_payload).encode()
            req = _ur.Request(gw_url, data=body,
                              headers={'Content-Type': 'application/json'},
                              method='POST')
            with _ur.urlopen(req, timeout=3) as r:
                resp = _json.loads(r.read())
            gateway_ok = resp.get('ok', False)
    except Exception:
        gateway_ok = False

    if gateway_ok:
        return jsonify(ok=True, delivered='websocket')

    # --- Fallback: queue in rmm_commands (picked up on next heartbeat) ---
    try:
        db.session.execute(
            text("""INSERT INTO rmm_commands (agent_id, command, command_type, status, created_at)
                    VALUES (:aid, :cmd, :ct, 'pending', NOW())"""),
            {'aid': agent_id, 'cmd': command, 'ct': command_type}
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500

    return jsonify(ok=True, delivered='queue')


@bp.route('/api/rmm/admin/agent-version', methods=['GET', 'POST'])
@login_required
def api_rmm_admin_agent_version():
    """GET: return current published version info.
    POST {action:'rollback'}: revert version.txt to the previously known-good version.
    POST {action:'validate'}: run pre-publish checks on current agent files without changing anything.
    POST {version:'x.y.z'}: manually set the published version string.
    """
    if current_user.role != 'admin':
        return jsonify(ok=False, error='Admin required'), 403

    import ast as _ast, hashlib as _hl
    agent_dir  = os.path.join(current_app.root_path, 'rmm_agent')
    ver_path   = os.path.join(agent_dir, 'version.txt')
    prev_path  = os.path.join(agent_dir, 'version.txt.prev')
    client_py  = os.path.join(agent_dir, 'agent_client.py')
    launcher_py = os.path.join(agent_dir, 'agent_launcher.py')

    def _validate_agent_file(path):
        """Return list of validation errors for a Python agent file."""
        errors = []
        try:
            raw = open(path, 'rb').read()
        except OSError as e:
            return [f'Cannot read file: {e}']
        # Syntax check
        try:
            _ast.parse(raw)
        except SyntaxError as e:
            errors.append(f'SyntaxError line {e.lineno}: {e.msg}')
        # ASCII-only check (catches cp932/cp1252 Unicode crashes)
        bad_chars = [(i+1, hex(ord(c)), repr(c))
                     for i, c in enumerate(raw.decode('utf-8', errors='replace'))
                     if ord(c) > 127]
        if bad_chars:
            sample = bad_chars[:5]
            errors.append(f'{len(bad_chars)} non-ASCII character(s) found: {sample}')
        return errors

    if request.method == 'GET':
        cur_ver = open(ver_path).read().strip() if os.path.exists(ver_path) else 'unknown'
        prev_ver = open(prev_path).read().strip() if os.path.exists(prev_path) else None
        client_cksum = _hl.sha256(open(client_py,'rb').read()).hexdigest()[:16] if os.path.exists(client_py) else None
        errs = _validate_agent_file(client_py) + _validate_agent_file(launcher_py)
        return jsonify(ok=True, version=cur_ver, prev_version=prev_ver,
                       client_checksum=client_cksum, validation_errors=errs)

    data = request.get_json(force=True) or {}
    action = data.get('action', '').strip()

    if action == 'validate':
        errs = _validate_agent_file(client_py) + _validate_agent_file(launcher_py)
        return jsonify(ok=True, valid=(len(errs) == 0), errors=errs)

    if action == 'rollback':
        if not os.path.exists(prev_path):
            return jsonify(ok=False, error='No previous version on file to roll back to'), 404
        prev_ver = open(prev_path).read().strip()
        cur_ver  = open(ver_path).read().strip() if os.path.exists(ver_path) else ''
        # Swap: write prev over current, save current as prev
        with open(ver_path, 'w') as f:
            f.write(prev_ver)
        with open(prev_path, 'w') as f:
            f.write(cur_ver)
        logger.info(f'Agent version rolled back from {cur_ver} to {prev_ver} by {current_user.email}')
        return jsonify(ok=True, rolled_back_to=prev_ver, previous_was=cur_ver)

    new_ver = data.get('version', '').strip()
    if new_ver:
        # Validate files before accepting a version bump
        errs = _validate_agent_file(client_py) + _validate_agent_file(launcher_py)
        if errs:
            return jsonify(ok=False, error='Validation failed — fix errors before publishing',
                           validation_errors=errs), 422
        cur_ver = open(ver_path).read().strip() if os.path.exists(ver_path) else ''
        if cur_ver:
            with open(prev_path, 'w') as f:
                f.write(cur_ver)
        with open(ver_path, 'w') as f:
            f.write(new_ver)
        logger.info(f'Agent version set to {new_ver} by {current_user.email}')
        return jsonify(ok=True, version=new_ver, prev_version=cur_ver)

    return jsonify(ok=False, error='Provide action or version'), 400


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
        'gateway_url': _gateway_url_for_request(),
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
    resp = make_response(render_template('rmm_terminal.html',
        agent_id=agent_id,
        asset_name=asset_name,
        asset_id=asset_id,
        gateway_url=_gateway_url_for_request(),
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


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

# ── Route groups split into sibling modules (registered on bp above) ──
from blueprints import rmm_terminal, rmm_eagle, rmm_agent_data, rmm_agent_install, rmm_agent_ingest  # noqa: E402,F401
