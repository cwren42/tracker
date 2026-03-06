#!/usr/bin/env python3
"""
Migration script to add hardware fields to Asset table
"""

from app import app, db
from sqlalchemy import text

def migrate():
    with app.app_context():
        print("Adding hardware fields to Asset table...")
        
        # List of new columns to add
        migrations = [
            ("hardware_cpu", "VARCHAR(100)"),
            ("hardware_ram_gb", "REAL"),
            ("hardware_storage_total_gb", "REAL"),
            ("hardware_storage_free_gb", "REAL"),
            ("hardware_bios_version", "VARCHAR(100)"),
            ("hardware_mac_wifi", "VARCHAR(50)"),
            ("hardware_mac_ethernet", "VARCHAR(50)"),
            ("hardware_tpm_version", "VARCHAR(50)"),
            ("azure_ad_device_id", "VARCHAR(100)"),
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
        print("\nNew hardware fields added:")
        print("  • hardware_cpu - CPU/Processor architecture")
        print("  • hardware_ram_gb - Physical memory in GB")
        print("  • hardware_storage_total_gb - Total storage in GB")
        print("  • hardware_storage_free_gb - Free storage in GB")
        print("  • hardware_bios_version - BIOS/Firmware version")
        print("  • hardware_mac_wifi - WiFi MAC address")
        print("  • hardware_mac_ethernet - Ethernet MAC address")
        print("  • hardware_tpm_version - TPM (Trusted Platform Module) version")
        print("  • azure_ad_device_id - Azure Active Directory device ID")

if __name__ == '__main__':
    migrate()
