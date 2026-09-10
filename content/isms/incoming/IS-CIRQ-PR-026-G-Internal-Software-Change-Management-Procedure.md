---
title: Internal Software Change Management Procedure
slug: is-cirq-pr-026-g
document_type: procedure
category: ISMS Manual
version: 1.0
---

---

**Document:** IS-CIRQ-PR-026-G  
**Standards Name:** Internal Software Change Management Procedure  
**Category:** Product Development Related  
**Division:** Procedure  
**Standard Retention:** Exist and No Corrections  
**Standard Type:** Global  

---

**Version:** 1.0 &nbsp;·&nbsp; **Effective Date:** 2026-08-01 &nbsp;·&nbsp; **Review Date:** 2027-08-01 &nbsp;·&nbsp; **Approved By:** Engineering Manager  

---


## 1. Purpose
The purpose of this procedure is to define a standardized and controlled process for managing all changes to Cirque's internally developed software tools.  This procedure aims to minimize the risks associated with changes, prevent unauthorized modifications, reduce disruptions to business operations, and ensure that changes are securely implemented and documented, in accordance with IS-CIRQ-P-011-G: Operations Security Policy and ISO/IEC 27001:2022 Annex A.8.2.

## 2. Scope
This procedure applies to all planned changes to Cirque's internally developed software that could impact the confidentiality, integrity, or availability of information assets. This includes, but is not limited to:
Touchpad development tools like Spidermeas and Touchtools
Systems for tracking and recording test results like Chronos and The QA Database.

## 3. Definitions
Change: Any code or deployment change.
Change Request (CR): A formal proposal for a change. In Gitlab this is done using a Merge Request.
Change Advisory Board (CAB): A group of stakeholders (e.g., IT Manager, Department Managers, relevant technical personnel) responsible for reviewing, evaluating, and approving significant changes.
Gitlab: The version control system and ticketing system used to track and manage changes.

## 4. Responsibilities
Change Initiator: The individual proposing the change.
Change Approver: The individual(s) authorized to approve a change.
Engineering Manager: Overall owner of this procedure, responsible for overseeing the change management process and acting as the primary change approver, or leading the CAB.
Affected Parties: Stakeholders (e.g., end-users, department heads) who will be impacted by the change.

## 5. Procedure

### 5.1. Change Request (CR) Submission
When a developer has an update to a software tool that is ready for approval and use, he or she will create a new Merge Request in Gitlab.  This will create a ticket, outline all the changes in code along with a required description by the developer of the changes, and then require at least one reviewer be assigned to review and approve the changes.

### 5.2. Change Review and Assessment
Once a change request has been submitted the reviewer must review and approve the changes independently.  Once approval has been given in Gitlab the Merge Request can be submitted for use.

### 5.3. Change Approval
After the merge request has been approved by the reviewer, it must be presented to the full team before it can be approved for use.  Every Thursday each engineering team has a Merge Request Management meeting where all Merge requests are reviewed.  Any merge request that is approved by the reviewer must also be presented to the full team and the full team must vote to approve the merge request before it is approved and actually merged.


### 5.4. Change Implementation
Changes can only be approved and implemented after a Merge Request Management.  Once approved, changes can be implemented immediately.  Because the scope is limited to internally developed software only immediate roll out is deemed safe.

### 5.5. Emergency Changes a. In situations requiring immediate action to resolve critical incidents (e.g., security breach, major system outage) where standard change procedures cannot be followed, an Emergency Change may be initiated. b. Authorization: Emergency changes must be verbally approved by the Engineering Manager, or in their absence, a designated alternate, before implementation. c. Documentation (Post-Facto): All emergency changes must be fully documented retroactively, immediately after the critical situation is resolved. This documentation must include the reason for the emergency, the actions taken, the impact, and post-implementation verification. d. Review: All emergency changes will be reviewed by the Engineering Manager and/or CAB to identify lessons learned and improve future processes.

## 6. Tools
Ticketing system for managing Change Requests is all in Gitlab.
Version control systems (GitLab for code and configuration files).

## 7. Review and Update
This procedure will be reviewed at least annually, or sooner if there are significant changes to Cirque's IT environment, operational processes, or identified areas for improvement in change management.

## 8. Related Documents
IS-CIRQ-P-011-G: Operations Security Policy
IS-CIRQ-F-001-G: Risk Assessment Register
IS-CIRQ-P-001-G: Information Security Policy
IS-CIRQ-PR-015-G: Logging and Monitoring Procedure (for monitoring changes
