**IS-CIRQ-PR-013-G: Change Management Procedure**

**Document: IS-CIRQ-PR-013-G**

**Standards Name: Change Management Procedure**

**Category: IT Security Related**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to define a standardized and controlled
process for managing all changes to Cirque\'s information systems,
applications, networks, and infrastructure. This procedure aims to
minimize the risks associated with changes, prevent unauthorized
modifications, reduce disruptions to business operations, and ensure
that changes are securely implemented and documented, in accordance with
IS-CIRQ-P-011-G: Operations Security Policy and ISO/IEC 27001:2022 Annex
A.8.2.

**2. Scope**

This procedure applies to all planned changes to Cirque\'s IT
environment that could impact the confidentiality, integrity, or
availability of information assets. This includes, but is not limited
to:

-   Hardware installations, upgrades, or decommissioning (e.g., servers,
    network devices, workstations).

-   Software installations, upgrades, patches, or configurations (e.g.,
    operating systems, applications like Omnify, Cadence, GitLab, Asana,
    Microsoft 365, QuickBooks).

-   Network changes (e.g., firewall rules, routing, VPN configurations).

-   Database schema changes or upgrades.

-   Cloud service configurations or changes.

-   Physical infrastructure changes impacting IT (e.g., power, cooling
    for server rooms).

-   Changes to security controls or policies.

**3. Definitions**

-   **Change:** Any addition, modification, or removal of an authorized,
    planned, or supported IT service or service component.

-   **Change Request (CR):** A formal proposal for a change.

-   **Change Advisory Board (CAB):** A group of stakeholders (e.g., IT
    Manager, Department Managers, relevant technical personnel)
    responsible for reviewing, evaluating, and approving significant
    changes.

**4. Responsibilities**

-   **Change Initiator:** The individual proposing the change.

-   **Change Approver:** The individual(s) authorized to approve a
    change (e.g., IT Manager, CAB).

-   **IT Manager:** Overall owner of this procedure, responsible for
    overseeing the change management process and acting as the primary
    change approver, or leading the CAB.

-   **Implementation Team:** The IT personnel responsible for executing
    the change.

-   **Affected Parties:** Stakeholders (e.g., end-users, department
    heads) who will be impacted by the change.

**5. Procedure**

**5.1. Change Request (CR) Submission** a. All proposed changes shall be
formally submitted via a designated ticketing system (if available) or
an email to the IT Manager, containing the following information: \*
Unique Change Request (CR) ID. \* Date of request. \* Change Initiator.
\* Detailed description of the proposed change. \* Business
justification for the change. \* Expected impact of the change (e.g.,
services affected, users impacted, downtime). \* Risk assessment
(potential security implications, operational risks). \* Resources
required (personnel, time, budget). \* Proposed implementation
date/time. \* Back-out plan (rollback procedure in case of failure). \*
Test plan (how the change will be validated). \* Affected parties to be
notified.

**5.2. Change Review and Assessment** a. The IT Manager (or designated
IT personnel) reviews the CR for completeness and initial feasibility.
b. **Impact Analysis:** A detailed impact analysis is performed to
understand the potential effects on systems, services, and security.
This includes reviewing security implications to ensure the change does
not introduce new vulnerabilities or weaken existing controls. c. **Risk
Assessment:** The risks associated with the change are formally assessed
(referencing the IS-CIRQ-F-001-G: Risk Assessment Register). d.
**Technical Review:** Technical personnel review the proposed change for
technical soundness and adherence to existing architecture/standards.

**5.3. Change Approval** a. **Minor Changes (Low Risk/Impact):** Changes
deemed low risk and low impact (e.g., routine software updates, minor
configuration changes) may be approved directly by the IT Manager. b.
**Major Changes (Medium/High Risk/Impact):** Changes with medium to high
risk or significant business impact (e.g., core system upgrades, network
reconfigurations, changes affecting sensitive data like CAD, ASIC
designs, or customer data) shall require approval from a **Change
Advisory Board (CAB)**, which includes the IT Manager and
representatives from affected business units or executive management as
needed. c. **Documentation:** All approvals (or rejections) shall be
documented in the CR.

**5.4. Change Implementation** a. **Scheduling:** Approved changes are
scheduled in a maintenance window to minimize disruption. b.
**Pre-Implementation Checks:** Before implementation, all necessary
preparations (e.g., backups, verification of pre-requisites) are
completed. c. **Implementation:** The change is implemented according to
the approved plan. All steps are documented. d. **Back-out Plan:** If
issues arise during implementation, the back-out plan is initiated to
restore the system to its previous state. e. **Monitoring:** Systems are
actively monitored during and after the change for any adverse effects.

**5.5. Post-Implementation Review (PIR)** a. **Verification:** After the
change is implemented, the change initiator and/or a designated reviewer
verify that the change achieved its intended outcome without unintended
side effects. b. **Testing:** The test plan is executed to confirm
functionality and security. c. **Documentation Update:** All relevant
documentation (e.g., system configurations, network diagrams,
procedures) is updated to reflect the change. d. **Closure:** The Change
Request is formally closed once the change is verified and documentation
is updated.

**5.6. Emergency Changes** a. In situations requiring immediate action
to resolve critical incidents (e.g., security breach, major system
outage) where standard change procedures cannot be followed, an
**Emergency Change** may be initiated. b. **Authorization:** Emergency
changes must be verbally approved by the IT Manager, or in their
absence, a designated alternate, before implementation. c.
**Documentation (Post-Facto):** All emergency changes must be fully
documented retroactively, immediately after the critical situation is
resolved. This documentation must include the reason for the emergency,
the actions taken, the impact, and post-implementation verification. d.
**Review:** All emergency changes will be reviewed by the IT Manager
and/or CAB to identify lessons learned and improve future processes.

**6. Tools**

-   Ticketing system (if available) for managing Change Requests.

-   Version control systems (**GitLab** for code and configuration
    files).

-   Configuration management tools (**Intune** for endpoints).

**7. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are significant changes to Cirque\'s IT environment, operational
processes, or identified areas for improvement in change management.

**8. Related Documents**

-   IS-CIRQ-P-011-G: Operations Security Policy

-   IS-CIRQ-F-001-G: Risk Assessment Register

-   IS-CIRQ-P-001-G: Information Security Policy

-   IS-CIRQ-PR-015-G: Logging and Monitoring Procedure (for monitoring
    changes
