"""Intune asset sync for the assets blueprint. Split from blueprints/assets.py.
perform_intune_asset_sync is re-exported from blueprints.assets for sync_scheduler.
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


@bp.route('/assets/sync-from-intune', methods=['POST'])
@login_required
@manager_required
@license_required
def sync_assets_from_intune():
    """Sync assets from Microsoft Intune/Defender"""
    result = perform_intune_asset_sync()
    if result.get('success'):
        message_parts = []
        if result.get('synced_count', 0) > 0:
            message_parts.append(f"{result['synced_count']} new assets synced")
        if result.get('updated_count', 0) > 0:
            message_parts.append(f"{result['updated_count']} assets updated")
        if result.get('skipped_count', 0) > 0:
            message_parts.append(f"{result['skipped_count']} devices skipped")
        if message_parts:
            flash(', '.join(message_parts) + ' from Intune', 'success')
        if result.get('errors'):
            for error in result['errors'][:5]:
                flash(error, 'warning')
    else:
        flash(result.get('error') or 'Error syncing from Intune', 'danger')

    return redirect(url_for('assets.assets'))


def perform_intune_asset_sync():
    """Core Intune asset sync logic.

    Returns:
        dict: {success, synced_count, updated_count, skipped_count, errors, error}
    """
    try:
        db.session.rollback()

        from m365_service import M365Service

        from m365_config import get_m365_credentials
        tenant_id, client_id, client_secret = get_m365_credentials()

        if not all([tenant_id, client_id, client_secret]):
            return {
                'success': False,
                'error': 'M365 credentials not configured. Please configure in Settings.'
            }

        m365 = M365Service(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )

        devices = m365.get_all_devices_with_hardware()
        if not devices:
            return {
                'success': True,
                'synced_count': 0,
                'updated_count': 0,
                'skipped_count': 0,
                'errors': []
            }

        synced_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        # Preload assets/employees to avoid per-device queries
        all_assets = Asset.query.all()
        assets_by_serial = {}
        assets_by_azure_id = {}
        assets_by_name_lower = {}
        existing_asset_tags = set()
        _ZERO_GUID = '00000000-0000-0000-0000-000000000000'
        for existing_asset in all_assets:
            if existing_asset.asset_tag:
                existing_asset_tags.add(existing_asset.asset_tag)
            if existing_asset.serial_number:
                assets_by_serial[existing_asset.serial_number] = existing_asset
            _aad = (existing_asset.azure_ad_device_id or '').strip().lower()
            if _aad and _aad != _ZERO_GUID:
                assets_by_azure_id.setdefault(_aad, existing_asset)
            if existing_asset.name:
                assets_by_name_lower.setdefault(existing_asset.name.strip().lower(), existing_asset)

        employees_by_email_lower = {
            (emp.email or '').strip().lower(): emp
            for emp in Employee.query.all()
            if emp.email
        }

        def parse_graph_datetime(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except Exception:
                return None

        def normalize_serial(value):
            if not value:
                return None
            value = str(value).strip()
            if not value:
                return None
            if value.lower() in ['unknown', 'n/a', 'none']:
                return None
            return value

        def normalize_azure_id(value):
            v = (value or '').strip().lower()
            if not v or v == _ZERO_GUID:
                return None
            return v

        def build_unique_asset_tag(base):
            if not base:
                base = f"TEMP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            base_tag = base.upper().replace(' ', '').replace('/', '-').replace('_', '-')
            candidate = base_tag
            counter = 1
            while candidate in existing_asset_tags:
                candidate = f"{base_tag}{counter}"
                counter += 1
            existing_asset_tags.add(candidate)
            return candidate

        for device in devices:
            try:
                device_name = device.get('deviceName')
                if not device_name:
                    skipped_count += 1
                    continue

                device_name_norm = str(device_name).strip()
                serial_number = normalize_serial(device.get('serialNumber'))
                azure_id = normalize_azure_id(device.get('azureADDeviceId'))

                asset = None
                if serial_number and serial_number in assets_by_serial:
                    asset = assets_by_serial[serial_number]
                # Match on the stable Azure AD device id before name/create. A device
                # that re-enrolls in Intune gets a NEW intune_device_id (and Intune may
                # return several records) but keeps the SAME azureADDeviceId — and
                # serial-less devices never matched at all. Without this, every sync
                # created duplicate assets (e.g. gao.yang_Windows_N piling up daily).
                if not asset and azure_id and azure_id in assets_by_azure_id:
                    asset = assets_by_azure_id[azure_id]
                if not asset:
                    asset = assets_by_name_lower.get(device_name_norm.lower())

                upn = (device.get('userPrincipalName') or '').strip().lower()
                employee = employees_by_email_lower.get(upn) if upn else None

                os_type = (device.get('operatingSystem') or '').lower()
                if 'windows' in os_type:
                    category = 'Laptop' if 'laptop' in device_name_norm.lower() else 'Desktop'
                elif 'mac' in os_type or 'ios' in os_type:
                    category = 'Laptop' if 'mac' in os_type else 'Mobile Device'
                else:
                    category = 'Other'

                compliance = device.get('complianceState', 'unknown')
                status = 'In Use' if (compliance == 'compliant' and employee) else ('Available' if compliance == 'compliant' else 'Needs Attention')

                os_name = device.get('operatingSystem', '')
                os_ver = device.get('osVersion', '')

                enrollment_dt = parse_graph_datetime(device.get('enrolledDateTime'))
                last_sync_dt = parse_graph_datetime(device.get('lastSyncDateTime'))

                hw_info = device.get('hardwareInformation', {}) or {}
                cpu_arch = device.get('processorArchitecture') or hw_info.get('processorArchitecture')

                ram_bytes = device.get('physicalMemoryInBytes') or 0
                ram_gb = round(ram_bytes / (1024**3), 2) if ram_bytes and ram_bytes > 0 else None

                total_storage = device.get('totalStorageSpaceInBytes') or hw_info.get('totalStorageSpace') or 0
                free_storage = device.get('freeStorageSpaceInBytes') or hw_info.get('freeStorageSpace') or 0
                total_storage_gb = round(total_storage / (1024**3), 2) if total_storage and total_storage > 0 else None
                free_storage_gb = round(free_storage / (1024**3), 2) if free_storage and free_storage > 0 else None

                bios_ver = hw_info.get('systemManagementBIOSVersion')
                tpm_ver = hw_info.get('tpmVersion') or device.get('tpmVersion')
                wifi_mac = hw_info.get('wifiMac') or device.get('wiFiMacAddress')
                eth_mac = device.get('ethernetMacAddress')

                if asset:
                    asset.name = device_name_norm or asset.name
                    asset.manufacturer = device.get('manufacturer') or asset.manufacturer
                    asset.model = device.get('model') or asset.model
                    if os_name:
                        asset.os_version = f"{os_name} {os_ver}".strip()
                    asset.intune_os_version = os_ver

                    asset.intune_device_id = device.get('id')
                    asset.intune_compliance_state = device.get('complianceState', 'unknown')
                    asset.intune_management_state = device.get('managementState', 'unknown')
                    if enrollment_dt:
                        asset.intune_enrolled_date = enrollment_dt
                    if last_sync_dt:
                        asset.intune_last_sync = last_sync_dt
                        asset.last_seen = last_sync_dt

                    asset.online_state = device.get('complianceState', 'unknown')
                    asset.hardware_cpu = cpu_arch
                    if ram_gb is not None:
                        asset.hardware_ram_gb = ram_gb
                    if total_storage_gb is not None:
                        asset.hardware_storage_total_gb = total_storage_gb
                    if free_storage_gb is not None:
                        asset.hardware_storage_free_gb = free_storage_gb
                    asset.hardware_bios_version = bios_ver
                    asset.hardware_tpm_version = tpm_ver
                    asset.hardware_mac_wifi = wifi_mac
                    asset.hardware_mac_ethernet = eth_mac
                    asset.azure_ad_device_id = device.get('azureADDeviceId')
                    if azure_id:
                        assets_by_azure_id.setdefault(azure_id, asset)

                    if employee:
                        if not asset.employee_id:
                            asset.employee_id = employee.id
                            asset.status = 'In Use'
                        elif asset.employee_id != employee.id:
                            asset.employee_id = employee.id

                    updated_count += 1
                else:
                    if serial_number and len(serial_number) >= 10:
                        tag_base = serial_number[:10]
                    else:
                        tag_base = device_name_norm[:10] if len(device_name_norm) >= 10 else device_name_norm
                    asset_tag = build_unique_asset_tag(tag_base)

                    enrollment_date = enrollment_dt.date() if enrollment_dt else None
                    os_full = f"{os_name} {os_ver}".strip() if os_name else None

                    new_asset = Asset(
                        asset_tag=asset_tag,
                        name=device_name_norm,
                        category=category,
                        manufacturer=device.get('manufacturer'),
                        model=device.get('model'),
                        serial_number=serial_number,
                        status=status,
                        os_version=os_full,
                        intune_os_version=os_ver,
                        online_state=compliance,
                        employee_id=employee.id if employee else None,
                        purchase_date=enrollment_date,
                        intune_device_id=device.get('id'),
                        intune_enrolled_date=enrollment_dt,
                        intune_last_sync=last_sync_dt,
                        intune_compliance_state=device.get('complianceState', 'unknown'),
                        intune_management_state=device.get('managementState', 'unknown'),
                        hardware_cpu=cpu_arch,
                        hardware_ram_gb=ram_gb,
                        hardware_storage_total_gb=total_storage_gb,
                        hardware_storage_free_gb=free_storage_gb,
                        hardware_bios_version=bios_ver,
                        hardware_mac_wifi=wifi_mac,
                        hardware_mac_ethernet=eth_mac,
                        hardware_tpm_version=tpm_ver,
                        azure_ad_device_id=device.get('azureADDeviceId'),
                        last_seen=last_sync_dt,
                        notes=f"Synced from Microsoft Intune on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                    )
                    db.session.add(new_asset)
                    if serial_number:
                        assets_by_serial[serial_number] = new_asset
                    if azure_id:
                        assets_by_azure_id.setdefault(azure_id, new_asset)
                    assets_by_name_lower.setdefault(device_name_norm.lower(), new_asset)
                    synced_count += 1

            except Exception as e:
                errors.append(f"Error syncing {device.get('deviceName', 'Unknown')}: {str(e)}")
                continue

        db.session.commit()

        return {
            'success': True,
            'synced_count': synced_count,
            'updated_count': updated_count,
            'skipped_count': skipped_count,
            'errors': errors
        }
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {
            'success': False,
            'error': str(e)
        }


