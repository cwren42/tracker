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
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
)


bp = Blueprint('dashboard', __name__)



@bp.route('/')
@login_required
@license_required
def index():
    # Eagle Eyes role: their home is the fleet monitor, not the full dashboard
    if current_user.role == 'eagle_eyes':
        return redirect(url_for('rmm.rmm_eagle_eyes_fleet'))
    # Base users and viewers: their home is tickets
    if current_user.role in ['base_user', 'viewer']:
        return redirect(url_for('tickets.tickets'))

    # Get user's dashboard configuration
    user_widgets = DashboardWidget.query.filter_by(
        user_id=current_user.id,
        enabled=True
    ).order_by(DashboardWidget.position).all()

    # If no custom widgets, use default layout
    if not user_widgets:
        user_widgets = get_default_widgets()
    
    # Gather all dashboard data
    dashboard_data = get_dashboard_data()

    # Last time the underlying device/asset data was refreshed (best-effort)
    last_report_run_display = None
    try:
        candidate_times_utc = []

        def _as_utc(dt: datetime) -> datetime:
            if not dt:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        try:
            from sqlalchemy import func
            max_intune = db.session.query(func.max(Asset.intune_last_sync)).scalar()
            max_intune_utc = _as_utc(max_intune) if max_intune else None
            if max_intune_utc:
                candidate_times_utc.append(max_intune_utc)
        except Exception:
            pass

        if candidate_times_utc:
            latest_utc = max(candidate_times_utc)
            last_report_run_display = latest_utc.strftime('%Y-%m-%d %H:%M UTC')
    except Exception:
        last_report_run_display = None
    
    return render_template('index.html', 
                         widgets=user_widgets,
                         data=dashboard_data,
                         edit_mode=request.args.get('edit') == 'true',
                         last_report_run_display=last_report_run_display)


@bp.route('/api/dashboard/live-status')
@login_required
def dashboard_live_status():
    """JSON snapshot of live agent counts (polled by client every 15s)."""
    try:
        online = db.session.execute(
            text("SELECT COUNT(*) FROM asset WHERE online_state='Online'")
        ).scalar() or 0
        offline = db.session.execute(
            text("SELECT COUNT(*) FROM asset WHERE online_state='Offline'")
        ).scalar() or 0
        open_tickets = db.session.execute(
            text("SELECT COUNT(*) FROM support_ticket WHERE status='Open'")
        ).scalar() or 0
        open_alerts = db.session.execute(
            text("SELECT COUNT(*) FROM monitoring_alert WHERE status='open'")
        ).scalar() or 0
        crit_cves = db.session.execute(
            text("SELECT COUNT(*) FROM device_vulnerability "
                 "WHERE severity IN ('Critical','High') AND status='Open'")
        ).scalar() or 0
    except Exception:
        return json.dumps({}), 200, {'Content-Type': 'application/json'}
    return json.dumps({
        'online': online, 'offline': offline,
        'open_tickets': open_tickets, 'open_alerts': open_alerts,
        'crit_cves': crit_cves,
        'ts': datetime.utcnow().strftime('%H:%M:%S')
    }), 200, {'Content-Type': 'application/json', 'Cache-Control': 'no-cache'}


