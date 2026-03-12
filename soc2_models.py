"""
SOC2 Compliance Models for Evidence Collection and Audit Trail
Integrates with Microsoft 365 and Intune for automated compliance reporting
"""
from datetime import datetime
from extensions import db

class SOC2Control(db.Model):
    """SOC2 Control definitions from StrikeGraph"""
    __tablename__ = 'soc2_control'
    
    id = db.Column(db.Integer, primary_key=True)
    control_name = db.Column(db.String(200), nullable=False, unique=True)
    control_description = db.Column(db.Text)
    control_frequency = db.Column(db.String(50))  # Daily, Weekly, Monthly, Quarterly, Annually, As Needed
    control_owner = db.Column(db.String(200))
    control_progress = db.Column(db.String(50))  # In Place, Partially In Place, Not In Place
    is_active = db.Column(db.Boolean, default=True)
    audit_alignment = db.Column(db.Text)  # SOC2 framework references
    automation_enabled = db.Column(db.Boolean, default=False)
    last_evidence_date = db.Column(db.DateTime)
    next_evidence_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    evidence_snapshots = db.relationship('EvidenceSnapshot', backref='control', lazy=True, cascade='all, delete-orphan')
    
    def get_frequency_days(self):
        """Convert frequency to days for scheduling"""
        frequency_map = {
            'Daily': 1,
            'Weekly': 7,
            'Monthly': 30,
            'Quarterly': 90,
            'Annually': 365,
            'Continuous': 1,
            'As Needed': None
        }
        return frequency_map.get(self.control_frequency)


class EvidenceSnapshot(db.Model):
    """Historical snapshots of evidence for audit trail"""
    __tablename__ = 'evidence_snapshot'
    
    id = db.Column(db.Integer, primary_key=True)
    control_id = db.Column(db.Integer, db.ForeignKey('soc2_control.id'), nullable=False)
    snapshot_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    evidence_type = db.Column(db.String(100))  # M365Users, IntuneDevices, AdminRoles, etc.
    evidence_data = db.Column(db.Text)  # JSON blob of collected data
    record_count = db.Column(db.Integer)  # Number of records in snapshot
    status = db.Column(db.String(50), default='collected')  # collected, verified, submitted
    collected_by = db.Column(db.String(100))  # user or 'automated'
    notes = db.Column(db.Text)
    file_path = db.Column(db.String(500))  # Path to exported evidence file
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class M365User(db.Model):
    """Microsoft 365 User snapshot for compliance"""
    __tablename__ = 'm365_user'
    
    id = db.Column(db.Integer, primary_key=True)
    user_principal_name = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(255))
    job_title = db.Column(db.String(255))
    department = db.Column(db.String(255))
    office_location = db.Column(db.String(255))
    is_admin = db.Column(db.Boolean, default=False)
    admin_roles = db.Column(db.Text)  # JSON array of admin roles
    account_enabled = db.Column(db.Boolean)
    created_datetime = db.Column(db.DateTime)
    last_signin_datetime = db.Column(db.DateTime)
    licenses = db.Column(db.Text)  # JSON array of license assignments
    m365_id = db.Column(db.String(100), unique=True)  # Microsoft Graph ID
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_current = db.Column(db.Boolean, default=True)  # False for historical records
    
    # Link to employee if exists
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'))


class IntuneDevice(db.Model):
    """Intune Device snapshot for compliance"""
    __tablename__ = 'intune_device'
    
    id = db.Column(db.Integer, primary_key=True)
    device_name = db.Column(db.String(255), nullable=False)
    device_id = db.Column(db.String(100), unique=True)  # Intune device ID
    azure_ad_device_id = db.Column(db.String(100))
    serial_number = db.Column(db.String(100))
    manufacturer = db.Column(db.String(100))
    model = db.Column(db.String(100))
    os_version = db.Column(db.String(100))
    os_build = db.Column(db.String(100))
    
    # Compliance Information
    compliance_state = db.Column(db.String(50))  # compliant, noncompliant, unknown
    last_sync_datetime = db.Column(db.DateTime)
    enrollment_date = db.Column(db.DateTime)
    
    # Security Status
    is_encrypted = db.Column(db.Boolean)
    antivirus_status = db.Column(db.String(50))
    firewall_status = db.Column(db.String(50))
    
    # User Assignment
    user_principal_name = db.Column(db.String(255))
    user_display_name = db.Column(db.String(255))
    
    # Management
    management_agent = db.Column(db.String(50))  # mdm, eas, intune, etc.
    ownership = db.Column(db.String(50))  # company, personal
    
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_current = db.Column(db.Boolean, default=True)  # False for historical records
    
    # Link to asset if exists
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'))
    
    # Relationships
    software_inventory = db.relationship('DeviceSoftware', backref='device', lazy=True, cascade='all, delete-orphan')


class DeviceSoftware(db.Model):
    """Software installed on Intune devices"""
    __tablename__ = 'device_software'
    
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('intune_device.id'), nullable=False)
    app_name = db.Column(db.String(255), nullable=False)
    app_version = db.Column(db.String(100))
    app_publisher = db.Column(db.String(255))
    app_id = db.Column(db.String(100))  # Intune app ID
    install_date = db.Column(db.DateTime)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_current = db.Column(db.Boolean, default=True)


