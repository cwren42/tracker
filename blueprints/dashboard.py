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
    # HR: their home is the Employees page (where onboarding/offboarding lives)
    if current_user.role == 'hr':
        return redirect(url_for('employees.employees'))
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
        ('offline', 'Agents offline 7+ days', 'warning', 'bi-wifi-off', '/assets',
         # Real connectivity: an enabled RMM agent silent 7+ days. NOT intune_last_sync,
         # which is Intune sync-lag (flags live boxes as "offline"). See online_state-vs-compliance.
         "FROM rmm_agent ra WHERE ra.enabled AND (ra.last_seen_at IS NULL OR ra.last_seen_at < now() - interval '7 days')",
         "SELECT a.name FROM rmm_agent ra JOIN asset a ON a.id = ra.asset_id WHERE ra.enabled AND (ra.last_seen_at IS NULL OR ra.last_seen_at < now() - interval '7 days') AND a.name IS NOT NULL ORDER BY ra.last_seen_at NULLS FIRST LIMIT 5"),
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

    # Open critical/high CVEs — count DISTINCT CVEs (device_vulnerability rows are
    # device-CVE PAIRS, which over-count ~10x), on non-retired assets, so the headline
    # is the real vulnerability count, not an alarmist pair total.
    try:
        vcnt = scalar("""SELECT count(DISTINCT dv.cve_id) FROM device_vulnerability dv
                         JOIN asset a ON a.id = dv.asset_id
                         WHERE dv.status='Open' AND dv.severity IN ('Critical','High')
                           AND a.status <> 'Retired'""")
        if vcnt > 0:
            vsample = names("""SELECT a.name FROM asset a
                               WHERE a.status <> 'Retired' AND a.name IS NOT NULL
                                 AND EXISTS (SELECT 1 FROM device_vulnerability v
                                     WHERE v.asset_id=a.id AND v.status='Open' AND v.severity IN ('Critical','High'))
                               ORDER BY (SELECT count(*) FROM device_vulnerability v
                                     WHERE v.asset_id=a.id AND v.status='Open' AND v.severity IN ('Critical','High')) DESC
                               LIMIT 5""")
            groups.append({'key': 'vulns', 'title': 'Open critical/high CVEs', 'severity': 'danger',
                           'icon': 'bi-bug', 'count': int(vcnt), 'link': '/vulnerabilities', 'sample': vsample})
    except Exception:
        pass

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
@admin_required
def action_center():
    """Backward-compat: this view is now the Action Center tab of the Mission Control
    hub. Redirect bookmarks/old links there (keeps existing url_for references working)."""
    return redirect(url_for('dashboard.mission_control', tab='action-center'))


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
    # Assignment is authoritative: an assigned asset is never counted as Available.
    available = Asset.query.filter_by(status='Available', employee_id=None).count()
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
    
    # NEW: Non-compliant devices.
    # Compliance lives in intune_compliance_state, NOT online_state
    # (online_state is live connectivity: Online/Offline).
    noncompliant_assets = Asset.query.filter_by(intune_compliance_state='noncompliant').all()

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

# ── Approvals — the human-in-the-loop gate for risk-scored agent actions ─────────
# Default window for the resolved/history view so it never becomes an endless scroll.
APPROVALS_HISTORY_DAYS = 30
_TIER_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
_SENS_KEYS = ('pass', 'pwd', 'secret', 'token', 'key', 'credential')


def _asset_name_map(rows):
    """Batch-resolve the asset IDs referenced by these ledger rows to friendly asset names,
    so the 'Target' column shows e.g. 'Ken-Lenovo' instead of the bare id '966'."""
    from models import Asset
    ids = set()
    for r in rows:
        bs = r.before_state if isinstance(r.before_state, dict) else {}
        ctx = ((bs or {}).get('replay') or {}).get('ctx') or {}
        aid = ctx.get('asset_id')
        # Only treat a bare object_id as an asset id for asset-scoped actions — an
        # offboard's object_id is an EMPLOYEE id and must NOT resolve to an asset name.
        if not aid and str(r.object_id or '').isdigit() and (r.object_type or 'asset') == 'asset':
            aid = r.object_id
        if aid is not None:
            try:
                ids.add(int(aid))
            except (TypeError, ValueError):
                pass
    if not ids:
        return {}
    return {a.id: (a.name or a.asset_tag or ('asset ' + str(a.id)))
            for a in Asset.query.filter(Asset.id.in_(ids)).all()}


