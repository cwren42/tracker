#!/usr/bin/env python3
"""
Create Business Continuity/Disaster Recovery Plan policy in database
"""

import sqlite3
from datetime import datetime

DB_PATH = '/var/www/tracker/assets.db'

def create_bcdr_policy():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if policy already exists
    cursor.execute("SELECT id FROM policy WHERE document_id = 'IS-CIRQ-P-043-G'")
    existing = cursor.fetchone()
    
    if existing:
        print(f"Policy IS-CIRQ-P-043-G already exists with ID {existing[0]}")
        return existing[0]
    
    # Read the policy content from the file
    with open('/var/www/tracker/static/evidence/manual/IS-CIRQ-P-043-G_Business_Continuity_Disaster_Recovery_Plan_20260302.md', 'r') as f:
        content = f.read()
    
    # Insert the policy
    cursor.execute("""
        INSERT INTO policy (
            document_id,
            title,
            category,
            division,
            standard_type,
            version,
            effective_date,
            review_date,
            approved_by,
            content,
            file_path,
            created_at,
            updated_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'IS-CIRQ-P-043-G',
        'Business Continuity and Disaster Recovery Plan',
        'Operations Security',
        'General',
        'Policy',
        '1.0',
        '2026-03-02',
        '2027-03-02',
        'Chris Wren, CISO',
        content,
        'static/evidence/manual/IS-CIRQ-P-043-G_Business_Continuity_Disaster_Recovery_Plan_20260302.md',
        datetime.now().isoformat(),
        'chris.wren@cirque.com'
    ))
    
    policy_id = cursor.lastrowid
    print(f"Created policy IS-CIRQ-P-043-G with ID {policy_id}")
    
    conn.commit()
    conn.close()
    
    return policy_id

def map_policy_to_controls(policy_id):
    """Map BC/DR policy to relevant controls"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # BC/DR policy relates to multiple controls
    # Get control IDs for Business Continuity and related operational controls
    cursor.execute("""
        SELECT id, control_name FROM control 
        WHERE control_name LIKE '%Continuity%'
           OR control_name LIKE '%Disaster%'
           OR control_name LIKE '%Backup%'
           OR control_name LIKE '%Recovery%'
           OR control_name LIKE '%Incident%'
    """)
    
    controls = cursor.fetchall()
    
    if not controls:
        print("No matching controls found - will need to map manually")
        conn.close()
        return
    
    print(f"Found {len(controls)} related controls")
    
    for control_id, control_name in controls:
        # Check if mapping already exists
        cursor.execute("""
            SELECT id FROM policy_control_mapping 
            WHERE policy_id = ? AND control_id = ?
        """, (policy_id, control_id))
        
        if cursor.fetchone():
            print(f"Mapping already exists for control: {control_name}")
            continue
        
        cursor.execute("""
            INSERT INTO policy_control_mapping (policy_id, control_id)
            VALUES (?, ?)
        """, (policy_id, control_id))
        
        print(f"Mapped to control: {control_name}")
    
    conn.commit()
    conn.close()

if __name__ == '__main__':
    print("Creating Business Continuity/Disaster Recovery Plan policy...")
    policy_id = create_bcdr_policy()
    print(f"\nMapping policy to controls...")
    map_policy_to_controls(policy_id)
    print("\nDone! Policy created successfully.")
    print(f"Policy ID: {policy_id}")
    print("Policy Number: IS-CIRQ-P-043-G")
    print("\nNext steps:")
    print("1. Generate PDF using generate_policy_pdfs.py")
    print("2. Add to StrikeGraph evidence for relevant controls")
