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


bp = Blueprint('licenses', __name__)


# ==================== SOFTWARE LICENSES ====================



@bp.route('/licenses')
@login_required
@license_required
def licenses():
    """View all software licenses"""
    # Get filter parameters
    software = request.args.get('software', '')
    vendor = request.args.get('vendor', '')
    license_type = request.args.get('license_type', '')
    status = request.args.get('status', '')
    
    # Build query
    query = License.query
    
    if software:
        query = query.filter(License.software_name.ilike(f'%{software}%'))
    if vendor:
        query = query.filter(License.vendor.ilike(f'%{vendor}%'))
    if license_type:
        query = query.filter_by(license_type=license_type)
    if status:
        query = query.filter_by(status=status)
    
    licenses = query.order_by(License.software_name).all()
    
    # Calculate statistics
    total_licenses = License.query.count()
    active_licenses = License.query.filter_by(status='Active').count()
    expiring_soon = sum(1 for lic in License.query.filter_by(status='Active').all() if lic.is_expiring_soon(30))
    
    # Total cost calculations
    total_purchase_cost = db.session.query(db.func.sum(License.purchase_cost)).filter(License.purchase_cost.isnot(None)).scalar() or 0
    total_annual_cost = db.session.query(db.func.sum(License.annual_cost)).filter(License.annual_cost.isnot(None)).scalar() or 0
    
    return render_template('licenses.html',
                         licenses=licenses,
                         total_licenses=total_licenses,
                         active_licenses=active_licenses,
                         expiring_soon=expiring_soon,
                         total_purchase_cost=total_purchase_cost,
                         total_annual_cost=total_annual_cost)


@bp.route('/licenses/add', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def add_license():
    """Add new software license"""
    if request.method == 'POST':
        try:
            license = License(
                software_name=request.form['software_name'],
                vendor=request.form.get('vendor'),
                license_type=request.form.get('license_type'),
                license_key=request.form.get('license_key'),
                total_licenses=int(request.form.get('total_licenses', 1)),
                purchase_date=datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date() if request.form.get('purchase_date') else None,
                expiry_date=datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date() if request.form.get('expiry_date') else None,
                renewal_date=datetime.strptime(request.form['renewal_date'], '%Y-%m-%d').date() if request.form.get('renewal_date') else None,
                purchase_cost=float(request.form['purchase_cost']) if request.form.get('purchase_cost') else None,
                annual_cost=float(request.form['annual_cost']) if request.form.get('annual_cost') else None,
                status=request.form.get('status', 'Active'),
                notes=request.form.get('notes')
            )
            
            db.session.add(license)
            db.session.commit()
            
            flash(f'License for {license.software_name} added successfully!', 'success')
            return redirect(url_for('licenses.licenses'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding license: {str(e)}', 'danger')
    
    return render_template('add_license.html')


@bp.route('/licenses/<int:license_id>')
@login_required
@license_required
def view_license(license_id):
    """View license details"""
    license = License.query.get_or_404(license_id)
    assignments = LicenseAssignment.query.filter_by(license_id=license_id).order_by(LicenseAssignment.assigned_date.desc()).all()
    available = license.get_available_licenses()
    
    # Get unassigned assets and employees for assignment dropdown
    assets = Asset.query.order_by(Asset.asset_tag).all()
    employees = Employee.query.order_by(Employee.name).all()
    
    return render_template('view_license.html', 
                         license=license, 
                         assignments=assignments,
                         available=available,
                         assets=assets,
                         employees=employees)


@bp.route('/licenses/<int:license_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def edit_license(license_id):
    """Edit license"""
    license = License.query.get_or_404(license_id)
    
    if request.method == 'POST':
        try:
            license.software_name = request.form['software_name']
            license.vendor = request.form.get('vendor')
            license.license_type = request.form.get('license_type')
            license.license_key = request.form.get('license_key')
            license.total_licenses = int(request.form.get('total_licenses', 1))
            license.purchase_date = datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date() if request.form.get('purchase_date') else None
            license.expiry_date = datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date() if request.form.get('expiry_date') else None
            license.renewal_date = datetime.strptime(request.form['renewal_date'], '%Y-%m-%d').date() if request.form.get('renewal_date') else None
            license.purchase_cost = float(request.form['purchase_cost']) if request.form.get('purchase_cost') else None
            license.annual_cost = float(request.form['annual_cost']) if request.form.get('annual_cost') else None
            license.status = request.form.get('status', 'Active')
            license.notes = request.form.get('notes')
            license.updated_at = datetime.utcnow()
            
            db.session.commit()
            flash('License updated successfully!', 'success')
            return redirect(url_for('licenses.view_license', license_id=license.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating license: {str(e)}', 'danger')
    
    return render_template('edit_license.html', license=license)


@bp.route('/licenses/<int:license_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_license(license_id):
    """Delete license"""
    license = License.query.get_or_404(license_id)
    
    try:
        db.session.delete(license)
        db.session.commit()
        flash('License deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting license: {str(e)}', 'danger')
    
    return redirect(url_for('licenses.licenses'))


@bp.route('/licenses/<int:license_id>/assign', methods=['POST'])
@login_required
@manager_required
@license_required
def assign_license(license_id):
    """Assign license to asset or employee"""
    license = License.query.get_or_404(license_id)
    
    # Check if licenses are available
    if license.get_available_licenses() <= 0:
        flash('No available licenses to assign!', 'warning')
        return redirect(url_for('licenses.view_license', license_id=license_id))
    
    # Require employee assignment for user-based licenses
    employee_id = request.form.get('employee_id')
    if not employee_id:
        flash('Employee assignment is required for software licenses!', 'warning')
        return redirect(url_for('licenses.view_license', license_id=license_id))
    
    try:
        employee = Employee.query.get_or_404(int(employee_id))
        asset_id = request.form.get('asset_id')
        product_component = request.form.get('product_component', '').strip()
        
        assignment = LicenseAssignment(
            license_id=license_id,
            asset_id=int(asset_id) if asset_id else None,
            employee_id=int(employee_id),
            product_component=product_component if product_component else None,
            notes=request.form.get('notes'),
            status='Active'
        )
        
        db.session.add(assignment)
        db.session.commit()
        
        component_text = f" ({product_component})" if product_component else ""
        flash(f'License assigned to {employee.name}{component_text} successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error assigning license: {str(e)}', 'danger')
    
    return redirect(url_for('licenses.view_license', license_id=license_id))


@bp.route('/licenses/assignments/<int:assignment_id>/return', methods=['POST'])
@login_required
@manager_required
@license_required
def return_license(assignment_id):
    """Return/unassign license"""
    assignment = LicenseAssignment.query.get_or_404(assignment_id)
    
    try:
        assignment.status = 'Returned'
        db.session.commit()
        flash('License returned successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error returning license: {str(e)}', 'danger')
    
    return redirect(url_for('licenses.view_license', license_id=assignment.license_id))