def _action_center_groups():
    """Compute cross-module 'needs attention' groups from live data.
    Each group: key, title, severity, icon, count, link, sample (list of strings)."""
    def scalar(sql):
        return db.session.execute(text(sql)).scalar() or 0
    def names(sql):
        return [r[0] for r in db.session.execute(text(sql)).fetchall() if r[0]]

    defs = [
        ('offline', 'Devices offline 7+ days', 'warning', 'bi-wifi-off', '/assets',
         "FROM asset WHERE intune_last_sync IS NOT NULL AND intune_last_sync < now() - interval '7 days'",
         "SELECT name FROM asset WHERE intune_last_sync IS NOT NULL AND intune_last_sync < now() - interval '7 days' AND name IS NOT NULL ORDER BY intune_last_sync LIMIT 5"),
        ('low_storage', 'Low storage (<20% free)', 'warning', 'bi-hdd', '/assets',
         "FROM asset WHERE hardware_storage_total_gb > 0 AND hardware_storage_free_gb::float / hardware_storage_total_gb < 0.20",
         "SELECT name FROM asset WHERE hardware_storage_total_gb > 0 AND hardware_storage_free_gb::float / hardware_storage_total_gb < 0.20 AND name IS NOT NULL ORDER BY hardware_storage_free_gb::float / hardware_storage_total_gb LIMIT 5"),
        ('crit_alerts', 'Open critical alerts', 'danger', 'bi-exclamation-octagon', '/monitoring/alerts',
         "FROM monitoring_alert WHERE status='open' AND severity='critical'",
         "SELECT message FROM monitoring_alert WHERE status='open' AND severity='critical' ORDER BY triggered_at DESC LIMIT 5"),
        ('warn_alerts', 'Open warning alerts', 'warning', 'bi-exclamation-triangle', '/monitoring/alerts',
         "FROM monitoring_alert WHERE status='open' AND severity='warning'",
         "SELECT message FROM monitoring_alert WHERE status='open' AND severity='warning' ORDER BY triggered_at DESC LIMIT 5"),
        ('urgent_tickets', 'Urgent open tickets', 'danger', 'bi-fire', '/tickets',
         "FROM support_ticket WHERE status IN ('Open','In Progress') AND priority='Urgent'",
         "SELECT subject FROM support_ticket WHERE status IN ('Open','In Progress') AND priority='Urgent' ORDER BY created_at LIMIT 5"),
        ('unassigned_tickets', 'Unassigned open tickets', 'info', 'bi-person-dash', '/tickets',
         "FROM support_ticket WHERE status IN ('Open','In Progress') AND assigned_to_user_id IS NULL",
         "SELECT subject FROM support_ticket WHERE status IN ('Open','In Progress') AND assigned_to_user_id IS NULL ORDER BY created_at LIMIT 5"),
        ('warranty', 'Warranties expiring (30 days)', 'info', 'bi-calendar-x', '/assets',
         "FROM asset WHERE warranty_expiry IS NOT NULL AND warranty_expiry BETWEEN now()::date AND (now() + interval '30 days')::date",
         "SELECT name FROM asset WHERE warranty_expiry IS NOT NULL AND warranty_expiry BETWEEN now()::date AND (now() + interval '30 days')::date AND name IS NOT NULL ORDER BY warranty_expiry LIMIT 5"),
        ('licenses', 'Licenses expiring (30 days)', 'info', 'bi-key', '/licenses',
         "FROM license WHERE status='Active' AND expiry_date IS NOT NULL AND expiry_date <= (now() + interval '30 days')::date",
         "SELECT software_name FROM license WHERE status='Active' AND expiry_date IS NOT NULL AND expiry_date <= (now() + interval '30 days')::date ORDER BY expiry_date LIMIT 5"),
        ('vulns', 'Open critical/high vulnerabilities', 'danger', 'bi-bug', '/vulnerabilities',
         "FROM device_vulnerability WHERE status='Open' AND severity IN ('Critical','High')",
         None),
        ('dupes', 'Duplicate-name assets', 'info', 'bi-files', '/assets/find-duplicates',
         "FROM (SELECT name FROM asset GROUP BY name HAVING count(*)>1) d",
         None),
    ]
    groups = []
    for key, title, sev, icon, link, count_from, sample_sql in defs:
        try:
            cnt = scalar("SELECT count(*) " + count_from)
        except Exception:
            cnt = 0
        if cnt <= 0:
            continue
        sample = []
        if sample_sql:
            try:
                sample = names(sample_sql)
            except Exception:
                sample = []
        groups.append({'key': key, 'title': title, 'severity': sev, 'icon': icon,
                       'count': int(cnt), 'link': link, 'sample': sample})
    # Composite "at-risk" — devices with 2+ simultaneous risk factors (proactive:
    # catches compounding problems before any single one trips a hard alert).
    try:
        risk_names = names("""
          SELECT name FROM (
            SELECT a.name,
              (CASE WHEN a.hardware_storage_total_gb > 0
                     AND a.hardware_storage_free_gb::float / a.hardware_storage_total_gb < 0.20 THEN 1 ELSE 0 END
             + CASE WHEN EXISTS (SELECT 1 FROM device_vulnerability v
                     WHERE v.asset_id = a.id AND v.status = 'Open' AND v.severity IN ('Critical','High')) THEN 1 ELSE 0 END
             + CASE WHEN EXISTS (SELECT 1 FROM monitoring_alert m
                     WHERE m.asset_id = a.id AND m.status = 'open') THEN 1 ELSE 0 END
             + CASE WHEN a.warranty_expiry IS NOT NULL AND a.warranty_expiry < now()::date THEN 1 ELSE 0 END) AS score
            FROM asset a WHERE a.status <> 'Retired' AND a.name IS NOT NULL
          ) s WHERE s.score >= 2 ORDER BY s.score DESC
        """)
        if risk_names:
            groups.append({'key': 'at_risk', 'title': 'At-risk devices (multiple issues)',
                           'severity': 'danger', 'icon': 'bi-activity',
                           'count': len(risk_names), 'link': '/assets', 'sample': risk_names[:5]})
    except Exception:
        pass

    # most severe first, then biggest count
    order = {'danger': 0, 'warning': 1, 'info': 2}
    groups.sort(key=lambda g: (order.get(g['severity'], 3), -g['count']))
    return groups


@bp.route('/action-center')
@login_required
def action_center():
    """Deterministic cross-module 'needs attention' view with drill-downs."""
    groups = _action_center_groups()
    return render_template('action_center.html', groups=groups)


def get_default_widgets():
    """Return default dashboard widget configuration"""
    return [
        # Row 1 — system health cards
        {'id': 'tickets_summary', 'type': 'tickets_summary', 'title': 'Tickets', 'size': 'col-md-3', 'position': 0},
        {'id': 'monitoring_summary', 'type': 'monitoring_summary', 'title': 'Monitoring Alerts', 'size': 'col-md-3', 'position': 1},
        {'id': 'backup_summary', 'type': 'backup_summary', 'title': 'Backup Health', 'size': 'col-md-3', 'position': 2},
        {'id': 'licenses_summary', 'type': 'licenses_summary', 'title': 'Licenses', 'size': 'col-md-3', 'position': 3},
        # Row 2 — asset stats
        {'id': 'total_assets', 'type': 'stat', 'title': 'Total Assets', 'size': 'col-md-2', 'position': 4},
        {'id': 'in_use', 'type': 'stat', 'title': 'In Use', 'size': 'col-md-2', 'position': 5},
        {'id': 'available', 'type': 'stat', 'title': 'Available', 'size': 'col-md-2', 'position': 6},
        {'id': 'in_repair', 'type': 'stat', 'title': 'In Repair', 'size': 'col-md-2', 'position': 7},
        {'id': 'avg_age', 'type': 'stat', 'title': 'Avg Age', 'size': 'col-md-2', 'position': 8},
        {'id': 'replacement', 'type': 'stat', 'title': 'Need Replacement', 'size': 'col-md-2', 'position': 9},
        # Row 3 — alert cards
        {'id': 'noncompliant', 'type': 'alert', 'title': 'Non-Compliant Devices', 'size': 'col-md-3', 'position': 10, 'icon': 'bi-shield-exclamation'},
        {'id': 'low_storage', 'type': 'alert', 'title': 'Low Storage (<20%)', 'size': 'col-md-3', 'position': 11, 'icon': 'bi-hdd'},
        {'id': 'offline', 'type': 'alert', 'title': 'Offline 7+ Days', 'size': 'col-md-3', 'position': 12, 'icon': 'bi-wifi-off'},
        {'id': 'warranty_expiring', 'type': 'alert', 'title': 'Warranty Expiring Soon', 'size': 'col-md-3', 'position': 13, 'icon': 'bi-calendar-x'},
        # Row 4 — activity
        {'id': 'incomplete_assets', 'type': 'list', 'title': 'Assets Needing Information', 'size': 'col-md-6', 'position': 14},
    ]


