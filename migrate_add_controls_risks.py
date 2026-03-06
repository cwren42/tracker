#!/usr/bin/env python3
"""
Migration script to add SOC2 controls and risks tables
"""
import sqlite3
import os

def migrate():
    db_path = '/var/www/tracker/assets.db'
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Create control table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS control (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                control_name TEXT NOT NULL UNIQUE,
                control_description TEXT,
                control_frequency TEXT,
                control_owner TEXT,
                control_progress TEXT,
                is_active BOOLEAN DEFAULT 1,
                audit_alignment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create risk table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                risk_name TEXT NOT NULL UNIQUE,
                risk_description TEXT,
                risk_treatment TEXT,
                risk_progress TEXT,
                risk_category TEXT,
                risk_status BOOLEAN DEFAULT 1,
                risk_impact TEXT,
                risk_likelihood TEXT,
                risk_combined_score TEXT,
                risk_owner TEXT,
                active_controls TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create control_risk_mapping table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS control_risk_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                control_id INTEGER NOT NULL,
                risk_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (control_id) REFERENCES control(id) ON DELETE CASCADE,
                FOREIGN KEY (risk_id) REFERENCES risk(id) ON DELETE CASCADE,
                UNIQUE(control_id, risk_id)
            )
        """)
        
        conn.commit()
        print("✓ Created controls and risks tables")
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        conn.rollback()
        return False
        
    finally:
        conn.close()

if __name__ == '__main__':
    success = migrate()
    exit(0 if success else 1)
