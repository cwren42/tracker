**IS-CIRQ-D-009-G: Disaster Recovery Plan (DRP) - TEMPLATE**

**Document: IS-CIRQ-D-009-G**

**Standards Name: Disaster Recovery Plan (DRP)**

**Category: IT Security Related**

**Division: Document**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 (Template) **Effective Date:** 2025-07-01 **Review
Date:** 2026-07-01 **Approved By:** IT Manager (for actual DRP)

**Purpose:** This document serves as a template and high-level guide for
Cirque\'s comprehensive Disaster Recovery Plan (DRP). The actual DRP
will contain detailed, living operational and technical information
necessary to recover critical IT systems and data after a disruptive
incident, ensuring adherence to IS-CIRQ-P-015-G: Information Security
Continuity Policy and IS-CIRQ-PR-021-G: Business Continuity and Disaster
Recovery Procedure. It specifically integrates the use of **Veeam** for
server and fileserver recovery, including the immediate recovery of
physical servers to virtual machines (P2V).

**Instructions for Use:** This is a template. Populate each section with
specific Cirque technical details, configurations, and team assignments.
The full DRP should be maintained as a highly detailed, operational
document.

**TABLE OF CONTENTS**

1.  **Introduction and Purpose** 1.1. Purpose of the DRP 1.2. Scope
    (Critical IT Systems, Applications, Data, Infrastructure) 1.3.
    Objectives (RTOs for Systems, RPOs for Data) 1.4. Relationship to
    Other Plans (e.g., BCP, Incident Response) 1.5. Assumptions and
    Limitations

2.  **DRP Management and Governance** 2.1. DRP Owner/Coordinator (IT
    Manager) 2.2. Disaster Recovery Team (DRT) - Roles,
    Responsibilities, and Contact Information \* DRT Lead (IT Manager)
    \* Network Team \* Server/Infrastructure Team \* Database Team \*
    Application Team \* Security Team \* Vendor Management Liaison 2.3.
    Activation Authority and Escalation Path 2.4. Maintenance and Review
    Schedule

3.  **Critical Systems and Data Inventory** 3.1. Prioritized List of
    Critical Servers, Applications, and Data \* System Name /
    Application Name \* Ownership / Business Owner \* Server Hostname /
    IP Address \* Associated Data Classification (e.g., Confidential,
    Internal Use) \* Defined RTO (Recovery Time Objective) \* Defined
    RPO (Recovery Point Objective) \* Dependencies (Other Systems,
    Network, Personnel) \* Backup Schedule and Retention (Veeam) \*
    Recovery Method (e.g., Instant VM Recovery, full restore)

4.  **Disaster Recovery Site and Infrastructure** 4.1. Primary
    Production Environment Overview 4.2. Designated Disaster Recovery
    Site(s) (e.g., Cloud Environment - Azure/AWS, Co-location, Warm
    Site) \* Location details \* Connectivity (VPN, dedicated links) \*
    Hardware / Virtualization platform at DR site \* Network
    architecture at DR site (IP schemes, VLANs) \* Power and Cooling
    considerations

5.  **Backup and Restoration Strategy** 5.1. **Veeam Backup &
    Replication Configuration:** \* Backup Jobs Configuration
    (Frequency, Scope) \* Backup Repository Locations (On-site,
    Off-site, Cloud) \* Replication Jobs Configuration (if applicable)
    \* Backup Encryption and Security Measures 5.2. **Data Retention
    Policies:** 5.3. **Backup Verification Procedures:** 5.4. Off-site
    Storage and Media Management

