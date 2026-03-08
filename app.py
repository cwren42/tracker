from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
import os
import time as _time
import threading
# ── Force server timezone to MST (Mountain Standard Time, UTC-7) ──────────
os.environ['TZ'] = 'America/Denver'
_time.tzset()
def now_mst(): return datetime.now()
# ────────────────────────────────────────────────────────────────────────────
import qrcode
import io
import base64
import csv
import json
import requests
import logging
import msal
import uuid
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from m365_service import M365Service
from sqlalchemy import text
from api_system import require_api_key
from ssh_manager import get_ssh_manager
import secrets
import alert_service as _alert_svc
import workflow_engine as _wf_engine
import ai_engine as _ai_engine
import report_engine as _report_engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////var/www/tracker/assets.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = True  # Enable secure cookies for HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = True  # Enable secure cookies for HTTPS
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['PREFERRED_URL_SCHEME'] = 'https'  # Use HTTPS for URL generation
app.config['UPLOAD_FOLDER'] = '/var/www/tracker/static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- HOTFIX: Restore asset 108 values on startup ---
# Must be after db and Asset are defined

# ...existing code...

# Email Configuration
app.config['MAIL_SERVER'] = '10.15.0.4'
app.config['MAIL_PORT'] = 25
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = None
app.config['MAIL_PASSWORD'] = None
app.config['MAIL_DEFAULT_SENDER'] = 'assettracker@cirque.com'
app.config['SEND_EMPLOYEE_EMAILS'] = False  # Disabled for now

# Linux Agent Configuration
app.config['LINUX_AGENT_API_KEY'] = os.environ.get('LINUX_AGENT_API_KEY', 'CirqueLinuxAgent2024!')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
mail = Mail(app)

# Import SOC2 models
from soc2_models import (
    SOC2Control, EvidenceSnapshot, M365User, IntuneDevice,
    DeviceSoftware, AdminRoleSnapshot, ComplianceReport, AuditLog,
    StrikeGraphEvidence
)

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default='viewer')  # admin, manager, viewer
    theme = db.Column(db.String(30), default='default')  # Theme preference
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    azure_id = db.Column(db.String(100))  # Azure AD user object ID
    full_name = db.Column(db.String(200))  # User's display name from Azure AD
    
    def has_permission(self, permission):
        """Check if user has a specific permission"""
        permissions = {
            'admin': ['view', 'edit', 'delete', 'manage_users'],
            'manager': ['view', 'edit'],
            'viewer': ['view']
        }
        return permission in permissions.get(self.role, [])

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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def admin_required(f):
    """Decorator to require admin role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def manager_required(f):
    """Decorator to require manager or admin role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'manager']:
            flash('Access denied. Manager or Admin privileges required.', 'danger')
            return redirect(url_for('index'))
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
                return redirect(url_for('settings') + '#license-tab')
        
        return f(*args, **kwargs)
    
    return decorated_function

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==================== EMAIL FUNCTIONS ====================

def send_email(subject, recipients, text_body, html_body=None):
    """Send email via configured SMTP server"""
    try:
        msg = Message(subject, recipients=recipients)
        msg.body = text_body
        if html_body:
            msg.html = html_body
        mail.send(msg)
        return True
    except Exception as e:
        app.logger.error(f"Failed to send email: {str(e)}")
        return False

def send_admin_notification(subject, message):
    """Send notification to all admin users"""
    try:
        admins = User.query.filter_by(role='admin').all()
        admin_emails = [admin.email for admin in admins if admin.email]
        
        if not admin_emails:
            app.logger.warning("No admin emails configured")
            return False
        
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #0d6efd;">Asset Tracker Notification</h2>
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        {message}
                    </div>
                    <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                        This is an automated message from the Asset Tracker system.
                    </p>
                </div>
            </body>
        </html>
        """
        
        return send_email(subject, admin_emails, message, html_body)
    except Exception as e:
        app.logger.error(f"Failed to send admin notification: {str(e)}")
        return False

def send_asset_assignment_email(asset, employee, assigned_by):
    """Send email when asset is assigned to employee (if enabled)"""
    if not app.config['SEND_EMPLOYEE_EMAILS']:
        return False
    
    if not employee or not employee.email:
        return False
    
    try:
        subject = f"Asset Assigned: {asset.asset_tag}"
        
        text_body = f"""
Hello {employee.name},

An asset has been assigned to you:

Asset Tag: {asset.asset_tag}
Name: {asset.name}
Category: {asset.category}
Serial Number: {asset.serial_number or 'N/A'}
Status: {asset.status}

Assigned by: {assigned_by}

Please take care of this asset and report any issues immediately.

Thank you,
IT Asset Management Team
        """
        
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #0d6efd;">Asset Assignment Notice</h2>
                    <p>Hello {employee.name},</p>
                    <p>An asset has been assigned to you:</p>
                    
                    <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px; font-weight: bold; width: 40%;">Asset Tag:</td>
                                <td style="padding: 8px;">{asset.asset_tag}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Name:</td>
                                <td style="padding: 8px;">{asset.name}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Category:</td>
                                <td style="padding: 8px;">{asset.category}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Serial Number:</td>
                                <td style="padding: 8px;">{asset.serial_number or 'N/A'}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Status:</td>
                                <td style="padding: 8px;">{asset.status}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <p style="margin-top: 20px;"><strong>Assigned by:</strong> {assigned_by}</p>
                    <p>Please take care of this asset and report any issues immediately.</p>
                    
                    <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                        This is an automated message from the Asset Tracker system.
                    </p>
                </div>
            </body>
        </html>
        """
        
        return send_email(subject, [employee.email], text_body, html_body)
    except Exception as e:
        app.logger.error(f"Failed to send assignment email: {str(e)}")
        return False

def send_warranty_expiry_alert(asset):
    """Send alert to admins for expiring warranty"""
    try:
        days_until_expiry = (asset.warranty_expiry - datetime.utcnow().date()).days
        
        subject = f"Warranty Expiring Soon: {asset.asset_tag}"
        message = f"""
        <p><strong>Asset:</strong> {asset.asset_tag} - {asset.name}</p>
        <p><strong>Category:</strong> {asset.category}</p>
        <p><strong>Warranty Expiry:</strong> {asset.warranty_expiry.strftime('%Y-%m-%d')}</p>
        <p><strong>Days Remaining:</strong> {days_until_expiry} days</p>
        <p style="color: #dc3545;">Please review and renew if necessary.</p>
        """
        
        return send_admin_notification(subject, message)
    except Exception as e:
        app.logger.error(f"Failed to send warranty alert: {str(e)}")
        return False

def send_lifecycle_alert(asset):
    """Send alert to admins for assets needing replacement"""
    try:
        status = asset.get_lifecycle_status()
        age = asset.get_age_years()
        
        subject = f"Asset Lifecycle Alert: {asset.asset_tag} - {status}"
        message = f"""
        <p><strong>Asset:</strong> {asset.asset_tag} - {asset.name}</p>
        <p><strong>Category:</strong> {asset.category}</p>
        <p><strong>Lifecycle Status:</strong> <span style="color: #dc3545;">{status}</span></p>
        <p><strong>Current Age:</strong> {age:.1f} years</p>
        <p><strong>Expected Life:</strong> {asset.expected_life_years} years</p>
        <p><strong>Replacement Date:</strong> {asset.replacement_date.strftime('%Y-%m-%d') if asset.replacement_date else 'Not set'}</p>
        <p style="color: #dc3545;">This asset may need replacement soon.</p>
        """
        
        return send_admin_notification(subject, message)
    except Exception as e:
        app.logger.error(f"Failed to send lifecycle alert: {str(e)}")
        return False

# Routes
@app.route('/')
@login_required
@license_required
def index():
    # Get user's dashboard configuration
    user_widgets = DashboardWidget.query.filter_by(
        user_id=current_user.id,
        enabled=True
    ).order_by(DashboardWidget.position).all()
    
    # If no custom widgets, use default layout
    if not user_widgets:
        user_widgets = get_default_widgets()
    
    # Gather all dashboard data
    dashboard_data = get_dashboard_data()

    # Last time the underlying device/asset data was refreshed (best-effort)
    last_report_run_display = None
    try:
        candidate_times_utc = []

        def _as_utc(dt: datetime) -> datetime:
            if not dt:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        try:
            from sqlalchemy import func
            max_intune = db.session.query(func.max(Asset.intune_last_sync)).scalar()
            max_intune_utc = _as_utc(max_intune) if max_intune else None
            if max_intune_utc:
                candidate_times_utc.append(max_intune_utc)
        except Exception:
            pass

        if candidate_times_utc:
            latest_utc = max(candidate_times_utc)
            last_report_run_display = latest_utc.strftime('%Y-%m-%d %H:%M UTC')
    except Exception:
        last_report_run_display = None
    
    return render_template('index.html', 
                         widgets=user_widgets,
                         data=dashboard_data,
                         edit_mode=request.args.get('edit') == 'true',
                         last_report_run_display=last_report_run_display)

def get_default_widgets():
    """Return default dashboard widget configuration"""
    return [
        # Row 1 — system health cards
        {'id': 'tickets_summary', 'type': 'tickets_summary', 'title': 'Tickets', 'size': 'col-md-3', 'position': 0},
        {'id': 'monitoring_summary', 'type': 'monitoring_summary', 'title': 'Monitoring Alerts', 'size': 'col-md-3', 'position': 1},
        {'id': 'backup_summary', 'type': 'backup_summary', 'title': 'Backup Health', 'size': 'col-md-3', 'position': 2},
        {'id': 'licenses_summary', 'type': 'licenses_summary', 'title': 'Licenses', 'size': 'col-md-3', 'position': 3},
        # Row 2 — asset stats
        {'id': 'total_assets', 'type': 'stat', 'title': 'Total Assets', 'size': 'col-md-2', 'position': 4},
        {'id': 'in_use', 'type': 'stat', 'title': 'In Use', 'size': 'col-md-2', 'position': 5},
        {'id': 'available', 'type': 'stat', 'title': 'Available', 'size': 'col-md-2', 'position': 6},
        {'id': 'in_repair', 'type': 'stat', 'title': 'In Repair', 'size': 'col-md-2', 'position': 7},
        {'id': 'avg_age', 'type': 'stat', 'title': 'Avg Age', 'size': 'col-md-2', 'position': 8},
        {'id': 'replacement', 'type': 'stat', 'title': 'Need Replacement', 'size': 'col-md-2', 'position': 9},
        # Row 3 — alert cards
        {'id': 'noncompliant', 'type': 'alert', 'title': 'Non-Compliant Devices', 'size': 'col-md-3', 'position': 10, 'icon': 'bi-shield-exclamation'},
        {'id': 'low_storage', 'type': 'alert', 'title': 'Low Storage (<20%)', 'size': 'col-md-3', 'position': 11, 'icon': 'bi-hdd'},
        {'id': 'offline', 'type': 'alert', 'title': 'Offline 7+ Days', 'size': 'col-md-3', 'position': 12, 'icon': 'bi-wifi-off'},
        {'id': 'warranty_expiring', 'type': 'alert', 'title': 'Warranty Expiring Soon', 'size': 'col-md-3', 'position': 13, 'icon': 'bi-calendar-x'},
        # Row 4 — activity
        {'id': 'incomplete_assets', 'type': 'list', 'title': 'Assets Needing Information', 'size': 'col-md-6', 'position': 14},
    ]

def get_dashboard_data():
    """Gather all possible dashboard data"""
    def _format_dt_utc(dt: datetime | None) -> str | None:
        if not dt:
            return None

        try:
            if dt.tzinfo is None:
                dt_utc = dt.replace(tzinfo=timezone.utc)
            else:
                dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.strftime('%Y-%m-%d %H:%M UTC')
        except Exception:
            return None

    total_assets = Asset.query.count()
    in_use = Asset.query.filter_by(status='In Use').count()
    available = Asset.query.filter_by(status='Available').count()
    in_repair = Asset.query.filter_by(status='In Repair').count()
    
    # Check warranty expiring soon (within 30 days)
    thirty_days = datetime.utcnow().date() + timedelta(days=30)
    expiring_soon = Asset.query.filter(
        Asset.warranty_expiry.isnot(None),
        Asset.warranty_expiry <= thirty_days,
        Asset.warranty_expiry >= datetime.utcnow().date()
    ).count()
    
    # Check assets needing replacement (within 6 months)
    all_assets = Asset.query.all()
    replacement_needed = sum(1 for asset in all_assets if asset.needs_replacement())
    
    # NEW: Low storage devices (<20% free space)
    low_storage_assets = []
    for asset in all_assets:
        if asset.hardware_storage_total_gb and asset.hardware_storage_free_gb:
            free_pct = (asset.hardware_storage_free_gb / asset.hardware_storage_total_gb) * 100
            if free_pct < 20:
                low_storage_assets.append(asset)

    # Last updated for low storage is based on latest Intune sync among the matching assets
    low_storage_last_updated = None
    low_storage_sync_times = []
    for asset in low_storage_assets:
        if asset.intune_last_sync:
            try:
                low_storage_sync_times.append(
                    asset.intune_last_sync.replace(tzinfo=timezone.utc)
                    if asset.intune_last_sync.tzinfo is None
                    else asset.intune_last_sync.astimezone(timezone.utc)
                )
            except Exception:
                pass
    if low_storage_sync_times:
        low_storage_last_updated = _format_dt_utc(max(low_storage_sync_times))
    
    # NEW: Non-compliant devices
    noncompliant_assets = Asset.query.filter_by(online_state='noncompliant').all()

    # Last updated for non-compliant is based on latest Intune sync among the matching assets
    noncompliant_last_updated = None
    noncompliant_sync_times = []
    for asset in noncompliant_assets:
        if asset.intune_last_sync:
            try:
                noncompliant_sync_times.append(
                    asset.intune_last_sync.replace(tzinfo=timezone.utc)
                    if asset.intune_last_sync.tzinfo is None
                    else asset.intune_last_sync.astimezone(timezone.utc)
                )
            except Exception:
                pass
    if noncompliant_sync_times:
        noncompliant_last_updated = _format_dt_utc(max(noncompliant_sync_times))
    
    # NEW: Devices offline for 7+ days
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    offline_assets = Asset.query.filter(
        Asset.last_seen.isnot(None),
        Asset.last_seen < seven_days_ago
    ).all()
    
    # Calculate average asset age
    assets_with_age = [asset for asset in all_assets if asset.purchase_date]
    avg_age = sum(asset.get_age_years() for asset in assets_with_age) / len(assets_with_age) if assets_with_age else 0
    
    # Category breakdown for dashboard chart (with count and value)
    category_counts_raw = db.session.query(
        Asset.category, 
        db.func.count(Asset.id),
        db.func.sum(Asset.purchase_cost)
    ).group_by(Asset.category).all()
    category_counts = [[row[0], row[1], row[2] if row[2] else 0] for row in category_counts_raw]
    
    recent_assets = Asset.query.order_by(Asset.created_at.desc()).limit(5).all()
    
    # License statistics
    total_licenses = License.query.count()
    active_licenses = License.query.filter_by(status='Active').count()
    expired_licenses = License.query.filter_by(status='Expired').count()
    
    # License expiring soon (within 30 days)
    all_active_licenses = License.query.filter_by(status='Active').all()
    licenses_expiring_soon = sum(1 for lic in all_active_licenses if lic.is_expiring_soon(30))
    
    # Total license seats and usage
    total_license_seats = db.session.query(db.func.sum(License.total_licenses)).scalar() or 0
    total_assigned_seats = LicenseAssignment.query.filter_by(status='Active').count()
    
    # Annual license cost
    total_annual_license_cost = db.session.query(db.func.sum(License.annual_cost)).filter(
        License.annual_cost.isnot(None)
    ).scalar() or 0
    
    # Employee count
    total_employees = Employee.query.count()
    recent_employees = Employee.query.order_by(Employee.created_at.desc()).limit(5).all()
    
    # Warranty expiring assets
    warranty_expiring = Asset.query.filter(
        Asset.warranty_expiry.isnot(None),
        Asset.warranty_expiry <= thirty_days,
        Asset.warranty_expiry >= datetime.utcnow().date()
    ).limit(10).all()
    
    # Replacement needed assets
    replacement_list = [asset for asset in all_assets if asset.needs_replacement()][:10]
    
    # Licenses expiring soon
    licenses_expiring_list = [lic for lic in all_active_licenses if lic.is_expiring_soon(30)][:10]
    
    # Status breakdown
    status_counts_raw = db.session.query(
        Asset.status,
        db.func.count(Asset.id)
    ).group_by(Asset.status).all()
    status_counts = [[row[0], row[1]] for row in status_counts_raw]
    
    # Department breakdown
    dept_counts_raw = db.session.query(
        Employee.department,
        db.func.count(Asset.id)
    ).join(Asset, Employee.id == Asset.employee_id, isouter=True).group_by(Employee.department).all()
    dept_counts = [[row[0], row[1]] for row in dept_counts_raw]
    
    # Lifecycle stats
    lifecycle_stats = {}
    for asset in all_assets:
        if asset.purchase_date and asset.expected_life_years:
            status = asset.get_lifecycle_status()
            lifecycle_stats[status] = lifecycle_stats.get(status, 0) + 1
    
    # License vendor stats with annual cost
    license_vendor_stats_raw = db.session.query(
        License.vendor,
        db.func.count(License.id),
        db.func.sum(License.annual_cost)
    ).group_by(License.vendor).all()
    license_vendor_stats = [[row[0], row[1], row[2] if row[2] else 0] for row in license_vendor_stats_raw]

    # License type stats
    license_type_stats_raw = db.session.query(
        License.license_type,
        db.func.count(License.id)
    ).group_by(License.license_type).all()
    license_type_stats = [[row[0], row[1]] for row in license_type_stats_raw]

    # License utilization by software
    active_licenses_list = License.query.filter_by(status='Active').all()
    license_utilization = []
    for lic in active_licenses_list:
        assigned = LicenseAssignment.query.filter_by(license_id=lic.id, status='Active').count()
        available = (lic.total_licenses or 0) - assigned
        utilization_pct = (assigned / lic.total_licenses * 100) if lic.total_licenses else 0
        license_utilization.append({
            'software': lic.software_name,
            'vendor': lic.vendor,
            'total': lic.total_licenses,
            'assigned': assigned,
            'available': available,
            'utilization': utilization_pct
        })
    
    # License type stats
    license_type_stats_raw = db.session.query(
        License.license_type,
        db.func.count(License.id)
    ).group_by(License.license_type).all()
    license_type_stats = [[row[0], row[1]] for row in license_type_stats_raw]
    
    # Incomplete assets (missing manufacturer, model, or serial)
    incomplete_assets = Asset.query.filter(
        db.or_(
            Asset.manufacturer.is_(None),
            Asset.manufacturer == '',
            Asset.model.is_(None),
            Asset.model == '',
            Asset.serial_number.is_(None),
            Asset.serial_number == ''
        )
    ).order_by(Asset.created_at.desc()).limit(10).all()

    # ── Tickets cross-module data ─────────────────────────────────────────────
    tickets_open = SupportTicket.query.filter(SupportTicket.status == 'Open').count()
    tickets_in_progress = SupportTicket.query.filter(SupportTicket.status == 'In Progress').count()
    tickets_unassigned = SupportTicket.query.filter(
        SupportTicket.status.in_(['Open', 'In Progress']),
        SupportTicket.assigned_to_user_id.is_(None)
    ).count()
    # Urgent open tickets (treat Urgent priority as highest severity)
    tickets_urgent = SupportTicket.query.filter(
        SupportTicket.status.in_(['Open', 'In Progress']),
        SupportTicket.priority == 'Urgent'
    ).count()
    recent_tickets = SupportTicket.query.filter(
        SupportTicket.status.in_(['Open', 'In Progress'])
    ).order_by(SupportTicket.created_at.desc()).limit(5).all()

    # ── Monitoring alerts cross-module data ───────────────────────────────────
    alerts_critical = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open',
        MonitoringAlert.severity == 'critical'
    ).count()
    alerts_warning = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open',
        MonitoringAlert.severity == 'warning'
    ).count()
    alerts_open_total = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open'
    ).count()
    recent_alerts_list = MonitoringAlert.query.filter(
        MonitoringAlert.status == 'open'
    ).order_by(MonitoringAlert.triggered_at.desc()).limit(5).all()

    # ── Proxmox / backup health cross-module data ─────────────────────────────
    proxmox_degraded_pools = ProxmoxZfsPool.query.filter(
        ProxmoxZfsPool.health != 'ONLINE'
    ).count()
    proxmox_total_pools = ProxmoxZfsPool.query.count()
    proxmox_stale_vms = ProxmoxBackupJob.query.filter(
        ProxmoxBackupJob.backup_status == 'stale'
    ).count()
    proxmox_total_vms = ProxmoxBackupJob.query.count()

    return {
        'total_assets': total_assets,
        'in_use': in_use,
        'available': available,
        'in_repair': in_repair,
        'expiring_soon': expiring_soon,
        'replacement_needed': replacement_needed,
        'avg_age': avg_age,
        'low_storage_count': len(low_storage_assets),
        'low_storage_assets': low_storage_assets[:10],
        'low_storage_last_updated': low_storage_last_updated,
        'noncompliant_count': len(noncompliant_assets),
        'noncompliant_assets': noncompliant_assets[:10],
        'noncompliant_last_updated': noncompliant_last_updated,
        'offline_count': len(offline_assets),
        'offline_assets': offline_assets[:10],
        'category_counts': category_counts,
        'recent_assets': recent_assets,
        'total_licenses': total_licenses,
        'active_licenses': active_licenses,
        'expired_licenses': expired_licenses,
        'licenses_expiring_soon': licenses_expiring_soon,
        'total_license_seats': total_license_seats,
        'total_assigned_seats': total_assigned_seats,
        'total_annual_license_cost': total_annual_license_cost,
        'total_employees': total_employees,
        'recent_employees': recent_employees,
        'warranty_expiring': warranty_expiring,
        'replacement_list': replacement_list,
        'licenses_expiring_list': licenses_expiring_list,
        'incomplete_assets': incomplete_assets,
        'status_counts': status_counts,
        'dept_counts': dept_counts,
        'lifecycle_stats': lifecycle_stats,
        'license_vendor_stats': license_vendor_stats,
        'license_type_stats': license_type_stats,
        'license_utilization': license_utilization,
        'total_value': sum(row[2] if row[2] else 0 for row in category_counts),
        # Tickets
        'tickets_open': tickets_open,
        'tickets_inprog': tickets_in_progress,
        'tickets_unassigned': tickets_unassigned,
        'tickets_urgent': tickets_urgent,
        'recent_tickets': recent_tickets,
        # Monitoring
        'alerts_critical': alerts_critical,
        'alerts_warning': alerts_warning,
        'alerts_open_total': alerts_open_total,
        'recent_alerts_list': recent_alerts_list,
        # Backups
        'proxmox_degraded_pools': proxmox_degraded_pools,
        'proxmox_total_pools': proxmox_total_pools,
        'proxmox_stale_vms': proxmox_stale_vms,
        'proxmox_total_vms': proxmox_total_vms,
    }

