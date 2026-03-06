#!/usr/bin/env python3
"""
Test Phase 2 automation implementation
"""
import sys
sys.path.insert(0, '/var/www/tracker')

from app import app
from evidence_file_service import EvidenceFileService

def test_generators():
    """Test all Phase 2 evidence generators"""
    with app.app_context():
        service = EvidenceFileService()
        
        tests = [
            ('Software Inventory by Asset', 'generate_software_inventory_by_asset_file', 'Software Inventory by Asset'),
            ('System Updates & Hotfixes', 'generate_system_updates_file', 'System Updates Report'),
            ('Security Baseline Compliance', 'generate_security_baseline_file', 'Security Baseline Compliance'),
            ('Key Vault Access Policies', 'generate_key_vault_policies_file', 'Key Vault Access Policies'),
            ('Network Traffic Logs', 'generate_network_traffic_logs_file', 'Network Traffic Logs'),
        ]
        
        print("\n" + "="*80)
        print("TESTING PHASE 2 AUTOMATION GENERATORS")
        print("="*80 + "\n")
        
        results = []
        for name, method_name, evidence_name in tests:
            print(f"Testing {name}...")
            try:
                method = getattr(service, method_name)
                file_path = method(evidence_name)
                if file_path:
                    print(f"  ✅ SUCCESS: {file_path}")
                    results.append((name, 'SUCCESS', file_path))
                else:
                    print(f"  ❌ FAILED: No file generated")
                    results.append((name, 'FAILED', 'No file path returned'))
            except Exception as e:
                print(f"  ❌ ERROR: {str(e)[:100]}")
                import traceback
                traceback.print_exc()
                results.append((name, 'ERROR', str(e)[:100]))
            print()
        
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80 + "\n")
        
        for name, status, detail in results:
            status_icon = "✅" if status == "SUCCESS" else "❌"
            print(f"{status_icon} {name:35s} {status:10s} {detail[:40]}")
        
        success_count = sum(1 for _, status, _ in results if status == "SUCCESS")
        print(f"\nPassed: {success_count}/{len(results)}")

if __name__ == '__main__':
    test_generators()
