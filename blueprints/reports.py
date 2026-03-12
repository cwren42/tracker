import csv
import io
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
try:
    import report_engine as _report_engine
except ImportError:
    _report_engine = None

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


bp = Blueprint('reports', __name__)


# ==================== CUSTOM REPORTS ROUTES ====================


# ════════════════════════════════════════════════════════════════════════════════
# REPORT ROUTES
# ════════════════════════════════════════════════════════════════════════════════



@bp.route('/reports/custom')
@login_required
@license_required
def custom_reports():
    """Custom report builder page"""
    categories = db.session.query(Asset.category).distinct().all()
    return render_template('custom_reports.html', categories=categories)


@bp.route('/reports/custom/generate', methods=['POST'])
@login_required
@license_required
def generate_custom_report():
    """Generate custom report based on configuration"""
    try:
        config = request.get_json()
        fields = config.get('fields', [])
        filter_category = config.get('filterCategory', '')
        filter_status = config.get('filterStatus', '')
        filter_lifecycle = config.get('filterLifecycle', '')
        group_by = config.get('groupBy', '')
        sort_by = config.get('sortBy', 'asset_tag')
        
        # Build query
        query = Asset.query
        
        if filter_category:
            query = query.filter_by(category=filter_category)
        
        if filter_status:
            query = query.filter_by(status=filter_status)
        
        # Get assets
        all_assets = query.all()
        
        # Apply lifecycle filter if needed
        if filter_lifecycle:
            all_assets = [asset for asset in all_assets 
                         if asset.purchase_date and asset.expected_life_years 
                         and asset.get_lifecycle_status() == filter_lifecycle]
        
        # Sort assets
        if sort_by == 'purchase_date':
            all_assets.sort(key=lambda x: x.purchase_date or datetime.min.date())
        elif sort_by == 'purchase_cost':
            all_assets.sort(key=lambda x: x.purchase_cost or 0, reverse=True)
        elif sort_by == 'name':
            all_assets.sort(key=lambda x: x.name)
        elif sort_by == 'category':
            all_assets.sort(key=lambda x: x.category)
        else:  # asset_tag
            all_assets.sort(key=lambda x: x.asset_tag)
        
        # Build asset data
        assets_data = []
        total_value = 0
        total_age = 0
        age_count = 0
        
        for asset in all_assets:
            asset_dict = {}
            
            for field in fields:
                if field == 'asset_tag':
                    asset_dict[field] = asset.asset_tag
                elif field == 'name':
                    asset_dict[field] = asset.name
                elif field == 'category':
                    asset_dict[field] = asset.category
                elif field == 'manufacturer':
                    asset_dict[field] = asset.manufacturer or ''
                elif field == 'model':
                    asset_dict[field] = asset.model or ''
                elif field == 'serial_number':
                    asset_dict[field] = asset.serial_number or ''
                elif field == 'status':
                    asset_dict[field] = asset.status
                elif field == 'purchase_date':
                    asset_dict[field] = asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else ''
                elif field == 'purchase_cost':
                    asset_dict[field] = float(asset.purchase_cost) if asset.purchase_cost else 0
                    total_value += asset_dict[field]
                elif field == 'warranty_expiry':
                    asset_dict[field] = asset.warranty_expiry.strftime('%Y-%m-%d') if asset.warranty_expiry else ''
                elif field == 'assigned_to':
                    asset_dict[field] = asset.assigned_employee.name if asset.assigned_employee else ''
                elif field == 'department':
                    asset_dict[field] = asset.department or ''
                elif field == 'location':
                    asset_dict[field] = asset.location or ''
                elif field == 'lifecycle_status':
                    if asset.purchase_date and asset.expected_life_years:
                        asset_dict[field] = asset.get_lifecycle_status()
                    else:
                        asset_dict[field] = ''
                elif field == 'age_years':
                    if asset.purchase_date:
                        age = asset.get_age_years()
                        asset_dict[field] = round(age, 1)
                        total_age += age
                        age_count += 1
                    else:
                        asset_dict[field] = ''
                elif field == 'condition':
                    asset_dict[field] = asset.condition or ''
            
            assets_data.append(asset_dict)
        
        # Group if needed
        grouped_data = {}
        if group_by and group_by in fields:
            for asset in assets_data:
                group_key = asset.get(group_by, 'Not Set')
                if group_key not in grouped_data:
                    grouped_data[group_key] = []
                grouped_data[group_key].append(asset)
        
        # Calculate summary
        summary = {
            'count': len(assets_data),
            'total_value': total_value,
            'avg_age': total_age / age_count if age_count > 0 else 0
        }
        
        return jsonify({
            'success': True,
            'assets': assets_data,
            'fields': fields,
            'grouped': grouped_data if group_by else None,
            'summary': summary
        })
    
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/reports/custom/export', methods=['POST'])
@login_required
@license_required
def export_custom_report():
    """Export custom report to CSV"""
    try:
        config = json.loads(request.form.get('config'))
        fields = config.get('fields', [])
        filter_category = config.get('filterCategory', '')
        filter_status = config.get('filterStatus', '')
        filter_lifecycle = config.get('filterLifecycle', '')
        group_by = config.get('groupBy', '')
        sort_by = config.get('sortBy', 'asset_tag')
        
        # Build query (same as generate)
        query = Asset.query
        
        if filter_category:
            query = query.filter_by(category=filter_category)
        
        if filter_status:
            query = query.filter_by(status=filter_status)
        
        all_assets = query.all()
        
        if filter_lifecycle:
            all_assets = [asset for asset in all_assets 
                         if asset.purchase_date and asset.expected_life_years 
                         and asset.get_lifecycle_status() == filter_lifecycle]
        
        # Sort
        if sort_by == 'purchase_date':
            all_assets.sort(key=lambda x: x.purchase_date or datetime.min.date())
        elif sort_by == 'purchase_cost':
            all_assets.sort(key=lambda x: x.purchase_cost or 0, reverse=True)
        elif sort_by == 'name':
            all_assets.sort(key=lambda x: x.name)
        elif sort_by == 'category':
            all_assets.sort(key=lambda x: x.category)
        else:
            all_assets.sort(key=lambda x: x.asset_tag)
        
        # Group if needed
        if group_by:
            grouped = {}
            for asset in all_assets:
                group_key = getattr(asset, group_by, 'Not Set') or 'Not Set'
                if group_key not in grouped:
                    grouped[group_key] = []
                grouped[group_key].append(asset)
            all_assets = []
            for group_key in sorted(grouped.keys()):
                all_assets.extend(grouped[group_key])
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        header = [field.replace('_', ' ').title() for field in fields]
        writer.writerow(header)
        
        # Data
        current_group = None
        for asset in all_assets:
            # Group separator if grouping
            if group_by:
                group_value = getattr(asset, group_by, 'Not Set') or 'Not Set'
                if group_value != current_group:
                    writer.writerow([])  # Empty row
                    writer.writerow([f"{group_by.replace('_', ' ').title()}: {group_value}"])
                    current_group = group_value
            
            row = []
            for field in fields:
                if field == 'asset_tag':
                    row.append(asset.asset_tag)
                elif field == 'name':
                    row.append(asset.name)
                elif field == 'category':
                    row.append(asset.category)
                elif field == 'manufacturer':
                    row.append(asset.manufacturer or '')
                elif field == 'model':
                    row.append(asset.model or '')
                elif field == 'serial_number':
                    row.append(asset.serial_number or '')
                elif field == 'status':
                    row.append(asset.status)
                elif field == 'purchase_date':
                    row.append(asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '')
                elif field == 'purchase_cost':
                    row.append(asset.purchase_cost or '')
                elif field == 'warranty_expiry':
                    row.append(asset.warranty_expiry.strftime('%Y-%m-%d') if asset.warranty_expiry else '')
                elif field == 'assigned_to':
                    row.append(asset.assigned_employee.name if asset.assigned_employee else '')
                elif field == 'department':
                    row.append(asset.department or '')
                elif field == 'location':
                    row.append(asset.location or '')
                elif field == 'lifecycle_status':
                    if asset.purchase_date and asset.expected_life_years:
                        row.append(asset.get_lifecycle_status())
                    else:
                        row.append('')
                elif field == 'age_years':
                    if asset.purchase_date:
                        row.append(round(asset.get_age_years(), 1))
                    else:
                        row.append('')
                elif field == 'condition':
                    row.append(asset.condition or '')
            
            writer.writerow(row)
        
        # Send file
        output.seek(0)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'custom_report_{timestamp}.csv'
        )
    
    except Exception as e:
        return f"Error exporting report: {str(e)}", 500


