# ISMS Master - Table of Contents & Document Repository
**Cirque Corporation | Information Security Management System**

**Master Document Version:** 1.0 (Markdown Edition)  
**Original Source:** ISMS-Manual2025v1.docx  
**Conversion Date:** March 2, 2026  
**Purpose:** Centralized ISMS documentation with individual policy/procedure files for ease of editing and SOC 2 audit preparation

---

## 📋 Master Table of Contents

> **Naming note:** Canonical editable files use the `IS-CIRQ-... .md` naming format in this workspace. Any short-form `File:` labels below are legacy aliases from the original conversion outline.

### **PART 1: Governance & Framework (Pages 1-30)**

1. **[IS-CIRQ-P-001-G: Information Security Policy (Master Policy)](#governance-documents)**
   - File: `P-001-Information-Security-Policy.md`
   - Scope, objectives, roles, compliance commitment
   - SOC 2 Alignment: ALL Trust Service Criteria

2. **[IS-CIRQ-PR-001-G: ISMS Scope Definition Procedure](#governance-documents)**
   - File: `PR-001-ISMS-Scope-Definition-Procedure.md`
   - System boundaries, in-scope/out-of-scope definition

3. **[IS-CIRQ-D-001-G: ISMS Scope Document](#governance-documents)**
   - File: `D-001-ISMS-Scope-Document.md`
   - Detailed scope statement, locations, data types

4. **[IS-CIRQ-D-002-G: Interested Parties & Requirements Register](#governance-documents)**
   - File: `D-002-Interested-Parties-Register.md`
   - Stakeholder mapping, external requirements

5. **[IS-CIRQ-D-003-G: Legal, Regulatory & Contractual Requirements (Global)](#governance-documents)**
   - File: `D-003-G-Legal-Regulatory-Requirements.md`
   - GDPR, CCPA, PCI-DSS, SOC 2 requirements mapping

6. **[IS-CIRQ-D-003-US: Legal Requirements (US Localized)](#governance-documents)**
   - File: `D-003-US-Legal-Requirements.md`
   - US-specific compliance requirements

7. **[IS-CIRQ-D-003-ASIA: Legal Requirements (ASIA Localized)](#governance-documents)**
   - File: `D-003-ASIA-Legal-Requirements.md`
   - ASIA-specific compliance requirements

---

### **PART 2: Roles, Responsibilities & Authorities (Pages 31-40)**

8. **[IS-CIRQ-P-002-G: Roles, Responsibilities & Authorities Policy](#roles-documents)**
   - File: `P-002-Roles-Responsibilities-Policy.md`
   - Role definitions, approval authorities, escalation
   - SOC 2 Alignment: CC6.1, CC6.2

9. **[IS-CIRQ-D-004-G: Information Security Roles Matrix](#roles-documents)**
   - File: `D-004-Information-Security-Roles-Matrix.md`
   - RACI matrix, role-to-control mapping

10. **[SOC2-UB05-RACI-Draft.md: SOC 2 Audit-Specific RACI](#roles-documents)**
    - File: `SOC2-UB05-RACI-Draft.md` (created separately)
    - Chris Wren (IT/Security), Brenda Milian (HR), Executive Committee

---

### **PART 3: Risk Management (Pages 39-60)**

11. **[IS-CIRQ-P-003-G: Risk Management Policy](#risk-documents)**
    - File: `P-003-Risk-Management-Policy.md`
    - Risk assessment methodology, scoring, treatment
    - SOC 2 Alignment: RA1, RA2, RA3, RA4, RA5

12. **[IS-CIRQ-PR-002-G: Information Security Risk Assessment Procedure](#risk-documents)**
    - File: `PR-002-Risk-Assessment-Procedure.md`
    - Detailed risk assessment steps, frequency, ownership

13. **[IS-CIRQ-PR-003-G: Information Security Risk Treatment Procedure](#risk-documents)**
    - File: `PR-003-Risk-Treatment-Procedure.md`
    - Remediation planning, risk acceptance criteria

14. **[IS-CIRQ-F-001-G: Risk Assessment Register (Template)](#risk-documents)**
    - File: `F-001-Risk-Assessment-Register.md`
    - Forms template for risk tracking

15. **[IS-CIRQ-D-005-G: Risk Treatment Plan (RTP) (Template)](#risk-documents)**
    - File: `D-005-Risk-Treatment-Plan-Template.md`
    - Risk mitigation planning template

16. **[IS-CIRQ-D-007-G: Information Security Objectives](#risk-documents)**
    - File: `D-007-Information-Security-Objectives.md`
    - SMART goals aligned to ISO 27001 / SOC 2

17. **[SOC2-Risk-Register-Live.md: Live Risk Register (38 Risks)](#risk-documents)**
    - File: `SOC2-Risk-Register-Live.md` (created separately)
    - All active risks, scoring, mitigation strategies, timelines

---

### **PART 4: Competence, Awareness & Training (Pages 65-72)**

18. **[IS-CIRQ-P-004-G: Competence, Awareness & Training Policy](#training-documents)**
    - File: `P-004-Training-Policy.md`
    - Employee security training requirements and accountability
    - SOC 2 Alignment: CC6.1 (Awareness)

19. **[IS-CIRQ-PR-004-G: Information Security Awareness & Training Procedure](#training-documents)**
    - File: `PR-004-Training-Procedure.md`
    - Training delivery, tracking, effectiveness measurement

20. **[IS-CIRQ-F-002-G: Training Records Log](#training-documents)**
    - File: `F-002-Training-Records-Log.md`
    - Training attendance tracking template

---

### **PART 5: Communication (Pages 73-80)**

21. **[IS-CIRQ-P-005-G: Communication Policy](#communication-documents)**
    - File: `P-005-Communication-Policy.md`
    - Internal/external security communications, escalation

22. **[IS-CIRQ-PR-005-G: ISMS Communication Procedure](#communication-documents)**
    - File: `PR-005-Communication-Procedure.md`
    - Incident notification, security alerts, stakeholder updates

---

### **PART 6: Documented Information Control (Pages 81-92)**

23. **[IS-CIRQ-P-006-G: Documented Information Control Policy](#documentation-documents)**
    - File: `P-006-Documentation-Control-Policy.md`
    - Document management, retention, classification
    - SOC 2 Alignment: C1.1 (Data retention)

24. **[IS-CIRQ-PR-006-G: Document Control Procedure](#documentation-documents)**
    - File: `PR-006-Document-Control-Procedure.md`
    - Version control, approval, archival

25. **[IS-CIRQ-F-003-G: Document Change Request Form](#documentation-documents)**
    - File: `F-003-Document-Change-Request-Form.md`
    - Document update tracking template

---

### **PART 7: Asset Management (Pages 93-104)**

26. **[IS-CIRQ-P-007-G: Asset Management Policy](#asset-documents)**
    - File: `P-007-Asset-Management-Policy.md`
    - Asset inventory, classification, accountability
    - SOC 2 Alignment: CC6.1 (Asset inventory)

27. **[IS-CIRQ-PR-007-G: Asset Classification & Handling Procedure](#asset-documents)**
    - File: `PR-007-Asset-Classification-Procedure.md`
    - Asset tagging, data classification, secure handling

28. **[IS-CIRQ-F-004-G: Asset Register](#asset-documents)**
    - File: `F-004-Asset-Register.md`
    - Master asset inventory template

---

### **PART 7.5: Data Classification (Pages 105-120)**

29. **[IS-CIRQ-P-026-G: Data Classification Policy](#data-classification-documents)**
    - File: `P-026-Data-Classification-Policy.md`
    - Four-tier classification framework (Confidential, Restricted, Internal, Public)
    - Data lifecycle management, encryption requirements, access controls
    - SOC 2 Alignment: CC6.1, CC6.2, CC7.1, CC7.2, C1.1

---

### **PART 8: Access Control (Pages 121-132)**

30. **[IS-CIRQ-P-008-G: Access Control Policy](#access-documents)**
    - File: `P-008-Access-Control-Policy.md`
    - User provisioning, termination, review procedures
    - SOC 2 Alignment: CC6.1, CC6.2, CC7.1, CC7.2, C1.1, C1.2

31. **[IS-CIRQ-PR-008-G: Access Control Procedure](#access-documents)**
    - File: `PR-008-Access-Control-Procedure.md`
    - Detailed provisioning/termination steps, approval chain

32. **[IS-CIRQ-PR-009-G: Privileged Access Management Procedure](#access-documents)**
    - File: `PR-009-PAM-Procedure.md`
    - Admin account management, MFA, session logging
    - SOC 2 Alignment: CC6.1 (Segregation of duties)

---

### **PART 9: Cryptography (Pages 133-140)**

33. **[IS-CIRQ-P-009-G: Cryptography Policy](#crypto-documents)**
    - File: `P-009-Cryptography-Policy.md`
    - Encryption standards, key management, algorithms
    - SOC 2 Alignment: C1.2 (Encryption at rest/in transit)

33. **[IS-CIRQ-PR-010-G: Key Management Procedure](#crypto-documents)**
    - File: `PR-010-Key-Management-Procedure.md`
    - Key generation, storage, rotation, destruction

---

### **PART 10: Physical & Environmental Security (Pages 141-154)**

35. **[IS-CIRQ-P-010-G: Physical & Environmental Security Policy](#physical-documents)**
    - File: `P-010-Physical-Security-Policy.md`
    - Physical access controls, environmental monitoring
    - SOC 2 Alignment: CC6.1, C1.2

36. **[IS-CIRQ-PR-011-G: Physical Access Control Procedure](#physical-documents)**
    - File: `PR-011-Physical-Access-Procedure.md`
    - Badge systems, visitor logs, access reviews

36. **[IS-CIRQ-PR-012-G: Equipment Security Procedure](#physical-documents)**
    - File: `PR-012-Equipment-Security-Procedure.md`
    - Device disposal, secure wiping, transport security

---

### **PART 11: Operations Security (Pages 155-176)**

37. **[IS-CIRQ-P-011-G: Operations Security Policy](#operations-documents)**
    - File: `P-011-Operations-Security-Policy.md`
    - Change management, monitoring, logging, incident response
    - SOC 2 Alignment: CC7.4, CC8.1, CC9.1

38. **[IS-CIRQ-PR-013-G: Change Management Procedure](#operations-documents)**
    - File: `PR-013-Change-Management-Procedure.md`
    - CAB approval, testing, SoD, emergency changes
    - SOC 2 Alignment: CC7.4 (Change management)

39. **[IS-CIRQ-PR-015-G: Logging & Monitoring Procedure](#operations-documents)**
    - File: `PR-015-Logging-Monitoring-Procedure.md`
    - Log collection, retention, analysis, alerting
    - SOC 2 Alignment: CC9.1 (Monitoring & detection)

40. **[IS-CIRQ-PR-016-G: Network Security Management Procedure](#operations-documents)**
    - File: `PR-016-Network-Security-Procedure.md`
    - Firewall rules, segmentation, access controls

---

### **PART 12: Secure Development (Pages 153-164)**

41. **[IS-CIRQ-P-012-G: Secure System Acquisition, Development & Maintenance Policy](#development-documents)**
    - File: `P-012-Secure-Development-Policy.md`
    - Secure coding, code review, testing requirements

42. **[IS-CIRQ-PR-017-G: Secure Development Procedure](#development-documents)**
    - File: `PR-017-Secure-Development-Procedure.md`
    - SDLC security gates, code review checklist

43. **[IS-CIRQ-PR-018-G: System Testing & Acceptance Procedure](#development-documents)**
    - File: `PR-018-System-Testing-Procedure.md`
    - Penetration testing, security acceptance criteria

---

### **PART 13: Supplier Relationships (Pages 165-176)**

44. **[IS-CIRQ-P-013-G: Supplier Relationships Policy](#supplier-documents)**
    - File: `P-013-Supplier-Policy.md`
    - Vendor security assessment, contracts, SLAs
    - SOC 2 Alignment: CC9.2 (Vendor management)

45. **[IS-CIRQ-PR-019-G: Supplier Security Review Procedure](#supplier-documents)**
    - File: `PR-019-Supplier-Security-Review-Procedure.md`
    - Vendor risk assessment, SOC 2 attestation verification

---

### **PART 14: Information Security Incident Management (Pages 177-190)**

46. **[IS-CIRQ-P-014-G: Information Security Incident Management Policy](#incident-documents)**
    - File: `P-014-Incident-Management-Policy.md`
    - Incident definition, reporting, escalation
    - SOC 2 Alignment: C1.4, C1.5

47. **[IS-CIRQ-PR-020-G: Incident Response Procedure (Global Core)](#incident-documents)**
    - File: `PR-020-G-Incident-Response-Procedure.md`
    - Detection, investigation, containment, recovery
    - Includes GDPR/CCPA notification thresholds

48. **[IS-CIRQ-PR-020-US: Incident Response Procedure (US Localized)](#incident-documents)**
    - File: `PR-020-US-Incident-Response-Procedure.md`
    - US state-specific breach notification requirements

49. **[IS-CIRQ-PR-020-ASIA: Incident Response Procedure (ASIA Localized)](#incident-documents)**
    - File: `PR-020-ASIA-Incident-Response-Procedure.md`
    - ASIA regional breach notification requirements

50. **[IS-CIRQ-F-005-G: Incident Report Form](#incident-documents)**
    - File: `F-005-Incident-Report-Form.md`
    - Incident tracking template

---

### **PART 15: Information Security Continuity (Pages 191-204)**

51. **[IS-CIRQ-P-015-G: Information Security Continuity Policy](#continuity-documents)**
    - File: `P-015-Continuity-Policy.md`
    - Business continuity, disaster recovery, testing
    - SOC 2 Alignment: C1.3 (Recovery capability)

52. **[IS-CIRQ-PR-021-G: Business Continuity & Disaster Recovery Procedure](#continuity-documents)**
    - File: `PR-021-BCDR-Procedure.md`
    - RTO/RPO, backup/restore procedures, dr testing

53. **[IS-CIRQ-D-008-G: Business Continuity Plan (BCP) Template](#continuity-documents)**
    - File: `D-008-BCP-Template.md`
    - BC planning template with recovery strategies

54. **[IS-CIRQ-D-009-G: Disaster Recovery Plan (DRP) Template](#continuity-documents)**
    - File: `D-009-DRP-Template.md`
    - DR planning template with failover procedures

---

### **PART 16: Compliance & Audit (Pages 205-220)**

55. **[IS-CIRQ-P-016-G: Compliance Policy](#compliance-documents)**
    - File: `P-016-Compliance-Policy.md`
    - Internal audit, management review, corrective actions
    - SOC 2 Alignment: ALL (overarching compliance)

56. **[IS-CIRQ-PR-022-G: ISMS Performance Monitoring Procedure](#compliance-documents)**
    - File: `PR-022-Performance-Monitoring-Procedure.md`
    - KPI tracking, metrics, dashboards

57. **[IS-CIRQ-PR-023-G: Internal Audit Procedure](#compliance-documents)**
    - File: `PR-023-Internal-Audit-Procedure.md`
    - Audit scope, frequency, finding classification

58. **[IS-CIRQ-PR-024-G: Management Review Procedure](#compliance-documents)**
    - File: `PR-024-Management-Review-Procedure.md`
    - Board-level review, effectiveness assessment

59. **[IS-CIRQ-PR-025-G: Corrective Action Procedure](#compliance-documents)**
    - File: `PR-025-Corrective-Action-Procedure.md`
    - CAR process, root cause analysis, remediation tracking

60. **[IS-CIRQ-F-006-G: Internal Audit Report Template](#compliance-documents)**
    - File: `F-006-Internal-Audit-Report.md`
    - Audit finding documentation template

61. **[IS-CIRQ-F-007-G: Management Review Meeting Minutes Template](#compliance-documents)**
    - File: `F-007-Management-Review-Minutes.md`
    - Board meeting minutes template

62. **[IS-CIRQ-F-008-G: Corrective Action Request (CAR) Form](#compliance-documents)**
    - File: `F-008-CAR-Form.md`
    - CAR tracking and closure template

---

### **PART 17: Privacy Policies (Pages 221-240)**

63. **[IS-CIRQ-P-017-G: Privacy Policy (Global Core)](#privacy-documents)**
    - File: `P-017-G-Privacy-Policy.md`
    - GDPR/CCPA compliant global privacy policy

64. **[IS-CIRQ-P-017-US: Privacy Policy (US Localized)](#privacy-documents)**
    - File: `P-017-US-Privacy-Policy.md`
    - CCPA, state-level privacy requirements

65. **[IS-CIRQ-P-017-ASIA: Privacy Policy (ASIA Localized)](#privacy-documents)**
    - File: `P-017-ASIA-Privacy-Policy.md`
    - PDPA and regional privacy requirements

---

### **PART 18: Workplace Policies (Pages 241-258)**

66. **[IS-CIRQ-P-018-G: Clear Desk & Clear Screen Policy](#workplace-documents)**
    - File: `P-018-Clear-Desk-Policy.md`
    - Safe working practices, physical security

67. **[IS-CIRQ-P-019-G: Acceptable Use Policy](#workplace-documents)**
    - File: `P-019-Acceptable-Use-Policy.md`
    - Proper use of IT resources, consequences

68. **[IS-CIRQ-P-020-G: Remote Work Policy](#workplace-documents)**
    - File: `P-020-Remote-Work-Policy.md`
    - Telework security requirements, VPN, encryption

69. **[IS-CIRQ-P-025-G: Artificial Intelligence Acceptable Use Policy](#workplace-documents)**
    - File: `P-025-AI-Acceptable-Use-Policy.md`
    - Approved AI tools, data restrictions, AI use-case approvals

---

### **PART 19: Management & Improvement (Pages 259-274)**

70. **[IS-CIRQ-P-021-G: Monitoring, Measurement, Analysis & Evaluation Policy](#management-documents)**
    - File: `P-021-MMAE-Policy.md`
    - Performance metrics, data analysis

57. **[IS-CIRQ-P-022-G: Management Review Policy](#management-documents)**
    - File: `P-022-Management-Review-Policy.md`
    - Board governance, decision authority

58. **[IS-CIRQ-P-023-G: Nonconformity & Corrective Action Policy](#management-documents)**
    - File: `P-023-Nonconformity-Policy.md`
    - Non-conformance handling, remediation

59. **[IS-CIRQ-P-024-G: Continual Improvement Policy](#management-documents)**
    - File: `P-024-Continual-Improvement-Policy.md`
    - Kaizen approach to ISMS enhancement

---

## 📁 SOC 2-Specific Supplementary Documents

74. **[SOC2-Statement-of-Applicability.csv](#soc2-documents)**
    - File: `SOC2-Statement-of-Applicability.csv`
    - All 58 controls mapped to ISO 27001 + SOC 2 TSC + CC criteria

75. **[SOC2-Control-Gap-Matrix.csv](#soc2-documents)**
    - File: `SOC2-Control-Gap-Matrix.csv`
    - Implementation status (Not In Place / Partial / In Place)

76. **[SOC2-Risk-Register-Live.md](#soc2-documents)** 
    - File: `SOC2-Risk-Register-Live.md`
    - All 38 active risks with mitigation strategies

77. **[SOC2-System-Description.md](#soc2-documents)**
    - File: `SOC2-System-Description.md`
    - Complete architecture, components, data flows

78. **[SOC2-Evidence-Requirements-Roadmap.md](#soc2-documents)**
    - File: `SOC2-Evidence-Requirements-Roadmap.md`
    - 70+ evidence items, collection roadmap, owner matrix

---

## 🗂️ File Organization & Structure

```
ISMS-MANUAL/
├── ISMS-Master.md (THIS FILE - Master Table of Contents)
│
├── StrikeGraph Upload/ (Evidence upload repository)
│   ├── 01-Governance/ (Contains P-001, PR-001, D-001-007, etc.)
│   ├── 02-Policies/ (Contains P-002 through P-026)
│   ├── 03-System-Documentation/ (System Description, diagrams)
│   ├── 04-Control-Procedures/ (CC-001 through CC-058 procedures)
│   ├── 05-Risk-Management/ (Risk Register, assessments)
│   ├── 06-HR-Organizational/ (Org chart, roles, training)
│   ├── 07-Operational-Evidence/ (Logs, access records, incidents)
│   ├── 08-Control-Mappings/ (SoA, gaps, risk scores)
│   └── 09-Strike-Graph-Integration/ (Upload tracker, manifest)
│
└── [Individual Policy Files - Keep master copies in root]
    ├── P-001-Information-Security-Policy.md
    ├── P-002-Roles-Responsibilities-Policy.md
    ├── ... (all 26 policies)
    ├── PR-001-ISMS-Scope-Definition-Procedure.md
    ├── ... (all procedures)
    ├── D-001-ISMS-Scope-Document.md
    ├── ... (all documents)
    ├── F-001-Risk-Assessment-Register.md
    ├── ... (all forms)
    └── SOC2-*.md (all SOC2 supplementary docs)
```

---

## ✅ Quick Status Summary

| Category | Files | Status | Notes |
|---|---|---|---|
| **Governance** | 7 docs | ✅ Extracted | Ready for individual file creation |
| **Policies** | 25 policies | ✅ Extracted | Need individual MD files |
| **Procedures** | 25 procedures | ✅ Extracted | Need individual MD files |
| **Forms/Templates** | 8 forms | ✅ Extracted | Need individual MD files |
| **SOC 2 Specific** | 5 docs | ✅ Created | Already in StrikeGraph Upload folder |
| **TOTAL** | ~72 documents | 🟡 In Progress | Creating individual MD files next |

---

## 🎯 Next Steps

1. **Create Individual Policy Files** - Extract each policy/procedure into separate markdown file
2. **Copy Batch Files to StrikeGraph Upload** - Core policies → 02-Policies; Procedures → 04-Control-Procedures
3. **Collect Operational Evidence** - Access logs, change records, incident reports
4. **Upload to Strike Graph** - Per 5-batch schedule (March 5-21)

---

**Master Document Status:** CREATED  
**Last Updated:** 2026-03-02  
**Managed By:** Chris Wren (IT/Security Lead)  
**For:** SOC 2:2022 Type 1 Audit (April-June 2026)

---

### Document Index by Type

**Policies (P-00x):** P-001, P-002, P-003, P-004, P-005, P-006, P-007, P-008, P-009, P-010, P-011, P-012, P-013, P-014, P-015, P-016, P-017-G/US/ASIA, P-018, P-019, P-020, P-021, P-022, P-023, P-024, P-025

**Procedures (PR-00x):** PR-001, PR-002, PR-003, PR-004, PR-005, PR-006, PR-007, PR-008, PR-009, PR-010, PR-011, PR-012, PR-013, PR-014, PR-015, PR-016, PR-017, PR-018, PR-019, PR-020-G/US/ASIA, PR-021, PR-022, PR-023, PR-024, PR-025

**Documents (D-00x):** D-001, D-002, D-003-G/US/ASIA, D-004, D-005, D-006, D-007, D-008, D-009

**Forms (F-00x):** F-001, F-002, F-003, F-004, F-005, F-006, F-007, F-008

**SOC 2 Specific:** SOC2-Statement-of-Applicability.csv, SOC2-Control-Gap-Matrix.csv, SOC2-Risk-Register-Live.md, SOC2-System-Description.md, SOC2-Evidence-Requirements-Roadmap.md
