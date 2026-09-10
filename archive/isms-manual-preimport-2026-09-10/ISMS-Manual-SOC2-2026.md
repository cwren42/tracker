# ISMS Manual - SOC 2:2022 Edition
**Cirque Corporation | Global Information Security Management System**

**Document Version:** 1.0  
**Effective Date:** March 2, 2026  
**Audit Scope:** SOC 2:2022 Security Criteria (Type 1: April-June 2026)  
**Master Reference:** ISMS-Manual2025v1.docx

---

## Table of Contents

### Part 1: Governance & Framework
1. [Information Security Policy (Master)](#policy-001)
2. [ISMS Scope Definition](#scope-documents)
3. [Interested Parties & Requirements](#scope-documents)
4. [Legal, Regulatory & Contractual Requirements](#scope-documents)

### Part 2: Roles, Risk & Objectives
5. [Roles, Responsibilities & Authorities](#policy-002)
6. [Risk Management Policy & Procedures](#policy-003)
7. [Information Security Objectives](#scope-documents)

### Part 3: Access Control & Authentication
8. [Access Control Policy](#policy-008)
9. [Privileged Access Management](#policy-008)

### Part 4: Cryptography & Data Protection
10. [Cryptography Policy](#policy-009)
11. [Documented Information Control](#policy-006)

### Part 5: Physical & Environmental Security
12. [Physical & Environmental Security Policy](#policy-010)

### Part 6: Operations, Monitoring & Incident Response
13. [Operations Security Policy](#policy-011)
14. [Information Security Incident Management](#policy-014)

### Part 7: Business Continuity
15. [Information Security Continuity (BC/DR)](#policy-015)

### Part 8: Compliance & Vendor Management
16. [Supplier Relationships Policy](#policy-013)
17. [Compliance Policy](#policy-016)

---

## Part 1: Governance & Framework

### Policy 001: Information Security Policy (Master) {#policy-001}

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-001-G |
| **Status for SOC 2** | ✅ READY (may need SOC 2 addendum) |
| **Location in Manual** | p. 4 |
| **SOC 2 TSC Alignment** | ALL Trust Service Criteria (generic governance) |
| **Key Sections** | Scope, objectives, roles, responsibilities, compliance commitment |
| **SOC2-Specific Edits Needed** | TBD - Review for SOC 2 specific language |

**Current Content Summary:**
- Master policy covering entire ISMS governance
- Establishes roles, responsibilities, scope
- Commitment to compliance with ISO 27001
- References specific policies for detailed requirements

**Actions for Chris Wren (Prior to Audit):**
- [ ] Review policy for SOC 2 2022 specific references
- [ ] Add reference to SOC 2 Trust Service Criteria (TSC)
- [ ] Confirm security-only scope documented (Availability/Confidentiality/Privacy/PI deferred)
- [ ] Ensure executive signature/approval visible

---

### ISMS Scope Definition & Requirements Documents {#scope-documents}

| Document | ID | Status | Section |
|---|---|---|---|
| ISMS Scope Document | IS-CIRQ-D-001-G | ✅ READY | p. 10 |
| Interested Parties Register | IS-CIRQ-D-002-G | ✅ READY | p. 15 |
| Legal/Regulatory Requirements (Global) | IS-CIRQ-D-003-G | ✅ READY | p. 22 |
| Legal/Regulatory Requirements (US) | IS-CIRQ-D-003-US | ✅ READY | p. 26 |
| Legal/Regulatory Requirements (ASIA) | IS-CIRQ-D-003-ASIA | ✅ READY | p. 28 |
| Information Security Objectives | IS-CIRQ-D-007-G | ✅ READY | p. 60 |

**SOC 2 Alignment:**
- Scope doc = SOC 2 system boundaries (on-premise + cloud)
- Requirements register = vendor/customer SOC 2 audit requirements
- Legal/Regulatory = GDPR, CCPA, PCI-DSS requirements mapping
- Objectives = security goals aligned to TSC

**Actions for Chris Wren:**
- [ ] Update Legal/Regulatory doc to explicitly reference SOC 2 engagement with Strike Graph
- [ ] Ensure Objectives explicitly reference the 5 SOC 2 criteria (C/I/A/PI/Confidentiality)

---

## Part 2: Roles, Risk & Objectives

### Policy 002: Roles, Responsibilities & Authorities {#policy-002}

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-002-G |
| **Status for SOC 2** | ✅ READY (cross-ref with RACI) |
| **Location in Manual** | p. 31 |
| **SOC 2 TSC Alignment** | CC6.1, CC6.2 (Governance & Organization) |
| **Key Sections** | Role definitions, approval authorities, escalation |

**Related Documents:**
- IS-CIRQ-D-004-G: Information Security Roles Matrix (p. 35)
- SOC2-UB05-RACI-Draft.md: SOC 2 Audit-Specific RACI (created)

**SOC 2 Mapping:**
- IT/Security Lead: Chris Wren
- HR/People: Brenda Milian
- Finance/Audit: [Lead name]
- Executive Steering: Board/Executive Committee

**Actions for Chris Wren:**
- [ ] Cross-check RACI matrix for all 58 SOC 2 control owners
- [ ] Ensure evidence sign-off authorities documented

---

### Policy 003: Risk Management {#policy-003}

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-003-G |
| **Status for SOC 2** | ✅ READY (includes risk scoring) |
| **Location in Manual** | p. 39 |
| **SOC 2 TSC Alignment** | RA1, RA2, RA3, RA4, RA5 (Risk Assessment & Response) |

**Related Documents & Evidence:**
- IS-CIRQ-PR-002-G: Risk Assessment Procedure (p. 43)
- IS-CIRQ-PR-003-G: Risk Treatment Procedure (p. 47)
- IS-CIRQ-F-001-G: Risk Assessment Register Template (p. 51)
- IS-CIRQ-D-005-G: Risk Treatment Plan Template (p. 53)
- **SOC2-Risk-Register-Live.md**: All 38 risks scored & mitigated (created)

**Current Status:**
- ✅ All 38 risks identified in Risk Register Live
- ✅ Risk scoring methodology applied (Impact × Likelihood = Risk Score)
- ✅ Mitigation strategies assigned per risk
- ✅ Risk owners documented (Chris Wren, Brenda Milian)

**Actions for Chris Wren:**
- [ ] Ensure Risk Assessment Procedure explicitly covers SOC 2 risk categories
- [ ] Confirm annual/quarterly risk review documented

---

## Part 3: Access Control & Authentication {#policy-008}

### Policy 008: Access Control Policy

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-008-G |
| **Status for SOC 2** | ✅ READY (critical for audit) |
| **Location in Manual** | p. 105 |
| **SOC 2 TSC Alignment** | *CC6.1, CC6.2, CC7.1, CC7.2, C1.1, C1.2* |

**Related Procedures & Evidence:**
- IS-CIRQ-PR-008-G: Access Control Procedure (p. 109)
- IS-CIRQ-PR-009-G: Privileged Access Management Procedure (p. 113)
- SOC2 Evidence: User provisioning requests, termination logs, quarterly access reviews

**Control Summary (from SOC2-System-Description.md):**
- ✅ RBAC model documented (8 role categories: IT Admin, Security, Mfg, Finance, Sales, HR, Eng, Users)
- ✅ Provisioning process: Ticketing-based approval
- ✅ Termination process: Documented with sign-off
- ✅ Quarterly access reviews by managers
- ✅ Annual comprehensive re-audit

**SOC 2 Control Coverage:**
- CC6.1: Segregation of duties (documented in procedures)
- CC6.2: Authorization/approval (ticketing system, manager approval)
- CC7.1: User access provisioning (documented procedure)
- CC7.2: User access review & revocation (quarterly reviews documented)

**Actions for Chris Wren:**
- [ ] Ensure all 58 users assigned to RBAC role matrix
- [ ] Collect Q1 2026 access reviews (signed by managers)
- [ ] Gather 2025-2026 termination records with access removal evidence
- [ ] Document emergency access procedures (if used)

---

## Part 4: Cryptography & Data Protection

### Policy 009: Cryptography Policy

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-009-G |
| **Status for SOC 2** | ✅ READY (encryption verification needed) |
| **Location in Manual** | p. 117 |
| **SOC 2 TSC Alignment** | C1.2 (Encryption of sensitive data in transit & at rest) |

**Related Procedures:**
- IS-CIRQ-PR-010-G: Key Management Procedure (p. 121)

**Current Encryption Status (from SOC2-System-Description.md):**
- ✅ **At-Rest:** TDE (databases), BitLocker (endpoints), AES-256 (backups)
- ✅ **In-Transit:** TLS 1.2+ (all network communications)
- ✅ **Backup Encryption:** AES-256
- ✅ **Key Management:** Documented procedures for key rotation, storage

**SOC 2 Control Coverage:**
- C1.2: Sensitive data encrypted at rest and in transit
- Encryption algorithms meet current standards
- Key management procedures documented

**Actions for Chris Wren:**
- [ ] Verify TLS 1.2+ on all endpoints (firewall, servers, cloud services)
- [ ] Confirm key rotation frequency documented (monthly/quarterly)
- [ ] Gather encryption configuration screenshots/reports

---

### Policy 006: Documented Information Control

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-006-G |
| **Status for SOC 2** | ✅ READY (data retention alignment) |
| **Location in Manual** | p. 81 |
| **SOC 2 TSC Alignment** | C1.1 (Data retention & deletion policies) |

**Related Procedures:**
- IS-CIRQ-PR-006-G: Document Control Procedure (p. 85)
- IS-CIRQ-F-003-G: Document Change Request Form (p. 89)

**Current Status:**
- ✅ Data classification defined (Manufacturing, Financial, Customer, Employee, Supplier, IT Logs)
- ✅ Retention periods documented per data type
- ✅ Deletion procedures defined

**Actions for Chris Wren:**
- [ ] Confirm data retention periods align with GDPR/CCPA requirements
- [ ] Document data deletion procedures with timestamps/audit trail

---

## Part 5: Physical & Environmental Security

### Policy 010: Physical & Environmental Security

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-010-G |
| **Status for SOC 2** | ✅ READY (access controls active) |
| **Location in Manual** | p. 125 |
| **SOC 2 TSC Alignment** | CC6.1, C1.2 (Physical access, environmental protection) |

**Related Procedures:**
- IS-CIRQ-PR-011-G: Physical Access Control Procedure (p. 129)
- IS-CIRQ-PR-012-G: Equipment Security Procedure (p. 133)

**Current Control Status (from SOC2-System-Description.md):**
- ✅ **Status: In Place (3 of 58 controls)**
- Physical access control system active at 3 locations (US, Taipei, China)
- Badge-based access, visitor logs
- Environmental monitoring (temperature, humidity for server rooms)

**SOC 2 Control Coverage:**
- CC6.1: Physical access restricted to authorized personnel
- C1.2: Environmental controls prevent data loss/destruction

**Actions for Chris Wren:**
- [ ] Collect physical access logs (Q1 2026)
- [ ] Document visitor sign-in procedures
- [ ] Gather environmental monitoring reports (temperature readings)
- [ ] Verify badge access system audit logs

---

## Part 6: Operations, Monitoring & Incident Response

### Policy 011: Operations Security

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-011-G |
| **Status for SOC 2** | ✅ READY (change mgmt + monitoring) |
| **Location in Manual** | p. 137 |
| **SOC 2 TSC Alignment** | *CC7.3, CC7.4, CC7.5, CC8.1, CC9.1* |

**Related Procedures:**
- IS-CIRQ-PR-013-G: Change Management Procedure (p. xxx)
- IS-CIRQ-PR-015-G: Logging & Monitoring Procedure (p. xxx)
- IS-CIRQ-PR-016-G: Network Security Management Procedure (p. xxx)

**Change Management (CC7.4: Change Management):**
- ✅ CAB approval documented
- ✅ Testing required before production
- ✅ Segregation of duties enforced
- ✅ Jira-tracked changes

**Monitoring & Logging (CC9.1: Monitoring & Detection):**
- ✅ Antivirus: Daily incident monitoring
- ✅ Firewall logs: Daily review, 30-90 day retention
- ✅ Server/Email logs: 30-90 day retention
- ✅ Incident Response plan: Being finalized Q1 2026

**Actions for Chris Wren:**
- [ ] Collect Q1 2026 change request logs (with CAB approvals)
- [ ] Gather antivirus incident reports
- [ ] Document change backout procedures
- [ ] Finalize Incident Response Plan

---

### Policy 014: Information Security Incident Management

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-014-G |
| **Status for SOC 2** | 🟡 PARTIAL (IR plan being finalized) |
| **Location in Manual** | p. xxx |
| **SOC 2 TSC Alignment** | *C1.4, C1.5* (Detection & response to security incidents) |

**Related Procedures:**
- IS-CIRQ-PR-020-G: Incident Response Procedure (Global Core) (p. xxx)
- IS-CIRQ-PR-020-US: Incident Response Procedure (US Localized) (p. xxx)
- IS-CIRQ-PR-020-ASIA: Incident Response Procedure (ASIA Localized) (p. xxx)
- IS-CIRQ-F-005-G: Incident Report Form (p. xxx)

**Current Status:**
- ✅ Incident detection: Antivirus, firewall alerts, user reports
- ✅ Investigation process: Initial assessment, containment planning
- ✅ Evidence preservation: Log archival procedures
- 🟡 Remediation: Being documented
- 🟡 External notification: Legal review required for GDPR/CCPA compliance

**SOC 2 Control Coverage:**
- C1.4: Detection and response to incidents
- C1.5: Response and recovery procedures

**Actions for Chris Wren (Priority 1 - Due Mar 8):**
- [ ] Complete Incident Response Plan (finalize procedures section)
- [ ] Define incident severity levels & escalation
- [ ] Document retention period for incident records (min. 1 year)
- [ ] Confirm external notification thresholds (privacy law compliance)

---

## Part 7: Business Continuity & Disaster Recovery

### Policy 015: Information Security Continuity

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-015-G |
| **Status for SOC 2** | ✅ READY (DR/BC verified) |
| **Location in Manual** | p. xxx |
| **SOC 2 TSC Alignment** | *C1.3* (Recovery of operations) |

**Related Procedures:**
- IS-CIRQ-PR-021-G: Business Continuity & Disaster Recovery Procedure (p. xxx)
- IS-CIRQ-D-008-G: Business Continuity Plan Template (p. xxx)
- IS-CIRQ-D-009-G: Disaster Recovery Plan Template (p. xxx)

**Current DR/BC Status (from SOC2-System-Description.md):**
- ✅ **RTO (Recovery Time Objective):** 4 hours (critical systems)
- ✅ **RPO (Recovery Point Objective):** 24 hours
- ✅ **Backup Strategy:** 30-day on-site + 90-day off-site archival
- ✅ **Testing:** Annual DR drills (most recent: 2025)
- ✅ **Documentation:** Procedures documented, contact lists current

**SOC 2 Control Coverage:**
- C1.3: System monitoring, recovery capability, testing

**Actions for Chris Wren:**
- [ ] Confirm 2025 DR drill results (attach report)
- [ ] Document Q1 2026 backup verification
- [ ] Verify contact list currency (March 2026)

---

## Part 8: Compliance & Vendor Management

### Policy 013: Supplier Relationships

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-013-G |
| **Status for SOC 2** | ✅ READY (vendor SOC 2 attestations) |
| **Location in Manual** | p. xxx |
| **SOC 2 TSC Alignment** | *CC9.2* (Vendor/third-party management) |

**Related Procedures:**
- IS-CIRQ-PR-019-G: Supplier Security Review Procedure (p. xxx)

**Key Vendors (from SOC2-System-Description.md):**
- Office 365 (Microsoft)
- NetSuite (Oracle)
- Salesforce
- ADP
- Tableau
- Okta
- Shopify (e-commerce, PCI-DSS out-of-scope)

**SOC 2 Control Coverage:**
- CC9.2: Vendor security assessment, contracts, SLA review

**Actions for Chris Wren:**
- [ ] Collect SOC 2 Type II attestations for Office 365, NetSuite, Salesforce (if available)
- [ ] Create Vendor Master List with risk ratings
- [ ] Document vendor contract security SLAs
- [ ] Gather vendor DPA/BAA agreements (GDPR compliance)

---

### Policy 016: Compliance

| Attribute | Value |
|---|---|
| **Document ID** | IS-CIRQ-P-016-G |
| **Status for SOC 2** | ✅ READY (audit readiness) |
| **Location in Manual** | p. xxx |
| **SOC 2 TSC Alignment** | *ALL* (overarching compliance framework) |

**Related Procedures & Forms:**
- IS-CIRQ-PR-023-G: Internal Audit Procedure (p. xxx)
- IS-CIRQ-PR-024-G: Management Review Procedure (p. xxx)
- IS-CIRQ-F-006-G: Internal Audit Report Template (p. xxx)
- IS-CIRQ-F-007-G: Management Review Meeting Minutes Template (p. xxx)

**Current Compliance Status:**
- ✅ Internal audit: Annual (last: 2025)
- ✅ Management review: Quarterly board meetings
- ✅ Corrective actions: CAR form process
- ✅ Nonconformity tracking: Documented process

**Actions for Chris Wren:**
- [ ] Confirm 2025 internal audit results align with SOC 2 controls
- [ ] Gather board meeting minutes (past 12 months)
- [ ] Document nonconformities & corrective actions (past 12 months)

---

## Part 9: SOC 2 Control Mapping Summary

**Total Controls in Scope:** 58 (Security Criteria only)
**Implementation Status:**
- ✅ In Place: 3 (physical access, admin access, antivirus patching)
- 🟡 Partially In Place: 1 (incident response - procedures incomplete)
- ❌ Not In Place: 54 (procedures need to be written)

**Priority Control Procedures Due (Batch 3 - March 15, 2026) [Select 10 highest-risk controls]:**
- [ ] CC-001: Access Control (Provisioning/Termination)
- [ ] CC-002: Change Management (Approval + Testing)
- [ ] CC-003: Incident Response (Detection + Investigation)
- [ ] CC-004: Encryption (Key Management)
- [ ] CC-005: Monitoring (Log Review)
- [ ] [ ] [6-10: TBD based on risk register]

**Full Procedure Set Due:** March 25, 2026

---

## Part 10: Evidence Collection Roadmap

| Category | Evidence Items | Due Date | Owner | Status |
|---|---|---|---|---|
| **Governance** | P0-01, P0-02, RACI, Org Chart | Mar 6 | Chris Wren | 🟢 Batch 1 Ready |
| **Policies** | 10 core policies copied/reviewed | Mar 13 | Chris Wren | 🟡 In Review |
| **System Docs** | Architecture, diagrams, inventory | Mar 16 | Chris Wren | 🟢 Batch 3 Ready |
| **Procedures** | 54 control procedures (10 priority) | Mar 15-25 | Chris Wren | ❌ Not Started |
| **Risk Register** | All 38 risks + mitigations | Mar 16 | Chris Wren | 🟢 Batch 4 Ready |
| **HR/Org** | Org chart, roles, training records | Mar 16 | Brenda Milian | 🟡 In Progress |
| **Operational** | Access logs, change logs, incidents | Mar 21 | Chris Wren | 🟡 Collecting |
| **Control Mappings** | SoA, gap matrix, risk scores | Mar 16 | Chris Wren | 🟢 Batch 3 Ready |

---

## Sign-Off Checklist

**For Chris Wren (IT/Security Lead) - Before Upload to Strike Graph:**

- [ ] All 10 core policies reviewed for SOC 2 alignment
- [ ] No contradictions between policies and procedures
- [ ] ISMS-Manual-SOC2-2026.md signed as accurate
- [ ] Ready to provide to Strike Graph integration manager

**For Executive Committee / Board:**

- [ ] P0-01 Scope Confirmation signed
- [ ] P0-02 Audit Timeline approved
- [ ] P0-03 RACI confirmed (from UB-05)
- [ ] Control freeze date acknowledged (April 1, 2026)

**For Finance/Compliance Lead:**

- [ ] Vendor contracts reviewed for SOC 2 requirements
- [ ] External audit scope confirmed
- [ ] Budget approved for any remediation work

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-02  
**Next Update:** Weekly during audit prep (through March 31, 2026)  
**Prepared by:** [You + AI assistant]  
**For:** Strike Graph SOC 2:2022 Type 1 Audit (Apr-Jun 2026)
