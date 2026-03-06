#!/usr/bin/env python3
"""
Manually map the 7 remaining controls to appropriate policies
"""
import sqlite3

def manual_control_mappings():
    db_path = '/var/www/tracker/assets.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"🔗 CREATING MANUAL CONTROL-POLICY MAPPINGS")
    print(f"=" * 80)
    
    # Define manual mappings (control_name -> policy_document_ids)
    manual_mappings = {
        'Antivirus': ['IS-CIRQ-P-036-G'],  # Antivirus and Endpoint Protection Policy
        'Board Oversight OR Management Oversight': ['IS-CIRQ-P-037-G'],  # Corporate Governance and Oversight Policy
        'Employee Performance': ['IS-CIRQ-P-038-G'],  # Employee Performance Management Policy
        'Employee Shared Drive': ['IS-CIRQ-P-039-G'],  # Information Sharing and Document Repository Policy
        'Job Descriptions': ['IS-CIRQ-P-002-G'],  # Roles, Responsibilities, and Authorities Policy
        'Organizational Chart': ['IS-CIRQ-P-002-G', 'IS-CIRQ-P-037-G'],  # Roles Policy + Governance Policy
        'Separation of Duties: Developers': ['IS-CIRQ-P-012-G', 'IS-CIRQ-PR-017-G'],  # Secure System Dev + Secure Dev Procedure
        'Automatic Patching': ['IS-CIRQ-P-035-G'],  # Patch Management Policy
    }
    
    mappings_created = 0
    
    for control_name, policy_doc_ids in manual_mappings.items():
        # Get control ID
        cursor.execute("SELECT id FROM control WHERE control_name = ?", (control_name,))
        control_result = cursor.fetchone()
        
        if not control_result:
            print(f"⚠️  Control not found: {control_name}")
            continue
        
        control_id = control_result[0]
        
        for policy_doc_id in policy_doc_ids:
            # Get policy ID
            cursor.execute("SELECT id, title FROM policy WHERE document_id = ?", (policy_doc_id,))
            policy_result = cursor.fetchone()
            
            if not policy_result:
                print(f"⚠️  Policy not found: {policy_doc_id}")
                continue
            
            policy_id, policy_title = policy_result
            
            # Check if mapping already exists
            cursor.execute("""
                SELECT id FROM policy_control_mapping 
                WHERE policy_id = ? AND control_id = ?
            """, (policy_id, control_id))
            
            if cursor.fetchone():
                print(f"⏭️  Mapping already exists: {control_name} → {policy_doc_id}")
                continue
            
            # Create mapping
            cursor.execute("""
                INSERT INTO policy_control_mapping (policy_id, control_id, mapping_type)
                VALUES (?, ?, 'manual')
            """, (policy_id, control_id))
            
            print(f"✅ Mapped: {control_name}")
            print(f"   └─ {policy_doc_id}: {policy_title}")
            mappings_created += 1
    
    conn.commit()
    
    # Verify - check how many controls still have no mappings
    cursor.execute("""
        SELECT c.control_name
        FROM control c
        WHERE c.is_active = 1
        AND NOT EXISTS (
            SELECT 1 FROM policy_control_mapping pcm
            WHERE pcm.control_id = c.id
        )
        ORDER BY c.control_name
    """)
    
    unmapped = cursor.fetchall()
    
    print(f"\n📊 MAPPING SUMMARY")
    print(f"=" * 80)
    print(f"Manual Mappings Created: {mappings_created}")
    print(f"Controls Without Policies: {len(unmapped)}")
    
    if unmapped:
        print(f"\n⚠️  Remaining unmapped controls:")
        for (control_name,) in unmapped:
            print(f"  - {control_name}")
    else:
        print(f"\n✅ ALL CONTROLS NOW HAVE POLICY MAPPINGS!")
    
    # Show total mapping count
    cursor.execute("SELECT COUNT(*) FROM policy_control_mapping")
    total_mappings = cursor.fetchone()[0]
    print(f"\nTotal Policy-Control Mappings: {total_mappings}")
    
    conn.close()

if __name__ == '__main__':
    manual_control_mappings()
