import base64
import csv
import io
import json
import os
import re
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
try:
    import qrcode
except ImportError:
    qrcode = None
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
    MonitoringAlert, MonitoringCheck, MonitoringProfile, AssetMonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
    AssetLoan, InstalledApp, RmmBackupPolicy, RmmAgentBackupPolicy,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, RMM_GATEWAY_PUBLIC, RMM_TRACKER_URL,
)
logger = logging.getLogger(__name__)
from api_system import require_api_key


bp = Blueprint('assets', __name__)


def _rmm_cascade_delete(agent_id):
    """Delete all RMM data for an agent_id before removing the agent row."""
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


def _asset_cascade_delete(asset_id):
    """Nullify or delete all FK references to asset.id before deleting the asset row.
    Does NOT handle rmm_agent — call _rmm_cascade_delete first for that."""
    # Tables where asset_id can safely be nulled (ticket/loan/session history should be kept)
    nullable_tables = [
        'support_ticket',
        'asset_loan',
        'remote_session',
        'rmm_session',
        'license_assignment',
        'monitoring_alert',
        'intune_device',
        'rmm_enrollment_tokens',
    ]
    for tbl in nullable_tables:
        db.session.execute(
            text(f"UPDATE {tbl} SET asset_id = NULL WHERE asset_id = :aid"),
            {'aid': asset_id}
        )
    # Tables where rows are meaningless without the asset — delete them
    delete_tables = [
        'asset_history',
        'asset_maintenance_window',
        'asset_monitoring_profile',
        'installed_app',
        'monitoring_check_history',
    ]
    for tbl in delete_tables:
        db.session.execute(
            text(f"DELETE FROM {tbl} WHERE asset_id = :aid"),
            {'aid': asset_id}
        )


# ==================== BULK OPERATIONS ROUTES ====================


# ═══════════════════════════════════════════════════════════════════════════════
# RESTORED ROUTES (recovered from git HEAD)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Restored: /assets/<int:asset_id>/checkout ──


# ── Restored: /assets/<int:asset_id>/checkin/<int:loan_id> ──


# ── Software inventory (agent → server) ─────────────────────────────────────



# ── Restored: /api/asset/<int:asset_id>/software ──


# ── Global search ────────────────────────────────────────────────────────────



# ── Restored: /search ──


# ── Per-asset installer: generates a pre-configured PS1 with one-time enrollment token ──



# ─── Asset Lifecycle EOL Auto-Ticket ─────────────────────────────────────────



@bp.route('/api/asset/search')
@login_required
@license_required
def api_asset_search():
    """API endpoint to search for asset by tag (for QR scanner)"""
    asset_tag = request.args.get('tag', '')
    
    if not asset_tag:
        return jsonify({'success': False, 'message': 'Asset tag required'}), 400
    
    asset = Asset.query.filter_by(asset_tag=asset_tag).first()
    
    if asset:
        return jsonify({
            'success': True,
            'asset_id': asset.id,
            'asset_tag': asset.asset_tag,
            'name': asset.name,
            'category': asset.category,
            'status': asset.status
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Asset not found'
        }), 404


@bp.route('/assets')
@login_required
@manager_required
@license_required
def assets():
    search = request.args.get('search', '')
    category = request.args.get('category', '')  # backward-compat only (links from other pages)
    status = request.args.get('status', '')
    location_filter = request.args.get('location', '')
    device_type_filter = request.args.get('device_type', '')
    availability_filter = request.args.get('availability', '')
    lifecycle = request.args.get('lifecycle', '')
    purchase_from = request.args.get('purchase_from', '')
    purchase_to = request.args.get('purchase_to', '')
    warranty_status = request.args.get('warranty_status', '')
    quick_filter = request.args.get('filter', '')  # Quick filter from dashboard
    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')
    
    query = Asset.query
    
    # Text search
    if search:
        query = query.filter(
            (Asset.name.ilike(f'%{search}%')) |
            (Asset.asset_tag.ilike(f'%{search}%')) |
            (Asset.serial_number.ilike(f'%{search}%')) |
            (Asset.manufacturer.ilike(f'%{search}%'))
        )
    
    # Category (backward-compat for external links using ?category=)
    if category:
        query = query.filter_by(category=category)
    
    # Status filter
    if status:
        query = query.filter_by(status=status)

    # Location filter
    if location_filter:
        query = query.filter(Asset.location == location_filter)

    # Device type filter
    if device_type_filter:
        query = query.filter(Asset.device_type == device_type_filter)

    # Date range filters
    if purchase_from:
        try:
            from_date = datetime.strptime(purchase_from, '%Y-%m-%d')
            query = query.filter(Asset.purchase_date >= from_date)
        except ValueError:
            pass
    
    if purchase_to:
        try:
            to_date = datetime.strptime(purchase_to, '%Y-%m-%d')
            query = query.filter(Asset.purchase_date <= to_date)
        except ValueError:
            pass
    
    # Apply sorting
    sort_column = {
        'asset_tag': Asset.asset_tag,
        'name': Asset.name,
        'category': Asset.category,
        'manufacturer': Asset.manufacturer,
        'serial_number': Asset.serial_number,
        'status': Asset.status,
        'purchase_date': Asset.purchase_date
    }.get(sort_by, Asset.asset_tag)
    
    if sort_dir == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
    
    # Get assets for warranty and lifecycle filtering
    all_assets = query.all()
    
    # Warranty status filter
    if warranty_status:
        filtered_assets = []
        today = datetime.utcnow().date()  # Convert to date for comparison
        
        for asset in all_assets:
            if warranty_status == 'active' and asset.warranty_expiry:
                if asset.warranty_expiry > today:
                    filtered_assets.append(asset)
            elif warranty_status == 'expiring' and asset.warranty_expiry:
                days_until = (asset.warranty_expiry - today).days
                if 0 < days_until <= 30:
                    filtered_assets.append(asset)
            elif warranty_status == 'expired' and asset.warranty_expiry:
                if asset.warranty_expiry <= today:
                    filtered_assets.append(asset)
            elif warranty_status == 'none' and not asset.warranty_expiry:
                filtered_assets.append(asset)
        
        all_assets = filtered_assets
    
    # Lifecycle status filter
    if lifecycle:
        filtered_assets = [asset for asset in all_assets 
                          if asset.purchase_date and asset.expected_life_years 
                          and asset.get_lifecycle_status() == lifecycle]
        all_assets = filtered_assets
    
    # Quick filter from dashboard
    if quick_filter:
        today = datetime.utcnow().date()
        if quick_filter == 'noncompliant':
            all_assets = [asset for asset in all_assets if asset.online_state == 'noncompliant']
        elif quick_filter == 'low_storage':
            all_assets = [asset for asset in all_assets 
                         if asset.hardware_storage_total_gb and asset.hardware_storage_free_gb
                         and (asset.hardware_storage_free_gb / asset.hardware_storage_total_gb * 100) < 20]
        elif quick_filter == 'offline':
            cutoff = datetime.utcnow() - timedelta(days=7)
            all_assets = [asset for asset in all_assets if asset.last_seen and asset.last_seen < cutoff]
        elif quick_filter == 'warranty_expiring':
            all_assets = [asset for asset in all_assets 
                         if asset.warranty_expiry and today < asset.warranty_expiry <= (today + timedelta(days=60))]
        elif quick_filter == 'unassigned':
            all_assets = [asset for asset in all_assets if not asset.employee_id]
        elif quick_filter == 'with_agent':
            agent_asset_ids = {row[1] for row in db.session.execute(
                text("SELECT agent_id, asset_id FROM rmm_agent WHERE enabled = true AND asset_id IS NOT NULL")
            ).fetchall()}
            all_assets = [asset for asset in all_assets if asset.id in agent_asset_ids]
        elif quick_filter == 'without_agent':
            agent_asset_ids = {row[1] for row in db.session.execute(
                text("SELECT agent_id, asset_id FROM rmm_agent WHERE enabled = true AND asset_id IS NOT NULL")
            ).fetchall()}
            all_assets = [asset for asset in all_assets if asset.id not in agent_asset_ids]
        elif quick_filter == 'network_devices':
            all_assets = [asset for asset in all_assets
                          if asset.category and asset.category.lower() in (
                              'network', 'network device', 'switch', 'router',
                              'firewall', 'access point', 'ap', 'unifi', 'ubiquiti'
                          )]
    
    assets = all_assets
    # categories var removed — no longer passed to template

    # Build RMM online/offline sets based on last_seen_at (same 5-min logic as api_rmm_agent_status)
    # Also query gateway for live WebSocket-connected agents (covers Windows agents that don't POST telemetry)
    cutoff = datetime.utcnow() - timedelta(seconds=300)
    rmm_rows = db.session.execute(
        text("SELECT agent_id, asset_id, last_seen_at FROM rmm_agent WHERE enabled = true AND asset_id IS NOT NULL")
    ).fetchall()

    # Get live gateway connections
    gateway_online = set()
    try:
        gw_resp = requests.get(f'{RMM_GATEWAY_INTERNAL}/agents', timeout=2)
        gateway_online = set(gw_resp.json().get('agents', []))
    except Exception:
        pass

    rmm_asset_ids = set()
    rmm_online_ids = set()
    agent_id_to_asset_id = {}
    for row in rmm_rows:
        agent_id, asset_id, last_seen_at = row[0], row[1], row[2]
        rmm_asset_ids.add(asset_id)
        agent_id_to_asset_id[agent_id] = asset_id
        # Online if: gateway has live WS connection, OR last_seen_at within 5 min
        if agent_id in gateway_online:
            rmm_online_ids.add(asset_id)
        elif last_seen_at:
            if isinstance(last_seen_at, str):
                try:
                    last_seen_at = datetime.fromisoformat(last_seen_at)
                except ValueError:
                    last_seen_at = None
            if last_seen_at and last_seen_at > cutoff:
                rmm_online_ids.add(asset_id)

    # Build per-asset patch counts and reboot flags from rmm_pending_update
    patch_counts = {}   # asset_id -> int
    reboot_flags = {}   # asset_id -> bool
    try:
        patch_rows = db.session.execute(
            text("""
                SELECT ra.asset_id, COUNT(rpu.id), BOOL_OR(rpu.reboot_required)
                FROM rmm_agent ra
                JOIN rmm_pending_update rpu ON rpu.agent_id = ra.agent_id
                WHERE ra.enabled = true AND ra.asset_id IS NOT NULL
                GROUP BY ra.asset_id
            """)
        ).fetchall()
        for asset_id, count, reboot in patch_rows:
            patch_counts[asset_id] = count
            reboot_flags[asset_id] = bool(reboot)
    except Exception:
        pass

    # Build per-asset vulnerability counts (open Critical + High CVEs)
    vuln_counts = {}    # asset_id -> {'critical': int, 'high': int, 'total': int}
    try:
        vuln_rows = db.session.execute(
            text("""
                SELECT asset_id, severity, COUNT(*)
                FROM device_vulnerability
                WHERE status = 'Open'
                GROUP BY asset_id, severity
            """)
        ).fetchall()
        for asset_id, severity, count in vuln_rows:
            if asset_id not in vuln_counts:
                vuln_counts[asset_id] = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'total': 0}
            sev = severity.lower() if severity else 'low'
            if sev in vuln_counts[asset_id]:
                vuln_counts[asset_id][sev] += count
            vuln_counts[asset_id]['total'] += count
    except Exception:
        pass

    monitoring_profiles = MonitoringProfile.query.filter_by(enabled=True).order_by(MonitoringProfile.name).all()
    backup_policies = RmmBackupPolicy.query.filter_by(enabled=True).order_by(RmmBackupPolicy.name).all()

    # Availability filter (applied after rmm_online_ids is resolved)
    if availability_filter:
        if availability_filter == 'online':
            assets = [a for a in assets if a.id in rmm_online_ids or a.online_state == 'Online']
        elif availability_filter == 'offline':
            assets = [a for a in assets if
                      (a.id in rmm_asset_ids and a.id not in rmm_online_ids) or
                      (a.unifi_device_id and a.online_state != 'Online')]

    # Build per-asset logged-in user from rmm_telemetry
    logged_in_users = {}  # asset_id -> username string
    try:
        user_rows = db.session.execute(
            text("SELECT asset_id, logged_in_user FROM rmm_telemetry WHERE asset_id IS NOT NULL AND logged_in_user IS NOT NULL AND logged_in_user != ''")
        ).fetchall()
        for asset_id, username in user_rows:
            logged_in_users[asset_id] = username
    except Exception:
        pass

    return render_template('assets.html', assets=assets,
                           rmm_asset_ids=rmm_asset_ids, rmm_online_ids=rmm_online_ids,
                           patch_counts=patch_counts, reboot_flags=reboot_flags,
                           vuln_counts=vuln_counts,
                           monitoring_profiles=monitoring_profiles,
                           backup_policies=backup_policies,
                           location_filter=location_filter,
                           device_type_filter=device_type_filter,
                           logged_in_users=logged_in_users,
                           asset_agent_ids={v: k for k, v in agent_id_to_asset_id.items()})


