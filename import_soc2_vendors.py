"""Seed SOC 2 vendor management records from the ISMS manual's named vendors."""
import sys
from datetime import date

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from models import SOC2Vendor, SOC2VendorReview


VENDORS = [
    {
        'vendor_key': 'vendor-microsoft',
        'vendor_name': 'Microsoft',
        'service_description': 'Microsoft 365, Azure AD, Intune, email and device management',
        'vendor_type': 'Cloud Platform',
        'criticality': 'Critical',
        'risk_level': 'Low',
        'owner': 'chris.wren@cirque.com',
        'data_access_scope': 'Email, files, identity data, endpoint management data',
        'contract_status': 'Active',
        'assurance_status': 'SOC 2 / ISO reports available',
        'last_review_date': date(2026, 3, 15),
        'next_review_date': date(2027, 3, 15),
        'notes': 'Named in supplier relationships policy and core cloud stack.',
    },
    {
        'vendor_key': 'vendor-gitlab',
        'vendor_name': 'GitLab',
        'service_description': 'Source control and CI/CD for engineering repositories',
        'vendor_type': 'Software Platform',
        'criticality': 'High',
        'risk_level': 'Medium',
        'owner': 'chris.wren@cirque.com',
        'data_access_scope': 'Source code and development metadata',
        'contract_status': 'Active',
        'assurance_status': 'Vendor assurance under review',
        'last_review_date': date(2026, 3, 20),
        'next_review_date': date(2026, 9, 20),
        'notes': 'Explicitly named in the supplier security sections of the manual.',
    },
    {
        'vendor_key': 'vendor-cadence',
        'vendor_name': 'Cadence',
        'service_description': 'EDA tooling and licensed design software for ASIC engineering',
        'vendor_type': 'Engineering Software',
        'criticality': 'High',
        'risk_level': 'Medium',
        'owner': 'chris.wren@cirque.com',
        'data_access_scope': 'Design artifacts, licensing data, engineering workflows',
        'contract_status': 'Active',
        'assurance_status': 'Contract and licensing review on file',
        'last_review_date': date(2026, 2, 28),
        'next_review_date': date(2027, 2, 28),
        'notes': 'Supports IP-heavy engineering processes in scope for confidentiality.',
    },
    {
        'vendor_key': 'vendor-omnify',
        'vendor_name': 'Omnify',
        'service_description': 'PLM / product lifecycle support platform',
        'vendor_type': 'Business Application',
        'criticality': 'High',
        'risk_level': 'Medium',
        'owner': 'chris.wren@cirque.com',
        'data_access_scope': 'Product and operational data',
        'contract_status': 'Active',
        'assurance_status': 'Security questionnaire required',
        'last_review_date': date(2026, 3, 5),
        'next_review_date': date(2026, 9, 5),
        'notes': 'Named in supplier relationship scope examples in the manual.',
    },
    {
        'vendor_key': 'vendor-asana',
        'vendor_name': 'Asana',
        'service_description': 'Project management and workflow collaboration',
        'vendor_type': 'SaaS Collaboration',
        'criticality': 'Medium',
        'risk_level': 'Medium',
        'owner': 'chris.wren@cirque.com',
        'data_access_scope': 'Project plans and task metadata',
        'contract_status': 'Active',
        'assurance_status': 'Assurance review pending refresh',
        'last_review_date': date(2026, 1, 31),
        'next_review_date': date(2026, 7, 31),
        'notes': 'Requires periodic review because it may contain confidential project data.',
    },
    {
        'vendor_key': 'vendor-quickbooks',
        'vendor_name': 'QuickBooks',
        'service_description': 'Financial and accounting platform',
        'vendor_type': 'Finance Application',
        'criticality': 'High',
        'risk_level': 'Medium',
        'owner': 'chris.wren@cirque.com',
        'data_access_scope': 'Financial records and vendor/customer data',
        'contract_status': 'Active',
        'assurance_status': 'Vendor assurance under review',
        'last_review_date': date(2026, 3, 10),
        'next_review_date': date(2026, 9, 10),
        'notes': 'Named in the manual as an example software vendor.',
    },
]


def main():
    with app.app_context():
        try:
            for payload in VENDORS:
                vendor = SOC2Vendor.query.filter_by(vendor_key=payload['vendor_key']).first()
                created = vendor is None
                if created:
                    vendor = SOC2Vendor(vendor_key=payload['vendor_key'])
                    db.session.add(vendor)
                for field, value in payload.items():
                    setattr(vendor, field, value)
                db.session.flush()
                if created and not vendor.reviews:
                    db.session.add(SOC2VendorReview(
                        vendor_id=vendor.id,
                        review_date=payload['last_review_date'],
                        review_type='Annual Review',
                        status='Completed',
                        reviewer=payload['owner'],
                        summary='Seeded baseline vendor review from manual-aligned vendor register.',
                        findings='No critical gaps recorded in initial seed; refresh evidence references during Type 1 preparation.',
                        evidence_reference='Vendor register seed',
                    ))
            db.session.commit()
            print(f"✓ Seeded SOC 2 vendors ({SOC2Vendor.query.filter_by(is_active=True).count()} active vendors)")
            return True
        except Exception as exc:
            db.session.rollback()
            print(f"Error: {exc}")
            return False


if __name__ == '__main__':
    if not main():
        sys.exit(1)