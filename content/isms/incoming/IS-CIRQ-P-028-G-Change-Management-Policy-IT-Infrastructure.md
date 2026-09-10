---
title: "Change Management Policy: IT Infrastructure"
slug: is-cirq-p-028-g
document_type: policy
category: ISMS Manual
version: 0.1
---

---

**Document:** IS-CIRQ-P-028-G  
**Standards Name:** Change Management Policy: IT Infrastructure  
**Category:** IT Security Related  
**Division:** Policy  
**Standard Retention:** Exist and No Corrections  
**Standard Type:** Global  

---

**Version:** 0.1 (DRAFT — pending review and approval) &nbsp;·&nbsp; **Effective Date:** TBC &nbsp;·&nbsp; **Review Date:** TBC (annually) &nbsp;·&nbsp; **Approved By:** IT Manager  

---

| A stand-alone document? | Typical viewing audience | Employee signature required? | Typical review cadence |
|---|---|---|---|
| Yes | All employees (and contractors, where applicable) | No | Annually |

---


## 1. Purpose

Effective change management within Cirque Corporation's IT infrastructure is important to ensuring that we consistently deliver secure, available and reliable services to the business. The goal of this policy and its supporting procedure is to set adequate measures for the personnel who design, build, change and operate that infrastructure, so that changes are deliberate, authorized, tested and reversible.

The goal of documented and company-wide change management is to protect the confidentiality, integrity and availability of information held in Cirque's information systems, and to reduce the likelihood that an infrastructure change introduces a security weakness or an unplanned outage.

In the context of this policy, a **change** is the deliberate addition, modification, or removal of approved, supported or baselined IT infrastructure, or of its configuration, where that change could affect the confidentiality, integrity or availability of information assets.

## 2. Scope

This policy applies to Cirque's IT infrastructure — the systems and platforms operated by IT to run and support the business. It is implemented through **IS-CIRQ-PR-013-G: Change Management Procedure**. In scope:

- **Network infrastructure** — the UniFi controller and its managed switches and access points; firewall zones, policies and rules; VLANs, routing and DNS/DHCP; and the SSTP VPN concentrators.
- **Server and virtualisation infrastructure** — the Proxmox VE cluster and its hosts, virtual machines and containers; and the Proxmox Backup Server.
- **Directory and identity infrastructure** — domain controllers, Active Directory configuration, Group Policy, directory synchronisation scope, and RADIUS/NPS policy.
- **Storage and backup infrastructure** — file shares and their permissions, network-attached storage, and backup jobs, schedules and retention.
- **Endpoint management infrastructure** — endpoint configuration and compliance policy, the management agent and its deployment, and endpoint security tooling.
- **Business platform infrastructure** — the Cirque Tracker platform and the services supporting it.
- **Physical infrastructure supporting IT** — power and cooling serving server and network equipment.

**Out of scope.** The following are governed elsewhere and are not changes under this policy:

- **Customer firmware and customer product configuration** — governed by *Change Management: Customer Firmware* and the applicable quality procedures.
- **Cirque's internally developed software tools** — governed by IS-CIRQ-PR-016-G: Internal Software Change Management Procedure, via GitLab merge requests.
- **Secure design, acquisition and maintenance requirements**, including secure coding — set out in IS-CIRQ-P-012-G.
- **User account creation, amendment and removal** — governed by IS-CIRQ-P-008-G: Access Control Policy.
- **Disaster recovery invocation** — governed by IS-CIRQ-P-015-G.
- **Routine issue and return of individual employee hardware** — governed by the asset lifecycle.

## 3. Policy ownership

The **IT Manager** is the owner of this policy and its related processes, and will review and update it at least annually. Approval authority for individual changes is set out in section 6 and in IS-CIRQ-PR-013-G.

## 4. Policy

All changes to Cirque's IT infrastructure shall follow a standardized process. Changes shall be categorized (section 6) and controls applied appropriate to the category.

