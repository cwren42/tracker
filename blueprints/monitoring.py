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
    ProfileCheck, AssetMonitoringProfile, RmmBackupJob,
    RmmBackupPolicy, RmmAgentBackupPolicy,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
import alert_service as _alert_svc
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, RMM_GATEWAY_PUBLIC, RMM_TRACKER_URL,
    _valid_agent_key, _get_or_create_site_enrollment_token,
)
logger = logging.getLogger(__name__)


bp = Blueprint('monitoring', __name__)


# ==================== MONITORING PROFILES & CHECKS ====================


# ==================== PROXMOX / BACKUPS ====================


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FOR RAW DB ACCESS (security/workflow/report routes)
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    from pg_db import pg_connect
    return pg_connect()


# ─────────────────────────────────────────────────────────────────────────────
# ALERT CENTER
# ─────────────────────────────────────────────────────────────────────────────



@bp.route('/monitoring')
@login_required
def monitoring_dashboard():
    """Main monitoring dashboard showing all assets with profiles and current status"""
    # Get all assets with their monitoring profiles
    assets_query = db.session.query(
        Asset.id,
        Asset.name,
        Asset.asset_tag,
        Asset.category,
        Asset.status,
        Asset.os_version,
        db.func.coalesce(MonitoringProfile.name, 'Not Assigned').label('profile_name'),
        db.func.coalesce(MonitoringProfile.id, None).label('profile_id'),
        db.func.coalesce(MonitoringProfile.device_type, 'Unknown').label('device_type')
    ).outerjoin(
        AssetMonitoringProfile, Asset.id == AssetMonitoringProfile.c.asset_id
    ).outerjoin(
        MonitoringProfile, AssetMonitoringProfile.c.profile_id == MonitoringProfile.id
    ).all()
    
    assets = []
    for a in assets_query:
        assets.append({
            'id': a.id,
            'name': a.name,
            'asset_tag': a.asset_tag,
            'category': a.category,
            'status': a.status,
            'os_version': a.os_version,
            'profile_name': a.profile_name,
            'profile_id': a.profile_id,
            'device_type': a.device_type,
            'has_profile': a.profile_id is not None
        })
    
    # Get all profiles for assignment dropdown
    profiles = MonitoringProfile.query.filter_by(enabled=True).order_by(MonitoringProfile.name).all()
    
    # Get active alerts count
    active_alerts = db.session.query(db.func.count(MonitoringAlert.id)).filter_by(status='active').scalar() or 0
    
    # Get profile assignment stats
    total_assets = len(assets)
    assigned_assets = sum(1 for a in assets if a['has_profile'])
    unassigned_assets = total_assets - assigned_assets
    
    return render_template('monitoring_dashboard.html',
                         assets=assets,
                         profiles=profiles,
                         active_alerts=active_alerts,
                         total_assets=total_assets,
                         assigned_assets=assigned_assets,
                         unassigned_assets=unassigned_assets)


@bp.route('/monitoring/profiles')
@login_required
def monitoring_profiles():
    """View all monitoring profiles and their checks"""
    profiles = MonitoringProfile.query.order_by(MonitoringProfile.name).all()
    
    profiles_data = []
    for profile in profiles:
        # Count checks for this profile
        check_count = db.session.query(db.func.count(ProfileCheck.c.check_id)).filter(
            ProfileCheck.c.profile_id == profile.id
        ).scalar() or 0
        
        # Count assets using this profile
        asset_count = db.session.query(db.func.count(AssetMonitoringProfile.c.asset_id)).filter(
            AssetMonitoringProfile.c.profile_id == profile.id
        ).scalar() or 0
        
        profiles_data.append({
            'profile': profile,
            'check_count': check_count,
            'asset_count': asset_count
        })
    
    return render_template('monitoring_profiles.html', profiles=profiles_data)


