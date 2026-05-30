"""Seed SOC 2 readiness items from the ISMS manual and a native SOC 2 Type 1 baseline."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from models import SOC2ReadinessItem, SOC2ReadinessUpdate


TYPE1_BASELINE_ITEMS = [
    {
        'item_key': 'type1-management-review-record',
        'title': 'Record executive management review for the Type 1 audit window',
        'domain': 'Governance',
        'audit_alignment': 'CC1.2, CC2.1, CC4.1',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Quarterly',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L12',
        'next_step': 'Capture Q2 2026 management review agenda, attendees, decisions, and follow-up actions.',
        'notes': 'Type 1 requires point-in-time governance evidence, not just policy language.',
    },
    {
        'item_key': 'type1-org-chart-and-role-definition',
        'title': 'Assemble org chart, role definitions, and segregation of duties evidence',
        'domain': 'Governance',
        'audit_alignment': 'CC1.3, CC1.4',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'brenda.milian@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L152',
        'next_step': 'Publish current org chart and role/accountability evidence for in-scope functions.',
        'notes': 'Needed to support governance and personnel control design assertions.',
    },
    {
        'item_key': 'type1-security-training-evidence',
        'title': 'Collect annual security awareness and policy acknowledgment evidence',
        'domain': 'Training',
        'audit_alignment': 'CC2.2',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'brenda.milian@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L96',
        'next_step': 'Load attendance records, policy acknowledgements, and phishing/training completion evidence.',
        'notes': 'The manual states the requirement; the audit package still needs the actual records.',
    },
    {
        'item_key': 'type1-system-description-package',
        'title': 'Build current system description, network diagram, and data flow package',
        'domain': 'System Description',
        'audit_alignment': 'CC2.1, CC2.2, C1.1',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L142',
        'next_step': 'Generate the point-in-time narrative and diagrams for in-scope systems and confidential data flows.',
        'notes': 'Type 1 needs current design documentation that aligns with the manual scope.',
    },
    {
        'item_key': 'type1-risk-register-and-treatment',
        'title': 'Finalize risk register and treatment-plan evidence for in-scope risks',
        'domain': 'Risk Management',
        'audit_alignment': 'CC3.1, CC3.2, CC3.3',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L92',
        'next_step': 'Tie current risk treatments and residual risk decisions to the Q2 2026 audit package.',
        'notes': 'Design assertions need live risk artifacts, not only policy statements.',
    },
    {
        'item_key': 'type1-vendor-due-diligence',
        'title': 'Document vendor register, due diligence, and third-party security review evidence',
        'domain': 'Vendor Management',
        'audit_alignment': 'CC3.4, CC9.2, C1.2',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L102',
        'next_step': 'Load critical vendors, due diligence outcomes, contract controls, and current assurance reports.',
        'notes': 'Confidentiality scope depends on supplier oversight, especially cloud and IT vendors.',
    },
    {
        'item_key': 'type1-user-access-review-package',
        'title': 'Package user access review and privileged access review evidence',
        'domain': 'Access Control',
        'audit_alignment': 'CC5.2, CC6.2, CC6.3',
        'priority': 'P1-Critical',
        'status': 'Partially In Place',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L66',
        'next_step': 'Generate reviewer-approved access review outputs and sign-off records for the audit window.',
        'notes': 'Tracker has supporting data, but the auditor-facing review package still needs to be assembled.',
    },
    {
        'item_key': 'type1-provisioning-and-termination-evidence',
        'title': 'Assemble provisioning and termination-of-access evidence',
        'domain': 'Access Control',
        'audit_alignment': 'CC6.1, CC6.2, CC6.3, CC6.4',
        'priority': 'P1-Critical',
        'status': 'Partially In Place',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L96',
        'next_step': 'Capture onboarding/offboarding approvals, account actions, and termination review reports.',
        'notes': 'Design appears documented; evidence packaging remains incomplete.',
    },
    {
        'item_key': 'type1-asset-and-endpoint-evidence',
        'title': 'Publish point-in-time asset inventory, device compliance, and software inventory evidence',
        'domain': 'Asset Management',
        'audit_alignment': 'CC2.1, CC6.1, C1.1',
        'priority': 'P2-High',
        'status': 'Partially In Place',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Weekly',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L169',
        'next_step': 'Export the in-scope endpoint, asset, and software package for the audit snapshot date.',
        'notes': 'Tracker already has the core data; the Type 1 deliverable is the curated evidence pack.',
    },
    {
        'item_key': 'type1-change-management-evidence',
        'title': 'Capture change-management approvals, emergency changes, and separation-of-duties evidence',
        'domain': 'Change Management',
        'audit_alignment': 'CC5.2, CC8.1',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L76',
        'next_step': 'Produce current change records, approvals, and evidence of production change controls.',
        'notes': 'Still a common audit gap even when policy language exists.',
    },
    {
        'item_key': 'type1-incident-response-test',
        'title': 'Document incident response process evidence and at least one exercise or test',
        'domain': 'Incident Response',
        'audit_alignment': 'CC7.3, CC7.4, CC7.5',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L98',
        'next_step': 'Attach incident log structure, escalation path, and tabletop or test results for the audit package.',
        'notes': 'Type 1 needs design and readiness evidence, not only a written procedure.',
    },
    {
        'item_key': 'type1-confidentiality-classification-and-retention',
        'title': 'Evidence confidentiality classification, handling, retention, and disposal controls',
        'domain': 'Confidentiality',
        'audit_alignment': 'C1.1, C1.2',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L70',
        'next_step': 'Map confidential information classes to storage, transmission, retention, and disposal evidence.',
        'notes': 'This is the core additional scope beyond Security for this audit cycle.',
    },
    {
        'item_key': 'type1-internal-audit-execution',
        'title': 'Execute internal audit and track findings through closure planning',
        'domain': 'Internal Audit',
        'audit_alignment': 'CC4.1, CC4.2',
        'priority': 'P1-Critical',
        'status': 'Partially In Place',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L10727',
        'next_step': 'Use the new internal audit module to record scope, findings, and remediation linkage for Q2 2026.',
        'notes': 'Tracker support now exists; the audit record still needs to be populated.',
    },
    {
        'item_key': 'type1-audit-evidence-pack',
        'title': 'Assemble a point-in-time SOC 2 Type 1 evidence pack for the audit window',
        'domain': 'Audit Packaging',
        'audit_alignment': 'CC2.3, CC4.2',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'soc2_type1',
        'source_reference': 'SOC 2 Type 1 baseline',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L21',
        'next_step': 'Package the final auditor-facing set of reports, exports, approvals, and referenced documents for the defined audit window.',
        'notes': 'This is the consolidation step that replaces the old StrikeGraph upload mindset.',
    },
]

MANUAL_ITEMS = [
    {
        'item_key': 'manual-a61-screening-section',
        'title': 'Add HR screening section and supporting control narrative',
        'domain': 'People Controls',
        'audit_alignment': 'CC1.4',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'brenda.milian@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'manual',
        'source_reference': 'ISMS Manual Statement of Applicability',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L3470',
        'next_step': 'Publish the HR screening section referenced by A.6.1 and attach evidence expectations.',
        'notes': 'Explicitly called out in the manual as still to be added per Major Finding M-21.',
    },
    {
        'item_key': 'manual-threat-model-soa-row',
        'title': 'Update SoA to reflect secure development threat-modeling coverage',
        'domain': 'Secure Development',
        'audit_alignment': 'CC5.3, CC8.1, CC9.1',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'manual',
        'source_reference': 'CAR-2026-001',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L12320',
        'next_step': 'Add the threat-modeling row and final cross-reference in the SoA.',
        'notes': 'Manual CAR action 4 remains open.',
    },
    {
        'item_key': 'manual-threat-model-kpi',
        'title': 'Add threat-model coverage KPI to ISMS objectives',
        'domain': 'Monitoring',
        'audit_alignment': 'CC4.1, CC5.2',
        'priority': 'P2-High',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Quarterly',
        'source_type': 'manual',
        'source_reference': 'CAR-2026-001',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L12320',
        'next_step': 'Define KPI calculation and start measuring active engineering projects with approved threat models.',
        'notes': 'Manual CAR action 5 remains open.',
    },
    {
        'item_key': 'manual-threat-model-template',
        'title': 'Publish standard threat-model template for engineering repos',
        'domain': 'Secure Development',
        'audit_alignment': 'CC5.3, CC8.1',
        'priority': 'P2-High',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'manual',
        'source_reference': 'CAR-2026-001',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L12320',
        'next_step': 'Create THREAT-MODEL.md template and store repository location as evidence.',
        'notes': 'Manual CAR action 6 remains open.',
    },
    {
        'item_key': 'manual-threat-model-training',
        'title': 'Complete STRIDE training for development leads',
        'domain': 'Training',
        'audit_alignment': 'CC2.2',
        'priority': 'P2-High',
        'status': 'Open',
        'owner': 'brenda.milian@cirque.com',
        'frequency': 'As Needed',
        'source_type': 'manual',
        'source_reference': 'CAR-2026-001',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L12320',
        'next_step': 'Deliver training session and file attendance evidence.',
        'notes': 'Manual CAR action 7 remains open.',
    },
    {
        'item_key': 'manual-threat-model-audit-scope',
        'title': 'Add SDLC threat modeling to internal audit scope',
        'domain': 'Internal Audit',
        'audit_alignment': 'CC4.1, CC4.2',
        'priority': 'P1-Critical',
        'status': 'Open',
        'owner': 'chris.wren@cirque.com',
        'frequency': 'Annually',
        'source_type': 'manual',
        'source_reference': 'CAR-2026-001',
        'manual_reference': 'content/isms/incoming/ISMS-Manual.md#L12320',
        'next_step': 'Update the 2026 audit plan and sample SDLC artifacts during the audit window.',
        'notes': 'Manual CAR action 8 remains open.',
    },
]


def _upsert_item(payload):
    item = SOC2ReadinessItem.query.filter_by(item_key=payload['item_key']).first()
    created = item is None
    if created:
        item = SOC2ReadinessItem(item_key=payload['item_key'])
        db.session.add(item)

    for field, value in payload.items():
        setattr(item, field, value)

    if created:
        db.session.flush()
        db.session.add(SOC2ReadinessUpdate(
            readiness_item_id=item.id,
            update_type='seed',
            previous_status=None,
            new_status=item.status,
            note='Initial readiness item import',
            created_by='system-import',
        ))


def archive_legacy_gap_items():
    legacy_items = SOC2ReadinessItem.query.filter_by(source_type='gap_matrix', is_active=True).all()
    for item in legacy_items:
        item.is_active = False
        item.notes = ((item.notes or '').strip() + ' Archived during native SOC 2 Type 1 reseed.').strip()


def import_type1_baseline_items():
    for payload in TYPE1_BASELINE_ITEMS:
        _upsert_item(payload)


def import_manual_items():
    for payload in MANUAL_ITEMS:
        _upsert_item(payload)


def main():
    with app.app_context():
        try:
            archive_legacy_gap_items()
            import_type1_baseline_items()
            import_manual_items()
            db.session.commit()
            active_total = SOC2ReadinessItem.query.filter_by(is_active=True).count()
            print(f'✓ Seeded native SOC 2 Type 1 readiness items ({active_total} active items)')
            return True
        except Exception as exc:
            db.session.rollback()
            print(f'Error: {exc}')
            return False


if __name__ == '__main__':
    if not main():
        sys.exit(1)