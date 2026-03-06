# SOC 2 Evidence Requirements & Preparation Roadmap

**Document ID:** EVIDENCE-ROADMAP-2026-03-02  
**Date Created:** March 2, 2026  
**Status:** Planning Phase  
**Owner:** Chris Wren (IT/Security Lead)  
**Collaborators:** Brenda Milian (HR), Dept Managers

---

## Overview

This document identifies all evidence artifacts required to satisfy SOC 2:2022 audit criteria for the 58 controls currently in Strike Graph. Evidence must be uploaded to Strike Graph platform before fieldwork begins (April 1, 2026).

---

## Evidence Categories & Requirements

### Category 1: Governance & Policy Documents (Critical Path)

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **Information Security Policy** (master governance doc) | Template exists | ✅ CRITICAL | Mar 8 | Chris Wren | Governance |
| **Risk Assessment Methodology** (formal doc) | Template exists | ✅ CRITICAL | Mar 8 | Chris Wren | Risk Management |
| **Risk Register** (live, not template) | Template only | ✅ CRITICAL | Mar 15 | Chris Wren | Risks |
| **Statement of Applicability (SoA)** | Template in progress | ✅ CRITICAL | Mar 15 | Chris Wren | Controls |
| **Control Procedures Manual** (54 Not In Place controls) | 20% complete | ✅ CRITICAL | Mar 25 | Chris Wren | Control Details |
| **Data Classification Policy** | Draft created (`SOC2-Data-Classification-Policy.md`) | ✅ REQUIRED | Mar 10 | Chris Wren | Data Management |
| **Data Retention/Deletion Policy** | Draft created (`SOC2-Data-Retention-and-Deletion-Policy.md`) | ✅ REQUIRED | Mar 10 | Chris Wren | Data Management |
| **Incident Response Plan** | Draft created (`SOC2-Incident-Response-Plan.md`) | ✅ REQUIRED | Mar 12 | Chris Wren | Incident Mgmt |
| **Business Continuity Plan** | Draft created (`SOC2-Business-Continuity-Plan.md`) | ✅ REQUIRED | Mar 12 | Chris Wren | Availability |
| **Change Management Policy** | Draft created (`SOC2-Change-Management-Policy.md`) | ✅ REQUIRED | Mar 10 | Chris Wren | Operations |
| **Vendor Management Policy** | Draft created (`SOC2-Vendor-Management-Policy.md`) | ✅ REQUIRED | Mar 12 | Chris Wren | Vendor Mgmt |

---

### Category 2: Organizational & HR Documents

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **Organizational Chart** | Exists | ✅ REQUIRED | Mar 5 | Brenda Milian | Governance |
| **Job Descriptions** (all key roles) | Partial | ✅ REQUIRED | Mar 10 | Brenda Milian | HR |
| **Employee Handbook** | Exists | ✅ REQUIRED | Mar 5 | Brenda Milian | HR |
| **Code of Conduct / Acceptable Use Policy** | Template | ✅ REQUIRED | Mar 8 | Brenda Milian | HR |
| **Background Check Policy** | Exists | ✅ REQUIRED | Mar 5 | Brenda Milian | HR |
| **Security Training Records** (last 12 months) | Partial (2025) | ✅ REQUIRED | Mar 20 | Brenda Milian | Training |
| **Non-Disclosure Agreements (NDAs)** | Template | ✅ REQUIRED | Mar 10 | Brenda Milian | Vendor Mgmt |
| **Termination Procedures** | Documented | ✅ REQUIRED | Mar 5 | Brenda Milian | Access Mgmt |

---

