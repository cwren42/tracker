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
    card_number = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Active Directory fields (AD is master)
    sam_account_name = db.Column(db.String(100))
    ad_guid = db.Column(db.String(38), unique=True, nullable=True)
    ad_dn = db.Column(db.String(500))
    ad_enabled = db.Column(db.Boolean, nullable=True)   # True=active, False=disabled in AD
    ad_last_sync = db.Column(db.DateTime)
    # Microsoft 365 validation fields
    m365_id = db.Column(db.String(100))
    m365_account_enabled = db.Column(db.Boolean, nullable=True)
    m365_validated_at = db.Column(db.DateTime)
    m365_licenses_json = db.Column(db.Text)        # JSON list of M365 license SKUs
    m365_licenses_synced_at = db.Column(db.DateTime)
    # Display / org fields
    is_visible = db.Column(db.Boolean, default=True, nullable=False, server_default='true')
    location = db.Column(db.String(100))            # Cirque US / Cirque Taiwan / Cirque China
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

    @property
    def computed_eol_date(self):
        """Compute EOL date from purchase_date + expected_life_years."""
        if self.purchase_date and self.expected_life_years:
            from datetime import timedelta
            return self.purchase_date + timedelta(days=int(self.expected_life_years * 365.25))
        return None


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
    due_date = db.Column(db.Date)

    # Many-to-many tags (via ticket_tag_link association table)
    tags = db.relationship('TicketTag', secondary='ticket_tag_link', lazy='subquery',
                           backref=db.backref('tickets', lazy=True))
    # Watchers (users who get notified on updates)
    watchers = db.relationship('TicketWatcher', lazy='dynamic',
                               primaryjoin='SupportTicket.id == TicketWatcher.ticket_id',
                               cascade='all, delete-orphan')
    # Related ticket links (one-directional entries)
    links = db.relationship('TicketLink', lazy='dynamic',
                            primaryjoin='SupportTicket.id == TicketLink.ticket_id',
                            cascade='all, delete-orphan')
    _SLA_HOURS = {'Low': 120, 'Normal': 72, 'High': 24, 'Urgent': 4}

    @property
    def sla_target_hours(self):
        return self._SLA_HOURS.get(self.priority, 72)

    @property
    def sla_elapsed_hours(self):
        if not self.created_at:
            return 0.0
        return (datetime.utcnow() - self.created_at).total_seconds() / 3600

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
    is_internal = db.Column(db.Boolean, default=False, nullable=False)
    is_reply = db.Column(db.Boolean, default=False, nullable=False)
    reply_to = db.Column(db.Text)   # email address the reply was sent to
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