def _action_effect(at, ctx, device, is_quar):
    """Plain-English 'what approving this does' — so an approval is self-explanatory months
    later, not just a cryptic action name."""
    who = device or 'the target'
    g = ctx.get
    if at == 'offboard_employee':
        return (f"Offboards {who}: unassigns all their devices (set to 'Pending Return' for "
                "collection), returns their software licenses, hides them from the active "
                "roster, and opens an offboarding checklist ticket. Does NOT delete the AD/M365 account.")
    if at == 'delete_ad_user':
        since = g('disabled_since')
        return (f"PERMANENTLY deletes the Active Directory account for {who}"
                + (f" (disabled since {since})" if since else "")
                + ". This removes the AD object and propagates to M365 — irreversible.")
    if at == 'apply_fix':
        return f"Runs the vetted fix “{g('fix_name') or 'fix'}” as SYSTEM on {who}; closes the linked ticket if it succeeds."
    if at == 'install_local_tool':
        return f"Silently installs {g('file_name') or 'the tool'} as SYSTEM on {who} (locked to the approved file's hash)."
    if at == 'uninstall_software':
        return f"Uninstalls {g('file_name') or 'the software'} from {who}."
    if at == 'release_quarantine':
        return (f"Releases this quarantined email to {g('recipient') or 'the recipient'}’s inbox."
                if is_quar else
                "Whitelists the sender domain in Exchange — future mail from it is delivered (live mail-flow change).")
    if at == 'unlock_account':   return f"Unlocks the AD account {g('username') or g('upn') or who}."
    if at == 'disable_ad_user':  return f"Disables the AD account {g('username') or who} — they can no longer sign in."
    if at == 'enable_ad_user':   return f"Re-enables the AD account {g('username') or who}."
    if at == 'reset_password':   return f"Resets the AD password for {g('username') or who}."
    if at == 'create_user':      return f"Creates a new AD user {g('username') or g('upn') or ''}."
    if at == 'add_to_group':     return f"Adds {g('username') or who} to the AD group {g('group_name') or g('group_dn') or ''}."
    if at == 'remove_from_group':return f"Removes {g('username') or who} from the AD group {g('group_name') or g('group_dn') or ''}."
    if at == 'reboot_device':    return f"Reboots {who} now."
    if at == 'shutdown_device':  return f"Shuts down {who} now."
    if at == 'lock_device':      return f"Locks the screen on {who}."
    if at == 'apply_gpo':        return f"Forces a Group Policy update on {who}."
    if at in ('deploy_patch', 'install_patches'): return f"Installs the approved Windows update(s) on {who}."
    if at == 'run_script':       return f"Runs a script as SYSTEM on {who}."
    if at == 'azure_sync':       return "Triggers an Azure AD Connect delta sync."
    return f"Runs the “{(at or '').replace('_', ' ')}” action on {who}."


