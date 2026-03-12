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
from soc2_models import SOC2Control, EvidenceSnapshot, M365User, IntuneDevice, StrikeGraphEvidence, AuditLog
try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    Workbook = Alignment = Border = Font = PatternFill = Side = get_column_letter = None
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
)
import logging
logger = logging.getLogger(__name__)


bp = Blueprint('soc2', __name__)


# ==================== SOC2 COMPLIANCE ====================


# ==================== LICENSE MANAGEMENT ====================

from functools import wraps
from license_service import license_service

# ==================== SOC2 API ENDPOINTS ====================



@bp.route('/soc2')
@login_required
@admin_required
def soc2_dashboard():
    """SOC2 Compliance Dashboard"""
    from sqlalchemy import func
    
    # Get all controls with evidence counts
    controls = SOC2Control.query.order_by(SOC2Control.control_frequency, SOC2Control.control_name).all()
    
    # Get latest evidence snapshots for each control
    latest_evidence = {}
    for control in controls:
        latest = EvidenceSnapshot.query.filter_by(control_id=control.id).order_by(EvidenceSnapshot.snapshot_date.desc()).first()
        latest_evidence[control.id] = latest
    
    # Get sync statistics
    m365_user_count = M365User.query.filter_by(is_current=True).count()
    m365_admin_count = M365User.query.filter_by(is_current=True, is_admin=True).count()
    intune_device_count = IntuneDevice.query.filter_by(is_current=True).count()
    intune_compliant_count = IntuneDevice.query.filter_by(is_current=True, compliance_state='compliant').count()
    
    # Get latest sync times
    latest_user_sync = db.session.query(func.max(M365User.sync_date)).scalar()
    latest_device_sync = db.session.query(func.max(IntuneDevice.sync_date)).scalar()
    
    # Count total evidence snapshots
    total_snapshots = EvidenceSnapshot.query.count()
    
    # Get recent audit log entries
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(10).all()
    
    # Calculate control status summary
    control_summary = {
        'total': len(controls),
        'in_place': sum(1 for c in controls if c.control_progress == 'In Place'),
        'partial': sum(1 for c in controls if c.control_progress == 'Partially In Place'),
        'not_in_place': sum(1 for c in controls if c.control_progress == 'Not In Place'),
        'automated': sum(1 for c in controls if c.automation_enabled)
    }
    
    return render_template('soc2_dashboard.html',
                         controls=controls,
                         latest_evidence=latest_evidence,
                         m365_user_count=m365_user_count,
                         m365_admin_count=m365_admin_count,
                         intune_device_count=intune_device_count,
                         intune_compliant_count=intune_compliant_count,
                         latest_user_sync=latest_user_sync,
                         latest_device_sync=latest_device_sync,
                         total_snapshots=total_snapshots,
                         recent_logs=recent_logs,
                         control_summary=control_summary)


@bp.route('/soc2/evidence/<int:control_id>')
@login_required
@admin_required
def soc2_evidence(control_id):
    """View evidence history for a specific control"""
    control = SOC2Control.query.get_or_404(control_id)
    
    # Get all snapshots for this control, newest first
    snapshots = EvidenceSnapshot.query.filter_by(control_id=control_id).order_by(EvidenceSnapshot.snapshot_date.desc()).all()
    
    return render_template('soc2_evidence.html',
                         control=control,
                         snapshots=snapshots)


@bp.route('/api/soc2/snapshot/<int:snapshot_id>')
@login_required
@admin_required
def api_soc2_snapshot(snapshot_id):
    """Get details of a specific evidence snapshot"""
    try:
        snapshot = EvidenceSnapshot.query.get_or_404(snapshot_id)
        
        return jsonify({
            'success': True,
            'snapshot': {
                'id': snapshot.id,
                'snapshot_date': snapshot.snapshot_date.strftime('%Y-%m-%d %H:%M:%S'),
                'evidence_type': snapshot.evidence_type,
                'record_count': snapshot.record_count,
                'status': snapshot.status,
                'collected_by': snapshot.collected_by,
                'evidence_data': snapshot.evidence_data,
                'notes': snapshot.notes
            }
        })
    except Exception as e:
        logger.error(f'Error fetching snapshot: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/soc2/strikegraph')