@app.route('/dashboard/configure', methods=['GET', 'POST'])
@login_required
@license_required
def configure_dashboard():
    """Configure dashboard widgets"""
    if request.method == 'POST':
        try:
            # Get widget configuration from POST data
            widgets_data = request.get_json()
            
            # Delete existing widgets for this user
            DashboardWidget.query.filter_by(user_id=current_user.id).delete()
            
            # Create new widgets
            for widget_data in widgets_data:
                widget = DashboardWidget(
                    user_id=current_user.id,
                    widget_id=widget_data.get('id'),  # Store the widget identifier
                    widget_type=widget_data.get('type'),
                    title=widget_data.get('title'),
                    config=json.dumps(widget_data.get('config', {})),
                    position=widget_data.get('position', 0),
                    size=widget_data.get('size', 'col-md-3'),
                    enabled=widget_data.get('enabled', True)
                )
                db.session.add(widget)
            
            db.session.commit()
            return jsonify({'success': True, 'message': 'Dashboard updated successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 400
    
    # GET - return available widgets and current configuration
    available_widgets = get_available_widgets()
    current_widgets = DashboardWidget.query.filter_by(user_id=current_user.id).order_by(DashboardWidget.position).all()
    
    return render_template('configure_dashboard.html',
                         available_widgets=available_widgets,
                         current_widgets=current_widgets)

def get_available_widgets():
    """Return list of all available widget types"""
    return [
        {'id': 'total_assets', 'name': 'Total Assets', 'type': 'stat', 'icon': 'bi-box-seam', 'color': 'primary'},
        {'id': 'available', 'name': 'Available Assets', 'type': 'stat', 'icon': 'bi-check-circle', 'color': 'success'},
        {'id': 'in_use', 'name': 'In Use', 'type': 'stat', 'icon': 'bi-arrow-repeat', 'color': 'info'},
        {'id': 'in_repair', 'name': 'In Repair', 'type': 'stat', 'icon': 'bi-tools', 'color': 'warning'},
        {'id': 'avg_age', 'name': 'Average Age', 'type': 'stat', 'icon': 'bi-clock-history', 'color': 'secondary'},
        {'id': 'replacement', 'name': 'Need Replacement', 'type': 'stat', 'icon': 'bi-exclamation-circle', 'color': 'danger'},
        {'id': 'total_licenses', 'name': 'Total Licenses', 'type': 'stat', 'icon': 'bi-key', 'color': 'primary'},
        {'id': 'active_licenses', 'name': 'Active Licenses', 'type': 'stat', 'icon': 'bi-check-circle', 'color': 'success'},
        {'id': 'license_seats', 'name': 'License Seats Used', 'type': 'stat', 'icon': 'bi-people', 'color': 'info'},
        {'id': 'license_cost', 'name': 'Annual License Cost', 'type': 'stat', 'icon': 'bi-currency-dollar', 'color': 'warning'},
        {'id': 'total_employees', 'name': 'Total Employees', 'type': 'stat', 'icon': 'bi-people-fill', 'color': 'info'},
        {'id': 'overview_stats', 'name': 'Asset Overview Statistics', 'type': 'overview', 'icon': 'bi-graph-up', 'color': 'primary'},
        {'id': 'recent_assets', 'name': 'Recent Assets', 'type': 'list', 'icon': 'bi-list-ul', 'color': 'primary'},
        {'id': 'category_chart', 'name': 'Assets by Category', 'type': 'chart', 'icon': 'bi-pie-chart', 'color': 'success'},
        {'id': 'category_value_chart', 'name': 'Total Value by Category', 'type': 'chart', 'icon': 'bi-cash-stack', 'color': 'primary'},
        {'id': 'warranty_expiring', 'name': 'Warranty Expiring Soon', 'type': 'list', 'icon': 'bi-exclamation-triangle', 'color': 'warning'},
        {'id': 'replacement_needed', 'name': 'Replacement Needed', 'type': 'list', 'icon': 'bi-arrow-clockwise', 'color': 'danger'},
        {'id': 'licenses_expiring', 'name': 'Licenses Expiring', 'type': 'list', 'icon': 'bi-key', 'color': 'warning'},
        {'id': 'incomplete_assets', 'name': 'Incomplete Assets', 'type': 'list', 'icon': 'bi-clipboard-x', 'color': 'warning'},
        {'id': 'status_chart', 'name': 'Assets by Status', 'type': 'chart', 'icon': 'bi-bar-chart', 'color': 'info'},
        {'id': 'department_chart', 'name': 'Assets by Department', 'type': 'chart', 'icon': 'bi-building', 'color': 'success'},
        {'id': 'department_table', 'name': 'Department Asset Summary', 'type': 'table', 'icon': 'bi-table', 'color': 'info'},
        {'id': 'lifecycle_chart', 'name': 'Lifecycle Status', 'type': 'chart', 'icon': 'bi-clock-history', 'color': 'primary'},
        {'id': 'license_vendor_chart', 'name': 'Licenses by Vendor', 'type': 'chart', 'icon': 'bi-pie-chart-fill', 'color': 'info'},
        {'id': 'license_type_chart', 'name': 'Licenses by Type', 'type': 'chart', 'icon': 'bi-pie-chart-fill', 'color': 'info'},
        {'id': 'license_cost_chart', 'name': 'Annual License Costs', 'type': 'chart', 'icon': 'bi-cash-stack', 'color': 'success'},
        {'id': 'license_utilization_chart', 'name': 'License Utilization', 'type': 'chart', 'icon': 'bi-bar-chart', 'color': 'primary'},
        {'id': 'license_seat_table', 'name': 'License Seat Utilization', 'type': 'table', 'icon': 'bi-table', 'color': 'primary'},
        {'id': 'recent_employees', 'name': 'Recent Employees', 'type': 'list', 'icon': 'bi-people', 'color': 'info'},
    ]

@app.route('/dashboard/reset', methods=['POST'])
@login_required
@license_required
def reset_dashboard():
    """Reset dashboard to default layout"""
    try:
        DashboardWidget.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        flash('Dashboard reset to default layout', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error resetting dashboard: {str(e)}', 'danger')
        return redirect(url_for('index'))

@app.route('/dashboard/add-widget', methods=['POST'])
@login_required
@license_required
def add_widget_to_dashboard():
    """Add a single widget to the dashboard"""
    try:
        data = request.get_json()
        widget_id = data.get('widget_id')
        widget_type = data.get('widget_type')
        widget_title = data.get('title')
        widget_size = data.get('size', 'col-md-4 widget-1-row')
        
        # Check if widget already exists
        existing = DashboardWidget.query.filter_by(
            user_id=current_user.id,
            widget_id=widget_id
        ).first()
        
        if existing:
            return jsonify({'success': False, 'message': 'Widget already exists on your dashboard'}), 400
        
        # Determine position based on widget type
        if widget_type == 'stat':
            # For stat widgets, insert after the last stat widget
            last_stat_position = db.session.query(db.func.max(DashboardWidget.position)).filter_by(
                user_id=current_user.id,
                widget_type='stat'
            ).scalar()
            
            if last_stat_position is not None:
                # Insert after the last stat widget
                new_position = last_stat_position + 1
                # Shift all widgets after this position
                DashboardWidget.query.filter(
                    DashboardWidget.user_id == current_user.id,
                    DashboardWidget.position >= new_position
                ).update({DashboardWidget.position: DashboardWidget.position + 1})
            else:
                # No stat widgets exist, put at the beginning
                new_position = 0
                # Shift all existing widgets
                DashboardWidget.query.filter_by(user_id=current_user.id).update(
                    {DashboardWidget.position: DashboardWidget.position + 1}
                )
        else:
            # For non-stat widgets, add to the end
            max_position = db.session.query(db.func.max(DashboardWidget.position)).filter_by(
                user_id=current_user.id
            ).scalar() or -1
            new_position = max_position + 1
        
        # Create new widget
        widget = DashboardWidget(
            user_id=current_user.id,
            widget_id=widget_id,
            widget_type=widget_type,
            title=widget_title,
            config=json.dumps({}),
            position=new_position,
            size=widget_size,
            enabled=True
        )
        db.session.add(widget)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'{widget_title} added to dashboard'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page - supports local and Azure AD authentication"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'danger')
    
    # Check if Azure AD is configured for SSO button
    azure_config = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
    azure_enabled = azure_config is not None
    
    return render_template('login.html', azure_enabled=azure_enabled)

@app.route('/login/microsoft')
def login_microsoft():
    """Initiate Microsoft/Azure AD OAuth2 login flow"""
    # Get Azure configuration
    azure_config = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
    
    if not azure_config:
        flash('Azure AD authentication is not configured.', 'danger')
        return redirect(url_for('login'))
    
    # Create MSAL confidential client
    msal_app = msal.ConfidentialClientApplication(
        azure_config.client_id,
        authority=f"https://login.microsoftonline.com/{azure_config.tenant_id}",
        client_credential=azure_config.client_secret
    )
    
    # Generate auth URL with PKCE
    session['state'] = str(uuid.uuid4())
    
    auth_url = msal_app.get_authorization_request_url(
        scopes=["User.Read"],
        state=session['state'],
        redirect_uri=url_for('login_microsoft_callback', _external=True, _scheme='https')
    )
    
    return redirect(auth_url)

@app.route('/login/microsoft/callback')
def login_microsoft_callback():
    """Handle Microsoft/Azure AD OAuth2 callback"""
    # Verify state to prevent CSRF
    if request.args.get('state') != session.get('state'):
        flash('Authentication failed: Invalid state parameter.', 'danger')
        return redirect(url_for('login'))
    
    # Check for errors
    if 'error' in request.args:
        flash(f'Authentication failed: {request.args.get("error_description", "Unknown error")}', 'danger')
        return redirect(url_for('login'))
    
    # Get authorization code
    code = request.args.get('code')
    if not code:
        flash('Authentication failed: No authorization code received.', 'danger')
        return redirect(url_for('login'))
    
    # Get Azure configuration
    azure_config = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
    
    if not azure_config:
        flash('Azure AD authentication is not configured.', 'danger')
        return redirect(url_for('login'))
    
    # Exchange code for token
    msal_app = msal.ConfidentialClientApplication(
        azure_config.client_id,
        authority=f"https://login.microsoftonline.com/{azure_config.tenant_id}",
        client_credential=azure_config.client_secret
    )
    
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=["User.Read"],
        redirect_uri=url_for('login_microsoft_callback', _external=True, _scheme='https')
    )
    
    if "error" in result:
        flash(f'Authentication failed: {result.get("error_description", "Unknown error")}', 'danger')
        return redirect(url_for('login'))
    
    # Get user info from Microsoft Graph
    access_token = result['access_token']
    graph_response = requests.get(
        'https://graph.microsoft.com/v1.0/me',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    
    if graph_response.status_code != 200:
        flash('Failed to retrieve user information from Microsoft.', 'danger')
        return redirect(url_for('login'))
    
    user_info = graph_response.json()
    
    # Extract user details
    email = user_info.get('mail') or user_info.get('userPrincipalName')
    display_name = user_info.get('displayName', '')
    azure_id = user_info.get('id')
    
    # Check if user exists in database
    user = User.query.filter_by(email=email).first()
    
    if not user:
        # Auto-create user account from Azure AD
        username = email.split('@')[0]  # Use email prefix as username
        
        # Check if username already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            username = f"{username}_{azure_id[:8]}"  # Make unique
        
        user = User(
            username=username,
            email=email,
            full_name=display_name,
            password_hash=generate_password_hash(str(uuid.uuid4())),
            role='viewer',  # Default role for new users
            azure_id=azure_id,
            created_at=datetime.utcnow()
        )
        db.session.add(user)
        db.session.commit()
        
        flash(f'Welcome to the Asset Tracker! Your account has been created.', 'success')
    else:
        # Update azure_id if not set
        if not user.azure_id:
            user.azure_id = azure_id
            db.session.commit()
        
        flash(f'Welcome back, {display_name}!', 'success')
    
    # Log user in
    login_user(user)
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))

# ==================== SETTINGS ====================

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    """Admin settings page for email configuration and testing"""
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'test_email':
            # Test email to admin
            test_email = request.form.get('test_email')
            if test_email:
                try:
                    subject = "Asset Tracker - Test Email"
                    message = f"""
                    <p>This is a test email from the Asset Tracker system.</p>
                    <p><strong>Date/Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                    <p><strong>Sent by:</strong> {current_user.username}</p>
                    <p>If you received this email, your SMTP configuration is working correctly!</p>
                    """
                    
                    if send_email(subject, [test_email], "Test email from Asset Tracker", message):
                        flash('Test email sent successfully! Check your inbox.', 'success')
                    else:
                        flash('Failed to send test email. Check server logs for details.', 'danger')
                except Exception as e:
                    flash(f'Error sending test email: {str(e)}', 'danger')
            else:
                flash('Please enter an email address for testing.', 'warning')
        
        elif action == 'toggle_employee_emails':
            # Toggle employee email notifications
            app.config['SEND_EMPLOYEE_EMAILS'] = not app.config['SEND_EMPLOYEE_EMAILS']
            status = "enabled" if app.config['SEND_EMPLOYEE_EMAILS'] else "disabled"
            flash(f'Employee email notifications {status}.', 'success')
        
        elif action == 'update_sender':
            # Update default sender email
            new_sender = request.form.get('sender_email')
            if new_sender:
                app.config['MAIL_DEFAULT_SENDER'] = new_sender
                flash('Default sender email updated.', 'success')
        
        elif action == 'update_smtp':
            # Update SMTP settings
            smtp_server = request.form.get('smtp_server', '').strip()
            smtp_port = request.form.get('smtp_port', '').strip()
            smtp_username = request.form.get('smtp_username', '').strip()
            smtp_password = request.form.get('smtp_password', '').strip()
            use_tls = request.form.get('use_tls') == 'on'
            use_ssl = request.form.get('use_ssl') == 'on'
            sender_email = request.form.get('sender_email', '').strip()
            
            try:
                # Update settings in database
                settings_to_update = {
                    'smtp_server': smtp_server,
                    'smtp_port': smtp_port,
                    'smtp_username': smtp_username,
                    'smtp_use_tls': 'true' if use_tls else 'false',
                    'smtp_use_ssl': 'true' if use_ssl else 'false',
                    'smtp_sender': sender_email
                }
                
                # Only save password if provided
                if smtp_password:
                    settings_to_update['smtp_password'] = smtp_password
                
                for key, value in settings_to_update.items():
                    setting = Setting.query.filter_by(key=key).first()
                    if not setting:
                        setting = Setting(key=key)
                    setting.value = value
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)
                
                db.session.commit()
                
                # Update app config
                app.config['MAIL_SERVER'] = smtp_server
                app.config['MAIL_PORT'] = int(smtp_port) if smtp_port else 25
                app.config['MAIL_USERNAME'] = smtp_username if smtp_username else None
                if smtp_password:
                    app.config['MAIL_PASSWORD'] = smtp_password
                app.config['MAIL_USE_TLS'] = use_tls
                app.config['MAIL_USE_SSL'] = use_ssl
                app.config['MAIL_DEFAULT_SENDER'] = sender_email
                
                flash('SMTP settings updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating SMTP settings: {str(e)}', 'danger')

        elif action == 'update_unifi':
            # Save UniFi credentials
            def _save(key, val):
                s = Setting.query.filter_by(key=key).first()
                if not s:
                    s = Setting(key=key)
                    db.session.add(s)
                s.value = val
                s.updated_by = current_user.username
                s.updated_at = datetime.utcnow()
            raw_host = request.form.get('unifi_host', '').strip().rstrip('/')
            # Normalize scheme
            if raw_host and not raw_host.startswith(('http://', 'https://')):
                raw_host = 'https://' + raw_host
            _save('unifi_host', raw_host)
            _save('unifi_username', request.form.get('unifi_username', '').strip())
            pw = request.form.get('unifi_password', '')
            if pw:  # only overwrite if a new password was submitted
                _save('unifi_password', pw)
            _save('unifi_site', request.form.get('unifi_site', 'default').strip() or 'default')
            db.session.commit()
            flash('UniFi settings saved successfully!', 'success')

        elif action == 'update_theme':
            # Update user's theme preference
            theme = request.form.get('theme', 'default')
            current_user.theme = theme
            db.session.commit()
            flash('Theme updated successfully!', 'success')

        elif action == 'update_ad':
            ad_enabled = request.form.get('ad_enabled') == 'on'
            ad_server = request.form.get('ad_server', '').strip()
            ad_port = request.form.get('ad_port', '').strip()
            ad_use_ssl = request.form.get('ad_use_ssl') == 'on'
            ad_base_dn = request.form.get('ad_base_dn', '').strip()
            ad_bind_username = request.form.get('ad_bind_username', '').strip()
            ad_bind_password = request.form.get('ad_bind_password', '')
            ad_user_ou_dn = request.form.get('ad_user_ou_dn', '').strip()

            try:
                settings_to_update = {
                    'ad_enabled': 'true' if ad_enabled else 'false',
                    'ad_server': ad_server,
                    'ad_port': ad_port,
                    'ad_use_ssl': 'true' if ad_use_ssl else 'false',
                    'ad_base_dn': ad_base_dn,
                    'ad_bind_username': ad_bind_username,
                    'ad_user_ou_dn': ad_user_ou_dn,
                }
                for key, value in settings_to_update.items():
                    setting = Setting.query.filter_by(key=key).first()
                    if not setting:
                        setting = Setting(key=key)
                    setting.value = value
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)

                # Only update password if provided
                if ad_bind_password:
                    setting = Setting.query.filter_by(key='ad_bind_password').first()
                    if not setting:
                        setting = Setting(key='ad_bind_password')
                    setting.value = ad_bind_password
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)

                db.session.commit()
                flash('AD/LDAP settings updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating AD/LDAP settings: {str(e)}', 'danger')
        
        return redirect(url_for('settings'))
    
    # Load SMTP settings from database
    smtp_settings = {
        'smtp_server': Setting.query.filter_by(key='smtp_server').first(),
        'smtp_port': Setting.query.filter_by(key='smtp_port').first(),
        'smtp_username': Setting.query.filter_by(key='smtp_username').first(),
        'smtp_password': Setting.query.filter_by(key='smtp_password').first(),
        'smtp_use_tls': Setting.query.filter_by(key='smtp_use_tls').first(),
        'smtp_use_ssl': Setting.query.filter_by(key='smtp_use_ssl').first(),
        'smtp_sender': Setting.query.filter_by(key='smtp_sender').first()
    }
    
    # Get current configuration
    config = {
        'mail_server': smtp_settings['smtp_server'].value if smtp_settings['smtp_server'] else app.config['MAIL_SERVER'],
        'mail_port': smtp_settings['smtp_port'].value if smtp_settings['smtp_port'] else str(app.config['MAIL_PORT']),
        'mail_username': smtp_settings['smtp_username'].value if smtp_settings['smtp_username'] else (app.config['MAIL_USERNAME'] or ''),
        'mail_password': smtp_settings['smtp_password'].value if smtp_settings['smtp_password'] else (app.config['MAIL_PASSWORD'] or ''),
        'mail_use_tls': smtp_settings['smtp_use_tls'].value == 'true' if smtp_settings['smtp_use_tls'] else app.config['MAIL_USE_TLS'],
        'mail_use_ssl': smtp_settings['smtp_use_ssl'].value == 'true' if smtp_settings['smtp_use_ssl'] else app.config['MAIL_USE_SSL'],
        'mail_sender': smtp_settings['smtp_sender'].value if smtp_settings['smtp_sender'] else app.config['MAIL_DEFAULT_SENDER'],
        'employee_emails_enabled': app.config['SEND_EMPLOYEE_EMAILS'],
        'admin_count': User.query.filter_by(role='admin').count(),
        'admin_emails': [u.email for u in User.query.filter_by(role='admin').all() if u.email]
    }

    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    ad_config = {
        'ad_enabled': get_setting_value('ad_enabled', 'false') == 'true',
        'ad_server': get_setting_value('ad_server', ''),
        'ad_port': get_setting_value('ad_port', '636'),
        'ad_use_ssl': get_setting_value('ad_use_ssl', 'true') == 'true',
        'ad_base_dn': get_setting_value('ad_base_dn', ''),
        'ad_bind_username': get_setting_value('ad_bind_username', ''),
        'ad_bind_password': get_setting_value('ad_bind_password', ''),
        'ad_user_ou_dn': get_setting_value('ad_user_ou_dn', ''),
    }

    unifi_config = {
        'host': get_setting_value('unifi_host', ''),
        'username': get_setting_value('unifi_username', ''),
        'password_set': bool(get_setting_value('unifi_password', '')),
        'site': get_setting_value('unifi_site', 'default'),
        'last_sync_status': get_setting_value('unifi_last_sync_status', ''),
        'last_sync_message': get_setting_value('unifi_last_sync_message', ''),
        'last_sync_time': get_setting_value('unifi_last_sync_time', ''),
    }

    proxmox_settings = {
        'cluster_host': get_setting_value('proxmox_cluster_host', ''),
        'cluster_token_id': get_setting_value('proxmox_cluster_token_id', ''),
        'cluster_token_set': bool(get_setting_value('proxmox_cluster_token_secret', '')),
        'cluster_verify_ssl': get_setting_value('proxmox_cluster_verify_ssl', '0'),
        'backup_host': get_setting_value('proxmox_backup_host', ''),
        'backup_token_id': get_setting_value('proxmox_backup_token_id', ''),
        'backup_token_set': bool(get_setting_value('proxmox_backup_token_secret', '')),
        'backup_verify_ssl': get_setting_value('proxmox_backup_verify_ssl', '0'),
        'stale_hours': get_setting_value('proxmox_stale_hours', '26'),
    }

    return render_template('settings.html',
                          config=config,
                          ad_config=ad_config,
                          unifi_config=unifi_config,
                          proxmox_settings=proxmox_settings)


@app.route('/api/unifi/test', methods=['POST'])
@login_required
@admin_required
def api_unifi_test():
    """Test UniFi controller connection."""
    from unifi_service import UnifiService, load_unifi_config
    config = load_unifi_config(Setting)
    if not config:
        return jsonify({'success': False, 'message': 'UniFi credentials not configured'})
    svc = UnifiService(**config)
    result = svc.test_connection()
    return jsonify(result)


@app.route('/api/unifi/sync', methods=['POST'])
@login_required
@admin_required
def api_unifi_sync():
    """Manually trigger a UniFi device sync."""
    from unifi_service import sync_unifi_assets
    try:
        summary = sync_unifi_assets(app, db, Asset, Setting, AssetHistory, MonitoringAlert)
        return jsonify({'success': True, 'summary': summary})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/ad/test', methods=['POST'])
@login_required
@admin_required
def api_ad_test():
    try:
        from ldap_service import load_ad_config, LDAPService

        cfg = load_ad_config(Setting)
        service = LDAPService(cfg)
        result = service.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ad/user/disable', methods=['POST'])
@login_required
@admin_required
def api_ad_disable_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.disable_user(username))


@app.route('/api/ad/user/enable', methods=['POST'])
@login_required
@admin_required
def api_ad_enable_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.enable_user(username))


@app.route('/api/ad/user/delete', methods=['POST'])
@login_required
@admin_required
def api_ad_delete_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.delete_user(username))


