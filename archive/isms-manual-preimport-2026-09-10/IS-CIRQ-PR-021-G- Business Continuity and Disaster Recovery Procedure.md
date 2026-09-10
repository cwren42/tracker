**IS-CIRQ-PR-021-G: Business Continuity and Disaster Recovery
Procedure**

**Document: IS-CIRQ-PR-021-G**

**Standards Name: Business Continuity and Disaster Recovery Procedure**

**Category: IT Security Related**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to define the structured process for
planning, developing, maintaining, and executing Cirque\'s Business
Continuity (BC) and Disaster Recovery (DR) plans. This procedure aims to
ensure the continued availability of critical business processes and the
recovery of essential information systems and data in the event of a
disruptive incident, minimizing downtime and data loss, in accordance
with IS-CIRQ-P-015-G: Information Security Continuity Policy and ISO/IEC
27001:2022 Annex A.17.

**2. Scope**

This procedure applies to all Cirque departments, critical business
processes, information systems, applications, data, and infrastructure
across all global locations (US, China, Taiwan). It specifically covers
the recovery of servers and fileservers utilizing **Veeam** backup and
replication solutions, including the immediate recovery of physical
servers to virtual machines (P2V).

**3. Definitions**

-   **Business Continuity Plan (BCP):** A comprehensive plan outlining
    how Cirque will maintain or quickly resume mission-critical business
    functions after a disruption.

-   **Disaster Recovery Plan (DRP):** A detailed plan focused on the
    recovery of IT systems and infrastructure following a disruptive
    event.

-   **Recovery Time Objective (RTO):** The maximum tolerable length of
    time that a business process or system can be down after a disaster
    or failure.

-   **Recovery Point Objective (RPO):** The maximum tolerable period in
    which data might be lost from an IT service due to a major incident.

-   **Disaster Recovery Site:** An alternative location where IT
    operations can resume after a primary site disaster.

**4. Responsibilities**

-   **Executive Management:** Overall responsibility for approving
    BCP/DRP strategy, funding, and ensuring adequate resources.

-   **IT Manager (DRP Lead):** Primary responsible for the development,
    implementation, testing, and maintenance of the DRP. Coordinates IT
    recovery efforts.

-   **Business Unit Managers (BCP Owners):** Responsible for identifying
    critical business processes, defining their RTO/RPO, and developing
    their specific continuity plans.

-   **System Administrators/Engineers:** Responsible for implementing
    backup and recovery solutions (e.g., **Veeam**), maintaining
    recovery infrastructure, and executing recovery steps during a
    disaster.

-   **All Personnel:** Responsible for understanding their roles and
    responsibilities in BCP/DRP activation and execution.

**5. Procedure**

**5.1. Business Impact Analysis (BIA) and Risk Assessment** a.
**Identify Critical Processes:** Business Unit Managers shall identify
and document all critical business processes that are essential for
Cirque\'s operation. b. **Define RTO/RPO:** For each critical process,
define the maximum acceptable downtime (RTO) and maximum acceptable data
loss (RPO). This shall be documented (e.g., in IS-CIRQ-F-001-G: Risk
Assessment Register). c. **Identify Dependencies:** Document
dependencies of critical processes on specific information systems,
applications, infrastructure, and personnel. d. **Risk Assessment:**
Conduct a risk assessment to identify potential threats to critical
processes and their supporting IT infrastructure, and evaluate existing
controls and residual risks.

**5.2. Business Continuity Plan (BCP) Development** a. Based on the BIA,
each Business Unit Manager shall develop a BCP detailing: \* Manual
workarounds for critical processes if IT systems are unavailable. \*
Alternate communication methods (e.g., outside normal network). \*
Designated alternative work locations or remote work capabilities. \*
Key personnel roles and contact information during a disruption. \*
Procedures for essential functions that must continue without IT.

