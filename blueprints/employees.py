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
    
    # Base query
    query = Employee.query
    
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
    
    # Get all unique departments for filter dropdown
    all_departments = db.session.query(Employee.department).distinct().filter(
        Employee.department.isnot(None),
        Employee.department != ''
    ).order_by(Employee.department).all()
    departments = [dept[0] for dept in all_departments]
    
    # Calculate statistics
    total_employees = Employee.query.count()
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
                         search=search,
                         department_filter=department_filter,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         total_employees=total_employees,
                         total_assets_assigned=total_assets_assigned,
                         total_licenses_assigned=total_licenses_assigned,
                         departments_count=departments_count)


@bp.route('/employees/sync-from-m365', methods=['POST'])
@login_required
@manager_required
@license_required
def sync_employees_from_m365():
    """Sync employees from Microsoft 365 users and refresh profile photos."""
    try:
        tenant_id_setting = Setting.query.filter_by(key='m365_tenant_id').first()
        client_id_setting = Setting.query.filter_by(key='m365_client_id').first()
        client_secret_setting = Setting.query.filter_by(key='m365_client_secret').first()

        if not all([tenant_id_setting, client_id_setting, client_secret_setting]):
            flash('M365 credentials not configured. Please configure in Settings.', 'danger')
            return redirect(url_for('employees.employees'))

        m365 = M365Service(
            tenant_id=tenant_id_setting.value,
            client_id=client_id_setting.value,
            client_secret=client_secret_setting.value
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
                    emp = Employee(
                        name=display_name,
                        email=email,
                        department=department,
                        position=position,
                    )
                    db.session.add(emp)
                    db.session.flush()
                    employees_by_email[email.lower()] = emp
                    created += 1
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
                    photo_rel = f"employee_photos/employee_{emp.id}.jpg"
                    photo_abs = os.path.join(current_app.config['UPLOAD_FOLDER'], photo_rel)
                    try:
                        with open(photo_abs, 'wb') as f:
                            f.write(photo_bytes)
                        if emp.photo != photo_rel:
                            emp.photo = photo_rel
                        photo_updated += 1
                    except Exception:
                        pass
            except Exception:
                skipped += 1
                continue

        db.session.commit()
        flash(f'M365 sync complete: {created} created, {updated} updated, {photo_updated} photos refreshed, {skipped} skipped', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error syncing from M365: {str(e)}', 'danger')

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
    return render_template('view_employee.html', 
                         employee=employee,
                         license_assignments=license_assignments)


@bp.route('/employees/<int:employee_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def edit_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    
    if request.method == 'POST':
        email = request.form.get('email')
        
        # Check if email already exists for another employee
        if email:
            existing_employee = Employee.query.filter_by(email=email).first()
            if existing_employee and existing_employee.id != employee.id:
                flash(f'Error: An employee with email {email} already exists ({existing_employee.name})', 'danger')
                return render_template('edit_employee.html', employee=employee)
        
        try:
            employee.name = request.form.get('name')
            employee.email = email
            employee.department = request.form.get('department')
            employee.phone = request.form.get('phone')
            employee.position = request.form.get('position')
            
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


@bp.route('/employees/<int:employee_id>/offboard', methods=['POST'])
@login_required
@manager_required
@license_required
def offboard_employee(employee_id):
    """Offboarding workflow: unassign assets, revoke licenses, create checklist ticket."""
    employee = Employee.query.get_or_404(employee_id)

    steps_done = []

    # 1. Unassign all assets
    asset_list = []
    for asset in list(employee.assets):
        asset_list.append(f"{asset.asset_tag} ({asset.name})")
        asset.employee_id = None
        asset.status = 'Available'
        asset.updated_at = datetime.utcnow()
    if asset_list:
        steps_done.append(f"Assets unassigned: {', '.join(asset_list)}")
        db.session.flush()

    # 2. Revoke active license assignments
    active_licenses = LicenseAssignment.query.filter_by(
        employee_id=employee.id, status='Active').all()
    license_list = []
    for la in active_licenses:
        la.status = 'Returned'
        la.returned_date = datetime.utcnow()
        license_list.append(str(la.id))
    if license_list:
        steps_done.append(f"Licenses returned: {len(license_list)}")

    # 3. Create offboarding checklist ticket
    checklist = (
        "## Offboarding Checklist\n\n"
        f"**Employee:** {employee.name} ({employee.email or 'no email'})\n"
        f"**Department:** {employee.department or 'N/A'}\n"
        f"**Initiated by:** {current_user.username}\n\n"
        "### Automated Steps Completed\n"
    )
    for step in steps_done:
        checklist += f"- [x] {step}\n"
    checklist += (
        "\n### Manual Steps Required\n"
        "- [ ] Disable Active Directory / Azure AD account\n"
        "- [ ] Remove from all security groups and distribution lists\n"
        "- [ ] Revoke MFA tokens and app-specific passwords\n"
        "- [ ] Collect physical access cards / keys\n"
        "- [ ] Remove from VPN / remote access\n"
        "- [ ] Archive or transfer email and files\n"
        "- [ ] Update org chart and documentation\n"
    )

    ticket = SupportTicket(
        status='Open', priority='High', source='system',
        category='HR / Offboarding',
        subject=f'[OFFBOARD] {employee.name} — Offboarding Checklist',
        description=checklist,
        reporter_name=current_user.username,
        reporter_email=current_user.email,
        created_by_user_id=current_user.id,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.session.add(ticket)
    db.session.commit()

    flash(
        f'Offboarding initiated for {employee.name}. '
        f'Ticket #{ticket.id} created with checklist. '
        f'{len(asset_list)} asset(s) unassigned, {len(license_list)} license(s) returned.',
        'success')
    return redirect(url_for('employees.view_employee', employee_id=employee.id))


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