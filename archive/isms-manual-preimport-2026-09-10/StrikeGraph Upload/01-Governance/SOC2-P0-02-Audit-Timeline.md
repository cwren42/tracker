# P0-02: SOC 2 Audit Timeline & Period Confirmation

**Document ID:** P0-02-TIMELINE-2026-03-02  
**Date Created:** March 2, 2026  
**Status:** Ready for Executive Approval  
**Owner:** Executive Committee  
**Approver:** Chris Wren (IT/Security Lead)

---

## Purpose
Formally document and approve the audit engagement timeline, including audit period type (Type 1 vs. Type 2), fieldwork dates, reporting schedule, and future audit roadmap.

---

## Audit Engagement Overview

### Current Engagement
**Audit Type:** SOC 2:2022 Type 1 Examination  
**Scope:** Security Trust Services Criteria Only  
**Service Organization:** Cirque Corporation (all locations and systems per P0-01 scope)  
**Service Auditor:** Strike Graph (SOC 2 audit platform & assessment provider)  
**Engagement Status:** Pre-fieldwork planning phase (now)

---

## Type 1 vs. Type 2: Decision Rationale

### SOC 2 Type 1
- **Objective:** Evaluate design and implementation of controls *at a point in time* (March/April 2026)
- **Period:** Single assessment point; no operational history required
- **Evidence needed:** Policies, procedures, control designs, one-time evidence of implementation
- **Timeline:** 6-8 weeks total (planning + fieldwork + reporting)
- **Cost:** Moderate
- **Auditor deliverable:** Type 1 SOC 2 report (point-in-time attestation)

### SOC 2 Type 2
- **Objective:** Evaluate design and *operating effectiveness* of controls over a period of time
- **Period:** Minimum 6 months of operational evidence required
- **Evidence needed:** Policies, procedures, AND 6 months of logs, tickets, records, incident evidence
- **Timeline:** 9-12 weeks total, but requires 6-month operational baseline first
- **Cost:** Higher
- **Auditor deliverable:** Type 2 SOC 2 report (demonstrates controls work in practice)

### Cirque's Decision: Start with Type 1
**Rationale:**
1. **Immediate business need:** Customers/partners requesting SOC 2 credibility within weeks (not 6+ months)
2. **Current maturity:** Many controls are "Not In Place" or recently implemented — insufficient operational history for Type 2
3. **Cost efficiency:** Type 1 can be completed in 6-8 weeks; Type 2 requires 6-month baseline PLUS audit time
4. **Phased approach:** Type 1 in Q2 2026 establishes baseline → Type 2 in Q4 2026 demonstrates operational stability

---

## Proposed Timeline

### Phase 1: Pre-Fieldwork (Now – March 31, 2026)
| Milestone | Date | Owner | Deliverable |
|---|---|---|---|
| Scope approval (P0-01) | Mar 2, 2026 | Exec Committee | Signed scope statement |
| Audit timeline approval (P0-02) | Mar 2, 2026 | Exec Committee | This document (signed) |
| RACI confirmation (P0-03) | Mar 3, 2026 | Exec Committee | Approved governance matrix |
| Evidence repository structure (P0-05) | Mar 5, 2026 | IT/Security | Evidence storage & naming standard |
| P1 tasks (ISMS cleanup) | Mar 8-15, 2026 | IT/HR/Security | Finalized control procedures |
| Control evidence collection begins | Mar 15, 2026 | Control owners | First batch of supporting evidence |

**Success criteria:** All scope, governance, and P1 cleanup complete by March 31, 2026.

### Phase 2: Fieldwork & Assessment (April 1 – May 15, 2026)
| Milestone | Date | Duration | Owner | Activity |
|---|---|---|---|---|
| Fieldwork kick-off | Apr 1, 2026 | 1 day | Strike Graph + Cirque | Entrance meeting; walkthrough of controls |
| Control testing & evidence review | Apr 2 - May 1, 2026 | 4 weeks | Strike Graph + Control owners | Deep-dive testing of each control's design & implementation |
| Management interviews | Apr 15-22, 2026 | 1 week | Strike Graph + Exec/Mgmt | Risk, governance, operations assessment |
| Site visits (if required) | May 1-5, 2026 | 1 week optional | Strike Graph | Physical access controls, facilities assessment |
| Evidence gap remediation | May 6-10, 2026 | 1 week | Control owners | Provide any missing/late evidence |
| Fieldwork conclusion | May 15, 2026 | — | Strike Graph | End of testing period |

**Success criteria:** All 58 controls tested; 90%+ evidence completeness by May 15.

### Phase 3: Reporting & Finalization (May 16 – June 30, 2026)
| Milestone | Date | Duration | Owner | Activity |
|---|---|---|---|---|
| Draft report delivery | May 30, 2026 | — | Strike Graph | Preliminary SOC 2 Type 1 report with findings |
| Review & remediation planning | Jun 1-15, 2026 | 2 weeks | Cirque | Address any findings; plan remediation (if needed) |
| Final report issuance | Jun 30, 2026 | — | Strike Graph | Signed SOC 2 Type 1 report (ready for customer/partner sharing) |
| Report distribution | Jul 1, 2026 | — | IT/Marketing | Share with customers, partners, investors as needed |