### Category 3: Technical & System Documentation

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **System Description Document** (current architecture) | Outdated | ✅ CRITICAL | Mar 10 | Chris Wren | System Design |
| **Network Diagram** (high-level, current) | Outdated | ✅ CRITICAL | Mar 10 | Chris Wren | System Design |
| **Data Flow Diagram** (data categories & movement) | Template | ✅ CRITICAL | Mar 12 | Chris Wren | System Design |
| **Asset Inventory** (hardware, software, systems) | Partial | ✅ REQUIRED | Mar 15 | Chris Wren | System Design |
| **Backup & Disaster Recovery Procedures** | Template | ✅ REQUIRED | Mar 12 | Chris Wren | Availability |
| **Patch Management Schedule** | Template | ✅ REQUIRED | Mar 10 | Chris Wren | Operations |
| **Vulnerability Scanning Results** (last 3 months) | Not collected | ✅ REQUIRED | Mar 22 | Chris Wren | Security Ops |
| **Antivirus/EDR Incident Logs** (last 3 months) | Exists | ✅ REQUIRED | Mar 20 | Chris Wren | Security Ops |
| **Firewall Rules Documentation** | Partial | ✅ REQUIRED | Mar 10 | Chris Wren | Access |
| **Encryption Standard (at-rest & in-transit)** | Documented | ✅ REQUIRED | Mar 8 | Chris Wren | Security Ops |

---

### Category 4: Access Control & Provisioning Evidence

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **User Access Policy** (provisioning/de-provisioning) | Template | ✅ CRITICAL | Mar 10 | Chris Wren | Access |
| **Password Policy** (minimum standards) | Documented | ✅ CRITICAL | Mar 5 | Chris Wren | Access |
| **Privileged Access Management (PAM) Procedures** | Partial | ✅ CRITICAL | Mar 12 | Chris Wren | Access |
| **User Access Review Records** (last annual review) | Exist but incomplete | ✅ REQUIRED | Mar 15 | Chris Wren | Access |
| **Provisioning Request Log** (past 3 months) | In system | ✅ REQUIRED | Mar 20 | Chris Wren | Access |
| **De-provisioning/Termination Log** (past 3 months) | In system | ✅ REQUIRED | Mar 20 | Chris Wren | Access |
| **Third-Party Vendor Access Log** | Template | ✅ REQUIRED | Mar 15 | Chris Wren | Vendor |
| **VPN/Remote Access Logs** (past month) | In system | ✅ REQUIRED | Mar 20 | Chris Wren | Access |
| **Active Directory / Identity Management Config** | Exists | ✅ REQUIRED | Mar 15 | Chris Wren | Access |

---

### Category 5: Monitoring & Incident Response Evidence

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **Incident Log** (2025 + 2026 YTD) | Partial | ✅ CRITICAL | Mar 20 | Chris Wren | Incident Mgmt |
| **Incident Response Testing Results** | None documented | ✅ REQUIRED | Mar 25 | Chris Wren | Incident Mgmt |
| **Security Monitoring Alerts & Response** (sample 3 months) | In system | ✅ REQUIRED | Mar 20 | Chris Wren | Monitoring |
| **Log Retention & Review Standards** | Draft created (`SOC2-Log-Retention-and-Review-Standard.md`) | ✅ REQUIRED | Mar 10 | Chris Wren | Monitoring |
| **Intrusion Detection/Prevention Logs** (if applicable) | Partial | ✅ | Mar 20 | Chris Wren | Monitoring |
| **External Threat Monitoring** (if applicable) | Partial | Optional | TBD | Chris Wren | Monitoring |

---

### Category 6: Change Management & Configuration Evidence

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **Change Request Log** (past 6 months) | Partial (some in Jira) | ✅ REQUIRED | Mar 20 | Chris Wren | Change Mgmt |
| **Change Approval Records** | Partial | ✅ REQUIRED | Mar 20 | Chris Wren | Change Mgmt |
| **Emergency Change Procedures** | Template | ✅ REQUIRED | Mar 10 | Chris Wren | Change Mgmt |
| **Separation of Duties Evidence** (developer vs. prod access) | In policy | ✅ REQUIRED | Mar 15 | Chris Wren | Change Mgmt |
| **System Configuration Baselines** | Partial | ✅ REQUIRED | Mar 15 | Chris Wren | System Design |
| **Configuration Change Logs** | Partial | ✅ REQUIRED | Mar 20 | Chris Wren | Change Mgmt |

---

