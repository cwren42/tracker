#!/usr/bin/env python3
"""
Create missing SOC2 policies based on requirements analysis
"""
import sqlite3
import os
from datetime import datetime

# Policy templates
MISSING_POLICIES = [
    {
        'document_id': 'IS-CIRQ-P-027-G',
        'title': 'Background Check and Hiring Policy',
        'category': 'HR & Personnel Security',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CEO, HR Manager',
        'sections': [
            ('1', 'Introduction', 'Cirque Corporation ("the Company") is committed to maintaining a secure workforce and ensuring that all employees and contractors meet appropriate standards of trustworthiness and competence.'),
            ('2', 'Purpose', 'This policy establishes requirements for background checks and hiring procedures to verify the identity, qualifications, and suitability of individuals before granting access to company information systems and sensitive data.'),
            ('3', 'Scope', 'This policy applies to all employees, contractors, temporary staff, and third-party personnel who require access to Cirque systems or handle sensitive company information.'),
            ('4', 'Background Check Requirements', 'Prior to employment or contract engagement, the following checks shall be conducted:\n- Identity verification using government-issued identification\n- Employment history verification for the past 5 years\n- Education and professional certification verification\n- Criminal background check as permitted by applicable law\n- Reference checks from previous employers\n- Credit checks for positions with financial responsibilities (where legally permitted)'),
            ('5', 'Hiring Process', 'All hiring decisions must include:\n- Job description defining required skills and security responsibilities\n- Interview process assessing technical competence and cultural fit\n- Completion of all required background checks before offer acceptance\n- Documented approval from hiring manager and HR\n- Signed offer letter including security and confidentiality obligations'),
            ('6', 'International Considerations', 'For international hires, background checks shall comply with local laws and regulations while maintaining equivalent security standards. Alternative verification methods may be used where background checks are restricted by law.'),
            ('7', 'Ongoing Monitoring', 'For positions with elevated security responsibilities, periodic background re-checks may be conducted every 3-5 years or when there is cause for concern.'),
            ('8', 'Responsibilities', '- HR Manager: Coordinate background checks and maintain records\n- Hiring Managers: Define job requirements and security needs\n- Legal: Ensure compliance with applicable employment laws'),
            ('9', 'Policy Review', 'This policy shall be reviewed annually and updated as needed to reflect changes in risk profile, legal requirements, or business operations.'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-028-G',
        'title': 'Code of Conduct',
        'category': 'Ethics & Compliance',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CEO, General Counsel',
        'sections': [
            ('1', 'Introduction', 'Cirque Corporation is committed to conducting business with integrity, honesty, and respect. This Code of Conduct establishes the ethical standards and behavioral expectations for all personnel.'),
            ('2', 'Purpose', 'This Code of Conduct defines the principles and standards that guide our behavior and decision-making, ensuring compliance with legal requirements and maintaining stakeholder trust.'),
            ('3', 'Scope', 'This Code applies to all directors, officers, employees, contractors, and agents of Cirque Corporation worldwide.'),
            ('4', 'Core Values', 'All personnel shall:\n- Act with integrity and honesty in all business dealings\n- Treat colleagues, customers, and partners with respect and dignity\n- Comply with all applicable laws, regulations, and company policies\n- Protect company assets and confidential information\n- Avoid conflicts of interest\n- Report violations or concerns through established channels'),
            ('5', 'Information Security Responsibilities', 'All personnel must:\n- Protect sensitive and confidential information\n- Use company systems only for authorized business purposes\n- Follow acceptable use policies for technology resources\n- Report security incidents immediately\n- Complete required security awareness training\n- Respect intellectual property rights'),
            ('6', 'Conflicts of Interest', 'Personnel must avoid situations where personal interests conflict with company interests. Any potential conflicts must be disclosed to management promptly.'),
            ('7', 'Reporting Violations', 'Personnel are required to report suspected violations of this Code, company policies, or applicable laws. Reports may be made to:\n- Direct supervisor or manager\n- Human Resources\n- Legal Department\n- Anonymous hotline (if available)\n\nRetaliation against individuals who report concerns in good faith is strictly prohibited.'),
            ('8', 'Consequences of Violations', 'Violations of this Code may result in disciplinary action, up to and including termination of employment or contract, and may also result in civil or criminal penalties.'),
            ('9', 'Acknowledgment', 'All personnel must sign an acknowledgment confirming they have received, read, and agree to comply with this Code of Conduct.'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-029-G',
        'title': 'Configuration Management Policy',
        'category': 'Technical Controls',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, IT Manager',
        'sections': [
            ('1', 'Introduction', 'Proper configuration management is essential to maintaining secure, stable, and compliant information systems at Cirque Corporation.'),
            ('2', 'Purpose', 'This policy establishes requirements for developing, documenting, and maintaining secure baseline configurations for all information systems, network devices, and endpoints.'),
            ('3', 'Scope', 'This policy applies to all information systems, servers, network devices, workstations, mobile devices, and cloud services used by Cirque Corporation.'),
            ('4', 'Configuration Standards', 'The IT team shall develop and maintain secure baseline configurations for all system types, including:\n- Hardened operating system settings\n- Disabled unnecessary services and protocols\n- Secure authentication requirements\n- Logging and monitoring configurations\n- Network security settings\n- Application security parameters'),
            ('5', 'Configuration Development', 'Baseline configurations shall be developed using:\n- Industry best practices (CIS Benchmarks, vendor security guides)\n- Regulatory and compliance requirements\n- Risk assessment results\n- Business operational needs\n- Security team recommendations'),
            ('6', 'Configuration Deployment', 'All new systems must be deployed using approved baseline configurations. Configuration management tools should be used to automate deployment and enforcement where possible.'),
            ('7', 'Configuration Monitoring', 'Automated tools shall be used to monitor systems for configuration drift and non-compliance with baseline standards. Deviations must be investigated and remediated promptly.'),
            ('8', 'Configuration Changes', 'Changes to baseline configurations must follow the Change Management Policy, including:\n- Documentation of proposed changes\n- Security impact assessment\n- Testing in non-production environment\n- Approval by IT management\n- Documentation of changes in configuration repository'),
            ('9', 'Emergency Rollback', 'Previous baseline configurations shall be maintained to enable rapid rollback in case of system issues following configuration changes.'),
            ('10', 'Configuration Review', 'Baseline configurations shall be reviewed at least annually, or more frequently when:\n- Significant security vulnerabilities are identified\n- New threats emerge\n- Regulatory requirements change\n- Major system upgrades occur'),
            ('11', 'Documentation', 'Configuration standards, baselines, and change history shall be documented and maintained in a secure configuration repository with appropriate access controls.'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-030-G',
        'title': 'Contract and Agreement Management Policy',
        'category': 'Legal & Compliance',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CEO, General Counsel',
        'sections': [
            ('1', 'Introduction', 'Cirque Corporation relies on contracts and agreements to define commitments, responsibilities, and security requirements with customers, vendors, and partners.'),
            ('2', 'Purpose', 'This policy establishes requirements for contracts and agreements to ensure appropriate data security, privacy, and compliance commitments are clearly defined and enforceable.'),
            ('3', 'Scope', 'This policy applies to all contracts, service agreements, statements of work, and other binding agreements entered into by Cirque Corporation.'),
            ('4', 'Security Requirements in Contracts', 'All contracts involving access to Cirque systems or data must include:\n- Data security and confidentiality obligations\n- Access control and authentication requirements\n- Incident notification and reporting requirements\n- Data protection and privacy commitments\n- Compliance with applicable laws and regulations\n- Audit rights and security assessment provisions\n- Data retention and deletion requirements\n- Liability and indemnification clauses'),
            ('5', 'Customer Contracts', 'Customer agreements shall define:\n- Service level commitments\n- Data ownership and usage rights\n- Security controls and certifications (SOC 2, ISO 27001, etc.)\n- Data breach notification procedures\n- Customer rights to audit\n- Data portability and deletion rights'),
            ('6', 'Vendor and Supplier Contracts', 'Vendor agreements shall require:\n- Compliance with Cirque security policies\n- Background checks for personnel with data access\n- Confidentiality and non-disclosure obligations\n- Security incident reporting\n- Right to audit vendor security controls\n- Subcontractor management requirements\n- Insurance coverage for data breaches'),
            ('7', 'Privacy Requirements', 'Contracts involving personal data must address:\n- Legal basis for data processing\n- Data subject rights (access, deletion, portability)\n- International data transfer mechanisms\n- Data protection impact assessments\n- Compliance with GDPR, CCPA, and other privacy laws'),
            ('8', 'Contract Review and Approval', 'All contracts must be reviewed and approved by:\n- Legal counsel (for legal compliance)\n- IT/Security (for security requirements)\n- Finance (for financial terms)\n- Authorized signatory (for final approval)'),
            ('9', 'Contract Repository', 'All executed contracts shall be maintained in a secure, centralized repository with appropriate access controls and retention schedules.'),
            ('10', 'Contract Monitoring', 'Contract owners shall monitor compliance with security commitments and report any breaches or issues to management.'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-031-G',
        'title': 'Data Management Policy',
        'category': 'Data Governance',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, DPO',
        'sections': [
            ('1', 'Introduction', 'Effective data management is critical to Cirque Corporation\'s operations, security, and compliance obligations.'),
            ('2', 'Purpose', 'This policy establishes requirements for managing information throughout its lifecycle, including creation, storage, usage, sharing, archival, and destruction.'),
            ('3', 'Scope', 'This policy applies to all information created, received, processed, or stored by Cirque Corporation, regardless of format (electronic, paper, or other media).'),
            ('4', 'Data Lifecycle Management', 'All data shall be managed through its complete lifecycle:\n- Creation: Data created or received through authorized processes\n- Classification: Labeled according to Data Classification Policy\n- Storage: Stored securely based on classification level\n- Usage: Accessed only by authorized personnel for legitimate purposes\n- Sharing: Shared only when authorized and with appropriate protections\n- Archival: Retained according to legal and business requirements\n- Destruction: Securely disposed of when no longer needed'),
            ('5', 'Data Collection', 'Data collection must be:\n- Limited to what is necessary for defined business purposes\n- Transparent to data subjects\n- Lawful and compliant with privacy regulations\n- Documented with clear purpose and legal basis'),
            ('6', 'Data Quality', 'Data owners are responsible for ensuring:\n- Accuracy and completeness of data\n- Regular review and correction of errors\n- Timely updates when information changes\n- Validation of data inputs where feasible'),
            ('7', 'Data Storage', 'Data must be stored:\n- On approved systems and platforms\n- With encryption for sensitive data\n- With appropriate access controls\n- With regular backups for critical data\n- In compliance with data residency requirements'),
            ('8', 'Data Sharing', 'Data may be shared when:\n- Authorized by data owner or policy\n- Necessary for legitimate business purposes\n- Protected with appropriate security controls (encryption, secure transfer)\n- Governed by appropriate agreements (NDAs, DPAs)\n- Compliant with privacy regulations and data subject rights'),
            ('9', 'Data Ownership', 'Each category of data shall have a designated data owner responsible for:\n- Defining access requirements\n- Approving data sharing\n- Ensuring data quality\n- Compliance with policies'),
            ('10', 'Personal Data Processing', 'Processing of personal data must comply with applicable privacy laws (GDPR, CCPA, etc.) and include:\n- Lawful basis for processing\n- Privacy notices to data subjects\n- Mechanisms for data subject rights\n- Data protection impact assessments for high-risk processing'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-032-G',
        'title': 'Data Retention and Deletion Policy',
        'category': 'Data Governance',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, General Counsel',
        'sections': [
            ('1', 'Introduction', 'Proper data retention and deletion practices are essential for compliance, security, and efficient operations at Cirque Corporation.'),
            ('2', 'Purpose', 'This policy establishes requirements for retaining and securely deleting data based on legal, regulatory, business, and security requirements.'),
            ('3', 'Scope', 'This policy applies to all data and records created, received, or maintained by Cirque Corporation, including electronic and physical records.'),
            ('4', 'Retention Requirements', 'Data shall be retained according to:\n- Legal and regulatory requirements\n- Contractual obligations\n- Litigation holds and legal discovery needs\n- Business operational needs\n- Historical and archival value'),
            ('5', 'Retention Schedules', 'Retention schedules have been established for major data categories:\n- Financial records: 7 years\n- Employee records: 7 years after termination\n- Customer contracts: Duration of contract + 7 years\n- Security logs: 1 year minimum\n- Backup data: 30-90 days\n- Personal data: Only as long as necessary for processing purpose\n- Other business records: As defined by business need and legal requirements'),
            ('6', 'Data Deletion Requirements', 'Data must be securely deleted when:\n- Retention period expires\n- No longer needed for business purposes\n- Requested by data subject (where required by law)\n- Required by contract or agreement\n- Subject to litigation hold is released'),
            ('7', 'Secure Deletion Methods', 'Data deletion must render data unrecoverable:\n- Electronic data: Secure deletion using approved tools (overwrite, degaussing, physical destruction)\n- Databases: Secure deletion of records and backups\n- Physical records: Shredding or secure disposal service\n- Hardware: Sanitization or physical destruction before disposal\n- Cloud data: Verification of deletion across all storage locations'),
            ('8', 'Deletion Verification', 'Deletion activities shall be:\n- Documented with date, method, and responsible party\n- Verified to confirm completion\n- Tracked in deletion log or records management system'),
            ('9', 'Retention Exceptions', 'Data may be retained beyond normal retention period when:\n- Subject to legal hold or litigation\n- Required for ongoing investigation\n- Contractually required for customer audit\n- Approved by Legal counsel with documented justification'),
            ('10', 'Personal Data Rights', 'Individuals have the right to request deletion of their personal  data. Such requests shall be:\n- Processed within required timeframes (typically 30 days)\n- Evaluated for applicable legal exceptions\n- Documented with outcome and rationale\n- Executed across all systems and backups where feasible'),
            ('11', 'Roles and Responsibilities', '- Data Owners: Define retention requirements for their data\n- IT Team: Implement technical deletion procedures\n- Records Manager: Maintain retention schedules and track deletions\n- Legal: Advise on legal requirements and litigation holds'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-033-G',
        'title': 'Non-Disclosure Agreement (NDA) Policy',
        'category': 'Confidentiality & Legal',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CEO, General Counsel',
        'sections': [
            ('1', 'Introduction', 'Cirque Corporation depends on protecting confidential information and trade secrets. Non-disclosure agreements (NDAs) are a critical legal tool for this protection.'),
            ('2', 'Purpose', 'This policy establishes requirements for when NDAs must be used, what they must include, and how they are managed to protect Cirque confidential information.'),
            ('3', 'Scope', 'This policy applies to all employees, contractors, vendors, partners, customers, and other parties who may have access to Cirque confidential information.'),
            ('4', 'When NDAs are Required', 'NDAs must be executed before sharing confidential information with:\n- New employees and contractors\n- Vendors and service providers with system access\n- Business partners and potential partners\n- Customers receiving confidential product information\n- Any external party conducting audit or assessment\n- Consultants and temporary workers'),
            ('5', 'employee NDAs', 'All employees and contractors must sign an NDA:\n- Before or on first day of employment\n- As part of onboarding process\n- Covering duration of employment and period after termination\n- Including provisions for return of confidential materials upon termination'),
            ('6', 'Vendor and Partner NDAs', 'Third parties requiring access to Cirque systems or confidential data must execute NDAs including:\n- Definition of confidential information\n- Limitations on use and disclosure\n- Security requirements for protecting information\n- Rights to audit compliance\n- Duration of confidentiality obligations\n- Liability for breach\n- Jurisdiction and dispute resolution'),
            ('7', 'Mutual NDAs', 'For relationships involving exchange of confidential information, mutual NDAs shall be used where both parties have confidentiality obligations.'),
            ('8', 'NDA Requirements', 'All NDAs must include:\n- Clear definition of what constitutes confidential information\n- Permitted uses of confidential information\n- Prohibited disclosures and uses\n- Security requirements\n- Term and survival of obligations\n- Consequences of breach\n- Return or destruction of information upon termination'),
            ('9', 'NDA Approval and Execution', 'All NDAs must be:\n- Reviewed and approved by Legal counsel\n- Signed by authorized representative\n- Maintained in contract repository\n- Tracked for renewal or expiration'),
            ('10', 'Monitoring and Enforcement', 'Compliance with NDAs shall be monitored. Breaches must be:\n- Investigated promptly\n- Reported to Legal and management\n- Remediated with appropriate action\n- Enforced through legal action if necessary'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-034-G',
        'title': 'Vulnerability Management Policy',
        'category': 'Technical Controls',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, CISO',
        'sections': [
            ('1', 'Introduction', 'Identifying and remediating security vulnerabilities is essential to protecting Cirque Corporation\'s systems and data from cyber threats.'),
            ('2', 'Purpose', 'This policy establishes requirements for identifying, assessing, prioritizing, and remediating security vulnerabilities in information systems, applications, and infrastructure.'),
            ('3', 'Scope', 'This policy applies to all information systems, applications, network devices, servers, workstations, and cloud services used by Cirque Corporation.'),
            ('4', 'Vulnerability Scanning', 'Vulnerability scans shall be conducted:\n- Quarterly for all production systems (minimum)\n- Monthly for internet-facing systems (recommended)\n- After significant system changes\n- Before deployment of new systems\n- Using authenticated scanning where possible\n- Covering all network segments and system types'),
            ('5', 'Penetration Testing', 'Independent penetration testing shall be conducted:\n- Annually for internet-facing applications and infrastructure\n- For new critical applications before production deployment\n- By qualified external security professionals\n- With appropriate scope and rules of engagement\n- With management awareness and approval'),
            ('6', 'Vulnerability Assessment', 'Identified vulnerabilities shall be assessed for:\n- Severity level (Critical, High, Medium, Low based on CVSS scores)\n- Exploitability and threat landscape\n- Affected systems and data sensitivity\n- Business impact of exploitation\n- Compensating controls'),
            ('7', 'Remediation Requirements', 'Vulnerabilities shall be remediated according to severity:\n- Critical: Within 7 days or apply compensating controls\n- High: Within 30 days\n- Medium: Within 90 days\n- Low: Within 180 days or accept risk\n\nExceptions require documented risk acceptance by management.'),
            ('8', 'Patch Management', 'Security patches shall be:\n- Evaluated promptly upon release\n- Tested in non-production environment\n- Deployed according to vulnerability severity\n- Automated where possible for critical infrastructure\n- Documented with deployment dates and systems affected'),
            ('9', 'Emergency Patching', 'For actively exploited critical vulnerabilities:\n- Emergency change process may be invoked\n- Patches may be deployed rapidly with limited testing\n- Systems should be monitored closely after deployment\n- Full testing and validation conducted afterward'),
            ('10', 'Vulnerability Tracking', 'All vulnerabilities shall be tracked in a vulnerability management system including:\n- Discovery date and method\n- Affected systems\n- Severity and risk rating\n- Assigned owner\n- Remediation plan and timeline\n- Status updates\n- Verification of remediation'),
            ('11', 'Reporting', 'Vulnerability management metrics shall be reported monthly to IT management and quarterly to executive leadership including:\n- Number of vulnerabilities by severity\n- Mean time to remediation\n- Overdue remediation items\n- Trends and risk areas'),
        ]
    }
]

def create_policies():
    db_path = '/var/www/tracker/assets.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"📝 CREATING MISSING SOC2 POLICIES")
    print(f"=" * 80)
    
    for policy_data in MISSING_POLICIES:
        # Check if policy already exists
        cursor.execute("SELECT id FROM policy WHERE document_id = ?", (policy_data['document_id'],))
        existing = cursor.fetchone()
        
        if existing:
            print(f"⏭️  {policy_data['document_id']} already exists, skipping...")
            continue
        
        # Insert policy
        cursor.execute("""
            INSERT INTO policy (
                document_id, title, category, division, standard_type,
                version, effective_date, review_date, approved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            policy_data['document_id'],
            policy_data['title'],
            policy_data['category'],
            policy_data['division'],
            policy_data['standard_type'],
            policy_data['version'],
            policy_data['effective_date'],
            policy_data['review_date'],
            policy_data['approved_by']
        ))
        
        policy_id = cursor.lastrowid
        
        # Insert sections
        for idx, (section_num, section_title, section_content) in enumerate(policy_data['sections'], 1):
            cursor.execute("""
                INSERT INTO policy_section (
                    policy_id, section_number, section_title, section_content, section_order
                ) VALUES (?, ?, ?, ?, ?)
            """, (policy_id, section_num, section_title, section_content, idx))
        
        print(f"✅ Created {policy_data['document_id']}: {policy_data['title']}")
        print(f"   └─ {len(policy_data['sections'])} sections")
    
    conn.commit()
    
    # Show updated counts
    cursor.execute("SELECT COUNT(*) FROM policy")
    total_policies = cursor.fetchone()[0]
    
    print(f"\n📊 POLICY SUMMARY")
    print(f"=" * 80)
    print(f"Total Policies: {total_policies}")
    
    conn.close()

if __name__ == '__main__':
    create_policies()
