#!/usr/bin/env python3
"""
Test script to generate a sample evidence file
"""

import sys
import os
sys.path.insert(0, '/var/www/tracker')

from app import app, db
from evidence_file_service import EvidenceFileService

def test_generation():
    """Test evidence file generation"""
    with app.app_context():
        service = EvidenceFileService()
        
        print("Testing evidence file generation...")
        print("=" * 60)
        
        # Test M365 Users file
        print("\n1. Testing M365 Users export...")
        try:
            file_path = service.generate_m365_users_file('Application User List')
            if file_path and os.path.exists(file_path):
                size = os.path.getsize(file_path)
                print(f"   ✓ Generated: {file_path}")
                print(f"   ✓ Size: {size} bytes")
            else:
                print(f"   ✗ File not created")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        # Test Admin Users file
        print("\n2. Testing Admin Users export...")
        try:
            file_path = service.generate_admin_users_file('Administrator Access to Application')
            if file_path and os.path.exists(file_path):
                size = os.path.getsize(file_path)
                print(f"   ✓ Generated: {file_path}")
                print(f"   ✓ Size: {size} bytes")
            else:
                print(f"   ✗ File not created")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        # Test Intune Devices file
        print("\n3. Testing Intune Devices export...")
        try:
            file_path = service.generate_intune_devices_file('Workstation Asset Inventory')
            if file_path and os.path.exists(file_path):
                size = os.path.getsize(file_path)
                print(f"   ✓ Generated: {file_path}")
                print(f"   ✓ Size: {size} bytes")
            else:
                print(f"   ✗ File not created")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        print("\n" + "=" * 60)
        print("Test complete!")
        
        # List all generated files
        print("\nGenerated files:")
        evidence_dir = '/var/www/tracker/static/evidence'
        for subdir in ['m365', 'azure', 'isms', 'manual']:
            dir_path = f'{evidence_dir}/{subdir}'
            if os.path.exists(dir_path):
                files = os.listdir(dir_path)
                if files:
                    print(f"\n{subdir}/:")
                    for f in files:
                        full_path = os.path.join(dir_path, f)
                        size = os.path.getsize(full_path)
                        print(f"  - {f} ({size} bytes)")

if __name__ == '__main__':
    test_generation()
