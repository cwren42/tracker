#!/usr/bin/env python3
"""
Analyze SOC2 controls and map to existing policies
Identify missing policies that need to be created
"""
import sqlite3
import re
from collections import defaultdict

def analyze_policy_requirements():
    db_path = '/var/www/tracker/assets.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get all controls
    cursor.execute("SELECT id, control_name, control_description FROM control")
    controls = cursor.fetchall()
    
    # Get all existing policies
    cursor.execute("SELECT id, document_id, title FROM policy")
    policies = cursor.fetchall()
    
    print(f"📊 SOC2 POLICY REQUIREMENTS ANALYSIS")
    print(f"=" * 80)
    print(f"\n✓ Found {len(controls)} controls")
    print(f"✓ Found {len(policies)} existing policies\n")
    
    # Policy keywords to match
    policy_mappings = {
        'Acceptable Use': ['acceptable use', 'aup'],
        'Access Control': ['access control', 'logical access', 'password', 'authentication', 'provisioning', 'termination'],
        'Asset Management': ['asset', 'inventory'],
        'Background Check': ['background', 'hiring', 'hr'],
        'Business Continuity': ['business continuity', 'disaster recovery', 'bcdr'],
        'Change Management': ['change management', 'change control'],
        'Code of Conduct': ['code of conduct', 'ethics'],
        'Configuration': ['configuration', 'baseline'],
        'Contracts': ['contract', 'agreement', 'vendor contract'],
        'Communication': ['communication'],
        'Data Classification': ['data classification', 'information classification'],
        'Data Management': ['data management', 'data handling'],
        'Data Retention': ['retention', 'deletion', 'disposal'],
        'Document Control': ['document', 'documentation'],
        'Encryption': ['encryption', 'cryptography'],
        'Incident Response': ['incident', 'security incident'],
        'Management Review': ['management review'],
        'Network Security': ['network', 'firewall', 'intrusion'],
        'NDA': ['non-disclosure', 'nda', 'confidentiality'],
        'Physical Security': ['physical', 'environmental'],
        'Risk Management': ['risk management', 'risk assessment'],
        'Security Training': ['training', 'awareness', 'competence'],
        'Vendor Management': ['vendor', 'supplier', 'third party'],
        'Vulnerability': ['vulnerability', 'patching', 'scanning']
    }
    
    # Analysis results
    control_policy_map = defaultdict(list)
    required_policies = set()
    
    # Analyze each control
    for control_id, control_name, control_description in controls:
        # Check if control name contains policy keyword
        for policy_type, keywords in policy_mappings.items():
            for keyword in keywords:
                if keyword.lower() in control_name.lower() or keyword.lower() in (control_description or '').lower():
                    control_policy_map[control_id].append(policy_type)
                    required_policies.add(policy_type)
                    break
    
    # Match existing policies
    existing_policy_types = {}
    for policy_id, doc_id, title in policies:
        for policy_type, keywords in policy_mappings.items():
            for keyword in keywords:
                if keyword.lower() in title.lower():
                    existing_policy_types[policy_type] = (policy_id, doc_id, title)
                    break
    
    # Identify missing policies
    missing_policies = required_policies - set(existing_policy_types.keys())
    
    print(f"📋 REQUIRED POLICY TYPES: {len(required_policies)}")
    print("-" * 80)
    for policy_type in sorted(required_policies):
        if policy_type in existing_policy_types:
            _, doc_id, title = existing_policy_types[policy_type]
            print(f"✅ {policy_type:30} → {doc_id} - {title}")
        else:
            print(f"❌ {policy_type:30} → MISSING - NEEDS TO BE CREATED")
    
    print(f"\n🔍 MISSING POLICIES: {len(missing_policies)}")
    print("-" * 80)
    for policy_type in sorted(missing_policies):
        print(f"  - {policy_type}")
    
    # Create policy-control mappings in database
    print(f"\n💾 CREATING POLICY-CONTROL MAPPINGS...")
    print("-" * 80)
    
    cursor.execute("DELETE FROM policy_control_mapping")
    
    mappings_created = 0
    for control_id, policy_types in control_policy_map.items():
        for policy_type in policy_types:
            if policy_type in existing_policy_types:
                policy_id = existing_policy_types[policy_type][0]
                try:
                    cursor.execute("""
                        INSERT INTO policy_control_mapping (policy_id, control_id, mapping_type)
                        VALUES (?, ?, 'automated')
                    """, (policy_id, control_id))
                    mappings_created += 1
                except:
                    pass  # Duplicate, skip
    
    conn.commit()
    print(f"✓ Created {mappings_created} policy-control mappings")
    
    # Summary
    print(f"\n📊 SUMMARY")
    print("=" * 80)
    print(f"Total Controls: {len(controls)}")
    print(f"Required Policy Types: {len(required_policies)}")
    print(f"Existing Policies: {len(existing_policy_types)}")
    print(f"Missing Policies: {len(missing_policies)}")
    print(f"Policy-Control Mappings: {mappings_created}")
    
    # Controls without policies
    unmapped_controls = []
    for control_id, control_name, _ in controls:
        if control_id not in control_policy_map or not control_policy_map[control_id]:
            unmapped_controls.append(control_name)
    
    if unmapped_controls:
        print(f"\n⚠️  {len(unmapped_controls)} controls without policy mappings:")
        for name in unmapped_controls[:10]:
            print(f"  - {name}")
        if len(unmapped_controls) > 10:
            print(f"  ... and {len(unmapped_controls) - 10} more")
    
    conn.close()

if __name__ == '__main__':
    analyze_policy_requirements()
