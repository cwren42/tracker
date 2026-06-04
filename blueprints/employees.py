import csv
import io
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
try:
    from m365_service import M365Service
except ImportError:
    M365Service = None

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


bp = Blueprint('employees', __name__)



@bp.route('/employees')
@login_required
@license_required
def employees():
    # Get query parameters for filtering and sorting
    search = request.args.get('search', '').strip()
    department_filter = request.args.get('department', '').strip()
    sort_by = request.args.get('sort', 'name')
    sort_order = request.args.get('order', 'asc')
    location_filter = request.args.get('location', '').strip()
    show_hidden = request.args.get('show_hidden', '0') == '1' and current_user.role == 'admin'
    account_state = request.args.get('account_state', 'active')  # active | disabled | all

    # Base query — hidden set, or visible set filtered by directory account state.
    if show_hidden:
        query = Employee.query.filter(Employee.is_visible == False)
    else:
        query = Employee.query.filter(Employee.is_visible == True)
        # Default: hide directory-disabled accounts. NULL = unknown -> keep visible.
        if account_state == 'active':
            query = query.filter(
                db.or_(Employee.ad_enabled.is_(None), Employee.ad_enabled == True)
            ).filter(
                db.or_(Employee.m365_account_enabled.is_(None), Employee.m365_account_enabled == True)
            )
        elif account_state == 'disabled':
            query = query.filter(
                db.or_(Employee.ad_enabled == False, Employee.m365_account_enabled == False)
            )
        # account_state == 'all' -> no extra filter

    # Apply search filter
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            db.or_(
                Employee.name.ilike(search_filter),
                Employee.email.ilike(search_filter),
                Employee.department.ilike(search_filter),
                Employee.position.ilike(search_filter),
                Employee.phone.ilike(search_filter)
            )
        )
    
    # Apply department filter
    if department_filter:
        query = query.filter(Employee.department == department_filter)
    
    # Apply location filter
    if location_filter:
        query = query.filter(Employee.location == location_filter)
    
    # Get all matching employees
    employees = query.all()

    from sqlalchemy import func

    employee_ids = [e.id for e in employees]

    # Get license counts for employees (bulk)
    employee_license_counts = {}
    if employee_ids:
        rows = db.session.query(
            LicenseAssignment.employee_id,
            func.count(LicenseAssignment.id)
        ).filter(
            LicenseAssignment.status == 'Active',
            LicenseAssignment.employee_id.isnot(None),
            LicenseAssignment.employee_id.in_(employee_ids)
        ).group_by(LicenseAssignment.employee_id).all()
        employee_license_counts = {emp_id: cnt for emp_id, cnt in rows}

    # Compute last activity (max last_seen across assigned assets)
    employee_activity = {}
    if employee_ids:
        activity_rows = db.session.query(
            Asset.employee_id,
            func.max(Asset.last_seen)
        ).filter(
            Asset.employee_id.isnot(None),
            Asset.employee_id.in_(employee_ids)
        ).group_by(Asset.employee_id).all()

        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)

        for emp_id, last_seen in activity_rows:
            if not last_seen:
                continue
            try:
                last_seen_utc = last_seen.replace(tzinfo=timezone.utc) if last_seen.tzinfo is None else last_seen.astimezone(timezone.utc)
            except Exception:
                continue

            age = now_utc - last_seen_utc
            if age <= timedelta(minutes=30):
                status = 'online'
            elif age <= timedelta(hours=8):
                status = 'away'
            else:
                status = 'offline'

            employee_activity[emp_id] = {
                'status': status,
                'last_seen': last_seen_utc,
                'last_seen_display': last_seen_utc.strftime('%Y-%m-%d %H:%M UTC')
            }
    
    # Build RMM agent online sets (live gateway + 5-min last_seen_at)
    rmm_online_asset_ids = set()
    try:
        import requests as _requests
        RMM_GATEWAY_INTERNAL = current_app.config.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
        cutoff = datetime.utcnow() - timedelta(seconds=300)
        rmm_rows = db.session.execute(
            text("SELECT agent_id, asset_id, last_seen_at FROM rmm_agent WHERE enabled = true AND asset_id IS NOT NULL")
        ).fetchall()
        gateway_online = set()
        try:
            gw_resp = _requests.get(f'{RMM_GATEWAY_INTERNAL}/agents', timeout=2)
            gateway_online = set(gw_resp.json().get('agents', []))
        except Exception:
            pass
        for row in rmm_rows:
            agent_id, asset_id, last_seen_at = row[0], row[1], row[2]
            online = agent_id in gateway_online
            if not online and last_seen_at:
                if isinstance(last_seen_at, str):
                    try:
                        last_seen_at = datetime.fromisoformat(last_seen_at)
                    except ValueError:
                        last_seen_at = None
                if last_seen_at and last_seen_at.replace(tzinfo=None) > cutoff:
                    online = True
            if online:
                rmm_online_asset_ids.add(asset_id)
    except Exception:
        pass

    # Map employee_id -> True if any assigned asset has a live RMM agent
    employee_rmm_online = {}
    for emp in employees:
        for asset in emp.assets:
            if asset.id in rmm_online_asset_ids:
                employee_rmm_online[emp.id] = asset
                break

    # Sort employees
    if sort_by == 'name':
        employees.sort(key=lambda e: e.name.lower() if e.name else '', reverse=(sort_order == 'desc'))
    elif sort_by == 'department':
        employees.sort(key=lambda e: (e.department or '').lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'email':
        employees.sort(key=lambda e: (e.email or '').lower(), reverse=(sort_order == 'desc'))
    elif sort_by == 'assets':
        employees.sort(key=lambda e: len(e.assets), reverse=(sort_order == 'desc'))
    elif sort_by == 'licenses':
        employees.sort(key=lambda e: employee_license_counts.get(e.id, 0), reverse=(sort_order == 'desc'))
    elif sort_by == 'location':
        employees.sort(key=lambda e: (e.location or '').lower(), reverse=(sort_order == 'desc'))
    
    # Get all unique departments for filter dropdown
    all_departments = db.session.query(Employee.department).distinct().filter(
        Employee.department.isnot(None),
        Employee.department != ''
    ).order_by(Employee.department).all()
    departments = [dept[0] for dept in all_departments]

    # Get all unique locations for filter dropdown
    all_locations = db.session.query(Employee.location).distinct().filter(
        Employee.location.isnot(None),
        Employee.location != ''
    ).order_by(Employee.location).all()
    locations = [loc[0] for loc in all_locations]
    
    # Calculate statistics
    total_employees = Employee.query.filter(Employee.is_visible == True).count()
    total_assets_assigned = db.session.query(Asset).filter(Asset.employee_id.isnot(None)).count()
    total_licenses_assigned = LicenseAssignment.query.filter_by(status='Active').filter(
        LicenseAssignment.employee_id.isnot(None)
    ).count()
    departments_count = len(departments)
    
    return render_template('employees.html', 
                         employees=employees,
                         employee_license_counts=employee_license_counts,
                         employee_activity=employee_activity,
                         employee_rmm_online=employee_rmm_online,
                         departments=departments,
                         locations=locations,
                         search=search,
                         department_filter=department_filter,
                         location_filter=location_filter,
                         show_hidden=show_hidden,
                         account_state=account_state,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         total_employees=total_employees,
                         total_assets_assigned=total_assets_assigned,
                         total_licenses_assigned=total_licenses_assigned,
                         departments_count=departments_count)



@bp.route('/employees/bulk-update', methods=['POST'])
@login_required
@manager_required
def bulk_update_employees():
    """Bulk update location or visibility for a set of employees."""
    action = request.form.get('action', 'location')
    employee_ids = request.form.getlist('employee_ids')

    if not employee_ids:
        flash('No employees selected.', 'warning')
        return redirect(request.referrer or url_for('employees.employees'))

    try:
        ids = [int(eid) for eid in employee_ids]
    except ValueError:
        flash('Invalid selection.', 'danger')
        return redirect(request.referrer or url_for('employees.employees'))

    employees_to_update = Employee.query.filter(Employee.id.in_(ids)).all()

    if action == 'location':
        location = request.form.get('location', '').strip() or None
        for emp in employees_to_update:
            emp.location = location
        db.session.commit()
        loc_label = location or '(none)'
        flash(f'Updated location to "{loc_label}" for {len(employees_to_update)} employee(s).', 'success')

    elif action == 'hide' and current_user.role == 'admin':
        for emp in employees_to_update:
            emp.is_visible = False
        db.session.commit()
        flash(f'Hidden {len(employees_to_update)} employee(s).', 'success')

    elif action == 'unhide' and current_user.role == 'admin':
        for emp in employees_to_update:
            emp.is_visible = True
        db.session.commit()
        flash(f'Restored {len(employees_to_update)} employee(s) to visible.', 'success')

    else:
        flash('Unknown action.', 'danger')

    return redirect(request.referrer or url_for('employees.employees'))


@bp.route('/employees/sync-from-m365', methods=['POST'])
@login_required
@manager_required
@license_required
def sync_employees_from_m365():
    """Sync employees from Microsoft 365 users and refresh profile photos."""
    try:
        from m365_config import get_m365_credentials
        tenant_id, client_id, client_secret = get_m365_credentials()

        if not all([tenant_id, client_id, client_secret]):
            flash('M365 credentials not configured. Please configure in Settings.', 'danger')
            return redirect(url_for('employees.employees'))

        m365 = M365Service(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )

        users = m365.get_all_users() or []
        if not users:
            flash('No users returned from Microsoft 365', 'warning')
            return redirect(url_for('employees.employees'))

        created = 0
        updated = 0
        photo_updated = 0
        skipped = 0

        os.makedirs(os.path.join(current_app.config['UPLOAD_FOLDER'], 'employee_photos'), exist_ok=True)

        # Preload employees by email for fast match
        existing_employees = Employee.query.all()
        employees_by_email = {
            (e.email or '').strip().lower(): e
            for e in existing_employees
            if e.email
        }

        for u in users:
            try:
                email = (u.get('mail') or u.get('userPrincipalName') or '').strip()
                display_name = (u.get('displayName') or '').strip()
                if not email or not display_name:
                    skipped += 1
                    continue

                department = (u.get('department') or '').strip() or None
                position = (u.get('jobTitle') or '').strip() or None

                emp = employees_by_email.get(email.lower())
                if not emp:
                    # AD is master — skip M365-only users, don't create new records
                    skipped += 1
                    continue
                else:
                    changed = False
                    if display_name and emp.name != display_name:
                        emp.name = display_name
                        changed = True
                    if department is not None and emp.department != department:
                        emp.department = department
                        changed = True
                    if position is not None and emp.position != position:
                        emp.position = position
                        changed = True
                    if changed:
                        updated += 1

                # Photo sync
                photo_bytes = m365.get_user_photo_bytes(email)
                if photo_bytes:
                    photo_filename = f"employee_{emp.id}.jpg"
                    photo_abs = os.path.join(current_app.config['UPLOAD_FOLDER'], 'employee_photos', photo_filename)
                    try:
                        with open(photo_abs, 'wb') as f:
                            f.write(photo_bytes)
                        import time as _time
                        photo_rel = f"employee_photos/{photo_filename}?v={int(_time.time())}"
                        emp.photo = photo_rel
                        photo_updated += 1
                    except Exception:
                        pass
            except Exception:
                skipped += 1
                continue

        db.session.commit()
        flash(f'M365 sync complete: {updated} updated, {photo_updated} photos refreshed, {skipped} skipped (M365-only users not imported — AD is master)', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error syncing from M365: {str(e)}', 'danger')

    return redirect(url_for('employees.employees'))


def run_ad_employee_sync():
    """Core AD -> employee sync. Callable by the manual route AND the daily scheduler.
    AD is master (name/email/dept/position/phone/ad_guid/ad_dn/ad_enabled); M365 validates
    accountEnabled + licenses; stale (removed-from-AD) employees get ad_enabled=False.
    Returns a result dict {created, updated, skipped, photo_saved, deactivated,
    m365_validated, error}. Never raises. Requires an app context."""
    result = {'created': 0, 'updated': 0, 'skipped': 0, 'photo_saved': 0,
              'deactivated': 0, 'm365_validated': 0, 'error': None}
    try:
        from ldap_service import LDAPService, load_ad_config
        config = load_ad_config(Setting)
        if not config.enabled:
            result['error'] = 'Active Directory integration is not enabled (Settings → Directory).'
            return result

        svc = LDAPService(config)
        ad_users = svc.get_all_users()
        svc.disconnect()

        if not ad_users:
            result['error'] = 'No users returned from AD — check base DN / bind credentials.'
            return result

        # ---- Load M365 users for validation (optional) ----
        m365 = None
        m365_by_upn = {}
        try:
            from m365_config import get_m365_credentials
            _t, _c, _s = get_m365_credentials()
            if _t and _c and _s:
                m365 = M365Service(tenant_id=_t, client_id=_c, client_secret=_s)
                m365_users_raw = m365.get_all_users() or []
                m365_by_upn = {(u.get('userPrincipalName') or '').strip().lower(): u
                               for u in m365_users_raw}
        except Exception:
            m365_by_upn = {}

        # ---- Index existing employees ----
        existing_by_guid  = {e.ad_guid: e for e in Employee.query.filter(Employee.ad_guid.isnot(None)).all()}
        existing_by_email = {(e.email or '').strip().lower(): e
                             for e in Employee.query.filter(Employee.ad_guid.is_(None), Employee.email.isnot(None)).all()}

        os.makedirs(os.path.join(current_app.config['UPLOAD_FOLDER'], 'employee_photos'), exist_ok=True)

        created = updated = skipped = photo_saved = 0
        now = datetime.utcnow()

        for u in ad_users:
            guid         = u.get('ad_guid', '')
            display_name = (u.get('display_name') or '').strip()
            email        = (u.get('email') or '').strip()

            if not display_name:
                skipped += 1
                continue

            # Find or create
            emp = existing_by_guid.get(guid)
            if not emp and email:
                emp = existing_by_email.pop(email.lower(), None)

            if emp is None:
                emp = Employee(ad_guid=guid)
                db.session.add(emp)
                db.session.flush()   # get emp.id
                existing_by_guid[guid] = emp
                created += 1
            else:
                updated += 1

            # AD is master — always overwrite these
            emp.name             = display_name
            emp.ad_guid          = guid
            emp.sam_account_name = u.get('sam_account_name') or emp.sam_account_name
            emp.ad_dn            = u.get('distinguished_name')
            emp.ad_enabled       = u.get('ad_enabled', True)
            emp.ad_last_sync     = now
            if email:
                emp.email = email
            if u.get('department'):
                emp.department = u['department']
            if u.get('title'):
                emp.position = u['title']
            if u.get('phone'):
                emp.phone = u['phone']
            # Store OU-inferred location only when no manual location is set
            if u.get('ou_location') and not emp.id:  # new records only
                pass  # location not a field on Employee model yet — safe to skip

            # M365 validation
            upn_key = (u.get('upn') or email).strip().lower()
            m365_u  = m365_by_upn.get(upn_key)
            if m365_u:
                m365_user_id = m365_u.get('id')
                emp.m365_id              = m365_user_id
                emp.m365_account_enabled = m365_u.get('accountEnabled', False)
                emp.m365_validated_at    = now
                # Fetch + cache M365 licenses for this user
                if m365 and m365_user_id:
                    try:
                        raw_licenses = m365.get_user_licenses(m365_user_id)
                        license_list = []
                        for lic in (raw_licenses or []):
                            sku = lic.get('skuPartNumber') or lic.get('skuId', '')
                            plans = [p.get('servicePlanName', '') for p in lic.get('servicePlans', []) if p.get('provisioningStatus') == 'Success']
                            license_list.append({'sku': sku, 'skuId': lic.get('skuId'), 'plans': plans})
                        emp.m365_licenses_json = json.dumps(license_list)
                        emp.m365_licenses_synced_at = now
                    except Exception:
                        pass

            # Photos — AD thumbnailPhoto first, M365 fallback
            thumb = u.get('thumbnail_photo')
            if thumb and isinstance(thumb, bytes):
                try:
                    photo_rel  = f"employee_photos/employee_{emp.id}.jpg"
                    photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], photo_rel)
                    with open(photo_path, 'wb') as fh:
                        fh.write(thumb)
                    emp.photo = photo_rel
                    photo_saved += 1
                except Exception:
                    thumb = None  # fall through to M365

            if not thumb and m365 and upn_key:
                try:
                    photo_bytes = m365.get_user_photo_bytes(upn_key)
                    if photo_bytes:
                        photo_rel  = f"employee_photos/employee_{emp.id}.jpg"
                        photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], photo_rel)
                        with open(photo_path, 'wb') as fh:
                            fh.write(photo_bytes)
                        emp.photo = photo_rel
                        photo_saved += 1
                except Exception:
                    pass

        # ---- Deactivate AD-synced employees no longer in AD ----
        # Only affects employees that have an ad_guid (previously synced from AD).
        # Manually-added employees (no ad_guid) are never touched.
        synced_guids = {u.get('ad_guid') for u in ad_users if u.get('ad_guid')}
        deactivated = 0
        stale = Employee.query.filter(
            Employee.ad_guid.isnot(None),
            ~Employee.ad_guid.in_(synced_guids)
        ).all()
        for emp in stale:
            if emp.ad_enabled is not False:
                emp.ad_enabled = False
                emp.ad_last_sync = now
                deactivated += 1

        db.session.commit()

        result.update({'created': created, 'updated': updated, 'skipped': skipped,
                       'photo_saved': photo_saved, 'deactivated': deactivated,
                       'm365_validated': len(m365_by_upn)})

    except Exception as e:
        db.session.rollback()
        result['error'] = str(e)
    return result


