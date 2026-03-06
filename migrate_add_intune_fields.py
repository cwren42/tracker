#!/usr/bin/env python3
"""
Migration script to add Intune-specific fields to Asset table
"""

from app import app, db
from sqlalchemy import text

def migrate():
    with app.app_context():
        print("Adding Intune fields to Asset table...")
        
        # List of new columns to add
        migrations = [
            ("intune_device_id", "VARCHAR(100)"),
            ("intune_enrolled_date", "DATETIME"),
            ("intune_last_sync", "DATETIME"),
            ("intune_compliance_state", "VARCHAR(50)"),
            ("intune_management_state", "VARCHAR(50)"),
            ("intune_os_version", "VARCHAR(100)"),
        ]
        
        for column_name, column_type in migrations:
            try:
                # Check if column exists
                result = db.session.execute(text(
                    f"SELECT COUNT(*) FROM pragma_table_info('asset') WHERE name='{column_name}'"
                ))
                exists = result.scalar() > 0
                
                if not exists:
                    print(f"  Adding column: {column_name} ({column_type})...")
                    db.session.execute(text(
                        f"ALTER TABLE asset ADD COLUMN {column_name} {column_type}"
                    ))
                    db.session.commit()
                    print(f"  ✓ Added {column_name}")
                else:
                    print(f"  - Column {column_name} already exists, skipping")
                    
            except Exception as e:
                print(f"  ✗ Error adding {column_name}: {str(e)}")
                db.session.rollback()
        
        print("\n✅ Migration completed!")
        print("\nNew Intune fields added:")
        print("  • intune_device_id - Microsoft Intune device identifier")
        print("  • intune_enrolled_date - Date device was enrolled in Intune")
        print("  • intune_last_sync - Last sync time with Intune")
        print("  • intune_compliance_state - Device compliance status")
        print("  • intune_management_state - Device management state")
        print("  • intune_os_version - Full OS version from Intune")

if __name__ == '__main__':
    migrate()