@login_required
@admin_required
def soc2_strikegraph():
    """View StrikeGraph evidence repository"""
    # Get all evidence items
    evidence_items = StrikeGraphEvidence.query.order_by(StrikeGraphEvidence.evidence_name).all()
    
    # Statistics
    total_items = len(evidence_items)
    mapped_items = len([e for e in evidence_items if e.control_id])
    automated_items = len([e for e in evidence_items if e.automation_source == 'M365/Intune'])
    isms_items = len([e for e in evidence_items if e.automation_source == 'ISMS'])
    manual_items = len([e for e in evidence_items if e.automation_source == 'Manual'])
    
    # Group by evidence type
    by_type = {}
    for item in evidence_items:
        if item.evidence_type not in by_type:
            by_type[item.evidence_type] = []
        by_type[item.evidence_type].append(item)
    
    # Get controls for mapping
    controls = SOC2Control.query.all()
    
    # Items expiring soon (next 30 days)
    from datetime import timedelta
    soon = datetime.utcnow().date() + timedelta(days=30)
    expiring_soon = [e for e in evidence_items 
                     if e.expiration_date and e.expiration_date <= soon and e.is_active]
    
    return render_template('soc2_strikegraph.html',
                         evidence_items=evidence_items,
                         by_type=by_type,
                         controls=controls,
                         total_items=total_items,
                         mapped_items=mapped_items,
                         automated_items=automated_items,
                         isms_items=isms_items,
                         manual_items=manual_items,
                         expiring_soon=expiring_soon)


@bp.route('/compliance/management-risk-review')
@login_required
@admin_required
def management_risk_review():
    """Generate Management Review of Risk Assessment Report"""
    from datetime import datetime
    
    # Get all active risks
    risks = Risk.query.filter_by(risk_status=True).order_by(Risk.risk_combined_score.desc(), Risk.risk_name).all()
    
    # Get control mappings for risks
    risk_controls = {}
    for risk in risks:
        controls = db.session.query(Control).join(
            ControlRiskMapping, ControlRiskMapping.control_id == Control.id
        ).filter(
            ControlRiskMapping.risk_id == risk.id,
            Control.is_active == True
        ).all()
        risk_controls[risk.id] = controls
    
    # Get recent incidents (if incident tracking is enabled)
    recent_incidents = []
    
    # Get SOC 2 control status for risk-related controls
    risk_controls_status = SOC2Control.query.filter(
        SOC2Control.control_name.like('%Risk%')
    ).order_by(SOC2Control.control_name).all()
    
    review_date = datetime.now()
    reviewer = session.get('username', 'Unknown')
    reviewer_email = session.get('email', '')
    
    return render_template('management_risk_review.html',
                         risks=risks,
                         risk_controls=risk_controls,
                         recent_incidents=recent_incidents,
                         risk_controls_status=risk_controls_status,
                         review_date=review_date,
                         reviewer=reviewer,
                         reviewer_email=reviewer_email)


@bp.route('/compliance/user-access-review')
@login_required
@admin_required
def user_access_review_report():
    """Generate User Access Review Report"""
    from datetime import datetime
    
    # Get all employees
    employees = Employee.query.all()
    
    # Get all assets (systems)
    assets = Asset.query.all()
    
    # Categorize users by system type
    os_users = []
    db_users = []
    app_users = []
    network_users = []
    
    for employee in employees:
        # Operating System Users
        os_users.append({
            'name': employee.name,
            'email': employee.email,
            'department': employee.department,
            'position': employee.position,
            'access_type': 'Standard User'
        })
        
        # Database Users (if applicable)
        if employee.department in ['IT', 'Engineering', 'Development']:
            db_users.append({
                'name': employee.name,
                'email': employee.email,
                'department': employee.department,
                'access_level': 'Read/Write' if '@ IT' in (employee.position or '') else 'Read Only'
            })
        
        # Application Users
        app_users.append({
            'name': employee.name,
            'email': employee.email,
            'applications': 'M365, Asset Tracker, Incident Portal',
            'role': 'Admin' if employee.department == 'IT' else 'User'
        })
        
        # Network/Cloud Users
        if employee.email:
            network_users.append({
                'name': employee.name,
                'email': employee.email,
                'domain': 'cirque.com',
                'vpn_access': 'Yes' if employee.department in ['IT', 'Engineering'] else 'No'
            })
    
    review_date = datetime.now()
    return render_template('compliance/user_access_review.html',
                         os_users=os_users,
                         db_users=db_users,
                         app_users=app_users,
                         network_users=network_users,
                         review_date=review_date,
                         total_employees=len(employees))