@bp.route('/employees/sync/ad', methods=['POST'])
@login_required
@manager_required
@license_required
def sync_employees_from_ad():
    """Manual AD employee sync (also runs daily via the scheduler)."""
    r = run_ad_employee_sync()
    if r.get('error'):
        flash(f"AD sync: {r['error']}", 'warning')
    else:
        m365_note = (f", {r['m365_validated']} validated against M365"
                     if r['m365_validated'] else ' (M365 validation skipped)')
        deact_note = f", {r['deactivated']} marked inactive (removed from AD)" if r['deactivated'] else ''
        flash(f"AD sync complete: {r['created']} added, {r['updated']} updated, "
              f"{r['skipped']} skipped, {r['photo_saved']} photos saved{deact_note}{m365_note}.", 'success')
    return redirect(url_for('employees.employees'))


@bp.route('/employees/add', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def add_employee():
    if request.method == 'POST':
        email = request.form.get('email')
        
        # Check if email already exists
        existing_employee = Employee.query.filter_by(email=email).first()
        if existing_employee:
            flash(f'Error: An employee with email {email} already exists ({existing_employee.name})', 'danger')
            return render_template('add_employee.html')
        
        try:
            employee = Employee(
                name=request.form.get('name'),
                email=email,
                department=request.form.get('department'),
                phone=request.form.get('phone')
            )
            
            db.session.add(employee)
            db.session.commit()
            
            flash(f'Employee {employee.name} added successfully!', 'success')
            return redirect(url_for('employees.employees'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding employee: {str(e)}', 'danger')
            return render_template('add_employee.html')
    
    return render_template('add_employee.html')


@bp.route('/employees/<int:employee_id>')
@login_required
@license_required
def view_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    # Get assigned licenses for this employee
    license_assignments = LicenseAssignment.query.filter_by(
        employee_id=employee_id, 
        status='Active'
    ).all()
    # Parse cached M365 license data (populated during AD sync)
    m365_licenses = []
    if employee.m365_licenses_json:
        try:
            m365_licenses = json.loads(employee.m365_licenses_json)
        except Exception:
            pass
    return render_template('view_employee.html', 
                         employee=employee,
                         license_assignments=license_assignments,
                         m365_licenses=m365_licenses)


@bp.route('/employees/<int:employee_id>/disable-ad', methods=['POST'])
@login_required
@manager_required
@license_required
def disable_in_ad(employee_id):
    """Disable employee's Active Directory account.

    AD is master — this only touches AD. If Azure AD Connect is running,
    the disabled state will propagate to M365 automatically on the next
    connect sync cycle (typically ≤30 minutes).
    """
    employee = Employee.query.get_or_404(employee_id)

    if not employee.ad_guid:
        flash('This employee has no linked Active Directory account.', 'warning')
        return redirect(url_for('employees.view_employee', employee_id=employee_id))

    try:
        from ldap_service import LDAPService, load_ad_config
        config = load_ad_config(Setting)
        if not config.enabled:
            flash('Active Directory integration is not enabled in Settings.', 'warning')
            return redirect(url_for('employees.view_employee', employee_id=employee_id))

        svc = LDAPService(config)
        identifier = employee.sam_account_name or employee.email or employee.ad_guid
        result = svc.disable_user(identifier)

        if result.get('success'):
            employee.ad_enabled = False
            db.session.commit()
            flash(
                f'AD account for {employee.name} ({identifier}) has been disabled. '
                'If Azure AD Connect is configured, M365 will reflect this within the next sync cycle.',
                'success',
            )
        else:
            flash(f'Failed to disable AD account: {result.get("error")}', 'danger')

    except Exception as e:
        db.session.rollback()
        flash(f'Error disabling AD account: {e}', 'danger')

    return redirect(url_for('employees.view_employee', employee_id=employee_id))


@bp.route('/employees/<int:employee_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def edit_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Error: Employee name cannot be blank.', 'danger')
            return render_template('edit_employee.html', employee=employee)

        email = request.form.get('email')
        
        # Check if email already exists for another employee
        if email:
            existing_employee = Employee.query.filter_by(email=email).first()
            if existing_employee and existing_employee.id != employee.id:
                flash(f'Error: An employee with email {email} already exists ({existing_employee.name})', 'danger')
                return render_template('edit_employee.html', employee=employee)
        
        try:
            employee.name = name
            employee.email = email
            employee.department = request.form.get('department')
            employee.phone = request.form.get('phone')
            employee.position = request.form.get('position')
            employee.location = request.form.get('location') or None
            if current_user.role == 'admin':
                # getlist returns all values; checkbox sends '1' when checked
                is_visible_vals = request.form.getlist('is_visible')
                employee.is_visible = '1' in is_visible_vals
            
            db.session.commit()
            
            flash(f'Employee {employee.name} updated successfully!', 'success')
            return redirect(url_for('employees.view_employee', employee_id=employee.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating employee: {str(e)}', 'danger')
            return render_template('edit_employee.html', employee=employee)
    
    return render_template('edit_employee.html', employee=employee)


@bp.route('/employees/<int:employee_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    employee_name = employee.name
    
    # Check if employee has assigned assets
    if employee.assets:
        flash(f'Cannot delete employee {employee_name}. They have {len(employee.assets)} assigned assets. Please unassign all assets first.', 'danger')
        return redirect(url_for('employees.view_employee', employee_id=employee.id))
    
    # Check if employee has assigned licenses
    active_licenses = LicenseAssignment.query.filter_by(
        employee_id=employee.id, 
        status='Active'
    ).count()
    if active_licenses > 0:
        flash(f'Cannot delete employee {employee_name}. They have {active_licenses} assigned licenses. Please return all licenses first.', 'danger')
        return redirect(url_for('employees.view_employee', employee_id=employee.id))
    
    db.session.delete(employee)
    db.session.commit()
    
    flash(f'Employee {employee_name} deleted successfully', 'success')
    return redirect(url_for('employees.employees'))


def verify_and_park_offboards():
    """Park offboards ONLY for employees AD positively reports as disabled — never on a
    mere 'absent from the last sync' (a scope gap / partial pull could otherwise flag an
    active person). For each visible employee currently flagged disabled:
      * AD says DISABLED -> park a 1-click offboard (confident).
      * AD says ENABLED  -> false flag; self-heal ad_enabled=True (don't park).
      * AD says ABSENT/ERROR -> leave for MANUAL review (never auto-offboard).
    Returns {parked, healed, absent, errors}. Requires an app context."""
    from ldap_service import LDAPService, load_ad_config
    import workflow_engine
    res = {'parked': 0, 'healed': 0, 'absent': 0, 'errors': 0}
    cfg = load_ad_config(Setting)
    if not cfg.enabled:
        return res
    candidates = Employee.query.filter(
        Employee.is_visible == True,
        db.or_(Employee.ad_enabled == False, Employee.m365_account_enabled == False),
    ).all()
    if not candidates:
        return res
    svc = LDAPService(cfg)
    try:
        svc.connect()
        for e in candidates:
            ident = e.sam_account_name or e.email
            state = svc.get_account_state(ident) if ident else 'absent'
            if state == 'disabled':
                if workflow_engine.park_offboard(e.id, e.name, reason='AD account disabled (live-verified)'):
                    res['parked'] += 1
            elif state == 'enabled':
                e.ad_enabled = True   # sync false-flagged an active user — self-heal
                res['healed'] += 1
            else:  # 'absent' or 'error' — too weak to auto-offboard
                res['absent' if state == 'absent' else 'errors'] += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('verify_and_park_offboards failed')
    finally:
        try:
            svc.disconnect()
        except Exception:
            pass
    return res


def _offboard_employee(employee, actor='system', actor_email=None):
    """Core offboarding, reusable by the manual button AND the 1-click approval handler:
    unassign assets (-> 'Pending Return' so they surface for collection), return active
    licenses, HIDE the employee (is_visible=False), and open an [OFFBOARD] checklist ticket.
    Returns a result dict. Caller commits are handled here."""
    steps_done = []

    asset_list = []
    for asset in list(employee.assets):
        asset_list.append(f"{asset.asset_tag} ({asset.name})")
        asset.employee_id = None
        asset.status = 'Pending Return'   # surfaces in a "to collect" view, not silently Available
        asset.updated_at = datetime.utcnow()
    if asset_list:
        steps_done.append(f"Assets unassigned (Pending Return): {', '.join(asset_list)}")
        db.session.flush()

    active_licenses = LicenseAssignment.query.filter_by(
        employee_id=employee.id, status='Active').all()
    for la in active_licenses:
        la.status = 'Returned'
        la.returned_date = datetime.utcnow()
    if active_licenses:
        steps_done.append(f"Licenses returned: {len(active_licenses)}")

    # Hide the offboarded employee from the active roster.
    employee.is_visible = False

    checklist = (
        "## Offboarding Checklist\n\n"
        f"**Employee:** {employee.name} ({employee.email or 'no email'})\n"
        f"**Department:** {employee.department or 'N/A'}\n"
        f"**Initiated by:** {actor}\n\n"
        "### Automated Steps Completed\n"
    )
    for step in steps_done:
        checklist += f"- [x] {step}\n"
    checklist += (
        "\n### Manual Steps Required\n"
        "- [ ] Disable Active Directory / Azure AD account\n"
        "- [ ] Remove from all security groups and distribution lists\n"
        "- [ ] Revoke MFA tokens and app-specific passwords\n"
        "- [ ] Collect physical access cards / keys (assets marked Pending Return)\n"
        "- [ ] Remove from VPN / remote access\n"
        "- [ ] Archive or transfer email and files\n"
        "- [ ] Update org chart and documentation\n"
    )
    ticket = SupportTicket(
        status='Open', priority='High', source='system',
        category='HR / Offboarding',
        subject=f'[OFFBOARD] {employee.name} — Offboarding Checklist',
        description=checklist,
        reporter_name=actor, reporter_email=actor_email,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.session.add(ticket)
    db.session.commit()
    return {'ticket_id': ticket.id, 'assets_unassigned': len(asset_list),
            'licenses_returned': len(active_licenses)}


@bp.route('/employees/<int:employee_id>/offboard', methods=['POST'])
@login_required
@manager_required
@license_required
def offboard_employee(employee_id):
    """Offboarding workflow: unassign assets, revoke licenses, hide, create checklist ticket."""
    employee = Employee.query.get_or_404(employee_id)
    res = _offboard_employee(employee, actor=current_user.username, actor_email=current_user.email)
    flash(
        f"Offboarding complete for {employee.name}. Ticket #{res['ticket_id']} created. "
        f"{res['assets_unassigned']} asset(s) set Pending Return, "
        f"{res['licenses_returned']} license(s) returned.",
        'success')
    return redirect(url_for('employees.employees'))


@bp.route('/employees/import', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def import_employees():
    """Import employees from CSV file"""
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        if not file.filename.endswith('.csv'):
            flash('Only CSV files are allowed', 'danger')
            return redirect(request.url)
        
        results = {'success': 0, 'errors': []}
        
        try:
            # Read CSV file
            stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
            csv_reader = csv.DictReader(stream)
            
            row_num = 1
            for row in csv_reader:
                row_num += 1
                
                try:
                    # Validate required fields
                    name = row.get('Name', '').strip()
                    if not name:
                        results['errors'].append({
                            'row': row_num,
                            'message': 'Name is required'
                        })
                        continue
                    
                    email = row.get('Email', '').strip()
                    
                    # Check for duplicate email
                    if email:
                        existing = Employee.query.filter_by(email=email).first()
                        if existing:
                            results['errors'].append({
                                'row': row_num,
                                'message': f'Employee with email {email} already exists'
                            })
                            continue
                    
                    # Create employee
                    employee = Employee(
                        name=name,
                        email=email if email else None,
                        phone=row.get('Phone', '').strip() or None,
                        department=row.get('Department', '').strip() or None,
                        position=row.get('Position', '').strip() or None
                    )
                    
                    db.session.add(employee)
                    results['success'] += 1
                    
                except Exception as e:
                    results['errors'].append({
                        'row': row_num,
                        'message': str(e)
                    })
            
            db.session.commit()
            
            if results['success'] > 0:
                flash(f'Successfully imported {results["success"]} employee(s)!', 'success')
            if results['errors']:
                flash(f'Skipped {len(results["errors"])} row(s) with errors', 'warning')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error reading CSV file: {str(e)}', 'danger')
            results = {'success': 0, 'errors': [{'row': 0, 'message': str(e)}]}
        
        return render_template('import_employees.html', results=results)
    
    return render_template('import_employees.html')


@bp.route('/employees/export/csv')
@login_required
@license_required
def export_employees_csv():
    """Export all employees to CSV"""
    employees = Employee.query.all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Name', 'Email', 'Phone', 'Department', 'Position', 'Asset Count'])
    
    # Write data
    for employee in employees:
        writer.writerow([
            employee.name,
            employee.email or '',
            employee.phone or '',
            employee.department or '',
            employee.position or '',
            len(employee.assets)
        ])
    
    # Prepare response
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'employees_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
    )


@bp.route('/employees/template')
@login_required
@license_required
def download_employee_template():
    """Download a sample employee CSV template"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Name', 'Email', 'Phone', 'Department', 'Position'])
    
    # Write sample rows
    writer.writerow(['John Doe', 'john.doe@company.com', '555-0100', 'Engineering', 'Software Engineer'])
    writer.writerow(['Jane Smith', 'jane.smith@company.com', '555-0101', 'Marketing', 'Marketing Manager'])
    writer.writerow(['Bob Johnson', 'bob.johnson@company.com', '555-0102', 'Sales', 'Sales Representative'])
    writer.writerow(['Alice Williams', 'alice.williams@company.com', '555-0103', 'HR', 'HR Specialist'])
    writer.writerow(['Charlie Brown', 'charlie.brown@company.com', '555-0104', 'IT', 'System Administrator'])
    
    # Prepare response
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='employee_import_template.csv'
    )


# ---------------------------------------------------------------------------
# ID Card routes
# ---------------------------------------------------------------------------

@bp.route('/employees/<int:employee_id>/upload-photo', methods=['POST'])
@login_required
@manager_required
@license_required
def upload_employee_photo(employee_id):
    """Manual photo upload for an employee (fallback when AD/M365 has no photo)."""
    employee = Employee.query.get_or_404(employee_id)

    if 'photo' not in request.files:
        flash('No file selected.', 'warning')
        return redirect(url_for('employees.edit_employee', employee_id=employee_id))

    file = request.files['photo']
    if file.filename == '':
        flash('No file selected.', 'warning')
        return redirect(url_for('employees.edit_employee', employee_id=employee_id))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in {'jpg', 'jpeg', 'png', 'gif', 'webp'}:
        flash('Invalid file type. Only jpg, png, gif, webp allowed.', 'danger')
        return redirect(url_for('employees.edit_employee', employee_id=employee_id))

    try:
        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'employee_photos')
        os.makedirs(upload_dir, exist_ok=True)

        # Use same naming convention as M365/AD sync so they can overwrite this
        filename = f"employee_{employee_id}.{ext}"
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)

        import time
        photo_rel = f"employee_photos/{filename}?v={int(time.time())}"
        employee.photo = photo_rel
        db.session.commit()

        flash(f'Photo updated for {employee.name}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Photo upload failed: {e}', 'danger')

    return redirect(url_for('employees.edit_employee', employee_id=employee_id))


@bp.route('/employees/id-cards')
@login_required
@license_required
def id_card_designer():
    """Bulk ID card designer — print any selection of employees."""
    # Include hidden (is_visible=False) employees — they may still need ID cards
    employees = Employee.query.order_by(Employee.name).all()
    # Ensure all employees have a card number
    changed = False
    used = {e.card_number for e in employees if e.card_number}
    import random as _random
    for emp in employees:
        if not emp.card_number:
            while True:
                num = str(_random.randint(100000, 999999))
                if num not in used:
                    used.add(num)
                    break
            emp.card_number = num
            changed = True
    if changed:
        db.session.commit()

    company_name = Setting.query.filter_by(key='company_name').first()
    company_logo_url = url_for('static', filename='images/company_logo.png') if os.path.exists(
        os.path.join(current_app.root_path, 'static', 'images', 'company_logo.png')) else None

    all_locations = db.session.query(Employee.location).distinct().filter(
        Employee.location.isnot(None),
        Employee.location != ''
    ).order_by(Employee.location).all()
    locations = [loc[0] for loc in all_locations]

    return render_template('id_card_designer.html', employees=employees,
                           company_name=company_name.value if company_name else 'Cirque Corporation',
                           company_logo_url=company_logo_url,
                           locations=locations)


@bp.route('/employees/<int:employee_id>/id-card')
@login_required
@license_required
def employee_id_card(employee_id):
    """Single-employee card preview / print page."""
    employee = Employee.query.get_or_404(employee_id)
    if not employee.card_number:
        import random as _random
        employee.card_number = str(_random.randint(100000, 999999))
        db.session.commit()

    company_name = Setting.query.filter_by(key='company_name').first()
    company_logo_url = url_for('static', filename='images/company_logo.png') if os.path.exists(
        os.path.join(current_app.root_path, 'static', 'images', 'company_logo.png')) else None

    return render_template('id_card_designer.html', employees=[employee], single=True,
                           company_name=company_name.value if company_name else 'Cirque Corporation',
                           company_logo_url=company_logo_url)