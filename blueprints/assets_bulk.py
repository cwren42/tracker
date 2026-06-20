"""Bulk asset operations for the assets blueprint (bulk status/department/
edit/export/patches/delete/assign). Split from blueprints/assets.py.
"""
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
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, AssetMonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
    AssetLoan, InstalledApp, RmmBackupPolicy, RmmAgentBackupPolicy, _log_audit,
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


from blueprints.assets import bp, _asset_cascade_delete, _rmm_cascade_delete


@bp.route('/assets/bulk/eagle-eyes', methods=['POST'])
@login_required
@eagle_eyes_required
@license_required
def bulk_eagle_eyes():
    """Bulk enable/disable Eagle Eyes for selected assets.

    Gated to admin + eagle_eyes (a manager has no Eagle Eyes access per policy and
    must not be able to bulk-toggle monitoring). For an eagle_eyes actor, any agent in
    the hidden set (manual exclusions + servers) is skipped so they can't bulk-enable a
    server/excluded device; admins are unaffected.
    """
    from blueprints.rmm_eagle import _ee_hidden_ids
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
    # eagle_eyes actors cannot touch hidden devices; admins act on everything.
    if current_user.role == 'eagle_eyes':
        hidden = _ee_hidden_ids()
        rows = [r for r in rows if r[1] not in hidden]
        if not rows:
            return jsonify({'success': False, 'message': 'Selected device(s) are not available for Eagle Eyes'}), 403
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
    # Audit: one row for the bulk surveillance toggle (actor, new state, affected agents/count).
    _log_audit('rmm_eagle_config', 0, 'eagle_eyes.bulk_config', {
        'enabled': enabled,
        'count': count,
        'agent_ids': [r[1] for r in rows],
    })
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
        skipped_assigned = 0
        for asset_id in asset_ids:
            asset = Asset.query.get(asset_id)
            if asset:
                # Assignment is authoritative: an asset that still has an assignee
                # can't be marked 'Available'. Skip those (unassign first) instead of
                # writing an assigned+Available drift row.
                if new_status == 'Available' and asset.employee_id:
                    skipped_assigned += 1
                    continue
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

        msg = f'Successfully updated {count} assets'
        if skipped_assigned:
            msg += (f'; skipped {skipped_assigned} still assigned to an employee '
                    f'(unassign them first to mark Available)')
        return jsonify({
            'success': True,
            'count': count,
            'skipped_assigned': skipped_assigned,
            'message': msg
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
    """Trigger a self-update on the RMM agents linked to selected assets.

    Sends a live WS 'update_now' via the gateway — the agent has no working heartbeat/
    rmm_commands consumer, so the old queue-a-force_update approach never fired (it just
    piled up zombie rows). Online agents update immediately; offline ones pick it up on
    their next periodic update check (~4h) or restart."""
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

    import urllib.request as _ur, json as _json
    delivered = offline = 0
    for row in rows:
        agent_id = row[0]
        try:
            req = _ur.Request(f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
                              data=_json.dumps({'type': 'update_now', 'session_id': 0}).encode(),
                              headers={'Content-Type': 'application/json'}, method='POST')
            with _ur.urlopen(req, timeout=3) as r:
                ok = _json.loads(r.read()).get('ok', False)
            delivered += 1 if ok else 0
            offline += 0 if ok else 1
        except Exception:
            offline += 1

    msg = f'Sent update to {delivered} online agent(s).'
    if offline:
        msg += f' {offline} offline — they will update on their next check (~4h) or restart.'
    return jsonify({'success': True, 'count': delivered, 'message': msg})


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