6.  **DRP Activation and Execution Procedures** 6.1. **Declaration of
    Disaster:** \* Criteria for DRP Activation \* Authorization Process
    6.2. **Initial Response and Damage Assessment:** \* Establish
    Incident Command Center (Virtual/Physical) \* Damage Assessment
    Checklist (Infrastructure, Systems, Data) \* Security Assessment of
    the Incident (coordinated with Incident Response) 6.3.
    **Communication Plan:** \* Internal DRT Communication (e.g., secure
    chat, bridge lines) \* External Vendor Communication (e.g., ISP,
    Cloud Provider, Veeam Support) \* Updates to BCP Team / Business
    Unit Managers 6.4. **DR Site Activation and Connectivity:** \*
    Procedures for activating DR site infrastructure. \* Network
    re-configuration (DNS updates, routing changes, VPN establishment).

7.  **System-Specific Recovery Procedures** (Detailed, step-by-step
    instructions for each critical system, aligned with RTO/RPO) 7.1.
    **Core Infrastructure Recovery:** \* Virtualization Platform (e.g.,
    VMware vCenter, Hyper-V) \* Active Directory / DNS \* Networking
    Devices (Routers, Firewalls, Switches) \* Storage Systems 7.2.
    **Server Recovery (Veeam Specific):** \* **Physical Servers to VM
    Recovery (P2V):** Procedures for using Veeam\'s Instant VM Recovery
    to power on physical server backups as VMs on the DR site
    immediately. \* **Virtual Server Recovery:** Procedures for
    restoring virtual servers from Veeam backups. \* **Fileserver
    Recovery:** Procedures for restoring fileservers and shared data. \*
    Detailed steps for each critical server (e.g., SQL Server, ERP
    Server, Omnify Server, Cadence License Server): \* Restore
    prerequisites \* Veeam restore steps (e.g., Instant Recovery, Full
    VM Restore) \* Post-restore configuration (IP addresses, service
    startup) \* Application installation/reconfiguration \* Data
    integrity checks 7.3. **Database Recovery:** \* Specific steps for
    restoring critical databases (e.g., SQL, MySQL). \* Transaction log
    application. \* Database consistency checks. 7.4. **Application
    Recovery:** \* Installation and configuration of critical
    applications (e.g., Omnify, Cadence, GitLab, Asana, QuickBooks). \*
    Integration points and dependencies. \* Functionality testing. 7.5.
    **Data Restoration:** \* Procedures for restoring specific data sets
    based on RPO. \* Validation of restored data. 7.6. **User Access
    Restoration:** \* Re-enabling user accounts and access in the DR
    environment.

8.  **Testing and Maintenance** 8.1. **DRP Testing Schedule:** \* Annual
    full-scale DR drills, including failover and failback. \* Periodic
    component-level tests (e.g., backup recovery tests, individual
    server restore). \* Testing of Veeam recovery processes. 8.2. **Test
    Scenarios:** Define various disruptive scenarios for testing. 8.3.
    **Test Results Documentation:** Record all test results,
    observations, and deviations. 8.4. **Lessons Learned and Corrective
    Actions:** \* Identify weaknesses and areas for improvement. \*
    Assign action items and track their completion. 8.5. **DRP Review
    and Update Cycle:** \* Annual review or after significant changes to
    IT infrastructure, applications, or business processes.

9.  **Return to Normal Operations (Reversion Plan)** 9.1. Criteria for
    Initiating Reversion (Primary site fully restored and verified).
    9.2. Phased Reversion Strategy (e.g., reverse replication,
    controlled switchback). 9.3. Detailed Steps for Returning Operations
    to Primary Site. 9.4. Data Synchronization back to Primary Site.
    9.5. Post-Reversion Verification and Monitoring.

**APPENDICES (EXAMPLES)**

-   Appendix A: DRT Contact List (with primary and secondary numbers,
    emergency contacts)

-   Appendix B: Vendor Contact List (ISP, Hardware, Software, Cloud
    Providers)

-   Appendix C: Network Diagrams (Primary & DR Site)

-   Appendix D: IP Address Assignments for DR Site

-   Appendix E: Application Dependency Matrix

-   Appendix F: System Recovery Checklists

-   Appendix G: Test Scenario Documentation & Results

-   Appendix H: Veeam Configuration Snapshots

-   Appendix I: Software Licenses and Installation Media Locations
