#!/usr/bin/env python3
"""
Map policy evidence to SOC2 controls based on policy_control_mapping table.

This script:
1. Finds all policies in the database
2. Gets the controls mapped to each policy via policy_control_mapping
3. Creates/updates strikegraph_evidence entries with control_id for each mapping
"""

import sqlite3
from datetime import datetime, timedelta

def map_policy_evidence_to_controls():
    """Map policy evidence entries to controls"""
    db_path = '/var/www/tracker/assets.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🔗 MAPPING POLICY EVIDENCE TO CONTROLS")
    print("=" * 80)
    
    # Get all policies with their file paths from strikegraph_evidence
    cursor.execute("""
        SELECT DISTINCT p.id, p.title, p.document_id, se.file_path
        FROM policy p
        LEFT JOIN strikegraph_evidence se ON se.evidence_name = p.title
        WHERE se.evidence_type = 'Policy' AND se.file_path IS NOT NULL
        ORDER BY p.document_id
    """)
    
    policies = cursor.fetchall()
    print(f"Found {len(policies)} policies with evidence files\n")
    
    created_count = 0
    updated_count = 0
    skipped_count = 0
    
    for policy_id, policy_title, document_id, file_path in policies:
        # Get all controls mapped to this policy
        cursor.execute("""
            SELECT c.id, c.control_name
            FROM control c
            JOIN policy_control_mapping pcm ON c.id = pcm.control_id
            WHERE pcm.policy_id = ?
            ORDER BY c.control_name
        """, (policy_id,))
        
        controls = cursor.fetchall()
        
        if not controls:
            print(f"⚠️  {document_id} - {policy_title}: No controls mapped")
            continue
        
        print(f"📄 {document_id} - {policy_title}")
        print(f"   Mapped to {len(controls)} control(s)")
        
        for control_id, control_name in controls:
            # Check if evidence entry exists for this policy-control pair
            cursor.execute("""
                SELECT id, file_path
                FROM strikegraph_evidence
                WHERE evidence_name = ? AND control_id = ?
            """, (policy_title, control_id))
            
            existing = cursor.fetchone()
            
            if existing:
                evidence_id, old_file_path = existing
                if old_file_path != file_path:
                    # Update file path
                    cursor.execute("""
                        UPDATE strikegraph_evidence
                        SET file_path = ?,
                            updated_at = ?
                        WHERE id = ?
                    """, (file_path, datetime.now(), evidence_id))
                    updated_count += 1
                    print(f"      ✓ Updated: {control_name} (ID: {control_id})")
                else:
                    skipped_count += 1
                    print(f"      • Exists: {control_name} (ID: {control_id})")
            else:
                # Create new evidence entry for this control
                expiration_date = (datetime.now() + timedelta(days=365)).date()
                
                cursor.execute("""
                    INSERT INTO strikegraph_evidence (
                        control_id, evidence_name, evidence_description, 
                        evidence_type, expiration_schedule, expiration_date,
                        is_active, owner, automation_source, file_path,
                        submission_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    control_id,
                    policy_title,
                    f"{document_id} - {policy_title}",
                    'Policy',
                    365,
                    expiration_date,
                    True,
                    'chris.wren@cirque.com',
                    'ISMS',
                    file_path,
                    'Not Submitted'
                ))
                created_count += 1
                print(f"      + Created: {control_name} (ID: {control_id})")
        
        print()
    
    conn.commit()
    conn.close()
    
    print("\n" + "=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)
    print(f"Evidence Created: {created_count}")
    print(f"Evidence Updated: {updated_count}")
    print(f"Already Current: {skipped_count}")
    print(f"\n✅ Policy evidence mapped to controls successfully!")

if __name__ == '__main__':
    map_policy_evidence_to_controls()
