# SOC2 Evidence Register

**Document ID:** SOC2-EVID-REG-2026-03-02  
**Owner:** Chris Wren  
**Status:** Active

Use this register to track required SOC 2 evidence artifacts, storage location, freshness, reviewer, and upload status.

| Evidence ID | Control Area | Evidence Artifact | Owner | Frequency | Repository Location | Reviewer | Next Due | Status |
|---|---|---|---|---|---|---|---|---|
| E-001 | Governance | Approved RACI and role matrix | Chris Wren | Annual | 01-Governance | Exec Committee | 2026-03-15 | In Progress |
| E-002 | Governance | Policy review and approval log | Chris Wren | Annual | 01-Governance | Exec Committee | 2026-03-15 | In Progress |
| E-003 | Risk | Risk register (live) | Chris Wren | Monthly | 05-Risk-Management | Exec Committee | 2026-03-15 | In Progress |
| E-004 | Risk | Risk treatment plan tracker | Chris Wren | Monthly | 05-Risk-Management | Exec Committee | 2026-03-15 | In Progress |
| E-005 | Access | Provisioning request samples | IT Operations | Monthly | 07-Operational-Evidence/Access-Control-Logs | IT/Security Lead | 2026-03-20 | Not Started |
| E-006 | Access | Deprovisioning completion records | IT Operations | Monthly | 07-Operational-Evidence/Access-Control-Logs | IT/Security Lead | 2026-03-20 | Not Started |
| E-007 | Access | Privileged access review report | IT/Security Lead | Quarterly | 07-Operational-Evidence/Access-Control-Logs | Exec Committee | 2026-03-20 | Not Started |
| E-008 | Monitoring | Security alert investigation tickets | Security Operations | Monthly | 07-Operational-Evidence/Monitoring-Logs | IT/Security Lead | 2026-03-20 | Not Started |
| E-009 | Monitoring | Log retention configuration export | Security Operations | Quarterly | 07-Operational-Evidence/Monitoring-Logs | IT/Security Lead | 2026-03-20 | Not Started |
| E-010 | Incident | Incident register and PIRs | IT/Security Lead | Ongoing | 07-Operational-Evidence/Incident-Response-Evidence | Exec Committee | 2026-03-20 | In Progress |
| E-011 | Incident | Incident response tabletop results | IT/Security Lead | Annual | 07-Operational-Evidence/Incident-Response-Evidence | Exec Committee | 2026-03-25 | Not Started |
| E-012 | Change | Change requests with approvals | IT Operations | Monthly | 07-Operational-Evidence/Change-Management-Evidence | IT/Security Lead | 2026-03-20 | Not Started |
| E-013 | Change | Emergency change post-review records | IT Operations | Monthly | 07-Operational-Evidence/Change-Management-Evidence | IT/Security Lead | 2026-03-20 | Not Started |
| E-014 | Vendor | Vendor inventory with risk tiering | Chris Wren | Quarterly | 07-Operational-Evidence/Vendor-Management-Evidence | Legal/Procurement | 2026-03-20 | In Progress |
| E-015 | Vendor | Critical vendor SOC reports | Chris Wren | Annual | 07-Operational-Evidence/Vendor-Management-Evidence | Legal/Procurement | 2026-03-20 | Not Started |
| E-016 | Availability | Backup restore test report | IT Operations | Quarterly | 07-Operational-Evidence/Security-Operations-Evidence | IT/Security Lead | 2026-03-22 | Not Started |
| E-017 | Availability | BCP tabletop exercise report | IT/Security Lead | Annual | 07-Operational-Evidence/Security-Operations-Evidence | Exec Committee | 2026-03-25 | Not Started |
| E-018 | Confidentiality | Data classification inventory export | Data Owners | Quarterly | 07-Operational-Evidence/Security-Operations-Evidence | IT/Security Lead | 2026-03-20 | Not Started |
| E-019 | Confidentiality | Secure deletion logs | IT Operations | Monthly | 07-Operational-Evidence/Security-Operations-Evidence | IT/Security Lead | 2026-03-20 | Not Started |
| E-020 | Training/HR | Security awareness completion report | Brenda Milian | Quarterly | 06-HR-Organizational | IT/Security Lead | 2026-03-20 | In Progress |

## Evidence Quality Rules
- Evidence must include date/time, system/source, approver/reviewer, and control linkage.
- Evidence must be within the audit period and reproducible on request.
- Screenshots are acceptable only when exports/log files are not available.

## Upload Readiness Definition
A row is **Done** only when the artifact exists, was reviewed, and was uploaded/mapped in Strike Graph.

## Full Control Linkage
- Full per-control evidence linkage (`EC-001` to `EC-058`) is maintained in `SOC2-Evidence-Catalog.md`.
- Control join table is maintained in `../08-Control-Mappings/SOC2-Control-Implementation-Matrix.md`.
