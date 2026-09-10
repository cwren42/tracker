# ARCHIVED 2026-09-10 — do not edit

Tracker is the system of record for the ISMS. These files are the pre-import
sources, kept for provenance only. **Editing anything here changes nothing.**

To change a policy or procedure, edit it in Tracker (`/isms`), which versions it
and records the change in the audit trail.

## What was imported into Tracker on 2026-09-10

| From | Into Tracker |
|---|---|
| 28 `IS-CIRQ-P-*` policies | published |
| 27 `IS-CIRQ-PR-*` procedures | published (+ PR-026-G, renumbered from PR-016-G) |
| 7 `IS-CIRQ-D-*` documents | published (D-001, D-002, D-003 G/US/ASIA, D-004, D-007) |
| `D-008` BCP, `D-009` DRP | imported as DRAFT — both are unfilled TEMPLATEs |
| `08-Control-Mappings/SOC2-Statement-of-Applicability.csv` | backfilled into `soc2_control` (ISO clause, applicability, justification) |

## What was deliberately NOT imported

- **`IS-CIRQ-F-001` Risk Assessment Register** and **`IS-CIRQ-F-004` Asset Register**
  — Tracker generates these live (`Risk_Assessment_*.xlsx`, `Asset_Inventory_*.xlsx`).
  A static copy would be stale the day it was made.
- **`IS-CIRQ-D-005` Risk Treatment Plan** and **`IS-CIRQ-D-006` Statement of Applicability**
  — to be generated from `risk` + `soc2_control` rather than maintained by hand.
- **`StrikeGraph Upload/` `EC-*` files** — 62 evidence placeholders, every one
  still marked `Status: Pending Collection` with no evidence attached. They are
  an empty scaffold, and much of what they ask for is already produced by the
  SOC 2 evidence pack.
- **`SOC2-Control-Gap-Matrix.csv` / `SOC2-Risk-Scoring-Final.csv`** — superseded.
  Both read "Not In Place" for every control against a single owner; Tracker now
  has 55/55 controls owned and active with real progress.

## Open items this archive raised

Three controls appear in the auditor's SoA but are **not tracked in Tracker**:
`CC-022 Encryption at Rest`, `CC-024 Firewall Rules`, `CC-028 Incidents External`.
Either they were consolidated into other controls or they were dropped; that
needs a decision, not an assumption.
