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
import asset_field_ownership as afo  # field-ownership / lock-in model
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
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
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
    admin_required, manager_required,
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

    # Triage: sync-discovered assets that need an operator pass (placeholder/auto tag
    # or no serial), excluding network gear which legitimately has neither. Drives the
    # "Needs attention" lock-in view.
    if request.args.get('needs_attention'):
        query = query.filter(
            Asset.auto_discovered.is_(True),
            db.func.coalesce(Asset.category, '') != 'Network Device',
            (Asset.asset_tag.ilike('UNTAGGED-%') |
             Asset.asset_tag.ilike('RMM-%') |
             Asset.asset_tag.ilike('LINUX-%') |
             Asset.asset_tag.ilike('TEMP-%') |
             Asset.serial_number.is_(None))
        )

    # Location filter
    if location_filter:
        query = query.filter(Asset.location == location_filter)

    # Device type filter
    if device_type_filter:
        query = query.filter(Asset.device_type == device_type_filter)

    # Quick category chips that map to indexed columns — applied in SQL (fast), unlike
    # the post-materialization Python quick_filters below.
    if quick_filter == 'network':
        query = query.filter(Asset.category == 'Network Device')
    elif quick_filter == 'workstations':
        # Client endpoints: Windows PCs/Macs by device_type, plus the laptop/desktop
        # categories, but never servers / network gear / VMs.
        query = query.filter(
            Asset.device_type.in_(['Windows PC', 'Windows Workstation', 'Mac']) |
            (Asset.category.in_(['Laptop', 'Desktop', 'Computer', 'Workstation', 'Mini PC', 'Tablet']) &
             db.func.coalesce(Asset.device_type, '').notin_(['Windows Server', 'Linux Server', 'Network Device', 'Virtual Machine'])))
    elif quick_filter == 'windows_servers':
        # device_type is the reliable signal; the category='Server' hosts (Star-Wars-named
        # boxes) are Windows unless explicitly typed Linux.
        query = query.filter(
            (Asset.device_type == 'Windows Server') |
            ((Asset.category == 'Server') & (db.func.coalesce(Asset.device_type, '') != 'Linux Server')))
    elif quick_filter == 'linux_servers':
        query = query.filter(
            (Asset.device_type == 'Linux Server') |
            Asset.os_version.ilike('%linux%') | Asset.os_version.ilike('%ubuntu%') | Asset.os_version.ilike('%LTS%'))
    elif quick_filter == 'mobile':
        query = query.filter(
            (Asset.device_type == 'Mobile Device') |
            Asset.os_version.ilike('%android%') | Asset.os_version.ilike('%ios%'))
    elif quick_filter == 'windows_10':
        # Windows 10 builds report as 'Windows 10.0.1xxxx' (e.g. 19045); Windows 11 is
        # 'Windows 10.0.2xxxx' (22000+). Surfaces past-EOL Win10 stations for upgrade.
        query = query.filter(Asset.os_version.ilike('Windows 10.0.1%'))

    # Source filter — derived from the sync-id columns (intune / unifi / manual).
    source_filter = request.args.get('source', '')
    if source_filter == 'intune':
        query = query.filter(Asset.intune_device_id.isnot(None))
    elif source_filter == 'unifi':
        query = query.filter(Asset.unifi_device_id.isnot(None))
    elif source_filter == 'manual':
        query = query.filter(Asset.intune_device_id.is_(None), Asset.unifi_device_id.is_(None))

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
        'purchase_date': Asset.purchase_date,
        'device_type': Asset.device_type,   # was a dead clickable header
        'last_seen': Asset.last_seen,        # was a dead clickable header
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
            # Compliance lives in intune_compliance_state, NOT online_state
            # (online_state is live connectivity: Online/Offline).
            all_assets = [asset for asset in all_assets if asset.intune_compliance_state == 'noncompliant']
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

    # Sync freshness — shown as a "Last synced …" line so staff can see the syncs are alive.
    sync_status = {}
    try:
        for k, v in db.session.execute(text(
            "SELECT key, value FROM setting WHERE key IN "
            "('intune_asset_sync_last_finished','intune_asset_sync_last_status','intune_asset_sync_last_message',"
            " 'unifi_last_sync_time','unifi_last_sync_status','unifi_last_sync_message',"
            " 'ad_asset_sync_last_finished','ad_asset_sync_last_status','ad_asset_sync_last_message')")).fetchall():
            sync_status[k] = v
    except Exception:
        pass

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
                           sync_status=sync_status,
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
            # '' -> NULL: asset_tag & serial_number are UNIQUE; empty strings collide.
            asset_tag=(request.form.get('asset_tag') or '').strip() or None,
            name=request.form.get('name'),
            category=request.form.get('category'),
            manufacturer=request.form.get('manufacturer'),
            model=request.form.get('model'),
            serial_number=(request.form.get('serial_number') or '').strip() or None,
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
    rmm_visible = False  # whether the current user may see the RMM area for THIS asset
    try:
        rmm_row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = true LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()
        if rmm_row:
            rmm_agent_id = rmm_row[0]
            # Gate the RMM area: STRICTLY admin-only. Fail-closed for everyone
            # else (managers, eagle_eyes) — no tabs, toolbar, JS engine or cards.
            rmm_visible = (current_user.role == 'admin')
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

    # Stat-card counts: OS vs software pending patches + active alerts by severity
    os_patch_count = 0
    sw_patch_count = 0
    alert_counts = {'critical': 0, 'warning': 0, 'info': 0, 'total': 0}
    try:
        if rmm_agent_id:
            for cat, c in db.session.execute(
                text("SELECT category, COUNT(*) FROM rmm_pending_update WHERE agent_id=:aid GROUP BY category"),
                {'aid': rmm_agent_id}).fetchall():
                cl = (cat or '').lower()
                if ('security update' in cl or 'critical update' in cl or 'operating system' in cl
                        or 'update rollup' in cl or 'cumulative' in cl):
                    os_patch_count += c
                elif 'driver' in cl:
                    sw_patch_count += c
                # (.NET / definitions / EU-choice etc. counted on neither card — interim)
    except Exception:
        pass
    try:
        for sev, c in db.session.execute(
            text("SELECT severity, COUNT(*) FROM monitoring_alert WHERE asset_id=:aid AND resolved_at IS NULL GROUP BY severity"),
            {'aid': asset_id}).fetchall():
            s = (sev or '').lower()
            key = 'critical' if s == 'critical' else ('warning' if s == 'warning' else 'info')
            alert_counts[key] += c
            alert_counts['total'] += c
    except Exception:
        pass
    asset_alerts = []
    try:
        rows = db.session.execute(
            text("""SELECT severity, message, triggered_at, status, acknowledged_at
                    FROM monitoring_alert WHERE asset_id=:aid AND resolved_at IS NULL
                    ORDER BY triggered_at DESC LIMIT 50"""),
            {'aid': asset_id}).fetchall()
        asset_alerts = [r._mapping for r in rows]
    except Exception:
        pass

    # Candidate chassis for an HDD/OS transfer (exclude self).
    transfer_targets = (Asset.query.with_entities(Asset.id, Asset.name, Asset.serial_number)
                        .filter(Asset.id != asset.id)
                        .order_by(Asset.name).all())

    return render_template('view_asset.html', asset=asset, history=history, employees=employees,
                         now=datetime.utcnow,
                         active_loan=active_loan, loan_history=loan_history,
                         rmm_agent_id=rmm_agent_id, rmm_tele=rmm_tele, rmm_visible=rmm_visible,
                         monitoring_profile=monitoring_profile, all_profiles=all_profiles,
                         vuln_count=vuln_count, transfer_targets=transfer_targets,
                         os_patch_count=os_patch_count, sw_patch_count=sw_patch_count,
                         alert_counts=alert_counts, asset_alerts=asset_alerts)