# Association table for ticket ↔ tag (many-to-many)
ticket_tag_link = db.Table(
    'ticket_tag_link',
    db.Column('ticket_id', db.Integer, db.ForeignKey('support_ticket.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('ticket_tag.id', ondelete='CASCADE'), primary_key=True),
)


class TicketTag(db.Model):
    __tablename__ = 'ticket_tag'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    color = db.Column(db.String(7), nullable=False, default='#6c757d')


class TicketWatcher(db.Model):
    __tablename__ = 'ticket_watcher'
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id', ondelete='CASCADE'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True)
    user = db.relationship('User', foreign_keys=[user_id])


class TicketLink(db.Model):
    __tablename__ = 'ticket_link'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id', ondelete='CASCADE'), nullable=False)
    linked_ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id', ondelete='CASCADE'), nullable=False)
    link_type = db.Column(db.String(20), nullable=False, default='related')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    linked_ticket = db.relationship('SupportTicket', foreign_keys=[linked_ticket_id])


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


class ISMSDocument(db.Model):
    __tablename__ = 'isms_document'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    doc_type = db.Column(db.String(50), default='policy')
    category = db.Column(db.String(100))
    status = db.Column(db.String(20), default='draft')
    source_path = db.Column(db.String(500), unique=True)
    current_version_id = db.Column(db.Integer, db.ForeignKey('isms_document_version.id'))
    created_by = db.Column(db.String(100))
    updated_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = db.relationship(
        'ISMSDocumentVersion',
        backref='document',
        lazy=True,
        cascade='all, delete-orphan',
        foreign_keys='ISMSDocumentVersion.document_id',
        order_by='desc(ISMSDocumentVersion.version_number)',
    )
    current_version = db.relationship(
        'ISMSDocumentVersion',
        foreign_keys=[current_version_id],
        post_update=True,
    )


class ISMSDocumentVersion(db.Model):
    __tablename__ = 'isms_document_version'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('isms_document.id', ondelete='CASCADE'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    markdown_body = db.Column(db.Text, nullable=False)
    rendered_html = db.Column(db.Text)
    change_summary = db.Column(db.Text)
    is_restore = db.Column(db.Boolean, default=False)
    restored_from_version_id = db.Column(db.Integer, db.ForeignKey('isms_document_version.id', ondelete='SET NULL'))
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    restored_from_version = db.relationship(
        'ISMSDocumentVersion',
        remote_side=[id],
        foreign_keys=[restored_from_version_id],
    )


class ISMSExportRun(db.Model):
    __tablename__ = 'isms_export_run'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('isms_document.id', ondelete='CASCADE'), nullable=False)
    document_version_id = db.Column(db.Integer, db.ForeignKey('isms_document_version.id', ondelete='SET NULL'))
    export_format = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending')
    output_path = db.Column(db.String(500))
    generated_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    export_document = db.relationship('ISMSDocument', foreign_keys=[document_id])
    export_version = db.relationship('ISMSDocumentVersion', foreign_keys=[document_version_id])


class SOC2ReadinessItem(db.Model):
    __tablename__ = 'soc2_readiness_item'

    id = db.Column(db.Integer, primary_key=True)
    item_key = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    domain = db.Column(db.String(120))
    audit_alignment = db.Column(db.Text)
    priority = db.Column(db.String(50), default='P2-High')
    status = db.Column(db.String(50), default='Not In Place', index=True)
    owner = db.Column(db.String(200))
    frequency = db.Column(db.String(100))
    source_type = db.Column(db.String(50), default='manual')
    source_reference = db.Column(db.String(500))
    manual_reference = db.Column(db.String(500))
    evidence_reference = db.Column(db.String(500))
    next_step = db.Column(db.Text)
    notes = db.Column(db.Text)
    due_date = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    updates = db.relationship(
        'SOC2ReadinessUpdate',
        backref='item',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='desc(SOC2ReadinessUpdate.created_at)',
    )
    audit_findings = db.relationship(
        'SOC2InternalAuditFinding',
        backref='linked_readiness_item',
        lazy=True,
        foreign_keys='SOC2InternalAuditFinding.readiness_item_id',
        order_by='desc(SOC2InternalAuditFinding.created_at)',
    )


class SOC2ReadinessUpdate(db.Model):
    __tablename__ = 'soc2_readiness_update'

    id = db.Column(db.Integer, primary_key=True)
    readiness_item_id = db.Column(db.Integer, db.ForeignKey('soc2_readiness_item.id', ondelete='CASCADE'), nullable=False)
    update_type = db.Column(db.String(50), default='status_change')
    previous_status = db.Column(db.String(50))
    new_status = db.Column(db.String(50))
    note = db.Column(db.Text)
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SOC2InternalAudit(db.Model):
    __tablename__ = 'soc2_internal_audit'

    id = db.Column(db.Integer, primary_key=True)
    audit_key = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    scope = db.Column(db.Text)
    status = db.Column(db.String(50), default='Planned', index=True)
    owner = db.Column(db.String(200))
    audit_period_start = db.Column(db.Date)
    audit_period_end = db.Column(db.Date)
    planned_date = db.Column(db.Date)
    performed_date = db.Column(db.Date)
    summary = db.Column(db.Text)
    evidence_reference = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    findings = db.relationship(
        'SOC2InternalAuditFinding',
        backref='audit',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='desc(SOC2InternalAuditFinding.created_at)',
    )


class SOC2InternalAuditFinding(db.Model):
    __tablename__ = 'soc2_internal_audit_finding'

    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.Integer, db.ForeignKey('soc2_internal_audit.id', ondelete='CASCADE'), nullable=False)
    readiness_item_id = db.Column(db.Integer, db.ForeignKey('soc2_readiness_item.id', ondelete='SET NULL'))
    finding_key = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(50), default='Minor')
    status = db.Column(db.String(50), default='Open', index=True)
    criteria_reference = db.Column(db.String(200))
    owner = db.Column(db.String(200))
    due_date = db.Column(db.Date)
    description = db.Column(db.Text)
    recommendation = db.Column(db.Text)
    evidence_reference = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SOC2Vendor(db.Model):
    __tablename__ = 'soc2_vendor'

    id = db.Column(db.Integer, primary_key=True)
    vendor_key = db.Column(db.String(120), unique=True, nullable=False)
    vendor_name = db.Column(db.String(200), nullable=False)
    service_description = db.Column(db.Text)
    vendor_type = db.Column(db.String(100))
    criticality = db.Column(db.String(50), default='Medium')
    risk_level = db.Column(db.String(50), default='Medium')
    owner = db.Column(db.String(200))
    data_access_scope = db.Column(db.Text)
    contract_status = db.Column(db.String(50), default='Active')
    assurance_status = db.Column(db.String(100))
    last_review_date = db.Column(db.Date)
    next_review_date = db.Column(db.Date)
    evidence_reference = db.Column(db.String(500))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reviews = db.relationship(
        'SOC2VendorReview',
        backref='vendor',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='desc(SOC2VendorReview.review_date)',
    )