@bp.route('/monitoring/profile/<int:profile_id>')
@login_required
def monitoring_profile_detail(profile_id):
    """View details of a specific monitoring profile including all checks"""
    profile = MonitoringProfile.query.get_or_404(profile_id)
    
    # Get all checks for this profile with their parameters
    checks_query = db.session.query(
        MonitoringCheck,
        ProfileCheck.c.enabled,
        ProfileCheck.c.check_interval_override,
        ProfileCheck.c.warning_threshold,
        ProfileCheck.c.critical_threshold,
        ProfileCheck.c.parameters
    ).join(
        ProfileCheck, MonitoringCheck.id == ProfileCheck.c.check_id
    ).filter(
        ProfileCheck.c.profile_id == profile_id
    ).order_by(MonitoringCheck.name).all()
    
    checks = []
    for check, enabled, interval, warning, critical, parameters in checks_query:
        checks.append({
            'check': check,
            'enabled': enabled,
            'interval_override': interval,
            'warning_threshold': warning,
            'critical_threshold': critical,
            'parameters': parameters
        })
    
    # Get assets using this profile
    assets = db.session.query(Asset).join(
        AssetMonitoringProfile, Asset.id == AssetMonitoringProfile.c.asset_id
    ).filter(
        AssetMonitoringProfile.c.profile_id == profile_id
    ).all()

    # Get active alerts for assets in this profile
    asset_ids = [a.id for a in assets]
    active_alerts = []
    if asset_ids:
        active_alerts = MonitoringAlert.query.filter(
            MonitoringAlert.asset_id.in_(asset_ids),
            MonitoringAlert.status.in_(['active', 'open', 'acknowledged'])
        ).order_by(MonitoringAlert.triggered_at.desc()).limit(100).all()

    # For Windows profiles, get backup policy data
    backup_policies = []
    asset_backup_info = {}
    if profile.os_family == 'Windows' and asset_ids:
        backup_policies = RmmBackupPolicy.query.filter_by(enabled=True).order_by(RmmBackupPolicy.name).all()
        try:
            rows = db.session.execute(text("""
                SELECT ra.asset_id, ra.agent_id,
                       abp.policy_id, abp.enabled AS backup_enabled,
                       p.name AS policy_name
                FROM rmm_agent ra
                LEFT JOIN rmm_agent_backup_policy abp ON abp.agent_id = ra.agent_id
                LEFT JOIN rmm_backup_policy p ON p.id = abp.policy_id
                WHERE ra.asset_id = ANY(:aids) AND ra.enabled = true
            """), {'aids': asset_ids}).mappings().fetchall()
            for row in rows:
                asset_backup_info[row['asset_id']] = dict(row)
        except Exception as ex:
            logger.warning(f'Could not load backup data for profile {profile_id}: {ex}')

    # Checks NOT yet in this profile, for the "Add Check" modal\n    existing_check_ids = [item['check'].id for item in checks]\n    available_checks = MonitoringCheck.query.filter(\n        MonitoringCheck.enabled == True,\n        ~MonitoringCheck.id.in_(existing_check_ids) if existing_check_ids else True\n    ).order_by(MonitoringCheck.name).all()\n\n    # Count assets matching this profile's device_type/os_family that are not yet assigned\n    unassigned_query = db.session.query(Asset).outerjoin(\n        AssetMonitoringProfile, Asset.id == AssetMonitoringProfile.c.asset_id\n    ).filter(AssetMonitoringProfile.c.profile_id == None)\n    if profile.device_type:\n        unassigned_query = unassigned_query.filter(Asset.category.ilike(f'%{profile.device_type}%'))\n    unassigned_count = unassigned_query.count()\n\n    return render_template('monitoring_profile_detail.html',\n                         profile=profile,\n                         checks=checks,\n                         assets=assets,\n                         active_alerts=active_alerts,\n                         backup_policies=backup_policies,\n                         asset_backup_info=asset_backup_info,\n                         available_checks=available_checks,\n                         unassigned_count=unassigned_count)