@bp.route('/assets/add', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def add_asset():
    if request.method == 'POST':
        # Handle photo upload
        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Add timestamp to filename to avoid conflicts
                photo_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
                os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], photo_filename))
        
        purchase_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date() if request.form.get('purchase_date') else None
        expected_life_years = int(request.form.get('expected_life_years', 3))
        
        # Auto-calculate replacement date if purchase date is provided
        replacement_date = None
        if purchase_date and expected_life_years:
            replacement_date = datetime(purchase_date.year + expected_life_years, purchase_date.month, purchase_date.day).date()
        elif request.form.get('replacement_date'):
            replacement_date = datetime.strptime(request.form.get('replacement_date'), '%Y-%m-%d').date()
        
        asset = Asset(
            asset_tag=request.form.get('asset_tag'),
            name=request.form.get('name'),
            category=request.form.get('category'),
            manufacturer=request.form.get('manufacturer'),
            model=request.form.get('model'),
            serial_number=request.form.get('serial_number'),
            purchase_date=purchase_date,
            purchase_cost=float(request.form.get('purchase_cost')) if request.form.get('purchase_cost') else None,
            warranty_expiry=datetime.strptime(request.form.get('warranty_expiry'), '%Y-%m-%d').date() if request.form.get('warranty_expiry') else None,
            status=request.form.get('status', 'Available'),
            location=request.form.get('location'),
            notes=request.form.get('notes'),
            photo=photo_filename,
            expected_life_years=expected_life_years,
            replacement_date=replacement_date,
            condition=request.form.get('condition', 'Good'),
            rustdesk_id=(request.form.get('rustdesk_id') or '').strip() or None
        )
        
        db.session.add(asset)
        db.session.commit()
        
        # Add history entry
        history = AssetHistory(
            asset_id=asset.id,
            action='Created',
            description=f'Asset {asset.asset_tag} created',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash(f'Asset {asset.asset_tag} added successfully!', 'success')
        return redirect(url_for('assets.assets'))
    
    employees = Employee.query.all()
    return render_template('add_asset.html', employees=employees)


@bp.route('/assets/<int:asset_id>')
@login_required
@manager_required
@license_required
def view_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    history = AssetHistory.query.filter_by(asset_id=asset_id).order_by(AssetHistory.timestamp.desc()).all()
    employees = Employee.query.all()
    
    # Checkout / loan data
    active_loan = AssetLoan.query.filter_by(asset_id=asset_id, checked_in_at=None).first()
    loan_history = AssetLoan.query.filter_by(asset_id=asset_id).order_by(AssetLoan.checked_out_at.desc()).all()

    # Look up RMM agent for this asset
    rmm_agent_id = None
    rmm_tele = None
    try:
        rmm_row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = true LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()
        if rmm_row:
            rmm_agent_id = rmm_row[0]
            # Fetch latest telemetry for server-side rendering (Device Identity card, etc.)
            tele_row = db.session.execute(
                text("SELECT * FROM rmm_telemetry WHERE agent_id = :aid"),
                {'aid': rmm_agent_id}
            ).fetchone()
            if tele_row:
                rmm_tele = tele_row._mapping
    except Exception:
        pass

    # Fetch current monitoring profile for this asset
    monitoring_profile = None
    try:
        mp_row = db.session.execute(
            text("""
                SELECT mp.id, mp.name, mp.device_type, mp.description
                FROM monitoring_profile mp
                JOIN asset_monitoring_profile amp ON amp.profile_id = mp.id
                WHERE amp.asset_id = :aid
                LIMIT 1
            """),
            {'aid': asset_id}
        ).fetchone()
        if mp_row:
            monitoring_profile = mp_row._mapping
    except Exception:
        pass

    all_profiles = MonitoringProfile.query.filter_by(enabled=True).order_by(MonitoringProfile.name).all()

    # Vulnerability badge count (open Critical + High + others)
    vuln_count = 0
    try:
        row = db.session.execute(
            text("SELECT COUNT(*) FROM device_vulnerability WHERE asset_id=:aid AND status='Open'"),
            {'aid': asset_id}
        ).fetchone()
        if row:
            vuln_count = row[0]
    except Exception:
        pass

    return render_template('view_asset.html', asset=asset, history=history, employees=employees,
                         now=datetime.utcnow,
                         active_loan=active_loan, loan_history=loan_history,
                         rmm_agent_id=rmm_agent_id, rmm_tele=rmm_tele,
                         monitoring_profile=monitoring_profile, all_profiles=all_profiles,
                         vuln_count=vuln_count)


@bp.route('/assets/<int:asset_id>/rmm/<section>')
@login_required
def rmm_section(asset_id, section):
    """Full-page view for an individual RMM management section."""
    ALLOWED = {'hw', 'sec', 'sysinfo', 'metrics', 'avail', 'patches',
               'software', 'scripts', 'services', 'events', 'transfer', 'power'}
    if section not in ALLOWED:
        abort(404)

    asset = Asset.query.get_or_404(asset_id)

    rmm_agent_id = None
    try:
        row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = true LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()
        if row:
            rmm_agent_id = row[0]
    except Exception:
        pass

    SECTION_LABELS = {
        'hw': 'Hardware', 'sec': 'Security', 'sysinfo': 'System',
        'metrics': 'Metrics', 'avail': 'Activity', 'patches': 'Patch Management',
        'software': 'Software', 'scripts': 'Scripts', 'services': 'Services',
        'events': 'Events', 'transfer': 'File Transfer', 'power': 'Power',
    }

    return render_template('rmm_section.html',
                           asset=asset,
                           rmm_agent_id=rmm_agent_id,
                           section=section,
                           section_label=SECTION_LABELS.get(section, section.title()))


@bp.route('/assets/<int:asset_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def edit_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    if asset.id == 108:
        asset.manufacturer = 'Dell'
        asset.model = 'XPS 15 9520'
        asset.serial_number = 'GJBBLR3'
        db.session.commit()
    
    if request.method == 'POST':
        old_status = asset.status
        
        # Handle photo upload
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename != '' and allowed_file(file.filename):
                # Delete old photo if exists
                if asset.photo:
                    old_photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], asset.photo)
                    if os.path.exists(old_photo_path):
                        os.remove(old_photo_path)
                
                filename = secure_filename(file.filename)
                photo_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
                os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], photo_filename))
                asset.photo = photo_filename
        
        asset.asset_tag = request.form.get('asset_tag')
        asset.name = request.form.get('name')
        asset.category = request.form.get('category')
        asset.manufacturer = request.form.get('manufacturer')
        asset.model = request.form.get('model')
        asset.serial_number = request.form.get('serial_number')
        asset.purchase_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date() if request.form.get('purchase_date') else None
        asset.purchase_cost = float(request.form.get('purchase_cost')) if request.form.get('purchase_cost') else None
        asset.warranty_expiry = datetime.strptime(request.form.get('warranty_expiry'), '%Y-%m-%d').date() if request.form.get('warranty_expiry') else None
        asset.status = request.form.get('status')
        asset.location = request.form.get('location')
        asset.notes = request.form.get('notes')
        asset.rustdesk_id = (request.form.get('rustdesk_id') or '').strip() or None
        asset.service_urls = request.form.get('service_urls')
        asset.expected_life_years = int(request.form.get('expected_life_years', 3))
        asset.replacement_date = datetime.strptime(request.form.get('replacement_date'), '%Y-%m-%d').date() if request.form.get('replacement_date') else None
        asset.condition = request.form.get('condition', 'Good')
        asset.device_type = request.form.get('device_type')
        asset.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Add history entry
        if old_status != asset.status:
            history = AssetHistory(
                asset_id=asset.id,
                action='Status Changed',
                description=f'Status changed from {old_status} to {asset.status}',
                user_id=current_user.id
            )
            db.session.add(history)
            db.session.commit()
        
        flash(f'Asset {asset.asset_tag} updated successfully!', 'success')
        return redirect(url_for('assets.view_asset', asset_id=asset.id))
    
    employees = Employee.query.all()
    # Check if this asset has an RMM agent (so we can show read-only indicators)
    rmm_agent_id = None
    try:
        rmm_row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = true LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()
        if rmm_row:
            rmm_agent_id = rmm_row[0]
    except Exception:
        pass
    return render_template('edit_asset.html', asset=asset, employees=employees, rmm_agent_id=rmm_agent_id, rmm_linked=bool(rmm_agent_id))