def get_dashboard_data():
    """Gather all possible dashboard data"""
    def _format_dt_utc(dt: datetime | None) -> str | None:
        if not dt:
            return None

        try:
            if dt.tzinfo is None:
                dt_utc = dt.replace(tzinfo=timezone.utc)
            else:
                dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.strftime('%Y-%m-%d %H:%M UTC')
        except Exception:
            return None

    total_assets = Asset.query.count()
    in_use = Asset.query.filter_by(status='In Use').count()
    available = Asset.query.filter_by(status='Available').count()
    in_repair = Asset.query.filter_by(status='In Repair').count()
    
    # Check warranty expiring soon (within 30 days)
    thirty_days = datetime.utcnow().date() + timedelta(days=30)
    expiring_soon = Asset.query.filter(
        Asset.warranty_expiry.isnot(None),
        Asset.warranty_expiry <= thirty_days,
        Asset.warranty_expiry >= datetime.utcnow().date()
    ).count()
    
    # Check assets needing replacement (within 6 months)
    all_assets = Asset.query.all()
    replacement_needed = sum(1 for asset in all_assets if asset.needs_replacement())
    
    # NEW: Low storage devices (<20% free space)
    low_storage_assets = []
    for asset in all_assets:
        if asset.hardware_storage_total_gb and asset.hardware_storage_free_gb:
            free_pct = (asset.hardware_storage_free_gb / asset.hardware_storage_total_gb) * 100
            if free_pct < 20:
                low_storage_assets.append(asset)

    # Last updated for low storage is based on latest Intune sync among the matching assets
    low_storage_last_updated = None
    low_storage_sync_times = []
    for asset in low_storage_assets:
        if asset.intune_last_sync:
            try:
                low_storage_sync_times.append(
                    asset.intune_last_sync.replace(tzinfo=timezone.utc)
                    if asset.intune_last_sync.tzinfo is None
                    else asset.intune_last_sync.astimezone(timezone.utc)
                )
            except Exception:
                pass
    if low_storage_sync_times:
        low_storage_last_updated = _format_dt_utc(max(low_storage_sync_times))
    
    # NEW: Non-compliant devices
    noncompliant_assets = Asset.query.filter_by(online_state='noncompliant').all()

    # Last updated for non-compliant is based on latest Intune sync among the matching assets
    noncompliant_last_updated = None
    noncompliant_sync_times = []
    for asset in noncompliant_assets:
        if asset.intune_last_sync:
            try:
                noncompliant_sync_times.append(
                    asset.intune_last_sync.replace(tzinfo=timezone.utc)
                    if asset.intune_last_sync.tzinfo is None
                    else asset.intune_last_sync.astimezone(timezone.utc)
                )
            except Exception:
                pass
    if noncompliant_sync_times:
        noncompliant_last_updated = _format_dt_utc(max(noncompliant_sync_times))
    
    # NEW: Devices offline for 7+ days
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    offline_assets = Asset.query.filter(
        Asset.last_seen.isnot(None),
        Asset.last_seen < seven_days_ago
    ).all()
    
    # Calculate average asset age
    assets_with_age = [asset for asset in all_assets if asset.purchase_date]
    avg_age = sum(asset.get_age_years() for asset in assets_with_age) / len(assets_with_age) if assets_with_age else 0
    
    # Category breakdown for dashboard chart (with count and value)
    category_counts_raw = db.session.query(
        Asset.category, 
        db.func.count(Asset.id),
        db.func.sum(Asset.purchase_cost)
    ).group_by(Asset.category).all()
    category_counts = [[row[0], row[1], row[2] if row[2] else 0] for row in category_counts_raw]
    
    recent_assets = Asset.query.order_by(Asset.created_at.desc()).limit(5).all()
    
    # License statistics
    total_licenses = License.query.count()
    active_licenses = License.query.filter_by(status='Active').count()
    expired_licenses = License.query.filter_by(status='Expired').count()
    
    # License expiring soon (within 30 days)
    all_active_licenses = License.query.filter_by(status='Active').all()
    licenses_expiring_soon = sum(1 for lic in all_active_licenses if lic.is_expiring_soon(30))
    
    # Total license seats and usage
    total_license_seats = db.session.query(db.func.sum(License.total_licenses)).scalar() or 0
    total_assigned_seats = LicenseAssignment.query.filter_by(status='Active').count()
    
    # Annual license cost
    total_annual_license_cost = db.session.query(db.func.sum(License.annual_cost)).filter(
        License.annual_cost.isnot(None)
    ).scalar() or 0
    
    # Employee count
    total_employees = Employee.query.count()
    recent_employees = Employee.query.order_by(Employee.created_at.desc()).limit(5).all()
    
    # Warranty expiring assets
    warranty_expiring = Asset.query.filter(
        Asset.warranty_expiry.isnot(None),
        Asset.warranty_expiry <= thirty_days,
        Asset.warranty_expiry >= datetime.utcnow().date()
    ).limit(10).all()
    
    # Replacement needed assets
    replacement_list = [asset for asset in all_assets if asset.needs_replacement()][:10]
    
    # Licenses expiring soon
    licenses_expiring_list = [lic for lic in all_active_licenses if lic.is_expiring_soon(30)][:10]
    
    # Status breakdown
    status_counts_raw = db.session.query(
        Asset.status,
        db.func.count(Asset.id)
    ).group_by(Asset.status).all()
    status_counts = [[row[0], row[1]] for row in status_counts_raw]
    
    # Department breakdown
    dept_counts_raw = db.session.query(
        Employee.department,
        db.func.count(Asset.id)
    ).join(Asset, Employee.id == Asset.employee_id, isouter=True).group_by(Employee.department).all()
    dept_counts = [[row[0], row[1]] for row in dept_counts_raw]
    
    # Lifecycle stats
    lifecycle_stats = {}
    for asset in all_assets:
        if asset.purchase_date and asset.expected_life_years:
            status = asset.get_lifecycle_status()
            lifecycle_stats[status] = lifecycle_stats.get(status, 0) + 1
    
    # License vendor stats with annual cost
    license_vendor_stats_raw = db.session.query(
        License.vendor,
        db.func.count(License.id),
        db.func.sum(License.annual_cost)
    ).group_by(License.vendor).all()
    license_vendor_stats = [[row[0], row[1], row[2] if row[2] else 0] for row in license_vendor_stats_raw]

    # License type stats
    license_type_stats_raw = db.session.query(
        License.license_type,
        db.func.count(License.id)
    ).group_by(License.license_type).all()
    license_type_stats = [[row[0], row[1]] for row in license_type_stats_raw]

    # License utilization by software
    active_licenses_list = License.query.filter_by(status='Active').all()
    # One grouped query for active assignment counts per license (avoids an
    # N+1: previously this issued one COUNT query per active license).
    assigned_counts = dict(
        db.session.query(
            LicenseAssignment.license_id,
            db.func.count(LicenseAssignment.id)
        ).filter(LicenseAssignment.status == 'Active')
         .group_by(LicenseAssignment.license_id).all()
    )
    license_utilization = []
    for lic in active_licenses_list:
        assigned = assigned_counts.get(lic.id, 0)
        available = (lic.total_licenses or 0) - assigned
        utilization_pct = (assigned / lic.total_licenses * 100) if lic.total_licenses else 0
        license_utilization.append({
            'software': lic.software_name,
            'vendor': lic.vendor,
            'total': lic.total_licenses,
            'assigned': assigned,
            'available': available,
            'utilization': utilization_pct
        })
    
    # License type stats
    license_type_stats_raw = db.session.query(
        License.license_type,
        db.func.count(License.id)
    ).group_by(License.license_type).all()
    license_type_stats = [[row[0], row[1]] for row in license_type_stats_raw]
    
    # Incomplete assets (missing manufacturer, model, or serial)
    incomplete_assets = Asset.query.filter(
        db.or_(
            Asset.manufacturer.is_(None),
            Asset.manufacturer == '',
            Asset.model.is_(None),
            Asset.model == '',
            Asset.serial_number.is_(None),
            Asset.serial_number == ''
        )
    ).order_by(Asset.created_at.desc()).limit(10).all()

    # ── Tickets cross-module data ─────────────────────────────────────────────
    tickets_open = SupportTicket.query.filter(SupportTicket.status == 'Open').count()
    tickets_in_progress = SupportTicket.query.filter(SupportTicket.status == 'In Progress').count()
    tickets_unassigned = SupportTicket.query.filter(
        SupportTicket.status.in_(['Open', 'In Progress']),
        SupportTicket.assigned_to_user_id.is_(None)
    ).count()
    # Urgent open tickets (treat Urgent priority as highest severity)
    tickets_urgent = SupportTicket.query.filter(
        SupportTicket.status.in_(['Open', 'In Progress']),
        SupportTicket.priority == 'Urgent'
    ).count()
    recent_tickets = SupportTicket.query.filter(
        SupportTicket.status.in_(['Open', 'In Progress'])
    ).order_by(SupportTicket.created_at.desc()).limit(5).all()

    # ── Monitoring alerts cross-module data ───────────────────────────────────
    alerts_critical = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open',
        MonitoringAlert.severity == 'critical'
    ).count()
    alerts_warning = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open',
        MonitoringAlert.severity == 'warning'
    ).count()
    alerts_open_total = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open'
    ).count()
    recent_alerts_list = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open'
    ).order_by(MonitoringAlert.triggered_at.desc()).limit(5).all()

    # ── Proxmox / backup health cross-module data ─────────────────────────────
    proxmox_degraded_pools = ProxmoxZfsPool.query.filter(
        ProxmoxZfsPool.health != 'ONLINE'
    ).count()
    proxmox_total_pools = ProxmoxZfsPool.query.count()
    proxmox_stale_vms = ProxmoxBackupJob.query.filter(
        ProxmoxBackupJob.backup_status == 'stale'
    ).count()
    proxmox_total_vms = ProxmoxBackupJob.query.count()

    return {
        'total_assets': total_assets,
        'in_use': in_use,
        'available': available,
        'in_repair': in_repair,
        'expiring_soon': expiring_soon,
        'replacement_needed': replacement_needed,
        'avg_age': avg_age,
        'low_storage_count': len(low_storage_assets),
        'low_storage_assets': low_storage_assets[:10],
        'low_storage_last_updated': low_storage_last_updated,
        'noncompliant_count': len(noncompliant_assets),
        'noncompliant_assets': noncompliant_assets[:10],
        'noncompliant_last_updated': noncompliant_last_updated,
        'offline_count': len(offline_assets),
        'offline_assets': offline_assets[:10],
        'category_counts': category_counts,
        'recent_assets': recent_assets,
        'total_licenses': total_licenses,
        'active_licenses': active_licenses,
        'expired_licenses': expired_licenses,
        'licenses_expiring_soon': licenses_expiring_soon,
        'total_license_seats': total_license_seats,
        'total_assigned_seats': total_assigned_seats,
        'total_annual_license_cost': total_annual_license_cost,
        'total_employees': total_employees,
        'recent_employees': recent_employees,
        'warranty_expiring': warranty_expiring,
        'replacement_list': replacement_list,
        'licenses_expiring_list': licenses_expiring_list,
        'incomplete_assets': incomplete_assets,
        'status_counts': status_counts,
        'dept_counts': dept_counts,
        'lifecycle_stats': lifecycle_stats,
        'license_vendor_stats': license_vendor_stats,
        'license_type_stats': license_type_stats,
        'license_utilization': license_utilization,
        'total_value': sum(row[2] if row[2] else 0 for row in category_counts),
        # Tickets
        'tickets_open': tickets_open,
        'tickets_inprog': tickets_in_progress,
        'tickets_unassigned': tickets_unassigned,
        'tickets_urgent': tickets_urgent,
        'recent_tickets': recent_tickets,
        # Monitoring
        'alerts_critical': alerts_critical,
        'alerts_warning': alerts_warning,
        'alerts_open_total': alerts_open_total,
        'recent_alerts_list': recent_alerts_list,
        # Backups
        'proxmox_degraded_pools': proxmox_degraded_pools,
        'proxmox_total_pools': proxmox_total_pools,
        'proxmox_stale_vms': proxmox_stale_vms,
        'proxmox_total_vms': proxmox_total_vms,
    }


