"""Refresh SOC 2 Type 1 controls from the current system description."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from soc2_artifact_service import import_system_description_from_markdown
from models import SystemDescription
from soc2_models import SOC2Control, sync_control_automation_flags, sync_control_progress_flags


def _section_map():
    sections = SystemDescription.query.order_by(SystemDescription.section_order.asc()).all()
    return {section.section_title.strip(): (section.content or '').strip() for section in sections}


def _compact(text, fallback=''):
    value = ' '.join((text or '').split())
    return value or fallback


def _build_control_specs(sections):
    data_summary = _compact(
        sections.get('Data'),
        'Customer design requirements and project materials are handled as confidential information.',
    )
    change_summary = _compact(
        sections.get('Change Management'),
        'Changes are requested, documented, tested, approved, peer reviewed, and quality validated prior to release.',
    )
    backup_summary = _compact(
        sections.get('Data Backup and Disaster Recovery'),
        'Design data is backed up locally and to off-site locations to support recovery.',
    )
    incident_summary = _compact(
        sections.get('Incident Response'),
        'A formal incident response plan defines reporting, escalation, containment, recovery, and post-incident review.',
    )
    vendor_summary = _compact(
        sections.get('Vendor Management'),
        'Vendors are governed by contracts, due diligence, and recurring security review requirements.',
    )
    monitoring_summary = _compact(
        sections.get('System Monitoring'),
        'Infrastructure and applications are monitored through automated tooling, centralized logging, and recurring review.',
    )
    security_management_summary = _compact(
        sections.get('Security Management'),
        'Security policies and procedures govern access control, change management, monitoring, backup, and incident response.',
    )
    access_summary = _compact(
        sections.get('Logical and Physical Access'),
        'Logical and physical access is restricted to authorized personnel and visitors are escorted.',
    )
    oversight_summary = _compact(
        sections.get('Board/Owner/Management Oversight'),
        'Executive management meets periodically to oversee operations management activities and related issues.',
    )
    org_summary = _compact(
        sections.get('Organizational Structure'),
        'Organizational structure, reporting hierarchies, and responsibilities are defined across functional areas.',
    )
    integrity_summary = _compact(
        sections.get('Integrity and Ethical Values'),
        'Employee handbook, security training, NDAs, and background checks reinforce expected behavior.',
    )

    return [
        {
            'name': 'Board Oversight OR Management Oversight',
            'description': f"Executive management oversight is established through periodic management review and direct monitoring of operational and security matters. {oversight_summary}",
            'frequency': 'Quarterly',
            'owner': 'Executive Management',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.1.2, SOC2:2022.CC.3.1, SOC2:2022.CC.4.1',
            'automation': False,
        },
        {
            'name': 'Job Descriptions',
            'description': f"Roles, reporting lines, and accountability for in-scope operations are formally defined to support segregation of duties and control ownership. {org_summary}",
            'frequency': 'Annually',
            'owner': 'Human Resources',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.1.3, SOC2:2022.CC.1.4, SOC2:2022.CC.2.2',
            'automation': False,
        },
        {
            'name': 'Background Check',
            'description': f"Pre-employment screening is part of the control environment for personnel in scope of the system. {integrity_summary}",
            'frequency': 'As Needed',
            'owner': 'Human Resources',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.1.4',
            'automation': False,
        },
        {
            'name': 'Non Disclosure Agreement',
            'description': f"Personnel confidentiality obligations are established through executed NDAs and supporting conduct expectations. {integrity_summary}",
            'frequency': 'As Needed',
            'owner': 'Human Resources',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.1.1, SOC2:2022.CC.6.7, SOC2:2022.C1.2',
            'automation': False,
        },
        {
            'name': 'Security Awareness Training',
            'description': f"Personnel receive mandatory security awareness and incident reporting training, supplemented by quarterly phishing simulations. {incident_summary}",
            'frequency': 'Quarterly',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.2.2, SOC2:2022.CC.7.3',
            'automation': False,
        },
        {
            'name': 'Logical Access Policy',
            'description': f"Access to facilities, systems, and project data is restricted to authorized personnel according to business need. {access_summary}",
            'frequency': 'Annually',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.5.2, SOC2:2022.CC.6.1, SOC2:2022.CC.6.2, SOC2:2022.CC.6.3',
            'automation': False,
        },
        {
            'name': 'Administrator Access',
            'description': f"Privileged administrative access requires individual accountability, MFA, and logging across Azure, Active Directory, and application layers. {monitoring_summary}",
            'frequency': 'Daily',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.6.2, SOC2:2022.CC.6.3, SOC2:2022.CC.6.6',
            'automation': True,
        },
        {
            'name': 'Provisioning',
            'description': f"Authorization, creation, and modification of information system access follow defined procedures maintained as part of the security program. {security_management_summary}",
            'frequency': 'As Needed',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.6.1, SOC2:2022.CC.6.2, SOC2:2022.CC.6.3, SOC2:2022.CC.6.4',
            'automation': True,
        },
        {
            'name': 'Termination of Access',
            'description': f"Termination and change-of-role access actions are part of the documented security lifecycle for logical and physical access. {security_management_summary}",
            'frequency': 'As Needed',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.6.2, SOC2:2022.CC.6.3, SOC2:2022.CC.6.4',
            'automation': True,
        },
        {
            'name': 'User Access Review',
            'description': f"Management reviews user and privileged access to ensure project data and supporting systems remain limited to approved personnel with a need to know. {data_summary}",
            'frequency': 'Quarterly',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.5.2, SOC2:2022.CC.6.2, SOC2:2022.CC.6.3',
            'automation': True,
        },
        {
            'name': 'Data Classification Policy',
            'description': f"Customer design requirements, project files, and related materials are classified and handled according to their confidentiality requirements. {data_summary}",
            'frequency': 'Annually',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.C1.1, SOC2:2022.C1.2, SOC2:2022.CC.2.1, SOC2:2022.CC.6.1',
            'automation': False,
        },
        {
            'name': 'Data Retention/Deletion',
            'description': f"Customer project data is archived at project completion and retained for a defined period before deletion. {data_summary}",
            'frequency': 'Annually',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.C1.1, SOC2:2022.C1.2, SOC2:2022.CC.6.5',
            'automation': False,
        },
        {
            'name': 'Encryption at Rest',
            'description': f"Confidential design data is stored on controlled infrastructure and cloud services with encryption requirements defined as part of the service commitments and security architecture. {backup_summary}",
            'frequency': 'Daily',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.C1.2, SOC2:2022.CC.6.1, SOC2:2022.CC.6.7',
            'automation': True,
        },
        {
            'name': 'Encryption in Transit',
            'description': f"Transmission of confidential information is protected through managed cloud services, secured administrative channels, and monitored infrastructure pathways. {monitoring_summary}",
            'frequency': 'Daily',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.C1.2, SOC2:2022.CC.6.6, SOC2:2022.CC.6.7',
            'automation': True,
        },
        {
            'name': 'Asset Inventory',
            'description': 'Tracker maintains centralized visibility of assets, vulnerabilities, and patch status for in-scope systems and company-issued endpoints.',
            'frequency': 'Weekly',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.2.1, SOC2:2022.CC.6.1, SOC2:2022.CC.7.1',
            'automation': True,
        },
        {
            'name': 'Antivirus',
            'description': f"Microsoft Defender for Business is deployed across company-issued workstations and servers, with daily signature updates and centralized alerting. {monitoring_summary}",
            'frequency': 'Daily',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.6.7, SOC2:2022.CC.7.1, SOC2:2022.CC.7.2',
            'automation': True,
        },
        {
            'name': 'Change Management Policy',
            'description': f"Technology changes are formally requested, documented, tested, approved, peer reviewed, and quality validated prior to release. {change_summary}",
            'frequency': 'Annually',
            'owner': 'Quality Director',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.5.2, SOC2:2022.CC.8.1',
            'automation': False,
        },
        {
            'name': 'Business Continuity',
            'description': f"Design data backup and recovery procedures support continued availability through nightly local backups, Taipei replication, and recurring off-site AWS backups. {backup_summary}",
            'frequency': 'Quarterly',
            'owner': 'IT Manager',
            'progress': 'Partially In Place',
            'alignment': 'SOC2:2022.CC.7.3, SOC2:2022.CC.7.4, SOC2:2022.CC.9.1',
            'automation': False,
        },
        {
            'name': 'Incident Response: Process',
            'description': f"A formal security incident response plan defines reporting, escalation, containment, eradication, recovery, and post-incident review for security events. {incident_summary}",
            'frequency': 'Annually',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.7.3, SOC2:2022.CC.7.4, SOC2:2022.CC.7.5',
            'automation': False,
        },
        {
            'name': 'Incident Response: Testing',
            'description': f"The incident response plan is tested through semi-annual tabletop exercises and supported by guided playbooks and tracked post-incident actions. {incident_summary}",
            'frequency': 'Semi-Annual',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.7.4, SOC2:2022.CC.7.5',
            'automation': False,
        },
        {
            'name': 'Contracts',
            'description': f"Vendor and business partner relationships are governed by formal contracts that define scope, responsibilities, compliance requirements, and service expectations. {vendor_summary}",
            'frequency': 'Annually',
            'owner': 'CEO',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.2.3, SOC2:2022.CC.9.2, SOC2:2022.C1.2',
            'automation': False,
        },
        {
            'name': 'Vendor Due Diligence',
            'description': f"New vendors undergo security due diligence before contract execution and are reviewed annually based on vendor risk, including third-party SOC 2 report review when applicable. {vendor_summary}",
            'frequency': 'Annually',
            'owner': 'CEO',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.3.2, SOC2:2022.CC.9.2, SOC2:2022.C1.2',
            'automation': False,
        },
        {
            'name': 'Monitoring Infrastructure',
            'description': f"Infrastructure and applications are monitored through Azure Sentinel, Defender, Intune, firewall telemetry, and Tracker alerting with defined daily, weekly, and monthly review cadences. {monitoring_summary}",
            'frequency': 'Daily',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.4.1, SOC2:2022.CC.7.1, SOC2:2022.CC.7.2',
            'automation': True,
        },
        {
            'name': 'Intrusion Detection',
            'description': f"Azure Firewall IDS/IPS, Azure Security Center file integrity monitoring, Linux AIDE checks, and alert escalation support detection of suspicious activity and configuration drift. {monitoring_summary}",
            'frequency': 'Daily',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.6.6, SOC2:2022.CC.7.1, SOC2:2022.CC.7.2',
            'automation': True,
        },
        {
            'name': 'Vulnerability Scan',
            'description': f"Weekly automated vulnerability scans are executed through Microsoft Defender for Cloud and tracked through defined remediation SLAs in Tracker. {monitoring_summary}",
            'frequency': 'Weekly',
            'owner': 'IT Manager',
            'progress': 'In Place',
            'alignment': 'SOC2:2022.CC.4.1, SOC2:2022.CC.7.1, SOC2:2022.CC.7.2, SOC2:2022.CC.7.3',
            'automation': True,
        },
    ]


def refresh_controls():
    import_result = import_system_description_from_markdown()
    sections = _section_map()
    specs = _build_control_specs(sections)
    active_control_names = {spec['name'] for spec in specs}

    created = []
    updated = []

    for control in SOC2Control.query.all():
        control.is_active = control.control_name in active_control_names

    for spec in specs:
        control = SOC2Control.query.filter_by(control_name=spec['name']).first()
        if control is None:
            control = SOC2Control(control_name=spec['name'])
            db.session.add(control)
            created.append(spec['name'])
        else:
            updated.append(spec['name'])

        control.control_description = spec['description']
        control.control_frequency = spec['frequency']
        control.control_owner = spec['owner']
        control.control_progress = spec['progress']
        control.audit_alignment = spec['alignment']
        control.automation_enabled = spec['automation']
        control.is_active = True

    sync_control_automation_flags(db.session)
    sync_control_progress_flags(db.session)
    db.session.commit()

    return {
        'import_result': import_result,
        'created': created,
        'updated': updated,
        'total_controls_refreshed': len(specs),
    }


def main():
    with app.app_context():
        result = refresh_controls()
        print(
            f"✓ Imported system description sections: {result['import_result']['sections']} total, "
            f"{result['import_result']['matched']} matched headings, {result['import_result']['updated']} updated"
        )
        print(f"✓ Refreshed {result['total_controls_refreshed']} Type 1 controls from the system description")
        print(f"  Created: {len(result['created'])}")
        for name in result['created']:
            print(f"    + {name}")
        print(f"  Updated: {len(result['updated'])}")
        for name in result['updated']:
            print(f"    ~ {name}")


if __name__ == '__main__':
    main()