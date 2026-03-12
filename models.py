"""
models.py — All SQLAlchemy models for the Asset Tracker.

Imports db from extensions.py (no circular dependency with app.py).
"""
from datetime import datetime, timedelta, timezone
import json

from flask import has_request_context, request as _req, session
from flask_login import UserMixin, current_user
from sqlalchemy import event as _sa_event

from extensions import db, login_manager

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'csv', 'zip', 'tar', 'gz', 'mp4', 'mov', 'avi'}

def now_mst():
    """Return current datetime in MST (TZ env var is set to America/Denver)."""
    return datetime.now()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='viewer')  # admin, manager, viewer
    theme = db.Column(db.String(30), default='dark')  # Theme preference
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    azure_id = db.Column(db.String(100))  # Azure AD user object ID
    full_name = db.Column(db.String(200))  # User's display name from Azure AD
    
    def has_permission(self, permission):
        """Check if user has a specific permission"""
        permissions = {
            'admin': ['view', 'edit', 'delete', 'manage_users'],
            'manager': ['view', 'edit'],
            'viewer': ['view'],
            'eagle_eyes': ['view'],
            'base_user': ['view'],
        }
        return permission in permissions.get(self.role, [])

    @property
    def display_name(self):
        return self.full_name or self.username

class AzureIntegrationConfig(db.Model):
    __tablename__ = 'azure_integration_config'
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(100), nullable=False)
    client_id = db.Column(db.String(100), nullable=False)
    client_secret = db.Column(db.String(500), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    app_name = db.Column(db.String(30), default='tracker')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True)
    department = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    position = db.Column(db.String(100))
    photo = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    assets = db.relationship('Asset', backref='assigned_employee', lazy=True)

class Asset(db.Model):
    def restore_asset_108():
        from sqlalchemy.exc import OperationalError
        try:
            with app.app_context():
                asset = db.session.query(Asset).get(108)
                if asset:
                    asset.manufacturer = 'Dell'
                    asset.model = 'XPS 15 9520'
                    asset.serial_number = 'GJBBLR3'
                    db.session.commit()
        except OperationalError:
            pass  # DB might not be ready during migrations
    id = db.Column(db.Integer, primary_key=True)
    asset_tag = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    manufacturer = db.Column(db.String(100))
    model = db.Column(db.String(100))
    serial_number = db.Column(db.String(100), unique=True)
    purchase_date = db.Column(db.Date)
    purchase_cost = db.Column(db.Float)
    warranty_expiry = db.Column(db.Date)
    status = db.Column(db.String(20), default='Available')
    location = db.Column(db.String(100))
    notes = db.Column(db.Text)
    photo = db.Column(db.String(255))
    expected_life_years = db.Column(db.Integer, default=3)  # Expected useful life in years
    replacement_date = db.Column(db.Date)  # When asset should be replaced
    condition = db.Column(db.String(20), default='Good')  # Excellent, Good, Fair, Poor
    device_type = db.Column(db.String(50))  # Windows PC, Mac, Linux Server, Virtual Machine, etc.
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'))
    os_version = db.Column(db.String(100))  # Operating system version
    online_state = db.Column(db.String(20))  # Online, Offline, Busy
    rustdesk_id = db.Column(db.String(100))  # RustDesk device ID
    rustdesk_password = db.Column(db.String(255))  # Optional (prefer OTP / user-confirmed)
    intune_device_id = db.Column(db.String(100))  # Microsoft Intune device ID
    intune_enrolled_date = db.Column(db.DateTime)  # Date enrolled in Intune
    intune_last_sync = db.Column(db.DateTime)  # Last sync with Intune
    intune_compliance_state = db.Column(db.String(50))  # compliant, noncompliant, unknown
    intune_management_state = db.Column(db.String(50))  # managed, unmanaged
    intune_os_version = db.Column(db.String(100))  # Full OS version from Intune
    hardware_cpu = db.Column(db.String(100))  # CPU/Processor architecture
    hardware_ram_gb = db.Column(db.Float)  # Physical memory in GB
    hardware_storage_total_gb = db.Column(db.Float)  # Total storage in GB
    hardware_storage_free_gb = db.Column(db.Float)  # Free storage in GB
    hardware_bios_version = db.Column(db.String(100))  # BIOS version
    hardware_mac_wifi = db.Column(db.String(50))  # WiFi MAC address
    hardware_mac_ethernet = db.Column(db.String(50))  # Ethernet MAC address
    hardware_tpm_version = db.Column(db.String(50))  # TPM version
    azure_ad_device_id = db.Column(db.String(100))  # Azure AD device ID
    ip_address = db.Column(db.String(50))  # Primary IP address
    service_urls = db.Column(db.Text)  # Comma-separated list of service URLs
    unifi_device_id = db.Column(db.String(100))  # UniFi device UUID / MAC
    unifi_last_seen = db.Column(db.DateTime)  # Last seen from UniFi sync
    unifi_uptime_secs = db.Column(db.Integer)  # Device uptime in seconds
    last_seen = db.Column(db.DateTime)  # Last seen timestamp (RMM / TeamViewer / UniFi)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    history = db.relationship('AssetHistory', backref='asset', lazy=True, cascade='all, delete-orphan')
    
    def get_age_years(self):
        """Calculate asset age in years"""
        if self.purchase_date:
            return (datetime.utcnow().date() - self.purchase_date).days / 365.25
        return None
    
    def get_lifecycle_status(self):
        """Get lifecycle status: New, Active, Aging, Replace Soon, End of Life"""
        age = self.get_age_years()
        if not age:
            return 'Unknown'
        
        if age < 1:
            return 'New'
        elif age < self.expected_life_years * 0.6:
            return 'Active'
        elif age < self.expected_life_years * 0.85:
            return 'Aging'
        elif age < self.expected_life_years:
            return 'Replace Soon'
        else:
            return 'End of Life'

    def needs_replacement(self):
        """Return True if asset's replacement date is within 6 months or already past"""
        today = datetime.utcnow().date()
        if self.replacement_date:
            from datetime import timedelta
            return self.replacement_date <= today + timedelta(days=182)
        # Fall back to lifecycle status if no replacement_date set
        return self.get_lifecycle_status() in ('Replace Soon', 'End of Life')


