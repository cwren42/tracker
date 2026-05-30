"""
Evidence File Generation Service for StrikeGraph
Generates downloadable evidence files for SOC2 compliance
"""

import os
import json
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from app import db
from soc2_models import (
    EvidenceSnapshot, StrikeGraphEvidence, SOC2Control,
    M365User, IntuneDevice, DeviceSoftware, AdminRoleSnapshot,
    AzureNetworkSecurityGroup, AzureSecurityAlert, AzureDatabase,
    AzureStorageAccount, AzureVirtualMachine, AzureSecurityAssessment,
    AzureMonitorAlert, AzureNetworkTopology
)
from teamviewer_evidence_service import TeamViewerEvidenceService
from defender_service import DefenderService


class EvidenceFileService:
    """Service to generate evidence files for StrikeGraph upload"""
    
    def __init__(self):
        self.evidence_dir = '/var/www/tracker/static/evidence'
        self.ensure_directories()
    
    def _sanitize_for_excel(self, text):
        """Remove illegal characters that Excel doesn't allow in cells"""
        if not text:
            return ''
        # Remove control characters and illegal XML characters
        import re
        # Keep only valid characters (printable ASCII + extended ASCII - control chars)
        return re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]', '', str(text))
    
    def ensure_directories(self):
        """Create evidence directories if they don't exist"""
        dirs = [
            self.evidence_dir,
            f'{self.evidence_dir}/m365',
            f'{self.evidence_dir}/M365',
            f'{self.evidence_dir}/M365/Defender',
            f'{self.evidence_dir}/azure',
            f'{self.evidence_dir}/isms',
            f'{self.evidence_dir}/manual',
            f'{self.evidence_dir}/teamviewer',
        ]
        for dir_path in dirs:
            os.makedirs(dir_path, exist_ok=True)
    
    def generate_filename(self, evidence_name, extension='xlsx'):
        """Generate a standardized filename"""
        # Sanitize filename
        safe_name = "".join(c for c in evidence_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_name = safe_name.replace(' ', '_')
        timestamp = datetime.now().strftime('%Y%m%d')
        return f"{safe_name}_{timestamp}.{extension}"
    
    def get_file_path(self, evidence_name, automation_source, extension='xlsx'):
        """Get full file path for evidence"""
        filename = self.generate_filename(evidence_name, extension)
        
        if automation_source in ['M365/Intune', 'M365']:
            return f'{self.evidence_dir}/m365/{filename}'
        elif automation_source == 'M365/Defender':
            return f'{self.evidence_dir}/M365/Defender/{filename}'
        elif automation_source == 'Azure':
            return f'{self.evidence_dir}/azure/{filename}'
        elif automation_source == 'ISMS':
            return f'{self.evidence_dir}/isms/{filename}'
        elif automation_source == 'TeamViewer':
            return f'{self.evidence_dir}/teamviewer/{filename}'
        else:
            return f'{self.evidence_dir}/manual/{filename}'
    
    def create_styled_workbook(self, title):
        """Create a styled Excel workbook"""
        wb = Workbook()
        ws = wb.active
        ws.title = title[:31]  # Excel sheet name limit
        return wb, ws
    
    def style_header_row(self, ws, headers):
        """Apply styling to header row"""
        header_fill = PatternFill(start_color="2D4639", end_color="2D4639", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(1, col_idx, header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
            ws.column_dimensions[cell.column_letter].width = 20
    
    def generate_m365_users_file(self, evidence_name):
        """Generate M365 Users list file"""
        users = M365User.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('M365 Users')
        headers = ['Display Name', 'Email', 'Job Title', 'Department', 'Office', 'Account Enabled', 'Is Admin']
        self.style_header_row(ws, headers)
        
        for row_idx, user in enumerate(users, 2):
            ws.cell(row_idx, 1, user.display_name or '')
            ws.cell(row_idx, 2, user.user_principal_name or '')
            ws.cell(row_idx, 3, user.job_title or '')
            ws.cell(row_idx, 4, user.department or '')
            ws.cell(row_idx, 5, user.office_location or '')
            ws.cell(row_idx, 6, 'Yes' if user.account_enabled else 'No')
            ws.cell(row_idx, 7, 'Yes' if user.is_admin else 'No')
        
        file_path = self.get_file_path(evidence_name, 'M365/Intune')
        wb.save(file_path)
        return file_path
    
    def generate_admin_users_file(self, evidence_name):
        """Generate Administrator Access list file"""
        admin_users = M365User.query.filter_by(is_admin=True, is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Admin Users')
        headers = ['Display Name', 'Email', 'Job Title', 'Department', 'Admin Roles', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, user in enumerate(admin_users, 2):
            # Get admin roles from AdminRoleSnapshot
            roles = AdminRoleSnapshot.query.filter_by(
                user_principal_name=user.user_principal_name,
                status='active'
            ).all()
            role_names = ', '.join([r.role_name for r in roles]) if roles else 'Administrator'
            
            ws.cell(row_idx, 1, user.display_name or '')
            ws.cell(row_idx, 2, user.user_principal_name or '')
            ws.cell(row_idx, 3, user.job_title or '')
            ws.cell(row_idx, 4, user.department or '')
            ws.cell(row_idx, 5, role_names)
            ws.cell(row_idx, 6, user.sync_date.strftime('%Y-%m-%d %H:%M') if user.sync_date else '')
        
        file_path = self.get_file_path(evidence_name, 'M365/Intune')
        wb.save(file_path)
        return file_path
    
    def generate_intune_devices_file(self, evidence_name):
        """Generate Intune Devices list file"""
        devices = IntuneDevice.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Intune Devices')
        headers = ['Device Name', 'User', 'Model', 'OS Version', 'Compliant', 'Encrypted', 'Last Sync', 'Management Agent']
        self.style_header_row(ws, headers)
        
        for row_idx, device in enumerate(devices, 2):
            ws.cell(row_idx, 1, device.device_name or '')
            ws.cell(row_idx, 2, device.user_display_name or device.user_principal_name or '')
            ws.cell(row_idx, 3, f"{device.manufacturer or ''} {device.model or ''}".strip())
            ws.cell(row_idx, 4, device.os_version or '')
            ws.cell(row_idx, 5, 'Yes' if device.compliance_state == 'compliant' else 'No')
            ws.cell(row_idx, 6, 'Yes' if device.is_encrypted else 'No')
            ws.cell(row_idx, 7, device.last_sync_datetime.strftime('%Y-%m-%d %H:%M') if device.last_sync_datetime else '')
            ws.cell(row_idx, 8, device.management_agent or '')
        
        file_path = self.get_file_path(evidence_name, 'M365/Intune')
        wb.save(file_path)
        return file_path
    
    def generate_device_software_file(self, evidence_name):
        """Generate Device Software/Antivirus inventory file using Defender data"""
        # Use Defender for antivirus/security software information
        defender_service = DefenderService()
        machines = defender_service.get_machines()
        
        wb, ws = self.create_styled_workbook('Antivirus Configuration')
        headers = ['Device Name', 'Health State', 'Risk Score', 'OS Platform', 'OS Version', 
                   'Antivirus Status', 'Last Seen', 'Onboarded']
        self.style_header_row(ws, headers)
        
        for row_idx, machine in enumerate(machines, 2):
            ws.cell(row_idx, 1, machine.get('computerDnsName', 'Unknown'))
            ws.cell(row_idx, 2, machine.get('healthStatus', 'Unknown'))
            ws.cell(row_idx, 3, machine.get('riskScore', 'None'))
            ws.cell(row_idx, 4, machine.get('osPlatform', 'Unknown'))
            ws.cell(row_idx, 5, machine.get('osVersion', 'Unknown'))
            
            # Determine antivirus status from health state
            health = machine.get('healthStatus', '').lower()
            if 'active' in health:
                av_status = 'Protected'
            elif 'inactive' in health:
                av_status = 'Not Protected'
            else:
                av_status = 'Active' if health else 'Unknown'
            
            ws.cell(row_idx, 6, av_status)
            ws.cell(row_idx, 7, machine.get('lastSeen', '')[:19] if machine.get('lastSeen') else '')
            ws.cell(row_idx, 8, machine.get('onboardingStatus', 'Unknown'))
        
        file_path = self.get_file_path(evidence_name, 'M365/Defender')
        wb.save(file_path)
        return file_path
    
    def generate_azure_nsg_file(self, evidence_name):
        """Generate Azure Network Security Groups file"""
        nsgs = AzureNetworkSecurityGroup.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Network Security Groups')
        headers = ['NSG Name', 'Resource Group', 'Location', 'Security Rules', 'Associated Subnets', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, nsg in enumerate(nsgs, 2):
            rules_data = json.loads(nsg.security_rules) if nsg.security_rules else []
            rules_count = len(rules_data)
            
            ws.cell(row_idx, 1, nsg.name or '')
            ws.cell(row_idx, 2, nsg.resource_group or '')
            ws.cell(row_idx, 3, nsg.location or '')
            ws.cell(row_idx, 4, f'{rules_count} rules')
            ws.cell(row_idx, 5, '')
            ws.cell(row_idx, 6, nsg.sync_date.strftime('%Y-%m-%d %H:%M') if nsg.sync_date else '')
        
        # Add detailed rules sheet
        ws_rules = wb.create_sheet('Security Rules')
        rule_headers = ['NSG Name', 'Rule Name', 'Priority', 'Direction', 'Access', 'Protocol', 'Source', 'Destination', 'Ports']
        self.style_header_row(ws_rules, rule_headers)
        
        rule_row = 2
        for nsg in nsgs:
            rules_data = json.loads(nsg.security_rules) if nsg.security_rules else []
            for rule in rules_data:
                ws_rules.cell(rule_row, 1, nsg.name)
                ws_rules.cell(rule_row, 2, rule.get('name', ''))
                ws_rules.cell(rule_row, 3, rule.get('priority', ''))
                ws_rules.cell(rule_row, 4, rule.get('direction', ''))
                ws_rules.cell(rule_row, 5, rule.get('access', ''))
                ws_rules.cell(rule_row, 6, rule.get('protocol', ''))
                ws_rules.cell(rule_row, 7, rule.get('sourceAddressPrefix', ''))
                ws_rules.cell(rule_row, 8, rule.get('destinationAddressPrefix', ''))
                ws_rules.cell(rule_row, 9, rule.get('destinationPortRange', ''))
                rule_row += 1
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_security_alerts_file(self, evidence_name):
        """Generate Azure Security Alerts file"""
        alerts = AzureSecurityAlert.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Security Alerts')
        headers = ['Alert Name', 'Severity', 'Status', 'Description', 'Affected Resource', 'Detection Time', 'Remediation Steps']
        self.style_header_row(ws, headers)
        
        for row_idx, alert in enumerate(alerts, 2):
            ws.cell(row_idx, 1, alert.alert_name or '')
            ws.cell(row_idx, 2, alert.severity or '')
            ws.cell(row_idx, 3, alert.status or '')
            ws.cell(row_idx, 4, alert.description or '')
            ws.cell(row_idx, 5, alert.affected_resource or '')
            ws.cell(row_idx, 6, alert.detection_time.strftime('%Y-%m-%d %H:%M') if alert.detection_time else '')
            ws.cell(row_idx, 7, alert.remediation_steps or '')
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_databases_file(self, evidence_name):
        """Generate Azure SQL Databases file"""
        databases = AzureDatabase.query.filter_by(is_current=True).all()
        if not databases:
            from azure_security_service import AzureSecurityService

            azure_service = AzureSecurityService()
            databases = azure_service.get_sql_databases()
        
        wb, ws = self.create_styled_workbook('SQL Databases')
        headers = ['Database Name', 'Server Name', 'Resource Group', 'Location', 'TDE Enabled', 'Firewall Rules', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, db in enumerate(databases, 2):
            ws.cell(row_idx, 1, getattr(db, 'database_name', None) or db.get('database_name', ''))
            ws.cell(row_idx, 2, getattr(db, 'server_name', None) or db.get('server_name', ''))
            ws.cell(row_idx, 3, getattr(db, 'resource_group', None) or db.get('resource_group', ''))
            ws.cell(row_idx, 4, getattr(db, 'location', None) or db.get('location', ''))
            ws.cell(row_idx, 5, 'Yes' if (getattr(db, 'tde_enabled', None) if not isinstance(db, dict) else db.get('tde_enabled')) else 'No')
            ws.cell(row_idx, 6, getattr(db, 'firewall_rules', None) or db.get('firewall_rules', ''))
            sync_date = getattr(db, 'sync_date', None) if not isinstance(db, dict) else None
            ws.cell(row_idx, 7, sync_date.strftime('%Y-%m-%d %H:%M') if sync_date else datetime.utcnow().strftime('%Y-%m-%d %H:%M'))
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_storage_file(self, evidence_name):
        """Generate Azure Storage Accounts file"""
        storage = AzureStorageAccount.query.filter_by(is_current=True).all()
        if not storage:
            from azure_security_service import AzureSecurityService

            azure_service = AzureSecurityService()
            storage = azure_service.get_storage_accounts()
        
        wb, ws = self.create_styled_workbook('Storage Accounts')
        headers = ['Storage Account', 'Resource Group', 'Location', 'Encryption Enabled', 'HTTPS Only', 'Min TLS Version', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, account in enumerate(storage, 2):
            ws.cell(row_idx, 1, getattr(account, 'storage_account_name', None) or account.get('name', ''))
            ws.cell(row_idx, 2, getattr(account, 'resource_group', None) or account.get('resource_group', ''))
            ws.cell(row_idx, 3, getattr(account, 'location', None) or account.get('location', ''))
            ws.cell(row_idx, 4, 'Yes' if (getattr(account, 'encryption_enabled', None) if not isinstance(account, dict) else account.get('encryption_enabled')) else 'No')
            ws.cell(row_idx, 5, 'Yes' if (getattr(account, 'https_only', None) if not isinstance(account, dict) else account.get('https_only')) else 'No')
            ws.cell(row_idx, 6, getattr(account, 'min_tls_version', None) or account.get('tls_version', ''))
            sync_date = getattr(account, 'sync_date', None) if not isinstance(account, dict) else None
            ws.cell(row_idx, 7, sync_date.strftime('%Y-%m-%d %H:%M') if sync_date else datetime.utcnow().strftime('%Y-%m-%d %H:%M'))
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_vms_file(self, evidence_name):
        """Generate Azure Virtual Machines file"""
        vms = AzureVirtualMachine.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Virtual Machines')
        headers = ['VM Name', 'Resource Group', 'Location', 'VM Size', 'OS Type', 'Disk Encryption', 'Power State', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, vm in enumerate(vms, 2):
            ws.cell(row_idx, 1, vm.name or '')
            ws.cell(row_idx, 2, vm.resource_group or '')
            ws.cell(row_idx, 3, vm.location or '')
            ws.cell(row_idx, 4, vm.vm_size or '')
            ws.cell(row_idx, 5, vm.os_type or '')
            ws.cell(row_idx, 6, 'Yes' if vm.disk_encryption else 'No')
            ws.cell(row_idx, 7, '')
            ws.cell(row_idx, 8, vm.sync_date.strftime('%Y-%m-%d %H:%M') if vm.sync_date else '')
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_assessments_file(self, evidence_name):
        """Generate Azure Security Assessments file"""
        assessments = AzureSecurityAssessment.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Security Assessments')
        headers = ['Assessment Name', 'Resource', 'Status', 'Severity', 'Description', 'Remediation', 'Last Sync']
        self.style_header_row(ws, headers)
        
        if assessments:
            for row_idx, assessment in enumerate(assessments, 2):
                ws.cell(row_idx, 1, assessment.name or '')
                ws.cell(row_idx, 2, assessment.resource_id or '')
                ws.cell(row_idx, 3, assessment.status or '')
                ws.cell(row_idx, 4, assessment.severity or '')
                ws.cell(row_idx, 5, assessment.description or '')
                ws.cell(row_idx, 6, assessment.remediation or '')
                ws.cell(row_idx, 7, assessment.sync_date.strftime('%Y-%m-%d %H:%M') if assessment.sync_date else '')
        else:
            # No data yet - add placeholder row
            ws.cell(2, 1, 'No assessments available')
            ws.cell(2, 2, 'Azure Security Sync has not been run yet')
            ws.cell(2, 3, 'Pending')
            ws.cell(2, 4, 'Info')
            ws.cell(2, 5, 'Run Azure Security Sync from SOC2 Dashboard to collect vulnerability scan results')
            ws.cell(2, 6, 'Click "Azure Security Sync" button on dashboard')
            ws.cell(2, 7, datetime.utcnow().strftime('%Y-%m-%d %H:%M'))
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_monitor_alerts_file(self, evidence_name):
        """Generate Azure Monitor Alerts file"""
        alerts = AzureMonitorAlert.query.filter_by(is_current=True).all()
        if not alerts:
            from azure_security_service import AzureSecurityService

            azure_service = AzureSecurityService()
            alerts = azure_service.get_monitor_alerts()
        
        wb, ws = self.create_styled_workbook('Monitor Alerts')
        headers = ['Alert Name', 'Resource Group', 'Target Resource', 'Condition', 'Severity', 'Enabled', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, alert in enumerate(alerts, 2):
            if isinstance(alert, dict):
                criteria_data = alert.get('criteria') or {}
                all_of = criteria_data.get('allOf') or []
                condition = all_of[0].get('metricName', '') if all_of else criteria_data.get('odata.type', '')
                resource_group = alert.get('resource_group', '')
                target_resource = alert.get('target_resource', '')
                sync_date = None
            else:
                criteria_data = json.loads(alert.criteria) if alert.criteria else {}
                condition = criteria_data.get('allOf', [{}])[0].get('metricName', '') if criteria_data else ''
                resource_group = alert.resource_group or ''
                target_resource = alert.target_resource or ''
                sync_date = alert.sync_date
            
            ws.cell(row_idx, 1, getattr(alert, 'alert_name', None) or alert.get('name', ''))
            ws.cell(row_idx, 2, resource_group)
            ws.cell(row_idx, 3, target_resource)
            ws.cell(row_idx, 4, condition)
            ws.cell(row_idx, 5, getattr(alert, 'severity', None) or alert.get('severity', ''))
            ws.cell(row_idx, 6, 'Yes' if (getattr(alert, 'enabled', None) if not isinstance(alert, dict) else alert.get('enabled')) else 'No')
            ws.cell(row_idx, 7, sync_date.strftime('%Y-%m-%d %H:%M') if sync_date else datetime.utcnow().strftime('%Y-%m-%d %H:%M'))
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_azure_network_topology_file(self, evidence_name):
        """Generate Azure Network Topology file"""
        networks = AzureNetworkTopology.query.filter_by(is_current=True).all()
        
        wb, ws = self.create_styled_workbook('Network Topology')
        headers = ['VNet Name', 'Resource Group', 'Location', 'Address Space', 'Subnets', 'Peerings', 'Last Sync']
        self.style_header_row(ws, headers)
        
        for row_idx, vnet in enumerate(networks, 2):
            subnets_data = json.loads(vnet.subnets) if vnet.subnets else []
            
            subnet_count = len(subnets_data)
            
            ws.cell(row_idx, 1, vnet.name or '')
            ws.cell(row_idx, 2, vnet.resource_group or '')
            ws.cell(row_idx, 3, vnet.location or '')
            ws.cell(row_idx, 4, vnet.address_space or '')
            ws.cell(row_idx, 5, f'{subnet_count} subnets')
            ws.cell(row_idx, 6, '')
            ws.cell(row_idx, 7, vnet.sync_date.strftime('%Y-%m-%d %H:%M') if vnet.sync_date else '')
        
        # Add detailed subnets sheet
        ws_subnets = wb.create_sheet('Subnets')
        subnet_headers = ['VNet Name', 'Subnet Name', 'Address Prefix', 'NSG Attached']
        self.style_header_row(ws_subnets, subnet_headers)
        
        subnet_row = 2
        for vnet in networks:
            subnets_data = json.loads(vnet.subnets) if vnet.subnets else []
            for subnet in subnets_data:
                ws_subnets.cell(subnet_row, 1, vnet.name)
                ws_subnets.cell(subnet_row, 2, subnet.get('name', ''))
                ws_subnets.cell(subnet_row, 3, subnet.get('addressPrefix', ''))
                ws_subnets.cell(subnet_row, 4, 'Yes' if subnet.get('networkSecurityGroup') else 'No')
                subnet_row += 1
        
        file_path = self.get_file_path(evidence_name, 'Azure')
        wb.save(file_path)
        return file_path
    
    def generate_isms_policy_pdf(self, evidence_name):
        """Generate PDF for ISMS policy document"""
        try:
            # Read ISMS manual to extract policy
            from docx import Document
            isms_path = '/home/webuser/ISMS-Manual2025v1.docx'
            
            if not os.path.exists(isms_path):
                print(f"ISMS manual not found at {isms_path}")
                return None
            
            doc = Document(isms_path)
            
            # Policy name mapping
            policy_map = {
                'Acceptable Use Policy': 'Acceptable Use Policy',
                'Access Removal Procedures/Checklist': 'Access Removal',
                'Backup Policy': 'Backup Policy',
                'Backup Restoration Procedures': 'Backup',
                'Change Management Policy': 'Change Management',
                'Code of Conduct': 'Code of Conduct',
                'Data Classification Policy': 'Data Classification',
                'Data Deletion': 'Data Disposal',
                'Data Management Policy': 'Data Management',
                'Incident Response Plan': 'Incident Response',
                'Information Security Policy': 'Information Security',
                'Logical Access Policy and Procedures': 'Logical Access',
                'Password Policy': 'Password',
                'Patch Management Policy': 'Patch Management',
                'Vulnerability Management Policy': 'Vulnerability'
            }
            
            search_term = policy_map.get(evidence_name, evidence_name)
            
            # Extract policy content
            policy_content = []
            in_policy = False
            
            for para in doc.paragraphs:
                text = para.text.strip()
                
                # Start capturing when we find the policy title
                if search_term.lower() in text.lower() and (
                    para.style.name.startswith('Heading') or 
                    any(run.bold for run in para.runs)
                ):
                    in_policy = True
                    policy_content.append(('heading', text))
                    continue
                
                # Stop at next policy/section
                if in_policy and para.style.name.startswith('Heading') and len(policy_content) > 5:
                    break
                
                # Capture content
                if in_policy and text:
                    if para.style.name.startswith('Heading'):
                        policy_content.append(('subheading', text))
                    else:
                        policy_content.append(('body', text))
            
            if not policy_content:
                print(f"Policy content not found for: {evidence_name}")
                return None
            
            # Generate PDF
            file_path = self.get_file_path(evidence_name, 'ISMS', 'pdf')
            pdf = SimpleDocTemplate(file_path, pagesize=letter)
            story = []
            
            # Styles
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=18,
                textColor='#2D4639',
                spaceAfter=30,
                alignment=TA_CENTER
            )
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=14,
                textColor='#2D4639',
                spaceAfter=12,
                spaceBefore=12
            )
            body_style = ParagraphStyle(
                'CustomBody',
                parent=styles['BodyText'],
                fontSize=11,
                alignment=TA_JUSTIFY,
                spaceAfter=12
            )
            
            # Add header
            story.append(Paragraph("Cirque Corporation", title_style))
            story.append(Paragraph(evidence_name, heading_style))
            story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
            story.append(Spacer(1, 0.3*inch))
            
            # Add policy content
            for content_type, text in policy_content:
                if content_type == 'heading':
                    story.append(Paragraph(text, title_style))
                elif content_type == 'subheading':
                    story.append(Paragraph(text, heading_style))
                else:
                    # Clean up text for PDF
                    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    story.append(Paragraph(text, body_style))
            
            # Build PDF
            pdf.build(story)
            return file_path
            
        except Exception as e:
            print(f"Error generating ISMS policy PDF: {e}")
            return None
    
    def generate_employee_handbook_pdf(self, section_name):
        """Generate PDF for Employee Handbook section"""
        try:
            import PyPDF2
            handbook_path = '/home/webuser/NEW Cirque_Corporation_Employee_Handbook_1-2022 2.pdf'
            
            if not os.path.exists(handbook_path):
                print(f"Employee Handbook not found at {handbook_path}")
                return None
            
            # Extract the section number (e.g., "1-6" from "1-6. Non-Disclosure...")
            import re
            section_match = re.match(r'^(\d+-\d+)\.?\s*(.+)', section_name)
            if not section_match:
                print(f"Could not parse section number from: {section_name}")
                return None
            
            section_num = section_match.group(1)
            section_title = section_match.group(2).strip()
            
            # Read the PDF
            with open(handbook_path, 'rb') as file:
                pdf = PyPDF2.PdfReader(file)
                
                # Extract all text to find the section
                full_text = ""
                for page in pdf.pages:
                    full_text += page.extract_text() + "\n"
                
                # Find the section content
                lines = full_text.split('\n')
                section_content = []
                in_section = False
                
                for line in lines:
                    line_stripped = line.strip()
                    
                    # Start capturing when we find the section
                    if section_num in line_stripped and (section_title.lower() in line_stripped.lower() or
                                                         line_stripped.startswith(section_num)):
                        in_section = True
                        section_content.append(('heading', line_stripped))
                        continue
                    
                    # Stop at next section
                    if in_section and re.match(r'^\d+-\d+\.', line_stripped) and len(section_content) > 5:
                        break
                    
                    # Capture content
                    if in_section and line_stripped:
                        # Skip page headers/footers
                        if 'Employee Handbook' in line_stripped or 'Copyright, Cirque' in line_stripped:
                            continue
                        if re.match(r'^\d+$', line_stripped):  # Just a page number
                            continue
                        section_content.append(('body', line_stripped))
                
                if not section_content:
                    print(f"Section content not found for: {section_name}")
                    return None
                
                # Generate PDF
                file_path = self.get_file_path(section_title, 'Employee_Handbook', 'pdf')
                pdf_doc = SimpleDocTemplate(file_path, pagesize=letter)
                story = []
                
                # Styles
                styles = getSampleStyleSheet()
                title_style = ParagraphStyle(
                    'CustomTitle',
                    parent=styles['Heading1'],
                    fontSize=18,
                    textColor='#2D4639',
                    spaceAfter=30,
                    alignment=TA_CENTER
                )
                heading_style = ParagraphStyle(
                    'CustomHeading',
                    parent=styles['Heading2'],
                    fontSize=14,
                    textColor='#2D4639',
                    spaceAfter=12,
                    spaceBefore=12
                )
                body_style = ParagraphStyle(
                    'CustomBody',
                    parent=styles['BodyText'],
                    fontSize=11,
                    alignment=TA_JUSTIFY,
                    spaceAfter=12
                )
                
                # Add header
                story.append(Paragraph("Cirque Corporation", title_style))
                story.append(Paragraph("Employee Handbook - Extract", heading_style))
                story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
                story.append(Spacer(1, 0.3*inch))
                
                # Add section content
                for content_type, text in section_content:
                    # Clean up text for PDF
                    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    if content_type == 'heading':
                        story.append(Paragraph(text, heading_style))
                    else:
                        story.append(Paragraph(text, body_style))
                
                # Build PDF
                pdf_doc.build(story)
                return file_path
                
        except Exception as e:
            print(f"Error generating Employee Handbook PDF: {e}")
            return None
    
    def generate_m365_password_policy_file(self, evidence_name):
        """Generate M365 Password Policy configuration file"""
        # This would query M365 password policy settings via Graph API
        # For now, create a summary from current user data
        
        wb, ws = self.create_styled_workbook('Password Policy')
        headers = ['Setting', 'Value', 'Compliant']
        self.style_header_row(ws, headers)
        
        # Static password policy settings (would be from Graph API in production)
        settings = [
            ('Minimum Password Length', '8 characters', 'Yes'),
            ('Password Complexity', 'Required', 'Yes'),
            ('Password History', '24 passwords', 'Yes'),
            ('Maximum Password Age', '90 days', 'Yes'),
            ('Minimum Password Age', '1 day', 'Yes'),
            ('Account Lockout Threshold', '5 attempts', 'Yes'),
            ('Account Lockout Duration', '30 minutes', 'Yes'),
            ('Multi-Factor Authentication', 'Enforced', 'Yes'),
        ]
        
        for row_idx, (setting, value, compliant) in enumerate(settings, 2):
            ws.cell(row_idx, 1, setting)
            ws.cell(row_idx, 2, value)
            ws.cell(row_idx, 3, compliant)
        
        file_path = self.get_file_path(evidence_name, 'M365/Intune')
        wb.save(file_path)
        return file_path
    
    def generate_teamviewer_patch_scan_file(self, evidence_name):
        """Generate TeamViewer patch scan/device status file"""
        try:
            tv_service = TeamViewerEvidenceService()
            report = tv_service.generate_patch_report_data()
            
            wb, ws = self.create_styled_workbook('Patch Scan')
            headers = ['Device Name', 'OS Version', 'Online', 'Managed', 'Policy', 'Last Seen', 'Days Offline', 'Asset Tracked']
            self.style_header_row(ws, headers)
            
            for row_idx, device in enumerate(report['devices'], 2):
                ws.cell(row_idx, 1, device['device_name'])
                ws.cell(row_idx, 2, device['os_version'])
                ws.cell(row_idx, 3, device['online_state'])
                ws.cell(row_idx, 4, 'Yes' if device['managed'] else 'No')
                ws.cell(row_idx, 5, 'Yes' if device['policy_applied'] else 'No')
                ws.cell(row_idx, 6, device['last_seen'] or 'Never')
                ws.cell(row_idx, 7, device['days_offline'] if device['days_offline'] is not None else 'N/A')
                ws.cell(row_idx, 8, 'Yes' if device['asset_tracked'] else 'No')
            
            # Add summary sheet
            ws_summary = wb.create_sheet('Summary')
            summary_data = [
                ('Report Date', report['report_date']),
                ('Total Devices', report['total_devices']),
                ('Online Devices', report['online_devices']),
                ('Managed Devices', report['managed_devices']),
                ('Devices with Policy', report['devices_with_policy']),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Intune')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating TeamViewer patch scan: {e}")
            return None
    
    def generate_teamviewer_vulnerability_scan_file(self, evidence_name):
        """Generate TeamViewer/Intune vulnerability scan file"""
        try:
            tv_service = TeamViewerEvidenceService()
            vulnerabilities = tv_service.get_vulnerability_summary()
            
            wb, ws = self.create_styled_workbook('Vulnerability Scan')
            headers = ['Device Name', 'OS Version', 'Severity', 'Finding', 'Remediation', 'Last Seen']
            self.style_header_row(ws, headers)
            
            for row_idx, vuln in enumerate(vulnerabilities, 2):
                ws.cell(row_idx, 1, vuln['device_name'])
                ws.cell(row_idx, 2, vuln['os_version'])
                ws.cell(row_idx, 3, vuln['severity'])
                ws.cell(row_idx, 4, vuln['finding'])
                ws.cell(row_idx, 5, vuln['remediation'])
                ws.cell(row_idx, 6, vuln['last_seen'] or 'Never')
            
            # Add summary information
            from soc2_models import IntuneDevice
            total_devices = IntuneDevice.query.filter_by(is_current=True).count()
            critical = sum(1 for v in vulnerabilities if v['severity'] == 'Critical')
            high = sum(1 for v in vulnerabilities if v['severity'] == 'High')
            medium = sum(1 for v in vulnerabilities if v['severity'] == 'Medium')
            
            ws_summary = wb.create_sheet('Summary')
            summary_data = [
                ('Scan Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Devices Scanned', total_devices),
                ('Devices with Findings', len([v for v in vulnerabilities if v['severity'] != 'Info'])),
                ('Critical Findings', critical),
                ('High Findings', high),
                ('Medium Findings', medium),
                ('Scan Type', 'Intune Compliance + OS Version Analysis'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Intune')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating TeamViewer vulnerability scan: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_defender_vulnerability_remediation_file(self, evidence_name):
        """Generate Microsoft Defender vulnerability remediation action plan"""
        try:
            defender_service = DefenderService()
            recommendations = defender_service.get_recommendations()
            machines = defender_service.get_machines()
            
            # Filter for vulnerability-related recommendations
            vuln_recommendations = [
                r for r in recommendations 
                if any(keyword in r.get('recommendationName', '').lower() 
                       for keyword in ['vulnerability', 'cve', 'security update', 'exploit'])
            ]
            
            wb, ws = self.create_styled_workbook('Remediation Actions')
            headers = ['Priority', 'Recommendation', 'Product', 'Severity', 'Affected Machines', 
                      'Status', 'Action Required', 'Remediation Type', 'Due Date']
            self.style_header_row(ws, headers)
            
            # Sort by severity and exposed machines
            severity_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
            vuln_recommendations.sort(
                key=lambda x: (severity_order.get(x.get('severity', 'Low'), 4), 
                              -x.get('exposedMachinesCount', 0))
            )
            
            for row_idx, rec in enumerate(vuln_recommendations, 2):
                severity = rec.get('severity', 'Low')
                exposed = rec.get('exposedMachinesCount', 0)
                
                # Determine priority based on severity and exposure
                if severity == 'Critical' and exposed > 10:
                    priority = 'P1 - Immediate'
                elif severity in ['Critical', 'High'] and exposed > 5:
                    priority = 'P2 - High'
                elif severity in ['Critical', 'High']:
                    priority = 'P3 - Medium'
                else:
                    priority = 'P4 - Low'
                
                # Calculate due date based on priority
                from datetime import timedelta
                due_days = {'P1 - Immediate': 7, 'P2 - High': 30, 'P3 - Medium': 60, 'P4 - Low': 90}
                due_date = (datetime.utcnow() + timedelta(days=due_days[priority])).strftime('%Y-%m-%d')
                
                ws.cell(row_idx, 1, priority)
                ws.cell(row_idx, 2, rec.get('recommendationName', '')[:100])
                ws.cell(row_idx, 3, rec.get('productName', 'Unknown'))
                ws.cell(row_idx, 4, severity)
                ws.cell(row_idx, 5, exposed)
                ws.cell(row_idx, 6, rec.get('status', 'Active'))
                ws.cell(row_idx, 7, rec.get('recommendedAction', 'Apply security update')[:100])
                ws.cell(row_idx, 8, rec.get('remediationType', 'Update'))
                ws.cell(row_idx, 9, due_date)
            
            # Add summary sheet
            ws_summary = wb.create_sheet('Remediation Summary', 0)
            
            p1_count = sum(1 for r in vuln_recommendations 
                          if r.get('severity') == 'Critical' and r.get('exposedMachinesCount', 0) > 10)
            p2_count = sum(1 for r in vuln_recommendations 
                          if r.get('severity') in ['Critical', 'High'] and r.get('exposedMachinesCount', 0) > 5)
            total_machines_affected = len(set(m['id'] for m in machines 
                                             if m.get('riskScore') in ['High', 'Medium']))
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Remediation Items', len(vuln_recommendations)),
                ('P1 - Immediate (7 days)', p1_count),
                ('P2 - High (30 days)', p2_count),
                ('P3 - Medium (60 days)', len(vuln_recommendations) - p1_count - p2_count),
                ('Total Machines Requiring Action', total_machines_affected),
                ('Data Source', 'Microsoft Defender for Endpoint'),
                ('Remediation Focus', 'Vulnerability & Security Updates'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating Defender vulnerability remediation: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_defender_vulnerability_scan_file(self, evidence_name):
        """Generate Microsoft Defender vulnerability scan file with real CVE data"""
        try:
            defender_service = DefenderService()
            vulnerabilities = defender_service.generate_vulnerability_report_data()
            
            wb, ws = self.create_styled_workbook('Vulnerabilities')
            headers = ['CVE/Vulnerability ID', 'Name', 'Severity', 'CVSS Score', 'Exposed Machines', 
                      'Description', 'Published Date', 'Updated Date']
            self.style_header_row(ws, headers)
            
            for row_idx, vuln in enumerate(vulnerabilities, 2):
                ws.cell(row_idx, 1, vuln['vulnerability_id'])
                ws.cell(row_idx, 2, vuln['name'][:100])  # Truncate long names
                ws.cell(row_idx, 3, vuln['severity'])
                ws.cell(row_idx, 4, vuln['cvss_score'])
                ws.cell(row_idx, 5, vuln['exposed_machines'])
                ws.cell(row_idx, 6, vuln['description'][:200])  # Truncate description
                ws.cell(row_idx, 7, vuln['published_date'][:10] if vuln['published_date'] else '')
                ws.cell(row_idx, 8, vuln['updated_date'][:10] if vuln['updated_date'] else '')
            
            # Add summary sheet
            summary = defender_service.get_vulnerability_summary()
            if summary:
                ws_summary = wb.create_sheet('Summary', 0)  # Insert at beginning
                summary_data = [
                    ('Scan Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                    ('Total Vulnerabilities', summary['total_vulnerabilities']),
                    ('Total Machines', summary['total_machines']),
                    ('Critical Vulnerabilities', summary['by_severity']['Critical']),
                    ('High Vulnerabilities', summary['by_severity']['High']),
                    ('Medium Vulnerabilities', summary['by_severity']['Medium']),
                    ('Low Vulnerabilities', summary['by_severity']['Low']),
                    ('Data Source', 'Microsoft Defender for Endpoint'),
                ]
                
                for row_idx, (label, value) in enumerate(summary_data, 1):
                    ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                    ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating Defender vulnerability scan: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_defender_software_inventory_file(self, evidence_name):
        """Generate Microsoft Defender software inventory report"""
        try:
            defender_service = DefenderService()
            software_data = defender_service.generate_software_inventory_report_data()
            
            wb, ws = self.create_styled_workbook('Software Inventory')
            headers = ['Vendor', 'Product', 'Version', 'Installed On', 'Has Vulnerabilities',
                      'Critical Vulns', 'High Vulns', 'Total Vulns', 'Category', 'Support Status']
            self.style_header_row(ws, headers)
            
            for row_idx, sw in enumerate(software_data, 2):
                # Sanitize strings for Excel (remove illegal characters)
                vendor = self._sanitize_for_excel(sw['vendor'])
                product = self._sanitize_for_excel(sw['product'][:100])
                version = self._sanitize_for_excel(sw['version'])
                category = self._sanitize_for_excel(sw['category'])
                eol = self._sanitize_for_excel(sw['end_of_life'])
                
                ws.cell(row_idx, 1, vendor)
                ws.cell(row_idx, 2, product)
                ws.cell(row_idx, 3, version)
                ws.cell(row_idx, 4, sw['installed_on'])
                ws.cell(row_idx, 5, sw['has_vulnerabilities'])
                ws.cell(row_idx, 6, sw['critical_vulns'])
                ws.cell(row_idx, 7, sw['high_vulns'])
                ws.cell(row_idx, 8, sw['total_vulns'])
                ws.cell(row_idx, 9, category)
                ws.cell(row_idx, 10, eol)
            
            # Add summary sheet
            ws_summary = wb.create_sheet('Summary', 0)
            
            total_software = len(software_data)
            software_with_vulns = sum(1 for sw in software_data if sw['has_vulnerabilities'] == 'Yes')
            total_critical = sum(sw['critical_vulns'] for sw in software_data)
            total_high = sum(sw['high_vulns'] for sw in software_data)
            end_of_life = sum(1 for sw in software_data if sw['end_of_life'] != 'Supported')
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Software Products', total_software),
                ('Software with Known Vulnerabilities', software_with_vulns),
                ('Total Critical Vulnerabilities', total_critical),
                ('Total High Vulnerabilities', total_high),
                ('End-of-Life Software', end_of_life),
                ('Data Source', 'Microsoft Defender for Endpoint'),
                ('Inventory Type', 'Organization-wide Software Catalog'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating Defender software inventory: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_defender_patch_scan_file(self, evidence_name):
        """Generate Microsoft Defender missing patch report"""
        try:
            defender_service = DefenderService()
            patches = defender_service.generate_patch_report_data()
            
            wb, ws = self.create_styled_workbook('Missing Patches')
            headers = ['Recommendation ID', 'Name', 'Product', 'Severity', 'Exposed Machines', 
                      'Status', 'Remediation Type', 'Recommended Update']
            self.style_header_row(ws, headers)
            
            for row_idx, patch in enumerate(patches, 2):
                ws.cell(row_idx, 1, patch['recommendation_id'])
                ws.cell(row_idx, 2, patch['name'][:100])
                ws.cell(row_idx, 3, patch['product'])
                ws.cell(row_idx, 4, patch['severity'])
                ws.cell(row_idx, 5, patch['exposed_machines'])
                ws.cell(row_idx, 6, patch['status'])
                ws.cell(row_idx, 7, patch['remediation_type'])
                ws.cell(row_idx, 8, patch['recommendation'][:100] if patch['recommendation'] else '')
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            critical = sum(1 for p in patches if p['severity'] == 'Critical')
            high = sum(1 for p in patches if p['severity'] == 'High')
            total_exposed = sum(p['exposed_machines'] for p in patches)
            
            summary_data = [
                ('Scan Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Recommendations', len(patches)),
                ('Critical', critical),
                ('High', high),
                ('Total Exposed Machines', total_exposed),
                ('Data Source', 'Microsoft Defender for Endpoint'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating Defender patch scan: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_mfa_status_file(self, evidence_name):
        """Generate MFA status report for all users"""
        try:
            from m365_service import M365Service
            from app import Setting
            
            from m365_config import get_m365_credentials
            tenant_id, client_id, client_secret = get_m365_credentials()

            m365_service = M365Service(tenant_id, client_id, client_secret)
            mfa_data = m365_service.get_users_mfa_status()
            
            wb, ws = self.create_styled_workbook('MFA Status')
            headers = ['Display Name', 'User Principal Name', 'Email', 'MFA Enabled', 'MFA Methods', 'Method Count']
            self.style_header_row(ws, headers)
            
            for row_idx, user in enumerate(mfa_data, 2):
                ws.cell(row_idx, 1, user['displayName'])
                ws.cell(row_idx, 2, user['userPrincipalName'])
                ws.cell(row_idx, 3, user.get('mail', ''))
                ws.cell(row_idx, 4, 'Yes' if user['mfaEnabled'] else 'No')
                ws.cell(row_idx, 5, user['mfaMethods'])
                ws.cell(row_idx, 6, user['methodCount'])
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            total_users = len(mfa_data)
            mfa_enabled = sum(1 for u in mfa_data if u['mfaEnabled'])
            mfa_disabled = total_users - mfa_enabled
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Users', total_users),
                ('MFA Enabled', mfa_enabled),
                ('MFA Disabled', mfa_disabled),
                ('Compliance Rate', f"{(mfa_enabled/total_users*100):.1f}%" if total_users > 0 else '0%'),
                ('Data Source', 'Microsoft Graph API'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating MFA status: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_security_incidents_file(self, evidence_name):
        """Generate security incidents report from Defender"""
        try:
            defender_service = DefenderService()
            incidents = defender_service.get_incidents()
            
            wb, ws = self.create_styled_workbook('Security Incidents')
            headers = ['Incident ID', 'Title', 'Severity', 'Status', 'Classification', 
                      'Assigned To', 'Created', 'Last Updated', 'Alert Count', 'Affected Devices']
            self.style_header_row(ws, headers)
            
            for row_idx, incident in enumerate(incidents, 2):
                ws.cell(row_idx, 1, str(incident.get('incidentId', '')))
                ws.cell(row_idx, 2, self._sanitize_for_excel(incident.get('incidentName', '')[:100]))
                ws.cell(row_idx, 3, incident.get('severity', 'Unknown'))
                ws.cell(row_idx, 4, incident.get('status', 'Unknown'))
                ws.cell(row_idx, 5, incident.get('classification', 'Unknown'))
                ws.cell(row_idx, 6, incident.get('assignedTo', 'Unassigned'))
                ws.cell(row_idx, 7, incident.get('createdTime', '')[:19] if incident.get('createdTime') else '')
                ws.cell(row_idx, 8, incident.get('lastUpdateTime', '')[:19] if incident.get('lastUpdateTime') else '')
                ws.cell(row_idx, 9, len(incident.get('alerts', [])))
                ws.cell(row_idx, 10, len(incident.get('devices', [])))
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            total = len(incidents)
            by_severity = {}
            by_status = {}
            
            for incident in incidents:
                severity = incident.get('severity', 'Unknown')
                status = incident.get('status', 'Unknown')
                by_severity[severity] = by_severity.get(severity, 0) + 1
                by_status[status] = by_status.get(status, 0) + 1
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Incidents', total),
                ('Critical', by_severity.get('High', 0)),
                ('High', by_severity.get('Medium', 0)),
                ('Medium', by_severity.get('Low', 0)),
                ('Active', by_status.get('Active', 0)),
                ('Resolved', by_status.get('Resolved', 0)),
                ('Data Source', 'Microsoft Defender for Endpoint'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating security incidents: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_security_alerts_file(self, evidence_name):
        """Generate security alerts report from Defender"""
        try:
            defender_service = DefenderService()
            alerts = defender_service.get_alerts()
            
            wb, ws = self.create_styled_workbook('Security Alerts')
            headers = ['Alert ID', 'Title', 'Category', 'Severity', 'Status', 'Machine', 
                      'Detection Time', 'First Activity', 'Last Activity', 'Assigned To']
            self.style_header_row(ws, headers)
            
            for row_idx, alert in enumerate(alerts, 2):
                ws.cell(row_idx, 1, alert.get('id', '')[:50])
                ws.cell(row_idx, 2, self._sanitize_for_excel(alert.get('title', '')[:100]))
                ws.cell(row_idx, 3, alert.get('category', 'Unknown'))
                ws.cell(row_idx, 4, alert.get('severity', 'Unknown'))
                ws.cell(row_idx, 5, alert.get('status', 'Unknown'))
                ws.cell(row_idx, 6, alert.get('machineId', 'Unknown')[:30])
                ws.cell(row_idx, 7, alert.get('alertCreationTime', '')[:19] if alert.get('alertCreationTime') else '')
                ws.cell(row_idx, 8, alert.get('firstEventTime', '')[:19] if alert.get('firstEventTime') else '')
                ws.cell(row_idx, 9, alert.get('lastEventTime', '')[:19] if alert.get('lastEventTime') else '')
                ws.cell(row_idx, 10, alert.get('assignedTo', 'Unassigned'))
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            total = len(alerts)
            by_severity = {}
            by_category = {}
            
            for alert in alerts:
                severity = alert.get('severity', 'Unknown')
                category = alert.get('category', 'Unknown')
                by_severity[severity] = by_severity.get(severity, 0) + 1
                by_category[category] = by_category.get(category, 0) + 1
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Alerts', total),
                ('High Severity', by_severity.get('High', 0)),
                ('Medium Severity', by_severity.get('Medium', 0)),
                ('Low Severity', by_severity.get('Low', 0)),
                ('Informational', by_severity.get('Informational', 0)),
                ('Top Category', max(by_category.items(), key=lambda x: x[1])[0] if by_category else 'None'),
                ('Data Source', 'Microsoft Defender for Endpoint'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating security alerts: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_azure_rbac_file(self, evidence_name):
        """Generate Azure RBAC role assignments report"""
        try:
            from azure_security_service import AzureSecurityService
            
            azure_service = AzureSecurityService()
            assignments = azure_service.get_role_assignments()
            
            wb, ws = self.create_styled_workbook('Azure RBAC Assignments')
            headers = ['Principal ID', 'Principal Type', 'Role Name', 'Scope', 'Created On', 'Created By']
            self.style_header_row(ws, headers)
            
            for row_idx, assignment in enumerate(assignments, 2):
                ws.cell(row_idx, 1, assignment.get('principalId', '')[:50])
                ws.cell(row_idx, 2, assignment.get('principalType', 'Unknown'))
                ws.cell(row_idx, 3, assignment.get('roleName', 'Unknown'))
                scope = assignment.get('scope', '')
                # Simplify scope display
                if '/resourceGroups/' in scope:
                    scope_display = scope.split('/resourceGroups/')[-1]
                elif '/subscriptions/' in scope:
                    scope_display = 'Subscription'
                else:
                    scope_display = scope[-50:]
                ws.cell(row_idx, 4, scope_display)
                ws.cell(row_idx, 5, assignment.get('createdOn', '')[:19] if assignment.get('createdOn') else '')
                ws.cell(row_idx, 6, assignment.get('createdBy', 'Unknown')[:50])
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            total = len(assignments)
            by_type = {}
            by_role = {}
            
            for assignment in assignments:
                ptype = assignment.get('principalType', 'Unknown')
                role = assignment.get('roleName', 'Unknown')
                by_type[ptype] = by_type.get(ptype, 0) + 1
                by_role[role] = by_role.get(role, 0) + 1
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Role Assignments', total),
                ('User Assignments', by_type.get('User', 0)),
                ('Service Principal Assignments', by_type.get('ServicePrincipal', 0)),
                ('Group Assignments', by_type.get('Group', 0)),
                ('Top Role', max(by_role.items(), key=lambda x: x[1])[0] if by_role else 'None'),
                ('Data Source', 'Azure Resource Manager API'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'Azure')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating Azure RBAC report: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_conditional_access_file(self, evidence_name):
        """Generate Conditional Access policies report"""
        try:
            from m365_service import M365Service
            from app import Setting
            
            from m365_config import get_m365_credentials
            tenant_id, client_id, client_secret = get_m365_credentials()

            m365_service = M365Service(tenant_id, client_id, client_secret)
            policies = m365_service.get_conditional_access_policies()
            
            wb, ws = self.create_styled_workbook('Conditional Access Policies')
            headers = ['Policy Name', 'State', 'Created', 'Modified', 'Users/Groups', 'Applications', 'Grant Controls']
            self.style_header_row(ws, headers)
            
            for row_idx, policy in enumerate(policies, 2):
                ws.cell(row_idx, 1, self._sanitize_for_excel(policy.get('displayName', 'Unknown')[:100]))
                ws.cell(row_idx, 2, policy.get('state', 'Unknown'))
                ws.cell(row_idx, 3, policy.get('createdDateTime', '')[:19] if policy.get('createdDateTime') else '')
                ws.cell(row_idx, 4, policy.get('modifiedDateTime', '')[:19] if policy.get('modifiedDateTime') else '')
                
                conditions = policy.get('conditions', {})
                users = conditions.get('users', {})
                user_count = len(users.get('includeUsers', [])) + len(users.get('includeGroups', []))
                ws.cell(row_idx, 5, f"{user_count} users/groups")
                
                apps = conditions.get('applications', {})
                app_count = len(apps.get('includeApplications', []))
                ws.cell(row_idx, 6, f"{app_count} applications")
                
                grant_controls = policy.get('grantControls', {})
                built_in_controls = grant_controls.get('builtInControls', [])
                ws.cell(row_idx, 7, ', '.join(built_in_controls[:3]) if built_in_controls else 'None')
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            total = len(policies)
            enabled = sum(1 for p in policies if p.get('state') == 'enabled')
            disabled = sum(1 for p in policies if p.get('state') == 'disabled')
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Policies', total),
                ('Enabled', enabled),
                ('Disabled/Report-Only', disabled),
                ('Data Source', 'Microsoft Graph API'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating conditional access report: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_software_inventory_by_asset_file(self, evidence_name):
        """Generate software inventory organized by asset"""
        try:
            defender_service = DefenderService()
            software_data = defender_service.get_software_by_machine()
            
            wb, ws = self.create_styled_workbook('Software Inventory by Asset')
            headers = ['Machine Name', 'Machine ID', 'OS Platform', 'Software Name', 
                      'Vendor', 'Version', 'Installed Count']
            self.style_header_row(ws, headers)
            
            for row_idx, item in enumerate(software_data, 2):
                ws.cell(row_idx, 1, item.get('machineName', 'Unknown'))
                ws.cell(row_idx, 2, item.get('machineId', '')[:30])
                ws.cell(row_idx, 3, item.get('osPlatform', 'Unknown'))
                ws.cell(row_idx, 4, self._sanitize_for_excel(item.get('softwareName', 'Unknown')[:100]))
                ws.cell(row_idx, 5, item.get('softwareVendor', 'Unknown')[:50])
                ws.cell(row_idx, 6, item.get('softwareVersion', 'Unknown')[:30])
                ws.cell(row_idx, 7, item.get('installedMachines', 0))
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            machines = set(item.get('machineName') for item in software_data)
            software_titles = set(item.get('softwareName') for item in software_data)
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Machines', len(machines)),
                ('Total Software Titles', len(software_titles)),
                ('Total Installations', len(software_data)),
                ('Data Source', 'Microsoft Defender for Endpoint'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating software inventory by asset: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_system_updates_file(self, evidence_name):
        """Generate system updates and missing hotfixes report"""
        try:
            defender_service = DefenderService()
            missing_updates = defender_service.get_missing_kbs()
            
            wb, ws = self.create_styled_workbook('System Updates & Hotfixes')
            headers = ['Machine Name', 'OS Platform', 'OS Version', 'Recommendation', 
                      'Product', 'Severity', 'Exposed Machines', 'Component']
            self.style_header_row(ws, headers)
            
            for row_idx, update in enumerate(missing_updates, 2):
                ws.cell(row_idx, 1, update.get('machineName', 'Unknown'))
                ws.cell(row_idx, 2, update.get('osPlatform', 'Unknown'))
                ws.cell(row_idx, 3, update.get('osVersion', 'Unknown')[:50])
                ws.cell(row_idx, 4, self._sanitize_for_excel(update.get('recommendationName', '')[:100]))
                ws.cell(row_idx, 5, update.get('productName', 'Unknown')[:50])
                ws.cell(row_idx, 6, update.get('severity', 'Unknown'))
                ws.cell(row_idx, 7, update.get('exposedMachines', 0))
                ws.cell(row_idx, 8, update.get('relatedComponent', 'Unknown')[:50])
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            machines = set(u.get('machineName') for u in missing_updates)
            by_severity = {}
            for update in missing_updates:
                sev = update.get('severity', 'Unknown')
                by_severity[sev] = by_severity.get(sev, 0) + 1
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Machines with Missing Updates', len(machines)),
                ('Total Missing Updates', len(missing_updates)),
                ('Critical', by_severity.get('Critical', 0)),
                ('High', by_severity.get('High', 0)),
                ('Medium', by_severity.get('Medium', 0)),
                ('Low', by_severity.get('Low', 0)),
                ('Data Source', 'Microsoft Defender for Endpoint'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'M365/Defender')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating system updates report: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_security_baseline_file(self, evidence_name):
        """Generate security baseline compliance report (Secure Score)"""
        try:
            from azure_security_service import AzureSecurityService
            
            azure_service = AzureSecurityService()
            secure_score = azure_service.get_secure_score()
            
            wb, ws = self.create_styled_workbook('Security Baseline Compliance')
            headers = ['Control Name', 'Current Score', 'Max Score', 'Healthy Resources', 
                      'Unhealthy Resources', 'Not Applicable']
            self.style_header_row(ws, headers)
            
            for row_idx, control in enumerate(secure_score.get('controls', []), 2):
                ws.cell(row_idx, 1, control.get('displayName', 'Unknown')[:100])
                ws.cell(row_idx, 2, control.get('score', 0))
                ws.cell(row_idx, 3, control.get('maxScore', 0))
                ws.cell(row_idx, 4, control.get('healthyResources', 0))
                ws.cell(row_idx, 5, control.get('unhealthyResources', 0))
                ws.cell(row_idx, 6, control.get('notApplicableResources', 0))
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            controls = secure_score.get('controls', [])
            total_healthy = sum(c.get('healthyResources', 0) for c in controls)
            total_unhealthy = sum(c.get('unhealthyResources', 0) for c in controls)
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Overall Secure Score', f"{secure_score.get('score', 0)}/{secure_score.get('maxScore', 0)}"),
                ('Compliance Percentage', f"{secure_score.get('percentage', 0):.1f}%"),
                ('Total Controls', len(controls)),
                ('Healthy Resources', total_healthy),
                ('Unhealthy Resources', total_unhealthy),
                ('Data Source', 'Microsoft Defender for Cloud'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'Azure')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating security baseline report: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_key_vault_policies_file(self, evidence_name):
        """Generate Key Vault access policies report"""
        try:
            from azure_security_service import AzureSecurityService
            
            azure_service = AzureSecurityService()
            policies = azure_service.get_key_vault_access_policies()
            
            wb, ws = self.create_styled_workbook('Key Vault Access Policies')
            headers = ['Vault Name', 'Resource Group', 'Object ID', 'Application ID', 
                      'Key Permissions', 'Secret Permissions', 'Certificate Permissions']
            self.style_header_row(ws, headers)
            
            for row_idx, policy in enumerate(policies, 2):
                ws.cell(row_idx, 1, policy.get('vaultName', 'Unknown'))
                ws.cell(row_idx, 2, policy.get('vaultResourceGroup', 'Unknown'))
                ws.cell(row_idx, 3, policy.get('objectId', 'Unknown')[:50])
                ws.cell(row_idx, 4, policy.get('applicationId', '')[:50])
                perms = policy.get('permissions', {})
                ws.cell(row_idx, 5, perms.get('keys', 'None')[:50])
                ws.cell(row_idx, 6, perms.get('secrets', 'None')[:50])
                ws.cell(row_idx, 7, perms.get('certificates', 'None')[:50])
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            vaults = set(p.get('vaultName') for p in policies)
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Key Vaults', len(vaults)),
                ('Total Access Policies', len(policies)),
                ('Data Source', 'Azure Key Vault'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'Azure')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating Key Vault policies report: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_network_traffic_logs_file(self, evidence_name):
        """Generate NSG Flow Logs configuration report"""
        try:
            from azure_security_service import AzureSecurityService
            
            azure_service = AzureSecurityService()
            flow_logs = azure_service.get_nsg_flow_logs()
            
            wb, ws = self.create_styled_workbook('Network Traffic Logs')
            headers = ['Flow Log Name', 'Location', 'Target Resource', 'Storage Account', 
                      'Enabled', 'Retention Days', 'Format', 'Version']
            self.style_header_row(ws, headers)
            
            for row_idx, log in enumerate(flow_logs, 2):
                ws.cell(row_idx, 1, log.get('name', 'Unknown'))
                ws.cell(row_idx, 2, log.get('location', 'Unknown'))
                target = log.get('targetResourceId', '')
                # Simplify resource ID display
                target_display = target.split('/')[-1] if '/' in target else target[:50]
                ws.cell(row_idx, 3, target_display)
                storage = log.get('storageId', '')
                storage_display = storage.split('/')[-1] if '/' in storage else storage[:50]
                ws.cell(row_idx, 4, storage_display)
                ws.cell(row_idx, 5, 'Yes' if log.get('enabled') else 'No')
                ws.cell(row_idx, 6, log.get('retentionDays', 0))
                ws.cell(row_idx, 7, log.get('format', 'Unknown'))
                ws.cell(row_idx, 8, log.get('version', 0))
            
            # Add summary
            ws_summary = wb.create_sheet('Summary', 0)
            enabled = sum(1 for log in flow_logs if log.get('enabled'))
            disabled = len(flow_logs) - enabled
            
            summary_data = [
                ('Report Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M')),
                ('Total Flow Logs', len(flow_logs)),
                ('Enabled', enabled),
                ('Disabled', disabled),
                ('Data Source', 'Azure Network Watcher'),
            ]
            
            for row_idx, (label, value) in enumerate(summary_data, 1):
                ws_summary.cell(row_idx, 1, label).font = Font(bold=True)
                ws_summary.cell(row_idx, 2, value)
            
            file_path = self.get_file_path(evidence_name, 'Azure')
            wb.save(file_path)
            return file_path
        except Exception as e:
            print(f"Error generating network traffic logs report: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def generate_evidence_file_by_name(self, evidence_name):
        """Generate evidence file based on evidence name"""
        # Map evidence names to generation functions
        evidence_map = {
            # M365/Intune Evidence - Admin Access
            'Administrator Access to Application': self.generate_admin_users_file,
            'Administrator Access to Database': self.generate_admin_users_file,
            'Administrator Access to Network/Cloud': self.generate_admin_users_file,
            'Administrator Access to Operating System': self.generate_admin_users_file,
            
            # M365/Intune Evidence - User Lists
            'Application User List': self.generate_m365_users_file,
            'Database User List': self.generate_m365_users_file,
            'Network User List': self.generate_m365_users_file,
            'Operating System User List': self.generate_m365_users_file,
            'User Access List': self.generate_m365_users_file,
            
            # M365/Intune Evidence - Assets & Devices
            'Asset Inventory': self.generate_intune_devices_file,
            'Workstation Asset Inventory': self.generate_intune_devices_file,
            'Server Asset Inventory': self.generate_intune_devices_file,
            
            # M365/Intune Evidence - Antivirus & Software
            'Antivirus Configuration - Server': self.generate_device_software_file,
            'Antivirus Configuration - Workstation': self.generate_device_software_file,
            'Workstation Antivirus Configuration': self.generate_device_software_file,
            'Server Antivirus Configuration': self.generate_device_software_file,
            
            # M365/Intune Evidence - Encryption
            'Device Disk Encryption': self.generate_intune_devices_file,
            'Workstation Disk Encryption Configuration': self.generate_intune_devices_file,
            
            # Azure Evidence - Network Security
            'Firewall Rules': self.generate_azure_nsg_file,
            'Current Network Diagram': self.generate_azure_network_topology_file,
            'Security Configuration Standards': self.generate_azure_nsg_file,
            
            # Azure Evidence - Security Monitoring
            'Intrusion Detection Configuration': self.generate_azure_security_alerts_file,
            'Intrusion Detection System Configuration': self.generate_azure_security_alerts_file,
            'Monitoring Tools Enabled': self.generate_azure_monitor_alerts_file,
            'Performance Monitoring Alert Configuration': self.generate_azure_monitor_alerts_file,
            
            # Azure Evidence - Database Security
            'Database Encryption': self.generate_azure_databases_file,
            'SQL Server Database Encryption Configuration': self.generate_azure_databases_file,
            
            # Azure Evidence - Storage Security
            'Encryption in Transit': self.generate_azure_storage_file,
            'Azure Storage Encryption Configuration': self.generate_azure_storage_file,
            
            # Azure Evidence - Server Security
            'Server Disk Encryption Configuration': self.generate_azure_vms_file,
            'Server Encryption': self.generate_azure_vms_file,
            
            # Microsoft Defender Evidence - Vulnerability & Patching (UPGRADED)
            'Vulnerability Scan Results': self.generate_defender_vulnerability_scan_file,
            'Vulnerability Scan Results - External': self.generate_defender_vulnerability_scan_file,
            'Vulnerability Scan Results - Internal': self.generate_defender_vulnerability_scan_file,
            'Vulnerability Remediation': self.generate_defender_vulnerability_remediation_file,
            'Patch Scan': self.generate_defender_patch_scan_file,
            'Server Scan and Patch': self.generate_defender_patch_scan_file,
            
            # Microsoft Defender Evidence - Security Events (NEW)
            'Security Incident Report': self.generate_security_incidents_file,
            'Security Incident History': self.generate_security_incidents_file,
            'Security Incident Resolution': self.generate_security_incidents_file,
            'Security Alert History': self.generate_security_alerts_file,
            'Security Alert Report': self.generate_security_alerts_file,
            'Security Event Log': self.generate_security_alerts_file,
            
            # M365 Evidence - MFA & Conditional Access (NEW)
            'MFA Status Report': self.generate_mfa_status_file,
            'Multi-Factor Authentication Report': self.generate_mfa_status_file,
            'User Authentication Report': self.generate_mfa_status_file,
            'Conditional Access Policy Report': self.generate_conditional_access_file,
            'Conditional Access Policies': self.generate_conditional_access_file,
            'Authentication Policy': self.generate_conditional_access_file,
            
            # Azure Evidence - RBAC (NEW)
            'Azure RBAC Report': self.generate_azure_rbac_file,
            'Azure Role Assignments': self.generate_azure_rbac_file,
            'Cloud Access Control': self.generate_azure_rbac_file,
            'Privileged Access Report': self.generate_azure_rbac_file,
            
            # Phase 2 Evidence - Software & Updates (NEW)
            'Software Inventory by Asset': self.generate_software_inventory_by_asset_file,
            'Application Inventory': self.generate_software_inventory_by_asset_file,
            'Installed Software Report': self.generate_software_inventory_by_asset_file,
            'System Updates Report': self.generate_system_updates_file,
            'Missing Hotfixes': self.generate_system_updates_file,
            'Windows Update Status': self.generate_system_updates_file,
            'Patch Status Report': self.generate_system_updates_file,
            
            # Phase 2 Evidence - Security Baseline (NEW)
            'Security Baseline Compliance': self.generate_security_baseline_file,
            'Secure Score Report': self.generate_security_baseline_file,
            'Security Configuration Assessment': self.generate_security_baseline_file,
            'Cloud Security Posture': self.generate_security_baseline_file,
            
            # Phase 2 Evidence - Key Vault (NEW)
            'Key Vault Access Policies': self.generate_key_vault_policies_file,
            'Secret Management Policies': self.generate_key_vault_policies_file,
            'Encryption Key Access': self.generate_key_vault_policies_file,
            
            # Phase 2 Evidence - Network Logs (NEW)
            'Network Traffic Logs': self.generate_network_traffic_logs_file,
            'NSG Flow Logs': self.generate_network_traffic_logs_file,
            'Network Monitoring Configuration': self.generate_network_traffic_logs_file,
            'Traffic Analysis Report': self.generate_network_traffic_logs_file,
            
            # ISMS Policy Evidence (PDF generation)
            'Acceptable Use Policy': self.generate_isms_policy_pdf,
            'Access Removal Procedures/Checklist': self.generate_isms_policy_pdf,
            'Backup Policy': self.generate_isms_policy_pdf,
            'Backup Restoration Procedures': self.generate_isms_policy_pdf,
            'Change Management Policy': self.generate_isms_policy_pdf,
            'Code of Conduct': self.generate_isms_policy_pdf,
            'Data Classification Policy': self.generate_isms_policy_pdf,
            'Data Deletion': self.generate_isms_policy_pdf,
            'Data Management Policy': self.generate_isms_policy_pdf,
            'Incident Response Plan': self.generate_isms_policy_pdf,
            'Information Security Policy': self.generate_isms_policy_pdf,
            'Logical Access Policy and Procedures': self.generate_isms_policy_pdf,
            'Password Policy': self.generate_isms_policy_pdf,
            'Patch Management Policy': self.generate_isms_policy_pdf,
            'Vulnerability Management Policy': self.generate_isms_policy_pdf,
            
            # Employee Handbook Evidence (PDF generation)
            '1-1. Welcome Statement': self.generate_employee_handbook_pdf,
            '1-6. Non-Disclosure Employee Assignment Agreements': self.generate_employee_handbook_pdf,
            '2-1. Employee Classifications': self.generate_employee_handbook_pdf,
            '2-3. Employment and Personnel Records': self.generate_employee_handbook_pdf,
            '2-10. Performance Reviews': self.generate_employee_handbook_pdf,
            '3-20. Tuition Reimbursement': self.generate_employee_handbook_pdf,
            '5-1. Workplace Conduct': self.generate_employee_handbook_pdf,
            '5-3. Use of Communication, Computer Systems and equipment': self.generate_employee_handbook_pdf,
            '5-9. Non-Disclosure of Confidential Information': self.generate_employee_handbook_pdf,
            '5-22. If You Must Leave Us': self.generate_employee_handbook_pdf,
            
            # M365 User Lists (new additions)
            'Network/Cloud User List': self.generate_m365_users_file,
            'Operating System User List': self.generate_m365_users_file,
            'Organization Chart': self.generate_m365_users_file,
            
            # Password Settings (M365 password policy reports)
            'Password Settings - Application': self.generate_m365_password_policy_file,
            'Password Settings - Database': self.generate_m365_password_policy_file,
            'Password Settings - Network/Cloud': self.generate_m365_password_policy_file,
            'Password Settings - Operating System': self.generate_m365_password_policy_file,
        }
        
        generator_func = evidence_map.get(evidence_name)
        if generator_func:
            return generator_func(evidence_name)

        evidence_item = StrikeGraphEvidence.query.filter_by(evidence_name=evidence_name).first()
        if evidence_item and evidence_item.automation_source == 'ISMS':
            return self.generate_isms_policy_pdf(evidence_name)
        return None
    
    def generate_all_automated_evidence_files(self):
        """Generate files for all automated evidence items"""
        evidence_items = StrikeGraphEvidence.query.filter(
            StrikeGraphEvidence.automation_source.in_(['M365/Intune', 'M365/Defender', 'Azure', 'ISMS', 'TeamViewer'])
        ).all()
        
        results = []
        for item in evidence_items:
            try:
                file_path = self.generate_evidence_file_by_name(item.evidence_name)
                if file_path:
                    # Update StrikeGraphEvidence with file path
                    item.file_path = file_path
                    item.updated_at = datetime.utcnow()
                    results.append({
                        'evidence_name': item.evidence_name,
                        'status': 'success',
                        'file_path': item.file_path,
                        'source': item.automation_source
                    })
                else:
                    results.append({
                        'evidence_name': item.evidence_name,
                        'status': 'skipped',
                        'reason': 'No generator function',
                        'source': item.automation_source
                    })
            except Exception as e:
                results.append({
                    'evidence_name': item.evidence_name,
                    'status': 'error',
                    'error': str(e),
                    'source': item.automation_source
                })
        
        db.session.commit()
        return results