def _ledger_display(row, asset_names=None):
    """Serialize a CommandLedger row to a JSON-friendly dict for the approvals inbox.
    Sensitive config values are redacted here, server-side. `asset_names` (from
    _asset_name_map) turns a bare asset id into a friendly device name."""
    bs = row.before_state if isinstance(row.before_state, dict) else {}
    bs = bs or {}
    rp = bs.get('replay') or {}
    cfg = rp.get('config') or {}
    ctx = rp.get('ctx') or {}
    at = row.action_type or ''
    params = {k: ('[redacted]' if any(s in k.lower() for s in _SENS_KEYS) else v)
              for k, v in cfg.items() if not k.startswith('_')}
    device = ctx.get('hostname') or ''
    if not device and (row.object_type == 'employee' or at in ('offboard_employee', 'delete_ad_user')):
        # Employee-scoped action → target is the person, never an asset.
        device = ctx.get('employee_name') or ('employee #' + str(row.object_id or ''))
    if not device:
        aid = ctx.get('asset_id')
        if not aid and str(row.object_id or '').isdigit() and (row.object_type or 'asset') == 'asset':
            aid = row.object_id
        try:
            aid_i = int(aid) if aid is not None else None
        except (TypeError, ValueError):
            aid_i = None
        if aid_i is not None and asset_names and aid_i in asset_names:
            device = asset_names[aid_i]
        elif aid is not None:
            device = 'asset ' + str(aid)
        else:
            device = row.object_id or ''
    is_quar = (ctx.get('release_status') == 'Quarantined')
    if at == 'release_quarantine':
        label = 'Release from quarantine' if is_quar else 'Whitelist sender domain'
        summary = ctx.get('subject') or '(no subject)'
    elif at == 'install_local_tool':
        label, summary = 'Install software', (ctx.get('file_name') or '(installer)')
    elif at == 'apply_fix':
        label = 'Apply fix'
        summary = ctx.get('fix_name') or ('fix #' + str(ctx.get('fix_id') or ''))
    elif at == 'unlock_account':
        label = 'Unlock account'
        summary = ctx.get('username') or ctx.get('upn') or ''
    elif at == 'offboard_employee':
        label = 'Offboard employee'
        summary = ctx.get('employee_name') or rp.get('node_label') or ''
    elif at == 'onboard_employee':
        label = 'Onboard new hire'
        summary = ctx.get('employee_name') or rp.get('node_label') or ''
    elif at == 'delete_ad_user':
        nm = ctx.get('employee_name') or rp.get('node_label') or ''
        label = f'Delete AD account — {nm}' if nm else 'Delete AD account'
        since = bs.get('disabled_since') or ctx.get('disabled_since')
        summary = (f'disabled since {since}; retention elapsed — this permanently removes the AD object.'
                   if since else 'retention elapsed — this permanently removes the AD object.')
    else:
        label = rp.get('node_label') or at.replace('_', ' ').title()
        summary = rp.get('node_label') or ''
    return {
        'id': row.id, 'action_type': at, 'label': label, 'summary': summary,
        'effect': _action_effect(at, ctx, device, is_quar),
        'device': device, 'risk_tier': row.risk_tier or 'low',
        'requested_by': row.requested_by or '', 'correlation_id': row.correlation_id or '',
        'approval_status': row.approval_status, 'status': row.status,
        'verification_status': row.verification_status, 'verification_detail': row.verification_detail or '',
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'completed_at': row.completed_at.isoformat() if row.completed_at else None,
        'is_email': at == 'release_quarantine', 'is_quarantined': is_quar,
        'message_id': ctx.get('message_id'), 'sender': ctx.get('sender'),
        'recipient': ctx.get('recipient'), 'release_status': ctx.get('release_status'),
        'justification': ctx.get('justification'), 'sha256': ctx.get('sha256'), 'args': ctx.get('args'),
        'ai': ({'recommended': ctx.get('ai_recommended'), 'confidence': ctx.get('ai_confidence'),
                'verdict': ctx.get('ai_verdict'), 'rationale': ctx.get('ai_rationale')}
               if ctx.get('ai_recommended') else None),
        'policy': bs.get('policy', ''), 'params': params,
        'is_onboard': at == 'onboard_employee',
        'onboard': (bs.get('onboard') if at == 'onboard_employee' else None),
        'is_delete': at == 'delete_ad_user',
        'disabled_since': bs.get('disabled_since') or ctx.get('disabled_since'),
        'ledger_url': url_for('dashboard.ledger_detail', row_id=row.id),
    }


def _query_pending():
    """All parked approvals, sorted high-risk first then oldest-first so aging items surface."""
    from models import CommandLedger
    rows = (CommandLedger.query
            .filter_by(status='awaiting_approval', approval_status='pending').all())
    names = _asset_name_map(rows)
    items = [_ledger_display(r, names) for r in rows]
    items.sort(key=lambda i: (_TIER_ORDER.get(i['risk_tier'], 4), i['created_at'] or ''))
    return items


def _query_resolved(type_=None, risk=None, device=None, q=None,
                    days=APPROVALS_HISTORY_DAYS, offset=0, limit=50):
    """Resolved history with filters + a default time window (auto-archive). device/q are
    matched against the JSON-derived display fields after serialization; type/risk/window
    are pushed into SQL. Over-fetches when text filters are active so paging stays sane."""
    from models import CommandLedger
    qry = CommandLedger.query.filter(CommandLedger.status.in_(['succeeded', 'failed', 'denied']))
    if type_:
        qry = qry.filter(CommandLedger.action_type == type_)
    if risk:
        qry = qry.filter(CommandLedger.risk_tier == risk)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = APPROVALS_HISTORY_DAYS
    if days > 0:
        qry = qry.filter(CommandLedger.created_at >= (now_mst() - timedelta(days=days)))
    qry = qry.order_by(CommandLedger.id.desc())
    text_filter = bool(device or q)
    fetch = (limit + 1) if not text_filter else (limit * 6 + 1)
    rows = qry.offset(offset).limit(fetch).all()
    names = _asset_name_map(rows)
    items = [_ledger_display(r, names) for r in rows]
    if device:
        dl = device.lower()
        items = [i for i in items if dl in (i['device'] or '').lower()]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i['summary'] or '').lower()
                 or ql in (i['device'] or '').lower() or ql in (i['label'] or '').lower()
                 or ql in (i['requested_by'] or '').lower() or ql == str(i['id'])]
    has_more = len(items) > limit
    return items[:limit], has_more


