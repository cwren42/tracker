"""Agent-data API routes for the RMM blueprint (metrics, patches, commands,
rustdesk). Split out of blueprints/rmm.py; routes register on the same
'rmm' blueprint so URLs/endpoint names are unchanged.
"""
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
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
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


from blueprints.rmm import bp, _dt_iso, _verify_agent_token


@bp.route('/api/rmm/last-scan/<agent_id>', methods=['POST'])
@login_required
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
            # Agent not live on the gateway — DO NOT mark 'deploying' into the void.
            # The job row stays 'queued'; the gateway reconnect flush will deliver it
            # the next time this agent connects.
            return jsonify({'ok': False, 'error': result.get('error', 'Gateway error'),
                            'queued': True}), 502
    except _err.HTTPError as e:
        body = e.read().decode()
        return jsonify({'ok': False, 'error': f'Gateway {e.code}: {body}', 'queued': True}), 502
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'queued': True}), 502

    # Confirmed send to a live agent → mark as deploying.
    db.session.execute(
        text("UPDATE rmm_patch_job SET status='deploying', deployed_at=NOW(), updated_at=NOW() WHERE id=:jid"),
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


