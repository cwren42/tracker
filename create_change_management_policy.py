#!/usr/bin/env python3
"""
Create Change Management Policy for SOC2 compliance.
"""

import sqlite3
from datetime import datetime

def create_change_management_policy():
    """Create Change Management Policy"""
    db_path = '/var/www/tracker/assets.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("📋 CREATING CHANGE MANAGEMENT POLICY")
    print("=" * 80)
    
    # Policy details
    document_id = "IS-CIRQ-P-040-G"
    title = "Change Management Policy"
    category = "Information Security"
    division = "Global"
    standard_type = "Policy"
    version = "1.0"
    effective_date = "2026-03-02"
    review_date = "2027-03-02"
    approved_by = "Chris Wren, CISO"
    
    # Insert policy
    cursor.execute("""
        INSERT INTO policy (
            document_id, title, category, division, standard_type,
            version, effective_date, review_date, approved_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (document_id, title, category, division, standard_type,
          version, effective_date, review_date, approved_by))
    
    policy_id = cursor.lastrowid
    print(f"✅ Created policy: {document_id} - {title} (ID: {policy_id})")
    
    # Define sections
    sections = [
        {
            "number": "1",
            "title": "Purpose",
            "content": """This Change Management Policy establishes the framework for managing changes to Cirque Corporation's information technology infrastructure, applications, and systems in a controlled and systematic manner.

The policy ensures that:
- All changes are properly evaluated for risk and impact
- Changes are tested and approved before implementation
- Changes are documented and communicated to stakeholders
- Changes can be rolled back if necessary
- Service continuity is maintained during change implementation"""
        },
        {
            "number": "2",
            "title": "Scope",
            "content": """This policy applies to all changes affecting:
- Production systems and infrastructure
- Business-critical applications and software
- Security systems and controls
- Network infrastructure and configurations
- Cloud services and platforms
- Database systems and data structures
- IT service management tools
- User access and permissions systems

This policy covers all personnel including:
- IT staff and system administrators
- Software developers and engineers
- Third-party vendors and contractors
- Management and executives
- Any personnel requesting or implementing changes"""
        },
        {
            "number": "3",
            "title": "Roles and Responsibilities",
            "content": """Chief Information Security Officer (CISO):
- Overall accountability for change management policy
- Approval of high-risk and emergency changes
- Review of change management effectiveness

Head of IT:
- Responsible for ensuring changes are made appropriately
- Oversight of change management process
- Final approval authority for standard changes
- Coordination of Change Advisory Board (CAB)

Change Manager:
- Administration of change management system
- Scheduling and coordination of changes
- Facilitation of CAB meetings
- Reporting on change success rates and issues

Change Advisory Board (CAB):
- Review and approval of significant changes
- Assessment of change impacts and risks
- Coordination of change schedules
- Resolution of change conflicts

Change Requesters:
- Submission of accurate change requests
- Provision of complete technical documentation
- Coordination with affected teams
- Communication of change outcomes

Change Implementers:
- Execution of approved changes according to plan
- Documentation of implementation steps
- Testing and verification of changes
- Rollback execution if required"""
        },
        {
            "number": "4",
            "title": "Change Categories",
            "content": """Standard Changes:
- Pre-approved, low-risk, routine changes
- Follow established procedures and documentation
- Examples: password resets, user provisioning, routine patches
- No CAB approval required
- Documented in ticketing system

Normal Changes:
- Changes requiring assessment and approval
- May impact multiple systems or users
- Require testing and rollback plans
- CAB approval required for significant changes
- Examples: application updates, infrastructure modifications, security changes

Emergency Changes:
- Changes required to resolve critical incidents
- Expedited approval process
- CISO or designated authority approval required
- Post-implementation review mandatory
- Examples: security patches for active exploits, critical system failures

Major Changes:
- High-risk changes with significant business impact
- Extended planning and testing period
- Multiple stakeholder approvals required
- Executive management notification
- Examples: data center migrations, major system replacements"""
        },
        {
            "number": "5",
            "title": "Change Request Process",
            "content": """All changes must follow this process:

1. Change Initiation:
- Submit change request in approved ticketing system
- Include detailed description and justification
- Specify affected systems and components
- Identify business impact and urgency

2. Change Assessment:
- Technical feasibility review
- Risk and impact analysis
- Resource and scheduling evaluation
- Security impact assessment
- Compliance verification

3. Change Approval:
- Standard changes: Automatic approval
- Normal changes: Change Manager or CAB approval
- Major changes: CAB and executive approval
- Emergency changes: CISO or delegate approval

4. Change Planning:
- Development of implementation plan
- Creation of rollback procedures
- Identification of testing requirements
- Communication plan to stakeholders
- Scheduling of maintenance window

5. Change Implementation:
- Execution according to approved plan
- Real-time monitoring of systems
- Documentation of all actions taken
- Immediate notification of issues

6. Change Verification:
- Testing of implemented changes
- Validation of expected outcomes
- Confirmation of rollback capability
- Performance monitoring

7. Change Closure:
- Documentation of results
- Closure in ticketing system
- Communication to stakeholders
- Lessons learned capture"""
        },
        {
            "number": "6",
            "title": "Change Documentation Requirements",
            "content": """All change requests must include:
- Detailed description of the change
- Business justification and benefits
- List of affected systems and components
- Technical implementation steps
- Testing and validation procedures
- Rollback plan and procedures
- Risk assessment and mitigation strategies
- Required resources and personnel
- Estimated duration and maintenance window
- Communication plan and stakeholders
- Pre-change and post-change validation criteria
- Dependencies on other systems or changes

Documentation must be maintained in the centralized change management system and retained for a minimum of three years."""
        },
        {
            "number": "7",
            "title": "Testing Requirements",
            "content": """All changes must be tested before production implementation:

Development Environment Testing:
- Initial development and unit testing
- Code review and security analysis
- Documentation of test results

Non-Production Environment Testing:
- Testing in environment mirroring production
- Integration testing with dependent systems
- Performance and load testing
- Security and vulnerability testing
- User acceptance testing when applicable

Production Validation:
- Smoke testing immediately after implementation
- Functionality verification
- Performance baseline comparison
- Monitoring of system logs and alerts
- User confirmation of successful change

Emergency changes may have abbreviated testing with mandatory post-implementation validation."""
        },
        {
            "number": "8",
            "title": "Change Advisory Board (CAB)",
            "content": """The CAB provides oversight and governance for significant changes:

CAB Membership:
- Head of IT (Chair)
- Change Manager
- Security representatives
- Network and infrastructure representatives
- Application development representatives
- Business stakeholders as needed

CAB Meetings:
- Regular meetings held weekly or as needed
- Emergency CAB meetings for urgent changes
- Quorum requirements for decision-making
- Documentation of all decisions and rationale

CAB Responsibilities:
- Review and approve normal and major changes
- Assess change risk and impact
- Coordinate change schedules to minimize conflicts
- Monitor change success rates and trends
- Recommend process improvements
- Escalate high-risk changes to executive management"""
        },
        {
            "number": "9",
            "title": "Emergency Change Process",
            "content": """Emergency changes address critical situations requiring immediate action:

Authorization:
- CISO or designated on-call authority must approve
- Verbal approval acceptable with documentation within 2 hours
- Business justification of emergency status required

Implementation:
- Follow abbreviated but documented process
- Implement minimum necessary changes
- Continuous monitoring during implementation
- Communication to relevant stakeholders

Post-Implementation:
- Full documentation within 24 hours
- Post-implementation review within 5 business days
- CAB retrospective of emergency change
- Permanent solution planned if temporary fix applied
- Update of emergency procedures if needed"""
        },
        {
            "number": "10",
            "title": "Separation of Duties",
            "content": """Change management maintains separation of duties:

Development and Production:
- Developers do not have production access
- Code promotion requires approval process
- Automated deployment tools used when possible
- Production changes logged and auditable

Change Request and Approval:
- Requesters cannot approve their own changes
- Independent review required for changes
- CAB provides independent oversight
- Audit trail maintained for all approvals

Implementation and Verification:
- Different personnel verify changes when possible
- Independent testing team for major changes
- Automated monitoring for change verification
- Management review of high-risk changes"""
        },
        {
            "number": "11",
            "title": "Rollback Procedures",
            "content": """All changes must include documented rollback procedures:

Rollback Requirements:
- Step-by-step rollback instructions
- Rollback decision criteria and thresholds
- Data backup and recovery procedures
- Rollback authorization process
- Estimated rollback duration

Rollback Execution:
- Immediate rollback if critical systems impacted
- Change Manager or CISO authorization for rollback
- Communication of rollback to stakeholders
- Documentation of rollback actions
- Root cause analysis of rollback necessity

Post-Rollback:
- System validation after rollback
- Incident documentation
- Corrective action planning
- Re-planning of change if still required"""
        },
        {
            "number": "12",
            "title": "Change Communication",
            "content": """Effective communication is essential for successful changes:

Before Implementation:
- Notification to affected users and stakeholders
- Maintenance window announcements
- Expected downtime or service impacts
- Contact information for questions or issues

During Implementation:
- Status updates at key milestones
- Immediate notification of issues or delays
- Extended downtime notifications
- Availability of support resources

After Implementation:
- Confirmation of successful completion
- Summary of changes made
- Known issues or limitations
- Instructions for reporting problems
- Thank you to affected users for patience"""
        },
        {
            "number": "13",
            "title": "Change Performance Monitoring",
            "content": """The organization monitors change management effectiveness:

Metrics Tracked:
- Number of changes by category
- Change success rate
- Number of failed changes
- Number of changes requiring rollback
- Emergency change frequency
- Average time to implement changes
- Number of unauthorized changes detected
- Compliance with change procedures

Regular Reviews:
- Monthly reporting on change metrics
- Quarterly CAB effectiveness review
- Annual policy review and update
- Trend analysis and process improvements
- Lessons learned from failed changes

Management receives monthly reports on change performance and any significant issues or trends."""
        },
        {
            "number": "14",
            "title": "Technology Acquisition and Development",
            "content": """All technology acquisition, development, and maintenance is governed by change management:

New Technology Acquisition:
- Must follow change request process
- Security and compliance review required
- Integration impact assessment
- Budget and resource approval
- Vendor risk assessment for third-party products

Software Development:
- Development follows change management procedures
- Code changes tracked in version control
- Peer review and security analysis required
- Promotion through environments controlled
- Production deployment as approved change

System Maintenance:
- Scheduled maintenance as standard changes
- Patch management integrated with change process
- End-of-life system replacements planned as major changes
- Continuous monitoring and optimization"""
        },
        {
            "number": "15",
            "title": "Policy Review and Updates",
            "content": """This policy is reviewed at least annually:

Annual Review:
- Conducted by Head of IT and CISO
- Assessment of policy effectiveness
- Review of change success metrics
- Evaluation against industry best practices
- Update of procedures and requirements as needed

Policy Distribution:
- Updated policy distributed to all IT staff
- Training provided on significant changes
- Management briefing on policy updates
- Policy available in centralized document repository
- Acknowledgment of receipt tracked

Ad-hoc Reviews:
- Reviews triggered by significant incidents
- Reviews triggered by compliance requirements
- Reviews requested by audit findings
- Reviews for organizational changes"""
        },
        {
            "number": "16",
            "title": "Compliance and Enforcement",
            "content": """Compliance with this policy is mandatory:

Monitoring:
- Regular audits of change records
- Automated detection of unauthorized changes
- Review of change approval documentation
- Verification of testing and rollback procedures

Violations:
- Unauthorized changes constitute policy violation
- Bypassing approval process not permitted
- Disciplinary action for policy violations
- Possible termination for serious violations

Exceptions:
- Rare exceptions only with CISO approval
- Emergency situations with retroactive documentation
- Documented business justification required
- Exception review and closure mandatory"""
        }
    ]
    
    # Insert sections
    for order, section in enumerate(sections, start=1):
        cursor.execute("""
            INSERT INTO policy_section (
                policy_id, section_order, section_number, 
                section_title, section_content
            ) VALUES (?, ?, ?, ?, ?)
        """, (policy_id, order, section["number"], 
              section["title"], section["content"]))
    
    print(f"✅ Created {len(sections)} sections")
    
    # Map to controls
    control_mappings = [
        9,   # Change Management: Application/Software
        10,  # Change Management: Emergency Process
        11,  # Change Management: Infrastructure
        12,  # Change Management Policy
        13,  # Change Management: Separation of Duties
        14   # Change Management: Ticketing System
    ]
    
    for control_id in control_mappings:
        cursor.execute("""
            INSERT INTO policy_control_mapping (policy_id, control_id)
            VALUES (?, ?)
        """, (policy_id, control_id))
    
    print(f"✅ Mapped to {len(control_mappings)} controls")
    
    conn.commit()
    conn.close()
    
    print("\n" + "=" * 80)
    print(f"✅ Change Management Policy created successfully!")
    print(f"   Document ID: {document_id}")
    print(f"   Policy ID: {policy_id}")
    print(f"   Sections: {len(sections)}")
    print(f"   Controls mapped: {len(control_mappings)}")
    print("\n📝 Next steps:")
    print("   1. Run generate_policy_pdfs.py to create PDF")
    print("   2. Run map_policy_evidence_to_controls.py to create evidence entries")

if __name__ == '__main__':
    create_change_management_policy()