class RemoteSession(db.Model):
    __tablename__ = 'remote_session'
    id = db.Column(db.Integer, primary_key=True)
    tool = db.Column(db.String(50), nullable=False)  # rustdesk, anydesk, etc.
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'), nullable=False)
    started_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ended_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reason = db.Column(db.String(500))
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ended_at = db.Column(db.DateTime)

    asset = db.relationship('Asset', foreign_keys=[asset_id])
    started_by = db.relationship('User', foreign_keys=[started_by_user_id])
    ended_by = db.relationship('User', foreign_keys=[ended_by_user_id])


class SupportTicket(db.Model):
    __tablename__ = 'support_ticket'
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default='Open')  # Open, In Progress, Closed, Merged
    priority = db.Column(db.String(20), default='Normal')  # Low, Normal, High, Urgent
    source = db.Column(db.String(20), default='web')  # web, tray, api
    category = db.Column(db.String(50), default='General')

    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)

    reporter_name = db.Column(db.String(120))
    reporter_email = db.Column(db.String(200))

    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'))
    asset_tag = db.Column(db.String(50))
    hostname = db.Column(db.String(200))

    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    closed_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    merged_into_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    asset = db.relationship('Asset', foreign_keys=[asset_id])
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])
    closed_by = db.relationship('User', foreign_keys=[closed_by_user_id])
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_user_id])
    notes = db.relationship('TicketNote', backref='ticket', lazy='dynamic', cascade='all, delete-orphan')
    activity = db.relationship('TicketActivity', backref='ticket', lazy='dynamic', cascade='all, delete-orphan')
    csat_token = db.Column(db.String(64))
    csat_score = db.Column(db.Integer)       # 1=positive, 0=negative
    csat_comment = db.Column(db.Text)

    _SLA_HOURS = {'Low': 120, 'Normal': 72, 'High': 24, 'Urgent': 4}

    @property
    def sla_target_hours(self):
        return self._SLA_HOURS.get(self.priority, 72)

    @property
    def sla_hours_remaining(self):
        if not self.created_at:
            return self.sla_target_hours
        elapsed = (datetime.utcnow() - self.created_at).total_seconds() / 3600
        return max(0.0, self.sla_target_hours - elapsed)

    @property
    def sla_breached(self):
        if self.status in ('Closed', 'Merged'):
            return False
        return self.sla_hours_remaining == 0.0


