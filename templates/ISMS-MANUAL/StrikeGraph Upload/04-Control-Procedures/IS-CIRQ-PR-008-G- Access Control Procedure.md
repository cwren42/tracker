**IS-CIRQ-PR-008-G: Access Control Procedure**

**Document: IS-CIRQ-PR-008-G**

**Standards Name: Access Control Procedure**

**Category: IT Security Related**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to detail the systematic process for
granting, modifying, reviewing, and revoking user access to Cirque\'s
information assets, systems, applications, networks, and physical
facilities. This procedure ensures adherence to IS-CIRQ-P-008-G: Access
Control Policy and the principles of least privilege and need-to-know.

**2. Scope**

This procedure applies to all Cirque personnel (employees, contractors),
external parties requiring limited access (e.g., SharePoint guest
access), and all relevant information assets, IT systems, applications,
networks, and physical facilities across all Cirque locations (US,
Japan, China).

**3. Responsibilities**

-   **IT Manager:** Overall owner of this procedure. Manages user
    accounts in Active Directory and other systems. Configures and
    manages Unifi Access. Conducts access reviews.

-   **HR Department:** Initiates onboarding and offboarding processes
    for employees and contractors, providing necessary documentation for
    account creation/deletion.

-   **Department Managers/Asset Owners:** Responsible for approving
    access requests for their direct reports and for assets they own.

-   **All Personnel:** Responsible for protecting their login
    credentials and adhering to all access control policies and
    procedures.

**4. Procedure**

**4.1. User Access Request and Provisioning**

**4.1.1. New User Onboarding (Employees)**

a\. **HR Notification:** HR notifies the IT Manager of a new hire,
providing start date, role, department, and initial access requirements
(e.g., standard employee access profile).

b\. **Active Directory Account Creation:** IT Manager creates a unique
user account in **Active Directory**.

c\. **System Access Provisioning:** IT Manager provisions access to
standard applications (e.g., Microsoft 365, Teams) based on the
employee\'s role. Access to specialized applications (e.g., Omnify,
Cadence, GitLab, Asana) requires specific approval from the relevant
Department Manager/Asset Owner.

d\. **MFA Enrollment:** New users are required to enroll in Multi-Factor
Authentication (MFA) for their Microsoft 365/Azure AD account and any
other critical systems.

e\. **Physical Access (US/Taipei Offices):** HR coordinates with the IT
Manager to provision physical access credentials (e.g., keycard in
**Unifi Access system**) based on the employee\'s role and need for
access to specific office areas.

f\. **Initial Security Awareness:** New users receive initial
information security awareness training, including access control
policies.

**4.1.2. Contractor/Temporary Staff Onboarding**

a\. **Department Manager Request:** Department Manager submits a formal
request to the IT Manager, specifying contractor name, start/end dates,
required access (least privilege basis), and the business justification.

b\. **Account Creation/Provisioning:** IT Manager creates temporary
accounts in **Active Directory** and provisions access based on the
approved request. All contractor access is time-limited.

c\. **Physical Access:** If physical office access is required, the
Department Manager requests it, and the IT Manager provisions temporary
access in **Unifi Access**.

d\. **Security Acknowledgement:** Contractors must acknowledge
understanding of Cirque\'s security policies before access is granted.

**4.1.3. Role Change / Access Modification**

a\. **Manager Request:** Employee\'s manager submits a formal request to
the IT Manager detailing the change in role and the corresponding access
modifications (additions or removals).

b\. **Access Adjustment:** IT Manager adjusts permissions in **Active
Directory** and relevant applications based on the principle of least
privilege. Permissions no longer needed are immediately revoked.

**4.2. User Access De-registration / Offboarding**

**4.2.1. Employee Termination**

a\. **HR Notification:** HR notifies the IT Manager of an employee\'s
termination date and time.

b\. **Immediate Account De-activation:** The IT Manager shall
immediately de-activate (disable) the employee\'s **Active Directory
account** and all associated system/application accounts (e.g.,
Microsoft 365, GitLab, Omnify) upon notification. Accounts will not be
deleted immediately but retained as disabled for a defined period (e.g.,
90 days) for auditing and data retention purposes.

c\. **Physical Access Revocation:** Physical access credentials (e.g.,
Unifi Access keycard) are immediately revoked/collected.

d\. **Device Retrieval:** Company-issued devices are collected by HR or
IT.

e\. **Data Transfer/Backup:** Relevant data from the user\'s
accounts/devices is transferred to the manager or backed up as per data
retention policies.

**4.2.2. Contractor/Temporary Staff Offboarding**

a\. **Manager Notification:** Department Manager notifies the IT Manager
of the contractor\'s end date.

b\. **Account De-activation/Deletion:** The IT Manager de-activates
accounts immediately upon the contract end date. For contractors,
accounts may be deleted sooner than employee accounts if no data
retention requirements exist.

c\. **Physical Access Revocation:**

Immediate revocation of Unifi Access credentials.

**4.3. Access Review (Regular)**

a\. **Frequency:** The IT Manager shall conduct formal access reviews at
least annually for all Active Directory users, application-specific
accounts, and physical access permissions. Ad-hoc reviews may be
conducted for privileged accounts or high-risk systems more frequently.

b\. **Review Process:** \* The IT Manager generates reports of current
access rights for systems, applications, and physical locations. \*
These reports are sent to the respective Department Managers and Asset
Owners for their review and confirmation. \* Managers/Owners confirm
that all listed access rights are still required and align with the
principle of least privilege. \* Any unauthorized or unnecessary access
identified during the review shall be immediately revoked by the IT
Manager.

c\. **Documentation:** Records of access reviews (e.g., signed
confirmations, audit trail from review tools) shall be maintained.

**4.4. Privileged Access Management**

a\. **Dedicated Accounts:** Where possible, separate, non-email-enabled,
privileged accounts will be created for administrative tasks in **Active
Directory** and critical applications.

b\. **Justification:** Use of privileged accounts requires specific
justification and is logged.

c\. **Monitoring:** All activities performed with privileged accounts
shall be logged and monitored for suspicious behavior.

d\. **Review:** Privileged access rights will be reviewed more
frequently (e.g., quarterly) than standard user access.

**4.5. External Access (SharePoint Guest Access)**

a\. **Request and Approval:** Guest access to SharePoint is granted only
upon documented request from an employee, approved by the relevant
Document Owner/Department Manager, and the IT Manager.

b\. **Least Privilege:** Access is granted only to the specific
documents or folders required, with read-only permissions by default,
unless write access is explicitly justified and approved.

c\. **Time-Limited:** Guest access accounts shall be time-limited and
reviewed periodically for continued necessity.

d\. **Monitoring:** Guest access activity should be logged and monitored
within SharePoint.

**5. Documentation**

All access requests, approvals, modifications, and revocations shall be
documented. This includes:

-   User access request forms (manual or electronic).

-   Email approvals.

-   Logs from Active Directory, Unifi Access, SharePoint, and other
    application access management systems.

-   Access review reports.

**6. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are changes to IS-CIRQ-P-008-G: Access Control Policy, Cirque\'s IT
environment (e.g., new identity management systems), or legal/regulatory
requirements.

**7. Related Documents**

-   IS-CIRQ-P-008-G: Access Control Policy

-   IS-CIRQ-P-007-G: Asset Management Policy

-   IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure

-   IS-CIRQ-P-019-G: Acceptable Use Policy

-   User Access Request Form