@bp.route('/dashboard/configure', methods=['GET', 'POST'])
@login_required
@license_required
def configure_dashboard():
    """Configure dashboard widgets"""
    if request.method == 'POST':
        try:
            # Get widget configuration from POST data
            widgets_data = request.get_json()
            
            # Delete existing widgets for this user
            DashboardWidget.query.filter_by(user_id=current_user.id).delete()
            
            # Create new widgets
            for widget_data in widgets_data:
                widget = DashboardWidget(
                    user_id=current_user.id,
                    widget_id=widget_data.get('id'),  # Store the widget identifier
                    widget_type=widget_data.get('type'),
                    title=widget_data.get('title'),
                    config=json.dumps(widget_data.get('config', {})),
                    position=widget_data.get('position', 0),
                    size=widget_data.get('size', 'col-md-3'),
                    enabled=widget_data.get('enabled', True)
                )
                db.session.add(widget)
            
            db.session.commit()
            return jsonify({'success': True, 'message': 'Dashboard updated successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 400
    
    # GET - return available widgets and current configuration
    available_widgets = get_available_widgets()
    current_widgets = DashboardWidget.query.filter_by(user_id=current_user.id).order_by(DashboardWidget.position).all()
    
    return render_template('configure_dashboard.html',
                         available_widgets=available_widgets,
                         current_widgets=current_widgets)


def get_available_widgets():
    """Return list of all available widget types"""
    return [
        {'id': 'total_assets', 'name': 'Total Assets', 'type': 'stat', 'icon': 'bi-box-seam', 'color': 'primary'},
        {'id': 'available', 'name': 'Available Assets', 'type': 'stat', 'icon': 'bi-check-circle', 'color': 'success'},
        {'id': 'in_use', 'name': 'In Use', 'type': 'stat', 'icon': 'bi-arrow-repeat', 'color': 'info'},
        {'id': 'in_repair', 'name': 'In Repair', 'type': 'stat', 'icon': 'bi-tools', 'color': 'warning'},
        {'id': 'avg_age', 'name': 'Average Age', 'type': 'stat', 'icon': 'bi-clock-history', 'color': 'secondary'},
        {'id': 'replacement', 'name': 'Need Replacement', 'type': 'stat', 'icon': 'bi-exclamation-circle', 'color': 'danger'},
        {'id': 'total_licenses', 'name': 'Total Licenses', 'type': 'stat', 'icon': 'bi-key', 'color': 'primary'},
        {'id': 'active_licenses', 'name': 'Active Licenses', 'type': 'stat', 'icon': 'bi-check-circle', 'color': 'success'},
        {'id': 'license_seats', 'name': 'License Seats Used', 'type': 'stat', 'icon': 'bi-people', 'color': 'info'},
        {'id': 'license_cost', 'name': 'Annual License Cost', 'type': 'stat', 'icon': 'bi-currency-dollar', 'color': 'warning'},
        {'id': 'total_employees', 'name': 'Total Employees', 'type': 'stat', 'icon': 'bi-people-fill', 'color': 'info'},
        {'id': 'overview_stats', 'name': 'Asset Overview Statistics', 'type': 'overview', 'icon': 'bi-graph-up', 'color': 'primary'},
        {'id': 'recent_assets', 'name': 'Recent Assets', 'type': 'list', 'icon': 'bi-list-ul', 'color': 'primary'},
        {'id': 'category_chart', 'name': 'Assets by Category', 'type': 'chart', 'icon': 'bi-pie-chart', 'color': 'success'},
        {'id': 'category_value_chart', 'name': 'Total Value by Category', 'type': 'chart', 'icon': 'bi-cash-stack', 'color': 'primary'},
        {'id': 'warranty_expiring', 'name': 'Warranty Expiring Soon', 'type': 'list', 'icon': 'bi-exclamation-triangle', 'color': 'warning'},
        {'id': 'replacement_needed', 'name': 'Replacement Needed', 'type': 'list', 'icon': 'bi-arrow-clockwise', 'color': 'danger'},
        {'id': 'licenses_expiring', 'name': 'Licenses Expiring', 'type': 'list', 'icon': 'bi-key', 'color': 'warning'},
        {'id': 'incomplete_assets', 'name': 'Incomplete Assets', 'type': 'list', 'icon': 'bi-clipboard-x', 'color': 'warning'},
        {'id': 'status_chart', 'name': 'Assets by Status', 'type': 'chart', 'icon': 'bi-bar-chart', 'color': 'info'},
        {'id': 'department_chart', 'name': 'Assets by Department', 'type': 'chart', 'icon': 'bi-building', 'color': 'success'},
        {'id': 'department_table', 'name': 'Department Asset Summary', 'type': 'table', 'icon': 'bi-table', 'color': 'info'},
        {'id': 'lifecycle_chart', 'name': 'Lifecycle Status', 'type': 'chart', 'icon': 'bi-clock-history', 'color': 'primary'},
        {'id': 'license_vendor_chart', 'name': 'Licenses by Vendor', 'type': 'chart', 'icon': 'bi-pie-chart-fill', 'color': 'info'},
        {'id': 'license_type_chart', 'name': 'Licenses by Type', 'type': 'chart', 'icon': 'bi-pie-chart-fill', 'color': 'info'},
        {'id': 'license_cost_chart', 'name': 'Annual License Costs', 'type': 'chart', 'icon': 'bi-cash-stack', 'color': 'success'},
        {'id': 'license_utilization_chart', 'name': 'License Utilization', 'type': 'chart', 'icon': 'bi-bar-chart', 'color': 'primary'},
        {'id': 'license_seat_table', 'name': 'License Seat Utilization', 'type': 'table', 'icon': 'bi-table', 'color': 'primary'},
        {'id': 'recent_employees', 'name': 'Recent Employees', 'type': 'list', 'icon': 'bi-people', 'color': 'info'},
    ]


@bp.route('/dashboard/reset', methods=['POST'])
@login_required
@license_required
def reset_dashboard():
    """Reset dashboard to default layout"""
    try:
        DashboardWidget.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        flash('Dashboard reset to default layout', 'success')
        return redirect(url_for('dashboard.index'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error resetting dashboard: {str(e)}', 'danger')
        return redirect(url_for('dashboard.index'))


@bp.route('/dashboard/add-widget', methods=['POST'])
@login_required
@license_required
def add_widget_to_dashboard():
    """Add a single widget to the dashboard"""
    try:
        data = request.get_json()
        widget_id = data.get('widget_id')
        widget_type = data.get('widget_type')
        widget_title = data.get('title')
        widget_size = data.get('size', 'col-md-4 widget-1-row')
        
        # Check if widget already exists
        existing = DashboardWidget.query.filter_by(
            user_id=current_user.id,
            widget_id=widget_id
        ).first()
        
        if existing:
            return jsonify({'success': False, 'message': 'Widget already exists on your dashboard'}), 400
        
        # Determine position based on widget type
        if widget_type == 'stat':
            # For stat widgets, insert after the last stat widget
            last_stat_position = db.session.query(db.func.max(DashboardWidget.position)).filter_by(
                user_id=current_user.id,
                widget_type='stat'
            ).scalar()
            
            if last_stat_position is not None:
                # Insert after the last stat widget
                new_position = last_stat_position + 1
                # Shift all widgets after this position
                DashboardWidget.query.filter(
                    DashboardWidget.user_id == current_user.id,
                    DashboardWidget.position >= new_position
                ).update({DashboardWidget.position: DashboardWidget.position + 1})
            else:
                # No stat widgets exist, put at the beginning
                new_position = 0
                # Shift all existing widgets
                DashboardWidget.query.filter_by(user_id=current_user.id).update(
                    {DashboardWidget.position: DashboardWidget.position + 1}
                )
        else:
            # For non-stat widgets, add to the end
            max_position = db.session.query(db.func.max(DashboardWidget.position)).filter_by(
                user_id=current_user.id
            ).scalar() or -1
            new_position = max_position + 1
        
        # Create new widget
        widget = DashboardWidget(
            user_id=current_user.id,
            widget_id=widget_id,
            widget_type=widget_type,
            title=widget_title,
            config=json.dumps({}),
            position=new_position,
            size=widget_size,
            enabled=True
        )
        db.session.add(widget)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'{widget_title} added to dashboard'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400

# ── IT Graph — "inside view of the brain": the connected world model ─────────
@bp.route('/it-graph')
@login_required
@admin_required
def it_graph():
    return render_template('it_graph.html')


@bp.route('/api/it-graph/data')
@login_required
@admin_required
def it_graph_data():
    """Nodes + links of the connected world model for the force-graph view."""
    from models import Employee, Asset, License, LicenseAssignment
    from soc2_models import M365User, IntuneDevice

    nodes, links = [], []
    emp_ids, asset_ids = set(), set()

    for e in Employee.query.all():
        nodes.append({'id': f'emp:{e.id}', 'label': e.name or f'Employee {e.id}', 'type': 'employee'})
        emp_ids.add(e.id)

    for a in Asset.query.all():
        nodes.append({'id': f'asset:{a.id}', 'label': a.name or a.asset_tag or f'Asset {a.id}', 'type': 'asset'})
        asset_ids.add(a.id)
        if a.employee_id in emp_ids:
            links.append({'source': f'emp:{a.employee_id}', 'target': f'asset:{a.id}', 'type': 'owns'})

    for u in M365User.query.filter_by(is_current=True):
        nodes.append({'id': f'm365:{u.id}', 'label': u.user_principal_name or u.display_name or f'M365 {u.id}', 'type': 'identity'})
        if u.employee_id in emp_ids:
            links.append({'source': f'emp:{u.employee_id}', 'target': f'm365:{u.id}', 'type': 'identity'})

    for d in IntuneDevice.query.filter_by(is_current=True):
        nodes.append({'id': f'mdm:{d.id}', 'label': d.device_name or f'Device {d.id}', 'type': 'device'})
        if d.asset_id in asset_ids:
            links.append({'source': f'asset:{d.asset_id}', 'target': f'mdm:{d.id}', 'type': 'managed'})

    # Licenses — only those assigned to a known employee
    lic_used = set()
    for la in LicenseAssignment.query.filter(LicenseAssignment.employee_id.isnot(None)).all():
        if la.employee_id in emp_ids and la.license_id:
            links.append({'source': f'emp:{la.employee_id}', 'target': f'lic:{la.license_id}', 'type': 'license'})
            lic_used.add(la.license_id)
    for lic in License.query.all():
        if lic.id in lic_used:
            nodes.append({'id': f'lic:{lic.id}', 'label': lic.software_name or f'License {lic.id}', 'type': 'license'})

    stats = {
        'employees': len(emp_ids), 'assets': len(asset_ids),
        'identities': sum(1 for n in nodes if n['type'] == 'identity'),
        'devices': sum(1 for n in nodes if n['type'] == 'device'),
        'licenses': len(lic_used), 'nodes': len(nodes), 'links': len(links),
    }
    return jsonify({'nodes': nodes, 'links': links, 'stats': stats})


# ── Approvals — the human-in-the-loop gate for risk-scored agent actions ─────────
@bp.route('/approvals')
@login_required
@admin_required
def approvals():
    """Pending approvals queue + recently resolved actions (the command ledger's
    human-decision surface). Medium/high-risk device actions land here instead of
    firing automatically — see approval.py / docs/AGENTIC_IT_OS_GAMEPLAN.md."""
    from models import CommandLedger
    pending = (CommandLedger.query
               .filter_by(status='awaiting_approval', approval_status='pending')
               .order_by(CommandLedger.created_at.desc()).all())
    recent = (CommandLedger.query
              .filter(CommandLedger.status.in_(['succeeded', 'failed', 'denied']))
              .order_by(CommandLedger.id.desc()).limit(25).all())

    def _plan(row):
        """(replay_dict, policy_note) parsed from the parked row's before_state."""
        bs = row.before_state if isinstance(row.before_state, dict) else {}
        bs = bs or {}
        return (bs.get('replay') or {}), bs.get('policy', '')

    return render_template('approvals.html', pending=pending, recent=recent, plan=_plan)


@bp.route('/approvals/<int:row_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_action(row_id):
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    claimed, info = workflow_engine.approve_action(row_id, approver)
    if not claimed:
        flash(info.get('error', 'Could not approve.'), 'warning')
    elif info.get('error'):
        flash(info['error'], 'warning')
    else:
        flash(f"Approved — {info.get('action_type', 'action')} is running. Refresh for the result.", 'success')
    return redirect(url_for('dashboard.approvals'))


@bp.route('/approvals/<int:row_id>/deny', methods=['POST'])
@login_required
@admin_required
def deny_action(row_id):
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    reason = (request.form.get('reason') or '').strip()
    success, output = workflow_engine.deny_action(row_id, approver, reason)
    flash("Action denied — it will not run." if success else (output.get('error') or 'Could not deny.'),
          'info' if success else 'warning')
    return redirect(url_for('dashboard.approvals'))


# ── Access view — "what does this person have access to?" (blast radius) ─────────
@bp.route('/employees/<int:employee_id>/access')
@login_required
@admin_required
def employee_access(employee_id):
    """Read-only blast-radius view over the connected world model (Track B identity
    graph): identity + privileged roles, owned assets, managed devices, and licenses.
    This is what an offboarding saga would need to revoke — and the brain answering
    'what does this person have access to?'."""
    import json as _json
    from models import Asset, License, LicenseAssignment
    from soc2_models import M365User, IntuneDevice, AdminRoleSnapshot

    emp = Employee.query.get_or_404(employee_id)

    # Identity (M365), via the Track B employee_id link.
    m365 = (M365User.query.filter_by(employee_id=emp.id, is_current=True)
            .order_by(M365User.sync_date.desc()).first())
    admin_roles, m365_licenses = [], []
    if m365:
        try:
            admin_roles = [r for r in (_json.loads(m365.admin_roles or '[]')) if r]
        except Exception:
            admin_roles = []
        try:
            m365_licenses = _json.loads(m365.licenses or '[]')
        except Exception:
            m365_licenses = []
        # Fold in any active privileged-role snapshots not already listed.
        for s in AdminRoleSnapshot.query.filter_by(
                user_principal_name=m365.user_principal_name, status='active').all():
            if s.role_name and s.role_name not in admin_roles:
                admin_roles.append(s.role_name)

    # Owned assets.
    assets = Asset.query.filter_by(employee_id=emp.id).all()
    asset_ids = {a.id for a in assets}

    # Managed devices: linked via an owned asset (Track B asset_id) OR by UPN match.
    upn = (m365.user_principal_name or '').lower() if m365 else ''
    devices, seen = [], set()
    for d in IntuneDevice.query.filter_by(is_current=True):
        if d.id in seen:
            continue
        if d.asset_id in asset_ids or (upn and (d.user_principal_name or '').lower() == upn):
            devices.append(d)
            seen.add(d.id)

    # Internally-tracked license assignments.
    lic_assigns = LicenseAssignment.query.filter_by(employee_id=emp.id).all()
    lic_ids = [la.license_id for la in lic_assigns if la.license_id]
    lic_map = {l.id: l for l in License.query.filter(License.id.in_(lic_ids)).all()} if lic_ids else {}

    summary = {
        'assets': len(assets),
        'devices': len(devices),
        'licenses': len(lic_assigns) + len(m365_licenses),
        'admin_roles': len(admin_roles),
        'account_enabled': (m365.account_enabled if m365 else None),
        'has_identity': bool(m365),
    }
    return render_template('employee_access.html', emp=emp, m365=m365,
                           admin_roles=admin_roles, m365_licenses=m365_licenses,
                           assets=assets, devices=devices, lic_assigns=lic_assigns,
                           lic_map=lic_map, summary=summary)


# ── Event bus monitor — the brain's nervous system, made visible ─────────────────
@bp.route('/events')
@login_required
@admin_required
def events():
    """Recent events on the bus + dispatch status. Read-only window onto event_outbox."""
    from sqlalchemy import text
    rows, counts = [], {}
    try:
        res = db.session.execute(text(
            "SELECT id, event_type, source, status, attempts, last_error, "
            "created_at, dispatched_at FROM event_outbox ORDER BY id DESC LIMIT 100"))
        rows = [dict(r._mapping) for r in res]
        cres = db.session.execute(text(
            "SELECT status, COUNT(*) AS n FROM event_outbox GROUP BY status"))
        counts = {r._mapping['status']: r._mapping['n'] for r in cres}
    except Exception:
        # Table may not exist yet (dispatcher creates it at startup).
        db.session.rollback()
    return render_template('events.html', rows=rows, counts=counts)


@bp.route('/ledger/<int:row_id>')
@login_required
@admin_required
def ledger_detail(row_id):
    """Full record of one command-ledger action: who/planned-by, risk + approval, before/after
    state, verification, timing, and any sibling actions sharing its correlation id."""
    import json as _json
    from models import CommandLedger
    row = CommandLedger.query.get_or_404(row_id)

    def _pretty(v):
        if v is None:
            return None
        try:
            return _json.dumps(v if isinstance(v, (dict, list)) else _json.loads(v), indent=2)
        except Exception:
            return str(v)

    siblings = []
    if row.correlation_id:
        siblings = (CommandLedger.query
                    .filter(CommandLedger.correlation_id == row.correlation_id,
                            CommandLedger.id != row.id)
                    .order_by(CommandLedger.id).all())
    return render_template('ledger_detail.html', row=row,
                           before_pretty=_pretty(row.before_state),
                           after_pretty=_pretty(row.after_state), siblings=siblings)


@bp.route('/events/<int:event_id>/requeue', methods=['POST'])
@login_required
@admin_required
def event_requeue(event_id):
    """Reset a failed (status='error') event back to pending so the dispatcher retries it."""
    from sqlalchemy import text
    try:
        res = db.session.execute(text(
            "UPDATE event_outbox SET status='pending', attempts=0, last_error=NULL "
            "WHERE id=:i AND status='error'"), {"i": event_id})
        db.session.commit()
        if res.rowcount:
            flash(f'Event #{event_id} requeued — the dispatcher will retry it.', 'success')
        else:
            flash('Event not found or not in an error state.', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Requeue failed: {e}', 'danger')
    return redirect(url_for('dashboard.events'))


# ── Mission Control — the single pane onto the whole agentic OS ──────────────────
@bp.route('/mission-control')
@login_required
@admin_required
def mission_control():
    """One view of the brain: world-model coverage, the approval queue, the command
    ledger, the event bus, and workflow activity — the layers stitched together."""
    from sqlalchemy import text
    from models import Asset, License, LicenseAssignment, CommandLedger
    from soc2_models import M365User, IntuneDevice

    def _scalar(sql):
        try:
            return db.session.execute(text(sql)).scalar() or 0
        except Exception:
            db.session.rollback()
            return 0

    # World model + Track B link coverage.
    emp_n = Employee.query.count()
    asset_n = Asset.query.count()
    m365_total = M365User.query.filter_by(is_current=True).count()
    m365_linked = M365User.query.filter(M365User.is_current == True, M365User.employee_id.isnot(None)).count()
    intune_total = IntuneDevice.query.filter_by(is_current=True).count()
    intune_linked = IntuneDevice.query.filter(IntuneDevice.is_current == True, IntuneDevice.asset_id.isnot(None)).count()
    world = {
        'employees': emp_n, 'assets': asset_n,
        'm365_total': m365_total, 'm365_linked': m365_linked,
        'm365_pct': round(100 * m365_linked / m365_total) if m365_total else 0,
        'intune_total': intune_total, 'intune_linked': intune_linked,
        'intune_pct': round(100 * intune_linked / intune_total) if intune_total else 0,
    }

    # Approvals queue (pending) + a small preview.
    pending = (CommandLedger.query
               .filter_by(status='awaiting_approval', approval_status='pending')
               .order_by(CommandLedger.created_at.desc()).limit(6).all())
    pending_n = (CommandLedger.query
                 .filter_by(status='awaiting_approval', approval_status='pending').count())

    # Command ledger — recent + status mix.
    recent_actions = CommandLedger.query.order_by(CommandLedger.id.desc()).limit(8).all()
    ledger_counts = {}
    try:
        for r in db.session.execute(text("SELECT status, COUNT(*) AS n FROM command_ledger GROUP BY status")):
            ledger_counts[r._mapping['status']] = r._mapping['n']
    except Exception:
        db.session.rollback()

    # Event bus — status mix + recent.
    event_counts, recent_events = {}, []
    try:
        for r in db.session.execute(text("SELECT status, COUNT(*) AS n FROM event_outbox GROUP BY status")):
            event_counts[r._mapping['status']] = r._mapping['n']
        recent_events = [dict(r._mapping) for r in db.session.execute(text(
            "SELECT id, event_type, source, status, created_at FROM event_outbox ORDER BY id DESC LIMIT 6"))]
    except Exception:
        db.session.rollback()

    # Workflows — enabled + run status mix + recent runs.
    wf_enabled = _scalar("SELECT COUNT(*) FROM workflow_definitions WHERE enabled=true")
    run_counts, recent_runs = {}, []
    try:
        for r in db.session.execute(text("SELECT status, COUNT(*) AS n FROM workflow_runs GROUP BY status")):
            run_counts[r._mapping['status']] = r._mapping['n']
        recent_runs = [dict(r._mapping) for r in db.session.execute(text(
            "SELECT wr.id, wr.status, wr.started_at, wd.name FROM workflow_runs wr "
            "LEFT JOIN workflow_definitions wd ON wd.id = wr.workflow_id "
            "ORDER BY wr.id DESC LIMIT 6"))]
    except Exception:
        db.session.rollback()

    return render_template('mission_control.html',
                           world=world, pending=pending, pending_n=pending_n,
                           recent_actions=recent_actions, ledger_counts=ledger_counts,
                           event_counts=event_counts, recent_events=recent_events,
                           wf_enabled=wf_enabled, run_counts=run_counts, recent_runs=recent_runs)


# ── Knowledge Agent — semantic search + RAG over ISMS/system docs ────────────────
@bp.route('/knowledge', methods=['GET', 'POST'])
@login_required
@admin_required
def knowledge():
    """Ask the IT knowledge base. Semantic retrieval over the ISMS policies + system docs,
    answered with citations (knowledge_agent). Read-only against the corpus."""
    import knowledge_agent
    query = (request.form.get('q') or request.args.get('q') or '').strip()
    result, answer_html = None, None
    chunk_count = knowledge_agent.count()
    runbook_count = knowledge_agent.count('runbook')
    if query:
        try:
            result = knowledge_agent.answer(query)
            import markdown as _md
            answer_html = _md.markdown(result.get('answer') or '', extensions=['extra'])
        except ValueError as e:
            flash(str(e), 'warning')  # e.g. no OpenAI key configured
        except Exception as e:
            flash(f'Knowledge search failed: {e}', 'danger')
    return render_template('knowledge.html', query=query, result=result,
                           answer_html=answer_html, chunk_count=chunk_count,
                           runbook_count=runbook_count)


@bp.route('/knowledge/add', methods=['POST'])
@login_required
@admin_required
def knowledge_add():
    """Operator-authored how-to / SOP added directly to the knowledge base."""
    import knowledge_agent
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    if not content:
        flash('Content is required.', 'warning')
        return redirect(url_for('dashboard.knowledge'))
    try:
        knowledge_agent.add_manual(title, content)
        flash('Knowledge entry added and indexed.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Could not add knowledge: {e}', 'danger')
    return redirect(url_for('dashboard.knowledge'))


@bp.route('/knowledge/reindex', methods=['POST'])
@login_required
@admin_required
def knowledge_reindex():
    import knowledge_agent
    try:
        n = knowledge_agent.reindex()
        flash(f'Knowledge base reindexed — {n} chunks embedded from the ISMS + system docs.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Reindex failed: {e}', 'danger')
    return redirect(url_for('dashboard.knowledge'))