class TicketNote(db.Model):
    __tablename__ = 'ticket_note'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('User', foreign_keys=[user_id])


class TicketActivity(db.Model):
    __tablename__ = 'ticket_activity'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(50), nullable=False)
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])


class AssetHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def log_change(asset, username, action, description, old_values=None, new_values=None):
        """
        Log a change to an asset. Optionally include old/new values for auditing.
        """
        from flask_login import current_user
        user = current_user if hasattr(current_user, 'id') else None
        history = AssetHistory(
            asset_id=asset.id,
            action=action.capitalize(),
            description=description,
            user_id=user.id if user else None,
            timestamp=datetime.utcnow()
        )
        db.session.add(history)
        db.session.commit()
        # Optionally, store old/new values in a separate audit table or as JSON in description if needed
        # For now, just append to description if values are provided
        if old_values or new_values:
            details = []
            if old_values:
                details.append(f"Old: {old_values}")
            if new_values:
                details.append(f"New: {new_values}")
            if details:
                history.description += "\n" + "\n".join(details)
                db.session.commit()

class License(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    software_name = db.Column(db.String(200), nullable=False)
    vendor = db.Column(db.String(100))
    license_type = db.Column(db.String(50))  # Per User, Per Device, Site License, Subscription, Perpetual
    license_key = db.Column(db.String(500))
    total_licenses = db.Column(db.Integer, default=1)
    purchase_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    renewal_date = db.Column(db.Date)
    purchase_cost = db.Column(db.Float)
    annual_cost = db.Column(db.Float)  # For subscriptions
    status = db.Column(db.String(20), default='Active')  # Active, Expired, Cancelled
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    assignments = db.relationship('LicenseAssignment', backref='license', lazy=True, cascade='all, delete-orphan')
    
    def get_available_licenses(self):
        """Calculate available licenses"""
        assigned = LicenseAssignment.query.filter_by(license_id=self.id, status='Active').count()
        return self.total_licenses - assigned
    
    def is_expiring_soon(self, days=30):
        """Check if license expires within specified days"""
        if self.expiry_date:
            days_until_expiry = (self.expiry_date - datetime.utcnow().date()).days
            return 0 < days_until_expiry <= days
        return False


class LicenseAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    license_id = db.Column(db.Integer, db.ForeignKey('license.id'), nullable=False)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'))
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'))
    product_component = db.Column(db.String(200))  # Specific product/component (e.g., "Adobe Acrobat Pro", "Full Suite")
    assigned_date = db.Column(db.Date, default=datetime.utcnow)
    status = db.Column(db.String(20), default='Active')  # Active, Returned
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(100))

class SystemDescription(db.Model):
    __tablename__ = 'system_description'
    id = db.Column(db.Integer, primary_key=True)
    section_title = db.Column(db.Text, nullable=False)
    section_level = db.Column(db.Integer, default=1)
    section_order = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text)
    auto_populated = db.Column(db.Boolean, default=False)
    template_content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(100))

class Policy(db.Model):
    __tablename__ = 'policy'
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.String(100), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(100))
    division = db.Column(db.String(50))
    standard_type = db.Column(db.String(50))
    version = db.Column(db.String(20))
    effective_date = db.Column(db.String(50))
    review_date = db.Column(db.String(50))
    approved_by = db.Column(db.String(200))
    content = db.Column(db.Text)
    file_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(100))
    
    sections = db.relationship('PolicySection', backref='policy', lazy=True, cascade='all, delete-orphan')

class PolicySection(db.Model):
    __tablename__ = 'policy_section'
    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('policy.id'), nullable=False)
    section_number = db.Column(db.String(20))
    section_title = db.Column(db.Text, nullable=False)
    section_content = db.Column(db.Text)
    section_order = db.Column(db.Integer)