class SOC2VendorReview(db.Model):
    __tablename__ = 'soc2_vendor_review'

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('soc2_vendor.id', ondelete='CASCADE'), nullable=False)
    review_date = db.Column(db.Date, nullable=False)
    review_type = db.Column(db.String(100), default='Annual Review')
    status = db.Column(db.String(50), default='Completed')
    reviewer = db.Column(db.String(200))
    summary = db.Column(db.Text)
    findings = db.Column(db.Text)
    evidence_reference = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SOC2ManagementReview(db.Model):
    __tablename__ = 'soc2_management_review'

    id = db.Column(db.Integer, primary_key=True)
    review_key = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    review_date = db.Column(db.Date, nullable=False)
    review_period_start = db.Column(db.Date)
    review_period_end = db.Column(db.Date)
    chairperson = db.Column(db.String(200))
    minute_taker = db.Column(db.String(200))
    location = db.Column(db.String(200))
    status = db.Column(db.String(50), default='Planned', index=True)
    attendees = db.Column(db.Text)
    agenda_summary = db.Column(db.Text)
    decisions_summary = db.Column(db.Text)
    effectiveness_summary = db.Column(db.Text)
    resource_summary = db.Column(db.Text)
    evidence_reference = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    actions = db.relationship(
        'SOC2ManagementReviewAction',
        backref='review',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='desc(SOC2ManagementReviewAction.created_at)',
    )


class SOC2ManagementReviewAction(db.Model):
    __tablename__ = 'soc2_management_review_action'

    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey('soc2_management_review.id', ondelete='CASCADE'), nullable=False)
    action_key = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    owner = db.Column(db.String(200))
    due_date = db.Column(db.Date)
    status = db.Column(db.String(50), default='Open', index=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SOC2SecurityTrainingRecord(db.Model):
    __tablename__ = 'soc2_security_training_record'

    id = db.Column(db.Integer, primary_key=True)
    record_key = db.Column(db.String(120), unique=True, nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id', ondelete='SET NULL'))
    trainee_name = db.Column(db.String(200), nullable=False)
    trainee_email = db.Column(db.String(200))
    department = db.Column(db.String(100))
    role_title = db.Column(db.String(100))
    training_date = db.Column(db.Date, nullable=False)
    training_topic = db.Column(db.String(200), nullable=False)
    provider_method = db.Column(db.String(200))
    duration = db.Column(db.String(100))
    completion_status = db.Column(db.String(50), default='Completed', index=True)
    score = db.Column(db.Integer)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])