@bp.route('/monitoring/profile/<int:profile_id>/bulk-assign-assets', methods=['POST'])
@login_required
def monitoring_profile_bulk_assign_assets(profile_id):
    """Bulk-assign all unassigned matching assets to this profile"""
    profile = MonitoringProfile.query.get_or_404(profile_id)
    overwrite = request.form.get('overwrite') == '1'

    try:
        # Build query: assets matching device_type, optionally already-assigned ones too
        q = db.session.query(Asset)
        if profile.device_type:
            q = q.filter(Asset.category.ilike(f'%{profile.device_type}%'))

        if not overwrite:
            # Only unassigned assets
            assigned_ids = db.session.query(AssetMonitoringProfile.c.asset_id).subquery()
            q = q.filter(~Asset.id.in_(assigned_ids))

        assets_to_assign = q.all()

        assigned = 0
        for asset in assets_to_assign:
            if overwrite:
                db.session.execute(
                    AssetMonitoringProfile.delete().where(
                        AssetMonitoringProfile.c.asset_id == asset.id
                    )
                )
            db.session.execute(
                AssetMonitoringProfile.insert().values(
                    asset_id=asset.id,
                    profile_id=profile_id,
                    assigned_by=current_user.id,
                )
            )
            assigned += 1

        db.session.commit()
        flash(f'Assigned {assigned} asset(s) to profile "{profile.name}"', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error bulk-assigning assets to profile {profile_id}: {e}')
        flash('Error assigning assets', 'danger')

    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/monitoring/profile/<int:profile_id>/add-check', methods=['POST'])
@login_required
def monitoring_profile_add_check(profile_id):
    """Add a monitoring check to a profile with optional custom thresholds"""
    MonitoringProfile.query.get_or_404(profile_id)
    check_id = request.form.get('check_id', type=int)
    warning = request.form.get('warning_threshold', '').strip() or None
    critical = request.form.get('critical_threshold', '').strip() or None
    interval_override = request.form.get('interval_override', type=int) or None

    if not check_id:
        flash('Please select a check', 'warning')
        return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

    MonitoringCheck.query.get_or_404(check_id)

    try:
        # Upsert: remove existing entry first, then insert fresh
        db.session.execute(
            ProfileCheck.delete().where(
                (ProfileCheck.c.profile_id == profile_id) &
                (ProfileCheck.c.check_id == check_id)
            )
        )
        db.session.execute(
            ProfileCheck.insert().values(
                profile_id=profile_id,
                check_id=check_id,
                enabled=True,
                warning_threshold=warning,
                critical_threshold=critical,
                check_interval_override=interval_override,
            )
        )
        db.session.commit()
        flash('Check added to profile', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error adding check to profile {profile_id}: {e}')
        flash('Error adding check', 'danger')

    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/monitoring/profile/<int:profile_id>/remove-check/<int:check_id>', methods=['POST'])
@login_required
def monitoring_profile_remove_check(profile_id, check_id):
    """Remove a monitoring check from a profile"""
    MonitoringProfile.query.get_or_404(profile_id)
    try:
        db.session.execute(
            ProfileCheck.delete().where(
                (ProfileCheck.c.profile_id == profile_id) &
                (ProfileCheck.c.check_id == check_id)
            )
        )
        db.session.commit()
        flash('Check removed from profile', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error removing check from profile {profile_id}: {e}')
        flash('Error removing check', 'danger')
    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/monitoring/profile/<int:profile_id>/assign-existing-backup-policy', methods=['POST'])
@login_required
def monitoring_profile_bulk_assign_backup(profile_id):
    """Bulk-assign an existing backup policy to all enrolled agents in this profile"""
    MonitoringProfile.query.get_or_404(profile_id)
    policy_id = request.form.get('policy_id', type=int)

    if not policy_id:
        flash('Please select a backup policy', 'warning')
        return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

    policy = RmmBackupPolicy.query.get_or_404(policy_id)

    try:
        assets = db.session.query(Asset).join(
            AssetMonitoringProfile, Asset.id == AssetMonitoringProfile.c.asset_id
        ).filter(AssetMonitoringProfile.c.profile_id == profile_id).all()
        asset_ids = [a.id for a in assets]

        if not asset_ids:
            flash('No assets in this profile', 'warning')
            return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

        agent_rows = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = ANY(:aids) AND enabled = true"),
            {'aids': asset_ids}
        ).fetchall()

        assigned = 0
        for (agent_id,) in agent_rows:
            existing = RmmAgentBackupPolicy.query.filter_by(agent_id=agent_id).first()
            if existing:
                existing.policy_id = policy_id
                existing.enabled = True
                existing.updated_at = datetime.utcnow()
            else:
                db.session.add(RmmAgentBackupPolicy(
                    agent_id=agent_id,
                    policy_id=policy_id,
                    enabled=True,
                ))
            assigned += 1

        db.session.commit()
        flash(f'Policy "{policy.name}" assigned to {assigned} agent(s)', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error bulk-assigning backup policy for profile {profile_id}: {e}')
        flash('Error assigning backup policy', 'danger')

    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/monitoring/profile/<int:profile_id>/create-backup-policy', methods=['POST'])
@login_required
def monitoring_profile_create_backup_policy(profile_id):
    """Quick-create a backup policy and optionally assign to all Windows agents in this profile"""
    MonitoringProfile.query.get_or_404(profile_id)
    name = request.form.get('policy_name', '').strip()
    nas_unc = request.form.get('nas_unc_path', '').strip()
    retention = request.form.get('retention_days', type=int) or 30
    full_interval = request.form.get('full_backup_interval_days', type=int) or 7
    assign_all = request.form.get('assign_all') == '1'

    if not name or not nas_unc:
        flash('Policy name and NAS UNC path are required', 'warning')
        return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

    try:
        import json as _json
        policy = RmmBackupPolicy(
            name=name,
            nas_unc_path=nas_unc,
            retention_days=retention,
            full_backup_interval_days=full_interval,
            enabled=True,
            include_paths=_json.dumps(['C:\\Users']),
            exclude_extensions=_json.dumps(['.tmp', '.log', '.iso', '.vhd', '.vmdk', '.vhdx']),
            exclude_folders=_json.dumps(['node_modules', '.git', '$RECYCLE.BIN', 'Windows',
                                         'Program Files', 'Program Files (x86)']),
        )
        db.session.add(policy)
        db.session.flush()  # get policy.id

        if assign_all:
            # Find all RMM agents for assets in this profile
            assets = db.session.query(Asset).join(
                AssetMonitoringProfile, Asset.id == AssetMonitoringProfile.c.asset_id
            ).filter(AssetMonitoringProfile.c.profile_id == profile_id).all()
            asset_ids = [a.id for a in assets]
            if asset_ids:
                agent_rows = db.session.execute(
                    text("SELECT agent_id FROM rmm_agent WHERE asset_id = ANY(:aids) AND enabled = true"),
                    {'aids': asset_ids}
                ).fetchall()
                for (agent_id,) in agent_rows:
                    existing = RmmAgentBackupPolicy.query.filter_by(agent_id=agent_id).first()
                    if existing:
                        existing.policy_id = policy.id
                        existing.enabled = True
                    else:
                        db.session.add(RmmAgentBackupPolicy(
                            agent_id=agent_id,
                            policy_id=policy.id,
                            enabled=True,
                        ))

        db.session.commit()
        flash(f'Backup policy "{name}" created' + (' and assigned to all agents in this profile' if assign_all else ''), 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error creating backup policy for profile {profile_id}: {e}')
        flash('Error creating backup policy', 'danger')

    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/monitoring/profile/<int:profile_id>/add-alert', methods=['POST'])
@login_required
def monitoring_profile_add_alert(profile_id):
    """Manually create a monitoring alert for an asset in this profile"""
    profile = MonitoringProfile.query.get_or_404(profile_id)
    asset_id = request.form.get('asset_id', type=int)
    severity = request.form.get('severity', 'warning')
    message = request.form.get('message', '').strip()

    if not asset_id or not message:
        flash('Asset and message are required', 'warning')
        return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

    if severity not in ('info', 'warning', 'critical'):
        severity = 'warning'

    asset = Asset.query.get_or_404(asset_id)

    try:
        alert = MonitoringAlert(
            asset_id=asset_id,
            severity=severity,
            status='active',
            message=message,
            triggered_at=datetime.utcnow(),
        )
        db.session.add(alert)
        db.session.commit()
        flash(f'Alert created for {asset.name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error creating alert for profile {profile_id}: {e}')
        flash('Error creating alert', 'danger')

    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/monitoring/profile/<int:profile_id>/assign-backup-policy', methods=['POST'])
@login_required
def monitoring_profile_assign_backup(profile_id):
    """Assign a backup policy to an asset (via its RMM agent) from the profile detail page"""
    profile = MonitoringProfile.query.get_or_404(profile_id)
    asset_id = request.form.get('asset_id', type=int)
    policy_id = request.form.get('policy_id', type=int) or None

    if not asset_id:
        flash('Asset is required', 'warning')
        return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

    try:
        row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = true LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()

        if not row:
            flash('No RMM agent found for this asset. Make sure the agent is enrolled.', 'warning')
            return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))

        agent_id = row[0]
        existing = RmmAgentBackupPolicy.query.filter_by(agent_id=agent_id).first()
        if existing:
            existing.policy_id = policy_id
            existing.enabled = True
            existing.updated_at = datetime.utcnow()
        else:
            existing = RmmAgentBackupPolicy(
                agent_id=agent_id,
                policy_id=policy_id,
                enabled=True,
            )
            db.session.add(existing)

        db.session.commit()
        asset = Asset.query.get(asset_id)
        if policy_id:
            policy_name = RmmBackupPolicy.query.get(policy_id).name if policy_id else 'None'
            flash(f'Backup policy "{policy_name}" assigned to {asset.name}', 'success')
        else:
            flash(f'Backup policy removed from {asset.name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error assigning backup policy for profile {profile_id}: {e}')
        flash('Error assigning backup policy', 'danger')

    return redirect(url_for('monitoring.monitoring_profile_detail', profile_id=profile_id))


@bp.route('/agent/download')
def agent_download():
    """Serve the Linux agent Python script for download"""
    agent_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'agent.py')
    return send_file(agent_path, as_attachment=True, download_name='cirque-rmm-agent')


@bp.route('/agent/install.sh')
def agent_install_script():
    """Serve the agent installer script with the API key pre-filled"""
    from flask import Response
    script_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'install.sh')
    with open(script_path, 'r') as f:
        script = f.read()
    # Inject current API key so the installer works without any manual config
    api_key = current_app.config.get('LINUX_AGENT_API_KEY', '')
    script = script.replace(
        'API_KEY="${API_KEY:-}"',
        f'API_KEY="${{API_KEY:-{api_key}}}"'
    )
    return Response(script, mimetype='text/x-shellscript')


