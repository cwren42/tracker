#!/usr/bin/env python3
"""
Test Phase 1 automation implementation
"""
import sys
sys.path.insert(0, '/var/www/tracker')

from app import app
from evidence_file_service import EvidenceFileService

def test_generators():
    """Test all Phase 1 evidence generators"""
    with app.app_context():
        service = EvidenceFileService()
        
        tests = [
            ('MFA Status', 'generate_mfa_status_file', 'MFA Status Report'),
            ('Security Incidents', 'generate_security_incidents_file', 'Security Incident Report'),
            ('Security Alerts', 'generate_security_alerts_file', 'Security Alert History'),
            ('Azure RBAC', 'generate_azure_rbac_file', 'Azure RBAC Report'),
            ('Conditional Access', 'generate_conditional_access_file', 'Conditional Access Policies'),
        ]
        
        print("\\n" + "="*80)
        print("TESTING PHASE 1 AUTOMATION GENERATORS")
        print("="*80 + "\\n")
        
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
                results.append((name, 'ERROR', str(e)[:100]))
            print()
        
        print("\\n" + "="*80)
        print("SUMMARY")
        print("="*80 + "\\n")
        
        for name, status, detail in results:
            status_icon = "✅" if status == "SUCCESS" else "❌"
            print(f"{status_icon} {name:25s} {status:10s} {detail[:50]}")
        
        success_count = sum(1 for _, status, _ in results if status == "SUCCESS")
        print(f"\\nPassed: {success_count}/{len(results)}")

if __name__ == '__main__':
    test_generators()