class SOC2PolicyAcknowledgement(db.Model):
    __tablename__ = 'soc2_policy_acknowledgement'

    id = db.Column(db.Integer, primary_key=True)
    acknowledgement_key = db.Column(db.String(120), unique=True, nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id', ondelete='SET NULL'))
    person_name = db.Column(db.String(200), nullable=False)
    person_email = db.Column(db.String(200))
    department = db.Column(db.String(100))
    acknowledgement_type = db.Column(db.String(100), default='Security Policy')
    policy_name = db.Column(db.String(200), nullable=False)
    policy_version = db.Column(db.String(50))
    acknowledged_on = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default='Acknowledged', index=True)
    evidence_reference = db.Column(db.String(500))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])


class SOC2PhishingCampaign(db.Model):
    __tablename__ = 'soc2_phishing_campaign'

    id = db.Column(db.Integer, primary_key=True)
    campaign_key = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    campaign_date = db.Column(db.Date, nullable=False)
    provider = db.Column(db.String(200))
    scope = db.Column(db.String(200))
    status = db.Column(db.String(50), default='Completed', index=True)
    scenario = db.Column(db.Text)
    follow_up_training_topic = db.Column(db.String(200))
    summary = db.Column(db.Text)
    evidence_reference = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    results = db.relationship(
        'SOC2PhishingResult',
        backref='campaign',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='SOC2PhishingResult.employee_name.asc()',
    )


class SOC2PhishingResult(db.Model):
    __tablename__ = 'soc2_phishing_result'

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('soc2_phishing_campaign.id', ondelete='CASCADE'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id', ondelete='SET NULL'))
    result_key = db.Column(db.String(120), unique=True, nullable=False)
    employee_name = db.Column(db.String(200), nullable=False)
    employee_email = db.Column(db.String(200))
    department = db.Column(db.String(100))
    delivered = db.Column(db.Boolean, default=True)
    opened = db.Column(db.Boolean, default=True)
    clicked = db.Column(db.Boolean, default=False)
    reported = db.Column(db.Boolean, default=False)
    training_completed = db.Column(db.Boolean, default=False)
    training_completed_on = db.Column(db.Date)
    outcome = db.Column(db.String(100), default='Completed Follow-up Training', index=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])


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

# ─── Windows Backup Models ────────────────────────────────────────────────────

class RmmBackupNas(db.Model):
    __tablename__ = 'rmm_backup_nas'
    id = db.Column(db.BigInteger, primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)   # "ioSafe NL Series", "UniFi UNAS Pro"
    nas_type = db.Column(db.Text, nullable=False, default='smb')
    unc_path = db.Column(db.Text, nullable=False)             # \\SERVER\Share (SMB) or \\HOST for SFTP
    notes = db.Column(db.Text)
    enabled = db.Column(db.Boolean, default=True)
    # ── Isolated auth (non-domain credentials) ──────────────────────────────
    auth_method = db.Column(db.Text, nullable=False, default='smb_local')  # smb_local | sftp
    nas_username = db.Column(db.Text)          # local NAS account (not domain)
    nas_password_enc = db.Column(db.Text)      # Fernet-encrypted password
    sftp_port = db.Column(db.Integer, default=22)
    sftp_remote_path = db.Column(db.Text)      # e.g. /volume1/Backups (SFTP mode)
    # ────────────────────────────────────────────────────────────────────────
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RmmBackupPolicy(db.Model):
    __tablename__ = 'rmm_backup_policy'
    id = db.Column(db.BigInteger, primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)
    description = db.Column(db.Text)
    enabled = db.Column(db.Boolean, default=True)
    nas_unc_path = db.Column(db.Text, nullable=False, default='')
    nas_type = db.Column(db.Text, default='smb')
    include_paths = db.Column(db.Text, default='[]')          # JSON array
    exclude_extensions = db.Column(db.Text, default='[]')      # JSON array
    exclude_folders = db.Column(db.Text, default='[]')         # JSON array
    max_file_size_mb = db.Column(db.Integer, default=500)
    full_backup_interval_days = db.Column(db.Integer, default=7)
    retention_days = db.Column(db.Integer, default=30)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RmmAgentBackupPolicy(db.Model):
    __tablename__ = 'rmm_agent_backup_policy'
    id = db.Column(db.BigInteger, primary_key=True)
    agent_id = db.Column(db.Text, nullable=False, unique=True)
    policy_id = db.Column(db.BigInteger, db.ForeignKey('rmm_backup_policy.id', ondelete='SET NULL'))
    enabled = db.Column(db.Boolean, default=True)
    extra_paths = db.Column(db.Text, default='[]')             # JSON array
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    policy = db.relationship('RmmBackupPolicy', backref='agent_assignments')

