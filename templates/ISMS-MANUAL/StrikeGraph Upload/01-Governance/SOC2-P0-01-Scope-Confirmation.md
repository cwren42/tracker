# P0-01: SOC 2 Audit Scope Confirmation

**Document ID:** P0-01-SCOPE-2026-03-02  
**Date Created:** March 2, 2026  
**Status:** Ready for Executive Approval  
**Owner:** Executive Committee  
**Approver:** Chris Wren (IT/Security Lead)

---

## Purpose
Formally document and approve the scope of Cirque Corporation's SOC 2:2022 audit engagement with Strike Graph. This scope statement defines which entities, systems, locations, and data fall within the audit boundary.

---

## Scope Decision Summary

### 1. Trust Services Criteria In Scope
✅ **SOC 2:2022 Security (CC) Criteria Only**
- CC.1: Risk, Strategy, and Governance
- CC.2: Communications and Information
- CC.3: Risk Assessment
- CC.4: Monitoring Activities
- CC.5: Control Activities
- CC.6: Logical and Physical Access Controls
- CC.7: System Operations
- CC.8: Change Management
- CC.9: Risk Mitigation

❌ **Excluded from this audit cycle:**
- Availability (A.1.x) — Out of scope
- Confidentiality (C.1.x) — Out of scope
- Privacy (P.x.x) — Out of scope
- Processing Integrity (PI.x.x) — Out of scope

**Rationale:** Initial SOC 2 engagement focuses on foundational Security criteria. Confidentiality, Availability, Privacy, and Processing Integrity will be evaluated in Phase 2 (projected Q3 2026).

---

### 2. In-Scope Systems & Infrastructure

#### **On-Premises (In Scope)**
All production systems physically located at Cirque facilities are **in scope**, including:
- Manufacturing systems (US facility, Taipei facility, China facility)
- Engineering workstations and servers (on-site)
- Finance systems (on-site)
- HR systems (on-site, except cloud email/chat)
- Sales systems (on-site)
- Marketing systems (on-site)

**Physical Access Controls:** ✅ **Active and in scope**
- Card access systems at all facilities
- Visitor logging procedures
- Badge provisioning and termination
- Server room access controls

#### **Cloud Services (Partial Scope)**
The following cloud services are **in scope for security controls only**:
- Microsoft Office 365 (email, Teams, SharePoint) — Cirque-controlled access and authentication only
- Azure cloud services (if deployed) — Access controls and encryption in-scope

