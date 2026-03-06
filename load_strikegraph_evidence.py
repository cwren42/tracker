#!/usr/bin/env python3
"""
Load StrikeGraph evidence repository into database
"""
import csv
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from soc2_models import SOC2Control, StrikeGraphEvidence

def parse_date(date_str):
    """Parse date from MM/DD/YYYY format"""
    if not date_str or date_str == 'null':
        return None
    try:
        return datetime.strptime(date_str, '%m/%d/%Y').date()
    except:
        return None

def get_control_mappings():
    """Return StrikeGraph evidence to SOC2 control mappings - using exact control names"""
    return {
        # Access Controls
        'Administrator Access to Application': 'Administrator Access',
        'Administrator Access to Database': 'Administrator Access',
        'Administrator Access to Network/Cloud': 'Administrator Access',
        'Administrator Access to Operating System': 'Administrator Access',
        'Access Request - New Hire': 'Provisioning',
        'Access Request - Current Employee': 'Provisioning',
        'Access Removal Procedures/Checklist': 'Termination of Access',
        'Access Termination Ticket': 'Termination of Access',
        'Application User List': 'Password Requirements',
        'Database User List': 'Password Requirements',
        'Network/Cloud User List': 'Password Requirements',
        'Operating System User List': 'Password Requirements',
        'Periodic Logical Access Review': 'User Access Review',
        'New Employee Access': 'Provisioning',
        
        # Asset Management
        'Asset Inventory': 'Asset Inventory',
        'Antivirus Configuration - Server': 'Antivirus',
        'Antivirus Configuration - Workstation': 'Antivirus',
        
        # Change Management
        'Change Management Policy': 'Change Management Policy',
        'Change Management Tool': 'Change Management: Ticketing System',
        'Change Management - Developers': 'Change Management: Application/Software',
        'Change Management - Production Access': 'Separation of Duties: IT Operations',
        'Application/Software Change List Parameters': 'Change Management: Application/Software',
        'Application/Software Change Testing': 'Change Management: Application/Software',
        'Emergency Change': 'Change Management: Emergency Process',
        'Infrastructure Change Testing': 'Change Management: Infrastructure',
        'Merge Overrider List': 'Change Management: Separation of Duties',
        'Merge SOD Configuration Check': 'Change Management: Separation of Duties',
        'Separation of Environments': 'Separation of Environments',
        
        # Security Configuration
        'Device Disk Encryption': 'Disk Encryption',
        'Database Encryption': 'Encryption at Rest',
        'Encryption in Transit': 'Encryption in Transit',
        'Firewall Rules': 'Firewall Rules',
        'Server Encryption': 'Encryption at Rest',
        'Security Configuration Standards': 'Configuration Standards',
        'Intrusion Detection Configuration': 'Intrusion Detection',
        'Monitoring Tools Enabled': 'Monitoring Infrastructure',
        
        # Patch Management
        'Server Scan and Patch': 'Automatic Patching',
        'Patch Scan': 'Automatic Patching',
        'Patch Management Policy': 'Automatic Patching',
        
        # Vulnerability Management
        'Vulnerability Scan Results': 'Vulnerability Scanning',
        'Vulnerability Remediation': 'Vulnerability Scanning',
        'Vulnerability Management Policy': 'Vulnerability Scanning',
        
        # Data Classification & Management
        'Data Classification Policy': 'Data Classification Policy',
        'Data Management Policy': 'Data Management Policy',
        'Record Retention Schedule': 'Data Retention/Deletion',
        'Data Deletion': 'Data Retention/Deletion',
        'Data Disposal Ticket': 'Data Retention/Deletion',
        'Current Data Flow Diagram': 'Data Flow Diagram',
        
        # Policies
        'Password Policy': 'Password Requirements',
        'Password Settings - Application': 'Password Requirements',
        'Password Settings - Database': 'Password Requirements',
        'Password Settings - Network/Cloud': 'Password Requirements',
        'Password Settings - Operating System': 'Password Requirements',
        'Logical Access Policy and Procedures': 'Logical Access Policy',
        'Acceptable Use Policy': 'Acceptable Use Policy',
        'Signed Acceptable Use Policy': 'Acceptable Use Policy',
        'Code of Conduct': 'Code of Conduct',
        'Signed Code of Conduct': 'Code of Conduct',
        
        # Business Continuity
        'Business Continuity Plan': 'Business Continuity',
        'Business Continuity Tabletop Test': 'Business Continuity',
        'Backup Policy': 'Backup Configuration',
        'Backup Restoration Procedures': 'Backup Configuration',
        'Restoration Test': 'Backup Configuration',
        
        # Incident Response
        'Incident Response Plan': 'Incident Response: Process',
        'Incident Response Tabletop Test': 'Incident Response: Testing',
        'Inbound Communication Resolution': 'Incidents External',
        'Contact Information': 'Incidents External',
        'Employee Reporting': 'Incident Response: Employee Responsibility',
        'Security Incident Resolution': 'Incident Response: Process',
        
        # Risk Management
        'Critical Vendor SOC 2 Reports': 'Vendor Risk Management',
        'Critical Vendor SOC 2 Review': 'Vendor Risk Management',
        'Vendor Contract': 'Contracts',
        'Vendor Due Diligence': 'Vendor Risk Management',
        'Vendor Risk Register': 'Vendor Risk Management',
        'Vendor Management Policy and Procedures': 'Vendor Risk Management',
        'Risk Assessment': 'Risk Assessment Methodology',
        'Risk Management Policy and Procedures': 'Risk Assessment Policy',
        'Management Review - Risk Assessment': 'Risk Assessment Action Plans',
        
        # Training & HR
        'Annual Employee Training': 'Security Training',
        'Training Materials': 'Security Training',
        'New Hire Training': 'Security Training',
        'Employee Screening': 'Background Check',
        'Employee Job Descriptions': 'Job Descriptions',
        'Employee Intranet': 'Employee Shared Drive',
        'Performance Review or Template': 'Employee Performance',
        'Signed Non Disclosure Agreement - Employee': 'Non Disclosure Agreement',
        'Signed Non Disclosure - Third Party': 'Contracts',
        
        # System Documentation
        'Control Matrix': 'Control Ownership',
        'Control Review': 'Control Ownership',
        'Current Network Diagram': 'Network Diagram',
        'Organizational Chart': 'Organizational Chart',
        'System Description Document': 'System Description',
        'Information Security Policy': 'Information Security Policy',
        
        # Monitoring & Alerts
        'Performance Monitoring Alert': 'Monitoring Infrastructure',
        'Performance Monitoring Alert Configuration': 'Monitoring Infrastructure',
        
        # Customer/Privacy
        'Customer Contract': 'Contracts',
        'Collection: Reliable Source': 'Collection: Reliable Source',
        
        # System Documentation
        'Current Network Diagram': 'System Documentation',
        'Current Data Flow Diagram': 'System Documentation',
        'Control Matrix': 'System Documentation',
        'Control Review': 'System Documentation',
        
        # Data Protection
        'Data Deletion': 'Data Protection',
        'Data Disposal Ticket': 'Data Protection',
        'Customer Contract': 'Data Protection'
    }