@bp.route('/agent/service')
def agent_service_file():
    """Serve the systemd service file"""
    service_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'cirque-rmm-agent.service')
    return send_file(service_path, mimetype='text/plain')


@bp.route('/monitoring/assign/<int:asset_id>', methods=['POST'])
@login_required
def monitoring_assign_profile(asset_id):
    """Assign a monitoring profile to an asset"""
    asset = Asset.query.get_or_404(asset_id)
    profile_id = request.form.get('profile_id')
    
    if not profile_id:
        flash('Please select a monitoring profile', 'warning')
        return redirect(request.referrer or url_for('monitoring.monitoring_dashboard'))
    
    profile = MonitoringProfile.query.get_or_404(profile_id)
    
    try:
        # Remove existing profile assignment if any
        db.session.execute(
            AssetMonitoringProfile.delete().where(
                AssetMonitoringProfile.c.asset_id == asset_id
            )
        )
        
        # Insert new assignment
        db.session.execute(
            AssetMonitoringProfile.insert().values(
                asset_id=asset_id,
                profile_id=profile_id,
                assigned_by=current_user.id
            )
        )
        
        db.session.commit()
        flash(f'Monitoring profile "{profile.name}" assigned to {asset.name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error assigning monitoring profile: {str(e)}')
        flash('Error assigning monitoring profile', 'danger')
    
    return redirect(request.referrer or url_for('monitoring.monitoring_dashboard'))