class RmmBackupJob(db.Model):
    __tablename__ = 'rmm_backup_job'
    id = db.Column(db.BigInteger, primary_key=True)
    agent_id = db.Column(db.Text, nullable=False)
    job_type = db.Column(db.Text, default='full')              # full | incremental
    status = db.Column(db.Text, default='running')             # running | success | partial | failed
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    files_copied = db.Column(db.Integer, default=0)
    files_skipped = db.Column(db.Integer, default=0)
    files_failed = db.Column(db.Integer, default=0)
    bytes_transferred = db.Column(db.BigInteger, default=0)
    snapshot_path = db.Column(db.Text)
    errors_json = db.Column(db.Text)                           # JSON array of error strings
    triggered_by = db.Column(db.Text, default='scheduled')    # scheduled | manual | lock | idle


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

# ─── Exchange Online Quarantine Models ───────────────────────────────────────

class QuarantineMessage(db.Model):
    __tablename__ = 'quarantine_message'
    id                   = db.Column(db.BigInteger, primary_key=True)
    message_id           = db.Column(db.Text, nullable=False, unique=True)
    internet_message_id  = db.Column(db.Text)
    sender_address       = db.Column(db.Text)
    sender_display_name  = db.Column(db.Text)
    sender_domain        = db.Column(db.Text)
    recipient_address    = db.Column(db.Text)
    subject              = db.Column(db.Text)
    received_time        = db.Column(db.DateTime(timezone=True))
    expiry_time          = db.Column(db.DateTime(timezone=True))
    quarantine_reason    = db.Column(db.Text)
    policy_type          = db.Column(db.Text)
    threat_type          = db.Column(db.Text)           # Phish, Malware, Spam, Bulk, Unknown
    spf_result           = db.Column(db.Text)
    dkim_result          = db.Column(db.Text)
    dmarc_result         = db.Column(db.Text)
    sender_ip            = db.Column(db.Text)           # SenderIPv4/IPv6 from Advanced Hunting
    email_direction      = db.Column(db.Text)           # Inbound | IntraOrg | OutboundToExternal
    release_status       = db.Column(db.Text, nullable=False, default='Quarantined')  # Quarantined | Released | Deleted
    released_by          = db.Column(db.Text)
    released_at          = db.Column(db.DateTime(timezone=True))
    release_requested_by = db.Column(db.Text)                  # user who asked an admin to release it
    release_requested_at = db.Column(db.DateTime(timezone=True))
    url_count            = db.Column(db.Integer, default=0)
    attachment_count     = db.Column(db.Integer, default=0)
    urls_json            = db.Column(db.Text)           # JSON array of extracted URLs
    attachments_json     = db.Column(db.Text)           # JSON array of attachment names/hashes
    raw_headers          = db.Column(db.Text)
    campaign_id          = db.Column(db.Text)           # sender_domain used as campaign key
    last_synced          = db.Column(db.DateTime, default=datetime.utcnow)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)
    iocs = db.relationship('QuarantineIOC', backref='message', lazy=True, cascade='all, delete-orphan',
                           primaryjoin='QuarantineMessage.message_id == foreign(QuarantineIOC.message_id)')

    @property
    def risk_score(self):
        score = 0
        tt = (self.threat_type or "").lower()
        if tt == "phish":     score += 40
        elif tt == "malware": score += 50
        elif tt == "spam":    score += 10
        if (self.spf_result or "").lower()  in ("fail", "softfail"): score += 20
        if (self.dkim_result or "").lower() == "fail":               score += 20
        if (self.dmarc_result or "").lower() == "fail":              score += 15
        if (self.url_count or 0)        > 3: score += 5
        if (self.attachment_count or 0) > 0: score += 5
        return min(score, 100)

    @property
    def risk_label(self):
        s = self.risk_score
        if s >= 75: return "Critical"
        if s >= 50: return "High"
        if s >= 25: return "Medium"
        return "Low"