def get_automation_source(evidence_name):
    """Determine automation source for evidence"""
    automated_m365 = [
        'Administrator Access to Application',
        'Administrator Access to Database',
        'Administrator Access to Network/Cloud',
        'Administrator Access to Operating System',
        'Application User List',
        'Database User List',
        'Antivirus Configuration - Server',
        'Antivirus Configuration - Workstation',
        'Asset Inventory',
        'Device Disk Encryption'
    ]
    
    isms_policies = [
        'Acceptable Use Policy',
        'Access Removal Procedures/Checklist',
        'Backup Policy',
        'Backup Restoration Procedures',
        'Change Management Policy',
        'Code of Conduct',
        'Data Classification Policy',
        'Data Management Policy',
        'Incident Response Plan'
    ]
    
    if evidence_name in automated_m365:
        return 'M365/Intune'
    elif evidence_name in isms_policies:
        return 'ISMS'
    else:
        return 'Manual'

def load_evidence(csv_path):
    """Load StrikeGraph evidence from CSV"""
    
    control_mappings = get_control_mappings()
    
    with app.app_context():
        # Get all control IDs
        controls = {c.control_name: c.id for c in SOC2Control.query.all()}
        
        # Clear existing evidence
        print("Clearing existing StrikeGraph evidence...")
        StrikeGraphEvidence.query.delete()
        db.session.commit()
        
        print(f"Loading evidence from {csv_path}...")
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            count = 0
            
            for row in reader:
                evidence_name = row['Evidence Name']
                control_name = control_mappings.get(evidence_name)
                control_id = controls.get(control_name) if control_name else None
                
                # Parse expiration schedule (days as integer)
                try:
                    exp_schedule = int(row['Expiration Schedule']) if row['Expiration Schedule'] else None
                except:
                    exp_schedule = None
                
                evidence = StrikeGraphEvidence(
                    control_id=control_id,
                    evidence_name=evidence_name,
                    evidence_description=row['Evidence Description'],
                    evidence_type=row['Type'],
                    expiration_schedule=exp_schedule,
                    expiration_date=parse_date(row['Expiration Date']),
                    is_active=row['Inactive/Active'] == 'TRUE',
                    owner=row['Evidence Owner'] if row['Evidence Owner'] else None,
                    automation_source=get_automation_source(evidence_name)
                )
                
                db.session.add(evidence)
                count += 1
                
                if count % 10 == 0:
                    print(f"  Loaded {count} items...")
            
            db.session.commit()
            print(f"\n✓ Successfully loaded {count} evidence items")
            
            # Show statistics
            print("\nStatistics:")
            total = StrikeGraphEvidence.query.count()
            mapped = StrikeGraphEvidence.query.filter(StrikeGraphEvidence.control_id.isnot(None)).count()
            automated = StrikeGraphEvidence.query.filter(StrikeGraphEvidence.automation_source == 'M365/Intune').count()
            isms = StrikeGraphEvidence.query.filter(StrikeGraphEvidence.automation_source == 'ISMS').count()
            manual = StrikeGraphEvidence.query.filter(StrikeGraphEvidence.automation_source == 'Manual').count()
            
            print(f"  Total items: {total}")
            print(f"  Mapped to controls: {mapped} ({mapped/total*100:.1f}%)")
            print(f"  Automated (M365/Intune): {automated}")
            print(f"  ISMS Policies: {isms}")
            print(f"  Manual: {manual}")
            
            # Show by type
            print("\nBy Evidence Type:")
            for evidence_type in ['Policy', 'Sample', 'General', 'Settings', 'Population']:
                count = StrikeGraphEvidence.query.filter_by(evidence_type=evidence_type).count()
                print(f"  {evidence_type}: {count}")
            
            # Show items needing attention (expiring soon)
            print("\nItems Expiring in Next 30 Days:")
            from datetime import timedelta
            soon = datetime.utcnow().date() + timedelta(days=30)
            expiring = StrikeGraphEvidence.query.filter(
                StrikeGraphEvidence.expiration_date.isnot(None),
                StrikeGraphEvidence.expiration_date <= soon,
                StrikeGraphEvidence.is_active == True
            ).all()
            
            if expiring:
                for item in expiring:
                    days = item.days_until_expiration()
                    print(f"  - {item.evidence_name}: {days} days (expires {item.expiration_date})")
            else:
                print("  None")

def main():
    csv_path = "/home/webuser/cirque_corporation-evidence-1-9-2026-sg (1).csv"
    
    if not os.path.exists(csv_path):
        print(f"Error: File not found: {csv_path}")
        return
    
    load_evidence(csv_path)
    print("\n✓ StrikeGraph evidence loaded successfully")

if __name__ == '__main__':
    main()