@bp.route('/approvals')
@login_required
@admin_required
def approvals():
    """Backward-compat: the agentic action-gate inbox is now the Approvals tab of the
    Mission Control hub. Redirect old links/bookmarks (and the approval action routes'
    non-AJAX redirects) there — the interactive actions live in the tab now."""
    return redirect(url_for('dashboard.mission_control', tab='approvals'))


def _ou_hierarchy(ous, base_dn):
    """Turn list_ous's flat [{'name','dn'}] into a tree-ordered list with each OU's
    'depth' and 'path' (relative to the CirqueUsers base), so the picker can indent
    children under their parent (CirqueUS → Engineering/Production/…; CirqueTaiwan →
    China/…). Parent OUs sort before their children; siblings sort alphabetically.

    The base (CirqueUsers itself) is dropped — it's the implied root, not a choice.
    Falls back to the raw list (alphabetical) if base_dn is unknown."""
    if not base_dn:
        return ous
    b = base_dn.strip().lower()

    def rel_names(dn):
        # OU RDN values ABOVE the base, top-most first. e.g. for
        # OU=Engineering,OU=CirqueUS,OU=CirqueUsers,... → ['CirqueUS','Engineering'].
        d = dn.strip()
        if not d.lower().endswith(b):
            rdns = [p for p in d.split(',') if p.strip().lower().startswith('ou=')]
            return [p.split('=', 1)[1] for p in rdns][::-1]
        prefix = d[:len(d) - len(base_dn)].rstrip(',')
        if not prefix:
            return []  # the base OU itself
        rdns = [p for p in prefix.split(',') if p.strip().lower().startswith('ou=')]
        return [p.split('=', 1)[1] for p in rdns][::-1]

    enriched = []
    for o in ous:
        rel = rel_names(o.get('dn') or '')
        if not rel:
            continue  # drop the CirqueUsers root — it's the implied base
        enriched.append({**o, 'name': rel[-1], 'depth': len(rel) - 1,
                         'path': ' / '.join(rel),
                         '_sort': [r.lower() for r in rel]})
    enriched.sort(key=lambda o: o['_sort'])
    for o in enriched:
        o.pop('_sort', None)
    return enriched


def _onboard_directory_options():
    """Read-only OU + group lists for the onboarding approval card. list_ous resolves
    its base from config.user_ou_dn (Setting ad_user_ou_dn) or derives
    OU=CirqueUsers,<base_dn>; list_groups uses Setting ad_groups_ou_dn when set, else
    derives OU=CirqueGroups,<base_dn>. Both fail-soft to []."""
    try:
        from models import Setting
        from ldap_service import LDAPService, load_ad_config
        cfg = load_ad_config(Setting)
        if not cfg.enabled:
            return [], []
        svc = LDAPService(cfg)
        ous = svc.list_ous()
        # The picker base is always CirqueUsers (under CirqueCompany). list_ous returns a
        # FLAT subtree dump; compute each OU's path + depth relative to that base so the
        # approval card can render the real hierarchy (CirqueUS / CirqueTaiwan and the
        # folders inside each) instead of one alphabetical list.
        base_row = Setting.query.filter_by(key='ad_user_ou_dn').first()
        base_dn = (base_row.value if base_row and base_row.value else '').strip()
        ous = _ou_hierarchy(ous, base_dn)
        groups_base = Setting.query.filter_by(key='ad_groups_ou_dn').first()
        groups = svc.list_groups(groups_base.value if groups_base and groups_base.value else None)
        # Tag each group by its sub-OU so the approval card can segregate Privileged
        # from Standard (least-privilege at a glance). Derived from the DN.
        for g in groups:
            dn = (g.get('dn') or '')
            if 'OU=Privileged Groups' in dn:
                g['category'] = 'Privileged'
            elif 'OU=Standard Groups' in dn:
                g['category'] = 'Standard'
            else:
                g['category'] = 'Other'
        return ous, groups
    except Exception:
        current_app.logger.exception('onboard directory options failed')
        return [], []


