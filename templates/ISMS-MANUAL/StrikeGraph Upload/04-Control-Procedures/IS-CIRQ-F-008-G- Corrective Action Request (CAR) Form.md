**IS-CIRQ-F-008-G: Corrective Action Request (CAR) Form**

**Document: IS-CIRQ-F-008-G**

**Standards Name: Corrective Action Request (CAR) Form**

**Category: ISMS Support Process**

**Division: Form**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** ISMS Owner / IT Manager

**CORRECTIVE ACTION REQUEST (CAR) FORM**

  --------------------------------------------------------------------------------
  **Document    IS-CIRQ-F-008-G                 **Version:**      1.0
  ID:**                                                           
  ------------- ------------------------------- ----------------- ----------------
  **CAR ID:**   \[e.g., CAR-YYYYMMDD-001\]      **Date            \[DD-MM-YYYY\]
                                                Initiated:**      

  **Status:**   \[Open / In Progress /          **Due Date for    \[DD-MM-YYYY\]
                Verification Pending / Closed\] RCA & Plan:**     
  --------------------------------------------------------------------------------



**1. NONCONFORMITY IDENTIFICATION**

-   **Source of Nonconformity:**

    -   \[ \] Internal Audit (Audit ID: \[If applicable\])

    -   \[ \] External Audit

    -   \[ \] Security Incident (Incident ID: \[If applicable\])

    -   \[ \] Monitoring/Measurement

    -   \[ \] Management Review

    -   \[ \] Feedback (Customer, Regulator, Employee)

    -   \[ \] Other (Specify: \[ \])

-   **Originator (Reported By):** \[Name, Department\]

-   **Date of Nonconformity Occurrence/Detection:** \[DD-MM-YYYY\]

-   **Department/Process Affected:** \[e.g., IT Operations, HR,
    Development, Access Control Process\]

-   **Nonconformity Description:** \[Provide a clear, concise, and
    factual description of the nonconformity. What is the deviation?
    From what requirement? What was observed?\] \*Example: \"During the
    monthly review of user access logs, it was observed that User ID
    \'jsmith\' retained access to the Finance Sharepoint for 72 hours
    after their termination date of 2025-06-01.\"\]

-   **Relevant ISMS Requirement Violated:** \[Reference the specific
    standard clause, policy, or procedure violated.\] *Example:
    \"IS-CIRQ-P-008-G: Access Control Policy, Section 5.3 (Access
    Revocation) - \'Access for terminated employees must be revoked
    within 24 hours of termination.\' Also, ISO 27001:2022 A.9.2.1.\"*

-   **Objective Evidence:** \[Provide specific references to records,
    logs, reports, or observations that substantiate the
    nonconformity.\] *Example: \"HR termination record for \'jsmith\'
    (ID: HR-T-005). Finance Sharepoint access logs from 2025-06-01 to
    2025-06-04 showing activity from \'jsmith\'.\"*

**2. IMMEDIATE CORRECTION (Containment / Symptom Fix)**

-   **Action Taken (Temporary Fix):** \[What immediate action was taken
    to control the nonconformity and deal with its consequences? This is
    typically a band-aid, not the long-term solution.\] *Example: \"User
    ID \'jsmith\' access to Finance Sharepoint manually revoked on
    2025-06-04 at 10:30 AM.\"*

-   **Date Completed:** \[DD-MM-YYYY\]

**3. INVESTIGATION AND ROOT CAUSE ANALYSIS (RCA)**

-   **Action Item Owner (Responsible Party):** \[Name, Department\]

-   **Root Cause Analysis Performed (Methodology used, e.g., 5 Whys,
    Fishbone):** \[Describe the method used for RCA.\] *Example: \"5
    Whys analysis was conducted.\"*

-   **Identified Root Cause(s):** \[What were the underlying systemic
    reasons for the nonconformity? Why did it happen?\] \*Example: \"The
    automated HR-IT termination workflow failed to trigger the access
    revocation script for the Finance Sharepoint due to an incorrect
    group mapping. This manual review process, intended as a fallback,
    has a 48-hour SLA, which was exceeded due to personnel being on
    leave.\"\]

**4. CORRECTIVE ACTION PLAN**

-   **Plan Developed By:** \[Name, Department\]

-   **Date Plan Developed:** \[DD-MM-YYYY\]

  -----------------------------------------------------------------------------------------------
  **Action   **Detailed Action to    **Responsible   **Target         **Status**   **Evidence of
  Item \#**  Eliminate Root Cause**  Person(s)**     Completion                    Completion**
                                                     Date**                        
  ---------- ----------------------- --------------- ---------------- ------------ --------------
  1\.        Correct group mapping   \[IT Automation \[YYYY-MM-DD\]   Open         \[Ref: Change
             in HR-IT termination    Specialist\]                                  Request ID,
             workflow for Finance                                                  Screenshot\]
             Sharepoint access.                                                    

  2\.        Update IS-CIRQ-PR-008-G \[ISMS Owner\]  \[YYYY-MM-DD\]   Open         \[Ref:
             (Access Management                                                    Document
             Procedure) to include                                                 Version\]
             daily reconciliation                                                  
             report of terminated                                                  
             users vs. active access                                               
             for critical systems.                                                 

  3\.        Conduct refresher       \[IT Training   \[YYYY-MM-DD\]   Open         \[Ref:
             training for IT Help    Lead\]                                        Training Log,
             Desk staff on manual                                                  Course
             access revocation                                                     Module\]
             procedures, emphasizing                                               
             critical systems.                                                     
  -----------------------------------------------------------------------------------------------



**5. VERIFICATION OF EFFECTIVENESS**

-   **Verifier:** \[Name, Department\]

-   **Date of Verification:** \[DD-MM-YYYY\]

-   **Verification Method(s):** \[How was it confirmed that the
    corrective action eliminated the root cause and prevented
    recurrence? e.g., Re-audit, review of new data, observation.\]
    *Example: \"1. Reviewed updated HR-IT workflow configuration. 2.
    Monitored daily reconciliation report for 3 weeks (no discrepancies
    found). 3. Reviewed training records and interviewed Help Desk staff
    on updated procedure.\"*

-   **Results of Verification (Effective? Yes/No):** \[State clearly if
    the corrective action was effective in preventing recurrence.\]
    *Example: \"Yes, the corrective actions were effective. The
    automated workflow is now correctly configured, the manual review
    process has an effective daily reconciliation, and personnel are
    re-trained.\"*

**6. CAR CLOSURE**

-   **Closed By (ISMS Owner):** \[Name\]

-   **Date Closed:** \[DD-MM-YYYY\]

-   **Comments:** \[Any final comments regarding the closure, e.g.,
    \"Nonconformity successfully closed. Continued monitoring through
    daily reconciliation report.\"\]

**7. DOCUMENT HISTORY**

  -----------------------------------------------------------------------
  Version        Date                        Changes Made
  -------------- --------------------------- ----------------------------
  1.0            \[DD-MM-YYYY\]              Initial Release

  -----------------------------------------------------------------------
