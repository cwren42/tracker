"""Agent telemetry/ingestion routes for the RMM blueprint (screenshot,
system-info, telemetry, software inventory). Split from blueprints/rmm.py.
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


from blueprints.rmm import bp, _verify_agent_token


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