@app.route('/api/ad/user/create', methods=['POST'])
@login_required
@admin_required
def api_ad_create_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    user_principal_name = (payload.get('user_principal_name') or payload.get('upn') or '').strip()
    display_name = (payload.get('display_name') or '').strip()
    email = (payload.get('email') or '').strip() or None
    password = payload.get('password') or None
    enable = bool(payload.get('enable', True))
    ou_dn = (payload.get('ou_dn') or '').strip() or None

    if not username or not user_principal_name or not display_name:
        return jsonify({'success': False, 'error': 'username, user_principal_name, display_name are required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    result = service.create_user(
        username=username,
        user_principal_name=user_principal_name,
        display_name=display_name,
        email=email,
        password=password,
        enable=enable,
        ou_dn=ou_dn,
    )
    status = 200 if result.get('success') else 400
    return jsonify(result), status


@app.route('/api/ad/group/add-member', methods=['POST'])
@login_required
@admin_required
def api_ad_group_add_member():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    group_dn = (payload.get('group_dn') or '').strip()
    if not username or not group_dn:
        return jsonify({'success': False, 'error': 'username and group_dn are required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.add_user_to_group(username, group_dn))


@app.route('/api/ad/group/remove-member', methods=['POST'])
@login_required
@admin_required
def api_ad_group_remove_member():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    group_dn = (payload.get('group_dn') or '').strip()
    if not username or not group_dn:
        return jsonify({'success': False, 'error': 'username and group_dn are required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.remove_user_from_group(username, group_dn))

# ==================== SOC2 COMPLIANCE ====================

@app.route('/soc2')
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

@app.route('/soc2/evidence/<int:control_id>')
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

@app.route('/api/soc2/snapshot/<int:snapshot_id>')
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

@app.route('/soc2/strikegraph')
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

@app.route('/compliance/management-risk-review')
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

@app.route('/compliance/user-access-review')
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

@app.route('/compliance/vendor-risk-register')
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

@app.route('/compliance/risk-assessment-methodology')
@login_required
@admin_required
def risk_assessment_methodology():
    """Generate Risk Assessment Methodology Document"""
    from datetime import datetime
    
    review_date = datetime.now()
    return render_template('compliance/risk_assessment_methodology.html',
                         review_date=review_date)

@app.route('/compliance/employee-training-report')
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

@app.route('/compliance/employee-reporting-procedure')
@login_required
@admin_required
def employee_reporting_procedure():
    """Generate Employee Security Reporting Procedure Document"""
    from datetime import datetime
    
    review_date = datetime.now()
    return render_template('compliance/employee_reporting_procedure.html',
                         review_date=review_date)

@app.route('/api/soc2/control-evidence/<int:control_id>')
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

@app.route('/api/soc2/export/<int:control_id>')
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
        return redirect(url_for('soc2_dashboard'))

@app.route('/api/soc2/export/all')
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
        return redirect(url_for('soc2_dashboard'))


@app.route('/api/soc2/generate-evidence-files', methods=['POST'])
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


@app.route('/api/soc2/download-evidence/<int:evidence_id>')
@login_required
@license_required
def api_download_evidence(evidence_id):
    """Download a specific evidence file"""
    try:
        evidence = StrikeGraphEvidence.query.get_or_404(evidence_id)
        
        if not evidence.file_path:
            flash('Evidence file not yet generated. Click "Generate Evidence Files" first.', 'warning')
            return redirect(url_for('soc2_strikegraph'))
        
        full_path = f'/var/www/tracker/static/{evidence.file_path}'
        
        if not os.path.exists(full_path):
            flash('Evidence file not found. It may need to be regenerated.', 'danger')
            return redirect(url_for('soc2_strikegraph'))
        
        return send_file(
            full_path,
            as_attachment=True,
            download_name=os.path.basename(full_path)
        )
    except Exception as e:
        logger.error(f'Error downloading evidence: {str(e)}')
        flash(f'Error downloading evidence: {str(e)}', 'danger')
        return redirect(url_for('soc2_strikegraph'))


@app.route('/api/soc2/download-control-evidence/<int:control_id>')
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
            return redirect(url_for('soc2_strikegraph'))
        
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
        return redirect(url_for('soc2_strikegraph'))


@app.route('/api/soc2/download-all-evidence')
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
            return redirect(url_for('soc2_strikegraph'))
        
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
        return redirect(url_for('soc2_strikegraph'))


@app.route('/api/soc2/generate-software-inventory', methods=['POST'])
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


# ==================== SOFTWARE LICENSES ====================

@app.route('/licenses')
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

@app.route('/licenses/add', methods=['GET', 'POST'])
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
            return redirect(url_for('licenses'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding license: {str(e)}', 'danger')
    
    return render_template('add_license.html')

@app.route('/licenses/<int:license_id>')
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

@app.route('/licenses/<int:license_id>/edit', methods=['GET', 'POST'])
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
            return redirect(url_for('view_license', license_id=license.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating license: {str(e)}', 'danger')
    
    return render_template('edit_license.html', license=license)

@app.route('/licenses/<int:license_id>/delete', methods=['POST'])
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
    
    return redirect(url_for('licenses'))

@app.route('/licenses/<int:license_id>/assign', methods=['POST'])
@login_required
@manager_required
@license_required
def assign_license(license_id):
    """Assign license to asset or employee"""
    license = License.query.get_or_404(license_id)
    
    # Check if licenses are available
    if license.get_available_licenses() <= 0:
        flash('No available licenses to assign!', 'warning')
        return redirect(url_for('view_license', license_id=license_id))
    
    # Require employee assignment for user-based licenses
    employee_id = request.form.get('employee_id')
    if not employee_id:
        flash('Employee assignment is required for software licenses!', 'warning')
        return redirect(url_for('view_license', license_id=license_id))
    
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
    
    return redirect(url_for('view_license', license_id=license_id))

@app.route('/licenses/assignments/<int:assignment_id>/return', methods=['POST'])
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
    
    return redirect(url_for('view_license', license_id=assignment.license_id))


# ==================== SUPPORT TICKETS ====================

@app.route('/tickets')
@login_required
@manager_required
@license_required
def tickets():
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    total_closed = SupportTicket.query.filter_by(status='Closed').count()
    total_open = SupportTicket.query.filter(SupportTicket.status.in_(['Open', 'In Progress'])).count()
    return render_template('tickets.html', tickets=tickets, total_closed=total_closed, total_open=total_open)


@app.route('/tickets/new', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def new_ticket():
    if request.method == 'POST':
        subject = (request.form.get('subject') or '').strip()
        description = (request.form.get('description') or '').strip()
        if not subject or not description:
            flash('Subject and description are required.', 'danger')
            return redirect(url_for('new_ticket'))

        priority = (request.form.get('priority') or 'Normal').strip()
        if priority not in ['Low', 'Normal', 'High', 'Urgent']:
            priority = 'Normal'

        reporter_name = (request.form.get('reporter_name') or '').strip() or None
        reporter_email = (request.form.get('reporter_email') or '').strip() or None

        asset_id = request.form.get('asset_id')
        asset = Asset.query.get(int(asset_id)) if asset_id else None

        ticket = SupportTicket(
            status='Open',
            priority=priority,
            source='web',
            subject=subject,
            description=description,
            reporter_name=reporter_name,
            reporter_email=reporter_email,
            asset_id=asset.id if asset else None,
            asset_tag=asset.asset_tag if asset else None,
            hostname=asset.name if asset else None,
            created_by_user_id=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(ticket)
        db.session.commit()

        if asset:
            history = AssetHistory(
                asset_id=asset.id,
                action='Ticket Created',
                description=f'Support ticket #{ticket.id} created: {ticket.subject}',
                user_id=current_user.id
            )
            db.session.add(history)
            db.session.commit()

        flash(f'Ticket #{ticket.id} created.', 'success')
        return redirect(url_for('view_ticket', ticket_id=ticket.id))

    assets = Asset.query.order_by(Asset.asset_tag.asc()).all()
    return render_template('add_ticket.html', assets=assets)


@app.route('/tickets/<int:ticket_id>')
@login_required
@manager_required
@license_required
def view_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    techs = User.query.filter(User.role.in_(['admin', 'manager', 'viewer'])).order_by(User.display_name).all()
    # Build timeline: merge notes + activity sorted by created_at
    notes = [{'type': 'note', 'obj': n, 'ts': n.created_at} for n in ticket.notes.order_by(TicketNote.created_at).all()]
    acts = [{'type': 'activity', 'obj': a, 'ts': a.created_at} for a in ticket.activity.order_by(TicketActivity.created_at).all()]
    timeline = sorted(notes + acts, key=lambda x: x['ts'] or datetime.utcnow())
    return render_template('view_ticket.html', ticket=ticket, techs=techs, timeline=timeline)


@app.route('/tickets/<int:ticket_id>/status', methods=['POST'])
@login_required
@license_required
def set_ticket_status(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    new_status = request.form.get('status', '').strip()
    if new_status in ('Open', 'In Progress', 'Closed'):
        old = ticket.status
        ticket.status = new_status
        if new_status == 'Closed':
            ticket.closed_at = datetime.utcnow()
            ticket.closed_by_user_id = current_user.id
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='status_changed', detail=f'{old} → {new_status}'))
        db.session.commit()
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/edit', methods=['POST'])
@login_required
@manager_required
@license_required
def edit_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    ticket.subject = request.form.get('subject', ticket.subject).strip()
    ticket.description = request.form.get('description', ticket.description).strip()
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='edited', detail='Subject/description updated'))
    db.session.commit()
    flash('Ticket updated.', 'success')
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/assign', methods=['POST'])
@login_required
@manager_required
@license_required
def assign_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    assignee_id = request.form.get('assignee_id', '0')
    if assignee_id == '0':
        ticket.assigned_to_user_id = None
        detail = 'Unassigned'
    else:
        ticket.assigned_to_user_id = int(assignee_id)
        u = User.query.get(ticket.assigned_to_user_id)
        detail = f'Assigned to {u.display_name if u else assignee_id}'
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='assigned', detail=detail))
    db.session.commit()
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/category', methods=['POST'])
@login_required
@license_required
def set_ticket_category(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    cat = request.form.get('category', 'General').strip()
    ticket.category = cat
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='category_changed', detail=f'Category set to {cat}'))
    db.session.commit()
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/priority', methods=['POST'])
@login_required
@license_required
def set_ticket_priority(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    priority = request.form.get('priority', 'Normal').strip()
    if priority in ('Low', 'Normal', 'High', 'Urgent'):
        old = ticket.priority
        ticket.priority = priority
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='priority_changed', detail=f'{old} → {priority}'))
        db.session.commit()
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/note', methods=['POST'])
@login_required
@license_required
def add_ticket_note(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    content = request.form.get('content', '').strip()
    if content:
        note = TicketNote(ticket_id=ticket.id, user_id=current_user.id, content=content)
        db.session.add(note)
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='note_added', detail='Internal note added'))
        db.session.commit()
        flash('Note added.', 'success')
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/merge', methods=['POST'])
@login_required
@manager_required
@license_required
def merge_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    try:
        target_id = int(request.form.get('merge_into_id', 0))
    except (ValueError, TypeError):
        flash('Invalid target ticket ID.', 'danger')
        return redirect(url_for('view_ticket', ticket_id=ticket.id))
    target = SupportTicket.query.get(target_id)
    if not target or target.id == ticket.id:
        flash('Target ticket not found.', 'danger')
        return redirect(url_for('view_ticket', ticket_id=ticket.id))
    ticket.status = 'Merged'
    ticket.merged_into_id = target.id
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='merged', detail=f'Merged into #{target.id}'))
    db.session.add(TicketActivity(ticket_id=target.id, user_id=current_user.id,
                                  action='merged', detail=f'#{ticket.id} merged into this ticket'))
    db.session.commit()
    flash(f'Ticket #{ticket.id} merged into #{target.id}.', 'success')
    return redirect(url_for('view_ticket', ticket_id=target.id))


@app.route('/tickets/<int:ticket_id>/close', methods=['POST'])
@login_required
@manager_required
@license_required
def close_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    if ticket.status != 'Closed':
        ticket.status = 'Closed'
        ticket.closed_at = datetime.utcnow()
        ticket.closed_by_user_id = current_user.id
        db.session.commit()
    flash(f'Ticket #{ticket.id} closed.', 'success')
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/tickets/<int:ticket_id>/reopen', methods=['POST'])
@login_required
@manager_required
@license_required
def reopen_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    if ticket.status != 'Open':
        ticket.status = 'Open'
        ticket.closed_at = None
        ticket.closed_by_user_id = None
        db.session.commit()
    flash(f'Ticket #{ticket.id} reopened.', 'success')
    return redirect(url_for('view_ticket', ticket_id=ticket.id))


@app.route('/api/support-tickets', methods=['POST'])
@license_required
@require_api_key('create_tickets')
def api_create_support_ticket():
    payload = request.get_json(silent=True) or {}
    subject = (payload.get('subject') or '').strip()
    description = (payload.get('description') or '').strip()

    if not subject or not description:
        return jsonify({'error': 'subject and description are required'}), 400

    priority = (payload.get('priority') or 'Normal').strip()
    if priority not in ['Low', 'Normal', 'High', 'Urgent']:
        priority = 'Normal'

    source = (payload.get('source') or 'api').strip().lower()
    if source not in ['api', 'tray']:
        source = 'api'

    reporter_name = (payload.get('reporter_name') or '').strip() or None
    reporter_email = (payload.get('reporter_email') or '').strip() or None
    hostname = (payload.get('hostname') or '').strip() or None
    asset_tag = (payload.get('asset_tag') or '').strip() or None
    asset_id = payload.get('asset_id')

    asset = None
    if asset_id:
        try:
            asset = Asset.query.get(int(asset_id))
        except Exception:
            asset = None
    if asset is None and asset_tag:
        asset = Asset.query.filter_by(asset_tag=asset_tag).first()

    if asset:
        asset_id = asset.id
        asset_tag = asset.asset_tag
        if not hostname:
            hostname = asset.name

    created_by_user_id = getattr(request, 'api_user_id', None)
    ticket = SupportTicket(
        status='Open',
        priority=priority,
        source=source,
        subject=subject,
        description=description,
        reporter_name=reporter_name,
        reporter_email=reporter_email,
        hostname=hostname,
        asset_id=asset_id,
        asset_tag=asset_tag,
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(ticket)
    db.session.commit()

    if asset and created_by_user_id:
        history = AssetHistory(
            asset_id=asset.id,
            action='Ticket Created',
            description=f'Support ticket #{ticket.id} created via API: {ticket.subject}',
            user_id=created_by_user_id
        )
        db.session.add(history)
        db.session.commit()

    return jsonify({
        'success': True,
        'ticket_id': ticket.id,
        'ticket_url': url_for('view_ticket', ticket_id=ticket.id, _external=True),
        'status': ticket.status,
    })


# ==================== MONITORING PROFILES & CHECKS ====================

@app.route('/monitoring')
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


@app.route('/monitoring/profiles')
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


@app.route('/monitoring/profile/<int:profile_id>')
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
    
    return render_template('monitoring_profile_detail.html',
                         profile=profile,
                         checks=checks,
                         assets=assets)

@app.route('/agent/download')
def agent_download():
    """Serve the Linux agent Python script for download"""
    agent_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'agent.py')
    return send_file(agent_path, as_attachment=True, download_name='cirque-rmm-agent')

@app.route('/agent/install.sh')
def agent_install_script():
    """Serve the agent installer script"""
    script_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'install.sh')
    return send_file(script_path, mimetype='text/x-shellscript')

@app.route('/agent/service')
def agent_service_file():
    """Serve the systemd service file"""
    service_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'cirque-rmm-agent.service')
    return send_file(service_path, mimetype='text/plain')


@app.route('/monitoring/assign/<int:asset_id>', methods=['POST'])
@login_required
def monitoring_assign_profile(asset_id):
    """Assign a monitoring profile to an asset"""
    asset = Asset.query.get_or_404(asset_id)
    profile_id = request.form.get('profile_id')
    
    if not profile_id:
        flash('Please select a monitoring profile', 'warning')
        return redirect(request.referrer or url_for('monitoring_dashboard'))
    
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
    
    return redirect(request.referrer or url_for('monitoring_dashboard'))


@app.route('/monitoring/unassign/<int:asset_id>', methods=['POST'])
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
    
    return redirect(request.referrer or url_for('monitoring_dashboard'))


@app.route('/monitoring/alerts')
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


@app.route('/monitoring/alert/<int:alert_id>/acknowledge', methods=['POST'])
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
    
    return redirect(request.referrer or url_for('monitoring_alerts'))


@app.route('/monitoring/alert/<int:alert_id>/resolve', methods=['POST'])
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
    
    return redirect(request.referrer or url_for('monitoring_alerts'))


@app.route('/monitoring/maintenance-windows')
@login_required
def monitoring_maintenance_windows():
    """View and manage maintenance windows"""
    windows = MaintenanceWindow.query.order_by(MaintenanceWindow.day_of_week, MaintenanceWindow.start_time).all()
    
    return render_template('monitoring_maintenance_windows.html', windows=windows)


# ==================== PROXMOX / BACKUPS ====================

@app.route('/backups')
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

    return render_template(
        'backups.html',
        pools=pools, jobs=jobs,
        summary=summary,
        stale_hours=stale_hours,
        cluster_configured=cluster_configured,
        last_sync=last_sync,
        now=datetime.utcnow(),
    )


@app.route('/api/proxmox/sync', methods=['POST'])
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


@app.route('/api/proxmox/test', methods=['POST'])
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


@app.route('/api/proxmox/settings', methods=['POST'])
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


# ==================== SSH TERMINAL ====================

from ssh_terminal_manager import get_ssh_manager

@app.route('/terminal/<int:asset_id>')
@login_required
def terminal(asset_id):
    """Web-based SSH terminal for an asset"""
    asset = Asset.query.get_or_404(asset_id)
    return render_template('terminal.html', asset=asset)

@app.route('/api/terminal/connect', methods=['POST'])
@login_required
def api_terminal_connect():
    """Connect to an asset via SSH"""
    data = request.get_json()
    
    asset_id = data.get('asset_id')
    username = data.get('username', 'root')
    password = data.get('password')
    
    asset = Asset.query.get_or_404(asset_id)
    
    # Generate session ID
    import uuid
    session_id = f"{current_user.id}:{asset_id}:{str(uuid.uuid4())[:8]}"
    
    # Create SSH session
    ssh_manager = get_ssh_manager()
    session = ssh_manager.create_session(
        session_id=session_id,
        hostname=asset.ip_address_1,
        username=username,
        password=password,
        port=22
    )
    
    if session:
        return jsonify({
            'success': True,
            'session_id': session_id
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to connect'
        }), 500

@app.route('/api/terminal/input', methods=['POST'])
@login_required
def api_terminal_input():
    """Send input to SSH session"""
    data = request.get_json()
    
    session_id = data.get('session_id')
    input_data = data.get('data', '')
    
    ssh_manager = get_ssh_manager()
    session = ssh_manager.get_session(session_id)
    
    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    if session.send_input(input_data):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to send input'}), 500

@app.route('/api/terminal/output', methods=['POST'])
@login_required
def api_terminal_output():
    """Get output from SSH session"""
    data = request.get_json()
    
    session_id = data.get('session_id')
    since_index = data.get('since_index', 0)
    
    ssh_manager = get_ssh_manager()
    session = ssh_manager.get_session(session_id)
    
    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    output = session.get_output(since_index)
    
    return jsonify({
        'success': True,
        'output': output,
        'index': since_index + len(output)
    })

@app.route('/api/terminal/disconnect', methods=['POST'])
@login_required
def api_terminal_disconnect():
    """Disconnect SSH session"""
    data = request.get_json()
    
    session_id = data.get('session_id')
    
    ssh_manager = get_ssh_manager()
    if ssh_manager.close_session(session_id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Session not found'}), 404


# ==================== LINUX AGENT API ====================

@app.route('/api/linux-agent/heartbeat', methods=['POST'])
def api_linux_agent_heartbeat():
    """Receive heartbeat from Linux monitoring agent"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not api_key or api_key != app.config.get('LINUX_AGENT_API_KEY', 'change-me-in-production'):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    data = request.get_json()
    
    try:
        agent_id = data.get('agent_id')
        asset_id = data.get('asset_id')
        system_info = data.get('system_info', {})
        metrics = data.get('metrics', {})
        disks = data.get('disks', [])
        services_running = data.get('services_running', 0)
        updates = data.get('updates', {})
        
        # Save heartbeat to database
        cursor = db.session.execute(text("""
            INSERT INTO linux_agent_heartbeat 
            (agent_id, asset_id, system_info, metrics, disks, services_running, 
             updates_available, security_updates, timestamp)
            VALUES (:agent_id, :asset_id, :system_info, :metrics, :disks, 
                    :services_running, :updates_available, :security_updates, :timestamp)
        """), {
            'agent_id': agent_id,
            'asset_id': asset_id,
            'system_info': json.dumps(system_info),
            'metrics': json.dumps(metrics),
            'disks': json.dumps(disks),
            'services_running': services_running,
            'updates_available': updates.get('available', 0),
            'security_updates': updates.get('security', 0),
            'timestamp': datetime.utcnow()
        })
        db.session.commit()
        
        # If asset_id provided, update last seen
        if asset_id:
            cursor = db.session.execute(text("""
                UPDATE assets 
                SET last_seen = :last_seen 
                WHERE id = :asset_id
            """), {
                'last_seen': datetime.utcnow(),
                'asset_id': asset_id
            })
            db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Heartbeat received',
            'timestamp': datetime.utcnow().isoformat()
        })
    
    except Exception as e:
        print(f"Error processing agent heartbeat: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/linux-agent/checks', methods=['GET'])
def api_linux_agent_checks():
    """Get checks that Linux agent should execute"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not api_key or api_key != app.config.get('LINUX_AGENT_API_KEY', 'change-me-in-production'):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    asset_id = request.args.get('asset_id')
    if not asset_id:
        return jsonify({'success': False, 'error': 'asset_id required'}), 400
    
    try:
        # Get checks for this asset's profile
        result = db.session.execute(text("""
            SELECT 
                mc.id, mc.name, mc.check_type, mc.check_command,
                mc.warning_threshold, mc.critical_threshold, 
                mc.check_interval_minutes
            FROM monitoring_check mc
            JOIN profile_check pc ON mc.id = pc.check_id
            JOIN asset_monitoring_profile amp ON pc.profile_id = amp.profile_id
            WHERE amp.asset_id = :asset_id AND mc.enabled = 1
        """), {'asset_id': asset_id})
        
        checks = []
        for row in result:
            checks.append({
                'id': row[0],
                'name': row[1],
                'type': row[2],
                'command': row[3],
                'warning_threshold': row[4],
                'critical_threshold': row[5],
                'interval_minutes': row[6]
            })
        
        return jsonify({
            'success': True,
            'checks': checks
        })
    
    except Exception as e:
        print(f"Error getting agent checks: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/linux-agent/check-result', methods=['POST'])
def api_linux_agent_check_result():
    """Receive check result from Linux agent"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not api_key or api_key != app.config.get('LINUX_AGENT_API_KEY', 'change-me-in-production'):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    data = request.get_json()
    
    try:
        asset_id = data.get('asset_id')
        check_id = data.get('check_id')
        status = data.get('status')
        value = data.get('value')
        message = data.get('message')
        
        # Save check result
        cursor = db.session.execute(text("""
            INSERT INTO monitoring_check_result 
            (asset_id, check_id, status, value, message, checked_at)
            VALUES (:asset_id, :check_id, :status, :value, :message, :checked_at)
        """), {
            'asset_id': asset_id,
            'check_id': check_id,
            'status': status,
            'value': str(value) if value is not None else None,
            'message': message,
            'checked_at': datetime.utcnow()
        })
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Check result saved'
        })
    
    except Exception as e:
        print(f"Error saving check result: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== RMM AGENT DOWNLOAD ====================

@app.route('/rmm/agent-download')
@login_required
@admin_required
@license_required
def rmm_agent_download():
    """Serve the RMM agent folder as a zip for admins to push to endpoints."""
    import zipfile

    agent_dir = os.path.join(os.path.dirname(__file__), 'rmm_agent')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in ['agent_client.py', 'requirements.txt', 'install_service.ps1', 'README.md']:
            fpath = os.path.join(agent_dir, fname)
            if os.path.exists(fpath):
                zf.write(fpath, arcname=fname)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name='CirqueRMM-agent.zip',
    )


RMM_GATEWAY_INTERNAL = os.environ.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
RMM_GATEWAY_PUBLIC   = os.environ.get('RMM_GATEWAY_URL', 'wss://rmm.corp.cirque.com')
RMM_TRACKER_URL      = os.environ.get('RMM_TRACKER_URL', 'https://tracker.corp.cirque.com')


def _verify_agent_token(agent_id: str, token: str) -> bool:
    """Check agent_id + token against the rmm_agent table (SHA-256 comparison)."""
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = db.session.execute(
        text("SELECT id FROM rmm_agent WHERE agent_id = :aid AND agent_token_sha256 = :h AND enabled = 1"),
        {'aid': agent_id, 'h': token_hash}
    ).fetchone()
    return row is not None


@app.route('/rmm/agent/version')
def rmm_agent_version():
    """Return current agent version + SHA-256 of agent_client.py.
    Authenticated by agent_id + token query params (agent calls this on startup).
    """
    agent_id = request.args.get('agent_id', '')
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401

    import hashlib
    agent_dir = os.path.join(os.path.dirname(__file__), 'rmm_agent')
    version_path = os.path.join(agent_dir, 'version.txt')
    agent_path = os.path.join(agent_dir, 'agent_client.py')

    version = '0.0.0'
    if os.path.exists(version_path):
        version = open(version_path).read().strip()

    checksum = ''
    if os.path.exists(agent_path):
        checksum = hashlib.sha256(open(agent_path, 'rb').read()).hexdigest()

    return jsonify({'version': version, 'checksum': checksum})


@app.route('/rmm/agent/file')
def rmm_agent_file():
    """Serve agent_client.py for self-update. Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401

    agent_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'agent_client.py')
    return send_file(agent_path, mimetype='text/x-python', as_attachment=False)


@app.route('/api/rmm/agent-status/<agent_id>')
@login_required
def api_rmm_agent_status(agent_id):
    """Proxy the gateway /status/<agent_id>, falling back to DB last_seen_at recency."""
    try:
        resp = requests.get(f'{RMM_GATEWAY_INTERNAL}/status/{agent_id}', timeout=3)
        data = resp.json()
        if data.get('online'):
            return jsonify(data)
    except Exception:
        pass
    # Gateway doesn't have a live WS for HTTP-based agents — check DB freshness instead
    row = db.session.execute(
        text("SELECT last_seen_at FROM rmm_agent WHERE agent_id = :aid AND enabled = 1"),
        {'aid': agent_id}
    ).fetchone()
    if row and row[0]:
        last = datetime.fromisoformat(str(row[0]))
        online = (datetime.utcnow() - last).total_seconds() < 300  # 5-minute window
        return jsonify({'agent_id': agent_id, 'online': online, 'source': 'db'})
    return jsonify({'agent_id': agent_id, 'online': False})


@app.route('/api/rmm/issue-token', methods=['POST'])
@login_required
def api_rmm_issue_token():
    """Issue a short-lived connect token so the browser terminal can auth with the gateway."""
    data = request.get_json(force=True) or {}
    agent_id = data.get('agent_id', '').strip()
    if not agent_id:
        return jsonify({'error': 'agent_id required'}), 400

    token = 'rct_' + secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=5)).isoformat(timespec='seconds')

    db.session.execute(text(
        "INSERT INTO rmm_connect_token (token, agent_id, user_id, expires_at) VALUES (:t, :a, :u, :e)"
    ), {'t': token, 'a': agent_id, 'u': current_user.id, 'e': expires_at})
    db.session.commit()

    return jsonify({
        'token': token,
        'agent_id': agent_id,
        'gateway_url': RMM_GATEWAY_PUBLIC,
        'expires_at': expires_at,
    })


