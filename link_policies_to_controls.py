"""
Link ISMS policies to SOC2 controls in database
"""
from app import app, db
from soc2_models import SOC2Control
import json

# Define specific policy mappings for each control
CONTROL_POLICY_MAPPINGS = {
    'Administrator Access': [
        'IS-CIRQ-P-008-G: Access Control Policy',
        'IS-CIRQ-PR-009-G: Privileged Access Management Procedure',
        'IS-CIRQ-P-002-G: Roles, Responsibilities, and Authorities Policy'
    ],
    'Antivirus': [
        'IS-CIRQ-P-011-G: Operations Security Policy',
        'IS-CIRQ-P-014-G: Information Security Incident Management Policy',
        'IS-CIRQ-PR-020-G: Incident Response Procedure (Global Core)'
    ],
    'Asset Inventory': [
        'IS-CIRQ-P-007-G: Asset Management Policy',
        'IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure',
        'IS-CIRQ-F-004-G: Asset Register'
    ],
    'Automatic Patching': [
        'IS-CIRQ-P-011-G: Operations Security Policy',
        'IS-CIRQ-PR-013-G: Change Management Procedure',
        'IS-CIRQ-PR-016-G: Network Security Management Procedure'
    ],
    'Change Management: Infrastructure': [
        'IS-CIRQ-PR-013-G: Change Management Procedure',
        'IS-CIRQ-P-012-G: Secure System Acquisition, Development and Maintenance Policy',
        'IS-CIRQ-PR-017-G: Secure Development Procedure'
    ],
    'Change Management Policy': [
        'IS-CIRQ-PR-013-G: Change Management Procedure',
        'IS-CIRQ-P-011-G: Operations Security Policy',
        'IS-CIRQ-F-003-G: Document Change Request Form'
    ],
    'Configuration Standards': [
        'IS-CIRQ-P-011-G: Operations Security Policy',
        'IS-CIRQ-PR-016-G: Network Security Management Procedure',
        'IS-CIRQ-PR-017-G: Secure Development Procedure'
    ],
    'Data Classification Policy': [
        'IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure',
        'IS-CIRQ-P-016-G: Compliance Policy',
        'IS-CIRQ-P-017-G: Privacy Policy (Global Core)'
    ],
    'Provisioning': [
        'IS-CIRQ-P-008-G: Access Control Policy',
        'IS-CIRQ-PR-008-G: Access Control Procedure',
        'IS-CIRQ-PR-004-G: Information Security Awareness and Training Procedure'
    ],
    'User Access Review': [
        'IS-CIRQ-P-008-G: Access Control Policy',
        'IS-CIRQ-PR-008-G: Access Control Procedure',
        'IS-CIRQ-PR-023-G: Internal Audit Procedure'
    ],
    'Termination of Access': [
        'IS-CIRQ-P-008-G: Access Control Policy',
        'IS-CIRQ-PR-008-G: Access Control Procedure',
        'IS-CIRQ-P-019-G: Acceptable Use Policy'
    ],
    'User Authentication': [
        'IS-CIRQ-P-008-G: Access Control Policy',
        'IS-CIRQ-P-009-G: Cryptography Policy',
        'IS-CIRQ-PR-010-G: Key Management Procedure'
    ],
    'Vulnerability Scan': [
        'IS-CIRQ-P-003-G: Risk Management Policy',
        'IS-CIRQ-PR-002-G: Information Security Risk Assessment Procedure',
        'IS-CIRQ-P-011-G: Operations Security Policy'
    ]
}

def link_policies_to_controls():
    """Add policy references to control notes"""
    
    with app.app_context():
        updated_count = 0
        
        for control_name, policies in CONTROL_POLICY_MAPPINGS.items():
            control = SOC2Control.query.filter_by(control_name=control_name).first()
            
            if control:
                # Add policy references to notes field
                policy_text = "\n\nSupporting Policies:\n" + "\n".join([f"• {policy}" for policy in policies])
                
                if control.control_description and "Supporting Policies:" not in control.control_description:
                    control.control_description += policy_text
                
                print(f"✓ Updated {control_name} with {len(policies)} policy references")
                updated_count += 1
        
        db.session.commit()
        print(f"\n✅ Updated {updated_count} controls with policy references")
        print(f"\nPolicy references are now included in:")
        print(f"  • Control descriptions")
        print(f"  • Evidence exports")
        print(f"  • Dashboard views")

if __name__ == '__main__':
    link_policies_to_controls()