### Category 7: Vendor & Third-Party Management

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **Vendor Master List** (all vendors w/ system access) | Partial | ✅ REQUIRED | Mar 10 | Chris Wren | Vendor |
| **Vendor Contracts/SOWs** (sample of critical vendors) | Exist, scattered | ✅ REQUIRED | Mar 15 | Chris Wren | Vendor |
| **Vendor SOC 2 Attestations** (if required by contract) | Some exist | ✅ REQUIRED | Mar 20 | Chris Wren | Vendor |
| **Vendor Risk Assessment Records** | None documented | ✅ REQUIRED | Mar 15 | Chris Wren | Vendor |
| **Vendor Performance Reviews** (annual) | Partial | ✅ REQUIRED | Mar 20 | Chris Wren | Vendor |
| **Vendor SLA Verification** (uptime, support) | Some documented | ✅ REQUIRED | Mar 20 | Chris Wren | Vendor |

---

### Category 8: Governance & Review Evidence

| Evidence Item | Current Status | Required for Audit | Due Date | Owner | Strike Graph Section |
|---|---|---|---|---|---|
| **Board/Management Risk Review Minutes** (last 4 quarters) | Exist | ✅ REQUIRED | Mar 15 | Exec Committee | Governance |
| **Security Committee Meeting Minutes** (if applicable) | Partial | ✅ REQUIRED | Mar 15 | Chris Wren | Governance |
| **Control Self-Assessment Results** | None documented | ✅ REQUIRED | Mar 25 | Chris Wren | Governance |
| **Internal Audit Reports** (if performed) | None recent | Optional | N/A | Finance | Governance |
| **Compliance Assessment Results** | Partial | ✅ REQUIRED | Mar 20 | Chris Wren | Governance |

---

## Evidence Upload Timeline & Priorities

### **Must-Have for Fieldwork Start (April 1)**
- Scope statement ✅ (P0-01)
- Audit timeline ✅ (P0-02)
- System description & network/data flow diagrams
- Risk register (finalized, live)
- Statement of Applicability (SoA)
- Key policy documents (info security, incident response, change management)
- Organizational chart
- RACI matrix ✅

**Status:** 50% complete (created 3/2; need 5 more by 3/15)

### **Should-Have by Mid-March**
- All control procedures (54 currently "Not In Place")
- User access logs/evidence (last 3 months)
- Incident logs (2025 + YTD 2026)
- Vendor contracts & assessments
- Training records
- Change request logs

**Status:** 20% complete (sprinting through P1 tasks)

### **Can Complete in Late March/Early April**
- Historical monitoring logs (antivirus, firewall)
- Vulnerability scan results
- Third-party audit reports
- Board meeting minutes
- Security review records

**Status:** Will be collected during Apr-May fieldwork if needed

---

## Missing Evidence Action Plan

### **Priority 1: Create by March 8** (5 docs)
1. **Information Security Policy (master)** — Chris Wren
   - Current: Existing (`IS-CIRQ-P-001-G`) and available for SOC 2 alignment updates
   - Required changes: Finalize scope, governance, roles, risk tolerance
   - Target: 1-2 page executive summary + reference to detailed controls

2. **Data Classification Policy** — Chris Wren
   - Current: Draft created (`SOC2-Data-Classification-Policy.md`)
   - Required changes: Define classifications (Public, Internal, Confidential, Restricted), marking standards, handling rules
   - Target: 2-3 pages

3. **Change Management Policy** — Chris Wren
   - Current: Draft created (`SOC2-Change-Management-Policy.md`)
   - Required changes: Define change types, approval authority, testing requirements, emergency process
   - Target: 2-3 pages

4. **Log Retention & Review Standards** — Chris Wren
   - Current: Draft created (`SOC2-Log-Retention-and-Review-Standard.md`)
   - Required changes: New doc defining what logs are kept, for how long, review frequency
   - Target: 1-2 pages

5. **System Description Document** — Chris Wren
   - Current: Outdated (last updated 2024)
   - Required changes: Current architecture, technologies, components, external dependencies
   - Target: 4-5 pages + diagrams

### **Priority 2: Create by March 12** (4 docs)
- Incident Response Plan (detailed procedures)
- Business Continuity Plan
- Vendor Management Policy
- Disaster Recovery Procedures