@bp.route('/api/approvals/list')
@login_required
@admin_required
def approvals_list():
    """Filtered/paginated feed for the approvals inbox (resolved history + pending refresh)."""
    scope = request.args.get('scope', 'resolved')
    if scope == 'pending':
        return jsonify({'items': _query_pending(), 'has_more': False})
    items, has_more = _query_resolved(
        type_=request.args.get('type') or None, risk=request.args.get('risk') or None,
        device=request.args.get('device') or None, q=request.args.get('q') or None,
        days=request.args.get('days', APPROVALS_HISTORY_DAYS),
        offset=int(request.args.get('offset', 0) or 0),
        limit=int(request.args.get('limit', 50) or 50))
    return jsonify({'items': items, 'has_more': has_more})


@bp.route('/approvals/bulk', methods=['POST'])
@login_required
@admin_required
def approvals_bulk():
    """Approve or deny several parked actions at once. Email (release_quarantine) items are
    NOT bulk-approvable — they need the release-vs-whitelist decision — so the UI excludes
    them from bulk-approve; bulk-deny applies to anything."""
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    reason = (data.get('reason') or '').strip()
    results = []
    for raw in (data.get('ids') or []):
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        try:
            if action == 'approve':
                # Permanent/irreversible actions are NOT bulk-approvable — they require the
                # individual one-click confirm (don't rely on the client-side JS exclusion).
                # delete_ad_user permanently removes an AD object; onboard_employee needs the
                # OU/groups custom approve route.
                at = workflow_engine.get_action_type(rid)
                if at in ('delete_ad_user', 'onboard_employee'):
                    results.append({'id': rid, 'ok': False, 'skipped': True,
                                    'error': f'{at} cannot be bulk-approved — use the individual confirm.'})
                    continue
                claimed, info = workflow_engine.approve_action(rid, approver)
                ok = bool(claimed) and not info.get('error')
                results.append({'id': rid, 'ok': ok, 'error': None if ok else info.get('error', 'could not approve')})
            elif action == 'deny':
                success, output = workflow_engine.deny_action(rid, approver, reason)
                results.append({'id': rid, 'ok': bool(success),
                                'error': None if success else output.get('error', 'could not deny')})
            else:
                results.append({'id': rid, 'ok': False, 'error': 'unknown bulk action'})
        except Exception as e:
            results.append({'id': rid, 'ok': False, 'error': str(e)})
    return jsonify({'results': results})


@bp.route('/approvals/<int:row_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_action(row_id):
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    claimed, info = workflow_engine.approve_action(row_id, approver)
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if ajax:
        if not claimed:
            return jsonify({'ok': False, 'error': info.get('error', 'Could not approve.')}), 409
        if info.get('error'):
            return jsonify({'ok': False, 'error': info['error']}), 400
        return jsonify({'ok': True, 'running': True, 'action_type': info.get('action_type')})
    if not claimed:
        flash(info.get('error', 'Could not approve.'), 'warning')
    elif info.get('error'):
        flash(info['error'], 'warning')
    else:
        flash(f"Approved — {info.get('action_type', 'action')} is running.", 'success')
    return redirect(url_for('dashboard.approvals'))


@bp.route('/approvals/<int:row_id>/deny', methods=['POST'])
@login_required
@admin_required
def deny_action(row_id):
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or request.form.get('reason') or '').strip()
    success, output = workflow_engine.deny_action(row_id, approver, reason)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return (jsonify({'ok': True}) if success
                else (jsonify({'ok': False, 'error': output.get('error', 'Could not deny.')}), 409))
    flash("Action denied — it will not run." if success else (output.get('error') or 'Could not deny.'),
          'info' if success else 'warning')
    return redirect(url_for('dashboard.approvals'))


@bp.route('/approvals/<int:row_id>/resolve-email', methods=['POST'])
@login_required
@admin_required
def resolve_email(row_id):
    """Resolve a parked release_quarantine approval with a chosen remediation:
    release | whitelist_domain | remove_blocklist. The whitelist/remove options run the
    EXO app-only transport-rule change so the problem is fixed for good, not just this once."""
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    data = request.get_json(silent=True) or {}
    mode = (data.get('mode') or request.form.get('mode') or '').strip()
    claimed, info = workflow_engine.resolve_email_remediation(row_id, approver, mode)
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if ajax:
        if not claimed:
            return jsonify({'ok': False, 'error': info.get('error', 'Could not resolve.')}), 409
        return jsonify({'ok': True, 'running': True, 'mode': info.get('mode')})
    flash(info.get('error') or f"Resolving ({mode})…", 'warning' if not claimed else 'success')
    return redirect(url_for('dashboard.approvals'))


