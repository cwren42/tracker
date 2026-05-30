"""CSV import/export routes for the assets blueprint. Split from blueprints/assets.py."""
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
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
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
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, RMM_GATEWAY_PUBLIC, RMM_TRACKER_URL,
)
logger = logging.getLogger(__name__)
from api_system import require_api_key


from blueprints.assets import bp


@bp.route('/assets/export/csv')
@login_required
@license_required
def export_assets_csv():
    """Export all assets to CSV"""
    assets = Asset.query.all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Asset Tag', 'Name', 'Category', 'Manufacturer', 'Model', 'Serial Number', 
                     'Purchase Date', 'Purchase Cost', 'Warranty Expiry', 'Status', 'Location', 
                     'Assigned To', 'Expected Life (Years)', 'Replacement Date', 'Condition', 'Notes'])
    
    # Write data
    for asset in assets:
        writer.writerow([
            asset.asset_tag,
            asset.name,
            asset.category,
            asset.manufacturer or '',
            asset.model or '',
            asset.serial_number or '',
            asset.purchase_date.strftime('%Y-%m-%d') if asset.purchase_date else '',
            asset.purchase_cost if asset.purchase_cost else '',
            asset.warranty_expiry.strftime('%Y-%m-%d') if asset.warranty_expiry else '',
            asset.status,
            asset.location or '',
            asset.assigned_employee.name if asset.assigned_employee else '',
            asset.expected_life_years if asset.expected_life_years else '',
            asset.replacement_date.strftime('%Y-%m-%d') if asset.replacement_date else '',
            asset.condition or '',
            asset.notes or ''
        ])
    
    # Prepare response
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'assets_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
    )


@bp.route('/assets/import', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def import_assets():
    """Import assets from CSV"""
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file uploaded', 'danger')
            return redirect(url_for('assets.import_assets'))
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(url_for('assets.import_assets'))
        
        if file and file.filename.endswith('.csv'):
            try:
                # Read CSV file
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_reader = csv.DictReader(stream)
                
                imported = 0
                skipped = 0
                errors = []
                
                for row_num, row in enumerate(csv_reader, start=2):
                    try:
                        # Check if row is completely empty
                        if not any(row.values()):
                            skipped += 1
                            continue
                        
                        # Generate temporary asset tag if empty
                        asset_tag = row.get('Asset Tag', '').strip()
                        if not asset_tag:
                            # Generate unique temporary tag with microseconds for uniqueness
                            timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
                            asset_tag = f"TEMP-{timestamp}-{row_num}"
                        
                        # Check if asset tag already exists
                        if Asset.query.filter_by(asset_tag=asset_tag).first():
                            errors.append(f"Row {row_num}: Asset tag '{asset_tag}' already exists")
                            skipped += 1
                            continue
                        
                        # Handle serial number - set to None if it's a placeholder or empty
                        serial = row.get('Serial Number', '').strip()
                        if serial.lower() in ['', 'to be filled by o.e.m.', 'default string', 'n/a', 'na', 'none', 'unknown', '123456789', '0', '00000000']:
                            serial = None
                        # Check if serial number already exists in database
                        elif serial and Asset.query.filter_by(serial_number=serial).first():
                            errors.append(f"Row {row_num}: Duplicate serial number '{serial}', setting to None")
                            serial = None
                        
                        # Parse dates with multiple format support
                        def parse_date(date_str):
                            if not date_str or not date_str.strip():
                                return None
                            date_str = date_str.strip()
                            # Ignore placeholder values
                            if date_str in ['0', 'N/A', 'NA', 'n/a', 'na', 'None', 'none']:
                                return None
                            # Try multiple date formats
                            for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']:
                                try:
                                    return datetime.strptime(date_str, fmt).date()
                                except ValueError:
                                    continue
                            raise ValueError(f"Unable to parse date '{date_str}'")
                        
                        asset = Asset(
                            asset_tag=row['Asset Tag'],
                            name=row['Name'],
                            category=row['Category'],
                            manufacturer=row.get('Manufacturer', ''),
                            model=row.get('Model', ''),
                            serial_number=serial,
                            purchase_date=parse_date(row.get('Purchase Date')),
                            purchase_cost=float(row['Purchase Cost']) if row.get('Purchase Cost') else None,
                            warranty_expiry=parse_date(row.get('Warranty Expiry')),
                            status=row.get('Status', 'Available'),
                            location=row.get('Location', ''),
                            expected_life_years=int(row['Expected Life (Years)']) if row.get('Expected Life (Years)') else None,
                            replacement_date=parse_date(row.get('Replacement Date')),
                            condition=row.get('Condition', ''),
                            notes=row.get('Notes', '')
                        )
                        
                        db.session.add(asset)
                        db.session.commit()  # Commit each asset immediately
                        imported += 1
                        
                    except Exception as e:
                        db.session.rollback()  # Rollback this failed row only
                        errors.append(f"Row {row_num}: {str(e)}")
                
                if imported > 0:
                    flash(f'Successfully imported {imported} assets! (Skipped {skipped} duplicates/empty rows)', 'success')
                if errors:
                    error_msg = "; ".join(errors[:10])
                    if len(errors) > 10:
                        error_msg += f"; ... and {len(errors) - 10} more errors"
                    flash(error_msg, 'warning')
                
                return redirect(url_for('assets.assets'))
                
            except Exception as e:
                flash(f'Error processing CSV: {str(e)}', 'danger')
                return redirect(url_for('assets.import_assets'))
        else:
            flash('Please upload a valid CSV file', 'danger')
            return redirect(url_for('assets.import_assets'))
    
    return render_template('import_assets.html')