- No change shall be made to in-scope infrastructure without a recorded Change Request, except under the Emergency Change path, which requires retrospective documentation.
- Changes shall not be implemented without recorded authorization from an appropriate approver, obtained **before** implementation.
- Every change shall have a documented means of reversal — a back-out plan, a configuration snapshot, or a verified backup — established and confirmed **before** implementation begins.
- Where a change could sever the management path to the device being changed, an **independent recovery path** shall be arranged before implementation. Acceptable paths include an out-of-band console, a second management route, or an automatic rollback timer that restores the prior configuration if the change is not confirmed.
- Changes carrying a foreseeable service impact shall be scheduled in a maintenance window and communicated to affected parties in advance.
- Production infrastructure shall be separated from development and test infrastructure by network segmentation, and that separation shall be enforced by access controls.
- Production data shall not be copied into development or test environments unless equivalent controls are applied to those environments.
- Baseline security configuration shall not be altered unless there is a clear, documented security or operational reason.
- The right to implement changes in production infrastructure shall be restricted by role and reviewed periodically.
- Changes shall be verified after implementation, and the outcome recorded before the Change Request is closed.

## 5. Change process required elements

Change management for IT infrastructure will adhere to the following steps, in order:

- **1. Planning** — Plan and assess the change, including implementation design, scheduling, dependencies, blast radius, and the risks associated with the change. Establish the back-out plan.
- **2. Communication** — Communicate the change to relevant stakeholders and affected parties.
- **3. Testing** — Validate the change where a test path exists; where it does not, peer review of the proposed change is performed in its place.
- **4. Authorization** — Only authorized changes are implemented.
- **5. Implementation** — Implement per the approved plan, after pre-implementation checks and backups, and with an independent recovery path where management access is at risk.
- **6. Documentation** — Maintain documentation capturing the planning, testing, authorization, implementation and contingency arrangements for each change.
- **7. Post-change review** — Verify the change achieved its intended outcome without unintended effects, update affected documentation, and review the change with a view to future improvement.

## 6. Change types

Cirque adheres to the following change categories for IT infrastructure:

| Type | Description | Process |
|---|---|---|
| **Minor Change** | A low-risk change with well-understood outcomes affecting an isolated component, made regularly in the course of business — for example adding a DNS record, adding a firewall address object, adjusting a monitoring threshold, or a routine endpoint policy adjustment. Reversal is trivial or has no impact. | All elements of change management are recorded, proportionate to risk. Approved directly by the IT Manager under IS-CIRQ-PR-013-G section 5.3(a). Affected stakeholders are notified where relevant. |
| **Routine Change** | A medium to high impact change to critical infrastructure, with less predictable outcomes or more than one dependency — for example a VLAN or routing change, a firewall policy change, a hypervisor or server upgrade, a Group Policy or directory configuration change, or a change to backup jobs or retention. | All elements of change management are documented. Peer review is performed, and the change validated where a test path exists. Approved by the IT Manager, and by the **Change Advisory Board** where risk or business impact is medium to high (IS-CIRQ-PR-013-G section 5.3(b)). Scheduled in a maintenance window, implemented with a confirmed back-out plan, verified in production, and stakeholders notified. |
| **Emergency Change** | A change that, if not implemented immediately, would leave Cirque exposed to significant security risk, data loss or service downtime — for example an active security incident, a major outage, or imminent loss of a critical service. | Testing and peer review may not precede implementation. Authorization must be obtained from the **IT Manager**, or in their absence a designated alternate, before implementation; verbal or instant-message approval is acceptable and is recorded afterwards. Stakeholders are kept informed and affected parties notified on completion. The change must be **documented retrospectively within two business days**, stating the nature of the emergency, who authorized it, the actions taken, the systems affected and the verification performed, and is then reviewed by the IT Manager and/or CAB for lessons learned (IS-CIRQ-PR-013-G section 5.6). |
| **Vendor-Initiated Change** | A change supplied by a vendor to apply security patches, service packs, firmware or other updates to in-scope infrastructure. | All elements of change management are followed and recorded. Updates are staged — applied to a limited set and observed — before broad deployment, proportionate to risk. Deployment is recorded and the change verified. Stakeholders are notified before and after where a service impact is expected. Security patching is additionally governed by the patch management process. |

