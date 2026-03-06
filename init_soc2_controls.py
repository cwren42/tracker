"""
Initialize SOC2 controls from StrikeGraph
Run this once to populate the controls table
"""
from app import app, db
from soc2_models import SOC2Control
from datetime import datetime


def init_controls():
    """Initialize SOC2 controls in database"""
    
    controls_data = [
        {
            'name': 'Administrator Access',
            'description': 'Internal administrator access to the application, database, network, VPN, and operating system is restricted to authorized users.',
            'frequency': 'Daily',
            'owner': 'chris.wren@cirque.com',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.6.2, SOC2:2022.CC.6.3',
            'automation': True
        },
        {
            'name': 'Antivirus',
            'description': 'Antivirus is installed on all workstations and servers to help protect against viruses and malicious software on the systems.',
            'frequency': 'Daily',
            'owner': 'chris.wren@cirque.com',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.6.7, SOC2:2022.CC.6.8',
            'automation': True
        },
        {
            'name': 'Asset Inventory',
            'description': 'An inventory of information assets, including hardware and software, is maintained and updated at least annually. All assets have an assigned asset owner. All assets are classified based on the data classification convention.',
            'frequency': 'Weekly',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.C.1.1, SOC2:2022.CC.2.1, SOC2:2022.CC.6.1',
            'automation': True
        },
        {
            'name': 'Automatic Patching',
            'description': 'Servers are configured to automatically install critical security patches.',
            'frequency': 'Continuous',
            'owner': 'chris.wren@cirque.com',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.8.1',
            'automation': False
        },
        {
            'name': 'Change Management: Infrastructure',
            'description': 'Infrastructure changes are tested, reviewed, and approved by authorized personnel prior to implementation.',
            'frequency': 'As Needed',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.A.1.1, SOC2:2022.CC.8.1',
            'automation': False
        },
        {
            'name': 'Change Management Policy',
            'description': 'A Change Management Policy and Procedures are in place to request, document, test, and approve changes. The head of IT is responsible for ensuring that changes to IT services are made in a manner appropriate to their impact on operations.',
            'frequency': 'Annually',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.5.2, SOC2:2022.CC.6.1, SOC2:2022.CC.6.8, SOC2:2022.CC.8.1',
            'automation': False
        },
        {
            'name': 'Configuration Standards',
            'description': 'A baseline security configuration is maintained by the information technology team and is deployed to all systems upon installation or upgrade.',
            'frequency': 'Annually',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.7.1, SOC2:2022.CC.8.1',
            'automation': False
        },
        {
            'name': 'Data Classification Policy',
            'description': 'A defined information classification scheme has been established to label and handle data. This policy is reviewed, updated, and approved annually.',
            'frequency': 'Annually',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.C.1.1, SOC2:2022.CC.2.1, SOC2:2022.CC.6.1',
            'automation': False
        },
        {
            'name': 'Provisioning',
            'description': 'Logical/physical user access requests are documented and require approval prior to access being provisioned.',
            'frequency': 'As Needed',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.6.1, SOC2:2022.CC.6.2, SOC2:2022.CC.6.3, SOC2:2022.CC.6.4',
            'automation': True
        },
        {
            'name': 'User Access Review',
            'description': 'Management performs at least an annual review of user access to systems based on job duties. Inactive users are removed and removal is documented.',
            'frequency': 'Annually',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.5.2, SOC2:2022.CC.6.2, SOC2:2022.CC.6.3',
            'automation': True
        },
        {
            'name': 'Termination of Access',
            'description': "A user's logical [and physical] access to IT systems is revoked within [# hours or business days] of termination or transfer and all assets are returned to the organization.",
            'frequency': 'As Needed',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.6.2, SOC2:2022.CC.6.3, SOC2:2022.CC.6.4',
            'automation': True
        },
        {
            'name': 'User Authentication',
            'description': 'Unique usernames and passwords are required to authenticate all users.',
            'frequency': 'As Needed',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.6.1',
            'automation': True
        },
        {
            'name': 'Vulnerability Scan',
            'description': 'Vulnerability scans are performed quarterly to help identify security risks. Results are assessed and, where required, remediated.',
            'frequency': 'Quarterly',
            'owner': 'chris.wren@cirque.com',
            'progress': 'Not In Place',
            'alignment': 'SOC2:2022.CC.4.1, SOC2:2022.CC.6.8, SOC2:2022.CC.7.1, SOC2:2022.CC.7.2',
            'automation': False
        }
    ]
    
    with app.app_context():
        for control_data in controls_data:
            # Check if control already exists
            existing = SOC2Control.query.filter_by(control_name=control_data['name']).first()
            
            if not existing:
                control = SOC2Control(
                    control_name=control_data['name'],
                    control_description=control_data['description'],
                    control_frequency=control_data['frequency'],
                    control_owner=control_data['owner'],
                    control_progress=control_data['progress'],
                    is_active=True,
                    audit_alignment=control_data['alignment'],
                    automation_enabled=control_data['automation']
                )
                db.session.add(control)
                print(f"✓ Added control: {control_data['name']}")
            else:
                print(f"- Control already exists: {control_data['name']}")
        
        db.session.commit()
        print(f"\n✅ Initialized {len(controls_data)} SOC2 controls")


if __name__ == '__main__':
    init_controls()
