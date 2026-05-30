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


@bp.route('/api/rmm/screenshot/<agent_id>', methods=['POST'])
@login_required
def api_rmm_screenshot_request(agent_id):
    """Ask the gateway to request a screenshot from the agent.
    The gateway must have an agent session open.
    POSTs a JSON command to the gateway internal HTTP endpoint.
    """
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
                 ram_available_gb, disk_json, captured_at, last_seen)
            VALUES
                (:aid, :asid, :cpu, :ram, :ramt, :rava, :dj, :ts, NOW())
            ON CONFLICT(agent_id) DO UPDATE SET
                cpu_percent=excluded.cpu_percent,
                ram_percent=excluded.ram_percent,
                ram_total_gb=excluded.ram_total_gb,
                ram_available_gb=excluded.ram_available_gb,
                disk_json=excluded.disk_json,
                captured_at=excluded.captured_at,
                last_seen=NOW()
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
def download_agent_ps1():
    """Serve the raw PowerShell installer script directly."""
    import os
    path = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')


@bp.route('/download/agent-bat')
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

    # ?public=1 bypasses the internal URL entirely — use for devices that
    # can resolve but not reach the corporate network (e.g. China offices).
    use_public = request.args.get('public', '').strip() in ('1', 'true', 'yes')
    if use_public:
        tracker_url = 'https://tracker.cirquetools.com'
        gateway_url = 'wss://rmm.cirquetools.com'
    else:
        tracker_url = RMM_TRACKER_URL.rstrip('/')
        gateway_url = RMM_GATEWAY_PUBLIC.rstrip('/')
    tracker_url_fallback = 'https://tracker.cirquetools.com'
    gateway_url_fallback = 'wss://rmm.cirquetools.com'
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
        f"$SiteToken          = '{site_token}'\n"
        f"$TrackerUrl         = '{tracker_url}'\n"
        f"$TrackerUrlFallback = '{tracker_url_fallback}'\n"
        f"$GatewayUrl         = '{gateway_url}'\n"
        f"$GatewayUrlFallback = '{gateway_url_fallback}'\n"
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


@bp.route('/api/rmm/agent/<agent_id>/remove', methods=['POST'])
@login_required
def rmm_remove_agent(agent_id):
    """Remove an RMM agent and all associated data. Admin only."""
    if current_user.role != 'admin':
        return jsonify(ok=False, error='Admin required'), 403
    try:
        child_tables = [
            'alert_log', 'cve_patch_job', 'device_vulnerability',
            'eagle_eyes_exclusions', 'linux_agent_heartbeat',
            'rmm_agent_flags', 'rmm_availability', 'rmm_commands',
            'rmm_connect_token', 'rmm_eagle_alert_log', 'rmm_eagle_alert_rule',
            'rmm_eagle_config', 'rmm_eagle_current', 'rmm_eagle_event',
            'rmm_eagle_report_schedule', 'rmm_metrics_history', 'rmm_patch',
            'rmm_patch_job', 'rmm_pending_update', 'rmm_screenshot',
            'rmm_session_events', 'rmm_software', 'rmm_telemetry',
        ]
        for tbl in child_tables:
            db.session.execute(text(f"DELETE FROM {tbl} WHERE agent_id = :aid"), {'aid': agent_id})
        db.session.execute(text("DELETE FROM rmm_agent WHERE agent_id = :aid"), {'aid': agent_id})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500


@bp.route('/rmm/agent/launcher')
def rmm_agent_launcher():
    """Serve agent_launcher.py (self-healing wrapper). Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    launcher_path = os.path.join(current_app.root_path, 'rmm_agent', 'agent_launcher.py')
    if not os.path.exists(launcher_path):
        return jsonify({'error': 'launcher not found'}), 404
    return send_file(launcher_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/rmm/agent/repair')
def rmm_agent_repair():
    """Serve agent_repair.ps1. Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    repair_path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'agent_repair.ps1')
    return send_file(repair_path, mimetype='text/plain', as_attachment=False)


@bp.route('/rmm/agent/tray')
def rmm_agent_tray():
    """Serve tray.py to authenticated agents."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    tray_path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'tray.py')
    if not os.path.isfile(tray_path):
        return jsonify({'error': 'tray.py not found on server'}), 404
    return send_file(tray_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/rmm/agent/tray-sha')
def rmm_agent_tray_sha():
    """Return SHA-256 of tray.py so agents can detect updates without downloading the full file."""
    import hashlib as _hl
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    tray_path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'tray.py')
    if not os.path.isfile(tray_path):
        return jsonify({'error': 'tray.py not found'}), 404
    with open(tray_path, 'rb') as f:
        sha = _hl.sha256(f.read()).hexdigest()
    return jsonify({'sha256': sha})


@bp.route('/rmm/agent/tray-install')
def rmm_agent_tray_install():
    """Serve tray_install.py — authenticated by agent token or browser session."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    # Accept either a valid agent token OR an active browser session
    if not (current_user.is_authenticated or
            (agent_id and token and _verify_agent_token(agent_id, token))):
        return jsonify({'error': 'Unauthorized'}), 401
    path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'tray_install.py')
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
    key      = 'u2i12pLeK9MQJH8h3S4FeKtPVRt75gXyR6Rbj20LKOo='

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
        'type':       'install_patches',
        'job_id':     job_id,
        'update_ids': _json.loads(row[0] or '[]'),
        'kb_ids':     _json.loads(row[1] or '[]'),
        'titles':     _json.loads(row[2] or '[]'),
    }).encode()

    gw = RMM_GATEWAY_INTERNAL
    try:
        req = _req.Request(
            f"{gw}/send-msg/{agent_id}",
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

# ── Route groups split into sibling modules (registered on bp above) ──
from blueprints import rmm_terminal, rmm_eagle  # noqa: E402,F401
