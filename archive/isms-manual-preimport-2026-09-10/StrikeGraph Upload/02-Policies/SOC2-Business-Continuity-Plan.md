# SOC2 Business Continuity Plan

**Document ID:** SOC2-BCP-001  
**Owner:** IT/Security Lead  
**Effective Date:** 2026-03-02  
**Review Frequency:** Annual + post-disruption

## 1. Purpose
Maintain critical operations during and after disruptive events.

## 2. Scope
Covers critical business services, technology dependencies, staff availability, and third-party dependencies.

## 3. Critical Service Tiers
- **Tier 1:** Customer-facing production services
- **Tier 2:** Core business applications
- **Tier 3:** Supporting internal systems

## 4. Continuity Objectives
| Tier | Target Recovery Time Objective (RTO) | Target Recovery Point Objective (RPO) |
|---|---|---|
| Tier 1 | 8 hours | 4 hours |
| Tier 2 | 24 hours | 12 hours |
| Tier 3 | 72 hours | 24 hours |

## 5. Activation and Governance
- Activation authority: IT/Security Lead or designated backup.
- Incident command: technical lead, communications lead, operations lead.
- Executive updates required for Tier 1 disruptions.

## 6. Minimum Continuity Controls
- Backup verification and restoration tests
- Alternate communication channels
- Dependency and vendor escalation list
- Manual workaround procedures for key processes

## 7. Testing Requirements
- At least annual tabletop test
- At least annual restore test for critical systems
- Post-test action tracking with owners and due dates

## 8. SOC 2 Evidence
- BCP tests and outcomes
- Restore test reports
- Continuity activation records
- Corrective action logs
