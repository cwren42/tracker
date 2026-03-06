# SOC 2 + Strike Graph Task List (Execution Tracker)

## How to use
- Set an owner and due date for every open task.
- Keep status values to: `Not Started`, `In Progress`, `Blocked`, `Done`.
- Attach evidence links/filenames as each task completes.

## Current snapshot (from Strike Graph CSV export on 2026-03-02)
- Controls total: **58**
- `Not In Place`: **54**
- `Partially In Place`: **1**
- `In Place`: **3**
- Controls missing owner: **0** ✅ (resolved UB-01)
- Risks total: **38**
- Risks missing owner: **0** ✅ (resolved UB-02)
- Risks unscored: **0** ✅ (resolved UB-03/04; all scored with scope decisions applied)
- Scope decision status: **All 7 conditional risks resolved** ✅ (UB-04)
- **🎯 ALL UNBLOCKERS COMPLETE: 5/5** ✅ (UB-01, UB-02, UB-03, UB-04, UB-05 approved)

## Immediate unblockers (complete before other work)
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| UB-01 | Assign owners to unowned controls (`Incident Response: Employee Responsibility`, `Third Party SOC2`) | Chris Wren | 2026-03-02 | Done | Updated control register |
| UB-02 | Assign a risk owner for each of 38 active risks | Chris Wren | 2026-03-02 | Done | Updated risk cleanup register |
| UB-03 | Score 37 unscored risks (impact, likelihood, combined score) | Chris Wren | 2026-03-02 | Done | `SOC2-Risk-Scoring-Final.csv` created (all 38 scored) |
| UB-04 | Decide scope treatment for 7 conditional risks (deactivate or score/treat) | Executive Committee | 2026-03-02 | Done | Scope decisions applied: Security-only; keep Env + PAC; deactivate CA/CI/P |
| UB-05 | Align control/risk owners with RACI and management approval | Executive Committee | 2026-03-02 | Done | `SOC2-UB05-RACI-Draft.md` approved and signed off |

## Control & Risk Upload Status
**⚠️ CRITICAL TRACKING**: As we finalize each control/risk/doc, track its upload to Strike Graph in [SOC2-StrikeGraph-Upload-Tracker.md](SOC2-StrikeGraph-Upload-Tracker.md).
- 58 Controls ready for upload (see [SOC2-Control-Gap-Matrix.csv](SOC2-Control-Gap-Matrix.csv))
- 38 Risks finalized and scored (see [SOC2-Risk-Scoring-Final.csv](SOC2-Risk-Scoring-Final.csv))
- RACI to be approved via [SOC2-UB05-RACI-Draft.md](SOC2-UB05-RACI-Draft.md)

## ⚠️ NEXT IMMEDIATE ACTIONS
1. **Sign off on UB-05:** Get executive approval on `SOC2-UB05-RACI-Draft.md` (defines owners and review cycle)
2. **Upload trigger:** Once UB-05 signed, immediately upload all 58 controls + 38 risks to Strike Graph and track in `SOC2-StrikeGraph-Upload-Tracker.md`
3. **Then begin P0 series:** Confirm scope, audit timeline, and governance in writing

---

## Priority 0 — Program setup (Week 1)
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| P0-01 | Confirm SOC 2 scope (entities, systems, products, locations, in-scope data) |  |  | Not Started | Approved scope statement |
| P0-02 | Confirm audit period target (Type 1 vs Type 2 timeline) |  |  | Not Started | Audit timeline memo |
| P0-03 | Approve governance RACI for SOC 2 program |  |  | Not Started | RACI matrix |
| P0-04 | Define control ID convention (`CC-###`) |  |  | Not Started | Control naming standard |
| P0-05 | Approve evidence repository structure and retention rules |  |  | Not Started | Evidence storage standard |