class Control(db.Model):
    __tablename__ = 'control'
    id = db.Column(db.Integer, primary_key=True)
    control_name = db.Column(db.Text, nullable=False, unique=True)
    control_description = db.Column(db.Text)
    control_frequency = db.Column(db.String(50))
    control_owner = db.Column(db.String(200))
    control_progress = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    audit_alignment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Risk(db.Model):
    __tablename__ = 'risk'
    id = db.Column(db.Integer, primary_key=True)
    risk_name = db.Column(db.Text, nullable=False, unique=True)
    risk_description = db.Column(db.Text)
    risk_treatment = db.Column(db.String(50))
    risk_progress = db.Column(db.String(50))
    risk_category = db.Column(db.String(50))
    risk_status = db.Column(db.Boolean, default=True)
    risk_impact = db.Column(db.String(20))
    risk_likelihood = db.Column(db.String(20))
    risk_combined_score = db.Column(db.String(20))
    risk_owner = db.Column(db.String(200))
    active_controls = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ControlRiskMapping(db.Model):
    __tablename__ = 'control_risk_mapping'
    id = db.Column(db.Integer, primary_key=True)
    control_id = db.Column(db.Integer, db.ForeignKey('control.id', ondelete='CASCADE'))
    risk_id = db.Column(db.Integer, db.ForeignKey('risk.id', ondelete='CASCADE'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DashboardWidget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    widget_id = db.Column(db.String(100), nullable=False)  # Widget identifier (e.g., 'total_assets', 'in_use')
    widget_type = db.Column(db.String(50), nullable=False)  # stat, chart, table, report
    title = db.Column(db.String(200))
    config = db.Column(db.Text)  # JSON config for widget
    position = db.Column(db.Integer, default=0)  # Order/position
    size = db.Column(db.String(20), default='col-md-3')  # Bootstrap grid class
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CustomReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    report_type = db.Column(db.String(20), nullable=False)  # stat, list, chart
    config = db.Column(db.Text, nullable=False)  # JSON config for report (fields, filters, etc.)
    is_public = db.Column(db.Boolean, default=False)  # Can other users see it
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class LicenseInfo(db.Model):
    __tablename__ = 'license_info'
    id = db.Column(db.Integer, primary_key=True)
    license_key = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(255))  # License server API key
    device_id = db.Column(db.String(100))  # Device identifier for license verification
    status = db.Column(db.String(20), default='pending')  # active, expired, invalid, pending
    company_name = db.Column(db.String(255))
    plan_name = db.Column(db.String(100))
    expiry_date = db.Column(db.DateTime)
    max_devices = db.Column(db.Integer)
    last_checked = db.Column(db.DateTime)
    last_check_status = db.Column(db.String(50))  # success, invalid, server_unreachable
    grace_period_ends = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Monitoring models
class MonitoringProfile(db.Model):
    __tablename__ = 'monitoring_profile'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    device_type = db.Column(db.String(50), nullable=False)
    os_family = db.Column(db.String(50))
    severity_level = db.Column(db.String(20), default='standard')
    check_interval_minutes = db.Column(db.Integer, default=15)
    enabled = db.Column(db.Boolean, default=True)
    is_template = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MonitoringCheck(db.Model):
    __tablename__ = 'monitoring_check'
    id = db.Column(db.Integer, primary_key=True)
    check_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    script_type = db.Column(db.String(20))
    script_content = db.Column(db.Text)
    timeout_seconds = db.Column(db.Integer, default=30)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MonitoringAlert(db.Model):
    __tablename__ = 'monitoring_alert'
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'), nullable=False)
    check_id = db.Column(db.Integer, db.ForeignKey('monitoring_check.id'))
    severity = db.Column(db.String(20), nullable=False)  # info, warning, critical
    status = db.Column(db.String(20), default='open')  # open, acknowledged, resolved
    message = db.Column(db.Text, nullable=False)
    details = db.Column(db.Text)  # JSON with full check results
    triggered_at = db.Column(db.DateTime, default=datetime.utcnow)
    acknowledged_at = db.Column(db.DateTime)
    acknowledged_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    resolved_at = db.Column(db.DateTime)
    resolved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    failure_count = db.Column(db.Integer, default=1)
    first_failed_at = db.Column(db.DateTime)
    last_failed_at = db.Column(db.DateTime)
    asset = db.relationship('Asset', backref='monitoring_alerts')

class MaintenanceWindow(db.Model):
    __tablename__ = 'maintenance_window'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    window_type = db.Column(db.String(50), default='patching')
    day_of_week = db.Column(db.String(20))
    start_time = db.Column(db.String(5), nullable=False)
    end_time = db.Column(db.String(5), nullable=False)
    timezone = db.Column(db.String(50), default='America/Denver')
    recurrence = db.Column(db.String(20), default='weekly')
    specific_date = db.Column(db.Date)
    allow_patching = db.Column(db.Boolean, default=True)
    allow_reboots = db.Column(db.Boolean, default=True)
    suppress_alerts = db.Column(db.Boolean, default=True)
    notify_before_minutes = db.Column(db.Integer, default=60)
    notify_emails = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ProxmoxZfsPool(db.Model):
    __tablename__ = 'proxmox_zfs_pool'
    id = db.Column(db.Integer, primary_key=True)
    server = db.Column(db.String(100))       # cluster label or backup label
    node = db.Column(db.String(100))         # PVE node hostname
    pool_name = db.Column(db.String(100))    # ZFS pool name
    health = db.Column(db.String(20))        # ONLINE, DEGRADED, FAULTED, etc.
    used_gb = db.Column(db.Float, default=0.0)
    total_gb = db.Column(db.Float, default=0.0)
    percent_used = db.Column(db.Float, default=0.0)
    fragmentation = db.Column(db.Integer, default=0)
    last_synced = db.Column(db.DateTime)

class ProxmoxBackupJob(db.Model):
    __tablename__ = 'proxmox_backup_job'
    id = db.Column(db.Integer, primary_key=True)
    node = db.Column(db.String(100))
    vmid = db.Column(db.Integer)
    vm_name = db.Column(db.String(200))
    vm_type = db.Column(db.String(10))       # qemu, lxc
    vm_status = db.Column(db.String(20))     # running, stopped
    last_snapshot = db.Column(db.String(200))
    last_snapshot_time = db.Column(db.DateTime)
    snapshot_count = db.Column(db.Integer, default=0)
    backup_status = db.Column(db.String(20))  # ok, stale, missing
    last_synced = db.Column(db.DateTime)

# Association tables for many-to-many relationships
ProfileCheck = db.Table('profile_check',
    db.Column('id', db.Integer, primary_key=True),
    db.Column('profile_id', db.Integer, db.ForeignKey('monitoring_profile.id'), nullable=False),
    db.Column('check_id', db.Integer, db.ForeignKey('monitoring_check.id'), nullable=False),
    db.Column('enabled', db.Boolean, default=True),
    db.Column('check_interval_override', db.Integer),
    db.Column('warning_threshold', db.String(50)),
    db.Column('critical_threshold', db.String(50)),
    db.Column('parameters', db.Text)
)

AssetMonitoringProfile = db.Table('asset_monitoring_profile',
    db.Column('id', db.Integer, primary_key=True),
    db.Column('asset_id', db.Integer, db.ForeignKey('asset.id'), nullable=False),
    db.Column('profile_id', db.Integer, db.ForeignKey('monitoring_profile.id'), nullable=False),
    db.Column('assigned_at', db.DateTime, default=datetime.utcnow),
    db.Column('assigned_by', db.Integer),
    db.Column('notes', db.Text)
)

# ─── Audit Trail Model ─────────────────────────────────────────────────────
class AuditTrail(db.Model):
    __tablename__ = 'audit_trail'
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.Text, nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.Text, nullable=False)  # create, update, delete
    changes = db.Column(db.Text)  # JSON
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ip_address = db.Column(db.Text)
    user_agent = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.utcnow().replace(tzinfo=timezone.utc))

