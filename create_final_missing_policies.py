#!/usr/bin/env python3
"""
Create final missing SOC2 policies for unmapped controls
"""
import sqlite3
import os

# Policy templates for unmapped controls
MISSING_POLICIES = [
    {
        'document_id': 'IS-CIRQ-P-035-G',
        'title': 'Patch Management Policy',
        'category': 'Technical Controls',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, CISO',
        'sections': [
            ('1', 'Introduction', 'Timely application of security patches is critical to protecting Cirque Corporation systems from known vulnerabilities and cyber threats.'),
            ('2', 'Purpose', 'This policy establishes requirements for identifying, testing, approving, and deploying security patches and updates to operating systems, applications, firmware, and network devices.'),
            ('3', 'Scope', 'This policy applies to all servers, workstations, network devices, applications, and systems operated by or on behalf of Cirque Corporation.'),
            ('4', 'Patch Sources', 'Security patches shall be obtained from:\n- Operating system vendors (Microsoft, Linux distributions, etc.)\n- Application vendors and publishers\n- Hardware and firmware manufacturers\n- Security advisories and threat intelligence feeds\n- Vulnerability scanning tool recommendations'),
            ('5', 'Patch Assessment', 'Upon release, patches shall be assessed for:\n- Criticality and severity of vulnerability addressed\n- Applicability to Cirque systems\n- Dependencies and compatibility requirements\n- Potential business impact\n- Vendor recommendations and deployment timeline'),
            ('6', 'Patch Testing', 'Before production deployment, patches shall be:\n- Tested in non-production environment\n- Validated for compatibility with existing systems\n- Assessed for performance impact\n- Documented with test results\n\nCritical security patches may have abbreviated testing when urgent deployment is needed.'),
            ('7', 'Patch Deployment Schedule', 'Patches shall be deployed according to severity:\n- Critical: Within 7 days of release (or immediately if actively exploited)\n- High: Within 30 days of release\n- Medium: Within 60 days of release\n- Low: Within 90 days or with next scheduled maintenance\n\nAutomated patching should be used where feasible for critical infrastructure.'),
            ('8', 'Automatic Patching', 'Critical systems may be configured for automatic patch installation:\n- Server operating systems: Automatic installation of critical security patches\n- Workstations: Automatic updates enabled for OS and antivirus\n- Network devices: Scheduled automated updates where supported\n- Monitoring to verify successful installation'),
            ('9', 'Manual Patching', 'Systems requiring manual patching:\n- Legacy systems with compatibility concerns\n- Production systems requiring change control approval\n- Systems with custom configurations\n- Third-party managed systems\n\nManual patching shall follow Change Management Policy procedures.'),
            ('10', 'Emergency Patching', 'For zero-day vulnerabilities or actively exploited threats:\n- Emergency change process may be invoked\n- Patches deployed rapidly with abbreviated testing\n- Rollback plan prepared before deployment\n- Close monitoring during and after deployment\n- Full documentation and post-deployment review'),
            ('11', 'Patch Verification', 'After deployment, verify:\n- Successful installation on all targeted systems\n- No adverse impacts on system functionality\n- Vulnerability scanner confirms patch applied\n- Documentation updated with deployment status'),
            ('12', 'Patch Documentation', 'Maintain records including:\n- Patches evaluated and deployment decisions\n- Systems patched and dates deployed\n- Test results and approvals\n- Failed deployments and remediation actions\n- Compliance with deployment timelines'),
            ('13', 'Exceptions', 'Patch deployment exceptions require:\n- Documented justification\n- Risk assessment and compensating controls\n- Approval by IT Manager and CISO\n- Re-evaluation at least quarterly'),
            ('14', 'Roles and Responsibilities', '- IT Team: Assess, test, and deploy patches\n- System Owners: Approve patches for their systems\n- IT Manager: Oversee patch management program\n- CISO: Monitor compliance and approve exceptions'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-036-G',
        'title': 'Antivirus and Endpoint Protection Policy',
        'category': 'Technical Controls',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, CISO',
        'sections': [
            ('1', 'Introduction', 'Malware, viruses, and other malicious software pose significant threats to Cirque Corporation\'s information systems and data.'),
            ('2', 'Purpose', 'This policy establishes requirements for antivirus and endpoint protection software to detect, prevent, and respond to malicious software threats.'),
            ('3', 'Scope', 'This policy applies to all workstations, laptops, servers, mobile devices, and other endpoints that access Cirque systems or data.'),
            ('4', 'Antivirus Software Requirements', 'All endpoints must have approved antivirus/endpoint protection software that provides:\n- Real-time scanning and protection\n- On-demand and scheduled scanning\n- Automatic signature updates\n- Heuristic and behavioral analysis\n- Ransomware protection\n- Web and email filtering\n- Centralized management and reporting'),
            ('5', 'Approved Solutions', 'Cirque uses the following endpoint protection solutions:\n- Microsoft Defender for Endpoint (primary solution)\n- Additional solutions as approved by IT management\n\nPersonal or unauthorized antivirus software is prohibited.'),
            ('6', 'Installation and Configuration', 'Antivirus software shall be:\n- Installed before connecting to network\n- Configured with company-approved settings\n- Managed through centralized console\n- Set to automatic updates for signatures and software\n- Configured for real-time protection (cannot be disabled by users)'),
            ('7', 'Scanning Requirements', 'Systems shall be configured for:\n- Real-time scanning of all file access operations\n- Scheduled full system scans (at least weekly)\n- Scan of all removable media upon insertion\n- Scan of all email attachments and downloads\n- Regular scans of servers (daily for critical systems)'),
            ('8', 'Signature Updates', 'Antivirus signatures must be:\n- Updated automatically at least daily\n- Verified as current (not more than 48 hours old)\n- Monitored through central console\n- Manually updated if automatic updates fail'),
            ('9', 'Threat Response', 'When malware is detected:\n- Infected files automatically quarantined\n- User notified of detection\n- IT security team alerted for high-severity threats\n- Incident response procedures followed for serious infections\n- Affected system isolated if necessary\n- Remediation actions documented'),
            ('10', 'Monitoring and Reporting', 'IT security team shall:\n- Monitor antivirus console for alerts and infections\n- Review scan logs and detection reports\n- Verify all systems have current protection\n- Identify systems with disabled or outdated protection\n- Report metrics to management monthly'),
            ('11', 'User Responsibilities', 'All users must:\n- Keep antivirus software enabled at all times\n- Not disable or circumvent antivirus protection\n- Report suspected malware infections immediately\n- Not open suspicious email attachments\n- Exercise caution when downloading files'),
            ('12', 'Exceptions', 'Temporary disabling of antivirus requires:\n- Valid business justification\n- Approval from IT Manager\n- Limited duration (maximum 24 hours)\n- Compensating controls (network isolation, etc.)\n- Re-enable verification'),
            ('13', 'Mobile Devices', 'Company-owned and BYOD devices accessing company data must:\n- Have approved mobile security software installed\n- Comply with mobile device management policies\n- Regular security scans performed'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-037-G',
        'title': 'Corporate Governance and Oversight Policy',
        'category': 'Governance & Management',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CEO, Board of Directors',
        'sections': [
            ('1', 'Introduction', 'Effective corporate governance and management oversight are essential for ensuring Cirque Corporation operates with integrity, transparency, and accountability.'),
            ('2', 'Purpose', 'This policy establishes the governance structure, management oversight responsibilities, and accountability mechanisms to ensure the organization achieves its objectives while managing risks appropriately.'),
            ('3', 'Scope', 'This policy applies to the Board of Directors, executive leadership, management, and all employees of Cirque Corporation.'),
            ('4', 'Governance Structure', 'Cirque Corporation\'s governance includes:\n- Board of Directors: Provides independent oversight and strategic direction\n- Executive Leadership: CEO and department heads responsible for operations\n- Management Team: Department leads and managers implementing policies\n- All Employees: Responsible for compliance with policies and procedures'),
            ('5', 'Board of Directors Responsibilities', 'The Board shall:\n- Meet quarterly to review organizational performance\n- Oversee risk management and internal controls\n- Approve major strategic decisions and investments\n- Review and approve annual budgets\n- Ensure compliance with legal and regulatory requirements\n- Evaluate CEO and executive performance\n- Oversee information security program'),
            ('6', 'Executive Leadership Responsibilities', 'Executive leadership shall:\n- Establish and communicate organizational objectives\n- Implement policies and procedures approved by the Board\n- Manage organizational resources effectively\n- Report to the Board on performance, risks, and compliance\n- Foster a culture of ethics, integrity, and compliance\n- Ensure adequate resources for information security'),
            ('7', 'Management Oversight', 'Department heads and managers shall:\n- Oversee day-to-day operations in their areas\n- Ensure policies and procedures are followed\n- Monitor performance and address issues promptly\n- Identify and escalate risks to leadership\n- Develop and train their teams\n- Report to executive leadership regularly'),
            ('8', 'Meetings and Reporting', 'Governance meetings shall occur:\n- Board meetings: Quarterly (minimum)\n- Executive leadership meetings: Monthly\n- Department management meetings: Weekly or bi-weekly\n\nMeetings shall be documented with minutes, decisions, and action items.'),
            ('9', 'Information Security Oversight', 'The Board and executive leadership shall:\n- Review information security metrics quarterly\n- Approve information security policies and budget\n- Receive reports on security incidents and breaches\n- Ensure adequate cybersecurity insurance\n- Oversee third-party security risk management\n- Review audit findings and remediation plans'),
            ('10', 'Performance Monitoring', 'Management shall monitor:\n- Progress toward organizational objectives\n- Key performance indicators (KPIs)\n- Financial performance and budget compliance\n- Risk levels and control effectiveness\n- Compliance with policies and regulations\n- Employee performance and engagement'),
            ('11', 'Ethics and Compliance', 'The governance structure shall ensure:\n- Code of Conduct is enforced\n- Ethics violations are reported and addressed\n- Whistleblower protections are maintained\n- Conflicts of interest are disclosed and managed\n- Compliance program is effective'),
            ('12', 'Document Retention', 'Governance documents shall be retained:\n- Board meeting minutes: Permanently\n- Executive meeting minutes: 7 years\n- Financial reports: 7 years\n- Audit reports: 7 years\n- Policy approval records: Duration + 7 years'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-038-G',
        'title': 'Employee Performance Management Policy',
        'category': 'HR & Personnel',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CEO, HR Manager',
        'sections': [
            ('1', 'Introduction', 'Effective performance management ensures employees understand expectations, receive feedback, and have opportunities for growth and development at Cirque Corporation.'),
            ('2', 'Purpose', 'This policy establishes a fair and consistent process for evaluating employee performance, providing feedback, recognizing achievements, and addressing performance issues.'),
            ('3', 'Scope', 'This policy applies to all full-time and part-time employees of Cirque Corporation. Contractors and temporary staff may have similar assessments as defined in their agreements.'),
            ('4', 'Performance Evaluation Cycle', 'Performance evaluations shall be conducted:\n- Annually for all employees\n- At 90 days for new hires (probationary review)\n- More frequently for employees with performance concerns\n- Following significant role changes or promotions'),
            ('5', 'Performance Standards', 'Performance shall be evaluated based on:\n- Achievement of job-specific goals and objectives\n- Quality and timeliness of work\n- Technical competency and skills\n- Communication and collaboration\n- Adherence to company policies and values\n- Security awareness and compliance (for IT roles)\n- Professional development and growth'),
            ('6', 'Goal Setting', 'Managers and employees shall:\n- Establish SMART goals (Specific, Measurable, Achievable, Relevant, Time-bound)\n- Align individual goals with department and company objectives\n- Document goals in writing\n- Review and adjust goals quarterly as needed'),
            ('7', 'Performance Review Process', 'The review process includes:\n- Employee self-assessment\n- Manager evaluation and rating\n- One-on-one review meeting\n- Discussion of accomplishments and areas for improvement\n- Setting goals for next evaluation period\n- Development plan for skill enhancement\n- Documentation in employee file'),
            ('8', 'Performance Ratings', 'Employees shall be rated on a scale:\n- Exceeds Expectations: Consistently performs above requirements\n- Meets Expectations: Consistently performs at required level\n- Needs Improvement: Performance below expectations in some areas\n- Unsatisfactory: Performance significantly below requirements\n\nRatings consider the full evaluation period, not just recent performance.'),
            ('9', 'Continuous Feedback', 'Managers shall provide:\n- Regular informal feedback (ongoing)\n- Recognition of achievements promptly\n- Constructive feedback on areas for improvement\n- Mid-year check-ins (formal or informal)\n- Real-time feedback on critical issues'),
            ('10', 'Performance Improvement Plans', 'For employees rated "Needs Improvement" or "Unsatisfactory":\n- Performance Improvement Plan (PIP) developed\n- Specific performance deficiencies identified\n- Clear expectations and measurable goals set\n- Timeline for improvement (typically 30-90 days)\n- Regular progress reviews conducted\n- HR involved in process\n- Consequences of continued poor performance explained'),
            ('11', 'Recognition and Rewards', 'High performers shall be recognized through:\n- Formal recognition programs\n- Performance-based bonuses or raises\n- Promotion opportunities\n- Professional development opportunities\n- Additional responsibilities and growth opportunities'),
            ('12', 'Documentation', 'All performance management activities shall be documented:\n- Performance evaluations in employee file\n- Goal setting worksheets\n- Performance improvement plans\n- Feedback and coaching sessions\n- Recognition and disciplinary actions'),
            ('13', 'Training for Managers', 'All managers shall receive training on:\n- Conducting effective performance reviews\n- Providing constructive feedback\n- Setting SMART goals\n- Managing difficult conversations\n- Legal compliance in performance management'),
        ]
    },
    {
        'document_id': 'IS-CIRQ-P-039-G',
        'title': 'Information Sharing and Document Repository Policy',
        'category': 'Information Management',
        'division': 'Policy',
        'standard_type': 'Global',
        'version': '1.0',
        'effective_date': 'March 2026',
        'review_date': 'March 2027',
        'approved_by': 'CTO, General Counsel',
        'sections': [
            ('1', 'Introduction', 'Effective information sharing and centralized document management enable Cirque Corporation employees to access the information they need while maintaining security and compliance.'),
            ('2', 'Purpose', 'This policy establishes requirements for the centralized document repository, information sharing practices, and access controls to ensure appropriate availability of corporate information.'),
            ('3', 'Scope', 'This policy applies to all corporate documents, policies, procedures, forms, templates, and other information shared among Cirque employees.'),
            ('4', 'Centralized Document Repository', 'Cirque maintains a centralized document repository for:\n- Corporate policies and procedures\n- Employee handbook and HR documents\n- IT standards and guidelines\n- Forms and templates\n- Training materials\n- Job descriptions\n- Organizational charts\n- Process documentation'),
            ('5', 'Repository Platform', 'The official document repository is located:\n- Primary: SharePoint/OneDrive shared drive\n- Alternative: Designated shared network drive\n- Access: Through company network or VPN\n- URL/Path: [To be specified by IT]'),
            ('6', 'Document Organization', 'Documents shall be organized by:\n- Department or functional area\n- Document type (policy, procedure, form, etc.)\n- Logical folder structure\n- Consistent naming conventions\n- Version control for updated documents\n- Metadata tags for searchability'),
            ('7', 'Document Types and Ownership', 'Document categories include:\n- Policies: Owned by executive leadership, approved by Board/CEO\n- Procedures: Owned by department heads\n- Forms/Templates: Owned by functional area managers\n- Job Descriptions: Owned by HR\n- Training Materials: Owned by respective departments'),
            ('8', 'Access Controls', 'Repository access shall be:\n- All employees: Read access to public documents\n- Managers: Read/write access to their department folders\n- HR: Full access to employee and HR documents\n- IT: Administrative access for management\n- Contractors: Limited access as defined in agreements'),
            ('9', 'Document Publishing Process', 'To publish documents to repository:\n- Document created and reviewed by owner\n- Approved by appropriate authority\n- Verified for accuracy and compliance\n- Uploaded to appropriate folder\n- Superseded versions archived or removed\n- Employees notified of important updates'),
            ('10', 'Version Control', 'All documents shall include:\n- Version number and date\n- Change history or revision log\n- Approval signatures/dates\n- Review/expiration dates\n- Previous versions archived for reference'),
            ('11', 'Information Sharing', 'When sharing corporate information:\n- Use official repository link, not email attachments (when possible)\n- Respect access controls and permissions\n- Do not share confidential documents publicly\n- Follow Data Classification Policy\n- Use secure methods for sensitive information'),
            ('12', 'User Responsibilities', 'All employees shall:\n- Access documents from official repository\n- Report broken links or outdated documents\n- Not maintain personal copies of frequently updated documents\n- Not redistribute documents externally without authorization\n- Provide feedback on document usefulness'),
            ('13', 'Document Review and Maintenance', 'Document owners shall:\n- Review documents at least annually\n- Update documents when processes change\n- Remove or archive obsolete documents\n- Ensure consistency across related documents\n- Maintain current contact information'),
            ('14', 'Training and Communication', 'New employees shall:\n- Be informed of repository location during onboarding\n- Receive training on how to access and use repository\n- Know where to find key documents (handbook, policies, etc.)\n\nRegular reminders sent about repository and key document updates.'),
        ]
    },
]

def create_policies():
    db_path = '/var/www/tracker/assets.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"📝 CREATING FINAL MISSING SOC2 POLICIES")
    print(f"=" * 80)
    
    created_count = 0
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
        created_count += 1
    
    conn.commit()
    
    # Show updated counts
    cursor.execute("SELECT COUNT(*) FROM policy")
    total_policies = cursor.fetchone()[0]
    
    print(f"\n📊 POLICY SUMMARY")
    print(f"=" * 80)
    print(f"Policies Created: {created_count}")
    print(f"Total Policies: {total_policies}")
    
    conn.close()

if __name__ == '__main__':
    create_policies()
