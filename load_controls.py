#!/usr/bin/env python3
"""
Load SOC2 controls from CSV file into database
"""
import sqlite3
import csv
import os

def load_controls():
    db_path = '/var/www/tracker/assets.db'
    csv_path = '/var/www/tracker/cirque_corporation-controls.csv'
    
    if not os.path.exists(csv_path):
        print(f"❌ CSV file not found at {csv_path}")
        print("Please upload the controls CSV file to /var/www/tracker/")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Clear existing controls
        cursor.execute("DELETE FROM control")
        
        # Read and load controls from CSV
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            controls_loaded = 0
            for row in reader:
                control_name = row.get('Control Name', '').strip()
                if not control_name:
                    continue
                
                control_description = row.get('Control Description', '').strip()
                control_frequency = row.get('Control Frequency', '').strip()
                control_owner = row.get('Control Owner', '').strip()
                control_progress = row.get('Control Progress', '').strip()
                is_active = row.get('Inactive/Active', 'TRUE').strip().upper() == 'TRUE'
                audit_alignment = row.get('Audit Alignment', '').strip()
                
                cursor.execute("""
                    INSERT INTO control (
                        control_name, control_description, control_frequency,
                        control_owner, control_progress, is_active, audit_alignment
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    control_name, control_description, control_frequency,
                    control_owner, control_progress, is_active, audit_alignment
                ))
                
                controls_loaded += 1
        
        conn.commit()
        print(f"✓ Loaded {controls_loaded} controls from CSV")
        
        # Show summary by progress
        cursor.execute("""
            SELECT control_progress, COUNT(*) 
            FROM control 
            GROUP BY control_progress
        """)
        print("\nControls by Progress:")
        for progress, count in cursor.fetchall():
            print(f"  - {progress}: {count}")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to load controls: {e}")
        conn.rollback()
        return False
        
    finally:
        conn.close()

if __name__ == '__main__':
    success = load_controls()
    exit(0 if success else 1)