@bp.route('/approvals/<int:row_id>/approve-onboard', methods=['POST'])
@login_required
@admin_required
def approve_onboard(row_id):
    """Approve a parked onboard_employee request with the IT-supplied OU + groups.
    Threads ou_dn + group_dns[] into workflow_engine.resolve_onboard, which merges
    them into the parked replay config and runs the provisioning in the background."""
    import workflow_engine
    approver = current_user.username or current_user.email or f'user#{current_user.id}'
    data = request.get_json(silent=True) or {}
    ou_dn = (data.get('ou_dn') or '').strip()
    group_dns = data.get('group_dns') or []
    if isinstance(group_dns, str):
        group_dns = [group_dns]
    claimed, info = workflow_engine.resolve_onboard(row_id, approver, ou_dn, group_dns)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        if not claimed:
            return jsonify({'ok': False, 'error': info.get('error', 'Could not approve.')}), 409
        if info.get('error'):
            return jsonify({'ok': False, 'error': info['error']}), 400
        return jsonify({'ok': True, 'running': True})
    flash(info.get('error') or 'Provisioning the new hire…',
          'warning' if (not claimed or info.get('error')) else 'success')
    return redirect(url_for('dashboard.approvals'))


@bp.route('/api/approvals/<int:row_id>/status')
@login_required
@admin_required
def approval_status(row_id):
    """Live status of one approval/ledger row — polled by the approvals UI after Approve."""
    from models import CommandLedger
    r = CommandLedger.query.get_or_404(row_id)
    when = r.completed_at or r.created_at
    return jsonify({'id': r.id, 'status': r.status, 'approval_status': r.approval_status,
                    'verification_status': r.verification_status,
                    'verification_detail': r.verification_detail,
                    'action_type': r.action_type, 'tool': r.tool, 'risk_tier': r.risk_tier,
                    'object_id': r.object_id,
                    'when': when.strftime('%b %d %H:%M') if when else ''})


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
    """Backward-compat: the event-bus monitor is now the Event Bus tab of the Mission
    Control hub. Redirect old links (event_requeue's redirect + the timeline links)."""
    return redirect(url_for('dashboard.mission_control', tab='events'))


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
    import workflow_engine
    reversible = (workflow_engine.is_reversible(row.action_type)
                  and row.status in ('succeeded', 'completed'))
    return render_template('ledger_detail.html', row=row,
                           before_pretty=_pretty(row.before_state),
                           after_pretty=_pretty(row.after_state), siblings=siblings,
                           reversible=reversible)


@bp.route('/ledger/<int:row_id>/rollback', methods=['POST'])
@login_required
@admin_required
def ledger_rollback(row_id):
    import workflow_engine
    actor = current_user.username or current_user.email or f'user#{current_user.id}'
    new_id = workflow_engine.create_rollback(row_id, actor)
    if new_id:
        flash(f'Rollback queued for approval (ledger #{new_id}) — approve it to execute the reverse.', 'success')
        return redirect(url_for('dashboard.approvals'))
    flash('This action cannot be rolled back automatically.', 'warning')
    return redirect(url_for('dashboard.ledger_detail', row_id=row_id))


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
# Valid hub tabs (used by the route + the backward-compat redirects).
MC_TABS = ('overview', 'action-center', 'approvals', 'events')