**Success criteria:** Type 1 report issued by June 30; zero critical findings; minor findings have documented remediation plans.

---

## Operational Timeline Markers

### Key Decision Points
1. **March 2, 2026 (Now):** Approve scope, timeline, control ID convention, evidence structure
2. **March 15, 2026:** Complete P1 ISMS cleanup; sync final procedures to Strike Graph
3. **April 1, 2026:** Fieldwork begins; no new control design changes allowed (freeze control scope)
4. **May 15, 2026:** Fieldwork ends; no new evidence collection starts
5. **June 30, 2026:** Final report issued; audit complete

### Resource Commitments
- **Exec Committee:** 4 hours (entrance + exit meetings + approval gates)
- **IT/Security Lead (Chris Wren):** 8-12 weeks part-time (~30% capacity)
- **Control owners:** 1-2 weeks mid-April (evidence gathering + interviews)
- **HR (Brenda Milian):** 1 week (HR controls, org governance, training records)
- **Finance/Operations:** 1 week (controls testing participation)

---

## Future Audit Roadmap

### Type 2 Engagement (Planned Q4 2026)
**Timing:** October 1 - December 31, 2026  
**Prerequisite:** Successful Type 1 report + 6 months of operational evidence (April 1 - September 30, 2026)  
**Benefit:** Demonstrates controls are effective in practice; required by some enterprise customers (Fortune 500, financial institutions)  
**Cost estimate:** 20-30% higher than Type 1  
**Decision point:** September 1, 2026 (confirm if Type 2 remains business priority)

### Additional Certifications (Future Phases)
- **Phase 2 (2027):** SOC 2 for Confidentiality/Availability/Privacy (if customer demand exists)
- **Phase 3 (2027-28):** ISO 27001 certification (comprehensive information security management)
- **Phase 4 (TBD):** Industry-specific compliance (PCI-DSS, HIPAA, NIST 800-53 if applicable)

---

## Control Change Freeze

**Effective:** April 1, 2026 (fieldwork begins)  
**Duration:** Through June 30, 2026 (report issuance)

**During this period:**
- ✅ **Allowed:** Bug fixes, emergency patches, operational improvements that don't change control design
- ✅ **Allowed:** Evidence collection and testing for existing controls
- ❌ **Not allowed:** New controls, control design changes, scope additions
- ❌ **Not allowed:** System migrations or major infrastructure changes

**Rationale:** Audit tests control design as of April 1. Changes during fieldwork complicate testing and delay reporting.

**Exception process:** If critical control change required (security incident, major bug), notify Strike Graph + Exec Committee immediately for scope amendment.

---

## Contingency Planning

### If critical issue discovered during fieldwork
1. **April-May:** Pause testing on affected control
2. **May 15 deadline extension:** May request 2-week extension (to May 31) with Executive Committee approval
3. **Report impact:** Finding documented in final report; can be resolved via remediation plan before customer sharing

### If evidence is incomplete
1. **May 6-10 remediation window:** Provide missing evidence for quick review
2. **If still incomplete:** Document as finding in report with remediation timeline
3. **Type 2 adds proof:** Next year's Type 2 audit validates remediation effectiveness

### If Strike Graph team unavailable
1. **Backup provider:** Identify backup SOC 2 audit firm by March 15 (escalate to Exec Committee)
2. **Delay:** Acceptable to postpone T1 to July-August if needed (Type 2 requirement remains 6-month baseline)

---

## Communication & Approval Gates

### Stakeholder Notifications
- **Employees:** Communication about audit by March 15 (training, email access changes, etc.)
- **Customers/Partners:** Notification that audit is underway (optional; consider sharing preliminary Type 1 upon completion)
- **Board/Investors:** Monthly updates on audit progress (optional but recommended)

### Executive Sign-Off Required
1. **Scope (P0-01):** ___________  Date: ________
2. **Timeline (P0-02):** ___________  Date: ________
3. **RACI (P0-03):** ___________  Date: ________
4. **Evidence structure (P0-05):** ___________  Date: ________

---

## Success Metrics

By June 30, 2026:
- ✅ Type 1 SOC 2 report issued
- ✅ **0 critical findings** in final report
- ✅ **≤3 major findings** (if any), each with documented remediation plan
- ✅ **All 58 controls** assessed as "design appropriate" or better
- ✅ Report approved for customer/partner distribution

---

## Next Steps

1. **Exec Committee approval** of this timeline (March 2, 2026)
2. **Send to Chris Wren** for IT/Security sign-off (March 2, 2026)
3. **Create P0-03 (RACI), P0-04 (Control IDs), P0-05 (Evidence structure)** (March 3-5, 2026)
4. **Begin P1 ISMS cleanup tasks** (March 8, 2026)
5. **First evidence collection checkpoint** (March 22, 2026)

---

## Reference Documents
- [P0-01-Scope-Confirmation.md](SOC2-P0-01-Scope-Confirmation.md)
- [SOC2-UB05-RACI-Draft.md](SOC2-UB05-RACI-Draft.md)
- [SOC2-StrikeGraph-Task-List.md](SOC2-StrikeGraph-Task-List.md)

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-02  
**Next Review:** April 1, 2026 (fieldwork kick-off)