@bp.route('/reports/custom/save', methods=['POST'])
@login_required
@license_required
def save_custom_report():
    """Save a custom report"""
    try:
        data = request.get_json()
        report_name = data.get('name')
        report_type = data.get('report_type', 'list')
        description = data.get('description', '')
        config = json.dumps(data.get('config', {}))
        is_public = data.get('is_public', False)
        
        if not report_name:
            return jsonify({'success': False, 'message': 'Report name is required'}), 400
        
        # Create new report
        report = CustomReport(
            user_id=current_user.id,
            name=report_name,
            description=description,
            report_type=report_type,
            config=config,
            is_public=is_public
        )
        db.session.add(report)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Report saved successfully', 'report_id': report.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/reports/custom/list', methods=['GET'])
@login_required
@license_required
def list_custom_reports():
    """Get list of user's saved reports"""
    try:
        reports = CustomReport.query.filter(
            db.or_(
                CustomReport.user_id == current_user.id,
                CustomReport.is_public == True
            )
        ).order_by(CustomReport.created_at.desc()).all()
        
        reports_data = [{
            'id': r.id,
            'name': r.name,
            'description': r.description,
            'report_type': r.report_type,
            'config': json.loads(r.config),
            'is_own': r.user_id == current_user.id,
            'created_at': r.created_at.strftime('%Y-%m-%d %H:%M')
        } for r in reports]
        
        return jsonify({'success': True, 'reports': reports_data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/reports/custom/delete/<int:report_id>', methods=['DELETE'])
@login_required
@license_required
def delete_custom_report(report_id):
    """Delete a custom report"""
    try:
        report = CustomReport.query.get_or_404(report_id)
        
        if report.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        db.session.delete(report)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Report deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/reports')
@login_required
@license_required
def reports():
    # Category breakdown
    category_stats = db.session.query(
        Asset.category, 
        db.func.count(Asset.id).label('count'),
        db.func.sum(Asset.purchase_cost).label('total_cost')
    ).group_by(Asset.category).all()
    
    # Status breakdown
    status_stats = db.session.query(
        Asset.status,
        db.func.count(Asset.id).label('count')
    ).group_by(Asset.status).all()
    
    # Department breakdown
    dept_stats = db.session.query(
        Employee.department,
        db.func.count(Asset.id).label('count')
    ).join(Asset, Employee.id == Asset.employee_id, isouter=True).group_by(Employee.department).all()
    
    # Warranty expiring soon
    thirty_days = datetime.utcnow().date() + timedelta(days=30)
    expiring = Asset.query.filter(
        Asset.warranty_expiry.isnot(None),
        Asset.warranty_expiry <= thirty_days,
        Asset.warranty_expiry >= datetime.utcnow().date()
    ).all()
    
    # Lifecycle statistics
    all_assets = Asset.query.all()
    lifecycle_stats = {}
    for asset in all_assets:
        if asset.purchase_date and asset.expected_life_years:
            status = asset.get_lifecycle_status()
            lifecycle_stats[status] = lifecycle_stats.get(status, 0) + 1
    
    # Assets needing replacement within 6 months
    replacement_needed = [asset for asset in all_assets if asset.needs_replacement()]
    
    # Calculate total value and average age
    total_value = sum(asset.purchase_cost for asset in all_assets if asset.purchase_cost)
    assets_with_age = [asset for asset in all_assets if asset.purchase_date]
    avg_age = sum(asset.get_age_years() for asset in assets_with_age) / len(assets_with_age) if assets_with_age else 0
    
    # License statistics for reports
    license_vendor_stats = db.session.query(
        License.vendor,
        db.func.count(License.id).label('count'),
        db.func.sum(License.annual_cost).label('total_annual_cost')
    ).filter(License.vendor.isnot(None)).group_by(License.vendor).all()
    
    license_type_stats = db.session.query(
        License.license_type,
        db.func.count(License.id).label('count')
    ).filter(License.license_type.isnot(None)).group_by(License.license_type).all()
    
    # License utilization by software
    all_licenses = License.query.filter_by(status='Active').all()
    license_utilization = []
    for lic in all_licenses:
        assigned = LicenseAssignment.query.filter_by(license_id=lic.id, status='Active').count()
        utilization_pct = (assigned / lic.total_licenses * 100) if lic.total_licenses > 0 else 0
        license_utilization.append({
            'software': lic.software_name,
            'vendor': lic.vendor,
            'total': lic.total_licenses,
            'assigned': assigned,
            'available': lic.total_licenses - assigned,
            'utilization': utilization_pct
        })
    
    # License expiring soon
    licenses_expiring = [lic for lic in all_licenses if lic.is_expiring_soon(30)]
    
    # Total license costs
    total_license_purchase_cost = db.session.query(db.func.sum(License.purchase_cost)).filter(
        License.purchase_cost.isnot(None)
    ).scalar() or 0
    
    total_license_annual_cost = db.session.query(db.func.sum(License.annual_cost)).filter(
        License.annual_cost.isnot(None)
    ).scalar() or 0
    
    return render_template('reports.html', 
                         category_stats=category_stats,
                         status_stats=status_stats,
                         dept_stats=dept_stats,
                         expiring=expiring,
                         lifecycle_stats=lifecycle_stats,
                         replacement_needed=replacement_needed,
                         total_value=total_value,
                         avg_age=avg_age,
                         license_vendor_stats=license_vendor_stats,
                         license_type_stats=license_type_stats,
                         license_utilization=license_utilization,
                         licenses_expiring=licenses_expiring,
                         total_license_purchase_cost=total_license_purchase_cost,
                         total_license_annual_cost=total_license_annual_cost,
                         today=datetime.utcnow().date())


@bp.route('/reports/advanced')
@login_required
def reports_advanced():
    return render_template('reports_advanced.html')


@bp.route('/api/reports/templates', methods=['GET'])
@login_required
def api_report_templates():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, description, report_type, is_builtin, created_at FROM report_templates ORDER BY is_builtin DESC, name"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/reports/runs', methods=['GET'])
@login_required
def api_report_runs_list():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, report_type, status, row_count, file_csv, file_pdf, generated_by, generated_at, completed_at FROM report_runs ORDER BY id DESC LIMIT 100"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/reports/run', methods=['POST'])
@login_required
def api_report_run():
    data    = request.get_json()
    rtype   = data.get('report_type')
    name    = data.get('name') or rtype
    config  = data.get('config', {})
    tmpl_id = data.get('template_id')
    if not rtype:
        return jsonify({'error': 'report_type required'}), 400
    db_conn = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cur = db_conn.execute(
        "INSERT INTO report_runs (template_id, name, report_type, config, status, generated_by, generated_at) VALUES (?,?,?,?,?,?,?)",
        (tmpl_id, name, rtype, json.dumps(config), 'pending', current_user.username, now)
    )
    run_id = cur.lastrowid
    db_conn.commit(); db_conn.close()

    def _bg():
        _report_engine.run_report(run_id, tmpl_id, name, rtype, config, current_user.username)
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({'ok': True, 'run_id': run_id})


@bp.route('/api/reports/runs/<int:run_id>', methods=['GET'])
@login_required
def api_report_run_status(run_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM report_runs WHERE id=?", (run_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@bp.route('/api/reports/runs/<int:run_id>/data', methods=['GET'])
@login_required
def api_report_run_data(run_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM report_runs WHERE id=?", (run_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['status'] != 'ready':
        return jsonify({'error': 'Report not ready yet', 'status': row['status']}), 202
    rtype   = row['report_type']
    config  = json.loads(row['config'] or '{}')
    fetcher = _report_engine.FETCHERS.get(rtype)
    if not fetcher:
        return jsonify({'error': 'Unknown report type'}), 400
    cols, rows = fetcher(config)
    return jsonify({'cols': cols, 'rows': rows, 'count': len(rows)})


@bp.route('/api/reports/download/<string:filename>')
@login_required
def api_report_download(filename):
    safe = os.path.basename(filename)
    path = os.path.join(_report_engine.REPORT_DIR, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    mimetype = 'text/csv' if safe.endswith('.csv') else 'application/pdf' if safe.endswith('.pdf') else 'text/html'
    return send_file(path, as_attachment=True, download_name=safe, mimetype=mimetype)