@bp.route('/monitoring/unassign/<int:asset_id>', methods=['POST'])
@login_required
def monitoring_unassign_profile(asset_id):
    """Remove monitoring profile from an asset"""
    asset = Asset.query.get_or_404(asset_id)
    
    try:
        db.session.execute(
            AssetMonitoringProfile.delete().where(
                AssetMonitoringProfile.c.asset_id == asset_id
            )
        )
        db.session.commit()
        flash(f'Monitoring profile removed from {asset.name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error removing monitoring profile: {str(e)}')
        flash('Error removing monitoring profile', 'danger')
    
    return redirect(request.referrer or url_for('monitoring.monitoring_dashboard'))


@bp.route('/monitoring/alerts')
@login_required
def monitoring_alerts():
    """View all monitoring alerts"""
    status_filter = request.args.get('status', 'active')
    severity_filter = request.args.get('severity', '')
    
    query = MonitoringAlert.query
    
    if status_filter and status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    if severity_filter:
        query = query.filter_by(severity=severity_filter)
    
    alerts = query.order_by(MonitoringAlert.triggered_at.desc()).limit(500).all()
    
    # Get alert statistics
    stats = {
        'active': db.session.query(db.func.count(MonitoringAlert.id)).filter_by(status='active').scalar() or 0,
        'acknowledged': db.session.query(db.func.count(MonitoringAlert.id)).filter_by(status='acknowledged').scalar() or 0,
        'resolved': db.session.query(db.func.count(MonitoringAlert.id)).filter_by(status='resolved').scalar() or 0,
        'critical': db.session.query(db.func.count(MonitoringAlert.id)).filter(
            MonitoringAlert.status == 'active',
            MonitoringAlert.severity == 'critical'
        ).scalar() or 0,
        'warning': db.session.query(db.func.count(MonitoringAlert.id)).filter(
            MonitoringAlert.status == 'active',
            MonitoringAlert.severity == 'warning'
        ).scalar() or 0
    }
    
    return render_template('monitoring_alerts.html',
                         alerts=alerts,
                         stats=stats,
                         status_filter=status_filter,
                         severity_filter=severity_filter)