## 7. Change controls and evidence

The following controls are in place for IT infrastructure change, with the evidence that demonstrates each.

| # | Control | Evidence |
|---|---|---|
| 1 | This policy and its supporting procedure are documented, and reviewed and approved on an annual basis. | Document history for this policy and for IS-CIRQ-PR-013-G, showing the date of last review and approval within one year. |
| 2 | Infrastructure changes are authorized and approved prior to implementation, by an approver appropriate to the change type. | Approval recorded on the Change Request, per the tiering in IS-CIRQ-PR-013-G section 5.3. |
| 3 | Changes are assessed for impact and risk before approval, including their security effect. | The impact analysis and security/risk assessment recorded on the Change Request (IS-CIRQ-PR-013-G section 5.2). |
| 4 | Changes are peer reviewed or validated before implementation, proportionate to risk. Where no test path exists, peer review of the proposed change is performed in its place. | Review or validation recorded on the Change Request. |
| 5 | Every change is reversible. A back-out plan, configuration snapshot or verified backup exists before implementation. | Dated configuration snapshots retained for network and gateway devices; virtual machines and infrastructure backed up nightly to the Proxmox Backup Server under documented retention; the back-out plan recorded on the Change Request. |
| 6 | Changes that could sever management access are performed with an independent recovery path in place. | The recovery arrangement recorded on the Change Request — out-of-band console, second management route, or an automatic rollback timer. |
| 7 | Changes are recorded and attributable to the individual who made them. | The Cirque Tracker change audit trail records every mutation with the acting operator, timestamp, action and field-level detail, and provides no user-facing means to amend or delete an audit record. Automated service-account synchronisation activity is identified separately and excluded from managed-change reporting. |
| 8 | The right to change production infrastructure is restricted by role and reviewed periodically. | Role assignments and the periodic logical access review. |
| 9 | Production infrastructure is separated from development and test infrastructure. | Network segmentation of the development environment onto a dedicated VLAN, and the current network diagram. |
| 10 | Vendor security updates are deployed and verified. | Patch installation records reported by each endpoint, listing the patches installed and their installation dates. |
| 11 | Changes are verified after implementation and documentation updated before closure. | The verification result and outcome recorded on the Change Request (IS-CIRQ-PR-013-G section 5.5). |

## 8. Document management

This document is subject to management review processes: document development, revision, management approval, communication, publication, acknowledgement where required, and annual review. It is retained in accordance with IS-CIRQ-P-006-G: Documented Information Control Policy.

## 9. Enforcement

Failure to comply with this policy may result in disciplinary action up to and including termination of employment for employees, or termination of contract for third parties and contractors.

## 10. Related documents

IS-CIRQ-PR-013-G: Change Management Procedure  
IS-CIRQ-PR-016-G: Internal Software Change Management Procedure  
IS-CIRQ-P-012-G: Secure System Acquisition, Development and Maintenance Policy  
IS-CIRQ-P-011-G: Operations Security Policy  
IS-CIRQ-P-008-G: Access Control Policy  
IS-CIRQ-P-006-G: Documented Information Control Policy  
IS-CIRQ-P-015-G: Information Security Continuity Policy  
IS-CIRQ-F-001-G: Risk Assessment Register  

## 11. Revision history

| Revision date | Action | Approver |
|---|---|---|
| 2026-09-03 | Initial draft | Prepared for review — IT Manager |
| TBC | Approval | [name], [role] |
| TBC | Review | [name], [role] |