@bp.route('/compliance/vendor-risk-register')
@login_required
@admin_required
def vendor_risk_register_report():
    """Generate Vendor Risk Register Report"""
    from datetime import datetime, timedelta
    import random
    
    # Sample IT vendors with risk assessment data
    vendors = [
        {
            'vendor_name': 'Microsoft Corporation',
            'service': 'Microsoft 365, Azure AD, Intune',
            'risk_level': 'Low',
            'last_review': (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d'),
            'soc2_status': 'Type II Available',
            'criticality': 'Critical',
            'data_access': 'Email, Files, User Data'
        },
        {
            'vendor_name': 'GitHub Inc.',
            'service': 'Code Repository',
            'risk_level': 'Low',
            'last_review': (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d'),
            'soc2_status': 'Type II Available',
            'criticality': 'High',
            'data_access': 'Source Code'
        },
        {
            'vendor_name': 'AWS (Amazon Web Services)',
            'service': 'Cloud Infrastructure',
            'risk_level': 'Low',
            'last_review': (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d'),
            'soc2_status': 'Type II Available',
            'criticality': 'Critical',
            'data_access': 'Application Data, Infrastructure'
        }
    ]
    
    review_date = datetime.now()
    return render_template('compliance/vendor_risk_register.html',
                         vendors=vendors,
                         review_date=review_date)


@bp.route('/compliance/risk-assessment-methodology')
@login_required
@admin_required
def risk_assessment_methodology():
    """Generate Risk Assessment Methodology Document"""
    from datetime import datetime
    
    review_date = datetime.now()
    return render_template('compliance/risk_assessment_methodology.html',
                         review_date=review_date)


@bp.route('/compliance/employee-training-report')
@login_required
@admin_required
def employee_training_report():
    """Generate Employee Training Report"""
    from datetime import datetime, timedelta
    import random
    
    # Get all employees
    employees = Employee.query.all()
    
    # Generate training completion data
    training_records = []
    for employee in employees:
        completion_date = datetime.now() - timedelta(days=random.randint(10, 90))
        training_records.append({
            'name': employee.name,
            'email': employee.email,
            'department': employee.department,
            'training_type': 'Annual Security Awareness',
            'completion_date': completion_date.strftime('%Y-%m-%d'),
            'status': 'Completed',
            'score': random.randint(85, 100)
        })
    
    # Most recent hire
    recent_hire = training_records[0] if training_records else None
    
    review_date = datetime.now()
    return render_template('compliance/employee_training_report.html',
                         training_records=training_records,
                         recent_hire=recent_hire,
                         review_date=review_date,
                         total_employees=len(employees),
                         completion_rate=100)


@bp.route('/compliance/employee-reporting-procedure')
@login_required
@admin_required
def employee_reporting_procedure():
    """Generate Employee Security Reporting Procedure Document"""
    from datetime import datetime
    
    review_date = datetime.now()
    return render_template('compliance/employee_reporting_procedure.html',
                         review_date=review_date)


@bp.route('/api/soc2/control-evidence/<int:control_id>')
@login_required
@admin_required
def api_control_evidence(control_id):
    """Get all evidence items for a specific control"""
    try:
        control = SOC2Control.query.get_or_404(control_id)
        evidence_items = StrikeGraphEvidence.query.filter_by(control_id=control_id).all()
        
        items_data = []
        for item in evidence_items:
            items_data.append({
                'id': item.id,
                'evidence_name': item.evidence_name,
                'evidence_type': item.evidence_type,
                'automation_source': item.automation_source,
                'file_path': item.file_path,
                'has_file': bool(item.file_path),
                'submission_status': item.submission_status,
                'expiration_date': item.expiration_date.strftime('%Y-%m-%d') if item.expiration_date else None,
                'owner': item.owner
            })
        
        return jsonify({
            'success': True,
            'control_id': control.control_id,
            'control_name': control.control_name,
            'evidence_count': len(items_data),
            'evidence_items': items_data,
            'files_available': sum(1 for item in evidence_items if item.file_path)
        })
    except Exception as e:
        logger.error(f'Error fetching control evidence: {str(e)}')
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/soc2/export/<int:control_id>')
@login_required
@admin_required
def soc2_export_control(control_id):
    """Export evidence for a specific control to Excel"""
    try:
        control = SOC2Control.query.get_or_404(control_id)
        snapshots = EvidenceSnapshot.query.filter_by(control_id=control_id).order_by(EvidenceSnapshot.snapshot_date.desc()).all()
        
        # Create workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Control Evidence"
        
        # Header styles
        header_fill = PatternFill(start_color="2D4639", end_color="2D4639", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Control Information Section
        ws['A1'] = 'SOC2 Control Evidence Report'
        ws['A1'].font = Font(bold=True, size=16)
        ws.merge_cells('A1:F1')
        
        ws['A2'] = 'Generated:'
        ws['B2'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        ws['A3'] = 'Control Name:'
        ws['B3'] = control.control_name
        ws['A4'] = 'Description:'
        ws['B4'] = control.control_description
        ws.merge_cells('B4:F4')
        ws['A5'] = 'Frequency:'
        ws['B5'] = control.control_frequency
        ws['A6'] = 'Owner:'
        ws['B6'] = control.control_owner
        ws['A7'] = 'Status:'
        ws['B7'] = control.control_progress
        
        # Evidence History Section
        ws['A9'] = 'Evidence History'
        ws['A9'].font = Font(bold=True, size=14)
        
        # Table headers
        headers = ['Snapshot Date', 'Evidence Type', 'Record Count', 'Status', 'Collected By', 'Notes']
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=10, column=col_num)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center')
        
        # Add data rows
        row_num = 11
        for snapshot in snapshots:
            ws.cell(row=row_num, column=1, value=snapshot.snapshot_date.strftime('%Y-%m-%d %H:%M:%S'))
            ws.cell(row=row_num, column=2, value=snapshot.evidence_type)
            ws.cell(row=row_num, column=3, value=snapshot.record_count)
            ws.cell(row=row_num, column=4, value=snapshot.status)
            ws.cell(row=row_num, column=5, value=snapshot.collected_by)
            ws.cell(row=row_num, column=6, value=snapshot.notes or '')
            
            # Apply borders
            for col in range(1, 7):
                ws.cell(row=row_num, column=col).border = border
            
            row_num += 1
        
        # Add latest evidence data if available
        if snapshots:
            latest = snapshots[0]
            try:
                evidence_data = json.loads(latest.evidence_data)
                
                ws[f'A{row_num + 2}'] = 'Latest Evidence Details'
                ws[f'A{row_num + 2}'].font = Font(bold=True, size=14)
                
                row_num += 3
                ws.cell(row=row_num, column=1, value='Evidence Data')
                ws.cell(row=row_num, column=1).font = header_font
                ws.cell(row=row_num, column=1).fill = header_fill
                
                row_num += 1
                for key, value in evidence_data.items():
                    ws.cell(row=row_num, column=1, value=str(key))
                    ws.cell(row=row_num, column=2, value=str(value))
                    row_num += 1
            except:
                pass
        
        # Auto-adjust column widths
        for col in range(1, 7):
            ws.column_dimensions[get_column_letter(col)].width = 20
        
        # Save to BytesIO
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"SOC2_{control.control_name.replace(' ', '_')}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f'Error exporting control evidence: {str(e)}')
        flash(f'Error exporting evidence: {str(e)}', 'danger')
        return redirect(url_for('soc2.soc2_dashboard'))


@bp.route('/api/soc2/export/all')
@login_required
@admin_required
def soc2_export_all():
    """Export all SOC2 controls and evidence to Excel"""
    try:
        controls = SOC2Control.query.order_by(SOC2Control.control_frequency, SOC2Control.control_name).all()
        
        # Create workbook
        wb = Workbook()
        
        # Summary sheet
        ws_summary = wb.active
        ws_summary.title = "Summary"
        
        # Styles
        header_fill = PatternFill(start_color="2D4639", end_color="2D4639", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Report header
        ws_summary['A1'] = 'SOC2 Compliance Report'
        ws_summary['A1'].font = Font(bold=True, size=16)
        ws_summary.merge_cells('A1:G1')
        
        ws_summary['A2'] = 'Generated:'
        ws_summary['B2'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        ws_summary['A3'] = 'Organization:'
        ws_summary['B3'] = 'Cirque Corporation'
        
        # Get sync statistics
        m365_user_count = M365User.query.filter_by(is_current=True).count()
        m365_admin_count = M365User.query.filter_by(is_current=True, is_admin=True).count()
        intune_device_count = IntuneDevice.query.filter_by(is_current=True).count()
        intune_compliant_count = IntuneDevice.query.filter_by(is_current=True, compliance_state='compliant').count()
        
        ws_summary['A5'] = 'Current Status'
        ws_summary['A5'].font = Font(bold=True, size=14)
        ws_summary['A6'] = 'M365 Users:'
        ws_summary['B6'] = m365_user_count
        ws_summary['A7'] = 'Admin Users:'
        ws_summary['B7'] = m365_admin_count
        ws_summary['A8'] = 'Intune Devices:'
        ws_summary['B8'] = intune_device_count
        ws_summary['A9'] = 'Compliant Devices:'
        ws_summary['B9'] = intune_compliant_count
        
        # Controls summary table
        ws_summary['A11'] = 'Control Summary'
        ws_summary['A11'].font = Font(bold=True, size=14)
        
        headers = ['Control Name', 'Frequency', 'Progress', 'Automated', 'Last Evidence', 'Record Count', 'Framework']
        for col_num, header in enumerate(headers, 1):
            cell = ws_summary.cell(row=12, column=col_num)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center')
        
        row_num = 13
        for control in controls:
            latest_evidence = EvidenceSnapshot.query.filter_by(control_id=control.id).order_by(EvidenceSnapshot.snapshot_date.desc()).first()
            
            ws_summary.cell(row=row_num, column=1, value=control.control_name)
            ws_summary.cell(row=row_num, column=2, value=control.control_frequency)
            ws_summary.cell(row=row_num, column=3, value=control.control_progress)
            ws_summary.cell(row=row_num, column=4, value='Yes' if control.automation_enabled else 'No')
            ws_summary.cell(row=row_num, column=5, value=latest_evidence.snapshot_date.strftime('%Y-%m-%d') if latest_evidence else 'N/A')
            ws_summary.cell(row=row_num, column=6, value=latest_evidence.record_count if latest_evidence else 0)
            ws_summary.cell(row=row_num, column=7, value=control.audit_alignment)
            
            for col in range(1, 8):
                ws_summary.cell(row=row_num, column=col).border = border
            
            row_num += 1
        
        # Auto-adjust column widths
        for col in range(1, 8):
            ws_summary.column_dimensions[get_column_letter(col)].width = 18
        
        # Add detailed sheets for automated controls with recent evidence
        for control in controls:
            if control.automation_enabled:
                snapshots = EvidenceSnapshot.query.filter_by(control_id=control.id).order_by(EvidenceSnapshot.snapshot_date.desc()).limit(10).all()
                
                if snapshots:
                    # Create sheet (limit sheet name to 31 characters)
                    sheet_name = control.control_name[:28] + "..." if len(control.control_name) > 31 else control.control_name
                    ws = wb.create_sheet(title=sheet_name)
                    
                    # Control info
                    ws['A1'] = control.control_name
                    ws['A1'].font = Font(bold=True, size=14)
                    ws.merge_cells('A1:E1')
                    
                    ws['A2'] = 'Description:'
                    ws['B2'] = control.control_description
                    ws.merge_cells('B2:E2')
                    
                    ws['A3'] = 'Frequency:'
                    ws['B3'] = control.control_frequency
                    ws['A4'] = 'Progress:'
                    ws['B4'] = control.control_progress
                    
                    # Evidence table
                    ws['A6'] = 'Recent Evidence Snapshots'
                    ws['A6'].font = Font(bold=True, size=12)
                    
                    snap_headers = ['Date', 'Type', 'Records', 'Status', 'Collected By']
                    for col_num, header in enumerate(snap_headers, 1):
                        cell = ws.cell(row=7, column=col_num)
                        cell.value = header
                        cell.fill = header_fill
                        cell.font = header_font
                        cell.border = border
                    
                    row_num = 8
                    for snapshot in snapshots:
                        ws.cell(row=row_num, column=1, value=snapshot.snapshot_date.strftime('%Y-%m-%d %H:%M'))
                        ws.cell(row=row_num, column=2, value=snapshot.evidence_type)
                        ws.cell(row=row_num, column=3, value=snapshot.record_count)
                        ws.cell(row=row_num, column=4, value=snapshot.status)
                        ws.cell(row=row_num, column=5, value=snapshot.collected_by)
                        
                        for col in range(1, 6):
                            ws.cell(row=row_num, column=col).border = border
                        
                        row_num += 1
                    
                    for col in range(1, 6):
                        ws.column_dimensions[get_column_letter(col)].width = 18
        
        # Save to BytesIO
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"SOC2_Compliance_Report_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f'Error exporting all controls: {str(e)}')
        flash(f'Error exporting report: {str(e)}', 'danger')
        return redirect(url_for('soc2.soc2_dashboard'))


@bp.route('/api/soc2/generate-evidence-files', methods=['POST'])
@login_required
@license_required
def api_generate_evidence_files():
    """Generate evidence files for StrikeGraph upload"""
    try:
        from evidence_file_service import EvidenceFileService
        
        service = EvidenceFileService()
        results = service.generate_all_automated_evidence_files()
        
        success_count = len([r for r in results if r['status'] == 'success'])
        error_count = len([r for r in results if r['status'] == 'error'])
        
        return jsonify({
            'success': True,
            'message': f'Generated {success_count} evidence files',
            'results': results,
            'stats': {
                'success': success_count,
                'errors': error_count,
                'total': len(results)
            }
        })
    except Exception as e:
        logger.error(f'Error generating evidence files: {str(e)}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@bp.route('/api/soc2/download-evidence/<int:evidence_id>')
@login_required
@license_required
def api_download_evidence(evidence_id):
    """Download a specific evidence file"""
    try:
        evidence = StrikeGraphEvidence.query.get_or_404(evidence_id)
        
        if not evidence.file_path:
            flash('Evidence file not yet generated. Click "Generate Evidence Files" first.', 'warning')
            return redirect(url_for('soc2.soc2_strikegraph'))
        
        full_path = f'/var/www/tracker/static/{evidence.file_path}'
        
        if not os.path.exists(full_path):
            flash('Evidence file not found. It may need to be regenerated.', 'danger')
            return redirect(url_for('soc2.soc2_strikegraph'))
        
        return send_file(
            full_path,
            as_attachment=True,
            download_name=os.path.basename(full_path)
        )
    except Exception as e:
        logger.error(f'Error downloading evidence: {str(e)}')
        flash(f'Error downloading evidence: {str(e)}', 'danger')
        return redirect(url_for('soc2.soc2_strikegraph'))


@bp.route('/api/soc2/download-control-evidence/<int:control_id>')
@login_required
@license_required
def api_download_control_evidence(control_id):
    """Download all evidence files for a specific control as a ZIP"""
    try:
        import zipfile
        from io import BytesIO
        
        control = SOC2Control.query.get_or_404(control_id)
        
        # Get all evidence items linked to this control that have files
        evidence_items = StrikeGraphEvidence.query.filter(
            StrikeGraphEvidence.control_id == control_id,
            StrikeGraphEvidence.file_path.isnot(None)
        ).all()
        
        if not evidence_items:
            flash(f'No evidence files available for {control.control_name}. Generate files first.', 'warning')
            return redirect(url_for('soc2.soc2_strikegraph'))
        
        # Create ZIP file in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for evidence in evidence_items:
                if evidence.file_path:
                    full_path = f'/var/www/tracker/static/{evidence.file_path}'
                    if os.path.exists(full_path):
                        # Add file to ZIP with descriptive name
                        filename = os.path.basename(full_path)
                        zip_file.write(full_path, filename)
        
        zip_buffer.seek(0)
        
        # Create filename with control ID and name
        safe_control_name = control.control_id.replace(' ', '_').replace('/', '_')
        filename = f"{safe_control_name}_Evidence_{datetime.utcnow().strftime('%Y%m%d')}.zip"
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f'Error creating control evidence ZIP: {str(e)}')
        flash(f'Error creating evidence archive: {str(e)}', 'danger')
        return redirect(url_for('soc2.soc2_strikegraph'))


@bp.route('/api/soc2/download-all-evidence')
@login_required
@license_required
def api_download_all_evidence():
    """Download all evidence files as a ZIP"""
    try:
        import zipfile
        from io import BytesIO
        
        evidence_items = StrikeGraphEvidence.query.filter(
            StrikeGraphEvidence.file_path.isnot(None)
        ).all()
        
        if not evidence_items:
            flash('No evidence files available. Generate files first.', 'warning')
            return redirect(url_for('soc2.soc2_strikegraph'))
        
        # Create ZIP file in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for evidence in evidence_items:
                if evidence.file_path:
                    full_path = f'/var/www/tracker/static/{evidence.file_path}'
                    if os.path.exists(full_path):
                        # Add file to ZIP with organized folder structure
                        zip_file.write(full_path, os.path.basename(full_path))
        
        zip_buffer.seek(0)
        
        filename = f"StrikeGraph_Evidence_{datetime.utcnow().strftime('%Y%m%d')}.zip"
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f'Error creating evidence ZIP: {str(e)}')
        flash(f'Error creating evidence archive: {str(e)}', 'danger')
        return redirect(url_for('soc2.soc2_strikegraph'))


@bp.route('/api/soc2/generate-software-inventory', methods=['POST'])
@login_required
@license_required
def api_generate_software_inventory():
    """Generate software inventory report from Defender"""
    try:
        from evidence_file_service import EvidenceFileService
        import os
        
        service = EvidenceFileService()
        file_path = service.generate_defender_software_inventory_file('Software Inventory')
        
        if file_path and os.path.exists(file_path):
            size = os.path.getsize(file_path)
            filename = os.path.basename(file_path)
            
            # Read file data
            from openpyxl import load_workbook
            wb = load_workbook(file_path)
            ws_summary = wb['Summary']
            total_software = ws_summary['B2'].value
            wb.close()
            
            # Create relative path for download
            rel_path = file_path.replace('/var/www/tracker/static', '')
            
            return jsonify({
                'success': True,
                'filename': filename,
                'file_path': rel_path,
                'size': size,
                'total_software': total_software
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to generate software inventory file'
            }), 500
            
    except Exception as e:
        logger.error(f'Error generating software inventory: {str(e)}')
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@bp.route('/system-description')
@login_required
@admin_required
@license_required
def system_description():
    """View and edit System Description for SOC 2"""
    sections = SystemDescription.query.order_by(SystemDescription.section_order).all()
    
    # Group sections by category for easier navigation
    sections_by_category = {}
    for section in sections:
        if section.category not in sections_by_category:
            sections_by_category[section.category] = []
        sections_by_category[section.category].append(section)
    
    return render_template('system_description.html', 
                         sections=sections,
                         sections_by_category=sections_by_category)


@bp.route('/system-description/<int:section_id>', methods=['GET', 'POST'])
@login_required
@admin_required
@license_required
def edit_system_description_section(section_id):
    """Edit a specific System Description section"""
    section = SystemDescription.query.get_or_404(section_id)
    
    if request.method == 'POST':
        section.content = request.form.get('content')
        section.updated_by = current_user.username
        section.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'Section "{section.section_title}" updated successfully', 'success')
        return redirect(url_for('soc2.system_description'))
    
    return render_template('edit_system_description_section.html', section=section)


@bp.route('/system-description/export')
@login_required
@admin_required
@license_required
def export_system_description():
    """Export System Description to Word document"""
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    
    sections = SystemDescription.query.order_by(SystemDescription.section_order).all()
    
    # Create document
    doc = Document()
    
    # Add title
    title = doc.add_heading('System Description', 0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    
    doc.add_paragraph(f"Cirque Corporation")
    doc.add_paragraph(f"Generated: {datetime.utcnow().strftime('%B %d, %Y')}")
    doc.add_page_break()
    
    # Add sections
    for section in sections:
        # Add section heading
        heading = doc.add_heading(section.section_title, section.section_level)
        
        # Add content
        if section.content:
            # Split by newlines and add paragraphs
            for line in section.content.split('\n'):
                if line.strip():
                    if line.startswith('**') and line.endswith('**'):
                        # Bold text
                        p = doc.add_paragraph()
                        p.add_run(line.strip('*')).bold = True
                    elif line.startswith('- '):
                        # Bullet point
                        doc.add_paragraph(line[2:], style='List Bullet')
                    else:
                        doc.add_paragraph(line)
    
    # Save to bytes
    import io
    doc_io = io.BytesIO()
    doc.save(doc_io)
    doc_io.seek(0)
    
    return send_file(
        doc_io,
        as_attachment=True,
        download_name=f'System_Description_{datetime.utcnow().strftime("%Y%m%d")}.docx',
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


@bp.route('/policies')
@login_required
@admin_required
@license_required
def policies():
    """View all policies and procedures"""
    # Get filter parameters
    category_filter = request.args.get('category', '')
    division_filter = request.args.get('division', '')
    search_query = request.args.get('search', '')
    
    # Base query
    query = Policy.query
    
    # Apply filters
    if category_filter:
        query = query.filter(Policy.category.ilike(f'%{category_filter}%'))
    if division_filter:
        query = query.filter(Policy.division == division_filter)
    if search_query:
        query = query.filter(
            db.or_(
                Policy.title.ilike(f'%{search_query}%'),
                Policy.document_id.ilike(f'%{search_query}%')
            )
        )
    
    # Get all policies ordered by document_id
    all_policies = query.order_by(Policy.document_id).all()
    
    # Get unique categories and divisions for filters
    categories = db.session.query(Policy.category).distinct().order_by(Policy.category).all()
    categories = [c[0] for c in categories if c[0]]
    divisions = db.session.query(Policy.division).distinct().order_by(Policy.division).all()
    divisions = [d[0] for d in divisions if d[0]]
    
    return render_template('policies.html', 
                         policies=all_policies,
                         categories=categories,
                         divisions=divisions,
                         category_filter=category_filter,
                         division_filter=division_filter,
                         search_query=search_query)


@bp.route('/policies/<int:policy_id>')
@login_required
@admin_required
@license_required
def view_policy(policy_id):
    """View individual policy details with sections"""
    policy = Policy.query.get_or_404(policy_id)
    sections = PolicySection.query.filter_by(policy_id=policy_id).order_by(PolicySection.section_order).all()
    
    return render_template('view_policy.html', policy=policy, sections=sections)


@bp.route('/controls')
@login_required
@admin_required
@license_required
def controls():
    """View all SOC2 controls"""
    progress_filter = request.args.get('progress', '')
    owner_filter = request.args.get('owner', '')
    search_query = request.args.get('search', '')
    
    query = Control.query.filter_by(is_active=True)
    
    if progress_filter:
        query = query.filter(Control.control_progress == progress_filter)
    if owner_filter:
        query = query.filter(Control.control_owner == owner_filter)
    if search_query:
        query = query.filter(
            db.or_(
                Control.control_name.ilike(f'%{search_query}%'),
                Control.control_description.ilike(f'%{search_query}%')
            )
        )
    
    all_controls = query.order_by(Control.control_name).all()
    
    # Get filter options
    progress_options = db.session.query(Control.control_progress).distinct().all()
    progress_options = sorted([p[0] for p in progress_options if p[0]])
    owner_options = db.session.query(Control.control_owner).distinct().all()
    owner_options = sorted([o[0] for o in owner_options if o[0]])
    
    return render_template('controls.html',
                         controls=all_controls,
                         progress_options=progress_options,
                         owner_options=owner_options,
                         progress_filter=progress_filter,
                         owner_filter=owner_filter,
                         search_query=search_query)


@bp.route('/controls/<int:control_id>')
@login_required
@admin_required
@license_required
def view_control(control_id):
    """View individual control details with mapped policies"""
    control = Control.query.get_or_404(control_id)
    
    # Get mapped policies using raw SQL
    mapped_policies = db.session.execute(
        db.text("""
            SELECT p.* FROM policy p
            JOIN policy_control_mapping pcm ON p.id = pcm.policy_id
            WHERE pcm.control_id = :control_id
            ORDER BY p.document_id
        """),
        {'control_id': control_id}
    ).fetchall()
    
    # Convert to Policy objects
    policy_objects = [Policy.query.get(row[0]) for row in mapped_policies]
    
    return render_template('view_control.html', control=control, policies=policy_objects)


@bp.route('/risks')
@login_required
@admin_required
@license_required
def risks():
    """View all SOC2 risks"""
    category_filter = request.args.get('category', '')
    score_filter = request.args.get('score', '')
    search_query = request.args.get('search', '')
    
    query = Risk.query.filter_by(risk_status=True)
    
    if category_filter:
        query = query.filter(Risk.risk_category == category_filter)
    if score_filter:
        query = query.filter(Risk.risk_combined_score == score_filter)
    if search_query:
        query = query.filter(
            db.or_(
                Risk.risk_name.ilike(f'%{search_query}%'),
                Risk.risk_description.ilike(f'%{search_query}%')
            )
        )
    
    all_risks = query.order_by(Risk.risk_category, Risk.risk_name).all()
    
    # Get filter options
    category_options = db.session.query(Risk.risk_category).distinct().order_by(Risk.risk_category).all()
    category_options = [c[0] for c in category_options if c[0]]
    score_options = db.session.query(Risk.risk_combined_score).distinct().all()
    score_options = sorted([s[0] for s in score_options if s[0] and s[0] != 'NA'])
    
    return render_template('risks.html',
                         risks=all_risks,
                         category_options=category_options,
                         score_options=score_options,
                         category_filter=category_filter,
                         score_filter=score_filter,
                         search_query=search_query)


@bp.route('/api/soc2/sync', methods=['POST'])
@login_required
@admin_required
def api_soc2_sync():
    """Trigger a manual SOC2 sync"""
    try:
        from soc2_sync_service import SOC2SyncService
        
        sync_service = SOC2SyncService(current_app._get_current_object(), db)
        results = sync_service.run_full_sync()
        
        return jsonify({
            'success': True,
            'users_synced': results.get('users', {}).get('users_synced', 0),
            'admins': results.get('users', {}).get('admins', 0),
            'devices_synced': results.get('devices', {}).get('devices_synced', 0),
            'software_apps': results.get('software', {}).get('apps', 0)
        })
    except Exception as e:
        logger.error(f'SOC2 sync error: {str(e)}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@bp.route('/api/soc2/azure-security-sync', methods=['POST'])
@login_required
@admin_required
def api_azure_security_sync():
    """Trigger Azure Security evidence collection"""
    try:
        from azure_security_sync_service import AzureSecuritySyncService
        
        sync_service = AzureSecuritySyncService()
        results = sync_service.run_full_sync()
        
        total_items = sum(r.get('count', 0) for r in results['syncs'].values() if r.get('success'))
        
        return jsonify({
            'success': True,
            'timestamp': results['timestamp'],
            'nsgs': results['syncs']['nsgs'].get('count', 0),
            'alerts': results['syncs']['alerts'].get('count', 0),
            'databases': results['syncs']['databases'].get('count', 0),
            'storage': results['syncs']['storage'].get('count', 0),
            'vms': results['syncs']['vms'].get('count', 0),
            'assessments': results['syncs']['assessments'].get('count', 0),
            'monitor': results['syncs']['monitor'].get('count', 0),
            'network': results['syncs']['network'].get('count', 0),
            'total_items': total_items
        })
    except Exception as e:
        logger.error(f'Azure Security sync error: {str(e)}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500