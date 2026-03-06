**IS-CIRQ-PR-014-G: Backup and Restoration Procedure**

**Document: IS-CIRQ-PR-014-G**

**Standards Name: Backup and Restoration Procedure**

**Category: IT Security Related**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to define the systematic process for
performing, managing, and testing backups and restoration of Cirque\'s
critical information and software. This procedure aims to ensure the
availability and integrity of information assets, facilitate recovery
from data loss events (e.g., hardware failure, human error,
cyber-attack), and comply with IS-CIRQ-P-011-G: Operations Security
Policy and ISO/IEC 27001:2022 Annex A.8.13.

**2. Scope**

This procedure applies to all critical information, software, and system
configurations identified as requiring backup within Cirque\'s IT
environment. This explicitly includes:

-   **Servers:** All production servers.

-   **File Server:** The central file server where critical company data
    is stored.

**This procedure does NOT cover backup of individual user laptops or
workstations, which are considered end-user responsibility for personal
files, though company data on them should reside on backed-up network
drives or cloud services.**

**3. Responsibilities**

-   **IT Manager:** Overall owner of this procedure. Responsible for
    defining backup strategies, ensuring backup systems are properly
    configured and monitored, and overseeing restoration tests.

-   **System Administrators / IT Personnel:** Responsible for executing
    backup jobs, monitoring backup success/failure, performing
    restoration tests, and managing backup media.

-   **Department Managers/Asset Owners:** Responsible for identifying
    critical data and systems within their purview that require
    inclusion in backup schedules.

-   **All Personnel:** Responsible for saving critical work-related data
    to designated network drives or approved cloud storage that is
    covered by this backup procedure.

**4. Procedure**

**4.1. Backup Strategy and Scope**

a\. \*\*Critical Data Identification:\*\* Department Managers and Asset
Owners, in conjunction with the IT Manager, shall identify and classify
critical data and systems requiring backup based on their value,
criticality, and impact of loss (refer to \`IS-CIRQ-PR-007-G: Asset
Classification and Handling Procedure\`).

b\. \*\*Backup Scope:\*\* Backups shall cover all data and
configurations on:

\* \*\*All production servers.\*\*

\* \*\*The central file server.\*\*

c\. \*\*Exclusions:\*\* Individual user laptops are excluded from this
centralized backup procedure. Users are responsible for ensuring
critical company data is stored on network drives or approved cloud
services (e.g., SharePoint, GitLab) which are included in backups.

d\. \*\*Backup Tool:\*\* \*\*Veeam\*\* is the designated software for
backing up servers and the file server.

e\. \*\*Retention Periods:\*\* Backup retention periods shall be defined
based on data classification, regulatory requirements, and recovery time
objectives (RTO) / recovery point objectives (RPO).

**4.2. Backup Execution**

a\. \*\*Scheduling:\*\* Backup jobs shall be scheduled to run
automatically using \*\*Veeam\*\*, typically during off-peak hours to
minimize impact on operational systems.

b\. \*\*Types of Backups:\*\* A combination of full, incremental, or
differential backups will be utilized as appropriate to meet RPO/RTO
requirements and optimize storage.

c\. \*\*Media Management:\*\*

\* Backups shall be stored on designated backup media (e.g., network
attached storage (NAS), cloud storage).

\* Critical backups shall be replicated to an off-site location or cloud
storage for disaster recovery purposes.

\* Backup media containing sensitive data shall be encrypted.

d\. \*\*Monitoring:\*\*

\* System Administrators shall regularly monitor \*\*Veeam\*\* for
backup job success or failure.

\* Automated alerts for backup failures shall be configured and
addressed promptly.

**4.3. Data Integrity and Security of Backups**

a\. \*\*Encryption:\*\* All backups containing sensitive or confidential
information shall be encrypted both in transit and at rest.

b\. \*\*Access Control:\*\* Access to backup systems, backup software
(\*\*Veeam\*\*), and backup media shall be strictly controlled on a
need-to-know basis, following \`IS-CIRQ-P-008-G: Access Control Policy\`
and \`IS-CIRQ-PR-009-G: Privileged Access Management Procedure\`.

c\. \*\*Integrity Checks:\*\* Regular integrity checks on backup sets
shall be performed to ensure data can be successfully restored.

**4.4. Restoration Process**

a\. \*\*Restoration Request:\*\* In the event of data loss or system
failure, restoration requests are submitted to the IT Manager or
designated System Administrator.

b\. \*\*Prioritization:\*\* Restoration efforts shall be prioritized
based on the criticality of the system/data and impact on business
operations.

c\. \*\*Restoration Execution:\*\*

\* System Administrators shall use \*\*Veeam\*\* to restore data or
systems from the appropriate backup set.

\* Restoration steps shall be documented.

d\. \*\*Verification:\*\* After restoration, the integrity and
functionality of the restored data/system shall be verified by the IT
team and confirmed by the requesting party.

**4.5. Backup and Restoration Testing**

a\. \*\*Regular Testing:\*\* Restoration procedures shall be regularly
tested at least annually, or more frequently for highly critical
systems.

b\. \*\*Test Scope:\*\* Tests shall involve restoring a representative
sample of data and/or systems from backup media to a non-production
environment.

c\. \*\*Documentation:\*\* Test results, including any issues
encountered and resolutions, shall be documented.

d\. \*\*Review and Improvement:\*\* Test failures shall trigger a review
of backup strategies and procedures to implement necessary improvements.

**4.6. Secure Disposal of Backup Media**

a\. When backup media is no longer required or has reached the end of
its lifecycle, it shall be securely disposed of in accordance with
\`IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure\` to
prevent unauthorized data recovery.

**5. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are significant changes to Cirque\'s IT infrastructure, data
classification, backup tools (**Veeam**), or storage locations.

**6. Related Documents**

-   IS-CIRQ-P-011-G: Operations Security Policy

-   IS-CIRQ-P-007-G: Asset Management Policy

-   IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure

-   IS-CIRQ-P-009-G: Cryptography Policy

-   IS-CIRQ-PR-010-G: Key Management Procedure