@bp.route('/monitoring/alert/<int:alert_id>/acknowledge', methods=['POST'])
@login_required
def monitoring_acknowledge_alert(alert_id):
    """Acknowledge a monitoring alert"""
    alert = MonitoringAlert.query.get_or_404(alert_id)
    
    try:
        alert.status = 'acknowledged'
        alert.acknowledged_at = datetime.utcnow()
        alert.acknowledged_by = current_user.username
        db.session.commit()
        flash('Alert acknowledged', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error acknowledging alert: {str(e)}')
        flash('Error acknowledging alert', 'danger')
    
    return redirect(request.referrer or url_for('monitoring.monitoring_alerts'))


@bp.route('/monitoring/alert/<int:alert_id>/resolve', methods=['POST'])
@login_required
def monitoring_resolve_alert(alert_id):
    """Resolve a monitoring alert"""
    alert = MonitoringAlert.query.get_or_404(alert_id)
    resolution_notes = request.form.get('resolution_notes', '')
    
    try:
        alert.status = 'resolved'
        alert.resolved_at = datetime.utcnow()
        alert.resolved_by = current_user.username
        alert.resolution_notes = resolution_notes
        db.session.commit()
        flash('Alert resolved', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error resolving alert:{str(e)}')
        flash('Error resolving alert', 'danger')
    
    return redirect(request.referrer or url_for('monitoring.monitoring_alerts'))


@bp.route('/monitoring/maintenance-windows')
@login_required
def monitoring_maintenance_windows():
    """View and manage maintenance windows"""
    windows = MaintenanceWindow.query.order_by(MaintenanceWindow.day_of_week, MaintenanceWindow.start_time).all()
    
    return render_template('monitoring_maintenance_windows.html', windows=windows)


@bp.route('/backups')
@login_required
@admin_required
def backups():
    """Proxmox backup & ZFS health dashboard."""
    from proxmox_service import _get_setting

    stale_hours = int(_get_setting(Setting, 'proxmox_stale_hours', '26') or '26')

    pools = ProxmoxZfsPool.query.order_by(
        ProxmoxZfsPool.server, ProxmoxZfsPool.node, ProxmoxZfsPool.pool_name
    ).all()

    jobs = ProxmoxBackupJob.query.order_by(
        ProxmoxBackupJob.node, ProxmoxBackupJob.vm_name
    ).all()

    cluster_configured = bool(
        Setting.query.filter_by(key='proxmox_cluster_host').first()
        and (Setting.query.filter_by(key='proxmox_cluster_host').first().value or '').strip()
    )

    last_sync_row = Setting.query.filter_by(key='proxmox_last_sync').first()
    last_sync = last_sync_row.value if last_sync_row else None

    summary = {
        'total_pools': len(pools),
        'degraded_pools': sum(1 for p in pools if p.health not in ('ONLINE', 'AVAILABLE')),
        'critical_pools': sum(1 for p in pools if p.percent_used and p.percent_used >= 80),
        'total_vms': len(jobs),
        'ok_vms': sum(1 for j in jobs if j.backup_status == 'ok'),
        'stale_vms': sum(1 for j in jobs if j.backup_status == 'stale'),
        'missing_vms': sum(1 for j in jobs if j.backup_status == 'missing'),
    }

    # Windows agent backup jobs (last 100)
    win_jobs_raw = db.session.execute(text("""
        SELECT bj.id, bj.agent_id, bj.job_type, bj.status,
               bj.started_at, bj.completed_at,
               bj.files_copied, bj.files_skipped, bj.files_failed,
               bj.bytes_transferred, bj.snapshot_path, bj.triggered_by,
               COALESCE(a.name, bj.agent_id) AS asset_name, a.id AS asset_id
        FROM rmm_backup_job bj
        LEFT JOIN rmm_agent ra ON ra.agent_id = bj.agent_id
        LEFT JOIN asset a ON a.id = ra.asset_id
        ORDER BY bj.started_at DESC
        LIMIT 100
    """)).mappings().fetchall()
    win_jobs = [dict(r) for r in win_jobs_raw]

    win_summary = {
        'total': len(win_jobs),
        'success': sum(1 for j in win_jobs if j['status'] == 'success'),
        'running': sum(1 for j in win_jobs if j['status'] == 'running'),
        'failed': sum(1 for j in win_jobs if j['status'] == 'failed'),
    }

    return render_template(
        'backups.html',
        pools=pools, jobs=jobs,
        summary=summary,
        stale_hours=stale_hours,
        cluster_configured=cluster_configured,
        last_sync=last_sync,
        now=datetime.utcnow(),
        win_jobs=win_jobs,
        win_summary=win_summary,
    )


@bp.route('/api/proxmox/sync', methods=['POST'])
@login_required
@admin_required
def api_proxmox_sync():
    """Trigger a manual Proxmox sync."""
    from proxmox_service import sync_proxmox
    try:
        result = sync_proxmox(app, db, ProxmoxBackupJob, ProxmoxZfsPool,
                              Setting, MonitoringAlert)
        # Record last sync time
        row = Setting.query.filter_by(key='proxmox_last_sync').first()
        if row is None:
            row = Setting(key='proxmox_last_sync')
            db.session.add(row)
        row.value = datetime.utcnow().isoformat()
        db.session.commit()
        return jsonify({'success': True, **result})
    except Exception as e:
        logger.exception('Proxmox manual sync failed')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/proxmox/test', methods=['POST'])
@login_required
@admin_required
def api_proxmox_test():
    """Test connection to configured Proxmox host(s)."""
    from proxmox_service import test_proxmox_connection
    data = request.get_json(silent=True) or {}
    prefix = data.get('prefix', 'cluster')
    if prefix not in ('cluster', 'backup'):
        return jsonify({'success': False, 'error': 'Invalid prefix'}), 400
    result = test_proxmox_connection(Setting, prefix)
    return jsonify(result)


@bp.route('/api/proxmox/settings', methods=['POST'])
@login_required
@admin_required
def api_proxmox_settings():
    """Save Proxmox settings."""
    data = request.get_json(silent=True) or {}
    allowed_keys = {
        'proxmox_cluster_host', 'proxmox_cluster_port',
        'proxmox_cluster_token_id', 'proxmox_cluster_token_secret',
        'proxmox_cluster_verify_ssl',
        'proxmox_backup_host', 'proxmox_backup_port',
        'proxmox_backup_token_id', 'proxmox_backup_token_secret',
        'proxmox_backup_verify_ssl',
        'proxmox_stale_hours',
    }
    saved = []
    for key, value in data.items():
        if key not in allowed_keys:
            continue
        row = Setting.query.filter_by(key=key).first()
        if row is None:
            row = Setting(key=key, value=str(value))
            db.session.add(row)
        else:
            row.value = str(value)
        saved.append(key)
    try:
        db.session.commit()
        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/alerts/center')
@login_required
@admin_required
def alert_center():
    users = db.session.execute(text('SELECT id, username, full_name FROM "user" ORDER BY username')).mappings().fetchall()
    return render_template('alert_center.html', users=[dict(u) for u in users])


@bp.route('/api/alerts/rules', methods=['GET', 'POST'])
@login_required
@admin_required
def api_alert_rules():
    con = _alert_svc._get_db()
    try:
        if request.method == 'GET':
            cat = request.args.get('category')
            if cat:
                rows = con.execute("SELECT * FROM alert_rule WHERE category=? ORDER BY alert_type", (cat,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM alert_rule ORDER BY category, alert_type").fetchall()
            return jsonify(ok=True, rules=[dict(r) for r in rows])
        d = request.get_json(force=True)
        con.execute(
            """INSERT INTO alert_rule (category, alert_type, label, threshold_value, threshold_unit,
               enabled, auto_ticket, ticket_priority, assigned_to_user_id, email_notify,
               teams_notify, teams_webhook_url, cooldown_minutes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d.get('category', 'agent'), d.get('alert_type', ''), d.get('label', ''),
             d.get('threshold_value', 0), d.get('threshold_unit', ''),
             1 if d.get('enabled', True) else 0,
             1 if d.get('auto_ticket') else 0,
             d.get('ticket_priority', 'Normal'), d.get('assigned_to_user_id'),
             1 if d.get('email_notify', True) else 0,
             1 if d.get('teams_notify') else 0,
             d.get('teams_webhook_url', ''), d.get('cooldown_minutes', 60))
        )
        con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route('/api/alerts/rules/<int:rid>', methods=['PUT', 'DELETE'])
@login_required
@admin_required
def api_alert_rule(rid):
    con = _alert_svc._get_db()
    try:
        if request.method == 'DELETE':
            con.execute("DELETE FROM alert_rule WHERE id=?", (rid,))
            con.commit()
            return jsonify(ok=True)
        d = request.get_json(force=True)
        con.execute(
            """UPDATE alert_rule SET label=?, threshold_value=?, threshold_unit=?,
               enabled=?, auto_ticket=?, ticket_priority=?, assigned_to_user_id=?,
               email_notify=?, teams_notify=?, teams_webhook_url=?, cooldown_minutes=?
               WHERE id=?""",
            (d.get('label', ''), d.get('threshold_value', 0), d.get('threshold_unit', ''),
             1 if d.get('enabled', True) else 0,
             1 if d.get('auto_ticket') else 0,
             d.get('ticket_priority', 'Normal'), d.get('assigned_to_user_id'),
             1 if d.get('email_notify', True) else 0,
             1 if d.get('teams_notify') else 0,
             d.get('teams_webhook_url', ''), d.get('cooldown_minutes', 60), rid)
        )
        con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route('/api/alerts/rules/<int:rid>/toggle', methods=['POST'])
@login_required
@admin_required
def api_alert_rule_toggle(rid):
    con = _alert_svc._get_db()
    try:
        row = con.execute("SELECT enabled FROM alert_rule WHERE id=?", (rid,)).fetchone()
        if not row:
            return jsonify(ok=False, error='Not found'), 404
        new_state = 0 if row['enabled'] else 1
        con.execute("UPDATE alert_rule SET enabled=? WHERE id=?", (new_state, rid))
        con.commit()
        return jsonify(ok=True, enabled=bool(new_state))
    finally:
        con.close()


@bp.route('/api/alerts/log')
@login_required
@admin_required
def api_alert_log():
    con = _alert_svc._get_db()
    try:
        cat = request.args.get('category')
        limit = int(request.args.get('limit', 100))
        if cat:
            rows = con.execute(
                "SELECT * FROM alert_log WHERE category=? ORDER BY fired_at DESC LIMIT ?", (cat, limit)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM alert_log ORDER BY fired_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return jsonify(ok=True, log=[dict(r) for r in rows])
    finally:
        con.close()


# NOTE: /api/notifications/bell and /api/notifications/mark-read are defined
# in blueprints/misc.py — do not duplicate them here.