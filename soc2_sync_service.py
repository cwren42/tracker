"""
SOC2 Evidence Collection and Sync Service
Automates data collection from M365/Intune for compliance reporting
"""
import json
from datetime import datetime, timedelta
import logging
from m365_service import M365Service

logger = logging.getLogger(__name__)


class SOC2SyncService:
    """Service for syncing M365/Intune data for SOC2 compliance"""
    
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.m365_service = None
    
    def initialize_m365_service(self):
        """Initialize M365 service with credentials from settings"""
        from app import Setting
        
        with self.app.app_context():
            from m365_config import get_m365_credentials
            tenant_id, client_id, client_secret = get_m365_credentials()

            if not all([tenant_id, client_id, client_secret]):
                raise Exception("M365 credentials not configured")

            self.m365_service = M365Service(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret
            )
            
            return self.m365_service
    
    def sync_m365_users(self):
        """Sync all M365 users and their admin roles"""
        from soc2_models import M365User, AdminRoleSnapshot, EvidenceSnapshot, SOC2Control, AuditLog
        
        if not self.m365_service:
            self.initialize_m365_service()
        
        with self.app.app_context():
            try:
                logger.info("Starting M365 user sync")
                
                # Mark all current users as historical
                M365User.query.update({'is_current': False})
                
                # Get all users from M365
                users = self.m365_service.get_all_users()
                
                # Get admin roles
                admin_roles = self.m365_service.get_admin_roles()
                admin_dict = {}
                for admin in admin_roles:
                    upn = admin['user_principal_name']
                    if upn not in admin_dict:
                        admin_dict[upn] = []
                    admin_dict[upn].append(admin['role_name'])
                
                synced_count = 0
                for user in users:
                    upn = user.get('userPrincipalName')
                    is_admin = upn in admin_dict
                    
                    # Check if user exists
                    m365_user = M365User.query.filter_by(m365_id=user['id']).first()
                    
                    if m365_user:
                        # Update existing
                        m365_user.display_name = user.get('displayName')
                        m365_user.job_title = user.get('jobTitle')
                        m365_user.department = user.get('department')
                        m365_user.office_location = user.get('officeLocation')
                        m365_user.is_admin = is_admin
                        m365_user.admin_roles = json.dumps(admin_dict.get(upn, []))
                        m365_user.account_enabled = user.get('accountEnabled')
                        m365_user.user_type = user.get('userType')
                        m365_user.sync_date = datetime.utcnow()
                        m365_user.is_current = True
                        
                        # Update sign-in info if available
                        if 'signInActivity' in user and user['signInActivity']:
                            last_signin = user['signInActivity'].get('lastSignInDateTime')
                            if last_signin:
                                m365_user.last_signin_datetime = datetime.fromisoformat(last_signin.replace('Z', '+00:00'))
                    else:
                        # Create new
                        created_dt = user.get('createdDateTime')
                        created = datetime.fromisoformat(created_dt.replace('Z', '+00:00')) if created_dt else None
                        signin = (user.get('signInActivity') or {}).get('lastSignInDateTime')
                        last_signin = (datetime.fromisoformat(signin.replace('Z', '+00:00'))
                                       if signin else None)

                        m365_user = M365User(
                            user_principal_name=upn,
                            display_name=user.get('displayName'),
                            job_title=user.get('jobTitle'),
                            department=user.get('department'),
                            office_location=user.get('officeLocation'),
                            is_admin=is_admin,
                            admin_roles=json.dumps(admin_dict.get(upn, [])),
                            account_enabled=user.get('accountEnabled'),
                            user_type=user.get('userType'),
                            created_datetime=created,
                            last_signin_datetime=last_signin,
                            m365_id=user['id'],
                            sync_date=datetime.utcnow(),
                            is_current=True
                        )
                        self.db.session.add(m365_user)
                    
                    synced_count += 1
                
                # Create admin role snapshots
                for admin in admin_roles:
                    snapshot = AdminRoleSnapshot(
                        snapshot_date=datetime.utcnow(),
                        user_principal_name=admin['user_principal_name'],
                        role_name=admin['role_name'],
                        role_id=admin['role_id'],
                        status='active'
                    )
                    self.db.session.add(snapshot)
                
                # Create evidence snapshot
                control = SOC2Control.query.filter_by(control_name='Administrator Access').first()
                if control:
                    evidence = EvidenceSnapshot(
                        control_id=control.id,
                        snapshot_date=datetime.utcnow(),
                        evidence_type='M365Users',
                        evidence_data=json.dumps({'users': len(users), 'admins': len(admin_roles)}),
                        record_count=len(users),
                        status='collected',
                        collected_by='automated'
                    )
                    self.db.session.add(evidence)
                    control.last_evidence_date = datetime.utcnow()
                
                # Log the sync
                audit = AuditLog(
                    action='m365_user_sync',
                    entity_type='m365_user',
                    entity_id='all',
                    user_email='system',
                    details=json.dumps({'synced': synced_count, 'admins': len(admin_roles)}),
                    timestamp=datetime.utcnow()
                )
                self.db.session.add(audit)
                
                self.db.session.commit()
                logger.info(f"Successfully synced {synced_count} users and {len(admin_roles)} admin roles")
                
                return {
                    'success': True,
                    'users_synced': synced_count,
                    'admins': len(admin_roles)
                }
                
            except Exception as e:
                self.db.session.rollback()
                logger.error(f"Failed to sync M365 users: {e}")
                return {
                    'success': False,
                    'error': str(e)
                }
    
    def sync_intune_devices(self):
        """Sync all Intune managed devices"""
        from soc2_models import IntuneDevice, EvidenceSnapshot, SOC2Control, AuditLog
        
        if not self.m365_service:
            self.initialize_m365_service()
        
        with self.app.app_context():
            try:
                logger.info("Starting Intune device sync")
                
                # Mark all current devices as historical
                IntuneDevice.query.update({'is_current': False})
                
                # Get all managed devices
                devices = self.m365_service.get_managed_devices()
                
                synced_count = 0
                compliant_count = 0
                
                for device in devices:
                    device_id = device.get('id')
                    
                    # Check if device exists
                    intune_device = IntuneDevice.query.filter_by(device_id=device_id).first()
                    
                    last_sync = device.get('lastSyncDateTime')
                    last_sync_dt = datetime.fromisoformat(last_sync.replace('Z', '+00:00')) if last_sync else None
                    
                    enrollment = device.get('enrolledDateTime')
                    enrollment_dt = datetime.fromisoformat(enrollment.replace('Z', '+00:00')) if enrollment else None
                    
                    compliance = device.get('complianceState', 'unknown')
                    if compliance == 'compliant':
                        compliant_count += 1
                    
                    device_data = {
                        'device_name': device.get('deviceName'),
                        'device_id': device_id,
                        'azure_ad_device_id': device.get('azureADDeviceId'),
                        'serial_number': device.get('serialNumber'),
                        'manufacturer': device.get('manufacturer'),
                        'model': device.get('model'),
                        'os_version': device.get('osVersion'),
                        'os_build': device.get('operatingSystem'),
                        'compliance_state': compliance,
                        'last_sync_datetime': last_sync_dt,
                        'enrollment_date': enrollment_dt,
                        'is_encrypted': device.get('isEncrypted'),
                        'user_principal_name': device.get('userPrincipalName'),
                        'user_display_name': device.get('userDisplayName'),
                        'management_agent': device.get('managementAgent'),
                        'ownership': device.get('managedDeviceOwnerType'),
                        'sync_date': datetime.utcnow(),
                        'is_current': True
                    }
                    
                    if intune_device:
                        # Update existing
                        for key, value in device_data.items():
                            setattr(intune_device, key, value)
                    else:
                        # Create new
                        intune_device = IntuneDevice(**device_data)
                        self.db.session.add(intune_device)
                    
                    synced_count += 1
                
                # Create evidence snapshot for Asset Inventory control
                control = SOC2Control.query.filter_by(control_name='Asset Inventory').first()
                if control:
                    evidence = EvidenceSnapshot(
                        control_id=control.id,
                        snapshot_date=datetime.utcnow(),
                        evidence_type='IntuneDevices',
                        evidence_data=json.dumps({
                            'total_devices': len(devices),
                            'compliant': compliant_count,
                            'non_compliant': len(devices) - compliant_count
                        }),
                        record_count=len(devices),
                        status='collected',
                        collected_by='automated'
                    )
                    self.db.session.add(evidence)
                    control.last_evidence_date = datetime.utcnow()
                
                # Create evidence for Antivirus control
                antivirus_control = SOC2Control.query.filter_by(control_name='Antivirus').first()
                if antivirus_control:
                    evidence = EvidenceSnapshot(
                        control_id=antivirus_control.id,
                        snapshot_date=datetime.utcnow(),
                        evidence_type='DeviceCompliance',
                        evidence_data=json.dumps({
                            'compliant_devices': compliant_count,
                            'total_devices': len(devices)
                        }),
                        record_count=len(devices),
                        status='collected',
                        collected_by='automated'
                    )
                    self.db.session.add(evidence)
                    antivirus_control.last_evidence_date = datetime.utcnow()
                
                # Log the sync
                audit = AuditLog(
                    action='intune_device_sync',
                    entity_type='intune_device',
                    entity_id='all',
                    user_email='system',
                    details=json.dumps({
                        'synced': synced_count,
                        'compliant': compliant_count
                    }),
                    timestamp=datetime.utcnow()
                )
                self.db.session.add(audit)
                
                self.db.session.commit()
                logger.info(f"Successfully synced {synced_count} devices ({compliant_count} compliant)")
                
                return {
                    'success': True,
                    'devices_synced': synced_count,
                    'compliant': compliant_count
                }
                
            except Exception as e:
                self.db.session.rollback()
                logger.error(f"Failed to sync Intune devices: {e}")
                return {
                    'success': False,
                    'error': str(e)
                }
    
    def sync_software_inventory(self):
        """Sync software inventory from Intune devices"""
        from soc2_models import IntuneDevice, DeviceSoftware, EvidenceSnapshot, SOC2Control, AuditLog
        
        if not self.m365_service:
            self.initialize_m365_service()
        
        with self.app.app_context():
            try:
                logger.info("Starting software inventory sync")
                
                # Mark all current software as historical
                DeviceSoftware.query.update({'is_current': False})
                
                # Get all detected apps
                apps = self.m365_service.get_all_detected_apps()
                
                synced_count = 0
                
                for app in apps:
                    app_name = app.get('displayName')
                    app_version = app.get('version')
                    app_publisher = app.get('publisher')
                    
                    # Get devices this app is installed on
                    managed_devices = app.get('managedDevices', [])
                    
                    for device_ref in managed_devices:
                        device_id = device_ref.get('id')
                        
                        # Find the Intune device in our DB
                        intune_device = IntuneDevice.query.filter_by(device_id=device_id).first()
                        
                        if intune_device:
                            software = DeviceSoftware(
                                device_id=intune_device.id,
                                app_name=app_name,
                                app_version=app_version,
                                app_publisher=app_publisher,
                                app_id=app.get('id'),
                                sync_date=datetime.utcnow(),
                                is_current=True
                            )
                            self.db.session.add(software)
                            synced_count += 1
                
                # Create evidence snapshot
                control = SOC2Control.query.filter_by(control_name='Asset Inventory').first()
                if control:
                    evidence = EvidenceSnapshot(
                        control_id=control.id,
                        snapshot_date=datetime.utcnow(),
                        evidence_type='SoftwareInventory',
                        evidence_data=json.dumps({
                            'unique_apps': len(apps),
                            'total_installations': synced_count
                        }),
                        record_count=len(apps),
                        status='collected',
                        collected_by='automated'
                    )
                    self.db.session.add(evidence)
                
                # Log the sync
                audit = AuditLog(
                    action='software_inventory_sync',
                    entity_type='device_software',
                    entity_id='all',
                    user_email='system',
                    details=json.dumps({
                        'apps': len(apps),
                        'installations': synced_count
                    }),
                    timestamp=datetime.utcnow()
                )
                self.db.session.add(audit)
                
                self.db.session.commit()
                logger.info(f"Successfully synced {len(apps)} apps ({synced_count} installations)")
                
                return {
                    'success': True,
                    'apps': len(apps),
                    'installations': synced_count
                }
                
            except Exception as e:
                self.db.session.rollback()
                logger.error(f"Failed to sync software inventory: {e}")
                return {
                    'success': False,
                    'error': str(e)
                }
    
    def run_full_sync(self):
        """Run all sync operations"""
        logger.info("Starting full SOC2 sync")
        
        results = {
            'users': self.sync_m365_users(),
            'devices': self.sync_intune_devices(),
            'software': self.sync_software_inventory()
        }

        # IT-graph: link freshly-synced M365 users -> employees and Intune devices -> assets
        # (idempotent; only fills NULL FKs). Track B of the Agentic IT-OS gameplan.
        try:
            from identity_graph import resolve_identity_links
            results['identity_links'] = resolve_identity_links(commit=True)
        except Exception as e:
            self.db.session.rollback()  # don't leave a broken txn for a later commit to inherit
            logger.exception("identity link resolution failed (non-fatal)")
            results['identity_links'] = {'error': str(e)}

        logger.info("Full SOC2 sync completed")
        return results