**5.3. Disaster Recovery Plan (DRP) Development** a. The IT Manager
shall develop a comprehensive DRP focusing on IT systems recovery,
including: \* **Recovery Team:** Defined roles, responsibilities, and
contact information for the DR team. \* **Critical Systems Inventory:**
A prioritized list of critical servers, applications, and data with
their RTOs/RPOs. \* **Backup and Recovery Strategy:** Detailed
procedures for data backup and restoration using **Veeam** for servers
and fileservers, as per IS-CIRQ-PR-014-G: Backup and Restoration
Procedure. \* **Alternative Site Strategy:** Documentation of the
designated disaster recovery site(s) (e.g., cloud environment,
co-location, warm site). \* **System Recovery Procedures:** Step-by-step
instructions for recovering operating systems, applications, databases,
and network services. \* **Physical-to-Virtual (P2V) Recovery:**
Specific procedures for recovering physical servers as virtual machines
using **Veeam\'s Instant VM Recovery** feature to ensure immediate
operational capability. \* **Network Recovery:** Procedures for
restoring network connectivity at the recovery site. \* **Application
Recovery:** Procedures for restoring and configuring critical
applications. \* **Data Synchronization:** Procedures for synchronizing
data to the recovery environment. \* **Hardware Requirements:** List of
minimum hardware requirements for recovery (if applicable). \* **Vendor
Contacts:** List of critical vendor support contacts.

**5.4. Backup and Data Management** a. All critical data and system
configurations shall be regularly backed up using **Veeam Backup &
Replication**. b. Backups shall be stored securely both on-site and
off-site, with appropriate encryption. c. Backup integrity shall be
verified periodically. d. Retention periods for backups shall be defined
based on data criticality and regulatory requirements.

**5.5. Incident Detection and Declaration of Disaster** a. Critical
incidents that escalate beyond immediate incident response capabilities
and significantly disrupt operations shall trigger the BCP/DRP. b. The
IT Manager, in consultation with Executive Management, is responsible
for declaring a \"Disaster\" and initiating the DRP.

**5.6. DRP Execution (Recovery Phases)** a. **Activation:** The DR team
is convened, and the DRP is activated. b. **Damage Assessment:** Initial
assessment of damage and impact to IT infrastructure. c. **Site
Mobilization:** If an alternate site is required, the team moves to or
activates the designated DR site/cloud environment. d. **Infrastructure
Recovery:** Restore core infrastructure components (e.g., networking,
virtualization platform). e. **System and Data Recovery (Veeam
Specific):** \* Prioritize recovery of systems based on RTO/RPO. \*
Utilize **Veeam Instant VM Recovery** to immediately power on physical
server backups as VMs on the recovery site to meet critical RTOs. \*
Restore other critical servers and fileservers from **Veeam** backups.
\* Restore data volumes and databases. f. **Application Recovery:**
Reconfigure and test critical applications. g. **Testing and
Verification:** Rigorously test all recovered systems and data to ensure
functionality and integrity. h. **User Access Restoration:**
Re-establish user access to systems and applications.

**5.7. Return to Normal Operations (Reversion)** a. Once the primary
site or systems are restored and verified as stable and secure, a
planned reversion process will be executed to return operations from the
DR site to the primary environment. b. This process must also be
detailed in the DRP to ensure a smooth, secure transition without
further disruption or data loss.

**5.8. Testing and Maintenance** a. **Regular Testing:** BCPs and DRPs
shall be tested at least annually, or following significant changes to
IT infrastructure or business processes. Tests may include: \* Tabletop
exercises. \* Component testing (e.g., backup restoration tests,
individual system recovery). \* Full-scale disaster recovery drills
(end-to-end testing of recovery site, **Veeam** recovery processes, and
critical applications). b. **Documentation Updates:** Test results shall
be documented, and any identified gaps or areas for improvement in the
BCP/DRP shall be addressed and incorporated into revised plans
(IS-CIRQ-PR-006-G: Document Control Procedure). c. **Plan Review:**
Review the BCP/DRP at least annually to ensure accuracy, relevance, and
alignment with business objectives and RTO/RPO requirements.

**6. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are significant changes to Cirque\'s IT infrastructure, critical
business processes, or lessons learned from tests or actual incidents.

**7. Related Documents**

-   IS-CIRQ-P-015-G: Information Security Continuity Policy

-   IS-CIRQ-P-014-G: Information Security Incident Management Policy

-   IS-CIRQ-PR-020-G: Incident Response Procedure (Global Core)

-   IS-CIRQ-PR-014-G: Backup and Restoration Procedure

-   IS-CIRQ-P-011-G: Operations Security Policy

-   IS-CIRQ-F-001-G: Risk Assessment Register

-   IS-CIRQ-PR-006-G: Document Control Procedure

-   **Veeam Documentation** (External reference for specific technical
    recovery steps)