@app.route('/rmm/terminal/<agent_id>')
@login_required
def rmm_terminal(agent_id):
    """Full-page xterm.js terminal for a connected RMM agent."""
    # Look up asset name from agent record
    row = db.session.execute(text(
        "SELECT a.name, a.id FROM rmm_agent ra LEFT JOIN asset a ON a.id = ra.asset_id WHERE ra.agent_id = :aid"
    ), {'aid': agent_id}).fetchone()
    asset_name = row[0] if row else agent_id
    asset_id   = row[1] if row else None
    return render_template('rmm_terminal.html',
        agent_id=agent_id,
        asset_name=asset_name,
        asset_id=asset_id,
        gateway_url=RMM_GATEWAY_PUBLIC,
    )


@app.route('/api/rmm/telemetry/<agent_id>')
@login_required
def api_rmm_telemetry(agent_id):
    """Return latest saved telemetry for an agent."""
    import json as _json
    row = db.session.execute(
        text("SELECT * FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'No telemetry yet'}), 404
    d = dict(row._mapping)
    try:
        d['disk_json'] = _json.loads(d.get('disk_json') or '[]')
    except Exception:
        d['disk_json'] = []
    try:
        d['network_json'] = _json.loads(d.get('network_json') or '[]')
    except Exception:
        d['network_json'] = []
    return jsonify({'ok': True, 'telemetry': d})




# ── Eagle Eyes ────────────────────────────────────────────────────────────────

@app.route('/api/rmm/eagle-eyes/<agent_id>', methods=['GET', 'POST'])
@login_required
def api_rmm_eagle_eyes(agent_id):
    """GET: return current Eagle Eyes config.  POST: enable/disable and push to agent."""
    import json as _json
    if request.method == 'GET':
        row = db.session.execute(
            text("SELECT enabled, screenshot_interval_min FROM rmm_eagle_config WHERE agent_id = :aid"),
            {'aid': agent_id}
        ).fetchone()
        if row:
            return jsonify({'ok': True, 'enabled': bool(row[0]), 'screenshot_interval_min': row[1]})
        return jsonify({'ok': True, 'enabled': False, 'screenshot_interval_min': 30})

    # POST — update config and push to gateway
    data = request.get_json(force=True) or {}
    enabled  = bool(data.get('enabled', False))
    interval = int(data.get('screenshot_interval_min', 30))
    db.session.execute(
        text("""INSERT INTO rmm_eagle_config (agent_id, enabled, screenshot_interval_min, updated_at)
                VALUES (:aid, :en, :iv, datetime('now', '-7 hours'))
                ON CONFLICT(agent_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    screenshot_interval_min = excluded.screenshot_interval_min,
                    updated_at = excluded.updated_at"""),
        {'aid': agent_id, 'en': 1 if enabled else 0, 'iv': interval}
    )
    db.session.commit()
    # Push config to connected agent via gateway
    try:
        import urllib.request as _ur
        payload = _json.dumps({'enabled': enabled, 'screenshot_interval_min': interval}).encode()
        req = _ur.Request(
            f"{RMM_GATEWAY_INTERNAL}/eagle-eyes/{agent_id}/push",
            data=payload, headers={'Content-Type': 'application/json'}, method='POST',
        )
        with _ur.urlopen(req, timeout=4) as r:
            _json.loads(r.read())
    except Exception:
        pass  # agent may not be connected; config is persisted so it applies on next connect
    return jsonify({'ok': True, 'enabled': enabled, 'screenshot_interval_min': interval})


import re as _re_tz

def _agent_tz_offset_minutes(agent_id: str) -> int:
    """Return the agent's UTC offset in minutes by reading stored timezone string."""
    row = db.session.execute(
        text("SELECT timezone FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    tz_str = (row[0] or '') if row else ''
    m = _re_tz.search(r'\(UTC([+-])(\d+):(\d+)\)', tz_str)
    if not m:
        return 0
    sign = 1 if m.group(1) == '+' else -1
    return sign * (int(m.group(2)) * 60 + int(m.group(3)))


def _eagle_date_params(default_days: int = 7) -> tuple:
    """Return (where_clause, params_dict) for date filtering Eagle Eyes queries.
    All timestamps stored in MST. UI dates are MST. No conversion needed."""
    from_date = request.args.get('from_date', '').strip()
    to_date   = request.args.get('to_date', '').strip()
    if from_date and to_date:
        return (
            "captured_at >= :from_date AND captured_at < datetime(:to_date, '+1 day')",
            {'from_date': from_date, 'to_date': to_date}
        )
    days = int(request.args.get('days', default_days))
    # datetime('now','-7 hours') = current MST time (server clock is UTC)
    return ("captured_at >= datetime('now', '-7 hours', :since)", {'since': f'-{days} days'})


@app.route('/api/rmm/eagle-eyes/<agent_id>/events')
@login_required
def api_rmm_eagle_events(agent_id):
    """Return Eagle Eyes window events. Query params: days/from_date/to_date, limit."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    limit = int(request.args.get('limit', 500))
    rows = db.session.execute(
        text(f"""SELECT captured_at, process_name, window_title, duration_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                ORDER BY captured_at DESC
                LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit, **date_params}
    ).fetchall()
    events = [{'captured_at': r[0], 'process_name': r[1], 'window_title': r[2], 'duration_s': r[3]} for r in rows]
    return jsonify({'ok': True, 'events': events})


@app.route('/api/rmm/eagle-eyes/<agent_id>/app-summary')
@login_required
def api_rmm_eagle_app_summary(agent_id):
    """Return total time per process for the requested day range."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    rows = db.session.execute(
        text(f"""SELECT process_name,
                       COUNT(*) as events,
                       SUM(duration_s) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                GROUP BY process_name
                ORDER BY total_s DESC
                LIMIT 30"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    summary = [{'process_name': r[0], 'events': r[1], 'total_s': r[2] or 0} for r in rows]
    return jsonify({'ok': True, 'summary': summary})


@app.route('/api/rmm/eagle-eyes/<agent_id>/hourly')
@login_required
def api_rmm_eagle_hourly(agent_id):
    """Return total active seconds per hour-of-day (0-23) grouped in agent local time."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    tz_adj = f'{_agent_tz_offset_minutes(agent_id)} minutes'
    rows = db.session.execute(
        text(f"""SELECT CAST(strftime('%H', datetime(captured_at, :tz)) AS INTEGER) as hr,
                       SUM(COALESCE(duration_s, 0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                GROUP BY hr ORDER BY hr"""),
        {'aid': agent_id, 'tz': tz_adj, **date_params}
    ).fetchall()
    by_hour = {r[0]: r[1] or 0 for r in rows}
    result = [{'hour': h, 'total_s': by_hour.get(h, 0)} for h in range(24)]
    return jsonify({'ok': True, 'hourly': result})


@app.route('/api/rmm/eagle-eyes/<agent_id>/daily')
@login_required
def api_rmm_eagle_daily(agent_id):
    """Return total active seconds per calendar day grouped in agent local time."""
    date_clause, date_params = _eagle_date_params(default_days=30)
    tz_adj = f'{_agent_tz_offset_minutes(agent_id)} minutes'
    rows = db.session.execute(
        text(f"""SELECT date(captured_at, :tz) as day,
                       SUM(COALESCE(duration_s, 0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                GROUP BY day ORDER BY day"""),
        {'aid': agent_id, 'tz': tz_adj, **date_params}
    ).fetchall()
    result = [{'day': r[0], 'total_s': r[1] or 0} for r in rows]
    return jsonify({'ok': True, 'daily': result})


@app.route('/api/rmm/eagle-eyes/<agent_id>/top-sites')
@login_required
def api_rmm_eagle_top_sites(agent_id):
    """Return top browser sites derived from window titles."""
    import re as _re_site
    date_clause, date_params = _eagle_date_params(default_days=7)
    rows = db.session.execute(
        text(f"""SELECT window_title, SUM(COALESCE(duration_s,0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                  AND LOWER(process_name) IN ('msedge','chrome','firefox','brave','opera','iexplore','safari')
                  AND window_title IS NOT NULL AND window_title != ''
                GROUP BY window_title ORDER BY total_s DESC LIMIT 200"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    # Strip browser name suffixes then take last " - " segment as site name
    strip_re = _re_site.compile(
        r'\s*[-\u2013|]\s*(Google Chrome|Microsoft\u200b?\s*Edge|Mozilla Firefox'
        r'|Brave|Opera|Work\s*[-\u2013]\s*Microsoft\u200b?\s*Edge).*$'
        r'|( and \d+ more pages.*$)', _re_site.IGNORECASE
    )
    agg: dict = {}
    for title, total_s in rows:
        t = strip_re.sub('', title or '').strip()
        parts = _re_site.split(r'\s+[-\u2013|]\s+', t)
        site = (parts[-1] if len(parts) >= 2 else parts[0]).strip()
        if not site or site.lower() in ('new tab', 'about:blank', ''):
            continue
        agg[site] = agg.get(site, 0) + (total_s or 0)
    result = sorted([{'site': k, 'total_s': v} for k, v in agg.items()], key=lambda x: -x['total_s'])[:15]
    return jsonify({'ok': True, 'sites': result})


@app.route('/api/rmm/eagle-eyes/<agent_id>/screenshots')
@login_required
def api_rmm_eagle_screenshots(agent_id):
    """Return Eagle Eyes screenshots metadata (no image data) for the gallery."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    limit = int(request.args.get('limit', 200))
    rows = db.session.execute(
        text(f"""SELECT id, captured_at, width, height, image_format
                FROM rmm_screenshot
                WHERE agent_id = :aid AND source = 'eagle' AND {date_clause}
                ORDER BY id DESC LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit, **date_params}
    ).fetchall()
    shots = [{'id': r[0], 'time': r[1], 'width': r[2], 'height': r[3], 'format': r[4]} for r in rows]
    return jsonify({'ok': True, 'screenshots': shots})


@app.route('/api/rmm/eagle-eyes/screenshot/<int:shot_id>')
@login_required
def api_rmm_eagle_screenshot_image(shot_id):
    """Return a single Eagle Eyes screenshot including the base64 image."""
    import base64 as _b64, os as _os
    row = db.session.execute(
        text("SELECT agent_id, image_b64, image_format, width, height, captured_at, file_path FROM rmm_screenshot WHERE id = :id"),
        {'id': shot_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    b64 = row[1]
    if not b64 and row[6] and _os.path.exists(row[6]):
        with open(row[6], 'rb') as fh:
            b64 = _b64.b64encode(fh.read()).decode()
    return jsonify({'ok': True, 'screenshot': {
        'id': shot_id, 'agent_id': row[0], 'data': b64,
        'format': row[2], 'width': row[3], 'height': row[4], 'time': row[5],
    }})


@app.route('/api/rmm/eagle-eyes/screenshot/<int:shot_id>/download')
@login_required
def api_rmm_eagle_screenshot_download(shot_id):
    """Download a screenshot as an image file attachment."""
    import base64 as _b64, io, os as _os
    from flask import send_file
    row = db.session.execute(
        text("SELECT agent_id, image_b64, image_format, captured_at, file_path FROM rmm_screenshot WHERE id = :id"),
        {'id': shot_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    agent_id, b64_data, fmt, captured_at, file_path = row
    ts = (captured_at or 'unknown').replace(':', '').replace(' ', '_').replace('T', '_')
    fname = f"{agent_id}_{ts}.{fmt or 'jpeg'}"
    if file_path and _os.path.exists(file_path):
        return send_file(file_path, mimetype=f'image/{fmt or "jpeg"}',
                         as_attachment=True, download_name=fname)
    if b64_data:
        buf = io.BytesIO(_b64.b64decode(b64_data))
        return send_file(buf, mimetype=f'image/{fmt or "jpeg"}',
                         as_attachment=True, download_name=fname)
    return jsonify({'ok': False, 'error': 'Image data not available'}), 404


@app.route('/rmm/eagle-eyes/<agent_id>')
@login_required
def rmm_eagle_eyes_dashboard(agent_id):
    """Eagle Eyes dashboard page for a specific agent."""
    row = db.session.execute(
        text("""SELECT ra.asset_id, COALESCE(a.name, ra.agent_id)
                FROM rmm_agent ra
                LEFT JOIN asset a ON ra.asset_id = a.id
                WHERE ra.agent_id = :aid COLLATE NOCASE"""),
        {'aid': agent_id}
    ).fetchone()
    hostname     = row[1] if row else agent_id
    asset_id_num = row[0] if row else None
    # Timezone: read from telemetry, parse '(UTC-07:00) Mountain Time...'
    tz_row = db.session.execute(
        text("SELECT timezone FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    tz_str = (tz_row[0] or '') if tz_row else ''
    tz_offset_h = 0.0
    tz_label    = 'UTC'
    m_tz = _re_tz.search(r'\(UTC([+-])(\d+):(\d+)\)', tz_str)
    if m_tz:
        sign = 1 if m_tz.group(1) == '+' else -1
        tz_offset_h = sign * (int(m_tz.group(2)) + int(m_tz.group(3)) / 60)
        # Friendly abbreviation lookup keyed by (TZ keyword in display name, offset)
        _tz_abbr = {
            ('Mountain', -7): 'MST', ('Mountain', -6): 'MDT',
            ('Eastern',  -5): 'EST', ('Eastern',  -4): 'EDT',
            ('Central',  -6): 'CST', ('Central',  -5): 'CDT',
            ('Pacific',  -8): 'PST', ('Pacific',  -7): 'PDT',
            ('Alaska',   -9): 'AKST',('Alaska',   -8): 'AKDT',
            ('Hawaii',  -10): 'HST',
            ('Atlantic', -4): 'AST', ('Atlantic', -3): 'ADT',
        }
        off_int = int(tz_offset_h)  # exact only for :00 zones
        tz_label = next(
            (abbr for (kw, off), abbr in _tz_abbr.items() if kw in tz_str and off == off_int),
            f"UTC{m_tz.group(1)}{int(m_tz.group(2))}" if int(m_tz.group(3)) == 0
            else f"UTC{m_tz.group(1)}{int(m_tz.group(2))}:{m_tz.group(3)}"
        )
    return render_template('eagle_eyes.html', agent_id=agent_id, hostname=hostname,
                           asset_id_num=asset_id_num,
                           tz_offset_h=tz_offset_h, tz_label=tz_label)


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Live current state
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/rmm/eagle-eyes/<agent_id>/current')
@login_required
def api_eagle_current(agent_id):
    try:
        row = db.session.execute(
            text("SELECT process_name, window_title, idle_s, is_idle, captured_at FROM rmm_eagle_current WHERE agent_id = :aid"),
            {"aid": agent_id}
        ).mappings().fetchone()
        if row:
            return jsonify(ok=True, current=dict(row))
        return jsonify(ok=True, current=None)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Focus Sessions (consecutive uninterrupted blocks per app)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/rmm/eagle-eyes/<agent_id>/focus-sessions')
@login_required
def api_eagle_focus_sessions(agent_id):
    date_clause, date_params = _eagle_date_params(default_days=7)
    try:
        rows = db.session.execute(
            text(f"""
                SELECT process_name, window_title, duration_s, captured_at
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                ORDER BY captured_at
            """),
            {"aid": agent_id, **date_params}
        ).mappings().fetchall()
        # Group consecutive events on the same process into focus sessions
        sessions = []
        FOCUS_MIN_S = 600   # only surface sessions ≥ 10 min
        BREAK_S     = 120   # gap ≥ 2 min breaks the session
        if rows:
            cur_proc  = rows[0]['process_name']
            cur_title = rows[0]['window_title']
            cur_start = rows[0]['captured_at']
            cur_dur   = rows[0]['duration_s'] or 0
            for r in rows[1:]:
                if r['process_name'] == cur_proc:
                    cur_dur += r['duration_s'] or 0
                else:
                    if cur_dur >= FOCUS_MIN_S:
                        sessions.append({'process_name': cur_proc, 'window_title': cur_title,
                                         'started_at': cur_start, 'duration_s': cur_dur})
                    cur_proc  = r['process_name']
                    cur_title = r['window_title']
                    cur_start = r['captured_at']
                    cur_dur   = r['duration_s'] or 0
            if cur_dur >= FOCUS_MIN_S:
                sessions.append({'process_name': cur_proc, 'window_title': cur_title,
                                 'started_at': cur_start, 'duration_s': cur_dur})
        sessions.sort(key=lambda s: s['duration_s'], reverse=True)
        return jsonify(ok=True, sessions=sessions[:50])
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — App Classifications
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/rmm/eagle-eyes/app-classifications', methods=['GET', 'POST'])
@login_required
def api_eagle_app_classifications():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, process_pattern, label, productivity, created_at FROM rmm_eagle_app_class ORDER BY process_pattern")
            ).mappings().fetchall()
            return jsonify(ok=True, classifications=[dict(r) for r in rows])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    # POST — add or update
    data = request.get_json() or {}
    pattern = (data.get('process_pattern') or '').strip().lower()
    label   = (data.get('label') or '').strip()
    prod    = (data.get('productivity') or 'neutral').strip()
    if not pattern:
        return jsonify(ok=False, error='process_pattern required')
    if prod not in ('productive','unproductive','neutral'):
        return jsonify(ok=False, error='Invalid productivity value')
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_app_class (process_pattern, label, productivity, created_at)
            VALUES (:p, :l, :pr, :ca)
            ON CONFLICT(process_pattern) DO UPDATE SET label=excluded.label, productivity=excluded.productivity
        """), {'p': pattern, 'l': label, 'pr': prod, 'ca': datetime.utcnow().isoformat()})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route('/api/rmm/eagle-eyes/app-classifications/<int:cid>', methods=['DELETE'])
@login_required
def api_eagle_app_class_delete(cid):
    try:
        db.session.execute(text("DELETE FROM rmm_eagle_app_class WHERE id = :id"), {'id': cid})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Alert Rules
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/rmm/eagle-eyes/alerts', methods=['GET', 'POST'])
@login_required
def api_eagle_alerts():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, agent_id, alert_type, threshold, process_pattern, email_notify, enabled, last_fired_at, created_at FROM rmm_eagle_alert_rule ORDER BY id DESC")
            ).mappings().fetchall()
            logs = db.session.execute(
                text("SELECT rule_id, agent_id, message, fired_at FROM rmm_eagle_alert_log ORDER BY fired_at DESC LIMIT 50")
            ).mappings().fetchall()
            return jsonify(ok=True, rules=[dict(r) for r in rows], log=[dict(l) for l in logs])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    alert_type = (data.get('alert_type') or '').strip()
    if alert_type not in ('productivity_below','app_used','idle_over','unproductive_app'):
        return jsonify(ok=False, error='Invalid alert_type')
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_alert_rule (agent_id, alert_type, threshold, process_pattern, email_notify, enabled, created_at)
            VALUES (:aid, :at, :th, :pp, :en, 1, :ca)
        """), {
            'aid': data.get('agent_id') or None,
            'at':  alert_type,
            'th':  data.get('threshold') or None,
            'pp':  (data.get('process_pattern') or '').strip().lower() or None,
            'en':  1 if data.get('email_notify', True) else 0,
            'ca':  datetime.utcnow().isoformat()
        })
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route('/api/rmm/eagle-eyes/alerts/<int:rid>', methods=['PUT', 'DELETE'])
@login_required
def api_eagle_alert_rule(rid):
    if request.method == 'DELETE':
        try:
            db.session.execute(text("DELETE FROM rmm_eagle_alert_rule WHERE id = :id"), {'id': rid})
            db.session.commit()
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    try:
        db.session.execute(text("""
            UPDATE rmm_eagle_alert_rule SET
              enabled=:en, threshold=:th, email_notify=:email
            WHERE id=:id
        """), {'en': 1 if data.get('enabled',True) else 0, 'th': data.get('threshold'), 'email': 1 if data.get('email_notify',True) else 0, 'id': rid})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Report Schedules
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/rmm/eagle-eyes/report-schedules', methods=['GET', 'POST', 'DELETE'])
@login_required
def api_eagle_report_schedules():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, agent_id, frequency, day_of_week, send_time, email_to, last_sent_at, enabled, created_at FROM rmm_eagle_report_schedule ORDER BY id DESC")
            ).mappings().fetchall()
            return jsonify(ok=True, schedules=[dict(r) for r in rows])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    if request.method == 'DELETE':
        sid = request.args.get('id')
        try:
            db.session.execute(text("DELETE FROM rmm_eagle_report_schedule WHERE id = :id"), {'id': sid})
            db.session.commit()
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_report_schedule (agent_id, frequency, day_of_week, send_time, email_to, enabled, created_at)
            VALUES (:aid, :freq, :dow, :st, :email, 1, :ca)
        """), {
            'aid':   data.get('agent_id') or None,
            'freq':  data.get('frequency','weekly'),
            'dow':   data.get('day_of_week', 1),
            'st':    data.get('send_time','08:00'),
            'email': data.get('email_to',''),
            'ca':    datetime.utcnow().isoformat()
        })
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Eagle Eyes — Multi-agent comparison page + data
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/rmm/eagle-eyes/compare')
@login_required
def rmm_eagle_compare():
    agents = db.session.execute(
        text("""
            SELECT a.agent_id, COALESCE(t.hostname, a.agent_id) as hostname
            FROM rmm_agent a
            LEFT JOIN rmm_telemetry t ON t.agent_id = a.agent_id
            WHERE a.enabled = 1
            ORDER BY hostname
        """)
    ).mappings().fetchall()
    return render_template('compare_agents.html', agents=[dict(a) for a in agents])


@app.route('/api/rmm/eagle-eyes/compare-data')
@login_required
def api_eagle_compare_data():
    agent_ids = request.args.get('agents','').split(',')
    agent_ids = [a.strip() for a in agent_ids if a.strip()]
    days      = int(request.args.get('days', 7))
    if not agent_ids:
        return jsonify(ok=False, error='No agents specified')
    results = {}
    for aid in agent_ids:
        try:
            summary = db.session.execute(text(f"""
                SELECT process_name, SUM(duration_s) as total_s, COUNT(*) as events
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND captured_at >= datetime('now','-{days} days')
                GROUP BY process_name ORDER BY total_s DESC LIMIT 10
            """), {'aid': aid}).mappings().fetchall()
            daily = db.session.execute(text(f"""
                SELECT DATE(captured_at) as day, SUM(duration_s) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND captured_at >= datetime('now','-{days} days')
                GROUP BY day ORDER BY day
            """), {'aid': aid}).mappings().fetchall()
            hostname = db.session.execute(
                text("SELECT hostname FROM rmm_telemetry WHERE agent_id = :aid LIMIT 1"), {'aid': aid}
            ).scalar() or aid
            results[aid] = {
                'hostname': hostname,
                'summary':  [dict(r) for r in summary],
                'daily':    [dict(r) for r in daily],
                'total_s':  sum(r['total_s'] or 0 for r in summary),
            }
        except Exception as e:
            results[aid] = {'hostname': aid, 'error': str(e)}
    return jsonify(ok=True, results=results, days=days)


@app.route('/api/rmm/eagle-eyes/<agent_id>/gantt')
@login_required
def api_eagle_gantt(agent_id):
    """Return events for a specific day as a gantt-ready list."""
    day = request.args.get('day')  # YYYY-MM-DD
    if not day:
        from datetime import date
        day = date.today().isoformat()
    try:
        rows = db.session.execute(text("""
            SELECT process_name, window_title, duration_s, idle_s, captured_at
            FROM rmm_eagle_event
            WHERE agent_id = :aid
              AND DATE(captured_at) = :day
            ORDER BY captured_at
        """), {'aid': agent_id, 'day': day}).mappings().fetchall()
        return jsonify(ok=True, day=day, events=[dict(r) for r in rows])
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route('/api/rmm/screenshot/<agent_id>', methods=['POST'])
@login_required
def api_rmm_screenshot_request(agent_id):
    """Ask the gateway to request a screenshot from the agent.
    The gateway must have an agent session open.
    We POST a JSON command to the gateway's internal HTTP endpoint."""
    gw_internal = RMM_GATEWAY_INTERNAL
    try:
        import urllib.request as _ur, json as _json
        payload = _json.dumps({
            'type': 'screenshot_request',
            'target_agent': agent_id,
        }).encode()
        req = _ur.Request(
            f"{gw_internal}/screenshot-request/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _ur.urlopen(req, timeout=5) as r:
            resp = _json.loads(r.read())
        return jsonify({'ok': True, **resp})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502


@app.route('/api/rmm/screenshot/<agent_id>/latest')
@login_required
def api_rmm_screenshot_latest(agent_id):
    """Return the most recent stored screenshot for an agent."""
    row = db.session.execute(
        text("SELECT id, image_b64, image_format, width, height, captured_at FROM rmm_screenshot WHERE agent_id = :aid ORDER BY id DESC LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'No screenshot yet'}), 404
    return jsonify({
        'ok': True,
        'id': row[0],
        'image_b64': row[1],
        'format': row[2],
        'width': row[3],
        'height': row[4],
        'captured_at': row[5],
    })


# ==================== RMM AGENT DATA SYNC ====================

@app.route('/api/rmm/system-info', methods=['POST'])
def receive_system_info():
    """Receive system info from Linux agent and update asset record"""
    try:
        data = request.get_json()
        
        if not data or 'agent_id' not in data:
            return jsonify({'ok': False, 'error': 'Missing agent_id'}), 400
        
        agent_id = data['agent_id']
        hostname = data.get('hostname', 'Unknown')
        
        # Find asset by agent_id (stored in serial_number) or by hostname
        asset = Asset.query.filter(
            (Asset.serial_number == agent_id) | 
            (Asset.name.ilike(f'%{hostname}%'))
        ).first()
        
        if not asset:
            # Create new asset from agent data
            asset = Asset(
                asset_tag=f'LINUX-{agent_id[:8].upper()}',
                name=hostname,
                category='Server' if 'server' in hostname.lower() else 'Workstation',
                device_type='Virtual Machine' if data.get('virtualization', {}).get('is_virtual') else 'Linux Workstation',
                status='In Use'
            )
            db.session.add(asset)
        
        # Update asset with system info
        os_info = data.get('os', {})
        cpu_info = data.get('cpu', {})
        mem_info = data.get('memory', {})
        virt_info = data.get('virtualization', {})
        network_info = data.get('network', [])
        
        asset.operating_system = os_info.get('pretty_name', 'Linux')
        asset.os_version = os_info.get('version', 'Unknown')
        asset.hardware_cpu = cpu_info.get('model', 'Unknown').strip()
        asset.hardware_ram_gb = mem_info.get('total_gb')
        asset.last_seen = datetime.utcnow()
        asset.online_state = 'Online'
        
        # Extract primary IP and MAC from network interfaces
        if network_info:
            for iface in network_info:
                if iface.get('is_up') and iface.get('addresses'):
                    # Get first IPv4 address
                    for addr in iface.get('addresses', []):
                        if addr.get('type') == 'ipv4' and not asset.ip_address:
                            asset.ip_address = addr.get('address')
                    
                    # Get MAC address
                    mac = iface.get('mac')
                    if mac:
                        # Store in appropriate field based on interface name
                        if 'wl' in iface.get('name', '') or 'wifi' in iface.get('name', '').lower():
                            asset.hardware_mac_wifi = mac
                        else:
                            asset.hardware_mac_ethernet = mac
        
        # Set manufacturer/model from virtualization or detect
        if virt_info.get('is_virtual'):
            asset.manufacturer = virt_info.get('type', 'Unknown').upper()
            asset.model = 'Virtual Machine'
            if not asset.device_type or asset.device_type == '-':
                asset.device_type = 'Virtual Machine'
        
        # Store agent_id in serial_number field
        if not asset.serial_number:
            asset.serial_number = agent_id
        
        db.session.commit()
        
        # Ensure rmm_agent entry exists for this agent (auto-register if missing)
        try:
            rmm_row = db.session.execute(
                text("SELECT id FROM rmm_agent WHERE agent_id = :aid"),
                {'aid': agent_id}
            ).fetchone()
            if not rmm_row:
                import hashlib, secrets
                tok = secrets.token_hex(32)
                tok_hash = hashlib.sha256(tok.encode()).hexdigest()
                db.session.execute(
                    text("INSERT INTO rmm_agent (agent_id, asset_id, agent_token_sha256, enabled, created_at, last_seen_at) VALUES (:aid, :asid, :hash, 1, :now, :now)"),
                    {'aid': agent_id, 'asid': asset.id, 'hash': tok_hash, 'now': datetime.utcnow().isoformat()}
                )
            else:
                db.session.execute(
                    text("UPDATE rmm_agent SET last_seen_at = :now, asset_id = :asid WHERE agent_id = :aid"),
                    {'now': datetime.utcnow().isoformat(), 'asid': asset.id, 'aid': agent_id}
                )
            db.session.commit()
        except Exception as e:
            logger.warning(f"rmm_agent upsert failed: {e}")
        
        logger.info(f"Updated asset {asset.name} (ID {asset.id}) from agent {agent_id}")
        
        return jsonify({
            'ok': True,
            'asset_id': asset.id,
            'message': f'Updated asset {asset.name}'
        })
        
    except Exception as e:
        logger.error(f"Error receiving system info: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rmm/telemetry', methods=['POST'])
def receive_telemetry():
    """Receive telemetry from Linux agent, update asset last_seen, and persist metrics."""
    import json as _json
    try:
        data = request.get_json()

        if not data or 'agent_id' not in data:
            return jsonify({'ok': False, 'error': 'Missing agent_id'}), 400

        agent_id = data['agent_id']
        now = datetime.utcnow()

        # Update asset last_seen / online_state
        asset = Asset.query.filter_by(serial_number=agent_id).first()
        asset_id = asset.id if asset else None
        if asset:
            asset.last_seen = now
            asset.online_state = 'Online'

        # Refresh rmm_agent.last_seen_at
        db.session.execute(
            text("UPDATE rmm_agent SET last_seen_at = :ts WHERE agent_id = :aid"),
            {'ts': now.isoformat(), 'aid': agent_id}
        )

        # Persist CPU / RAM / Disk metrics into rmm_telemetry
        cpu_info  = data.get('cpu', {})
        mem_info  = data.get('memory', {})
        disk_info = data.get('disk', {})

        cpu_pct  = cpu_info.get('usage_percent')
        ram_pct  = mem_info.get('usage_percent')
        ram_total_gb = (mem_info.get('total_mb') or 0) / 1024
        ram_avail_gb = (mem_info.get('available_mb') or 0) / 1024

        # Normalise disk into the same JSON array the UI expects
        disk_json = '[]'
        if disk_info:
            disk_json = _json.dumps([{
                'mountpoint': '/',
                'device': '/',
                'total_gb': disk_info.get('total_gb', 0),
                'free_gb':  disk_info.get('free_gb', 0),
                'percent':  disk_info.get('usage_percent', 0),
            }])

        db.session.execute(text("""
            INSERT INTO rmm_telemetry
                (agent_id, asset_id, cpu_percent, ram_percent, ram_total_gb,
                 ram_available_gb, disk_json, captured_at)
            VALUES
                (:aid, :asid, :cpu, :ram, :ramt, :rava, :dj, :ts)
            ON CONFLICT(agent_id) DO UPDATE SET
                cpu_percent=excluded.cpu_percent,
                ram_percent=excluded.ram_percent,
                ram_total_gb=excluded.ram_total_gb,
                ram_available_gb=excluded.ram_available_gb,
                disk_json=excluded.disk_json,
                captured_at=excluded.captured_at
        """), {
            'aid': agent_id, 'asid': asset_id,
            'cpu': cpu_pct, 'ram': ram_pct,
            'ramt': ram_total_gb, 'rava': ram_avail_gb,
            'dj': disk_json, 'ts': now.isoformat()
        })

        db.session.commit()
        return jsonify({'ok': True})

    except Exception as e:
        logger.error(f"Error receiving telemetry: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ==================== API ENDPOINTS ====================

@app.route('/api/asset/search')
@login_required
@license_required
def api_asset_search():
    """API endpoint to search for asset by tag (for QR scanner)"""
    asset_tag = request.args.get('tag', '')
    
    if not asset_tag:
        return jsonify({'success': False, 'message': 'Asset tag required'}), 400
    
    asset = Asset.query.filter_by(asset_tag=asset_tag).first()
    
    if asset:
        return jsonify({
            'success': True,
            'asset_id': asset.id,
            'asset_tag': asset.asset_tag,
            'name': asset.name,
            'category': asset.category,
            'status': asset.status
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Asset not found'
        }), 404

@app.route('/assets')
@login_required
@license_required
def assets():
    search = request.args.get('search', '')
    categories_list = request.args.getlist('categories')  # Multi-select categories
    category = request.args.get('category', '')  # Single category (backward compatibility)
    status = request.args.get('status', '')
    lifecycle = request.args.get('lifecycle', '')
    purchase_from = request.args.get('purchase_from', '')
    purchase_to = request.args.get('purchase_to', '')
    warranty_status = request.args.get('warranty_status', '')
    quick_filter = request.args.get('filter', '')  # Quick filter from dashboard
    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')
    
    query = Asset.query
    
    # Text search
    if search:
        query = query.filter(
            (Asset.name.contains(search)) |
            (Asset.asset_tag.contains(search)) |
            (Asset.serial_number.contains(search)) |
            (Asset.manufacturer.contains(search))
        )
    
    # Multi-select categories
    if categories_list:
        query = query.filter(Asset.category.in_(categories_list))
    elif category:  # Backward compatibility
        query = query.filter_by(category=category)
    
    # Status filter
    if status:
        query = query.filter_by(status=status)
    
    # Date range filters
    if purchase_from:
        try:
            from_date = datetime.strptime(purchase_from, '%Y-%m-%d')
            query = query.filter(Asset.purchase_date >= from_date)
        except ValueError:
            pass
    
    if purchase_to:
        try:
            to_date = datetime.strptime(purchase_to, '%Y-%m-%d')
            query = query.filter(Asset.purchase_date <= to_date)
        except ValueError:
            pass
    
    # Apply sorting
    sort_column = {
        'asset_tag': Asset.asset_tag,
        'name': Asset.name,
        'category': Asset.category,
        'manufacturer': Asset.manufacturer,
        'serial_number': Asset.serial_number,
        'status': Asset.status,
        'purchase_date': Asset.purchase_date
    }.get(sort_by, Asset.asset_tag)
    
    if sort_dir == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
    
    # Get assets for warranty and lifecycle filtering
    all_assets = query.all()
    
    # Warranty status filter
    if warranty_status:
        filtered_assets = []
        today = datetime.utcnow().date()  # Convert to date for comparison
        
        for asset in all_assets:
            if warranty_status == 'active' and asset.warranty_expiry:
                if asset.warranty_expiry > today:
                    filtered_assets.append(asset)
            elif warranty_status == 'expiring' and asset.warranty_expiry:
                days_until = (asset.warranty_expiry - today).days
                if 0 < days_until <= 30:
                    filtered_assets.append(asset)
            elif warranty_status == 'expired' and asset.warranty_expiry:
                if asset.warranty_expiry <= today:
                    filtered_assets.append(asset)
            elif warranty_status == 'none' and not asset.warranty_expiry:
                filtered_assets.append(asset)
        
        all_assets = filtered_assets
    
    # Lifecycle status filter
    if lifecycle:
        filtered_assets = [asset for asset in all_assets 
                          if asset.purchase_date and asset.expected_life_years 
                          and asset.get_lifecycle_status() == lifecycle]
        all_assets = filtered_assets
    
    # Quick filter from dashboard
    if quick_filter:
        today = datetime.utcnow().date()
        if quick_filter == 'noncompliant':
            all_assets = [asset for asset in all_assets if asset.online_state == 'noncompliant']
        elif quick_filter == 'low_storage':
            all_assets = [asset for asset in all_assets 
                         if asset.hardware_storage_total_gb and asset.hardware_storage_free_gb
                         and (asset.hardware_storage_free_gb / asset.hardware_storage_total_gb * 100) < 20]
        elif quick_filter == 'offline':
            cutoff = datetime.utcnow() - timedelta(days=7)
            all_assets = [asset for asset in all_assets if asset.last_seen and asset.last_seen < cutoff]
        elif quick_filter == 'warranty_expiring':
            all_assets = [asset for asset in all_assets 
                         if asset.warranty_expiry and today < asset.warranty_expiry <= (today + timedelta(days=60))]
        elif quick_filter == 'unassigned':
            all_assets = [asset for asset in all_assets if not asset.employee_id]
    
    assets = all_assets
    categories = db.session.query(Asset.category).distinct().all()

    # Build RMM online/offline sets based on last_seen_at (same 5-min logic as api_rmm_agent_status)
    # Also query gateway for live WebSocket-connected agents (covers Windows agents that don't POST telemetry)
    cutoff = datetime.utcnow() - timedelta(seconds=300)
    rmm_rows = db.session.execute(
        text("SELECT agent_id, asset_id, last_seen_at FROM rmm_agent WHERE enabled = 1 AND asset_id IS NOT NULL")
    ).fetchall()

    # Get live gateway connections
    gateway_online = set()
    try:
        gw_resp = requests.get(f'{RMM_GATEWAY_INTERNAL}/agents', timeout=2)
        gateway_online = set(gw_resp.json().get('agents', []))
    except Exception:
        pass

    rmm_asset_ids = set()
    rmm_online_ids = set()
    for row in rmm_rows:
        agent_id, asset_id, last_seen_at = row[0], row[1], row[2]
        rmm_asset_ids.add(asset_id)
        # Online if: gateway has live WS connection, OR last_seen_at within 5 min
        if agent_id in gateway_online:
            rmm_online_ids.add(asset_id)
        elif last_seen_at:
            if isinstance(last_seen_at, str):
                try:
                    last_seen_at = datetime.fromisoformat(last_seen_at)
                except ValueError:
                    last_seen_at = None
            if last_seen_at and last_seen_at > cutoff:
                rmm_online_ids.add(asset_id)

    return render_template('assets.html', assets=assets, categories=categories,
                           rmm_asset_ids=rmm_asset_ids, rmm_online_ids=rmm_online_ids)

@app.route('/assets/add', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def add_asset():
    if request.method == 'POST':
        # Handle photo upload
        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Add timestamp to filename to avoid conflicts
                photo_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
        
        purchase_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date() if request.form.get('purchase_date') else None
        expected_life_years = int(request.form.get('expected_life_years', 3))
        
        # Auto-calculate replacement date if purchase date is provided
        replacement_date = None
        if purchase_date and expected_life_years:
            replacement_date = datetime(purchase_date.year + expected_life_years, purchase_date.month, purchase_date.day).date()
        elif request.form.get('replacement_date'):
            replacement_date = datetime.strptime(request.form.get('replacement_date'), '%Y-%m-%d').date()
        
        asset = Asset(
            asset_tag=request.form.get('asset_tag'),
            name=request.form.get('name'),
            category=request.form.get('category'),
            manufacturer=request.form.get('manufacturer'),
            model=request.form.get('model'),
            serial_number=request.form.get('serial_number'),
            purchase_date=purchase_date,
            purchase_cost=float(request.form.get('purchase_cost')) if request.form.get('purchase_cost') else None,
            warranty_expiry=datetime.strptime(request.form.get('warranty_expiry'), '%Y-%m-%d').date() if request.form.get('warranty_expiry') else None,
            status=request.form.get('status', 'Available'),
            location=request.form.get('location'),
            notes=request.form.get('notes'),
            photo=photo_filename,
            expected_life_years=expected_life_years,
            replacement_date=replacement_date,
            condition=request.form.get('condition', 'Good'),
            rustdesk_id=(request.form.get('rustdesk_id') or '').strip() or None
        )
        
        db.session.add(asset)
        db.session.commit()
        
        # Add history entry
        history = AssetHistory(
            asset_id=asset.id,
            action='Created',
            description=f'Asset {asset.asset_tag} created',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash(f'Asset {asset.asset_tag} added successfully!', 'success')
        return redirect(url_for('assets'))
    
    employees = Employee.query.all()
    return render_template('add_asset.html', employees=employees)

@app.route('/assets/<int:asset_id>')
@login_required
@license_required
def view_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    history = AssetHistory.query.filter_by(asset_id=asset_id).order_by(AssetHistory.timestamp.desc()).all()
    employees = Employee.query.all()
    
    # Fetch Intune data for this device if serial number exists
    intune_device = None
    intune_error = None
    if asset.serial_number:
        try:
            # Get M365 credentials
            tenant_id_setting = Setting.query.filter_by(key='m365_tenant_id').first()
            client_id_setting = Setting.query.filter_by(key='m365_client_id').first()
            client_secret_setting = Setting.query.filter_by(key='m365_client_secret').first()
            
            if all([tenant_id_setting, client_id_setting, client_secret_setting]):
                m365 = M365Service(
                    tenant_id=tenant_id_setting.value,
                    client_id=client_id_setting.value,
                    client_secret=client_secret_setting.value
                )
                
                # Get all devices and find the matching one
                devices = m365.get_managed_devices()
                for device in devices:
                    if device.get('serialNumber') == asset.serial_number:
                        intune_device = device
                        break
            else:
                intune_error = "M365 credentials not configured"
        except Exception as e:
            intune_error = str(e)

    # Look up RMM agent for this asset
    rmm_agent_id = None
    try:
        rmm_row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = 1 LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()
        if rmm_row:
            rmm_agent_id = rmm_row[0]
    except Exception:
        pass

    return render_template('view_asset.html', asset=asset, history=history, employees=employees,
                         now=datetime.utcnow, intune_device=intune_device, intune_error=intune_error,
                         rmm_agent_id=rmm_agent_id)

@app.route('/assets/<int:asset_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def edit_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    if asset.id == 108:
        asset.manufacturer = 'Dell'
        asset.model = 'XPS 15 9520'
        asset.serial_number = 'GJBBLR3'
        db.session.commit()
    
    if request.method == 'POST':
        old_status = asset.status
        
        # Handle photo upload
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename != '' and allowed_file(file.filename):
                # Delete old photo if exists
                if asset.photo:
                    old_photo_path = os.path.join(app.config['UPLOAD_FOLDER'], asset.photo)
                    if os.path.exists(old_photo_path):
                        os.remove(old_photo_path)
                
                filename = secure_filename(file.filename)
                photo_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
                asset.photo = photo_filename
        
        asset.asset_tag = request.form.get('asset_tag')
        asset.name = request.form.get('name')
        asset.category = request.form.get('category')
        asset.manufacturer = request.form.get('manufacturer')
        asset.model = request.form.get('model')
        asset.serial_number = request.form.get('serial_number')
        asset.purchase_date = datetime.strptime(request.form.get('purchase_date'), '%Y-%m-%d').date() if request.form.get('purchase_date') else None
        asset.purchase_cost = float(request.form.get('purchase_cost')) if request.form.get('purchase_cost') else None
        asset.warranty_expiry = datetime.strptime(request.form.get('warranty_expiry'), '%Y-%m-%d').date() if request.form.get('warranty_expiry') else None
        asset.status = request.form.get('status')
        asset.location = request.form.get('location')
        asset.notes = request.form.get('notes')
        asset.rustdesk_id = (request.form.get('rustdesk_id') or '').strip() or None
        asset.service_urls = request.form.get('service_urls')
        asset.expected_life_years = int(request.form.get('expected_life_years', 3))
        asset.replacement_date = datetime.strptime(request.form.get('replacement_date'), '%Y-%m-%d').date() if request.form.get('replacement_date') else None
        asset.condition = request.form.get('condition', 'Good')
        asset.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Add history entry
        if old_status != asset.status:
            history = AssetHistory(
                asset_id=asset.id,
                action='Status Changed',
                description=f'Status changed from {old_status} to {asset.status}',
                user_id=current_user.id
            )
            db.session.add(history)
            db.session.commit()
        
        flash(f'Asset {asset.asset_tag} updated successfully!', 'success')
        return redirect(url_for('view_asset', asset_id=asset.id))
    
    employees = Employee.query.all()
    # Check if this asset has an RMM agent (so we can show read-only indicators)
    rmm_agent_id = None
    try:
        rmm_row = db.session.execute(
            text("SELECT agent_id FROM rmm_agent WHERE asset_id = :aid AND enabled = 1 LIMIT 1"),
            {'aid': asset_id}
        ).fetchone()
        if rmm_row:
            rmm_agent_id = rmm_row[0]
    except Exception:
        pass
    return render_template('edit_asset.html', asset=asset, employees=employees, rmm_agent_id=rmm_agent_id, rmm_linked=bool(rmm_agent_id))


@app.route('/assets/<int:asset_id>/remote/rustdesk/start', methods=['POST'])
@login_required
@manager_required
@license_required
def start_rustdesk_remote_session(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    payload = request.get_json(silent=True) or {}
    reason = (payload.get('reason') or '').strip()
    if not reason:
        return jsonify({'success': False, 'error': 'reason is required'}), 400
    if not asset.rustdesk_id:
        return jsonify({'success': False, 'error': 'asset has no RustDesk ID configured'}), 400

    session_row = RemoteSession(
        tool='rustdesk',
        asset_id=asset.id,
        started_by_user_id=current_user.id,
        reason=reason,
        started_at=datetime.utcnow()
    )
    db.session.add(session_row)

    history = AssetHistory(
        asset_id=asset.id,
        action='Remote Session Started',
        description=f'RustDesk session started: {reason}',
        user_id=current_user.id
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({
        'success': True,
        'session_id': session_row.id,
        'rustdesk_id': asset.rustdesk_id,
    })


@app.route('/remote-sessions/<int:session_id>/end', methods=['POST'])
@login_required
@manager_required
@license_required
def end_remote_session(session_id):
    session_row = RemoteSession.query.get_or_404(session_id)
    if session_row.ended_at is not None:
        return jsonify({'success': True})

    session_row.ended_at = datetime.utcnow()
    session_row.ended_by_user_id = current_user.id

    history = AssetHistory(
        asset_id=session_row.asset_id,
        action='Remote Session Ended',
        description=f'{session_row.tool.title()} session ended',
        user_id=current_user.id
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({'success': True})

@app.route('/assets/<int:asset_id>/assign', methods=['POST'])
@login_required
@manager_required
@license_required
def assign_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    employee_id = request.form.get('employee_id')
    
    if employee_id:
        employee = Employee.query.get(employee_id)
        old_employee = asset.assigned_employee
        
        asset.employee_id = employee_id
        asset.status = 'In Use'
        db.session.commit()
        
        # Add history entry
        history = AssetHistory(
            asset_id=asset.id,
            action='Assigned',
            description=f'Asset assigned to {employee.name}',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash(f'Asset assigned to {employee.name}', 'success')
    
    return redirect(url_for('view_asset', asset_id=asset.id))

@app.route('/assets/<int:asset_id>/unassign', methods=['POST'])
@login_required
@manager_required
@license_required
def unassign_asset(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    
    if asset.employee_id:
        employee_name = asset.assigned_employee.name
        asset.employee_id = None
        asset.status = 'Available'
        db.session.commit()
        
        # Add history entry
        history = AssetHistory(
            asset_id=asset.id,
            action='Unassigned',
            description=f'Asset unassigned from {employee_name}',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash('Asset unassigned successfully', 'success')
    
    return redirect(url_for('view_asset', asset_id=asset.id))

@app.route('/assets/<int:asset_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_asset(asset_id):
    
    asset = Asset.query.get_or_404(asset_id)
    asset_tag = asset.asset_tag
    
    db.session.delete(asset)
    db.session.commit()
    
    flash(f'Asset {asset_tag} deleted successfully', 'success')
    return redirect(url_for('assets'))

@app.route('/assets/find-duplicates')
@login_required
@manager_required
@license_required
def find_duplicate_assets():
    """Find assets with duplicate names"""
    from sqlalchemy import func
    
    # Find names that appear more than once
    duplicate_names = db.session.query(
        Asset.name,
        func.count(Asset.id).label('count')
    ).group_by(Asset.name).having(func.count(Asset.id) > 1).all()
    
    duplicates = []
    for name, count in duplicate_names:
        assets = Asset.query.filter_by(name=name).order_by(Asset.created_at).all()
        duplicates.append({
            'name': name,
            'count': count,
            'assets': assets
        })
    
    return render_template('find_duplicates.html', duplicates=duplicates)

@app.route('/assets/merge-duplicates', methods=['POST'])
@login_required
@manager_required
@license_required
def merge_duplicate_assets():
    """Merge duplicate assets by keeping one and deleting others"""
    try:
        data = request.get_json()
        keep_id = data.get('keep_id')
        delete_ids = data.get('delete_ids', [])
        
        if not keep_id or not delete_ids:
            return jsonify({'success': False, 'message': 'Missing parameters'}), 400
        
        keep_asset = Asset.query.get_or_404(keep_id)
        
        # Collect duplicates and gather data to transfer
        duplicates_to_delete = []
        transfer_serial = None
        transfer_employee_id = None
        transfer_intune_id = None
        
        for delete_id in delete_ids:
            if delete_id != keep_id:
                duplicate = Asset.query.get(delete_id)
                if duplicate:
                    # Collect data from duplicates that we want to keep
                    if not transfer_serial and duplicate.serial_number:
                        transfer_serial = duplicate.serial_number
                    if not transfer_employee_id and duplicate.employee_id:
                        transfer_employee_id = duplicate.employee_id
                    if not transfer_intune_id and duplicate.intune_device_id:
                        transfer_intune_id = duplicate.intune_device_id
                    
                    duplicates_to_delete.append(duplicate)
        
        # Clear unique constraints from duplicates FIRST to avoid conflicts
        for duplicate in duplicates_to_delete:
            duplicate.serial_number = None
            duplicate.asset_tag = f"DEL-{duplicate.id}-{duplicate.asset_tag[:30]}"
        
        db.session.flush()  # Apply the clearing changes
        
        # NOW transfer data to the keep_asset (after duplicates are cleared)
        if not keep_asset.serial_number and transfer_serial:
            keep_asset.serial_number = transfer_serial
        if not keep_asset.employee_id and transfer_employee_id:
            keep_asset.employee_id = transfer_employee_id
        if not keep_asset.intune_device_id and transfer_intune_id:
            keep_asset.intune_device_id = transfer_intune_id
        
        db.session.flush()  # Apply the transfers
        
        # Now delete the duplicates
        deleted_count = 0
        for duplicate in duplicates_to_delete:
            db.session.delete(duplicate)
            deleted_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Kept {keep_asset.name} and deleted {deleted_count} duplicate(s)',
            'redirect': url_for('find_duplicate_assets')
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/assets/<int:asset_id>/update-status', methods=['POST'])
@login_required
@manager_required
@license_required
def update_asset_status(asset_id):
    """Update asset status via inline edit"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        
        if not new_status:
            return jsonify({'success': False, 'message': 'Status is required'}), 400
        
        asset = Asset.query.get_or_404(asset_id)
        old_status = asset.status
        asset.status = new_status
        asset.updated_at = datetime.utcnow()
        
        # Create history entry
        history = AssetHistory(
            asset_id=asset.id,
            action=f'Status changed from {old_status} to {new_status}',
            changed_by=current_user.username,
            timestamp=datetime.utcnow()
        )
        db.session.add(history)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Status updated to {new_status}'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/assets/<int:asset_id>/qr')
@login_required
@license_required
def asset_qr(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(f"{request.host_url}assets/{asset.id}")
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return render_template('qr_code.html', asset=asset, qr_code=img_str)

@app.route('/employees')
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
                         departments=departments,
                         search=search,
                         department_filter=department_filter,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         total_employees=total_employees,
                         total_assets_assigned=total_assets_assigned,
                         total_licenses_assigned=total_licenses_assigned,
                         departments_count=departments_count)


@app.route('/employees/sync-from-m365', methods=['POST'])
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
            return redirect(url_for('employees'))

        m365 = M365Service(
            tenant_id=tenant_id_setting.value,
            client_id=client_id_setting.value,
            client_secret=client_secret_setting.value
        )

        users = m365.get_all_users() or []
        if not users:
            flash('No users returned from Microsoft 365', 'warning')
            return redirect(url_for('employees'))

        created = 0
        updated = 0
        photo_updated = 0
        skipped = 0

        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'employee_photos'), exist_ok=True)

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
                    photo_abs = os.path.join(app.config['UPLOAD_FOLDER'], photo_rel)
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

    return redirect(url_for('employees'))

@app.route('/employees/add', methods=['GET', 'POST'])
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
            return redirect(url_for('employees'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding employee: {str(e)}', 'danger')
            return render_template('add_employee.html')
    
    return render_template('add_employee.html')

@app.route('/employees/<int:employee_id>')
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

@app.route('/employees/<int:employee_id>/edit', methods=['GET', 'POST'])
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
            return redirect(url_for('view_employee', employee_id=employee.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating employee: {str(e)}', 'danger')
            return render_template('edit_employee.html', employee=employee)
    
    return render_template('edit_employee.html', employee=employee)

@app.route('/employees/<int:employee_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    employee_name = employee.name
    
    # Check if employee has assigned assets
    if employee.assets:
        flash(f'Cannot delete employee {employee_name}. They have {len(employee.assets)} assigned assets. Please unassign all assets first.', 'danger')
        return redirect(url_for('view_employee', employee_id=employee.id))
    
    # Check if employee has assigned licenses
    active_licenses = LicenseAssignment.query.filter_by(
        employee_id=employee.id, 
        status='Active'
    ).count()
    if active_licenses > 0:
        flash(f'Cannot delete employee {employee_name}. They have {active_licenses} assigned licenses. Please return all licenses first.', 'danger')
        return redirect(url_for('view_employee', employee_id=employee.id))
    
    db.session.delete(employee)
    db.session.commit()
    
    flash(f'Employee {employee_name} deleted successfully', 'success')
    return redirect(url_for('employees'))

@app.route('/employees/import', methods=['GET', 'POST'])
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

@app.route('/employees/export/csv')
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

@app.route('/employees/template')
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

# ==================== BULK OPERATIONS ROUTES ====================

@app.route('/assets/bulk/status', methods=['POST'])
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
        for asset_id in asset_ids:
            asset = Asset.query.get(asset_id)
            if asset:
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
        
        return jsonify({
            'success': True,
            'count': count,
            'message': f'Successfully updated {count} assets'
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/assets/bulk/department', methods=['POST'])
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

@app.route('/assets/bulk/export', methods=['POST'])
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

@app.route('/assets/bulk/delete', methods=['POST'])
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
                    photo_path = os.path.join(app.config['UPLOAD_FOLDER'], asset.photo)
                    if os.path.exists(photo_path):
                        os.remove(photo_path)
                
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

@app.route('/assets/bulk/auto-assign', methods=['GET', 'POST'])
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
        
        return redirect(url_for('assets'))
    
    # GET request - show preview
    unassigned_count = Asset.query.filter(Asset.employee_id == None).count()
    employee_count = Employee.query.count()
    
    return render_template('bulk_auto_assign.html', 
                          unassigned_count=unassigned_count,
                          employee_count=employee_count)

@app.route('/assets/bulk/assign-csv', methods=['GET', 'POST'])
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
                    return redirect(url_for('bulk_assign_csv'))
                
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
                return redirect(url_for('assets'))
            except Exception as e:
                flash(f'Error processing assignments: {str(e)}', 'danger')
                return redirect(url_for('bulk_assign_csv'))
        
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

# ==================== CUSTOM REPORTS ROUTES ====================

@app.route('/reports/custom')
@login_required
@license_required
def custom_reports():
    """Custom report builder page"""
    categories = db.session.query(Asset.category).distinct().all()
    return render_template('custom_reports.html', categories=categories)

@app.route('/reports/custom/generate', methods=['POST'])
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

@app.route('/reports/custom/export', methods=['POST'])
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

@app.route('/reports/custom/save', methods=['POST'])
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

@app.route('/reports/custom/list', methods=['GET'])
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

@app.route('/reports/custom/delete/<int:report_id>', methods=['DELETE'])
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

@app.route('/reports')
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

@app.route('/system-description')
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

@app.route('/system-description/<int:section_id>', methods=['GET', 'POST'])
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
        return redirect(url_for('system_description'))
    
    return render_template('edit_system_description_section.html', section=section)

@app.route('/system-description/export')
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

@app.route('/policies')
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

@app.route('/policies/<int:policy_id>')
@login_required
@admin_required
@license_required
def view_policy(policy_id):
    """View individual policy details with sections"""
    policy = Policy.query.get_or_404(policy_id)
    sections = PolicySection.query.filter_by(policy_id=policy_id).order_by(PolicySection.section_order).all()
    
    return render_template('view_policy.html', policy=policy, sections=sections)

@app.route('/controls')
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

@app.route('/controls/<int:control_id>')
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

@app.route('/risks')
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

@app.route('/assets/export/csv')
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

@app.route('/assets/import', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def import_assets():
    """Import assets from CSV"""
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file uploaded', 'danger')
            return redirect(url_for('import_assets'))
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(url_for('import_assets'))
        
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
                
                return redirect(url_for('assets'))
                
            except Exception as e:
                flash(f'Error processing CSV: {str(e)}', 'danger')
                return redirect(url_for('import_assets'))
        else:
            flash('Please upload a valid CSV file', 'danger')
            return redirect(url_for('import_assets'))
    
    return render_template('import_assets.html')

@app.route('/assets/sync-from-intune', methods=['POST'])
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

    return redirect(url_for('assets'))


def perform_intune_asset_sync():
    """Core Intune asset sync logic.

    Returns:
        dict: {success, synced_count, updated_count, skipped_count, errors, error}
    """
    try:
        db.session.rollback()

        from m365_service import M365Service

        tenant_id_setting = Setting.query.filter_by(key='m365_tenant_id').first()
        client_id_setting = Setting.query.filter_by(key='m365_client_id').first()
        client_secret_setting = Setting.query.filter_by(key='m365_client_secret').first()

        if not all([tenant_id_setting, client_id_setting, client_secret_setting]):
            return {
                'success': False,
                'error': 'M365 credentials not configured. Please configure in Settings.'
            }

        m365 = M365Service(
            tenant_id=tenant_id_setting.value,
            client_id=client_id_setting.value,
            client_secret=client_secret_setting.value
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
        assets_by_name_lower = {}
        existing_asset_tags = set()
        for existing_asset in all_assets:
            if existing_asset.asset_tag:
                existing_asset_tags.add(existing_asset.asset_tag)
            if existing_asset.serial_number:
                assets_by_serial[existing_asset.serial_number] = existing_asset
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

                asset = None
                if serial_number and serial_number in assets_by_serial:
                    asset = assets_by_serial[serial_number]
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

@app.route('/users')
@login_required
@admin_required
@license_required
def users():
    """List all users"""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('users.html', users=users)

@app.route('/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
@license_required
def add_user():
    """Add a new user"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'viewer')
        
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('add_user'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists', 'danger')
            return redirect(url_for('add_user'))
        
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            is_admin=(role == 'admin')
        )
        
        db.session.add(user)
        db.session.commit()
        
        flash(f'User {username} created successfully!', 'success')
        return redirect(url_for('users'))
    
    return render_template('add_user.html')

@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@license_required
def edit_user(user_id):
    """Edit user details"""
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form.get('username')
        user.email = request.form.get('email')
        user.role = request.form.get('role')
        user.is_admin = (user.role == 'admin')
        
        # Update password if provided
        new_password = request.form.get('password')
        if new_password:
            user.password_hash = generate_password_hash(new_password)
        
        db.session.commit()
        flash(f'User {user.username} updated successfully!', 'success')
        return redirect(url_for('users'))
    
    return render_template('edit_user.html', user=user)

@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_user(user_id):
    """Delete a user"""
    user = User.query.get_or_404(user_id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account', 'danger')
        return redirect(url_for('users'))
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {username} deleted successfully', 'success')
    return redirect(url_for('users'))

@app.route('/init-db')
def init_db():
    """Initialize database and create admin user"""
    db.create_all()
    
    # Check if admin exists
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@company.com',
            password_hash=generate_password_hash('admin123'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        return 'Database initialized! Admin user created (username: admin, password: admin123)'
    
    return 'Database already initialized!'

# ==================== LICENSE MANAGEMENT ====================

from functools import wraps
from license_service import license_service

# Initialize license service
license_service.init_app(app, db)

# ==================== SOC2 API ENDPOINTS ====================

@app.route('/api/soc2/sync', methods=['POST'])
@login_required
@admin_required
def api_soc2_sync():
    """Trigger a manual SOC2 sync"""
    try:
        from soc2_sync_service import SOC2SyncService
        
        sync_service = SOC2SyncService(app, db)
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

@app.route('/api/soc2/azure-security-sync', methods=['POST'])
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

# ==================== LICENSE API ENDPOINTS ====================

@app.route('/api/license', methods=['GET'])
@login_required
@admin_required
def get_license():
    """Get current license information"""
    try:
        license_info = LicenseInfo.query.order_by(LicenseInfo.id.desc()).first()
        
        if not license_info:
            return jsonify({'exists': False})
        
        # Calculate days remaining if expiry date exists
        days_remaining = None
        if license_info.expiry_date:
            delta = license_info.expiry_date - datetime.utcnow()
            days_remaining = max(0, delta.days)
        
        return jsonify({
            'exists': True,
            'license': {
                'id': license_info.id,
                'license_key': license_info.license_key,
                'api_key': license_info.api_key[:8] + '...' if license_info.api_key and len(license_info.api_key) > 8 else None,
                'device_id': license_info.device_id,
                'status': license_info.status,
                'company_name': license_info.company_name,
                'plan_name': license_info.plan_name,
                'expiry_date': license_info.expiry_date.isoformat() if license_info.expiry_date else None,
                'days_remaining': days_remaining,
                'max_devices': license_info.max_devices,
                'last_checked': license_info.last_checked.isoformat() if license_info.last_checked else None,
                'last_check_status': license_info.last_check_status,
                'grace_period_ends': license_info.grace_period_ends.isoformat() if license_info.grace_period_ends else None,
                'created_at': license_info.created_at.isoformat(),
                'updated_at': license_info.updated_at.isoformat()
            }
        })
    except Exception as e:
        logger.error(f'Error fetching license: {str(e)}')
        return jsonify({'error': 'Failed to fetch license'}), 500

@app.route('/api/license', methods=['POST'])
@login_required
@admin_required
def save_license():
    """Save or update license"""
    try:
        data = request.get_json()
        license_key = data.get('licenseKey')
        company_name = data.get('companyName')
        api_key = data.get('apiKey')
        device_id = data.get('deviceId')
        
        if not license_key:
            return jsonify({'error': 'License key is required'}), 400
        
        # Delete existing licenses
        LicenseInfo.query.delete()
        
        # Create new license
        new_license = LicenseInfo(
            license_key=license_key,
            company_name=company_name,
            api_key=api_key if api_key else None,
            device_id=device_id if device_id else None,
            status='pending'
        )
        db.session.add(new_license)
        db.session.commit()
        
        return jsonify({
            'message': 'License saved successfully',
            'license': {
                'id': new_license.id,
                'license_key': new_license.license_key,
                'company_name': new_license.company_name,
                'api_key': '***' if new_license.api_key else None,
                'device_id': new_license.device_id,
                'status': new_license.status
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error saving license: {str(e)}')
        return jsonify({'error': 'Failed to save license'}), 500

@app.route('/api/license/verify', methods=['POST'])
@login_required
@admin_required
def verify_license():
    """Manually verify license with server"""
    try:
        license_service.perform_check()
        
        license_info = LicenseInfo.query.order_by(LicenseInfo.id.desc()).first()
        
        if not license_info:
            return jsonify({'error': 'No license found'}), 404
        
        # Calculate days remaining
        days_remaining = None
        if license_info.expiry_date:
            delta = license_info.expiry_date - datetime.utcnow()
            days_remaining = max(0, delta.days)
        
        return jsonify({
            'message': 'License verification completed',
            'license': {
                'id': license_info.id,
                'status': license_info.status,
                'plan_name': license_info.plan_name,
                'expiry_date': license_info.expiry_date.isoformat() if license_info.expiry_date else None,
                'days_remaining': days_remaining,
                'last_checked': license_info.last_checked.isoformat() if license_info.last_checked else None,
                'last_check_status': license_info.last_check_status,
                'grace_period_ends': license_info.grace_period_ends.isoformat() if license_info.grace_period_ends else None
            }
        })
    except Exception as e:
        logger.error(f'Error verifying license: {str(e)}')
        return jsonify({'error': f'Failed to verify license: {str(e)}'}), 500

@app.route('/api/license/<int:license_id>', methods=['DELETE'])
@login_required
@admin_required
def remove_license_key(license_id):
    """Delete a license"""
    try:
        license_info = LicenseInfo.query.get_or_404(license_id)
        db.session.delete(license_info)
        db.session.commit()
        
        return jsonify({'message': 'License removed successfully'})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error deleting license: {str(e)}')
        return jsonify({'error': 'Failed to remove license'}), 500




# ═══════════════════════════════════════════════════════════════════════════════
# RESTORED ROUTES (recovered from git HEAD)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Restored: /assets/<int:asset_id>/checkout ──
@app.route('/assets/<int:asset_id>/checkout', methods=['POST'])
@login_required
@manager_required
@license_required
def asset_checkout(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    # Check not already checked out
    active = AssetLoan.query.filter_by(asset_id=asset_id, checked_in_at=None).first()
    if active:
        flash(f'Asset is already checked out to {active.checked_out_to}.', 'warning')
        return redirect(url_for('view_asset', asset_id=asset_id))
    borrower = (request.form.get('checked_out_to') or '').strip()
    if not borrower:
        flash('Borrower name is required.', 'danger')
        return redirect(url_for('view_asset', asset_id=asset_id))
    due_str = (request.form.get('due_back_at') or '').strip()
    due = None
    if due_str:
        try:
            from datetime import date as _date
            due = _date.fromisoformat(due_str)
        except ValueError:
            pass
    loan = AssetLoan(
        asset_id=asset_id,
        checked_out_to=borrower,
        checked_out_by_user_id=current_user.id,
        due_back_at=due,
        notes=(request.form.get('notes') or '').strip() or None,
    )
    db.session.add(loan)
    log_change(asset, current_user.username, 'checkout', f'Checked out to {borrower}')
    db.session.commit()
    flash(f'Asset checked out to {borrower}.', 'success')
    return redirect(url_for('view_asset', asset_id=asset_id))




# ── Restored: /assets/<int:asset_id>/checkin/<int:loan_id> ──
@app.route('/assets/<int:asset_id>/checkin/<int:loan_id>', methods=['POST'])
@login_required
@manager_required
@license_required
def asset_checkin(asset_id, loan_id):
    loan = AssetLoan.query.get_or_404(loan_id)
    if loan.asset_id != asset_id:
        flash('Loan does not belong to this asset.', 'danger')
        return redirect(url_for('view_asset', asset_id=asset_id))
    if not loan.is_active:
        flash('Asset is already checked in.', 'warning')
        return redirect(url_for('view_asset', asset_id=asset_id))
    loan.checked_in_at = now_mst()
    loan.checked_in_by_user_id = current_user.id
    asset = Asset.query.get(asset_id)
    log_change(asset, current_user.username, 'checkin', f'Checked in from {loan.checked_out_to}')
    db.session.commit()
    flash(f'Asset checked in from {loan.checked_out_to}.', 'success')
    return redirect(url_for('view_asset', asset_id=asset_id))


# ── Software inventory (agent → server) ─────────────────────────────────────



# ── Restored: /api/asset/<int:asset_id>/software ──
@app.route('/api/asset/<int:asset_id>/software', methods=['POST'])
@license_required
@require_api_key('agent')
def api_update_software(asset_id):
    """Agent POSTs full software inventory; replace existing records."""
    asset = Asset.query.get_or_404(asset_id)
    apps = request.get_json(silent=True) or []
    InstalledApp.query.filter_by(asset_id=asset_id).delete()
    now = now_mst()
    for a in apps:
        name = (a.get('name') or '').strip()
        if not name:
            continue
        db.session.add(InstalledApp(
            asset_id=asset_id,
            name=name,
            version=(a.get('version') or '').strip() or None,
            publisher=(a.get('publisher') or '').strip() or None,
            install_date=(a.get('install_date') or '').strip() or None,
            recorded_at=now,
        ))
    db.session.commit()
    return jsonify({'ok': True, 'count': len(apps)})




# ── Restored: /api/rmm/<agent_id>/software ──
@app.route('/api/rmm/<agent_id>/software', methods=['POST'])
def rmm_update_software(agent_id):
    """RMM agent POSTs software inventory via its agent_id + token (no user login needed)."""
    token = request.args.get('token', '') or request.headers.get('X-Agent-Token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    # Look up the asset linked to this agent
    row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = 1 LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row or not row[0]:
        return jsonify({'error': 'No asset linked to this agent'}), 404
    asset_id = row[0]
    apps = request.get_json(silent=True) or []
    InstalledApp.query.filter_by(asset_id=asset_id).delete()
    now = now_mst()
    inserted = 0
    for a in apps:
        name = (a.get('name') or '').strip()
        if not name:
            continue
        db.session.add(InstalledApp(
            asset_id=asset_id,
            name=name,
            version=(a.get('version') or '').strip() or None,
            publisher=(a.get('publisher') or '').strip() or None,
            install_date=(a.get('install_date') or '').strip() or None,
            recorded_at=now,
        ))
        inserted += 1
    db.session.commit()
    return jsonify({'ok': True, 'count': inserted})


# ── Global search ────────────────────────────────────────────────────────────



# ── Restored: /search ──
@app.route('/search')
@login_required
@license_required
def global_search():
    q = (request.args.get('q') or '').strip()
    if not q or len(q) < 2:
        return render_template('search.html', q=q, assets=[], tickets=[], employees=[])
    like = f'%{q}%'

    from sqlalchemy import or_
    assets = Asset.query.filter(or_(
        Asset.asset_tag.ilike(like),
        Asset.hostname.ilike(like),
        Asset.make.ilike(like),
        Asset.model.ilike(like),
        Asset.serial_number.ilike(like),
    )).limit(20).all()

    tickets = SupportTicket.query.filter(or_(
        SupportTicket.subject.ilike(like),
        SupportTicket.description.ilike(like),
        SupportTicket.reporter_name.ilike(like),
        SupportTicket.reporter_email.ilike(like),
        SupportTicket.hostname.ilike(like),
        SupportTicket.asset_tag.ilike(like),
    )).order_by(SupportTicket.created_at.desc()).limit(20).all()

    try:
        from models_employee import Employee
        employees = Employee.query.filter(or_(
            Employee.name.ilike(like),
            Employee.email.ilike(like),
            Employee.department.ilike(like),
            Employee.job_title.ilike(like),
        )).limit(20).all()
    except Exception:
        employees = []

    return render_template('search.html', q=q, assets=assets, tickets=tickets, employees=employees)


# ── Agent installer download ─────────────────────────────────────────────────



# ── Restored: /download/agent-installer ──
@app.route('/download/agent-installer')
@login_required
@license_required
def download_agent_installer():
    """Serve the MSI installer (preferred) or PS1 fallback."""
    import os
    msi_path = os.path.join(app.root_path, 'rmm_agent', 'CirqueRMM.msi')
    if os.path.exists(msi_path):
        return send_file(msi_path, as_attachment=True, download_name='CirqueRMM.msi',
                         mimetype='application/x-msi')
    # Fallback to PS1 if MSI not built yet
    ps1_path = os.path.join(app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(ps1_path):
        return "Installer not found on server.", 404
    return send_file(ps1_path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')




# ── Restored: /download/agent-ps1 ──
@app.route('/download/agent-ps1')
@login_required
@license_required
def download_agent_ps1():
    """Serve the raw PowerShell installer script directly."""
    import os
    path = os.path.join(app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')




# ── Restored: /download/agent-file/<path:filename> ──
@app.route('/download/agent-file/<path:filename>')
@login_required
@license_required
def download_agent_file(filename):
    """Serve individual agent files for the installer (authenticated users only)."""
    import os, posixpath
    # Whitelist allowed files
    allowed = {
        'agent_client.py', 'agent_launcher.py', 'tray.py',
        'requirements.txt', 'version.txt',
        'cirque_icon_ico.b64', 'cirque_icon_png.b64',
    }
    # Sanitize filename to prevent path traversal
    clean = posixpath.basename(filename)
    if clean not in allowed:
        return "File not available.", 404
    path = os.path.join(app.root_path, 'rmm_agent', clean)
    if not os.path.exists(path):
        return f"{clean} not found on server.", 404
    return send_file(path, as_attachment=False, mimetype='text/plain')




# ── Restored: /rmm/agent/launcher ──
@app.route('/rmm/agent/launcher')
def rmm_agent_launcher():
    """Serve agent_launcher.py (self-healing wrapper). Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    launcher_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'agent_launcher.py')
    return send_file(launcher_path, mimetype='text/x-python', as_attachment=False)




# ── Restored: /rmm/agent/repair ──
@app.route('/rmm/agent/repair')
def rmm_agent_repair():
    """Serve agent_repair.ps1. Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    repair_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'agent_repair.ps1')
    return send_file(repair_path, mimetype='text/plain', as_attachment=False)




# ── Restored: /rmm/agent/tray ──
@app.route('/rmm/agent/tray')
def rmm_agent_tray():
    """Serve tray.py to authenticated agents."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    tray_path = os.path.join(os.path.dirname(__file__), 'rmm_agent', 'tray.py')
    if not os.path.isfile(tray_path):
        return jsonify({'error': 'tray.py not found on server'}), 404
    return send_file(tray_path, mimetype='text/x-python', as_attachment=False)




# ── Restored: /api/rmm/last-scan/<agent_id> ──
@app.route('/api/rmm/last-scan/<agent_id>', methods=['POST'])
@login_required
def api_rmm_last_scan(agent_id):
    """Persist the last AV scan time into security_json."""
    import json as _json
    data = request.get_json(force=True) or {}
    scan_type = data.get('scan_type', 'quick')
    scan_time = data.get('scan_time', '')
    row = db.session.execute(
        text("SELECT security_json FROM rmm_telemetry WHERE agent_id = :aid"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'No telemetry row'}), 404
    try:
        sec = _json.loads(row[0] or '{}')
    except Exception:
        sec = {}
    sec['last_scan'] = {'type': scan_type, 'time': scan_time}
    db.session.execute(
        text("UPDATE rmm_telemetry SET security_json = :sj WHERE agent_id = :aid"),
        {'sj': _json.dumps(sec), 'aid': agent_id}
    )
    db.session.commit()
    return jsonify({'ok': True})


# ── Eagle Eyes ────────────────────────────────────────────────────────────────



# ── Restored: /api/rmm/metrics-history/<agent_id> ──
@app.route('/api/rmm/metrics-history/<agent_id>')
@login_required
def api_rmm_metrics_history(agent_id):
    """Return CPU/RAM history for the last N hours (default 24)."""
    hours = int(request.args.get('hours', 24))
    rows = db.session.execute(
        text("""SELECT recorded_at, cpu_percent, memory_percent
                FROM rmm_metrics_history
                WHERE agent_id = :aid
                  AND recorded_at >= datetime('now', :delta)
                ORDER BY recorded_at ASC"""),
        {'aid': agent_id, 'delta': f'-{hours} hours'}
    ).fetchall()
    return jsonify({
        'ok': True,
        'hours': hours,
        'data': [{'ts': r[0], 'cpu': r[1], 'ram': r[2]} for r in rows],
    })




# ── Restored: /api/rmm/availability/<agent_id> ──
@app.route('/api/rmm/availability/<agent_id>')
@login_required
def api_rmm_availability(agent_id):
    """Return recent online/offline events for an agent."""
    limit = int(request.args.get('limit', 100))
    rows = db.session.execute(
        text("""SELECT event, recorded_at
                FROM rmm_availability
                WHERE agent_id = :aid
                ORDER BY recorded_at DESC
                LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit}
    ).fetchall()
    return jsonify({
        'ok': True,
        'events': [{'event': r[0], 'ts': r[1]} for r in rows],
    })




# ── Restored: /api/rmm/patches/<agent_id> ──
@app.route('/api/rmm/patches/<agent_id>')
@login_required
def api_rmm_patches(agent_id):
    """Return installed Windows hotfixes for an agent."""
    rows = db.session.execute(
        text("""SELECT hotfix_id, description, installed_on
                FROM rmm_patch
                WHERE agent_id = :aid
                ORDER BY installed_on DESC"""),
        {'aid': agent_id}
    ).fetchall()
    return jsonify({
        'ok': True,
        'count': len(rows),
        'patches': [{'id': r[0], 'description': r[1], 'installed_on': r[2]} for r in rows],
    })




# ── Restored: /api/rmm/pending-updates/<agent_id> ──
@app.route('/api/rmm/pending-updates/<agent_id>')
@login_required
def api_rmm_pending_updates(agent_id):
    """List available (not yet installed) Windows Updates reported by the agent."""
    rows = db.session.execute(
        text("""SELECT update_id, title, kb_ids, severity, size_mb,
                       reboot_required, category, recorded_at
                FROM rmm_pending_update
                WHERE agent_id = :aid
                ORDER BY
                    CASE severity
                        WHEN 'Critical'  THEN 1 WHEN 'Important' THEN 2
                        WHEN 'Moderate'  THEN 3 WHEN 'Low'       THEN 4 ELSE 5
                    END, title"""),
        {'aid': agent_id}
    ).fetchall()
    import json as _json
    return jsonify({
        'ok': True,
        'count': len(rows),
        'updates': [{
            'update_id':       r[0], 'title':    r[1],
            'kb_ids':          _json.loads(r[2] or '[]'),
            'severity':        r[3], 'size_mb':  r[4],
            'reboot_required': bool(r[5]),
            'category':        r[6], 'recorded_at': r[7],
        } for r in rows],
    })




# ── Restored: /api/rmm/session-events/<agent_id> ──
@app.route('/api/rmm/session-events/<agent_id>')
@login_required
def api_rmm_session_events(agent_id):
    """Return session activity events (logon/logoff/lock/unlock/sleep/wake) for an agent."""
    from rmm_gateway.db import get_session_events
    days = request.args.get('days', 7, type=int)
    events = get_session_events(agent_id, min(days, 90))
    return jsonify({'ok': True, 'events': events})




# ── Restored: /api/rmm/software/<agent_id> ──
@app.route('/api/rmm/software/<agent_id>')
@login_required
def api_rmm_software(agent_id):
    """Return installed software inventory for an agent."""
    from rmm_gateway.db import get_software
    software = get_software(agent_id)
    return jsonify({'ok': True, 'software': software, 'count': len(software)})




# ── Restored: /api/rmm/patch-jobs/<agent_id> ──
@app.route('/api/rmm/patch-jobs/<agent_id>', methods=['GET'])
@login_required
def api_rmm_patch_jobs_get(agent_id):
    """List patch deployment jobs for an agent."""
    import json as _json
    rows = db.session.execute(
        text("""SELECT id, update_ids, kb_ids, titles, status,
                       approved_by, approved_at, deployed_at, completed_at,
                       result_json, reboot_required, created_at, updated_at
                FROM rmm_patch_job
                WHERE agent_id = :aid
                ORDER BY id DESC LIMIT 30"""),
        {'aid': agent_id}
    ).fetchall()
    return jsonify({
        'ok': True,
        'jobs': [{
            'id':              r[0],
            'update_ids':      _json.loads(r[1] or '[]'),
            'kb_ids':          _json.loads(r[2] or '[]'),
            'titles':          _json.loads(r[3] or '[]'),
            'status':          r[4],
            'approved_by':     r[5],
            'approved_at':     r[6],
            'deployed_at':     r[7],
            'completed_at':    r[8],
            'result':          _json.loads(r[9]) if r[9] else None,
            'reboot_required': bool(r[10]),
            'created_at':      r[11],
            'updated_at':      r[12],
        } for r in rows],
    })




# ── Restored: /api/rmm/patch-jobs/<agent_id> ──
@app.route('/api/rmm/patch-jobs/<agent_id>', methods=['POST'])
@login_required
def api_rmm_patch_jobs_create(agent_id):
    """Approve a set of pending updates, creating a queued patch job."""
    import json as _json
    data       = request.get_json() or {}
    update_ids = data.get('update_ids') or []
    kb_ids     = data.get('kb_ids') or []
    titles     = data.get('titles') or []
    if not update_ids:
        return jsonify({'ok': False, 'error': 'update_ids required'}), 400
    db.session.execute(
        text("""INSERT INTO rmm_patch_job
                    (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
                VALUES (:aid, :uids, :kbids, :titles, 'queued', :uid, datetime('now', '-7 hours'))"""),
        {
            'aid':    agent_id,
            'uids':   _json.dumps(update_ids),
            'kbids':  _json.dumps(kb_ids),
            'titles': _json.dumps(titles),
            'uid':    current_user.id if hasattr(current_user, 'id') else None,
        }
    )
    db.session.commit()
    job_id = db.session.execute(text("SELECT last_insert_rowid()")).scalar()
    return jsonify({'ok': True, 'job_id': job_id})




# ── Restored: /api/rmm/cmd/<agent_id> ──
@app.route('/api/rmm/cmd/<agent_id>', methods=['POST'])
@login_required
def api_rmm_cmd(agent_id):
    """Proxy any JSON message to the connected agent via gateway send-msg."""
    import json as _json, urllib.request as _req, urllib.error as _err
    data = request.get_json(force=True) or {}
    session_id = data.get('session_id') or 0
    if not session_id:
        try:
            res = db.session.execute(
                text("INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at) VALUES (:aid, :uid, :reason, datetime('now', '-7 hours'))"),
                {'aid': None, 'uid': current_user.id, 'reason': data.get('type', 'cmd')}
            )
            db.session.commit()
            session_id = res.lastrowid
        except Exception:
            session_id = 0
    data['session_id'] = session_id
    payload = _json.dumps(data).encode()
    try:
        req = _req.Request(
            f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
            data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return jsonify({'ok': False, 'session_id': session_id, 'error': result.get('error', 'Gateway error')}), 502
    except Exception as e:
        return jsonify({'ok': False, 'session_id': session_id, 'error': str(e)}), 502
    return jsonify({'ok': True, 'session_id': session_id})




# ── Restored: /api/rmm/cmd-result/<agent_id>/<int:session_id> ──
@app.route('/api/rmm/cmd-result/<agent_id>/<int:session_id>')
@login_required
def api_rmm_cmd_result(agent_id, session_id):
    """Poll for latest agent response in rmm_event for a given session."""
    import json as _json
    row = db.session.execute(
        text("""SELECT event_type, data_json, created_at
                FROM rmm_event WHERE session_id = :sid AND actor_type = 'agent'
                ORDER BY id DESC LIMIT 1"""),
        {'sid': session_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'ready': False})
    try:
        data = _json.loads(row[1] or '{}')
    except Exception:
        data = {}
    return jsonify({'ok': True, 'ready': True, 'event_type': row[0], 'data': data, 'created_at': row[2]})




# ── Restored: /api/rmm/deploy-rustdesk/<agent_id> ──
@app.route('/api/rmm/deploy-rustdesk/<agent_id>', methods=['POST'])
@login_required
@manager_required
def api_rmm_deploy_rustdesk(agent_id):
    """Send a PowerShell script to the agent to install RustDesk via winget
    and configure it to use the internal relay server."""
    import json as _json, urllib.request as _req

    # Find the linked asset so we can update rustdesk_id after install
    row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = 1 LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'agent not found'}), 404

    server   = 'rust.corp.cirque.com'
    key      = 's18RB+OV0ctX3SuOIcXy6EeYqA+Elx25RODTGYBnyV8='

    # PowerShell: install RustDesk silently, then write server config
    ps = r"""
$ErrorActionPreference = 'Stop'
Write-Host 'Installing RustDesk via winget...'
try {
    winget install --id RustDesk.RustDesk --silent --accept-source-agreements --accept-package-agreements 2>&1
} catch { Write-Host "winget error: $_" }

Start-Sleep -Seconds 5

$cfg2 = @"
rendezvous_server = '{server}'
nat_type = 1
serial = 0

[options]
custom-rendezvous-server = '{server}'
key = '{key}'
relay-server = ''
api-server = ''
"@

# Write config for current user and for SYSTEM (service)
$paths = @(
    "$env:APPDATA\RustDesk\config",
    "$env:ProgramData\RustDesk\config",
    "C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config",
    "C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config"
)
foreach ($dir in $paths) {
    try {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $cfg2 | Set-Content -Path "$dir\RustDesk2.toml" -Encoding UTF8 -Force
        Write-Host "Wrote config to $dir"
    } catch { Write-Host "Skipped $dir: $_" }
}

# Restart RustDesk service if running
try {
    $svc = Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue
    if ($svc) { Restart-Service -Name 'RustDesk' -Force; Write-Host 'RustDesk service restarted' }
} catch { Write-Host "Service restart: $_" }

# Start RustDesk briefly to trigger peer ID generation, then close it
try {
    $rd = "$env:ProgramFiles\RustDesk\RustDesk.exe"
    if (-not (Test-Path $rd)) { $rd = "${env:ProgramFiles(x86)}\RustDesk\RustDesk.exe" }
    if (Test-Path $rd) {
        Start-Process $rd --minimized -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 8
        Stop-Process -Name 'RustDesk' -ErrorAction SilentlyContinue
    }
} catch { Write-Host "RustDesk launch: $_" }

# Read and report the peer ID so the tracker can store it
try {
    $tomlPaths = @(
        "$env:APPDATA\RustDesk\config\RustDesk.toml",
        "$env:ProgramData\RustDesk\config\RustDesk.toml",
        "C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config\RustDesk.toml"
    )
    foreach ($tp in $tomlPaths) {
        if (Test-Path $tp) {
            $raw = Get-Content $tp -Raw -ErrorAction SilentlyContinue
            if ($raw -match '(?m)^id\s*=\s*(\S+)') {
                Write-Host "RUSTDESK_ID=$($Matches[1].Trim())"
                break
            }
        }
    }
} catch { Write-Host "ID read: $_" }

Write-Host 'Done.'
""".replace('{server}', server).replace('{key}', key)

    try:
        session_row = db.session.execute(
            text("INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at) VALUES (:aid, :uid, 'Deploy RustDesk', datetime('now','-7 hours'))"),
            {'aid': row[0], 'uid': current_user.id}
        )
        db.session.commit()
        session_id = session_row.lastrowid
    except Exception:
        session_id = 0

    payload = _json.dumps({'type': 'run_script', 'shell': 'powershell', 'code': ps, 'timeout': 180, 'session_id': session_id}).encode()
    try:
        req = _req.Request(f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
                           data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        with _req.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return jsonify({'ok': False, 'error': result.get('error', 'Gateway error')}), 502
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

    return jsonify({'ok': True, 'session_id': session_id})




# ── Restored: /api/rmm/patch-jobs/<agent_id>/<int:job_id>/deploy ──
@app.route('/api/rmm/patch-jobs/<agent_id>/<int:job_id>/deploy', methods=['POST'])
@login_required
def api_rmm_patch_jobs_deploy(agent_id, job_id):
    """Push a queued patch job to the connected agent via the gateway."""
    import json as _json, urllib.request as _req, urllib.error as _err
    row = db.session.execute(
        text("SELECT update_ids, kb_ids, titles, status FROM rmm_patch_job WHERE id = :jid AND agent_id = :aid"),
        {'jid': job_id, 'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    if row[3] not in ('queued', 'failed'):
        return jsonify({'ok': False, 'error': f'Job is already {row[3]}'}), 400

    payload = _json.dumps({
        'job_id':     job_id,
        'update_ids': _json.loads(row[0] or '[]'),
        'kb_ids':     _json.loads(row[1] or '[]'),
        'titles':     _json.loads(row[2] or '[]'),
    }).encode()

    gw = RMM_GATEWAY_INTERNAL
    try:
        req = _req.Request(
            f"{gw}/deploy-patches/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return jsonify({'ok': False, 'error': result.get('error', 'Gateway error')}), 502
    except _err.HTTPError as e:
        body = e.read().decode()
        return jsonify({'ok': False, 'error': f'Gateway {e.code}: {body}'}), 502
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

    # Mark as deploying
    db.session.execute(
        text("UPDATE rmm_patch_job SET status='deploying', deployed_at=datetime('now', '-7 hours'), updated_at=datetime('now', '-7 hours') WHERE id=:jid"),
        {'jid': job_id}
    )
    db.session.commit()
    return jsonify({'ok': True, 'message': 'Deploy command sent to agent'})




# ── Restored: /api/rmm/rustdesk-sync/<agent_id> ──
@app.route('/api/rmm/rustdesk-sync/<agent_id>', methods=['POST'])
def api_rmm_rustdesk_sync(agent_id):
    """RMM agent reports its RustDesk peer ID so the tracker can auto-populate
    asset.rustdesk_id without manual entry.

    Auth: agent_id + token as query params (same as other agent endpoints).
    Body JSON: { "rustdesk_id": "<10-digit peer id>", "rustdesk_password": "<optional>" }
    """
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    peer_id = (data.get('rustdesk_id') or '').strip()
    if not peer_id:
        return jsonify({'ok': False, 'error': 'rustdesk_id is required'}), 400

    # Find the asset linked to this agent
    row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = 1 LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row or not row[0]:
        return jsonify({'ok': False, 'error': 'agent not linked to an asset'}), 404

    asset = Asset.query.get(row[0])
    if not asset:
        return jsonify({'ok': False, 'error': 'asset not found'}), 404

    changed = asset.rustdesk_id != peer_id
    asset.rustdesk_id = peer_id

    optional_pw = (data.get('rustdesk_password') or '').strip()
    if optional_pw:
        asset.rustdesk_password = optional_pw

    if changed:
        db.session.add(AssetHistory(
            asset_id=asset.id,
            action='RustDesk ID Updated',
            description=f'RMM agent auto-synced RustDesk peer ID: {peer_id}',
            user_id=None
        ))

    db.session.commit()
    return jsonify({'ok': True, 'asset_id': asset.id, 'changed': changed})




# ── Restored: /api/rmm/agent-info/<agent_id> ──
@app.route('/api/rmm/agent-info/<agent_id>')
def api_rmm_agent_info(agent_id):
    """Return asset info for a given agent (authenticated by token).
    Used by the tray app setup to populate tray_config.json."""
    token = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    row = db.session.execute(
        text("SELECT a.id, a.asset_tag, a.name FROM rmm_agent ra LEFT JOIN asset a ON a.id = ra.asset_id WHERE ra.agent_id = :aid AND ra.enabled = 1 LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'agent not found'}), 404
    return jsonify({'ok': True, 'asset_id': row[0], 'asset_tag': row[1] or '', 'hostname': row[2] or ''})




# ── Restored: /csat/<token>/<int:score> ──
@app.route('/csat/<token>/<int:score>')
def csat_response(token, score):
    """Public endpoint — reporter clicks 👍 or 👎 in the close email."""
    ticket = SupportTicket.query.filter_by(csat_token=token).first_or_404()
    if ticket.csat_score is None:
        ticket.csat_score = 1 if score >= 1 else 0
        ticket.csat_comment = request.args.get('comment', '').strip() or None
        db.session.commit()
        label = 'positive' if ticket.csat_score == 1 else 'negative'
        return f"""<!doctype html><html><head><meta charset=utf-8>
        <title>Feedback received</title>
        <style>body{{font-family:Arial,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f8f9fa;}}
        .box{{text-align:center;padding:40px;background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.1);max-width:400px;}}</style></head>
        <body><div class="box">
        <div style="font-size:64px">{'👍' if ticket.csat_score==1 else '👎'}</div>
        <h2>Thanks for your feedback!</h2>
        <p>Your {label} response has been recorded for ticket <strong>#{ticket.id}</strong>.</p>
        </div></body></html>"""
    return """<!doctype html><html><head><meta charset=utf-8><title>Already rated</title></head>
    <body style="font-family:Arial,sans-serif;text-align:center;padding:60px">
    <h2>Already recorded</h2><p>This ticket has already been rated. Thank you!</p></body></html>"""


# ── Asset check-out / check-in ──────────────────────────────────────────────



# ── Restored: /api/ai/test ──
@app.route('/api/ai/test', methods=['POST'])
@login_required
def api_ai_test():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    try:
        import openai as _oai
        db_conn = get_db()
        row = db_conn.execute("SELECT value FROM setting WHERE key='openai_api_key'").fetchone()
        model_row = db_conn.execute("SELECT value FROM setting WHERE key='openai_model'").fetchone()
        db_conn.close()
        api_key = row['value'] if row else None
        model   = (model_row['value'] if model_row else None) or 'gpt-4o'
        if not api_key:
            return jsonify({'ok': False, 'error': 'No API key saved. Add your key and hit Save first.'}), 400
        client = _oai.OpenAI(api_key=api_key)
        resp   = client.chat.completions.create(
            model=model,
            messages=[{'role':'user', 'content':'Reply with just the word OK.'}],
            max_tokens=5
        )
        reply = resp.choices[0].message.content.strip()
        return jsonify({'ok': True, 'model': model, 'reply': reply})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════════
# REPORT ROUTES
# ════════════════════════════════════════════════════════════════════════════════



# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'error': 'Not found'}), 404
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_server_error(e):
    db.session.rollback()
    logger.error(f'500 error on {request.path}: {e}')
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('errors/500.html', error=str(e)), 500


@app.errorhandler(403)
def forbidden(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'error': 'Forbidden'}), 403
    return render_template('errors/403.html'), 403


# ── AI Cross-Module Ask ────────────────────────────────────────────────────────

@app.route('/api/ai/ask', methods=['POST'])
@login_required
def api_ai_ask():
    """Cross-module AI question answering."""
    data = request.get_json(force=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question required'}), 400
    try:
        result = _ai_engine.ask_ai(question)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f'AI ask error: {e}')
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FOR RAW DB ACCESS (security/workflow/report routes)
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    import sqlite3 as _sq3
    _conn = _sq3.connect('/var/www/tracker/assets.db')
    _conn.row_factory = _sq3.Row
    return _conn


# ─────────────────────────────────────────────────────────────────────────────
# ALERT CENTER
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/alerts/center')
@login_required
def alert_center():
    users = db.session.execute(text("SELECT id, username, full_name FROM user ORDER BY username")).mappings().fetchall()
    return render_template('alert_center.html', users=[dict(u) for u in users])


@app.route('/api/alerts/rules', methods=['GET', 'POST'])
@login_required
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


@app.route('/api/alerts/rules/<int:rid>', methods=['PUT', 'DELETE'])
@login_required
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


@app.route('/api/alerts/rules/<int:rid>/toggle', methods=['POST'])
@login_required
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


@app.route('/api/alerts/log')
@login_required
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


@app.route('/api/notifications/bell')
@login_required
def api_notifications_bell():
    con = _alert_svc._get_db()
    try:
        unread = con.execute("SELECT COUNT(*) FROM notification_bell WHERE read_flag=0").fetchone()[0]
        recent = con.execute("SELECT * FROM notification_bell ORDER BY created_at DESC LIMIT 20").fetchall()
        return jsonify(ok=True, unread=unread, items=[dict(r) for r in recent])
    finally:
        con.close()


@app.route('/api/notifications/mark-read', methods=['POST'])
@login_required
def api_notifications_mark_read():
    con = _alert_svc._get_db()
    try:
        ids = (request.get_json(force=True) or {}).get('ids')
        if ids:
            con.execute(f"UPDATE notification_bell SET read_flag=1 WHERE id IN ({','.join('?'*len(ids))})", ids)
        else:
            con.execute("UPDATE notification_bell SET read_flag=1")
        con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@app.route('/api/settings/<key>', methods=['GET'])
@login_required
def api_setting_get(key):
    allowed = {'teams_webhook_url'}
    if key not in allowed:
        return jsonify(ok=False, error='Not allowed'), 403
    s = db.session.execute(text("SELECT value FROM setting WHERE key=:k"), {'k': key}).fetchone()
    return jsonify(ok=True, key=key, value=s[0] if s else '')


@app.route('/api/settings', methods=['POST'])
@login_required
def api_setting_set():
    data = request.get_json(force=True) or {}
    key  = data.get('key', '')
    val  = data.get('value', '')
    allowed = {'teams_webhook_url'}
    if key not in allowed:
        return jsonify(ok=False, error='Not allowed'), 403
    existing = db.session.execute(text("SELECT id FROM setting WHERE key=:k"), {'k': key}).fetchone()
    if existing:
        db.session.execute(text("UPDATE setting SET value=:v WHERE key=:k"), {'k': key, 'v': val})
    else:
        db.session.execute(text("INSERT INTO setting (key, value) VALUES (:k, :v)"), {'k': key, 'v': val})
    db.session.commit()
    return jsonify(ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# VULNERABILITY DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/vulnerabilities')
@login_required
def vulnerability_dashboard():
    con = _alert_svc._get_db()
    try:
        counts = {s: 0 for s in ('Critical', 'High', 'Medium', 'Low')}
        for row in con.execute("""
            SELECT vc.severity, COUNT(DISTINCT vc.cve_id) as c
            FROM vulnerability_cache vc
            INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status='Open'
            GROUP BY vc.severity
        """).fetchall():
            sev = row['severity']
            if sev in counts:
                counts[sev] = row['c']
        last_sync_raw = con.execute("SELECT MAX(synced_at) FROM vulnerability_cache").fetchone()[0]
        device_count = con.execute("SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
        open_count   = con.execute("SELECT COUNT(*) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
    finally:
        con.close()
    last_sync = None
    if last_sync_raw:
        try:
            _MST = timezone(timedelta(hours=-7))
            _dt  = datetime.fromisoformat(last_sync_raw.replace('Z', '+00:00'))
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=timezone.utc)
            last_sync = _dt.astimezone(_MST).strftime('%Y-%m-%d %H:%M') + ' MST'
        except Exception:
            last_sync = last_sync_raw[:16]
    return render_template('vulnerability_dashboard.html',
                           counts=counts, last_sync=last_sync,
                           device_count=device_count, open_count=open_count)


@app.route('/api/vulnerabilities/sync', methods=['POST'])
@login_required
def api_vuln_sync():
    _app = app
    def _bg():
        with _app.app_context():
            vc, dc, err = _alert_svc.sync_defender_vulnerabilities()
            if err:
                logger.error(f'Background Defender sync error: {err}')
            else:
                logger.info(f'Background Defender sync complete: {vc} CVEs, {dc} device exposures')
    threading.Thread(target=_bg, daemon=True, name='defender-sync').start()
    return jsonify(ok=True, message='Sync started')


@app.route('/api/vulnerabilities/stats')
@login_required
def api_vuln_stats():
    con = _alert_svc._get_db()
    try:
        counts = {}
        for row in con.execute("""
            SELECT vc.severity, COUNT(DISTINCT vc.cve_id) as c
            FROM vulnerability_cache vc
            INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status='Open'
            GROUP BY vc.severity
        """).fetchall():
            counts[row['severity']] = row['c']
        devices = con.execute("SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
        open_exp = con.execute("SELECT COUNT(*) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
        last_sync_raw = con.execute("SELECT MAX(synced_at) FROM vulnerability_cache").fetchone()[0]
        last_sync_mst = None
        if last_sync_raw:
            try:
                _MST = timezone(timedelta(hours=-7))
                _dt  = datetime.fromisoformat(last_sync_raw.replace('Z', '+00:00'))
                if _dt.tzinfo is None:
                    _dt = _dt.replace(tzinfo=timezone.utc)
                last_sync_mst = _dt.astimezone(_MST).strftime('%Y-%m-%d %H:%M') + ' MST'
            except Exception:
                last_sync_mst = last_sync_raw[:16]
        return jsonify(Critical=counts.get('Critical', 0), High=counts.get('High', 0),
                       Medium=counts.get('Medium', 0), Low=counts.get('Low', 0),
                       devices=devices, open_exposures=open_exp, last_sync=last_sync_mst)
    finally:
        con.close()


@app.route('/api/vulnerabilities')
@login_required
def api_vulnerabilities():
    con = _alert_svc._get_db()
    try:
        sev   = request.args.get('severity')
        limit = int(request.args.get('limit', 500))
        if sev:
            rows = con.execute(
                """SELECT vc.*, COUNT(DISTINCT dv.asset_id) AS device_count
                   FROM vulnerability_cache vc
                   INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status='Open'
                   WHERE vc.severity=?
                   GROUP BY vc.cve_id
                   ORDER BY vc.cvss DESC LIMIT ?""",
                (sev, limit)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT vc.*, COUNT(DISTINCT dv.asset_id) AS device_count
                   FROM vulnerability_cache vc
                   INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status='Open'
                   GROUP BY vc.cve_id
                   ORDER BY CASE vc.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                             WHEN 'Medium' THEN 3 ELSE 4 END, vc.cvss DESC LIMIT ?""",
                (limit,)
            ).fetchall()
        return jsonify(ok=True, vulnerabilities=[dict(r) for r in rows])
    finally:
        con.close()


@app.route('/api/vulnerabilities/devices')
@login_required
def api_vuln_devices():
    con = _alert_svc._get_db()
    try:
        cve_id   = request.args.get('cve_id')
        asset_id = request.args.get('asset_id')
        if cve_id:
            rows = con.execute(
                """SELECT dv.*, a.name as asset_name,
                          COALESCE(a.hostname, a.name) as display_name
                   FROM device_vulnerability dv
                   LEFT JOIN asset a ON a.id = dv.asset_id
                   WHERE dv.cve_id=? ORDER BY dv.severity""",
                (cve_id,)
            ).fetchall()
        elif asset_id:
            rows = con.execute(
                """SELECT dv.*, vc.name as vuln_name, vc.description
                   FROM device_vulnerability dv
                   LEFT JOIN vulnerability_cache vc ON vc.cve_id = dv.cve_id
                   WHERE dv.asset_id=? ORDER BY CASE dv.severity
                   WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END""",
                (asset_id,)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT dv.*, a.name as asset_name, a.hostname
                   FROM device_vulnerability dv
                   LEFT JOIN asset a ON a.id = dv.asset_id
                   WHERE dv.status='Open'
                   ORDER BY CASE dv.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                            WHEN 'Medium' THEN 3 ELSE 4 END
                   LIMIT 500"""
            ).fetchall()
        return jsonify(ok=True, devices=[dict(r) for r in rows])
    finally:
        con.close()


@app.route('/api/vulnerabilities/<cve_id>/status', methods=['PUT'])
@login_required
def api_vuln_status(cve_id):
    d        = request.get_json(force=True)
    con      = _alert_svc._get_db()
    username = current_user.username if current_user.is_authenticated else 'system'
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    try:
        asset_id = d.get('asset_id')
        status   = d.get('status', 'Open')
        note     = d.get('remediation_note', '')
        plan     = d.get('plan_date')
        if asset_id:
            con.execute(
                """UPDATE device_vulnerability
                   SET status=?, remediation_note=?, plan_date=?, updated_at=?, updated_by=?
                   WHERE cve_id=? AND asset_id=?""",
                (status, note, plan, now_str, username, cve_id, asset_id)
            )
            con.commit()
        else:
            con.execute(
                """UPDATE device_vulnerability
                   SET status=?, remediation_note=?, plan_date=?, updated_at=?, updated_by=?
                   WHERE cve_id=?""",
                (status, note, plan, now_str, username, cve_id)
            )
            con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@app.route('/api/vulnerabilities/<cve_id>/deploy', methods=['POST'])
@login_required
def api_vuln_deploy(cve_id):
    data     = request.get_json(force=True) or {}
    asset_id = data.get('asset_id')
    username = current_user.username if current_user.is_authenticated else 'system'
    con      = _alert_svc._get_db()
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    try:
        if asset_id:
            rows = con.execute(
                """SELECT dv.asset_id, ra.agent_id
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = 1
                   WHERE dv.cve_id = ? AND dv.asset_id = ? LIMIT 1""",
                (cve_id, asset_id)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT DISTINCT dv.asset_id, ra.agent_id
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = 1
                   WHERE dv.cve_id = ? AND dv.status = 'Open'""",
                (cve_id,)
            ).fetchall()
    finally:
        con.close()
    if not rows:
        return jsonify(ok=False, error='No connected agents found for this CVE'), 404
    dispatched = []
    errors     = []
    for (aid, agent_id) in rows:
        try:
            db.session.execute(
                text("""INSERT INTO cve_patch_job
                        (asset_id, agent_id, cve_id, status, deployed_by, deployed_at, updated_at, created_at)
                        VALUES (:aid, :agent, :cve, 'queued', :who, :now, :now, :now)"""),
                {'aid': aid, 'agent': agent_id, 'cve': cve_id, 'who': username, 'now': now_str}
            )
            db.session.commit()
            job_id = db.session.execute(text("SELECT last_insert_rowid()")).scalar()
        except Exception as e:
            errors.append({'agent_id': agent_id, 'error': f'DB error: {e}'})
            continue
        payload = json.dumps({'job_id': job_id, 'cve_ids': [cve_id]}).encode()
        try:
            import urllib.request as _req
            req = _req.Request(
                f"{RMM_GATEWAY_INTERNAL}/deploy-cve-patches/{agent_id}",
                data=payload, headers={'Content-Type': 'application/json'}, method='POST'
            )
            with _req.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            if result.get('ok'):
                dispatched.append({'asset_id': aid, 'agent_id': agent_id, 'job_id': job_id})
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='deploying', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
            else:
                errors.append({'agent_id': agent_id, 'error': result.get('error', 'gateway error')})
        except Exception as e:
            errors.append({'agent_id': agent_id, 'error': str(e)})
    return jsonify(ok=True, dispatched=dispatched, errors=errors, total=len(rows), sent=len(dispatched))


@app.route('/api/vulnerabilities/cve-patch-jobs')
@login_required
def api_cve_patch_jobs():
    cve_id   = request.args.get('cve_id')
    asset_id = request.args.get('asset_id')
    if not cve_id:
        return jsonify(ok=False, error='cve_id required'), 400
    con = _alert_svc._get_db()
    try:
        if asset_id:
            rows = con.execute(
                """SELECT j.*, a.name as asset_name FROM cve_patch_job j
                   LEFT JOIN asset a ON a.id = j.asset_id
                   WHERE j.cve_id = ? AND j.asset_id = ? ORDER BY j.id DESC LIMIT 1""",
                (cve_id, asset_id)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT j.*, a.name as asset_name FROM cve_patch_job j
                   LEFT JOIN asset a ON a.id = j.asset_id
                   WHERE j.cve_id = ? ORDER BY j.id DESC LIMIT 50""",
                (cve_id,)
            ).fetchall()
        return jsonify(ok=True, jobs=[dict(r) for r in rows])
    finally:
        con.close()


# ════════════════════════════════════════════════════════════════════════════════
# WORKFLOW ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@app.route('/workflows')
@login_required
def workflows():
    if current_user.role != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('index'))
    return render_template('workflows.html')


@app.route('/api/workflows', methods=['GET'])
@login_required
def api_workflows_list():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, description, trigger_type, enabled, created_by, created_at FROM workflow_definitions ORDER BY id DESC"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/workflows', methods=['POST'])
@login_required
def api_workflow_create():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    data = request.get_json()
    if not data.get('name') or not data.get('trigger_type'):
        return jsonify({'error': 'name and trigger_type required'}), 400
    db_conn = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cur = db_conn.execute(
        """INSERT INTO workflow_definitions
           (name, description, trigger_type, trigger_config, nodes, edges, enabled, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,1,?,?,?)""",
        (data['name'], data.get('description', ''), data['trigger_type'],
         json.dumps(data.get('trigger_config', {})),
         json.dumps(data.get('nodes', [])),
         json.dumps(data.get('edges', [])),
         current_user.username, now, now)
    )
    wf_id = cur.lastrowid
    db_conn.commit(); db_conn.close()
    return jsonify({'id': wf_id, 'ok': True})


@app.route('/api/workflows/<int:wf_id>', methods=['GET'])
@login_required
def api_workflow_get(wf_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM workflow_definitions WHERE id=?", (wf_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    r = dict(row)
    r['nodes']          = json.loads(r['nodes'] or '[]')
    r['edges']          = json.loads(r['edges'] or '[]')
    r['trigger_config'] = json.loads(r['trigger_config'] or '{}')
    return jsonify(r)


@app.route('/api/workflows/<int:wf_id>', methods=['PUT'])
@login_required
def api_workflow_update(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    data    = request.get_json()
    now     = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db_conn = get_db()
    db_conn.execute(
        """UPDATE workflow_definitions
           SET name=?, description=?, trigger_type=?, trigger_config=?,
               nodes=?, edges=?, enabled=?, updated_at=?
           WHERE id=?""",
        (data.get('name'), data.get('description', ''), data.get('trigger_type'),
         json.dumps(data.get('trigger_config', {})),
         json.dumps(data.get('nodes', [])),
         json.dumps(data.get('edges', [])),
         1 if data.get('enabled', True) else 0,
         now, wf_id)
    )
    db_conn.commit(); db_conn.close()
    return jsonify({'ok': True})


@app.route('/api/workflows/<int:wf_id>', methods=['DELETE'])
@login_required
def api_workflow_delete(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    db_conn = get_db()
    db_conn.execute("DELETE FROM workflow_definitions WHERE id=?", (wf_id,))
    db_conn.commit(); db_conn.close()
    return jsonify({'ok': True})


@app.route('/api/workflows/<int:wf_id>/toggle', methods=['POST'])
@login_required
def api_workflow_toggle(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    db_conn = get_db()
    row = db_conn.execute("SELECT enabled FROM workflow_definitions WHERE id=?", (wf_id,)).fetchone()
    if not row:
        db_conn.close()
        return jsonify({'error': 'Not found'}), 404
    new_val = 0 if row['enabled'] else 1
    db_conn.execute("UPDATE workflow_definitions SET enabled=? WHERE id=?", (new_val, wf_id))
    db_conn.commit(); db_conn.close()
    return jsonify({'enabled': new_val})


@app.route('/api/workflows/<int:wf_id>/run', methods=['POST'])
@login_required
def api_workflow_run(wf_id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    ctx = request.get_json() or {}
    ctx['triggered_by'] = current_user.username
    try:
        run_id = _wf_engine.execute_workflow(wf_id, ctx)
        return jsonify({'ok': True, 'run_id': run_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/workflows/<int:wf_id>/runs', methods=['GET'])
@login_required
def api_workflow_runs(wf_id):
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, status, started_at, completed_at, error FROM workflow_runs WHERE workflow_id=? ORDER BY id DESC LIMIT 50",
        (wf_id,)
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/workflows/ai-generate', methods=['POST'])
@login_required
def api_workflow_ai_generate():
    data   = request.get_json(force=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'ok': False, 'error': 'prompt required'}), 400
    result = _ai_engine.generate_workflow(prompt)
    return jsonify(result)


@app.route('/api/workflows/runs/<int:run_id>/steps', methods=['GET'])
@login_required
def api_workflow_run_steps(run_id):
    db_conn = get_db()
    steps = db_conn.execute(
        "SELECT * FROM workflow_run_steps WHERE run_id=? ORDER BY id", (run_id,)
    ).fetchall()
    db_conn.close()
    result = []
    for s in steps:
        r = dict(s)
        r['output_data'] = json.loads(r.get('output_data') or '{}')
        result.append(r)
    return jsonify(result)


# ════════════════════════════════════════════════════════════════════════════════
# AI ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@app.route('/api/ai/ticket/<int:ticket_id>/suggest', methods=['POST'])
@login_required
def api_ai_ticket_suggest(ticket_id):
    try:
        result = _ai_engine.suggest_ticket_resolution(ticket_id)
        if result.get('suggestion'):
            try:
                result['parsed'] = json.loads(result['suggestion'])
            except Exception:
                result['parsed'] = {'diagnosis': result['suggestion']}
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/suggestions/<int:sug_id>/apply', methods=['POST'])
@login_required
def api_ai_suggestion_apply(sug_id):
    try:
        _ai_engine.apply_ticket_suggestion(sug_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/ai/suggestions/<int:sug_id>/dismiss', methods=['POST'])
@login_required
def api_ai_suggestion_dismiss(sug_id):
    _ai_engine.dismiss_ticket_suggestion(sug_id)
    return jsonify({'ok': True})


@app.route('/api/ai/ticket/<int:ticket_id>/suggestions', methods=['GET'])
@login_required
def api_ai_ticket_suggestions(ticket_id):
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT * FROM ai_ticket_suggestions WHERE ticket_id=? ORDER BY id DESC LIMIT 10", (ticket_id,)
    ).fetchall()
    db_conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d['parsed'] = json.loads(d['suggestion'])
        except Exception:
            d['parsed'] = {'diagnosis': d['suggestion']}
        result.append(d)
    return jsonify(result)


@app.route('/api/ai/security-summary', methods=['GET'])
@login_required
def api_ai_security_summary_get():
    summary = _ai_engine.get_latest_security_summary()
    return jsonify(summary or {})


@app.route('/api/ai/security-summary/generate', methods=['POST'])
@login_required
def api_ai_security_summary_generate():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    try:
        result = _ai_engine.generate_security_summary()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/settings', methods=['GET'])
@login_required
def api_ai_settings_get():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    db_conn = get_db()
    keys = ['openai_api_key', 'openai_model', 'ai_ticket_enabled',
            'ai_ticket_auto_mode', 'ai_security_monitor_enabled']
    result = {}
    for key in keys:
        row = db_conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        val = row['value'] if row else ''
        if key == 'openai_api_key' and val:
            val = val[:8] + '…' + val[-4:]
        result[key] = val
    db_conn.close()
    return jsonify(result)


@app.route('/api/ai/settings', methods=['POST'])
@login_required
def api_ai_settings_save():
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin only'}), 403
    data    = request.get_json()
    db_conn = get_db()
    allowed = ['openai_api_key', 'openai_model', 'ai_ticket_enabled',
               'ai_ticket_auto_mode', 'ai_security_monitor_enabled']
    for key in allowed:
        if key in data:
            if key == 'openai_api_key' and '…' in str(data[key]):
                continue
            existing = db_conn.execute("SELECT id FROM setting WHERE key=?", (key,)).fetchone()
            if existing:
                db_conn.execute("UPDATE setting SET value=? WHERE key=?", (data[key], key))
            else:
                db_conn.execute("INSERT INTO setting (key, value) VALUES (?,?)", (key, data[key]))
    db_conn.commit(); db_conn.close()
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════════════════════
# REPORT ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@app.route('/reports/advanced')
@login_required
def reports_advanced():
    return render_template('reports_advanced.html')


@app.route('/api/reports/templates', methods=['GET'])
@login_required
def api_report_templates():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, description, report_type, is_builtin, created_at FROM report_templates ORDER BY is_builtin DESC, name"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reports/runs', methods=['GET'])
@login_required
def api_report_runs_list():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, report_type, status, row_count, file_csv, file_pdf, generated_by, generated_at, completed_at FROM report_runs ORDER BY id DESC LIMIT 100"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reports/run', methods=['POST'])
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


@app.route('/api/reports/runs/<int:run_id>', methods=['GET'])
@login_required
def api_report_run_status(run_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM report_runs WHERE id=?", (run_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@app.route('/api/reports/runs/<int:run_id>/data', methods=['GET'])
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


@app.route('/api/reports/download/<string:filename>')
@login_required
def api_report_download(filename):
    safe = os.path.basename(filename)
    path = os.path.join(_report_engine.REPORT_DIR, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    mimetype = 'text/csv' if safe.endswith('.csv') else 'application/pdf' if safe.endswith('.pdf') else 'text/html'
    return send_file(path, as_attachment=True, download_name=safe, mimetype=mimetype)


# Start alert evaluator and workflow schedule runner
try:
    _alert_svc.start_background_thread()
    _wf_engine.start_schedule_runner()
except Exception as _svc_err:
    logger.warning(f'Background service startup warning: {_svc_err}')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Verify license on startup
        license_service.verify_on_startup()
        
        # Start periodic license checks
        license_service.start_periodic_check()
        
    app.run(host='0.0.0.0', port=5000, debug=True)