def _mission_control_overview_data():
    """Gather the Overview-tab data: world-model coverage, the approval queue preview,
    the command ledger, the event-bus mix, workflow activity, KB size, and the unified
    activity timeline. Split out of the route so the hub can render it as one tab."""
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

    # Workflows — enabled + run status mix + recent runs + the wired automations list.
    wf_enabled = _scalar("SELECT COUNT(*) FROM workflow_definitions WHERE enabled=true")
    automations = []
    try:
        automations = [dict(r._mapping) for r in db.session.execute(text(
            "SELECT id, name, trigger_type, enabled FROM workflow_definitions ORDER BY enabled DESC, id"))]
    except Exception:
        db.session.rollback()
    # Knowledge base size (static + learned + manual).
    kb = {'total': 0, 'runbook': 0, 'manual': 0}
    try:
        import knowledge_agent
        kb = {'total': knowledge_agent.count(), 'runbook': knowledge_agent.count('runbook'),
              'manual': knowledge_agent.count('manual')}
    except Exception:
        pass
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

    # Unified activity timeline — merge actions + events + runs, newest first.
    timeline = []
    for a in recent_actions:
        timeline.append({'ts': a.created_at, 'kind': 'action', 'icon': 'bi-lightning-charge',
                         'text': a.action_type.replace('_', ' ') + (f" → {a.object_id}" if a.object_id else ''),
                         'status': a.status, 'link': url_for('dashboard.ledger_detail', row_id=a.id)})
    for e in recent_events:
        timeline.append({'ts': e.get('created_at'), 'kind': 'event', 'icon': 'bi-broadcast-pin',
                         'text': e.get('event_type'), 'status': e.get('status'),
                         'link': url_for('dashboard.events')})
    for r in recent_runs:
        timeline.append({'ts': r.get('started_at'), 'kind': 'run', 'icon': 'bi-diagram-2',
                         'text': (r.get('name') or 'Workflow') + f" #{r.get('id')}",
                         'status': r.get('status'), 'link': None})
    timeline = sorted([t for t in timeline if t['ts']], key=lambda t: t['ts'], reverse=True)[:15]

    return dict(world=world, pending=pending, pending_n=pending_n,
                recent_actions=recent_actions, ledger_counts=ledger_counts,
                event_counts=event_counts, recent_events=recent_events,
                wf_enabled=wf_enabled, run_counts=run_counts, recent_runs=recent_runs,
                automations=automations, kb=kb, timeline=timeline)


def _events_data():
    """Event-bus tab data: recent event_outbox rows + status counts. Fail-soft."""
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
        db.session.rollback()
    return rows, counts


def _approvals_data():
    """Approvals tab data: parked pending + resolved history + filter option lists +
    the onboard OU/group pickers (only queried when an onboard is actually parked)."""
    pending = _query_pending()
    resolved, has_more = _query_resolved()
    types = sorted({i['action_type'] for i in pending + resolved if i['action_type']})
    devices = sorted({i['device'] for i in pending + resolved if i['device']})
    ad_ous, ad_groups = [], []
    if any(i.get('is_onboard') for i in pending):
        ad_ous, ad_groups = _onboard_directory_options()
    return dict(pending=pending, resolved=resolved, has_more=has_more,
                types=types, devices=devices, history_days=APPROVALS_HISTORY_DAYS,
                ad_ous=ad_ous, ad_groups=ad_groups)


@bp.route('/mission-control')
@login_required
@admin_required
def mission_control():
    """The single admin hub — four tabs (Overview · Action Center · Approvals ·
    Event Bus) stitched into one pane. Deep-link a tab with ?tab=<name> (the client
    keeps the URL hash in sync too). Each tab's data is gathered fail-soft so one
    failing query shows that tab's error state without 500-ing the whole hub."""
    active_tab = (request.args.get('tab') or '').strip().lower()
    if active_tab not in MC_TABS:
        active_tab = 'overview'

    # Each tab gathered independently; a failure degrades to that tab's error banner.
    errors = {}

    mc = {}
    try:
        mc = _mission_control_overview_data()
    except Exception:
        current_app.logger.exception('mission_control overview data failed')
        db.session.rollback()
        errors['overview'] = True

    ac_groups = []
    try:
        ac_groups = _action_center_groups()
    except Exception:
        current_app.logger.exception('mission_control action-center data failed')
        db.session.rollback()
        errors['action-center'] = True

    appr = {}
    try:
        appr = _approvals_data()
    except Exception:
        current_app.logger.exception('mission_control approvals data failed')
        db.session.rollback()
        errors['approvals'] = True

    ev_rows, ev_counts = [], {}
    try:
        ev_rows, ev_counts = _events_data()
    except Exception:
        current_app.logger.exception('mission_control events data failed')
        db.session.rollback()
        errors['events'] = True

    return render_template('mission_control.html',
                           active_tab=active_tab, mc_errors=errors,
                           # Overview
                           world=mc.get('world', {}), pending=mc.get('pending', []),
                           pending_n=mc.get('pending_n', 0),
                           recent_actions=mc.get('recent_actions', []),
                           ledger_counts=mc.get('ledger_counts', {}),
                           event_counts=mc.get('event_counts', {}),
                           recent_events=mc.get('recent_events', []),
                           wf_enabled=mc.get('wf_enabled', 0),
                           run_counts=mc.get('run_counts', {}),
                           recent_runs=mc.get('recent_runs', []),
                           automations=mc.get('automations', []),
                           kb=mc.get('kb', {'total': 0, 'runbook': 0, 'manual': 0}),
                           timeline=mc.get('timeline', []),
                           # Action Center
                           groups=ac_groups,
                           # Approvals
                           appr_pending=appr.get('pending', []),
                           appr_resolved=appr.get('resolved', []),
                           appr_has_more=appr.get('has_more', False),
                           appr_types=appr.get('types', []),
                           appr_devices=appr.get('devices', []),
                           appr_history_days=appr.get('history_days', APPROVALS_HISTORY_DAYS),
                           appr_ad_ous=appr.get('ad_ous', []),
                           appr_ad_groups=appr.get('ad_groups', []),
                           # Events
                           ev_rows=ev_rows, ev_counts=ev_counts)


