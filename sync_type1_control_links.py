"""Link refreshed Type 1 controls to evidence items and readiness items."""
import re
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from models import SOC2ReadinessItem, SOC2ReadinessUpdate
from soc2_models import SOC2Control, StrikeGraphEvidence, sync_control_automation_flags, sync_control_progress_flags


CONTROL_EVIDENCE_MAP = {
    'Board Oversight OR Management Oversight': [
        {'name': 'Management Review Minutes', 'type': 'General', 'source': 'Tracker', 'owner': 'Executive Management', 'description': 'Executive management review records for the Type 1 audit window.'},
    ],
    'Job Descriptions': [
        {'name': 'Employee Job Descriptions', 'type': 'General', 'source': 'Manual', 'owner': 'Human Resources', 'description': 'Current role definitions and accountability assignments for in-scope personnel.'},
        {'name': 'Organizational Chart', 'type': 'General', 'source': 'Manual', 'owner': 'Human Resources', 'description': 'Current organizational chart for management oversight and segregation of duties.'},
    ],
    'Background Check': [
        {'name': 'Employee Screening', 'type': 'Sample', 'source': 'Manual', 'owner': 'Human Resources', 'description': 'Background screening evidence for in-scope employees.'},
    ],
    'Non Disclosure Agreement': [
        {'name': 'Signed Non Disclosure Agreement - Employee', 'type': 'Sample', 'source': 'Manual', 'owner': 'Human Resources', 'description': 'Executed confidentiality agreements for employees.'},
    ],
    'Security Awareness Training': [
        {'name': 'Annual Employee Training', 'type': 'Population', 'source': 'Tracker', 'owner': 'IT Manager', 'description': 'Quarterly and annual security training completion records from Tracker.'},
        {'name': 'Training Materials', 'type': 'General', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Security awareness and incident reporting training materials.'},
    ],
    'Logical Access Policy': [
        {'name': 'Logical Access Policy and Procedures', 'type': 'Policy', 'source': 'ISMS', 'owner': 'IT Manager', 'description': 'Logical and physical access policy and procedure set.'},
    ],
    'Administrator Access': [
        {'name': 'Administrator Access to Network/Cloud', 'type': 'Population', 'source': 'M365/Defender', 'owner': 'IT Manager', 'description': 'Privileged access inventory for network, cloud, and administrative systems.'},
    ],
    'Provisioning': [
        {'name': 'Access Request - New Hire', 'type': 'Sample', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'New hire provisioning approvals and evidence.'},
        {'name': 'Access Request - Current Employee', 'type': 'Sample', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Role change and access modification approvals.'},
    ],
    'Termination of Access': [
        {'name': 'Access Termination Ticket', 'type': 'Sample', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Termination and offboarding access removal evidence.'},
        {'name': 'Access Control Policy', 'type': 'Policy', 'source': 'ISMS', 'owner': 'IT Manager', 'description': 'Access lifecycle, revocation, and offboarding policy requirements.'},
    ],
    'User Access Review': [
        {'name': 'Periodic Logical Access Review', 'type': 'General', 'source': 'Tracker', 'owner': 'IT Manager', 'description': 'Periodic review of user and privileged access.'},
    ],
    'Data Classification Policy': [
        {'name': 'Data Classification Policy', 'type': 'Policy', 'source': 'ISMS', 'owner': 'IT Manager', 'description': 'Classification and handling requirements for confidential information.'},
    ],
    'Data Retention/Deletion': [
        {'name': 'Records Retention Schedule', 'type': 'Policy', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Retention and deletion schedule for records and project data.'},
        {'name': 'Data Deletion', 'type': 'Sample', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Deletion and disposal evidence for archived data.'},
    ],
    'Encryption at Rest': [
        {'name': 'Database Encryption', 'type': 'Settings', 'source': 'Azure', 'owner': 'IT Manager', 'description': 'Azure SQL database encryption evidence.'},
        {'name': 'Server Encryption', 'type': 'Settings', 'source': 'Azure', 'owner': 'IT Manager', 'description': 'Server-side encryption evidence for in-scope systems.'},
    ],
    'Encryption in Transit': [
        {'name': 'Encryption in Transit', 'type': 'Settings', 'source': 'Azure', 'owner': 'IT Manager', 'description': 'TLS and secure transport settings evidence.'},
    ],
    'Asset Inventory': [
        {'name': 'Asset Inventory', 'type': 'Population', 'source': 'M365/Intune', 'owner': 'IT Manager', 'description': 'In-scope endpoint and asset inventory.'},
    ],
    'Antivirus': [
        {'name': 'Antivirus Configuration - Workstation', 'type': 'Settings', 'source': 'M365/Defender', 'owner': 'IT Manager', 'description': 'Defender and endpoint protection status for workstations.'},
    ],
    'Change Management Policy': [
        {'name': 'Change Management Policy', 'type': 'Policy', 'source': 'ISMS', 'owner': 'Quality Director', 'description': 'Approved change management policy and procedures.'},
        {'name': 'Change Management Tool', 'type': 'General', 'source': 'Manual', 'owner': 'Quality Director', 'description': 'Asana and repository workflow evidence supporting change control.'},
    ],
    'Business Continuity': [
        {'name': 'Business Continuity Plan', 'type': 'Policy', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Business continuity and recovery planning documentation.'},
        {'name': 'Business Continuity and Disaster Recovery Procedure', 'type': 'Policy', 'source': 'ISMS', 'owner': 'IT Manager', 'description': 'Backup, restoration, and recovery procedure for design data and supporting systems.'},
    ],
    'Incident Response: Process': [
        {'name': 'Incident Response Plan', 'type': 'Policy', 'source': 'ISMS', 'owner': 'IT Manager', 'description': 'Formal incident response plan aligned to the updated system description.'},
        {'name': 'Security Incident Resolution', 'type': 'Sample', 'source': 'Tracker', 'owner': 'IT Manager', 'description': 'Incident records, timelines, RCA, and corrective actions from Tracker.'},
    ],
    'Incident Response: Testing': [
        {'name': 'Incident Response Tabletop Test', 'type': 'General', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'Tabletop exercise and incident response test results.'},
    ],
    'Contracts': [
        {'name': 'Vendor Contract', 'type': 'Sample', 'source': 'Manual', 'owner': 'CEO', 'description': 'Executed vendor agreements defining security and service obligations.'},
    ],
    'Vendor Due Diligence': [
        {'name': 'Vendor Due Diligence', 'type': 'General', 'source': 'Tracker', 'owner': 'CEO', 'description': 'Vendor review, assurance, and due diligence records.'},
        {'name': 'Critical Vendor SOC 2 Reports', 'type': 'General', 'source': 'Manual', 'owner': 'CEO', 'description': 'Third-party assurance reports for critical vendors.'},
    ],
    'Monitoring Infrastructure': [
        {'name': 'Monitoring Tools Enabled', 'type': 'Settings', 'source': 'Azure', 'owner': 'IT Manager', 'description': 'Monitoring alert configuration for Azure and integrated tooling.'},
        {'name': 'Performance Monitoring Alert Configuration', 'type': 'Settings', 'source': 'Tracker', 'owner': 'IT Manager', 'description': 'Tracker monitoring profiles and alert routing configuration.'},
    ],
    'Intrusion Detection': [
        {'name': 'Intrusion Detection Configuration', 'type': 'Settings', 'source': 'Manual', 'owner': 'IT Manager', 'description': 'IDS/IPS, FIM, and intrusion detection configuration evidence.'},
        {'name': 'Firewall Rules', 'type': 'Settings', 'source': 'Azure', 'owner': 'IT Manager', 'description': 'Firewall and network segmentation rules supporting threat detection.'},
    ],
    'Vulnerability Scan': [
        {'name': 'Vulnerability Scan Results', 'type': 'Population', 'source': 'M365/Defender', 'owner': 'IT Manager', 'description': 'Automated vulnerability scan outputs and remediation tracking.'},
    ],
}


READINESS_DOMAIN_MAP = {
    'Board Oversight OR Management Oversight': 'Governance',
    'Job Descriptions': 'Governance',
    'Background Check': 'People Controls',
    'Non Disclosure Agreement': 'Confidentiality',
    'Security Awareness Training': 'Training',
    'Logical Access Policy': 'Access Control',
    'Administrator Access': 'Access Control',
    'Provisioning': 'Access Control',
    'Termination of Access': 'Access Control',
    'User Access Review': 'Access Control',
    'Data Classification Policy': 'Confidentiality',
    'Data Retention/Deletion': 'Confidentiality',
    'Encryption at Rest': 'Confidentiality',
    'Encryption in Transit': 'Confidentiality',
    'Asset Inventory': 'Asset Management',
    'Antivirus': 'System Monitoring',
    'Change Management Policy': 'Change Management',
    'Business Continuity': 'Data Backup and Disaster Recovery',
    'Incident Response: Process': 'Incident Response',
    'Incident Response: Testing': 'Incident Response',
    'Contracts': 'Vendor Management',
    'Vendor Due Diligence': 'Vendor Management',
    'Monitoring Infrastructure': 'System Monitoring',
    'Intrusion Detection': 'System Monitoring',
    'Vulnerability Scan': 'System Monitoring',
}


PRIORITY_MAP = {
    'Not In Place': 'P1-Critical',
    'Partially In Place': 'P2-High',
    'In Place': 'P3-Validate',
}


STATUS_MAP = {
    'Not In Place': 'Open',
    'Partially In Place': 'Partially In Place',
    'In Place': 'In Place',
}


def _slugify(value):
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


def _upsert_evidence_for_control(control):
    created = 0
    updated = 0
    evidence_names = []

    for payload in CONTROL_EVIDENCE_MAP.get(control.control_name, []):
        evidence = StrikeGraphEvidence.query.filter_by(evidence_name=payload['name']).first()
        if evidence is None:
            evidence = StrikeGraphEvidence(evidence_name=payload['name'])
            db.session.add(evidence)
            created += 1
        else:
            updated += 1

        evidence.control_id = control.id
        evidence.evidence_description = payload['description']
        evidence.evidence_type = payload['type']
        evidence.owner = payload['owner']
        evidence.automation_source = payload['source']
        evidence.is_active = True
        evidence_names.append(payload['name'])

    return created, updated, evidence_names


def _upsert_readiness_for_control(control, evidence_names):
    item_key = f"type1-control-{_slugify(control.control_name)}"
    readiness = SOC2ReadinessItem.query.filter_by(item_key=item_key).first()
    created = False
    previous_status = None
    if readiness is None:
        readiness = SOC2ReadinessItem(item_key=item_key)
        db.session.add(readiness)
        created = True
    else:
        previous_status = readiness.status

    readiness.title = f"Validate {control.control_name} design and evidence package"
    readiness.domain = READINESS_DOMAIN_MAP.get(control.control_name, 'SOC 2 Controls')
    readiness.audit_alignment = control.audit_alignment
    readiness.priority = PRIORITY_MAP.get(control.control_progress, 'P2-High')
    readiness.status = STATUS_MAP.get(control.control_progress, 'Open')
    readiness.owner = control.control_owner
    readiness.frequency = control.control_frequency
    readiness.source_type = 'type1_control'
    readiness.source_reference = f"SOC2Control:{control.control_name}"
    readiness.evidence_reference = ', '.join(evidence_names)
    readiness.next_step = f"Review evidence for {control.control_name} and confirm the Type 1 audit package contains current support for: {', '.join(evidence_names) or 'linked evidence items'}."
    readiness.notes = f"Generated from refreshed Type 1 control '{control.control_name}'."
    readiness.is_active = True

    if created:
        db.session.flush()

    if created or previous_status != readiness.status:
        db.session.add(SOC2ReadinessUpdate(
            readiness_item_id=readiness.id,
            update_type='seed' if created else 'status_change',
            previous_status=previous_status,
            new_status=readiness.status,
            note='Refreshed from current Type 1 control-to-evidence mapping.',
            created_by='system',
        ))

    return created


def sync_links():
    created_evidence = 0
    updated_evidence = 0
    created_readiness = 0

    controls = SOC2Control.query.filter(SOC2Control.control_name.in_(CONTROL_EVIDENCE_MAP.keys())).all()
    for control in controls:
        evidence_created, evidence_updated, evidence_names = _upsert_evidence_for_control(control)
        created_evidence += evidence_created
        updated_evidence += evidence_updated
        if _upsert_readiness_for_control(control, evidence_names):
            created_readiness += 1

    updated_controls = sync_control_automation_flags(db.session)
    updated_progress = sync_control_progress_flags(db.session)

    db.session.commit()
    return {
        'controls_linked': len(controls),
        'evidence_created': created_evidence,
        'evidence_updated': updated_evidence,
        'readiness_created': created_readiness,
        'controls_reflagged': len(updated_controls),
        'controls_progress_updated': len(updated_progress),
    }


def main():
    with app.app_context():
        result = sync_links()
        print(f"✓ Linked {result['controls_linked']} Type 1 controls to evidence items")
        print(f"  Evidence created: {result['evidence_created']}")
        print(f"  Evidence updated: {result['evidence_updated']}")
        print(f"  Readiness items created: {result['readiness_created']}")
        print(f"  Controls reflagged: {result['controls_reflagged']}")
        print(f"  Controls progress updated: {result['controls_progress_updated']}")


if __name__ == '__main__':
    main()