class QuarantineIOC(db.Model):
    __tablename__ = 'quarantine_ioc'
    id           = db.Column(db.BigInteger, primary_key=True)
    message_id   = db.Column(db.Text, db.ForeignKey('quarantine_message.message_id', ondelete='CASCADE'), nullable=False)
    ioc_type     = db.Column(db.Text, nullable=False)   # url, domain, email, ip, hash
    ioc_value    = db.Column(db.Text, nullable=False)
    threat_label = db.Column(db.Text)
    first_seen   = db.Column(db.DateTime, default=datetime.utcnow)
    seen_count   = db.Column(db.Integer, default=1)

# ─────────────────────────────────────────────────────────────────────────────


# NOTE: the auth decorators (admin_required, manager_required, eagle_eyes_required,
# ticket_access_required, license_required) used to be defined here too, but they
# were dead duplicates — the live versions live in utils.py and everything imports
# them from there. Removed to avoid the confusing duplicate.

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


class CommandLedger(db.Model):
    """Immutable ledger of every attempted agent/automation action — the audit, debug,
    training, and trust spine of the Agentic IT-OS. One row per action; only the action's
    own lifecycle fields (status/verification/rollback/completed_at) ever update — history
    is never rewritten. See docs/AGENTIC_IT_OS_GAMEPLAN.md."""
    __tablename__ = 'command_ledger'
    id = db.Column(db.BigInteger, primary_key=True)
    correlation_id = db.Column(db.String(64), index=True)   # ties multi-step workflows together
    requested_by = db.Column(db.String(120))                # human username/email or 'system'
    planned_by = db.Column(db.String(120))                  # agent/runtime that planned it (nullable)
    tool = db.Column(db.String(120))                        # e.g. rmm.run_script, ldap.disable_user
    object_type = db.Column(db.String(60))                  # asset | employee | license | ...
    object_id = db.Column(db.String(120))
    action_type = db.Column(db.String(80))
    risk_tier = db.Column(db.String(20), default='low')     # low | medium | high | critical
    approval_status = db.Column(db.String(20), default='auto')  # auto | pending | approved | rejected
    before_state = db.Column(db.JSON)                       # snapshot for rollback
    after_state = db.Column(db.JSON)
    verification_status = db.Column(db.String(20), default='pending')  # pending|verified|failed|unverifiable
    verification_detail = db.Column(db.Text)
    rollback_available = db.Column(db.Boolean, default=False)
    rolled_back_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), default='planned')    # planned|dispatched|succeeded|failed
    created_at = db.Column(db.DateTime(timezone=True), default=now_mst)
    completed_at = db.Column(db.DateTime(timezone=True))


for _model, _etype in [(Asset, 'asset'), (Employee, 'employee'), (Policy, 'policy')]:
    _ins, _upd, _del = _make_audit_listener(_etype)
    _sa_event.listen(_model, 'after_insert', _ins)
    _sa_event.listen(_model, 'after_update', _upd)
    _sa_event.listen(_model, 'after_delete', _del)
# ─────────────────────────────────────────────────────────────────────────────
