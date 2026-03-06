#!/usr/bin/env python3
"""
Parse StrikeGraph Evidence Repository CSV and map to SOC2 controls
"""
import csv
import sys
import os
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from soc2_models import SOC2Control

def parse_strikegraph_csv(csv_path):
    """Parse the StrikeGraph evidence repository CSV"""
    evidence_items = []
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            evidence_items.append({
                'name': row['Evidence Name'],
                'description': row['Evidence Description'],
                'expiration_schedule': row['Expiration Schedule'],
                'expiration_date': row['Expiration Date'],
                'is_active': row['Inactive/Active'] == 'TRUE',
                'owner': row['Evidence Owner'],
                'type': row['Type']  # Policy, Sample, General, Settings, Population
            })
    
    return evidence_items

def categorize_by_type(evidence_items):
    """Categorize evidence by type"""
    by_type = defaultdict(list)
    for item in evidence_items:
        by_type[item['type']].append(item)
    
    print(f"\n{'='*80}")
    print("STRIKEGRAPH EVIDENCE SUMMARY")
    print(f"{'='*80}\n")
    print(f"Total Evidence Items: {len(evidence_items)}\n")
    
    for evidence_type, items in sorted(by_type.items()):
        print(f"{evidence_type}: {len(items)} items")
    
    return by_type

def map_to_soc2_controls(evidence_items):
    """Map StrikeGraph evidence to SOC2 controls"""
    
    # Mapping StrikeGraph evidence names to our SOC2 control names
    mappings = {
        # Access Controls
        'Administrator Access': [
            'Administrator Access to Application',
            'Administrator Access to Database',
            'Administrator Access to Network/Cloud',
            'Administrator Access to Operating System'
        ],
        'User Access Provisioning': [
            'Access Request - New Hire',
            'Access Request - Current Employee'
        ],
        'User Access Termination': [
            'Access Removal Procedures/Checklist',
            'Access Termination Ticket'
        ],
        'User Authentication': [
            'Application User List',
            'Database User List'
        ],
        
        # Asset Management
        'Asset Inventory': [
            'Asset Inventory'
        ],
        'Antivirus Protection': [
            'Antivirus Configuration - Server',
            'Antivirus Configuration - Workstation'
        ],
        
        # Change Management
        'Change Management Policy': [
            'Change Management Policy',
            'Change Management Tool',
            'Change Management - Developers',
            'Change Management - Production Access',
            'Application/Software Change List Parameters',
            'Application/Software Change Testing',
            'Emergency Change'
        ],
        
        # Security Configuration
        'Security Configuration': [
            'Device Disk Encryption',
            'Database Encryption',
            'Encryption in Transit',
            'Firewall Rules'
        ],
        
        # Risk Management
        'Risk Assessment': [
            'Critical Vendor SOC 2 Reports',
            'Critical Vendor SOC 2 Review'
        ],
        
        # Business Continuity
        'Business Continuity': [
            'Business Continuity Plan',
            'Business Continuity Tabletop Test',
            'Backup Policy',
            'Backup Restoration Procedures'
        ],
        
        # Incident Response
        'Incident Response': [
            'Incident Response Plan',
            'Inbound Communication Resolution',
            'Contact Information',
            'Employee Reporting'
        ],
        
        # Policies & Training
        'Policies & Training': [
            'Acceptable Use Policy',
            'Code of Conduct',
            'Data Classification Policy',
            'Data Management Policy',
            'Annual Employee Training',
            'Employee Intranet',
            'Employee Job Descriptions',
            'Employee Screening'
        ],
        
        # System Documentation
        'System Documentation': [
            'Current Network Diagram',
            'Current Data Flow Diagram',
            'Control Matrix',
            'Control Review'
        ],
        
        # Data Protection
        'Data Protection': [
            'Data Deletion',
            'Data Disposal Ticket',
            'Customer Contract'
        ]
    }
    
    # Reverse mapping: StrikeGraph evidence -> SOC2 control
    sg_to_control = {}
    for control_name, sg_items in mappings.items():
        for sg_item in sg_items:
            sg_to_control[sg_item] = control_name
    
    # Analyze coverage
    print(f"\n{'='*80}")
    print("STRIKEGRAPH TO SOC2 CONTROL MAPPING")
    print(f"{'='*80}\n")
    
    mapped_count = 0
    unmapped_items = []
    
    control_evidence_count = defaultdict(int)
    
    for item in evidence_items:
        if item['name'] in sg_to_control:
            mapped_count += 1
            control_name = sg_to_control[item['name']]
            control_evidence_count[control_name] += 1
        else:
            unmapped_items.append(item['name'])
    
    print(f"Mapped: {mapped_count}/{len(evidence_items)} items ({mapped_count/len(evidence_items)*100:.1f}%)\n")
    
    # Show evidence count per control
    print("Evidence Items per SOC2 Control:")
    for control_name in sorted(control_evidence_count.keys()):
        count = control_evidence_count[control_name]
        print(f"  {control_name}: {count} items")
    
    if unmapped_items:
        print(f"\n\nUnmapped Evidence Items ({len(unmapped_items)}):")
        for item in sorted(unmapped_items):
            print(f"  - {item}")
    
    return sg_to_control, mappings