@bp.route('/assets/<int:asset_id>/rmm/<section>')
@login_required
@admin_required
def rmm_section(asset_id, section):
    """Legacy standalone RMM console — now retired.

    The RMM console has been merged into the asset detail page as in-page tabs.
    Redirect existing bookmarks/links to the matching in-page tab via URL hash.
    The four deduped sections (metrics/patches/software/scripts) live under their
    original tab IDs; the rest are the new in-page RMM tabs (#tab-rmm-<section>).
    """
    ALLOWED = {'hw', 'sec', 'sysinfo', 'metrics', 'avail', 'patches',
               'software', 'scripts', 'services', 'events', 'transfer', 'power'}
    if section not in ALLOWED:
        abort(404)

    # Map each legacy section to its in-page tab hash:
    #  - metrics/patches/software/scripts keep their pre-existing pane IDs
    #  - hw/sec/sysinfo now live in full on the Overview tab (no separate panes)
    #  - the rest are the new in-page RMM panes (#tab-rmm-<section>)
    FRAG = {
        'metrics': 'tab-metrics', 'patches': 'tab-patches',
        'software': 'tab-software', 'scripts': 'tab-scripts',
        'hw': 'tab-overview', 'sec': 'tab-overview', 'sysinfo': 'tab-overview',
        'avail': 'tab-rmm-avail', 'services': 'tab-rmm-services',
        'events': 'tab-rmm-events', 'transfer': 'tab-rmm-transfer',
        'power': 'tab-rmm-power',
    }
    frag = FRAG.get(section, 'tab-overview')
    return redirect(url_for('assets.view_asset', asset_id=asset_id) + '#' + frag, code=302)


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
        
        # asset_tag and serial_number carry a UNIQUE constraint. Empty strings collide
        # (many assets legitimately have no serial), so normalize '' -> NULL — Postgres
        # allows multiple NULLs but not multiple ''. This was the cause of edit 500s.
        asset.asset_tag = (request.form.get('asset_tag') or '').strip() or None
        asset.name = request.form.get('name')
        asset.category = request.form.get('category')
        asset.manufacturer = request.form.get('manufacturer')
        asset.model = request.form.get('model')
        asset.serial_number = (request.form.get('serial_number') or '').strip() or None
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
        # Operator just set these by hand -> lock them so syncs enrich-only and never
        # clobber them (the Phase 2 lock-in guarantee). asset_tag is operator-only
        # regardless; name/serial are the fields syncs would otherwise overwrite.
        _locks = ['asset_tag']
        if (asset.name or '').strip():
            _locks.append('name')
        if asset.serial_number:
            _locks.append('serial_number')
        afo.lock_fields(asset, *_locks)
        asset.updated_at = datetime.utcnow()

        try:
            db.session.commit()
        except IntegrityError:
            # asset_tag and serial_number are UNIQUE — a collision used to 500.
            # Roll back and show a clear message instead.
            db.session.rollback()
            flash('That asset tag or serial number is already used by another asset. '
                  'Pick a unique value.', 'danger')
            return redirect(url_for('assets.edit_asset', asset_id=asset_id))

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
        # Operator assignment is authoritative -> lock so Intune can't revert it.
        afo.lock_fields(asset, 'employee_id')
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
        Asset.name.ilike(like),
        Asset.manufacturer.ilike(like),
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
@admin_required
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
@admin_required
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


def _asset_eol_check(app):
    """Daily: open a ticket for assets nearing warranty expiry or age EOL."""
    EOL_AGE_YEARS = 5        # auto-ticket assets older than this
    WARN_DAYS = 30           # warn this many days before warranty expiry

    while True:
        try:
            _time.sleep(86400)  # run once per day
            with app.app_context():
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
                            status='Open', priority='Low', source='system',
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


# Route groups split into sibling modules (registered on bp above)
from blueprints import assets_bulk, assets_intune, assets_io  # noqa: E402,F401
from blueprints.assets_intune import perform_intune_asset_sync, sync_assets_from_intune  # noqa: E402,F401  re-export