@bp.route('/assets/<int:asset_id>/remote/rustdesk/start', methods=['POST'])
@login_required
@manager_required
@license_required
def start_rustdesk_remote_session(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    payload = request.get_json(silent=True) or {}
    reason = (payload.get('reason') or '').strip()
    if not reason:
        return jsonify({'success': False, 'error': 'reason is required'}), 400
    if not asset.rustdesk_id:
        return jsonify({'success': False, 'error': 'asset has no RustDesk ID configured'}), 400

    session_row = RemoteSession(
        tool='rustdesk',
        asset_id=asset.id,
        started_by_user_id=current_user.id,
        reason=reason,
        started_at=datetime.utcnow()
    )
    db.session.add(session_row)

    history = AssetHistory(
        asset_id=asset.id,
        action='Remote Session Started',
        description=f'RustDesk session started: {reason}',
        user_id=current_user.id
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({
        'success': True,
        'session_id': session_row.id,
        'rustdesk_id': asset.rustdesk_id,
        'rustdesk_password': asset.rustdesk_password,
    })


@bp.route('/remote-sessions/<int:session_id>/end', methods=['POST'])
@login_required
@manager_required
@license_required
def end_remote_session(session_id):
    session_row = RemoteSession.query.get_or_404(session_id)
    if session_row.ended_at is not None:
        return jsonify({'success': True})

    session_row.ended_at = datetime.utcnow()
    session_row.ended_by_user_id = current_user.id

    history = AssetHistory(
        asset_id=session_row.asset_id,
        action='Remote Session Ended',
        description=f'{session_row.tool.title()} session ended',
        user_id=current_user.id
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({'success': True})


@bp.route('/assets/<int:asset_id>/assign', methods=['POST'])
@login_required
@manager_required
@license_required
def assign_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    employee_id = request.form.get('employee_id')
    
    if employee_id:
        employee = Employee.query.get(employee_id)
        old_employee = asset.assigned_employee
        
        asset.employee_id = employee_id
        asset.status = 'In Use'
        db.session.commit()
        
        # Add history entry
        history = AssetHistory(
            asset_id=asset.id,
            action='Assigned',
            description=f'Asset assigned to {employee.name}',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash(f'Asset assigned to {employee.name}', 'success')
    
    return redirect(url_for('assets.view_asset', asset_id=asset.id))


@bp.route('/assets/<int:asset_id>/unassign', methods=['POST'])
@login_required
@manager_required
@license_required
def unassign_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    
    if asset.employee_id:
        employee_name = asset.assigned_employee.name
        asset.employee_id = None
        asset.status = 'Available'
        db.session.commit()
        
        # Add history entry
        history = AssetHistory(
            asset_id=asset.id,
            action='Unassigned',
            description=f'Asset unassigned from {employee_name}',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash('Asset unassigned successfully', 'success')
    
    return redirect(url_for('assets.view_asset', asset_id=asset.id))


@bp.route('/assets/<int:asset_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_asset(asset_id):
    
    asset = Asset.query.get_or_404(asset_id)
    asset_tag = asset.asset_tag

    # Remove any RMM agent (and all child data) linked to this asset
    agent_row = db.session.execute(
        text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid"), {'aid': asset_id}
    ).mappings().fetchone()
    if agent_row:
        _rmm_cascade_delete(agent_row['agent_id'])

    # Nullify/delete all other FK references to this asset
    _asset_cascade_delete(asset_id)

    db.session.delete(asset)
    db.session.commit()
    
    flash(f'Asset {asset_tag} deleted successfully', 'success')
    return redirect(url_for('assets.assets'))


@bp.route('/assets/find-duplicates')
@login_required
@manager_required
@license_required
def find_duplicate_assets():
    """Find assets with duplicate names"""
    from sqlalchemy import func
    
    # Find names that appear more than once
    duplicate_names = db.session.query(
        Asset.name,
        func.count(Asset.id).label('count')
    ).group_by(Asset.name).having(func.count(Asset.id) > 1).all()
    
    duplicates = []
    for name, count in duplicate_names:
        assets = Asset.query.filter_by(name=name).order_by(Asset.created_at).all()
        duplicates.append({
            'name': name,
            'count': count,
            'assets': assets
        })
    
    return render_template('find_duplicates.html', duplicates=duplicates)


@bp.route('/assets/merge-duplicates', methods=['POST'])
@login_required
@manager_required
@license_required
def merge_duplicate_assets():
    """Merge duplicate assets by keeping one and deleting others"""
    try:
        data = request.get_json()
        keep_id = data.get('keep_id')
        delete_ids = data.get('delete_ids', [])
        
        if not keep_id or not delete_ids:
            return jsonify({'success': False, 'message': 'Missing parameters'}), 400
        
        keep_asset = Asset.query.get_or_404(keep_id)
        
        # Collect duplicates and gather data to transfer
        duplicates_to_delete = []
        transfer_serial = None
        transfer_employee_id = None
        transfer_intune_id = None
        
        for delete_id in delete_ids:
            if delete_id != keep_id:
                duplicate = Asset.query.get(delete_id)
                if duplicate:
                    # Collect data from duplicates that we want to keep
                    if not transfer_serial and duplicate.serial_number:
                        transfer_serial = duplicate.serial_number
                    if not transfer_employee_id and duplicate.employee_id:
                        transfer_employee_id = duplicate.employee_id
                    if not transfer_intune_id and duplicate.intune_device_id:
                        transfer_intune_id = duplicate.intune_device_id
                    
                    duplicates_to_delete.append(duplicate)
        
        # Clear unique constraints from duplicates FIRST to avoid conflicts
        for duplicate in duplicates_to_delete:
            duplicate.serial_number = None
            duplicate.asset_tag = f"DEL-{duplicate.id}-{duplicate.asset_tag[:30]}"
        
        db.session.flush()  # Apply the clearing changes
        
        # NOW transfer data to the keep_asset (after duplicates are cleared)
        if not keep_asset.serial_number and transfer_serial:
            keep_asset.serial_number = transfer_serial
        if not keep_asset.employee_id and transfer_employee_id:
            keep_asset.employee_id = transfer_employee_id
        if not keep_asset.intune_device_id and transfer_intune_id:
            keep_asset.intune_device_id = transfer_intune_id
        
        db.session.flush()  # Apply the transfers
        
        # Now delete the duplicates
        deleted_count = 0
        for duplicate in duplicates_to_delete:
            db.session.delete(duplicate)
            deleted_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Kept {keep_asset.name} and deleted {deleted_count} duplicate(s)',
            'redirect': url_for('assets.find_duplicate_assets')
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/assets/<int:asset_id>/update-status', methods=['POST'])
@login_required
@manager_required
@license_required
def update_asset_status(asset_id):
    """Update asset status via inline edit"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        
        if not new_status:
            return jsonify({'success': False, 'message': 'Status is required'}), 400
        
        asset = Asset.query.get_or_404(asset_id)
        old_status = asset.status
        asset.status = new_status
        asset.updated_at = datetime.utcnow()
        
        # Create history entry
        history = AssetHistory(
            asset_id=asset.id,
            action=f'Status changed from {old_status} to {new_status}',
            changed_by=current_user.username,
            timestamp=datetime.utcnow()
        )
        db.session.add(history)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Status updated to {new_status}'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@bp.route('/assets/<int:asset_id>/qr')
@login_required
@license_required
def asset_qr(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(f"{request.host_url}assets/{asset.id}")
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return render_template('qr_code.html', asset=asset, qr_code=img_str)


@bp.route('/assets/bulk/eagle-eyes', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_eagle_eyes():
    """Bulk enable/disable Eagle Eyes for selected assets."""
    data = request.get_json(force=True) or {}
    asset_ids = data.get('asset_ids', [])
    enabled = bool(data.get('enabled', True))
    if not asset_ids:
        return jsonify({'success': False, 'message': 'No assets selected'}), 400
    # Resolve asset_ids -> agent_ids via rmm_agent table
    rows = db.session.execute(
        text("SELECT asset_id, agent_id FROM rmm_agent WHERE asset_id = ANY(:ids) AND enabled = true"),
        {'ids': [int(i) for i in asset_ids]}
    ).fetchall()
    if not rows:
        return jsonify({'success': False, 'message': 'None of the selected assets have an RMM agent enrolled'}), 400
    count = 0
    for row in rows:
        agent_id = row[1]
        db.session.execute(
            text("""INSERT INTO rmm_eagle_config (agent_id, enabled, screenshot_interval_min, updated_at)
                    VALUES (:aid, :en, 30, NOW())
                    ON CONFLICT(agent_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at"""),
            {'aid': agent_id, 'en': enabled}
        )
        count += 1
    db.session.commit()
    # Push updated config to each connected agent via gateway (best-effort)
    import urllib.request as _ur
    payload = json.dumps({'enabled': enabled, 'screenshot_interval_min': 30}).encode()
    for row in rows:
        try:
            req = _ur.Request(
                f"{RMM_GATEWAY_INTERNAL}/eagle-eyes/{row[1]}/push",
                data=payload, headers={'Content-Type': 'application/json'}, method='POST',
            )
            _ur.urlopen(req, timeout=4)
        except Exception:
            pass  # agent offline; config persists and applies on next connect
    state = 'enabled' if enabled else 'disabled'
    return jsonify({'success': True, 'count': count, 'message': f'Eagle Eyes {state} for {count} agent(s)'})


@bp.route('/assets/bulk/status', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_update_status():
    """Bulk update asset status"""
    try:
        data = request.get_json()
        asset_ids = data.get('asset_ids', [])
        new_status = data.get('status')
        
        if not asset_ids or not new_status:
            return jsonify({'success': False, 'message': 'Missing required data'}), 400
        
        # Update assets
        count = 0
        for asset_id in asset_ids:
            asset = Asset.query.get(asset_id)
            if asset:
                old_status = asset.status
                asset.status = new_status
                
                # Log history
                history = AssetHistory(
                    asset_id=asset.id,
                    action=f'Status changed from {old_status} to {new_status} (Bulk update)',
                    user_id=current_user.id
                )
                db.session.add(history)
                count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'count': count,
            'message': f'Successfully updated {count} assets'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/assets/bulk/department', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_assign_department():
    """Bulk assign assets to department"""
    try:
        data = request.get_json()
        asset_ids = data.get('asset_ids', [])
        department = data.get('department')
        
        if not asset_ids or not department:
            return jsonify({'success': False, 'message': 'Missing required data'}), 400
        
        # Update assets
        count = 0
        for asset_id in asset_ids:
            asset = Asset.query.get(asset_id)
            if asset:
                old_dept = asset.department or 'None'
                asset.department = department
                
                # Log history
                history = AssetHistory(
                    asset_id=asset.id,
                    action=f'Department changed from {old_dept} to {department} (Bulk assignment)',
                    user_id=current_user.id
                )
                db.session.add(history)
                count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'count': count,
            'message': f'Successfully assigned {count} assets to {department}'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/assets/bulk/edit', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_edit_assets():
    """Bulk edit asset fields"""
    try:
        data = request.get_json()
        asset_ids = data.get('asset_ids', [])

        if not asset_ids:
            return jsonify({'success': False, 'message': 'No assets selected'}), 400

        editable = ['status', 'category', 'location', 'department', 'device_type', 'notes']
        updates = {k: data[k] for k in editable if k in data and data[k] is not None}

        monitoring_profile_id = data.get('monitoring_profile_id') or None
        backup_policy_id = data.get('backup_policy_id') or None

        if not updates and monitoring_profile_id is None and backup_policy_id is None:
            return jsonify({'success': False, 'message': 'No fields provided'}), 400

        notes_replace = data.get('notes_replace', False)

        count = 0
        for asset_id in asset_ids:
            asset = Asset.query.get(asset_id)
            if not asset:
                continue
            changes = []
            for field, new_val in updates.items():
                old_val = getattr(asset, field, None)
                if field == 'notes' and not notes_replace:
                    combined = (old_val + '\n' + new_val) if old_val else new_val
                    setattr(asset, field, combined)
                    changes.append('notes appended')
                elif str(old_val) != str(new_val):
                    setattr(asset, field, new_val)
                    changes.append(f'{field}: {old_val} → {new_val}')

            # Monitoring profile assignment
            if monitoring_profile_id:
                try:
                    db.session.execute(
                        AssetMonitoringProfile.delete().where(
                            AssetMonitoringProfile.c.asset_id == asset_id
                        )
                    )
                    db.session.execute(
                        AssetMonitoringProfile.insert().values(
                            asset_id=asset_id,
                            profile_id=int(monitoring_profile_id),
                            assigned_by=current_user.id
                        )
                    )
                    changes.append(f'monitoring_profile → {monitoring_profile_id}')
                except Exception:
                    pass

            # Backup policy assignment (via RMM agent)
            if backup_policy_id:
                try:
                    agent_row = db.session.execute(
                        text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = true LIMIT 1"),
                        {'aid': asset_id}
                    ).fetchone()
                    if agent_row:
                        agent_id = agent_row[0]
                        existing = db.session.execute(
                            text("SELECT id FROM rmm_agent_backup_policy WHERE agent_id = :aid LIMIT 1"),
                            {'aid': agent_id}
                        ).fetchone()
                        if existing:
                            db.session.execute(
                                text("UPDATE rmm_agent_backup_policy SET policy_id = :pid WHERE agent_id = :aid"),
                                {'pid': int(backup_policy_id), 'aid': agent_id}
                            )
                        else:
                            db.session.execute(
                                text("INSERT INTO rmm_agent_backup_policy (agent_id, policy_id, enabled) VALUES (:aid, :pid, true)"),
                                {'aid': agent_id, 'pid': int(backup_policy_id)}
                            )
                        changes.append(f'backup_policy → {backup_policy_id}')
                except Exception:
                    pass

            if changes:
                history = AssetHistory(
                    asset_id=asset.id,
                    action='Bulk edit: ' + '; '.join(changes),
                    user_id=current_user.id
                )
                db.session.add(history)
                count += 1

        db.session.commit()
        return jsonify({'success': True, 'count': count, 'message': f'Updated {count} assets'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/assets/bulk/export', methods=['POST'])
@login_required
@license_required
def bulk_export_selected():
    """Export selected assets to CSV"""
    try:
        asset_ids = json.loads(request.form.get('asset_ids', '[]'))
        
        if not asset_ids:
            return "No assets selected", 400
        
        # Get selected assets
        assets = Asset.query.filter(Asset.id.in_(asset_ids)).all()
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'Asset Tag', 'Name', 'Category', 'Manufacturer', 'Model',
            'Serial Number', 'Status', 'Purchase Date', 'Purchase Cost',
            'Warranty Expiry', 'Assigned To', 'Department', 'Location',
            'Expected Life (years)', 'Condition', 'Notes'
        ])
        
        # Write data
        for asset in assets:
            writer.writerow([
                asset.asset_tag,
                asset.name,
                asset.category,
                asset.manufacturer or '',
                asset.model or '',
                asset.serial_number or '',
                asset.status,
                asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '',
                asset.purchase_cost or '',
                asset.warranty_expiry.strftime('%Y-%m-%d') if asset.warranty_expiry else '',
                asset.assigned_employee.name if asset.assigned_employee else '',
                asset.department or '',
                asset.location or '',
                asset.expected_life_years or '',
                asset.condition or '',
                asset.notes or ''
            ])
        
        # Prepare response
        output.seek(0)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'selected_assets_{timestamp}.csv'
        )
    
    except Exception as e:
        return f"Error exporting assets: {str(e)}", 500


@bp.route('/assets/bulk/update-agent', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_update_agent():
    """Queue a force_update command for the RMM agents linked to selected assets."""
    data = request.get_json(force=True) or {}
    asset_ids = data.get('asset_ids', [])
    if not asset_ids:
        return jsonify({'success': False, 'message': 'No assets selected'}), 400

    rows = db.session.execute(
        text("SELECT agent_id FROM rmm_agent WHERE asset_id = ANY(:ids) AND enabled = true"),
        {'ids': [int(i) for i in asset_ids]}
    ).fetchall()
    if not rows:
        return jsonify({'success': False, 'message': 'None of the selected assets have an RMM agent enrolled'}), 400

    queued = 0
    for row in rows:
        agent_id = row[0]
        # Skip if a pending force_update already exists
        existing = db.session.execute(
            text("SELECT 1 FROM rmm_commands WHERE agent_id = :aid AND command_type = 'control' AND status = 'pending' LIMIT 1"),
            {'aid': agent_id}
        ).fetchone()
        if existing:
            continue
        db.session.execute(
            text("INSERT INTO rmm_commands (agent_id, command, command_type, status, created_at) VALUES (:aid, 'force_update', 'control', 'pending', NOW())"),
            {'aid': agent_id}
        )
        queued += 1
    db.session.commit()

    skipped = len(rows) - queued
    msg = f'Queued update for {queued} agent(s).'
    if skipped:
        msg += f' {skipped} already had a pending command.'
    return jsonify({'success': True, 'count': queued, 'message': msg})


@bp.route('/assets/bulk/scan-patches', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_scan_patches():
    """Send request_patch_scan to RMM agents linked to selected assets via the gateway."""
    import urllib.request as _ur
    data = request.get_json(force=True) or {}
    asset_ids = data.get('asset_ids', [])
    if not asset_ids:
        return jsonify({'success': False, 'message': 'No assets selected'}), 400

    rows = db.session.execute(
        text("SELECT agent_id FROM rmm_agent WHERE asset_id = ANY(:ids) AND enabled = true"),
        {'ids': [int(i) for i in asset_ids]}
    ).fetchall()
    if not rows:
        return jsonify({'success': False, 'message': 'None of the selected assets have an RMM agent enrolled'}), 400

    sent = 0
    skipped = 0
    for row in rows:
        agent_id = row[0]
        payload = json.dumps({"type": "request_patch_scan"}).encode()
        try:
            req = _ur.Request(
                f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with _ur.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
            if result.get('ok'):
                sent += 1
            else:
                skipped += 1  # agent offline
        except Exception:
            skipped += 1

    msg = f'Patch scan sent to {sent} online agent(s).'
    if skipped:
        msg += f' {skipped} agent(s) were offline or unreachable.'
    return jsonify({'success': True, 'count': sent, 'message': msg})


@bp.route('/assets/bulk/deploy-patches', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_deploy_patches():
    """Create patch jobs for all pending Windows Updates on selected assets and deploy them."""
    import urllib.request as _ur
    data = request.get_json(force=True) or {}
    asset_ids = data.get('asset_ids', [])
    if not asset_ids:
        return jsonify({'success': False, 'message': 'No assets selected'}), 400

    agents = db.session.execute(
        text("SELECT agent_id FROM rmm_agent WHERE asset_id = ANY(:ids) AND enabled = true"),
        {'ids': [int(i) for i in asset_ids]}
    ).fetchall()
    if not agents:
        return jsonify({'success': False, 'message': 'None of the selected assets have an RMM agent enrolled'}), 400

    deployed = 0
    no_updates = 0
    offline = 0

    for row in agents:
        agent_id = row[0]
        # Fetch all pending updates for this agent
        updates = db.session.execute(
            text("SELECT update_id, title FROM rmm_pending_update WHERE agent_id = :aid"),
            {'aid': agent_id}
        ).fetchall()
        if not updates:
            no_updates += 1
            continue

        update_ids = [u[0] for u in updates]
        kb_ids     = []   # metadata only — not needed by WUA installer
        titles     = [u[1] for u in updates]

        # Create the patch job
        res = db.session.execute(
            text("""INSERT INTO rmm_patch_job
                        (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
                    VALUES (:aid, :uids, :kbids, :titles, 'queued', :uid, NOW())
                    RETURNING id"""),
            {
                'aid':    agent_id,
                'uids':   json.dumps(update_ids),
                'kbids':  json.dumps(kb_ids),
                'titles': json.dumps(titles),
                'uid':    current_user.id if hasattr(current_user, 'id') else None,
            }
        )
        db.session.commit()
        job_id = res.scalar()

        # Fire the deploy immediately
        payload = json.dumps({
            'type':       'install_patches',
            'job_id':     job_id,
            'update_ids': update_ids,
            'kb_ids':     kb_ids,
            'titles':     titles,
        }).encode()
        try:
            req = _ur.Request(
                f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with _ur.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
            if result.get('ok'):
                db.session.execute(
                    text("UPDATE rmm_patch_job SET status='deploying', deployed_at=NOW(), updated_at=NOW() WHERE id=:jid"),
                    {'jid': job_id}
                )
                db.session.commit()
                deployed += 1
            else:
                offline += 1
        except Exception:
            offline += 1

    parts = []
    if deployed:
        parts.append(f'Deploying patches to {deployed} online agent(s).')
    if no_updates:
        parts.append(f'{no_updates} agent(s) had no pending updates.')
    if offline:
        parts.append(f'{offline} agent(s) were offline (job queued for next connect).')
    return jsonify({'success': True, 'count': deployed, 'message': ' '.join(parts) or 'Done.'})


@bp.route('/assets/bulk/delete', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_delete_assets():
    """Delete multiple assets"""
    try:
        data = request.get_json()
        asset_ids = data.get('asset_ids', [])
        
        if not asset_ids:
            return jsonify({'success': False, 'message': 'No assets selected'}), 400
        
        # Delete assets
        deleted_count = 0
        for asset_id in asset_ids:
            asset = Asset.query.get(asset_id)
            if asset:
                # Delete photo file if it exists
                if asset.photo:
                    photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], asset.photo)
                    if os.path.exists(photo_path):
                        os.remove(photo_path)

                # Remove any linked RMM agent and its child data
                agent_row = db.session.execute(
                    text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid"), {'aid': asset_id}
                ).mappings().fetchone()
                if agent_row:
                    _rmm_cascade_delete(agent_row['agent_id'])

                # Nullify/delete all other FK references to this asset
                _asset_cascade_delete(asset_id)

                db.session.delete(asset)
                deleted_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'count': deleted_count,
            'message': f'Successfully deleted {deleted_count} assets'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@bp.route('/assets/bulk/auto-assign', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def bulk_auto_assign():
    """Auto-assign assets to employees based on name matching"""
    if request.method == 'POST':
        matched = 0
        unmatched = []
        
        # Get all unassigned assets
        unassigned_assets = Asset.query.filter(Asset.employee_id == None).all()
        employees = Employee.query.all()
        
        # Create a mapping of employee first names to employee objects
        employee_map = {}
        for emp in employees:
            first_name = emp.name.split()[0].upper()
            employee_map[first_name] = emp
        
        # Try to match assets
        for asset in unassigned_assets:
            asset_name_upper = asset.name.upper()
            matched_emp = None
            
            # Try to find employee name in asset name
            for first_name, emp in employee_map.items():
                if first_name in asset_name_upper:
                    matched_emp = emp
                    break
            
            if matched_emp:
                asset.employee_id = matched_emp.id
                asset.status = 'In Use'
                db.session.commit()
                matched += 1
            else:
                unmatched.append(asset.asset_tag)
        
        flash(f'Auto-assigned {matched} assets to employees!', 'success')
        if unmatched and len(unmatched) <= 10:
            flash(f'Could not match: {", ".join(unmatched[:10])}', 'info')
        elif unmatched:
            flash(f'Could not match {len(unmatched)} assets', 'info')
        
        return redirect(url_for('assets.assets'))
    
    # GET request - show preview
    unassigned_count = Asset.query.filter(Asset.employee_id == None).count()
    employee_count = Employee.query.count()
    
    return render_template('bulk_auto_assign.html', 
                          unassigned_count=unassigned_count,
                          employee_count=employee_count)


@bp.route('/assets/bulk/assign-csv', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def bulk_assign_csv():
    """Import asset assignments from CSV"""
    if request.method == 'POST':
        # Check if this is confirmation or initial upload
        if request.form.get('confirm'):
            # Process the confirmed assignments from session
            try:
                preview_data = session.get('bulk_assign_preview', [])
                if not preview_data:
                    flash('Session expired. Please upload the CSV again.', 'warning')
                    return redirect(url_for('assets.bulk_assign_csv'))
                
                matched = 0
                for assignment in preview_data:
                    if assignment['status'] == 'valid':
                        asset = Asset.query.get(assignment['asset_id'])
                        if asset:
                            asset.employee_id = assignment['employee_id']
                            asset.status = 'In Use'
                            db.session.commit()
                            matched += 1
                
                # Clear session data
                session.pop('bulk_assign_preview', None)
                
                flash(f'Successfully assigned {matched} assets!', 'success')
                return redirect(url_for('assets.assets'))
            except Exception as e:
                flash(f'Error processing assignments: {str(e)}', 'danger')
                return redirect(url_for('assets.bulk_assign_csv'))
        
        # Initial CSV upload - preview assignments
        if 'file' not in request.files:
            flash('No file uploaded', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        if not file.filename.endswith('.csv'):
            flash('File must be a CSV', 'danger')
            return redirect(request.url)
        
        try:
            # Read CSV and prepare preview
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            preview_data = []
            
            for row in csv_reader:
                # Skip empty rows
                if not any(row.values()):
                    continue
                
                # Get employee name and asset identifier from CSV
                emp_name = (row.get('Employee Name') or row.get('Employee') or 
                           row.get('User Name') or row.get('User') or 
                           row.get('Name') or '').strip()
                
                asset_name = (row.get('Asset Name') or row.get('Device Name') or 
                             row.get('Computer Name') or row.get('Asset') or 
                             row.get('Asset Tag') or '').strip()
                
                if not emp_name or not asset_name:
                    continue
                
                # Find employee by full name (case insensitive)
                employee = Employee.query.filter(
                    db.func.lower(Employee.name) == emp_name.lower()
                ).first()
                
                # Find asset by name first, then asset tag (case insensitive)
                asset = Asset.query.filter(
                    db.or_(
                        db.func.lower(Asset.name) == asset_name.lower(),
                        db.func.lower(Asset.asset_tag) == asset_name.lower()
                    )
                ).first()
                
                # Prepare preview entry
                entry = {
                    'csv_employee': emp_name,
                    'csv_asset': asset_name,
                    'employee_id': employee.id if employee else None,
                    'employee_name': employee.name if employee else None,
                    'employee_dept': employee.department if employee else None,
                    'asset_id': asset.id if asset else None,
                    'asset_tag': asset.asset_tag if asset else None,
                    'asset_name': asset.name if asset else None,
                    'asset_current': None,
                    'status': 'valid' if (employee and asset) else 'error',
                    'error': None
                }
                
                # Get current assignment if asset exists
                if asset and asset.employee_id:
                    current_emp = Employee.query.get(asset.employee_id)
                    if current_emp:
                        entry['asset_current'] = current_emp.name
                else:
                    entry['asset_current'] = 'Unassigned'
                
                if not employee:
                    entry['error'] = f"Employee not found"
                elif not asset:
                    entry['error'] = f"Asset not found"
                
                preview_data.append(entry)
            
            if not preview_data:
                flash('No valid data found in CSV', 'warning')
                return redirect(request.url)
            
            # Store preview data in session
            session['bulk_assign_preview'] = preview_data
            
            # Show preview page
            valid_count = sum(1 for x in preview_data if x['status'] == 'valid')
            error_count = len(preview_data) - valid_count
            
            return render_template('bulk_assign_preview.html',
                                  preview_data=preview_data,
                                  valid_count=valid_count,
                                  error_count=error_count)
            
        except Exception as e:
            flash(f'Error processing CSV: {str(e)}', 'danger')
            return redirect(request.url)
    
    # GET request - show upload form
    unassigned_count = Asset.query.filter(Asset.employee_id == None).count()
    employee_count = Employee.query.count()
    
    return render_template('bulk_assign_csv.html',
                          unassigned_count=unassigned_count,
                          employee_count=employee_count)


@bp.route('/assets/export/csv')
@login_required
@license_required
def export_assets_csv():
    """Export all assets to CSV"""
    assets = Asset.query.all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Asset Tag', 'Name', 'Category', 'Manufacturer', 'Model', 'Serial Number', 
                     'Purchase Date', 'Purchase Cost', 'Warranty Expiry', 'Status', 'Location', 
                     'Assigned To', 'Expected Life (Years)', 'Replacement Date', 'Condition', 'Notes'])
    
    # Write data
    for asset in assets:
        writer.writerow([
            asset.asset_tag,
            asset.name,
            asset.category,
            asset.manufacturer or '',
            asset.model or '',
            asset.serial_number or '',
            asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '',
            asset.purchase_cost if asset.purchase_cost else '',
            asset.warranty_expiry.strftime('%Y-%m-%d') if asset.warranty_expiry else '',
            asset.status,
            asset.location or '',
            asset.assigned_employee.name if asset.assigned_employee else '',
            asset.expected_life_years if asset.expected_life_years else '',
            asset.replacement_date.strftime('%Y-%m-%d') if asset.replacement_date else '',
            asset.condition or '',
            asset.notes or ''
        ])
    
    # Prepare response
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'assets_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
    )


@bp.route('/assets/import', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def import_assets():
    """Import assets from CSV"""
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file uploaded', 'danger')
            return redirect(url_for('assets.import_assets'))
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(url_for('assets.import_assets'))
        
        if file and file.filename.endswith('.csv'):
            try:
                # Read CSV file
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_reader = csv.DictReader(stream)
                
                imported = 0
                skipped = 0
                errors = []
                
                for row_num, row in enumerate(csv_reader, start=2):
                    try:
                        # Check if row is completely empty
                        if not any(row.values()):
                            skipped += 1
                            continue
                        
                        # Generate temporary asset tag if empty
                        asset_tag = row.get('Asset Tag', '').strip()
                        if not asset_tag:
                            # Generate unique temporary tag with microseconds for uniqueness
                            timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
                            asset_tag = f"TEMP-{timestamp}-{row_num}"
                        
                        # Check if asset tag already exists
                        if Asset.query.filter_by(asset_tag=asset_tag).first():
                            errors.append(f"Row {row_num}: Asset tag '{asset_tag}' already exists")
                            skipped += 1
                            continue
                        
                        # Handle serial number - set to None if it's a placeholder or empty
                        serial = row.get('Serial Number', '').strip()
                        if serial.lower() in ['', 'to be filled by o.e.m.', 'default string', 'n/a', 'na', 'none', 'unknown', '123456789', '0', '00000000']:
                            serial = None
                        # Check if serial number already exists in database
                        elif serial and Asset.query.filter_by(serial_number=serial).first():
                            errors.append(f"Row {row_num}: Duplicate serial number '{serial}', setting to None")
                            serial = None
                        
                        # Parse dates with multiple format support
                        def parse_date(date_str):
                            if not date_str or not date_str.strip():
                                return None
                            date_str = date_str.strip()
                            # Ignore placeholder values
                            if date_str in ['0', 'N/A', 'NA', 'n/a', 'na', 'None', 'none']:
                                return None
                            # Try multiple date formats
                            for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']:
                                try:
                                    return datetime.strptime(date_str, fmt).date()
                                except ValueError:
                                    continue
                            raise ValueError(f"Unable to parse date '{date_str}'")
                        
                        asset = Asset(
                            asset_tag=row['Asset Tag'],
                            name=row['Name'],
                            category=row['Category'],
                            manufacturer=row.get('Manufacturer', ''),
                            model=row.get('Model', ''),
                            serial_number=serial,
                            purchase_date=parse_date(row.get('Purchase Date')),
                            purchase_cost=float(row['Purchase Cost']) if row.get('Purchase Cost') else None,
                            warranty_expiry=parse_date(row.get('Warranty Expiry')),
                            status=row.get('Status', 'Available'),
                            location=row.get('Location', ''),
                            expected_life_years=int(row['Expected Life (Years)']) if row.get('Expected Life (Years)') else None,
                            replacement_date=parse_date(row.get('Replacement Date')),
                            condition=row.get('Condition', ''),
                            notes=row.get('Notes', '')
                        )
                        
                        db.session.add(asset)
                        db.session.commit()  # Commit each asset immediately
                        imported += 1
                        
                    except Exception as e:
                        db.session.rollback()  # Rollback this failed row only
                        errors.append(f"Row {row_num}: {str(e)}")
                
                if imported > 0:
                    flash(f'Successfully imported {imported} assets! (Skipped {skipped} duplicates/empty rows)', 'success')
                if errors:
                    error_msg = "; ".join(errors[:10])
                    if len(errors) > 10:
                        error_msg += f"; ... and {len(errors) - 10} more errors"
                    flash(error_msg, 'warning')
                
                return redirect(url_for('assets.assets'))
                
            except Exception as e:
                flash(f'Error processing CSV: {str(e)}', 'danger')
                return redirect(url_for('assets.import_assets'))
        else:
            flash('Please upload a valid CSV file', 'danger')
            return redirect(url_for('assets.import_assets'))
    
    return render_template('import_assets.html')


@bp.route('/assets/sync-from-intune', methods=['POST'])
@login_required
@manager_required
@license_required
def sync_assets_from_intune():
    """Sync assets from Microsoft Intune/Defender"""
    result = perform_intune_asset_sync()
    if result.get('success'):
        message_parts = []
        if result.get('synced_count', 0) > 0:
            message_parts.append(f"{result['synced_count']} new assets synced")
        if result.get('updated_count', 0) > 0:
            message_parts.append(f"{result['updated_count']} assets updated")
        if result.get('skipped_count', 0) > 0:
            message_parts.append(f"{result['skipped_count']} devices skipped")
        if message_parts:
            flash(', '.join(message_parts) + ' from Intune', 'success')
        if result.get('errors'):
            for error in result['errors'][:5]:
                flash(error, 'warning')
    else:
        flash(result.get('error') or 'Error syncing from Intune', 'danger')

    return redirect(url_for('assets.assets'))


def perform_intune_asset_sync():
    """Core Intune asset sync logic.

    Returns:
        dict: {success, synced_count, updated_count, skipped_count, errors, error}
    """
    try:
        db.session.rollback()

        from m365_service import M365Service

        tenant_id_setting = Setting.query.filter_by(key='m365_tenant_id').first()
        client_id_setting = Setting.query.filter_by(key='m365_client_id').first()
        client_secret_setting = Setting.query.filter_by(key='m365_client_secret').first()

        if not all([tenant_id_setting, client_id_setting, client_secret_setting]):
            return {
                'success': False,
                'error': 'M365 credentials not configured. Please configure in Settings.'
            }

        m365 = M365Service(
            tenant_id=tenant_id_setting.value,
            client_id=client_id_setting.value,
            client_secret=client_secret_setting.value
        )

        devices = m365.get_all_devices_with_hardware()
        if not devices:
            return {
                'success': True,
                'synced_count': 0,
                'updated_count': 0,
                'skipped_count': 0,
                'errors': []
            }

        synced_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        # Preload assets/employees to avoid per-device queries
        all_assets = Asset.query.all()
        assets_by_serial = {}
        assets_by_name_lower = {}
        existing_asset_tags = set()
        for existing_asset in all_assets:
            if existing_asset.asset_tag:
                existing_asset_tags.add(existing_asset.asset_tag)
            if existing_asset.serial_number:
                assets_by_serial[existing_asset.serial_number] = existing_asset
            if existing_asset.name:
                assets_by_name_lower.setdefault(existing_asset.name.strip().lower(), existing_asset)

        employees_by_email_lower = {
            (emp.email or '').strip().lower(): emp
            for emp in Employee.query.all()
            if emp.email
        }

        def parse_graph_datetime(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except Exception:
                return None

        def normalize_serial(value):
            if not value:
                return None
            value = str(value).strip()
            if not value:
                return None
            if value.lower() in ['unknown', 'n/a', 'none']:
                return None
            return value

        def build_unique_asset_tag(base):
            if not base:
                base = f"TEMP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            base_tag = base.upper().replace(' ', '').replace('/', '-').replace('_', '-')
            candidate = base_tag
            counter = 1
            while candidate in existing_asset_tags:
                candidate = f"{base_tag}{counter}"
                counter += 1
            existing_asset_tags.add(candidate)
            return candidate

        for device in devices:
            try:
                device_name = device.get('deviceName')
                if not device_name:
                    skipped_count += 1
                    continue

                device_name_norm = str(device_name).strip()
                serial_number = normalize_serial(device.get('serialNumber'))

                asset = None
                if serial_number and serial_number in assets_by_serial:
                    asset = assets_by_serial[serial_number]
                if not asset:
                    asset = assets_by_name_lower.get(device_name_norm.lower())

                upn = (device.get('userPrincipalName') or '').strip().lower()
                employee = employees_by_email_lower.get(upn) if upn else None

                os_type = (device.get('operatingSystem') or '').lower()
                if 'windows' in os_type:
                    category = 'Laptop' if 'laptop' in device_name_norm.lower() else 'Desktop'
                elif 'mac' in os_type or 'ios' in os_type:
                    category = 'Laptop' if 'mac' in os_type else 'Mobile Device'
                else:
                    category = 'Other'

                compliance = device.get('complianceState', 'unknown')
                status = 'In Use' if (compliance == 'compliant' and employee) else ('Available' if compliance == 'compliant' else 'Needs Attention')

                os_name = device.get('operatingSystem', '')
                os_ver = device.get('osVersion', '')

                enrollment_dt = parse_graph_datetime(device.get('enrolledDateTime'))
                last_sync_dt = parse_graph_datetime(device.get('lastSyncDateTime'))

                hw_info = device.get('hardwareInformation', {}) or {}
                cpu_arch = device.get('processorArchitecture') or hw_info.get('processorArchitecture')

                ram_bytes = device.get('physicalMemoryInBytes') or 0
                ram_gb = round(ram_bytes / (1024**3), 2) if ram_bytes and ram_bytes > 0 else None

                total_storage = device.get('totalStorageSpaceInBytes') or hw_info.get('totalStorageSpace') or 0
                free_storage = device.get('freeStorageSpaceInBytes') or hw_info.get('freeStorageSpace') or 0
                total_storage_gb = round(total_storage / (1024**3), 2) if total_storage and total_storage > 0 else None
                free_storage_gb = round(free_storage / (1024**3), 2) if free_storage and free_storage > 0 else None

                bios_ver = hw_info.get('systemManagementBIOSVersion')
                tpm_ver = hw_info.get('tpmVersion') or device.get('tpmVersion')
                wifi_mac = hw_info.get('wifiMac') or device.get('wiFiMacAddress')
                eth_mac = device.get('ethernetMacAddress')

                if asset:
                    asset.name = device_name_norm or asset.name
                    asset.manufacturer = device.get('manufacturer') or asset.manufacturer
                    asset.model = device.get('model') or asset.model
                    if os_name:
                        asset.os_version = f"{os_name} {os_ver}".strip()
                    asset.intune_os_version = os_ver

                    asset.intune_device_id = device.get('id')
                    asset.intune_compliance_state = device.get('complianceState', 'unknown')
                    asset.intune_management_state = device.get('managementState', 'unknown')
                    if enrollment_dt:
                        asset.intune_enrolled_date = enrollment_dt
                    if last_sync_dt:
                        asset.intune_last_sync = last_sync_dt
                        asset.last_seen = last_sync_dt

                    asset.online_state = device.get('complianceState', 'unknown')
                    asset.hardware_cpu = cpu_arch
                    if ram_gb is not None:
                        asset.hardware_ram_gb = ram_gb
                    if total_storage_gb is not None:
                        asset.hardware_storage_total_gb = total_storage_gb
                    if free_storage_gb is not None:
                        asset.hardware_storage_free_gb = free_storage_gb
                    asset.hardware_bios_version = bios_ver
                    asset.hardware_tpm_version = tpm_ver
                    asset.hardware_mac_wifi = wifi_mac
                    asset.hardware_mac_ethernet = eth_mac
                    asset.azure_ad_device_id = device.get('azureADDeviceId')

                    if employee:
                        if not asset.employee_id:
                            asset.employee_id = employee.id
                            asset.status = 'In Use'
                        elif asset.employee_id != employee.id:
                            asset.employee_id = employee.id

                    updated_count += 1
                else:
                    if serial_number and len(serial_number) >= 10:
                        tag_base = serial_number[:10]
                    else:
                        tag_base = device_name_norm[:10] if len(device_name_norm) >= 10 else device_name_norm
                    asset_tag = build_unique_asset_tag(tag_base)

                    enrollment_date = enrollment_dt.date() if enrollment_dt else None
                    os_full = f"{os_name} {os_ver}".strip() if os_name else None

                    new_asset = Asset(
                        asset_tag=asset_tag,
                        name=device_name_norm,
                        category=category,
                        manufacturer=device.get('manufacturer'),
                        model=device.get('model'),
                        serial_number=serial_number,
                        status=status,
                        os_version=os_full,
                        intune_os_version=os_ver,
                        online_state=compliance,
                        employee_id=employee.id if employee else None,
                        purchase_date=enrollment_date,
                        intune_device_id=device.get('id'),
                        intune_enrolled_date=enrollment_dt,
                        intune_last_sync=last_sync_dt,
                        intune_compliance_state=device.get('complianceState', 'unknown'),
                        intune_management_state=device.get('managementState', 'unknown'),
                        hardware_cpu=cpu_arch,
                        hardware_ram_gb=ram_gb,
                        hardware_storage_total_gb=total_storage_gb,
                        hardware_storage_free_gb=free_storage_gb,
                        hardware_bios_version=bios_ver,
                        hardware_mac_wifi=wifi_mac,
                        hardware_mac_ethernet=eth_mac,
                        hardware_tpm_version=tpm_ver,
                        azure_ad_device_id=device.get('azureADDeviceId'),
                        last_seen=last_sync_dt,
                        notes=f"Synced from Microsoft Intune on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                    )
                    db.session.add(new_asset)
                    if serial_number:
                        assets_by_serial[serial_number] = new_asset
                    assets_by_name_lower.setdefault(device_name_norm.lower(), new_asset)
                    synced_count += 1

            except Exception as e:
                errors.append(f"Error syncing {device.get('deviceName', 'Unknown')}: {str(e)}")
                continue

        db.session.commit()

        return {
            'success': True,
            'synced_count': synced_count,
            'updated_count': updated_count,
            'skipped_count': skipped_count,
            'errors': errors
        }
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {
            'success': False,
            'error': str(e)
        }


@bp.route('/assets/<int:asset_id>/checkout', methods=['POST'])
@login_required
@manager_required
@license_required
def asset_checkout(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    # Check not already checked out
    active = AssetLoan.query.filter_by(asset_id=asset_id, checked_in_at=None).first()
    if active:
        flash(f'Asset is already checked out to {active.checked_out_to}.', 'warning')
        return redirect(url_for('assets.view_asset', asset_id=asset_id))
    borrower = (request.form.get('checked_out_to') or '').strip()
    if not borrower:
        flash('Borrower name is required.', 'danger')
        return redirect(url_for('assets.view_asset', asset_id=asset_id))
    due_str = (request.form.get('due_back_at') or '').strip()
    due = None
    if due_str:
        try:
            from datetime import date as _date
            due = _date.fromisoformat(due_str)
        except ValueError:
            pass
    loan = AssetLoan(
        asset_id=asset_id,
        checked_out_to=borrower,
        checked_out_by_user_id=current_user.id,
        due_back_at=due,
        notes=(request.form.get('notes') or '').strip() or None,
    )
    db.session.add(loan)
    log_change(asset, current_user.username, 'checkout', f'Checked out to {borrower}')
    db.session.commit()
    flash(f'Asset checked out to {borrower}.', 'success')
    return redirect(url_for('assets.view_asset', asset_id=asset_id))


@bp.route('/assets/<int:asset_id>/checkin/<int:loan_id>', methods=['POST'])
@login_required
@manager_required
@license_required
def asset_checkin(asset_id, loan_id):
    loan = AssetLoan.query.get_or_404(loan_id)
    if loan.asset_id != asset_id:
        flash('Loan does not belong to this asset.', 'danger')
        return redirect(url_for('assets.view_asset', asset_id=asset_id))
    if not loan.is_active:
        flash('Asset is already checked in.', 'warning')
        return redirect(url_for('assets.view_asset', asset_id=asset_id))
    loan.checked_in_at = now_mst()
    loan.checked_in_by_user_id = current_user.id
    asset = Asset.query.get(asset_id)
    log_change(asset, current_user.username, 'checkin', f'Checked in from {loan.checked_out_to}')
    db.session.commit()
    flash(f'Asset checked in from {loan.checked_out_to}.', 'success')
    return redirect(url_for('assets.view_asset', asset_id=asset_id))


@bp.route('/api/asset/<int:asset_id>/software', methods=['POST'])
@license_required
@require_api_key('agent')
def api_update_software(asset_id):
    """Agent POSTs full software inventory; replace existing records."""
    asset = Asset.query.get_or_404(asset_id)
    apps = request.get_json(silent=True) or []
    InstalledApp.query.filter_by(asset_id=asset_id).delete()
    now = now_mst()
    for a in apps:
        name = (a.get('name') or '').strip()
        if not name:
            continue
        db.session.add(InstalledApp(
            asset_id=asset_id,
            name=name,
            version=(a.get('version') or '').strip() or None,
            publisher=(a.get('publisher') or '').strip() or None,
            install_date=(a.get('install_date') or '').strip() or None,
            recorded_at=now,
        ))
    db.session.commit()
    return jsonify({'ok': True, 'count': len(apps)})


@bp.route('/search')
@login_required
@license_required
def global_search():
    q = (request.args.get('q') or '').strip()
    if not q or len(q) < 2:
        return render_template('search.html', q=q, assets=[], tickets=[], employees=[])
    like = f'%{q}%'

    from sqlalchemy import or_
    assets = Asset.query.filter(or_(
        Asset.asset_tag.ilike(like),
        Asset.hostname.ilike(like),
        Asset.make.ilike(like),
        Asset.model.ilike(like),
        Asset.serial_number.ilike(like),
    )).limit(20).all()

    tickets = SupportTicket.query.filter(or_(
        SupportTicket.subject.ilike(like),
        SupportTicket.description.ilike(like),
        SupportTicket.reporter_name.ilike(like),
        SupportTicket.reporter_email.ilike(like),
        SupportTicket.hostname.ilike(like),
        SupportTicket.asset_tag.ilike(like),
    )).order_by(SupportTicket.created_at.desc()).limit(20).all()

    try:
        from models_employee import Employee
        employees = Employee.query.filter(or_(
            Employee.name.ilike(like),
            Employee.email.ilike(like),
            Employee.department.ilike(like),
            Employee.job_title.ilike(like),
        )).limit(20).all()
    except Exception:
        employees = []

    return render_template('search.html', q=q, assets=assets, tickets=tickets, employees=employees)


@bp.route('/asset/<int:asset_id>/unlink-agent', methods=['POST'])
@login_required
def unlink_rmm_agent(asset_id):
    """Remove the RMM agent record linked to this asset without deleting the asset."""
    db.session.execute(
        text("DELETE FROM rmm_agent WHERE asset_id = :aid"),
        {'aid': asset_id}
    )
    db.session.commit()
    flash('RMM agent unlinked. The asset record was not deleted.', 'success')
    return redirect(url_for('assets.view_asset', asset_id=asset_id))


@bp.route('/assets/<int:asset_id>/download-installer')
@login_required
@license_required
def download_asset_installer(asset_id):
    """Generate a ready-to-run PS1 installer pre-configured for a specific asset.
    Creates a one-time enrollment token (valid 7 days) baked into the script.
    The user just right-clicks the downloaded PS1 → Run with PowerShell (as Administrator).
    """
    import hashlib, secrets as _secrets, io as _io
    from datetime import timedelta

    asset = db.session.get(Asset, asset_id)
    if not asset:
        return "Asset not found.", 404

    # Generate a one-time enrollment token
    raw_token = 'enroll_' + _secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.utcnow() + timedelta(days=7)

    # Store in DB
    db.session.execute(
        text("""INSERT INTO rmm_enrollment_tokens
                (token_sha256, asset_id, created_by, created_at, expires_at)
                VALUES (:hash, :asid, :uid, NOW(), :exp)"""),
        {
            'hash': token_hash,
            'asid': asset_id,
            'uid': current_user.id if hasattr(current_user, 'id') else None,
            'exp': expires_at.isoformat(),
        }
    )
    db.session.commit()

    tracker_url = RMM_TRACKER_URL.rstrip('/')
    gateway_url = RMM_GATEWAY_PUBLIC.rstrip('/')

    # Build a self-contained PS1 with all parameters pre-filled
    ps1_content = f"""#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Pre-configured Cirque RMM installer for asset: {asset.name}
    Token is valid for 7 days. Run as Administrator.
    Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
#>

# Pre-configured parameters — do not edit
$Token      = '{raw_token}'
$TrackerUrl = '{tracker_url}'
$GatewayUrl = '{gateway_url}'
$AgentId    = ''      # Leave blank to use computer hostname

# Download and run the full installer with pre-filled parameters
$InstallScript = (Invoke-WebRequest -Uri "$TrackerUrl/download/agent-ps1" -UseBasicParsing).Content
$sb = [ScriptBlock]::Create($InstallScript)
& $sb -Token $Token -TrackerUrl $TrackerUrl -GatewayUrl $GatewayUrl -AgentId $AgentId
"""

    safe_name = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in asset.name)
    filename = f'CirqueRMM-Install-{safe_name}.ps1'

    buf = _io.BytesIO(ps1_content.encode('utf-8-sig'))  # BOM for Windows PowerShell
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype='application/octet-stream')


def _asset_eol_check():
    """Daily: open a ticket for assets nearing warranty expiry or age EOL."""
    EOL_AGE_YEARS = 5        # auto-ticket assets older than this
    WARN_DAYS = 30           # warn this many days before warranty expiry

    while True:
        try:
            _time.sleep(86400)  # run once per day
            with current_app._get_current_object().app_context():
                today = datetime.utcnow().date()
                warn_date = today + timedelta(days=WARN_DAYS)
                eol_age_days = EOL_AGE_YEARS * 365

                # Assets with warranty expiring soon
                expiring = Asset.query.filter(
                    Asset.warranty_expiry.isnot(None),
                    Asset.warranty_expiry <= warn_date,
                    Asset.warranty_expiry >= today,
                    Asset.status != 'Retired'
                ).all()

                # Assets over EOL by age
                from datetime import date as _date
                aged_out = Asset.query.filter(
                    Asset.purchase_date.isnot(None),
                    Asset.status != 'Retired'
                ).all()
                aged_out = [a for a in aged_out
                            if (today - a.purchase_date).days >= eol_age_days]

                system_user_id = db.session.execute(
                    text('SELECT id FROM "user" ORDER BY id LIMIT 1')
                ).scalar() or 1

                def _ticket_exists(subject_prefix, asset_id):
                    return SupportTicket.query.filter(
                        SupportTicket.asset_id == asset_id,
                        SupportTicket.subject.like(f'{subject_prefix}%'),
                        SupportTicket.status != 'Closed'
                    ).first() is not None

                for asset in expiring:
                    pfx = f'[EOL] Warranty expiring'
                    if not _ticket_exists(pfx, asset.id):
                        days_left = (asset.warranty_expiry - today).days
                        t = SupportTicket(
                            status='Open', priority='High', source='system',
                            category='Asset Management',
                            subject=f'{pfx}: {asset.asset_tag} ({asset.name}) — {days_left}d left',
                            description=(
                                f'Asset {asset.asset_tag} ({asset.name}) warranty expires on '
                                f'{asset.warranty_expiry}. Please review replacement or extended '
                                f'warranty options.'),
                            asset_id=asset.id, asset_tag=asset.asset_tag,
                            hostname=asset.name,
                            created_by_user_id=system_user_id,
                            created_at=datetime.utcnow(), updated_at=datetime.utcnow())
                        db.session.add(t)
                        logger.info(f'EOL ticket created for expiring warranty: {asset.asset_tag}')

                for asset in aged_out:
                    pfx = f'[EOL] Asset age exceeded'
                    if not _ticket_exists(pfx, asset.id):
                        age_years = (today - asset.purchase_date).days / 365.25
                        t = SupportTicket(
                            status='Open', priority='Normal', source='system',
                            category='Asset Management',
                            subject=f'{pfx}: {asset.asset_tag} ({asset.name}) — {age_years:.1f}yr old',
                            description=(
                                f'Asset {asset.asset_tag} ({asset.name}) was purchased on '
                                f'{asset.purchase_date} ({age_years:.1f} years ago), exceeding the '
                                f'{EOL_AGE_YEARS}-year EOL threshold. Please evaluate for replacement.'),
                            asset_id=asset.id, asset_tag=asset.asset_tag,
                            hostname=asset.name,
                            created_by_user_id=system_user_id,
                            created_at=datetime.utcnow(), updated_at=datetime.utcnow())
                        db.session.add(t)
                        logger.info(f'EOL ticket created for aged asset: {asset.asset_tag}')

                db.session.commit()
        except Exception as _eol_err:
            logger.warning(f'Asset EOL check error: {_eol_err}')