**Out of Scope:** Vendor-managed security controls on cloud platforms (e.g., Azure's infrastructure encryption; Cirque responsible only for logical access and data handling)

#### **Third-Party Systems (Limited Scope)**
- ERP/accounting software (on-premises): ✅ In scope
- HR platform (cloud-based): ✅ In scope (Cirque access/authentication controls)
- e-commerce platform (cloud): ✅ In scope (Cirque administrative controls)
- CDN / hosting partners: ❌ Out of scope (vendor responsibility; evaluate via SOC 2 attestation)

---

### 3. In-Scope Locations
| Location | Region | Status | Physical Controls |
|---|---|---|---|
| US Facility | North America | In Scope | ✅ Active |
| Taipei Facility | Asia-Pacific | In Scope | ✅ Active |
| China Facility | Asia-Pacific | In Scope | ✅ Active |
| Remote/Home Offices | Global | Partial (policy & training only) | Limited |

---

### 4. In-Scope Data Categories

#### **Company Data (In Scope)**
- Product designs and specifications
- Manufacturing data and BOMs
- Customer contract information
- Financial records
- Employee personnel records (limited to access controls; privacy assessment deferred to Phase 2)
- Supplier/vendor data
- System configurations and network diagrams

#### **Customer Data (Out of Scope for This Audit)**
- End-user personal data (privacy assessment deferred to Phase 2)
- Customer payment information (evaluated separately under PCI-DSS if applicable)
- Customer transaction logs (security controls in-scope; data sensitivity assessment deferred)

---

### 5. Organizational Entities Involved
✅ **In Scope:**
- Cirque Corporation (parent entity)
- All operating divisions and departments
- Management and governance structures
- All facilities and operations centers

❌ **Out of Scope:**
- Acquired entities or subsidiaries not yet integrated (none currently)
- Joint ventures (none currently)
- Franchise operations (none currently)

---

### 6. Audit Period & Type

**Audit Type:** SOC 2 Type 1  
**Recommended Engagement Timeline:**
- **Fieldwork period:** April - May 2026 (6 weeks)
- **Report issuance:** June 2026
- **Future:** Type 2 engagement recommended for Q4 2026 (minimum 6-month operational period)

---

### 7. Control Environment & Governance

**In Scope:**
- Board/executive oversight of security
- IT governance framework
- Risk assessment and management
- Control documentation and evidence
- Incident response capability
- Change management processes
- Vendor management and SLAs
- Security training and awareness

**Out of Scope (Phase 2):**
- Detailed data classification (security scope only; privacy classification deferred)
- End-user privacy rights processes
- Data residency compliance
- Industry-specific regulatory compliance (e.g., export control, HIPAA)

---

### 8. Exclusions & Constraints

1. **Compliance Exemptions:** This audit does NOT provide assessment of:
   - PCI-DSS compliance (separate engagement if required)
   - HIPAA/HITRUST (separate engagement if required)
   - SOC 2 Confidentiality/Privacy/Availability (Phase 2, 2026)
   - ISO 27001 certification (separate engagement if required)

2. **Vendor/Third-Party Responsibility:**
   - Cirque is responsible for vendor selection and contractual SLA verification
   - Cirque is NOT responsible for vendor's internal security controls (except where documented in SOC 2 attestations)
   - Third-party cloud provider uptime/availability: Out of scope (vendor's responsibility)

3. **Post-Audit Support:**
   - Remediation support beyond audit finding documentation: Out of scope (deferred to P4-5 tasks if needed)

---

### 9. Evidence & Documentation Scope

**Cirque must provide/maintain:**
- ✅ Policy documents and procedures
- ✅ Evidence of control execution (logs, records, screenshots)
- ✅ Incident logs and response records
- ✅ Change management records
- ✅ Risk assessment documentation
- ✅ Training attendance records
- ✅ Access control evidence (provisioning, reviews, termination)

**Strike Graph will assess:**
- Completeness and design of controls
- Evidence quality and timeliness
- Risk mitigation effectiveness

---

### 10. System Descriptions & Boundaries

**In-Scope System Boundary:** All systems that process, store, or transmit confidential company data within Cirque's controlled environment (on-premises or cloud systems with Cirque administrative access).

**Data Flow:** Data flowing between in-scope systems (manufacturing → finance, HR → payroll, sales → fulfillment, etc.) is in scope for access controls and encryption.

---

## Executive Approval Section

**This scope statement defines the boundary for the SOC 2:2022 audit engagement. All deviations must be documented and approved.**

| Role | Name | Signature | Date |
|---|---|---|---|
| IT/Security Lead | Chris Wren | _________________ | ________ |
| Board/Governance | [BDM/Board Member] | _________________ | ________ |
| Finance/Operations | [CFO or COO] | _________________ | ________ |

---

## Sign-Off

**I/We confirm that this scope statement accurately reflects Cirque Corporation's intended audit boundary and authorize commencement of SOC 2:2022 fieldwork under these scope parameters.**

Signed: _________________________  
Date: _________________________  
Title: _________________________

---

## Reference Documents
- [SOC2-StrikeGraph-ISMS-Plan.md](SOC2-StrikeGraph-ISMS-Plan.md)
- [SOC2-Control-Gap-Matrix.csv](SOC2-Control-Gap-Matrix.csv)
- [SOC2-Risk-Scoring-Final.csv](SOC2-Risk-Scoring-Final.csv)
- [SOC2-UB05-RACI-Draft.md](SOC2-UB05-RACI-Draft.md)

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-02  
**Next Review:** Post-audit (June 2026)
