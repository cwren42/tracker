**IS-CIRQ-PR-015-G: Logging and Monitoring Procedure**

**Document: IS-CIRQ-PR-015-G**

**Standards Name: Logging and Monitoring Procedure**

**Category: IT Security Related**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to define the systematic process for
logging, storing, and reviewing events related to Cirque\'s information
systems, applications, and networks. This procedure ensures that
sufficient information is available for audit trails, incident
investigation, problem diagnosis, and compliance monitoring, in
accordance with IS-CIRQ-P-011-G: Operations Security Policy and ISO/IEC
27001:2022 Annex A.8.14.

**2. Scope**

This procedure applies to all Cirque information systems, applications,
network devices, cloud services, and physical access systems that
generate logs of security-relevant events, operational activities, or
user actions. This includes, but is not limited to:

-   **Operating Systems:** Windows Servers, workstations (managed by
    **Intune**).

-   **Network Devices:** Firewalls, switches, wireless access points.

-   **Applications:** Omnify, Cadence, GitLab, Asana, QuickBooks,
    Microsoft 365 services (e.g., SharePoint, Exchange Online).

-   **Directory Services:** Active Directory.

-   **Security Tools:** **Windows Defender for Business**.

-   **Physical Access Control Systems:** **Unifi Access system**.

-   **Cloud Infrastructure:** Azure logs.

**3. Responsibilities**

-   **IT Manager:** Overall owner of this procedure. Responsible for
    defining logging requirements, ensuring logging systems are properly
    configured, and overseeing log review and incident response
    processes related to logs.

-   **System Administrators / IT Personnel:** Responsible for
    configuring systems to generate appropriate logs, ensuring logs are
    collected and stored securely, and performing initial log reviews
    and investigations.

-   **All Personnel:** Responsible for reporting suspicious activities
    or anomalies observed on systems they use.

**4. Procedure**

**4.1. Identification of Logging Requirements** a. The IT Manager, in
conjunction with system owners, shall identify and define the types of
events that must be logged for each system, based on: \* Business
criticality of the system. \* Data classification (Confidential,
Internal Use). \* Legal, regulatory, or contractual requirements. \*
Risk assessments (IS-CIRQ-F-001-G: Risk Assessment Register). b. Key
events to be logged include, but are not limited to: \* All successful
and failed logon attempts. \* Changes to system configuration, security
settings, or access rights. \* Use of privileged accounts. \* Attempts
to access unauthorized resources. \* Execution of critical system
commands. \* Malware detection and quarantine events. \* Physical access
attempts (successful and failed) via **Unifi Access system**.

**4.2. Log Generation and Collection** a. **System Configuration:** All
relevant systems shall be configured to generate necessary logs with
sufficient detail. b. **Centralized Logging (where applicable):** Logs
from critical systems (e.g., servers, network devices, firewalls) should
be forwarded to a centralized log management system or Security
Information and Event Management (SIEM) solution (if implemented) to
facilitate aggregation, correlation, and analysis. c. **Endpoint
Logging:** For workstations and laptops, relevant logs (e.g., security
events, **Windows Defender for Business** alerts) are collected and
monitored via **Intune** and integrated with Microsoft 365 security
features. d. **Cloud Service Logging:** Cirque shall utilize and
configure the native logging capabilities of cloud services (e.g., Azure
Activity Logs, Microsoft 365 Unified Audit Log, GitLab audit logs) to
capture relevant events.

**4.3. Log Storage and Retention** a. **Secure Storage:** Logs shall be
stored securely to prevent unauthorized access, modification, or
deletion. Access to log storage shall be strictly controlled based on
need-to-know. b. **Integrity:** Mechanisms shall be in place to ensure
the integrity and authenticity of logs (e.g., hashing, read-only access
for archived logs). c. **Retention Periods:** Logs shall be retained for
a period consistent with legal, regulatory, and business requirements.
The default retention period for security-relevant logs is \[X\]
days/months/years (e.g., 90 days for operational, 1 year for security
incidents, 7 years for compliance). d. **Time Synchronization:** All
systems shall have their time synchronized to a central, reliable time
source (e.g., NTP server) to ensure accurate timestamps in logs for
correlation purposes.

**4.4. Log Review and Analysis** a. **Regular Review:** Logs shall be
reviewed regularly for security events, operational issues, and
anomalies: \* **Daily:** Critical system logs (e.g., firewall, server
authentication logs), privileged access logs. \* **Weekly/Monthly:**
General system logs, application logs, physical access logs from **Unifi
Access system**. \* **Ad-hoc:** In response to alerts, suspected
incidents, or during investigations. b. **Automated Monitoring and
Alerts:** \* Automated monitoring tools shall be configured to identify
and alert on pre-defined critical security events (e.g., multiple failed
logins, unauthorized access attempts, malware outbreaks detected by
**Windows Defender for Business**, significant changes in **Active
Directory**). \* Alerts shall be escalated to the IT Manager or
designated personnel for immediate investigation. c. **Incident Response
Integration:** Log analysis is a critical component of the Incident
Response process (IS-CIRQ-PR-020-G: Incident Response Procedure - Global Core).

**4.5. Protection of Logging Facilities** a. Logging facilities and log
data shall be protected from tampering and unauthorized access. b. Only
authorized personnel shall have access to configure, review, or modify
logging systems.

**5. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are significant changes to Cirque\'s IT environment, logging tools, or
legal/regulatory requirements.

**6. Related Documents**

-   IS-CIRQ-P-011-G: Operations Security Policy

-   IS-CIRQ-P-008-G: Access Control Policy

-   IS-CIRQ-PR-009-G: Privileged Access Management Procedure

-   IS-CIRQ-F-001-G: Risk Assessment Register

-   IS-CIRQ-P-001-G: Information Security Policy

-   IS-CIRQ-P-010-G: Physical and Environmental Security Policy

-   IS-CIRQ-PR-011-G: Physical Access Control Procedure