# ── Knowledge Agent — semantic search + RAG over ISMS/system docs ────────────────
@bp.route('/knowledge', methods=['GET', 'POST'])
@login_required
@admin_required
def knowledge():
    """The IT knowledge library — GitHub-style browse of our learned runbooks/docs by
    category, plus semantic 'Ask' over the full corpus. States via query params:
    root (folders) → ?cat=<category> (files) → ?doc=<id> (rendered doc, +&edit=1)."""
    import knowledge_agent
    import markdown as _md
    query = (request.form.get('q') or request.args.get('q') or '').strip()
    cat = (request.args.get('cat') or '').strip() or None
    doc_id = request.args.get('doc', type=int)
    edit = request.args.get('edit') == '1'
    result, answer_html = None, None
    chunk_count = knowledge_agent.count()
    if query:
        try:
            result = knowledge_agent.answer(query)
            answer_html = _md.markdown(result.get('answer') or '', extensions=['extra'])
        except ValueError as e:
            flash(str(e), 'warning')  # e.g. no OpenAI key configured
        except Exception as e:
            flash(f'Knowledge search failed: {e}', 'danger')

    # Browse state. The library = learned/operational knowledge (runbook + manual);
    # ISMS policy + system docs are searchable but managed in their own subsystems.
    view, docs, entry, entry_html = 'root', None, None, None
    categories = knowledge_agent.category_counts()
    if doc_id:
        entry = knowledge_agent.get_chunk(doc_id)
        if entry:
            entry_html = _md.markdown(entry.get('content') or '', extensions=['extra', 'fenced_code', 'tables'])
            entry['editable'] = entry['source_type'] in knowledge_agent.LIBRARY_TYPES
            entry['category'] = entry.get('category') or 'Runbooks'
            view = 'doc'
        else:
            flash('Entry not found.', 'warning')
    elif cat:
        docs = knowledge_agent.list_knowledge(category=cat)
        view = 'category'

    return render_template('knowledge.html', query=query, result=result, answer_html=answer_html,
                           chunk_count=chunk_count, runbook_count=knowledge_agent.count('runbook'),
                           categories=categories, view=view, cat=cat, docs=docs,
                           entry=entry, entry_html=entry_html, edit=edit,
                           all_categories=knowledge_agent.CATEGORIES)


@bp.route('/knowledge/entry/<int:chunk_id>/edit', methods=['POST'])
@login_required
@admin_required
def knowledge_entry_edit(chunk_id):
    import knowledge_agent
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    category = (request.form.get('category') or '').strip()
    try:
        knowledge_agent.update_chunk(chunk_id, title, content, category=category)
        flash('Runbook updated and re-indexed.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Could not update entry: {e}', 'danger')
    return redirect(url_for('dashboard.knowledge', doc=chunk_id))


@bp.route('/knowledge/entry/<int:chunk_id>/delete', methods=['POST'])
@login_required
@admin_required
def knowledge_entry_delete(chunk_id):
    import knowledge_agent
    cat = (request.form.get('category') or '').strip() or None
    try:
        if knowledge_agent.delete_chunk(chunk_id):
            flash('Runbook deleted.', 'success')
        else:
            flash('Entry not found.', 'warning')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Could not delete entry: {e}', 'danger')
    return redirect(url_for('dashboard.knowledge', cat=cat) if cat else url_for('dashboard.knowledge'))


@bp.route('/knowledge/add', methods=['POST'])
@login_required
@admin_required
def knowledge_add():
    """Operator-authored runbook/doc added directly to the library (and search index)."""
    import knowledge_agent
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    category = (request.form.get('category') or '').strip() or 'Runbooks'
    if not content:
        flash('Content is required.', 'warning')
        return redirect(url_for('dashboard.knowledge'))
    try:
        knowledge_agent.add_runbook(title, content, category=category)
        flash(f'Doc added to “{category}” and indexed.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Could not add knowledge: {e}', 'danger')
    return redirect(url_for('dashboard.knowledge', cat=category))


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
