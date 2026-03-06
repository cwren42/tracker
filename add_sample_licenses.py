from app import app, db, License
from datetime import datetime, timedelta

with app.app_context():
    # Check if licenses already exist
    existing = License.query.count()
    if existing > 0:
        print(f"Already have {existing} licenses in database. Skipping sample data.")
    else:
        # Add sample software licenses
        licenses = [
            License(
                software_name='Microsoft Visual Studio Professional',
                vendor='Microsoft',
                license_type='Per User',
                total_licenses=10,
                purchase_date=datetime(2024, 1, 15).date(),
                expiry_date=datetime(2026, 1, 15).date(),
                purchase_cost=5990.00,
                annual_cost=0,
                status='Active',
                notes='MSDN subscription included'
            ),
            License(
                software_name='SolidWorks Premium',
                vendor='Dassault Systèmes',
                license_type='Per User',
                total_licenses=5,
                purchase_date=datetime(2024, 3, 1).date(),
                expiry_date=datetime(2025, 3, 1).date(),
                renewal_date=datetime(2025, 2, 15).date(),
                purchase_cost=12500.00,
                annual_cost=2500.00,
                status='Active',
                notes='Includes simulation and rendering packages'
            ),
            License(
                software_name='Adobe Creative Cloud',
                vendor='Adobe',
                license_type='Subscription',
                total_licenses=15,
                purchase_date=datetime(2024, 6, 1).date(),
                expiry_date=datetime(2025, 6, 1).date(),
                renewal_date=datetime(2025, 5, 15).date(),
                annual_cost=7980.00,
                status='Active',
                notes='All apps subscription for design team'
            ),
            License(
                software_name='AutoCAD',
                vendor='Autodesk',
                license_type='Per User',
                total_licenses=8,
                purchase_date=datetime(2023, 9, 1).date(),
                expiry_date=datetime(2025, 9, 1).date(),
                renewal_date=datetime(2025, 8, 15).date(),
                purchase_cost=16000.00,
                annual_cost=4000.00,
                status='Active',
                notes='Standard subscription'
            ),
            License(
                software_name='Microsoft Office 365 E3',
                vendor='Microsoft',
                license_type='Per User',
                total_licenses=50,
                purchase_date=datetime(2024, 1, 1).date(),
                expiry_date=datetime(2025, 12, 31).date(),
                renewal_date=datetime(2025, 12, 1).date(),
                annual_cost=12000.00,
                status='Active',
                notes='Enterprise subscription for all staff'
            ),
            License(
                software_name='Slack Business+',
                vendor='Slack',
                license_type='Per User',
                total_licenses=100,
                purchase_date=datetime(2024, 4, 1).date(),
                expiry_date=datetime(2025, 4, 1).date(),
                renewal_date=datetime(2025, 3, 15).date(),
                annual_cost=1500.00,
                status='Active',
                notes='Company-wide communication'
            ),
            License(
                software_name='Atlassian Jira Software',
                vendor='Atlassian',
                license_type='Site License',
                total_licenses=100,
                purchase_date=datetime(2023, 11, 1).date(),
                expiry_date=datetime(2024, 11, 1).date(),
                purchase_cost=3500.00,
                status='Expired',
                notes='Project management - needs renewal'
            ),
            License(
                software_name='GitHub Enterprise',
                vendor='GitHub',
                license_type='Per User',
                total_licenses=25,
                purchase_date=datetime(2024, 2, 1).date(),
                expiry_date=datetime(2025, 2, 1).date(),
                renewal_date=datetime(2025, 1, 15).date(),
                annual_cost=5250.00,
                status='Active',
                notes='Development team subscription'
            )
        ]
        
        for license in licenses:
            db.session.add(license)
        
        db.session.commit()
        print(f"✓ Added {len(licenses)} sample software licenses!")
        
        # Show what was added
        print("\nSample licenses:")
        for lic in licenses:
            print(f"  - {lic.software_name} ({lic.license_type}) - {lic.total_licenses} licenses")

