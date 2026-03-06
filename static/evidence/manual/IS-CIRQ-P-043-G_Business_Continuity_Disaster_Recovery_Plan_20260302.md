# Business Continuity/Disaster Recovery Plan

**Document Number:** IS-CIRQ-P-043-G  
**Document Title:** Business Continuity and Disaster Recovery Plan  
**Version:** 1.0  
**Effective Date:** March 2, 2026  
**Owner:** Chris Wren, CISO  
**Review Frequency:** Annually  

---

## Document Classification

| Stand Alone Document? | Typical Viewing Audience | Employee Signature Required? | Typical Review Cadence |
| :---: | :---: | :---: | :---: |
| Yes | Viewable to Managers and up | No | Annually |

---

## Table of Contents

1. [Introduction](#introduction)
2. [Definition of a Disaster](#definition-of-a-disaster)
3. [Purpose & Scope](#purpose-scope)
4. [Disaster Recovery Roles & Responsibilities](#disaster-recovery-roles-responsibilities)
5. [Disaster Recovery Communications](#disaster-recovery-communications)
6. [Data and Backups](#data-and-backups)
7. [Dealing with a Disaster](#dealing-with-a-disaster)
8. [Plan Testing & Maintenance](#plan-testing-maintenance)
9. [Appendix A: Organization Chart](#appendix-a-organization-chart)
10. [Appendix B: Employee Contact Information](#appendix-b-employee-contact-information)
11. [Appendix C: Critical Service Providers](#appendix-c-critical-service-providers)

---

## Introduction {#introduction}

This Business Continuity and Disaster Recovery Plan ("BC/DR Plan") captures the information that describes Cirque Corporation's ability to withstand a disaster as well as the processes that must be followed to achieve disaster recovery.

This plan is designed to ensure business continuity and minimize operational disruption in the event of a disaster affecting Cirque Corporation's IT infrastructure, facilities, or personnel. The plan establishes clear roles, responsibilities, and procedures to ensure rapid recovery and resumption of critical business operations.

---

## Definition of a Disaster {#definition-of-a-disaster}

A disaster can be caused by man or nature and results in Cirque Corporation's IT network being down for an extended period, or creates conditions that prevent normal business operations. The company defines disasters as the following:

* **One or more vital systems are non-functional for an extended period (> 4 hours)**
* **The building is available, but all systems are non-functional**
* **The building and all systems are non-functional**
* **An event which adversely impacts the health or safety of our personnel or the public**
* **A cybersecurity incident that compromises critical systems or data**

The following events can result in a disaster, requiring this BC/DR Plan to be activated:

* **Fire, flooding, earthquake, blizzard, or other environmental event**
* **War/Terrorist Attack**
* **Theft or vandalism affecting critical infrastructure**
* **Pandemic or public health emergency**
* **Ransomware attack or major cybersecurity breach**
* **Extended power outage or utility failure**
* **Critical vendor service disruption**
* **Any event which puts the health or safety of our personnel or the public at risk**

---

## Purpose & Scope {#purpose-scope}

In the event of a disaster, the first priority of Cirque Corporation is to prevent the loss of life; Cirque will ensure that all employees, contractors, and any other individuals on the premises are safe and secure. The next goal is to bring the company back to business-as-usual as quickly as possible.

### Recovery Time Objectives (RTO)

Cirque Corporation has established the following Recovery Time Objectives:

| System/Service | RTO | Priority |
| :---- | :---- | :---- |
| Email/Communication Systems (Microsoft 365) | 2 hours | Critical |
| Production Web Servers | 4 hours | Critical |
| Database Systems (SQL Server, PostgreSQL) | 4 hours | Critical |
| Azure Cloud Infrastructure | 4 hours | Critical |
| File Servers and Shared Storage | 8 hours | High |
| Development/QA Environments | 24 hours | Medium |
| Administrative Systems | 48 hours | Low |

### Recovery Point Objectives (RPO)

Cirque Corporation has established the following Recovery Point Objectives:

| Data Type | RPO | Backup Frequency |
| :---- | :---- | :---- |
| Production Databases | 1 hour | Continuous replication + hourly backups |
| Critical Business Data | 4 hours | Every 4 hours |
| User Files and Documents | 24 hours | Daily |
| System Configurations | 24 hours | Daily |
| Development/QA Data | 7 days | Weekly |

The Cirque Corporation BC/DR Plan is focused on network & server infrastructure, data storage, backup systems, cloud services, and other IT devices such as end-user computers. This BC/DR Plan does not take into full consideration any non-IT, personnel, Human Resources, and real estate-related disasters, though coordination with these functions is addressed where necessary.

---

## Disaster Recovery Roles & Responsibilities {#disaster-recovery-roles-responsibilities}

In the event of a disaster, employees (which hereafter also includes contractors) will be required to assist Cirque Corporation Management in the restoration of IT systems to restore normal functionality. The following roles and responsibilities reflect the tasks that individuals will have to perform.

### Executive Team {#executive-team}

The Executive Team (CEO and Senior Leadership) will be responsible for making all decisions related to the Disaster Recovery efforts.

**Team Members:**
- CEO (Chief Executive Officer)
- CFO (Chief Financial Officer)
- CTO (Chief Technology Officer)
- Chris Wren, CISO (Chief Information Security Officer)

**The Executive Team will:**
- Officially declare Cirque Corporation in a disaster state
- Consider all information to assess how long of a delay in resumption of service will be acceptable
- Approve expenditures necessary for disaster recovery
- Sign all contractual agreements necessary for resumption of service (Appendix C)
- Communicate with customers, partners, and stakeholders as needed
- Coordinate with legal counsel regarding contractual obligations and liabilities
- Make decisions regarding temporary relocation or remote work arrangements
- Determine when normal operations can safely resume

### IT Team {#it-team}

The IT Team will report to the CISO (Chris Wren) and will be responsible for assessing damage specific to any network infrastructure and for re-establishing network and server functionality. Refer to Appendix A for team members.

**Primary Contact:** Chris Wren (chris.wren@cirque.com)

**The IT Team will:**
- Assess the extent of IT infrastructure damage or disruption
- Reestablish network services either at a secondary location or in the primary office location (Appendix C)
- Determine which servers or services are not functioning and reestablish functionality
- Restore data from backups (Azure Backup, database replication, file backups)
- Order equipment necessary to re-establish services
- Ensure that information security practices continue to be carried out
- Monitor for cybersecurity threats during recovery operations
- Install and implement any tools, hardware, software, and systems
- Coordinate with cloud service providers (Microsoft Azure, Microsoft 365)
- Verify data integrity after restoration
- Document all recovery actions and timeline
- Provide regular status updates to Executive Team

**IT Infrastructure Priorities:**
1. Restore communication systems (Microsoft 365, Teams, Email)
2. Restore internet connectivity and VPN access
3. Restore production web servers and load balancers
4. Restore database systems (SQL Server, PostgreSQL)
5. Restore file servers and shared storage
6. Restore end-user workstation access
7. Restore development and QA environments

### Operations Team {#operations-team}

The Operations Team will report to the COO/Executive Team and will provide employees with the tools they need to perform their roles as quickly and efficiently as possible. The Operations Team will be made up of department leaders across the organization. Refer to Appendix A.

**The Operations Team will:**
- Ensure that employees know where to work (primary office, secondary location, or remote)
- Ensure that telephone & Internet services are (re)established (Appendix C)
- Maintain the conference call line and schedule, should it be needed
- Ensure supplies are provisioned appropriately in the event of a disaster
- Ensure sufficient computer and laptop resources are on hand so that work can resume
- Coordinate workspace setup at alternate locations if needed
- Manage employee transportation and lodging if required
- Coordinate with HR regarding employee welfare and status
- Maintain communication with customers during the disaster recovery period
- Track employee availability and readiness to resume work

### Security and Compliance Team {#security-team}

The Security and Compliance Team will report to the CISO and will be responsible for ensuring that security controls remain in place during disaster recovery and that any security incidents are properly managed.

**Primary Contact:** Chris Wren, CISO (chris.wren@cirque.com)

**The Security Team will:**
- Assess whether the disaster involved a security breach or cyberattack
- Ensure that recovered systems are free from malware or compromise
- Monitor for opportunistic attacks during the recovery period
- Ensure compliance with data protection regulations during recovery
- Coordinate with law enforcement if criminal activity is suspected
- Document the incident for audit and compliance purposes
- Notify affected parties as required by GDPR, CalOPPA, or other regulations
- Review and update security controls based on lessons learned

---

## Disaster Recovery Communications {#disaster-recovery-communications}

Cirque Corporation will make use of Microsoft Teams and email to ensure that appropriate individuals are contacted in a timely manner. If that platform is unavailable, telephone communication (mobile phones) and SMS will be used. Refer to Appendix B: Employee Contact Information.

### Emergency Contact Procedures

**When a disaster has been identified where emergency authorities need to be contacted (via a 911 call), any employee may inform appropriate authorities.** This includes any threat to the health or safety of individuals, colleagues, or the general public.

After authorities have been contacted (if applicable), the communication tree goes into effect:

### Communication Tree

#### Level 1: Initial Notification

Any employee may initiate the communication tree after contacting authorities or upon discovering a disaster condition. The first contact should be made to one of the following individuals (in priority order):

1. **Chris Wren, CISO** - Primary Contact  
   - Mobile: [REDACTED - See Appendix B]
   - Email: chris.wren@cirque.com

2. **CEO** - Secondary Contact  
   - Mobile: [REDACTED - See Appendix B]
   - Email: [REDACTED - See Appendix B]

3. **CTO** - Tertiary Contact  
   - Mobile: [REDACTED - See Appendix B]
   - Email: [REDACTED - See Appendix B]

These phone numbers should be stored on each employee's personal cell phone under a single "Cirque ICE" (In Case of Emergency) contact for quick reference.

#### Level 2: Leadership Notification

The Point Person (first executive contacted) will notify the Executive Team:
- CEO
- CFO
- CTO
- CISO
- VP of Operations
- VP of Engineering
- VP of Sales/Marketing

The Point Person must obtain confirmation from the CEO or designee that Cirque Corporation has incurred a disaster and this Disaster Recovery Plan is now in effect.

#### Level 3: Department Heads

Executive Team members notify their direct reports/department heads:
- Engineering managers
- Operations managers
- Customer support managers
- Finance/HR managers
- Marketing managers

#### Level 4: All Employees

Department heads notify their teams. If teams cannot be reached via corporate email or Teams:
- Use personal email addresses (maintain backup contact list)
- Use SMS/text messaging
- Use phone calls

### Out of Area Contact

In the event of a catastrophic regional event, an out-of-area contact has been designated:

**Out of Area Contact:** [Name of employee in different geographic region]  
**Phone:** [REDACTED - See Appendix B]  
**Email:** [REDACTED - See Appendix B]

### Communication Templates

#### Initial Notification Template

**Subject: URGENT - Cirque Corporation Disaster Declaration**

Team,

This is an official notification that Cirque Corporation has experienced a [TYPE OF DISASTER] and has activated our Business Continuity/Disaster Recovery Plan.

**Current Status:**
- [Brief description of situation]
- [Building accessibility status]
- [System availability status]

**Immediate Actions:**
- [Where to work from]
- [What systems are available]
- [Next update time]

**Safety:** The safety of our employees is our top priority. If you have any safety concerns, please contact [NAME] immediately at [NUMBER].

More information will be provided as it becomes available.

[NAME], [TITLE]

### Customer Communication

The VP of Sales/Marketing will be responsible for customer communications during a disaster:

**Customer Communication Checklist:**
- Post status update on company website
- Update customer support phone system voicemail
- Send email to active customers (if email is available)
- Post updates on social media channels
- Provide regular status updates every 4 hours minimum
- Document all customer communications for legal/compliance purposes

### Stakeholder Communication

The CEO/CFO will be responsible for communicating with key stakeholders:
- Board of Directors
- Investors
- Insurance providers
- Legal counsel
- Regulatory bodies (if required)
- Key partners/vendors

---

## Data and Backups {#data-and-backups}

Data backup and restoration procedures are critical to Cirque Corporation's disaster recovery capabilities. Detailed data restoration procedures are described in the following documents:

### Backup Infrastructure

**Primary Backup Systems:**

1. **Microsoft Azure Backup**
   - Location: Azure West US 3 Region
   - Backup Frequency: Continuous replication for critical VMs
   - Retention: 30 days (daily), 12 months (monthly)
   - Data: Azure Virtual Machines, Azure SQL Database

2. **Database Backups**
   - SQL Server: Full backup daily, differential every 4 hours, transaction log every hour
   - PostgreSQL: Full backup daily, incremental every 4 hours
   - Retention: 30 days local, 1 year offsite
   - Location: Azure Blob Storage (geo-redundant)

3. **File Server Backups**
   - Microsoft OneDrive/SharePoint: Automatic versioning and 93-day retention
   - Windows File Server: Daily incremental, weekly full
   - Retention: 30 days
   - Location: Azure Backup

4. **Workstation Backups**
   - Method: Microsoft OneDrive for Business (automatic sync)
   - Critical user data backed up continuously
   - 93-day version history

5. **Configuration Backups**
   - Network device configurations: Daily backup
   - Azure infrastructure as code: Version controlled in Git
   - Documentation: SharePoint with version control

### Backup Verification

- Monthly backup restoration tests on non-production systems
- Quarterly full disaster recovery simulation
- Annual full-scale disaster recovery test

### Data Restoration Priorities

**Priority 1 (0-4 hours):**
- Email and communication systems
- Production databases
- Identity and authentication systems (Azure AD)

**Priority 2 (4-8 hours):**
- Production web servers and applications
- File servers and shared storage
- Customer-facing systems

**Priority 3 (8-24 hours):**
- Development and QA environments
- Non-critical business applications
- Historical/archival data

### Detailed Restoration Procedures

Detailed step-by-step restoration procedures for each system are documented in:

**Location:** SharePoint > IT Documentation > Disaster Recovery > System Restoration Procedures  
**Link:** https://cirque.sharepoint.com/sites/IT/DR/Restoration

Includes:
- Azure VM restoration procedures
- SQL Server database restoration
- PostgreSQL database restoration
- File server restoration
- Active Directory/Azure AD restoration
- Network infrastructure restoration
- End-user workstation provisioning

---

## Dealing with a Disaster {#dealing-with-a-disaster}

### Disaster Declaration

Once the Executive Team has determined that a disaster has occurred, the CEO or designee must officially declare that the company is in an official state of disaster and initiate the communication tree if it has not yet been initiated.

### Safety Assessment

Before any employees from Cirque Corporation can enter the primary facility after a disaster, appropriate authorities must first ensure that the premises are safe to enter. This includes:
- Fire department clearance (if fire-related)
- Structural engineering assessment (if earthquake/structural damage)
- HAZMAT clearance (if chemical/environmental)
- Law enforcement clearance (if criminal activity)
- Building management clearance

### Employee Notification

Employees will be notified using the most practical means available (Cirque email, Microsoft Teams, mobile phone, personal email, conference call). As soon as all information can be collected, a company-wide communication will go out from a member of the Executive Team (or a designee) summarizing the following:

- Whether it is safe for them to come into the office
- Where they should go if they cannot come into the office
- Which services are still available to them
- Work expectations of them during the disaster
- Timeline for next update
- Who to contact with questions or concerns

### Alternate Work Locations

Cirque Corporation has identified the following alternate work locations should the primary facility be unavailable:

**Primary Office:** [Address]

**Alternate Locations:**

1. **Work from Home (Remote Work)**
   - All employees equipped with laptops and VPN access
   - Microsoft 365 cloud services accessible from anywhere
   - Teams/Zoom for video conferencing
   - Priority: Default alternate location for most employees

2. **Secondary Office Location** (if available)
   - Location: [Address or "To be determined based on disaster location"]
   - Capacity: [Number] employees
   - Facilities: Internet, power, workspace

3. **Co-Working Space/Hotel Conference Facilities**
   - Multiple options in [City] area
   - Can be arranged within 24-48 hours
   - Reserved through Regus/WeWork or similar services

4. **Vendor/Partner Facilities**
   - Potential partners identified: [List if applicable]
   - Mutual aid agreements in place: [Yes/No]

### Post-Disaster Roles and Responsibilities

**CISO (Chris Wren):**
- Lead IT disaster recovery efforts
- Coordinate with cloud service providers (Microsoft, Azure)
- Oversee data restoration from backups
- Ensure security controls remain in place
- Provide regular status updates to Executive Team (every 2-4 hours)
- Document all recovery activities for audit trail

**VP of Sales/Marketing:**
- Inform customers of the disaster and impact on service delivery
- Post updates on company website "Status" page
- Update customer support phone system with current status
- Manage social media communications
- Maintain password-protected list of key customer contacts in OneDrive
- Coordinate with customer account managers

**VP of Customer Service:**
- Inform customers of the disaster and impact on service availability
- Post updates on "Contact Us" page of website
- Update automated voicemail message on customer support line
- Set up call forwarding to mobile phones or alternate numbers
- Track customer issues during disaster recovery
- Escalate critical customer issues to executive team

**VP of Engineering:**
- Assess impact on product development and release schedules
- Coordinate with IT on restoring development environments
- Communicate timeline changes to customers/stakeholders
- Support IT team with application-specific recovery needs

**VP of Operations:**
- Coordinate alternative work locations
- Manage facilities and logistics
- Ensure employee access to necessary resources
- Coordinate with building management/landlord
- Work with HR on employee welfare

**HR/Finance:**
- Ensure employee safety and welfare
- Coordinate payroll processing if systems are down
- Manage employee communications regarding time tracking, benefits
- Coordinate with insurance providers
- Track disaster-related expenses for insurance claims

### IT Recovery Process

The IT Team will work to restore IT functionality either at the primary business location or a secondary location as determined by the Executive Team. The following resources will be utilized to support this effort:

**Documentation Resources:**

1. **Current System Architecture**
   - Location: SharePoint > IT Documentation > Architecture
   - Includes: Network diagrams, server inventory, cloud infrastructure diagrams
   - Link: https://cirque.sharepoint.com/sites/IT/Architecture

2. **Minimum Required System Components**
   - Document: "Critical Systems Inventory"
   - Location: SharePoint > IT Documentation > Disaster Recovery
   - Details: Minimum hardware, software, and bandwidth requirements

3. **Vendor Contact Information**
   - Document: Appendix C of this plan
   - Includes: Account numbers, support contacts, SLAs

4. **Recovery Procedures**
   - Document: "System Restoration Procedures" (referenced in Data and Backups section)
   - Step-by-step procedures for each system type

### Recovery Status Tracking

The IT Team will maintain a recovery status dashboard tracking:
- Systems recovered / Total systems
- Data restoration progress
- Outstanding issues and blockers
- Estimated time to full recovery
- Resource needs (personnel, equipment, budget)

Status updates provided to Executive Team every 2-4 hours during active recovery.

### Return to Normal Operations

Criteria for declaring end of disaster state:
- All Priority 1 systems restored and stable (RTO met)
- Data integrity verified
- Security controls validated
- Employees able to perform critical job functions
- Customer-facing systems operational
- Executive Team approval

Post-recovery activities:
- Conduct post-mortem meeting within 1 week
- Document lessons learned
- Update BC/DR plan based on experience
- Submit insurance claims
- Thank employees and vendors for recovery efforts
- Communicate "all clear" to customers and stakeholders

---

## Plan Testing & Maintenance {#plan-testing-maintenance}

This BC/DR Plan will be updated annually or any time a major system update or upgrade is performed. The CISO (Chris Wren) will be responsible for updating the document with the assistance of IT personnel and third parties familiar with Cirque Corporation's IT environment.

### Testing Schedule

**Quarterly (Every 3 months):**
- Backup restoration test (random system selection)
- Communication tree verification (test call)
- Contact information updates

**Semi-Annually (Every 6 months):**
- Tabletop exercise with key stakeholders
- Review and update vendor contact information (Appendix C)
- Review and test alternate work location plans

**Annually:**
- Full disaster recovery simulation (one full work day)
- Complete review and update of BC/DR Plan
- Executive-level tabletop exercise
- Third-party assessment (if budget allows)

### Tabletop Exercise Procedure

Team members verbally walk through the specific steps as documented in the plan to confirm design effectiveness, identify gaps, bottlenecks, or other weaknesses. The BC/DR Plan may be updated as a result of the tabletop exercise.

**Tabletop Exercise Scenarios:**
1. Ransomware attack encrypting all file servers
2. Fire in primary data center room
3. Regional power outage lasting 48+ hours
4. Hurricane/severe weather forcing building closure
5. Key cloud service provider outage (Azure/Microsoft 365)

### Update Triggers

This plan will be updated when:
- New critical systems are deployed
- Office locations change
- Key personnel changes (executives, IT staff)
- Major infrastructure changes (cloud migration, new services)
- After any actual disaster or major incident
- After testing identifies plan deficiencies
- Annually (scheduled review)

### Document Availability

This plan and documents referenced within it will be available independent of Corporate servers. Copies will reside at the following locations:

**Primary:** SharePoint Online (cloud-based, accessible from anywhere)  
Link: https://cirque.sharepoint.com/sites/ISMS/BCDR

**Secondary:** Printed copy in safe at primary office location

**Tertiary:** Copy provided to each Executive Team member (secure storage)

**Backup:** PDF copy stored in CISO's personal OneDrive account

**Tracker:** https://tracker.corp.cirque.com/soc2/strikegraph (evidence repository)

---

## Revision History

| Revision Date | Action | Approver |
| :---- | :---- | :---- |
| March 2, 2026 | Initial Draft | Chris Wren, CISO |
| March 2, 2026 | Review and Approval | [Pending] CEO |
| | Next Review Due | March 2, 2027 |

---

## Appendix A: Organization Chart {#appendix-a-organization-chart}

**Disaster Recovery Organization Structure**

```
CEO (Chief Executive Officer)
├── Executive Team (Decision Making Authority)
│   ├── CEO
│   ├── CFO (Chief Financial Officer)
│   ├── CTO (Chief Technology Officer)
│   └── CISO - Chris Wren (IT Disaster Recovery Lead)
│
├── IT Recovery Team (Reports to CISO)
│   ├── IT Operations Manager
│   ├── Network Administrator
│   ├── Systems Administrator
│   ├── Database Administrator
│   ├── Cloud Infrastructure Engineer
│   └── Security Analyst
│
├── Operations Team (Reports to COO/Executive Team)
│   ├── VP of Operations
│   ├── Facilities Manager
│   ├── Office Manager
│   └── Administrative Staff
│
├── Communications Team
│   ├── VP of Sales/Marketing (Customer Communications)
│   ├── VP of Customer Service (Support Communications)
│   └── HR Director (Employee Communications)
│
└── Security & Compliance Team (Reports to CISO)
    ├── Information Security Team
    ├── Compliance Officer
    └── Legal Counsel (External)
```

**Key Contacts:**

| Role | Name | Primary Phone | Email | Backup Contact |
| :---- | :---- | :---- | :---- | :---- |
| CEO | [Name] | [Mobile] | [Email] | CFO |
| CISO (IT Lead) | Chris Wren | [Mobile] | chris.wren@cirque.com | CTO |
| CTO | [Name] | [Mobile] | [Email] | CISO |
| CFO | [Name] | [Mobile] | [Email] | CEO |
| VP Operations | [Name] | [Mobile] | [Email] | COO |
| VP Engineering | [Name] | [Mobile] | [Email] | CTO |
| VP Sales/Marketing | [Name] | [Mobile] | [Email] | CEO |
| HR Director | [Name] | [Mobile] | [Email] | CFO |

Full organization chart available at: [Link to SharePoint org chart]

---

## Appendix B: Employee Contact Information {#appendix-b-employee-contact-information}

**Comprehensive employee contact information maintained separately due to privacy and security considerations.**

**Location:** SharePoint > HR > Employee Directory > Emergency Contact Information

**Access:** Restricted to HR, Executive Team, and authorized personnel

**Content Includes:**
- Employee name
- Job title
- Department
- Mobile phone number
- Personal email address
- Emergency contact person
- Emergency contact phone number
- Home address (for welfare checks if needed)

**Maintenance:**
- Updated quarterly by HR
- Employees responsible for notifying HR of changes
- Verified during annual benefits enrollment

**Backup Copies:**
- HR Director (printed copy in secure location)
- CISO (encrypted digital copy)
- CEO (printed copy in secure location)

**Emergency Access:**
If primary contacts cannot access employee information, contact:
- HR Director: [Phone]
- HR Information System: [Access instructions if available remotely]

---

## Appendix C: Critical Service Providers {#appendix-c-critical-service-providers}

| Service Provided | Provider | Phone Number/Email | Account # / Name on File | Notes |
| :---- | :---- | :---- | :---- | :---- |
| **Cloud Infrastructure** | Microsoft Azure | 1-800-867-1389 | Subscription ID: [REDACTED] | West US 3 region, 24/7 support |
| **Email/Collaboration** | Microsoft 365 | 1-800-865-9408 | Tenant: cirque.com | Premier support available |
| **Network Consultants** | [Vendor Name] | [Phone] | Account: [Number] | 24/7 emergency support |
| **Internet Service Provider (Primary)** | [ISP Name] | [Phone] | Account: [Number] | Business fiber 1Gbps |
| **Internet Service Provider (Backup)** | [ISP Name] | [Phone] | Account: [Number] | Backup connection |
| **Cloud Backup** | Azure Backup | Via Azure Portal | Subscription: [REDACTED] | Geo-redundant storage |
| **Telephones/VoIP** | [Provider Name] | [Phone] | Account: [Number] | Call forwarding available |
| **Mobile Phones** | [Carrier Name] | [Phone] | Account: [Number] | Corporate account |
| **Laptop/Hardware Vendor** | Dell/Lenovo/HP | [Phone] | Account: [Number] | Next business day support |
| **Software Licensing** | Microsoft (EA) | Via Microsoft portal | EA Number: [REDACTED] | Enterprise Agreement |
| **Security Tools** | [Vendor Names] | [Phones] | Accounts: [Numbers] | Antivirus, EDR, SIEM |
| **Landlord/Building Management** | [Company Name] | [Phone] | Suite/Unit: [Number] | Building access, HVAC, power |
| **Electrician (Commercial)** | [Company Name] | [Phone] | N/A | Emergency electrical services |
| **HVAC Service** | [Company Name] | [Phone] | N/A | Emergency HVAC repair |
| **Insurance (Property)** | [Insurance Company] | [Phone] | Policy: [Number] | Business interruption coverage |
| **Insurance (Cyber/E&O)** | [Insurance Company] | [Phone] | Policy: [Number] | Cyber incident coverage |
| **Legal Counsel** | [Law Firm] | [Phone] | Client: Cirque Corp | Disaster/contract issues |
| **Payroll Provider** | [Provider Name] | [Phone] | Account: [Number] | Emergency payroll processing |
| **Bank (Primary)** | [Bank Name] | [Phone] | Account: [Number] | Wire transfer capabilities |
| **Equipment Rental** | [Company Name] | [Phone] | N/A | Computers, servers, networking |
| **Co-Working Space** | Regus/WeWork | [Phone] | N/A | Temporary office space |
| **Hotel Conference Facilities** | [Hotel Names] | [Phones] | N/A | Meeting space rental |
| **Document Recovery** | [Company Name] | [Phone] | N/A | Paper/electronic document restoration |
| **IT Emergency Services** | [Company Name] | [Phone] | N/A | 24/7 IT emergency support |
| **Public Relations** | [Firm Name] | [Phone] | N/A | Crisis communications |
| **GitLab (Code Repository)** | GitLab | Support Portal | Account: [Name] | Source code repository |
| **AWS (if used)** | Amazon Web Services | 1-877-850-1895 | Account: [REDACTED] | Alternative cloud resources |

**Service Provider SLA Summary:**

| Provider | Response Time | Resolution Time | Support Hours |
| :---- | :---- | :---- | :---- |
| Microsoft Azure | 1 hour (Severity A) | Varies by issue | 24/7 |
| Microsoft 365 | 1 hour (critical) | Varies by issue | 24/7 |
| ISP (Primary) | 4 hours | 24 hours | 24/7 |
| Network Consultant | 2 hours | Varies by issue | 24/7 emergency |

**Emergency Procurement:**

**Pre-Approved Vendors for Emergency Purchases (up to $10,000 without additional approval):**
- Dell/Microsoft Store: Computers and laptops
- Amazon Business: Networking equipment, cables, peripherals
- Best Buy Business: Emergency consumer electronics
- Office Depot: Office supplies and furniture

**Credit Cards for Emergency Purchases:**
- Corporate credit card (CFO holds)
- CISO corporate card (for IT emergencies)
- Backup: CEO corporate card

**Emergency Budget Authorization:**
- Up to $10,000: CISO or CFO approval
- $10,000 - $50,000: CEO approval
- Over $50,000: CEO + Board notification

---

## Document Control

**Document Owner:** Chris Wren, CISO  
**Document Location:** SharePoint > ISMS > Policies > BC/DR Plan  
**Access Control:** Restricted - Managers and above  
**Distribution:** Executive Team, IT Team, Operations Team, HR  
**Review Cycle:** Annual (or after any disaster/major test)  
**Next Review Date:** March 2, 2027

**Related Documents:**
- IS-CIRQ-P-011-G: Operations Security Policy
- IS-CIRQ-P-014-G: Information Security Incident Management Policy
- IS-CIRQ-P-029-G: Configuration Management Policy
- IS-CIRQ-P-035-G: Patch Management Policy
- System Restoration Procedures (Technical Document)
- Network Architecture Diagrams
- Azure Infrastructure Documentation

---

**END OF DOCUMENT**

---

**Approval Signatures:**

| Role | Name | Signature | Date |
| :---- | :---- | :---- | :---- |
| Document Owner | Chris Wren, CISO | [Pending] | March 2, 2026 |
| Reviewed By | CTO | [Pending] | |
| Approved By | CEO | [Pending] | |

This Business Continuity/Disaster Recovery Plan is a controlled document. Printed copies are uncontrolled and may not reflect the current version. Always refer to the electronic version in SharePoint for the most current version.