### **Priority 3: Update/Finalize by March 15** (6 docs)
- **Risk Register** — Convert from template to live register with all 38 risks
- **Statement of Applicability (SoA)** — Map all 58 controls to ISO 27001 + SOC 2 criteria
- **User Access Policy** — Finalize provisioning/de-provisioning procedures
- **Privileged Access Management Procedures** — Admin account management
- **Vendor Master List** — All vendors with system/data access
- **Job Descriptions** — All key roles (IT, Security, HR, Finance, Ops)

---

## Evidence Collection Responsibility Matrix

| Category | Owner | Collaborators | Deadline |
|---|---|---|---|
| Governance & Policy (11 docs) | Chris Wren | Brenda Milian | Mar 12 |
| Organizational & HR (8 docs) | Brenda Milian | Chris Wren (for training) | Mar 10 |
| Technical & System (10 docs) | Chris Wren | Tech team | Mar 15 |
| Access Control (9 docs) | Chris Wren | IT Ops | Mar 15 |
| Monitoring & Incident (6 docs) | Chris Wren | Security Ops | Mar 20 |
| Change Management (6 docs) | Chris Wren | Dev/Ops teams | Mar 20 |
| Vendor & Third-Party (6 docs) | Chris Wren | Procurement | Mar 20 |
| Governance & Review (4 docs) | Chris Wren + Exec Committee | Finance | Mar 25 |

---

## Strike Graph Upload Sequence

**Week 1 (Mar 3-7):** Upload foundational governance docs
1. Scope statement (P0-01)
2. Audit timeline (P0-02)
3. RACI (from UB-05)
4. Organizational chart

**Week 2 (Mar 8-14):** Upload critical policies & procedures
5. Information Security Policy (master)
6. Data Classification Policy
7. Change Management Policy
8. System Description Document

**Week 3 (Mar 15-21):** Upload control-level evidence
9. Risk Register (live)
10. Statement of Applicability
11. User Access Policy + provisioning logs
12. Vendor Master List & contracts

**Week 4 (Mar 22-28):** Upload operational evidence
13. Incident logs
14. Change request logs
15. Training records
16. Monitoring/security logs

**Week 5 (Mar 29-Apr 1):** Final verification before fieldwork
- All 58 controls have procedures or evidence
- All critical evidence uploaded
- Fieldwork readiness checklist complete

---

## Success Criteria (Pre-Fieldwork)

By April 1, 2026, Cirque will have uploaded to Strike Graph:

- ✅ Scope statement (signed by exec)
- ✅ Audit timeline (signed by exec)
- ✅ RACI governance matrix (signed by exec)
- ✅ 54 control procedures (design + one-time implementation evidence)
- ✅ 3 control operational logs (at least 1 control procedure for 1 in-place control is validated)
- ✅ Risk register (all 38 risks with mitigation strategies)
- ✅ Statement of Applicability (all 58 controls mapped)
- ✅ Policies: InfoSec, Data Classification, Change Management, Incident Response, BC/DR, User Access, PAM, Vendor Management
- ✅ System documentation: Network diagram, data flow diagram, asset inventory
- ✅ HR evidence: Org chart, job descriptions, training records
- ✅ Monitoring evidence: Incident logs, access logs, security alerts (samples)

**Evidence completeness target:** 85%+ of required docs in Strike Graph by fieldwork start

---

## Next Actions

1. **Today (Mar 2):** Chris Wren reviews this roadmap + prioritizes top 5 Policy docs
2. **Mar 3-7:** Begin writing Priority 1 policy documents + System Description document
3. **Mar 8:** First batch uploaded to Strike Graph (5 governance docs)
4. **Mar 8-14:** Write Priority 2 control procedures + data documentation
5. **Mar 15:** Second batch uploaded; begin collecting operational evidence
6. **Mar 20-28:** Complete operational logs & monitoring evidence
7. **Mar 29:** Final readiness checkpoint with Strike Graph
8. **Apr 1:** Fieldwork begins with 85%+ evidence already loaded

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-02  
**Owner:** Chris Wren (IT/Security Lead)  
**Next Review:** March 8, 2026
