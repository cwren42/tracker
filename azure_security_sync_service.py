"""
Azure Security Sync Service
Collects and stores Azure Security Center, networking, and vulnerability data
"""
import json
from datetime import datetime
from app import db
from soc2_models import (
    AzureNetworkSecurityGroup, AzureSecurityAlert, AzureDatabase, 
    AzureStorageAccount, AzureVirtualMachine, AzureSecurityAssessment,
    AzureMonitorAlert, AzureNetworkTopology, EvidenceSnapshot, AuditLog
)
from azure_security_service import AzureSecurityService
import logging

logger = logging.getLogger(__name__)

class AzureSecuritySyncService:
    """Sync service for Azure security evidence"""
    
    def __init__(self):
        self.azure_service = AzureSecurityService()
    
    def sync_network_security_groups(self):
        """Sync Azure NSG firewall rules"""
        try:
            logger.info("Starting Azure NSG sync...")
            
            # Mark all existing records as not current
            AzureNetworkSecurityGroup.query.update({'is_current': False})
            
            # Fetch current NSGs
            nsgs = self.azure_service.get_network_security_groups()
            
            for nsg_data in nsgs:
                nsg = AzureNetworkSecurityGroup(
                    name=nsg_data['name'],
                    location=nsg_data['location'],
                    resource_group=nsg_data['resource_group'],
                    security_rules=json.dumps(nsg_data['security_rules']),
                    is_current=True
                )
                db.session.add(nsg)
            
            db.session.commit()
            
            # Create evidence snapshot
            self._create_snapshot('Firewall Rules', 'AzureNSGs', len(nsgs))
            
            logger.info(f"Synced {len(nsgs)} Azure NSGs")
            return {'success': True, 'count': len(nsgs)}
            
        except Exception as e:
            logger.error(f"Error syncing Azure NSGs: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_security_alerts(self):
        """Sync Defender for Cloud alerts"""
        try:
            logger.info("Starting Azure security alerts sync...")
            
            AzureSecurityAlert.query.update({'is_current': False})
            
            alerts = self.azure_service.get_security_alerts()
            
            for alert_data in alerts:
                alert = AzureSecurityAlert(
                    alert_name=alert_data['name'],
                    severity=alert_data['severity'],
                    status=alert_data['status'],
                    description=alert_data['description'],
                    detected_time=datetime.fromisoformat(alert_data['detected_time'].replace('Z', '+00:00')) if alert_data.get('detected_time') else None,
                    resource_id=alert_data['resource_id'],
                    remediation=str(alert_data.get('remediation', '')),
                    is_current=True
                )
                db.session.add(alert)
            
            db.session.commit()
            
            self._create_snapshot('Intrusion Detection', 'DefenderAlerts', len(alerts))
            
            logger.info(f"Synced {len(alerts)} security alerts")
            return {'success': True, 'count': len(alerts)}
            
        except Exception as e:
            logger.error(f"Error syncing security alerts: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_databases(self):
        """Sync Azure SQL databases with encryption settings"""
        try:
            logger.info("Starting Azure database sync...")
            
            AzureDatabase.query.update({'is_current': False})
            
            databases = self.azure_service.get_sql_databases()
            
            for db_data in databases:
                azure_db = AzureDatabase(
                    server_name=db_data['server_name'],
                    database_name=db_data['database_name'],
                    location=db_data['location'],
                    resource_group=db_data['resource_group'],
                    tde_enabled=db_data['tde_enabled'],
                    tde_status=db_data['tde_status'],
                    is_current=True
                )
                db.session.add(azure_db)
            
            db.session.commit()
            
            self._create_snapshot('Database Encryption', 'AzureDatabase', len(databases))
            
            logger.info(f"Synced {len(databases)} Azure databases")
            return {'success': True, 'count': len(databases)}
            
        except Exception as e:
            logger.error(f"Error syncing databases: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_storage_accounts(self):
        """Sync Azure storage accounts with encryption settings"""
        try:
            logger.info("Starting Azure storage sync...")
            
            AzureStorageAccount.query.update({'is_current': False})
            
            accounts = self.azure_service.get_storage_accounts()
            
            for account_data in accounts:
                storage = AzureStorageAccount(
                    name=account_data['name'],
                    location=account_data['location'],
                    resource_group=account_data['resource_group'],
                    encryption_enabled=account_data['encryption_enabled'],
                    https_only=account_data['https_only'],
                    tls_version=account_data['tls_version'],
                    is_current=True
                )
                db.session.add(storage)
            
            db.session.commit()
            
            self._create_snapshot('Encryption at Rest', 'AzureStorage', len(accounts))
            
            logger.info(f"Synced {len(accounts)} storage accounts")
            return {'success': True, 'count': len(accounts)}
            
        except Exception as e:
            logger.error(f"Error syncing storage: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_virtual_machines(self):
        """Sync Azure VMs with security settings"""
        try:
            logger.info("Starting Azure VM sync...")
            
            AzureVirtualMachine.query.update({'is_current': False})
            
            vms = self.azure_service.get_virtual_machines()
            
            for vm_data in vms:
                vm = AzureVirtualMachine(
                    name=vm_data['name'],
                    location=vm_data['location'],
                    resource_group=vm_data['resource_group'],
                    os_type=vm_data['os_type'],
                    disk_encryption=vm_data['disk_encryption'],
                    vm_size=vm_data['vm_size'],
                    is_current=True
                )
                db.session.add(vm)
            
            db.session.commit()
            
            self._create_snapshot('Server Encryption', 'AzureVMs', len(vms))
            
            logger.info(f"Synced {len(vms)} virtual machines")
            return {'success': True, 'count': len(vms)}
            
        except Exception as e:
            logger.error(f"Error syncing VMs: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_security_assessments(self):
        """Sync Defender security assessments (vulnerability scans)"""
        try:
            logger.info("Starting security assessments sync...")
            
            AzureSecurityAssessment.query.update({'is_current': False})
            
            assessments = self.azure_service.get_security_assessments()
            
            for assessment_data in assessments:
                assessment = AzureSecurityAssessment(
                    name=assessment_data['name'],
                    severity=assessment_data['severity'],
                    status=assessment_data['status'],
                    description=assessment_data['description'],
                    remediation=assessment_data['remediation'],
                    resource_id=assessment_data['resource_id'],
                    is_current=True
                )
                db.session.add(assessment)
            
            db.session.commit()
            
            self._create_snapshot('Vulnerability Scan Results', 'SecurityAssessments', len(assessments))
            
            logger.info(f"Synced {len(assessments)} security assessments")
            return {'success': True, 'count': len(assessments)}
            
        except Exception as e:
            logger.error(f"Error syncing assessments: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_monitor_alerts(self):
        """Sync Azure Monitor alert rules"""
        try:
            logger.info("Starting monitor alerts sync...")
            
            AzureMonitorAlert.query.update({'is_current': False})
            
            alerts = self.azure_service.get_monitor_alerts()
            
            for alert_data in alerts:
                alert = AzureMonitorAlert(
                    name=alert_data['name'],
                    location=alert_data['location'],
                    enabled=alert_data['enabled'],
                    severity=alert_data['severity'],
                    description=alert_data['description'],
                    criteria=json.dumps(alert_data['criteria']),
                    is_current=True
                )
                db.session.add(alert)
            
            db.session.commit()
            
            self._create_snapshot('Monitoring Tools Enabled', 'MonitorAlerts', len(alerts))
            
            logger.info(f"Synced {len(alerts)} monitor alerts")
            return {'success': True, 'count': len(alerts)}
            
        except Exception as e:
            logger.error(f"Error syncing monitor alerts: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def sync_network_topology(self):
        """Sync Azure network topology"""
        try:
            logger.info("Starting network topology sync...")
            
            AzureNetworkTopology.query.update({'is_current': False})
            
            vnets = self.azure_service.get_network_topology()
            
            for vnet_data in vnets:
                vnet = AzureNetworkTopology(
                    name=vnet_data['name'],
                    location=vnet_data['location'],
                    resource_group=vnet_data['resource_group'],
                    address_space=json.dumps(vnet_data['address_space']),
                    subnets=json.dumps(vnet_data['subnets']),
                    is_current=True
                )
                db.session.add(vnet)
            
            db.session.commit()
            
            self._create_snapshot('Current Network Diagram', 'NetworkTopology', len(vnets))
            
            logger.info(f"Synced {len(vnets)} virtual networks")
            return {'success': True, 'count': len(vnets)}
            
        except Exception as e:
            logger.error(f"Error syncing network topology: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def _create_snapshot(self, evidence_name, evidence_type, record_count):
        """Create an evidence snapshot record"""
        # Find the control for this evidence
        from soc2_models import SOC2Control, StrikeGraphEvidence
        
        # Find control via evidence mapping
        evidence_item = StrikeGraphEvidence.query.filter_by(evidence_name=evidence_name).first()
        if evidence_item and evidence_item.control_id:
            snapshot = EvidenceSnapshot(
                control_id=evidence_item.control_id,
                evidence_type=evidence_type,
                record_count=record_count,
                status='Collected',
                collected_by='Azure Security Sync'
            )
            db.session.add(snapshot)
            db.session.commit()
    
    def run_full_sync(self):
        """Run all Azure security syncs"""
        results = {
            'timestamp': datetime.utcnow().isoformat(),
            'syncs': {}
        }
        
        # Run all syncs
        results['syncs']['nsgs'] = self.sync_network_security_groups()
        results['syncs']['alerts'] = self.sync_security_alerts()
        results['syncs']['databases'] = self.sync_databases()
        results['syncs']['storage'] = self.sync_storage_accounts()
        results['syncs']['vms'] = self.sync_virtual_machines()
        results['syncs']['assessments'] = self.sync_security_assessments()
        results['syncs']['monitor'] = self.sync_monitor_alerts()
        results['syncs']['network'] = self.sync_network_topology()
        
        # Create audit log
        audit = AuditLog(
            action='azure_security_sync',
            entity_type='azure_security',
            details=json.dumps(results)
        )
        db.session.add(audit)
        db.session.commit()
        
        return results