def _log_audit(entity_type, entity_id, action, changes=None):
    """Write an audit record. Safe to call from routes and event listeners."""
    from flask import has_request_context, request as _req, session
    try:
        uid = current_user.id if has_request_context() and current_user.is_authenticated else 1
        ip  = _req.remote_addr if has_request_context() else None
        ua  = _req.user_agent.string[:500] if has_request_context() else None
        entry = AuditTrail(
            entity_type=entity_type, entity_id=int(entity_id or 0),
            action=action, changes=json.dumps(changes) if changes else None,
            user_id=uid, ip_address=ip, user_agent=ua)
        db.session.add(entry)
        # Don't commit here — will be committed with the parent transaction
    except Exception:
        pass  # Never break the main operation due to audit logging

# ─────────────────────────────────────────────────────────────────────────────


def admin_required(f):
    """Decorator to require admin role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def manager_required(f):
    """Decorator to require manager or admin role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'manager']:
            flash('Access denied. Manager or Admin privileges required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def eagle_eyes_required(f):
    """Decorator to require admin or eagle_eyes role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'eagle_eyes']:
            flash('Access denied. Eagle Eyes access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def ticket_access_required(f):
    """Decorator to allow admin, manager, eagle_eyes, viewer, or base_user access to tickets"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'manager', 'eagle_eyes', 'viewer', 'base_user']:
            flash('Access denied.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def license_required(f):
    """Decorator to check license validity before allowing access"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow license management endpoints
        if request.endpoint in ['get_license', 'save_license', 'verify_license', 'remove_license_key', 'settings']:
            return f(*args, **kwargs)
        
        # Import license_service here to avoid circular imports
        from license_service import license_service
        
        # Check license validity
        status = license_service.is_license_valid()
        
        if not status['valid']:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({
                    'error': 'License expired or invalid',
                    'message': status['message'],
                    'licenseExpired': True
                }), 403
            else:
                flash(f'❌ LICENSE EXPIRED: {status["message"]} - Please update your license to continue using the system.', 'danger')
                return redirect(url_for('settings.settings') + '#license-tab')
        
        return f(*args, **kwargs)
    
    return decorated_function

@login_manager.user_loader
def load_user(user_id):
    real_user = User.query.get(int(user_id))
    # Admin impersonation: if a valid impersonation is stored in session, return that user
    if real_user and real_user.role == 'admin':
        impersonate_id = session.get('impersonate_user_id')
        if impersonate_id and int(impersonate_id) != int(user_id):
            imp_user = User.query.get(int(impersonate_id))
            if imp_user:
                return imp_user
    return real_user


# ─── SQLAlchemy Audit Event Listeners ────────────────────────────────────────
from sqlalchemy import event as _sa_event

def _make_audit_listener(entity_type):
    """Return after_insert / after_update / after_delete listeners for a model."""
    def _after_insert(mapper, connection, target):
        _log_audit(entity_type, target.id, 'create')
    def _after_delete(mapper, connection, target):
        _log_audit(entity_type, target.id, 'delete')
    def _after_update(mapper, connection, target):
        changed = {
            attr.key: str(getattr(target, attr.key))
            for attr in db.inspect(target).attrs
            if db.inspect(target).attrs[attr.key].history.has_changes()
            and attr.key not in ('updated_at', 'last_seen', 'last_login')
        }
        if changed:
            _log_audit(entity_type, target.id, 'update', changed)
    return _after_insert, _after_update, _after_delete

class AssetLoan(db.Model):
    __tablename__ = 'asset_loan'
    id = db.Column(db.BigInteger, primary_key=True)
    asset_id = db.Column(db.BigInteger, db.ForeignKey('asset.id'))
    checked_out_to = db.Column(db.Text)
    checked_out_by_user_id = db.Column(db.BigInteger)
    checked_out_at = db.Column(db.DateTime(timezone=True))
    due_back_at = db.Column(db.Date)
    checked_in_at = db.Column(db.DateTime(timezone=True))
    checked_in_by_user_id = db.Column(db.BigInteger)
    notes = db.Column(db.Text)


class InstalledApp(db.Model):
    __tablename__ = 'installed_app'
    id = db.Column(db.BigInteger, primary_key=True)
    asset_id = db.Column(db.BigInteger, db.ForeignKey('asset.id'))
    name = db.Column(db.Text)
    version = db.Column(db.Text)
    publisher = db.Column(db.Text)
    install_date = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime(timezone=True))


for _model, _etype in [(Asset, 'asset'), (Employee, 'employee'), (Policy, 'policy')]:
    _ins, _upd, _del = _make_audit_listener(_etype)
    _sa_event.listen(_model, 'after_insert', _ins)
    _sa_event.listen(_model, 'after_update', _upd)
    _sa_event.listen(_model, 'after_delete', _del)
# ─────────────────────────────────────────────────────────────────────────────