## Priority 1 — ISMS cleanup and document hardening (Week 1–2)
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| P1-01 | Remove all `Export to Sheets` placeholders from in-scope docs | Chris Wren | 2026-03-02 | Done | Root + StrikeGraph `IS-CIRQ-*.md` files cleaned |
| P1-02 | Remove bracket placeholders (`[Date]`, `[Name]`, etc.) from active docs | Chris Wren | 2026-03-02 | Done | Active docs cleaned of unresolved `XXX/TBD` placeholders |
| P1-03 | Finalize Statement of Applicability (`IS-CIRQ-D-006-G`) |  |  | Not Started | Completed SoA |
| P1-04 | Convert Risk Register template to live register (`IS-CIRQ-F-001-G`) |  |  | Not Started | Live risk register |
| P1-05 | Convert RTP template to active plans (`IS-CIRQ-D-005-G`) |  |  | Not Started | Active RTP entries |
| P1-06 | Standardize metadata (version/effective/review/approver) across policy+procedure set |  |  | Not Started | Controlled document index |
| P1-07 | Correct cadence wording where not operationally accurate (e.g., audit frequency statements) |  |  | Not Started | Updated procedures + change record |

## Priority 2 — Controls build for Strike Graph (Week 2–4)
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| P2-01 | Build SOC 2 control register mapped to TSC (CC1–CC9) | Chris Wren | 2026-03-02 | Done | Full 58-control matrix completed in `StrikeGraph Upload/08-Control-Mappings/SOC2-Control-Implementation-Matrix.md` |
| P2-02 | Map each control to policy/procedure source docs | Chris Wren | 2026-03-02 | Done | All 58 controls mapped to governing docs and domain/owner/frequency |
| P2-03 | Define per-control owner, frequency, reviewer, and exception process |  |  | Not Started | Completed control metadata |
| P2-04 | Define required evidence artifact(s) per control | Chris Wren | 2026-03-02 | Done | `SOC2-Evidence-Catalog.md` created with `EC-001` to `EC-058` linkage |
| P2-05 | Upload/import controls into Strike Graph and verify mappings |  |  | Not Started | Strike Graph mapping export |

## Priority 3 — Risk management operationalization (Week 2–4)
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| P3-01 | Finalize risk scoring model (Likelihood x Impact criteria) |  |  | Not Started | Scoring methodology doc |
| P3-02 | Set risk acceptance thresholds and approval levels |  |  | Not Started | Approved risk acceptance criteria |
| P3-03 | Link each non-accepted risk to one or more control IDs |  |  | Not Started | Risk-to-control mapping |
| P3-04 | Define residual risk acceptance workflow (owner + approver + expiry) |  |  | Not Started | Residual acceptance log |
| P3-05 | Establish review cadence (High monthly, Medium quarterly, full annual) |  |  | Not Started | Published review calendar |

## Priority 4 — Evidence readiness and dry run (Week 4–6)
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| P4-01 | Run evidence dry run for top 10 controls |  |  | Not Started | Evidence test results |
| P4-02 | Execute internal readiness audit on SOC 2 controls |  |  | Not Started | Internal audit report |
| P4-03 | Log and remediate findings via CAR process |  |  | Not Started | CAR tracker with closure proof |
| P4-04 | Conduct management review focused on SOC 2 readiness |  |  | Not Started | Signed management review minutes |
| P4-05 | Confirm all control evidence is time-bounded and reviewer-approved |  |  | Not Started | Final evidence completeness report |

## Priority 5 — Final pre-audit packaging
| ID | Task | Owner | Due | Status | Output/Evidence |
|---|---|---|---|---|---|
| P5-01 | Validate Strike Graph control/evidence completeness | Chris Wren | 2026-03-02 | In Progress | `SOC2-Upload-Queue.csv`, `SOC2-Upload-Batch-01.csv`, staged Batch-01 files |
| P5-02 | Compile request-response index for auditor PBC list |  |  | Not Started | PBC response index |
| P5-03 | Confirm exceptions and risk acceptances have approvals and dates |  |  | Not Started | Exception register |
| P5-04 | Final exec sign-off on readiness |  |  | Not Started | Sign-off memo |

## Immediate next 5 tasks (start now)
1. Complete `P0-01` scope confirmation.
2. Complete `P1-03` SoA finalization.
3. Complete `P2-01` SOC 2 control register draft.
4. Complete `P3-01` risk scoring methodology finalization.
5. Complete `P4-01` evidence dry run on top 10 controls.
