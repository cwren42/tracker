# UB-04 Scope Decision Draft (7 Conditional Risks)

Date: 2026-03-02
Prepared by: Copilot (draft for Executive Committee approval)

## Default audit scope assumption
- Proposed scope: **SOC 2 Security criteria only** (no additional Confidentiality, Availability, Processing Integrity, or Privacy criteria in this cycle).

## Proposed decisions
| Risk | Current Trigger Text | Proposed Decision | Rationale |
|---|---|---|---|
| Availability | Deactivate if Availability criteria not pursued | Deactivate | Not required for Security-only scope in this cycle |
| Confidentiality | Deactivate if Confidentiality criteria not pursued | Deactivate | Not required as separate supplemental criteria in this cycle |
| Environment | Deactivate if no on-site production servers | Deactivate (if no on-site prod) | Keep active only if production assets are physically hosted on-site |
| Physical Access Controls | Deactivate if fully remote | Keep Active (recommended) | Even with remote-first teams, office/device physical controls still influence security posture |
| Privacy of User Data | Deactivate if Privacy criteria not pursued | Deactivate (for SOC2 Security-only) | Keep as roadmap item for future Privacy criteria audit |
| Processing Integrity | Deactivate if PI criteria not pursued | Deactivate | Not required for Security-only scope in this cycle |
| User Data Collection | Deactivate if Privacy criteria not pursued | Deactivate (for SOC2 Security-only) | Keep as roadmap item for future Privacy criteria audit |

## Required approval inputs
1. Confirm Security-only scope for this audit cycle.
2. Confirm whether production systems are hosted on-site.
3. Confirm whether to keep Physical Access Controls active as a Security baseline.

## Sign-off
- Executive approver:
- Date:
- Final decisions recorded in Strike Graph: Yes / No