def identify_automation_opportunities(evidence_items, by_type):
    """Identify which evidence can be automated vs manual"""
    
    print(f"\n{'='*80}")
    print("AUTOMATION ANALYSIS")
    print(f"{'='*80}\n")
    
    # Our current automated collections
    automated = [
        'Administrator Access to Application',
        'Administrator Access to Database',
        'Administrator Access to Network/Cloud',
        'Administrator Access to Operating System',
        'Application User List',
        'Database User List',
        'Antivirus Configuration - Server',
        'Antivirus Configuration - Workstation',
        'Asset Inventory',
        'Device Disk Encryption',
        'Change Management - Developers',
        'Change Management - Production Access'
    ]
    
    # Could be automated with M365/Intune
    could_automate = [
        'Annual Employee Training',  # Training completion reports from LMS
        'Firewall Rules',  # Azure firewall/NSG rules via API
        'Database Encryption',  # Database settings via API
        'Encryption in Transit'  # TLS/SSL settings
    ]
    
    # Manual evidence required
    manual_only = [item['name'] for item in evidence_items 
                   if item['name'] not in automated and item['name'] not in could_automate]
    
    print(f"Currently Automated: {len(automated)} items")
    print(f"Could Automate: {len(could_automate)} items")
    print(f"Manual Only: {len(manual_only)} items")
    
    print("\n\nCould Automate with Additional API Integration:")
    for item in could_automate:
        print(f"  - {item}")
    
    return automated, could_automate, manual_only

def show_coverage_gaps(evidence_items, sg_to_control):
    """Show what StrikeGraph requires that we don't collect"""
    
    print(f"\n{'='*80}")
    print("EVIDENCE COLLECTION GAPS")
    print(f"{'='*80}\n")
    
    # Items StrikeGraph wants that we're not collecting
    not_collecting = []
    
    automated_evidence = [
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
    
    for item in evidence_items:
        if item['name'] not in automated_evidence and item['is_active']:
            not_collecting.append({
                'name': item['name'],
                'type': item['type'],
                'owner': item['owner'],
                'control': sg_to_control.get(item['name'], 'Unmapped')
            })
    
    # Group by control
    by_control = defaultdict(list)
    for item in not_collecting:
        by_control[item['control']].append(item)
    
    print("Evidence Not Yet Automated (by Control):\n")
    for control_name in sorted(by_control.keys()):
        items = by_control[control_name]
        print(f"\n{control_name} ({len(items)} items):")
        for item in items:
            owner_str = f" [Owner: {item['owner']}]" if item['owner'] else ""
            print(f"  - [{item['type']}] {item['name']}{owner_str}")

def main():
    csv_path = "/home/webuser/cirque_corporation-evidence-1-9-2026-sg (1).csv"
    
    print("Parsing StrikeGraph Evidence Repository...")
    evidence_items = parse_strikegraph_csv(csv_path)
    
    by_type = categorize_by_type(evidence_items)
    sg_to_control, mappings = map_to_soc2_controls(evidence_items)
    automated, could_automate, manual_only = identify_automation_opportunities(evidence_items, by_type)
    show_coverage_gaps(evidence_items, sg_to_control)
    
    print(f"\n{'='*80}")
    print("NEXT STEPS")
    print(f"{'='*80}\n")
    print("1. Create StrikeGraphEvidence model to store this mapping")
    print("2. Link evidence items to SOC2Control records")
    print("3. Track submission status and expiration dates")
    print("4. Add StrikeGraph view to dashboard showing coverage")
    print("5. Generate evidence packages for manual submission")
    print()

if __name__ == '__main__':
    main()
