# ISMS Review + SOC 2 (Strike Graph) Implementation Plan

## 1) What appears to be missing right now

### A. Core artifacts are still template-level
Based on document content checks, many files still contain placeholders such as **"Export to Sheets"**, bracket fields (`[Date]`, `[Name]`, etc.), or explicit **Template** markers.

High-impact examples:
- `IS-CIRQ-D-006-G-Statement of Applicability (SoA) (Template).docx` is not fully populated.
- `IS-CIRQ-D-005-G-Risk Treatment Plan (RTP) (Template).docx` contains placeholder sections.
- `IS-CIRQ-F-001-G- Risk Assessment Register (Template).docx` is still template-based.
- `IS-CIRQ-D-002/003/004/007` registers/matrices include placeholders.
- Several management review/audit/CAR forms still show template placeholder text.

### B. Evidence model is not yet explicit
The policy/procedure set is broad, but SOC 2 readiness needs each control tied to:
- control owner
- operating frequency
- evidence artifact
- evidence location
- reviewer/approver
- exception handling

These are not consistently visible in the currently reviewed docs.

### C. SOC 2 control taxonomy and mapping are missing
Current docs are ISO 27001 oriented. For Strike Graph SOC 2, you still need a clean mapping to Trust Services Criteria (at minimum Security/CC-series), including:
- CC1.x, CC2.x, CC3.x, CC4.x, CC5.x, CC6.x, CC7.x, CC8.x, CC9.x
- entity-level controls vs technical controls
- “control to evidence” links in one system of record

### D. Risk management is present but not yet audit-operationalized
Risk policy/procedures exist, but for SOC 2 you need clear periodicity and governance outputs that auditors can test easily (risk universe, scoring, treatment, residual acceptance, linkage to controls and CAPAs).

### E. One procedure statement likely needs correction
`IS-CIRQ-PR-003-G` references **monthly internal audits**. That cadence may be unrealistic and can conflict with your actual operating model if not truly performed. Better to align audit cadence statements to your approved audit program and actual evidence.

---

## 2) Steps to clean up the ISMS package (practical order)

### Phase 1 (Week 1-2): Stabilize document quality
1. Freeze naming/versioning conventions and set one owner per document family (Policy, Procedure, Form, Register).
2. Remove all placeholder text (`Export to Sheets`, bracket fields) in in-scope docs.
3. Convert templates to operational records where required (SoA, Risk Register, RTP, Interested Parties, Legal Register, Objectives).
4. Align Approver fields (Executive Committee vs IT Manager) based on governance policy.
5. Ensure all docs have consistent: Effective Date, Review Date, Version, Owner, Approver.

### Phase 2 (Week 2-4): Make controls auditable
1. Build a single **Control Register** with columns:
   - Control ID
   - Control statement
   - TSC mapping (CCx.x)
   - Owner
   - Frequency
   - Evidence artifact
   - Evidence source/system
   - Reviewer
   - Last run date
   - Exceptions/CAPA link
2. Map each policy/procedure to control IDs.
3. Define evidence retention periods and storage path conventions.
4. Run a mock sample test (5–10 controls) to validate evidence can be produced quickly.

### Phase 3 (Week 4-6): Operational readiness
1. Complete SoA with full Annex A coverage and implementation status.
2. Reconcile risk register ↔ treatment plan ↔ controls ↔ management review outputs.
3. Execute one full internal audit cycle and one management review with complete records.
4. Track all findings in CAR register with root cause and closure evidence.

---

## 3) SOC 2 controls package to create for Strike Graph

Create these core control domains first (Security criteria):

1. **Entity-Level Governance (CC1, CC2)**
   - Code of conduct, roles/responsibilities, security governance, board/exec oversight.
2. **Risk Assessment (CC3)**
   - Formal risk identification, scoring, treatment tracking, residual approval.
3. **Change Management (CC8)**
   - Ticketing, approval, testing, segregation where possible, emergency changes.
4. **Logical Access (CC6)**
   - Joiner/mover/leaver, MFA, privileged access reviews, periodic recertification.
5. **Operations Security / Vulnerability / Patch (CC7)**
   - Vulnerability scans, remediation SLAs, endpoint/server hardening, monitoring.
6. **Logging and Monitoring (CC7)**
   - Log sources, alert triage, escalation, incident linkage.
7. **Incident Response (CC7)**
   - Incident workflow, severity model, response timing, post-incident review.
8. **Vendor Risk Management (CC9)**
   - Due diligence, contract security clauses, periodic reassessments.
9. **BCDR (A1.2 / Security availability support)**
   - Backup tests, DR exercises, RTO/RPO validation, lessons learned.

For each domain, add 2–5 key controls first; avoid overbuilding in round one.

---

## 4) Risk management workflow for Strike Graph SOC 2

Implement this minimum workflow:

1. **Risk universe definition**
   - Include systems, data classes, vendors, processes, and organizational risks.
2. **Scoring model**
   - Likelihood (1–5) × Impact (1–5) with defined criteria per score.
3. **Risk acceptance thresholds**
   - Define numeric thresholds for Accept / Treat / Escalate.
4. **Treatment tracking**
   - Every non-accepted risk must map to one or more control IDs and due dates.
5. **Residual approval**
   - Document approver, date, rationale, and expiration/review date.
6. **Review cadence**
   - Monthly review for high risks, quarterly for medium, annual full refresh.
7. **Governance output**
   - Management review includes risk trend, overdue treatments, exceptions, CAPAs.

Required fields in the risk register:
- Risk ID, Asset/Process, Threat, Vulnerability, Existing Controls, Inherent L/I/Score,
- Treatment Decision, Action Owner, Due Date,
- Residual L/I/Score, Residual Acceptance, Approval Evidence,
- Linked Control IDs, Linked Incident/CAR IDs, Last Reviewed.

---

## 5) 45-day execution plan (recommended)

### Days 1–10
- Complete all placeholders in core docs.
- Finalize SoA, Risk Register structure, RTP structure.
- Publish control ID convention (`CC-###`).

### Days 11–25
- Build SOC 2 control register and map to TSC + existing docs.
- Attach first evidence artifacts for top-priority controls.
- Remediate wording/cadence mismatches in procedures.

### Days 26–45
- Run internal readiness audit on selected controls.
- Close findings with CARs and evidence.
- Hold management review focused on SOC 2 readiness and risk posture.
- Enter finalized controls/evidence workflows into Strike Graph.

---

## 6) Suggested immediate next deliverables

1. `SOC2-Control-Register.xlsx` (or CSV) with TSC mapping.
2. Updated `IS-CIRQ-D-006-G` SoA (fully populated).
3. Updated `IS-CIRQ-F-001-G` Risk Register (live, not template).
4. Updated `IS-CIRQ-D-005-G` RTP with active risks and owners.
5. `SOC2-Evidence-Matrix.xlsx` linking controls to artifacts and systems.

---

## Notes
- `ISMS-Manual2025v1.docx` was file-locked during automated parsing, so this review is based on the full surrounding document set and extracted policy/procedure/template content.
- If needed, run one final pass against the manual text once it is closed/unlocked to confirm no conflicting language exists.