class AdminRoleSnapshot(db.Model):
    """Track admin role assignments over time for audit"""
    __tablename__ = 'admin_role_snapshot'
    
    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user_principal_name = db.Column(db.String(255), nullable=False)
    role_name = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.String(100))
    assigned_date = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='active')  # active, removed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ComplianceReport(db.Model):
    """Generated compliance reports for StrikeGraph submission"""
    __tablename__ = 'compliance_report'
    
    id = db.Column(db.Integer, primary_key=True)
    report_name = db.Column(db.String(255), nullable=False)
    report_type = db.Column(db.String(100))  # control_evidence, user_access_review, asset_inventory
    report_period_start = db.Column(db.Date)
    report_period_end = db.Column(db.Date)
    controls_included = db.Column(db.Text)  # JSON array of control IDs
    generated_date = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by = db.Column(db.String(100))
    status = db.Column(db.String(50), default='draft')  # draft, submitted, approved
    file_path = db.Column(db.String(500))  # Path to PDF/Excel report
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class StrikeGraphEvidence(db.Model):
    """StrikeGraph evidence repository items"""
    __tablename__ = 'strikegraph_evidence'
    
    id = db.Column(db.Integer, primary_key=True)
    control_id = db.Column(db.Integer, db.ForeignKey('soc2_control.id'), nullable=True)  # Link to SOC2 control
    evidence_name = db.Column(db.String(255), nullable=False, unique=True)
    evidence_description = db.Column(db.Text)
    evidence_type = db.Column(db.String(50))  # Policy, Sample, General, Settings, Population
    expiration_schedule = db.Column(db.Integer)  # Days until expiration
    expiration_date = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    owner = db.Column(db.String(255))
    submission_status = db.Column(db.String(50), default='Not Submitted')  # Not Submitted, Submitted, Approved, Rejected
    last_submitted_date = db.Column(db.DateTime)
    file_path = db.Column(db.String(500))  # Path to uploaded evidence file
    automation_source = db.Column(db.String(100))  # M365Users, IntuneDevices, ISMS, Manual
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def is_automated(self):
        """Check if this evidence can be automatically collected"""
        automated_items = [
            'Administrator Access to Application',
            'Administrator Access to Database',
            'Administrator Access to Network/Cloud',
            'Administrator Access to Operating System',
            'Application User List',
            'Database User List',
            'Antivirus Configuration - Server',
            'Antivirus Configuration - Workstation',
            'Asset Inventory'
        ]
        return self.evidence_name in automated_items
    
    def days_until_expiration(self):
        """Calculate days until expiration"""
        if self.expiration_date:
            delta = self.expiration_date - datetime.utcnow().date()
            return delta.days
        return None


class AzureNetworkSecurityGroup(db.Model):
    """Azure Network Security Groups and firewall rules"""
    __tablename__ = 'azure_nsg'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    location = db.Column(db.String(100))
    resource_group = db.Column(db.String(255))
    security_rules = db.Column(db.Text)  # JSON array of rules
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureSecurityAlert(db.Model):
    """Defender for Cloud security alerts"""
    __tablename__ = 'azure_security_alert'
    
    id = db.Column(db.Integer, primary_key=True)
    alert_name = db.Column(db.String(255))
    severity = db.Column(db.String(50))
    status = db.Column(db.String(50))
    description = db.Column(db.Text)
    detected_time = db.Column(db.DateTime)
    resource_id = db.Column(db.String(500))
    remediation = db.Column(db.Text)
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureDatabase(db.Model):
    """Azure SQL databases with encryption settings"""
    __tablename__ = 'azure_database'
    
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(255))
    database_name = db.Column(db.String(255))
    location = db.Column(db.String(100))
    resource_group = db.Column(db.String(255))
    tde_enabled = db.Column(db.Boolean)
    tde_status = db.Column(db.String(50))
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureStorageAccount(db.Model):
    """Azure storage accounts with encryption settings"""
    __tablename__ = 'azure_storage'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    location = db.Column(db.String(100))
    resource_group = db.Column(db.String(255))
    encryption_enabled = db.Column(db.Boolean)
    https_only = db.Column(db.Boolean)
    tls_version = db.Column(db.String(50))
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureVirtualMachine(db.Model):
    """Azure virtual machines with security settings"""
    __tablename__ = 'azure_vm'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    location = db.Column(db.String(100))
    resource_group = db.Column(db.String(255))
    os_type = db.Column(db.String(50))
    disk_encryption = db.Column(db.Boolean)
    vm_size = db.Column(db.String(100))
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureSecurityAssessment(db.Model):
    """Defender for Cloud security assessments (vulnerability scans)"""
    __tablename__ = 'azure_security_assessment'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    severity = db.Column(db.String(50))
    status = db.Column(db.String(50))
    description = db.Column(db.Text)
    remediation = db.Column(db.Text)
    resource_id = db.Column(db.String(500))
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureMonitorAlert(db.Model):
    """Azure Monitor alert rules"""
    __tablename__ = 'azure_monitor_alert'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    location = db.Column(db.String(100))
    enabled = db.Column(db.Boolean)
    severity = db.Column(db.Integer)
    description = db.Column(db.Text)
    criteria = db.Column(db.Text)  # JSON
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AzureNetworkTopology(db.Model):
    """Azure virtual networks and topology"""
    __tablename__ = 'azure_network_topology'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    location = db.Column(db.String(100))
    resource_group = db.Column(db.String(255))
    address_space = db.Column(db.Text)  # JSON array
    subnets = db.Column(db.Text)  # JSON array
    is_current = db.Column(db.Boolean, default=True)
    sync_date = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    """Audit log for SOC2 compliance actions"""
    __tablename__ = 'soc2_audit_log'
    
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(100), nullable=False)  # sync, report_generated, evidence_collected
    entity_type = db.Column(db.String(100))  # control, user, device, etc.
    entity_id = db.Column(db.String(100))
    user_email = db.Column(db.String(255))
    details = db.Column(db.Text)  # JSON details
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
